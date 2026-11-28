"""The agent loop: a port of the extension's lib/agent.ts. It drives one Reddit tab through Bridge."""
from __future__ import annotations

import random
import re
import time

from . import brain, governor, ops
from .bridge import Bridge, Stop, clamp, lognormal
from .central import CentralBrain, Unreachable
from .llm import Llm
from .state import State

REDDIT = "https://www.reddit.com"


_TIME = re.compile(r"\b\d+\s*(s|m|h|d|w|mo|y)\b|\b(yesterday|just now|now|today)\b", re.I)


def room_replied(text: str, unread: bool, lead: dict) -> bool:
    """Did the person answer? Judged from a chat-list row, without relying on an unread badge: the row names them, and its
    preview isn't our own message ("You: ..." or our text), so they spoke last. Read or unread."""
    flat = " ".join(text.split())
    m = re.search(rf"(?<![\w-]){re.escape(lead['username'])}(?![\w-])", flat, re.I)
    if not m:
        return False
    rest = _TIME.sub("", (flat[:m.start()] + " " + flat[m.end():])).strip(" :·-–\n")
    mine = (lead.get("message") or "").lower().strip()[:25]
    if re.search(r"(^|\s)you\s*[:·]", rest, re.I) or (mine and mine in rest.lower()):
        return False  # the last message in that room is ours
    return bool(unread) or len(rest) >= 3


class TeamHold(Exception):
    """A team-wide limit stopped a DM. scope 'team': stop DMs this session; 'subreddit': skip just that subreddit's leads."""

    def __init__(self, scope: str, reason: str):
        super().__init__(reason)
        self.scope, self.reason = scope, reason


class _Outcome(Stop):
    """Stop the session after a definite DM outcome (rate limit, captcha, repeated failures)."""


class Agent:
    def __init__(self, bridge: Bridge, st: State, llm: Llm | None, central: CentralBrain | None = None, diag_dir=None):
        self.b, self.st, self.llm, self.central, self.diag_dir = bridge, st, llm, central, diag_dir
        self.upvotes = 0
        self.opp_drafts = 0
        self.fails = 0
        self.deferred: set[str] = set()  # leads parked this session by a per-subreddit team limit

    @property
    def s(self) -> dict:
        return self.st.settings

    def _need_llm(self) -> Llm:
        if not self.llm:
            raise RuntimeError("No DeepSeek key: `pigeon agent config --deepseek-key KEY` or set DEEPSEEK_API_KEY.")
        return self.llm

    def snap(self, label: str) -> None:
        """When something fails on a real page, keep a screenshot and the HTML so it can be diagnosed afterwards."""
        if self.diag_dir:
            path = self.b.snapshot(self.diag_dir, label)
            if path:
                self.st.log("info", f"Saved a snapshot of the page for debugging: {path}.png / .html")

    @property
    def ctx(self) -> ops.Ctx:
        return ops.Ctx(self.st, self.llm, self.central)

    # ---------------------------------------------------------------- brain glue
    def ingest(self, entries: list[dict]) -> dict[str, dict]:
        """Score new authors and queue the ones that clear min_fit. Returns scores by item id."""
        mine = self.s["my_username"].lower()
        own = {mine} | (self.central.own_usernames() if self.central else set())
        fresh, seen = [], set()
        for e in entries:
            if (not e["author"] or e["author"].lower() in own or len(e["text"].strip()) < 25 or e["id"] in seen
                    or self.st.was_contacted(e["author"])):
                continue
            seen.add(e["id"])
            fresh.append(e)
        out: dict[str, dict] = {}
        if self.central and fresh:  # don't spend model calls on people the team already reached or is already working on
            names = sorted({e["author"] for e in fresh})
            skip = {k.lower() for k in self.central.seen("dm", names)} | {k.lower() for k in self.central.seen_other("lead", names)}
            if skip:
                self.st.log("info", f"Skipping {len(skip)} people the team already contacted or queued")
                fresh = [e for e in fresh if e["author"].lower() not in skip]
        if not fresh:
            return out
        results = brain.score_items(self._need_llm(), self.st.product,
                                    [{k: e[k] for k in ("id", "kind", "author", "subreddit", "title", "text")} for e in fresh])
        self.st.bump("scanned", len(fresh))
        by_id = {e["id"]: e for e in fresh}
        for r in results:
            out[r["id"]] = r
            e = by_id.get(r["id"])
            if not e or not r.get("dm_ok") or r.get("fit", 0) < self.s["min_fit"]:
                continue
            old = self.st.get_lead(e["author"])
            if old and (old["status"] != "queued" or old["fit"] >= r["fit"]):
                continue
            if not old and self.central:  # tell the team this person is ours to work, so no other account queues them too
                try:
                    cl = self.central.claim("lead", e["author"], subreddit=e["subreddit"], title=e["post_title"], url=e["post_url"])
                except Unreachable:
                    cl = None  # a lead claim is a courtesy; the DM claim is the one that must hold
                if cl is not None:
                    if not cl.ok:
                        continue  # someone else queued them first, or they are one of our own accounts
                    self.central.finish(cl, "done")
            self.st.put_lead(dict(username=e["author"], subreddit=e["subreddit"], post_url=e["post_url"],
                                  post_title=e["post_title"], snippet=e["text"][:600], fit=r["fit"], intent=r.get("intent", ""),
                                  reason=r.get("reason", ""), status="queued", source=e.get("source", "browse"), created_at=time.time()))
            if not old:
                self.st.bump("leads")
                self.st.log("ok", f"Lead u/{e['author']} · fit {r['fit']} · {r.get('reason', '')}")
        return out

    def pick_subreddit(self) -> dict | None:
        on = [x for x in self.st.subreddits if x["enabled"]]
        if not on:
            return None
        pool = sorted(on, key=lambda x: x.get("last_visited") or 0)[:4]
        return random.choices(pool, weights=[x["score"] + 10 for x in pool])[0]

    def maybe_upvote(self, scope: str, permalink: str, author: str = "") -> None:
        if not self.s["upvotes"] or self.upvotes >= self.s["max_upvotes_per_session"]:
            return
        claim = None
        if self.central:  # one upvote per post/comment from the whole team (several accounts piling on looks like brigading)
            try:
                claim = self.central.claim("upvote", permalink, target_user=author)
            except Unreachable:
                return  # an upvote isn't worth risking a double
            if not claim.ok:
                return
        ok = False
        try:
            r = self.b.cmd({"cmd": "upvote", "scope": scope, "permalink": permalink}, 30)
            ok = bool(r and r.get("ok"))
            if ok and not r.get("already"):
                self.upvotes += 1
                self.st.bump("upvotes")
        finally:
            if claim:
                self.central.finish(claim, "done" if ok else "failed")

    def maybe_draft_opportunity(self, post: dict, r: dict) -> None:
        if not r.get("comment_fit") or self.opp_drafts >= 5 or any(o["id"] == post["id"] for o in self.st.opportunities):
            return
        if self.central and (self.central.seen("comment", [post["permalink"]]) or post["author"].lower() in self.central.own_usernames()):
            return  # the team already commented in this thread, or it's one of our own accounts' posts
        d = brain.draft_comment(self._need_llm(), self.st.product, post, self.ctx.team(post["subreddit"]))
        if not d.get("comment"):
            return
        self.opp_drafts += 1
        self.st.opportunities.insert(0, dict(
            id=post["id"], post_url=REDDIT + post["permalink"], title=post["title"], subreddit=post["subreddit"],
            author=post["author"], draft=d["comment"], why=d.get("why", ""), status="pending", created_at=time.time()))
        del self.st.opportunities[100:]
        self.st.save()
        self.st.log("info", f"Comment opportunity drafted in r/{post['subreddit']}: {post['title'][:60]}")

    # ---------------------------------------------------------------- browsing
    def visit_subreddit(self, sub: dict) -> None:
        name = sub["name"]
        self.st.log("info", f"Browsing r/{name}")
        self.b.hud(f"🕊 Browsing <b>r/{name}</b>")
        self.b.click_nav(f"/r/{name}", f"{REDDIT}/r/{name}/")
        sub["last_visited"] = time.time()
        self.st.save()

        br = self.b.cmd({"cmd": "browse", "maxPosts": 25, "seconds": round(clamp(lognormal(36, 0.35), 18, 85))}, 300)
        posts = br.get("posts") or []
        self.st.log("info", f"Read {len(posts)} posts in r/{name}")
        if not posts:
            self.st.log("warn", f"No posts could be read in r/{name} — Reddit's DOM may have changed or the page didn't load.")
            self.snap(f"no-posts-{name}")
            return

        self.b.hud(f"🕊 Reading people in <b>r/{name}</b>…")
        results = self.ingest([dict(id=p["id"], kind="post", author=p["author"], subreddit=p.get("subreddit") or name,
                                    title=p["title"], text=p.get("body") or p["title"], post_url=p["permalink"],
                                    post_title=p["title"]) for p in posts])
        ranked = sorted(((p, results[p["id"]]) for p in posts
                         if p["id"] in results and results[p["id"]].get("fit", 0) >= max(40, self.s["min_fit"] - 15)),
                        key=lambda x: -x[1]["fit"])[:2]

        for p, r in ranked:
            self.b.check()
            self.b.hud(f"🕊 Reading <b>{p['title'][:40]}…</b>")
            self.b.click_nav(p["permalink"], REDDIT + p["permalink"])
            rp = self.b.cmd({"cmd": "readPost"}, 240)
            if r["fit"] >= 75:
                self.maybe_upvote("post", p["permalink"], p["author"])
            self.maybe_draft_opportunity(rp.get("post") or p, r)
            cr = self.ingest([dict(id=c["id"], kind="comment", author=c["author"], subreddit=p.get("subreddit") or name,
                                   title=p["title"], text=c["text"], post_url=c["permalink"], post_title=p["title"])
                              for c in rp.get("comments") or []])
            authors = {c["id"]: c["author"] for c in rp.get("comments") or []}
            for cid in [k for k, v in cr.items() if v.get("fit", 0) >= 80 and v.get("dm_ok")][:2]:
                self.maybe_upvote("comment", cid, authors.get(cid, ""))

    # ---------------------------------------------------------------- DMs
    def prepare_queued(self, limit: int) -> None:
        for l in [l for l in self.st.list_leads() if l["status"] == "queued"][:limit]:
            self.b.check()
            try:
                status = ops.write_message(self.ctx, l)
            except Exception as e:
                self.st.log("warn", f"Could not write DM for u/{l['username']}: {e}")
                continue
            self.st.save()
            self.st.log("info", {"draft": "Draft ready for approval", "ready": "Message ready",
                                 "skipped": "Skipped (team already contacted)"}[status] + f": u/{l['username']}")

    def send_dm(self, lead: dict) -> None:
        u = lead["username"]
        if self.st.contacted.get(u.lower()) is not None:  # this account already messaged them (maybe before a Clear)
            self.st.patch_lead(u, status="skipped", error="this account already messaged them")
            self.st.log("info", f"Skipped u/{u}: already messaged from this account")
            return
        claim = None
        if self.central:  # reserve the person for this account first; refuse to send if we can't be sure nobody else has
            try:
                claim = self.central.claim("dm", u, subreddit=lead["subreddit"], title=lead["post_title"],
                                           body=lead["message"], url=lead["post_url"])
            except Unreachable as e:
                self.st.log("error", f"{e}. Not sending, to avoid messaging someone twice.")
                raise Stop()
            if claim.limit:
                self.st.log("warn", f"DM to u/{u} held: {claim.why()}")
                raise TeamHold(claim.limit.get("scope", "team"), claim.why())
            if not claim.ok:
                self.st.patch_lead(u, status="skipped", error=claim.why())
                self.st.log("info", f"Skipped u/{u}: {claim.why()}")
                return
        try:
            self._send_dm(lead)
        finally:
            if claim:  # settle the claim from what actually happened; anything unconfirmed is released
                now = self.st.get_lead(u) or {}
                status = {"sent": "done", "skipped": "blocked"}.get(now.get("status"), "failed")
                self.central.finish(claim, status, now.get("error") or ("" if status == "done" else "interrupted"))

    def _send_dm(self, lead: dict) -> None:
        u = lead["username"]
        self.st.log("info", f"DMing u/{u}")
        self.b.hud(f"🕊 Messaging <b>u/{u}</b>")
        self.b.navigate(f"{REDDIT}/user/{u}/")
        res = self.b.cmd({"cmd": "dm", "text": lead["message"]}, 240)
        if not res.get("ok") and res.get("code") == "no-chat-button":
            self.st.log("info", "No chat button; trying the message-compose page")
            self.b.navigate(f"{REDDIT}/message/compose/?to={u}")
            res = self.b.cmd({"cmd": "dm", "text": lead["message"]}, 240)
        if res.get("ok"):
            self.fails = 0
            self.st.patch_lead(u, status="sent", sent_at=time.time(), error=None)
            governor.record_dm(self.st)
            self.st.bump("dms")
            self.st.log("ok", f"Sent DM to u/{u}")
            return
        code, err = res.get("code"), res.get("error") or res.get("code")
        self.st.bump("errors")
        self.st.patch_lead(u, status="skipped" if code == "blocked" else "failed", error=err)
        self.st.log("error", f"DM to u/{u} failed: {err}")
        if code not in ("rate-limit", "captcha", "blocked"):
            self.snap(f"dm-{code}")  # a missing button or composer usually means Reddit's markup changed
        if code == "rate-limit":
            governor.pause_for(self.st, 6 * 3600)
            self.st.log("warn", "Reddit is rate limiting DMs. Paused DMs for 6 hours.")
            raise _Outcome()
        if code == "captcha":
            governor.pause_for(self.st, 24 * 3600)
            self.st.log("warn", "Captcha shown. Paused for 24h — solve it by hand in this profile (`pigeon run ID`), then resume.")
            raise _Outcome()
        if code != "blocked":
            self.fails += 1
            if self.fails >= 3:
                governor.pause_for(self.st, 3600)
                self.st.log("warn", "DM flow failed 3 times in a row — Reddit's chat UI may have changed. Stopping. Check the log.")
                raise _Outcome()

    def dm_tick(self, limit: int) -> bool:
        """Returns True when it's pointless to keep trying DMs this session."""
        for _ in range(limit):
            self.b.check()
            g = governor.check_dm(self.st)
            if not g["ok"]:
                if g.get("hard"):
                    self.st.log("info", f"DMs held: {g['reason']}")
                return bool(g.get("hard"))
            ready = lambda: [l for l in self.st.list_leads() if l["status"] == "ready" and l.get("message")
                             and l["username"].lower() not in self.deferred]
            leads = ready()
            if not leads:
                self.prepare_queued(3)
                leads = ready()
            if not leads:
                return False
            try:
                self.send_dm(leads[0])
            except TeamHold as h:
                if h.scope == "team":
                    return True  # the whole team has sent enough for today: nothing more to do this session
                self.deferred.add(leads[0]["username"].lower())  # that subreddit is at its cap; try someone else
        return False

    # ---------------------------------------------------------------- session
    def sync_team(self) -> None:
        """Adopt the team's shared setup if a newer one was published. A no-op without the central brain."""
        if not self.central:
            return
        try:
            r = ops.team_pull(self.ctx)
            if r["pulled"]:
                self.st.log("info", r["out"])
                self.st.save()
        except Exception as e:
            self.st.log("warn", f"couldn't check the team setup: {e}")

    def identify(self) -> None:
        """Work out which Reddit user this profile is logged in as, and tell the team, so our own accounts never target each other."""
        if not self.s["my_username"]:
            try:
                name = self.b.reddit_json("/api/me.json")["data"]["name"]
                if name:
                    self.s["my_username"] = name
                    self.st.save()
                    self.st.log("info", f"Logged in as u/{name}")
            except Exception:
                self.st.log("warn", "Couldn't tell which Reddit user this is (not logged in?). Set it: `pigeon agent set ID my_username=NAME`.")
        if self.central and self.s["my_username"]:
            err = self.central.register_account(self.s["my_username"])
            if err:
                self.st.log("warn", f"Couldn't register u/{self.s['my_username']} with the team: {err}")

    def run(self, mode: str, minutes: float | None = None) -> None:
        self.sync_team()
        s = self.s
        if mode != "dm":
            if not self.st.product:
                raise RuntimeError("Set up the product first: `pigeon agent setup ID URL`.")
            if not any(x["enabled"] for x in self.st.subreddits):
                raise RuntimeError("No subreddits enabled. Run `pigeon agent setup ID URL` or enable some with `pigeon agent set`.")
        if s["warmup"] and not s["warmup_start"]:
            s["warmup_start"] = time.time()
            self.st.save()
        self.upvotes = self.opp_drafts = self.fails = 0
        self.deferred.clear()
        self.b.ready()
        if "reddit.com" not in self.b.page.url:  # same-origin for the username lookup, and where a person would start anyway
            self.b.navigate(f"{REDDIT}/", settle=(0.5, 3.0, 1.0))
        self.identify()
        end = time.time() + (minutes if minutes is not None else s["session_minutes"]) * 60
        self.st.log("info", f"Session started ({mode})")
        try:
            if s.get("auto_check_replies", True) and any(l["status"] == "sent" for l in self.st.leads.values()):
                try:  # who answered since last time: keeps the shared reply record (and the writing context) current
                    self.check_replies()
                except Stop:
                    raise
                except Exception as e:
                    self.st.log("warn", f"Couldn't check replies: {e}")
            if mode == "dm":
                while time.time() < end:
                    if self.dm_tick(1):
                        break
                    g = governor.check_dm(self.st)
                    if not any(l["status"] in ("ready", "queued") for l in self.st.leads.values()):
                        break
                    self.b.hud("🕊 Waiting before the next DM…")
                    self.b.wait(5 if g["ok"] else min(g.get("wait", 20), 120))
                return
            while time.time() < end:
                self.b.check()
                sub = self.pick_subreddit()
                if not sub:
                    break
                self.visit_subreddit(sub)
                if mode == "full":
                    hard = self.dm_tick(2)
                    if hard and not governor.in_active_hours(s):
                        break
                self.b.wait(clamp(lognormal(7, 0.55), 3, 40))
            self.st.log("ok", "Session finished")
        except Stop:
            self.st.log("info", "Stopped")
        except Exception as e:
            self.st.log("error", str(e))
            self.st.bump("errors")
            raise
        finally:
            self.b.hud("")
            self.st.save()

    # ---------------------------------------------------------------- one-off actions
    def send_lead_now(self, username: str) -> None:
        lead = self.st.get_lead(username)
        if not lead or not lead.get("message"):
            raise RuntimeError("No message written for this lead yet.")
        if lead["status"] != "ready":
            raise RuntimeError("Approve this message first: every message waits for approval before it is sent.")
        g = governor.check_dm(self.st)
        if not g["ok"]:
            raise RuntimeError(g["reason"])
        self.b.ready()
        self.send_dm(lead)

    def post_opportunity(self, oid: str) -> None:
        o = next((x for x in self.st.opportunities if x["id"] == oid), None)
        if not o:
            raise RuntimeError("Opportunity not found")
        claim = None
        if self.central:  # one comment per thread across all our accounts
            try:
                claim = self.central.claim("comment", o["post_url"], subreddit=o["subreddit"], title=o["title"],
                                           body=o["draft"], url=o["post_url"])
            except Unreachable as e:
                raise RuntimeError(f"{e}. Not commenting, to avoid doubling up in a thread.")
            if not claim.ok:
                o["status"] = "skipped"
                self.st.save()
                raise RuntimeError(f"the team already commented in this thread: {claim.who()}")
        ok = False
        try:
            self.b.ready()
            self.st.log("info", f"Posting comment in r/{o['subreddit']}")
            self.b.navigate(o["post_url"])
            r = self.b.cmd({"cmd": "postComment", "text": o["draft"]}, 180)
            ok = bool(r.get("ok"))
            o["status"] = "posted" if ok else "failed"
            self.st.save()
            if not ok:
                self.snap("comment-failed")
                raise RuntimeError(r.get("error") or "Comment failed")
            self.st.log("ok", f"Comment posted in r/{o['subreddit']}")
        finally:
            if claim:
                self.central.finish(claim, "done" if ok else "failed", "" if ok else (o.get("error") or "not posted"))

    def check_replies(self) -> int:
        """Mark sent leads as replied. Two independent sources: Reddit's private-message inbox (a documented JSON API,
        which is where replies to compose-page DMs land) and the chat list (judged by who spoke last, not a badge)."""
        sent = [l for l in self.st.leads.values() if l["status"] == "sent"]
        if not sent:
            return 0
        self.b.ready()
        self.b.navigate(f"{REDDIT}/chat")
        self.b.wait(3)
        found: dict[str, str] = {}
        by_name = {l["username"].lower(): l for l in sent}
        try:  # 1. private messages
            for ch in self.b.reddit_json("/message/inbox.json?limit=100")["data"]["children"]:
                d = ch["data"]
                lead = by_name.get((d.get("author") or "").lower())
                if lead and d.get("created_utc", 0) >= (lead.get("sent_at") or 0) - 60:
                    found[lead["username"]] = "inbox"
        except Exception:
            pass  # not available on this account/page; the chat list may still tell us
        rooms = (self.b.cmd({"cmd": "scanInbox"}, 30) or {}).get("rooms") or []
        for room in rooms:  # 2. chat rooms
            for lead in sent:
                if lead["username"] not in found and room_replied(room.get("text", ""), room.get("unread", False), lead):
                    found[lead["username"]] = "chat"
        for name, via in found.items():
            lead = self.st.get_lead(name)
            self.st.patch_lead(name, status="replied")
            ops.record_reply(self.ctx, lead)
            self.st.log("ok", f"u/{name} replied ({via})")
        if not rooms:
            self.snap("chat-no-rooms")  # nothing parsed from the chat list: empty, or the markup changed
        self.st.log("info", f"Checked replies: {len(sent)} awaiting, {len(rooms)} chat rooms, {len(found)} new repl{'y' if len(found) == 1 else 'ies'}")
        return len(found)

    # ---------------------------------------------------------------- setup + page scan
    def analyze(self, url: str, extra: str = "") -> None:
        """Read the product's site and build the ICP profile (what the Setup tab's Analyze does)."""
        llm = self._need_llm()
        self.st.log("info", f"Reading {url}")
        self.st.product = brain.build_profile(llm, url, brain.scrape_site(url), extra)
        self.st.save()
        self.st.log("ok", f"Product profile: {self.st.product['name']}")

    def discover(self) -> None:
        """Find and score subreddits for the product. Keeps your on/off choices for ones it already knew."""
        if not self.st.product:
            raise RuntimeError("No product yet: run setup with a URL first.")
        self.b.ready()
        self.b.navigate(f"{REDDIT}/", settle=(0.5, 3.0, 1.0))  # same-origin, so the logged-in session is used
        found = brain.discover_subreddits(self._need_llm(), self.st.product, self.b.reddit_json,
                                          progress=lambda m: self.st.log("info", m))
        old = {s["name"].lower(): s for s in self.st.subreddits}
        for s in found:
            if s["name"].lower() in old:
                s["enabled"] = old[s["name"].lower()]["enabled"]
                s["last_visited"] = old[s["name"].lower()].get("last_visited", 0)
        manual = [s for s in self.st.subreddits if s["reason"] == "added manually" and s["name"].lower() not in {f["name"].lower() for f in found}]
        self.st.subreddits[:] = found + manual
        self.st.save()
        self.st.log("ok", f"{len(found)} subreddits scored, {sum(s['enabled'] for s in found)} enabled")

    def scan_page(self, skip_scoring: bool, url: str | None = None) -> int:
        """Page DMer: queue the people on the Reddit page this bridge is looking at (or on `url`)."""
        if url:
            self.b.navigate(url)
        self.b.ready()
        items = (self.b.cmd({"cmd": "scanPage"}, 30) or {}).get("items") or []
        if not items:
            self.snap("scan-nothing")
        mine = self.s["my_username"].lower()
        if skip_scoring:
            names = sorted({it["username"] for it in items})
            team = {k.lower() for k in (self.central.seen("dm", names) if self.central else {})}
            team |= self.central.own_usernames() if self.central else set()
            added = 0
            for it in items:
                u = it["username"]
                if u.lower() == mine or u.lower() in team or self.st.get_lead(u) or self.st.was_contacted(u):
                    continue
                self.st.put_lead(dict(username=u, subreddit=it["subreddit"], post_url=it["postUrl"], post_title=it["postTitle"],
                                      snippet=it["snippet"], fit=50, intent="unscored", reason="Added from page scan",
                                      status="queued", source="page", created_at=time.time()))
                added += 1
        else:
            if not self.st.product:
                raise RuntimeError("Set up the product first, or use --no-score.")
            before = len(self.st.leads)
            self.ingest([dict(id=it["postUrl"] + it["username"], kind="post", author=it["username"], subreddit=it["subreddit"],
                              title=it["postTitle"], text=it["snippet"], post_url=it["postUrl"], post_title=it["postTitle"],
                              source="page") for it in items])
            added = len(self.st.leads) - before
        self.st.log("ok", f"Page scan: {len(items)} people seen, {added} added to queue")
        return added

    # ---------------------------------------------------------------- submit a post
    def submit_idea(self, iid: str, force: bool = False) -> str:
        """Post one of the generated ideas as a text post. Only ever on explicit request, never on autopilot. Guarded by
        the daily post cap, the subreddit's rules risk, the team's claim on the title and the team's post limits.
        Success is judged by Reddit redirecting to the new thread, never by the click. Returns the post's URL."""
        i = next((x for x in self.st.post_ideas if x["id"] == iid), None)
        if not i:
            raise RuntimeError("post idea not found (see `pigeon agent ideas ID`)")
        sub = i["subreddit"]
        meta = next((x for x in self.st.subreddits if x["name"].lower() == sub.lower()), None)
        if meta and meta["rules_risk"] == "high" and not force:
            raise RuntimeError(f"r/{sub} bans or limits promotion (rules risk: high). Read its rules; --force posts anyway.")
        d = self.st.get_daily()
        if d.get("posts", 0) >= self.s["posts_per_day"] and not force:
            raise RuntimeError(f"daily post cap reached ({d.get('posts', 0)}/{self.s['posts_per_day']}); --force overrides")
        claim = None
        if self.central:
            try:
                claim = self.central.claim("post", i["title"], subreddit=sub, title=i["title"], body=i["body"])
            except Unreachable as e:
                raise RuntimeError(f"{e}. Not posting, so the team doesn't double up.")
            if not claim.ok:
                raise RuntimeError(claim.why() if (claim.limit or claim.own) else f"the team already posted this: {claim.who()}")
        url = ""
        try:
            self.b.ready()
            self.st.log("info", f"Submitting a post to r/{sub}: {i['title'][:60]}")
            self.b.hud(f"🕊 Posting in <b>r/{sub}</b>")
            self.b.navigate(f"{REDDIT}/r/{sub}/submit?type=TEXT")
            try:
                r = self.b.cmd({"cmd": "submitPost", "title": i["title"], "body": i["body"]}, 300)
            except Stop:
                raise
            except Exception as e:  # the click can navigate away before we read the answer; the redirect is the real proof
                r = {"ok": True, "unread": str(e)}
            if not r.get("ok"):
                self.snap(f"submit-{r.get('code')}")
                extra = f" ({', '.join(r['errors'])})" if r.get("errors") else ""
                raise RuntimeError(f"{r.get('error') or r.get('code')}{extra}")
            end = time.time() + 30
            while time.time() < end and "/comments/" not in url:
                self.b.check()
                url = self.b.page.url if "/comments/" in self.b.page.url else ""
                self.b.nap(0.5)
            if not url:
                try:
                    errs = (self.b.cmd({"cmd": "submitErrors"}, 15) or {}).get("errors") or []
                except Exception:
                    errs = []
                self.snap("submit-no-redirect")
                raise RuntimeError("Reddit didn't take the post" + (f": {', '.join(errs)}" if errs else " (no redirect to a new thread within 30s)")
                                   + (f" [{r['unread']}]" if r.get("unread") else ""))
            d["posts"] = d.get("posts", 0) + 1
            self.st.posted.insert(0, dict(id=iid, subreddit=sub, title=i["title"], url=url, at=time.time()))
            self.st.post_ideas[:] = [x for x in self.st.post_ideas if x["id"] != iid]
            self.st.stats["posts"] = self.st.stats.get("posts", 0) + 1
            self.st.save()
            self.st.log("ok", f"Posted in r/{sub}: {url}")
            return url
        finally:
            self.b.hud("")
            if claim:
                self.central.finish(claim, "done" if url else "failed", "" if url else "not posted")

    # ---------------------------------------------------------------- doctor
    HINTS = {
        "logged in": "this profile isn't logged in to Reddit: open it with `pigeon run ID`, log in, close it",
        "post elements": "no posts found: logged out, a wall or captcha, or Reddit changed its feed markup",
        "posts parse": "posts were found but not parsed: the shreddit-post attributes (author, post-title, permalink) changed",
        "upvote button": "no visible upvote button: upvoting would skip silently",
        "comments parse": "comments weren't parsed: the shreddit-comment markup changed",
        "comment box": "no comment box: commenting would fail",
        "profile exists": "the sampled profile doesn't exist or is suspended (try again)",
        "chat / message button": "no Chat/Message button on a profile: DMs would fail with no-chat-button",
        "chat rooms list": "no chat rooms parsed: reply detection from the chat list wouldn't work (an empty inbox looks the same)",
        "title field": "no title field on the submit page: posting would fail",
        "body editor": "no body editor on the submit page",
        "Post button": "no Post button on the submit page",
    }

    def doctor(self) -> dict:
        """Check Pigeon's assumptions about Reddit's markup on this account, read-only: it visits a feed, a post, a profile, the
        chat list and a submit page and never types, clicks or sends. Each failing page is snapshotted for debugging."""
        self.b.ready()
        pages: list[dict] = []

        def visit(kind: str, url: str, label: str) -> dict:
            self.b.check()
            self.b.navigate(url)
            r = self.b.cmd({"cmd": "probe", "kind": kind}, 30) or {}
            failing = [c for c in r.get("checks", []) if not c["ok"]]
            if failing:
                self.snap(f"doctor-{kind}")
            pages.append(dict(kind=kind, label=label, url=r.get("url", url), checks=r.get("checks", []), sample=r.get("sample", {})))
            return r

        sub = next((x["name"] for x in self.st.subreddits if x["enabled"]), None)
        feed = visit("feed", f"{REDDIT}/r/{sub}/" if sub else f"{REDDIT}/", f"feed of r/{sub}" if sub else "home feed")
        sample = feed.get("sample") or {}
        if sample.get("permalink"):
            visit("post", REDDIT + sample["permalink"], "a post")
        if sample.get("author"):
            visit("profile", f"{REDDIT}/user/{sample['author']}/", f"a profile (u/{sample['author']})")
        visit("chat", f"{REDDIT}/chat", "chat list")
        visit("submit", f"{REDDIT}/r/{sub or 'test'}/submit?type=TEXT", f"submit page of r/{sub or 'test'}")

        lines, failed = [], 0
        for pg in pages:
            lines.append(f"{pg['label']}  ({pg['url']})")
            for c in pg["checks"]:
                failed += 0 if c["ok"] else 1
                hint = next((h for k, h in self.HINTS.items() if c["name"].startswith(k)), "")
                lines.append(f"  {'✓' if c['ok'] else '✗'} {c['name']}" + (f"  [{c['detail']}]" if c["detail"] else "") + (f"\n      → {hint}" if not c["ok"] and hint else ""))
            if pg["sample"]:
                lines.append(f"  sample: {pg['sample']}")
        total = sum(len(pg["checks"]) for pg in pages)
        lines.append(f"\n{total - failed}/{total} checks passed" + (f"; {failed} failed. Snapshots are in {self.diag_dir}" if failed and self.diag_dir else ""))
        self.st.log("ok" if not failed else "warn", f"Doctor: {total - failed}/{total} checks passed")
        return {"pages": pages, "failed": failed, "out": "\n".join(lines)}
