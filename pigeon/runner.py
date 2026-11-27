"""Cloud runner: runs profiles on a server and streams them to any browser (desktop or phone).

A client starts a session by sending the runner its Pigeon token and the team key
for that profile (over TLS). The runner then does what a desktop would: lock, pull,
decrypt, launch headless Camoufox, and sync back on stop. Decrypted data lives in a
temp dir that is deleted when the session ends. Keys are held in memory only.

Viewing: /v/<id>?t=<view token> streams JPEG frames over a websocket and sends
clicks, scrolls, keys, text, navigation and tab switches back.
"""
from __future__ import annotations

import asyncio
import json
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
from pydantic import BaseModel

from . import crypto
from .client import Client

VIEWER = Path(__file__).with_name("viewer.html")
FPS = 6
MAX_SESSIONS = 8


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
        self.log: deque = deque(maxlen=40)
        self.tabs: list[dict] = []
        self.name = ""
        self.started = time.time()
        self.home = Path(tempfile.mkdtemp(prefix="pigeon-run-"))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # -------------------------------------------------- browser thread
    def _run(self) -> None:
        from .engine import open_profile
        try:
            with open_profile(self.client, self.pid, headless=True, home=self.home, log=self.log.append) as ctx:
                self.name = ctx.pigeon_info["name"]
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                state = {"page": page}
                ctx.on("page", lambda p: state.update(page=p))
                if ctx.pigeon_info.get("start_url"):
                    page.goto(ctx.pigeon_info["start_url"], wait_until="commit")
                self.status = "running"
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
            self.status = "stopped"
        except Exception as e:
            self.status, self.error = "error", str(e)[:300]
            self.log.append(f"error: {e}")
        finally:
            shutil.rmtree(self.home, ignore_errors=True)

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
                page.close()
                state["page"] = ctx.pages[-1]

    def info(self) -> dict:
        return {"id": self.id, "profile_id": self.pid, "name": self.name, "status": self.status, "error": self.error,
                "started": self.started, "started_by": self.started_by, "log": list(self.log)[-5:]}


def _safe_title(p) -> str:
    try:
        return p.title()[:80]
    except Exception:
        return ""


def create_runner(key: str) -> FastAPI:
    app = FastAPI(title="Pigeon runner")
    sessions: dict[str, Session] = {}

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
        s.stop.set()
        s.thread.join(120)
        return s.info()

    @app.get("/sessions/{sid}/status")
    def status(sid: str, t: str = ""):
        return by_view(sid, t).info()

    @app.get("/v/{sid}", response_class=HTMLResponse)
    def view(sid: str, t: str = ""):
        by_view(sid, t)
        return VIEWER.read_text(encoding="utf-8")

    @app.websocket("/ws/{sid}")
    async def ws(sock: WebSocket, sid: str, t: str = ""):
        s = sessions.get(sid)
        if not s or not secrets.compare_digest(s.view_token, t or ""):
            await sock.close(code=4404)
            return
        await sock.accept()
        s.viewers += 1
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
            while not rx.done():
                if s.frame_no != sent and s.frame:
                    sent = s.frame_no
                    await sock.send_bytes(s.frame)
                if tick % 6 == 0:
                    await sock.send_text(json.dumps({"status": s.status, "name": s.name, "tabs": s.tabs,
                                                     "error": s.error}))
                if s.status in ("stopped", "error"):
                    break
                tick += 1
                await asyncio.sleep(1 / (FPS * 2))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            s.viewers -= 1
            rx.cancel()

    @app.get("/health")
    def health():
        return {"ok": True, "sessions": sum(1 for s in sessions.values() if s.status == "running")}

    app.state.sessions = sessions
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
