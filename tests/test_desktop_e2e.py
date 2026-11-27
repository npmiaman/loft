"""The real desktop app, end to end: real backend, real Loft server, a profile it launches itself in a headless Camoufox
subprocess, and the Pigeon panel driven in a browser. (test_ui_pigeon.py checks the front end against a stubbed API.)"""
import json
import os
import signal
import socket
import threading
import time

import pytest
import uvicorn

import loft.client as lc
import loft.control as control
import loft.engine as le
from loft.desktop import make_app
from tests.test_pigeon import HAVE, browser  # noqa: F401

pytestmark = pytest.mark.skipif(not HAVE, reason="needs the real Camoufox browser (the desktop app launches profiles with it)")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def desktop(tmp_path, monkeypatch, server):
    home = tmp_path / "home"
    monkeypatch.setattr(lc, "HOME", home)
    monkeypatch.setattr(lc, "CONFIG", home / "config.json")
    monkeypatch.setattr(le, "PROFILES", home / "profiles")
    monkeypatch.setattr(control, "PROFILES", home / "profiles")
    monkeypatch.setenv("LOFT_HOME", str(home))  # the profile subprocess the app starts finds the same config and profiles
    monkeypatch.setenv("LOFT_HEADLESS", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(make_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", server, home
    # stop any profile process the app launched
    for f in (home / "profiles").glob("*/control.json"):
        try:
            os.kill(json.loads(f.read_text())["os_pid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass
    srv.should_exit = True


def test_the_real_app_creates_launches_and_drives_pigeon(desktop, browser):
    url, loft_server, home = desktop
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(url)

    # sign up, create a team, create a profile with the canvas choice, all through the UI
    page.fill("#srv", loft_server)
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.click("button:text-is('Add account')")
    page.fill("#n_name", "Client A")
    page.click("#n_go")
    page.wait_for_selector("td:has-text('Client A')", timeout=60000)

    # the app launches the profile as its own subprocess; once that session's control channel is up, the panel appears
    page.click("button:text-is('Open browser')")
    page.wait_for_selector("text=Open on this computer", timeout=60000)
    page.wait_for_selector("button:text-is('Pigeon controls')", timeout=120000)
    page.click("button:text-is('Pigeon controls')")
    page.wait_for_selector("#pg h3")

    def panel():
        page.wait_for_timeout(500)
        return page.inner_text("#pg")

    assert "team history: on" in panel() and "No leads here" in panel()

    # Leads: the List DMer, end to end through the desktop backend, the control channel and the session's state
    page.fill("#pgimp", "spez\nu/example_user\nnot valid!")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("text=imported 2 username(s)")
    assert "u/spez" in panel() and "u/example_user" in panel()

    # Messages: save a template and it comes back
    page.click("#pg button:text-is('Messages')")
    page.wait_for_selector("#pgtpl")
    page.fill("#pgtpl", "Hi {name}, quick question about r/{subreddit}?")
    page.click("#pg button:text-is('Save')")
    page.wait_for_selector("#pg pre.log:text-is('saved')")  # the save has finished, not just some "saved" on the page
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Messages')")
    # the panel re-reads the server on every tab switch; wait for the settled value rather than a transient render
    page.wait_for_function("document.querySelector('#pgtpl') && document.querySelector('#pgtpl').value === 'Hi {name}, quick question about r/{subreddit}?'", timeout=15000)

    # Settings: change a setting and it persists
    page.click("#pg button:text-is('Settings')")
    page.wait_for_selector("input[data-k='daily_dm_cap']")
    page.fill("input[data-k='daily_dm_cap']", "7")
    page.click("#pg button:text-is('Save settings')")
    page.wait_for_selector("#pg pre.log:text-is('saved')")  # the save has finished, not just some "saved" on the page
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Settings')")
    page.wait_for_function("document.querySelector(\"input[data-k='daily_dm_cap']\") && document.querySelector(\"input[data-k='daily_dm_cap']\").value === '7'", timeout=15000)

    # Run: with no DeepSeek key or product, the refusal reaches the user instead of failing silently
    page.click("#pg button:text-is('Run')")
    page.wait_for_selector("#pgmode")
    page.click("#pg button:text-is('▶ Start')")
    page.wait_for_selector("#pg .err")
    assert any(w in page.inner_text("#pg .err") for w in ("DeepSeek key", "Set up the product"))

    # Brain and Setup tabs render from the live server
    page.click("#pg button:text-is('Team activity')")
    page.wait_for_selector("text=Team memory")
    page.click("#pg button:text-is('Product')")
    page.wait_for_selector("button:text-is('Analyze my product')")

    assert errors == [], errors
    page.close()
