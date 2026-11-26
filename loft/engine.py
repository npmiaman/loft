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
        return json.loads(self.meta_path.read_text(encoding="utf-8"))["version"] if self.meta_path.exists() else -1

    def set_version(self, v: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps({"version": v}), encoding="utf-8")


EXIT_IP_URLS = ("https://api64.ipify.org", "https://ifconfig.co/ip", "https://icanhazip.com")


def proxy_url_of(proxy: dict) -> str:
    from urllib.parse import quote
    scheme, rest = proxy["server"].split("://", 1)
    if proxy.get("username"):
        return f"{scheme}://{quote(proxy['username'], safe='')}:{quote(proxy.get('password', ''), safe='')}@{rest}"
    return proxy["server"]


def expand_proxy_placeholders(proxy: Optional[dict], pid: int) -> Optional[dict]:
    """Fill per-profile placeholders in a proxy's username/server, in place.

    This is how a proxynet gateway (or any rotating/session proxy) gives each
    Loft profile its own STABLE exit: set the profile's proxy to the gateway with
    a username like  country-us-session-loft-{pid}  (or just  session-loft-{pid}),
    and each profile resolves to its own sticky session while sharing one gateway
    config. {pid} -> the profile id; {session} -> loft-<pid>.

    proxynet routes by proxy username: 'session-<id>' is sticky (rendezvous-hashed
    to a node), 'country-<cc>-session-<id>' pins a country, 'node-<name>' a node."""
    if not proxy:
        return proxy
    subs = {"{pid}": str(pid), "{session}": f"loft-{pid}"}
    for field in ("username", "server"):
        v = proxy.get(field)
        if isinstance(v, str):
            for k, r in subs.items():
                v = v.replace(k, r)
            proxy[field] = v
    return proxy


def check_proxy(proxy: dict, timeout: float = 15, samples: int = 3) -> dict:
    """Exit IP as *sites* see it, plus latency. Uses dual-stack endpoints: a proxy that reaches dual-stack
    sites over IPv6 must report its IPv6 exit, or WebRTC (set from this IP) won't match the page IP."""
    import time
    import httpx
    errors = []
    for url in EXIT_IP_URLS:
        t0 = time.time()
        try:
            r = httpx.get(url, proxy=proxy_url_of(proxy), timeout=timeout)
            ip = r.text.strip()
            if r.status_code == 200 and ip and len(ip) < 60:
                ms = int((time.time() - t0) * 1000)
                seen = {ip}
                for _ in range(samples - 1):  # a proxy whose exit changes between connections can't be matched
                    try:
                        seen.add(httpx.get(url, proxy=proxy_url_of(proxy), timeout=timeout).text.strip())
                    except Exception:
                        pass
                out = {"ok": True, "ip": ip, "ipv6": ":" in ip, "ms": ms, "stable": len(seen) == 1}
                if len(seen) > 1:
                    out["warning"] = (f"exit IP changes between connections ({', '.join(sorted(seen))}); sites will "
                                      f"see different IPs. Fix the proxy (e.g. make it use only IPv4).")
                return out
            errors.append(f"{url}: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {str(e)[:120]}")
    return {"ok": False, "error": "; ".join(errors)}


# Countries whose desktop browsers mostly run in English even though another language is official/most spoken.
ENGLISH_BROWSER_COUNTRIES = {"IN", "PK", "BD", "NG", "KE", "GH", "PH", "SG", "MY", "ZA", "LK", "NP", "UG", "TZ", "ZW"}


def country_locale(cc: str, seed: str = "") -> list[str]:
    """Browser languages for a profile appearing from country `cc`.

    Camoufox picks a random locale on every launch, weighted by who *speaks* each language
    (it gave de-NL for a Dutch IP), so one profile showed different languages across sessions.
    Here: the country's official language (deterministic per profile when there are several),
    plus English the way real browsers list it."""
    import hashlib
    import xml.etree.ElementTree as ET
    from importlib.resources import files
    cc = cc.upper()
    if cc in ENGLISH_BROWSER_COUNTRIES:
        return [f"en-{cc}", "en"]
    root = ET.parse(files("camoufox") / "territoryInfo.xml").getroot()
    terr = root.find(f".//territory[@type='{cc}']")
    langs = []
    if terr is not None:
        for lp in terr.findall("languagePopulation"):
            if lp.get("officialStatus") in ("official", "de_facto_official"):
                code = lp.get("type").split("_")[0]
                langs.append((float(lp.get("populationPercent", 0)), {"gsw": "de"}.get(code, code)))
    if not langs:
        return ["en-US", "en"]
    langs.sort(reverse=True)
    top = [l for p, l in langs if p >= langs[0][0] * 0.5] or [langs[0][1]]  # e.g. CH: de/fr, BE: nl/fr
    lang = top[int(hashlib.sha256(f"{seed}:{cc}".encode()).hexdigest(), 16) % len(top)]
    out = [f"{lang}-{cc}", lang]
    if lang != "en":
        out += ["en-US", "en"]
    return out


def host_os() -> str:
    import sys
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


def host_font_families() -> set[str]:
    """Font families installed on this machine (cached for a day)."""
    import subprocess
    import sys
    import time
    cache = HOME / "host-fonts.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            return set(json.loads(cache.read_text(encoding="utf-8")))
    except Exception:
        pass
    fams: set[str] = set()
    try:
        if sys.platform == "darwin":
            try:
                from AppKit import NSFontManager  # pyobjc, present with the desktop extra
                fams = set(NSFontManager.sharedFontManager().availableFontFamilies())
            except ImportError:
                out = subprocess.run(["system_profiler", "SPFontsDataType", "-json"], capture_output=True,
                                     text=True, timeout=120).stdout
                for f in json.loads(out).get("SPFontsDataType", []):
                    for face in f.get("typefaces", []):
                        if face.get("family"):
                            fams.add(face["family"])
        elif sys.platform == "win32":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            for i in range(winreg.QueryInfoKey(k)[1]):
                name = winreg.EnumValue(k, i)[0].split(" (")[0]
                fams.add(name.replace(" Bold", "").replace(" Italic", "").replace(" Regular", "").strip())
        else:
            out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=60).stdout
            for line in out.splitlines():
                fams.update(x.strip() for x in line.split(",") if x.strip())
    except Exception:
        return set()
    if fams:
        HOME.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(sorted(fams)), encoding="utf-8")
    return fams


def native_fonts(os_name: str) -> Optional[list[str]]:
    """For a profile whose OS is this machine's OS: the fonts this machine really has.

    Camoufox claims only its 'essential' list for native identities, which hides system fonts
    every real install shows (on this Mac: Herculanum, Kefa, SignPainter). Pixelscan's server
    compares the measured list with the OS's core set and reports 'Masking detected'
    (osFontsStatus=false) -- measured 2026-11-27; with the real list it reports no masking."""
    from importlib.resources import files
    key = {"macos": "mac", "windows": "win", "linux": "lin"}[os_name]
    data = files("camoufox")
    known = set(json.loads((data / "fonts.json").read_text(encoding="utf-8"))[key])
    essential = set(json.loads((data / "essential-fonts.json").read_text(encoding="utf-8"))[key])
    host = host_font_families()
    if not host:
        return None
    return sorted((host & known) | essential)


def default_canvas(os_name: str) -> str:
    """real when the profile's OS is this machine's (needed to pass checkers like Pixelscan,
    whose WebGL red-box test catches any canvas noise); noise otherwise (a foreign OS can't
    show this machine's real rendering)."""
    return "real" if os_name == host_os() else "noise"


def camoufox_options(info: dict, user_data_dir: Path, headless: bool = False) -> dict:
    proxy = info.get("proxy")
    opts = dict(
        persistent_context=True,
        user_data_dir=str(user_data_dir),
        fingerprint=info["fingerprint"],
        headless=headless,
        # With a proxy, derive timezone/locale/geolocation from the proxy's exit IP so they match it.
        geoip=bool(proxy),  # replaced by the measured exit IP in open_profile when the proxy check works
        # noise: canvas/WebGL readback is random, so profiles on one machine can't be linked by canvas.
        # real: this machine's true rendering (stable, stock-Firefox-like, but shared by every same-OS profile here).
        canvas_noise=info.get("canvas", "noise") != "real",
        i_know_what_im_doing=True,  # the fingerprint is pinned on purpose: that's what keeps a profile stable
        # One IP per profile. On dual-stack networks (e.g. Airtel) pages arrive over IPv6 while WebRTC shows
        # the IPv4 address, and checkers read two IPs as "hiding your real IP" (BrowserScan -10%, measured).
        firefox_user_prefs={"network.dns.disableIPv6": True},
    )
    if not opts["canvas_noise"] and info.get("os") != host_os():
        raise LoftError(400, f"canvas 'real' needs a {host_os()} profile on this machine; this one is {info.get('os')}")
    if info.get("os") == host_os():
        fonts = native_fonts(info["os"])
        if fonts:
            opts["fonts"] = fonts  # replaces Camoufox's list (it is an allowlist, not an addition)
    if proxy:
        opts["proxy"] = {k: v for k, v in proxy.items() if k in ("server", "username", "password", "bypass") and v}
    return opts


def prepare_launch(info: dict, user_data_dir: Path, headless: bool = False, seed: str = "",
                   log: Callable[[str], None] = print) -> dict:
    """Camoufox options for a profile, with a live proxy check: the measured exit IP drives
    timezone, geolocation and WebRTC, and the browser language is the exit country's, stable per profile."""
    opts = camoufox_options(info, user_data_dir, headless)
    if info.get("proxy"):
        chk = check_proxy(info["proxy"])
        if not chk["ok"]:
            raise LoftError(502, f"proxy not working: {chk['error']}")
        opts["geoip"] = chk["ip"]
        log(f"proxy exit {chk['ip']} ({chk['ms']} ms)")
        if chk.get("warning"):
            log("WARNING: " + chk["warning"])
        try:
            from camoufox.geolocation import get_geolocation
            opts["locale"] = country_locale(get_geolocation(chk["ip"]).locale.region, seed=seed)
        except Exception as e:
            log(f"locale lookup failed ({e}); using Camoufox's")
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
    expand_proxy_placeholders(info.get("proxy"), pid)
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
        opts = prepare_launch(info, local.data, headless, seed=str(pid), log=log)
        log(f"launching '{info['name']}'" + (f" via {info['proxy']['server']}" if info.get("proxy") else ""))
        err: Optional[BaseException] = None
        with Camoufox(**opts) as context:
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


# ---------------------------------------------------------------- team backup
BACKUP_MAGIC = b"LFTT"


def export_team(client: Client, tid: int, passphrase: str, log: Callable[[str], None] = print) -> bytes:
    """One passphrase-encrypted archive of the whole team: every profile (settings,
    fingerprint, proxy, latest browser data) and the templates. Restorable into any team.

    A passphrase is required -- this file holds every logged-in session the team has."""
    if not passphrase:
        raise LoftError(400, "a team backup needs a passphrase (it contains every session)")
    profiles = client.profiles(tid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(profiles, 1):
            info = client.launch_info(p["id"])
            manifest = {"name": info["name"], "os": info["os"], "notes": info["notes"], "tags": info["tags"],
                        "start_url": info["start_url"], "canvas": info.get("canvas", "noise"),
                        "fingerprint": info["fingerprint"], "proxy": info.get("proxy")}
            z.writestr(f"profiles/{p['id']}/manifest.json", json.dumps(manifest))
            if p["version"] > 0:
                _, data = client.download(p["id"], tid=tid)
                if data:
                    z.writestr(f"profiles/{p['id']}/data.zip", data)
            log(f"backed up profile {i}/{len(profiles)}: {info['name']}")
        z.writestr("templates.json", json.dumps(client.templates(tid)))
        z.writestr("backup.json", json.dumps({"format": "loft-team/1", "profiles": len(profiles),
                                              "at": __import__("time").time()}))
    salt = os.urandom(16)
    return BACKUP_MAGIC + salt + crypto.encrypt(crypto.passphrase_key(passphrase, salt), buf.getvalue(), "backup")


def import_team(client: Client, tid: int, blob: bytes, passphrase: str,
                log: Callable[[str], None] = print) -> dict:
    """Restore a team backup into team `tid`. Creates fresh profiles (new ids); does not delete
    or merge existing ones. Templates are recreated. Returns a summary."""
    if blob[:4] != BACKUP_MAGIC:
        raise LoftError(400, "not a Loft team backup")
    try:
        raw = crypto.decrypt(crypto.passphrase_key(passphrase, blob[4:20]), blob[20:], "backup")
    except Exception:
        raise LoftError(400, "wrong passphrase")
    created = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        manifests = sorted(n for n in names if n.endswith("/manifest.json"))
        for i, mp in enumerate(manifests, 1):
            m = json.loads(z.read(mp))
            p = client.create_profile(tid, name=m["name"], os=m["os"], notes=m.get("notes", ""),
                                      tags=m.get("tags", []), start_url=m.get("start_url", ""),
                                      canvas=m.get("canvas", "noise"), fingerprint=m["fingerprint"],
                                      proxy=m.get("proxy"))
            data_path = mp.rsplit("/", 1)[0] + "/data.zip"
            if data_path in names:
                lk = client.lock(p["id"])["lock"]
                try:
                    client.upload(p["id"], lk, 0, z.read(data_path), tid=tid)
                finally:
                    client.unlock(p["id"], lk)
            created.append(p["id"])
            log(f"restored profile {i}/{len(manifests)}: {m['name']}")
        tmpls = 0
        if "templates.json" in names:
            existing = {t["name"] for t in client.templates(tid)}
            for t in json.loads(z.read("templates.json")):
                if t["name"] not in existing:
                    client.add_template(tid, t["name"], t["settings"])
                    tmpls += 1
    return {"profiles": len(created), "profile_ids": created, "templates": tmpls}


# ---------------------------------------------------------------- bulk
def bulk_create(client: Client, tid: int, count: int, *, name: str = "Profile {n}", os_name: str = "windows",
                proxies: Optional[list[dict]] = None, start_n: int = 1, **fields) -> list[dict]:
    """Create `count` profiles, each with its own fingerprint. Proxies are assigned round-robin."""
    if count < 1 or count > 500:
        raise LoftError(400, "count must be 1-500")
    out = []
    fields.setdefault("canvas", default_canvas(os_name))
    for i in range(count):
        proxy = proxies[i % len(proxies)] if proxies else None
        out.append(client.create_profile(tid, name=name.replace("{n}", str(start_n + i)), os=os_name,
                                         fingerprint=new_fingerprint(os_name), proxy=proxy, **fields))
    return out
