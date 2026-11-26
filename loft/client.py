"""HTTP client for the Loft server, local config (~/.loft/config.json) and team-key vault.

All secrets (fingerprints, proxies, template settings, browser data) are
encrypted here before upload and decrypted after download.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Optional

import httpx

from . import crypto

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
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.chmod(0o600)
    tmp.replace(CONFIG)


class Client:
    def __init__(self, server: Optional[str] = None, token: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None, persist: bool = False,
                 ca: Optional[str] = None):
        """persist=True: read/write token, vault key and team keys in ~/.loft/config.json."""
        cfg = load_config() if persist or server is None else {}
        self.persist = persist
        self.server = (server or cfg.get("server") or "http://127.0.0.1:4400").rstrip("/")
        self.token = token if token is not None else cfg.get("token")
        self.device = cfg.get("device") or platform.node() or "device"
        self.vault = crypto.b64d(cfg["vault_key"]) if cfg.get("vault_key") else None
        self.keys: dict[int, bytes] = {int(k): crypto.b64d(v) for k, v in cfg.get("keys", {}).items()}
        self.ca = ca or cfg.get("ca")
        verify: Any = self.ca if self.ca else True
        self.http = httpx.Client(base_url=self.server, timeout=300, transport=transport, verify=verify)

    # ------------------------------------------------------------ plumbing
    def _save(self) -> None:
        if not self.persist:
            return
        cfg = load_config()
        cfg.update(server=self.server, token=self.token,
                   vault_key=crypto.b64e(self.vault) if self.vault else None,
                   keys={str(k): crypto.b64e(v) for k, v in self.keys.items()})
        if self.ca:
            cfg["ca"] = self.ca
        save_config(cfg)

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

    # ------------------------------------------------------------ keys
    def team_key(self, tid: int) -> bytes:
        if tid in self.keys:
            return self.keys[tid]
        if not self.vault:
            raise LoftError(401, "sign in again to unlock team keys")
        try:
            wrapped = self.j("GET", f"/teams/{tid}/keys/me")["wrapped"]
        except LoftError as e:
            if e.status == 404:
                raise LoftError(403, "you don't have this team's key: join with the full invite code (code.key)")
            raise
        self.keys[tid] = crypto.decrypt(self.vault, crypto.b64d(wrapped), f"teamkey:{tid}")
        self._save()
        return self.keys[tid]

    def _store_key(self, tid: int, key: bytes) -> None:
        self.keys[tid] = key
        if self.vault:
            self.j("PUT", f"/teams/{tid}/keys/me", {"wrapped": crypto.b64e(crypto.encrypt(self.vault, key, f"teamkey:{tid}"))})
        self._save()

    def _enc(self, tid: int, obj: Any, purpose: str) -> str:
        return crypto.enc_text(self.team_key(tid), json.dumps(obj), f"{purpose}:{tid}")

    def _dec(self, tid: int, s: Optional[str], purpose: str) -> Any:
        if not s:
            return None
        return json.loads(crypto.dec_text(self.team_key(tid), s, f"{purpose}:{tid}"))

    def _profile(self, p: dict) -> dict:
        p = dict(p)
        try:
            p["proxy"] = self._dec(p["team_id"], p.get("proxy"), "proxy")
        except Exception:
            p["proxy"] = {"server": "(locked: no team key)"} if p.get("proxy") else None
        return p

    # ------------------------------------------------------------ auth
    def _after_auth(self, out: dict, password: str) -> dict:
        self.token = out["token"]
        self.vault = crypto.vault_key(password, out["vault_salt"])
        self.keys = {}
        self._save()
        return out

    def register(self, email: str, password: str, name: str = "", invite: Optional[str] = None) -> dict:
        code, key = _split_invite(invite)
        out = self.j("POST", "/auth/register", {"email": email, "password": password, "name": name, "invite": code})
        self._after_auth(out, password)
        if code and key:
            self._store_key(out["team_id"], key)
        return out

    def login(self, email: str, password: str) -> dict:
        return self._after_auth(self.j("POST", "/auth/login", {"email": email, "password": password}), password)

    def me(self) -> dict:
        return self.j("GET", "/me")

    # ------------------------------------------------------------ teams
    def create_team(self, name: str) -> dict:
        t = self.j("POST", "/teams", {"name": name})
        self._store_key(t["id"], crypto.new_key())
        return t

    def members(self, tid: int) -> list:
        return self.j("GET", f"/teams/{tid}/members")

    def invite(self, tid: int, role: str = "member") -> dict:
        out = self.j("POST", f"/teams/{tid}/invites", {"role": role})
        out["code"] = f"{out['code']}.{crypto.b64e(self.team_key(tid))}"
        return out

    def accept(self, invite: str) -> dict:
        code, key = _split_invite(invite)
        if not key:
            raise LoftError(400, "invite is missing its key part (expected code.key)")
        out = self.j("POST", f"/invites/{code}/accept")
        self._store_key(out["team_id"], key)
        return out

    def set_role(self, tid: int, uid: int, role: str) -> dict:
        return self.j("PATCH", f"/teams/{tid}/members/{uid}", {"role": role})

    def remove_member(self, tid: int, uid: int) -> dict:
        return self.j("DELETE", f"/teams/{tid}/members/{uid}")

    def audit(self, tid: int, limit: int = 100) -> list:
        return self.j("GET", f"/teams/{tid}/audit", params={"limit": limit})

    # ------------------------------------------------------------ profiles
    def profiles(self, tid: int) -> list:
        return [self._profile(p) for p in self.j("GET", f"/teams/{tid}/profiles")]

    def create_profile(self, tid: int, *, name: str, fingerprint: dict, proxy: Optional[dict] = None, **fields) -> dict:
        body = dict(fields, name=name, fingerprint=self._enc(tid, fingerprint, "fp"),
                    proxy=self._enc(tid, proxy, "proxy") if proxy else None)
        return self._profile(self.j("POST", f"/teams/{tid}/profiles", body))

    def profile(self, pid: int) -> dict:
        return self._profile(self.j("GET", f"/profiles/{pid}"))

    def launch_info(self, pid: int) -> dict:
        info = self._profile(self.j("GET", f"/profiles/{pid}/launch"))
        info["fingerprint"] = self._dec(info["team_id"], info.get("fingerprint"), "fp")
        return info

    def patch_profile(self, pid: int, proxy: Optional[dict] = None, **fields) -> dict:
        if proxy is not None:
            fields["proxy"] = self._enc(self.j("GET", f"/profiles/{pid}")["team_id"], proxy, "proxy")
        return self._profile(self.j("PATCH", f"/profiles/{pid}", fields))

    def delete_profile(self, pid: int) -> dict:
        return self.j("DELETE", f"/profiles/{pid}")

    def share(self, pid: int, uid: int, level: str = "run") -> dict:
        return self.j("POST", f"/profiles/{pid}/shares", {"user_id": uid, "level": level})

    def unshare(self, pid: int, uid: int) -> dict:
        return self.j("DELETE", f"/profiles/{pid}/shares/{uid}")

    def shares(self, pid: int) -> list:
        return self.j("GET", f"/profiles/{pid}/shares")

    # ------------------------------------------------------------ templates
    def templates(self, tid: int) -> list:
        out = []
        for t in self.j("GET", f"/teams/{tid}/templates"):
            out.append({"id": t["id"], "name": t["name"], "settings": self._dec(tid, t["payload"], "template")})
        return out

    def add_template(self, tid: int, name: str, settings: dict) -> dict:
        return self.j("POST", f"/teams/{tid}/templates", {"name": name, "payload": self._enc(tid, settings, "template")})

    def delete_template(self, xid: int) -> dict:
        return self.j("DELETE", f"/templates/{xid}")

    # ------------------------------------------------------------ runners
    def runners(self, tid: int) -> list:
        return [{"id": r["id"], "name": r["name"], "url": r["url"], "secret": self._dec(tid, r["secret"], "runner")}
                for r in self.j("GET", f"/teams/{tid}/runners")]

    def add_runner(self, tid: int, name: str, url: str, secret: str) -> dict:
        return self.j("POST", f"/teams/{tid}/runners", {"name": name, "url": url, "secret": self._enc(tid, secret, "runner")})

    def delete_runner(self, rid: int) -> dict:
        return self.j("DELETE", f"/runners/{rid}")

    # ------------------------------------------------------------ lock + data
    def lock(self, pid: int) -> dict:
        return self.j("POST", f"/profiles/{pid}/lock", {"device": self.device})

    def heartbeat(self, pid: int, lock: str) -> dict:
        return self.j("POST", f"/profiles/{pid}/lock/heartbeat", headers={"X-Lock": lock})

    def unlock(self, pid: int, lock: str = "", force: bool = False) -> dict:
        return self.j("DELETE", f"/profiles/{pid}/lock", headers={"X-Lock": lock},
                      params={"force": "true"} if force else None)

    def upload(self, pid: int, lock: str, base_version: int, data: bytes, tid: Optional[int] = None) -> dict:
        tid = tid if tid is not None else self.j("GET", f"/profiles/{pid}")["team_id"]
        blob = crypto.encrypt(self.team_key(tid), data, f"data:{tid}:{pid}")
        return self.req("PUT", f"/profiles/{pid}/data", content=blob,
                        headers={"X-Lock": lock, "X-Base-Version": str(base_version),
                                 "Content-Type": "application/octet-stream"}).json()

    def download(self, pid: int, version: Optional[int] = None, tid: Optional[int] = None) -> tuple[int, Optional[bytes]]:
        r = self.req("GET", f"/profiles/{pid}/data", params={"version": version} if version else None)
        v = int(r.headers.get("X-Version", "0"))
        if r.status_code != 200:
            return v, None
        tid = tid if tid is not None else self.j("GET", f"/profiles/{pid}")["team_id"]
        return v, crypto.decrypt(self.team_key(tid), r.content, f"data:{tid}:{pid}")

    def versions(self, pid: int) -> list:
        return self.j("GET", f"/profiles/{pid}/versions")


def _split_invite(invite: Optional[str]) -> tuple[Optional[str], Optional[bytes]]:
    if not invite:
        return None, None
    invite = invite.strip()
    if "." in invite:
        code, key = invite.split(".", 1)
        return code, crypto.b64d(key)
    return invite, None
