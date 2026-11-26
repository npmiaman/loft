"""Real Camoufox, headless, local HTTP page only (no external network).

Skipped unless the Camoufox browser is installed (`python -m camoufox fetch`).
"""
import http.server
import threading

import pytest

from loft.client import Client, LoftError
from loft.engine import (export_bundle, export_cookies, host_os, import_bundle, import_cookies, new_fingerprint,
                         open_profile, parse_cookies, run_profile)

try:
    from camoufox.pkgman import installed_verstr
    installed_verstr()
    HAVE = True
except Exception:
    HAVE = False

pytestmark = pytest.mark.skipif(not HAVE, reason="Camoufox browser not installed")

PROBE = """() => ({ua: navigator.userAgent, platform: navigator.platform, cores: navigator.hardwareConcurrency,
  w: screen.width, h: screen.height, tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  langs: navigator.languages.join(','), webgl: (() => { const g = document.createElement('canvas').getContext('webgl');
    const e = g && g.getExtension('WEBGL_debug_renderer_info'); return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : null })(),
  fonts: ['Arial','Helvetica Neue','Segoe UI','Calibri','Ubuntu'].map(f => document.fonts.check('12px "' + f + '"')).join(''),
  webdriver: navigator.webdriver})"""
CANVAS = """() => { const c = document.createElement('canvas'); const x = c.getContext('2d'); x.font = '16px Arial';
  x.fillText('loft fp 123', 2, 20); return c.toDataURL().slice(-40) }"""
QUIET = dict(log=lambda m: None)


@pytest.fixture
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>loft test</body></html>")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def team(server):
    owner = Client(server=server, token="")
    owner.register("owner@x.io", "password1")
    tid = owner.create_team("T")["id"]
    member = Client(server=server, token="")
    member.register("m@x.io", "password1", invite=owner.invite(tid)["code"])
    mid = [m["id"] for m in owner.members(tid) if m["email"] == "m@x.io"][0]
    owner.device, member.device = "laptop-A", "laptop-B"
    return owner, member, tid, mid


def test_two_devices_share_one_identity(team, tmp_path, site):
    owner, member, tid, mid = team
    p1 = owner.create_profile(tid, name="one", os="windows", fingerprint=new_fingerprint("windows"))["id"]
    p2 = owner.create_profile(tid, name="two", os="windows", fingerprint=new_fingerprint("windows"))["id"]
    owner.share(p1, mid, "run")
    seen = {}

    def first(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["a"] = page.evaluate(PROBE)
        seen["a_canvas"] = [page.evaluate(CANVAS), page.evaluate(CANVAS)]
        page.evaluate("() => { document.cookie = 'sess=abc123; max-age=86400'; localStorage.setItem('k', 'persisted') }")

    def second(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["b"] = page.evaluate(PROBE)
        seen["cookie"] = page.evaluate("() => document.cookie")
        seen["ls"] = page.evaluate("() => localStorage.getItem('k')")

    def other(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["c"] = page.evaluate(PROBE)

    assert run_profile(owner, p1, headless=True, session=first, home=tmp_path / "devA", **QUIET)["version"] == 1
    assert run_profile(member, p1, headless=True, session=second, home=tmp_path / "devB", **QUIET)["version"] == 2
    run_profile(owner, p2, headless=True, session=other, home=tmp_path / "devA", **QUIET)

    assert "sess=abc123" in seen["cookie"] and seen["ls"] == "persisted"
    assert seen["a"] == seen["b"], "same profile must present the same fingerprint on another device"
    assert seen["a"] != seen["c"], "two profiles should differ"
    assert seen["a_canvas"][0] != seen["a_canvas"][1], "noise mode: canvas readback is random per read"
    assert not seen["a"]["webdriver"]
    assert owner.profile(p1)["running"] is None


def test_real_canvas_is_stable_but_needs_host_os(team, tmp_path, site):
    owner, member, tid, mid = team
    host = host_os()
    p = owner.create_profile(tid, name="real", os=host, canvas="real", fingerprint=new_fingerprint(host))["id"]
    got = []

    def s(ctx):
        page = ctx.new_page()
        page.goto(site)
        got.extend([page.evaluate(CANVAS), page.evaluate(CANVAS)])

    run_profile(owner, p, headless=True, session=s, home=tmp_path / "A", **QUIET)
    run_profile(owner, p, headless=True, session=s, home=tmp_path / "B", **QUIET)
    # Measured: stable within a session; on web origins it changes per launch (cause unconfirmed),
    # so real mode doesn't link profiles across sessions either.
    assert got[0] == got[1] and got[2] == got[3]
    other = "windows" if host != "windows" else "macos"
    bad = owner.create_profile(tid, name="bad", os=other, canvas="real", fingerprint=new_fingerprint(other))["id"]
    with pytest.raises(LoftError, match="canvas 'real'"):
        run_profile(owner, bad, headless=True, session=s, home=tmp_path / "A", **QUIET)
    assert owner.profile(bad)["running"] is None  # lock released after the refusal


def test_cookie_import_export(team, tmp_path, site):
    owner, member, tid, mid = team
    p = owner.create_profile(tid, name="ck", fingerprint=new_fingerprint("windows"))["id"]
    netscape = "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t4102444800\timported\tyes\n" \
               "#HttpOnly_127.0.0.1\tFALSE\t/\tFALSE\t4102444800\thidden\th1\n"
    assert import_cookies(owner, p, parse_cookies(netscape), home=tmp_path, **QUIET)["cookies"] == 2
    seen = {}

    def s(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["js"] = page.evaluate("() => document.cookie")

    run_profile(owner, p, headless=True, session=s, home=tmp_path / "other", **QUIET)
    assert "imported=yes" in seen["js"] and "hidden" not in seen["js"]  # HttpOnly stays hidden from JS
    names = {c["name"] for c in export_cookies(owner, p, home=tmp_path, **QUIET)}
    assert {"imported", "hidden"} <= names


def test_bundle_roundtrip_with_passphrase(team, tmp_path, site):
    owner, member, tid, mid = team
    fp = new_fingerprint("linux")
    p = owner.create_profile(tid, name="src", os="linux", fingerprint=fp, proxy={"server": "http://127.0.0.1:9"},
                             tags=["x"])["id"]

    def s(ctx):
        page = ctx.new_page()
        page.goto(site)
        page.evaluate("() => localStorage.setItem('bundle', 'kept')")

    owner.patch_profile(p, clear_proxy=True)  # proxy at 127.0.0.1:9 would make the page load fail
    run_profile(owner, p, headless=True, session=s, home=tmp_path, **QUIET)
    blob = export_bundle(owner, p, passphrase="correct horse")
    assert b"bundle" not in blob
    with pytest.raises(LoftError, match="wrong passphrase"):
        import_bundle(owner, tid, blob, passphrase="nope")
    q = import_bundle(owner, tid, blob, passphrase="correct horse", name="copy")
    assert q["name"] == "copy" and q["version"] == 1 and q["tags"] == ["x"]
    assert owner.launch_info(q["id"])["fingerprint"] == fp
    seen = {}

    def chk(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["v"] = page.evaluate("() => localStorage.getItem('bundle')")

    run_profile(owner, q["id"], headless=True, session=chk, home=tmp_path / "imp", **QUIET)
    assert seen["v"] == "kept"


def test_crashing_script_still_syncs(team, tmp_path, site):
    owner, member, tid, mid = team
    p = owner.create_profile(tid, name="crash", fingerprint=new_fingerprint("windows"))["id"]
    with pytest.raises(RuntimeError):
        with open_profile(owner, p, headless=True, home=tmp_path, **QUIET) as ctx:
            page = ctx.new_page()
            page.goto(site)
            page.evaluate("() => localStorage.setItem('k', 'before-crash')")
            raise RuntimeError("script bug")
    assert owner.profile(p)["version"] == 1 and owner.profile(p)["running"] is None
