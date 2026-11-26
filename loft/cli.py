"""loft CLI: server, runner, account, team, profile, cookie, bundle, template and automation commands."""
from __future__ import annotations

import argparse
import getpass
import json
import runpy
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .client import Client, LoftError, load_config, save_config


class BadInput(ValueError):
    """User input we can't use (CLI prints it, the desktop API returns 400)."""


def parse_proxy(url: str | None) -> dict | None:
    """http://user:pass@host:port | socks5://host:port -> Playwright proxy dict."""
    if not url:
        return None
    u = urlsplit(url if "://" in url else "http://" + url)
    if not u.hostname or not u.port:
        raise BadInput(f"bad proxy {url!r}; use scheme://user:pass@host:port")
    out = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        out["username"] = u.username
        out["password"] = u.password or ""
    return out


def proxy_url(p: dict | None) -> str:
    if not p:
        return ""
    if p.get("username"):
        scheme, rest = p["server"].split("://", 1)
        return f"{scheme}://{p['username']}:{p.get('password', '')}@{rest}"
    return p["server"]


def read_proxies(path: str | None) -> list[dict] | None:
    if not path:
        return None
    lines = [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    return [parse_proxy(l) for l in lines] or None


def _team(c: Client, cfg: dict, override: int | None = None) -> int:
    if override:
        return override
    if cfg.get("team"):
        return cfg["team"]
    teams = c.me()["teams"]
    if not teams:
        raise SystemExit("you are in no team: `loft team create NAME` or `loft join CODE`")
    cfg = load_config()
    cfg["team"] = teams[0]["id"]
    save_config(cfg)
    return cfg["team"]


def _set_team(tid: int) -> None:
    cfg = load_config()
    cfg["team"] = tid
    save_config(cfg)


def _uid(c: Client, tid: int, email: str) -> int:
    for m in c.members(tid):
        if m["email"] == email.lower():
            return m["id"]
    raise SystemExit(f"{email} is not in this team")


def _runner(c: Client, tid: int, name: str | None) -> dict:
    rs = c.runners(tid)
    if not rs:
        raise SystemExit("no cloud runners: an admin adds one with `loft runner add NAME URL KEY`")
    if name:
        for r in rs:
            if r["name"] == name:
                return r
        raise SystemExit(f"no runner named {name}")
    return rs[0]


def _print_profiles(ps: list) -> None:
    if not ps:
        print("no profiles")
        return
    print(f"{'ID':>4}  {'NAME':24} {'OS':8} {'CANVAS':6} {'VER':>4}  {'ACCESS':6} {'PROXY':28} STATUS")
    for p in ps:
        run = f"running: {p['running']['by']} @ {p['running']['device']}" if p["running"] else ""
        proxy = (p["proxy"] or {}).get("server", "-")
        print(f"{p['id']:>4}  {p['name'][:24]:24} {p['os']:8} {p.get('canvas', 'noise'):6} {p['version']:>4}  "
              f"{p['access']:6} {proxy[:28]:28} {run}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="loft", description="Team browser profiles on Camoufox")
    ap.add_argument("--team", type=int, help="team id (default: saved/first team)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("server", help="run the cloud server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=4400)
    s.add_argument("--data", default=None, help="data dir (default ~/.loft-server)")
    s.add_argument("--db", help="postgresql://user:pass@host/db (default: SQLite in the data dir)")
    s.add_argument("--blobs", help="s3://bucket/prefix (default: files in the data dir; LOFT_S3_ENDPOINT for non-AWS)")
    s.add_argument("--tls-cert")
    s.add_argument("--tls-key")
    s.add_argument("--self-signed", metavar="HOSTS", help="comma-separated names/IPs for a generated cert")
    s.add_argument("--insecure", action="store_true", help="allow plain HTTP on a non-loopback address")

    a = sub.add_parser("app", help="open the desktop app")
    a.add_argument("--port", type=int, default=4410)
    a.add_argument("--browser", action="store_true", help="use your browser instead of a native window")
    a.add_argument("--no-window", action="store_true", help="serve only (automation API), open nothing")

    for name in ("register", "login"):
        x = sub.add_parser(name)
        x.add_argument("email")
        x.add_argument("--server")
        x.add_argument("--ca", help="CA/cert PEM to trust for a self-signed server")
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
    k = sub.add_parser("kick")
    k.add_argument("email")
    au = sub.add_parser("audit")
    au.add_argument("-n", type=int, default=40)

    sub.add_parser("ls")
    n = sub.add_parser("new", help="create a profile with a fresh stable fingerprint")
    n.add_argument("name")
    n.add_argument("--os", default=None, choices=["windows", "macos", "linux"],
                   help="default: this machine's OS, which can use real canvas + real fonts (passes Pixelscan)")
    n.add_argument("--proxy", help="scheme://user:pass@host:port")
    n.add_argument("--start-url", default=None)
    n.add_argument("--notes", default="")
    n.add_argument("--tag", action="append", default=None)
    n.add_argument("--canvas", choices=["noise", "real"], default=None)
    n.add_argument("--template", help="template name to start from")
    b = sub.add_parser("bulk", help="create many profiles")
    b.add_argument("count", type=int)
    b.add_argument("--name", default="Profile {n}", help="name pattern, {n} = number")
    b.add_argument("--start", type=int, default=1)
    b.add_argument("--os", default=None, choices=["windows", "macos", "linux"])
    b.add_argument("--proxies", help="file with one proxy URL per line, assigned round-robin")
    b.add_argument("--tag", action="append", default=[])
    b.add_argument("--start-url", default="")
    e = sub.add_parser("edit")
    e.add_argument("id", type=int)
    e.add_argument("--name")
    e.add_argument("--proxy")
    e.add_argument("--no-proxy", action="store_true")
    e.add_argument("--start-url")
    e.add_argument("--notes")
    e.add_argument("--canvas", choices=["noise", "real"])
    run = sub.add_parser("run", help="launch a profile; syncs back when you close it")
    run.add_argument("id", type=int)
    run.add_argument("--headless", action="store_true")
    ex = sub.add_parser("exec", help="run a Python automation script against a profile (gets `context`)")
    ex.add_argument("id", type=int)
    ex.add_argument("script")
    ex.add_argument("--headless", action="store_true")
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

    ck = sub.add_parser("cookies", help="cookies import ID FILE | cookies export ID [FILE]")
    ck.add_argument("action", choices=["import", "export"])
    ck.add_argument("id", type=int)
    ck.add_argument("file", nargs="?")
    xp = sub.add_parser("export", help="export a profile to a .loftprofile bundle")
    xp.add_argument("id", type=int)
    xp.add_argument("file")
    xp.add_argument("--no-passphrase", action="store_true", help="write it unencrypted (contains cookies!)")
    ip = sub.add_parser("import", help="import a .loftprofile bundle as a new profile")
    ip.add_argument("file")
    ip.add_argument("--name")

    bk = sub.add_parser("backup", help="admin: encrypted backup of the whole team to a file")
    bk.add_argument("file")
    rs = sub.add_parser("restore", help="restore a team backup into the current team (adds profiles)")
    rs.add_argument("file")
    sub.add_parser("overview", help="admin dashboard: counts, storage, who's running, key health")

    tp = sub.add_parser("template", help="template ls | add NAME [opts] | rm ID")
    tp.add_argument("action", choices=["ls", "add", "rm"])
    tp.add_argument("value", nargs="?")
    tp.add_argument("--os", choices=["windows", "macos", "linux"], default="windows")
    tp.add_argument("--proxy")
    tp.add_argument("--start-url", default="")
    tp.add_argument("--tag", action="append", default=[])
    tp.add_argument("--canvas", choices=["noise", "real"], default="noise")

    rn = sub.add_parser("runner", help="runner serve | add NAME URL KEY | ls | rm ID")
    rn.add_argument("action", choices=["serve", "add", "ls", "rm"])
    rn.add_argument("args", nargs="*")
    rn.add_argument("--host", default="127.0.0.1")
    rn.add_argument("--port", type=int, default=4420)
    rn.add_argument("--key", help="runner API key (serve); generated if omitted")
    rn.add_argument("--tls-cert")
    rn.add_argument("--tls-key")
    rn.add_argument("--self-signed", metavar="HOSTS")
    rn.add_argument("--insecure", action="store_true")
    cr = sub.add_parser("cloud", help="run a profile on a cloud runner and print the viewer link")
    cr.add_argument("id", type=int)
    cr.add_argument("--runner")

    sub.add_parser("rotate-key", help="admin: new team key, re-encrypt everything, re-key remaining members")
    sub.add_parser("apikey", help="show the local automation API key (desktop app /v1)")
    sub.add_parser("fetch-browser", help="download the Camoufox browser (~1.3 GB)")
    return ap


def _serve(app, host, port, cert, key, self_signed, insecure, data_dir: Path):
    import uvicorn
    from .tls import check_bind, fingerprint, self_signed as make_cert
    if self_signed:
        cert, key = map(str, make_cert(data_dir / "tls", [h.strip() for h in self_signed.split(",")]))
        print(f"self-signed cert: {cert}\n  sha256 {fingerprint(Path(cert))}\n  clients: --ca {cert}")
    check_bind(host, bool(cert), insecure)
    uvicorn.run(app, host=host, port=port, ssl_certfile=cert, ssl_keyfile=key)


def main(argv=None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd is None:  # double-clicked desktop build
        args = ap.parse_args(["app"])
    cfg = load_config()

    if args.cmd == "server":
        from .server import create_app
        data = Path(args.data or "~/.loft-server").expanduser()
        _serve(create_app(data, db_url=args.db, blobs_url=args.blobs), args.host, args.port, args.tls_cert, args.tls_key, args.self_signed, args.insecure, data)
        return
    if args.cmd == "app":
        from .desktop import serve
        serve(args.port, native=not args.browser and not args.no_window, open_browser=not args.no_window)
        return
    if args.cmd == "fetch-browser":
        from camoufox.__main__ import cli
        sys.argv = ["camoufox", "fetch"]
        cli()
        return
    if args.cmd == "runner" and args.action == "serve":
        from .runner import create_runner
        key = args.key or secrets.token_urlsafe(24)
        if not args.key:
            print(f"runner key (give to an admin for `loft runner add`): {key}")
        _serve(create_runner(key), args.host, args.port, args.tls_cert, args.tls_key, args.self_signed,
               args.insecure, Path("~/.loft-runner").expanduser())
        return
    if args.cmd == "apikey":
        from .desktop import api_key
        print(api_key())
        return

    if args.cmd in ("register", "login"):
        server = (args.server or cfg.get("server") or "http://127.0.0.1:4400").rstrip("/")
        c = Client(server=server, token="", persist=True, ca=args.ca)
        pw = getpass.getpass("password: ")
        if args.cmd == "register":
            out = c.register(args.email, pw, args.name, args.invite)
        else:
            out = c.login(args.email, pw)
        cfg = load_config()
        cfg["team"] = out.get("team_id")
        save_config(cfg)
        print(f"signed in to {server} as {args.email}")
        return

    c = Client(persist=True)
    if not c.token:
        raise SystemExit("not signed in: `loft login EMAIL --server URL`")

    if args.cmd == "whoami":
        me = c.me()
        print(f"{me['email']} on {c.server}")
        for t in me["teams"]:
            lock = "" if t["id"] in c.keys else "  (key not on this device yet)"
            print(f"  team {t['id']}: {t['name']} ({t['role']}){'  <- current' if t['id'] == cfg.get('team') else ''}{lock}")
        return
    if args.cmd == "teams":
        for t in c.me()["teams"]:
            print(f"{t['id']:>4}  {t['name']}  ({t['role']})")
        return
    if args.cmd == "team":
        tid = c.create_team(args.value)["id"] if args.action == "create" else int(args.value)
        _set_team(tid)
        print(f"current team: {tid}")
        return
    if args.cmd == "join":
        out = c.accept(args.code)
        _set_team(out["team_id"])
        print(f"joined team {out['team_id']} as {out['role']}")
        return
    if args.cmd == "import":
        from .engine import import_bundle
        tid = _team(c, cfg, args.team)
        blob = Path(args.file).read_bytes()
        pw = getpass.getpass("bundle passphrase (empty if none): ") if blob[:4] == b"LFTB" else None
        p = import_bundle(c, tid, blob, pw, args.name)
        print(f"imported as profile {p['id']}: {p['name']} (v{p['version']})")
        return

    tid = _team(c, cfg, args.team)
    if args.cmd == "invite":
        print(f"invite code ({args.role}, 7 days, single use):\n  {c.invite(tid, args.role)['code']}\n"
              f"It contains the team's encryption key: send it privately.")
    elif args.cmd == "members":
        for m in c.members(tid):
            print(f"{m['id']:>4}  {m['email']:30} {m['role']}")
    elif args.cmd == "role":
        c.set_role(tid, _uid(c, tid, args.email), args.role)
        print("ok")
    elif args.cmd == "kick":
        c.remove_member(tid, _uid(c, tid, args.email))
        print("ok. They still hold the current team key: run `loft rotate-key` to lock them out.")
    elif args.cmd == "audit":
        for a in c.audit(tid, args.n):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(a["at"]))
            pid = f" #{a['profile_id']}" if a["profile_id"] else ""
            print(f"{ts}  {a['email'] or '-':24} {a['action']}{pid} {a['detail']}")
    elif args.cmd == "ls":
        _print_profiles(c.profiles(tid))
    elif args.cmd == "new":
        from .engine import default_canvas, new_fingerprint
        base: dict = {}
        if args.template:
            match = [t for t in c.templates(tid) if t["name"] == args.template]
            if not match:
                raise SystemExit(f"no template named {args.template}")
            base = match[0]["settings"]
        from .engine import host_os
        os_name = args.os or base.get("os") or host_os()
        proxy = parse_proxy(args.proxy) if args.proxy else parse_proxy(base.get("proxy"))
        p = c.create_profile(tid, name=args.name, os=os_name, proxy=proxy,
                             start_url=args.start_url if args.start_url is not None else base.get("start_url", ""),
                             notes=args.notes, tags=args.tag if args.tag is not None else base.get("tags", []),
                             canvas=args.canvas or base.get("canvas") or default_canvas(os_name),
                             fingerprint=new_fingerprint(os_name))
        print(f"created profile {p['id']}: {p['name']} ({p['os']}, canvas {p['canvas']})")
    elif args.cmd == "bulk":
        from .engine import bulk_create
        from .engine import host_os
        ps = bulk_create(c, tid, args.count, name=args.name, os_name=args.os or host_os(), proxies=read_proxies(args.proxies),
                         start_n=args.start, tags=args.tag, start_url=args.start_url)
        print(f"created {len(ps)} profiles: {ps[0]['id']}..{ps[-1]['id']}")
    elif args.cmd == "edit":
        fields = {k: v for k, v in dict(name=args.name, start_url=args.start_url, notes=args.notes,
                                        canvas=args.canvas).items() if v is not None}
        if args.no_proxy:
            fields["clear_proxy"] = True
        elif args.proxy:
            fields["proxy"] = parse_proxy(args.proxy)
        _print_profiles([c.patch_profile(args.id, **fields)])
    elif args.cmd == "run":
        from .engine import run_profile
        run_profile(c, args.id, headless=args.headless)
    elif args.cmd == "exec":
        from .engine import open_profile
        with open_profile(c, args.id, headless=args.headless) as context:
            runpy.run_path(args.script, init_globals={"context": context, "profile": context.loft_info},
                           run_name="__main__")
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
    elif args.cmd == "cookies":
        from .engine import export_cookies, import_cookies, parse_cookies
        if args.action == "import":
            if not args.file:
                raise SystemExit("cookies import ID FILE")
            out = import_cookies(c, args.id, parse_cookies(Path(args.file).read_text()))
            print(f"imported {out['cookies']} cookies (synced v{out['version']})")
        else:
            data = json.dumps(export_cookies(c, args.id), indent=2)
            if args.file:
                Path(args.file).write_text(data)
                Path(args.file).chmod(0o600)
                print(f"wrote {args.file}")
            else:
                print(data)
    elif args.cmd == "export":
        from .engine import export_bundle
        pw = None
        if not args.no_passphrase:
            pw = getpass.getpass("passphrase for the bundle: ")
            if pw != getpass.getpass("again: "):
                raise SystemExit("passphrases differ")
        Path(args.file).write_bytes(export_bundle(c, args.id, pw))
        Path(args.file).chmod(0o600)
        print(f"wrote {args.file}")
    elif args.cmd == "template":
        if args.action == "ls":
            for t in c.templates(tid):
                st = dict(t["settings"])
                if st.get("proxy"):
                    st["proxy"] = parse_proxy(st["proxy"])["server"]
                print(f"{t['id']:>4}  {t['name']:20} {json.dumps(st)}")
        elif args.action == "add":
            if not args.value:
                raise SystemExit("template add NAME [--os --proxy --start-url --tag --canvas]")
            parse_proxy(args.proxy)  # validate
            c.add_template(tid, args.value, {"os": args.os, "proxy": args.proxy or "", "start_url": args.start_url,
                                             "tags": args.tag, "canvas": args.canvas})
            print("ok")
        else:
            c.delete_template(int(args.value))
            print("ok")
    elif args.cmd == "runner":
        if args.action == "add":
            if len(args.args) != 3:
                raise SystemExit("runner add NAME URL KEY")
            c.add_runner(tid, *args.args)
            print("ok")
        elif args.action == "ls":
            for r in c.runners(tid):
                print(f"{r['id']:>4}  {r['name']:16} {r['url']}")
        elif args.action == "rm":
            c.delete_runner(int(args.args[0]))
            print("ok")
    elif args.cmd == "rotate-key":
        from .client import rotate_team_key
        pv = c.rotation_preview(tid)
        if pv["running"]:
            raise SystemExit("stop running profiles first: " +
                             ", ".join(f"{r['name']} ({r['device']})" for r in pv["running"]))
        print(f"Rotating the key for team {tid} will:")
        print(f"  - re-encrypt {pv['profiles']} profile(s) and all templates")
        print(f"  - re-key {len(pv['rekey'])} member(s): {', '.join(m['email'] for m in pv['rekey']) or '(none)'}")
        print(f"  - revoke {pv['invites_to_revoke']} unused invite(s)")
        if pv["without_key"]:
            print(f"  - LEAVE WITHOUT ACCESS (no keypair yet): {', '.join(m['email'] for m in pv['without_key'])}")
            print("    (they must be re-invited after rotation)")
        if input("Proceed? [y/N] ").strip().lower() != "y":
            raise SystemExit("aborted")
        out = rotate_team_key(c, tid)
        print(f"done: re-keyed {len(out['rekeyed'])} members; {out['invites_revoked']} unused invites revoked")
        if out["without_key"]:
            print(f"no key for users {out['without_key']}: re-invite them")
    elif args.cmd == "backup":
        from .engine import export_team
        pw = getpass.getpass("backup passphrase: ")
        if pw != getpass.getpass("again: "):
            raise SystemExit("passphrases differ")
        Path(args.file).write_bytes(export_team(c, tid, pw))
        Path(args.file).chmod(0o600)
        print(f"wrote {args.file}")
    elif args.cmd == "restore":
        from .engine import import_team
        pw = getpass.getpass("backup passphrase: ")
        out = import_team(c, tid, Path(args.file).read_bytes(), pw)
        print(f"restored {out['profiles']} profile(s) and {out['templates']} template(s) into team {tid}")
    elif args.cmd == "overview":
        o = c.overview(tid)
        mb = o["bytes"] / (1024 * 1024)
        print(f"Team {tid}  (key epoch {o['key_epoch']}{', ROTATING' if o['rotating'] else ''})")
        print(f"  profiles: {o['profiles']}   members: {o['members']}   open invites: {o['open_invites']}")
        print(f"  storage:  {o['versions']} snapshots, {mb:.1f} MB")
        print(f"  running:  " + (", ".join(f"{r['name']} ({r['email']} @ {r['device']})" for r in o["running"]) or "none"))
        if o["members_without_key"]:
            print(f"  NO KEY:   {', '.join(o['members_without_key'])} (can't open profiles; re-invite)")
        if o["last_rotation"]:
            print(f"  last key rotation: {time.strftime('%Y-%m-%d %H:%M', time.localtime(o['last_rotation']))}")
    elif args.cmd == "cloud":
        from .runner import start_cloud_session
        out = start_cloud_session(c, args.id, _runner(c, tid, args.runner))
        print(f"running in the cloud. Open on any device:\n  {out['view']}\nThe link is the key to this session: "
              f"don't share it.")


def entry() -> None:
    try:
        main()
    except LoftError as e:
        print(f"error: {e.detail}", file=sys.stderr)
        sys.exit(1)
    except BadInput as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except ModuleNotFoundError as e:
        if e.name and e.name.split(".")[0] in ("camoufox", "pywebview", "webview"):
            print("error: this command needs the browser. Install it with:  pip install 'loft[client]'  "
                  "(or 'loft[desktop]' for the app).", file=sys.stderr)
            sys.exit(3)
        raise
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    entry()
