"""`loft pigeon doctor` and failure snapshots: how a Reddit markup change shows up, on mock pages (healthy and deliberately broken)."""
import time

import pytest

from loft.pigeon import ops
from loft.pigeon.agent import Agent
from loft.pigeon.bridge import Bridge
from tests.test_pigeon import browser, fast, lead, make_state, needs_browser, page  # noqa: F401


def agent_on(page, tmp_path, **kw):
    st = make_state(tmp_path / "state", **kw)
    return Agent(Bridge(page, pace=2), st, None, None, diag_dir=tmp_path / "diag")


@needs_browser
def test_doctor_passes_on_healthy_markup_and_reports_the_sample(page, tmp_path):
    ag = agent_on(page, tmp_path)
    ag.st.subreddits[0]["name"] = "saas"
    page.goto("https://www.reddit.com/")
    r = ops.run_browser_op(ag, "doctor")
    assert r["failed"] == 0, r["out"]
    out = r["out"]
    assert all(w in out for w in ("feed of r/saas", "a post", "a profile (u/founder_amy)", "chat list", "submit page of r/saas"))
    assert "✓ chat rooms list  [4 rooms," in out and "✓ posts parse (author, title, permalink)  [4 parsed]" in out
    assert "→" not in out and not (tmp_path / "diag").exists()  # nothing failed, so no snapshots


@needs_browser
def test_doctor_names_exactly_what_broke_and_saves_snapshots(page, tmp_path):
    page.seen["break"] = {"feed", "profile"}  # Reddit renames post-title, and drops the Chat button
    ag = agent_on(page, tmp_path)
    ag.st.subreddits[0]["name"] = "saas"
    page.goto("https://www.reddit.com/")
    r = ops.run_browser_op(ag, "doctor")
    out = r["out"]
    assert r["failed"] >= 2
    assert "✗ posts parse (author, title, permalink)" in out and "attributes (author, post-title, permalink) changed" in out
    assert "✗ chat / message button" in out and "DMs would fail with no-chat-button" in out
    assert "✓ chat rooms list" in out and "✓ title field" in out  # the parts that still work say so
    files = sorted(f.name for f in (tmp_path / "diag").iterdir())
    assert any(f.endswith("doctor-feed.png") for f in files) and any(f.endswith("doctor-profile.html") for f in files)
    html = next((tmp_path / "diag").glob("*doctor-feed.html")).read_text()
    assert html.startswith("<!-- https://www.reddit.com/r/saas/") and "headline=" in html  # the snapshot shows the page as it was
    assert next((tmp_path / "diag").glob("*.png")).stat().st_size > 1000


@needs_browser
def test_a_real_failure_leaves_a_snapshot_behind(page, tmp_path, fast):
    page.seen["break"] = {"profile"}
    ag = agent_on(page, tmp_path)
    ag.st.put_lead(lead("alex_dev"))
    page.goto("https://www.reddit.com/")
    ag.run("dm", minutes=0.4)
    assert ag.st.get_lead("alex_dev")["status"] == "failed"
    assert any("no-" in f.name and f.suffix == ".png" for f in (tmp_path / "diag").iterdir())  # no-chat-button / no-composer
    assert any("Saved a snapshot of the page" in e["msg"] for e in ag.st.logs)


@needs_browser
def test_snapshots_are_pruned(page, tmp_path):
    br = Bridge(page, pace=2)
    page.goto("https://www.reddit.com/")
    d = tmp_path / "diag"
    for i in range(5):
        assert br.snapshot(d, f"label {i}!", keep=3)
        time.sleep(1.1)  # the stamp has one-second resolution
    stems = sorted({f.stem for f in d.iterdir()})
    assert len(stems) == 3 and stems[-1].endswith("label-4")  # newest kept, oldest gone
    assert br.snapshot(tmp_path / "no" / "such" / "deep", "x")  # creates the directory itself


def test_agent_without_a_diag_dir_just_skips_snapshots():
    ag = Agent(None, make_state(__import__("pathlib").Path("/tmp/loft-diag-test")), None, None)
    ag.snap("anything")  # no directory, no bridge: must not raise
