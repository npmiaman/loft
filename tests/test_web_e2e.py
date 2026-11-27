"""The new Next.js UI, end to end: real desktop backend, real Pigeon server, a profile launched in a headless Camoufox subprocess,
and the Pigeon drawer driven in a browser. Needs the UI built first (`npm run build` in web/)."""
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
    page.goto(url)

    # sign up, create a team, add an account: all through the UI
    page.fill("#srv", pigeon_server)
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.click("button:text-is('Add your first account')")
    page.fill("#n_name", "Client A")
    page.click("#n_go")
    page.wait_for_selector("text=Not opened yet", timeout=60000)
    assert page.inner_text("body").count("No proxy: uses your own IP") == 1  # the warning shows for an account without a proxy

    # the app launches the account in its own subprocess; once its control channel is up, Pigeon's controls appear
    page.click("button:text-is('Open browser')")
    page.wait_for_selector("text=Open on this computer", timeout=60000)
    page.wait_for_selector("button:text-is('Agent controls')", timeout=120000)
    page.click("button:text-is('Agent controls')")
    page.wait_for_selector("#pg h3")

    def panel():
        page.wait_for_timeout(500)
        return page.inner_text("#pg")

    assert "team history: on" in panel() and "Start here" in panel()  # no product yet: the next step says so

    # People: add by username, end to end through the backend, the control channel and the session's state
    page.fill("#pgimp", "spez\nu/example_user\nnot valid!")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("text=imported 2 username(s)")
    page.wait_for_selector("#pg >> text=u/spez", timeout=15000)
    page.wait_for_selector("#pg >> text=u/example_user", timeout=15000)

    # Messages: save a template and it comes back
    page.click("#pg button:text-is('Messages')")
    page.wait_for_selector("#pgtpl")
    page.fill("#pgtpl", "Hi {name}, quick question about r/{subreddit}?")
    page.click("#pg button:text-is('Save')")
    page.wait_for_selector("#pg pre.log:text-is('saved')")
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Messages')")
    page.wait_for_function("document.querySelector('#pgtpl') && document.querySelector('#pgtpl').value === 'Hi {name}, quick question about r/{subreddit}?'", timeout=15000)

    # Settings: change one and it persists
    page.click("#pg button:text-is('Settings')")
    page.wait_for_selector("input[data-k='daily_dm_cap']")
    page.fill("input[data-k='daily_dm_cap']", "7")
    page.click("#pg button:text-is('Save settings')")
    page.wait_for_selector("#pg pre.log:text-is('saved')")
    page.click("#pg button:text-is('People')")
    page.click("#pg button:text-is('Settings')")
    page.wait_for_function("document.querySelector(\"input[data-k='daily_dm_cap']\") && document.querySelector(\"input[data-k='daily_dm_cap']\").value === '7'", timeout=15000)

    # Run: with no DeepSeek key or product, the refusal reaches the user (as a toast) instead of failing silently
    page.click("#pg button:text-is('Run')")
    page.wait_for_selector("#pgmode")
    page.click("#pg button:text-is('▶ Start')")
    page.wait_for_selector("[data-sonner-toast]")
    assert any(w in page.inner_text("[data-sonner-toast]") for w in ("DeepSeek key", "Set up the product"))

    # Team activity and Product render from the live server
    page.click("#pg button:text-is('Team activity')")
    page.wait_for_selector("text=Team memory")
    page.click("#pg button:text-is('Product')")
    page.wait_for_selector("button:text-is('Analyze my product')")

    # closing the account saves it and the row goes back to "Closed"
    page.keyboard.press("Escape")
    page.click("button:text-is('Close & save')")
    page.wait_for_selector("text=Closed", timeout=60000)

    assert errors == [], errors
    page.close()
