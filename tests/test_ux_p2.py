"""Phase 2 (flows) tickets, plus P4-4. Backend tests use the desktop API; UI tests drive the built dashboard against
tests/ux_harness.FakeApi. Run: `uv run pytest tests/test_ux_p2.py` (UI tests need `npm run build` in web/ first)."""
import time

import pytest

from tests.test_ux_p0 import desk  # noqa: F401  (fixture)
from tests.ux_harness import FakeApi, browser, lead, needs_ui, open_drawer, open_ui, profile, state, status, open_settings, SETTINGS_WIN, close_settings  # noqa: F401

MEMBERS = [dict(id=1, name="Owner", email="owner@x.io", role="owner"), dict(id=2, name="Bea", email="bea@x.io", role="member")]


def no_native(page):
    page.native = []
    page.on("dialog", lambda d: (page.native.append(d.message), d.dismiss()))


def drawer_api(**kw):
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=status())}, **kw))
    return api


# ------------------------------------------------------------------ P2-1 team management
@needs_ui
def test_p2_1_invites_create_and_copy(browser):
    api = FakeApi(state(members=MEMBERS))
    api.routes[r"POST /invite"] = lambda b, m: {"code": f"abc123.KEY-{b['role']}", "role": b["role"]}
    page = open_ui(browser, api, hash="team")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.select_option("select[aria-label='Role for the invite']", "admin")
    page.click("button:text-is('Create invite')")
    page.wait_for_selector("text=abc123.KEY-admin")
    assert api.called("POST", "/invite") == [{"role": "admin"}]
    page.click("button:text-is('Copy code')")
    assert page.evaluate("navigator.clipboard.readText()") == "abc123.KEY-admin"


@needs_ui
def test_p2_1_remove_member_offers_key_rotation(browser):
    api = FakeApi(state(members=MEMBERS))
    api.routes[r"GET /rotate-key/preview"] = dict(running=[], profiles=3, rekey=[dict(email="owner@x.io")], invites_to_revoke=1, without_key=[])
    api.routes[r"POST /rotate-key"] = dict(rekeyed=[1], invites_revoked=1, without_key=[])
    page = open_ui(browser, api, hash="team")
    no_native(page)
    page.click("button[aria-label='Remove bea@x.io']")
    assert "Remove bea@x.io from Agency?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Remove')")
    dlg = page.wait_for_selector("[role=alertdialog]:has-text('Rotate the team key')")
    assert "3 accounts" in dlg.inner_text()
    page.click("[role=alertdialog] button:text-is('Rotate key')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Key rotated')")
    assert api.called("DELETE", "/members/2") and api.called("POST", "/rotate-key") and page.native == []


@needs_ui
def test_p2_1_templates_runners_api_key(browser):  # backup/restore left the dashboard on request (CLI still has them)
    api = FakeApi(state(members=MEMBERS, templates=[dict(id=7, name="EU accounts", settings=dict(os="windows", canvas="noise", tags=["eu"]))],
                        runners=[dict(id=3, name="Box", url="https://box:4420")]))
    api.routes[r"GET /apikey"] = {"key": "pigeon_supersecretkey123", "base": "http://127.0.0.1:4410/v1"}
    api.routes[r"POST /restore"] = {"profiles": 2, "templates": 1}
    page = open_ui(browser, api, hash="team")
    no_native(page)
    # templates: new + delete (confirm)
    open_settings(page, "Templates")
    page.click("button:text-is('New template')")
    page.fill("#t_name", "US accounts")
    page.click("[role=dialog] button:text-is('Save template')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/templates")[0]["name"] == "US accounts"
    page.click("button[aria-label='Delete template EU accounts']")
    page.click("[role=alertdialog] button:text-is('Delete template')")
    page.wait_for_timeout(300)
    assert api.called("DELETE", "/templates/7")
    # runners: add + remove
    open_settings(page, "Cloud runners")
    page.fill("#r_name", "Second")
    page.fill("#r_url", "https://two:4420")
    page.fill("#r_key", "rk")
    page.click("button:text-is('Add runner')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/runners") == [{"name": "Second", "url": "https://two:4420", "key": "rk"}]
    page.click("button[aria-label='Remove runner Box']")
    page.click("[role=alertdialog] button:text-is('Remove runner')")
    page.wait_for_timeout(300)
    assert api.called("DELETE", "/runners/3")
    # automation API: base shown, key hidden until asked
    open_settings(page, "Automation API")
    page.wait_for_selector("text=http://127.0.0.1:4410/v1")
    body = page.inner_text(SETTINGS_WIN)
    assert "http://127.0.0.1:4410/v1" in body and "pigeon_supersecretkey123" not in body
    page.click("button:text-is('Show key')")
    assert "pigeon_supersecretkey123" in page.inner_text(SETTINGS_WIN)
    assert page.native == [] and page.errors == []


@needs_ui
def test_p2_1_share_dialog_links_to_invites(browser):
    api = FakeApi(state(members=MEMBERS[:1]))
    api.routes[r"GET /profiles/1/shares"] = []
    page = open_ui(browser, api)
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Share')")
    page.click("[role=dialog] button:text-is('Invite someone')")
    page.wait_for_selector(f"{SETTINGS_WIN} [data-settings-section] h2:text-is('Members')")
    page.wait_for_selector("select[aria-label='Role for the invite']")


# ------------------------------------------------------------------ P2-2 typed settings
@needs_ui
def test_p2_2_settings_are_grouped_typed_validated_and_guarded(browser):
    api = drawer_api()
    page = open_ui(browser, api)
    no_native(page)
    open_drawer(page)
    page.click("#pg [role=tab]:text-is('Settings')")
    for h in ("Limits", "Schedule", "AI"):
        page.wait_for_selector(f"#pg h4:text-is('{h}')")
    assert page.get_attribute("#set_daily_dm_cap", "type") == "number"
    assert page.query_selector("#pg select#set_active_start") and page.query_selector("#pg select#set_dm_mode")
    page.fill("#set_daily_dm_cap", "0")
    page.wait_for_selector("text=Between 1 and 500")
    assert page.is_disabled("#pg button:text-is('Save settings')")
    page.fill("#set_daily_dm_cap", "25")
    page.select_option("#set_active_start", "8")
    # switching tabs with unsaved edits asks first; Cancel keeps them
    page.click("#pg [role=tab]:text-is('People')")
    assert "unsaved" in page.inner_text("[role=alertdialog]").lower()
    page.click("[role=alertdialog] button:text-is('Keep editing')")
    assert page.input_value("#set_daily_dm_cap") == "25"
    page.click("#pg button:text-is('Save settings')")
    page.wait_for_timeout(400)
    assert [a for a in api.ops_called("settings_set") if a.get("pairs")][-1] == {"pairs": {"daily_dm_cap": 25, "active_start": 8}}
    page.click("#pg [role=tab]:text-is('People')")  # nothing unsaved now: no question
    page.wait_for_timeout(300)
    assert page.query_selector("[role=alertdialog]") is None
    # a backend refusal reads as a sentence, not a Python error
    page.click("#pg [role=tab]:text-is('Settings')")
    api.routes[r"POST /profiles/1/pigeon/op"] = lambda b, m: (400, {"detail": "invalid literal for int() with base 10: 'x'"}) if b["op"] == "settings_set" and b["args"].get("pairs") else (200, dict(settings=api.settings))
    page.fill("#set_posts_per_day", "3")
    page.click("#pg button:text-is('Save settings')")
    toast = page.wait_for_selector("[data-sonner-toast][data-type=error]")
    assert "invalid literal" not in toast.inner_text() and "whole number" in toast.inner_text()
    assert page.native == []


# ------------------------------------------------------------------ P2-3 optimistic actions + connection banner
@needs_ui
def test_p2_3_row_actions_show_progress_until_the_state_changes(browser):
    api = FakeApi()
    api.hold = {r"POST /profiles/1/run"}
    page = open_ui(browser, api)
    page.click("button:text-is('Open browser')")
    page.wait_for_selector("button:has-text('Opening…')")
    assert page.is_disabled("button:has-text('Opening…')")
    api.release()
    page.wait_for_timeout(500)
    assert page.query_selector("button:has-text('Opening…')")  # still opening: the app hasn't reported it open yet
    api.st["profiles"][0]["running_here"] = True
    api.st["jobs"] = {"run-1": dict(alive=True, log=[], pigeon=status())}
    page.wait_for_selector("[data-status]:has-text('Open')", timeout=8000)
    api.hold = {r"POST /profiles/1/stop"}
    page.click("button:has-text('Close & save')")
    page.wait_for_selector("button:has-text('Closing…')")
    api.release()


@needs_ui
def test_p2_3_connection_lost_banner_with_retry(browser):
    api = FakeApi()
    page = open_ui(browser, api)
    page.wait_for_selector("text=Reddit 1")
    api.routes[r"GET /state"] = (502, {"detail": "down"})
    banner = page.wait_for_selector("[role=alert]:has-text('Lost connection')", timeout=10000)
    assert page.is_disabled("button:text-is('Open browser')")
    del api.routes[r"GET /state"]
    banner.query_selector("button:text-is('Retry')").click()
    page.wait_for_selector("[role=alert]:has-text('Lost connection')", state="detached")
    assert not page.is_disabled("button:text-is('Open browser')")


# ------------------------------------------------------------------ P2-4 accounts filters + bulk
def test_p2_4_edit_can_change_tags(desk):
    t, _ = desk
    pid = t.post("/api/profiles", json={"name": "P", "tags": ["a"]}).json()["id"]
    r = t.patch(f"/api/profiles/{pid}", json={"tags": ["eu", "warm"]})
    assert r.status_code == 200, r.text
    mine = lambda: next(p for p in t.get("/api/state").json()["profiles"] if p["id"] == pid)  # not the team's starter account
    assert mine()["tags"] == ["eu", "warm"]
    t.patch(f"/api/profiles/{pid}", json={"notes": "n"})  # leaving tags out keeps them
    assert mine()["tags"] == ["eu", "warm"]


@needs_ui
def test_p2_4_filters_count_and_bulk_actions(browser):
    ps = [profile(1, "Alpha", tags=["eu"], running_here=True), profile(2, "Beta", tags=["us"], proxy=None),
          profile(3, "Gamma", tags=["eu"], proxy=None), profile(4, "Delta", tags=[])]
    api = FakeApi(state(profiles=ps, jobs={"run-1": dict(alive=True, log=[], pigeon=status(state="running", job="run", mode="find"))}))
    page = open_ui(browser, api)
    no_native(page)
    assert "4 accounts · 1 open · 2 no proxy" in page.inner_text("main")
    page.select_option("select[aria-label='Show']", "noproxy")
    page.wait_for_timeout(600)  # rows fade out
    assert [n for n in ("Alpha", "Beta", "Gamma", "Delta") if page.query_selector(f"[data-account='{n}']")] == ["Beta", "Gamma"]
    page.select_option("select[aria-label='Show']", "all")
    page.click("[data-account='Alpha'] button[title='Show accounts tagged eu']")  # clicking a tag filters by it
    page.wait_for_timeout(600)  # rows fade out
    assert [n for n in ("Alpha", "Beta", "Gamma", "Delta") if page.query_selector(f"[data-account='{n}']")] == ["Alpha", "Gamma"]
    page.click("button[aria-label='Stop filtering by eu']")
    # select two, tag them, then delete them with a confirm that names them
    page.click("[role=checkbox][aria-label='Select Beta']")
    page.click("[role=checkbox][aria-label='Select Delta']")
    page.wait_for_selector("text=2 selected")
    page.click("button:text-is('Add tag')")
    page.fill("#bulk_tag", "warm")
    page.click("[role=dialog] button:text-is('Add tag')")
    page.wait_for_timeout(400)
    assert api.called("PATCH", "/profiles/2") == [{"tags": ["us", "warm"]}] and api.called("PATCH", "/profiles/4") == [{"tags": ["warm"]}]
    page.click("button:text-is('Delete')")
    dlg = page.inner_text("[role=alertdialog]")
    assert "Delete 2 accounts?" in dlg and "Beta" in dlg and "Delta" in dlg
    page.click("[role=alertdialog] button:text-is('Delete 2 accounts')")
    page.wait_for_timeout(400)
    assert api.called("DELETE", "/profiles/2") and api.called("DELETE", "/profiles/4") and not api.called("DELETE", "/profiles/1")
    assert page.native == []


# ------------------------------------------------------------------ P2-5 people + drawer feedback
@needs_ui
def test_p2_5_people_search_sort_count_and_toasts(browser):
    api = drawer_api()
    api.leads = [lead(f"user{i:03d}", status="queued", message=None, fit=50 + i % 50, created_at=float(i)) for i in range(150)] + [lead("zed_target", fit=99)]
    api.ops["leads_import"] = dict(added=2, out="imported 2 username(s); 0 already in the queue")
    page = open_ui(browser, api)
    open_drawer(page)
    page.wait_for_selector("text=Showing 100 of 151")
    page.fill("input[aria-label='Search people']", "zed")
    page.wait_for_selector("text=Showing 1 of 1")
    page.fill("input[aria-label='Search people']", "")
    page.select_option("select[aria-label='Sort']", "newest")
    page.wait_for_timeout(200)
    first = page.eval_on_selector("#pg [data-lead]", "e => e.dataset.lead")
    assert first == "user149"
    page.click("button:text-is('Show more')")
    page.wait_for_selector("text=Showing 151 of 151")
    assert page.query_selector("#pg pre") is None  # no raw text box
    page.fill("#pgimp", "a_user\nb_user")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("[data-sonner-toast]:has-text('Imported 2 usernames')")


@needs_ui
def test_p2_5_inline_state_per_item(browser):
    api = drawer_api()
    api.hold = {r"POST /profiles/1/pigeon/op"}
    page = open_ui(browser, api)
    open_drawer(page)
    page.wait_for_timeout(500)  # let the list request arrive (it is held), then answer it
    api.release()
    page.wait_for_selector("#pg [data-lead=alice] button:text-is('Approve')")
    page.click("#pg [data-lead=alice] button:text-is('Approve')")
    page.wait_for_selector("#pg [data-lead=alice] button:has-text('Approving…')")
    api.hold = set()
    api.release()


# ------------------------------------------------------------------ P2-6 activity + overview
@needs_ui
def test_p2_6_activity_reads_as_sentences(browser):
    api = FakeApi(state(members=MEMBERS))
    now = time.time()
    api.routes[r"GET /audit"] = [
        dict(at=now - 60, email="owner@x.io", action="member.role", detail="user 2 -> admin", profile_id=None),
        dict(at=now - 120, email="owner@x.io", action="profile.created", detail="Reddit 1", profile_id=1),
        dict(at=now - 180, email="owner@x.io", action="profile.deleted", detail="Old one", profile_id=99),
        dict(at=now - 240, email="bea@x.io", action="profile.started", detail="laptop", profile_id=99),
    ]
    api.routes[r"GET /overview"] = dict(profiles=1, members=2, members_without_key=[], running=[], bytes=0, versions=0, open_invites=0,
                                        key_epoch=1, rotating=False, last_rotation=None, recent=[])
    page = open_ui(browser, api, hash="activity")  # an old Activity link opens the Overview's history panel
    page.wait_for_selector("text=changed bea@x.io's role to admin")
    body = page.inner_text("[role=dialog]")
    assert "created the account Reddit 1" in body and "deleted the account Old one" in body and "opened a deleted account" in body
    assert "member.role" not in body and "#99" not in body and "user 2" not in body


@needs_ui
def test_p2_6_overview_outcomes_skeleton_and_refresh(browser):
    api = FakeApi()
    ov = dict(profiles=2, members=1, members_without_key=[], running=[], bytes=1048576, versions=4, open_invites=0, key_epoch=3,
              rotating=False, last_rotation=None, recent=[])
    api.routes[r"GET /overview"] = ov
    brain = lambda b, m: dict(summary=dict(days=1, by_kind=dict(dm=4, reply=1, lead=9), stuck_claims=0, by_subreddit=[], by_profile=[]),
                              accounts=[], team_setup=dict(version=0, product=None, subreddits=0, limits={}), events=[])
    api.routes[r"GET /pigeon/brain"] = brain
    page = open_ui(browser, api, hash="overview")
    page.wait_for_selector("text=Messages sent today")
    body = page.inner_text("main")
    assert "Key epoch" not in body and "snapshots" not in body
    ov["profiles"] = 5  # the page refreshes with the app's poll
    page.wait_for_function("document.querySelector('main').innerText.includes('5')", timeout=10000)


# ------------------------------------------------------------------ P2-7 mobile
@needs_ui
def test_p2_7_mobile_top_bar_and_drawer(browser):
    api = drawer_api()
    page = open_ui(browser, api, width=390, height=844)
    head = page.wait_for_selector("header")
    assert head.bounding_box()["height"] <= 64
    open_settings(page, "Members")  # the team lives in Settings, opened from the account menu (inside the phone menu)
    close_settings(page)
    page.click("button[aria-label='Menu']")
    page.click("#mobile-menu button:has-text('Accounts')")
    open_drawer(page)
    pad = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#pg')).paddingLeft)")
    assert pad == 16
    assert page.is_visible("#pg select[aria-label='Section']") and not page.is_visible("#pg [role=tablist]")
    page.select_option("#pg select[aria-label='Section']", "settings")
    page.wait_for_selector("#ai_key")
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


# ------------------------------------------------------------------ P4-4 classic view retired
@needs_ui
def test_p4_4_no_classic_view_links(browser):
    api = FakeApi(state(members=MEMBERS))
    page = open_ui(browser, api)
    page.click("button[aria-label='More for Reddit 1']")
    assert "classic" not in page.inner_text("[role=menu]").lower()
    page.keyboard.press("Escape")
    open_settings(page, "Members")
    assert page.query_selector("a[href*='legacy']") is None and "classic" not in page.inner_text(SETTINGS_WIN).lower()
