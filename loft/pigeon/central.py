"""The central brain: the team's shared memory across every browser session, account and teammate.

It lives on the Loft server (see `pigeon_events` there). Before an account DMs someone or comments in a thread it
*claims* that target atomically; if another session already has, the claim is refused and says who. The same
record feeds the writing prompts, so the agent knows what the team has already said and where."""
from __future__ import annotations

import json
import time

from ..client import Client, LoftError


class Unreachable(Exception):
    """The server could not be asked. Sending is refused rather than risking a duplicate DM or comment."""


class Claim:
    """The answer to "may I act on this?": ok, or refused because the team already has it (`existing`), because it is one
    of our own accounts (`own`), or because a team-wide limit is reached (`limit`)."""

    def __init__(self, ok: bool, id: int | None = None, existing: dict | None = None, own: dict | None = None,
                 limit: dict | None = None):
        self.ok, self.id, self.existing, self.own, self.limit = ok, id, existing, own, limit

    def who(self) -> str:
        e = self.existing or {}
        when = time.strftime("%b %d", time.localtime(e["at"])) if e.get("at") else "earlier"
        return f"{e.get('profile') or 'another account'} ({e.get('status', 'claimed')}, {when})"

    def why(self) -> str:
        if self.own:
            return f"u/{self.own.get('username')} is one of our own accounts ({self.own.get('profile') or 'a team profile'})"
        if self.limit:
            l = self.limit
            where = f"r/{l['subreddit']}" if l.get("scope") == "subreddit" else "the team"
            return f"team limit reached for {where}: {l['used']}/{l['cap']} {l['kind']}s in the last 24h"
        return f"already handled by {self.who()}"


class CentralBrain:
    def __init__(self, client: Client, team_id: int, profile_id: int | None):
        self.c, self.tid, self.pid = client, team_id, profile_id
        self._ctx: dict[str, tuple[float, str]] = {}
        self._own: tuple[float, set[str]] = (0.0, set())
        self.setup_unreadable = False

    def _call(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except LoftError as e:
            if e.status in (409, 429):  # answers, not outages: "someone has it" / "a team limit is reached"
                raise
            raise Unreachable(f"central brain: {e.detail}")
        except Exception as e:  # network down, server restarting...
            raise Unreachable(f"central brain unreachable: {e}")

    # ---- writes (fail closed: no claim, no action) ---------------------------------------------
    def claim(self, kind: str, target: str, **fields) -> Claim:
        try:
            if fields.get("body"):  # message text is sealed on this machine when the team is encrypted
                fields["body"] = self._call(self.c.seal_text, self.tid, fields["body"])
            out = self._call(self.c.pigeon_claim, self.tid, kind=kind, target=target, profile_id=self.pid, **fields)
            return Claim(True, id=out["id"])
        except LoftError as e:  # 409: someone has it / it's ours; 429: a team limit
            try:
                d = json.loads(e.detail)
            except (ValueError, TypeError):
                d = {}
            if e.status == 429:
                return Claim(False, limit=d.get("limit") or {})
            return Claim(False, existing=d.get("existing"), own=d.get("own_account"))

    def finish(self, claim: Claim, status: str, error: str = "") -> None:
        if claim.id is not None:
            try:
                self._call(self.c.pigeon_finish, self.tid, claim.id, status, error[:500])
            except Unreachable:
                pass  # the claim stays 'claimed' (safe side); an admin can release it from `loft pigeon brain`

    # ---- reads (fail open: worst case we do a little redundant work) ---------------------------
    def seen(self, kind: str, targets: list[str]) -> dict[str, dict]:
        if not targets:
            return {}
        try:
            return self.c.pigeon_seen(self.tid, kind, targets[:500])
        except Exception:
            return {}

    def seen_other(self, kind: str, targets: list[str]) -> dict[str, dict]:
        """Like seen(), but only what *other* accounts did (our own earlier work on a target isn't a reason to skip it)."""
        return {t: e for t, e in self.seen(kind, targets).items() if e.get("profile_id") != self.pid}

    def own_usernames(self) -> set[str]:
        """Reddit usernames of every account the team runs, so none of them ever targets another. Cached a minute."""
        at, names = self._own
        if time.time() - at < 60:
            return names
        try:
            names = {a["username"].lower() for a in self.c.pigeon_accounts(self.tid)}
        except Exception:
            pass  # keep what we had; the server still refuses our own accounts at claim time
        self._own = (time.time(), names)
        return names

    def register_account(self, username: str) -> str | None:
        """Tell the team which Reddit user this profile is. Returns an error message, or None on success."""
        if not username or self.pid is None:
            return None
        try:
            self.c.pigeon_set_account(self.tid, self.pid, username)
            self._own = (0.0, self._own[1])
            return None
        except LoftError as e:
            return e.detail
        except Exception as e:
            return f"central brain unreachable: {e}"

    def team_config(self) -> tuple[int, dict]:
        """(version, shared setup + "limits"). (0, {}) if nothing is published or the server can't be asked. The setup is sealed with
        the team key; the limits are plain numbers because the server enforces them."""
        try:
            r = self.c.pigeon_config(self.tid)
        except Exception:
            return 0, {}
        self.setup_unreadable = r["unreadable"]
        data = dict(r["setup"] or {})
        if r["limits"]:
            data["limits"] = r["limits"]
        return r["version"], data

    def put_setup(self, setup: dict, base_version: int) -> int:
        """Publish the shared setup (sealed). The team's limits are left as they are."""
        return self._call(self.c.pigeon_put_config, self.tid, base_version, setup=setup)["version"]

    def put_limits(self, limits: dict, base_version: int) -> int:
        """Set the team-wide limits (plain). The shared setup is left as it is."""
        return self._call(self.c.pigeon_put_config, self.tid, base_version, limits=limits)["version"]

    def _opened(self, evs: list[dict]) -> list[dict]:
        for e in evs:
            if e.get("body"):
                e["body"] = self.c.open_text(self.tid, e["body"])
        return evs

    def events(self, **params) -> list[dict]:
        return self._opened(self.c.pigeon_events(self.tid, **params))

    def summary(self, days: float = 14) -> dict:
        return self.c.pigeon_summary(self.tid, days)

    def context(self, subreddit: str, days: float = 14) -> str:
        """What the whole team has done in a subreddit, and what has worked, as prompt text. Cached for a minute."""
        key = subreddit.lower()
        hit = self._ctx.get(key)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        try:
            evs = [e for e in self._opened(self.c.pigeon_events(self.tid, subreddit=subreddit, days=days, limit=200))
                   if e["status"] in ("claimed", "done", "blocked")]
        except Exception:
            return ""
        by = lambda k: [e for e in evs if e["kind"] == k]
        dms, coms, posts, replies = by("dm"), by("comment"), by("post"), by("reply")
        text = ""
        if evs and (dms or coms or posts):
            text = (f"TEAM ACTIVITY in r/{subreddit} over the last {days:g} days (all our accounts together): "
                    f"{len(dms)} DMs sent, {len(coms)} comments, {len(posts)} posts, {len(replies)} replies received"
                    + (f" ({len(replies) * 100 // len(dms)}% reply rate)" if len(dms) >= 5 else "") + ".")
            per: dict[str, list[int]] = {}
            for e in dms + coms + posts:
                row = per.setdefault(e.get("profile") or "an account", [0, 0, 0])
                row[("dm", "comment", "post").index(e["kind"])] += 1
            if len(per) > 1:
                text += "\nBy account: " + "; ".join(f"{n}: {d} DMs, {c} comments, {p} posts" for n, (d, c, p) in sorted(per.items())) + "."
            if coms:
                text += "\nOur recent comments there (do not repeat these points or this wording):\n" + "\n".join(
                    f"- {e.get('profile') or 'an account'} on \"{(e['title'] or e['target'])[:70]}\": {e['body'][:140]}" for e in coms[:4])
            if posts:
                text += "\nOur recent posts there (pick a different angle):\n" + "\n".join(f"- {e['title'][:90]}" for e in posts[:4])
            if len(dms) >= 15:
                text += "\nWe have messaged many people here already; be extra selective."
        worked = [e["body"] for e in replies if e.get("body")][:2]
        if worked:  # learn from what got an answer: the DMs that earned a reply here
            text += ("\nDMs that got a reply here (write in this spirit, don't copy):\n" if text else "DMs that got a reply here (write in this spirit, don't copy):\n") \
                + "\n".join(f"- {w[:160]}" for w in worked)
        self._ctx[key] = (time.time(), text)
        return text
