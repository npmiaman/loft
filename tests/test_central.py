"""The central brain on the Loft server: claims are atomic across accounts, releases work, history and summary add up."""
import json
import threading

import pytest

from loft.client import LoftError
from loft.pigeon.central import CentralBrain, Unreachable
from tests.conftest import FP

# Every test here runs twice: SQLite + files, and real Postgres + S3 (moto), as in test_server.py.
pytestmark = pytest.mark.usefixtures("backend")


@pytest.fixture
def team(mk):
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    member = mk()
    member.register("m@x.io", "password1", invite=owner.invite(tid, "member")["code"])
    a = owner.create_profile(tid, name="acct-a", fingerprint=FP)["id"]
    b = owner.create_profile(tid, name="acct-b", fingerprint=FP)["id"]
    return owner, member, tid, a, b


def test_second_account_cannot_dm_the_same_person(team):
    owner, _, tid, a, b = team
    first = CentralBrain(owner, tid, a).claim("dm", "u/Alice", subreddit="r/SaaS", body="hi")
    assert first.ok
    second = CentralBrain(owner, tid, b).claim("dm", "alice", subreddit="saas")  # same person, written differently
    assert not second.ok and second.existing["profile"] == "acct-a" and "acct-a" in second.who()


def test_claims_are_atomic_under_a_race(team):
    owner, _, tid, a, b = team
    results = []

    def go(pid):
        results.append(CentralBrain(owner, tid, pid).claim("dm", "racer").ok)

    ts = [threading.Thread(target=go, args=(p,)) for p in (a, b, a, b, a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1


def test_failed_releases_the_claim_but_blocked_and_done_do_not(team):
    owner, _, tid, a, b = team
    brain_a, brain_b = CentralBrain(owner, tid, a), CentralBrain(owner, tid, b)
    c = brain_a.claim("dm", "rate_limited_victim")
    brain_a.finish(c, "failed", "rate-limit")
    assert brain_b.claim("dm", "rate_limited_victim").ok  # released: anyone may retry

    c = brain_a.claim("dm", "closed_inbox")
    brain_a.finish(c, "blocked", "cannot be messaged")
    assert not brain_b.claim("dm", "closed_inbox").ok  # no point another account trying

    c = brain_a.claim("dm", "sent_already")
    brain_a.finish(c, "done")
    assert not brain_b.claim("dm", "sent_already").ok


def test_comment_threads_are_deduped_however_they_are_linked(team):
    owner, _, tid, a, b = team
    assert CentralBrain(owner, tid, a).claim("comment", "/r/saas/comments/p1/t1/", subreddit="saas", title="T", body="x").ok
    other = CentralBrain(owner, tid, b).claim("comment", "https://www.reddit.com/r/SaaS/comments/p1/t1/?utm=1")
    assert not other.ok


def test_dm_and_comment_are_independent_kinds(team):
    owner, _, tid, a, _ = team
    br = CentralBrain(owner, tid, a)
    assert br.claim("dm", "someone").ok and br.claim("comment", "someone").ok


def test_seen_and_context_and_summary(team):
    owner, member, tid, a, b = team
    ba, bb = CentralBrain(owner, tid, a), CentralBrain(owner, tid, b)
    ba.finish(ba.claim("dm", "alice", subreddit="saas"), "done")
    bb.finish(bb.claim("comment", "/r/saas/comments/p1/t1/", subreddit="saas", title="Finding customers", body="Start where buyers complain."), "done")
    ba.finish(ba.claim("post", "How we got our first 10 customers", subreddit="startups", title="How we got our first 10 customers"), "done")
    bb.finish(bb.claim("dm", "failed_one", subreddit="saas"), "failed", "x")

    seen = ba.seen("dm", ["ALICE", "bob", "failed_one"])
    assert set(seen) == {"ALICE"} and seen["ALICE"]["profile"] == "acct-a"

    ctx = ba.context("SaaS")
    assert "1 DMs sent, 1 comments, 0 posts" in ctx and "Start where buyers complain" in ctx  # failed DM not counted
    assert "do not repeat" in ctx and ba.context("emptysub") == ""

    s = owner.pigeon_summary(tid)
    assert s["by_kind"] == {"dm": 1, "comment": 1, "post": 1}
    assert {r["subreddit"]: (r["dms"], r["comments"], r["posts"]) for r in s["by_subreddit"]} == {"saas": (1, 1, 0), "startups": (0, 0, 1)}
    assert {r["profile"]: r["dms"] for r in s["by_profile"]} == {"acct-a": 1, "acct-b": 0}
    assert member.pigeon_summary(tid)["by_kind"]["dm"] == 1  # teammates share the same memory
    assert len(ba.events(kind="dm", subreddit="saas")) == 2  # events include the failed attempt, status says so


def test_permissions(team, mk):
    owner, member, tid, a, _ = team
    outsider = mk()
    outsider.register("x@x.io", "password1", invite=owner.invite(owner.create_team("Other")["id"])["code"])
    with pytest.raises(LoftError, match="404"):
        outsider.pigeon_summary(tid)
    with pytest.raises(LoftError):
        outsider.pigeon_claim(tid, kind="dm", target="x")
    # a member may not report under a profile they cannot access
    with pytest.raises(LoftError, match="profile not found"):
        member.pigeon_claim(tid, kind="dm", target="x", profile_id=a)
    # only the claimer or an admin settles a claim
    cid = member.pigeon_claim(tid, kind="dm", target="shared")["id"]
    other = mk()
    other.register("y@x.io", "password1", invite=owner.invite(tid, "member")["code"])
    with pytest.raises(LoftError, match="needs role"):
        other.pigeon_finish(tid, cid, "failed")
    owner.pigeon_finish(tid, cid, "failed", "released by admin")
    assert other.pigeon_claim(tid, kind="dm", target="shared")["id"]


def test_unreachable_server_fails_closed_for_writes_and_open_for_reads(team):
    owner, _, tid, a, _ = team

    class Down:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise ConnectionError("server down")
            return boom

    br = CentralBrain(Down(), tid, a)
    with pytest.raises(Unreachable):
        br.claim("dm", "alice")
    assert br.seen("dm", ["alice"]) == {} and br.context("saas") == ""
