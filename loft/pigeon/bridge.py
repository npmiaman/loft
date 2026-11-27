"""Drives Pigeon's content script (the same file the Chrome extension ships) inside a Camoufox page."""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from importlib import resources
from typing import Any

ASSETS = resources.files("loft.pigeon") / "assets"


class Stop(Exception):
    """The run was stopped (Stop button on the page, Ctrl-C, or a governor pause)."""


class Bridge:
    def __init__(self, page, pace: float = 1.0):
        self.page = page
        self.pace = min(2.0, max(0.5, pace))
        self.stop_flag = False
        self.on_tick = None  # called on every check()/poll, on the thread that owns the browser
        self._shim = (ASSETS / "shim.js").read_text()
        self._script = (ASSETS / "reddit.js").read_text()

    def nap(self, seconds: float) -> None:
        """Sleep, while letting Playwright process browser events (URL changes, route callbacks, closes). Plain
        time.sleep() doesn't: with the sync API, `page.url` goes stale and navigations can stall until the next call."""
        try:
            self.page.wait_for_timeout(max(0.0, seconds) * 1000)
        except Exception:
            time.sleep(max(0.0, seconds))  # the page is gone; just wait

    def snapshot(self, directory, label: str, keep: int = 20) -> str | None:
        """Save what the page looks like right now (screenshot + HTML + URL) so a failure on a real site can be diagnosed
        afterwards. Local only: it can contain personal data, so it is never synced. Keeps the newest `keep` snapshots."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:40]}"
            self.page.screenshot(path=str(directory / f"{stem}.png"), timeout=10_000)
            (directory / f"{stem}.html").write_text(f"<!-- {self.page.url} -->\n" + self.page.content()[:400_000])
            old = sorted({f.stem for f in directory.iterdir() if f.suffix in (".png", ".html")})[:-keep]
            for f in directory.iterdir():
                if f.stem in old:
                    f.unlink(missing_ok=True)
            return str(directory / stem)
        except Exception:
            return None

    # ---- injection -------------------------------------------------------------------------
    def ensure(self) -> None:
        """(Re)install the shim + content script. A full page load wipes it, SPA navigation doesn't."""
        self.page.wait_for_load_state("domcontentloaded")
        if self.page.evaluate("() => !!window.__pigeon"):
            return
        src = f"(function(){{ ({self._shim})({json.dumps({'pace': self.pace})}); {self._script}\n;}})()"
        self.page.evaluate(src)

    def ready(self, timeout: float = 25.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            self.check()
            try:
                self.ensure()
                if self.page.evaluate("() => window.__pigeon.send({target:'page',cmd:'ping'})").get("ok"):
                    return
            except Exception:
                pass  # mid-navigation; try again
            self.nap(0.5)
        raise RuntimeError("Reddit page did not become ready (is this profile logged in to Reddit?)")

    # ---- control ---------------------------------------------------------------------------
    def check(self) -> None:
        if self.on_tick:
            self.on_tick()
        if self.stop_flag:
            raise Stop()

    def stopped_on_page(self) -> bool:
        try:
            return bool(self.page.evaluate("() => !!(window.__pigeon && window.__pigeon.stopped)"))
        except Exception:
            return False

    def wait(self, seconds: float) -> None:
        end = time.time() + seconds
        last_poll = 0.0
        while True:
            self.check()
            now = time.time()
            if now - last_poll > 2:
                last_poll = now
                if self.stopped_on_page():
                    self.stop_flag = True
                    raise Stop()
            if now >= end:
                return
            self.nap(min(0.5, end - now))

    def abort(self) -> None:
        self.stop_flag = True
        try:
            self.page.evaluate("() => window.__pigeon && window.__pigeon.send({target:'page',cmd:'abort'})")
        except Exception:
            pass

    # ---- commands --------------------------------------------------------------------------
    # Retrying after a lost page context is only safe for commands that don't act on Reddit.
    IDEMPOTENT = {"ping", "hud", "browse", "readPost", "clickLink", "scanPage", "scanInbox", "idleClick", "moveCursor", "url"}
    _START = """([id, c]) => { const r = (window.__pigeonRes = window.__pigeonRes || {});
        window.__pigeon.send(Object.assign({target: 'page'}, c)).then(v => { r[id] = {v: v === undefined ? null : v}; },
                                                                       e => { r[id] = {e: String(e)}; }); }"""
    _POLL = """id => { const x = window.__pigeonRes && window.__pigeonRes[id]; if (x) delete window.__pigeonRes[id]; return x || null; }"""

    def cmd(self, c: dict, timeout: float = 180.0) -> Any:
        attempts = 3 if c.get("cmd") in self.IDEMPOTENT else 1
        last: Exception | None = None
        for _ in range(attempts):
            self.check()
            try:
                self.ensure()
                return self._run(c, timeout)
            except Stop:
                raise
            except Exception as e:  # navigation destroyed the context, page closed, timeout...
                last = e
                self.nap(1)
        raise last or RuntimeError(f"command failed: {c.get('cmd')}")

    def _run(self, c: dict, timeout: float) -> Any:
        """Start the command in the page and poll for its result, so a stop request (from another thread, via
        `stop_flag`, or the on-page Stop button) can abort it mid-flight instead of waiting it out."""
        cid = uuid.uuid4().hex
        self.page.evaluate(self._START, [cid, c])
        end = time.time() + timeout
        aborted_at: float | None = None
        last_page_poll = 0.0
        while True:
            if self.on_tick:
                self.on_tick()
            r = self.page.evaluate(self._POLL, cid)
            if r is not None:
                if "e" in r:
                    raise RuntimeError(r["e"])
                res = r["v"]
                if isinstance(res, dict) and res.get("aborted"):
                    self.stop_flag = True
                    raise Stop()
                return res  # a finished command is returned even if a stop arrived meanwhile; the next check() stops
            now = time.time()
            if now - last_page_poll > 2:
                last_page_poll = now
                if self.stopped_on_page():
                    self.stop_flag = True
            if self.stop_flag:
                if aborted_at is None:
                    aborted_at = now
                    self.page.evaluate("() => window.__pigeon.send({target:'page',cmd:'abort'})")
                elif now - aborted_at > 5:
                    raise Stop()  # the command isn't answering; give up on it
            if now > end:
                self.page.evaluate("() => window.__pigeon.send({target:'page',cmd:'abort'})")
                raise TimeoutError(f"timed out: {c.get('cmd')}")
            self.nap(0.25)

    def hud(self, text: str) -> None:
        try:
            self.cmd({"cmd": "hud", "text": text}, 5)
        except Exception:
            pass

    # ---- navigation ------------------------------------------------------------------------
    def navigate(self, url: str, settle=(0.9, 6.0, 1.9)) -> None:
        self.check()
        self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        self.ready()
        self.wait(clamp(lognormal(settle[2], 0.4), settle[0], settle[1]))

    def click_nav(self, href_includes: str, fallback_url: str) -> bool:
        """Click a link like a person would; if it isn't there, just go to the URL."""
        r = self.cmd({"cmd": "clickLink", "hrefIncludes": href_includes}, 10)
        if r and r.get("ok"):
            needle = href_includes.lower()
            end = time.time() + 10
            while time.time() < end:
                self.check()
                if needle in self.page.url.lower():
                    self.ready()
                    self.wait(clamp(lognormal(1.5, 0.4), 0.7, 5.0))
                    return True
                self.nap(0.4)
        self.navigate(fallback_url)
        return False

    def reddit_json(self, path: str) -> Any:
        """Same-origin fetch from the logged-in page (used for subreddit discovery)."""
        sep = "&" if "?" in path else "?"
        return self.page.evaluate(
            """async p => { const r = await fetch(p, {credentials:'include', headers:{Accept:'application/json'}});
                            if (!r.ok) throw new Error(String(r.status)); return r.json(); }""",
            f"{path}{sep}raw_json=1",
        )


def lognormal(median: float, sigma: float = 0.45) -> float:
    return median * random.lognormvariate(0, sigma)


def clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))
