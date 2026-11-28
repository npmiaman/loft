"""Desktop app: local web UI + local automation API, bound to 127.0.0.1 only.

* /api/*  - the UI's own API (needs the X-Pigeon header, which other websites can't send).
* /v1/*   - automation API for scripts (needs the API key from `pigeon apikey`).

Each launched profile runs as its own `pigeon run` subprocess so a browser crash never
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
from .client import Client, PigeonError, load_config, save_config

UI = Path(__file__).with_name("ui.html")
WEB = Path(__file__).with_name("web_ui")  # the Next.js app (a static export): `npm run build` in web/ copies it here


def api_key() -> str:
    cfg = load_config()
    if not cfg.get("api_key"):
        cfg["api_key"] = "pigeon_" + secrets.token_urlsafe(24)
        save_config(cfg)
    return cfg["api_key"]


def self_cmd(*args: str) -> list[str]:
    """Command that runs this program's CLI, for both source installs and frozen desktop builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "pigeon.cli", *args]


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
    proxy_name: str = ""


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
    proxy_name: Optional[str] = None  # a label for the proxy, so lists never need to show its address
    start_url: Optional[str] = None
    notes: Optional[str] = None
    canvas: Optional[str] = None
    tags: Optional[list[str]] = None  # None leaves them as they are


class TemplateIn(BaseModel):
    name: str
    os: str = "windows"
    proxy: str = ""
    start_url: str = ""
    tags: list[str] = []
    canvas: str = "noise"


def reddit_signed_in(data_dir: Path) -> Optional[bool]:
    """Is this account's browser signed in to Reddit? Read straight from its saved Firefox cookie store, without opening a browser.
    True/False, or None when this computer has no copy of the account's browser data (or it can't be read).
    Only `reddit_session` counts: Reddit also sets token_v2/loid for logged-out visitors."""
    import sqlite3
    import time
    db = Path(data_dir) / "cookies.sqlite"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)  # immutable: readable even while the browser holds a lock
        try:
            row = con.execute(
                "SELECT 1 FROM moz_cookies WHERE (host = 'reddit.com' OR host LIKE '%.reddit.com') AND name = 'reddit_session' "
                "AND (CASE WHEN expiry > 100000000000 THEN expiry / 1000 ELSE expiry END) > ? LIMIT 1", (time.time(),)).fetchone()
        finally:
            con.close()
        return row is not None
    except sqlite3.Error:
        return None


def make_app() -> FastAPI:
    app = FastAPI()
    jobs = Jobs()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "bad host"}, 403)
        path = request.url.path
        if path.startswith("/api/") and request.headers.get("x-pigeon") != "1" and request.headers.get("x-loft") != "1":  # x-loft: the old name
            return JSONResponse({"detail": "missing X-Pigeon header"}, 403)
        if path.startswith("/v1/"):
            k = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not k or not secrets.compare_digest(k, api_key()):
                return JSONResponse({"detail": "bad API key (see `pigeon apikey`)"}, 401)
        try:
            return await call_next(request)
        except PigeonError as e:
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
        proxy = parse_proxy(body.proxy or None)
        if proxy and body.proxy_name.strip():
            proxy["name"] = body.proxy_name.strip()[:60]  # kept inside the encrypted proxy; the launcher only passes server/user/password
        return c.create_profile(tid, name=body.name, os=os_name, proxy=proxy,
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
        if body.tags is not None:
            f["tags"] = list(dict.fromkeys(t.strip() for t in body.tags if t.strip()))
        label = body.proxy_name.strip()[:60] if body.proxy_name is not None else None
        if body.proxy is not None:
            if body.proxy.strip():
                f["proxy"] = parse_proxy(body.proxy)
                if label is None:  # a new address keeps the old label unless one is given
                    label = ((c.profile(pid).get("proxy") or {}).get("name") or "")
                if label:
                    f["proxy"]["name"] = label
            else:
                f["clear_proxy"] = True
        elif label is not None:  # renaming the proxy only: keep its address
            cur = dict(c.profile(pid).get("proxy") or {})
            if cur:
                if label:
                    cur["name"] = label
                else:
                    cur.pop("name", None)
                f["proxy"] = cur
        return c.patch_profile(pid, **f)

    reddit_users: dict[int, tuple[float, dict]] = {}  # team -> (when fetched, {profile id: Reddit username the agent registered})

    def registered_users(c: Client, tid: int) -> dict:
        import time
        hit = reddit_users.get(tid)
        if hit and time.time() - hit[0] < 30:  # the page polls every 3s; this changes rarely
            return hit[1]
        try:
            m = {a["profile_id"]: a["username"] for a in c.pigeon_accounts(tid) if a.get("profile_id")}
        except PigeonError:
            m = hit[1] if hit else {}
        reddit_users[tid] = (time.time(), m)
        return m

    def with_local(ps: list, users: Optional[dict] = None) -> list:
        from . import engine
        for p in ps:
            p["running_here"] = jobs.alive(f"run-{p['id']}")
            p["reddit"] = {"signed_in": reddit_signed_in(engine.PROFILES / str(p["id"]) / "data"), "user": (users or {}).get(p["id"])}
        return ps

    # ------------------------------------------------------------ UI
    @app.get("/", response_class=HTMLResponse)
    def index():
        page = WEB / "index.html"  # the new UI when it has been built; the classic page otherwise
        return (page if page.exists() else UI).read_text(encoding="utf-8")

    @app.get("/legacy", response_class=HTMLResponse)
    @app.get("/legacy/", response_class=HTMLResponse)
    def legacy():
        return UI.read_text(encoding="utf-8")

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
                except PigeonError:
                    pass  # still starting up, or already closing
        base = {"server": cfg.get("server"), "browser": browser_installed(), "jobs": live, "host_os": host_os()}
        if not cfg.get("token"):
            return {**base, "signed_in": False}
        c = Client(persist=True)
        try:
            me = c.me()
        except PigeonError as e:
            if e.status == 401:
                return {**base, "signed_in": False}
            raise
        out: dict[str, Any] = {**base, "signed_in": True, "server": c.server, "me": me, "device": c.device}
        if me["teams"]:
            tid = team(c)
            out.update(team=tid, role=next(t["role"] for t in me["teams"] if t["id"] == tid),
                       has_key=tid in c.keys or _try_key(c, tid))
            if out["has_key"]:
                out.update(profiles=with_local(c.profiles(tid), registered_users(c, tid)), members=c.members(tid),
                           templates=c.templates(tid), runners=[{"id": r["id"], "name": r["name"], "url": r["url"]}
                                                                for r in c.runners(tid)])
        return out

    def probe_server(server: Optional[str], ca: Optional[str] = None) -> dict:
        """Is `server` (or the saved one) a reachable Pigeon server, and does it have accounts yet? Raises HTTPException with a
        sentence a person can act on: never a stack trace, never someone else's HTML page."""
        import re

        import httpx
        cfg = load_config()
        url = ((server or "").strip() or cfg.get("server") or "http://127.0.0.1:4400").rstrip("/")
        if not re.match(r"^https?://[^\s/:]+(:\d+)?(/\S*)?$", url):
            raise HTTPException(400, "That isn't a valid server address. It should look like https://pigeon.yourcompany.com.")
        verify: Any = ca or cfg.get("ca") or True
        unreachable = "Couldn't reach that server. Check the address and that the server is running."
        not_pigeon = "That address doesn't look like a Pigeon server. Check it with whoever runs your team's server."
        try:
            r = httpx.get(url + "/auth/status", timeout=8, verify=verify)
            if r.status_code == 404:  # an older Pigeon server without /auth/status
                h = httpx.get(url + "/health", timeout=8, verify=verify)
                if h.headers.get("content-type", "").startswith("application/json") and h.json().get("ok"):
                    return {"server": url, "accounts": None}
                raise HTTPException(502, not_pigeon)
        except httpx.HTTPError:
            raise HTTPException(502, unreachable)
        try:
            j = r.json()
        except ValueError:
            raise HTTPException(502, not_pigeon)
        if not isinstance(j, dict) or "accounts" not in j:
            raise HTTPException(502, not_pigeon)
        return {"server": url, "accounts": bool(j["accounts"])}

    @app.get("/api/auth/status")
    def auth_status(server: Optional[str] = None):
        """For the sign-in page: open on "create the first account" for a new server; explain a bad address in plain words."""
        try:
            return {"reachable": True, **probe_server(server)}
        except HTTPException as e:
            cfg = load_config()
            return {"reachable": False, "error": e.detail, "server": ((server or "").strip() or cfg.get("server") or "")}

    @app.post("/api/login")
    def login(body: LoginIn):
        ca = None
        if body.ca and body.ca.strip():
            from .client import HOME
            HOME.mkdir(parents=True, exist_ok=True)
            ca = str(HOME / "server-ca.pem")
            Path(ca).write_text(body.ca.strip() + "\n")
        probe_server(body.server, ca)  # a wrong address fails here, with a plain reason
        c = Client(server=body.server, token="", persist=True, ca=ca)
        out = c.register(body.email, body.password, body.name, body.invite or None) if body.signup \
            else c.login(body.email, body.password)
        cfg = load_config()
        cfg["team"] = out.get("team_id")
        save_config(cfg)
        return {"ok": True}

    @app.patch("/api/me")
    def set_my_name(body: dict):
        return client().set_name(str(body.get("name") or ""))

    @app.post("/api/logout")
    def logout():
        cfg = load_config()
        for k in ("token", "vault_key", "keys"):
            cfg.pop(k, None)
        save_config(cfg)
        return {"ok": True}

    @app.post("/api/teams")
    def new_team(body: dict):
        c = client()
        t = c.create_team(body["name"])
        set_team(t["id"])
        # One browser profile to start with, so first run is "sign in to Reddit", not a form. Only one: more is the user's choice.
        new_profile(c, t["id"], NewProfile(name="My Reddit account", start_url="https://www.reddit.com/login"))
        return t

    @app.post("/api/team/{tid}")
    def use_team(tid: int):
        set_team(tid)
        return {"ok": True}

    @app.post("/api/join")
    def join(body: dict):
        c = client()
        code = str(body.get("code") or "").strip()
        try:
            out = c.accept(code)
        except PigeonError as e:
            if e.status != 409:
                raise
            out = _adopt_key(c, code)  # already a member, but this device has no key: the code is used for its key part
        set_team(out["team_id"])
        return out

    def _adopt_key(c: Client, code: str) -> dict:
        from .client import _split_invite
        try:
            _, key = _split_invite(code)
        except Exception:
            key = None
        if not key:
            raise HTTPException(400, "That code has no key part. Paste the whole invite code.")
        tid = team(c)
        if _try_key(c, tid):
            return {"team_id": tid, "role": None}
        epoch = next(t["key_epoch"] for t in c.me()["teams"] if t["id"] == tid)
        from . import crypto
        ps = c.j("GET", f"/teams/{tid}/profiles")
        # Decrypting one of the team's accounts proves the key is this team's. A member who can see no accounts has nothing to
        # check against; a wrong key then only means their accounts still won't open, and a new code fixes it.
        if ps:
            fp = c.j("GET", f"/profiles/{ps[0]['id']}/launch").get("fingerprint")
            try:
                crypto.dec_text(key, fp, f"fp:{tid}")
            except Exception:
                raise HTTPException(400, "That code's key doesn't unlock this team's accounts. Ask for a new invite code.")
        c._store_key(tid, key, epoch)
        return {"team_id": tid, "role": None}

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
    def cloud(pid: int, body: Optional[dict] = None):
        body = body or {}
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

    @app.post("/api/proxy/test")
    def test_proxy(body: dict):
        """Try a proxy before saving it: the exit IP sites will see, and how fast it answers. Errors in plain words."""
        from . import engine
        proxy = parse_proxy(str(body.get("proxy") or "").strip() or None)
        if not proxy:
            raise HTTPException(400, "Enter the proxy's host and port.")
        r = engine.check_proxy(proxy, timeout=10, samples=2)
        if r.get("ok"):
            return {k: r[k] for k in ("ok", "ip", "ms", "stable") if k in r} | ({"warning": "The exit IP changes between connections, so sites would see different IPs."} if not r.get("stable", True) else {})
        err = r.get("error", "")
        if "407" in err or "auth" in err.lower():
            msg = "The proxy rejected the username or password."
        elif "timeout" in err.lower():
            msg = "The proxy didn't answer in time. Check the host and port, or try again."
        else:
            msg = "Couldn't connect through this proxy. Check the host, port, username and password."
        return {"ok": False, "error": msg}

    @app.get("/api/profiles/{pid}/proxy")
    def get_proxy(pid: int):
        p = client().profile(pid).get("proxy")
        return {"proxy": proxy_url(p), "name": (p or {}).get("name", "")}

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
                        headers={"Content-Disposition": f'attachment; filename="{name}.pigeonprofile"'})

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

    # ------------------------------------------------------------ Pigeon (see pigeon/agent)
    def _session(pid: int, method: str, path: str, body: dict | None = None, timeout: float = 130) -> dict:
        try:
            return control.request(pid, method, path, body, timeout=timeout)
        except PigeonError as e:
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

    # The AI (DeepSeek) key lives only on this machine (pigeon/agent/state.KEYFILE, never synced). It is never sent back
    # in full: the UI only learns whether one is set and its last four characters.
    @app.get("/api/pigeon/key")
    def pigeon_key():
        from .agent.state import load_key
        key, _ = load_key()
        return {"set": bool(key), "hint": f"…{key[-4:]}" if len(key) >= 8 else ("set" if key else "")}

    @app.post("/api/pigeon/key")
    def pigeon_key_set(body: dict):
        from .agent.state import save_key
        key = str(body.get("key") or "").strip()
        if not key:
            raise HTTPException(400, "Paste the key first.")
        if len(key) < 8 or any(ch.isspace() for ch in key):
            raise HTTPException(400, "That doesn't look like an API key.")
        save_key(key)
        return {"ok": True}

    @app.delete("/api/pigeon/key")
    def pigeon_key_clear():
        from .agent.state import save_key
        save_key("")
        return {"ok": True}

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
                        headers={"Content-Disposition": 'attachment; filename="pigeon-team-backup.pigeonbackup"'})

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
    def get_api_key(request: Request):
        return {"key": api_key(), "base": f"http://{request.headers.get('host') or '127.0.0.1:4410'}/v1"}  # the port this app is on

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

    if (WEB / "_next").is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/_next", StaticFiles(directory=WEB / "_next"), name="next-assets")
    return app


def _try_key(c: Client, tid: int) -> bool:
    try:
        c.team_key(tid)
        return True
    except PigeonError:
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
        print(f"Pigeon desktop on {url}")
        server.run()
        return
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    webview.create_window("Pigeon", url, width=1200, height=800, min_size=(900, 600))
    webview.start()
    server.should_exit = True
