"""A local control channel for a running browser session.

The process that owns a profile's browser (`pigeon run ID`, or the desktop app's subprocess for it) hosts a tiny
HTTP server on 127.0.0.1 and writes its port + token to <profiles>/<id>/control.json. Other processes on this
machine (the CLI, the desktop app) find it there and talk to the live session. Nothing here leaves the machine,
and the file sits next to the profile data, not inside it, so it never syncs."""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from .client import PigeonError
from .engine import PROFILES

Handler = Callable[[dict], "tuple[int, dict]"]


def control_path(pid: int, home: Path | None = None) -> Path:
    return (home or PROFILES) / str(pid) / "control.json"


class SessionControl:
    def __init__(self, pid: int, home: Path | None = None):
        self.pid, self.home = pid, home or PROFILES
        self.token = secrets.token_urlsafe(24)
        self.routes: dict[tuple[str, str], Handler] = {}
        self.main_calls: queue.Queue = queue.Queue()  # work that must run on the thread that owns the browser
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self, method: str):
                u = urlsplit(self.path)
                if self.headers.get("X-Pigeon-Control") != outer.token:
                    return self._send(403, {"detail": "bad control token"})
                fn = outer.routes.get((method, u.path))
                if not fn:
                    return self._send(404, {"detail": "no such route"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    body["_query"] = {k: v[0] for k, v in parse_qs(u.query).items()}
                    code, out = fn(body)
                except Exception as e:  # a handler bug must not kill the session
                    code, out = 500, {"detail": str(e)}
                self._send(code, out)

            def _send(self, code: int, out: dict):
                data = json.dumps(out, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def route(self, method: str, path: str, fn: Handler) -> None:
        self.routes[(method, path)] = fn

    def start(self) -> "SessionControl":
        path = control_path(self.pid, self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"port": self.server.server_address[1], "token": self.token, "os_pid": os.getpid()}))
        path.chmod(0o600)
        self.thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        control_path(self.pid, self.home).unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    """Is this process running? (Never signals it: on Windows os.kill(pid, 0) would terminate the process.)"""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it's just not ours
    return True


def _info(pid: int, home: Path | None) -> dict | None:
    p = control_path(pid, home)
    if not p.exists():
        return None
    try:
        info = json.loads(p.read_text())
        return info if pid_alive(info["os_pid"]) else None  # a crash leaves the file behind
    except (OSError, ValueError, KeyError):
        return None


def request(pid: int, method: str, path: str, body: dict | None = None, *, home: Path | None = None,
            timeout: float = 5.0) -> dict:
    """Call a live session on this machine. Raises PigeonError(404) if the profile isn't running here."""
    info = _info(pid, home)
    if not info:
        raise PigeonError(404, f"profile {pid} is not running on this machine (start it with `pigeon run {pid}`)")
    try:
        r = httpx.request(method, f"http://127.0.0.1:{info['port']}{path}", json=body if method != "GET" else None,
                          headers={"X-Pigeon-Control": info["token"]}, timeout=timeout)
    except httpx.HTTPError as e:
        raise PigeonError(502, f"session {pid} did not answer: {e}")
    data = r.json() if r.content else {}
    if not r.is_success:
        raise PigeonError(r.status_code, data.get("detail", r.text))
    return data


def is_live(pid: int, home: Path | None = None) -> bool:
    return _info(pid, home) is not None
