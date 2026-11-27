"""run-all: accounts are visited strictly one at a time, with pauses between launches, skipping accounts with nothing to do."""
import io
import json
import random
import threading
import time
import zipfile
from datetime import datetime

import pytest

from loft.client import LoftError
from loft.pigeon import rotate as rot
from loft.pigeon.rotate import peek_state, rotate, skip_reason, summary
from loft.pigeon.state import STATE_NAME
from tests.conftest import FP

NOW = time.time()
TODAY = time.strftime("%Y-%m-%d", time.localtime(NOW))
HOUR = datetime.now().hour


def raw(**kw):
    base = dict(settings=dict(warmup=False, active_start=0, active_end=24, daily_dm_cap=10),
                product=dict(name="Acme"), subreddits=[dict(name="saas", enabled=True)],
                leads={"a": dict(status="ready")}, daily=dict(date=TODAY, count=0, paused_until=0, next_dm_at=0))
    base.update(kw)
    return base


class Rig:
    """Fake peek/launch/sleep that record what happened and prove sessions never overlap."""
    def __init__(self, states=None, fail=None):
        self.states, self.fail = states or {}, fail or {}
        self.launched, self.sleeps, self.active, self.max_active, self.lines = [], [], 0, 0, []

    def peek(self, pid):
        s = self.states.get(pid, raw())
        if isinstance(s, Exception):
            raise s
        return s

    def launch(self, pid):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.launched.append(pid)
            f = self.fail.get(pid)
            if f:
                raise f
            return dict(dms=1, leads=2, errors=0)
        finally:
            self.active -= 1

    def run(self, pids, **kw):
        kw.setdefault("mode", "full")
        return rotate(pids, peek=self.peek, launch=self.launch, sleep=self.sleeps.append, log=self.lines.append,
                      names={p: f"acct{p}" for p in range(1, 20)}, rng=random.Random(1), **kw)


def statuses(res):
    return [(r["pid"], r["status"]) for r in res]


def test_runs_each_account_in_order_one_at_a_time_with_gaps():
    rig = Rig()
    res = rig.run([3, 1, 2], gap=(30, 120))
    assert rig.launched == [3, 1, 2] and rig.max_active == 1
    assert statuses(res) == [(3, "ran"), (1, "ran"), (2, "ran")]
    assert len(rig.sleeps) == 2 and all(30 <= s <= 120 for s in rig.sleeps)  # between accounts, not after the last
    assert len(set(rig.sleeps)) == 2  # jittered, not a fixed beat


def test_really_sequential_with_threads_watching():
    """A launch that takes real time: if anything overlapped, the counter would see two at once."""
    rig = Rig()
    real = rig.launch

    def slow(pid):
        r = real(pid)
        time.sleep(0.05)
        return r
    rotate([1, 2, 3, 4], mode="full", peek=rig.peek, launch=slow,
           sleep=lambda s: None, log=lambda m: None)
    assert rig.max_active == 1 and rig.launched == [1, 2, 3, 4]


def test_skips_accounts_with_nothing_to_do_without_launching_or_pausing_for_them():
    rig = Rig(states={
        1: None,                                                              # never set up
        2: raw(product=None),                                                 # no product
        3: raw(),                                                             # fine
        4: raw(daily=dict(date=TODAY, count=10, paused_until=0, next_dm_at=0)),  # cap reached (but full mode still browses)
    })
    res = rig.run([1, 2, 3, 4])
    assert statuses(res) == [(1, "skipped"), (2, "skipped"), (3, "ran"), (4, "ran")]
    assert [r["detail"] for r in res[:2]] == ["Pigeon was never set up on this account", "no product set up"]
    assert rig.launched == [3, 4] and len(rig.sleeps) == 1  # only one pause: skipped accounts don't cost one


def test_dm_mode_skips_on_governor_and_empty_queues():
    day = dict(date=TODAY, count=0, next_dm_at=0)
    cases = {
        1: raw(leads={}),                                                     # nothing to send
        2: raw(leads={"a": dict(status="sent")}),                             # only already-sent
        3: raw(daily=dict(day, paused_until=NOW + 3600)),                     # Reddit pushed back
        4: raw(daily=dict(day, count=10, paused_until=0)),                    # cap reached
        5: raw(settings=dict(warmup=False, active_start=(HOUR + 2) % 24, active_end=(HOUR + 3) % 24, daily_dm_cap=10)),  # hours closed now
        6: raw(daily=dict(date="2000-01-01", count=99, paused_until=0, next_dm_at=0)),       # yesterday's count doesn't carry over
    }
    rig = Rig(states=cases)
    res = rig.run(list(cases), mode="dm")
    reasons = {r["pid"]: r["detail"] for r in res}
    assert reasons[1] == reasons[2] == "no leads waiting" and reasons[3].startswith("paused") and reasons[4] == "daily cap reached"
    assert reasons[5].startswith("outside active hours")
    assert statuses(res)[5] == (6, "ran") and rig.launched == [6]


def test_find_mode_ignores_dm_governor_but_needs_a_product_and_subreddits():
    rig = Rig(states={1: raw(daily=dict(date=TODAY, count=99, paused_until=NOW + 9999, next_dm_at=0)),
                      2: raw(subreddits=[dict(name="x", enabled=False)])})
    res = rig.run([1, 2], mode="find")
    assert statuses(res) == [(1, "ran"), (2, "skipped")] and res[1]["detail"] == "no subreddits enabled"


def test_a_failure_does_not_stop_the_rotation_unless_asked():
    rig = Rig(fail={2: RuntimeError("camoufox crashed")})
    res = rig.run([1, 2, 3])
    assert statuses(res) == [(1, "ran"), (2, "failed"), (3, "ran")] and res[1]["detail"] == "camoufox crashed"
    assert len(rig.sleeps) == 2  # a browser did open for #2, so still a pause before #3

    rig2 = Rig(fail={2: RuntimeError("boom")})
    assert statuses(rig2.run([1, 2, 3], stop_on_error=True)) == [(1, "ran"), (2, "failed")] and rig2.launched == [1, 2]


def test_accounts_in_use_or_open_here_are_skipped_not_failed():
    rig = Rig(fail={2: LoftError(423, "in use by sam on laptop")})
    res = rotate([1, 2, 3], mode="full", peek=rig.peek, launch=rig.launch, is_open=lambda p: p == 3, sleep=lambda s: None, log=lambda m: None)
    assert statuses(res) == [(1, "ran"), (2, "skipped"), (3, "skipped")]
    assert "in use by sam" in res[1]["detail"] and "attach" in res[2]["detail"]
    unreadable = Rig(states={1: LoftError(404, "profile not found")})
    assert statuses(unreadable.run([1, 2])) == [(1, "skipped"), (2, "ran")]


def test_ctrl_c_ends_the_whole_rotation_after_the_current_account_is_saved():
    rig = Rig(fail={2: KeyboardInterrupt()})
    res = rig.run([1, 2, 3, 4])
    assert statuses(res) == [(1, "ran"), (2, "interrupted")] and rig.launched == [1, 2]  # 3 and 4 never start

    # and Ctrl-C during a pause between accounts also ends it cleanly
    rig2 = Rig()
    calls = []

    def sleepy(sec):
        calls.append(sec)
        raise KeyboardInterrupt()
    res = rotate([1, 2, 3], mode="full", peek=rig2.peek, launch=rig2.launch, sleep=sleepy, log=lambda m: None)
    assert statuses(res) == [(1, "ran")] and rig2.launched == [1]


def test_time_budget_stops_new_launches():
    t = [0.0]
    rig = Rig()
    rotate([1, 2, 3, 4], mode="full", peek=rig.peek, launch=lambda p: (t.__setitem__(0, t[0] + 600), rig.launch(p))[1],
           sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], max_total=1500, gap=(10, 10), log=lambda m: None)
    assert rig.launched == [1, 2, 3]  # the 4th would start after 1500s of budget (3 x 600s + gaps)


def test_dry_run_launches_nothing_and_rounds_repeat_with_a_gap():
    rig = Rig(states={2: None})
    res = rig.run([1, 2, 3], dry_run=True)
    assert statuses(res) == [(1, "would-run"), (2, "skipped"), (3, "would-run")] and rig.launched == [] and not rig.sleeps

    rig = Rig()
    res = rig.run([1, 2], rounds=3, round_gap=1800, gap=(5, 5))
    assert [r["round"] for r in res] == [1, 1, 2, 2, 3, 3] and rig.launched == [1, 2, 1, 2, 1, 2]
    assert rig.sleeps.count(1800) == 2 and rig.sleeps.count(5) == 3  # round gaps only between rounds


def test_shuffle_is_reproducible_and_changes_the_order():
    orders = set()
    for seed in range(6):
        rig = Rig()
        rotate([1, 2, 3, 4], mode="full", peek=rig.peek, launch=rig.launch, sleep=lambda s: None, log=lambda m: None, shuffle=True, rng=random.Random(seed))
        orders.add(tuple(rig.launched))
    assert len(orders) > 1 and all(sorted(o) == [1, 2, 3, 4] for o in orders)


def test_summary_totals():
    rig = Rig(fail={2: RuntimeError("x")}, states={3: None})
    out = summary(rig.run([1, 2, 3, 4]))
    assert "2 ran" in out and "1 failed" in out and "1 skipped" in out and "dms 2" in out and "leads 4" in out


def test_skip_reason_active_hours_and_cap_ramp():
    night = raw(settings=dict(warmup=False, active_start=9, active_end=17, daily_dm_cap=10))
    assert skip_reason("dm", night, now=NOW, at=datetime(2026, 1, 1, 12)) is None
    assert "outside active hours" in skip_reason("dm", night, now=NOW, at=datetime(2026, 1, 1, 20))
    ramp = raw(settings=dict(warmup=True, warmup_start=NOW, daily_dm_cap=40, active_start=0, active_end=24),
               daily=dict(date=TODAY, count=5, paused_until=0, next_dm_at=0))
    assert skip_reason("dm", ramp, now=NOW) == "daily cap reached"  # day 0 of warm-up allows 5 a day


# ------------------------------------------------------------------ peeking at real server data without locking
def test_peek_reads_state_without_lock_or_upload(mk, tmp_path):
    from loft.pigeon.runner import with_state
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    fresh = owner.create_profile(tid, name="fresh", fingerprint=FP)["id"]
    seeded = owner.create_profile(tid, name="seeded", fingerprint=FP)["id"]

    assert peek_state(owner, fresh, home=tmp_path / "a") is None  # never run

    def seed(st):
        st.product = dict(name="Acme")
        st.subreddits = [dict(name="saas", enabled=True)]
    with_state(owner, seeded, seed, home=tmp_path / "a")
    v = owner.profile(seeded)["version"]

    # another machine (empty local dir) downloads the blob and reads just pigeon.json
    got = peek_state(owner, seeded, home=tmp_path / "b")
    assert got["product"]["name"] == "Acme" and skip_reason("find", got) is None
    assert owner.profile(seeded)["version"] == v and owner.profiles(tid)[1]["running"] is None  # no upload, no lock

    # the machine that already has the current version reads its local copy (no download at all)
    class NoDownload:
        def __init__(self, c):
            self.c = c

        def profile(self, pid):
            return self.c.profile(pid)

        def download(self, *a, **k):
            raise AssertionError("should have used the local copy")
    assert peek_state(NoDownload(owner), seeded, home=tmp_path / "a")["product"]["name"] == "Acme"

    # profile data without Pigeon state in it (cookies only) counts as never set up
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as z:
        z.writestr("cookies.sqlite", "x")
    lk = owner.lock(fresh)
    owner.upload(fresh, lk["lock"], lk["version"], blob.getvalue())
    owner.unlock(fresh, lk["lock"])
    assert peek_state(owner, fresh, home=tmp_path / "c") is None


# ------------------------------------------------------------------ the CLI command
def _parse(argv):
    import argparse
    from loft.pigeon.cli import register
    ap = argparse.ArgumentParser()
    register(ap.add_subparsers(dest="cmd", required=True))
    return ap.parse_args(["pigeon", *argv])


def test_run_all_selects_accounts_and_validates(mk, monkeypatch, capsys):
    from loft.pigeon.cli import handle
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    ids = {n: owner.create_profile(tid, name=n, fingerprint=FP, tags=t)["id"] for n, t in
           (("alpha", ["us"]), ("beta", ["eu"]), ("gamma", ["us", "vip"]))}
    monkeypatch.setattr(rot, "peek_state", lambda c, pid, home=None: raw())
    go = lambda *argv: (handle(_parse(["run-all", *argv]), owner, lambda: tid), capsys.readouterr().out)[1]

    out = go("--all", "--dry-run")
    assert "3 account(s), one at a time" in out and out.count("would-run  full") == 3 and "alpha" in out and "gamma" in out
    out = go("--tag", "us", "--dry-run")
    assert out.count("would-run  full") == 2 and "beta" not in out
    out = go(str(ids["gamma"]), str(ids["alpha"]), str(ids["gamma"]), "--dry-run")  # explicit order is kept, repeats collapse
    assert out.rindex("gamma") < out.rindex("alpha") and out.count("would-run  full") == 2
    assert "1 account(s)" in go("--tag", "vip", "--dry-run")

    for argv, msg in ((["--dry-run"], "say which accounts"), (["9999", "--dry-run"], "no such profile"),
                      (["--tag", "nope"], "no matching profiles"), (["--all", "--gap-min", "90", "--gap-max", "10"], "larger than")):
        with pytest.raises(SystemExit, match=msg):
            go(*argv)


# ------------------------------------------------------------------ one exit IP per account (proxynet-style {pid}/{session} usernames)
def prof(pid, name, proxy="__direct__"):
    return dict(id=pid, name=name, proxy=None if proxy == "__direct__" else proxy)


def test_proxy_warnings_catch_accounts_that_would_share_an_exit():
    from loft.pigeon.rotate import proxy_warnings as w
    gw = "http://gateway.example:8899"
    # each profile gets its own sticky session when the username carries {pid} or {session}: nothing to warn about
    assert w([prof(1, "a", dict(server=gw, username="country-us-session-{pid}", password="p")),
              prof(2, "b", dict(server=gw, username="country-us-session-{pid}", password="p")),
              prof(3, "c", dict(server=gw, username="session-{session}", password="p"))]) == []
    # the same literal username = the same session = the same exit IP
    msg = w([prof(1, "a", dict(server=gw, username="session-shared", password="p")), prof(2, "b", dict(server=gw, username="session-shared", password="p"))])
    assert len(msg) == 1 and "a (#1), b (#2)" in msg[0] and "share one proxy session" in msg[0] and "{pid} or {session}" in msg[0]
    # a placeholder in the server part expands too
    assert w([prof(1, "a", dict(server="http://node-{pid}.example:80")), prof(2, "b", dict(server="http://node-{pid}.example:80"))]) == []
    # no proxy at all: they'd all use this machine's IP (one account alone isn't a collision)
    assert "no proxy" in w([prof(1, "a"), prof(2, "b")])[0] and w([prof(1, "a")]) == []
    # a mix: only the colliding group is reported
    out = w([prof(1, "a", dict(server=gw, username="u")), prof(2, "b", dict(server=gw, username="u")), prof(3, "c", dict(server=gw, username="other"))])
    assert len(out) == 1 and "c (#3)" not in out[0]
    # a proxy this device can't read (no team key) can't be judged, so it isn't flagged
    assert w([prof(1, "a", {"server": "(locked: no team key)"}), prof(2, "b", {"server": "(locked: no team key)"})]) == []
    # judging must not change what the browser would use
    px = dict(server=gw, username="s-{pid}")
    w([prof(1, "a", px)])
    assert px["username"] == "s-{pid}"


def test_run_all_warns_about_shared_exits_using_real_encrypted_profiles(mk, monkeypatch, capsys):
    from loft.pigeon.cli import handle
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    gw = "http://gateway.example:8899"
    for n in (1, 2, 3):  # one proxynet template, three accounts: each expands to its own session
        owner.create_profile(tid, name=f"good{n}", fingerprint=FP, proxy={"server": gw, "username": "country-us-session-{pid}", "password": "GATEWAY_PASS"}, tags=["good"])
    for n in (1, 2):  # two accounts pasted the same literal session
        owner.create_profile(tid, name=f"clash{n}", fingerprint=FP, proxy={"server": gw, "username": "session-abc", "password": "p"}, tags=["clash"])
    for n in (1, 2):
        owner.create_profile(tid, name=f"bare{n}", fingerprint=FP, tags=["bare"])
    monkeypatch.setattr(rot, "peek_state", lambda c, pid, home=None: raw())
    go = lambda *argv: (handle(_parse(["run-all", *argv]), owner, lambda: tid), capsys.readouterr().out)[1]

    out = go("--tag", "good", "--dry-run")
    assert "⚠" not in out and out.count("would-run") == 4  # three lines + the summary: nothing to warn about
    out = go("--tag", "clash", "--dry-run")
    assert "⚠" in out and "clash1" in out and "share one proxy session" in out
    out = go("--tag", "bare", "--dry-run")
    assert "⚠" in out and "no proxy" in out
    with pytest.raises(SystemExit, match="strict-proxies"):
        go("--tag", "clash", "--strict-proxies", "--dry-run")  # refuses to start rather than risk linked accounts
    capsys.readouterr()  # that call raised before its output was read: don't let it leak into the next
    out = go("--all", "--dry-run")
    assert out.count("⚠") == 2  # clash and bare groups; the good ones are not flagged


# ------------------------------------------------------------------ `loft pigeon accounts`: each account bound to ONE unique proxy, team-wide
WEBSHARE = ["siawaiso-1:hunter2pw@p.webshare.io:80", "siawaiso-2:hunter2pw@p.webshare.io:80", "siawaiso-3:hunter2pw@p.webshare.io:80"]  # Webshare's scheme-less format


def _pool(monkeypatch, tmp_path, lines, exits=None, ok=True):
    """A proxies file; check_proxy is stubbed with the exit IP each proxy username 'has' (or a distinct default)."""
    import loft.engine as engine
    f = tmp_path / "proxies.txt"
    f.write_text("# my pool\n" + "\n".join(lines) + "\n\n")
    exits = exits or {}
    monkeypatch.setattr(engine, "check_proxy", lambda px, **kw: dict(ok=ok, ip=exits.get(px.get("username"), f"198.51.100.{abs(hash(px.get('username'))) % 200}"),
                                                                   ipv6=False, ms=12, stable=True, error="connection refused"))
    return f


def _accounts(mk, monkeypatch, tmp_path, lines, exits=None, ok=True):
    """An owner, an admin teammate, and a proxies file."""
    f = _pool(monkeypatch, tmp_path, lines, exits, ok)
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    admin = mk()
    admin.register("a@x.io", "password1", invite=owner.invite(tid, "admin")["code"])
    return owner, admin, tid, f


def _acct(owner, tid, capsys, *argv):
    from loft.pigeon.cli import handle
    handle(_parse(["accounts", *argv]), owner, lambda: tid)
    return capsys.readouterr().out


def test_accounts_add_binds_each_account_to_its_own_proxy_and_shares_it_with_the_team(mk, monkeypatch, tmp_path, capsys):
    owner, admin, tid, f = _accounts(mk, monkeypatch, tmp_path, WEBSHARE)
    out = _acct(owner, tid, capsys, "add", "--proxies", str(f), "--name", "Reddit {n}")
    assert "created 3 accounts" in out and "loft pigeon run-all --tag reddit" in out and "loft pigeon run" in out
    profs = {p["name"]: p for p in owner.profiles(tid)}
    assert set(profs) == {"Reddit 1", "Reddit 2", "Reddit 3"}
    # exactly one proxy each, all different, bound for good, with the password only readable by team members (it is ciphertext on the server)
    assert [profs[f"Reddit {n}"]["proxy"]["username"] for n in (1, 2, 3)] == ["siawaiso-1", "siawaiso-2", "siawaiso-3"]
    assert {p["proxy"]["server"] for p in profs.values()} == {"http://p.webshare.io:80"} and all(p["proxy"]["password"] == "hunter2pw" for p in profs.values())
    assert all(p["tags"] == ["reddit"] and p["start_url"] == "https://www.reddit.com/" for p in profs.values())
    raw = owner.j("GET", f"/profiles/{profs['Reddit 1']['id']}")["proxy"]
    assert "siawaiso" not in raw and "hunter2pw" not in raw  # the server never sees the credentials
    # a teammate with access sees the same bindings, with nothing to set up themselves
    assert [p["proxy"]["username"] for p in sorted(admin.profiles(tid), key=lambda p: p["id"])] == ["siawaiso-1", "siawaiso-2", "siawaiso-3"]
    # `ls` never prints the password; `check` reports three distinct exits
    ls = _acct(owner, tid, capsys, "ls")
    assert "siawaiso-2" in ls and "hunter2pw" not in ls and "Reddit 3" in ls
    chk = _acct(owner, tid, capsys, "check")
    assert "3 account(s), 3 distinct exit IP(s)" in chk and "⚠" not in chk


def test_accounts_add_refuses_duplicates_dead_proxies_and_shared_exits(mk, monkeypatch, tmp_path, capsys):
    owner, _, tid, f = _accounts(mk, monkeypatch, tmp_path, WEBSHARE[:1] + WEBSHARE[:1])
    with pytest.raises(SystemExit, match="line 2 repeats line 1"):
        _acct(owner, tid, capsys, "add", "--proxies", str(f))
    assert owner.profiles(tid) == []  # nothing half-created

    f = _pool(monkeypatch, tmp_path, WEBSHARE, ok=False)
    with pytest.raises(SystemExit, match="3 proxy.*didn't work"):
        _acct(owner, tid, capsys, "add", "--proxies", str(f))
    assert owner.profiles(tid) == []

    f = _pool(monkeypatch, tmp_path, WEBSHARE, exits={"siawaiso-1": "203.0.113.5", "siawaiso-2": "203.0.113.5", "siawaiso-3": "203.0.113.6"})
    with pytest.raises(SystemExit, match="same IP"):
        _acct(owner, tid, capsys, "add", "--proxies", str(f))
    assert owner.profiles(tid) == []
    out = _acct(owner, tid, capsys, "add", "--proxies", str(f), "--allow-duplicate-exits")  # explicit opt-in
    assert "SAME EXIT AS #1" in out
    assert len(owner.profiles(tid)) == 3
    assert "all exit from 203.0.113.5" in _acct(owner, tid, capsys, "check")  # and `check` keeps flagging it afterwards


def test_accounts_add_with_no_check_and_with_pid_placeholders(mk, monkeypatch, tmp_path, capsys):
    import loft.engine as engine
    lines = ["http://country-us-session-{pid}:GATEWAY@gateway.example:8899"]
    owner, _, tid, f = _accounts(mk, monkeypatch, tmp_path, lines)
    monkeypatch.setattr(engine, "check_proxy", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not check placeholder proxies before they have a pid")))
    out = _acct(owner, tid, capsys, "add", "--proxies", str(f))
    assert "checked after creation" in out and len(owner.profiles(tid)) == 1  # a {pid} proxy can only be checked once the profile has an id
    seen = []
    monkeypatch.setattr(engine, "check_proxy", lambda px, **kw: (seen.append(px["username"]), dict(ok=True, ip="203.0.113.9", ms=5))[1])
    pid = owner.profiles(tid)[0]["id"]
    _acct(owner, tid, capsys, "check")
    assert seen == [f"country-us-session-{pid}"]  # `check` expands it exactly as a launch would
