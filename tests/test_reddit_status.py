"""Is each account signed in to Reddit? Read from the account's saved browser cookies on this computer (no browser needed), plus the
Reddit username the agent registers with the team.

Run:  uv run pytest tests/test_reddit_status.py   (UI part needs `cd web && npm run build` first)
"""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

import pigeon.client as lc
import pigeon.engine as le
from pigeon.desktop import make_app, reddit_signed_in
from tests.conftest import FP
from tests.ux_harness import FakeApi, browser, needs_ui, open_ui, profile, state  # noqa: F401


def cookie_db(path, rows):
    """A minimal Firefox cookies.sqlite: (host, name, expiry) rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, host TEXT, name TEXT, value TEXT, expiry INTEGER)")
    con.executemany("INSERT INTO moz_cookies(host,name,value,expiry) VALUES(?,?,'x',?)", rows)
    con.commit()
    con.close()


def test_reddit_signed_in_from_cookies(tmp_path):
    later, earlier = int(time.time()) + 86400, int(time.time()) - 86400
    cases = {
        "signed_in": [(".reddit.com", "reddit_session", later)],
        "signed_in_ms": [(".reddit.com", "reddit_session", later * 1000)],  # newer Firefox stores milliseconds
        "expired": [(".reddit.com", "reddit_session", earlier)],
        "expired_ms": [(".reddit.com", "reddit_session", earlier * 1000)],
        "anonymous": [(".reddit.com", "token_v2", later), (".reddit.com", "loid", later)],  # Reddit sets these for logged-out visitors too
        "other_site": [(".example.com", "reddit_session", later)],
    }
    want = {"signed_in": True, "signed_in_ms": True, "expired": False, "expired_ms": False, "anonymous": False, "other_site": False}
    for name, rows in cases.items():
        cookie_db(tmp_path / name / "data" / "cookies.sqlite", rows)
        assert reddit_signed_in(tmp_path / name / "data") is want[name], name
    assert reddit_signed_in(tmp_path / "never_opened" / "data") is None, "no local data: unknown, not 'signed out'"
    (tmp_path / "broken" / "data").mkdir(parents=True)
    (tmp_path / "broken" / "data" / "cookies.sqlite").write_text("not a database")
    assert reddit_signed_in(tmp_path / "broken" / "data") is None


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "PROFILES", tmp_path / "home" / "profiles")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    return t, server, tmp_path / "home" / "profiles"


def test_state_reports_reddit_status_per_account(app):
    t, server, profiles = app
    assert t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True}).status_code == 200
    t.post("/api/teams", json={"name": "T"})
    ids = [t.post("/api/profiles", json={"name": n, "os": "windows"}).json()["id"] for n in ("A", "B", "C")]
    cookie_db(profiles / str(ids[0]) / "data" / "cookies.sqlite", [(".reddit.com", "reddit_session", int(time.time()) + 3600)])
    cookie_db(profiles / str(ids[1]) / "data" / "cookies.sqlite", [(".reddit.com", "token_v2", int(time.time()) + 3600)])
    c = lc.Client(persist=True)
    c.pigeon_set_account(c.me()["teams"][0]["id"], ids[0], "alice")  # what the agent registers once it runs (read is cached 30s)
    got = {p["name"]: p["reddit"] for p in t.get("/api/state").json()["profiles"]}
    assert got["A"] == {"signed_in": True, "user": "alice"}
    assert got["B"] == {"signed_in": False, "user": None}
    assert got["C"] == {"signed_in": None, "user": None}


# ---------------------------------------------------------------- the account list shows it
@needs_ui
def test_rows_show_reddit_status(browser):
    ps = [profile(1, "Alpha", version=3, reddit={"signed_in": True, "user": "alice"}),
          profile(2, "Beta", version=2, reddit={"signed_in": False, "user": None}),
          profile(3, "Gamma", version=0, reddit={"signed_in": None, "user": None}),
          profile(4, "Delta", version=4, reddit={"signed_in": None, "user": "dora"})]
    page = open_ui(browser, FakeApi(state(profiles=ps)))
    row = lambda n: page.inner_text(f"[data-account='{n}'] [data-reddit]")
    assert row("Alpha") == "u/alice"  # with a tick (see test_account_row.py)
    assert row("Beta") == "Not connected"  # with a cross, under the Reddit column
    assert row("Gamma") == "Not connected", "a never-opened account isn't signed in yet"
    assert row("Delta") == "u/dora", "known from the agent even if this computer has no copy"
    assert page.get_attribute("[data-account='Alpha'] [data-reddit]", "data-reddit") == "connected"
    assert page.get_attribute("[data-account='Beta'] [data-reddit]", "data-reddit") == "disconnected"
    assert "Open the browser and sign in to Reddit" in (page.get_attribute("[data-account='Beta'] [data-reddit]", "title") or "")


@needs_ui
def test_get_started_step_two_checks_off_when_an_account_is_signed_in(browser):
    signed = FakeApi(state(profiles=[profile(1, "Alpha", version=1, reddit={"signed_in": True, "user": "alice"})]))
    page = open_ui(browser, signed)
    assert page.locator("[data-step='2'][data-done='true']").count() == 1
    unsigned = FakeApi(state(profiles=[profile(1, "Alpha", version=1, reddit={"signed_in": False, "user": None})]))
    page = open_ui(browser, unsigned)
    assert page.locator("[data-step='2'][data-done='true']").count() == 0
