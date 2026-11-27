"""The Firefox build of Pigeon's extension, running in a real Firefox (Camoufox) as an installed add-on.

Commands go to the REAL content script, in Firefox's real extension sandbox (Xray wrappers and all), through a test-only message
bridge that exists only in a `-m testbridge` build (and is checked to be absent from the normal builds). This covers the content
script on Firefox. It does not cover the extension's background page or panel: Playwright can't open moz-extension:// pages."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.test_pigeon import HAVE, mock_reddit, page_global

ROOT = Path(__file__).parent.parent.parent  # the extension repo, next to pigeon/
pytestmark = pytest.mark.skipif(not (HAVE and (ROOT / "package.json").exists() and shutil.which("npm")),
                                reason="needs Camoufox, the extension repo next to pigeon/, and npm")

SEND = """new Promise(res => { const id = Math.random();
  window.addEventListener('message', function h(e) { if (e.data && e.data.__pigeonTest === 'res' && e.data.id === id) { window.removeEventListener('message', h); res(e.data.res); } });
  window.postMessage({__pigeonTest: 'cmd', id, msg: %s}, '*'); })"""


def build(script: str) -> Path:
    subprocess.run(["npm", "run", script], cwd=ROOT, check=True, capture_output=True, timeout=180)
    return ROOT / ".output" / ("firefox-mv3-testbridge" if "testbridge" in script else "firefox-mv3")


@pytest.fixture(scope="module")
def ext_dir():
    d = build("build:firefox:testbridge")
    yield d
    build("build:firefox")  # leave the normal Firefox build in place, as a developer would expect


@pytest.fixture
def ff(ext_dir):
    from camoufox.sync_api import Camoufox
    prof = tempfile.mkdtemp()
    errors = []
    try:
        with Camoufox(headless=True, addons=[str(ext_dir)], persistent_context=True, user_data_dir=prof, main_world_eval=True) as ctx:
            seen = {"users": []}
            ctx.route("https://www.reddit.com/**", lambda r: mock_reddit(r, seen))
            p = ctx.new_page()
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.seen, p.errors = seen, errors

            p.cmd = lambda c: p.evaluate("mw:" + SEND % json.dumps(c))
            yield p
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def ready(p):
    for _ in range(80):
        if p.evaluate("() => !!document.querySelector('pigeon-cursor')"):
            return
        p.wait_for_timeout(250)
    raise AssertionError("the extension's content script never ran in Firefox")


def test_production_builds_have_no_test_bridge():
    for d in ("chrome-mv3", "firefox-mv3"):
        f = ROOT / ".output" / d / "content-scripts" / "reddit.js"
        if f.exists():
            assert "__pigeonTest" not in f.read_text(), f"the test bridge leaked into the {d} production build"


def test_firefox_extension_runs_every_command(ff):
    p = ff
    p.goto("https://www.reddit.com/r/saas/")
    ready(p)  # the add-on installed, matched the page, and its content script initialized (it draws the cursor)

    r = p.cmd({"cmd": "browse", "maxPosts": 10, "seconds": 8})
    assert r["ok"], r
    assert sorted(x["author"] for x in r["posts"]) == ["dev_bob", "founder_amy", "sam_k"]  # AutoModerator dropped
    assert p.evaluate("window.scrollY") > 100

    probe = p.cmd({"cmd": "probe", "kind": "feed"})
    assert all(c["ok"] for c in probe["checks"]), probe["checks"]

    assert p.cmd({"cmd": "clickLink", "hrefIncludes": "/r/saas/comments/p1"})["ok"]
    p.wait_for_url("**/comments/p1/**", timeout=20000)
    ready(p)
    rp = p.cmd({"cmd": "readPost"})
    assert rp["post"]["author"] == "founder_amy" and sorted(c["author"] for c in rp["comments"]) == ["reader_one", "reader_two"]

    p.evaluate("window.scrollTo(0, 0)")
    assert p.cmd({"cmd": "upvote", "scope": "post", "permalink": "/r/saas/comments/p1/t1/"}) == {"ok": True}
    assert p.cmd({"cmd": "upvote", "scope": "post", "permalink": "/r/saas/comments/p1/t1/"})["already"]
    assert p.cmd({"cmd": "upvote", "scope": "comment", "permalink": "/r/saas/comments/p1/t1/c1/"})["ok"]  # shadow DOM, through Xray
    assert page_global(p, "window.__log.upvotes") == 2

    text = "Happy to help: I would start where buyers already complain."
    assert p.cmd({"cmd": "postComment", "text": text})["ok"]
    assert page_global(p, "window.__log.comments") == [text]

    assert p.errors == [], p.errors


def test_firefox_extension_dm_submit_and_inbox(ff):
    p = ff
    msg = "Hey alex_dev, saw your post about first customers. I build a tool for that, curious how you do it today?"
    p.goto("https://www.reddit.com/user/alex_dev/")
    ready(p)
    assert p.cmd({"cmd": "dm", "text": msg})["ok"]
    assert page_global(p, "window.__log.dms") == [msg]

    p.goto("https://www.reddit.com/user/ratelimited/")
    ready(p)
    res = p.cmd({"cmd": "dm", "text": msg})
    assert not res["ok"] and res["code"] == "rate-limit"

    p.goto("https://www.reddit.com/chat")
    ready(p)
    rooms = p.cmd({"cmd": "scanInbox"})["rooms"]
    assert len(rooms) == 4 and any("thanks, that sounds useful" in r["text"] for r in rooms)

    p.goto("https://www.reddit.com/r/saas/submit?type=TEXT")
    ready(p)
    assert p.cmd({"cmd": "submitPost", "title": "A title typed in Firefox", "body": "Some body text."})["ok"]
    p.wait_for_url("**/comments/new1/**", timeout=30000)  # the redirect to the new thread is the proof
    assert p.seen["posts"] == [{"title": "A title typed in Firefox", "body": "Some body text."}]
    assert p.errors == [], p.errors


def test_abort_stops_a_long_command_in_firefox(ff):
    import time
    p = ff
    p.goto("https://www.reddit.com/r/saas/")
    ready(p)
    t0 = time.time()
    # Playwright's sync API is single-threaded: start the browse without awaiting it, then abort from the same thread
    p.evaluate("mw:(() => { window.__r = null; const id = 'long'; window.addEventListener('message', function h(e) { if (e.data && e.data.id === id) { window.__r = e.data.res; } }); "
               "window.postMessage({__pigeonTest: 'cmd', id, msg: {cmd: 'browse', maxPosts: 25, seconds: 60}}, '*'); })()")
    p.wait_for_timeout(2500)
    p.evaluate("mw:window.postMessage({__pigeonTest: 'cmd', id: 'abort', msg: {cmd: 'abort'}}, '*')")
    for _ in range(40):
        if page_global(p, "window.__r") is not None:
            break
        p.wait_for_timeout(250)
    r = page_global(p, "window.__r")
    assert r is not None and (r.get("aborted") or not r.get("ok")), r
    assert time.time() - t0 < 25  # not the 60 seconds the browse would have taken
