import pytest
from fastapi.testclient import TestClient

import loft.client as lc
import loft.engine as le
from loft.cli import BadInput, parse_proxy, proxy_url
from loft.desktop import api_key, make_app
from tests.conftest import FP


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Loft"] = "1"
    return t, server


def signed_in(t, server):
    r = t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True})
    assert r.status_code == 200, r.text
    t.post("/api/teams", json={"name": "T"})


def test_foreign_host_and_missing_header_blocked(app):
    t, _ = app
    assert t.get("/api/state", headers={"host": "evil.com"}).status_code == 403
    assert TestClient(t.app, base_url="http://127.0.0.1").get("/api/state").status_code == 403
    assert t.get("/").status_code == 200


def test_flow_login_team_profile(app):
    t, server = app
    assert t.get("/api/state").json()["signed_in"] is False
    signed_in(t, server)
    r = t.post("/api/profiles", json={"name": "P1", "os": "macos", "proxy": "http://u:pw@9.9.9.9:3128", "tags": ["a"]})
    assert r.status_code == 200, r.text
    s = t.get("/api/state").json()
    assert s["role"] == "owner" and s["has_key"] is True
    p = s["profiles"][0]
    assert p["proxy"] == {"server": "http://9.9.9.9:3128", "username": "u", "password": "pw"} and p["os"] == "macos"
    assert t.get(f"/api/profiles/{p['id']}/proxy").json()["proxy"] == "http://u:pw@9.9.9.9:3128"
    t.patch(f"/api/profiles/{p['id']}", json={"proxy": "", "canvas": "real"})
    p = t.get("/api/state").json()["profiles"][0]
    assert p["proxy"] is None and p["canvas"] == "real"
    code = t.post("/api/invite", json={"role": "member"}).json()["code"]
    assert "." in code  # server code + team key
    assert t.get("/api/audit").status_code == 200


def test_bulk_and_templates(app):
    t, server = app
    signed_in(t, server)
    r = t.post("/api/profiles/bulk", json={"count": 3, "name": "Acct {n}", "proxies": ["http://1.1.1.1:80", "socks5://2.2.2.2:1080", ""]})
    assert r.json() == {"created": 3}
    ps = t.get("/api/state").json()["profiles"]
    assert [p["name"] for p in ps] == ["Acct 1", "Acct 2", "Acct 3"]
    assert [p["proxy"]["server"] for p in ps] == ["http://1.1.1.1:80", "socks5://2.2.2.2:1080", "http://1.1.1.1:80"]
    assert t.post("/api/profiles/bulk", json={"count": 0}).status_code == 400
    assert t.post("/api/templates", json={"name": "US", "os": "linux", "proxy": "http://u:p@h:1"}).status_code == 200
    tpl = t.get("/api/state").json()["templates"][0]
    assert tpl["settings"]["proxy"] == "http://u:p@h:1" and tpl["settings"]["os"] == "linux"
    assert t.post("/api/templates", json={"name": "bad", "proxy": "http://noport"}).status_code == 400


def test_v1_api_requires_key(app):
    t, server = app
    signed_in(t, server)
    plain = TestClient(t.app, base_url="http://127.0.0.1")
    assert plain.get("/v1/profiles").status_code == 401
    assert plain.get("/v1/profiles", headers={"X-API-Key": "wrong"}).status_code == 401
    h = {"X-API-Key": api_key()}
    r = plain.post("/v1/profiles", headers=h, json={"name": "via api", "os": "linux"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert plain.get("/v1/profiles", headers={"Authorization": "Bearer " + api_key()}).json()[0]["name"] == "via api"
    st = plain.get(f"/v1/profiles/{pid}/status", headers=h).json()
    assert st["running_here"] is False and st["version"] == 0
    assert plain.post(f"/v1/profiles/{pid}/stop", headers=h).status_code == 404
    assert len(plain.post("/v1/profiles/bulk", headers=h, json={"count": 2}).json()) == 2
    assert plain.get("/v1/profiles", headers={**h, "host": "evil.com"}).status_code == 403


def test_bad_login_reports_error(app):
    t, server = app
    r = t.post("/api/login", json={"server": server, "email": "nobody@x.io", "password": "password1"})
    assert r.status_code == 401


def test_parse_proxy_roundtrip():
    assert parse_proxy("socks5://1.2.3.4:1080") == {"server": "socks5://1.2.3.4:1080"}
    assert parse_proxy("u:p@h.com:80") == {"server": "http://h.com:80", "username": "u", "password": "p"}
    assert proxy_url(parse_proxy("http://u:p@h.com:80")) == "http://u:p@h.com:80"
    assert parse_proxy("") is None
    with pytest.raises(BadInput):
        parse_proxy("http://noport")


def test_tls_self_signed_and_bind_rule(tmp_path):
    from loft.tls import check_bind, fingerprint, self_signed
    cert, key = self_signed(tmp_path, ["loft.local", "10.0.0.5"])
    import sys
    assert cert.exists() and len(fingerprint(cert)) == 64
    if sys.platform != "win32":  # no POSIX modes on Windows
        assert oct(key.stat().st_mode)[-3:] == "600"
    assert self_signed(tmp_path, ["loft.local"]) == (cert, key)  # reused when hosts are covered
    with pytest.raises(SystemExit):
        check_bind("0.0.0.0", tls=False, insecure=False)
    check_bind("0.0.0.0", tls=True, insecure=False)
    check_bind("127.0.0.1", tls=False, insecure=False)


def test_dashboard_backup_restore_via_app(app):
    t, server = app
    signed_in(t, server)
    t.post("/api/profiles", json={"name": "P", "os": "windows", "canvas": "noise", "proxy": "http://u:pw@1.1.1.1:8080"})
    ov = t.get("/api/overview").json()
    assert ov["profiles"] == 1 and ov["members"] == 1 and ov["key_epoch"] == 1
    pv = t.get("/api/rotate-key/preview").json()
    assert pv["profiles"] == 1 and pv["running"] == []
    # backup -> restore into a second team through the desktop API
    blob = t.post("/api/backup", json={"passphrase": "pw"}).content
    assert blob[:4] == b"LFTT"
    t.post("/api/teams", json={"name": "T2"})
    import base64
    out = t.post("/api/restore", json={"data": base64.b64encode(blob).decode(), "passphrase": "pw"}).json()
    assert out["profiles"] == 1
    names = [p["name"] for p in t.get("/api/state").json()["profiles"]]
    assert names == ["P"]
    # wrong passphrase is a clean 400
    assert t.post("/api/restore", json={"data": base64.b64encode(blob).decode(), "passphrase": "no"}).status_code == 400


def test_proxy_placeholder_expansion():
    from loft.engine import expand_proxy_placeholders
    # per-profile sticky session for a proxynet gateway (or any session proxy)
    p = {"server": "http://gw:8899", "username": "country-us-session-{pid}", "password": "x"}
    expand_proxy_placeholders(p, 42)
    assert p["username"] == "country-us-session-42" and p["server"] == "http://gw:8899"
    p2 = {"server": "http://gw:8899", "username": "session-{session}", "password": "x"}
    expand_proxy_placeholders(p2, 7)
    assert p2["username"] == "session-loft-7"
    assert expand_proxy_placeholders(None, 1) is None
    # no placeholder = unchanged
    p3 = {"server": "http://h:1", "username": "static", "password": "x"}
    assert expand_proxy_placeholders(dict(p3), 9) == p3
