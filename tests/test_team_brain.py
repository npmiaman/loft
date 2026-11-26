"""The rest of the team's shared memory: our own accounts, one shared setup, team-wide limits, shared leads, upvotes,
replies and what the team has learned. Server rules, the client, ops, agent behaviour and the CLI."""
import json
import time

import pytest

from loft.client import LoftError
from loft.pigeon import ops
from loft.pigeon.agent import Agent, TeamHold
from loft.pigeon.central import CentralBrain
from loft.pigeon.state import State
from tests.conftest import FP
from tests.test_pigeon import (browser, fast, lead, make_state, mock_context, needs_browser, page)  # noqa: F401
from tests.test_pigeon_full import _parse, ctx_for


@pytest.fixture
def team(mk):
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    member = mk()
    member.register("m@x.io", "password1", invite=owner.invite(tid, "member")["code"])
    a = owner.create_profile(tid, name="acct-a", fingerprint=FP)["id"]
    b = owner.create_profile(tid, name="acct-b", fingerprint=FP)["id"]
    mine = member.create_profile(tid, name="members-acct", fingerprint=FP)["id"]
    return owner, member, tid, a, b, mine


def brains(owner, tid, a, b):
    return CentralBrain(owner, tid, a), CentralBrain(owner, tid, b)


# ------------------------------------------------------------------ our own accounts
def test_account_registry_and_validation(team, mk):
    owner, member, tid, a, b, mine = team
    assert owner.pigeon_set_account(tid, a, "u/Acct_A")["username"] == "acct_a"  # normalised
    owner.pigeon_set_account(tid, a, "acct_a2")  # re-registering the same profile just updates it
    owner.pigeon_set_account(tid, b, "acct_b")
    assert [(r["profile"], r["username"]) for r in owner.pigeon_accounts(tid)] == [("acct-a", "acct_a2"), ("acct-b", "acct_b")]
    with pytest.raises(LoftError, match="already registered"):
        owner.pigeon_set_account(tid, b, "acct_a2")  # one Reddit user, one profile
    with pytest.raises(LoftError, match="username"):
        owner.pigeon_set_account(tid, b, "no spaces!")
    with pytest.raises(LoftError, match="profile not found"):
        member.pigeon_set_account(tid, a, "someone_else")  # no access to that profile
    member.pigeon_set_account(tid, mine, "members_user")  # but their own is fine
    outsider = mk()
    outsider.register("x@x.io", "password1", invite=owner.invite(owner.create_team("Other")["id"])["code"])
    with pytest.raises(LoftError):
        outsider.pigeon_accounts(tid)


def test_nobody_targets_our_own_accounts(team):
    owner, _, tid, a, b, _ = team
    owner.pigeon_set_account(tid, a, "acct_a")
    owner.pigeon_set_account(tid, b, "acct_b")
    ba, bb = brains(owner, tid, a, b)
    for who, target in ((bb, "acct_a"), (bb, "u/ACCT_A"), (ba, "acct_a"), (ba, "acct_b")):  # incl. an account targeting itself
        c = who.claim("dm", target)
        assert not c.ok and c.own and "one of our own accounts" in c.why()
    assert not bb.claim("lead", "acct_a").ok
    # comments and upvotes are on permalinks, so the author is passed along
    assert not bb.claim("upvote", "/r/x/comments/p1/t/", target_user="acct_a").ok
    assert bb.claim("upvote", "/r/x/comments/p2/t/", target_user="somebody").ok
    assert bb.claim("dm", "a_normal_user").ok
    assert bb.own_usernames() == {"acct_a", "acct_b"}


def test_register_account_reports_clashes_instead_of_raising(team):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    assert ba.register_account("same_user") is None
    assert "already registered" in bb.register_account("same_user")
    assert ba.register_account("") is None


# ------------------------------------------------------------------ one shared setup
def test_team_config_is_versioned_and_admin_only(team):
    owner, member, tid, *_ = team
    assert owner.pigeon_config(tid) == dict(version=0, setup=None, unreadable=False, limits={}, updated=0.0)
    assert owner.pigeon_put_config(tid, 0, setup={"product": {"name": "Acme"}})["version"] == 1
    with pytest.raises(LoftError, match="newer setup"):
        owner.pigeon_put_config(tid, 0, setup={"product": {"name": "Stale"}})  # based on an old version
    with pytest.raises(LoftError, match="needs role"):
        member.pigeon_put_config(tid, 1, setup={"x": 1})
    assert member.pigeon_config(tid)["setup"]["product"]["name"] == "Acme"  # anyone on the team (who holds its key) can read it
    with pytest.raises(LoftError, match="too large"):
        owner.pigeon_put_config(tid, 1, setup={"blob": "x" * 400_000})


def test_the_shared_setup_is_ciphertext_on_the_server_but_limits_are_plain(team):
    owner, member, tid, *_ = team
    owner.pigeon_put_config(tid, 0, setup={"product": {"name": "Secret Product Name", "icp": "confidential ICP"}})
    owner.pigeon_put_config(tid, 1, limits={"team_dm_per_day": 40, "bogus": "not-a-number", "negative": -3, "subreddit_dm_per_day": 8})
    raw = owner.j("GET", f"/teams/{tid}/pigeon/config")  # exactly what the server holds
    assert raw["setup"] and "Secret Product Name" not in raw["setup"] and "confidential" not in raw["setup"]
    assert raw["limits"] == {"team_dm_per_day": 40, "subreddit_dm_per_day": 8}  # plain numbers, so the server can enforce them
    got = member.pigeon_config(tid)
    assert got["setup"]["product"]["icp"] == "confidential ICP" and got["limits"] == raw["limits"] and not got["unreadable"]
    owner.pigeon_put_config(tid, 2, limits={"team_dm_per_day": 5})  # changing limits leaves the setup alone
    assert owner.pigeon_config(tid)["setup"]["product"]["name"] == "Secret Product Name"


def test_a_team_key_rotation_carries_the_shared_setup_along(team):
    """Upstream's rotation re-encrypts everything; the shared setup must come through it readable by the remaining members."""
    from loft.client import rotate_team_key
    owner, member, tid, *_ = team
    owner.pigeon_put_config(tid, 0, setup={"product": {"name": "Acme"}})
    before = owner.j("GET", f"/teams/{tid}/pigeon/config")["setup"]
    rotate_team_key(owner, tid, log=lambda m: None)
    after = owner.j("GET", f"/teams/{tid}/pigeon/config")
    assert after["setup"] != before  # re-sealed with the new key
    assert owner.pigeon_config(tid)["setup"] == {"product": {"name": "Acme"}} and not owner.pigeon_config(tid)["unreadable"]
    assert member.pigeon_config(tid)["setup"] == {"product": {"name": "Acme"}}  # a remaining member gets the new key and reads it
    owner.pigeon_put_config(tid, after["version"], setup={"product": {"name": "Acme 2"}})  # and can publish under the new epoch
    assert member.pigeon_config(tid)["setup"]["product"]["name"] == "Acme 2"


def test_publish_then_other_accounts_adopt_it(team, tmp_path):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    src = ctx_for(tmp_path / "a", brain=ba, my_username="acct_a", daily_dm_cap=7, min_fit=55)
    src.st.subreddits[0]["last_visited"] = 123
    src.st.templates[0] = "Hi {name}, {saw|read} your post."
    r = ops.run_state_op(src, "team_publish")
    assert r["version"] == 1 and src.st.team_version == 1

    dst = ctx_for(tmp_path / "b", brain=bb, my_username="acct_b", daily_dm_cap=33)
    dst.st.product, dst.st.subreddits = None, [dict(name="startups", last_visited=999, enabled=True, score=1, rules_risk="low", reason="")]
    pulled = ops.run_state_op(dst, "team_pull")
    assert pulled["pulled"] and dst.st.team_version == 1
    assert dst.st.product["name"] == "Acme" and dst.st.templates[0].startswith("Hi {name}")
    assert dst.st.settings["min_fit"] == 55  # shared setting adopted
    assert dst.st.settings["daily_dm_cap"] == 33 and dst.st.settings["my_username"] == "acct_b"  # per-account safety stays
    assert dst.st.subreddits[0]["last_visited"] == 999  # keeps its own browsing history for subs it already had
    assert "last_visited" not in json.dumps(owner.pigeon_config(tid)["setup"]["subreddits"])  # and it isn't shared
    assert not ops.run_state_op(dst, "team_pull")["pulled"]  # nothing newer

    dst.st.product = dict(dst.st.product, name="Local edit")
    ops.run_state_op(dst, "team_pull")
    assert dst.st.product["name"] == "Local edit"  # a local edit survives until the team publishes something newer
    ops.run_state_op(src, "team_publish")
    ops.run_state_op(dst, "team_pull")
    assert dst.st.product["name"] == "Acme" and dst.st.team_version == 2


def test_an_account_can_opt_out_of_the_team_setup(team, tmp_path):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    ops.run_state_op(ctx_for(tmp_path / "a", brain=ba), "team_publish")
    c = ctx_for(tmp_path / "b", brain=bb, use_team_setup=False)
    c.st.product = dict(c.st.product, name="Mine")
    r = ops.run_state_op(c, "team_pull")
    assert not r["pulled"] and "ignores" in r["out"] and c.st.product["name"] == "Mine"
    assert ops.run_state_op(c, "team_pull", dict(force=True))["pulled"] and c.st.product["name"] == "Acme"


def test_publish_errors_are_readable(team, tmp_path):
    owner, member, tid, a, b, mine = team
    c = ctx_for(tmp_path / "m", brain=CentralBrain(member, tid, mine))
    with pytest.raises(RuntimeError, match="couldn't publish.*needs role"):
        ops.run_state_op(c, "team_publish")  # members can't publish
    empty = ctx_for(tmp_path / "e", brain=CentralBrain(owner, tid, a))
    empty.st.product = None
    with pytest.raises(RuntimeError, match="no product"):
        ops.run_state_op(empty, "team_publish")
    with pytest.raises(RuntimeError, match="unavailable"):
        ops.run_state_op(ctx_for(tmp_path / "n"), "team_pull")


def test_setting_my_username_registers_it_with_the_team(team, tmp_path):
    owner, _, tid, a, b, _ = team
    c = ctx_for(tmp_path / "a", brain=CentralBrain(owner, tid, a))
    assert "registered with the team" in ops.run_state_op(c, "settings_set", dict(pairs=dict(my_username="acct_a")))["out"]
    assert [r["username"] for r in owner.pigeon_accounts(tid)] == ["acct_a"]
    c2 = ctx_for(tmp_path / "b", brain=CentralBrain(owner, tid, b))
    assert "couldn't register" in ops.run_state_op(c2, "settings_set", dict(pairs=dict(my_username="acct_a")))["out"]


# ------------------------------------------------------------------ team-wide limits
def set_limits(owner, tid, **lim):
    owner.pigeon_put_config(tid, owner.pigeon_config(tid)["version"], limits=lim)


def test_team_dm_limit_counts_every_account_and_releases_on_failure(team):
    owner, _, tid, a, b, _ = team
    set_limits(owner, tid, team_dm_per_day=3)
    ba, bb = brains(owner, tid, a, b)
    ok = [ba.claim("dm", "u1"), bb.claim("dm", "u2"), ba.claim("dm", "u3")]
    assert all(c.ok for c in ok)
    hit = bb.claim("dm", "u4")
    assert not hit.ok and hit.limit == {"scope": "team", "kind": "dm", "cap": 3, "used": 3, "subreddit": ""}
    assert "team limit reached for the team: 3/3" in hit.why()
    ba.finish(ok[0], "failed", "rate-limit")  # a send that failed gives its slot back
    assert bb.claim("dm", "u4").ok
    assert owner.pigeon_summary(tid)["last_24h"]["dm"] == 3 and owner.pigeon_summary(tid)["limits"] == {"team_dm_per_day": 3}


def test_subreddit_and_comment_limits_and_backfill_exemption(team):
    owner, _, tid, a, b, _ = team
    set_limits(owner, tid, subreddit_dm_per_day=2, team_comment_per_day=1)
    ba, bb = brains(owner, tid, a, b)
    assert ba.claim("dm", "u1", subreddit="saas").ok and bb.claim("dm", "u2", subreddit="saas").ok
    c = ba.claim("dm", "u3", subreddit="saas")
    assert not c.ok and c.limit["scope"] == "subreddit" and "r/saas" in c.why()
    assert ba.claim("dm", "u4", subreddit="startups").ok  # other subreddits are unaffected
    assert ba.claim("comment", "/r/x/comments/p1/", subreddit="x").ok
    assert not bb.claim("comment", "/r/x/comments/p2/", subreddit="x").ok
    # history being backfilled isn't a real-time action: it never trips (or counts toward) a limit
    assert bb.claim("dm", "old1", subreddit="saas", at=time.time() - 86400 * 3).ok
    assert bb.claim("dm", "old2", subreddit="saas", at=time.time() - 3600 * 30).ok
    # and events older than 24h don't count against the rolling window
    assert owner.pigeon_summary(tid)["last_24h"]["dm"] == 3  # u1, u2, u4: the two backfilled ones are older than 24h


def test_limits_can_be_cleared(team):
    owner, _, tid, a, b, _ = team
    set_limits(owner, tid, team_dm_per_day=1)
    ba = CentralBrain(owner, tid, a)
    assert ba.claim("dm", "u1").ok and not ba.claim("dm", "u2").ok
    set_limits(owner, tid)
    assert ba.claim("dm", "u2").ok


# ------------------------------------------------------------------ shared leads, upvotes, replies
def test_lead_claims_expire_after_a_week(team):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    c = ba.claim("lead", "prospect", subreddit="saas")
    ba.finish(c, "done")
    assert not bb.claim("lead", "prospect").ok  # queued by A
    assert set(bb.seen_other("lead", ["prospect"])) == {"prospect"} and ba.seen_other("lead", ["prospect"]) == {}  # A's own doesn't count as "other"

    stale = ba.claim("lead", "cold_lead", at=time.time() - 8 * 86400)
    ba.finish(stale, "done")
    assert bb.seen("lead", ["cold_lead"]) == {}  # nobody acted on it for a week: free again
    took = bb.claim("lead", "cold_lead")
    assert took.ok and set(ba.seen("lead", ["cold_lead"])) == {"cold_lead"} and bb.seen("lead", ["cold_lead"])["cold_lead"]["profile"] == "acct-b"


def test_upvotes_and_replies_are_deduped_and_counted(team):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    assert ba.claim("upvote", "https://www.reddit.com/r/x/comments/p1/t/?utm=1", subreddit="x").ok
    assert not bb.claim("upvote", "/r/X/comments/p1/t").ok  # the same thread however it's linked
    ba.finish(ba.claim("dm", "alice", subreddit="x"), "done")
    ba.finish(ba.claim("reply", "alice", subreddit="x", body="Hey alice, quick question?"), "done")
    assert not bb.claim("reply", "ALICE").ok
    s = owner.pigeon_summary(tid)
    assert s["by_kind"]["upvote"] == 1 and s["by_kind"]["reply"] == 1
    assert s["by_subreddit"][0]["replies"] == 1 and s["by_subreddit"][0]["upvotes"] == 1
    assert next(p for p in s["by_profile"] if p["profile"] == "acct-a")["replies"] == 1


def test_context_shows_per_account_activity_reply_rate_and_what_worked(team):
    owner, _, tid, a, b, _ = team
    ba, bb = brains(owner, tid, a, b)
    for i in range(6):
        ba.finish(ba.claim("dm", f"u{i}", subreddit="saas"), "done")
    bb.finish(bb.claim("dm", "ub", subreddit="saas"), "done")
    ba.finish(ba.claim("reply", "u0", subreddit="saas", body="Hey u0, saw your post about onboarding. How do you handle it today?"), "done")
    bb.finish(bb.claim("comment", "/r/saas/comments/p1/", subreddit="saas", title="T", body="Start with churned users."), "done")
    ctx = ba.context("saas")
    assert "7 DMs sent, 1 comments, 0 posts, 1 replies received (14% reply rate)" in ctx
    assert "By account: acct-a: 6 DMs, 0 comments, 0 posts; acct-b: 1 DMs, 1 comments, 0 posts" in ctx
    assert "acct-b on" in ctx and "Start with churned users" in ctx
    assert "DMs that got a reply here" in ctx and "saw your post about onboarding" in ctx


# ------------------------------------------------------------------ agent behaviour (no browser: a stand-in bridge)
class FakeBridge:
    def __init__(self, cmds=None, me=None):
        self.cmds, self.me, self.sent, self.stop_flag = cmds or {}, me, [], False
        self.page = type("P", (), {"url": "https://www.reddit.com/"})()

    def cmd(self, c, timeout=0):
        self.sent.append(c)
        r = self.cmds.get(c["cmd"], {"ok": True})
        return r(c) if callable(r) else r

    def reddit_json(self, path):
        if self.me is None:
            raise RuntimeError("401")
        return {"data": {"name": self.me}}

    def check(self): pass
    def hud(self, *a): pass
    def ready(self, *a): pass
    def wait(self, *a): pass
    def navigate(self, *a, **k): self.sent.append({"cmd": "navigate", "url": a[0]})


def agent_for(tmp_path, owner, tid, pid, name="x", bridge=None, llm=True, **settings):
    from loft.pigeon.llm import Llm
    from tests.test_pigeon import fake_deepseek
    st = make_state(tmp_path / name, **settings)
    return Agent(bridge or FakeBridge(), st, Llm("k", transport=fake_deepseek()) if llm else None, CentralBrain(owner, tid, pid))


def entries(*authors):
    return [dict(id=f"p{n}", kind="post", author=u, subreddit="saas", title="t", text="I badly need help finding my first customers",
                 post_url=f"/r/saas/comments/p{n}/", post_title="t") for n, u in enumerate(authors)]


def test_ingest_skips_our_own_accounts_and_other_accounts_leads(team, tmp_path):
    owner, _, tid, a, b, _ = team
    owner.pigeon_set_account(tid, b, "acct_b")
    ag_a = agent_for(tmp_path, owner, tid, a, "a")
    ag_b = agent_for(tmp_path, owner, tid, b, "b", my_username="acct_b")
    res = ag_a.ingest(entries("acct_b", "prospect1", "prospect2"))
    assert set(ag_a.st.leads) == {"prospect1", "prospect2"} and "p0" not in res  # our own account was never even scored
    assert {e["kind"] for e in owner.pigeon_events(tid, kind="lead")} == {"lead"} and len(owner.pigeon_events(tid, kind="lead")) == 2
    assert owner.pigeon_summary(tid)["stuck_claims"] == 0  # lead claims are settled straight away

    res_b = ag_b.ingest(entries("prospect1", "prospect3"))
    assert set(ag_b.st.leads) == {"prospect3"} and set(res_b) == {"p1"}  # prospect1 is A's: not scored, not queued
    # an account re-seeing its OWN queued lead still re-scores it (it may upgrade the fit)
    ag_a.ingest(entries("prospect1"))
    assert ag_a.st.stats["scanned"] == 3


def test_upvote_is_claimed_once_for_the_whole_team(team, tmp_path):
    owner, _, tid, a, b, _ = team
    owner.pigeon_set_account(tid, b, "acct_b")
    ba_, bb_ = FakeBridge(), FakeBridge()
    ag_a, ag_b = agent_for(tmp_path, owner, tid, a, "a", ba_), agent_for(tmp_path, owner, tid, b, "b", bb_)
    ag_a.maybe_upvote("post", "/r/saas/comments/p1/t1/", "someone")
    ag_b.maybe_upvote("post", "/r/saas/comments/p1/t1/", "someone")  # same post, other account: refused
    ag_b.maybe_upvote("post", "/r/saas/comments/p2/t2/", "acct_b")   # its own account's post
    ag_a.maybe_upvote("post", "/r/saas/comments/p3/t3/", "acct_b")   # another of ours: refused for everyone
    assert [c["permalink"] for c in ba_.sent] == ["/r/saas/comments/p1/t1/"] and bb_.sent == []
    assert ag_a.st.stats["upvotes"] == 1 and owner.pigeon_summary(tid)["by_kind"]["upvote"] == 1

    failing = FakeBridge(cmds={"upvote": {"ok": False}})
    ag_c = agent_for(tmp_path, owner, tid, a, "c", failing)
    ag_c.maybe_upvote("post", "/r/saas/comments/p9/t9/", "z")
    assert ag_c.central.claim("upvote", "/r/saas/comments/p9/t9/").ok  # a failed click released the claim


def test_replies_are_recorded_for_the_team(team, tmp_path):
    owner, _, tid, a, b, _ = team
    rooms = [dict(text="alice: thanks, that sounds useful", unread=True),          # they replied, unread
             dict(text="bob\nsounds good, tell me more\n2h", unread=False),      # they replied and it's been read: still a reply
             dict(text="carol\nYou: Hey carol, quick question?\n1h", unread=False)]  # our own message is last: not a reply
    ag = agent_for(tmp_path, owner, tid, a, "a", FakeBridge(cmds={"scanInbox": {"ok": True, "rooms": rooms}}))
    for name in ("alice", "bob", "carol"):
        ag.st.put_lead(dict(lead(name), status="sent", message=f"Hey {name}, quick question?"))
    assert ag.check_replies() == 2
    assert [ag.st.get_lead(n)["status"] for n in ("alice", "bob", "carol")] == ["replied", "replied", "sent"]
    ev = owner.pigeon_events(tid, kind="reply")
    alice = next(e for e in ev if e["target"] == "alice")
    assert {e["target"] for e in ev} == {"alice", "bob"}
    assert alice["body"].startswith("enc1:") and "quick question" not in alice["body"]  # the server holds ciphertext
    assert owner.open_text(tid, alice["body"]) == "Hey alice, quick question?"  # members with the team key read it back
    # marking one by hand records it too
    ag.st.get_lead("carol")["status"] = "sent"
    ops.run_state_op(ops.Ctx(ag.st, None, ag.central), "lead_replied", dict(username="carol"))
    assert {e["target"] for e in owner.pigeon_events(tid, kind="reply")} == {"alice", "bob", "carol"}


def test_reply_detection_in_the_chat_list():
    from loft.pigeon.agent import room_replied
    lead_ = dict(username="alice", message="Hey alice, saw your post about onboarding. How do you handle it today?")
    cases = [
        ("alice\nthanks, I'll take a look\n3m", False, True),                  # they spoke last, read
        ("alice  thanks!", True, True),                                          # unread
        ("alice\n", True, True),                                                # nothing but a badge
        ("alice\n2m", False, False),                                            # no preview, no badge: can't tell, so no
        ("alice\nYou: Hey alice, saw your post\n1h", False, False),            # ours is last
        ("alice\nHey alice, saw your post about onboarding\n1h", False, False),  # our text shown without the "You:" prefix
        ("alice_dev\nthanks a lot\n1m", True, False),                          # a different user whose name contains hers
        ("bob\nthanks a lot\n1m", True, False),                                # someone else entirely
        ("Alice\nYes, interested", False, True),                                # case-insensitive
        ("alice yesterday", False, False),                                       # a timestamp isn't a message
    ]
    for text, unread, want in cases:
        assert room_replied(text, unread, lead_) is want, (text, unread, want)


def test_inbox_json_replies_count_even_when_the_chat_list_shows_nothing(team, tmp_path):
    owner, _, tid, a, b, _ = team
    now = time.time()
    inbox = {"data": {"children": [dict(data=dict(author="Alice", created_utc=now)),            # replied after we wrote
                                   dict(data=dict(author="bob", created_utc=now - 86400 * 9)),  # an old message from before our DM
                                   dict(data=dict(author="stranger", created_utc=now))]}}
    br = FakeBridge(cmds={"scanInbox": {"ok": True, "rooms": []}})
    br.reddit_json = lambda path: inbox
    ag = agent_for(tmp_path, owner, tid, a, "a", br)
    for name in ("alice", "bob"):
        ag.st.put_lead(dict(lead(name), status="sent", sent_at=now - 3600))
    assert ag.check_replies() == 1
    assert ag.st.get_lead("alice")["status"] == "replied" and ag.st.get_lead("bob")["status"] == "sent"
    assert any("replied (inbox)" in e["msg"] for e in ag.st.logs)


def test_identify_detects_and_registers_the_username(team, tmp_path):
    owner, _, tid, a, b, _ = team
    ag = agent_for(tmp_path, owner, tid, a, "a", FakeBridge(me="Detected_User"), my_username="")
    ag.identify()
    assert ag.s["my_username"] == "Detected_User" and [r["username"] for r in owner.pigeon_accounts(tid)] == ["detected_user"]

    bad = agent_for(tmp_path, owner, tid, b, "b", FakeBridge(me=None), my_username="")
    bad.identify()  # not logged in: says so and carries on
    assert bad.s["my_username"] == "" and any("which Reddit user" in e["msg"] for e in bad.st.logs)

    clash = agent_for(tmp_path, owner, tid, b, "c", FakeBridge(), my_username="detected_user")
    clash.identify()
    assert any("Couldn't register" in e["msg"] for e in clash.st.logs)


def test_run_adopts_the_team_setup_before_checking_for_a_product(team, tmp_path):
    owner, _, tid, a, b, _ = team
    ops.run_state_op(ctx_for(tmp_path / "src", brain=CentralBrain(owner, tid, a)), "team_publish")
    ag = agent_for(tmp_path, owner, tid, b, "new")
    ag.st.product, ag.st.subreddits = None, []  # a brand-new account with no setup of its own
    ag.sync_team()
    assert ag.st.product["name"] == "Acme" and any(s["enabled"] for s in ag.st.subreddits)


def test_team_dm_limit_stops_dms_and_subreddit_limit_parks_only_that_sub(team, tmp_path):
    owner, _, tid, a, b, _ = team
    set_limits(owner, tid, team_dm_per_day=1)
    CentralBrain(owner, tid, b).claim("dm", "someone_else")  # the team's one DM for today is already used
    ag = agent_for(tmp_path, owner, tid, a, "a", llm=False)
    ag.st.put_lead(lead("alice"))
    with pytest.raises(TeamHold) as h:
        ag.send_dm(ag.st.get_lead("alice"))
    assert h.value.scope == "team" and "limit reached" in str(h.value)
    assert ag.st.get_lead("alice")["status"] == "ready"  # not failed, not skipped: it simply waits for tomorrow
    assert ag.dm_tick(1) is True  # a team-wide cap is a hard stop for this session
    assert FakeBridge().sent == [] and ag.b.sent == []  # nothing was opened or typed

    set_limits(owner, tid, subreddit_dm_per_day=1)
    CentralBrain(owner, tid, b).claim("dm", "someone", subreddit="saas")
    ag2 = agent_for(tmp_path, owner, tid, a, "a2", llm=False)
    ag2.st.put_lead(lead("carol"))
    ag2.st.put_lead(lead("dave"))
    assert ag2.dm_tick(3) is False and ag2.deferred == {"carol", "dave"}  # both parked this session, nothing sent
    assert ag2.st.get_lead("carol")["status"] == "ready" and ag2.b.sent == []


# ------------------------------------------------------------------ the CLI
def test_cli_team_commands(team, capsys, tmp_path):
    from loft.pigeon.cli import handle
    owner, member, tid, a, b, mine = team
    run = lambda who, *argv: (handle(_parse(list(argv)), who, lambda: tid), capsys.readouterr().out)[1]

    assert "none" in run(owner, "team", "show") and "no accounts registered" in run(owner, "team", "accounts")
    assert "limits: none" in run(owner, "team", "limits")
    out = run(owner, "team", "limits", "--dm-per-day", "40", "--sub-dm-per-day", "8")
    assert "team_dm_per_day=40" in out and "subreddit_dm_per_day=8" in out and "v1" in out
    out = run(owner, "team", "limits", "--sub-dm-per-day", "0")  # 0 removes one
    assert "subreddit_dm_per_day" not in out and "team_dm_per_day=40" in out
    with pytest.raises(SystemExit, match="owners and admins only"):
        run(member, "team", "limits", "--dm-per-day", "1")
    with pytest.raises(SystemExit, match="which account"):
        run(owner, "team", "pull")

    owner.pigeon_set_account(tid, a, "acct_a")
    assert "u/acct_a" in run(owner, "team", "accounts") and "limits: team_dm_per_day=40" in run(owner, "team", "show")

    ba = CentralBrain(owner, tid, a)
    ba.finish(ba.claim("dm", "alice", subreddit="saas"), "done")
    ba.finish(ba.claim("reply", "alice", subreddit="saas", body="hi"), "done")
    out = run(owner, "brain")
    assert "1 DMs · 1 replies" in out and "team limits (rolling 24h): team_dm_per_day=40" in out and "used: 1 DMs" in out and "RPLY" in out


# ------------------------------------------------------------------ real browser (mock Reddit)
@needs_browser
def test_new_account_inherits_team_setup_registers_itself_and_queues_leads(page, tmp_path, fast, team):
    owner, _, tid, a, b, _ = team
    ops.run_state_op(ctx_for(tmp_path / "src", brain=CentralBrain(owner, tid, a)), "team_publish")  # an admin shares a setup

    from loft.pigeon.bridge import Bridge
    ag = agent_for(tmp_path, owner, tid, b, "new", Bridge(page, pace=2), my_username="", min_fit=70)
    ag.st.product, ag.st.subreddits = None, []  # brand new: nothing set up on this account
    ag.st.subreddits = []
    ag.run("find", minutes=0.4)

    assert ag.st.product["name"] == "Acme" and ag.st.team_version == 1  # inherited, no per-account setup needed
    assert ag.st.settings["my_username"] == "mock_me"  # read from the logged-in session (/api/me.json)
    assert {r["username"]: r["profile"] for r in owner.pigeon_accounts(tid)} == {"mock_me": "acct-b"}
    assert {"founder_amy", "dev_bob", "sam_k"} <= set(ag.st.leads)
    assert {e["target"] for e in owner.pigeon_events(tid, kind="lead")} >= {"founder_amy", "dev_bob", "sam_k"}  # and told the team
    assert owner.pigeon_events(tid, kind="upvote")  # upvotes went through the team claim too


@needs_browser
def test_subreddit_cap_parks_only_that_subreddits_leads(page, tmp_path, fast, team):
    owner, _, tid, a, b, _ = team
    set_limits(owner, tid, subreddit_dm_per_day=1)
    CentralBrain(owner, tid, b).claim("dm", "earlier_person", subreddit="saas")  # r/saas is at its cap for the team

    from loft.pigeon.bridge import Bridge
    ag = agent_for(tmp_path, owner, tid, a, "a", Bridge(page, pace=2), llm=False)
    ag.st.put_lead(dict(lead("carol"), fit=95))                       # r/saas: capped, parked
    ag.st.put_lead(dict(lead("alex_dev"), subreddit="startups", fit=80))  # another subreddit: goes out
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=0.6)
    assert ag.st.get_lead("alex_dev")["status"] == "sent" and ag.st.get_lead("carol")["status"] == "ready"
    assert page.seen["users"] == ["alex_dev"]
    assert any("held" in e["msg"] and "r/saas" in e["msg"] for e in ag.st.logs)


# ------------------------------------------------------------------ replies and post submission, end to end in a browser
@needs_browser
def test_reply_detection_against_a_real_chat_page_and_inbox(page, tmp_path, team):
    from loft.pigeon.bridge import Bridge
    owner, _, tid, a, b, _ = team
    ag = agent_for(tmp_path, owner, tid, a, "a", Bridge(page, pace=2))
    for name in ("alice", "bob", "carol", "dave", "erin", "frank"):
        ag.st.put_lead(dict(lead(name), status="sent", sent_at=time.time() - 3600, message=f"Hey {name}, quick question about your post?"))
    page.goto("https://www.reddit.com/")
    assert ag.check_replies() == 3
    status = {n: ag.st.get_lead(n)["status"] for n in ("alice", "bob", "carol", "dave", "erin", "frank")}
    assert status == dict(alice="replied", bob="sent", carol="replied", dave="replied", erin="sent", frank="sent"), status
    # alice + carol from the chat list (one unread, one long since read), dave from the private-message inbox; bob's row shows OUR
    # message last, erin's row has no preview, frank isn't there at all
    assert {e["target"] for e in owner.pigeon_events(tid, kind="reply")} == {"alice", "carol", "dave"}


@needs_browser
def test_agent_submits_a_post_and_the_team_records_it(page, tmp_path, team):
    from loft.pigeon.bridge import Bridge
    owner, _, tid, a, b, _ = team
    ag = agent_for(tmp_path, owner, tid, a, "a", Bridge(page, pace=2))
    ag.st.post_ideas = [dict(id="i1", subreddit="saas", title="What I learned finding my first ten customers", body="We tried cold DMs first.\nThen we went where buyers already complain.", angle="a"),
                        dict(id="i2", subreddit="saas", title="A second idea", body="body", angle="a")]
    page.goto("https://www.reddit.com/")
    url = ag.submit_idea("i1")
    assert "/comments/new1/" in url and page.url == url  # judged by the redirect to the new thread
    assert page.seen["posts"] == [dict(title="What I learned finding my first ten customers",
                                       body="We tried cold DMs first.\nThen we went where buyers already complain.")]  # typed exactly
    assert [p["title"] for p in ag.st.posted] == ["What I learned finding my first ten customers"] and [i["id"] for i in ag.st.post_ideas] == ["i2"]
    assert ag.st.get_daily()["posts"] == 1 and ag.st.stats["posts"] == 1
    ev = owner.pigeon_events(tid, kind="post")
    assert len(ev) == 1 and ev[0]["status"] == "done" and ev[0]["profile"] == "acct-a"
    with pytest.raises(RuntimeError, match="daily post cap reached"):
        ag.submit_idea("i2")  # posts_per_day defaults to 1
    assert "What I learned" in ops.run_state_op(ops.Ctx(ag.st), "posts_list")["out"]


@needs_browser
def test_a_rejected_post_is_reported_and_releases_the_claim(page, tmp_path, team):
    from loft.pigeon.bridge import Bridge
    owner, _, tid, a, b, _ = team
    ag = agent_for(tmp_path, owner, tid, a, "a", Bridge(page, pace=2))
    ag.st.post_ideas = [dict(id="f1", subreddit="flairsub", title="Needs a flair", body="body", angle="a")]
    page.goto("https://www.reddit.com/")
    with pytest.raises(RuntimeError, match="Post button is disabled.*requires flair"):
        ag.submit_idea("f1")  # the page says why; nothing was posted
    assert ag.st.posted == [] and [i["id"] for i in ag.st.post_ideas] == ["f1"] and ag.st.get_daily().get("posts", 0) == 0
    assert CentralBrain(owner, tid, b).claim("post", "Needs a flair").ok  # released: another account may try


def test_submit_is_guarded(team, tmp_path):
    owner, _, tid, a, b, _ = team
    idea = lambda sub="saas", t="A title": dict(id="x", subreddit=sub, title=t, body="b", angle="a")
    ag = agent_for(tmp_path, owner, tid, a, "a", llm=False)
    ag.st.subreddits.append(dict(name="spammy", enabled=True, score=1, rules_risk="high", reason="", subscribers=1, posts_per_day=1, icp_density=1, last_visited=0))
    ag.st.post_ideas = [idea("spammy")]
    with pytest.raises(RuntimeError, match="rules risk: high"):
        ag.submit_idea("x")
    assert ag.b.sent == []  # refused before touching the browser

    ag.st.post_ideas = [idea()]
    CentralBrain(owner, tid, b).finish(CentralBrain(owner, tid, b).claim("post", "A title", subreddit="saas"), "done")
    with pytest.raises(RuntimeError, match="team already posted this"):
        ag.submit_idea("x")

    ag.st.post_ideas = [idea(t="Another")]
    set_limits(owner, tid, team_post_per_day=1)  # the team's one post for today is already used
    with pytest.raises(RuntimeError, match="team limit reached for the team: 1/1 posts"):
        ag.submit_idea("x")

    class Down:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise ConnectionError("down")
            return boom
    offline = Agent(FakeBridge(), ag.st, None, CentralBrain(Down(), tid, a))
    with pytest.raises(RuntimeError, match="Not posting"):
        offline.submit_idea("x")
    assert ag.b.sent == [] and offline.b.sent == []


def test_posts_are_a_team_limit_flag_on_the_cli(team, capsys):
    from loft.pigeon.cli import handle
    owner, _, tid, *_ = team
    handle(_parse(["team", "limits", "--post-per-day", "2", "--sub-post-per-day", "1"]), owner, lambda: tid)
    out = capsys.readouterr().out
    assert "team_post_per_day=2" in out and "subreddit_post_per_day=1" in out
    assert owner.pigeon_summary(tid)["limits"] == {"team_post_per_day": 2, "subreddit_post_per_day": 1}


# Everything here that doesn't drive a browser also runs on real Postgres + S3 (moto), like test_server.py does: the brain's SQL
# has to be portable, not just work on SQLite.
import inspect as _inspect  # noqa: E402

for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn) and not ({"page", "mock_context", "browser"} & set(_inspect.signature(_fn).parameters)):
        globals()[_name] = pytest.mark.usefixtures("backend")(_fn)
