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

from .compat import env
from . import crypto
from .client import HOME, Client, PigeonError

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
    Pigeon profile its own STABLE exit: set the profile's proxy to the gateway with
    a username like  country-us-session-pigeon-{pid}  (or just  session-pigeon-{pid}),
    and each profile resolves to its own sticky session while sharing one gateway
    config. {pid} -> the profile id; {session} -> pigeon-<pid>.

    proxynet routes by proxy username: 'session-<id>' is sticky (rendezvous-hashed
    to a node), 'country-<cc>-session-<id>' pins a country, 'node-<name>' a node."""
    if not proxy:
        return proxy
    subs = {"{pid}": str(pid), "{session}": f"pigeon-{pid}"}
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


# English-language browsers abroad: the English variant a real user in that country would have (en-US unless the country has its own).
ENGLISH_VARIANTS = {"US": "en-US", "GB": "en-GB", "AU": "en-AU", "CA": "en-CA", "IE": "en-IE", "NZ": "en-NZ", "IN": "en-IN",
                    "ZA": "en-ZA", "SG": "en-SG", "PH": "en-PH", "PK": "en-PK", "NG": "en-NG", "KE": "en-KE", "MY": "en-MY"}


def english_locale(cc: str) -> list[str]:
    cc = (cc or "").upper()
    return [ENGLISH_VARIANTS.get(cc, "en-US"), "en"]


def browser_locale(cc: str, seed: str = "", language: Optional[str] = None) -> list[str]:
    """The browser language list for a profile appearing from country `cc`. Time zone, geolocation and region always follow the
    exit IP; the language follows `language`, which is English unless asked otherwise:
      "en" (default)   English: en-US, or the country's own English variant (en-GB, en-IN, ...)
      "country"        the country's native language, then English (e.g. it-IT, it, en-US, en)
      "it-IT" (a tag)  that language, then English"""
    language = (language or env("LANGUAGE") or "en").strip()
    if language.lower() == "en":
        return english_locale(cc)
    if language.lower() == "country":
        return country_locale(cc, seed)
    base = language.split("-")[0].lower()
    return [language, base] + ([] if base == "en" else ["en-US", "en"])


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
        raise PigeonError(400, f"canvas 'real' needs a {host_os()} profile on this machine; this one is {info.get('os')}")
    if not opts["canvas_noise"]:
        # Camoufox's own canvas cover is off in this mode, but Firefox's baseline fingerprint protection still randomizes canvas
        # readback on web pages (a fresh key per launch), so "real" wasn't stable: measured 3 distinct values over 3 launches of one
        # profile. With it off, the machine's true canvas comes through, and it is the same every launch.
        opts["firefox_user_prefs"]["privacy.baselineFingerprintingProtection"] = False
    if info.get("os") == host_os():
        fonts = native_fonts(info["os"])
        if fonts:
            opts["fonts"] = fonts  # replaces Camoufox's list (it is an allowlist, not an addition)
    if proxy:
        opts["proxy"] = {k: v for k, v in proxy.items() if k in ("server", "username", "password", "bypass") and v}
    return opts


# The voice a stock macOS picks as its default for a given language/region. Camoufox draws a voice list for the identity but marks
# none of them as the default, and macOS then fills the gap with *this machine's* default voice (e.g. Rishi [en-IN] on an
# Italian profile): a checker can read the mismatch. So Pigeon marks exactly one default itself, matching the profile's locale.
MAC_DEFAULT_VOICES = {
    "en-US": "Samantha", "en-GB": "Daniel", "en-AU": "Karen", "en-IN": "Rishi", "en-IE": "Moira", "en-ZA": "Tessa",
    "it-IT": "Alice", "fr-FR": "Thomas", "fr-CA": "Amélie", "fr-BE": "Thomas", "fr-CH": "Thomas", "de-DE": "Anna", "de-AT": "Anna",
    "de-CH": "Anna", "es-ES": "Mónica", "es-MX": "Paulina", "es-AR": "Diego", "es-CO": "Soledad", "es-CL": "Francisca",
    "pt-BR": "Luciana", "pt-PT": "Joana", "nl-NL": "Xander", "nl-BE": "Ellen", "ja-JP": "Kyoko", "zh-CN": "Tingting",
    "zh-TW": "Meijia", "zh-HK": "Sinji", "ko-KR": "Yuna", "ru-RU": "Milena", "pl-PL": "Zosia", "sv-SE": "Alva", "da-DK": "Sara",
    "nb-NO": "Nora", "fi-FI": "Satu", "tr-TR": "Yelda", "cs-CZ": "Zuzana", "el-GR": "Melina", "he-IL": "Carmit", "hu-HU": "Mariska",
    "id-ID": "Damayanti", "ro-RO": "Ioana", "sk-SK": "Laura", "th-TH": "Kanya", "uk-UA": "Lesya", "hi-IN": "Lekha", "ar-SA": "Maged",
}


BASE_VOICE_REGION = {"en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "pt": "pt-BR", "zh": "zh-CN", "nl": "nl-NL",
                     "it": "it-IT", "ja": "ja-JP", "ko": "ko-KR", "ru": "ru-RU", "ar": "ar-SA"}


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).casefold()


def profile_voices(os_name: str, locale, seed: str = "") -> Optional[list[dict]]:
    """The profile's speech voices, with exactly ONE marked as the default and that one matching the profile's locale (None if the
    list can't be drawn: then Camoufox's own draw is left alone). Deterministic per profile and locale."""
    import hashlib
    primary = (locale[0] if isinstance(locale, (list, tuple)) and locale else locale) or "en-US"
    try:
        from camoufox.fingerprints import _generate_random_voice_subset  # Camoufox's own per-OS, per-locale catalogue
        voices = _generate_random_voice_subset(os_name, primary, seed=int(hashlib.sha256(f"voices:{os_name}:{seed}".encode()).hexdigest()[:8], 16))
    except Exception:
        return None
    if not voices:
        return None
    lang = primary.split("-")[0].lower()
    exact = [v for v in voices if v["lang"].replace("_", "-").lower() == primary.lower()]
    same = [v for v in voices if v["lang"].lower().startswith(lang)]
    pick = None
    if os_name == "macos":
        # the region's own default if it has one (it-IT: Alice), else the language's usual one (en-NG -> en-US's Samantha)
        for tag in (primary, BASE_VOICE_REGION.get(lang, "")):
            want = MAC_DEFAULT_VOICES.get(tag)
            pool = exact if tag == primary else same
            pick = pick or (next((v for v in pool if want and _fold(v["name"]).split(" (")[0] == _fold(want)), None))
    pick = pick or (exact or same or voices)[0]
    return [dict(v, isDefault=v is pick) for v in voices]


def prepare_launch(info: dict, user_data_dir: Path, headless: bool = False, seed: str = "",
                   log: Callable[[str], None] = print, expect: Optional[dict] = None) -> dict:
    """Camoufox options for a profile, with a live proxy check: the measured exit IP drives timezone, geolocation and WebRTC; the
    language is English by default (see browser_locale), stable per profile; and the default speech voice follows that language.
    `expect`, if given, is filled with what the launched browser should then show (see audit_identity)."""
    opts = camoufox_options(info, user_data_dir, headless)
    expect = {} if expect is None else expect
    cc = None
    if info.get("proxy"):
        chk = check_proxy(info["proxy"])
        if not chk["ok"]:
            raise PigeonError(502, f"proxy not working: {chk['error']}")
        opts["geoip"] = chk["ip"]
        expect["ip"] = chk["ip"]
        log(f"proxy exit {chk['ip']} ({chk['ms']} ms)")
        if chk.get("warning"):
            log("WARNING: " + chk["warning"])
        try:
            from camoufox.geolocation import get_geolocation
            geo = get_geolocation(chk["ip"])
            cc = geo.locale.region
            expect.update(country=cc, timezone=geo.timezone)
            opts["locale"] = browser_locale(cc, seed=seed, language=info.get("language"))
        except Exception as e:
            log(f"locale lookup failed ({e}); using English")
            opts["locale"] = english_locale("")  # never leave it to Camoufox's random weighted draw
    if not opts.get("locale"):
        # No proxy (or no country): Camoufox would draw a random weighted locale on every launch, and it can even disagree with itself
        # (navigator.language en-US next to an Intl locale of en-IN). Same policy as with a proxy, without a country: English by default.
        opts["locale"] = browser_locale("", seed=seed, language=info.get("language"))
    locale = opts["locale"]
    expect["locale"] = list(locale) if isinstance(locale, (list, tuple)) else [locale]
    # one default speech voice, matching the language (see MAC_DEFAULT_VOICES)
    voices = profile_voices(info.get("os") or host_os(), locale, seed=seed)
    if voices:
        opts["config"] = {**opts.get("config", {}), "voices": voices}
    return opts


# What the launched browser is checked against: every signal that depends on the identity's language and exit country.
AUDIT_JS = """() => new Promise(res => {
  const o = Intl.DateTimeFormat().resolvedOptions(), d = new Date(2026, 0, 15, 13, 5, 9), lang = navigator.language;
  const done = v => res({
    language: lang, languages: Array.from(navigator.languages), intl_locale: o.locale, timezone: o.timeZone,
    number_follows_language: new Intl.NumberFormat().format(1234567.89) === new Intl.NumberFormat(lang).format(1234567.89),
    date_follows_language: d.toLocaleString() === d.toLocaleString(lang),
    voice_default: v.filter(x => x.default).map(x => ({name: x.name, lang: x.lang})),
    voices_in_language: v.filter(x => x.lang.toLowerCase().startsWith(lang.slice(0, 2).toLowerCase())).length,
  });
  let v = speechSynthesis.getVoices(); if (v.length) return done(v);
  speechSynthesis.onvoiceschanged = () => done(speechSynthesis.getVoices()); setTimeout(() => done(speechSynthesis.getVoices()), 4000); })"""


def audit_identity(context, expect: dict) -> list[dict]:
    """Read every language/country-dependent signal from the REAL launched browser and compare it with what the profile's language
    and exit IP imply. Returns [{signal, ok, actual, expected}]. A mismatch is a tell a thorough checker can use, so it is reported."""
    page = context.new_page()
    try:
        page.goto("about:blank")
        got = page.evaluate(AUDIT_JS)
    finally:
        page.close()
    loc = expect.get("locale") or []
    primary = loc[0] if loc else None
    rows = []

    def add(signal, ok, actual, expected):
        rows.append(dict(signal=signal, ok=bool(ok), actual=actual, expected=expected))
    if primary:
        add("browser language", got["language"] == primary, got["language"], primary)
        add("language list (Accept-Language)", got["languages"][:len(loc)] == loc, got["languages"], loc)
        add("Intl locale", got["intl_locale"] == primary, got["intl_locale"], primary)
        dv = got["voice_default"]
        base = primary.split("-")[0].lower()
        add("default speech voice", len(dv) == 1 and dv[0]["lang"].replace("_", "-").lower().startswith(base),
            [f"{v['name']} [{v['lang']}]" for v in dv] or "none", f"exactly one, in {base}")
        add("voices in the language", got["voices_in_language"] >= 1, got["voices_in_language"], ">= 1")
    if expect.get("timezone"):
        add("time zone (from the exit IP)", got["timezone"] == expect["timezone"], got["timezone"], expect["timezone"])
    add("number format follows the language", got["number_follows_language"], got["number_follows_language"], True)
    add("date/time format follows the language", got["date_follows_language"], got["date_follows_language"], True)
    return rows


def format_audit(rows: list[dict]) -> str:
    bad = [r for r in rows if not r["ok"]]
    lines = [f"  {'✓' if r['ok'] else '✗'} {r['signal']}: {r['actual']}" + ("" if r["ok"] else f"   (expected {r['expected']})") for r in rows]
    return ("identity is consistent" if not bad else f"IDENTITY MISMATCH ({len(bad)})") + "\n" + "\n".join(lines)


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
        raise PigeonError(500, "profile has no fingerprint")
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
        expect: dict = {}
        opts = prepare_launch(info, local.data, headless, seed=str(pid), log=log, expect=expect)
        log(f"launching '{info['name']}'" + (f" via {info['proxy']['server']}" if info.get("proxy") else ""))
        err: Optional[BaseException] = None
        with Camoufox(**opts) as context:
            context.pigeon_info = info  # type: ignore[attr-defined]
            try:  # check the real browser against what its language and exit IP imply; never let the check break a launch
                context.pigeon_audit = audit_identity(context, expect)  # type: ignore[attr-defined]
                bad = [r for r in context.pigeon_audit if not r["ok"]]
                log("identity consistent with the exit IP and language" if not bad else
                    "WARNING identity mismatch: " + "; ".join(f"{r['signal']} is {r['actual']}, expected {r['expected']}" for r in bad))
            except Exception as e:
                context.pigeon_audit = []  # type: ignore[attr-defined]
                log(f"(identity audit skipped: {type(e).__name__}: {str(e)[:80]})")
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
        except PigeonError as e:
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
        except PigeonError:
            pass


@contextmanager
def open_data(client: Client, pid: int, *, home: Path = PROFILES, log: Callable[[str], None] = print,
              result: Optional[dict] = None) -> Iterator[Path]:
    """Lock -> pull if stale -> yield the profile's data directory (no browser) -> push -> unlock.

    For tools that read or edit files inside a profile and want them synced (Pigeon keeps its state in one). Decryption and
    re-encryption are the client's, exactly as for `open_profile`."""
    info = client.launch_info(pid)
    tid = info["team_id"]
    lk = client.lock(pid)
    lock_tok, server_v = lk["lock"], lk["version"]
    local = LocalProfile(pid, home)
    stop = threading.Event()

    def beat():
        while not stop.wait(30):
            try:
                client.heartbeat(pid, lock_tok)
            except Exception as e:
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
        err: Optional[BaseException] = None
        try:
            yield local.data
        except BaseException as e:  # still sync what was done, then re-raise
            err = e
        stop.set()
        blob = pack(local.data)
        try:
            up = client.upload(pid, lock_tok, server_v, blob, tid=tid)
            local.set_version(up["version"])
            log(f"synced v{up['version']} ({len(blob) // 1024} KB)")
            if result is not None:
                result.update(version=up["version"], bytes=len(blob))
        except PigeonError as e:
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
        except PigeonError:
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
            should_stop = lambda: stop.is_set() or stop_file.exists()
            context.pigeon_stop = should_stop  # type: ignore[attr-defined]  sessions can watch for Ctrl+C / `pigeon stop` too
            if session:
                session(context)
            else:
                # keeps the window open, serves the session's control channel (so tools like Pigeon can attach), and ends on
                # window close or a stop request
                from .session import interactive
                interactive(context, pid, context.pigeon_info, home=home, log=log, client=client, should_stop=should_stop)
    finally:
        restore()
        stop_file.unlink(missing_ok=True)
    return result


def request_stop(pid: int, home: Optional[Path] = None) -> None:
    """Ask a running `pigeon run` (any process on this machine) to close and sync."""
    root = LocalProfile(pid, home or PROFILES).root  # PROFILES is read now, not when this module loaded (it can be re-pointed)
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
    """A portable .pigeonprofile: settings, fingerprint, proxy and the latest browser data."""
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
            raise PigeonError(400, "this bundle is encrypted: a passphrase is needed")
        try:
            blob = crypto.decrypt(crypto.passphrase_key(passphrase, blob[4:20]), blob[20:], "bundle")
        except Exception:
            raise PigeonError(400, "wrong passphrase")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        m = json.loads(z.read("manifest.json"))
        data = z.read("data.zip") if "data.zip" in z.namelist() else None
    if m.get("format") != "loft-profile/1":
        raise PigeonError(400, "not a Pigeon profile bundle")
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
        raise PigeonError(400, "a team backup needs a passphrase (it contains every session)")
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
        raise PigeonError(400, "not a Pigeon team backup")
    try:
        raw = crypto.decrypt(crypto.passphrase_key(passphrase, blob[4:20]), blob[20:], "backup")
    except Exception:
        raise PigeonError(400, "wrong passphrase")
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
        raise PigeonError(400, "count must be 1-500")
    out = []
    fields.setdefault("canvas", default_canvas(os_name))
    for i in range(count):
        proxy = proxies[i % len(proxies)] if proxies else None
        out.append(client.create_profile(tid, name=name.replace("{n}", str(start_n + i)), os=os_name,
                                         fingerprint=new_fingerprint(os_name), proxy=proxy, **fields))
    return out
