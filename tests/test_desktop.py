import pytest
from fastapi.testclient import TestClient

import loft.client as lc
import loft.engine as le
from loft.cli import parse_proxy
from loft.desktop import make_app
from tests.conftest import FP


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Loft"] = "1"
    return t, server


def test_foreign_host_and_missing_header_blocked(app):
    t, _ = app
    assert t.get("/api/state", headers={"host": "evil.com"}).status_code == 403
    assert TestClient(t.app, base_url="http://127.0.0.1").get("/api/state").status_code == 403
    assert t.get("/").status_code == 200


def test_flow_login_team_profile(app):
    t, server = app
    assert t.get("/api/state").json()["signed_in"] is False
    r = t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "register": True})
    assert r.status_code == 200, r.text
    assert t.get("/api/state").json()["me"]["teams"] == []
    t.post("/api/teams", json={"name": "T"})
    r = t.post("/api/profiles", json={"name": "P1", "os": "macos", "proxy": "http://u:pw@9.9.9.9:3128", "tags": ["a"]})
    assert r.status_code == 200, r.text
    s = t.get("/api/state").json()
    assert s["role"] == "owner"
    p = s["profiles"][0]
    assert p["proxy"] == {"server": "http://9.9.9.9:3128", "username": "u", "password": "pw"} and p["os"] == "macos"
    t.patch(f"/api/profiles/{p['id']}", json={"proxy": ""})
    assert t.get("/api/state").json()["profiles"][0]["proxy"] is None
    code = t.post("/api/invite", json={"role": "member"}).json()["code"]
    assert len(code) > 10
    assert t.get("/api/audit").status_code == 200


def test_bad_login_reports_error(app):
    t, server = app
    r = t.post("/api/login", json={"server": server, "email": "nobody@x.io", "password": "password1"})
    assert r.status_code == 401


def test_parse_proxy():
    assert parse_proxy("socks5://1.2.3.4:1080") == {"server": "socks5://1.2.3.4:1080"}
    assert parse_proxy("u:p@h.com:80") == {"server": "http://h.com:80", "username": "u", "password": "p"}
    assert parse_proxy("") is None
    with pytest.raises(SystemExit):
        parse_proxy("http://noport")
