"""Phase 1 (design system) end to end: every screen of the real dashboard, light and dark, at 1280 and 390 px wide, against a
real `pigeon server` + `pigeon app --no-window` sandbox (ports 4600-4619, temp dir), with a real account browser open so the
agent drawer has live data. Each screen is screenshotted (to $PIGEON_UX_SHOTS, default a temp dir) and checked for: no page
errors, no sideways scrolling, one control height per density, the one focus style, no emoji/glyph icons, the glossary.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_ux_phase1.py -s
Needs the built UI, a Playwright Chromium and the Camoufox browser."""
import os
import re
import tempfile
from pathlib import Path

import httpx
import pytest

from tests.test_pigeon import HAVE
from tests.ux_harness import Sandbox, browser, needs_ui, open_settings, SETTINGS_WIN, close_settings  # noqa: F401

pytestmark = [needs_ui, pytest.mark.skipif(not HAVE, reason="needs the Camoufox browser (the app launches accounts with it)")]
SHOTS = Path(os.environ.get("PIGEON_UX_SHOTS") or Path(tempfile.gettempdir()) / "pigeon-ux-shots")  # overwritten each run
SHOTS.mkdir(parents=True, exist_ok=True)
GLYPHS = set("▶✨↗⋯")


@pytest.fixture(scope="module")
def sb():
    s = Sandbox()
    yield s
    s.close()


@pytest.fixture(scope="module")
def ready(sb, browser):
    """Owner signed up, a team, two accounts (one with tags), and one of them open with its agent session up."""
    page = browser.new_page()
    page.set_default_timeout(30000)
    page.goto(sb.url)
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.click("main button:has-text('Add account') >> nth=0")  # the team already has its starter account
    page.fill("#n_name", "Client A")
    page.click("#n_go")
    page.wait_for_selector("text=Client A", timeout=60000)
    r = httpx.post(sb.url + "/api/profiles", headers={"X-Pigeon": "1"}, json={"name": "Client B", "tags": ["reddit", "eu"]}, timeout=60)
    assert r.status_code == 200, r.text
    page.reload()
    page.click("[data-account='Client A'] button:text-is('Open browser')")
    page.wait_for_selector("button:text-is('Agent controls')", timeout=150000)
    page.close()
    return sb


def check(page, name, glossary=True):
    page.wait_for_timeout(500)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 0, f"{name}: scrolls sideways by {over}px"
    text = page.inner_text("body")
    assert not GLYPHS & set(text), f"{name}: glyph icons {GLYPHS & set(text)}"
    if glossary:
        assert not re.search(r"(?<!/)\bprofiles?\b", text.lower()), f"{name}: says 'profile' (the word is 'account'; API paths excepted)"
    hs = set(page.eval_on_selector_all("main [data-size=md]", "els => els.filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))"))
    assert hs <= {36}, f"{name}: md controls of heights {hs}"
    nameless = page.evaluate("[...document.querySelectorAll('button')].filter(b => b.offsetParent && !b.innerText.trim() && !b.getAttribute('aria-label') && !b.getAttribute('aria-labelledby')).length")
    assert nameless == 0, f"{name}: {nameless} icon buttons without a name"


@pytest.mark.parametrize("width,dark", [(1280, False), (1280, True), (390, False), (390, True)])
def test_phase1_every_screen(ready, browser, width, dark):
    sb = ready
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme="dark" if dark else "light")
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    tag = f"{width}-{'dark' if dark else 'light'}"
    page.goto(sb.url)
    page.wait_for_selector("text=Client A")

    def nav(label):
        if width < 768:
            page.click("button[aria-label='Menu']")
            page.click(f"#mobile-menu button:has-text('{label}')")
        else:
            page.click(f"nav button:has-text('{label}')")

    check(page, f"accounts-{tag}")
    # the one focus style: an outline, no ring
    page.focus("input[aria-label='Search accounts']")
    st = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, s.boxShadow]; })()")
    assert st == ["solid", "none"], st

    for label, dialog in (("Add account", "add"),):
        page.click(f"main button:has-text('{label}')")
        page.wait_for_selector("[role=dialog]")
        check(page, f"dialog-{dialog}-{tag}")
        page.keyboard.press("Escape")
    page.click("button[aria-label='More for Client B']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.wait_for_selector("[role=dialog]")
    check(page, f"dialog-edit-{tag}")
    page.keyboard.press("Escape")

    # the agent drawer, every tab, from the live session
    page.click("button:text-is('Agent controls')")
    page.wait_for_selector("#pg h3")
    for t in ("People", "Run", "Comments", "Messages", "Product", "Settings", "Team memory"):
        if width < 640:  # phones get a select instead of the tab row
            page.select_option("#pg select[aria-label='Section']", label=t)
        else:
            page.click(f"#pg [role=tab]:text-is('{t}')")
        check(page, f"drawer-{t.lower().replace(' ', '-')}-{tag}")
        assert page.query_selector_all("#pg input[type=checkbox]") == [], t
    page.keyboard.press("Escape")

    for label in ("Overview",):  # activity is the Overview's history panel now
        nav(label)
        page.wait_for_selector(f"main h1:text-is('{label}')")
        page.wait_for_selector("[data-skeleton]", state="detached")
        check(page, f"{label.lower()}-{tag}")
    for sec in ("Personal", "Appearance", "Members", "Templates", "Cloud runners", "Automation API", "Teams"):
        open_settings(page, sec)  # the team lives in Settings now: every section is checked like any other screen
        page.wait_for_timeout(300)
        check(page, f"settings-{sec.lower().replace(' ', '-')}-{tag}")
    close_settings(page)

    assert errors == [], errors
    ctx.close()
