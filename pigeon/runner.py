"""Cloud runner: runs profiles on a server and streams them to any browser (desktop or phone).

A client starts a session by sending the runner its Pigeon token and the team key
for that profile (over TLS). The runner then does what a desktop would: lock, pull,
decrypt, launch headless Camoufox, and sync back on stop. Decrypted data lives in a
temp dir that is deleted when the session ends. Keys are held in memory only.

Viewing: /v/<id>?t=<view token> streams JPEG frames over a websocket and sends
clicks, scrolls, keys, text, navigation and tab switches back.

Ending: a session that nobody has watched for PIGEON_RUNNER_IDLE_MINUTES (default 10) is
stopped and synced, so closing the tab never leaves the account locked. When a session
ends the socket gets one last status and closes with code 4410; an unknown session or a
wrong token closes with 4404. Ended sessions are forgotten after an hour.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import queue
import secrets
import shutil
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketState
from pydantic import BaseModel

from . import crypto
from .client import Client

VIEWER = Path(__file__).with_name("viewer.html")
FPS = 6
MAX_SESSIONS = 8
ENDED = ("stopped", "error")
CLOSE_ENDED, CLOSE_UNKNOWN = 4410, 4404


def idle_timeout_default() -> float:
    """Seconds a session may run with no viewer before it is stopped and saved."""
    try:
        return max(0.1, float(os.environ.get("PIGEON_RUNNER_IDLE_MINUTES", "10"))) * 60
    except ValueError:
        return 600.0


def plain_error(raw: Optional[str]) -> str:
    """One sentence a person can act on, for an exception that ended a session."""
    t = (raw or "").lower()
    if "is running" in t or "already running" in t or "run lock" in t:
        return "This account is open on another device. Close it there, then start it again."
    if "proxy" in t:
        return "The account's proxy isn't responding, so the browser didn't open. Check the proxy, then start it again."
    if "sign in again" in t or t.startswith("401") or "bad token" in t:
        return "Your sign-in to Pigeon has expired. Sign in again, then start the account again."
    if "key" in t and ("team" in t or "epoch" in t):
        return "The team's encryption key changed. Open the dashboard to refresh it, then start the account again."
    if "timeout" in t or "timed out" in t:
        return "The browser took too long to start. Start it again from the dashboard."
    return "The cloud browser closed because of a problem. Start it again from the dashboard."


class StartIn(BaseModel):
    server: str
    token: str
    team_id: int
    team_key: str
    key_epoch: int
    profile_id: int
    ca: Optional[str] = None  # PEM text of the Pigeon server's CA, if self-signed


class Session:
    def __init__(self, client: Client, pid: int, started_by: str):
        self.id = secrets.token_urlsafe(9)
        self.view_token = secrets.token_urlsafe(24)
        self.client, self.pid, self.started_by = client, pid, started_by
        self.cmds: "queue.Queue[dict]" = queue.Queue()
        self.stop = threading.Event()
        self.frame, self.frame_no = b"", 0
        self.viewers = 0
        self.status, self.error = "starting", None
        self.reason: Optional[str] = None  # why it ended: "stopped" (someone pressed Stop), "idle", "error"
        self.os = ""
        self.idle_since = time.time()      # when the last viewer left (or when it started, if none came)
        self.ended_at: Optional[float] = None
        self.log: deque = deque(maxlen=40)
        self.tabs: list[dict] = []
        self.name = ""
        self.started = time.time()
        self.home = Path(tempfile.mkdtemp(prefix="pigeon-run-"))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # -------------------------------------------------- browser thread
    def _run(self) -> None:
        try:
            self._browse()
            self.status = "stopped"
            self.reason = self.reason or "stopped"
        except Exception as e:
            self.status, self.error, self.reason = "error", str(e)[:300], "error"
            self.log.append(f"error: {e}")
        finally:
            shutil.rmtree(self.home, ignore_errors=True)
            self.ended_at = time.time()

    def _mark_running(self) -> None:
        self.idle_since = time.time()
        self.status = "running"

    def attach(self) -> None:
        self.viewers += 1

    def detach(self) -> None:
        self.viewers = max(0, self.viewers - 1)
        if not self.viewers:
            self.idle_since = time.time()

    def _browse(self) -> None:
        from .engine import open_profile
        with open_profile(self.client, self.pid, headless=True, home=self.home, log=self.log.append) as ctx:
            self.name = ctx.pigeon_info["name"]
            self.os = ctx.pigeon_info.get("os") or ""
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            state = {"page": page}
            ctx.on("page", lambda p: state.update(page=p))
            if ctx.pigeon_info.get("start_url"):
                page.goto(ctx.pigeon_info["start_url"], wait_until="commit")
            self._mark_running()
            last = 0.0
            while not self.stop.is_set():
                try:
                    cmd = self.cmds.get(timeout=0.05)
                except queue.Empty:
                    cmd = None
                if cmd:
                    try:
                        self._apply(ctx, state, cmd)
                    except Exception as e:  # a bad click shouldn't end the session
                        self.log.append(f"{cmd.get('t')}: {str(e)[:120]}")
                if state["page"].is_closed():
                    state["page"] = ctx.pages[-1] if ctx.pages else ctx.new_page()
                now = time.time()
                if self.viewers and now - last >= 1 / FPS:
                    last = now
                    try:
                        self.frame = state["page"].screenshot(type="jpeg", quality=55, scale="css", timeout=5000)
                        self.frame_no += 1
                    except Exception:
                        pass
                    self.tabs = [{"i": i, "url": p.url, "title": _safe_title(p), "active": p == state["page"]}
                                 for i, p in enumerate(ctx.pages)]
            self.status = "syncing"

    def _apply(self, ctx, state, c: dict) -> None:
        page = state["page"]
        t = c.get("t")
        if t == "click":
            page.mouse.click(float(c["x"]), float(c["y"]), button=c.get("button", "left"), click_count=int(c.get("n", 1)))
        elif t == "move":
            page.mouse.move(float(c["x"]), float(c["y"]))
        elif t == "scroll":
            page.mouse.move(float(c.get("x", 10)), float(c.get("y", 10)))
            page.mouse.wheel(float(c.get("dx", 0)), float(c.get("dy", 0)))
        elif t == "key":
            page.keyboard.press(str(c["key"])[:40])
        elif t == "type":
            page.keyboard.insert_text(str(c["text"])[:5000])
        elif t == "goto":
            url = str(c["url"]).strip()
            if "://" not in url:
                url = "https://" + url
            page.goto(url, wait_until="commit")
        elif t == "back":
            page.go_back(wait_until="commit")
        elif t == "forward":
            page.go_forward(wait_until="commit")
        elif t == "reload":
            page.reload(wait_until="commit")
        elif t == "tab":
            i = int(c["i"])
            if 0 <= i < len(ctx.pages):
                state["page"] = ctx.pages[i]
                state["page"].bring_to_front()
        elif t == "newtab":
            state["page"] = ctx.new_page()
        elif t == "closetab":
            if len(ctx.pages) > 1:
                i = int(c.get("i", ctx.pages.index(page) if page in ctx.pages else -1))
                target = ctx.pages[i] if -len(ctx.pages) <= i < len(ctx.pages) else page
                target.close()
                if target == page or state["page"].is_closed():
                    state["page"] = ctx.pages[-1]

    def info(self) -> dict:
        return {"id": self.id, "profile_id": self.pid, "name": self.name, "status": self.status, "error": self.error,
                "reason": self.reason, "started": self.started, "started_by": self.started_by, "log": list(self.log)[-5:]}

    def view_status(self, idle_timeout: float) -> dict:
        """What the viewer gets: status plus a plain sentence for errors."""
        return {"status": self.status, "name": self.name, "tabs": self.tabs, "error": self.error, "reason": self.reason,
                "message": plain_error(self.error) if self.status == "error" else None, "os": self.os,
                "idle_minutes": round(idle_timeout / 60, 1)}


def _safe_title(p) -> str:
    try:
        return p.title()[:80]
    except Exception:
        return ""


ENDED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>{title} · Pigeon</title>
<style>
:root {{ --bg:#fff; --card:#fff; --ink:#0d0d0d; --mute:#6e6e80; --line:#ececf1; color-scheme:light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#151516; --card:#1b1b1d; --ink:#ececf1; --mute:#9b9ba8; --line:#2a2a2e; color-scheme:dark; }} }}
body {{ margin:0; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; padding:16px;
       box-sizing:border-box; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.card {{ max-width:420px; width:100%; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:24px; }}
h1 {{ font-size:18px; margin:0 0 8px; }} p {{ margin:0 0 8px; color:var(--mute); }}
</style></head><body><div class="card" role="status"><h1>{title}</h1><p>{body}</p><p>{next}</p></div></body></html>"""


def ended_page(s: Optional[Session], idle_timeout: float) -> tuple[str, int]:
    if s is None:
        return ENDED_PAGE.format(title="This link has expired", body="The session it pointed to has ended, or the link isn't complete.",
                                 next="Start the account again from the Pigeon dashboard to get a new link."), 404
    mins = round(idle_timeout / 60)
    body = {"idle": f"It stopped after {mins} minute{'s' if mins != 1 else ''} with no one watching. Your login was saved.",
            "error": plain_error(s.error)}.get(s.reason or "", "It was stopped. Your login was saved.")
    return ENDED_PAGE.format(title="This session has ended", body=html.escape(body),
                             next="You can close this tab, or start the account again from the Pigeon dashboard."), 410


def create_runner(key: str, *, idle_timeout: Optional[float] = None, ended_keep: float = 3600.0,
                  reap_every: float = 5.0) -> FastAPI:
    """idle_timeout: seconds with no viewer before a running session is stopped and saved (default from
    PIGEON_RUNNER_IDLE_MINUTES, 10 minutes). ended_keep: how long an ended session stays visible."""
    idle = idle_timeout_default() if idle_timeout is None else float(idle_timeout)
    sessions: dict[str, Session] = {}

    def reap(now: Optional[float] = None) -> None:
        now = now or time.time()
        for sid, s in list(sessions.items()):
            if s.status == "running" and not s.viewers and not s.stop.is_set() and now - s.idle_since >= idle:
                s.reason = "idle"
                s.log.append(f"stopping: no one watched for {round(idle / 60, 1)} min")
                s.stop.set()  # the browser thread syncs and unlocks on its way out
            elif s.status in ENDED and s.ended_at and now - s.ended_at >= ended_keep:
                sessions.pop(sid, None)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async def loop():
            while True:
                await asyncio.sleep(reap_every)
                try:
                    reap()
                except Exception:  # never let the reaper die
                    pass
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Pigeon runner", lifespan=lifespan)

    def need_key(k: str) -> None:
        if not k or not secrets.compare_digest(k, key):
            raise HTTPException(401, "bad runner key")

    def by_view(sid: str, t: str) -> Session:
        s = sessions.get(sid)
        if not s or not secrets.compare_digest(s.view_token, t or ""):
            raise HTTPException(404, "session not found")
        return s

    @app.post("/sessions")
    def start(body: StartIn, x_runner_key: str = Header(default="")):
        need_key(x_runner_key)
        live = [s for s in sessions.values() if s.status in ("starting", "running", "syncing")]
        if len(live) >= MAX_SESSIONS:
            raise HTTPException(503, "runner is full")
        if any(s.pid == body.profile_id and s.client.server == body.server.rstrip("/") for s in live):
            raise HTTPException(409, "this profile is already running here")
        ca_path = None
        if body.ca:
            f = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
            f.write(body.ca)
            f.close()
            ca_path = f.name
        c = Client(server=body.server, token=body.token, ca=ca_path)
        c.device = "cloud-runner"
        c.keys[body.team_id] = (body.key_epoch, crypto.b64d(body.team_key))
        me = c.me()
        s = Session(c, body.profile_id, me["email"])
        sessions[s.id] = s
        return {"id": s.id, "view": f"/v/{s.id}?t={s.view_token}"}

    @app.get("/sessions")
    def list_sessions(x_runner_key: str = Header(default="")):
        need_key(x_runner_key)
        return [s.info() for s in sessions.values()]

    @app.delete("/sessions/{sid}")
    def stop(sid: str, t: str = "", x_runner_key: str = Header(default="")):
        s = sessions.get(sid)
        if not s:
            raise HTTPException(404, "session not found")
        if not (x_runner_key and secrets.compare_digest(x_runner_key, key)):
            by_view(sid, t)
        if s.status not in ENDED:
            s.reason = s.reason or "stopped"
        s.stop.set()
        s.thread.join(120)
        return s.info()

    @app.get("/sessions/{sid}/status")
    def status(sid: str, t: str = ""):
        return by_view(sid, t).info()

    @app.get("/v/{sid}", response_class=HTMLResponse)
    def view(sid: str, t: str = ""):
        s = sessions.get(sid)
        if not s or not secrets.compare_digest(s.view_token, t or ""):
            body, code = ended_page(None, idle)
            return HTMLResponse(body, status_code=code)
        if s.status in ENDED:
            body, code = ended_page(s, idle)
            return HTMLResponse(body, status_code=code)
        return HTMLResponse(VIEWER.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/{sid}")
    async def ws(sock: WebSocket, sid: str, t: str = ""):
        s = sessions.get(sid)
        await sock.accept()  # accept first: a close before accept is a bare HTTP 403 and the browser never sees the code
        if not s or not secrets.compare_digest(s.view_token, t or ""):
            await sock.close(code=CLOSE_UNKNOWN)
            return
        s.attach()
        sent = -1

        async def receiver():
            while True:
                msg = await sock.receive_text()
                try:
                    s.cmds.put(json.loads(msg))
                except ValueError:
                    pass

        rx = asyncio.create_task(receiver())
        try:
            tick = 0
            last = None
            while not rx.done():
                if s.status in ENDED:
                    await sock.send_text(json.dumps(s.view_status(idle)))  # the last word, then a close the viewer won't retry
                    await sock.close(code=CLOSE_ENDED)
                    break
                if s.frame_no != sent and s.frame:
                    sent = s.frame_no
                    await sock.send_bytes(s.frame)
                st = s.view_status(idle)
                if tick % 6 == 0 or st != last:
                    last = st
                    await sock.send_text(json.dumps(st))
                tick += 1
                await asyncio.sleep(1 / (FPS * 2))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            s.detach()
            rx.cancel()
            if sock.client_state == WebSocketState.CONNECTED and sock.application_state == WebSocketState.CONNECTED:
                with contextlib.suppress(Exception):
                    await sock.close()

    @app.get("/health")
    def health():
        return {"ok": True, "sessions": sum(1 for s in sessions.values() if s.status == "running")}

    app.state.sessions = sessions
    app.state.idle_timeout = idle
    app.state.reap = reap
    return app


def start_cloud_session(client: Client, pid: int, runner: dict) -> dict:
    """Ask a runner to run a profile. Returns {id, view (absolute URL)}."""
    import httpx
    info = client.profile(pid)
    tid = info["team_id"]
    ca_text = Path(client.ca).read_text() if client.ca else None
    r = httpx.post(runner["url"] + "/sessions", headers={"X-Runner-Key": runner["secret"]}, timeout=60,
                   verify=client.ca or True,
                   json={"server": client.server, "token": client.token, "team_id": tid,
                         "team_key": crypto.b64e(client.team_key(tid)), "key_epoch": client.team_epoch(tid),
                         "profile_id": pid, "ca": ca_text})
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        from .client import PigeonError
        raise PigeonError(r.status_code, f"runner: {detail}")
    out = r.json()
    out["view"] = runner["url"] + out["view"]
    return out
