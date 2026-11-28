"""Accounts as a dense, CRM-style table: column headers that stay put, short rows, sortable columns. Built for 50-100 accounts.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_accounts_table.py
"""
from tests.ux_harness import FakeApi, browser, needs_ui, open_ui, profile, state  # noqa: F401

pytestmark = [needs_ui]


def many(n=60):
    out = []
    for i in range(1, n + 1):
        out.append(profile(i, f"Account {i:02d}", tags=["eu"] if i % 3 == 0 else [], version=1,
                           proxy={"server": "http://1.1.1.1:80", "name": f"Proxy {i}"} if i % 2 else None,
                           reddit={"signed_in": i % 4 == 0, "user": f"user{i}" if i % 4 == 0 else None},
                           running_here=(i == 7)))
    return out


def names(page):
    return page.eval_on_selector_all("[data-account]", "els => els.map(e => e.dataset.account)")


def test_table_has_columns_short_rows_and_a_sticky_header(browser):
    page = open_ui(browser, FakeApi(state(profiles=many())), width=1440, height=900)
    heads = [h.strip() for h in page.locator("[role=columnheader]").all_inner_texts() if h.strip()]
    assert heads[:3] == ["Account", "Reddit", "Proxy"] and "Status" not in heads and "Tags" not in heads, heads  # removed on request (the last header is a screen-reader "Actions")
    hs = page.eval_on_selector_all("[data-account]", "els => els.map(e => e.getBoundingClientRect().height)")
    assert len(hs) == 60 and max(hs) <= 64, f"rows must stay compact for long lists: tallest {max(hs)}px"
    page.mouse.wheel(0, 4000)
    page.wait_for_timeout(300)
    top = page.eval_on_selector("[role=row][data-head]", "e => e.getBoundingClientRect().top")
    assert 0 <= top <= 80, f"column headers stay visible while scrolling (top={top})"
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


def test_columns_sort(browser):
    page = open_ui(browser, FakeApi(state(profiles=many(12))), width=1440)
    page.click("[role=columnheader] button:has-text('Account')")
    assert names(page)[0] == "Account 01"
    page.click("[role=columnheader] button:has-text('Account')")
    assert names(page)[0] == "Account 12", "second click reverses"
    page.click("[role=columnheader] button:has-text('Reddit')")
    top = page.inner_text(f"[data-account='{names(page)[0]}'] [data-reddit]")
    assert top.startswith("u/"), "signed-in accounts first"
    assert page.get_attribute("[role=columnheader]:has(button:has-text('Reddit'))", "aria-sort") in ("ascending", "descending")


def test_phone_still_fits(browser):
    page = open_ui(browser, FakeApi(state(profiles=many(8))), width=390, height=844)
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    assert page.locator("[data-account] button:has-text('Open browser')").count() >= 1


def test_status_and_tags_sit_with_the_name_and_only_when_they_matter(browser):
    ps = [profile(1, "Saved one", version=2, tags=["eu", "warm"]), profile(2, "Open one", running_here=True),
          profile(3, "Busy one", running={"by": "sam@x.io", "device": "pc"}), profile(4, "Never opened", version=0)]
    api = FakeApi(state(profiles=ps))
    page = open_ui(browser, api, width=1440)
    badge = lambda n: page.locator(f"[data-account='{n}'] [data-status]")
    assert badge("Saved one").count() == 0 and badge("Never opened").count() == 0, "nothing to act on: no badge"
    assert badge("Open one").inner_text().strip() == "Open"
    assert badge("Busy one").inner_text().strip() == "In use by sam@x.io"
    row = page.inner_text("[data-account='Saved one']")
    assert "Closed" not in row and "Not opened" not in page.inner_text("[data-account='Never opened']")
    assert "eu" in row and "warm" in row, "tags show next to the name"
    page.click("[data-account='Saved one'] button[title='Show accounts tagged warm']")
    page.wait_for_timeout(500)
    assert names(page) == ["Saved one"], "tags still filter"
