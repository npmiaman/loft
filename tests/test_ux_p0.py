"""Phase 0 (safety) tickets from docs/ux-tickets.md, one test (or a few) per ticket.

Backend tests use the real ops / desktop API. UI tests drive the built dashboard in Chromium against tests/ux_harness.FakeApi.
Run: `uv run pytest tests/test_ux_p0.py` (UI tests need `npm run build` in web/ first)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import pigeon.agent.state as agent_state
import pigeon.client as lc
import pigeon.engine as le
from pigeon.agent import ops
from pigeon.agent.agent import Agent
from pigeon.agent.state import State
from pigeon.desktop import make_app
from tests.conftest import FP
from tests.ux_harness import FakeApi, browser, lead, needs_ui, open_drawer, open_ui, profile, state, status, open_settings, SETTINGS_WIN, close_settings  # noqa: F401


# ------------------------------------------------------------------ backend
@pytest.fixture
def desk(tmp_path, monkeypatch, server):
    monkeypatch.setattr(lc, "HOME", tmp_path / "home")
    monkeypatch.setattr(lc, "CONFIG", tmp_path / "home" / "config.json")
    monkeypatch.setattr(agent_state, "KEYFILE", tmp_path / "home" / "pigeon.json")
    monkeypatch.setattr(le, "new_fingerprint", lambda os_name="windows": dict(FP, os=os_name))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    t = TestClient(make_app(), base_url="http://127.0.0.1")
    t.headers["X-Pigeon"] = "1"
    assert t.post("/api/login", json={"server": server, "email": "a@x.io", "password": "password1", "signup": True}).status_code == 200
    t.post("/api/teams", json={"name": "T"})
    return t, tmp_path


def make_st(tmp_path, **settings) -> State:
    st = State(tmp_path / "pigeon.json")
    st.settings.update(dict(my_username="me"), **settings)
    return st


def ld(name, status="queued", **kw):
    return dict(dict(username=name, subreddit="saas", post_url="/r/saas/comments/p1/t1/", post_title="How do I find customers?",
                     snippet="I have built a product and nobody knows it exists.", fit=90, intent="asking", reason="asks",
                     status=status, source="browse", created_at=time.time(), message=None), **kw)


def test_p0_3_cloud_runner_needs_no_body_and_returns_view(desk, monkeypatch):
    t, _ = desk
    pid = t.post("/api/profiles", json={"name": "P"}).json()["id"]
    assert t.post("/api/runners", json={"name": "r1", "url": "http://runner.test", "key": "k"}).status_code == 200
    import pigeon.runner as runner
    monkeypatch.setattr(runner, "start_cloud_session", lambda c, p, r: {"id": "s1", "view": r["url"] + "/view/s1"})
    r = t.post(f"/api/profiles/{pid}/cloud")  # the dashboard sends no body
    assert r.status_code == 200, r.text
    assert r.json()["view"] == "http://runner.test/view/s1"


@pytest.mark.parametrize("mode,approve", [("template", True), ("template", False), ("ai", False), ("ai", True)])
def test_p0_4_every_written_message_waits_for_approval(tmp_path, mode, approve):
    from tests.test_pigeon import fake_deepseek
    from pigeon.agent.llm import Llm
    st = make_st(tmp_path, dm_mode=mode, approve_ai_drafts=approve)
    st.product = dict(url="https://x.test", name="Acme", summary="s", offer="o", icp="founders", personas=["a"], painPhrases=["p"],
                      buyingSignals=["b"], competitors=["c"], subredditIdeas=[], searchQueries=[])
    st.put_lead(ld("alice"))
    st.put_lead(ld("imported1", snippet="", source="csv"))
    c = ops.Ctx(st, Llm("k", transport=fake_deepseek()))
    ops.run_state_op(c, "lead_write", {"all": True})
    assert st.get_lead("alice")["status"] == "draft" and st.get_lead("alice")["message"]
    assert st.get_lead("imported1")["status"] == "draft"
    r = ops.run_state_op(c, "lead_approve", {"all": True})
    assert r["approved"] == 2 and st.get_lead("alice")["status"] == "ready"


def test_p0_4_send_now_refuses_an_unapproved_draft(tmp_path):
    st = make_st(tmp_path)
    st.put_lead(ld("alice", status="draft", message="hi"))
    with pytest.raises(RuntimeError, match="[Aa]pprove"):
        Agent(None, st, None).send_lead_now("alice")


def test_p0_4_status_reports_the_daily_cap(tmp_path):
    from pigeon.agent.attach import PigeonHost
    h = PigeonHost(1, tmp_path, llm_factory=lambda st: None)
    s = h._status()
    assert s["cap"] >= 1


def test_p0_6_clearing_keeps_contacted_history(tmp_path):
    st = make_st(tmp_path)
    st.put_lead(ld("sent_one", status="sent", message="hi", sent_at=time.time()))
    st.put_lead(ld("replied_one", status="replied", message="hi"))
    st.put_lead(ld("waiting", status="queued"))
    c = ops.Ctx(st)
    r = ops.run_state_op(c, "leads_clear", {"unsent": True})
    assert set(st.leads) == {"sent_one", "replied_one"} and "1" in r["out"]
    ops.run_state_op(c, "leads_clear", {})  # even clearing everything keeps who was contacted
    assert not st.leads
    st.save()
    st = State(st.path)  # and it survives a reload (the state file syncs with the account)
    c = ops.Ctx(st)
    r = ops.run_state_op(c, "leads_import", {"text": "sent_one\nReplied_One\nnewbie"})
    assert r["added"] == 1 and set(st.leads) == {"newbie"}
    assert st.was_contacted("SENT_ONE")


def test_p0_6_agent_never_requeues_a_contacted_person(tmp_path, monkeypatch):
    st = make_st(tmp_path, min_fit=10)
    st.product = dict(name="Acme")
    st.contacted["bob"] = time.time()
    import pigeon.agent.brain as brain
    monkeypatch.setattr(brain, "score_items", lambda llm, product, items: [dict(id=i["id"], fit=90, dm_ok=True, reason="r") for i in items])
    ag = Agent(None, st, object())
    entry = lambda a: dict(id="p" + a, kind="post", author=a, subreddit="saas", title="t", text="x" * 40, post_url="/r/x", post_title="t")
    ag.ingest([entry("bob"), entry("carol")])
    assert set(st.leads) == {"carol"}
    # and a lead written for someone contacted (e.g. synced from an older copy) is skipped, not drafted
    st.put_lead(ld("bob"))
    assert ops.write_message(ops.Ctx(st), st.get_lead("bob")) == "skipped"


def test_p0_7_ai_key_can_be_set_from_the_app_and_is_never_echoed(desk):
    t, tmp = desk
    assert t.get("/api/pigeon/key").json() == {"set": False, "hint": ""}
    assert t.post("/api/pigeon/key", json={"key": "  "}).status_code == 400
    assert t.post("/api/pigeon/key", json={"key": "sk-abcdef1234567890"}).status_code == 200
    got = t.get("/api/pigeon/key").json()
    assert got["set"] is True and "abcdef1234567890" not in json.dumps(got) and got["hint"].endswith("7890")
    assert json.loads((tmp / "home" / "pigeon.json").read_text())["deepseek_key"] == "sk-abcdef1234567890"
    assert agent_state.load_key()[0] == "sk-abcdef1234567890"
    assert t.delete("/api/pigeon/key").status_code == 200 and t.get("/api/pigeon/key").json()["set"] is False


# ------------------------------------------------------------------ UI
def drawer(browser, api=None, **kw):
    api = api or FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=status())}))
    page = open_ui(browser, api, **kw)
    open_drawer(page)
    return page, api


def no_native_dialogs(page):
    page.native = []
    page.on("dialog", lambda d: (page.native.append(d.message), d.dismiss()))


@needs_ui
def test_p0_1_approve_and_send_use_the_edit_on_screen(browser):
    page, api = drawer(browser)
    no_native_dialogs(page)
    page.wait_for_selector("#pg textarea[aria-label='Message to u/alice']")
    page.fill("#pg textarea[aria-label='Message to u/alice']", "my edited words")
    page.click("#pg button:text-is('Approve')")
    page.wait_for_timeout(400)
    sent = api.ops_called("lead_approve")
    assert sent and sent[-1]["message"] == "my edited words", api.calls
    # Send now: edit again, confirm, and the edit is what gets approved and sent
    page.fill("#pg textarea[aria-label='Message to u/alice']", "second edit")
    page.click("#pg button:text-is('Send now')")
    page.click("[role=alertdialog] button:has-text('Send')")
    page.wait_for_timeout(400)
    assert api.ops_called("lead_approve")[-1]["message"] == "second edit"
    assert api.jobs_called("send") == [{"username": "alice"}]
    assert page.native == [] and page.errors == []


@needs_ui
def test_p0_2_edit_never_wipes_the_proxy(browser):
    api = FakeApi()
    api.hold = {r"GET /profiles/1/proxy"}
    api.routes[r"GET /profiles/1/proxy"] = {"proxy": "http://u:p@1.2.3.4:8080"}
    page = open_ui(browser, api)
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.wait_for_selector("[role=dialog]")
    assert page.is_disabled("[role=dialog] button[type=submit]") and page.is_disabled("#px_host")
    api.release()
    page.wait_for_function("document.querySelector('#px_host').value === '1.2.3.4'")
    page.fill("#e_name", "Renamed")
    page.click("[role=dialog] button[type=submit]")
    page.wait_for_timeout(400)
    body = api.called("PATCH", "/profiles/1")[-1]
    assert body["name"] == "Renamed" and "proxy" not in body
    # if loading the proxy fails, saving still never sends it
    api2 = FakeApi()
    api2.routes[r"GET /profiles/1/proxy"] = (500, {"detail": "boom"})
    page2 = open_ui(browser, api2)
    page2.click("button[aria-label='More for Reddit 1']")
    page2.click("[role=menuitem]:text-is('Edit')")
    page2.wait_for_selector("text=couldn't load it")
    page2.fill("#e_name", "n")
    page2.click("[role=dialog] button[type=submit]")
    page2.wait_for_timeout(400)
    assert "proxy" not in api2.called("PATCH", "/profiles/1")[-1]


@needs_ui
def test_p0_3_cloud_runner_opens_the_view(browser):
    api = FakeApi(state(runners=[dict(id=1, name="r1", url="http://runner.test")]))
    api.routes[r"POST /profiles/1/cloud"] = {"id": "s1", "view": "http://runner.test/view/s1"}
    page = open_ui(browser, api)
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Run on a cloud runner')")
    page.wait_for_timeout(500)
    assert api.called("POST", "/profiles/1/cloud") == [{}]
    assert page.evaluate("window.__opened.map(w => w.location.href)") == ["http://runner.test/view/s1"]


@needs_ui
def test_p0_4_copy_says_every_message_waits_and_what_start_sends(browser):
    st = status(leads={"ready": 3, "draft": 2}, daily=dict(count=1, date="x", next_dm_at=0, paused_until=0), cap=5)
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=st)}))
    api.status = st
    page = open_ui(browser, api)
    page.select_option("select[aria-label='What should the agent do?']", "dm")
    assert "Sends up to 3 approved messages today" in page.inner_text("body")
    open_drawer(page)
    page.click("#pg [role=tab]:text-is('Messages')")
    page.wait_for_selector("text=Every message waits for your approval")
    assert "AI drafts wait for my approval" not in page.inner_text("#pg")
    page.click("#pg [role=tab]:text-is('People')")
    page.wait_for_selector("#pg button:text-is('Approve all (2)')")


@needs_ui
def test_p0_5_confirms_are_styled_and_name_the_object(browser):
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=status())}))
    api.ops["opps_list"] = dict(opportunities=[dict(id="o1", subreddit="saas", title="Finding customers", post_url="https://www.reddit.com/r/saas/comments/p1/",
                                                    why="answers", draft="Start where buyers complain.", status="pending")])
    page = open_ui(browser, api)
    no_native_dialogs(page)
    # Start in a sending mode asks first
    page.select_option("select[aria-label='What should the agent do?']", "full")
    page.click("button:text-is('Start agent')")
    dlg = page.wait_for_selector("[role=alertdialog]")
    assert "Reddit 1" in dlg.inner_text()
    page.click("[role=alertdialog] button:text-is('Cancel')")
    assert not api.called("POST", "/profiles/1/pigeon")
    page.click("button:text-is('Start agent')")
    page.click("[role=alertdialog] button:has-text('Start')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/profiles/1/pigeon") == [{"action": "start", "mode": "full"}]
    # Approve all asks
    open_drawer(page)
    page.click("#pg button:has-text('Approve all')")
    page.wait_for_selector("[role=alertdialog]")
    page.click("[role=alertdialog] button:has-text('Approve')")
    page.wait_for_timeout(300)
    assert api.ops_called("lead_approve") == [{"all": True}]
    # Approve & post lets you edit the comment first, then asks
    page.click("#pg [role=tab]:text-is('Comments')")
    page.wait_for_selector("#pg textarea[aria-label='Comment for r/saas']")
    page.fill("#pg textarea[aria-label='Comment for r/saas']", "Edited comment")
    page.click("#pg button:text-is('Approve & post')")
    dlg = page.wait_for_selector("[role=alertdialog]")
    assert "r/saas" in dlg.inner_text()
    page.click("[role=alertdialog] button:has-text('Post')")
    page.wait_for_timeout(400)
    assert api.ops_called("opp_edit")[-1] == {"id": "o1", "draft": "Edited comment"}
    assert api.jobs_called("post") == [{"id": "o1"}]
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    # deleting an account names it
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.click("[role=dialog] button:text-is('Delete')")
    dlg = page.wait_for_selector("[role=alertdialog]")
    assert "Delete Reddit 1" in dlg.inner_text()
    page.click("[role=alertdialog] button:text-is('Delete account')")
    page.wait_for_timeout(300)
    assert api.called("DELETE", "/profiles/1")
    assert page.native == [] and page.errors == []


@needs_ui
def test_p0_5_owner_role_change_and_force_unlock_confirm(browser):
    api = FakeApi(state(members=[dict(id=1, name="Owner", email="owner@x.io", role="owner"), dict(id=2, name="Bea", email="bea@x.io", role="member")],
                        profiles=[profile(running={"by": "bea@x.io", "device": "pc"})]))
    page = open_ui(browser, api)
    no_native_dialogs(page)
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Force unlock')")
    page.click("[role=alertdialog] button:has-text('Force unlock')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/profiles/1/unlock")
    open_settings(page, "Members")
    page.select_option("select[aria-label='Role for bea@x.io']", "owner")
    dlg = page.wait_for_selector("[role=alertdialog]")
    assert "bea@x.io" in dlg.inner_text()
    page.click("[role=alertdialog] button:has-text('Make owner')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/members/2/role") == [{"role": "owner"}]
    assert page.native == []


@needs_ui
def test_p0_6_clear_unsent_leads(browser):
    page, api = drawer(browser)
    no_native_dialogs(page)
    page.click("#pg button:text-is('Clear unsent leads')")
    page.click("[role=alertdialog] button:has-text('Clear')")
    page.wait_for_timeout(300)
    assert api.ops_called("leads_clear") == [{"unsent": True}]
    assert "Clear all" not in page.inner_text("#pg") and page.native == []


@needs_ui
def test_p0_7_ai_key_field_and_errors_point_to_it(browser):
    st = status(last=dict(kind="run", ok=False, error="No DeepSeek key: `pigeon agent config --deepseek-key KEY` or set DEEPSEEK_API_KEY."))
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=st)}))
    api.status = st
    page, _ = drawer(browser, api)
    body = page.inner_text("#pg")
    assert "pigeon agent config" not in body and "DEEPSEEK_API_KEY" not in body
    page.click("#pg button:text-is('Add AI key')")
    page.wait_for_selector("#ai_key")
    page.fill("#ai_key", "sk-abcdef1234567890")
    page.click("#pg button:text-is('Save key')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/pigeon/key") == [{"key": "sk-abcdef1234567890"}]


@needs_ui
def test_p0_8_no_key_screen_has_invite_input_and_sign_in_again(browser):
    api = FakeApi(state(has_key=False, profiles=None))
    page = open_ui(browser, api)
    page.wait_for_selector("text=This device doesn't have the team's encryption key")
    page.fill("#keycode", "abc.def.key")
    page.click("button:text-is('Join')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/join") == [{"code": "abc.def.key"}]
    page.click("button:text-is('Sign in again')")
    page.wait_for_timeout(300)
    assert api.called("POST", "/logout")


def test_p0_8_a_member_without_the_key_can_paste_the_full_code(desk):
    t, _ = desk
    t.post("/api/profiles", json={"name": "Shared"})
    full = t.post("/api/invite", json={"role": "admin"}).json()["code"]  # an admin sees every account, so the key can be checked
    server = t.get("/api/state").json()["server"]
    t.post("/api/logout")
    code_only = full.split(".", 1)[0]  # someone who signed up with the code but lost the key part
    assert t.post("/api/login", json={"server": server, "email": "b@x.io", "password": "password2", "signup": True, "invite": code_only}).status_code == 200
    assert t.get("/api/state").json()["has_key"] is False
    bad = full.split(".", 1)[0] + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    assert t.post("/api/join", json={"code": bad}).status_code == 400  # a key that doesn't open the team's accounts is refused
    assert t.get("/api/state").json()["has_key"] is False
    r = t.post("/api/join", json={"code": full})  # they're already a member: the code is used only for its key
    assert r.status_code == 200, r.text
    s = t.get("/api/state").json()
    assert s["has_key"] is True and sorted(p["name"] for p in s["profiles"]) == ["My Reddit account", "Shared"]  # an admin sees every account, incl. the team's starter
    assert t.post("/api/join", json={"code": "nokeypart"}).status_code in (400, 404)
