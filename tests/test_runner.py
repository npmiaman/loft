"""Cloud runner end to end: real Camoufox, websocket frames, input, sync, temp cleanup."""
import http.server
import json
import threading
import time

import pytest
import uvicorn
from websockets.sync.client import connect

from pigeon.client import Client, PigeonError
from pigeon.engine import new_fingerprint
from pigeon.runner import create_runner, start_cloud_session
from tests.conftest import _free_port
from tests.test_browser import HAVE

pytestmark = pytest.mark.skipif(not HAVE, reason="Camoufox browser not installed")

PAGE = b"""<html><body><h1>runner test</h1><input id=q autofocus style="width:300px;height:40px">
<script>document.getElementById('q').addEventListener('input', e => localStorage.setItem('typed', e.target.value))</script>
</body></html>"""


@pytest.fixture
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def runner():
    port = _free_port()
    app = create_runner("RKEY-123")
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "secret": "RKEY-123", "app": app}
    srv.should_exit = True


def wait_for(fn, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise AssertionError("timed out")


def test_cloud_session_streams_and_syncs(server, runner, site):
    owner = Client(server=server, token="")
    owner.register("o@x.io", "password1")
    tid = owner.create_team("T")["id"]
    pid = owner.create_profile(tid, name="cloud", fingerprint=new_fingerprint("windows"), start_url=site)["id"]

    with pytest.raises(PigeonError, match="bad runner key"):
        start_cloud_session(owner, pid, dict(runner, secret="wrong"))
    s = start_cloud_session(owner, pid, runner)
    sess = runner["app"].state.sessions[s["id"]]
    wait_for(lambda: sess.status == "running")
    assert owner.profile(pid)["running"]["device"] == "cloud-runner"
    home = sess.home
    assert home.exists()

    ws_url = s["view"].replace("http://", "ws://").replace("/v/", "/ws/")
    with connect(ws_url) as ws:
        frame = None
        status = None
        for _ in range(60):
            m = ws.recv(timeout=10)
            if isinstance(m, bytes):
                frame = m
            else:
                status = json.loads(m)
            if frame and status and status["tabs"]:
                break
        assert frame[:2] == b"\xff\xd8", "expected a JPEG frame"
        assert status["status"] == "running" and status["tabs"][0]["url"].startswith(site)
        ws.send(json.dumps({"t": "click", "x": 150, "y": 90}))
        ws.send(json.dumps({"t": "type", "text": "hello cloud"}))
        time.sleep(1.5)
        ws.send(json.dumps({"t": "newtab"}))
        wait_for(lambda: len(sess.tabs) == 2)

    # a wrong view token can't see or stop it
    import httpx
    assert httpx.get(runner["url"] + f"/sessions/{s['id']}/status", params={"t": "nope"}).status_code == 404
    r = httpx.delete(runner["url"] + s["view"].split(runner["url"])[1].replace("/v/", "/sessions/"), timeout=120)
    assert r.json()["status"] == "stopped", r.text
    assert not home.exists(), "decrypted profile data must be deleted from the runner"
    p = owner.profile(pid)
    assert p["version"] == 1 and p["running"] is None

    # the typed text was synced: check it from a fresh local launch
    from pigeon.engine import run_profile
    seen = {}

    def chk(ctx):
        pg = ctx.new_page()
        pg.goto(site)
        seen["typed"] = pg.evaluate("() => localStorage.getItem('typed')")

    run_profile(owner, pid, headless=True, session=chk, home=home.parent / "verify-pigeon", log=lambda m: None)
    assert seen["typed"] == "hello cloud"
