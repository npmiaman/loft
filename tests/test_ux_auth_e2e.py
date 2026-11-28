"""The auth page end to end: a real `pigeon server` + `pigeon app --no-window` (ports 4600-4619, temp dir), driven in Chromium.
Replays every problem found in the Nov 28 auth-page review and checks each is fixed.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_ux_auth_e2e.py -s
"""
import json
import time

import httpx
import pytest

from tests.ux_harness import Sandbox, browser, needs_ui  # noqa: F401

pytestmark = [needs_ui]


@pytest.fixture(scope="module")
def sb():
    s = Sandbox()
    (s.home / "config.json").write_text(json.dumps({"server": s.server_url}))  # like a normal install: the server is already known
    yield s
    s.close()


def alert(page) -> str:
    page.wait_for_selector("form p[role=alert]", timeout=30000)
    return page.inner_text("form p[role=alert]").strip()


def no_alert(page) -> bool:
    page.wait_for_timeout(200)
    return page.locator("form p[role=alert]").count() == 0


def test_auth_page_end_to_end(sb, browser):
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(sb.url)

    # 1. a brand-new server opens on "create the first account", not "Welcome back"
    page.wait_for_selector("h1:text-is('Create the first account')")
    assert page.locator("#inv").count() == 0, "the first user needs no invite, so don't ask for one"
    assert page.get_attribute("#pw", "autocomplete") == "new-password"
    form = page.inner_text("form")
    for gone in ("can't be recovered", "Use a different server", "end-to-end encrypted"):
        assert gone not in form, f"removed on request: {gone!r}"
    assert page.locator("#srv").count() == 0, "no server field: the app uses its saved server"
    assert "at least 8 characters" in page.inner_text("form").lower(), "say the rule before it's broken"
    page.fill("#pw", "abc")
    page.click("button[aria-label='Show password']")
    assert page.get_attribute("#pw", "type") == "text"
    page.click("button[aria-label='Hide password']")
    assert page.get_attribute("#pw", "type") == "password"

    # 2. Enter creates the account in create mode
    page.fill("#em", " Owner@X.io ")
    page.fill("#pw", "password1")
    page.press("#pw", "Enter")
    page.wait_for_selector("text=You're not in a team yet", timeout=40000)
    assert page.locator("button:has-text('Sign out')").count() == 0, "the no-team screen has no Sign out (removed on request)"
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.wait_for_selector("h1:text-is('Accounts')", timeout=30000)
    code = httpx.post(sb.url + "/api/invite", headers={"X-Pigeon": "1"}, json={"role": "member"}, timeout=30).json()["code"]
    page.click("button[aria-label='Account menu'] >> visible=true")  # Sign out lives in the account menu
    page.click("[role=menuitem]:has-text('Sign out')")

    # 3. a server with accounts opens on sign in
    page.wait_for_selector("h1:text-is('Welcome back')")
    assert page.get_attribute("#pw", "autocomplete") == "current-password"
    assert page.locator("#inv").count() == 0

    # 4. wrong password: a plain sentence, and it goes away as soon as you edit
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "nope-nope-1")
    page.click("button:text-is('Sign in')")
    assert alert(page) == "That email and password don't match."
    page.fill("#pw", "nope-nope-2")
    assert no_alert(page), "a stale error must clear when the user edits"

    # 5. "create account" with an email that already has one: say so, offer to sign in, keep the email
    page.click("button:text-is('Create an account')")
    page.wait_for_selector("h1:text-is('Create your account')")
    page.fill("#em", "OWNER@x.io")
    page.fill("#pw", "password9")
    page.click("button:text-is('Create account')")
    assert alert(page) == "That email already has an account."
    page.click("button:text-is('Sign in instead')")
    page.wait_for_selector("h1:text-is('Welcome back')")
    assert page.input_value("#em") == "OWNER@x.io"

    # 6. a second person without an invite: told what to do, and the invite field is right there
    page.click("button:text-is('Create an account')")
    assert page.is_visible("#inv"), "on a server with accounts the invite field is part of sign-up"
    assert "From the teammate who invited you" in page.inner_text("form")
    page.fill("#em", "sam@x.io")
    page.fill("#pw", "password2")
    page.click("button:text-is('Create account')")
    assert alert(page) == "You need an invite code to join. Ask a teammate for one."

    # 8. the sign-up form still works on a short window: nothing stuck above the top, and the button can be reached
    lap = browser.new_context(viewport={"width": 1366, "height": 420}).new_page()
    lap.goto(sb.url)
    lap.wait_for_selector("h1:text-is('Welcome back')")
    lap.click("button:text-is('Create an account')")
    lap.wait_for_timeout(500)
    lap.locator("h1").scroll_into_view_if_needed()
    assert lap.locator("h1").bounding_box()["y"] >= 0, "the heading must be reachable (before the fix it was stuck above the window)"
    btn = lap.locator("button:text-is('Create account')")
    btn.scroll_into_view_if_needed()
    box = btn.bounding_box()
    assert 0 <= box["y"] and box["y"] + box["height"] <= 420, "the submit button must be reachable by scrolling"

    # 9. with the invite, sign-up goes straight into the team
    page.fill("#inv", code)
    page.fill("#pw", "password2")
    page.click("button:text-is('Create account')")
    page.wait_for_selector("h1:text-is('Accounts')", timeout=40000)
    assert not errors, errors
