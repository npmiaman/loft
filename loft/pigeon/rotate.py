"""Run the Pigeon agent across many accounts, one at a time.

Each account gets its own full session (lock -> pull -> browser -> agent -> close -> push -> unlock) and only then does
the next one start: never two browsers at once. Before opening anything we peek at each account's saved state and skip the
ones with nothing to do (daily cap reached, paused, no leads, not set up), so a skipped account costs no browser launch.

The orchestration is dependency-injected (`peek`, `launch`, `is_open`, `sleep`) so it is testable without a browser."""
from __future__ import annotations

import io
import json
import random
import time
import zipfile
from datetime import datetime
from typing import Callable

from ..client import Client, LoftError
from ..engine import PROFILES, LocalProfile, expand_proxy_placeholders
from . import governor
from .state import DEFAULT_SETTINGS, STATE_NAME


# ------------------------------------------------------------------ peeking without locking or launching
def peek_state(client: Client, pid: int, home=PROFILES) -> dict | None:
    """The account's saved Pigeon state, read-only: no lock, no browser, no upload. None if it has never run."""
    version = client.profile(pid)["version"]
    if not version:
        return None
    local = LocalProfile(pid, home)
    f = local.data / STATE_NAME
    if local.version == version and f.exists():
        return json.loads(f.read_text())
    _, blob = client.download(pid, version)
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return json.loads(z.read(STATE_NAME))
    except KeyError:
        return None  # profile data exists (cookies) but Pigeon was never used on it


def skip_reason(mode: str, raw: dict | None, now: float | None = None, at: datetime | None = None) -> str | None:
    """Why launching this account would be pointless right now, or None if it's worth a session."""
    if raw is None:
        return "Pigeon was never set up on this account"
    now = now or time.time()
    s = {**DEFAULT_SETTINGS, **raw.get("settings", {})}
    if mode != "dm":
        if not raw.get("product"):
            return "no product set up"
        if not any(x.get("enabled") for x in raw.get("subreddits", [])):
            return "no subreddits enabled"
    if mode == "dm":
        if not any(l["status"] in ("ready", "queued") for l in raw.get("leads", {}).values()):
            return "no leads waiting"
        d = raw.get("daily") or {}
        count = d.get("count", 0) if d.get("date") == time.strftime("%Y-%m-%d", time.localtime(now)) else 0
        if d.get("paused_until", 0) > now:
            return f"paused until {time.strftime('%H:%M', time.localtime(d['paused_until']))}"
        if not governor.in_active_hours(s, at):
            return f"outside active hours ({s['active_start']}:00-{s['active_end']}:00)"
        if count >= governor.effective_cap(s, now):
            return "daily cap reached"
    return None


# ------------------------------------------------------------------ one exit IP per account
def proxy_warnings(profiles: list[dict]) -> list[str]:
    """Reddit links accounts that share an exit IP. Warn when the accounts about to run would: no proxy at all (they'd all use
    this machine's IP), or the same proxy *session* (a gateway like proxynet gives one sticky exit per username, so two accounts
    with the same username share it; `{pid}` or `{session}` in the username makes each profile's own, expanded at launch)."""
    groups: dict[tuple, list[dict]] = {}
    for p in profiles:
        px = p.get("proxy")
        if px and str(px.get("server", "")).startswith("(locked"):
            continue  # can't read it on this device, so can't judge
        if not px:
            key: tuple = ("direct",)
        else:
            exp = expand_proxy_placeholders(dict(px), p["id"])  # a copy: this is what the browser would actually use
            key = (exp.get("server", ""), exp.get("username") or "")
        groups.setdefault(key, []).append(p)
    out = []
    for key, ps in groups.items():
        if len(ps) < 2:
            continue
        who = ", ".join(f"{p['name']} (#{p['id']})" for p in ps)
        if key == ("direct",):
            out.append(f"{who} have no proxy: they would all use this machine's own IP. Give each its own (see `loft bulk --proxies`)")
        else:
            out.append(f"{who} would share one proxy session ({key[0]}, user {key[1]!r}) and so one exit IP. "
                       "Put {pid} or {session} in the proxy username (e.g. a proxynet gateway) so each gets its own sticky exit")
    return out


# ------------------------------------------------------------------ the rotation
def rotate(pids: list[int], *, mode: str, peek: Callable[[int], dict | None], launch: Callable[[int], dict],
           is_open: Callable[[int], bool] = lambda pid: False, names: dict[int, str] | None = None,
           gap: tuple[float, float] = (30, 120), rounds: int = 1, round_gap: float = 1800, max_total: float | None = None,
           shuffle: bool = False, stop_on_error: bool = False, dry_run: bool = False,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
           log: Callable[[str], None] = print, rng: random.Random | None = None) -> list[dict]:
    """Returns one result per account visit: {pid, name, round, status: ran|skipped|failed|interrupted|would-run, detail, delta}."""
    rng = rng or random.Random()
    names = names or {}
    results: list[dict] = []
    start = clock()
    label = lambda pid: f"{names.get(pid, '?')} (#{pid})"

    def add(pid, rnd, status, detail="", delta=None):
        results.append(dict(pid=pid, name=names.get(pid, ""), round=rnd, status=status, detail=detail, delta=delta or {}))
        log(f"  {label(pid):32} {status:10} {detail}")

    try:
        for rnd in range(1, rounds + 1):
            order = list(pids)
            if shuffle:
                rng.shuffle(order)
            if rounds > 1:
                log(f"round {rnd}/{rounds}")
            ran_last = False
            for pid in order:
                if max_total is not None and clock() - start >= max_total:
                    log(f"time budget used up ({max_total / 60:g} min); not starting more accounts")
                    return results
                if is_open(pid):
                    add(pid, rnd, "skipped", "open in a session here: use `loft pigeon attach`")
                    continue
                try:
                    why = skip_reason(mode, peek(pid))
                except LoftError as e:
                    add(pid, rnd, "skipped", f"can't read it: {e.detail}")
                    continue
                if why:
                    add(pid, rnd, "skipped", why)
                    continue
                if dry_run:
                    add(pid, rnd, "would-run", mode)
                    continue
                if ran_last:  # a pause between accounts, so sessions don't run back to back like a script
                    pause = rng.uniform(*gap)
                    log(f"  … {pause:.0f}s before the next account")
                    sleep(pause)
                log(f"▶ {label(pid)}: {mode}")
                try:
                    delta = launch(pid)
                    add(pid, rnd, "ran", ", ".join(f"{k} {v}" for k, v in delta.items() if v) or "nothing new", delta)
                    ran_last = True
                except LoftError as e:  # in use by a teammate, no access, ...
                    add(pid, rnd, "skipped", f"{e.detail}")
                except KeyboardInterrupt:
                    add(pid, rnd, "interrupted", "stopped by you; profile synced")
                    raise
                except Exception as e:
                    add(pid, rnd, "failed", str(e))
                    ran_last = True  # a browser did open; still pause before the next
                    if stop_on_error:
                        log("stopping: --stop-on-error")
                        return results
            if rnd < rounds:
                log(f"round {rnd} done; next round in {round_gap / 60:g} min")
                sleep(round_gap)
    except KeyboardInterrupt:
        log("interrupted: no further accounts will start")
    return results


def summary(results: list[dict]) -> str:
    by: dict[str, int] = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    tot: dict[str, int] = {}
    for r in results:
        for k, v in r["delta"].items():
            tot[k] = tot.get(k, 0) + v
    line = ", ".join(f"{n} {s}" for s, n in sorted(by.items())) or "nothing to do"
    return f"{line}" + (f"   totals: {', '.join(f'{k} {v}' for k, v in tot.items() if v)}" if tot else "")
