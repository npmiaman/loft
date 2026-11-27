"""Pigeon inside Pigeon: logic tests, plus the real extension content script driven in real Camoufox
against Pigeon's mock of Reddit's DOM (tests/fixtures). No network, no real Reddit, fake DeepSeek.

What this proves is the mechanics. It cannot prove Reddit's current markup matches the mock."""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from pigeon.agent import agent as agent_mod
from pigeon.agent import brain, governor
from pigeon.agent.agent import Agent
from pigeon.agent.bridge import Bridge
from pigeon.agent.llm import Llm, LlmError
from pigeon.agent.state import State

FIX = Path(__file__).parent / "fixtures"

try:
    from camoufox.pkgman import installed_verstr
    installed_verstr()
    HAVE = True
except Exception:
    HAVE = False
# Escape hatch for machines without the 1.3 GB Camoufox download: PIGEON_TEST_CHROMIUM=/path/to/chrome runs the same
# mechanics tests in Playwright Chromium. It does NOT cover Camoufox-specific behaviour (isolated world, fingerprints).
CHROMIUM = os.environ.get("PIGEON_TEST_CHROMIUM")
needs_browser = pytest.mark.skipif(not (HAVE or CHROMIUM), reason="Camoufox browser not installed")


# ------------------------------------------------------------------ helpers
def fake_deepseek(calls: list | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        sysm, usr = body["messages"][0]["content"], body["messages"][1]["content"]
        if calls is not None:
            calls.append(sysm[:40])
        if "qualify Reddit authors" in sysm:
            ids = re.findall(r"\[id=(.*?)\] (?:post|comment) by", usr)
            out = {"results": [dict(id=i, fit=90, intent="asking", reason="asks for help", dm_ok=True, comment_fit=True) for i in ids]}
        elif "first Reddit DM" in sysm:
            out = {"message": "Hey, saw your post about finding customers. I build a tool for that, how do you do it today?"}
        elif "Reddit comment" in sysm:
            out = {"comment": "I would start where your buyers already complain about alternatives.", "why": "answers the question"}
        elif "growth strategist" in sysm:
            out = dict(name="Acme", summary="Finds customers", offer="a tool", icp="founders", personas=["a"], painPhrases=["p"],
                       buyingSignals=["b"], competitors=["c"], subredditIdeas=["saas"], searchQueries=["q"])
        elif "judge whether a subreddit" in sysm:
            names = re.findall(r"### r/(\S+) ", usr)
            out = {"subs": [dict(name=n, icpDensity=80, rulesRisk="low", reason="good fit") for n in names]}
        elif "spintax writer" in sysm:
            out = {"template": "{Hey|Hi|Hello|Yo} {name}, {saw|noticed|read} your {post|thread|question} in r/{subreddit} about {topic}. "
                               "{I build|I'm building|We make} a tool for that, {curious|wondering} {how you do it today|what you use now}? {Cheers|Thanks|Best}"}
        elif "propose Reddit posts" in sysm:
            out = {"ideas": [dict(subreddit="saas", title=f"Idea {n}", body="body " * 20, angle="a") for n in range(5)]}
        else:
            raise AssertionError("unexpected prompt: " + sysm[:60])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})
    return httpx.MockTransport(handler)


def make_state(tmp_path, **settings) -> State:
    st = State(tmp_path / "pigeon.json")
    st.settings.update(dict(warmup=False, active_start=0, active_end=24, min_gap_sec=5, max_gap_sec=5,
                            approve_ai_drafts=False, my_username="me"), **settings)
    st.product = dict(url="https://x.test", name="Acme", summary="s", offer="o", icp="founders", personas=["a"],
                      painPhrases=["p"], buyingSignals=["b"], competitors=["c"], subredditIdeas=[], searchQueries=[])
    st.subreddits = [dict(name="startups", subscribers=1000, posts_per_day=5, icp_density=60, rules_risk="low",
                          score=60, reason="", enabled=True, last_visited=0)]
    return st


def lead(name, status="ready", message="Hello there, quick question about your post?"):
    return dict(username=name, subreddit="saas", post_url="/r/saas/comments/p1/t1/", post_title="How do I find customers?",
                snippet="I have built a product and nobody knows it exists.", fit=90, intent="asking", reason="asks",
                status=status, message=message, source="browse", created_at=time.time())


# ------------------------------------------------------------------ logic (no browser)
def test_warmup_ramp_and_cap():
    s = dict(warmup=True, warmup_start=1000.0, daily_dm_cap=40)
    assert governor.effective_cap(s, 1000.0) == 5
    assert governor.effective_cap(s, 1000.0 + 3 * 86400) == 18
    assert governor.effective_cap(s, 1000.0 + 99 * 86400) == 40  # ramp tops out at 60, capped by the user's 40
    assert governor.effective_cap({**s, "warmup": False}) == 40


def test_active_hours_including_overnight():
    s = dict(active_start=9, active_end=21)
    assert governor.in_active_hours(s, datetime(2026, 1, 1, 9)) and not governor.in_active_hours(s, datetime(2026, 1, 1, 21))
    night = dict(active_start=22, active_end=6)
    assert governor.in_active_hours(night, datetime(2026, 1, 1, 23)) and governor.in_active_hours(night, datetime(2026, 1, 1, 3))
    assert not governor.in_active_hours(night, datetime(2026, 1, 1, 12))


def test_governor_gates(tmp_path):
    st = make_state(tmp_path, daily_dm_cap=2)
    assert governor.check_dm(st)["ok"]
    governor.record_dm(st)
    g = governor.check_dm(st)
    assert not g["ok"] and not g["hard"] and g["wait"] > 0  # spacing, not a stop
    st.daily["next_dm_at"] = 0
    governor.record_dm(st)
    st.daily["next_dm_at"] = 0
    g = governor.check_dm(st)
    assert not g["ok"] and g["hard"] and "Daily cap" in g["reason"]
    st.daily.update(count=0)
    governor.pause_for(st, 3600)
    assert "Paused" in governor.check_dm(st)["reason"]


def test_gap_is_jittered_and_bounded():
    gaps = [governor.next_gap(dict(min_gap_sec=60, max_gap_sec=180)) for _ in range(300)]
    assert min(gaps) >= 42 and len(set(round(g) for g in gaps)) > 50


def test_state_roundtrip_and_daily_rollover(tmp_path):
    st = make_state(tmp_path)
    st.put_lead(lead("Alice"))
    st.daily.update(date="2000-01-01", count=7, paused_until=123.0)
    st.save()
    st2 = State(tmp_path / "pigeon.json")
    assert st2.get_lead("alice")["username"] == "Alice"
    d = st2.get_daily()
    assert d["count"] == 0 and d["paused_until"] == 123.0  # counter resets, a pause carries over


def test_template_rendering():
    out = brain.render_template("{Hey|Hi} {name}, r/{subreddit}: {topic}",
                                dict(username="bob", subreddit="saas", post_title="How do I find customers?"))
    assert out in ("Hey bob, r/saas: how do I find customers", "Hi bob, r/saas: how do I find customers")


def test_scoring_batches_by_twelve():
    calls = []
    llm = Llm("k", transport=fake_deepseek(calls))
    items = [dict(id=f"i{n}", kind="post", author=f"u{n}", subreddit="s", title="t", text="x" * 40) for n in range(25)]
    product = dict(name="A", summary="", offer="", icp="", personas=[], painPhrases=[], buyingSignals=[], competitors=[])
    res = brain.score_items(llm, product, items)
    assert len(res) == 25 and len(calls) == 3


def test_llm_errors():
    bad = Llm("k", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(LlmError, match="401"):
        bad.chat("s", "u")
    with pytest.raises(LlmError, match="No DeepSeek key"):
        Llm("").chat("s", "u")
    fenced = Llm("k", transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": 'sure! {"a": 1} done'}}]})))
    assert fenced.json("s", "u") == {"a": 1}


def test_scrape_site_text():
    html = ("<html><head><title>Acme</title><meta name='description' content='Best tool'></head><body><nav>menu</nav>"
            "<h1>Find customers fast</h1><p>Acme helps founders find their first customers on Reddit every day.</p>"
            "<script>var x=1</script></body></html>")
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    t = brain.scrape_site("https://acme.test", http)
    assert "TITLE: Acme" in t and "DESCRIPTION: Best tool" in t and "Find customers fast" in t and "menu" not in t and "var x" not in t


# ------------------------------------------------------------------ real Camoufox + mock Reddit
def mock_reddit(r, seen=None):
    """Pigeon's mock of Reddit's DOM, plus the JSON endpoints subreddit discovery reads."""
    path = httpx.URL(r.request.url).path
    if path == "/api/me.json":
        return r.fulfill(content_type="application/json", body=json.dumps({"data": {"name": "mock_me"}}))
    if path.endswith("/about.json"):
        return r.fulfill(content_type="application/json", body=json.dumps({"data": dict(
            display_name=path.split("/")[2], subscribers=5000, over18=False, subreddit_type="public", public_description="d")}))
    if path.endswith("/new.json"):
        kids = [{"data": dict(created_utc=1_700_000_000 + 3600 * n, title=f"Post {n}")} for n in range(10)]
        return r.fulfill(content_type="application/json", body=json.dumps({"data": {"children": kids}}))
    if path.endswith("/rules.json"):
        return r.fulfill(content_type="application/json", body=json.dumps({"rules": []}))
    if path == "/__submit":  # the mock submit page reports what was typed before it redirects
        if seen is not None:
            seen.setdefault("posts", []).append(json.loads(r.request.post_data or "{}"))
        return r.fulfill(content_type="application/json", body="{}")
    if path == "/message/inbox.json":
        return r.fulfill(content_type="application/json", body=json.dumps({"data": {"children": [
            {"data": {"author": "dave", "created_utc": time.time()}}, {"data": {"author": "stranger", "created_utc": time.time()}}]}}))
    if path == "/chat":
        return r.fulfill(content_type="text/html", body=(FIX / "chat.html").read_text())
    if path.endswith("/submit"):
        f = "submit_flair.html" if "/r/flairsub/" in path else "submit.html"
        return r.fulfill(content_type="text/html", body=(FIX / f).read_text())
    if path.startswith("/user/"):
        name = path.split("/")[2]
        if seen is not None:
            seen["users"].append(name)
        f = "profile_ratelimited.html" if name == "ratelimited" else "profile.html"
        html = (FIX / f).read_text().replace("__USER__", name)
    elif "/comments/" in path:
        html = (FIX / "post.html").read_text()
    else:
        html = (FIX / "listing.html").read_text()
    broken = (seen or {}).get("break", set())  # simulate Reddit changing its markup
    if "feed" in broken:
        html = html.replace('post-title="', 'headline="')
    if "profile" in broken and path.startswith("/user/"):
        html = html.replace('<button id="chat">Chat</button>', "")
    r.fulfill(content_type="text/html", body=html)


def page_global(page, expr: str):
    """Read a global the *page's own scripts* defined. Camoufox isolates evaluations from the page's world (so sites can't
    see our code), so those reads need its `mw:` main-world prefix; plain Chromium has no isolation."""
    return page.evaluate(expr if CHROMIUM else "mw:" + expr)


@pytest.fixture(scope="module")
def browser():
    if CHROMIUM:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=CHROMIUM, headless=True)
            yield b
            b.close()
        return
    from camoufox.sync_api import Camoufox
    with Camoufox(headless=True, main_world_eval=True) as b:  # main-world reads are for the tests' own assertions only
        yield b


@pytest.fixture
def page(browser):
    p = browser.new_page()
    p.seen = {"users": []}
    p.route("https://www.reddit.com/**", lambda r: mock_reddit(r, p.seen))
    yield p
    p.close()


@needs_browser
def test_content_script_commands(page):
    b = Bridge(page, pace=2)
    page.goto("https://www.reddit.com/r/saas/")
    b.ready()

    r = b.cmd({"cmd": "browse", "maxPosts": 10, "seconds": 8})
    assert sorted(p["author"] for p in r["posts"]) == ["dev_bob", "founder_amy", "sam_k"]  # AutoModerator dropped
    assert page.evaluate("window.scrollY") > 100
    assert page.evaluate("!!document.querySelector('pigeon-cursor')")  # the on-page cursor is real

    assert b.cmd({"cmd": "clickLink", "hrefIncludes": "/r/saas/comments/p1"})["ok"]
    page.wait_for_url("**/comments/p1/**")
    b.ready()  # full page load wiped the injection; ensure() reinstalls it
    rp = b.cmd({"cmd": "readPost"})
    assert rp["post"]["author"] == "founder_amy" and sorted(c["author"] for c in rp["comments"]) == ["reader_one", "reader_two"]

    page.evaluate("window.scrollTo(0, 0)")
    up = b.cmd({"cmd": "upvote", "scope": "post", "permalink": "/r/saas/comments/p1/t1/"})
    assert up["ok"] and not up.get("already")
    assert b.cmd({"cmd": "upvote", "scope": "post", "permalink": "/r/saas/comments/p1/t1/"})["already"]  # never double-votes
    assert page_global(page, "window.__log.upvotes") == 1

    text = "Happy to help: I would start where buyers already complain."
    assert b.cmd({"cmd": "postComment", "text": text})["ok"]
    assert page_global(page, "window.__log.comments") == [text]


@needs_browser
def test_dm_and_rate_limit_detection(page):
    b = Bridge(page, pace=2)
    msg = "Hey alex_dev, saw your post about first customers. I build a tool for that, curious how you do it today?"
    page.goto("https://www.reddit.com/user/alex_dev/")
    b.ready()
    assert b.cmd({"cmd": "dm", "text": msg})["ok"]
    assert page_global(page, "window.__log.dms") == [msg]

    page.goto("https://www.reddit.com/user/ratelimited/")
    b.ready()
    res = b.cmd({"cmd": "dm", "text": msg})
    assert not res["ok"] and res["code"] == "rate-limit"


@needs_browser
def test_stop_button_aborts(page):
    b = Bridge(page, pace=2)
    page.goto("https://www.reddit.com/r/saas/")
    b.ready()
    page.evaluate("window.__pigeon.stopped = true")  # what the on-page Stop button does
    with pytest.raises(Exception) as e:
        b.wait(5)
    assert e.type.__name__ == "Stop"


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(agent_mod, "lognormal", lambda m, s=0.45: m / 6)
    monkeypatch.setattr(agent_mod, "clamp", lambda x, lo, hi: min(x, 6))


@needs_browser
def test_find_session_queues_leads_upvotes_and_drafts(page, tmp_path, fast):
    st = make_state(tmp_path)
    ag = Agent(Bridge(page, pace=2), st, Llm("k", transport=fake_deepseek()))
    page.goto("https://www.reddit.com/")
    ag.run("find", minutes=0.4)
    names = {l["username"] for l in st.leads.values()}
    assert {"founder_amy", "dev_bob", "sam_k"} <= names, names
    assert st.stats["leads"] >= 3 and st.stats["scanned"] >= 3
    assert st.stats["upvotes"] >= 1
    assert st.opportunities and st.opportunities[0]["status"] == "pending"
    assert st.subreddits[0]["last_visited"] > 0
    assert all(l["status"] == "queued" for l in st.leads.values())  # find mode never DMs


@needs_browser
def test_dm_session_writes_and_sends(page, tmp_path, fast):
    st = make_state(tmp_path)
    st.put_lead(dict(lead("alex_dev", status="queued"), message=None))
    ag = Agent(Bridge(page, pace=2), st, Llm("k", transport=fake_deepseek()))
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=1)
    l = st.get_lead("alex_dev")
    assert l["status"] == "sent" and l["sent_at"]
    assert page_global(page, "window.__log.dms") == [l["message"]]
    assert st.daily["count"] == 1 and st.daily["next_dm_at"] > time.time()  # spacing for the next one is armed


@needs_browser
def test_ai_drafts_wait_for_approval(page, tmp_path, fast):
    st = make_state(tmp_path, approve_ai_drafts=True)
    st.put_lead(dict(lead("alex_dev", status="queued"), message=None))
    ag = Agent(Bridge(page, pace=2), st, Llm("k", transport=fake_deepseek()))
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=0.3)
    l = st.get_lead("alex_dev")
    assert l["status"] == "draft" and l["message"] and "alex_dev" not in page.seen["users"]


@needs_browser
def test_rate_limit_pauses_and_stops(page, tmp_path, fast):
    st = make_state(tmp_path)
    st.put_lead(dict(lead("ratelimited"), fit=95))  # leads go out highest fit first
    st.put_lead(lead("alex_dev"))
    ag = Agent(Bridge(page, pace=2), st, None)
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=1)
    assert st.get_lead("ratelimited")["status"] == "failed"
    assert st.daily["paused_until"] > time.time() + 5 * 3600  # 6h pause
    assert st.get_lead("alex_dev")["status"] == "ready" and "alex_dev" not in page.seen["users"]


@needs_browser
def test_daily_cap_blocks_sending(page, tmp_path, fast):
    st = make_state(tmp_path, daily_dm_cap=1)
    st.get_daily()["count"] = 1
    st.put_lead(lead("alex_dev"))
    ag = Agent(Bridge(page, pace=2), st, None)
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=0.3)
    assert st.get_lead("alex_dev")["status"] == "ready" and not page.seen["users"]


# ------------------------------------------------------------------ state travels with the profile (no browser)
def test_state_syncs_between_devices_through_pigeon(mk, tmp_path):
    from pigeon.agent.runner import with_state
    from pigeon.agent.state import STATE_NAME

    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    pid = owner.create_profile(tid, name="acct", fingerprint={"os": "windows"})["id"]

    def seed(st):
        st.put_lead(lead("alice", status="draft"))
        st.settings["daily_dm_cap"] = 12

    laptop_a, laptop_b = tmp_path / "a", tmp_path / "b"
    with_state(owner, pid, seed, home=laptop_a)
    # a second device pulls the synced profile and sees the same leads, settings and (shared) caps
    seen = with_state(owner, pid, lambda st: (st.get_lead("alice")["status"], st.settings["daily_dm_cap"]), home=laptop_b)
    assert seen == ("draft", 12)
    assert (laptop_b / pid.__str__() / "data" / STATE_NAME).exists()
    # editing there syncs back
    with_state(owner, pid, lambda st: st.patch_lead("alice", status="ready"), home=laptop_b)
    assert with_state(owner, pid, lambda st: st.get_lead("alice")["status"], home=laptop_a) == "ready"
    assert owner.profiles(tid)[0]["running"] is None  # lock released


# ------------------------------------------------------------------ attaching Pigeon to a live Pigeon session
import threading  # noqa: E402

from pigeon import control  # noqa: E402
from pigeon.client import PigeonError  # noqa: E402
from pigeon.agent.attach import PigeonHost  # noqa: E402
from pigeon.agent.bridge import Stop  # noqa: E402
from pigeon.agent.runner import state_path  # noqa: E402
from pigeon.session import interactive  # noqa: E402


def seed_state(home, pid, **settings) -> State:
    st = make_state(home, **settings)
    st.path = state_path(pid, home)
    st.save()
    return st


def host_for(home, pid, quit_when=None, llm=True, keep_state=False):
    """A PigeonHost wired to the fake DeepSeek; `quit_when()` lets a test close the browser from the session thread."""
    class H(PigeonHost):
        def pump(self, ctx):
            super().pump(ctx)
            if quit_when and quit_when():
                ctx.close()
    return lambda p, h, central=None: H(p, h, central, llm_factory=(lambda st: Llm("k", transport=fake_deepseek())) if llm else (lambda st: None))


def test_control_requires_token_and_a_live_session(tmp_path):
    with pytest.raises(PigeonError, match="not running on this machine"):
        control.request(1, "GET", "/pigeon/status", home=tmp_path)
    ctl = control.SessionControl(1, tmp_path)
    ctl.route("GET", "/x", lambda b: (200, {"ok": True}))
    ctl.start()
    try:
        assert control.request(1, "GET", "/x", home=tmp_path) == {"ok": True}
        info = json.loads(control.control_path(1, tmp_path).read_text())
        bad = httpx.get(f"http://127.0.0.1:{info['port']}/x", headers={"X-Pigeon-Control": "nope"})
        assert bad.status_code == 403
        assert httpx.get(f"http://127.0.0.1:{info['port']}/x").status_code == 403
    finally:
        ctl.close()
    assert not control.is_live(1, tmp_path)  # the file is removed on close


def serve_session_thread(host):
    """Stand in for the session loop: answer the host's hand-over calls like `pump()` does."""
    stop = threading.Event()
    t = threading.Thread(target=lambda: [host.service() or time.sleep(0.02) for _ in iter(stop.is_set, True)], daemon=True)
    t.start()
    return stop


def test_host_http_surface(tmp_path):
    seed_state(tmp_path, 1)
    host = host_for(tmp_path, 1)(1, tmp_path)
    host.st.put_lead(lead("alice", status="draft"))
    host.st.put_lead(lead("bob", status="draft", message=None))
    ctl = control.SessionControl(1, tmp_path)
    host.mount(ctl)
    ctl.start()
    stop = serve_session_thread(host)
    try:
        call = lambda m, p, b=None: control.request(1, m, p, b, home=tmp_path)
        op = lambda name, **args: call("POST", "/pigeon/op", {"op": name, "args": args})
        with pytest.raises(PigeonError, match="mode must be"):
            call("POST", "/pigeon/start", {"mode": "nuke"})
        with pytest.raises(PigeonError, match="unknown job"):
            call("POST", "/pigeon/job", {"kind": "format-disk"})
        host.st.product = None
        with pytest.raises(PigeonError, match="Set up the product"):
            call("POST", "/pigeon/start", {"mode": "find"})
        assert call("POST", "/pigeon/stop")["state"] == "idle"
        assert {l["username"] for l in op("leads_list", status="draft")["leads"]} == {"alice", "bob"}
        assert op("lead_approve", all=True)["approved"] == 1  # bob has no message to approve
        assert host.st.get_lead("alice")["status"] == "ready" and host.st.get_lead("bob")["status"] == "draft"
        with pytest.raises(PigeonError, match="no such lead"):
            op("lead_approve", username="ghost")
        with pytest.raises(PigeonError, match="unknown operation"):
            op("rm_rf")
        with pytest.raises(PigeonError, match="unknown setting"):
            op("settings_set", pairs={"nope": 1})
        assert call("GET", "/pigeon/status")["state"] == "idle"
        assert State(state_path(1, tmp_path)).get_lead("alice")["status"] == "ready"  # every op is persisted by the session thread
    finally:
        stop.set()
        ctl.close()


def test_no_key_is_refused_up_front(tmp_path):
    seed_state(tmp_path, 1)
    host = host_for(tmp_path, 1, llm=False)(1, tmp_path)
    ctl = control.SessionControl(1, tmp_path)
    host.mount(ctl)
    ctl.start()
    try:
        with pytest.raises(PigeonError, match="DeepSeek key"):
            control.request(1, "POST", "/pigeon/start", {"mode": "find"}, home=tmp_path)
    finally:
        ctl.close()


@pytest.fixture
def mock_context(browser):
    ctx = browser.new_context()
    ctx.seen = {"users": []}
    ctx.route("https://www.reddit.com/**", lambda r: mock_reddit(r, ctx.seen))
    ctx.new_page()  # the user's own tab: the agent must leave it alone
    yield ctx
    try:
        ctx.close()
    except Exception:
        pass


@needs_browser
def test_attach_to_live_session_find_then_detach(mock_context, tmp_path, fast):
    """The session thread owns the browser; another thread (like the CLI or desktop app) attaches the agent over the
    control channel, watches it work, and the user's own tab survives."""
    seed_state(tmp_path, 7)
    seen = {}

    def operator():
        try:
            for _ in range(100):
                if control.is_live(7, tmp_path):
                    break
                time.sleep(0.1)
            call = lambda m, p, b=None: control.request(7, m, p, b, home=tmp_path, timeout=10)
            assert call("GET", "/pigeon/status")["state"] == "idle"
            call("POST", "/pigeon/start", {"mode": "find", "minutes": 0.4})
            with pytest.raises(PigeonError, match="already"):
                call("POST", "/pigeon/start", {"mode": "find"})  # one agent per session
            time.sleep(0.5)
            seen["during"] = call("GET", "/pigeon/status")["state"]
            for _ in range(400):  # wait for the agent to finish by itself
                s = call("GET", "/pigeon/status")
                if s["state"] == "idle":
                    break
                time.sleep(0.5)
            seen["after"] = s
            seen["leads"] = call("POST", "/pigeon/op", {"op": "leads_list", "args": {}})["leads"]
            seen["pages_during_idle"] = None
        except BaseException as e:
            seen["error"] = e
        finally:
            seen["done"] = True

    t = threading.Thread(target=operator, daemon=True)
    t.start()
    interactive(mock_context, 7, {}, home=tmp_path, host_factory=host_for(tmp_path, 7, quit_when=lambda: seen.get("done")))
    t.join(5)
    assert "error" not in seen, seen.get("error")
    assert seen["during"] in ("running", "starting")
    assert seen["after"]["state"] == "idle" and seen["after"]["stats"]["leads"] >= 3
    assert {"founder_amy", "dev_bob", "sam_k"} <= {l["username"] for l in seen["leads"]}
    # state was persisted to the profile's data dir (so it syncs), and the control file is gone after close
    assert {"founder_amy", "dev_bob", "sam_k"} <= set(State(state_path(7, tmp_path)).leads)
    assert not control.is_live(7, tmp_path)


@needs_browser
def test_stop_from_another_thread_aborts_mid_flight(mock_context, tmp_path, fast):
    seed_state(tmp_path, 8)
    seen = {}

    def operator():
        try:
            for _ in range(100):
                if control.is_live(8, tmp_path):
                    break
                time.sleep(0.1)
            call = lambda m, p, b=None: control.request(8, m, p, b, home=tmp_path, timeout=10)
            call("POST", "/pigeon/start", {"mode": "find", "minutes": 10})
            for _ in range(200):  # wait until it is really browsing
                if any("Browsing" in e["msg"] for e in call("GET", "/pigeon/status")["log"]):
                    break
                time.sleep(0.2)
            t0 = time.time()
            call("POST", "/pigeon/stop")
            for _ in range(200):
                if call("GET", "/pigeon/status")["state"] == "idle":
                    break
                time.sleep(0.2)
            seen["stop_took"] = time.time() - t0
            seen["log"] = [e["msg"] for e in call("GET", "/pigeon/status")["log"]]
        except BaseException as e:
            seen["error"] = e
        finally:
            seen["done"] = True

    t = threading.Thread(target=operator, daemon=True)
    t.start()
    interactive(mock_context, 8, {}, home=tmp_path, host_factory=host_for(tmp_path, 8, quit_when=lambda: seen.get("done")))
    t.join(5)
    assert "error" not in seen, seen.get("error")
    assert seen["stop_took"] < 20, seen  # a 10-minute session, stopped promptly
    assert "Stopped" in seen["log"]


@needs_browser
def test_bridge_abort_interrupts_a_long_command(page):
    b = Bridge(page, pace=2)
    page.goto("https://www.reddit.com/r/saas/")
    b.ready()
    threading.Timer(2.0, lambda: setattr(b, "stop_flag", True)).start()  # what the control thread does
    t0 = time.time()
    with pytest.raises(Stop):
        b.cmd({"cmd": "browse", "maxPosts": 25, "seconds": 60})
    assert time.time() - t0 < 12  # not the 60 s the browse would have taken


@needs_browser
def test_dm_is_never_retried_after_a_lost_page(page):
    """A DM that errors mid-flight must not be sent a second time by the retry loop."""
    b = Bridge(page, pace=2)
    page.goto("https://www.reddit.com/user/alex_dev/")
    b.ready()
    calls = []
    real = b._run
    b._run = lambda c, t: (calls.append(c["cmd"]), (_ for _ in ()).throw(RuntimeError("context destroyed")))[1]
    with pytest.raises(RuntimeError):
        b.cmd({"cmd": "dm", "text": "hi"})
    assert calls == ["dm"]
    b._run = real
    calls.clear()
    b._run = lambda c, t: (calls.append(c["cmd"]), (_ for _ in ()).throw(RuntimeError("x")))[1]
    with pytest.raises(RuntimeError):
        b.cmd({"cmd": "readPost"})
    assert calls == ["readPost"] * 3  # read-only commands do retry


def test_vendored_content_script_matches_the_extension_build():
    """Pigeon runs a copy of the extension's built content script. When the extension repo is next door and has been built, the
    copy must be identical (refresh it with `npm run sync:pigeon` in the extension repo)."""
    built = Path(__file__).parent.parent.parent / ".output" / "chrome-mv3" / "content-scripts" / "reddit.js"
    if not built.exists():
        pytest.skip("extension build not found next to pigeon/")
    vendored = Path(__file__).parent.parent / "pigeon" / "agent" / "assets" / "reddit.js"
    assert vendored.read_bytes() == built.read_bytes(), "stale vendored script: run `npm run sync:pigeon` in the extension repo"
