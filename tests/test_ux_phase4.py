"""Phase 4 end to end: the cloud viewer on a 390px phone (and a 1280px desktop) against a real runner with a fake
session (no Camoufox), and the classic page (/legacy, ui.html) against a stubbed API.

One command:  uv run pytest tests/test_ux_phase4.py
Screenshots land in $PIGEON_UX_SHOTS (default: the session scratchpad viewer-track folder)."""
import json
import re
import time
from pathlib import Path

import pytest

from tests._viewer_support import SHOTS, FakeSession, Runner, chromium, wait_for

CHROME = chromium()
pytestmark = pytest.mark.skipif(not CHROME, reason="no Chromium for Playwright")
PHONE = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=3)
DESKTOP = dict(viewport={"width": 1280, "height": 800})
BLOBS = """
window.__blobs = { made: 0, freed: 0 };
const _c = URL.createObjectURL, _r = URL.revokeObjectURL;
URL.createObjectURL = (...a) => { window.__blobs.made++; return _c(...a); };
URL.revokeObjectURL = (...a) => { window.__blobs.freed++; return _r(...a); };
"""
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿⭐✨▶\U0001F000-\U0001F2FF]")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True)
        yield b
        b.close()


@pytest.fixture
def runner():
    r = Runner(idle_timeout=600, reap_every=0.2)
    yield r
    r.close()


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def open_viewer(browser, runner, s, ctx_kw=PHONE):
    ctx = browser.new_context(**ctx_kw)
    ctx.add_init_script(BLOBS)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(runner.view(s))
    page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
    return ctx, page


def touch(cdp, kind, *pts):
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y} for x, y in pts]})


# ================================================================ P4-1 phone usability
def test_viewer_phone_layout_targets_and_tabs(browser, runner):
    s = runner.add(FakeSession())
    ctx, page = open_viewer(browser, runner, s)
    meta = page.get_attribute("meta[name=viewport]", "content")
    assert "maximum-scale" not in meta and "user-scalable=no" not in meta, "pinch zoom must work"
    # flex column filling the dynamic viewport, nothing sideways
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.evaluate("""() => { const r = id => document.querySelector(id).getBoundingClientRect();
        return { stage: r('#stage'), kb: r('.kb'), app: r('.app') }; }""")
    assert box["app"]["height"] == 844 and box["stage"]["bottom"] <= box["kb"]["top"] + 0.5 and box["kb"]["bottom"] <= 844
    # every button is at least 44px on touch
    small = page.evaluate("""() => [...document.querySelectorAll('button')].filter(b => b.offsetParent)
        .map(b => { const r = b.getBoundingClientRect(); return [b.getAttribute('aria-label') || b.textContent, r.width, r.height]; })
        .filter(([, w, h]) => w < 44 || h < 44)""")
    assert not small, small
    # tabs are real buttons with names; one close button per tab once there are two
    tab = page.locator("#tabs button[role=tab]")
    assert tab.count() == 1 and tab.first.get_attribute("aria-label") == "Tab 1: Reddit"
    page.evaluate("document.querySelector('#tabs [role=tab]').__mark = 1")
    page.wait_for_timeout(1500)
    assert page.evaluate("document.querySelector('#tabs [role=tab]').__mark") == 1, "tab strip must not be rebuilt when nothing changed"
    page.tap("#tabs button[aria-label='New tab']")
    page.wait_for_function("document.querySelectorAll('#tabs [role=tab]').length === 2")
    closes = page.locator("#tabs button[aria-label^='Close tab']")
    assert closes.count() == 2
    closes.first.tap()
    wait_for(lambda: s.cmds_of("closetab"))
    assert s.cmds_of("closetab")[0]["i"] == 0
    shot(page, "phone-390-running")
    assert not page.errors
    ctx.close()


def test_viewer_tap_types_pastes_and_scrolls(browser, runner):
    s = runner.add(FakeSession())
    ctx, page = open_viewer(browser, runner, s)
    r = page.evaluate("(() => { const b = document.querySelector('#screen').getBoundingClientRect(); return [b.x, b.y, b.width]; })()")
    page.tap("#screen", position={"x": 195, "y": 40})
    wait_for(lambda: s.cmds_of("click"))
    c = s.cmds_of("click")[0]
    assert abs(c["x"] - 195 * 1280 / r[2]) <= 2 and abs(c["y"] - 40 * 1280 / r[2]) <= 2
    assert page.evaluate("document.activeElement.id") == "kbd", "a tap must give the soft keyboard somewhere to type"
    assert page.locator(".ripple").count() >= 1, "taps need visible feedback"
    page.keyboard.type("hi")
    page.keyboard.press("Backspace")
    page.keyboard.press("Enter")
    wait_for(lambda: len(s.cmds_of("key")) >= 2)
    assert "".join(c["text"] for c in s.cmds_of("type")) == "hi"
    assert [c["key"] for c in s.cmds_of("key")][:2] == ["Backspace", "Enter"]
    # Ctrl+V is left alone so the paste event fires, and the pasted text is typed remotely
    prevented = page.evaluate("""() => { const e = new KeyboardEvent('keydown', { key: 'v', ctrlKey: true, bubbles: true, cancelable: true });
        document.activeElement.dispatchEvent(e); return e.defaultPrevented; }""")
    assert not prevented
    page.evaluate("""() => { const d = new DataTransfer(); d.setData('text/plain', 'pasted!');
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: d, bubbles: true, cancelable: true })); }""")
    wait_for(lambda: any(c["text"] == "pasted!" for c in s.cmds_of("type")))
    # Meta (Cmd) means Control on a Windows profile
    page.evaluate("""() => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', metaKey: true, bubbles: true, cancelable: true }))""")
    wait_for(lambda: any(c["key"] == "Control+a" for c in s.cmds_of("key")))
    # one-finger drag scrolls both ways; two fingers are left to the browser (pinch zoom)
    cdp = ctx.new_cdp_session(page)
    touch(cdp, "touchStart", (300, 300))
    for i in range(1, 6):
        touch(cdp, "touchMove", (300 - i * 20, 300 - i * 15))
    touch(cdp, "touchEnd")
    wait_for(lambda: s.cmds_of("scroll"))
    sc = s.cmds_of("scroll")
    assert sum(c["dx"] for c in sc) > 0 and sum(c["dy"] for c in sc) > 0
    pinch = page.evaluate("""() => { const st = document.querySelector('#stage');
        const t = (id, x) => new Touch({ identifier: id, target: st, clientX: x, clientY: 300 });
        const e = new TouchEvent('touchmove', { touches: [t(1, 100), t(2, 250)], cancelable: true, bubbles: true });
        st.dispatchEvent(e); return [e.defaultPrevented, getComputedStyle(st).touchAction]; }""")
    assert pinch[0] is False and "pinch-zoom" in pinch[1]
    ctx.close()


def test_viewer_meta_stays_meta_on_a_mac_profile(browser, runner):
    s = runner.add(FakeSession(os_name="macos"))
    ctx, page = open_viewer(browser, runner, s, DESKTOP)
    page.click("#screen", position={"x": 100, "y": 100})
    page.keyboard.press("Meta+a")
    wait_for(lambda: s.cmds_of("key"))
    assert s.cmds_of("key")[0]["key"] == "Meta+a"
    ctx.close()


def test_viewer_zoom_send_and_frames(browser, runner):
    s = runner.add(FakeSession())
    ctx, page = open_viewer(browser, runner, s)
    z = page.locator("button#zoom")
    assert z.inner_text() == "Fit" and page.evaluate("document.querySelector('#screen').getBoundingClientRect().width") <= 390
    z.tap()
    assert z.inner_text() == "100%" and round(page.evaluate("document.querySelector('#screen').getBoundingClientRect().width")) == 1280
    z.tap()
    assert z.inner_text() == "150%" and round(page.evaluate("document.querySelector('#screen').getBoundingClientRect().width")) == 1920
    z.tap()
    assert z.inner_text() == "Fit"
    # Send and Enter in the text box do the same thing: type the text
    page.fill("#txt", "abc")
    page.press("#txt", "Enter")
    page.fill("#txt", "def")
    page.tap("button:text-is('Send')")
    wait_for(lambda: len(s.cmds_of("type")) >= 2)
    assert [c["text"] for c in s.cmds_of("type")] == ["abc", "def"] and not s.cmds_of("key")
    # frames don't leak blob URLs
    page.wait_for_timeout(2000)
    b = page.evaluate("window.__blobs")
    assert b["made"] >= 5 and b["made"] - b["freed"] <= 1, b
    ctx.close()


# ================================================================ P4-2 status and feedback
def test_viewer_status_card_title_and_note(browser, runner):
    s = runner.add(FakeSession(start_delay=2.5, name="acct-a"))
    ctx = browser.new_context(**PHONE)
    page = ctx.new_page()
    page.goto(runner.view(s))
    page.wait_for_selector("#card:has-text('Opening the browser')")
    shot(page, "phone-390-starting")
    assert "starting" not in page.inner_text("body")
    page.wait_for_function("document.querySelector('#card').hidden", timeout=8000)
    assert "acct-a" in page.title()
    assert "Anyone with this link can control this browser" in page.inner_text("body")
    ctx.close()


def test_viewer_url_bar_searches_words(browser, runner):
    s = runner.add(FakeSession())
    ctx, page = open_viewer(browser, runner, s, DESKTOP)
    for typed in ("cats", "example.com/a", "http://x.test/", "localhost:3000"):
        page.fill("#url", typed)
        page.press("#url", "Enter")
    wait_for(lambda: len(s.cmds_of("goto")) == 4)
    assert [c["url"] for c in s.cmds_of("goto")] == ["https://duckduckgo.com/?q=cats", "https://example.com/a", "http://x.test/",
                                                    "http://localhost:3000"]
    shot(page, "desktop-1280-running")
    ctx.close()


def test_viewer_paused_input_and_stop_feedback(browser, runner):
    s = runner.add(FakeSession(sync_seconds=1.5))
    ctx, page = open_viewer(browser, runner, s)
    page.evaluate("window.pigeonViewer.ws.close()")
    page.evaluate("window.pigeonViewer.send({t: 'key', key: 'Tab'})")
    page.wait_for_selector("#toast:has-text('Reconnecting — input paused')")
    page.wait_for_function("document.querySelector('#card').hidden", timeout=8000)
    # Stop can fail (network): say so plainly and let them retry
    page.route("**/sessions/**", lambda r: r.abort())
    page.on("dialog", lambda d: d.accept())
    stop = page.locator("button#stop")
    assert stop.inner_text() == "Stop and save login"
    stop.tap()
    page.wait_for_selector("#toast:has-text(\"Couldn't reach the runner\")")
    assert stop.is_enabled() and s.status == "running"
    page.unroute("**/sessions/**")
    stop.tap()
    page.wait_for_selector("#card:has-text('Saving your login')")
    page.wait_for_selector("#card:has-text('This session has ended')", timeout=10000)
    assert "Your login was saved" in page.inner_text("#card")
    shot(page, "phone-390-ended")
    ctx.close()


def test_viewer_error_is_a_plain_sentence(browser, runner):
    s = runner.add(FakeSession(start_delay=1.0, fail="502: proxy not working: ConnectError [Errno 61]"))
    ctx = browser.new_context(**DESKTOP)
    page = ctx.new_page()
    page.goto(runner.view(s))
    page.wait_for_selector("#card:has-text(\"proxy isn't responding\")", timeout=8000)
    txt = page.inner_text("#card")
    assert "Errno" not in txt and "Error:" not in txt
    shot(page, "desktop-1280-error")
    ctx.close()


# ================================================================ P4-3 classic page
UI = (Path(__file__).parent.parent / "pigeon" / "ui.html").read_text()
LEAD = dict(username="alice", subreddit="saas", fit=88, status="draft", reason="asks", message="Hey alice", error=None)
PG = dict(state="idle", job=None, mode=None, last=None, central=True, product="Acme",
          stats=dict(scanned=5, leads=2, upvotes=1, dms=1, errors=0), daily=dict(count=1), leads={"draft": 1}, log=[])
OPS = {"leads_list": dict(leads=[LEAD]),
       "opps_list": dict(opportunities=[dict(id="o1", subreddit="saas", title="T", post_url="https://x.test", why="w", draft="d", status="pending")]),
       "ideas_list": dict(ideas=[]), "posts_list": dict(posted=[]),
       "product_show": dict(product=dict(url="https://acme.test", name="Acme", summary="s", offer="o", icp="i")),
       "subs_edit": dict(subreddits=[dict(name="saas", enabled=True, score=70, rules_risk="low", reason="good")]),
       "settings_set": dict(settings=dict(daily_dm_cap=40)), "template_show": dict(template="t")}


def pwait(page, fn, timeout=6):
    """wait_for for routed pages: route handlers run on this thread, so wait inside Playwright, not time.sleep."""
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return
        page.wait_for_timeout(100)
    raise AssertionError("timed out")


def state(**kw):
    out = dict(signed_in=True, server="s", me=dict(id=1, email="a@x.io", teams=[dict(id=1, name="T", role="owner")]), device="d", team=1,
               role="owner", members=[dict(id=1, name="A", email="a@x.io", role="owner"), dict(id=2, name="B", email="b@x.io", role="member")],
               profiles=[dict(id=5, name="acct-a", os="windows", proxy=None, tags=[], notes="", version=1, access="edit", running=None,
                              running_here=True, canvas="noise", start_url="")],
               jobs={"run-5": dict(alive=True, log=[], pigeon=PG)}, host_os="macos", browser=True,
               templates=[dict(id=7, name="US", settings=dict(os="windows", canvas="noise"))], runners=[dict(id=3, name="r1", url="https://r")],
               has_key=True)
    out.update(kw)
    return out


class Classic:
    def __init__(self, browser, st=None, ctx_kw=DESKTOP, scheme="light"):
        self.calls, self.dialogs, self.accept, self.st = [], [], True, st or state()
        self.ctx = browser.new_context(**ctx_kw, color_scheme=scheme)
        self.page = p = self.ctx.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.on("dialog", self._dialog)
        p.route("http://pigeon.test/api/**", self._api)
        p.route("http://pigeon.test/legacy", lambda r: r.fulfill(content_type="text/html", body=UI))
        p.goto("http://pigeon.test/legacy")

    def _dialog(self, d):
        self.dialogs.append((d.type, d.message))
        d.accept() if self.accept else d.dismiss()

    def _api(self, route):
        req = route.request
        path = req.url.split("/api", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, path, body))
        code, out = 200, {}
        if path == "/state":
            out = self.st
        elif path == "/profiles/5/pigeon/status":
            out = PG
        elif path == "/profiles/5/pigeon/op":
            out = dict(OPS.get(body["op"], {}), out="")
        elif path == "/apikey":
            out = dict(base="http://127.0.0.1:4400", key="k")
        elif path == "/profiles" and req.method == "POST":
            code, out = 422, {"detail": [{"loc": ["body", "name"], "type": "string_too_short", "msg": "x", "ctx": {"min_length": 1}}]}
        elif path == "/profiles/bulk":
            out = dict(created=3)
        elif path == "/login":
            time.sleep(0.6)
            out = {}
        route.fulfill(status=code, content_type="application/json", body=json.dumps(out))

    def n(self, method, path):
        return sum(1 for m, p, _ in self.calls if m == method and p == path)


def test_classic_typing_survives_refresh(browser):
    c = Classic(browser)
    p = c.page
    p.wait_for_selector("#q")
    p.click("#q")
    p.keyboard.type("acc")
    before = c.n("GET", "/state")
    p.wait_for_timeout(3600)
    assert p.evaluate("document.activeElement.id") == "q" and p.input_value("#q") == "acc"
    assert c.n("GET", "/state") == before, "no refresh while typing"
    # a focused select stays open too
    p.focus("header select")
    p.evaluate("document.querySelector('header select').__mark = 1")
    p.wait_for_timeout(3300)
    assert p.evaluate("document.querySelector('header select').__mark") == 1
    # and refresh resumes once you're done
    p.evaluate("document.activeElement.blur()")
    pwait(p, lambda: c.n("GET", "/state") > before)
    assert not p.errors
    c.ctx.close()


def test_classic_confirms_risky_actions(browser):
    c = Classic(browser)
    p = c.page
    c.accept = False
    p.click("button:text-is('Agent controls')")
    p.wait_for_selector("#pg button:text-is('Send now')")
    for label in ("Send now", "Approve all drafts"):
        n = len(c.dialogs)
        p.click(f"#pg button:text-is('{label}')")
        pwait(p, lambda: len(c.dialogs) > n, timeout=3)
    p.click("#pg button:text-is('Comments')")
    p.wait_for_selector("#pg button:text-is('Approve & post')")
    p.click("#pg button:text-is('Approve & post')")
    p.click("#pg button:text-is('Product')")
    p.wait_for_selector("#pg button[aria-label='Remove r/saas']")
    p.click("#pg button[aria-label='Remove r/saas']")
    pwait(p, lambda: len(c.dialogs) == 4, timeout=3)
    assert len(c.dialogs) == 4 and all(t == "confirm" for t, _ in c.dialogs), c.dialogs
    jobs = [(b or {}).get("kind") for m, path, b in c.calls if path.endswith("/pigeon/job")]
    ops = [b for m, path, b in c.calls if path.endswith("/pigeon/op")]
    assert "send" not in jobs and "post" not in jobs
    assert not any(o["op"] == "lead_approve" for o in ops)
    assert not any(o["op"] == "subs_edit" and o["args"].get("remove") for o in ops)
    # say yes and it goes through
    c.accept = True
    p.click("#pg button:text-is('People')")
    p.wait_for_selector("#pg button:text-is('Send now')")
    p.click("#pg button:text-is('Send now')")
    pwait(p, lambda: any(path.endswith("/pigeon/job") and b["kind"] == "send" for m, path, b in c.calls))
    p.click("#pg button:text-is('Close')")
    # team: owner change, template delete, runner removal
    c.accept = False
    p.click("nav button:text-is('Team')")
    p.wait_for_selector("text=Templates")
    n = len(c.dialogs)
    p.select_option("select[aria-label='Role for b@x.io']", "owner")
    p.click("button[aria-label='Delete template US']")
    p.click("button[aria-label='Remove runner r1']")
    pwait(p, lambda: len(c.dialogs) == n + 3, timeout=3)
    assert "owner" in c.dialogs[n][1].lower()
    assert p.input_value("select[aria-label='Role for b@x.io']") == "member", "a cancelled role change goes back"
    assert not [x for x in c.calls if x[0] in ("POST", "DELETE") and ("/role" in x[1] or "/templates/" in x[1] or "/runners/" in x[1])]
    # deleting an account asks first
    p.click("nav button:text-is('Accounts')")
    p.click("button[aria-label='More actions for acct-a']")
    p.click(".menu.open button:text-is('Edit')")
    p.click("#dlg button:text-is('Delete')")
    pwait(p, lambda: len(c.dialogs) == n + 4, timeout=3)
    assert "Delete this account" in c.dialogs[-1][1]
    assert not [x for x in c.calls if x[0] == "DELETE" and x[1] == "/profiles/5"]
    assert not p.errors
    c.ctx.close()


def test_classic_readable_errors_banner_and_back_link(browser):
    c = Classic(browser)
    p = c.page
    p.wait_for_selector("header")
    back = p.locator("header a[href='/']")
    assert back.inner_text().strip() == "← Back to the new dashboard"
    p.click("button:text-is('Add account')")
    p.click("#n_go")
    p.wait_for_selector("#derr:has-text('Name is required.')")
    assert "{" not in p.inner_text("#derr")
    p.click("#dlg button:text-is('Cancel')")
    p.click("button:text-is('Add many')")
    p.click("#b_go")
    p.wait_for_selector("#notice:has-text('Created 3 accounts.')")
    assert not [d for d in c.dialogs if d[0] == "alert"], c.dialogs
    shot(p, "classic-1280-light")
    c.ctx.close()


def test_classic_login_is_a_form(browser):
    c = Classic(browser, st=dict(signed_in=False, server="http://127.0.0.1:4400"))
    p = c.page
    p.wait_for_selector("#em")
    for lab in p.locator("form label").all():
        f = lab.get_attribute("for")
        assert f and p.locator(f"#{f}").count() == 1, lab.inner_text()
    p.fill("#em", "a@x.io")
    p.fill("#pw", "password1")
    p.press("#pw", "Enter")
    p.wait_for_selector("text=Deriving keys")
    cls = p.evaluate("[...document.querySelectorAll('*')].find(e => e.children.length === 0 && /Deriving keys/.test(e.textContent)).className")
    assert "err" not in cls
    pwait(p, lambda: c.n("POST", "/login") == 1)
    shot(p, "classic-1280-login")
    c.ctx.close()


def test_classic_tokens_focus_vocabulary_and_no_emoji_buttons(browser):
    c = Classic(browser)
    p = c.page
    p.wait_for_selector("button:text-is('Add account')")
    assert p.evaluate("getComputedStyle(document.querySelector('button.p')).backgroundColor") == "rgb(13, 13, 13)"
    p.focus("button:text-is('Add account')")
    p.keyboard.press("Shift+Tab")
    p.keyboard.press("Tab")
    assert p.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none", "keyboard focus needs a ring"
    prose = "(() => { const m = document.querySelector('main').cloneNode(true); m.querySelectorAll('code').forEach(c => c.remove()); return m.innerText; })()"
    texts = [p.evaluate(prose)]
    p.click("button:text-is('Agent controls')")
    p.wait_for_selector("#pg h3")
    for t in ("People", "Run", "Comments", "Messages", "Product", "Settings"):
        p.click(f"#pg button:text-is('{t}')")
        p.wait_for_timeout(150)
        bad = [b for b in p.locator("#dlg button").all_inner_texts() if EMOJI.search(b)]
        assert not bad, bad
    p.click("#pg button:text-is('Close')")
    p.click("nav button:text-is('Team')")
    p.wait_for_selector("text=Templates")
    texts.append(p.evaluate(prose))
    bad = [b for b in p.locator("button").all_inner_texts() if EMOJI.search(b)]
    assert not bad, bad
    for t in texts:
        assert not re.search(r"\bprofiles?\b", t, re.I), re.findall(r".{30}\bprofiles?\b.{30}", t, re.I)
    c.ctx.close()
    # dark mode has its own status colours
    d = Classic(browser, scheme="dark")
    d.page.wait_for_selector("button:text-is('Add account')")
    warn = d.page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--warn').trim()")
    assert warn == "#f5a623"
    shot(d.page, "classic-1280-dark")
    d.ctx.close()
    ph = Classic(browser, ctx_kw=PHONE)
    ph.page.wait_for_selector("button:text-is('Add account')")
    shot(ph.page, "classic-390-light")
    ph.ctx.close()
