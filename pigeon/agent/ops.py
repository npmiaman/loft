"""Every Pigeon operation in one place, so the same thing works from the CLI, the desktop app, a standalone run and a
live browser session.

State/LLM operations take a `Ctx` and return a dict (`out` is text for humans). They never call `save()`: whoever runs
them does, on the right thread. Browser operations take an `Agent` that is already looking at a page."""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import brain
from .central import CentralBrain
from .llm import Llm
from .state import DEFAULT_SETTINGS, DEFAULT_TEMPLATE, State

# settings the team shares (the account-level safety ones, like caps, hours and username, always stay per account)
SHARED_SETTINGS = ("min_fit", "dm_mode", "approve_ai_drafts", "ai_guidance", "upvotes", "max_upvotes_per_session", "session_minutes")
PRODUCT_LISTS = ("personas", "painPhrases", "buyingSignals", "competitors", "subredditIdeas", "searchQueries")
PRODUCT_TEXT = ("name", "summary", "offer", "icp")


@dataclass
class Ctx:
    st: State
    llm: Llm | None = None
    brain: CentralBrain | None = None

    def need_llm(self) -> Llm:
        if not self.llm:
            raise RuntimeError("No DeepSeek key: `pigeon agent config --deepseek-key KEY` or set DEEPSEEK_API_KEY.")
        return self.llm

    def team(self, sub: str) -> str:
        return self.brain.context(sub) if self.brain and sub else ""


def _lead(c: Ctx, username: str) -> dict:
    l = c.st.get_lead(username)
    if not l:
        raise RuntimeError(f"no such lead: {username}")
    return l


# ---------------------------------------------------------------- writing a message for a lead
def write_message(c: Ctx, l: dict) -> str:
    """Write (AI or template) the DM for one lead and set its status. Returns the new status."""
    s = c.st.settings
    if c.brain and l["username"].lower() in c.brain.own_usernames():
        l.update(status="skipped", error="one of our own accounts")
        return "skipped"
    if c.brain:  # someone else may have got there first since this lead was queued
        hit = c.brain.seen("dm", [l["username"]]).get(l["username"])
        if hit:
            l.update(status="skipped", error=f"already contacted by {hit.get('profile') or 'another account'}")
            return "skipped"
    use_ai = s["dm_mode"] == "ai" and bool(c.st.product) and bool(l["snippet"])
    msg = (brain.write_dm(c.need_llm(), c.st.product, l, s["ai_guidance"], c.team(l["subreddit"])) if use_ai
           else brain.render_template((c.st.templates or [DEFAULT_TEMPLATE])[0], l))
    if not msg:
        raise RuntimeError("empty message")
    l["message"] = msg
    l["status"] = "draft" if use_ai and s["approve_ai_drafts"] else "ready"
    l["error"] = None
    return l["status"]


# ---------------------------------------------------------------- settings, product, subreddits, templates
def settings_set(c: Ctx, a: dict) -> dict:
    for k, v in (a.get("pairs") or {}).items():
        if k not in DEFAULT_SETTINGS or k == "warmup_start":
            raise RuntimeError(f"unknown setting {k!r}; choices: {', '.join(x for x in DEFAULT_SETTINGS if x != 'warmup_start')}")
        cur = DEFAULT_SETTINGS[k]
        c.st.settings[k] = (str(v).lower() in ("1", "true", "yes", "on")) if isinstance(cur, bool) else type(cur)(v)
    if a.get("restart_warmup"):
        c.st.settings["warmup_start"] = time.time()
    note = ""
    if c.brain and "my_username" in (a.get("pairs") or {}) and c.st.settings["my_username"]:
        err = c.brain.register_account(c.st.settings["my_username"])  # so the team's other accounts never target this one
        note = f"\n! couldn't register the username with the team: {err}" if err else "\nregistered with the team's account list"
    return {"settings": dict(c.st.settings), "out": "\n".join(f"  {k:26} {v}" for k, v in c.st.settings.items()) + note}


def product_set(c: Ctx, a: dict) -> dict:
    if not c.st.product:
        raise RuntimeError("No product yet: run `pigeon agent setup ID URL` first.")
    for k, v in (a.get("fields") or {}).items():
        if k in PRODUCT_LISTS:
            c.st.product[k] = [x.strip() for x in (v if isinstance(v, list) else str(v).split("|")) if x.strip()]
        elif k in PRODUCT_TEXT:
            c.st.product[k] = str(v)
        else:
            raise RuntimeError(f"unknown product field {k!r}; choices: {', '.join(PRODUCT_TEXT + PRODUCT_LISTS)}")
    return product_show(c, {})


def product_show(c: Ctx, _a: dict) -> dict:
    p = c.st.product
    if not p:
        return {"product": None, "out": "no product yet: run `pigeon agent setup ID URL`"}
    lines = [f"{k:15} {p[k]}" for k in PRODUCT_TEXT] + [f"{k:15} {' | '.join(p[k])}" for k in PRODUCT_LISTS]
    return {"product": p, "out": "\n".join(lines)}


def subs_edit(c: Ctx, a: dict) -> dict:
    low = lambda xs: {re.sub(r"^/?r/", "", x, flags=re.I).lower() for x in xs or []}
    on, off, rm = low(a.get("enable")), low(a.get("disable")), low(a.get("remove"))
    for name in low(a.get("add")):
        if not any(s["name"].lower() == name for s in c.st.subreddits):
            c.st.subreddits.append(dict(name=name, subscribers=0, posts_per_day=0, icp_density=0, rules_risk="medium",
                                        score=50, reason="added manually", enabled=True, last_visited=0))
    c.st.subreddits[:] = [s for s in c.st.subreddits if s["name"].lower() not in rm]
    for s in c.st.subreddits:
        if s["name"].lower() in on:
            s["enabled"] = True
        if s["name"].lower() in off:
            s["enabled"] = False
    rows = [f"  {'on ' if s['enabled'] else 'off'} r/{s['name']:24} score {s['score']:>3}  rules:{s['rules_risk']:6} {s['reason'][:60]}"
            for s in c.st.subreddits]
    return {"subreddits": c.st.subreddits, "out": "\n".join(rows) or "no subreddits: run `pigeon agent setup`"}


def template_show(c: Ctx, _a: dict) -> dict:
    return {"template": (c.st.templates or [DEFAULT_TEMPLATE])[0], "undo": c.st.template_undo is not None,
            "out": (c.st.templates or [DEFAULT_TEMPLATE])[0]}


def template_set(c: Ctx, a: dict) -> dict:
    body = (a.get("body") or "").strip()
    if not body:
        raise RuntimeError("empty template")
    c.st.templates[:] = [body] + c.st.templates[1:]
    c.st.template_undo = None
    return template_show(c, {})


def template_spin(c: Ctx, a: dict) -> dict:
    cur = (c.st.templates or [DEFAULT_TEMPLATE])[0]
    spun = brain.add_spintax(c.need_llm(), cur, a.get("intensity", "medium"))
    c.st.template_undo = cur
    c.st.templates[:] = [spun] + c.st.templates[1:]
    return template_preview(c, {"n": 4}) | {"template": spun}


def template_undo(c: Ctx, _a: dict) -> dict:
    if c.st.template_undo is None:
        raise RuntimeError("nothing to undo")
    c.st.templates[:] = [c.st.template_undo] + c.st.templates[1:]
    c.st.template_undo = None
    return template_show(c, {})


def template_preview(c: Ctx, a: dict) -> dict:
    body = (c.st.templates or [DEFAULT_TEMPLATE])[0]
    first = c.st.list_leads()[:1]
    sample = first[0] if first else dict(username="alex", subreddit="startups", post_title="How do I find my first customers?")
    prev = brain.preview_variants(body, sample, int(a.get("n", 4)))
    n = brain.count_variants(body)
    return {"template": body, "variants": n, "previews": prev,
            "out": f"{n} possible variations\n" + "\n".join(f"  · {p}" for p in prev)}


# ---------------------------------------------------------------- leads
def leads_list(c: Ctx, a: dict) -> dict:
    rows = [dict(l) for l in c.st.list_leads() if not a.get("status") or l["status"] == a["status"]]
    out = [f"{'USER':22} {'SUB':16} {'FIT':>3}  {'STATUS':8} {'WHEN':11} WHY / MESSAGE"]
    for l in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(l.get("sent_at") or l["created_at"]))
        out.append(f"{l['username'][:22]:22} {l['subreddit'][:16]:16} {l['fit']:>3}  {l['status']:8} {when:11} {l['reason'][:70]}"
                   + (f"\n{'':56}↳ {l['message'][:110]}" if l.get("message") and l["status"] in ("draft", "ready", "sent") else "")
                   + (f"\n{'':56}! {l['error']}" if l.get("error") else ""))
    return {"leads": rows, "out": "\n".join(out) if rows else "no leads"}


def lead_write(c: Ctx, a: dict) -> dict:
    todo = ([l for l in c.st.list_leads() if l["status"] in ("queued", "failed")] if a.get("all")
            else [_lead(c, a["username"])])
    done, errs = 0, []
    for l in todo:
        try:
            write_message(c, l)
            done += 1
        except Exception as e:
            errs.append(f"u/{l['username']}: {e}")
    return {"written": done, "errors": errs, "out": f"wrote {done} message(s)" + ("".join("\n  ! " + e for e in errs))}


def lead_edit(c: Ctx, a: dict) -> dict:
    l = _lead(c, a["username"])
    if l["status"] in ("sent", "replied"):
        raise RuntimeError("already sent; can't edit")
    l["message"] = a["message"]
    if l["status"] == "queued":
        l["status"] = "draft"
    return {"out": f"u/{l['username']}: {l['status']}"}


def lead_approve(c: Ctx, a: dict) -> dict:
    todo = [l for l in list(c.st.leads.values()) if l["status"] == "draft"] if a.get("all") else [_lead(c, a["username"])]
    n = 0
    for l in todo:
        if a.get("message"):
            l["message"] = a["message"]
        if not l.get("message"):
            continue
        l["status"] = "ready"
        n += 1
    return {"approved": n, "out": f"approved {n}"}


def record_reply(c: Ctx, l: dict) -> None:
    """Tell the team this person answered, and which of our messages earned it (it feeds the writing prompts)."""
    if not c.brain:
        return
    try:
        cl = c.brain.claim("reply", l["username"], subreddit=l["subreddit"], title=l["post_title"], body=l.get("message") or "")
        if cl.ok:
            c.brain.finish(cl, "done")
    except Exception:
        pass  # analytics only: never block marking a reply


def lead_set_status(status: str) -> Callable[[Ctx, dict], dict]:
    def fn(c: Ctx, a: dict) -> dict:
        l = _lead(c, a["username"])
        if status == "skipped" and l["status"] in ("sent", "replied", "skipped"):
            raise RuntimeError(f"u/{l['username']} is already {l['status']}")
        if status == "replied" and l["status"] != "sent":
            raise RuntimeError("only sent leads can be marked replied")
        l["status"] = status
        if status == "replied":
            record_reply(c, l)
        return {"out": f"u/{l['username']}: {status}"}
    return fn


def leads_import(c: Ctx, a: dict) -> dict:
    """List DMer: usernames one per line (or a CSV with usernames in the first column)."""
    names = []
    for raw in (a.get("text") or "").splitlines():
        n = re.sub(r"/$", "", re.sub(r"^/?u/", "", re.sub(r"^.*/user/", "", raw.split(",")[0].strip(), flags=re.I), flags=re.I))
        if re.fullmatch(r"[A-Za-z0-9_-]{3,20}", n) and n.lower() not in {x.lower() for x in names}:
            names.append(n)
    contacted = {k.lower() for k in (c.brain.seen("dm", names) if c.brain else {})}
    own = c.brain.own_usernames() if c.brain else set()
    added = dup = team = 0
    for n in names:
        if c.st.get_lead(n):
            dup += 1
        elif n.lower() in contacted or n.lower() in own or n.lower() == c.st.settings["my_username"].lower():
            team += 1
        else:
            c.st.leads[n.lower()] = dict(username=n, subreddit="", post_url="", post_title="", snippet="", fit=50, intent="imported",
                                         reason="Imported from list", status="queued", source="csv", created_at=time.time())
            added += 1
    return {"added": added, "out": f"imported {added} username(s); {dup} already in the queue" + (f"; {team} skipped (our own accounts, or the team already contacted them)" if team else "")}


def leads_clear(c: Ctx, _a: dict) -> dict:
    n = len(c.st.leads)
    c.st.leads.clear()
    return {"out": f"deleted {n} lead(s)"}


def leads_export(c: Ctx, _a: dict) -> dict:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["username", "subreddit", "fit", "status", "reason", "message", "post_url"])
    for l in c.st.list_leads():
        w.writerow([l["username"], l["subreddit"], l["fit"], l["status"], l["reason"], l.get("message") or "",
                    "https://www.reddit.com" + l["post_url"] if l["post_url"] else ""])
    return {"csv": buf.getvalue(), "out": buf.getvalue()}


# ---------------------------------------------------------------- comments, post ideas
def opps_list(c: Ctx, _a: dict) -> dict:
    rows = c.st.opportunities
    out = "\n".join(f"[{o['status']}] r/{o['subreddit']}  {o['title'][:70]}\n   id: {o['id']}\n   {o['draft']}\n" for o in rows)
    return {"opportunities": rows, "out": out or "no comment opportunities yet"}


def opp_skip(c: Ctx, a: dict) -> dict:
    o = next((x for x in c.st.opportunities if x["id"] == a["id"]), None)
    if not o:
        raise RuntimeError("opportunity not found")
    o["status"] = "skipped"
    return {"out": "skipped"}


def ideas_generate(c: Ctx, _a: dict) -> dict:
    if not c.st.product:
        raise RuntimeError("No product yet: run `pigeon agent setup ID URL` first.")
    subs = [s for s in c.st.subreddits if s["enabled"]][:8]
    ctx = "\n".join(t for t in (c.team(s["name"]) for s in subs) if t)
    c.st.post_ideas[:] = brain.generate_post_ideas(c.need_llm(), c.st.product, subs, ctx)
    return ideas_list(c, {})


def ideas_list(c: Ctx, _a: dict) -> dict:
    out = "\n".join(f"id: {i['id']}\nr/{i['subreddit']} — {i['title']}\n{i['body']}\n(angle: {i['angle']})\n" for i in c.st.post_ideas)
    return {"ideas": c.st.post_ideas, "out": out or "no post ideas yet: `pigeon agent ideas ID --generate`"}


def posts_list(c: Ctx, _a: dict) -> dict:
    rows = c.st.posted
    out = "\n".join(f"{time.strftime('%m-%d %H:%M', time.localtime(p['at']))}  r/{p['subreddit']}  {p['title'][:60]}\n   {p['url']}" for p in rows)
    return {"posted": rows, "out": out or "the agent hasn't submitted any posts yet"}


def idea_posted(c: Ctx, a: dict) -> dict:
    """Record that the team posted an idea (the agent doesn't submit posts itself), so nobody repeats it."""
    i = next((x for x in c.st.post_ideas if x["id"] == a["id"]), None)
    if not i:
        raise RuntimeError("idea not found")
    if not c.brain:
        raise RuntimeError("the central brain is unavailable, so this wasn't recorded")
    cl = c.brain.claim("post", i["title"], subreddit=i["subreddit"], title=i["title"], body=i["body"])
    if not cl.ok:
        raise RuntimeError(f"the team already posted this: {cl.who()}")
    c.brain.finish(cl, "done")
    c.st.post_ideas[:] = [x for x in c.st.post_ideas if x["id"] != i["id"]]
    return {"out": f"recorded in the central brain. Submit page: https://www.reddit.com/r/{i['subreddit']}/submit"}


def brain_backfill(c: Ctx, _a: dict) -> dict:
    """Push what this account already did (DMs sent, comments posted) into the team's shared memory, with the original
    dates, so other accounts won't repeat it. Safe to run again: things the team already knows are left alone."""
    if not c.brain:
        raise RuntimeError("the central brain is unavailable; nothing was recorded")
    added = known = 0
    for l in list(c.st.leads.values()):
        if l["status"] not in ("sent", "replied"):
            continue
        cl = c.brain.claim("dm", l["username"], subreddit=l["subreddit"], title=l["post_title"], body=l.get("message") or "",
                           url=l["post_url"], at=l.get("sent_at") or l["created_at"])
        if cl.ok:
            c.brain.finish(cl, "done")
            added += 1
        else:
            known += 1
    for o in c.st.opportunities:
        if o["status"] != "posted":
            continue
        cl = c.brain.claim("comment", o["post_url"], subreddit=o["subreddit"], title=o["title"], body=o["draft"],
                           url=o["post_url"], at=o["created_at"])
        if cl.ok:
            c.brain.finish(cl, "done")
            added += 1
        else:
            known += 1
    return {"added": added, "known": known, "out": f"recorded {added} past DM(s)/comment(s) in the team memory; {known} were already there"}


def team_show(c: Ctx, _a: dict) -> dict:
    if not c.brain:
        raise RuntimeError("the central brain is unavailable")
    v, data = c.brain.team_config()
    lim = data.get("limits") or {}
    return {"version": v, "mine": c.st.team_version, "data": data,
            "out": f"team setup v{v} (this account has v{c.st.team_version}, {'follows' if c.st.settings['use_team_setup'] else 'ignores'} it)\n"
                   f"  product: {(data.get('product') or {}).get('name', '-')}   subreddits: {len(data.get('subreddits') or [])}   "
                   f"template: {'yes' if data.get('template') else '-'}\n"
                   f"  limits: " + (", ".join(f"{k}={v}" for k, v in lim.items()) or "none")}


def team_pull(c: Ctx, a: dict | None = None) -> dict:
    """Adopt the team's shared setup (product, subreddits, template, shared settings) if it is newer than ours.
    Per-account things (caps, hours, username, warm-up, which subreddits you visited when) are never touched."""
    force = bool((a or {}).get("force"))
    if not c.brain:
        raise RuntimeError("the central brain is unavailable")
    if not c.st.settings["use_team_setup"] and not force:
        return {"pulled": False, "out": "this account ignores the team setup (use_team_setup=false)"}
    v, data = c.brain.team_config()
    if not data or (v <= c.st.team_version and not force):
        return {"pulled": False, "out": "already up to date" if data else "the team has no shared setup yet"}
    if data.get("product"):
        c.st.product = data["product"]
    if data.get("subreddits"):
        old = {s["name"].lower(): s for s in c.st.subreddits}
        c.st.subreddits[:] = [dict(s, last_visited=old.get(s["name"].lower(), {}).get("last_visited", 0)) for s in data["subreddits"]]
    if data.get("template"):
        c.st.templates[:] = [data["template"]] + c.st.templates[1:]
        c.st.template_undo = None
    for k, val in (data.get("settings") or {}).items():
        if k in SHARED_SETTINGS:
            c.st.settings[k] = val
    c.st.team_version = v
    return {"pulled": True, "out": f"adopted the team setup v{v}: product {(c.st.product or {}).get('name', '-')}, "
                                   f"{len(c.st.subreddits)} subreddits"}


def team_publish(c: Ctx, _a: dict) -> dict:
    """Share this account's setup with the whole team (owners/admins). Other accounts adopt it next time they run."""
    if not c.brain:
        raise RuntimeError("the central brain is unavailable")
    if not c.st.product:
        raise RuntimeError("this account has no product set up to share")
    v, _ = c.brain.team_config()
    setup = dict(product=c.st.product, subreddits=[{k: x for k, x in s.items() if k != "last_visited"} for s in c.st.subreddits],
                 template=(c.st.templates or [DEFAULT_TEMPLATE])[0], settings={k: c.st.settings[k] for k in SHARED_SETTINGS})
    try:
        new = c.brain.put_setup(setup, v)
    except Exception as e:
        raise RuntimeError(f"couldn't publish: {getattr(e, 'detail', e)}")
    c.st.team_version = new
    return {"version": new, "out": f"published as team setup v{new}"}


def status(c: Ctx, _a: dict) -> dict:
    d = c.st.get_daily()
    by: dict[str, int] = {}
    for l in c.st.leads.values():
        by[l["status"]] = by.get(l["status"], 0) + 1
    from . import governor
    cap = governor.effective_cap(c.st.settings)
    paused = d["paused_until"] if d["paused_until"] > time.time() else 0
    when = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
    out = [f"product: {(c.st.product or {}).get('name', '(not set up)')}   stats: {c.st.stats}",
           f"today: {d['count']}/{cap} DMs   active hours {c.st.settings['active_start']}-{c.st.settings['active_end']} "
           f"({'now' if governor.in_active_hours(c.st.settings) else 'closed'})   paused until: {when(paused) if paused else '-'}",
           f"leads: {by or 'none'}"]
    out += [f"  {time.strftime('%H:%M:%S', time.localtime(e['t']))} {e['level']:5} {e['msg']}" for e in c.st.logs[-8:]]
    return {"stats": c.st.stats, "leads": by, "daily": d, "cap": cap, "out": "\n".join(out)}


STATE_OPS: dict[str, Callable[[Ctx, dict], dict]] = {
    "status": status, "settings_set": settings_set, "product_show": product_show, "product_set": product_set,
    "subs_edit": subs_edit, "template_show": template_show, "template_set": template_set, "template_spin": template_spin,
    "template_undo": template_undo, "template_preview": template_preview, "leads_list": leads_list, "lead_write": lead_write,
    "lead_edit": lead_edit, "lead_approve": lead_approve, "lead_skip": lead_set_status("skipped"),
    "lead_replied": lead_set_status("replied"), "leads_import": leads_import, "leads_clear": leads_clear,
    "leads_export": leads_export, "opps_list": opps_list, "opp_skip": opp_skip, "ideas_generate": ideas_generate,
    "ideas_list": ideas_list, "idea_posted": idea_posted, "posts_list": posts_list, "brain_backfill": brain_backfill,
    "team_show": team_show, "team_pull": team_pull, "team_publish": team_publish,
}
# jobs that need a browser page; the Agent runs them
BROWSER_OPS = ("run", "setup", "discover", "send", "post", "replies", "scan", "submit", "doctor")


def run_state_op(c: Ctx, name: str, args: dict | None = None) -> dict:
    fn = STATE_OPS.get(name)
    if not fn:
        raise KeyError(name)
    return fn(c, args or {})


def run_browser_op(agent, name: str, args: dict | None = None) -> dict:
    a = args or {}
    if name == "run":
        agent.run(a.get("mode", "find"), a.get("minutes"))
        return {"out": "session finished"}
    if name == "setup":
        agent.analyze(a["url"], a.get("extra", ""))
        if not a.get("no_discover"):
            agent.discover()
        return {"out": ops_summary(agent)}
    if name == "discover":
        agent.discover()
        return {"out": ops_summary(agent)}
    if name == "send":
        agent.send_lead_now(a["username"])
        return {"out": f"sent to u/{a['username']}"}
    if name == "post":
        agent.post_opportunity(a["id"])
        return {"out": "comment posted"}
    if name == "replies":
        n = agent.check_replies()
        return {"out": f"{n} new repl{'y' if n == 1 else 'ies'}"}
    if name == "scan":
        added = agent.scan_page(bool(a.get("no_score")), a.get("url"))
        return {"added": added, "out": f"{added} added to the queue"}
    if name == "doctor":
        r = agent.doctor()
        return {"failed": r["failed"], "out": r["out"]}
    if name == "submit":
        url = agent.submit_idea(a["id"], bool(a.get("force")))
        return {"url": url, "out": f"posted: {url}"}
    raise KeyError(name)


def ops_summary(agent) -> str:
    p = agent.st.product or {}
    lines = [f"product: {p.get('name', '?')} — {p.get('icp', '')}"]
    lines += [f"  {'on ' if s['enabled'] else 'off'} r/{s['name']:24} score {s['score']:>3}  rules:{s['rules_risk']:6} {s['reason'][:60]}"
              for s in agent.st.subreddits]
    return "\n".join(lines)
