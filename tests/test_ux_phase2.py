"""Phase 2 (flows) end to end: a walk through the real dashboard against a sandbox `pigeon server` + `pigeon app --no-window`
(ports 4600-4619, temp dir) with a real account browser: team management (invites, roles, remove + rotate, templates, runners,
backup, API key), account filters and bulk actions, typed agent settings with the unsaved-changes guard, people search, row
progress, the connection-lost banner (the app is really stopped and started again), activity in sentences, the overview,
the phone layout, and no classic-view links.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_ux_phase2.py -s
Needs the built UI, a Playwright Chromium and the Camoufox browser."""
import os
import signal
import subprocess

import httpx
import pytest

from pigeon.client import Client
from tests.test_pigeon import HAVE
from tests.ux_harness import ROOT, Sandbox, _wait, browser, needs_ui, open_settings, SETTINGS_WIN, close_settings  # noqa: F401

pytestmark = [needs_ui, pytest.mark.skipif(not HAVE, reason="needs the Camoufox browser (the app launches accounts with it)")]


@pytest.fixture(scope="module")
def sb():
    s = Sandbox()
    yield s
    s.close()


def api(sb, method, path, body=None):
    r = httpx.request(method, sb.url + "/api" + path, headers={"X-Pigeon": "1"}, json=body, timeout=60)
    assert r.status_code < 400, r.text
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content


def restart_app(sb):
    os.killpg(sb.app.pid, signal.SIGTERM)
    sb.app.wait(10)
    return lambda: _start(sb)


def _start(sb):
    sb.app = subprocess.Popen(["uv", "run", "pigeon", "app", "--no-window", "--port", str(sb.app_port)], cwd=ROOT, env=sb.env,
                              stdout=sb.logs[1], stderr=subprocess.STDOUT, start_new_session=True)
    _wait(sb.url + "/api/state")


def test_phase2_walkthrough(sb, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors, native = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    page.goto(sb.url)
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.wait_for_selector("[data-account='My Reddit account']")  # a new team starts with one account to connect

    # ---- P2-1 team: invite (create + copy), a teammate joins, roles, remove + rotate, templates, runners, backup, API key
    open_settings(page, "Members")
    page.select_option("select[aria-label='Role for the invite']", "member")
    page.click("button:text-is('Create invite')")
    page.click("button:text-is('Copy code')")
    code = page.evaluate("navigator.clipboard.readText()")
    assert "." in code
    mate = Client(server=sb.server_url, token="")
    mate.register("bea@x.io", "password2", "Bea", code)
    page.reload()
    page.select_option("select[aria-label='Role for bea@x.io']", "admin")
    page.wait_for_selector("[data-sonner-toast]:has-text('bea@x.io is now admin')")
    page.select_option("select[aria-label='Role for bea@x.io']", "owner")
    assert "Make bea@x.io an owner?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Cancel')")
    open_settings(page, "Templates")
    page.click("button:text-is('New template')")
    page.fill("#t_name", "EU accounts")
    page.click("[role=dialog] button:text-is('Save template')")
    page.wait_for_selector("td:has-text('EU accounts')")
    open_settings(page, "Cloud runners")
    page.fill("#r_name", "Box")
    page.fill("#r_url", "http://127.0.0.1:1")
    page.fill("#r_key", "rk")
    page.click("button:text-is('Add runner')")
    page.wait_for_selector("td:has-text('Box')")
    page.click("button[aria-label='Remove runner Box']")
    page.click("[role=alertdialog] button:text-is('Remove runner')")
    page.wait_for_selector("td:has-text('Box')", state="detached")
    open_settings(page, "Automation API")
    page.click("button:text-is('Show key')")
    assert "pigeon_" in page.inner_text(SETTINGS_WIN)
    open_settings(page, "Members")
    page.click("button[aria-label='Remove bea@x.io']")
    page.click("[role=alertdialog] button:text-is('Remove')")
    page.wait_for_selector("[role=alertdialog]:has-text('Rotate the team key now?')")
    page.click("[role=alertdialog] button:text-is('Rotate key')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Key rotated')", timeout=60000)
    assert "bea@x.io" not in page.inner_text(f"{SETTINGS_WIN} table")
    close_settings(page)

    # ---- P2-4 accounts: count line, status + tag filters, bulk tag + delete, tags in Edit
    for name, tags, proxy in (("Alpha", ["eu"], ""), ("Beta", ["us"], ""), ("Gamma", ["eu"], "http://u:p@10.1.1.1:3128")):
        api(sb, "POST", "/profiles", {"name": name, "tags": tags, "proxy": proxy})
    page.click("nav button:has-text('Accounts')")
    page.wait_for_selector("text=4 accounts · 0 open · 3 no proxy")  # the 3 added + the starter
    page.select_option("select[aria-label='Show']", "noproxy")
    page.wait_for_selector("[data-account='Gamma']", state="detached")
    page.select_option("select[aria-label='Show']", "all")
    page.click("[data-account='Alpha'] button[title='Show accounts tagged eu']")
    page.wait_for_selector("[data-account='Beta']", state="detached")
    page.click("button[aria-label='Stop filtering by eu']")
    page.click("[role=checkbox][aria-label='Select Beta']")
    page.click("[role=checkbox][aria-label='Select Gamma']")
    page.click("button:text-is('Add tag')")
    page.fill("#bulk_tag", "warm")
    page.click("[role=dialog] button:text-is('Add tag')")
    page.wait_for_selector("[data-account='Gamma'] button[title='Show accounts tagged warm']")
    tags = {p["name"]: p["tags"] for p in api(sb, "GET", "/state")["profiles"]}
    assert tags["Beta"] == ["us", "warm"] and tags["Gamma"] == ["eu", "warm"] and tags["Alpha"] == ["eu"]
    page.click("[role=checkbox][aria-label='Select Beta']")  # leaves Gamma selected
    page.click("button:text-is('Delete')")
    assert "Delete Gamma?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Delete account')")
    page.wait_for_selector("[data-account='Gamma']", state="detached")
    page.click("[role=checkbox][aria-label='Select Beta']")  # tags are added in bulk now (not in the Edit dialog)
    page.click("button:text-is('Add tag')")
    page.fill("#bulk_tag", "hot")
    page.click("[role=dialog] button:text-is('Add tag')")
    page.wait_for_selector("[data-account='Beta'] >> text=+1")  # 3 tags: two pills, the rest as +1
    assert "hot" in next(p for p in api(sb, "GET", "/state")["profiles"] if p["name"] == "Beta")["tags"]
    if page.locator("button:text-is('Clear selection')").count():
        page.click("button:text-is('Clear selection')")

    # ---- P2-3 row progress: Open browser says so at once
    page.click("[data-account='Alpha'] button:has-text('Open browser')")
    page.wait_for_selector("[data-account='Alpha'] button:has-text('Opening…'), [data-account='Alpha'] [data-status]:has-text('Open')")
    page.wait_for_selector("[data-account='Alpha'] button:text-is('Agent controls')", timeout=150000)

    # ---- P2-7 phone layout, while the account is open
    phone = browser.new_context(viewport={"width": 390, "height": 844})
    m = phone.new_page()
    m.goto(sb.url)
    assert m.wait_for_selector("header").bounding_box()["height"] <= 64
    m.click("button:text-is('Agent controls')")
    m.wait_for_selector("#pg select[aria-label='Section']")
    assert not m.is_visible("#pg [role=tablist]")
    assert m.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    phone.close()

    # ---- P2-2 typed settings + unsaved guard, P2-5 people search and toasts
    page.click("[data-account='Alpha'] button:text-is('Agent controls')")
    page.click("#pg [role=tab]:text-is('Settings')")
    page.wait_for_selector("#pg h4:text-is('Limits')")
    page.fill("#set_daily_dm_cap", "900")
    page.wait_for_selector("text=Between 1 and 500")
    page.fill("#set_daily_dm_cap", "12")
    page.click("#pg [role=tab]:text-is('People')")
    page.click("[role=alertdialog] button:text-is('Keep editing')")
    page.click("#pg button:text-is('Save settings')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Settings saved')")
    pid = {p["name"]: p["id"] for p in api(sb, "GET", "/state")["profiles"]}["Alpha"]
    assert api(sb, "POST", f"/profiles/{pid}/pigeon/op", {"op": "settings_set"})["settings"]["daily_dm_cap"] == 12
    page.click("#pg [role=tab]:text-is('People')")
    page.fill("#pgimp", "spez\nexample_user\nanother_one")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Imported 3 usernames')")
    page.wait_for_selector("text=Showing 3 of 3")
    page.fill("input[aria-label='Search people']", "spez")
    page.wait_for_selector("text=Showing 1 of 1")
    assert page.query_selector("#pg pre") is None
    page.keyboard.press("Escape")
    page.click("[data-account='Alpha'] button:has-text('Close & save')")
    page.wait_for_selector("[data-account='Alpha'] button:has-text('Open browser')", timeout=90000)  # saved and closed

    # ---- P2-3 connection lost: stop the real app, see the banner and disabled actions, start it again, Retry
    start_again = restart_app(sb)
    banner = page.wait_for_selector("[role=alert]:has-text('Lost connection')", timeout=15000)
    assert page.is_disabled("[data-account='Alpha'] button:has-text('Open browser')")
    start_again()
    banner.query_selector("button:text-is('Retry')").click()
    page.wait_for_selector("[role=alert]:has-text('Lost connection')", state="detached", timeout=15000)
    assert not page.is_disabled("[data-account='Alpha'] button:has-text('Open browser')")

    # ---- P2-6 activity in sentences, overview without jargon
    page.click("nav button:has-text('Overview')")
    page.click("button[aria-label='Activity history']")  # activity is the Overview's history panel now
    page.wait_for_selector("text=created the account Alpha")
    body = page.inner_text("[role=dialog]")
    assert "removed bea@x.io from the team" in body or "removed a former member from the team" in body
    assert "profile.created" not in body and "member.role" not in body and "user " not in body
    page.keyboard.press("Escape")
    page.wait_for_selector("text=Stored data")
    body = page.inner_text("main")
    assert "Key epoch" not in body and "snapshots" not in body

    # ---- P4-4 no way back to the classic page from the dashboard
    page.click("nav button:has-text('Accounts')")
    page.click("button[aria-label='More for Alpha']")
    assert "classic" not in page.inner_text("[role=menu]").lower()
    page.keyboard.press("Escape")
    open_settings(page, "Members")
    assert page.query_selector("a[href*='legacy']") is None

    assert native == [], native
    assert errors == [], errors
    ctx.close()
