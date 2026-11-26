"""Pigeon + proxynet: each account's browser reaches the gateway with its OWN expanded session username.

A small local gateway stands in for proxynet: it answers 407, accepts the browser's credentials, and records the username it was
given (proxynet routes by username: `country-us-session-<id>` is a sticky US exit). Only the exit-IP lookup and the geolocation are
stubbed (they need the internet); upstream's {pid}/{session} expansion and Camoufox's proxy use are the real code, run through
Pigeon's own launch path."""
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loft import engine
from loft.pigeon.runner import with_agent
from tests.conftest import FP
from tests.test_pigeon import HAVE

pytestmark = pytest.mark.skipif(not HAVE, reason="needs the real Camoufox browser")


@pytest.fixture
def gateway():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Proxy-Authorization")
            if not auth:  # a browser only sends credentials after being challenged
                self.send_response(407)
                self.send_header("Proxy-Authenticate", 'Basic realm="gateway"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            user, _, pw = base64.b64decode(auth.split()[1]).decode().partition(":")
            seen.append((user, pw, self.path))
            body = b"<html><body>via the gateway</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def test_each_pigeon_account_gets_its_own_sticky_session_from_one_gateway(mk, tmp_path, gateway, monkeypatch):
    url, seen = gateway
    monkeypatch.setattr(engine, "check_proxy", lambda proxy, **kw: dict(ok=True, ip="203.0.113.9", ipv6=False, ms=1, stable=True))
    real_opts = engine.camoufox_options
    monkeypatch.setattr(engine, "prepare_launch", lambda info, d, headless=False, seed="", log=print: dict(real_opts(info, d, headless), geoip=False))

    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    # one template-style proxy shared by every account: the username carries the per-profile placeholder
    shared = {"server": url, "username": "country-us-session-{pid}", "password": "GATEWAY_PASS"}
    pids = [owner.create_profile(tid, name=f"reddit{n}", fingerprint=FP | {"os": engine.host_os()}, proxy=dict(shared))["id"] for n in (1, 2)]

    for pid in pids:
        def go(agent):
            agent.b.page.goto("http://some-site.invalid/", timeout=30_000)
            assert "via the gateway" in agent.b.page.content()
        with_agent(owner, pid, go, headless=True, home=tmp_path / f"p{pid}")

    users = [u for u, _, _ in seen]
    assert f"country-us-session-{pids[0]}" in users and f"country-us-session-{pids[1]}" in users  # expanded per profile
    assert "country-us-session-{pid}" not in users  # the placeholder itself never reaches the gateway
    assert {pw for _, pw, _ in seen} == {"GATEWAY_PASS"}  # the shared password came through the encrypted round trip
    assert len(set(users)) == 2  # two accounts, two sessions, so two exits
    # and the stored proxy is still the template: the server never held the expanded or the clear form
    raw = owner.j("GET", f"/profiles/{pids[0]}")["proxy"]
    assert "GATEWAY_PASS" not in raw and "session" not in raw
    assert owner.profile(pids[0])["proxy"]["username"] == "country-us-session-{pid}"
