"""Local side of a profile: fingerprint generation, browser data packing, launch + sync."""
from __future__ import annotations

import io
import json
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .client import HOME, Client, LoftError

PROFILES = HOME / "profiles"

# Firefox profile paths that are caches, locks or crash dumps: never synced.
SKIP_DIRS = {"cache2", "startupCache", "thumbnails", "crashes", "minidumps", "saved-telemetry-pings",
             "datareporting", "shader-cache", "safebrowsing", "jumpListCache", "OfflineCache"}
SKIP_FILES = {"parent.lock", "lock", ".parentlock", "Telemetry.ShutdownTime.txt"}


def new_fingerprint(os_name: str = "windows") -> dict:
    """A stable identity for a new profile. Stored server-side, reused on every launch."""
    from camoufox.fingerprints import generate_fingerprint
    return generate_fingerprint(os=os_name)


def pack(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or not p.is_file():
                continue
            z.write(p, rel.as_posix())
    return buf.getvalue()


def unpack(data: bytes, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    root = dst.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            target = (dst / m.filename).resolve()
            if not target.is_relative_to(root):  # zip-slip guard
                raise ValueError(f"unsafe path in profile archive: {m.filename}")
        z.extractall(dst)


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


def camoufox_options(info: dict, user_data_dir: Path, headless: bool = False) -> dict:
    proxy = info.get("proxy")
    opts = dict(
        persistent_context=True,
        user_data_dir=str(user_data_dir),
        fingerprint=info["fingerprint"],
        headless=headless,
        # With a proxy, derive timezone/locale/geolocation from the proxy's exit IP so they match it.
        geoip=bool(proxy),
    )
    if proxy:
        opts["proxy"] = {k: v for k, v in proxy.items() if k in ("server", "username", "password", "bypass") and v}
    return opts


def run_profile(client: Client, pid: int, *, headless: bool = False,
                session: Optional[Callable] = None, log: Callable[[str], None] = print,
                home: Path = PROFILES) -> dict:
    """Lock -> pull if stale -> launch -> wait for close (or run `session(context)`) -> push -> unlock."""
    info = client.launch_info(pid)
    if not info.get("fingerprint"):
        raise LoftError(500, "profile has no fingerprint")
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

    hb = threading.Thread(target=beat, daemon=True)
    try:
        if local.version != server_v or not local.data.exists():
            if server_v > 0:
                log(f"pulling v{server_v}")
                _, blob = client.download(pid, server_v)
                unpack(blob, local.data)
            else:
                if local.data.exists():
                    shutil.rmtree(local.data)
                local.data.mkdir(parents=True)
            local.set_version(server_v)
        hb.start()

        from camoufox.sync_api import Camoufox
        log(f"launching '{info['name']}'" + (f" via {info['proxy']['server']}" if info.get("proxy") else ""))
        with Camoufox(**camoufox_options(info, local.data, headless)) as context:
            if session:
                session(context)
            else:
                page = context.pages[0] if context.pages else context.new_page()
                if info.get("start_url"):
                    page.goto(info["start_url"])
                context.wait_for_event("close", timeout=0)
        stop.set()

        blob = pack(local.data)
        try:
            up = client.upload(pid, lock_tok, server_v, blob)
            local.set_version(up["version"])
            log(f"synced v{up['version']} ({len(blob) // 1024} KB)")
            return {"version": up["version"], "bytes": len(blob)}
        except LoftError as e:
            keep = local.root / f"unsynced-{server_v}.zip"
            keep.write_bytes(blob)
            log(f"upload failed ({e}); kept local copy at {keep}")
            raise
    finally:
        stop.set()
        try:
            client.unlock(pid, lock_tok)
        except LoftError:
            pass
