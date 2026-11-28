"""The account row: rename in place, a named proxy (the address never shown in the list) behind an Add/Edit proxy button, and a
tick or cross for whether the account is signed in to Reddit.

Run:  uv run pytest tests/test_account_row.py   (UI part needs `cd web && npm run build` first)
"""
import pytest
from fastapi.testclient import TestClient

import pigeon.client as lc
import pigeon.engine as le
from pigeon.desktop import make_app
from tests.conftest import FP
from tests.ux_harness import FakeApi, browser, needs_ui, open_ui, profile, state  # noqa: F401


@pytest.fixture
def app(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(le, "PROFILES", tmp_path / "home" / "profiles")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True})
    t.post("/api/teams", json={"name": "T"})
    return t


def prof(t, name):
    return next(p for p in t.get("/api/state").json()["profiles"] if p["name"] == name)


def test_proxy_has_a_name_and_editing_the_name_keeps_the_address(app):
    t = app
    pid = t.post("/api/profiles", json={"name": "A", "proxy": "http://u:pw@9.9.9.9:3128", "proxy_name": "US home"}).json()["id"]
    assert prof(t, "A")["proxy"]["name"] == "US home"
    assert t.get(f"/api/profiles/{pid}/proxy").json() == {"proxy": "http://u:pw@9.9.9.9:3128", "name": "US home"}
    t.patch(f"/api/profiles/{pid}", json={"proxy_name": "Office"})  # rename only
    p = prof(t, "A")["proxy"]
    assert p["name"] == "Office" and p["server"] == "http://9.9.9.9:3128" and p["password"] == "pw"
    t.patch(f"/api/profiles/{pid}", json={"proxy": "http://v:pw2@8.8.8.8:8080", "proxy_name": "Office"})  # new address, same name
    assert prof(t, "A")["proxy"]["server"] == "http://8.8.8.8:8080" and prof(t, "A")["proxy"]["name"] == "Office"
    t.patch(f"/api/profiles/{pid}", json={"proxy": ""})  # remove
    assert prof(t, "A")["proxy"] is None


def test_the_proxy_name_never_reaches_the_browser():
    opts = le.camoufox_options({"fingerprint": dict(FP), "proxy": {"server": "http://1.2.3.4:80", "username": "u", "password": "p", "name": "US home"},
                                "canvas": "noise", "os": "windows"}, le.PROFILES / "x")
    assert opts["proxy"] == {"server": "http://1.2.3.4:80", "username": "u", "password": "p"}


def test_starter_account_has_no_extra_note(app):
    assert prof(app, "My Reddit account")["notes"] == "", "the Reddit status already says what to do"


# ---------------------------------------------------------------- the row
@needs_ui
def test_row_proxy_pill_shows_a_name_never_the_address(browser):
    ps = [profile(1, "Alpha", proxy={"server": "http://9.9.9.9:3128", "username": "u", "name": "US home"}),
          profile(2, "Beta", proxy={"server": "http://8.8.8.8:8080"}),
          profile(3, "Gamma", proxy=None)]
    page = open_ui(browser, FakeApi(state(profiles=ps)))
    main = page.inner_text("main")
    assert "9.9.9.9" not in main and "8.8.8.8" not in main, "no raw proxy address in the list"
    assert page.inner_text("[data-account='Alpha'] button[data-proxy]").strip() == "US home"
    assert page.inner_text("[data-account='Beta'] button[data-proxy]").strip() == "Proxy", "unnamed proxies still don't show the address"
    assert page.inner_text("[data-account='Gamma'] button[data-proxy]").strip() == "Add proxy"


@needs_ui
def test_proxy_dialog_adds_names_and_removes(browser):
    api = FakeApi(state(profiles=[profile(1, "Alpha", proxy=None), profile(2, "Beta", proxy={"server": "http://8.8.8.8:8080", "name": "Office"})]))
    api.routes[r"GET /profiles/2/proxy"] = {"proxy": "http://u:p@8.8.8.8:8080", "name": "Office"}
    page = open_ui(browser, api)
    page.click("[data-account='Alpha'] button[data-proxy]")
    page.fill("#px_name", "US home")
    page.fill("#px_host", "http://u:p@1.2.3.4:3128")  # pasting a full proxy URL into Host fills every field
    page.click("[role=dialog] button:text-is('Save proxy')")
    page.wait_for_timeout(300)
    assert api.called("PATCH", "/profiles/1") == [{"proxy": "http://u:p@1.2.3.4:3128", "proxy_name": "US home"}]
    page.click("[data-account='Beta'] button[data-proxy]")
    page.wait_for_function("document.querySelector('#px_host')?.value === '8.8.8.8'")
    assert page.input_value("#px_name") == "Office"
    page.click("[role=dialog] button:text-is('Remove proxy')")
    page.click("[role=alertdialog] button:text-is('Remove proxy')")
    page.wait_for_timeout(300)
    assert api.called("PATCH", "/profiles/2") == [{"proxy": ""}]


@needs_ui
def test_rename_in_place(browser):
    api = FakeApi(state(profiles=[profile(1, "Alpha")]))
    page = open_ui(browser, api)
    page.click("[data-account='Alpha'] button[aria-label='Rename Alpha']")
    page.fill("#rename_1", "  Main  ")
    page.press("#rename_1", "Enter")
    page.wait_for_timeout(300)
    assert api.called("PATCH", "/profiles/1") == [{"name": "Main"}]
    page.click("[data-account='Alpha'] button[aria-label='Rename Alpha']")
    page.press("#rename_1", "Escape")
    assert page.locator("#rename_1").count() == 0 and len(api.called("PATCH", "/profiles/1")) == 1


@needs_ui
def test_reddit_status_is_a_tick_or_a_cross(browser):
    ps = [profile(1, "Alpha", version=2, reddit={"signed_in": True, "user": "alice"}),
          profile(2, "Beta", version=0, reddit={"signed_in": None, "user": None})]
    page = open_ui(browser, FakeApi(state(profiles=ps)))
    page.wait_for_selector("[data-account='Beta'] [data-reddit]")  # rows render after the first state load
    assert page.locator("[data-account='Alpha'] [data-reddit='connected'] [data-icon='tick']").count() == 1
    assert page.locator("[data-account='Beta'] [data-reddit='disconnected'] [data-icon='cross']").count() == 1
    assert page.inner_text("[data-account='Alpha'] [data-reddit]").strip() == "u/alice"
    assert page.inner_text("[data-account='Beta'] [data-reddit]").strip() == "Not connected"  # the column header says Reddit


@needs_ui
def test_overview_has_no_running_now_or_recent_activity(browser):
    api = FakeApi(state())
    api.routes[r"GET /overview"] = dict(profiles=1, members=1, members_without_key=[], running=[], bytes=0, versions=0, open_invites=0,
                                        key_epoch=1, rotating=False, last_rotation=None, recent=[dict(at=1, email="a@x.io", action="profile.created", detail="A")])
    page = open_ui(browser, api, hash="overview")
    page.wait_for_selector("main h1:text-is('Overview')")
    page.wait_for_timeout(600)
    heads = [h.strip() for h in page.locator("main h2, main h3").all_inner_texts()]
    assert "Running now" not in heads and "Recent activity" not in heads, f"those two sections were removed on request: {heads}"
    assert "a@x.io" not in page.inner_text("main"), "no activity rows either"
