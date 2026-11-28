"""Phase 1 (design system) tickets: tokens, components, copy, applied everywhere.

Source checks scan web/components and web/app; UI checks drive the built dashboard against tests/ux_harness.FakeApi.
Run: `uv run pytest tests/test_ux_p1.py` (UI checks need `npm run build` in web/ first)."""
import re
import time
from pathlib import Path

import pytest

from tests.ux_harness import FakeApi, browser, lead, needs_ui, open_drawer, open_ui, profile, state, status  # noqa: F401

SRC = Path(__file__).parent.parent / "web"
FILES = sorted([*SRC.glob("components/**/*.tsx"), *SRC.glob("app/*.tsx"), *SRC.glob("lib/*.ts*")])
GLYPHS = "▶✨↗⋯"


def src_hits(pattern: str, *, skip=()) -> list[str]:
    out = []
    for f in FILES:
        if f.name in skip:
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if line.strip().startswith(("//", "/*", "*")):  # comments may name what not to do
                continue
            if re.search(pattern, line):
                out.append(f"{f.relative_to(SRC)}:{i}: {line.strip()[:400]}")
    return out


# ------------------------------------------------------------------ P1-1 tokens
def test_p1_1_type_scale_has_no_arbitrary_sizes():
    assert src_hits(r"text-\[\d+(\.\d+)?px\]") == []


def test_p1_1_radii_controls_md_surfaces_lg_overlays_xl():
    assert src_hits(r"rounded-2xl|rounded-3xl") == []
    assert src_hits(r"(?<![\w-])rounded(?![\w-])") == []  # plain `rounded`: pick md/lg/xl/full


def test_p1_1_one_focus_mechanism():
    assert src_hits(r"focus(-visible)?:ring") == [], "focus rings duplicate the global :focus-visible outline"
    css = (SRC / "app" / "globals.css").read_text()
    assert ":focus-visible" in css and "--overlay" in css and "--on-ok" in css


def test_p1_1_no_hand_rolled_control_heights_or_dim_placeholders():
    assert src_hits(r"placeholder:text-mute/") == []
    # anchors that look like buttons use buttonStyles; only the ui kit sets control heights
    hits = src_hits(r'className="[^"]*\bh-(8|9|10|11)\b[^"]*"', skip=("button.tsx", "field.tsx", "checkbox.tsx"))
    assert [h for h in hits if "<Skeleton" not in h] == []


# ------------------------------------------------------------------ P1-3 copy (source level)
def test_p1_3_no_emoji_or_glyph_icons_in_the_ui():
    assert src_hits(f"[{GLYPHS}]") == []


def test_p1_3_no_native_confirm_or_bare_checkboxes():
    assert src_hits(r"\bconfirm\(\"|window\.confirm|[^.]\bconfirm\(`") == []
    assert src_hits(r'type="checkbox"', skip=("checkbox.tsx",)) == []


# ------------------------------------------------------------------ UI
def heights(page, sel):
    return page.eval_on_selector_all(sel, "els => els.filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))")


@needs_ui
def test_p1_1_controls_share_one_height_per_density(browser):
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=status())}))
    page = open_ui(browser, api)
    page.wait_for_selector("text=Reddit 1")
    md = set(heights(page, "main button[data-size=md], main input[data-size=md], main select[data-size=md]"))
    assert md == {36}, md
    open_drawer(page)
    sm = set(heights(page, "#pg button[data-size=sm], #pg input[data-size=sm], #pg select[data-size=sm]"))
    assert sm == {32}, sm


@needs_ui
def test_p1_1_focus_is_an_outline_not_a_ring(browser):
    page = open_ui(browser, FakeApi())
    page.wait_for_selector("text=Reddit 1")
    page.focus("input[aria-label='Search accounts']")
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    st = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, s.outlineWidth, s.boxShadow]; })()")
    assert st[0] == "solid" and st[1] == "2px" and st[2] == "none", st


@needs_ui
def test_p1_1_placeholder_and_done_step_contrast(browser):
    page = open_ui(browser, FakeApi(state(profiles=[profile(version=1)])))
    page.wait_for_selector("input[aria-label='Search accounts']")
    ph, mute = page.evaluate("""(() => { const i = document.querySelector("input[aria-label='Search accounts']");
      const d = document.createElement('span'); d.className = 'text-mute'; document.body.append(d);
      return [getComputedStyle(i, '::placeholder').color, getComputedStyle(d).color]; })()""")
    assert ph == mute, (ph, mute)


@needs_ui
def test_p1_2_empty_states_skeletons_and_tables(browser):
    api = FakeApi()
    api.hold = {r"GET /overview"}
    page = open_ui(browser, api, hash="overview")
    page.wait_for_selector("[data-skeleton]")  # loading looks like loading, not like "nothing here"
    api.routes[r"GET /overview"] = dict(profiles=2, members=1, members_without_key=[], running=[], bytes=0, versions=0, open_invites=0,
                                        key_epoch=1, rotating=False, last_rotation=None, recent=[])
    api.release()
    page.wait_for_selector("[data-skeleton]", state="detached")  # the numbers arrive and replace the placeholders
    # People search with no match: an EmptyState
    page2 = open_ui(browser, FakeApi())
    page2.fill("input[aria-label='Search accounts']", "zzz")
    page2.wait_for_selector("[data-empty]:has-text('No accounts match')")


@needs_ui
def test_p1_2_checkbox_switch_icon_buttons_and_numeric_columns(browser):
    api = FakeApi(state(profiles=[profile(running_here=True)], jobs={"run-1": dict(alive=True, log=[], pigeon=status())}))
    api.ops["subs_edit"] = dict(subreddits=[dict(name="saas", enabled=True, score=70, rules_risk="low", reason="good")])
    api.routes[r"GET /pigeon/brain"] = dict(summary=dict(days=14, by_kind=dict(dm=3), stuck_claims=0,
                                                         by_subreddit=[dict(subreddit="saas", dms=3, replies=1, comments=2, posts=0, upvotes=4, last_at=time.time() - 3600)],
                                                         by_profile=[]), accounts=[], team_setup=dict(version=1, product="Acme", subreddits=1, limits={}),
                                            events=[dict(at=time.time() - 120, kind="dm", status="done", profile="Reddit 1", subreddit="saas", target="alice")])
    page = open_ui(browser, api)
    open_drawer(page)
    for tab in ("Run", "Product", "Settings"):
        page.click(f"#pg [role=tab]:text-is('{tab}')")
        page.wait_for_timeout(400)
        assert page.query_selector_all("#pg input[type=checkbox]") == [], tab
    page.click("#pg [role=tab]:text-is('Product')")
    page.wait_for_selector("#pg [role=switch][aria-label='Use r/saas']")
    # every icon-only button has a name
    nameless = page.evaluate("""[...document.querySelectorAll('button')].filter(b => b.offsetParent && !b.innerText.trim() && !b.getAttribute('aria-label') && !b.getAttribute('aria-labelledby')).length""")
    assert nameless == 0
    page.click("#pg [role=tab]:text-is('Team memory')")
    page.wait_for_selector("#pg table")
    align = page.evaluate("""(() => { const t = document.querySelector('#pg table'); const i = [...t.querySelectorAll('th')].findIndex(h => h.innerText === 'DMs');
      return [getComputedStyle(t.querySelectorAll('th')[i]).textAlign, getComputedStyle(t.querySelector('tbody tr').children[i]).textAlign]; })()""")
    assert align == ["right", "right"], align
    when = page.query_selector("#pg time")
    assert re.fullmatch(r"\d+ (minutes?|hours?) ago|just now", when.inner_text()), when.inner_text()
    assert re.search(r"\d{4}", when.get_attribute("title"))  # the absolute date is on hover


@needs_ui
def test_p1_3_glossary_os_names_share_levels_and_mapped_errors(browser):
    api = FakeApi(state(members=[dict(id=1, name="Owner", email="owner@x.io", role="owner"), dict(id=2, name="M", email="m@x.io", role="member")]))
    api.routes[r"GET /profiles/1/shares"] = []
    api.routes[r"POST /profiles"] = (400, {"detail": "No DeepSeek key: `pigeon agent config --deepseek-key KEY` or set DEEPSEEK_API_KEY."})
    page = open_ui(browser, api)
    page.click("button:has-text('Add account')")
    page.click("text=Advanced")
    opts = page.eval_on_selector_all("[role=dialog] select:not(#px_type) >> nth=0 >> option", "os => os.map(o => o.innerText)")
    assert any(o.startswith("macOS") for o in opts) and any(o.startswith("Windows") for o in opts), opts
    page.fill("#n_name", "X")
    page.click("#n_go")
    toast = page.wait_for_selector("[data-sonner-toast]")
    assert "pigeon agent" not in toast.inner_text() and "AI key" in toast.inner_text()
    page.keyboard.press("Escape")
    page.click("button[aria-label='More for Reddit 1']")
    page.click("[role=menuitem]:text-is('Share')")
    opts = page.eval_on_selector_all("[role=dialog] option", "os => os.map(o => o.innerText)")
    assert {"Can open", "Can edit", "No access"} <= set(opts), opts
    page.keyboard.press("Escape")
    body = page.inner_text("body")
    assert not set(GLYPHS) & set(body)
    assert "profile" not in body.lower()


@needs_ui
@pytest.mark.parametrize("width,dark", [(1280, False), (1280, True), (390, False), (390, True)])
def test_p1_4_every_screen_renders_cleanly(browser, width, dark):
    api = FakeApi(state(profiles=[profile(running_here=True, tags=["reddit"]), profile(2, "Reddit 2", proxy=None)],
                        jobs={"run-1": dict(alive=True, log=[], pigeon=status())}))
    api.routes[r"GET /overview"] = dict(profiles=2, members=1, members_without_key=[], running=[], bytes=0, versions=0, open_invites=0,
                                        key_epoch=1, rotating=False, last_rotation=None, recent=[])
    api.routes[r"GET /audit"] = []
    for tab in ("accounts", "settings/profile", "settings/members", "settings/templates", "settings/runners", "activity", "overview"):
        page = open_ui(browser, api, width=width, dark=dark, hash=tab)
        page.wait_for_timeout(600)
        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 0, (tab, over)
        assert page.errors == [], page.errors
        page.context.close()
