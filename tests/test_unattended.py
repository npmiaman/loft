"""An unattended `loft pigeon run` (Loft launches the browser itself) has the same control channel as an attached session:
watch it, work on its state, and stop it cleanly from another thread. Real server, real launch/sync, Camoufox if installed."""
import threading
import time

import pytest

from loft import control
from loft.client import LoftError
from loft.pigeon import runner
from loft.pigeon.llm import Llm
from loft.pigeon.runner import with_agent, with_state
from tests.conftest import FP
from tests.test_pigeon import HAVE, fake_deepseek, fast, mock_reddit, needs_browser  # noqa: F401


@pytest.fixture
def team(mk):
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    return owner, tid, owner.create_profile(tid, name="acct", fingerprint=FP)["id"]


@pytest.mark.skipif(not HAVE, reason="needs the real Camoufox browser (run_profile launches it)")
def test_unattended_run_can_be_watched_edited_and_stopped(team, tmp_path, fast, monkeypatch):
    owner, tid, pid = team
    monkeypatch.setattr(runner, "make_llm", lambda st: Llm("k", transport=fake_deepseek()))

    def seed(st):
        st.product = dict(url="https://x.test", name="Acme", summary="s", offer="o", icp="founders", personas=["a"], painPhrases=["p"],
                          buyingSignals=["b"], competitors=["c"], subredditIdeas=[], searchQueries=[])
        st.subreddits = [dict(name="startups", subscribers=1, posts_per_day=1, icp_density=1, rules_risk="low", score=50, reason="", enabled=True, last_visited=0)]
        st.settings.update(my_username="me", min_fit=70, warmup=False)
    with_state(owner, pid, seed, home=tmp_path)
    v0 = owner.profile(pid)["version"]

    def go(a):
        a.b.page.context.route("https://www.reddit.com/**", lambda r: mock_reddit(r, None))
        a.run("find", 10)  # would run for ten minutes if nothing stopped it

    worker = threading.Thread(target=lambda: with_agent(owner, pid, go, headless=True, home=tmp_path), daemon=True)
    worker.start()
    call = lambda m, p, b=None: control.request(pid, m, p, b, home=tmp_path, timeout=60)

    for _ in range(300):
        if control.is_live(pid, tmp_path):
            break
        time.sleep(0.2)
    else:
        pytest.fail("the unattended run never opened its control channel")

    st = None
    for _ in range(600):  # wait until it is really browsing
        st = call("GET", "/pigeon/status")
        if any("Browsing" in e["msg"] for e in st["log"]):
            break
        time.sleep(0.3)
    assert st["state"] == "running" and st["job"] == "run" and st["product"] == "Acme"
    with pytest.raises(LoftError, match="already running"):
        call("POST", "/pigeon/job", {"kind": "scan", "args": {"no_score": True}})  # one job at a time

    # state operations are answered on the browser thread, between the agent's steps, while it works
    r = call("POST", "/pigeon/op", {"op": "leads_import", "args": {"text": "hand_added_user"}})
    assert r["added"] == 1
    assert "hand_added_user" in [l["username"] for l in call("POST", "/pigeon/op", {"op": "leads_list", "args": {}})["leads"]]

    t0 = time.time()
    assert call("POST", "/pigeon/stop")["state"] == "stopping"
    worker.join(120)
    assert not worker.is_alive() and time.time() - t0 < 120  # a ten-minute session, stopped promptly
    assert not control.is_live(pid, tmp_path)  # control channel closed with the session
    assert owner.profiles(tid)[0]["running"] is None  # lock released
    assert owner.profile(pid)["version"] == v0 + 1  # and the profile (with the hand-added lead and the log) synced back

    final = with_state(owner, pid, lambda s: ({l["username"] for l in s.leads.values()}, [e["msg"] for e in s.logs]), home=tmp_path)
    assert "hand_added_user" in final[0] and "Stopped" in final[1]
