"""Adding and editing an account: just a name and real proxy settings (type, host, port, username, password, a name for it, and a
Test proxy button). No Add many, no start page, tags or notes in the forms.

Run:  uv run pytest tests/test_account_dialogs.py   (UI part needs `cd web && npm run build` first)
"""
import pytest
from fastapi.testclient import TestClient

import pigeon.client as lc
import pigeon.engine as le
from pigeon.cli import parse_proxy
from pigeon.desktop import make_app
from tests.conftest import FP
from tests.ux_harness import FakeApi, browser, needs_ui, open_ui, profile, state  # noqa: F401


# ---------------------------------------------------------------- backend
def test_proxy_credentials_are_url_decoded():
    assert parse_proxy("http://us%40er:p%40ss%3Aword@1.2.3.4:3128") == {"server": "http://1.2.3.4:3128", "username": "us@er", "password": "p@ss:word"}


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "PROFILES", tmp_path / "home" / "profiles")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    return t


def test_proxy_test_endpoint(app, monkeypatch):
    seen = []
    monkeypatch.setattr(le, "check_proxy", lambda p, timeout=15, samples=3: (seen.append(p), {"ok": True, "ip": "5.6.7.8", "ms": 321, "stable": True})[1])
    r = app.post("/api/proxy/test", json={"proxy": "socks5://u:p%40w@9.9.9.9:1080"}).json()
    assert r == {"ok": True, "ip": "5.6.7.8", "ms": 321, "stable": True}
    assert seen == [{"server": "socks5://9.9.9.9:1080", "username": "u", "password": "p@w"}]
    monkeypatch.setattr(le, "check_proxy", lambda p, timeout=15, samples=3: {"ok": False, "error": "https://api.ipify.org: ConnectError: [Errno 61] Connection refused"})
    r = app.post("/api/proxy/test", json={"proxy": "http://9.9.9.9:1"}).json()
    assert r["ok"] is False and "Couldn't connect" in r["error"] and "Errno" not in r["error"]
    assert app.post("/api/proxy/test", json={"proxy": "not a proxy"}).status_code == 400


# ---------------------------------------------------------------- the dialogs
@needs_ui
def test_no_add_many(browser):
    page = open_ui(browser, FakeApi(state()))
    page.wait_for_selector("main button:has-text('Add account')")
    assert page.locator("main button:has-text('Add many')").count() == 0


@needs_ui
def test_add_account_has_name_and_proxy_settings_only(browser):
    api = FakeApi(state())
    api.routes[r"POST /proxy/test"] = {"ok": True, "ip": "5.6.7.8", "ms": 321, "stable": True}
    page = open_ui(browser, api)
    page.click("main button:has-text('Add account')")
    dlg = page.inner_text("[role=dialog]")
    for gone in ("Start page", "Tags", "Notes"):
        assert gone not in dlg, f"{gone} was removed on request"
    page.fill("#n_name", "Main")
    page.select_option("#px_type", "socks5")
    page.fill("#px_host", "1.2.3.4")
    page.fill("#px_port", "1080")
    page.fill("#px_user", "us@er")
    page.fill("#px_pass", "p@ss")
    page.fill("#px_name", "US home")
    page.click("[role=dialog] button:text-is('Test proxy')")
    page.wait_for_selector("[role=dialog] >> text=Working")
    assert "5.6.7.8" in page.inner_text("[role=dialog]")
    assert api.called("POST", "/proxy/test") == [{"proxy": "socks5://us%40er:p%40ss@1.2.3.4:1080"}]
    page.click("#n_go")
    page.wait_for_timeout(400)
    body = api.called("POST", "/profiles")[0]
    assert body["proxy"] == "socks5://us%40er:p%40ss@1.2.3.4:1080" and body["proxy_name"] == "US home"
    assert body["start_url"] == "https://www.reddit.com/login", "new accounts open on Reddit's sign-in"
    assert "notes" not in body and "tags" not in body


@needs_ui
def test_pasting_a_proxy_url_fills_the_fields(browser):
    page = open_ui(browser, FakeApi(state()))
    page.click("main button:has-text('Add account')")
    page.fill("#px_host", "http://bob:secret@10.0.0.5:8080")
    page.wait_for_timeout(100)
    assert (page.input_value("#px_type"), page.input_value("#px_host"), page.input_value("#px_port"), page.input_value("#px_user"), page.input_value("#px_pass")) == \
        ("http", "10.0.0.5", "8080", "bob", "secret")


@needs_ui
def test_test_proxy_failure_is_plain(browser):
    api = FakeApi(state())
    api.routes[r"POST /proxy/test"] = {"ok": False, "error": "Couldn't connect through this proxy. Check the host, port, username and password."}
    page = open_ui(browser, api)
    page.click("main button:has-text('Add account')")
    page.fill("#px_host", "1.2.3.4")
    page.fill("#px_port", "1")
    page.click("[role=dialog] button:text-is('Test proxy')")
    page.wait_for_selector("[role=dialog] >> text=Couldn't connect through this proxy")


@needs_ui
def test_edit_has_no_start_page_tags_or_notes_and_keeps_an_untouched_proxy(browser):
    api = FakeApi(state(profiles=[profile(1, "Alpha", proxy={"server": "http://8.8.8.8:8080", "name": "Office"})]))
    api.routes[r"GET /profiles/1/proxy"] = {"proxy": "http://u:p@8.8.8.8:8080", "name": "Office"}
    page = open_ui(browser, api)
    page.click("button[aria-label='More for Alpha']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.wait_for_function("document.querySelector('#px_host')?.value === '8.8.8.8'")
    dlg = page.inner_text("[role=dialog]")
    for gone in ("Start page", "Tags", "Notes"):
        assert gone not in dlg
    assert page.input_value("#px_user") == "u" and page.input_value("#px_name") == "Office"
    page.fill("#e_name", "Alpha 2")
    page.click("[role=dialog] button[type=submit]")
    page.wait_for_timeout(400)
    assert api.called("PATCH", "/profiles/1") == [{"name": "Alpha 2", "canvas": "real"}], "an untouched proxy isn't sent"
