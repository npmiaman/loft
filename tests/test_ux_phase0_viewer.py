"""P0-9: the cloud viewer stops cleanly. It doesn't reconnect forever after the session ended or the link is wrong, it
shows a final state with a next step, it warns before closing the tab, and the runner stops (and saves) a session
nobody is watching. Uses a fake session (no Camoufox); the real-browser path is covered by tests/test_runner.py."""
import json
import time

import httpx
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from tests._viewer_support import FakeSession, Runner, chromium, wait_for


@pytest.fixture
def runner():
    r = Runner(idle_timeout=1.0, reap_every=0.1, ended_keep=2.0)
    yield r
    r.close()


def _final(ws):
    """Read messages until the server closes; return (last status message, close code)."""
    last = None
    try:
        while True:
            m = ws.recv(timeout=10)
            if isinstance(m, str):
                last = json.loads(m)
    except ConnectionClosed as e:
        return last, e.rcvd.code if e.rcvd else None


def test_unknown_link_shows_an_html_page_not_json(runner):
    r = httpx.get(f"{runner.url}/v/nope?t=bad")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "detail" not in r.text
    assert "This link has expired" in r.text and "dashboard" in r.text


def test_ended_session_link_says_why_and_what_next(runner):
    s = runner.add(FakeSession(sync_seconds=0.05))
    wait_for(lambda: s.status == "running")
    httpx.delete(f"{runner.url}/sessions/{s.id}", params={"t": s.view_token}, timeout=10)
    r = httpx.get(runner.view(s))
    assert r.status_code == 410 and r.headers["content-type"].startswith("text/html")
    assert "This session has ended" in r.text and "Your login was saved" in r.text


def test_socket_sends_a_final_status_then_closes_with_an_ended_code(runner):
    s = runner.add(FakeSession(sync_seconds=0.1))
    wait_for(lambda: s.status == "running")
    with connect(runner.ws(s)) as ws:
        ws.recv(timeout=5)
        s.reason = "stopped"
        s.stop.set()
        last, code = _final(ws)
    assert code == 4410
    assert last["status"] == "stopped" and last["reason"] == "stopped"
    # connecting to an ended session gets the same final message and code, not a silent close
    with connect(runner.ws(s)) as ws:
        last, code = _final(ws)
    assert code == 4410 and last["status"] == "stopped"


def test_errored_session_has_a_plain_message(runner):
    s = runner.add(FakeSession(fail="409: profile is running: bob@x.io on laptop"))
    wait_for(lambda: s.status == "error")
    with connect(runner.ws(s)) as ws:
        last, code = _final(ws)
    assert code == 4410 and last["status"] == "error"
    assert last["message"] == "This account is open on another device. Close it there, then start it again."


def test_unknown_session_socket_closes_4404(runner):
    with connect(f"ws://127.0.0.1:{runner.port}/ws/nope?t=x") as ws:
        _, code = _final(ws)
    assert code == 4404


def test_idle_session_is_stopped_and_saved(runner):
    s = runner.add(FakeSession(sync_seconds=0.05))
    wait_for(lambda: s.status == "running")
    wait_for(lambda: s.status == "stopped", timeout=5)
    assert s.reason == "idle"
    r = httpx.get(runner.view(s))
    assert "no one watching" in r.text


def test_watched_session_is_not_stopped_for_idleness(runner):
    s = runner.add(FakeSession())
    wait_for(lambda: s.status == "running")
    with connect(runner.ws(s)) as ws:
        end = time.time() + 2.5
        while time.time() < end:
            ws.recv(timeout=5)
        assert s.status == "running"
    # once the viewer leaves, the idle clock starts
    wait_for(lambda: s.status == "stopped", timeout=5)
    assert s.reason == "idle"


def test_ended_sessions_are_removed_after_a_while(runner):
    s = runner.add(FakeSession(sync_seconds=0.01))
    wait_for(lambda: s.status == "running")
    s.stop.set()
    wait_for(lambda: s.status == "stopped")
    wait_for(lambda: s.id not in runner.sessions, timeout=6)


def test_idle_timeout_default_is_sensible_and_configurable(monkeypatch):
    from pigeon.runner import create_runner, idle_timeout_default
    assert idle_timeout_default() == 10 * 60
    monkeypatch.setenv("PIGEON_RUNNER_IDLE_MINUTES", "3")
    assert idle_timeout_default() == 180
    assert create_runner("k").state.idle_timeout == 180


# ---------------------------------------------------------------- the viewer page itself
CHROME = chromium()
needs_chrome = pytest.mark.skipif(not CHROME, reason="no Chromium for Playwright")
COUNT_SOCKETS = """
window.__sockets = 0;
const _WS = window.WebSocket;
window.WebSocket = function (...a) { window.__sockets++; return new _WS(...a); };
window.WebSocket.prototype = _WS.prototype;
"""


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(COUNT_SOCKETS)
        p = ctx.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        yield p
        b.close()


@needs_chrome
def test_viewer_stops_reconnecting_once_the_session_ended(runner, page):
    s = runner.add(FakeSession(sync_seconds=0.05))
    page.goto(runner.view(s))
    page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
    s.reason = "stopped"
    s.stop.set()
    page.wait_for_selector("#card:has-text('This session has ended')")
    assert "Your login was saved" in page.inner_text("#card")
    n = page.evaluate("window.__sockets")
    page.wait_for_timeout(3500)
    assert page.evaluate("window.__sockets") == n, "must not reconnect after the session ended"
    assert "Reconnecting" not in page.inner_text("body")
    assert not page.errors


@needs_chrome
def test_viewer_gives_up_on_a_missing_session(runner, page):
    s = runner.add(FakeSession())
    page.goto(runner.view(s))
    page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
    del runner.sessions[s.id]  # e.g. the runner restarted
    page.evaluate("window.pigeonViewer.ws.close()")
    page.wait_for_selector("#card:has-text('This link has expired')", timeout=8000)
    n = page.evaluate("window.__sockets")
    page.wait_for_timeout(3500)
    assert page.evaluate("window.__sockets") == n
    assert not page.errors


@needs_chrome
def test_viewer_backs_off_and_counts_attempts(runner, page):
    s = runner.add(FakeSession())
    page.goto(runner.view(s))
    page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
    page.evaluate("window.pigeonViewer.ws.close()")
    page.wait_for_selector("#card:has-text('Reconnecting')")
    assert "attempt 1" in page.inner_text("#card")
    page.wait_for_function("document.querySelector('#card').hidden", timeout=8000)  # back on its own
    assert page.evaluate("window.pigeonViewer.attempts") == 0


@needs_chrome
def test_viewer_warns_before_closing_a_running_session(runner, page):
    s = runner.add(FakeSession())
    page.goto(runner.view(s))
    page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
    warned = page.evaluate("""() => { const e = new Event('beforeunload', { cancelable: true });
                                      window.dispatchEvent(e); return e.defaultPrevented; }""")
    assert warned
    s.stop.set()
    page.wait_for_selector("#card:has-text('This session has ended')")
    warned = page.evaluate("""() => { const e = new Event('beforeunload', { cancelable: true });
                                      window.dispatchEvent(e); return e.defaultPrevented; }""")
    assert not warned, "no warning once nothing is running"
