"""loft CLI: server, account, team and profile commands."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from urllib.parse import urlsplit

from .client import Client, LoftError, load_config, save_config


def parse_proxy(url: str | None) -> dict | None:
    """http://user:pass@host:port | socks5://host:port -> Playwright proxy dict."""
    if not url:
        return None
    u = urlsplit(url if "://" in url else "http://" + url)
    if not u.hostname or not u.port:
        raise SystemExit(f"bad proxy {url!r}; use scheme://user:pass@host:port")
    out = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        out["username"] = u.username
        out["password"] = u.password or ""
    return out


def _team(c: Client, cfg: dict, override: int | None = None) -> int:
    if override:
        return override
    if cfg.get("team"):
        return cfg["team"]
    teams = c.me()["teams"]
    if not teams:
        raise SystemExit("you are in no team: `loft team create NAME` or `loft join CODE`")
    cfg["team"] = teams[0]["id"]
    save_config(cfg)
    return cfg["team"]


def _uid(c: Client, tid: int, email: str) -> int:
    for m in c.members(tid):
        if m["email"] == email.lower():
            return m["id"]
    raise SystemExit(f"{email} is not in this team")


def _print_profiles(ps: list) -> None:
    if not ps:
        print("no profiles")
        return
    print(f"{'ID':>4}  {'NAME':24} {'OS':8} {'VER':>4}  {'ACCESS':6} {'PROXY':28} STATUS")
    for p in ps:
        run = f"running: {p['running']['by']} @ {p['running']['device']}" if p["running"] else ""
        proxy = (p["proxy"] or {}).get("server", "-")
        print(f"{p['id']:>4}  {p['name'][:24]:24} {p['os']:8} {p['version']:>4}  {p['access']:6} {proxy[:28]:28} {run}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="loft", description="Team browser profiles on Camoufox")
    ap.add_argument("--team", type=int, help="team id (default: saved/first team)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("server", help="run the cloud server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=4400)
    s.add_argument("--data", default=None, help="data dir (default ~/.loft-server)")

    a = sub.add_parser("app", help="open the desktop app (local web UI)")
    a.add_argument("--port", type=int, default=4410)
    a.add_argument("--no-open", action="store_true")

    for name in ("register", "login"):
        x = sub.add_parser(name)
        x.add_argument("email")
        x.add_argument("--server")
        if name == "register":
            x.add_argument("--name", default="")
            x.add_argument("--invite")
    sub.add_parser("whoami")
    sub.add_parser("teams")
    t = sub.add_parser("team", help="team create NAME | team use ID")
    t.add_argument("action", choices=["create", "use"])
    t.add_argument("value")
    i = sub.add_parser("invite")
    i.add_argument("--role", default="member", choices=["member", "admin"])
    j = sub.add_parser("join")
    j.add_argument("code")
    sub.add_parser("members")
    r = sub.add_parser("role")
    r.add_argument("email")
    r.add_argument("role", choices=["owner", "admin", "member"])
    rm_m = sub.add_parser("kick")
    rm_m.add_argument("email")
    au = sub.add_parser("audit")
    au.add_argument("-n", type=int, default=40)

    sub.add_parser("ls")
    n = sub.add_parser("new", help="create a profile with a fresh stable fingerprint")
    n.add_argument("name")
    n.add_argument("--os", default="windows", choices=["windows", "macos", "linux"])
    n.add_argument("--proxy", help="scheme://user:pass@host:port")
    n.add_argument("--start-url", default="")
    n.add_argument("--notes", default="")
    n.add_argument("--tag", action="append", default=[])
    e = sub.add_parser("edit")
    e.add_argument("id", type=int)
    e.add_argument("--name")
    e.add_argument("--proxy")
    e.add_argument("--no-proxy", action="store_true")
    e.add_argument("--start-url")
    e.add_argument("--notes")
    run = sub.add_parser("run", help="launch a profile; syncs back to the cloud when you close it")
    run.add_argument("id", type=int)
    run.add_argument("--headless", action="store_true")
    sh = sub.add_parser("share")
    sh.add_argument("id", type=int)
    sh.add_argument("email")
    sh.add_argument("--level", default="run", choices=["run", "edit"])
    us = sub.add_parser("unshare")
    us.add_argument("id", type=int)
    us.add_argument("email")
    ul = sub.add_parser("unlock", help="admin: force-release a stuck run lock")
    ul.add_argument("id", type=int)
    d = sub.add_parser("rm")
    d.add_argument("id", type=int)
    v = sub.add_parser("versions")
    v.add_argument("id", type=int)

    args = ap.parse_args(argv)
    cfg = load_config()

    if args.cmd == "server":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(args.data), host=args.host, port=args.port)
        return
    if args.cmd == "app":
        from .desktop import serve
        serve(args.port, open_browser=not args.no_open)
        return

    if args.cmd in ("register", "login"):
        if args.server:
            cfg["server"] = args.server
        c = Client(server=cfg["server"], token="")
        pw = getpass.getpass("password: ")
        out = c.register(args.email, pw, args.name, args.invite) if args.cmd == "register" else c.login(args.email, pw)
        cfg.update(token=out["token"], team=None)
        save_config(cfg)
        print(f"signed in to {cfg['server']} as {args.email}")
        return

    c = Client()
    if not c.token:
        raise SystemExit("not signed in: `loft login EMAIL --server URL`")

    if args.cmd == "whoami":
        me = c.me()
        print(f"{me['email']} on {c.server}")
        for t in me["teams"]:
            print(f"  team {t['id']}: {t['name']} ({t['role']}){'  <- current' if t['id'] == cfg.get('team') else ''}")
    elif args.cmd == "teams":
        for t in c.me()["teams"]:
            print(f"{t['id']:>4}  {t['name']}  ({t['role']})")
    elif args.cmd == "team":
        if args.action == "create":
            t = c.create_team(args.value)
            cfg["team"] = t["id"]
        else:
            cfg["team"] = int(args.value)
        save_config(cfg)
        print(f"current team: {cfg['team']}")
    elif args.cmd == "join":
        out = c.accept(args.code)
        cfg["team"] = out["team_id"]
        save_config(cfg)
        print(f"joined team {out['team_id']} as {out['role']}")
    else:
        tid = _team(c, cfg, args.team)
        if args.cmd == "invite":
            print(f"invite code ({args.role}, 7 days): {c.invite(tid, args.role)['code']}")
        elif args.cmd == "members":
            for m in c.members(tid):
                print(f"{m['id']:>4}  {m['email']:30} {m['role']}")
        elif args.cmd == "role":
            c.set_role(tid, _uid(c, tid, args.email), args.role)
            print("ok")
        elif args.cmd == "kick":
            c.remove_member(tid, _uid(c, tid, args.email))
            print("ok")
        elif args.cmd == "audit":
            for a in c.audit(tid, args.n):
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(a["at"]))
                pid = f" #{a['profile_id']}" if a["profile_id"] else ""
                print(f"{ts}  {a['email'] or '-':24} {a['action']}{pid} {a['detail']}")
        elif args.cmd == "ls":
            _print_profiles(c.profiles(tid))
        elif args.cmd == "new":
            from .engine import new_fingerprint
            p = c.create_profile(tid, name=args.name, os=args.os, proxy=parse_proxy(args.proxy),
                                 start_url=args.start_url, notes=args.notes, tags=args.tag,
                                 fingerprint=new_fingerprint(args.os))
            print(f"created profile {p['id']}: {p['name']} ({p['os']})")
        elif args.cmd == "edit":
            fields = {k: v for k, v in dict(name=args.name, start_url=args.start_url, notes=args.notes).items() if v is not None}
            if args.no_proxy:
                fields["clear_proxy"] = True
            elif args.proxy:
                fields["proxy"] = parse_proxy(args.proxy)
            _print_profiles([c.patch_profile(args.id, **fields)])
        elif args.cmd == "run":
            from .engine import run_profile
            run_profile(c, args.id, headless=args.headless)
        elif args.cmd == "share":
            c.share(args.id, _uid(c, tid, args.email), args.level)
            print("ok")
        elif args.cmd == "unshare":
            c.unshare(args.id, _uid(c, tid, args.email))
            print("ok")
        elif args.cmd == "unlock":
            c.unlock(args.id, force=True)
            print("ok")
        elif args.cmd == "rm":
            c.delete_profile(args.id)
            print("ok")
        elif args.cmd == "versions":
            print(json.dumps(c.versions(args.id), indent=2))


def entry() -> None:
    try:
        main()
    except LoftError as e:
        print(f"error: {e.detail}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    entry()
