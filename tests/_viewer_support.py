"""Shared helpers for the cloud viewer tests: a fake runner session (no Camoufox), a runner on a fixed test port
range (4620-4639, never the live dev ports), and a Chromium for Playwright."""
from __future__ import annotations

import glob
import os
import socket
import struct
import threading
import time
import zlib
from pathlib import Path

import uvicorn

from pigeon.runner import Session, create_runner

KEY = "RKEY-UX"


def png(w: int = 1280, h: int = 800, shade: int = 0) -> bytes:
    """A real PNG frame: a light page with a dark band, so screenshots look like a page and tap maths can be checked."""
    rows = []
    for y in range(h):
        c = (40 + shade % 60) if 60 <= y < 140 else 245
        rows.append(b"\x00" + bytes([c, c, min(255, c + 8)]) * w)
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


class FakeClient:
    server = "http://pigeon.test"


class FakeSession(Session):
    """Behaves like a runner session (status, frames, tabs, commands, stop/sync) without launching a browser."""

    def __init__(self, *, name: str = "acct-a", os_name: str = "windows", start_delay: float = 0.0,
                 sync_seconds: float = 0.2, fail: str | None = None):
        self.applied: list[dict] = []
        self._fake = dict(name=name, os=os_name, start_delay=start_delay, sync=sync_seconds, fail=fail)
        self._frames = [png(shade=i * 7) for i in range(4)]
        super().__init__(FakeClient(), 1, "owner@x.io")

    def _browse(self) -> None:
        f = self._fake
        self.name, self.os = f["name"], f["os"]
        end = time.time() + f["start_delay"]
        while time.time() < end and not self.stop.is_set():
            time.sleep(0.02)
        if f["fail"]:
            raise RuntimeError(f["fail"])
        self.tabs = [{"i": 0, "url": "https://www.reddit.com/", "title": "Reddit", "active": True}]
        self._mark_running()
        n = 0
        while not self.stop.is_set():
            try:
                c = self.cmds.get(timeout=0.05)
            except Exception:
                c = None
            if c:
                self.applied.append(c)
                if c.get("t") == "newtab":
                    for t in self.tabs:
                        t["active"] = False
                    self.tabs = self.tabs + [{"i": len(self.tabs), "url": "about:blank", "title": "", "active": True}]
                elif c.get("t") == "closetab" and len(self.tabs) > 1:
                    i = int(c.get("i", len(self.tabs) - 1))
                    self.tabs = [dict(t, i=k, active=False) for k, t in enumerate(x for j, x in enumerate(self.tabs) if j != i)]
                    self.tabs[-1]["active"] = True
            if self.viewers:
                n += 1
                if n % 3 == 0:
                    self.frame = self._frames[(n // 3) % len(self._frames)]
                    self.frame_no += 1
        self.status = "syncing"
        time.sleep(f["sync"])

    def cmds_of(self, t: str) -> list[dict]:
        return [c for c in self.applied if c.get("t") == t]


def port_in_range(lo: int = 4620, hi: int = 4639) -> int:
    for p in range(lo, hi + 1):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free port in 4620-4639")


class Runner:
    """A real runner app on a test port. Add fake sessions with .add()."""

    def __init__(self, **kw):
        self.port = port_in_range()
        self.app = create_runner(KEY, **kw)
        self.sessions = self.app.state.sessions
        self.url = f"http://127.0.0.1:{self.port}"
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.t = threading.Thread(target=self.srv.run, daemon=True)
        self.t.start()
        for _ in range(200):
            if self.srv.started:
                break
            time.sleep(0.05)

    def add(self, s: FakeSession) -> FakeSession:
        self.sessions[s.id] = s
        return s

    def view(self, s: Session) -> str:
        return f"{self.url}/v/{s.id}?t={s.view_token}"

    def ws(self, s: Session) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/{s.id}?t={s.view_token}"

    def close(self) -> None:
        for s in list(self.sessions.values()):
            s.stop.set()
        for s in list(self.sessions.values()):
            s.thread.join(5)
        self.srv.should_exit = True
        self.t.join(5)


def wait_for(fn, timeout: float = 10, every: float = 0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out")


def chromium() -> str | None:
    if os.environ.get("PIGEON_TEST_CHROMIUM"):
        return os.environ["PIGEON_TEST_CHROMIUM"]
    home = Path.home() / "Library/Caches/ms-playwright"
    for pat in ("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium_headless_shell-*/chrome-linux*/headless_shell", "chromium-*/chrome-linux*/chrome"):
        hits = sorted(glob.glob(str(home / pat)) + glob.glob(str(Path.home() / ".cache/ms-playwright" / pat)))
        if hits:
            return hits[-1]
    return None


SHOTS = Path(os.environ.get("PIGEON_UX_SHOTS", "/private/tmp/claude-501/-Users-amanpandit-Desktop-Please/"
                                               "9a0e24de-dd92-4fb9-b47e-cdb808d0fbc5/scratchpad/viewer-track"))
