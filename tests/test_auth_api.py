"""Sign-in / sign-up API behaviour behind the auth page: honest errors, and a way for the page to know whether a server is new.

Run:  uv run pytest tests/test_auth_api.py
"""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import pigeon.client as lc
from pigeon.client import PigeonError
from pigeon.desktop import make_app


# ---------------------------------------------------------------- server
def test_existing_email_is_reported_before_the_invite_rule(mk):
    mk().register("a@x.io", "password1")  # first user
    with pytest.raises(PigeonError) as e:
        mk().register("A@x.io", "password2")  # same email, no invite
    assert e.value.status == 409 and "already registered" in e.value.detail


def test_auth_status_says_whether_the_server_has_accounts(server, mk):
    assert httpx.get(server + "/auth/status").json()["accounts"] is False
    mk().register("a@x.io", "password1")
    assert httpx.get(server + "/auth/status").json()["accounts"] is True


# ---------------------------------------------------------------- desktop app
@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    return t, server


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def html_site():
    """Any website that isn't a Pigeon server: answers every path with an HTML page."""
    site = FastAPI()

    @site.api_route("/{path:path}", methods=["GET", "POST"], response_class=HTMLResponse)
    def page(path: str):
        return "<!doctype html><html><head><title>Example Domain</title></head><body><h1>Example Domain</h1></body></html>"
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(site, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


def test_desktop_auth_status(app):
    t, server = app
    assert t.get("/api/auth/status", params={"server": server}).json() == {"reachable": True, "accounts": False, "server": server}
    t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True})
    assert t.get("/api/auth/status").json()["accounts"] is True, "with no server given it checks the saved one"
    down = t.get("/api/auth/status", params={"server": f"http://127.0.0.1:{_free_port()}"}).json()
    assert down["reachable"] is False and "<" not in down.get("error", "")


@pytest.mark.parametrize("bad,status,says", [
    ("unreachable", 502, "Couldn't reach that server"),
    ("not a url", 400, "isn't a valid server address"),
    ("html", 502, "doesn't look like a Pigeon server"),
])
def test_login_errors_for_a_wrong_server_are_plain(app, html_site, bad, status, says):
    t, _ = app
    server = {"unreachable": f"http://127.0.0.1:{_free_port()}", "html": html_site}.get(bad, bad)
    for signup in (False, True):
        r = t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": signup})
        assert r.status_code == status, r.text
        detail = r.json()["detail"]
        assert says in detail and "<" not in detail and "Internal Server Error" not in detail, detail


def test_user_can_change_their_name(mk, app):
    c = mk()
    c.register("a@x.io", "password1", "Al")
    assert c.set_name("  Alice Smith ")["name"] == "Alice Smith"
    assert c.me()["name"] == "Alice Smith"
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(PigeonError) as e:
            c.set_name(bad)
        assert e.value.status == 400
    t, server = app  # and through the desktop app
    assert t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1"}).status_code == 200
    assert t.patch("/api/me", json={"name": "Bea"}).json()["name"] == "Bea"
    assert t.get("/api/state").json()["me"]["name"] == "Bea"
