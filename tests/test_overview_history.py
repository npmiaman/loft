"""Activity lives in the Overview now: a clock button at the top right opens the history in a side panel.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_overview_history.py
"""
from tests.ux_harness import FakeApi, browser, needs_ui, open_ui, state  # noqa: F401

pytestmark = [needs_ui]
OVERVIEW = dict(profiles=1, members=1, members_without_key=[], running=[], bytes=0, versions=0, open_invites=0, key_epoch=1, rotating=False,
                last_rotation=None, recent=[])


def api_with_history():
    st = state(members=[dict(id=1, name="Owner", email="owner@x.io", role="owner"), dict(id=2, name="Bea", email="bea@x.io", role="member")])
    api = FakeApi(st)
    api.routes[r"GET /overview"] = OVERVIEW
    api.routes[r"GET /audit"] = [dict(at=1_700_000_000, email="owner@x.io", action="member.role", detail="user 2 -> admin"),
                                 dict(at=1_699_000_000, email="owner@x.io", action="team.created", detail="Agency")]
    return api


def test_activity_is_not_a_page_any_more(browser):
    page = open_ui(browser, api_with_history())
    nav = [t.strip() for t in page.locator("nav[aria-label='Pages'] button").all_inner_texts()]
    assert "Activity" not in nav and "Overview" in nav, nav


def test_clock_button_opens_the_history(browser):
    page = open_ui(browser, api_with_history(), hash="overview")
    page.wait_for_selector("main h1:text-is('Overview')")
    btn = page.locator("button[aria-label='Activity history']")
    box, head = btn.bounding_box(), page.locator("main h1").bounding_box()
    assert box["x"] > head["x"] + 400, "top right of the page"
    btn.click()
    panel = page.wait_for_selector("[role=dialog]:has-text('Activity history')")
    page.wait_for_selector("[role=dialog] >> text=changed bea@x.io's role to admin")
    assert "created the team Agency" in panel.inner_text()
    page.keyboard.press("Escape")
    page.wait_for_selector("[role=dialog]", state="detached")


def test_old_activity_links_open_the_history(browser):
    page = open_ui(browser, api_with_history(), hash="activity")
    page.wait_for_selector("main h1:text-is('Overview')")
    page.wait_for_selector("[role=dialog]:has-text('Activity history')")
