"""`loft pigeon ...`: all of Pigeon's functionality, from the terminal.

Every command works two ways. If the profile is open on this machine (`loft run ID`), it talks to that live session over
its control channel. Otherwise it locks the profile, does the work (opening a browser only if it needs one) and syncs."""
from __future__ import annotations

import sys
import time

from .. import control
from ..client import LoftError
from .runner import central_for, make_llm, with_agent, with_state
from .state import load_key, save_key
from . import ops


def register(sub) -> None:
    p = sub.add_parser("pigeon", help="Reddit outreach agent: runs inside a profile, shares one memory across the team")
    ps = p.add_subparsers(dest="pcmd", required=True)

    def cmd(name, help, *args, headless=False):
        x = ps.add_parser(name, help=help)
        if name not in ("config", "brain", "run-all", "team", "accounts"):
            x.add_argument("id", type=int)
        for a in args:
            x.add_argument(*a[0], **a[1])
        if headless:
            x.add_argument("--headless", action="store_true", help="when the command has to open its own browser")
        return x

    A = lambda *n, **k: (n, k)
    cmd("config", "DeepSeek key (stored in ~/.loft/pigeon.json, never synced)", A("--deepseek-key"),
        A("--model", choices=["deepseek-flash", "deepseek-v4-pro"]))
    cmd("status", "stats, today's cap, pauses, agent state")
    # --- running the agent
    cmd("run", "open the profile's own browser and run a session (unattended)", A("--mode", choices=["full", "find", "dm"], default="find"),
        A("--minutes", type=float), A("--pace", type=float), headless=True)
    cmd("run-all", "run the agent across many accounts, ONE AT A TIME (each gets a full session, then the next)",
        A("ids", nargs="*", type=int, metavar="ID", help="profile ids, in order (or use --all / --tag)"),
        A("--all", action="store_true", help="every profile in the team you can run"), A("--tag", action="append", default=[]),
        A("--mode", choices=["full", "find", "dm"], default="full"), A("--minutes", type=float, help="per account"),
        A("--pace", type=float), A("--gap-min", type=float, default=30, help="seconds to wait between accounts (min)"),
        A("--gap-max", type=float, default=120, help="seconds to wait between accounts (max)"),
        A("--rounds", type=int, default=1), A("--round-gap", type=float, default=30, help="minutes between rounds"),
        A("--max-total", type=float, help="minutes: don't start new accounts after this"), A("--shuffle", action="store_true"),
        A("--stop-on-error", action="store_true"), A("--dry-run", action="store_true", help="show what would run, launch nothing"),
        A("--strict-proxies", action="store_true", help="refuse to start if two accounts would share an exit IP"),
        headless=True)
    cmd("accounts", "the team's Reddit accounts, each bound to ONE unique proxy: add --proxies FILE | ls | check",
        A("action", choices=["add", "ls", "check"]),
        A("--proxies", metavar="FILE", help="add: one proxy per line (user:pass@host:port or a full URL); one account is created per line"),
        A("--name", default="Reddit {n}", help="add: account name pattern, {n} = number"), A("--start", type=int, default=1),
        A("--os", choices=["windows", "macos", "linux"], help="add: default is this machine's OS"),
        A("--tag", action="append", default=None, help="add: tags for the new accounts (default: reddit). ls/check: only these tags"),
        A("--no-check", action="store_true", help="add: don't test the proxies before creating accounts"),
        A("--allow-duplicate-exits", action="store_true", help="add: create them even if two proxies exit from the same IP"))
    cmd("attach", "connect the agent to a profile that is already open (`loft run ID`)", A("--mode", choices=["full", "find", "dm"], default="find"),
        A("--minutes", type=float), A("--pace", type=float, help="0.5 (slow) .. 2 (fast)"), A("--follow", action="store_true"))
    cmd("detach", "stop the agent in an open profile (the browser stays open)")
    # --- setup
    cmd("setup", "read the product site, build the ICP, find and score subreddits", A("url"), A("--extra", default=""),
        A("--no-discover", action="store_true"), headless=True)
    cmd("discover", "re-run subreddit discovery (keeps your on/off choices)", headless=True)
    cmd("product", "show or edit the product profile", A("--set", nargs="*", default=[], metavar="FIELD=VALUE",
                                                         help="lists use | between items, e.g. competitors='A|B'"))
    cmd("subs", "list subreddits; enable, disable, add, remove", A("--enable", action="append", default=[]),
        A("--disable", action="append", default=[]), A("--add", action="append", default=[]), A("--remove", action="append", default=[]))
    cmd("set", "change settings, e.g. daily_dm_cap=20 min_fit=65 my_username=you", A("pairs", nargs="*", metavar="KEY=VALUE"),
        A("--restart-warmup", action="store_true"))
    # --- messages
    cmd("template", "show or change the DM template", A("--set", metavar="TEXT"), A("--spin", action="store_true", help="add spintax with AI"),
        A("--intensity", choices=["light", "medium", "heavy"], default="medium"), A("--undo", action="store_true"),
        A("--preview", type=int, nargs="?", const=4, metavar="N"))
    # --- leads
    cmd("leads", "list leads, or export them", A("--status", choices=["queued", "draft", "ready", "sent", "replied", "skipped", "failed"]),
        A("--export", metavar="FILE", help="write CSV ('-' for stdout)"))
    cmd("write", "write the message for a lead (AI or your template)", A("username", nargs="?"), A("--all", action="store_true", help="every queued/failed lead"))
    cmd("edit", "replace a lead's message with your own", A("username"), A("--message", required=True))
    cmd("approve", "approve drafts so they can be sent", A("username", nargs="?"), A("--all", action="store_true"), A("--message"))
    cmd("skip", "skip a lead", A("username"))
    cmd("replied", "mark a sent lead as replied", A("username"))
    cmd("import", "List DMer: queue usernames from a file or stdin ('-')", A("file"))
    cmd("clear", "delete ALL leads", A("--yes", action="store_true"))
    cmd("send", "DM one lead now (bypasses the queue, not the governor or the team brain)", A("username"), headless=True)
    cmd("scan", "Page DMer: queue everyone on the Reddit tab you have open (or --url)", A("--url"), A("--no-score", action="store_true"), headless=True)
    cmd("replies", "scan Reddit chat for replies to sent DMs", headless=True)
    cmd("doctor", "read-only check that Reddit's markup matches what Pigeon expects on this account (run it first on a new account)", headless=True)
    # --- comments and posts
    cmd("opps", "comment opportunities: list, --post ID to publish, --skip ID", A("--post", metavar="ID"), A("--skip", metavar="ID"), headless=True)
    cmd("ideas", "post ideas: list, --generate, --submit ID (the agent posts it), --posted ID (you posted it yourself), --history",
        A("--generate", action="store_true"), A("--posted", metavar="ID"), A("--submit", metavar="ID", help="the agent submits this idea as a text post"),
        A("--force", action="store_true", help="--submit: ignore the daily post cap and a high-risk subreddit warning"),
        A("--history", action="store_true", help="posts the agent has submitted"), headless=True)
    # --- the shared memory
    cmd("team", "the team's shared setup and limits: show | pull ID | publish ID | limits | accounts",
        A("action", choices=["show", "pull", "publish", "limits", "accounts"]), A("id", nargs="?", type=int),
        A("--force", action="store_true", help="pull: adopt even if not newer"),
        A("--dm-per-day", type=int, help="limits: max DMs per rolling 24h across ALL accounts (0 = no limit)"),
        A("--sub-dm-per-day", type=int, help="limits: max DMs per subreddit per 24h across all accounts (0 = no limit)"),
        A("--comment-per-day", type=int, help="limits: max comments per 24h across all accounts (0 = no limit)"),
        A("--post-per-day", type=int, help="limits: max posts per 24h across all accounts (0 = no limit)"),
        A("--sub-post-per-day", type=int, help="limits: max posts per subreddit per 24h across all accounts (0 = no limit)"))
    cmd("backfill", "record this account's past DMs and comments in the team memory (so no other account repeats them)")
    cmd("brain", "the team's shared memory: who was DMed, where we commented and posted (across every account)",
            A("--days", type=float, default=14), A("--events", action="store_true", help="list recent events"),
            A("--kind", choices=["dm", "comment", "post"]), A("--sub"), A("--limit", type=int, default=30),
            A("--who", nargs="+", metavar="USERNAME", help="has the team contacted these people?"),
            A("--release", type=int, metavar="EVENT_ID", help="admin: free a stuck claim"))


# ------------------------------------------------------------------ execution: live session or standalone
def _show(res: dict) -> None:
    out = res.get("out")
    if out:
        print(out)


def run_state(c, pid: int, op: str, args: dict | None = None) -> dict:
    args = args or {}
    if control.is_live(pid):
        return control.request(pid, "POST", "/pigeon/op", {"op": op, "args": args}, timeout=130)

    def go(st):
        try:
            central = central_for(c, pid)
        except RuntimeError:
            central = None  # read-only context: carry on without the shared memory
        return ops.run_state_op(ops.Ctx(st, make_llm(st), central), op, args)
    try:
        return with_state(c, pid, go)
    except KeyError:
        raise SystemExit(f"unknown operation {op}")


def _follow(pid: int, since: float) -> dict:
    """Print the live session's log lines as they appear until its job finishes. Returns the job's result."""
    seen = since
    while True:
        s = control.request(pid, "GET", "/pigeon/status", timeout=10)
        for e in s["log"]:
            if e["t"] > seen:
                seen = e["t"]
                print(f"  {time.strftime('%H:%M:%S', time.localtime(e['t']))} {e['level']:5} {e['msg']}", flush=True)
        if s["state"] == "idle":
            return s.get("last") or {}
        time.sleep(1)


def run_browser(c, pid: int, kind: str, args: dict, headless: bool = False) -> dict:
    if control.is_live(pid):
        t0 = time.time()
        control.request(pid, "POST", "/pigeon/job", {"kind": kind, "args": args})
        last = _follow(pid, t0)
        if not last.get("ok"):
            raise SystemExit(f"{kind} failed: {last.get('error')}")
        return last
    if kind == "scan" and not args.get("url"):
        raise SystemExit("scan reads the Reddit tab you have open: start the profile with `loft run ID` first, or pass --url")
    out: list = []
    with_agent(c, pid, lambda a: out.append(ops.run_browser_op(a, kind, args)), headless=headless)
    return out[0] if out else {}


def _pairs(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        k, eq, v = p.partition("=")
        if not eq:
            raise SystemExit(f"expected KEY=VALUE, got {p!r}")
        out[k] = v
    return out


def handle(args, c, get_tid=None) -> None:
    cmd, pid = args.pcmd, getattr(args, "id", None)

    if cmd == "config":
        if args.deepseek_key is None and args.model is None:
            key, model = load_key()
            print(f"deepseek key: {'set (' + key[:5] + '…)' if key else 'not set'}   model: {model or '(profile default)'}")
        else:
            save_key(args.deepseek_key, args.model)
            print("ok")
        return

    if cmd == "brain":
        return _brain(args, c, get_tid())
    if cmd == "run-all":
        return _run_all(args, c, get_tid)
    if cmd == "team":
        return _team_cmd(args, c, get_tid)
    if cmd == "accounts":
        return _accounts_cmd(args, c, get_tid)

    live = control.is_live(pid)
    if cmd == "status":
        if live:
            s = control.request(pid, "GET", "/pigeon/status")
            job = f" ({s['job']}{' ' + s['mode'] if s['mode'] else ''}, since {time.strftime('%H:%M', time.localtime(s['since']))})" if s["state"] != "idle" else ""
            print(f"agent: {s['state']}{job}   team brain: {'connected' if s['central'] else 'OFF'}")
            if s.get("last"):
                print(f"last job: {s['last']['kind']} → {'ok' if s['last']['ok'] else 'failed: ' + str(s['last'].get('error'))}")
        _show(run_state(c, pid, "status"))
    elif cmd == "run":
        if live:
            raise SystemExit(f"profile {pid} is already open here: use `loft pigeon attach {pid}` to run the agent in it")
        print(f"starting. From another terminal: `loft pigeon status {pid}` to watch, `loft pigeon detach {pid}` to stop it cleanly.")
        run_browser(c, pid, "run", dict(mode=args.mode, minutes=args.minutes, pace=args.pace), args.headless)
    elif cmd == "attach":
        t0 = time.time()
        control.request(pid, "POST", "/pigeon/start", dict(mode=args.mode, minutes=args.minutes, pace=args.pace))
        print(f"Pigeon attached to profile {pid} ({args.mode}). `loft pigeon status {pid}` to watch, `loft pigeon detach {pid}` to stop.")
        if args.follow:
            last = _follow(pid, t0)
            print("finished" if last.get("ok") else f"ended: {last.get('error')}")
    elif cmd == "detach":
        print(f"Pigeon is {control.request(pid, 'POST', '/pigeon/stop')['state']}")
    elif cmd == "setup":
        _show(run_browser(c, pid, "setup", dict(url=args.url, extra=args.extra, no_discover=args.no_discover), args.headless))
    elif cmd == "discover":
        _show(run_browser(c, pid, "discover", {}, args.headless))
    elif cmd == "send":
        _show(run_browser(c, pid, "send", dict(username=args.username), args.headless))
    elif cmd == "replies":
        _show(run_browser(c, pid, "replies", {}, args.headless))
    elif cmd == "doctor":
        _show(run_browser(c, pid, "doctor", {}, args.headless))
    elif cmd == "scan":
        _show(run_browser(c, pid, "scan", dict(url=args.url, no_score=args.no_score), args.headless))
    elif cmd == "opps":
        if args.post:
            _show(run_browser(c, pid, "post", dict(id=args.post), args.headless))
        elif args.skip:
            _show(run_state(c, pid, "opp_skip", dict(id=args.skip)))
        else:
            _show(run_state(c, pid, "opps_list"))
    elif cmd == "product":
        _show(run_state(c, pid, "product_set", dict(fields=_pairs(args.set))) if args.set else run_state(c, pid, "product_show"))
    elif cmd == "subs":
        _show(run_state(c, pid, "subs_edit", dict(enable=args.enable, disable=args.disable, add=args.add, remove=args.remove)))
    elif cmd == "set":
        _show(run_state(c, pid, "settings_set", dict(pairs=_pairs(args.pairs), restart_warmup=args.restart_warmup)))
    elif cmd == "template":
        if args.set is not None:
            _show(run_state(c, pid, "template_set", dict(body=args.set)))
        elif args.spin:
            _show(run_state(c, pid, "template_spin", dict(intensity=args.intensity)))
        elif args.undo:
            _show(run_state(c, pid, "template_undo"))
        elif args.preview:
            _show(run_state(c, pid, "template_preview", dict(n=args.preview)))
        else:
            _show(run_state(c, pid, "template_show"))
    elif cmd == "leads":
        if args.export:
            csv_text = run_state(c, pid, "leads_export")["csv"]
            (sys.stdout.write(csv_text) if args.export == "-" else open(args.export, "w", encoding="utf-8", newline="").write(csv_text))
            if args.export != "-":
                print(f"wrote {args.export}")
        else:
            _show(run_state(c, pid, "leads_list", dict(status=args.status)))
    elif cmd == "write":
        if not args.all and not args.username:
            raise SystemExit("give a username or --all")
        _show(run_state(c, pid, "lead_write", dict(username=args.username, all=args.all)))
    elif cmd == "edit":
        _show(run_state(c, pid, "lead_edit", dict(username=args.username, message=args.message)))
    elif cmd == "approve":
        if not args.all and not args.username:
            raise SystemExit("give a username or --all")
        _show(run_state(c, pid, "lead_approve", dict(username=args.username, all=args.all, message=args.message)))
    elif cmd == "skip":
        _show(run_state(c, pid, "lead_skip", dict(username=args.username)))
    elif cmd == "replied":
        _show(run_state(c, pid, "lead_replied", dict(username=args.username)))
    elif cmd == "import":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        _show(run_state(c, pid, "leads_import", dict(text=text)))
    elif cmd == "clear":
        if not args.yes:
            raise SystemExit("this deletes every lead; pass --yes")
        _show(run_state(c, pid, "leads_clear"))
    elif cmd == "backfill":
        _show(run_state(c, pid, "brain_backfill"))
    elif cmd == "ideas":
        if args.submit:
            _show(run_browser(c, pid, "submit", dict(id=args.submit, force=args.force), args.headless))
        elif args.history:
            _show(run_state(c, pid, "posts_list"))
        elif args.posted:
            _show(run_state(c, pid, "idea_posted", dict(id=args.posted)))
        else:
            _show(run_state(c, pid, "ideas_generate" if args.generate else "ideas_list"))


def _brain(args, c, tid: int) -> None:
    from .central import CentralBrain
    b = CentralBrain(c, tid, None)
    when = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
    if args.release:
        c.pigeon_finish(tid, args.release, "failed", "released by admin")
        print(f"released event {args.release}")
    elif args.who:
        hits = b.seen("dm", args.who)
        for n in args.who:
            h = hits.get(n)
            print(f"{n:24} " + (f"{h['status']} by {h.get('profile') or '?'} on {when(h['at'])}" + (f" in r/{h['subreddit']}" if h["subreddit"] else "") if h else "not contacted"))
    elif args.events:
        evs = b.events(kind=args.kind, subreddit=args.sub, days=args.days, limit=args.limit)
        for e in evs:
            what = e["target"] if e["kind"] == "dm" else (e["title"] or e["target"])
            print(f"{when(e['at'])}  {e['kind']:7} {e['status']:8} {(e['profile'] or '-')[:16]:16} r/{(e['subreddit'] or '-')[:18]:18} {what[:60]}")
        if not evs:
            print("no events")
    else:
        s = b.summary(args.days)
        k = s["by_kind"]
        print(f"last {args.days:g} days, all accounts: {k.get('dm', 0)} DMs · {k.get('reply', 0)} replies · {k.get('comment', 0)} comments · "
              f"{k.get('post', 0)} posts · {k.get('upvote', 0)} upvotes · {k.get('lead', 0)} leads queued"
              + (f"   ⚠ {s['stuck_claims']} claim(s) stuck >1h (`--release ID`)" if s["stuck_claims"] else ""))
        lim, used = s.get("limits") or {}, s.get("last_24h") or {}
        if lim:
            print("team limits (rolling 24h): " + ", ".join(f"{n}={v}" for n, v in lim.items())
                  + f"   used: {used.get('dm', 0)} DMs, {used.get('comment', 0)} comments")
        hdr = f"{'':22} {'DMs':>4} {'RPLY':>5} {'CMTS':>5} {'POSTS':>5} {'UPV':>4}  LAST"
        row = lambda name, r: f"{name:22} {r['dms']:>4} {r['replies']:>5} {r['comments']:>5} {r['posts']:>5} {r['upvotes']:>4}  {when(r['last_at'])}"
        if s["by_subreddit"]:
            print("\n" + hdr.replace(" " * 22, "SUBREDDIT".ljust(22), 1))
            for r in s["by_subreddit"]:
                print(row(f"r/{r['subreddit'][:20]}", r))
        if s["by_profile"]:
            print("\n" + hdr.replace(" " * 22, "ACCOUNT".ljust(22), 1))
            for r in s["by_profile"]:
                print(row((r["profile"] or "-")[:22], r))


def _team_cmd(args, c, get_tid) -> None:
    from .central import CentralBrain
    act = args.action
    if act in ("pull", "publish") or (act == "show" and args.id):
        if not args.id:
            raise SystemExit(f"which account? loft pigeon team {act} ID")
        op = {"pull": "team_pull", "publish": "team_publish", "show": "team_show"}[act]
        _show(run_state(c, args.id, op, dict(force=args.force)))
        return
    tid = get_tid()
    b = CentralBrain(c, tid, None)
    if act == "show":
        v, data = b.team_config()
        lim = data.get("limits") or {}
        print(f"team setup v{v}: product {(data.get('product') or {}).get('name', '-')}, {len(data.get('subreddits') or [])} subreddits, "
              f"template {'yes' if data.get('template') else '-'}\nlimits: " + (", ".join(f"{k}={v}" for k, v in lim.items()) or "none"))
    elif act == "accounts":
        rows = c.pigeon_accounts(tid)
        for r in rows:
            print(f"u/{r['username']:24} profile #{r['profile_id']} {r['profile'] or ''}")
        if not rows:
            print("no accounts registered yet (they register themselves when the agent runs, or: loft pigeon set ID my_username=NAME)")
    elif act == "limits":
        names = {"team_dm_per_day": args.dm_per_day, "subreddit_dm_per_day": args.sub_dm_per_day, "team_comment_per_day": args.comment_per_day,
                 "team_post_per_day": args.post_per_day, "subreddit_post_per_day": args.sub_post_per_day}
        v, data = b.team_config()
        data = dict(data)
        lim = dict(data.get("limits") or {})
        if all(x is None for x in names.values()):
            print("limits: " + (", ".join(f"{k}={x}" for k, x in lim.items()) or "none") + "\n(set with --dm-per-day --sub-dm-per-day --comment-per-day --post-per-day --sub-post-per-day; 0 removes one)")
            return
        for k, x in names.items():
            if x is not None:
                lim.pop(k, None) if x == 0 else lim.__setitem__(k, x)
        try:
            nv = b.put_limits(lim, v)
        except Exception as e:
            raise SystemExit(f"couldn't save limits: {getattr(e, 'detail', e)} (owners and admins only)")
        print(f"saved as team setup v{nv}; limits: " + (", ".join(f"{k}={x}" for k, x in lim.items()) or "none"))


def _run_all(args, c, get_tid) -> None:
    from . import rotate as rot
    profs = c.profiles(get_tid())
    names = {p["id"]: p["name"] for p in profs}
    if args.ids:
        pids = list(dict.fromkeys(args.ids))
        for pid in pids:
            if pid not in names:
                raise SystemExit(f"no such profile: {pid}")
    elif args.all or args.tag:
        pids = [p["id"] for p in profs if (not args.tag or set(args.tag) & set(p["tags"]))]
    else:
        raise SystemExit("say which accounts: profile ids, --tag TAG, or --all")
    if not pids:
        raise SystemExit("no matching profiles")
    if args.gap_min > args.gap_max:
        raise SystemExit("--gap-min is larger than --gap-max")

    def launch(pid: int) -> dict:
        box: dict = {}

        def go(a):
            before = dict(a.st.stats)
            try:
                a.run(args.mode, args.minutes)
            finally:
                box["delta"] = {k: a.st.stats[k] - before.get(k, 0) for k in a.st.stats}
        with_agent(c, pid, go, headless=args.headless, pace=args.pace, reraise_interrupt=True)
        return box.get("delta", {})

    warnings = rot.proxy_warnings([p for p in profs if p["id"] in pids])
    for w in warnings:
        print(f"⚠ {w}")
    if warnings and args.strict_proxies:
        raise SystemExit("not starting (--strict-proxies): fix the shared exits above")
    print(f"{len(pids)} account(s), one at a time, mode={args.mode}" + (" (dry run)" if args.dry_run else ""))
    results = rot.rotate(
        pids, mode=args.mode, peek=lambda pid: rot.peek_state(c, pid), launch=launch, is_open=control.is_live, names=names,
        gap=(args.gap_min, args.gap_max), rounds=args.rounds, round_gap=args.round_gap * 60,
        max_total=args.max_total * 60 if args.max_total else None, shuffle=args.shuffle, stop_on_error=args.stop_on_error,
        dry_run=args.dry_run)
    print("\n" + rot.summary(results))


def _accounts_cmd(args, c, get_tid) -> None:
    """Every Reddit account is its own Loft profile with its own fingerprint and ONE proxy of its own, stored encrypted with the team.
    Whoever can run an account runs it through that proxy, so nobody sets proxies up per person."""
    from ..cli import read_proxies
    from ..engine import bulk_create, check_proxy, expand_proxy_placeholders, host_os
    tid = get_tid()
    key = lambda px: (px["server"], px.get("username") or "")

    if args.action == "add":
        if not args.proxies:
            raise SystemExit("give a file of proxies, one per line: --proxies FILE")
        proxies = read_proxies(args.proxies)
        if not proxies:
            raise SystemExit("no proxies found in that file")
        seen: dict = {}
        for n, px in enumerate(proxies, 1):
            if key(px) in seen:
                raise SystemExit(f"line {n} repeats line {seen[key(px)]} ({px['server']}, user {px.get('username') or '-'}): "
                                 "two accounts can't share a proxy, they would share an exit IP")
            seen[key(px)] = n
        if not args.no_check:
            print(f"checking {len(proxies)} proxies...")
            exits: dict[str, int] = {}
            bad = checked = 0
            for n, px in enumerate(proxies, 1):
                if "{" in (px.get("username") or "") + px["server"]:
                    print(f"  {n}. {px['server']} user {px.get('username')}: has a {{pid}}/{{session}} placeholder, checked after creation (`accounts check`)")
                    continue
                r = check_proxy(px, samples=1)
                if not r["ok"]:
                    bad += 1
                    print(f"  {n}. {px['server']} user {px.get('username') or '-'}: NOT WORKING: {r['error'][:90]}")
                    continue
                checked += 1
                dup = exits.get(r["ip"])
                exits.setdefault(r["ip"], n)
                print(f"  {n}. {px.get('username') or px['server']}: exit {r['ip']} ({r['ms']} ms)" + (f"   SAME EXIT AS #{dup}" if dup else ""))
            if bad:
                raise SystemExit(f"{bad} proxy(ies) didn't work: fix them, or pass --no-check to create the accounts anyway")
            if len(exits) < checked and not args.allow_duplicate_exits:
                raise SystemExit("two proxies exit from the same IP, so those accounts would be linked. Replace one, or --allow-duplicate-exits")
        made = bulk_create(c, tid, len(proxies), name=args.name, os_name=args.os or host_os(), proxies=proxies, start_n=args.start,
                           tags=args.tag or ["reddit"], start_url="https://www.reddit.com/")
        for p in made:
            print(f"  #{p['id']:<4} {p['name']}")
        print(f"created {len(made)} accounts, each bound to its own proxy and shared with the team.\n"
              f"  one account:   loft pigeon run {made[0]['id']} --mode full        (or `loft run {made[0]['id']}` then `loft pigeon attach {made[0]['id']}`)\n"
              f"  all of them:   loft pigeon run-all --tag {(args.tag or ['reddit'])[0]} --mode full      (one browser at a time, in turn)\n"
              "  first, log each in to Reddit once by hand (`loft run ID`), then `loft pigeon doctor ID`.")
        return

    profs = [p for p in c.profiles(tid) if not args.tag or set(args.tag) & set(p["tags"])]
    if args.action == "ls":
        reddit = {a["profile_id"]: a["username"] for a in c.pigeon_accounts(tid)}
        print(f"{'ID':>4}  {'NAME':22} {'PROXY (server, user)':44} {'REDDIT USER':18} STATUS")
        for p in profs:
            px = p.get("proxy")
            where = f"{px['server']} {px.get('username') or ''}" if px else "none (this machine's IP!)"
            r = p.get("running")
            print(f"{p['id']:>4}  {p['name'][:22]:22} {where[:44]:44} {('u/' + reddit[p['id']]) if p['id'] in reddit else '-':18} "
                  + (f"running: {r['by']} @ {r['device']}" if r else ""))
        if not profs:
            print("no accounts")
        return

    # check: really connect through each proxy and compare the exits
    exits: dict[str, list[str]] = {}
    problems = 0
    for p in profs:
        px = expand_proxy_placeholders(dict(p["proxy"]), p["id"]) if p.get("proxy") else None
        if not px:
            print(f"  #{p['id']:<4} {p['name'][:22]:22} no proxy: it would use this machine's own IP")
            problems += 1
            continue
        r = check_proxy(px, samples=1)
        if not r["ok"]:
            print(f"  #{p['id']:<4} {p['name'][:22]:22} NOT WORKING: {r['error'][:80]}")
            problems += 1
            continue
        exits.setdefault(r["ip"], []).append(f"{p['name']} (#{p['id']})")
        print(f"  #{p['id']:<4} {p['name'][:22]:22} exit {r['ip']} ({r['ms']} ms)")
    for ip, who in exits.items():
        if len(who) > 1:
            problems += 1
            print(f"  ⚠ {', '.join(who)} all exit from {ip}: Reddit would link those accounts")
    print(f"{len(profs)} account(s), {len(exits)} distinct exit IP(s)" + ("" if not problems else f", {problems} problem(s)"))
