"""The new Next.js UI, end to end: real desktop backend, real Pigeon server, a profile launched in a headless Camoufox subprocess,
and the Pigeon drawer driven in a browser. Needs the UI built first (`npm run build` in web/)."""
import json

import pytest

from pigeon.desktop import WEB
from tests.test_desktop_e2e import desktop  # noqa: F401  (the fixture)
from tests.test_pigeon import HAVE, browser  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not HAVE, reason="needs the real Camoufox browser (the desktop app launches profiles with it)"),
    pytest.mark.skipif(not (WEB / "index.html").exists(), reason="the web UI is not built: run `npm run build` in web/"),
]


def test_the_new_ui_signs_up_adds_launches_and_drives_pigeon(desktop, browser):
    url, pigeon_server, home = desktop
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    cfg = home / "config.json"  # like a normal install: the app already knows its server (the sign-in page has no server field)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({**(json.loads(cfg.read_text()) if cfg.exists() else {}), "server": pigeon_server}))
    page.goto(url)

    # sign up, create a team, add an account: all through the UI
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.click("main button:has-text('Add account') >> nth=0")  # the team already has its starter account
    page.fill("#n_name", "Client A")
    page.click("#n_go")
    page.wait_for_selector("[data-account='Client A'] button:text-is('Open browser')", timeout=60000)
    assert "Add proxy" in page.inner_text("[data-account='Client A']")  # an account without a proxy offers to add one

    # the app launches the account in its own subprocess; once its control channel is up, Pigeon's controls appear
    page.click("[data-account='Client A'] button:text-is('Open browser')")
    page.wait_for_selector("[data-status]:has-text('Open')", timeout=60000)
    page.wait_for_selector("button:text-is('Agent controls')", timeout=120000)
    page.click("button:text-is('Agent controls')")
    page.wait_for_selector("#pg h3")

    def panel():
        page.wait_for_timeout(500)
        return page.inner_text("#pg")

    assert "team memory: on" in panel() and "Start here" in panel()  # no product yet: the next step says so

    # People: add by username, end to end through the backend, the control channel and the session's state
    page.fill("#pgimp", "spez\nu/example_user\nnot valid!")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Imported 2 usernames')")
    page.wait_for_selector("#pg >> text=u/spez", timeout=15000)
    page.wait_for_selector("#pg >> text=u/example_user", timeout=15000)

    # Messages: save a template and it comes back
    page.click("#pg button:text-is('Messages')")
    page.wait_for_selector("#pgtpl")
    page.fill("#pgtpl", "Hi {name}, quick question about r/{subreddit}?")
    page.click("#pg button:text-is('Save')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Template saved')")
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Messages')")
    page.wait_for_function("document.querySelector('#pgtpl') && document.querySelector('#pgtpl').value === 'Hi {name}, quick question about r/{subreddit}?'", timeout=15000)

    # Settings: change one and it persists
    page.click("#pg button:text-is('Settings')")
    page.wait_for_selector("input[data-k='daily_dm_cap']")
    page.fill("input[data-k='daily_dm_cap']", "7")
    page.click("#pg button:text-is('Save settings')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Settings saved')")
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Settings')")
    page.wait_for_function("document.querySelector(\"input[data-k='daily_dm_cap']\") && document.querySelector(\"input[data-k='daily_dm_cap']\").value === '7'", timeout=15000)

    # Run: with no DeepSeek key or product, the refusal reaches the user (as a toast) instead of failing silently
    page.click("#pg button:text-is('Run')")
    page.wait_for_selector("#pgmode")
    page.click("#pg button:text-is('Start')")
    t = page.wait_for_selector("[data-sonner-toast]:has-text('AI key'), [data-sonner-toast]:has-text('Set up the product')")
    assert "pigeon agent" not in t.inner_text()  # no command line in the dashboard

    # Team activity and Product render from the live server
    page.click("#pg button:text-is('Team memory')")
    page.wait_for_selector("text=Team memory")
    page.click("#pg button:text-is('Product')")
    page.wait_for_selector("button:text-is('Analyze my product')")

    # closing the account saves it and the row goes back to "Closed"
    page.keyboard.press("Escape")
    page.click("button:text-is('Close & save')")
    page.wait_for_selector("[data-account='Client A'] button:text-is('Open browser')", timeout=60000)

    assert errors == [], errors
    page.close()
