"""Smoke test of the desktop app's Pigeon panel in a real browser: every tab renders from API data and the buttons send
the right calls. The API is stubbed; this checks the front end, not the backend (which has its own tests)."""
import json
import os
from pathlib import Path

import pytest

CHROMIUM = os.environ.get("PIGEON_TEST_CHROMIUM")
pytestmark = pytest.mark.skipif(not CHROMIUM, reason="set PIGEON_TEST_CHROMIUM=/path/to/chrome to run the UI smoke test")

UI = (Path(__file__).parent.parent / "pigeon" / "ui.html").read_text()
LEAD = dict(username="alice", subreddit="saas", fit=88, status="draft", reason="asks for help", message="Hey alice, quick q?",
            post_url="/r/saas/comments/p1/t1/", post_title="t", snippet="s", intent="asking", source="browse", created_at=1.0, error=None)
STATUS = dict(state="idle", job=None, mode=None, since=None, last=None, error=None, central=True, product="Acme",
              stats=dict(scanned=5, leads=2, upvotes=1, dms=1, errors=0), daily=dict(count=1, date="x", next_dm_at=0, paused_until=0),
              leads={"draft": 1}, log=[dict(t=1.0, level="ok", msg="Lead u/alice")])
SETTINGS = dict(model="deepseek-flash", my_username="me", daily_dm_cap=40, warmup=True, warmup_start=0, dm_mode="ai", approve_ai_drafts=True, min_fit=70)
OPS = {
    "leads_list": dict(leads=[LEAD], out=""),
    "opps_list": dict(opportunities=[dict(id="/r/saas/comments/p1/t1/", subreddit="saas", title="Finding customers", post_url="https://www.reddit.com/r/saas/comments/p1/t1/",
                                          why="answers", draft="Start where buyers complain.", status="pending")], out=""),
    "ideas_list": dict(ideas=[dict(id="1-0", subreddit="startups", title="What I learned", body="body", angle="a")], out=""),
    "posts_list": dict(posted=[dict(id="0-0", subreddit="saas", title="An earlier post", url="https://www.reddit.com/r/saas/comments/x/", at=1.0)], out=""),
    "template_show": dict(template="{Hey|Hi} {name}", undo=True, out=""),
    "settings_set": dict(settings=SETTINGS, out=""),
    "product_show": dict(product=dict(url="https://acme.test", name="Acme", summary="s", offer="o", icp="founders"), out=""),
    "subs_edit": dict(subreddits=[dict(name="saas", enabled=True, score=70, rules_risk="low", reason="good")], out=""),
}
BRAIN = dict(summary=dict(days=14, by_kind=dict(dm=3, comment=2, post=1, reply=1, upvote=4), stuck_claims=0,
                          by_subreddit=[dict(subreddit="saas", dms=3, replies=1, comments=2, posts=0, upvotes=4, last_at=1.0)],
                          by_profile=[dict(profile="acct-a", dms=3, replies=1, comments=2, posts=1, upvotes=4, last_at=1.0)]),
             accounts=[dict(profile_id=5, profile="acct-a", username="acct_a_reddit")],
             team_setup=dict(version=2, product="Acme", subreddits=3, limits=dict(team_dm_per_day=40)),
             events=[dict(at=1.0, kind="dm", status="done", profile="acct-a", subreddit="saas", target="alice", title="")])


def test_panel_renders_every_tab_and_sends_the_right_calls():
    from playwright.sync_api import sync_playwright
    calls, errors = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = b.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())

        def api(route):
            req = route.request
            path = req.url.split("/api", 1)[1]
            body = json.loads(req.post_data) if req.post_data else None
            if path == "/state":
                out = dict(signed_in=True, server="s", me=dict(email="a@x.io", teams=[dict(id=1, name="T", role="owner")]), device="d", team=1,
                           role="owner", members=[], profiles=[dict(id=5, name="acct-a", os="windows", proxy=None, tags=[], notes="", version=1, access="edit", running=None, running_here=True)],
                           jobs={"run-5": dict(alive=True, log=[], pigeon=STATUS)}, host_os="macos", browser=True, templates=[], runners=[], has_key=True)
            elif path == "/profiles/5/pigeon/status":
                out = STATUS
            elif path == "/profiles/5/pigeon/op":
                calls.append(("op", body["op"], body["args"]))
                out = OPS.get(body["op"], {"out": "ok"})
            elif path == "/profiles/5/pigeon/job":
                calls.append(("job", body["kind"], body["args"]))
                out = {"ok": True}
            elif path.startswith("/pigeon/brain"):
                out = BRAIN
            else:
                out = {}
            route.fulfill(content_type="application/json", body=json.dumps(out))

        page.route("http://pigeon.test/api/**", api)
        page.route("http://pigeon.test/", lambda r: r.fulfill(content_type="text/html", body=UI))
        page.goto("http://pigeon.test/")
        page.wait_for_selector("button:text-is('Agent controls')")
        page.click("button:text-is('Agent controls')")
        page.wait_for_selector("#pg h3")

        def text():
            page.wait_for_timeout(300)
            return page.inner_text("#pg")

        assert "u/alice" in text() and "team history: on" in text()
        assert "Hey alice" in page.input_value("#pg textarea")  # the draft is editable text
        page.click("#pg button:text-is('Approve')")
        page.click("#pg button:text-is('Send now')")
        page.click("#pg button:text-is('Skip')")

        page.click("#pg button:text-is('Run')")
        assert "Add the people on this page" in text()
        page.select_option("#pgmode", "full")
        page.click("#pg button:text-is('Start')")
        page.click("#pg button:text-is('Queue everyone on this page')")
        page.click("#pg button:text-is('Check replies')")
        page.click("#pg button:text-is('Run the check')")

        page.click("#pg button:text-is('Comments')")
        assert "Start where buyers complain" in text() and "What I learned" in text()
        page.click("#pg button:text-is('Approve & post')")
        assert "An earlier post" in text()  # posts the agent already submitted
        page.click("#pg button:text-is('Submit now')")
        page.click("#pg button:text-is('I posted it myself')")

        page.click("#pg button:text-is('Messages')")
        assert "Undo AI spin" in text()
        page.click("#pg button:text-is('Add spintax with AI')")

        page.click("#pg button:text-is('Product')")
        assert "Acme" in text() and "r/saas" in text()
        page.click("#pg input[type=checkbox]")  # toggle the subreddit off
        page.click("#pg button:text-is('Re-run discovery')")

        page.click("#pg button:text-is('Settings')")
        assert "Max messages per day" in text()
        page.click("#pg button:text-is('Save settings')")

        page.click("#pg button:text-is('Team activity')")
        t = text()
        assert "3 DMs" in t and "acct-a" in t and "u/acct_a_reddit" in t and "team_dm_per_day=40" in t and "team setup v2" in t
        page.click("#pg button:text-is('Pull team setup')")
        page.click("#pg button:text-is(\"Publish this account's setup to the team\")")
        page.click("#pg button:text-is(\"Record this account's past DMs & comments\")")
        b.close()

    assert not errors, errors
    got = {(k, n) for k, n, _ in calls}
    for want in [("op", "lead_approve"), ("job", "send"), ("op", "lead_skip"), ("job", "run"), ("job", "scan"), ("job", "replies"), ("job", "doctor"),
                 ("job", "post"), ("job", "submit"), ("op", "idea_posted"), ("op", "template_spin"), ("op", "subs_edit"), ("job", "discover"), ("op", "settings_set"), ("op", "brain_backfill"), ("op", "team_pull"), ("op", "team_publish")]:
        assert want in got, (want, sorted(got))
    assert ("job", "run", {"mode": "full", "minutes": None}) in calls
    assert ("op", "subs_edit", {"disable": ["saas"]}) in calls
