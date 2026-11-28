"""Settings: a floating window opened from the account menu at the bottom of the sidebar (Settings / Sign out). Sections on the
left, one pane at a time. The old Team page lives here now.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_settings_ui.py
"""
from tests.ux_harness import SETTINGS_WIN, FakeApi, browser, close_settings, needs_ui, open_settings, open_ui, state  # noqa: F401

pytestmark = [needs_ui]

ALL = ["Personal", "Appearance", "Members", "Templates", "Cloud runners", "Automation API", "Teams"]  # AI key and Security and backups removed on request
pane = lambda page: page.inner_text(f"{SETTINGS_WIN} [data-settings-section]:visible")
names = lambda page: [t.strip() for t in page.locator(f"{SETTINGS_WIN} [role=tab]").all_inner_texts()]


def test_user_menu_has_settings_and_sign_out_and_team_is_gone_from_the_sidebar(browser):
    api = FakeApi(state())
    page = open_ui(browser, api)
    nav = [t.strip() for t in page.locator("nav[aria-label='Pages'] button").all_inner_texts()]
    assert "Team" not in nav, nav
    page.click("button[aria-label='Account menu']")
    assert [t.strip() for t in page.locator("[role=menuitem]").all_inner_texts()] == ["Settings", "Sign out"]
    page.click("[role=menuitem]:has-text('Sign out')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/logout")


def test_settings_is_a_floating_window_over_the_page(browser):
    page = open_ui(browser, FakeApi(state()))
    open_settings(page)
    assert page.is_visible("main h1:text-is('Accounts')"), "the page stays underneath"
    box = page.locator(SETTINGS_WIN).bounding_box()
    assert box["width"] < 1280 and box["height"] < 900, "a window, not a page"
    close_settings(page)
    assert page.evaluate("location.hash") == "#accounts" or page.evaluate("location.hash") == ""


def test_sections_one_at_a_time(browser):
    api = FakeApi(state())
    api.routes["GET /apikey"] = {"key": "k" * 20, "base": "http://127.0.0.1:4472/v1"}
    page = open_ui(browser, api)
    open_settings(page)
    assert names(page) == ALL
    open_settings(page, "Members")
    assert "Invite someone as" in pane(page) and "X-API-Key" not in pane(page)
    open_settings(page, "Automation API")
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section] >> text=X-API-Key")
    assert "Invite someone as" not in pane(page)
    assert page.evaluate("location.hash") == "#settings/automation"


def test_members_only_see_what_applies_to_them(browser):
    st = state()
    st["role"] = "member"
    st["me"]["teams"][0]["role"] = "member"
    page = open_ui(browser, FakeApi(st))
    open_settings(page)
    n = names(page)
    assert "Personal" in n and "Members" in n, n


def test_old_team_links_open_members(browser):
    page = open_ui(browser, FakeApi(state()), hash="team")
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section] h2:text-is('Members')")
    assert page.evaluate("location.hash") == "#settings/members"


def test_theme_choice_applies_and_persists(browser):
    page = open_ui(browser, FakeApi(state()), hash="settings/appearance")
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section] h2:text-is('Appearance')")
    page.click(f"{SETTINGS_WIN} label:has-text('Dark')")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.reload()
    page.wait_for_selector(SETTINGS_WIN)
    assert page.evaluate("document.documentElement.dataset.theme") == "dark", "remembered after reload"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == bg
    page.click(f"{SETTINGS_WIN} label:has-text('Light')")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"


def test_removed_sections_stay_gone(browser):
    page = open_ui(browser, FakeApi(state()), hash="settings/security")  # an old link falls back to the first section
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section]:visible h2:text-is('Personal')")
    assert "AI key" not in names(page) and "Security and backups" not in names(page)


def test_invite_hint_does_not_talk_about_the_encryption_key(browser):
    api = FakeApi(state())
    api.routes[r"POST /invite"] = lambda b, m: {"code": "abc123.KEY", "role": b["role"]}
    page = open_ui(browser, api, hash="settings/members")
    page.click("button:text-is('Create invite')")
    page.wait_for_selector("text=abc123.KEY")
    assert "encryption key" not in pane(page) and "part after the dot" not in pane(page)
    assert "Send it privately" in pane(page)


def test_narrow_screen_shows_sections_as_a_row(browser):
    page = open_ui(browser, FakeApi(state()), width=390, height=844)
    open_settings(page, "Members")
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    assert "Invite someone as" in pane(page)


# ---------------------------------------------------------------- Personal: edit your name; the team row shows just the team
def test_personal_name_is_editable_and_team_shows_only_its_name(browser):
    api = FakeApi(state())
    api.routes["PATCH /me"] = lambda b, m: {"ok": True, "name": b["name"]}
    page = open_ui(browser, api, hash="settings/profile")
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section]:visible h2:text-is('Personal')")
    text = pane(page)
    assert "Agency" in text and "Owner" not in text.split("Team", 1)[1].split("This computer")[0], "team row: name only, no role"
    page.click(f"{SETTINGS_WIN} button[aria-label='Edit name']")
    page.fill("#settings_name", "  Aman P  ")
    page.press("#settings_name", "Enter")
    page.wait_for_timeout(300)
    assert api.called("PATCH", "/me") == [{"name": "Aman P"}]
    # empty names aren't sent; Escape cancels
    page.click(f"{SETTINGS_WIN} button[aria-label='Edit name']")
    page.fill("#settings_name", "   ")
    page.press("#settings_name", "Enter")
    assert "Enter a name" in pane(page)
    page.press("#settings_name", "Escape")
    assert page.locator("#settings_name").count() == 0 and page.is_visible(SETTINGS_WIN), "Escape cancels the edit, not the window"
    assert len(api.called("PATCH", "/me")) == 1
