"""Per-profile Pigeon state: one JSON file inside the profile's data dir, so it syncs with the profile.

That means the daily cap, warm-up clock, leads and pauses belong to the Reddit *account* and follow it
between teammates. The DeepSeek key is the exception: it stays in ~/.pigeon-app/pigeon.json and never syncs."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..client import HOME

KEYFILE = HOME / "pigeon.json"
STATE_NAME = "pigeon.json"

DEFAULT_SETTINGS = dict(
    model="deepseek-flash", my_username="", daily_dm_cap=40, warmup=True, warmup_start=0.0,
    active_start=9, active_end=21, min_gap_sec=60, max_gap_sec=180, pace=1.0, min_fit=70,
    session_minutes=30, upvotes=True, max_upvotes_per_session=10, dm_mode="ai", approve_ai_drafts=True,
    ai_guidance="", use_team_setup=True, auto_check_replies=True, posts_per_day=1,
)
DEFAULT_TEMPLATE = ("{Hey|Hi|Hello} {name}, saw your post in r/{subreddit} about {topic}. "
                    "I'm building something that helps with exactly that — {happy to share|would love your take} if useful?")


def load_key() -> tuple[str, str | None]:
    """(deepseek key, model override). The environment wins over the key file."""
    cfg = json.loads(KEYFILE.read_text()) if KEYFILE.exists() else {}
    return os.environ.get("DEEPSEEK_API_KEY") or cfg.get("deepseek_key", ""), cfg.get("model")


def save_key(key: str | None = None, model: str | None = None) -> None:
    cfg = json.loads(KEYFILE.read_text()) if KEYFILE.exists() else {}
    if key is not None:
        cfg["deepseek_key"] = key
    if model is not None:
        cfg["model"] = model
    HOME.mkdir(parents=True, exist_ok=True)
    KEYFILE.write_text(json.dumps(cfg, indent=2))
    KEYFILE.chmod(0o600)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class State:
    def __init__(self, path: Path):
        self.path = path
        d = json.loads(path.read_text()) if path.exists() else {}
        self.settings = {**DEFAULT_SETTINGS, **d.get("settings", {})}
        self.product = d.get("product")
        self.subreddits: list[dict] = d.get("subreddits", [])
        self.templates: list[str] = d.get("templates", [DEFAULT_TEMPLATE])
        self.leads: dict[str, dict] = d.get("leads", {})
        self.opportunities: list[dict] = d.get("opportunities", [])
        self.post_ideas: list[dict] = d.get("post_ideas", [])
        self.posted: list[dict] = d.get("posted", [])  # posts the agent submitted
        self.template_undo: str | None = d.get("template_undo")  # the template before the last AI spin
        self.team_version: int = d.get("team_version", 0)  # the team setup version this account last pulled
        self.daily = d.get("daily") or dict(date=_today(), count=0, posts=0, next_dm_at=0.0, paused_until=0.0)
        self.stats = d.get("stats") or dict(scanned=0, leads=0, upvotes=0, dms=0, errors=0, posts=0)
        self.logs: list[dict] = d.get("log", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(
            settings=self.settings, product=self.product, subreddits=self.subreddits, templates=self.templates,
            leads=self.leads, opportunities=self.opportunities, post_ideas=self.post_ideas, posted=self.posted,
            template_undo=self.template_undo, team_version=self.team_version, daily=self.daily, stats=self.stats,
            log=self.logs[-300:]), indent=1))
        tmp.replace(self.path)

    # ---- log ---------------------------------------------------------------------------------
    def log(self, level: str, msg: str) -> None:
        self.logs.append(dict(t=time.time(), level=level, msg=msg))
        print(f"[{time.strftime('%H:%M:%S')}] {level:5} {msg}", flush=True)
        self.save()

    def bump(self, k: str, n: int = 1) -> None:
        self.stats[k] += n
        self.save()

    # ---- daily -------------------------------------------------------------------------------
    def get_daily(self) -> dict:
        if self.daily.get("date") != _today():  # new day: counter resets, pauses and spacing carry over
            self.daily = dict(date=_today(), count=0, posts=0, next_dm_at=self.daily.get("next_dm_at", 0.0),
                              paused_until=self.daily.get("paused_until", 0.0))
            self.save()
        return self.daily

    # ---- leads (keyed by lower-cased username) -------------------------------------------------
    def get_lead(self, username: str) -> dict | None:
        return self.leads.get(username.lower())

    def put_lead(self, lead: dict) -> None:
        self.leads[lead["username"].lower()] = lead
        self.save()

    def patch_lead(self, username: str, **patch) -> None:
        cur = self.get_lead(username)
        if cur is not None:
            cur.update(patch)
            self.save()

    def list_leads(self) -> list[dict]:
        return sorted(self.leads.values(), key=lambda l: (-l["fit"], -l["created_at"]))
