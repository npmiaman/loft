"""Attach the Pigeon agent to a live Loft browser session, with all of its functionality.

The session's own thread owns the browser (Playwright's sync API is bound to it), so everything that touches Pigeon's
state or the browser runs there. The control server's HTTP thread only hands work over and waits for the answer:

  * state/LLM operations (leads, templates, settings, ...) go through `call()` and are serviced by `service()`, which the
    session loop and the agent's own polling both call, so they are answered promptly even mid-session and can never race
    the agent's edits;
  * browser operations (run, setup, send, comment, scan, replies) are queued as one job at a time and run by `pump()`.

Jobs work in a tab of their own, except page scan, which reads the Reddit tab you are looking at."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from ..control import SessionControl
from . import ops
from .agent import Agent
from .bridge import Bridge, Stop
from .central import CentralBrain
from .llm import Llm
from .runner import make_llm, state_path
from .state import State

MODES = ("find", "dm", "full")
NEEDS_LLM = {"setup", "discover"}


class PigeonHost:
    def __init__(self, pid: int, home: Path, central: CentralBrain | None = None,
                 llm_factory: Callable[[State], Llm | None] = make_llm):
        self.pid, self.home, self.central, self.llm_factory = pid, home, central, llm_factory
        self.st = State(state_path(pid, home))
        self.jobs: queue.Queue = queue.Queue()
        self.calls: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | stopping
        self.job: str | None = None
        self.mode: str | None = None
        self.since: float | None = None
        self.last: dict | None = None
        self.bridge: Bridge | None = None
        self.should_stop: Callable[[], bool] = lambda: False  # Ctrl+C / `loft stop` on the session: ends a running job too

    def tick_for(self, bridge: Bridge) -> Callable[[], None]:
        """Called by the bridge on every step: answer control requests, and wind a job down if the session was told to stop."""
        def tick() -> None:
            self.service()
            if self.should_stop():
                bridge.stop_flag = True
        return tick

    def mount(self, ctl: SessionControl) -> None:
        ctl.route("GET", "/pigeon/status", self.h_status)
        ctl.route("POST", "/pigeon/op", self.h_op)
        ctl.route("POST", "/pigeon/job", self.h_job)
        ctl.route("POST", "/pigeon/start", lambda b: self.h_job({"kind": "run", "args": {"mode": b.get("mode", "find"),
                                                                                         "minutes": b.get("minutes"), "pace": b.get("pace")}}))
        ctl.route("POST", "/pigeon/stop", self.h_stop)

    # ---- hand-over to the session thread -------------------------------------------------------
    def call(self, fn: Callable, timeout: float = 120.0):
        """HTTP thread: run `fn` on the session thread and return its result (or raise its exception)."""
        done, box = threading.Event(), {}
        self.calls.put((fn, done, box))
        if not done.wait(timeout):
            raise TimeoutError("the browser session is busy; try again in a moment")
        if "e" in box:
            raise box["e"]
        return box["v"]

    def service(self) -> None:
        """Session thread: answer pending calls. Cheap when there are none."""
        while True:
            try:
                fn, done, box = self.calls.get_nowait()
            except queue.Empty:
                return
            try:
                box["v"] = fn()
            except BaseException as e:
                box["e"] = e
            done.set()

    # ---- HTTP thread ---------------------------------------------------------------------------
    def _status(self) -> dict:
        st = self.st
        leads: dict[str, int] = {}
        for l in st.leads.values():
            leads[l["status"]] = leads.get(l["status"], 0) + 1
        return dict(state=self.state, job=self.job, mode=self.mode, since=self.since, last=self.last,
                    error=(self.last or {}).get("error"), stats=dict(st.stats), daily=dict(st.get_daily()),
                    product=(st.product or {}).get("name"), leads=leads, central=bool(self.central),
                    log=[dict(e) for e in st.logs[-8:]])

    def h_status(self, _b) -> tuple[int, dict]:
        return 200, self.call(self._status, 10)

    def h_op(self, body) -> tuple[int, dict]:
        name, args = body.get("op", ""), body.get("args") or {}

        def go():
            out = ops.run_state_op(ops.Ctx(self.st, self.llm_factory(self.st), self.central), name, args)
            self.st.save()
            return out
        try:
            return 200, self.call(go)
        except KeyError:
            return 404, {"detail": f"unknown operation {name!r}"}
        except TimeoutError as e:
            return 504, {"detail": str(e)}
        except Exception as e:
            self.st.log("warn", f"{name}: {e}")
            return 400, {"detail": str(e)}

    def h_job(self, body) -> tuple[int, dict]:
        kind, args = body.get("kind", ""), dict(body.get("args") or {})
        if kind not in ops.BROWSER_OPS:
            return 400, {"detail": f"unknown job {kind!r}; choices: {', '.join(ops.BROWSER_OPS)}"}
        if kind == "run" and args.get("mode", "find") not in MODES:
            return 400, {"detail": f"mode must be one of {', '.join(MODES)}"}
        needs_llm = kind in NEEDS_LLM or (kind == "run" and args.get("mode", "find") != "dm") or (kind == "scan" and not args.get("no_score"))
        with self.lock:
            if self.state != "idle":
                return 409, {"detail": f"Pigeon is already {self.state} ({self.job}) in this session"}
            if needs_llm and not self.llm_factory(self.st):
                return 400, {"detail": "No DeepSeek key: `loft pigeon config --deepseek-key KEY` or set DEEPSEEK_API_KEY."}
            if kind == "run" and args.get("mode", "find") != "dm" and not self.st.product:
                return 400, {"detail": f"Set up the product first: `loft pigeon setup {self.pid} URL`."}
            self.state, self.job, self.mode = "running", kind, args.get("mode") if kind == "run" else None
            self.since, self.last = time.time(), None
            self.jobs.put((kind, args))
        return 200, {"ok": True, "job": kind, "mode": self.mode}

    def h_stop(self, _b) -> tuple[int, dict]:
        with self.lock:
            if self.state == "idle":
                return 200, {"ok": True, "state": "idle"}
            self.state = "stopping"
            if self.bridge:
                self.bridge.stop_flag = True
            else:  # queued but not started: drop it
                try:
                    self.jobs.get_nowait()
                    self.state, self.job = "idle", None
                except queue.Empty:
                    pass
        return 200, {"ok": True, "state": self.state}

    # ---- session thread ------------------------------------------------------------------------
    @staticmethod
    def _reddit_tab(context):
        tabs = [p for p in context.pages if "reddit.com" in p.url]
        for p in tabs:
            try:
                if p.evaluate("() => document.visibilityState") == "visible":
                    return p
            except Exception:
                pass
        if tabs:
            return tabs[-1]
        raise RuntimeError("No Reddit tab is open in this session. Open one, or pass a URL to scan.")

    def pump(self, context) -> None:
        """Called every few hundred ms by the session loop."""
        self.service()
        try:
            kind, args = self.jobs.get_nowait()
        except queue.Empty:
            return
        page, own = None, True
        try:
            if kind == "scan" and not args.get("url"):
                page, own = self._reddit_tab(context), False  # the tab you are looking at
            else:
                page = context.new_page()
                page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45_000)
            bridge = Bridge(page, args["pace"] if args.get("pace") is not None else self.st.settings["pace"])
            bridge.on_tick = self.tick_for(bridge)
            with self.lock:
                if self.state == "stopping":
                    raise Stop()
                self.bridge = bridge
            agent = Agent(bridge, self.st, self.llm_factory(self.st), self.central, diag_dir=self.home / str(self.pid) / "diagnostics")
            out = ops.run_browser_op(agent, kind, args)
            self.last = dict(kind=kind, ok=True, out=out.get("out", ""), at=time.time())
        except Stop:
            self.last = dict(kind=kind, ok=False, error="stopped", at=time.time())
        except Exception as e:
            self.last = dict(kind=kind, ok=False, error=str(e), at=time.time())
            self.st.log("error", f"Pigeon {kind}: {e}")
        finally:
            if page is not None and own:
                try:
                    page.close()
                except Exception:
                    pass
            self.st.save()
            with self.lock:
                self.bridge, self.state, self.job = None, "idle", None
            self.service()

    def shutdown(self) -> None:
        if self.bridge:
            self.bridge.stop_flag = True
        self.service()
        self.st.save()
