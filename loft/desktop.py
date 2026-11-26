"""Desktop app: a local web UI (127.0.0.1 only) over the cloud client.

Each launched profile runs as its own `loft run` subprocess so a browser crash
never takes the app down, and closing the browser window triggers the sync.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .cli import parse_proxy
from .client import Client, LoftError, load_config, save_config

UI = Path(__file__).with_name("ui.html")


class Running:
    def __init__(self):
        self.procs: dict[int, subprocess.Popen] = {}
        self.logs: dict[int, deque] = {}

    def start(self, pid: int) -> None:
        if pid in self.procs and self.procs[pid].poll() is None:
            raise HTTPException(409, "already running on this machine")
        p = subprocess.Popen([sys.executable, "-m", "loft.cli", "run", str(pid)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.procs[pid] = p
        log = self.logs[pid] = deque(maxlen=50)

        def pump():
            for line in p.stdout:
                log.append(line.rstrip())
            log.append(f"exited ({p.wait()})")

        threading.Thread(target=pump, daemon=True).start()

    def state(self) -> dict:
        return {pid: {"alive": p.poll() is None, "log": list(self.logs.get(pid, []))[-6:]}
                for pid, p in self.procs.items()}


class LoginIn(BaseModel):
    server: str
    email: str
    password: str
    register: bool = False
    name: str = ""
    invite: Optional[str] = None


class NewProfile(BaseModel):
    name: str
    os: str = "windows"
    proxy: str = ""
    start_url: str = ""
    notes: str = ""
    tags: list[str] = []


class EditProfile(BaseModel):
    name: Optional[str] = None
    proxy: Optional[str] = None  # "" clears
    start_url: Optional[str] = None
    notes: Optional[str] = None


def make_app() -> FastAPI:
    app = FastAPI()
    running = Running()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Block other websites from driving this API: same-host check + a custom
        # header that cross-origin pages can't send without a CORS preflight.
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "bad host"}, 403)
        if request.url.path.startswith("/api/") and request.headers.get("x-loft") != "1":
            return JSONResponse({"detail": "missing X-Loft header"}, 403)
        try:
            return await call_next(request)
        except LoftError as e:
            return JSONResponse({"detail": e.detail}, e.status)

    def client() -> Client:
        c = Client()
        if not c.token:
            raise HTTPException(401, "not signed in")
        return c

    def team(c: Client) -> int:
        cfg = load_config()
        teams = c.me()["teams"]
        ids = [t["id"] for t in teams]
        if cfg.get("team") not in ids:
            if not ids:
                raise HTTPException(400, "no team")
            cfg["team"] = ids[0]
            save_config(cfg)
        return cfg["team"]

    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI.read_text()

    @app.get("/api/state")
    def state():
        cfg = load_config()
        if not cfg.get("token"):
            return {"signed_in": False, "server": cfg.get("server")}
        c = Client()
        try:
            me = c.me()
        except LoftError as e:
            if e.status == 401:
                return {"signed_in": False, "server": cfg.get("server")}
            raise
        out: dict[str, Any] = {"signed_in": True, "server": c.server, "me": me, "device": c.device,
                               "running": running.state()}
        if me["teams"]:
            tid = team(c)
            role = next(t["role"] for t in me["teams"] if t["id"] == tid)
            out.update(team=tid, role=role, profiles=c.profiles(tid), members=c.members(tid))
        return out

    @app.post("/api/login")
    def login(body: LoginIn):
        c = Client(server=body.server, token="")
        out = c.register(body.email, body.password, body.name, body.invite or None) if body.register \
            else c.login(body.email, body.password)
        cfg = load_config()
        cfg.update(server=body.server, token=out["token"], team=None)
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/logout")
    def logout():
        cfg = load_config()
        cfg.pop("token", None)
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/teams")
    def new_team(body: dict):
        t = client().create_team(body["name"])
        cfg = load_config()
        cfg["team"] = t["id"]
        save_config(cfg)
        return t

    @app.post("/api/team/{tid}")
    def use_team(tid: int):
        cfg = load_config()
        cfg["team"] = tid
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/join")
    def join(body: dict):
        out = client().accept(body["code"].strip())
        cfg = load_config()
        cfg["team"] = out["team_id"]
        save_config(cfg)
        return out

    @app.post("/api/profiles")
    def create(body: NewProfile):
        from .engine import new_fingerprint
        c = client()
        return c.create_profile(team(c), name=body.name, os=body.os, proxy=parse_proxy(body.proxy or None),
                                start_url=body.start_url, notes=body.notes, tags=body.tags,
                                fingerprint=new_fingerprint(body.os))

    @app.patch("/api/profiles/{pid}")
    def edit(pid: int, body: EditProfile):
        f: dict[str, Any] = {k: v for k, v in dict(name=body.name, start_url=body.start_url, notes=body.notes).items()
                             if v is not None}
        if body.proxy is not None:
            if body.proxy.strip():
                f["proxy"] = parse_proxy(body.proxy)
            else:
                f["clear_proxy"] = True
        return client().patch_profile(pid, **f)

    @app.delete("/api/profiles/{pid}")
    def delete(pid: int):
        return client().delete_profile(pid)

    @app.post("/api/profiles/{pid}/run")
    def run(pid: int):
        running.start(pid)
        return {"ok": True}

    @app.post("/api/profiles/{pid}/unlock")
    def unlock(pid: int):
        return client().unlock(pid, force=True)

    @app.get("/api/profiles/{pid}/shares")
    def shares(pid: int):
        return client().shares(pid)

    @app.post("/api/profiles/{pid}/shares")
    def share(pid: int, body: dict):
        return client().share(pid, int(body["user_id"]), body.get("level", "run"))

    @app.delete("/api/profiles/{pid}/shares/{uid}")
    def unshare(pid: int, uid: int):
        return client().unshare(pid, uid)

    @app.post("/api/invite")
    def invite(body: dict):
        c = client()
        return c.invite(team(c), body.get("role", "member"))

    @app.post("/api/members/{uid}/role")
    def role(uid: int, body: dict):
        c = client()
        return c.set_role(team(c), uid, body["role"])

    @app.delete("/api/members/{uid}")
    def kick(uid: int):
        c = client()
        return c.remove_member(team(c), uid)

    @app.get("/api/audit")
    def audit():
        c = client()
        return c.audit(team(c), 200)

    return app


def serve(port: int = 4410, open_browser: bool = True) -> None:
    import uvicorn
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Loft desktop on {url}")
    uvicorn.run(make_app(), host="127.0.0.1", port=port, log_level="warning")
