"""Shared helpers for the dashboard UX tests (tests/test_ux_*.py).

* `chromium()` finds a Playwright Chromium (headless shell) on this machine.
* `FakeApi` serves the built dashboard (pigeon/web_ui) and answers /api/* from Python, recording every call, so a UI behaviour
  can be checked in a real browser without a server, a desktop app or a launched profile. Any request can be held and
  released later (to test "the user clicks before it loaded").
* `Sandbox` starts a real `pigeon server` and a real `pigeon app --no-window` on ports 4600-4619 in a temp dir, for the
  per-phase end-to-end tests. It never touches the live dev instance (4468/4472, ~/.pigeon-server-dev, ~/.loft).
"""
from __future__ import annotations

import copy
import glob
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "pigeon" / "web_ui"
ORIGIN = "http://127.0.0.1:4619"  # never actually served: every request is answered by the route below


def chromium() -> str | None:
    if os.environ.get("PIGEON_TEST_CHROMIUM"):
        return os.environ["PIGEON_TEST_CHROMIUM"]
    found = sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"))
                   + glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")))
    return found[-1] if found else None


needs_ui = pytest.mark.skipif(not (WEB / "index.html").exists() or not chromium(),
                              reason="needs the built web UI (`npm run build` in web/) and a Playwright Chromium")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=chromium(), headless=True)
        yield b
        b.close()


# ------------------------------------------------------------------ fake data
def profile(pid=1, name="Reddit 1", **kw):
    return dict(dict(id=pid, name=name, os="macos", canvas="real", tags=[], notes="", version=1, access="edit", start_url="",
                     proxy={"server": "http://1.2.3.4:8080", "username": "u"}, running=None, running_here=False), **kw)


def lead(username="alice", status="draft", message="Hey alice, saved text", fit=88, **kw):
    return dict(dict(username=username, subreddit="saas", fit=fit, status=status, reason="asks for help", message=message,
                     post_url="/r/saas/comments/p1/t1/", post_title="t", snippet="s", intent="asking", source="browse",
                     created_at=1.0, error=None), **kw)


def status(**kw):
    return dict(dict(state="idle", job=None, mode=None, since=None, last=None, error=None, central=True, product="Acme",
                     stats=dict(scanned=5, leads=2, upvotes=1, dms=1, errors=0), daily=dict(count=1, date="x", next_dm_at=0, paused_until=0),
                     leads={"draft": 1}, log=[]), **kw)


SETTINGS = dict(model="deepseek-flash", my_username="me", daily_dm_cap=40, warmup=True, warmup_start=0.0, active_start=9, active_end=21,
                min_gap_sec=60, max_gap_sec=180, pace=1.0, min_fit=70, session_minutes=30, upvotes=True, max_upvotes_per_session=10,
                dm_mode="ai", approve_ai_drafts=True, ai_guidance="", use_team_setup=True, auto_check_replies=True, posts_per_day=1)


def state(**kw):
    s = dict(signed_in=True, server="http://srv.test", browser=True, host_os="macos", jobs={}, device="laptop",
             me=dict(id=1, email="owner@x.io", name="Owner", teams=[dict(id=1, name="Agency", role="owner")]), team=1, role="owner",
             has_key=True, profiles=[profile()], members=[dict(id=1, name="Owner", email="owner@x.io", role="owner")],
             templates=[], runners=[])
    s.update(kw)
    return s


class FakeApi:
    """Answers the dashboard's API. `routes` maps "METHOD /path" (regex) to a dict or a callable(body, match) -> dict | (status, dict)."""

    def __init__(self, st: dict | None = None):
        self.st = st or state()
        self.status = status()
        self.leads = [lead()]
        self.settings = copy.deepcopy(SETTINGS)
        self.ops: dict = {}
        self.routes: dict = {}
        self.calls: list[tuple[str, str, object]] = []
        self.hold: set[str] = set()   # "METHOD /path" regexes to hold until release()
        self.held: list = []

    # -- what the tests look at
    def called(self, method: str, path: str) -> list:
        return [b for m, p, b in self.calls if m == method and re.fullmatch(path, p)]

    def ops_called(self, name: str) -> list:
        return [b["args"] for b in self.called("POST", r"/profiles/\d+/pigeon/op") if b and b.get("op") == name]

    def jobs_called(self, kind: str) -> list:
        return [b["args"] for b in self.called("POST", r"/profiles/\d+/pigeon/job") if b and b.get("kind") == kind]

    def release(self):
        held, self.held = self.held, []
        for route, req in held:
            self._answer(route, req)

    # -- answering
    def _default_op(self, name: str, args: dict):
        if name == "leads_list":
            return dict(leads=self.leads, out="")
        if name == "settings_set":
            for k, v in (args.get("pairs") or {}).items():
                self.settings[k] = v
            return dict(settings=self.settings, out="")
        return dict(opportunities=[], ideas=[], posted=[], template="Hi {name}", undo=False, product=None, subreddits=[], out="")

    def answer(self, method: str, path: str, body):
        for pat, fn in self.routes.items():
            m = re.fullmatch(pat, f"{method} {path}")
            if m:
                out = fn(body, m) if callable(fn) else fn
                return out if isinstance(out, tuple) else (200, out)
        if path == "/state":
            return 200, self.st
        if re.fullmatch(r"/profiles/\d+/pigeon/status", path):
            return 200, self.status
        if re.fullmatch(r"/profiles/\d+/pigeon/op", path):
            name, args = body.get("op"), body.get("args") or {}
            fn = self.ops.get(name)
            return 200, (fn(args) if callable(fn) else fn) if fn is not None else self._default_op(name, args)
        if path == "/pigeon/brain":
            return 200, dict(summary=dict(days=14, by_kind={}, stuck_claims=0, by_subreddit=[], by_profile=[]), accounts=[],
                             team_setup=dict(version=0, product=None, subreddits=0, limits={}), events=[])
        if path == "/pigeon/key" and method == "GET":
            return 200, dict(set=False, hint="")
        return 200, {"ok": True}

    def _answer(self, route, req):
        path = urlparse(req.url).path[len("/api"):]
        body = None
        if req.post_data:
            try:
                body = json.loads(req.post_data)
            except ValueError:
                body = req.post_data
        code, out = self.answer(req.method, path, body)
        route.fulfill(status=code, content_type="application/json", body=json.dumps(out))

    def handle(self, route, req):
        u = urlparse(req.url)
        if u.path.startswith("/api/"):
            path = u.path[len("/api"):]
            body = None
            if req.post_data:
                try:
                    body = json.loads(req.post_data)
                except ValueError:
                    body = req.post_data
            if req.method != "GET" or path != "/state":
                self.calls.append((req.method, path, body))
            if any(re.fullmatch(h, f"{req.method} {path}") for h in self.hold):
                self.held.append((route, req))
                return
            return self._answer(route, req)
        if u.netloc != urlparse(ORIGIN).netloc:
            return route.fulfill(status=200, body="")  # never reach out to the internet
        f = WEB / (u.path.lstrip("/") or "index.html")
        if f.is_dir():
            f = f / "index.html"
        if not f.exists():
            return route.fulfill(status=404, body="")
        route.fulfill(status=200, body=f.read_bytes(), content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream")


def open_ui(browser, api: FakeApi, *, width=1280, height=900, dark=False, hash=""):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark" if dark else "light")
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.add_init_script("window.__opened = []; window.open = (u, ...r) => { const w = { location: { href: u || '' }, close() { w.closed = true; }, closed: false }; window.__opened.push(w); return w; };")
    page.route("**/*", api.handle)
    page.goto(ORIGIN + "/" + (("#" + hash) if hash else ""))
    return page


SETTINGS_WIN = "[role=dialog]:has(nav[aria-label='Settings sections'])"  # the Settings window (SETTINGS above is the fake agent settings)


def open_settings(page, section: str | None = None):
    """Settings the way a person gets there: the account menu at the bottom of the sidebar (inside the menu on phones). It opens as a
    floating window; `section` clicks that section's tab."""
    if not page.is_visible(SETTINGS_WIN):
        if page.is_visible("button[aria-label='Menu']"):
            page.click("button[aria-label='Menu']")
        page.click("button[aria-label='Account menu'] >> visible=true")
        page.click("[role=menuitem]:has-text('Settings')")
        page.wait_for_selector(SETTINGS_WIN)
    if section:
        page.click(f"{SETTINGS_WIN} [role=tab]:has-text('{section}')")
        page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section]:visible h2:text-is('{section}')")
        page.wait_for_function("document.querySelectorAll('[data-settings-section]').length === 1")  # the previous pane lingers for a moment


def close_settings(page):
    page.keyboard.press("Escape")
    page.wait_for_selector(SETTINGS_WIN, state="detached")


def open_drawer(page, account="Reddit 1"):
    page.click("button:text-is('Agent controls')")
    page.wait_for_selector("#pg h3")


# ------------------------------------------------------------------ real sandbox: server + desktop app
def _port_free(p: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _wait(url: str, timeout=60):
    import httpx
    t = time.time() + timeout
    while time.time() < t:
        try:
            httpx.get(url, timeout=2, headers={"X-Pigeon": "1"})
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"{url} never came up")


class Sandbox:
    """A real Pigeon server + desktop app, each in its own process, on free ports in 4600-4619, data in a temp dir."""

    def __init__(self):
        ports = [p for p in range(4600, 4619) if _port_free(p)]
        if len(ports) < 2:
            pytest.skip("no free ports in 4600-4618")
        self.srv_port, self.app_port = ports[0], ports[1]
        self.dir = Path(tempfile.mkdtemp(prefix="pigeon-ux-"))
        self.home = self.dir / "home"
        self.home.mkdir()
        env = dict(os.environ, PIGEON_HOME=str(self.home), PIGEON_HEADLESS="1", PYTHONUNBUFFERED="1")
        env.pop("DEEPSEEK_API_KEY", None)
        self.env = env
        self.logs = [open(self.dir / "server.log", "w"), open(self.dir / "app.log", "w")]
        self.srv = subprocess.Popen(["uv", "run", "pigeon", "server", "--host", "127.0.0.1", "--port", str(self.srv_port),
                                     "--data", str(self.dir / "srv")], cwd=ROOT, env=env, stdout=self.logs[0], stderr=subprocess.STDOUT,
                                    start_new_session=True)
        self.app = subprocess.Popen(["uv", "run", "pigeon", "app", "--no-window", "--port", str(self.app_port)], cwd=ROOT, env=env,
                                    stdout=self.logs[1], stderr=subprocess.STDOUT, start_new_session=True)
        self.server_url = f"http://127.0.0.1:{self.srv_port}"
        (self.home / "config.json").write_text(json.dumps({"server": self.server_url}))  # like a normal install: the server is known
        self.url = f"http://127.0.0.1:{self.app_port}"
        _wait(self.server_url)
        _wait(self.url + "/api/state")

    def close(self):
        import signal
        for f in (self.home / "profiles").glob("*/control.json"):  # any account browser the app launched
            try:
                os.kill(json.loads(f.read_text())["os_pid"], signal.SIGTERM)
            except (OSError, ValueError, KeyError):
                pass
        for p in (self.app, self.srv):
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
        for p in (self.app, self.srv):
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        for f in self.logs:
            f.close()
        d = str(self.dir)
        assert d.startswith(tempfile.gettempdir()) or "/pigeon-ux-" in d
        for _ in range(5):  # a closing account browser can still write a file or two just after it is told to stop
            shutil.rmtree(d, ignore_errors=True)
            time.sleep(1)
            if not os.path.exists(d):
                break


@pytest.fixture(scope="module")
def sandbox():
    sb = Sandbox()
    yield sb
    sb.close()
