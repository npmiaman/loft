"""Desktop app: local web UI + local automation API, bound to 127.0.0.1 only.

* /api/*  - the UI's own API (needs the X-Loft header, which other websites can't send).
* /v1/*   - automation API for scripts (needs the API key from `loft apikey`).

Each launched profile runs as its own `loft run` subprocess so a browser crash never
takes the app down. Stopping sends SIGINT, which closes the browser and syncs.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .cli import BadInput, parse_proxy, proxy_url
from . import control
from .client import Client, LoftError, load_config, save_config

UI = Path(__file__).with_name("ui.html")


def api_key() -> str:
    cfg = load_config()
    if not cfg.get("api_key"):
        cfg["api_key"] = "loft_" + secrets.token_urlsafe(24)
        save_config(cfg)
    return cfg["api_key"]


def self_cmd(*args: str) -> list[str]:
    """Command that runs this program's CLI, for both source installs and frozen desktop builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "loft.cli", *args]


def browser_installed() -> bool:
    try:
        from camoufox.pkgman import installed_verstr
        installed_verstr()
        return True
    except Exception:
        return False


class Jobs:
    """Local subprocesses: profile runs and the one-off browser download."""

    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, deque] = {}

    def start(self, key: str, args: list[str]) -> None:
        if key in self.procs and self.procs[key].poll() is None:
            raise HTTPException(409, "already running on this machine")
        p = subprocess.Popen(self_cmd(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                             start_new_session=True)  # own process group: our SIGINT reaches only it
        self.procs[key] = p
        log = self.logs[key] = deque(maxlen=50)

        def pump():
            for line in p.stdout:
                if "LeakWarning" not in line and "i_know_what_im_doing" not in line:
                    log.append(line.rstrip())
            log.append(f"exited ({p.wait()})")

        threading.Thread(target=pump, daemon=True).start()

    def stop(self, key: str) -> None:
        p = self.procs.get(key)
        if not p or p.poll() is not None:
            raise HTTPException(404, "not running on this machine")
        if key.startswith("run-"):
            from .engine import request_stop
            request_stop(int(key[4:]))  # run loop sees it, closes the browser, syncs, exits
        else:
            p.send_signal(signal.SIGTERM)

    def alive(self, key: str) -> bool:
        p = self.procs.get(key)
        return bool(p and p.poll() is None)

    def state(self) -> dict:
        return {k: {"alive": p.poll() is None, "log": list(self.logs.get(k, []))[-6:]} for k, p in self.procs.items()}


class LoginIn(BaseModel):
    server: str
    email: str
    password: str
    signup: bool = False
    name: str = ""
    invite: Optional[str] = None
    ca: Optional[str] = None


class NewProfile(BaseModel):
    name: str
    os: Optional[str] = None  # default: this machine's OS
    proxy: str = ""
    start_url: str = ""
    notes: str = ""
    tags: list[str] = []
    canvas: Optional[str] = None  # default: real on this machine's OS, else noise


class BulkIn(BaseModel):
    count: int
    name: str = "Profile {n}"
    os: Optional[str] = None
    proxies: list[str] = []
    tags: list[str] = []
    start_url: str = ""
    canvas: Optional[str] = None


class EditProfile(BaseModel):
    name: Optional[str] = None
    proxy: Optional[str] = None  # "" clears
    start_url: Optional[str] = None
    notes: Optional[str] = None
    canvas: Optional[str] = None


class TemplateIn(BaseModel):
    name: str
    os: str = "windows"
    proxy: str = ""
    start_url: str = ""
    tags: list[str] = []
    canvas: str = "noise"


def make_app() -> FastAPI:
    app = FastAPI()
    jobs = Jobs()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "bad host"}, 403)
        path = request.url.path
        if path.startswith("/api/") and request.headers.get("x-loft") != "1":
            return JSONResponse({"detail": "missing X-Loft header"}, 403)
        if path.startswith("/v1/"):
            k = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not k or not secrets.compare_digest(k, api_key()):
                return JSONResponse({"detail": "bad API key (see `loft apikey`)"}, 401)
        try:
            return await call_next(request)
        except LoftError as e:
            return JSONResponse({"detail": e.detail}, e.status)
        except BadInput as e:
            return JSONResponse({"detail": str(e)}, 400)

    def client() -> Client:
        c = Client(persist=True)
        if not c.token:
            raise HTTPException(401, "not signed in")
        return c

    def team(c: Client) -> int:
        cfg = load_config()
        ids = [t["id"] for t in c.me()["teams"]]
        if cfg.get("team") not in ids:
            if not ids:
                raise HTTPException(400, "no team")
            cfg["team"] = ids[0]
            save_config(cfg)
        return cfg["team"]

    def set_team(tid: int) -> None:
        cfg = load_config()
        cfg["team"] = tid
        save_config(cfg)

    def new_profile(c: Client, tid: int, body: NewProfile) -> dict:
        from .engine import default_canvas, host_os, new_fingerprint
        os_name = body.os or host_os()
        return c.create_profile(tid, name=body.name, os=os_name, proxy=parse_proxy(body.proxy or None),
                                start_url=body.start_url, notes=body.notes, tags=body.tags,
                                canvas=body.canvas or default_canvas(os_name), fingerprint=new_fingerprint(os_name))

    def bulk(c: Client, tid: int, body: BulkIn) -> list:
        from .engine import bulk_create
        proxies = [parse_proxy(p) for p in body.proxies if p.strip()] or None
        from .engine import default_canvas, host_os
        os_name = body.os or host_os()
        return bulk_create(c, tid, body.count, name=body.name, os_name=os_name, proxies=proxies, tags=body.tags,
                           start_url=body.start_url, canvas=body.canvas or default_canvas(os_name))

    def patch(c: Client, pid: int, body: EditProfile) -> dict:
        f: dict[str, Any] = {k: v for k, v in dict(name=body.name, start_url=body.start_url, notes=body.notes,
                                                   canvas=body.canvas).items() if v is not None}
        if body.proxy is not None:
            if body.proxy.strip():
                f["proxy"] = parse_proxy(body.proxy)
            else:
                f["clear_proxy"] = True
        return c.patch_profile(pid, **f)

    def with_local(ps: list) -> list:
        for p in ps:
            p["running_here"] = jobs.alive(f"run-{p['id']}")
        return ps

    # ------------------------------------------------------------ UI
    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI.read_text()

    @app.get("/api/state")
    def state():
        cfg = load_config()
        from .engine import host_os
        live = jobs.state()
        for k, v in live.items():  # a running profile has a control channel: show its Pigeon agent's status
            v["pigeon"] = None
            if k.startswith("run-") and v["alive"]:
                try:
                    v["pigeon"] = control.request(int(k[4:]), "GET", "/pigeon/status", timeout=1)
                except LoftError:
                    pass  # still starting up, or already closing
        base = {"server": cfg.get("server"), "browser": browser_installed(), "jobs": live, "host_os": host_os()}
        if not cfg.get("token"):
            return {**base, "signed_in": False}
        c = Client(persist=True)
        try:
            me = c.me()
        except LoftError as e:
            if e.status == 401:
                return {**base, "signed_in": False}
            raise
        out: dict[str, Any] = {**base, "signed_in": True, "server": c.server, "me": me, "device": c.device}
        if me["teams"]:
            tid = team(c)
            out.update(team=tid, role=next(t["role"] for t in me["teams"] if t["id"] == tid),
                       has_key=tid in c.keys or _try_key(c, tid))
            if out["has_key"]:
                out.update(profiles=with_local(c.profiles(tid)), members=c.members(tid),
                           templates=c.templates(tid), runners=[{"id": r["id"], "name": r["name"], "url": r["url"]}
                                                                for r in c.runners(tid)])
        return out

    @app.post("/api/login")
    def login(body: LoginIn):
        ca = None
        if body.ca and body.ca.strip():
            from .client import HOME
            HOME.mkdir(parents=True, exist_ok=True)
            ca = str(HOME / "server-ca.pem")
            Path(ca).write_text(body.ca.strip() + "\n")
        c = Client(server=body.server, token="", persist=True, ca=ca)
        out = c.register(body.email, body.password, body.name, body.invite or None) if body.signup \
            else c.login(body.email, body.password)
        cfg = load_config()
        cfg["team"] = out.get("team_id")
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/logout")
    def logout():
        cfg = load_config()
        for k in ("token", "vault_key", "keys"):
            cfg.pop(k, None)
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/teams")
    def new_team(body: dict):
        t = client().create_team(body["name"])
        set_team(t["id"])
        return t

    @app.post("/api/team/{tid}")
    def use_team(tid: int):
        set_team(tid)
        return {"ok": True}

    @app.post("/api/join")
    def join(body: dict):
        out = client().accept(body["code"].strip())
        set_team(out["team_id"])
        return out

    @app.post("/api/profiles")
    def create(body: NewProfile):
        c = client()
        return new_profile(c, team(c), body)

    @app.post("/api/profiles/bulk")
    def create_bulk(body: BulkIn):
        c = client()
        return {"created": len(bulk(c, team(c), body))}

    @app.patch("/api/profiles/{pid}")
    def edit(pid: int, body: EditProfile):
        return patch(client(), pid, body)

    @app.delete("/api/profiles/{pid}")
    def delete(pid: int):
        return client().delete_profile(pid)

    @app.post("/api/profiles/{pid}/run")
    def run(pid: int):
        if not browser_installed():
            raise HTTPException(400, "the browser isn't installed yet: use Install browser")
        jobs.start(f"run-{pid}", ["run", str(pid)])
        return {"ok": True}

    @app.post("/api/profiles/{pid}/stop")
    def stop(pid: int):
        jobs.stop(f"run-{pid}")
        return {"ok": True}

    @app.post("/api/profiles/{pid}/cloud")
    def cloud(pid: int, body: dict):
        from .runner import start_cloud_session
        c = client()
        rs = c.runners(team(c))
        r = next((x for x in rs if x["id"] == body.get("runner_id")), rs[0] if rs else None)
        if not r:
            raise HTTPException(400, "no cloud runners in this team")
        return start_cloud_session(c, pid, r)

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

    @app.get("/api/profiles/{pid}/proxy")
    def get_proxy(pid: int):
        return {"proxy": proxy_url(client().profile(pid).get("proxy"))}

    @app.post("/api/profiles/{pid}/cookies")
    def cookies_in(pid: int, body: dict):
        from .engine import import_cookies, parse_cookies
        if jobs.alive(f"run-{pid}"):
            raise HTTPException(409, "stop the profile first")
        return import_cookies(client(), pid, parse_cookies(body["text"]), log=lambda m: None)

    @app.get("/api/profiles/{pid}/cookies")
    def cookies_out(pid: int):
        from .engine import export_cookies
        if jobs.alive(f"run-{pid}"):
            raise HTTPException(409, "stop the profile first")
        return export_cookies(client(), pid, log=lambda m: None)

    @app.post("/api/profiles/{pid}/export")
    def export(pid: int, body: dict):
        from .engine import export_bundle
        c = client()
        blob = export_bundle(c, pid, body.get("passphrase") or None)
        name = c.profile(pid)["name"].replace('"', "")
        return Response(blob, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}.loftprofile"'})

    @app.post("/api/import")
    def import_(body: dict):
        from .engine import import_bundle
        c = client()
        return import_bundle(c, team(c), base64.b64decode(body["data"]), body.get("passphrase") or None,
                             body.get("name") or None)

    @app.post("/api/templates")
    def add_template(body: TemplateIn):
        parse_proxy(body.proxy or None)
        c = client()
        return c.add_template(team(c), body.name, body.model_dump(exclude={"name"}))

    @app.delete("/api/templates/{xid}")
    def del_template(xid: int):
        return client().delete_template(xid)

    @app.post("/api/runners")
    def add_runner(body: dict):
        c = client()
        return c.add_runner(team(c), body["name"], body["url"], body["key"])

    @app.delete("/api/runners/{rid}")
    def del_runner(rid: int):
        return client().delete_runner(rid)

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

    # ------------------------------------------------------------ Pigeon (see loft/pigeon)
    def _session(pid: int, method: str, path: str, body: dict | None = None, timeout: float = 130) -> dict:
        try:
            return control.request(pid, method, path, body, timeout=timeout)
        except LoftError as e:
            raise HTTPException(e.status, e.detail)

    # Everything Pigeon can do runs inside the profile's open browser session; these pass straight through to it.
    @app.get("/api/profiles/{pid}/pigeon/status")
    def pigeon_status(pid: int):
        return _session(pid, "GET", "/pigeon/status", timeout=10)

    @app.post("/api/profiles/{pid}/pigeon/op")
    def pigeon_op(pid: int, body: dict):
        return _session(pid, "POST", "/pigeon/op", {"op": body.get("op"), "args": body.get("args") or {}})

    @app.post("/api/profiles/{pid}/pigeon/job")
    def pigeon_job(pid: int, body: dict):
        return _session(pid, "POST", "/pigeon/job", {"kind": body.get("kind"), "args": body.get("args") or {}})

    @app.post("/api/profiles/{pid}/pigeon/stop")
    def pigeon_stop(pid: int):
        return _session(pid, "POST", "/pigeon/stop")

    @app.post("/api/profiles/{pid}/pigeon")  # kept for the small attach/stop controls on the profile row
    def pigeon(pid: int, body: dict):
        if body.get("action") == "stop":
            return _session(pid, "POST", "/pigeon/stop")
        return _session(pid, "POST", "/pigeon/start", {"mode": body.get("mode", "find")})

    @app.get("/api/pigeon/brain")
    def pigeon_brain(days: float = 14, limit: int = 40):
        """The team's shared memory: totals by kind, subreddit and account, plus the latest events."""
        c = client()
        tid = team(c)
        cfg = c.pigeon_config(tid)
        setup = cfg["setup"] or {}
        return {"summary": c.pigeon_summary(tid, days), "events": c.pigeon_events(tid, days=days, limit=limit),
                "accounts": c.pigeon_accounts(tid),
                "team_setup": {"version": cfg["version"], "product": (setup.get("product") or {}).get("name"),
                               "subreddits": len(setup.get("subreddits") or []), "limits": cfg["limits"],
                               "unreadable": cfg["unreadable"]}}

    @app.get("/api/rotate-key/preview")
    def rotate_preview():
        c = client()
        return c.rotation_preview(team(c))

    @app.post("/api/rotate-key")
    def rotate_key():
        from .client import rotate_team_key
        c = client()
        return rotate_team_key(c, team(c), log=lambda m: None)

    @app.get("/api/overview")
    def overview():
        c = client()
        return c.overview(team(c))

    @app.post("/api/backup")
    def backup(body: dict):
        from .engine import export_team
        c = client()
        blob = export_team(c, team(c), body["passphrase"], log=lambda m: None)
        return Response(blob, media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="loft-team-backup.loftbackup"'})

    @app.post("/api/restore")
    def restore(body: dict):
        from .engine import import_team
        c = client()
        return import_team(c, team(c), base64.b64decode(body["data"]), body["passphrase"], log=lambda m: None)

    @app.get("/api/audit")
    def audit():
        c = client()
        return c.audit(team(c), 200)

    @app.get("/api/apikey")
    def get_api_key():
        return {"key": api_key(), "base": "http://127.0.0.1:4410/v1"}

    @app.post("/api/browser/install")
    def install_browser():
        jobs.start("fetch", ["fetch-browser"])
        return {"ok": True}

    # ------------------------------------------------------------ automation API (/v1)
    @app.get("/v1/profiles")
    def v1_list():
        c = client()
        return with_local(c.profiles(team(c)))

    @app.post("/v1/profiles")
    def v1_create(body: NewProfile):
        c = client()
        return new_profile(c, team(c), body)

    @app.post("/v1/profiles/bulk")
    def v1_bulk(body: BulkIn):
        c = client()
        return bulk(c, team(c), body)

    @app.get("/v1/profiles/{pid}")
    def v1_get(pid: int):
        return with_local([client().profile(pid)])[0]

    @app.patch("/v1/profiles/{pid}")
    def v1_patch(pid: int, body: EditProfile):
        return patch(client(), pid, body)

    @app.delete("/v1/profiles/{pid}")
    def v1_delete(pid: int):
        return client().delete_profile(pid)

    @app.post("/v1/profiles/{pid}/start")
    def v1_start(pid: int, body: Optional[dict] = None):
        args = ["run", str(pid)] + (["--headless"] if (body or {}).get("headless") else [])
        jobs.start(f"run-{pid}", args)
        return {"ok": True}

    @app.post("/v1/profiles/{pid}/stop")
    def v1_stop(pid: int):
        jobs.stop(f"run-{pid}")
        return {"ok": True}

    @app.get("/v1/profiles/{pid}/status")
    def v1_status(pid: int):
        p = client().profile(pid)
        k = f"run-{pid}"
        return {"running_here": jobs.alive(k), "running": p["running"], "version": p["version"],
                "log": list(jobs.logs.get(k, []))[-10:]}

    @app.post("/v1/profiles/{pid}/cookies")
    def v1_cookies_in(pid: int, body: dict):
        from .engine import import_cookies, parse_cookies
        text = body["cookies"] if isinstance(body["cookies"], str) else json.dumps(body["cookies"])
        return import_cookies(client(), pid, parse_cookies(text), log=lambda m: None)

    @app.get("/v1/profiles/{pid}/cookies")
    def v1_cookies_out(pid: int):
        return cookies_out(pid)

    @app.post("/v1/profiles/{pid}/cloud")
    def v1_cloud(pid: int, body: Optional[dict] = None):
        return cloud(pid, body or {})

    return app


def _try_key(c: Client, tid: int) -> bool:
    try:
        c.team_key(tid)
        return True
    except LoftError:
        return False


def serve(port: int = 4410, native: bool = True, open_browser: bool = True) -> None:
    import uvicorn
    url = f"http://127.0.0.1:{port}"
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    server = uvicorn.Server(uvicorn.Config(make_app(), host="127.0.0.1", port=port, log_level="warning"))
    if native:
        try:
            import webview  # pywebview: native window on macOS/Windows/Linux
        except ImportError:
            native = False
    if not native:
        if open_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Loft desktop on {url}")
        server.run()
        return
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    webview.create_window("Loft", url, width=1200, height=800, min_size=(900, 600))
    webview.start()
    server.should_exit = True
