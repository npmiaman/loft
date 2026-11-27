"""Every DM goes through here: daily cap with warm-up ramp, active hours, jittered gaps, pauses."""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from .bridge import clamp, lognormal
from .state import State

RAMP = [5, 8, 12, 18, 25, 35, 45, 60]  # DMs/day by account warm-up day


def effective_cap(s: dict, now: float | None = None) -> int:
    if not s["warmup"]:
        return s["daily_dm_cap"]
    now = now or time.time()
    day = max(0, int((now - (s["warmup_start"] or now)) // 86400))
    return min(s["daily_dm_cap"], RAMP[min(day, len(RAMP) - 1)])


def in_active_hours(s: dict, now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    a, b = s["active_start"], s["active_end"]
    return a <= h < b if a < b else (h >= a or h < b)


def check_dm(st: State) -> dict:
    """{'ok': True} or {'ok': False, 'reason', 'hard', 'wait'}. `hard` means: stop trying this session."""
    s, d, now = st.settings, st.get_daily(), time.time()
    if d["paused_until"] > now:
        until = time.strftime("%H:%M", time.localtime(d["paused_until"]))
        return dict(ok=False, hard=True, reason=f"Paused until {until} (Reddit pushed back earlier)")
    if not in_active_hours(s):
        return dict(ok=False, hard=True, reason=f"Outside active hours ({s['active_start']}:00–{s['active_end']}:00)")
    cap = effective_cap(s, now)
    if d["count"] >= cap:
        return dict(ok=False, hard=True, reason=f"Daily cap reached ({d['count']}/{cap})")
    if d["next_dm_at"] > now:
        return dict(ok=False, hard=False, reason="Waiting between DMs", wait=d["next_dm_at"] - now)
    return dict(ok=True)


def next_gap(s: dict) -> float:
    """Seconds before the next DM: right-skewed around the middle of [min, max], plus a ~7% chance of a break."""
    lo = max(5, s["min_gap_sec"])
    hi = max(lo, s["max_gap_sec"])
    sec = clamp(lognormal(math.sqrt(lo * hi), 0.45), lo * 0.7, hi * 2.5)
    if random.random() < 0.07:
        sec += random.uniform(180, 600)
    return sec


def record_dm(st: State) -> None:
    d = st.get_daily()
    d["count"] += 1
    d["next_dm_at"] = time.time() + next_gap(st.settings)
    st.save()


def pause_for(st: State, seconds: float) -> None:
    st.get_daily()["paused_until"] = time.time() + seconds
    st.save()
