"""Local side of a profile: fingerprints, data packing, launch + sync, cookies, bundles."""
from __future__ import annotations

import io
import json
import os
import shutil
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import crypto
from .client import HOME, Client, LoftError

PROFILES = HOME / "profiles"

# Firefox profile paths that are caches, locks or crash dumps: never synced.
SKIP_DIRS = {"cache2", "startupCache", "thumbnails", "crashes", "minidumps", "saved-telemetry-pings",
             "datareporting", "shader-cache", "safebrowsing", "jumpListCache", "OfflineCache"}
SKIP_FILES = {"parent.lock", "lock", ".parentlock", "Telemetry.ShutdownTime.txt"}


def new_fingerprint(os_name: str = "windows") -> dict:
    """A stable identity for a new profile. Stored (encrypted) server-side, reused on every launch."""
    from camoufox.fingerprints import generate_fingerprint
    return generate_fingerprint(os=os_name)


# ---------------------------------------------------------------- packing
def pack(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or not p.is_file():
                continue
            z.write(p, rel.as_posix())
    return buf.getvalue()


def _safe_extract(z: zipfile.ZipFile, dst: Path) -> None:
    root = dst.resolve()
    for m in z.infolist():
        if not (dst / m.filename).resolve().is_relative_to(root):  # zip-slip guard
            raise ValueError(f"unsafe path in archive: {m.filename}")
    z.extractall(dst)


def unpack(data: bytes, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _safe_extract(z, dst)


class LocalProfile:
    def __init__(self, pid: int, home: Path = PROFILES):
        self.root = home / str(pid)
        self.data = self.root / "data"
        self.meta_path = self.root / "meta.json"

    @property
    def version(self) -> int:
        return json.loads(self.meta_path.read_text())["version"] if self.meta_path.exists() else -1

    def set_version(self, v: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps({"version": v}))


def host_os() -> str:
    import sys
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


def camoufox_options(info: dict, user_data_dir: Path, headless: bool = False) -> dict:
    proxy = info.get("proxy")
    opts = dict(
        persistent_context=True,
        user_data_dir=str(user_data_dir),
        fingerprint=info["fingerprint"],
        headless=headless,
        # With a proxy, derive timezone/locale/geolocation from the proxy's exit IP so they match it.
        geoip=bool(proxy),
        # noise: canvas/WebGL readback is random, so profiles on one machine can't be linked by canvas.
        # real: this machine's true rendering (stable, stock-Firefox-like, but shared by every same-OS profile here).
        canvas_noise=info.get("canvas", "noise") != "real",
        i_know_what_im_doing=True,  # the fingerprint is pinned on purpose: that's what keeps a profile stable
    )
    if not opts["canvas_noise"] and info.get("os") != host_os():
        raise LoftError(400, f"canvas 'real' needs a {host_os()} profile on this machine; this one is {info.get('os')}")
    if proxy:
        opts["proxy"] = {k: v for k, v in proxy.items() if k in ("server", "username", "password", "bypass") and v}
    return opts


# ---------------------------------------------------------------- launch + sync
@contextmanager
def open_profile(client: Client, pid: int, *, headless: bool = False, home: Path = PROFILES,
                 log: Callable[[str], None] = print, result: Optional[dict] = None) -> Iterator:
    """Lock -> pull if stale -> launch Camoufox -> yield the Playwright context -> push -> unlock.

    This is also the automation SDK:

        with open_profile(Client(persist=True), 12) as ctx:
            page = ctx.new_page(); page.goto("https://example.com")
    """
    info = client.launch_info(pid)
    if not info.get("fingerprint"):
        raise LoftError(500, "profile has no fingerprint")
    tid = info["team_id"]
    lk = client.lock(pid)
    lock_tok, server_v = lk["lock"], lk["version"]
    local = LocalProfile(pid, home)
    stop = threading.Event()

    def beat():
        while not stop.wait(30):
            try:
                client.heartbeat(pid, lock_tok)
            except Exception as e:  # keep the browser running; upload will report a lost lock
                log(f"heartbeat failed: {e}")

    try:
        if local.version != server_v or not local.data.exists():
            if server_v > 0:
                log(f"pulling v{server_v}")
                _, blob = client.download(pid, server_v, tid=tid)
                unpack(blob, local.data)
            else:
                if local.data.exists():
                    shutil.rmtree(local.data)
                local.data.mkdir(parents=True)
            local.set_version(server_v)
        threading.Thread(target=beat, daemon=True).start()

        from camoufox.sync_api import Camoufox
        log(f"launching '{info['name']}'" + (f" via {info['proxy']['server']}" if info.get("proxy") else ""))
        err: Optional[BaseException] = None
        with Camoufox(**camoufox_options(info, local.data, headless)) as context:
            context.loft_info = info  # type: ignore[attr-defined]
            try:
                yield context
            except BaseException as e:  # still sync what the session did, then re-raise
                err = e
        stop.set()

        blob = pack(local.data)
        try:
            up = client.upload(pid, lock_tok, server_v, blob, tid=tid)
            local.set_version(up["version"])
            log(f"synced v{up['version']} ({len(blob) // 1024} KB)")
            if result is not None:
                result.update(version=up["version"], bytes=len(blob))
        except LoftError as e:
            keep = local.root / f"unsynced-{server_v}.zip"
            keep.write_bytes(blob)
            log(f"upload failed ({e}); kept local copy at {keep}")
            raise
        if err is not None:
            raise err
    finally:
        stop.set()
        try:
            client.unlock(pid, lock_tok)
        except LoftError:
            pass


def run_profile(client: Client, pid: int, *, headless: bool = False,
                session: Optional[Callable] = None, log: Callable[[str], None] = print,
                home: Path = PROFILES) -> dict:
    """Interactive run: open the start URL and wait until the window closes, a stop is requested
    (stop file or Ctrl+C / SIGTERM), or `session(context)` returns. All three paths sync."""
    result: dict = {}
    stop_file = LocalProfile(pid, home).root / "stop"
    stop_file.unlink(missing_ok=True)
    stop = threading.Event()
    restore = _graceful_signals(stop)
    try:
        with open_profile(client, pid, headless=headless, home=home, log=log, result=result) as context:
            if session:
                session(context)
            else:
                closed = threading.Event()
                context.on("close", lambda *_: closed.set())
                page = context.pages[0] if context.pages else context.new_page()
                if context.loft_info.get("start_url"):
                    page.goto(context.loft_info["start_url"])
                while not closed.is_set():
                    if stop.is_set() or stop_file.exists():
                        log("stop requested: closing and syncing")
                        break
                    try:  # short waits keep Playwright's loop turning so the close event arrives
                        context.wait_for_event("close", timeout=500)
                        break
                    except Exception:
                        pass
    finally:
        restore()
        stop_file.unlink(missing_ok=True)
    return result


def request_stop(pid: int, home: Path = PROFILES) -> None:
    """Ask a running `loft run` (any process on this machine) to close and sync."""
    root = LocalProfile(pid, home).root
    root.mkdir(parents=True, exist_ok=True)
    (root / "stop").touch()


def _graceful_signals(stop: threading.Event) -> Callable[[], None]:
    """Turn Ctrl+C / SIGTERM into a stop request instead of an exception inside Playwright."""
    import signal
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    old = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            old[sig] = signal.signal(sig, lambda *_: stop.set())
    return lambda: [signal.signal(s, h) for s, h in old.items()]


# ---------------------------------------------------------------- cookies
_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def parse_cookies(text: str) -> list[dict]:
    """Accepts Playwright JSON, Cookie-Editor/EditThisCookie JSON, or Netscape cookies.txt."""
    text = text.strip()
    out: list[dict] = []
    if text.startswith("[") or text.startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("cookies", [])
        for c in data:
            ck = {"name": c["name"], "value": str(c.get("value", "")), "domain": c["domain"],
                  "path": c.get("path", "/"), "secure": bool(c.get("secure", False)),
                  "httpOnly": bool(c.get("httpOnly", False))}
            exp = c.get("expires", c.get("expirationDate"))
            if exp not in (None, -1) and not c.get("session"):
                ck["expires"] = float(exp)
            ss = c.get("sameSite")
            if isinstance(ss, str) and ss.lower() in _SAMESITE:
                ck["sameSite"] = _SAMESITE[ss.lower()]
            elif ss in ("Lax", "Strict", "None"):
                ck["sameSite"] = ss
            out.append(ck)
        return out
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            raise ValueError(f"bad cookies.txt line: {line[:60]}")
        domain, _, path, secure, expiry, name, value = parts
        ck = {"name": name, "value": value, "domain": domain, "path": path,
              "secure": secure.upper() == "TRUE", "httpOnly": http_only}
        if expiry not in ("0", ""):
            ck["expires"] = float(expiry)
        out.append(ck)
    return out


MAX_COOKIE_AGE = 399 * 86400  # browsers cap cookie lifetime at 400 days; Firefox silently drops longer ones


def clamp_cookies(cookies: list[dict]) -> list[dict]:
    import time
    limit = time.time() + MAX_COOKIE_AGE
    return [dict(c, expires=min(c["expires"], limit)) if c.get("expires") else c for c in cookies]


def import_cookies(client: Client, pid: int, cookies: list[dict], **kw) -> dict:
    """Open the profile headless, add the cookies, close: they are synced like any session."""
    cookies = clamp_cookies(cookies)
    result: dict = {}
    with open_profile(client, pid, headless=True, result=result, **kw) as ctx:
        ctx.add_cookies(cookies)
    result["cookies"] = len(cookies)
    return result


def export_cookies(client: Client, pid: int, **kw) -> list[dict]:
    with open_profile(client, pid, headless=True, **kw) as ctx:
        return ctx.cookies()


# ---------------------------------------------------------------- bundles (export / import)
BUNDLE_MAGIC = b"LFTB"


def export_bundle(client: Client, pid: int, passphrase: Optional[str] = None) -> bytes:
    """A portable .loftprofile: settings, fingerprint, proxy and the latest browser data."""
    info = client.launch_info(pid)
    _, data = client.download(pid, tid=info["team_id"])
    manifest = {"format": "loft-profile/1", "name": info["name"], "os": info["os"], "notes": info["notes"],
                "tags": info["tags"], "start_url": info["start_url"], "fingerprint": info["fingerprint"],
                "proxy": info.get("proxy")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        if data:
            z.writestr("data.zip", data)
    raw = buf.getvalue()
    if passphrase:
        salt = os.urandom(16)
        return BUNDLE_MAGIC + salt + crypto.encrypt(crypto.passphrase_key(passphrase, salt), raw, "bundle")
    return raw


def import_bundle(client: Client, tid: int, blob: bytes, passphrase: Optional[str] = None,
                  name: Optional[str] = None) -> dict:
    if blob[:4] == BUNDLE_MAGIC:
        if not passphrase:
            raise LoftError(400, "this bundle is encrypted: a passphrase is needed")
        try:
            blob = crypto.decrypt(crypto.passphrase_key(passphrase, blob[4:20]), blob[20:], "bundle")
        except Exception:
            raise LoftError(400, "wrong passphrase")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        m = json.loads(z.read("manifest.json"))
        data = z.read("data.zip") if "data.zip" in z.namelist() else None
    if m.get("format") != "loft-profile/1":
        raise LoftError(400, "not a Loft profile bundle")
    p = client.create_profile(tid, name=name or m["name"], os=m["os"], notes=m.get("notes", ""),
                              tags=m.get("tags", []), start_url=m.get("start_url", ""),
                              fingerprint=m["fingerprint"], proxy=m.get("proxy"))
    if data:
        lk = client.lock(p["id"])["lock"]
        try:
            client.upload(p["id"], lk, 0, data, tid=tid)
        finally:
            client.unlock(p["id"], lk)
    return client.profile(p["id"])


# ---------------------------------------------------------------- bulk
def bulk_create(client: Client, tid: int, count: int, *, name: str = "Profile {n}", os_name: str = "windows",
                proxies: Optional[list[dict]] = None, start_n: int = 1, **fields) -> list[dict]:
    """Create `count` profiles, each with its own fingerprint. Proxies are assigned round-robin."""
    if count < 1 or count > 500:
        raise LoftError(400, "count must be 1-500")
    out = []
    for i in range(count):
        proxy = proxies[i % len(proxies)] if proxies else None
        out.append(client.create_profile(tid, name=name.replace("{n}", str(start_n + i)), os=os_name,
                                         fingerprint=new_fingerprint(os_name), proxy=proxy, **fields))
    return out
