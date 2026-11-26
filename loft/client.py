"""HTTP client for the Loft server + local config (~/.loft/config.json)."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Optional

import httpx

HOME = Path(os.environ.get("LOFT_HOME", "~/.loft")).expanduser()
CONFIG = HOME / "config.json"


class LoftError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def load_config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {"server": "http://127.0.0.1:4400", "device": platform.node() or "device"}


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    CONFIG.chmod(0o600)


class Client:
    def __init__(self, server: Optional[str] = None, token: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None):
        cfg = load_config()
        self.server = (server or cfg.get("server")).rstrip("/")
        self.token = token if token is not None else cfg.get("token")
        self.device = cfg.get("device", "device")
        self.http = httpx.Client(base_url=self.server, timeout=120, transport=transport)

    def _h(self, extra: Optional[dict] = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        h.update(extra or {})
        return h

    def req(self, method: str, path: str, *, json_body: Any = None, content: Optional[bytes] = None,
            headers: Optional[dict] = None, params: Optional[dict] = None) -> httpx.Response:
        r = self.http.request(method, path, json=json_body, content=content, headers=self._h(headers), params=params)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise LoftError(r.status_code, str(detail))
        return r

    def j(self, method: str, path: str, body: Any = None, **kw) -> Any:
        return self.req(method, path, json_body=body, **kw).json()

    # auth
    def register(self, email: str, password: str, name: str = "", invite: Optional[str] = None) -> dict:
        out = self.j("POST", "/auth/register", {"email": email, "password": password, "name": name, "invite": invite})
        self.token = out["token"]
        return out

    def login(self, email: str, password: str) -> dict:
        out = self.j("POST", "/auth/login", {"email": email, "password": password})
        self.token = out["token"]
        return out

    def me(self) -> dict:
        return self.j("GET", "/me")

    # teams
    def create_team(self, name: str) -> dict:
        return self.j("POST", "/teams", {"name": name})

    def members(self, tid: int) -> list:
        return self.j("GET", f"/teams/{tid}/members")

    def invite(self, tid: int, role: str = "member") -> dict:
        return self.j("POST", f"/teams/{tid}/invites", {"role": role})

    def accept(self, code: str) -> dict:
        return self.j("POST", f"/invites/{code}/accept")

    def set_role(self, tid: int, uid: int, role: str) -> dict:
        return self.j("PATCH", f"/teams/{tid}/members/{uid}", {"role": role})

    def remove_member(self, tid: int, uid: int) -> dict:
        return self.j("DELETE", f"/teams/{tid}/members/{uid}")

    def audit(self, tid: int, limit: int = 100) -> list:
        return self.j("GET", f"/teams/{tid}/audit", params={"limit": limit})

    # profiles
    def profiles(self, tid: int) -> list:
        return self.j("GET", f"/teams/{tid}/profiles")

    def create_profile(self, tid: int, **fields) -> dict:
        return self.j("POST", f"/teams/{tid}/profiles", fields)

    def profile(self, pid: int) -> dict:
        return self.j("GET", f"/profiles/{pid}")

    def launch_info(self, pid: int) -> dict:
        return self.j("GET", f"/profiles/{pid}/launch")

    def patch_profile(self, pid: int, **fields) -> dict:
        return self.j("PATCH", f"/profiles/{pid}", fields)

    def delete_profile(self, pid: int) -> dict:
        return self.j("DELETE", f"/profiles/{pid}")

    def share(self, pid: int, uid: int, level: str = "run") -> dict:
        return self.j("POST", f"/profiles/{pid}/shares", {"user_id": uid, "level": level})

    def unshare(self, pid: int, uid: int) -> dict:
        return self.j("DELETE", f"/profiles/{pid}/shares/{uid}")

    def shares(self, pid: int) -> list:
        return self.j("GET", f"/profiles/{pid}/shares")

    # lock + data
    def lock(self, pid: int) -> dict:
        return self.j("POST", f"/profiles/{pid}/lock", {"device": self.device})

    def heartbeat(self, pid: int, lock: str) -> dict:
        return self.j("POST", f"/profiles/{pid}/lock/heartbeat", headers={"X-Lock": lock})

    def unlock(self, pid: int, lock: str = "", force: bool = False) -> dict:
        return self.j("DELETE", f"/profiles/{pid}/lock", headers={"X-Lock": lock},
                      params={"force": "true"} if force else None)

    def upload(self, pid: int, lock: str, base_version: int, data: bytes) -> dict:
        return self.req("PUT", f"/profiles/{pid}/data", content=data,
                        headers={"X-Lock": lock, "X-Base-Version": str(base_version),
                                 "Content-Type": "application/zip"}).json()

    def download(self, pid: int, version: Optional[int] = None) -> tuple[int, Optional[bytes]]:
        r = self.req("GET", f"/profiles/{pid}/data", params={"version": version} if version else None)
        v = int(r.headers.get("X-Version", "0"))
        return v, (r.content if r.status_code == 200 else None)

    def versions(self, pid: int) -> list:
        return self.j("GET", f"/profiles/{pid}/versions")
