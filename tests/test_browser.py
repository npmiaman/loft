"""Real Camoufox: fingerprint stability per profile + cookie/storage sync across two devices.

Skipped unless the Camoufox binary is installed (`python -m camoufox fetch`).
Uses a local HTTP page only; no external network.
"""
import http.server
import json
import threading

import pytest

from loft.client import Client
from loft.engine import new_fingerprint, run_profile

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
  canvas: (() => { const c = document.createElement('canvas'); const x = c.getContext('2d'); x.font = '16px Arial';
    x.fillText('loft fp 123', 2, 20); return c.toDataURL().slice(-48) })(),
  webdriver: navigator.webdriver})"""


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


def test_two_devices_share_one_identity(server, tmp_path, site):
    owner = Client(server=server, token="")
    owner.register("owner@x.io", "password1")
    tid = owner.create_team("T")["id"]
    member = Client(server=server, token="")
    member.register("m@x.io", "password1", invite=owner.invite(tid)["code"])
    mid = [m["id"] for m in owner.members(tid) if m["email"] == "m@x.io"][0]
    owner.device, member.device = "laptop-A", "laptop-B"

    p1 = owner.create_profile(tid, name="one", os="windows", fingerprint=new_fingerprint("windows"))["id"]
    p2 = owner.create_profile(tid, name="two", os="windows", fingerprint=new_fingerprint("windows"))["id"]
    owner.share(p1, mid, "run")
    seen = {}

    def first(ctx):
        page = ctx.new_page()
        page.goto(site)
        seen["a"] = page.evaluate(PROBE)
        page.evaluate("() => { document.cookie = 'sess=abc123; max-age=86400'; localStorage.setItem('k', 'persisted') }")
        page.close()

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

    r1 = run_profile(owner, p1, headless=True, session=first, home=tmp_path / "devA", log=lambda m: None)
    assert r1["version"] == 1
    r2 = run_profile(member, p1, headless=True, session=second, home=tmp_path / "devB", log=lambda m: None)
    assert r2["version"] == 2
    run_profile(owner, p2, headless=True, session=other, home=tmp_path / "devA", log=lambda m: None)

    print(json.dumps(seen, indent=2))
    assert "sess=abc123" in seen["cookie"]
    assert seen["ls"] == "persisted"
    assert seen["a"] == seen["b"], "same profile must present the same fingerprint on another device"
    assert seen["a"]["ua"] != seen["c"]["ua"] or seen["a"]["canvas"] != seen["c"]["canvas"] \
        or seen["a"]["webgl"] != seen["c"]["webgl"], "two profiles should differ"
    assert not seen["a"]["webdriver"]
    assert owner.profile(p1)["running"] is None
