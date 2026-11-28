"""A new team starts with one browser profile, ready for its owner to sign in to Reddit. Only one, and only for a team you create.

Run:  uv run pytest tests/test_starter_account.py
"""
import pytest
from fastapi.testclient import TestClient

import pigeon.client as lc
import pigeon.engine as le
from pigeon.desktop import make_app
from tests.conftest import FP


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "PROFILES", tmp_path / "home" / "profiles")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    return t, server


def test_a_new_team_starts_with_one_account_to_connect(app):
    t, server = app
    t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True})
    t.post("/api/teams", json={"name": "Agency"})
    ps = t.get("/api/state").json()["profiles"]
    assert len(ps) == 1, "exactly one starter, never several"
    p = ps[0]
    assert p["name"] == "My Reddit account"
    assert p["start_url"] == "https://www.reddit.com/login", "opening it goes straight to Reddit's sign-in"
    assert p["reddit"]["signed_in"] is None and p["reddit"]["user"] is None

    t.post("/api/teams", json={"name": "Second"})  # every team you create gets its own one
    assert [p["name"] for p in t.get("/api/state").json()["profiles"]] == ["My Reddit account"]


def test_joining_a_team_adds_nothing(app):
    t, server = app
    owner = lc.Client(server=server, token="")
    owner.register("o@x.io", "password1")
    tid = owner.create_team("Theirs")["id"]  # straight through the server: no starter for this team
    code = owner.invite(tid, "member")["code"]
    t.post("/api/login", json={"server": server, "email": "m@x.io", "password": "password1", "signup": True, "invite": code})
    assert t.get("/api/state").json().get("profiles", []) == []
