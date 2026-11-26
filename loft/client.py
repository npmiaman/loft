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
        self.keys: dict[int, tuple[int, bytes]] = {}  # team id -> (key epoch, key)
        for k, v in cfg.get("keys", {}).items():
            if isinstance(v, dict):
                self.keys[int(k)] = (int(v["e"]), crypto.b64d(v["k"]))
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
                   keys={str(k): {"e": e, "k": crypto.b64e(v)} for k, (e, v) in self.keys.items()})
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
        return self._key(tid)[1]

    def team_epoch(self, tid: int) -> int:
        return self._key(tid)[0]

    def _key(self, tid: int) -> tuple[int, bytes]:
        if tid not in self.keys:
            self._fetch_key(tid)
        return self.keys[tid]

    def refresh_key(self, tid: int) -> tuple[int, bytes]:
        self.keys.pop(tid, None)
        return self._key(tid)

    def _keypair(self) -> tuple[bytes, bytes]:
        me = self.j("GET", "/me")
        if not me.get("pubkey"):
            raise LoftError(403, "no keypair on this account yet: sign in again")
        priv = crypto.decrypt(self.vault, crypto.b64d(me["privkey"]), f"privkey:{me['id']}")
        return priv, crypto.b64d(me["pubkey"])

    def _fetch_key(self, tid: int) -> None:
        if not self.vault:
            raise LoftError(401, "sign in again to unlock team keys")
        try:
            r = self.j("GET", f"/teams/{tid}/keys/me")
        except LoftError as e:
            if e.status == 404:
                raise LoftError(403, "you don't have this team's current key: join with the full invite code "
                                     "(code.key), or ask an admin to rotate the key")
            raise
        if r["kind"] == "sealed":  # granted by a key rotation: open with our private key, then keep a vault copy
            priv, pub = self._keypair()
            key = crypto.unseal(priv, pub, crypto.b64d(r["wrapped"]))
            self._store_key(tid, key, r["epoch"])
        else:
            key = crypto.decrypt(self.vault, crypto.b64d(r["wrapped"]), f"teamkey:{tid}:{r['epoch']}")
            self.keys[tid] = (r["epoch"], key)
            self._save()

    def _store_key(self, tid: int, key: bytes, epoch: int) -> None:
        self.keys[tid] = (epoch, key)
        if self.vault:
            self.j("PUT", f"/teams/{tid}/keys/me", {"epoch": epoch, "wrapped": crypto.b64e(
                crypto.encrypt(self.vault, key, f"teamkey:{tid}:{epoch}"))})
        self._save()

    def _enc(self, tid: int, obj: Any, purpose: str) -> str:
        return crypto.enc_text(self.team_key(tid), json.dumps(obj), f"{purpose}:{tid}")

    def _dec(self, tid: int, s: Optional[str], purpose: str) -> Any:
        if not s:
            return None
        try:
            return json.loads(crypto.dec_text(self.team_key(tid), s, f"{purpose}:{tid}"))
        except Exception:  # maybe the key was rotated since we cached it
            self.refresh_key(tid)
            return json.loads(crypto.dec_text(self.team_key(tid), s, f"{purpose}:{tid}"))

    def _write(self, tid: int, fn):
        """Run an encrypted write; if the team key rotated meanwhile, refresh it and redo the write."""
        try:
            return fn()
        except LoftError as e:
            if e.status == 409 and "team key rotated" in e.detail:
                self.refresh_key(tid)
                return fn()
            raise

    def _eh(self, tid: int) -> dict:
        return {"X-Key-Epoch": str(self.team_epoch(tid))}

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
        me = self.j("GET", "/me")
        if not me.get("pubkey"):
            priv, pub = crypto.new_keypair()
            self.j("PUT", "/me/keypair", {"pubkey": crypto.b64e(pub), "privkey": crypto.b64e(
                crypto.encrypt(self.vault, priv, f"privkey:{me['id']}"))})
        self._save()
        return out

    def register(self, email: str, password: str, name: str = "", invite: Optional[str] = None) -> dict:
        code, key = _split_invite(invite)
        out = self.j("POST", "/auth/register", {"email": email, "password": password, "name": name, "invite": code})
        self._after_auth(out, password)
        if code and key:
            self._store_key(out["team_id"], key, self._invite_epoch(out["team_id"]))
        return out

    def _invite_epoch(self, tid: int) -> int:
        return next(t["key_epoch"] for t in self.me()["teams"] if t["id"] == tid)

    def login(self, email: str, password: str) -> dict:
        return self._after_auth(self.j("POST", "/auth/login", {"email": email, "password": password}), password)

    def me(self) -> dict:
        return self.j("GET", "/me")

    # ------------------------------------------------------------ teams
    def create_team(self, name: str) -> dict:
        t = self.j("POST", "/teams", {"name": name})
        self._store_key(t["id"], crypto.new_key(), t["key_epoch"])
        return t

    def members(self, tid: int) -> list:
        return self.j("GET", f"/teams/{tid}/members")

    def invite(self, tid: int, role: str = "member") -> dict:
        out = self.j("POST", f"/teams/{tid}/invites", {"role": role})
        if out["epoch"] != self.team_epoch(tid):
            self.refresh_key(tid)
        out["code"] = f"{out['code']}.{crypto.b64e(self.team_key(tid))}"
        return out

    def accept(self, invite: str) -> dict:
        code, key = _split_invite(invite)
        if not key:
            raise LoftError(400, "invite is missing its key part (expected code.key)")
        out = self.j("POST", f"/invites/{code}/accept")
        self._store_key(out["team_id"], key, self._invite_epoch(out["team_id"]))
        return out

    def set_role(self, tid: int, uid: int, role: str) -> dict:
        return self.j("PATCH", f"/teams/{tid}/members/{uid}", {"role": role})

    def remove_member(self, tid: int, uid: int) -> dict:
        return self.j("DELETE", f"/teams/{tid}/members/{uid}")

    def audit(self, tid: int, limit: int = 100) -> list:
        return self.j("GET", f"/teams/{tid}/audit", params={"limit": limit})

    def rotation_preview(self, tid: int) -> dict:
        return self.j("GET", f"/teams/{tid}/rotation/preview")

    def overview(self, tid: int) -> dict:
        return self.j("GET", f"/teams/{tid}/overview")

    # ------------------------------------------------------------ profiles
    def profiles(self, tid: int) -> list:
        return [self._profile(p) for p in self.j("GET", f"/teams/{tid}/profiles")]

    def create_profile(self, tid: int, *, name: str, fingerprint: dict, proxy: Optional[dict] = None, **fields) -> dict:
        def go():
            body = dict(fields, name=name, fingerprint=self._enc(tid, fingerprint, "fp"),
                        proxy=self._enc(tid, proxy, "proxy") if proxy else None)
            return self.j("POST", f"/teams/{tid}/profiles", body, headers=self._eh(tid))
        return self._profile(self._write(tid, go))

    def profile(self, pid: int) -> dict:
        return self._profile(self.j("GET", f"/profiles/{pid}"))

    def launch_info(self, pid: int) -> dict:
        info = self._profile(self.j("GET", f"/profiles/{pid}/launch"))
        info["fingerprint"] = self._dec(info["team_id"], info.get("fingerprint"), "fp")
        return info

    def patch_profile(self, pid: int, proxy: Optional[dict] = None, **fields) -> dict:
        if proxy is None:
            return self._profile(self.j("PATCH", f"/profiles/{pid}", fields))
        tid = self.j("GET", f"/profiles/{pid}")["team_id"]

        def go():
            return self.j("PATCH", f"/profiles/{pid}", dict(fields, proxy=self._enc(tid, proxy, "proxy")),
                          headers=self._eh(tid))
        return self._profile(self._write(tid, go))

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
        return self._write(tid, lambda: self.j("POST", f"/teams/{tid}/templates", {
            "name": name, "payload": self._enc(tid, settings, "template")}, headers=self._eh(tid)))

    def delete_template(self, xid: int) -> dict:
        return self.j("DELETE", f"/templates/{xid}")

    # ------------------------------------------------------------ runners
    def runners(self, tid: int) -> list:
        return [{"id": r["id"], "name": r["name"], "url": r["url"], "secret": self._dec(tid, r["secret"], "runner")}
                for r in self.j("GET", f"/teams/{tid}/runners")]

    def add_runner(self, tid: int, name: str, url: str, secret: str) -> dict:
        return self._write(tid, lambda: self.j("POST", f"/teams/{tid}/runners", {
            "name": name, "url": url, "secret": self._enc(tid, secret, "runner")}, headers=self._eh(tid)))

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

        def go():
            blob = crypto.encrypt(self.team_key(tid), data, f"data:{tid}:{pid}")
            return self.req("PUT", f"/profiles/{pid}/data", content=blob,
                            headers={"X-Lock": lock, "X-Base-Version": str(base_version),
                                     "Content-Type": "application/octet-stream", **self._eh(tid)}).json()
        return self._write(tid, go)

    def download(self, pid: int, version: Optional[int] = None, tid: Optional[int] = None) -> tuple[int, Optional[bytes]]:
        r = self.req("GET", f"/profiles/{pid}/data", params={"version": version} if version else None)
        v = int(r.headers.get("X-Version", "0"))
        if r.status_code != 200:
            return v, None
        tid = tid if tid is not None else self.j("GET", f"/profiles/{pid}")["team_id"]
        try:
            return v, crypto.decrypt(self.team_key(tid), r.content, f"data:{tid}:{pid}")
        except Exception:
            self.refresh_key(tid)
            return v, crypto.decrypt(self.team_key(tid), r.content, f"data:{tid}:{pid}")

    def versions(self, pid: int) -> list:
        return self.j("GET", f"/profiles/{pid}/versions")

    # ------------------------------------------------------------ pigeon: the team's shared memory (see loft/pigeon)
    SEALED = "enc1:"

    def seal_text(self, tid: int, text: str) -> str:
        """Message text for the team memory, sealed with the team key: the server only ever holds ciphertext."""
        return self.SEALED + crypto.enc_text(self.team_key(tid), text, f"pigeon-body:{tid}") if text else ""

    def open_text(self, tid: int, text: str) -> str:
        """Plain text passes through; sealed text is opened, or shows as [encrypted] if the key has since been rotated."""
        if not text or not text.startswith(self.SEALED):
            return text
        try:
            return crypto.dec_text(self.team_key(tid), text[len(self.SEALED):], f"pigeon-body:{tid}")
        except Exception:
            return "[encrypted]"

    def pigeon_claim(self, tid: int, **fields) -> dict:
        return self.j("POST", f"/teams/{tid}/pigeon/claim", fields)

    def pigeon_finish(self, tid: int, eid: int, status: str, error: str = "") -> dict:
        return self.j("POST", f"/teams/{tid}/pigeon/events/{eid}", {"status": status, "error": error})

    def pigeon_seen(self, tid: int, kind: str, targets: list) -> dict:
        return self.j("POST", f"/teams/{tid}/pigeon/seen", {"kind": kind, "targets": targets})["seen"]

    def pigeon_events(self, tid: int, **params) -> list:
        return self.j("GET", f"/teams/{tid}/pigeon/events", params={k: v for k, v in params.items() if v})

    def pigeon_summary(self, tid: int, days: float = 14) -> dict:
        return self.j("GET", f"/teams/{tid}/pigeon/summary", params={"days": days})

    def pigeon_set_account(self, tid: int, pid: int, username: str) -> dict:
        return self.j("PUT", f"/teams/{tid}/pigeon/accounts/{pid}", {"username": username})

    def pigeon_accounts(self, tid: int) -> list:
        return self.j("GET", f"/teams/{tid}/pigeon/accounts")

    def pigeon_config(self, tid: int) -> dict:
        """{version, setup (decrypted, or None), unreadable, limits, updated}. `unreadable` = a setup exists but this key can't open it."""
        r = self.j("GET", f"/teams/{tid}/pigeon/config")
        setup, unreadable = None, False
        if r["setup"]:
            try:
                setup = self._dec(tid, r["setup"], "pigeon-setup")
            except Exception:
                unreadable = True
        return {"version": r["version"], "setup": setup, "unreadable": unreadable, "limits": r["limits"], "updated": r["updated"]}

    def pigeon_put_config(self, tid: int, base_version: int, setup: Optional[dict] = None, limits: Optional[dict] = None) -> dict:
        body: dict = {"base_version": base_version}
        if limits is not None:
            body["limits"] = limits
        if setup is None:
            return self.j("PUT", f"/teams/{tid}/pigeon/config", body)
        body["setup"] = self._enc(tid, setup, "pigeon-setup")
        return self._write(tid, lambda: self.j("PUT", f"/teams/{tid}/pigeon/config", {**body, "setup": self._enc(tid, setup, "pigeon-setup")},
                                               headers=self._eh(tid)))


def rotate_team_key(c: "Client", tid: int, log=print) -> dict:
    """Re-key a team: new key, everything re-encrypted, key sealed to each remaining member.

    Members removed before this keep only the old key, which no longer opens anything on the server
    (data they already downloaded stays theirs: no crypto can take that back)."""
    old_epoch, old = c._key(tid)
    try:
        st = c.j("POST", f"/teams/{tid}/rotation/start")
    except LoftError as e:
        raise LoftError(e.status, f"can't start rotation: {e.detail}")
    epoch, new = st["epoch"], crypto.new_key()
    try:
        for i, p in enumerate(st["profiles"], 1):
            info = c.j("GET", f"/profiles/{p['id']}/launch")
            fp = crypto.dec_text(old, info["fingerprint"], f"fp:{tid}")
            proxy = crypto.dec_text(old, info["proxy"], f"proxy:{tid}") if info.get("proxy") else None
            if p["version"] > 0:
                r = c.req("GET", f"/profiles/{p['id']}/data")
                data = crypto.decrypt(old, r.content, f"data:{tid}:{p['id']}")
                c.req("PUT", f"/teams/{tid}/rotation/profiles/{p['id']}/data",
                      content=crypto.encrypt(new, data, f"data:{tid}:{p['id']}"),
                      headers={"Content-Type": "application/octet-stream"})
            c.j("PUT", f"/teams/{tid}/rotation/profiles/{p['id']}", {
                "fingerprint": crypto.enc_text(new, fp, f"fp:{tid}"),
                "proxy": crypto.enc_text(new, proxy, f"proxy:{tid}") if proxy else None})
            log(f"re-encrypted profile {i}/{len(st['profiles'])}")
        tpls = [{"id": t["id"], "payload": crypto.enc_text(new, crypto.dec_text(old, t["payload"], f"template:{tid}"),
                                                           f"template:{tid}")}
                for t in c.j("GET", f"/teams/{tid}/templates")]
        runs = [{"id": r["id"], "payload": crypto.enc_text(new, crypto.dec_text(old, r["secret"], f"runner:{tid}"),
                                                           f"runner:{tid}")}
                for r in c.j("GET", f"/teams/{tid}/runners")]
        pcfg = c.j("GET", f"/teams/{tid}/pigeon/config")
        pigeon_setup = (crypto.enc_text(new, crypto.dec_text(old, pcfg["setup"], f"pigeon-setup:{tid}"), f"pigeon-setup:{tid}")
                        if pcfg["setup"] else None)
        me_id = c.j("GET", "/me")["id"]
        grants = [{"user_id": m["user_id"], "sealed": crypto.b64e(crypto.seal(crypto.b64d(m["pubkey"]), new))}
                  for m in st["members"] if m["user_id"] != me_id and m.get("pubkey")]
        out = c.j("POST", f"/teams/{tid}/rotation/commit", {
            "epoch": epoch, "grants": grants, "templates": tpls, "runners": runs, "pigeon_setup": pigeon_setup,
            "self_wrapped": crypto.b64e(crypto.encrypt(c.vault, new, f"teamkey:{tid}:{epoch}"))})
    except BaseException:
        try:
            c.j("POST", f"/teams/{tid}/rotation/abort")
        except Exception:
            pass
        raise
    c.keys[tid] = (epoch, new)
    c._save()
    log(f"team key rotated: epoch {old_epoch} -> {epoch}")
    return out


def _split_invite(invite: Optional[str]) -> tuple[Optional[str], Optional[bytes]]:
    if not invite:
        return None, None
    invite = invite.strip()
    if "." in invite:
        code, key = invite.split(".", 1)
        return code, crypto.b64d(key)
    return invite, None
