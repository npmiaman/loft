"""Pigeon's full feature set, and its shared team memory, exercised for real:

  * ops (no browser): every state/LLM operation behind the CLI and the desktop panel
  * the central brain across accounts: no double DMs, no double comments, context in prompts, fail-closed when unreachable
  * one live browser session driven through its control channel, using every feature end to end

Browser parts run on a Chromium context against Pigeon's mock of Reddit (PIGEON_TEST_CHROMIUM) or Camoufox if installed."""
import json
import threading
import time

import httpx
import pytest

from pigeon.client import PigeonError

from pigeon import control
from pigeon.agent import brain as brain_mod
from pigeon.agent import ops
from pigeon.agent.agent import Agent
from pigeon.agent.bridge import Bridge, Stop
from pigeon.agent.central import CentralBrain
from pigeon.agent.llm import Llm
from pigeon.agent.runner import state_path
from pigeon.agent.state import State
from pigeon.session import interactive
from tests.conftest import FP
from tests.test_pigeon import (browser, fake_deepseek, fast, host_for, lead, make_state, mock_context, needs_browser,  # noqa: F401
                               serve_session_thread,
                               page, seed_state)


def ctx_for(tmp_path, brain=None, prompts=None, **settings) -> ops.Ctx:
    st = make_state(tmp_path, **settings)
    base = fake_deepseek()

    def handler(req):
        if prompts is not None:
            prompts.append(json.loads(req.content)["messages"][1]["content"])
        return base.handler(req)
    return ops.Ctx(st, Llm("k", transport=httpx.MockTransport(handler)), brain)


@pytest.fixture
def team(mk):
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    a = owner.create_profile(tid, name="acct-a", fingerprint=FP)["id"]
    b = owner.create_profile(tid, name="acct-b", fingerprint=FP)["id"]
    return owner, tid, a, b


# ------------------------------------------------------------------ ops (no browser)
def test_settings_product_and_subreddit_edits(tmp_path):
    c = ctx_for(tmp_path)
    r = ops.run_state_op(c, "settings_set", dict(pairs=dict(daily_dm_cap="12", warmup="false", min_fit="65", dm_mode="template"), restart_warmup=True))
    s = r["settings"]
    assert (s["daily_dm_cap"], s["warmup"], s["min_fit"], s["dm_mode"]) == (12, False, 65, "template") and s["warmup_start"] > 0
    with pytest.raises(RuntimeError, match="unknown setting"):
        ops.run_state_op(c, "settings_set", dict(pairs=dict(bogus=1)))

    ops.run_state_op(c, "product_set", dict(fields=dict(icp="indie hackers", competitors="A|B | C")))
    assert c.st.product["icp"] == "indie hackers" and c.st.product["competitors"] == ["A", "B", "C"]
    with pytest.raises(RuntimeError, match="unknown product field"):
        ops.run_state_op(c, "product_set", dict(fields=dict(nope="x")))

    ops.run_state_op(c, "subs_edit", dict(add=["r/SaaS", "indiehackers"]))
    assert {s["name"] for s in c.st.subreddits} == {"startups", "saas", "indiehackers"}
    ops.run_state_op(c, "subs_edit", dict(disable=["startups"], remove=["indiehackers"]))
    assert {s["name"]: s["enabled"] for s in c.st.subreddits} == {"startups": False, "saas": True}


def test_template_spin_preview_undo(tmp_path):
    c = ctx_for(tmp_path)
    ops.run_state_op(c, "template_set", dict(body="Hi {name}, saw your post about {topic}."))
    r = ops.run_state_op(c, "template_spin", dict(intensity="medium"))
    assert r["variants"] >= 8 and len(r["previews"]) == 4 and "{name}" in r["template"]
    assert c.st.template_undo == "Hi {name}, saw your post about {topic}."
    ops.run_state_op(c, "template_undo")
    assert c.st.templates[0].startswith("Hi {name}") and c.st.template_undo is None
    with pytest.raises(RuntimeError, match="nothing to undo"):
        ops.run_state_op(c, "template_undo")
    with pytest.raises(RuntimeError, match="empty"):
        ops.run_state_op(c, "template_set", dict(body="  "))


def test_spintax_rejects_bad_model_output():
    bad = Llm("k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
        {"template": "{Hey|Hi} {name} http://spam.example"})}}]})))
    with pytest.raises(RuntimeError, match="could not produce a valid template"):
        brain_mod.add_spintax(bad, "Hi {name}")
    assert brain_mod.valid_spintax("Hi {name}", "Hi") == "lost variable {name}"
    assert brain_mod.valid_spintax("x", "{a|{b|c}}") == "unbalanced or nested braces"


def test_lead_lifecycle_ops(tmp_path):
    c = ctx_for(tmp_path, dm_mode="template", approve_ai_drafts=True)
    c.st.put_lead(dict(lead("alice", status="queued"), message=None))
    c.st.put_lead(dict(lead("bob", status="queued"), message=None, snippet=""))
    r = ops.run_state_op(c, "lead_write", dict(all=True))
    assert r["written"] == 2 and c.st.get_lead("alice")["status"] == "ready"  # template mode needs no approval
    assert "alice" in c.st.get_lead("alice")["message"] or "saas" in c.st.get_lead("alice")["message"]

    c.st.settings["dm_mode"] = "ai"
    c.st.put_lead(dict(lead("carol", status="queued"), message=None))
    ops.run_state_op(c, "lead_write", dict(username="carol"))
    assert c.st.get_lead("carol")["status"] == "draft"  # AI drafts wait for approval

    ops.run_state_op(c, "lead_edit", dict(username="carol", message="my own words"))
    assert c.st.get_lead("carol")["message"] == "my own words"
    ops.run_state_op(c, "lead_approve", dict(username="carol"))
    assert c.st.get_lead("carol")["status"] == "ready"
    ops.run_state_op(c, "lead_skip", dict(username="carol"))
    assert c.st.get_lead("carol")["status"] == "skipped"
    with pytest.raises(RuntimeError, match="only sent leads"):
        ops.run_state_op(c, "lead_replied", dict(username="alice"))
    c.st.get_lead("alice")["status"] = "sent"
    ops.run_state_op(c, "lead_replied", dict(username="alice"))
    with pytest.raises(RuntimeError, match="already sent"):
        ops.run_state_op(c, "lead_edit", dict(username="alice", message="x"))
    with pytest.raises(RuntimeError, match="already"):
        ops.run_state_op(c, "lead_skip", dict(username="alice"))


def test_import_export_and_clear(tmp_path):
    c = ctx_for(tmp_path)
    c.st.put_lead(lead("existing"))
    r = ops.run_state_op(c, "leads_import", dict(text="spez\nu/example_user,extra\nhttps://www.reddit.com/user/Some-One/\nnot valid!\nexisting\nspez\n"))
    assert r["added"] == 3 and {"spez", "example_user", "some-one"} <= set(c.st.leads)
    csv_text = ops.run_state_op(c, "leads_export")["csv"]
    rows = list(__import__("csv").reader(csv_text.splitlines()))
    assert rows[0] == ["username", "subreddit", "fit", "status", "reason", "message", "post_url"] and len(rows) == 5
    assert any(r[0] == "existing" and r[6] == "https://www.reddit.com/r/saas/comments/p1/t1/" for r in rows[1:])
    c.st.get_lead("existing")["message"] = 'He said "hi", twice'
    assert '"He said ""hi"", twice"' in ops.run_state_op(c, "leads_export")["csv"]  # quotes and commas survive
    assert ops.run_state_op(c, "leads_clear")["out"] == "deleted 4 lead(s)" and not c.st.leads


# ------------------------------------------------------------------ the central brain, across accounts
def test_ops_use_the_team_memory(tmp_path, team):
    owner, tid, a, b = team
    brain_a, brain_b = CentralBrain(owner, tid, a), CentralBrain(owner, tid, b)
    brain_a.finish(brain_a.claim("dm", "alice", subreddit="saas"), "done")
    brain_a.finish(brain_a.claim("comment", "/r/saas/comments/p9/x/", subreddit="saas", title="Old thread", body="Start where buyers complain."), "done")

    # importing a list skips people another account already reached
    c = ctx_for(tmp_path, brain=brain_b)
    r = ops.run_state_op(c, "leads_import", dict(text="alice\nnewperson"))
    assert r["added"] == 1 and "team already contacted" in r["out"] and c.st.get_lead("alice") is None

    # a lead queued before the team reached them is skipped when it is time to write
    c.st.put_lead(dict(lead("alice", status="queued"), message=None))
    ops.run_state_op(c, "lead_write", dict(username="alice"))
    assert c.st.get_lead("alice")["status"] == "skipped" and "acct-a" in c.st.get_lead("alice")["error"]

    # the writing prompts carry what the team already did in that subreddit
    prompts = []
    c2 = ctx_for(tmp_path, brain=brain_b, prompts=prompts)
    c2.st.put_lead(dict(lead("zed", status="queued"), message=None))
    ops.run_state_op(c2, "lead_write", dict(username="zed"))
    assert "TEAM ACTIVITY in r/saas" in prompts[-1] and "Start where buyers complain" in prompts[-1]


def test_post_ideas_are_recorded_once_across_the_team(tmp_path, team):
    owner, tid, a, b = team
    ca, cb = ctx_for(tmp_path / "a", brain=CentralBrain(owner, tid, a)), ctx_for(tmp_path / "b", brain=CentralBrain(owner, tid, b))
    for c in (ca, cb):
        ops.run_state_op(c, "ideas_generate")
        assert len(c.st.post_ideas) == 5
    iid = ca.st.post_ideas[0]["id"]
    assert "submit" in ops.run_state_op(ca, "idea_posted", dict(id=iid))["out"]
    cb.st.post_ideas[0]["title"] = "Idea 0"  # the same idea, from the other account
    with pytest.raises(RuntimeError, match="already posted"):
        ops.run_state_op(cb, "idea_posted", dict(id=cb.st.post_ideas[0]["id"]))
    lone = ctx_for(tmp_path / "c")  # no central brain: refuse rather than record nothing
    lone.st.post_ideas.append(dict(id="x", subreddit="saas", title="T", body="b", angle="a"))
    with pytest.raises(RuntimeError, match="unavailable"):
        ops.run_state_op(lone, "idea_posted", dict(id="x"))
    assert owner.pigeon_summary(tid)["by_kind"]["post"] == 1


def test_ingest_skips_people_the_team_already_contacted(tmp_path, team):
    owner, tid, a, b = team
    CentralBrain(owner, tid, a).finish(CentralBrain(owner, tid, a).claim("dm", "founder_amy"), "done")
    st = make_state(tmp_path)
    ag = Agent(None, st, Llm("k", transport=fake_deepseek()), CentralBrain(owner, tid, b))
    res = ag.ingest([dict(id=f"p{n}", kind="post", author=u, subreddit="saas", title="t", text="I need help finding customers badly",
                          post_url=f"/r/saas/comments/p{n}/", post_title="t") for n, u in enumerate(["founder_amy", "dev_bob"])])
    assert set(st.leads) == {"dev_bob"} and set(res) == {"p1"}  # amy was neither scored nor queued


def test_sending_is_refused_when_the_brain_cannot_be_asked(tmp_path):
    class Down:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise ConnectionError("server down")
            return boom
    st = make_state(tmp_path)
    st.put_lead(lead("alice"))
    ag = Agent(None, st, None, CentralBrain(Down(), 1, 1))  # no bridge: it must not even try to open a page
    with pytest.raises(Stop):
        ag.send_dm(st.get_lead("alice"))
    assert st.get_lead("alice")["status"] == "ready" and any("Not sending" in e["msg"] for e in st.logs)
    with pytest.raises(RuntimeError, match="Not commenting"):
        st.opportunities.append(dict(id="o", post_url="https://www.reddit.com/r/saas/comments/p1/t1/", subreddit="saas", title="T", draft="d", status="pending"))
        ag.post_opportunity("o")


@needs_browser
def test_two_accounts_never_dm_the_same_person_or_comment_twice(page, tmp_path, fast, team):
    owner, tid, a, b = team
    st_a, st_b = make_state(tmp_path / "a"), make_state(tmp_path / "b")
    for st in (st_a, st_b):
        st.put_lead(lead("alex_dev"))
        st.opportunities.append(dict(id="/r/saas/comments/p1/t1/", post_url="https://www.reddit.com/r/saas/comments/p1/t1/", subreddit="saas",
                                     title="Finding customers", draft="Start where buyers already complain.", status="pending", why=""))
    ag_a = Agent(Bridge(page, pace=2), st_a, None, CentralBrain(owner, tid, a))
    ag_b = Agent(Bridge(page, pace=2), st_b, None, CentralBrain(owner, tid, b))
    page.goto("https://www.reddit.com/")

    ag_a.run("dm", minutes=0.5)
    assert st_a.get_lead("alex_dev")["status"] == "sent"
    ag_b.run("dm", minutes=0.5)
    skipped = st_b.get_lead("alex_dev")
    assert skipped["status"] == "skipped" and "acct-a" in skipped["error"]
    assert page.seen["users"] == ["alex_dev"]  # account B never even opened their profile

    ag_a.post_opportunity("/r/saas/comments/p1/t1/")
    assert st_a.opportunities[0]["status"] == "posted"
    with pytest.raises(RuntimeError, match="team already commented.*acct-a"):
        ag_b.post_opportunity("/r/saas/comments/p1/t1/")
    assert st_b.opportunities[0]["status"] == "skipped"
    assert owner.pigeon_summary(tid)["by_kind"] == {"dm": 1, "comment": 1}


@needs_browser
def test_failed_send_releases_the_claim_for_another_account(page, tmp_path, fast, team):
    owner, tid, a, b = team
    st = make_state(tmp_path)
    st.put_lead(lead("ratelimited"))  # the mock shows a rate-limit banner for this user
    ag = Agent(Bridge(page, pace=2), st, None, CentralBrain(owner, tid, a))
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=0.5)
    assert st.get_lead("ratelimited")["status"] == "failed"
    assert CentralBrain(owner, tid, b).claim("dm", "ratelimited").ok  # released: not the person's fault, another account may try later


# ------------------------------------------------------------------ one live session, every feature, through the control channel
@needs_browser
def test_every_feature_inside_one_live_session(mock_context, tmp_path, fast, team, monkeypatch):
    owner, tid, pid, _ = team
    monkeypatch.setattr(brain_mod, "scrape_site", lambda url, http=None: "TITLE: Acme\nTEXT: we help founders find customers")
    seed_state(tmp_path, pid, pace=2.0, dm_mode="template")
    st0 = State(state_path(pid, tmp_path))
    st0.product, st0.subreddits = None, []
    st0.opportunities.append(dict(id="/r/saas/comments/p1/t1/", post_url="https://www.reddit.com/r/saas/comments/p1/t1/", subreddit="saas",
                                  title="Finding customers", draft="Start where buyers already complain.", status="pending", why=""))
    st0.save()
    user_tab = mock_context.pages[0]
    user_tab.goto("https://www.reddit.com/r/saas/")  # the Reddit tab "you are looking at", for the page scan
    seen: dict = {}

    def operator():
        try:
            for _ in range(100):
                if control.is_live(pid, tmp_path):
                    break
                time.sleep(0.1)
            req = lambda m, p, b=None: control.request(pid, m, p, b, home=tmp_path, timeout=120)
            op = lambda name, **args: req("POST", "/pigeon/op", {"op": name, "args": args})

            def job(kind, **args):
                req("POST", "/pigeon/job", {"kind": kind, "args": args})
                for _ in range(600):
                    s = req("GET", "/pigeon/status")
                    if s["state"] == "idle":
                        assert s["last"]["ok"], (kind, s["last"])
                        return s
                    time.sleep(0.3)
                raise AssertionError(f"{kind} never finished")

            seen["blocked_without_key"] = None
            job("setup", url="https://acme.test")                                    # Setup: analyze + discover subreddits
            seen["subs"] = op("subs_edit")["subreddits"]
            op("subs_edit", disable=["saas"])
            op("subs_edit", enable=["saas"])
            job("scan", no_score=True)                                               # Page DMer on the tab you have open
            seen["after_scan"] = sorted(l["username"] for l in op("leads_list")["leads"])
            op("lead_write", all=True)                                               # Write messages (template mode: ready)
            op("lead_skip", username="sam_k")
            op("leads_import", text="newperson1\nfounder_amy")                       # List DMer
            job("send", username="dev_bob")                                          # Send now
            seen["bob"] = op("leads_list", status="sent")["leads"]
            job("post", id="/r/saas/comments/p1/t1/")                                # Approve & post a comment
            op("ideas_generate")                                                     # Post ideas, then record one as posted
            idea = req("POST", "/pigeon/op", {"op": "ideas_list", "args": {}})["ideas"][0]
            op("idea_posted", id=idea["id"])
            job("replies")                                                           # Check replies
            seen["export"] = op("leads_export")["csv"]
            seen["final"] = req("GET", "/pigeon/status")
        except BaseException as e:
            seen["error"] = e
        finally:
            seen["done"] = True

    t = threading.Thread(target=operator, daemon=True)
    t.start()
    interactive(mock_context, pid, {"team_id": tid}, home=tmp_path, client=owner,
                host_factory=host_for(tmp_path, pid, quit_when=lambda: seen.get("done"), keep_state=True))
    t.join(10)
    assert "error" not in seen, seen.get("error")

    assert [s["name"] for s in seen["subs"]] == ["saas"] and seen["subs"][0]["enabled"]
    assert seen["after_scan"] == ["dev_bob", "founder_amy", "sam_k"]
    assert [l["username"] for l in seen["bob"]] == ["dev_bob"]
    assert "dev_bob" in seen["export"] and "newperson1" in seen["export"]
    assert seen["final"]["central"] is True

    final = State(state_path(pid, tmp_path))
    assert final.product["name"] == "Acme" and final.get_lead("sam_k")["status"] == "skipped"
    assert final.get_lead("founder_amy")["status"] == "ready" and final.get_lead("newperson1")["status"] == "queued"
    assert final.opportunities[0]["status"] == "posted" and len(final.post_ideas) == 4
    assert final.get_lead("dev_bob")["message"].startswith(("Hey", "Hi", "Hello")) and "dev_bob" in final.get_lead("dev_bob")["message"]
    # the whole thing is on the team's shared record
    s = owner.pigeon_summary(tid)
    assert s["by_kind"] == {"dm": 1, "comment": 1, "post": 1}, s
    assert owner.pigeon_events(tid, kind="dm")[0]["target"] == "dev_bob"
    assert mock_context.seen["users"] == ["dev_bob"]  # only the one DM was sent; skipped/ready leads were not touched


# ------------------------------------------------------------------ the CLI: routing and output
def _parse(argv):
    import argparse
    from pigeon.agent.cli import register
    ap = argparse.ArgumentParser()
    register(ap.add_subparsers(dest="cmd", required=True))
    return ap.parse_args(["pigeon", *argv])


def test_cli_brain_output(team, capsys):
    from pigeon.agent.cli import handle
    owner, tid, a, b = team
    ba = CentralBrain(owner, tid, a)
    ba.finish(ba.claim("dm", "alice", subreddit="saas"), "done")
    ba.finish(ba.claim("comment", "/r/saas/comments/p1/t1/", subreddit="saas", title="Finding customers", body="x"), "done")
    stuck = ba.claim("dm", "stuck_one", subreddit="saas")

    handle(_parse(["brain"]), owner, lambda: tid)
    out = capsys.readouterr().out
    assert "2 DMs" in out and "1 comments" in out and "0 posts" in out and "r/saas" in out and "acct-a" in out

    handle(_parse(["brain", "--who", "alice", "nobody"]), owner, lambda: tid)
    out = capsys.readouterr().out
    assert "done by acct-a" in out and "nobody" in out and "not contacted" in out

    handle(_parse(["brain", "--events", "--kind", "comment"]), owner, lambda: tid)
    assert "Finding customers" in capsys.readouterr().out

    handle(_parse(["brain", "--release", str(stuck.id)]), owner, lambda: tid)
    assert CentralBrain(owner, tid, b).claim("dm", "stuck_one").ok  # an admin freed the stuck claim


def test_cli_commands_route_to_the_live_session(tmp_path, monkeypatch, capsys):
    import pigeon.control as ctl_mod
    from pigeon.agent.cli import handle
    monkeypatch.setattr(ctl_mod, "PROFILES", tmp_path)
    seed_state(tmp_path, 3, dm_mode="template")
    host = host_for(tmp_path, 3)(3, tmp_path)
    host.st.put_lead(dict(lead("alice", status="draft")))
    ctl = ctl_mod.SessionControl(3, tmp_path)
    host.mount(ctl)
    ctl.start()
    stop = serve_session_thread(host)
    try:
        run = lambda *argv: (handle(_parse(list(argv)), None), capsys.readouterr().out)[1]
        assert "alice" in run("leads", "3") and "draft" in run("leads", "3", "--status", "draft")
        assert run("approve", "3", "--all") == "approved 1\n"
        assert "ready" in run("leads", "3", "--status", "ready")
        run("template", "3", "--set", "Hi {name}!")
        assert "Hi {name}!" in run("template", "3")
        assert "daily_dm_cap" in run("set", "3", "daily_dm_cap=9")
        assert host.st.settings["daily_dm_cap"] == 9
        assert "agent: idle" in run("status", "3")
        with pytest.raises(SystemExit, match="already open"):
            run("run", "3")
        assert "attached" in run("attach", "3", "--mode", "find")
        assert "already running" in str(pytest.raises(PigeonError, handle, _parse(["attach", "3"]), None).value)  # one agent per session
        assert run("detach", "3") == "Agent is idle\n"  # it was still queued, so detaching just drops it
    finally:
        stop.set()
        ctl.close()


def test_backfill_records_history_once_with_original_dates(tmp_path, team):
    owner, tid, a, b = team
    old = time.time() - 5 * 86400
    st = make_state(tmp_path)
    st.put_lead(dict(lead("alice"), status="sent", sent_at=old))
    st.put_lead(dict(lead("bob"), status="replied", sent_at=old))
    st.put_lead(lead("carol"))  # ready, not sent: not history
    st.opportunities.append(dict(id="o1", post_url="https://www.reddit.com/r/saas/comments/p1/t1/", subreddit="saas", title="T",
                                 draft="d", status="posted", created_at=old, why=""))
    c = ops.Ctx(st, None, CentralBrain(owner, tid, a))
    r = ops.run_state_op(c, "brain_backfill")
    assert r["added"] == 3 and r["known"] == 0
    assert ops.run_state_op(c, "brain_backfill")["added"] == 0  # idempotent

    evs = owner.pigeon_events(tid, kind="dm")
    assert {e["target"] for e in evs} == {"alice", "bob"} and all(abs(e["at"] - old) < 5 for e in evs)  # original dates kept
    # and now another account is stopped from messaging them, but can still reach carol
    other = CentralBrain(owner, tid, b)
    assert not other.claim("dm", "alice").ok and not other.claim("comment", "/r/saas/comments/p1/t1/").ok and other.claim("dm", "carol").ok
    with pytest.raises(RuntimeError, match="unavailable"):
        ops.run_state_op(ops.Ctx(st), "brain_backfill")
