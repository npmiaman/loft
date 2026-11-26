"""Loft cloud server: accounts, teams, roles, profile sync, run locks, audit log.

Storage is SQLite + a blob directory so it runs anywhere with no setup.
Both sit behind tiny helpers (_db, BlobStore) so Postgres/S3 can replace them.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

ROLES = ("owner", "admin", "member")
LEVELS = ("run", "edit")  # per-profile share level for plain members
LOCK_TTL = 120  # seconds; clients heartbeat every ~30s

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, pw_hash TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS teams(id INTEGER PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members(team_id INTEGER, user_id INTEGER, role TEXT NOT NULL,
  PRIMARY KEY(team_id, user_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, team_id INTEGER NOT NULL, role TEXT NOT NULL,
  created_by INTEGER NOT NULL, expires REAL NOT NULL, used_by INTEGER);
CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL, name TEXT NOT NULL,
  os TEXT NOT NULL, folder_id INTEGER, notes TEXT DEFAULT '', tags TEXT DEFAULT '[]',
  proxy TEXT, start_url TEXT DEFAULT '', created_by INTEGER NOT NULL, created REAL NOT NULL,
  updated REAL NOT NULL, version INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS shares(profile_id INTEGER, user_id INTEGER, level TEXT NOT NULL,
  PRIMARY KEY(profile_id, user_id));
CREATE TABLE IF NOT EXISTS versions(profile_id INTEGER, version INTEGER, sha256 TEXT NOT NULL,
  size INTEGER NOT NULL, created_by INTEGER NOT NULL, created REAL NOT NULL,
  PRIMARY KEY(profile_id, version));
CREATE TABLE IF NOT EXISTS locks(profile_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
  device TEXT NOT NULL, token TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, team_id INTEGER, user_id INTEGER,
  profile_id INTEGER, action TEXT NOT NULL, detail TEXT DEFAULT '', at REAL NOT NULL);
"""


# ---------------------------------------------------------------- storage
class BlobStore:
    """Content-addressed-ish file store. Swap for S3 by keeping put/get/delete."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        assert "/" not in key and ".." not in key
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        tmp = self._p(key + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(self._p(key))

    def get(self, key: str) -> Optional[bytes]:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)


def _hash_pw(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def _check_pw(pw: str, stored: str) -> bool:
    salt, _ = stored.split("$")
    return secrets.compare_digest(_hash_pw(pw, bytes.fromhex(salt)), stored)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------- models
class Register(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    invite: Optional[str] = None


class Login(BaseModel):
    email: str
    password: str


class TeamIn(BaseModel):
    name: str


class InviteIn(BaseModel):
    role: str = "member"
    days: float = 7


class RoleIn(BaseModel):
    role: str


class FolderIn(BaseModel):
    name: str


class ProfileIn(BaseModel):
    name: str
    os: str = "windows"
    folder_id: Optional[int] = None
    notes: str = ""
    tags: list[str] = []
    proxy: Optional[dict[str, Any]] = None  # {"server": "http://h:p", "username":..., "password":...}
    start_url: str = ""
    fingerprint: dict[str, Any]


class ProfilePatch(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    proxy: Optional[dict[str, Any]] = None
    clear_proxy: bool = False
    start_url: Optional[str] = None


class ShareIn(BaseModel):
    user_id: int
    level: str = "run"


class LockIn(BaseModel):
    device: str = "unknown"


# ---------------------------------------------------------------- app
def create_app(data_dir: str | Path | None = None, open_signup: Optional[bool] = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("LOFT_DATA", "~/.loft-server")).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    db_path = data / "loft.db"
    blobs = BlobStore(data / "blobs")
    if open_signup is None:
        open_signup = os.environ.get("LOFT_OPEN_SIGNUP", "1") == "1"

    con0 = sqlite3.connect(db_path)
    con0.executescript(SCHEMA)
    con0.close()

    @contextmanager
    def _db():
        con = sqlite3.connect(db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    app = FastAPI(title="Loft", version="0.1")

    def audit(con, team_id, user_id, action, profile_id=None, detail=""):
        con.execute("INSERT INTO audit(team_id,user_id,profile_id,action,detail,at) VALUES(?,?,?,?,?,?)",
                    (team_id, user_id, profile_id, action, detail, time.time()))

    def issue_token(con, user_id: int) -> str:
        tok = secrets.token_urlsafe(32)
        con.execute("INSERT INTO tokens VALUES(?,?,?)", (_sha(tok), user_id, time.time()))
        return tok

    def current_user(authorization: str = Header(default="")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        with _db() as con:
            row = con.execute("SELECT u.id,u.email,u.name FROM tokens t JOIN users u ON u.id=t.user_id "
                              "WHERE t.hash=?", (_sha(authorization[7:]),)).fetchone()
        if not row:
            raise HTTPException(401, "bad token")
        return dict(row)

    def role_in(con, team_id: int, user_id: int) -> Optional[str]:
        r = con.execute("SELECT role FROM members WHERE team_id=? AND user_id=?", (team_id, user_id)).fetchone()
        return r["role"] if r else None

    def need_role(con, team_id: int, user: dict, *allowed: str) -> str:
        role = role_in(con, team_id, user["id"])
        if role is None:
            raise HTTPException(404, "team not found")
        if allowed and role not in allowed:
            raise HTTPException(403, f"needs role {'/'.join(allowed)}")
        return role

    def profile_access(con, pid: int, user: dict) -> tuple[sqlite3.Row, str]:
        """Return (profile, level) where level is 'edit' or 'run'. 404 if no access."""
        p = con.execute("SELECT * FROM profiles WHERE id=? AND deleted=0", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "profile not found")
        role = role_in(con, p["team_id"], user["id"])
        if role in ("owner", "admin") or (role and p["created_by"] == user["id"]):
            return p, "edit"
        if role:
            s = con.execute("SELECT level FROM shares WHERE profile_id=? AND user_id=?",
                            (pid, user["id"])).fetchone()
            if s:
                return p, s["level"]
        raise HTTPException(404, "profile not found")

    def need_edit(level: str):
        if level != "edit":
            raise HTTPException(403, "you can run this profile but not edit it")

    def profile_out(con, p, level) -> dict:
        lock = con.execute("SELECT l.user_id,l.device,l.expires,u.name,u.email FROM locks l JOIN users u "
                           "ON u.id=l.user_id WHERE profile_id=? AND expires>?", (p["id"], time.time())).fetchone()
        proxy = json.loads(p["proxy"]) if p["proxy"] else None
        return {
            "id": p["id"], "team_id": p["team_id"], "name": p["name"], "os": p["os"],
            "folder_id": p["folder_id"], "notes": p["notes"], "tags": json.loads(p["tags"]),
            "proxy": proxy if level == "edit" else ({"server": proxy["server"]} if proxy else None),
            "start_url": p["start_url"], "created_by": p["created_by"], "updated": p["updated"],
            "version": p["version"], "access": level,
            "running": ({"user_id": lock["user_id"], "by": lock["name"] or lock["email"],
                         "device": lock["device"]} if lock else None),
        }

    def check_lock(con, pid: int, user: dict, lock_token: str):
        l = con.execute("SELECT * FROM locks WHERE profile_id=? AND expires>?", (pid, time.time())).fetchone()
        if not l or l["user_id"] != user["id"] or not secrets.compare_digest(l["token"], lock_token or ""):
            raise HTTPException(423, "you must hold the run lock to upload profile data")

    # ------------------------------------------------------------ auth
    @app.post("/auth/register")
    def register(body: Register):
        email = body.email.strip().lower()
        with _db() as con:
            first = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
            inv = None
            if body.invite:
                inv = con.execute("SELECT * FROM invites WHERE code=? AND used_by IS NULL AND expires>?",
                                  (body.invite, time.time())).fetchone()
                if not inv:
                    raise HTTPException(400, "invite invalid or expired")
            elif not (open_signup or first):
                raise HTTPException(403, "signup needs an invite")
            if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HTTPException(409, "email already registered")
            cur = con.execute("INSERT INTO users(email,name,pw_hash,created) VALUES(?,?,?,?)",
                              (email, body.name or email.split("@")[0], _hash_pw(body.password), time.time()))
            uid = cur.lastrowid
            if inv:
                con.execute("INSERT INTO members VALUES(?,?,?)", (inv["team_id"], uid, inv["role"]))
                con.execute("UPDATE invites SET used_by=? WHERE code=?", (uid, inv["code"]))
                audit(con, inv["team_id"], uid, "member.joined", detail=inv["role"])
            return {"token": issue_token(con, uid), "user_id": uid}

    @app.post("/auth/login")
    def login(body: Login):
        with _db() as con:
            u = con.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
            if not u or not _check_pw(body.password, u["pw_hash"]):
                raise HTTPException(401, "wrong email or password")
            return {"token": issue_token(con, u["id"]), "user_id": u["id"]}

    @app.post("/auth/logout")
    def logout(authorization: str = Header(default="")):
        with _db() as con:
            con.execute("DELETE FROM tokens WHERE hash=?", (_sha(authorization[7:]),))
        return {"ok": True}

    @app.get("/me")
    def me(user=Depends(current_user)):
        with _db() as con:
            teams = con.execute("SELECT t.id,t.name,m.role FROM teams t JOIN members m ON m.team_id=t.id "
                                "WHERE m.user_id=? ORDER BY t.id", (user["id"],)).fetchall()
        return {**user, "teams": [dict(t) for t in teams]}

    # ------------------------------------------------------------ teams
    @app.post("/teams")
    def create_team(body: TeamIn, user=Depends(current_user)):
        with _db() as con:
            tid = con.execute("INSERT INTO teams(name,created) VALUES(?,?)", (body.name, time.time())).lastrowid
            con.execute("INSERT INTO members VALUES(?,?,?)", (tid, user["id"], "owner"))
            audit(con, tid, user["id"], "team.created", detail=body.name)
        return {"id": tid, "name": body.name, "role": "owner"}

    @app.get("/teams/{tid}/members")
    def members(tid: int, user=Depends(current_user)):
        with _db() as con:
            need_role(con, tid, user)
            rows = con.execute("SELECT u.id,u.email,u.name,m.role FROM members m JOIN users u ON u.id=m.user_id "
                               "WHERE m.team_id=? ORDER BY u.id", (tid,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/teams/{tid}/invites")
    def invite(tid: int, body: InviteIn, user=Depends(current_user)):
        if body.role not in ROLES or body.role == "owner":
            raise HTTPException(400, "role must be admin or member")
        with _db() as con:
            need_role(con, tid, user, "owner", "admin")
            code = secrets.token_urlsafe(12)
            con.execute("INSERT INTO invites(code,team_id,role,created_by,expires) VALUES(?,?,?,?,?)",
                        (code, tid, body.role, user["id"], time.time() + body.days * 86400))
            audit(con, tid, user["id"], "invite.created", detail=body.role)
        return {"code": code, "role": body.role}

    @app.post("/invites/{code}/accept")
    def accept(code: str, user=Depends(current_user)):
        with _db() as con:
            inv = con.execute("SELECT * FROM invites WHERE code=? AND used_by IS NULL AND expires>?",
                              (code, time.time())).fetchone()
            if not inv:
                raise HTTPException(400, "invite invalid or expired")
            if role_in(con, inv["team_id"], user["id"]):
                raise HTTPException(409, "already a member")
            con.execute("INSERT INTO members VALUES(?,?,?)", (inv["team_id"], user["id"], inv["role"]))
            con.execute("UPDATE invites SET used_by=? WHERE code=?", (user["id"], code))
            audit(con, inv["team_id"], user["id"], "member.joined", detail=inv["role"])
        return {"team_id": inv["team_id"], "role": inv["role"]}

    @app.patch("/teams/{tid}/members/{uid}")
    def set_role(tid: int, uid: int, body: RoleIn, user=Depends(current_user)):
        if body.role not in ROLES:
            raise HTTPException(400, "bad role")
        with _db() as con:
            mine = need_role(con, tid, user, "owner", "admin")
            theirs = role_in(con, tid, uid)
            if theirs is None:
                raise HTTPException(404, "not a member")
            if "owner" in (body.role, theirs) and mine != "owner":
                raise HTTPException(403, "only an owner can grant or remove owner")
            if theirs == "owner" and body.role != "owner":
                n = con.execute("SELECT COUNT(*) c FROM members WHERE team_id=? AND role='owner'", (tid,)).fetchone()
                if n["c"] <= 1:
                    raise HTTPException(400, "team needs at least one owner")
            con.execute("UPDATE members SET role=? WHERE team_id=? AND user_id=?", (body.role, tid, uid))
            audit(con, tid, user["id"], "member.role", detail=f"user {uid} -> {body.role}")
        return {"ok": True}

    @app.delete("/teams/{tid}/members/{uid}")
    def remove_member(tid: int, uid: int, user=Depends(current_user)):
        with _db() as con:
            mine = need_role(con, tid, user) if uid == user["id"] else need_role(con, tid, user, "owner", "admin")
            theirs = role_in(con, tid, uid)
            if theirs is None:
                raise HTTPException(404, "not a member")
            if theirs == "owner":
                if mine != "owner":
                    raise HTTPException(403, "only an owner can remove an owner")
                n = con.execute("SELECT COUNT(*) c FROM members WHERE team_id=? AND role='owner'", (tid,)).fetchone()
                if n["c"] <= 1:
                    raise HTTPException(400, "team needs at least one owner")
            con.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, uid))
            con.execute("DELETE FROM shares WHERE user_id=? AND profile_id IN (SELECT id FROM profiles WHERE team_id=?)",
                        (uid, tid))
            con.execute("DELETE FROM locks WHERE user_id=? AND profile_id IN (SELECT id FROM profiles WHERE team_id=?)",
                        (uid, tid))
            audit(con, tid, user["id"], "member.removed", detail=f"user {uid}")
        return {"ok": True}

    @app.get("/teams/{tid}/audit")
    def get_audit(tid: int, limit: int = 200, user=Depends(current_user)):
        with _db() as con:
            need_role(con, tid, user, "owner", "admin")
            rows = con.execute("SELECT a.*,u.email FROM audit a LEFT JOIN users u ON u.id=a.user_id "
                               "WHERE team_id=? ORDER BY a.id DESC LIMIT ?", (tid, min(limit, 1000))).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ folders
    @app.get("/teams/{tid}/folders")
    def folders(tid: int, user=Depends(current_user)):
        with _db() as con:
            need_role(con, tid, user)
            return [dict(r) for r in con.execute("SELECT id,name FROM folders WHERE team_id=? ORDER BY name", (tid,))]

    @app.post("/teams/{tid}/folders")
    def make_folder(tid: int, body: FolderIn, user=Depends(current_user)):
        with _db() as con:
            need_role(con, tid, user, "owner", "admin")
            fid = con.execute("INSERT INTO folders(team_id,name) VALUES(?,?)", (tid, body.name)).lastrowid
        return {"id": fid, "name": body.name}

    # ------------------------------------------------------------ profiles
    @app.get("/teams/{tid}/profiles")
    def list_profiles(tid: int, user=Depends(current_user)):
        with _db() as con:
            role = need_role(con, tid, user)
            if role in ("owner", "admin"):
                rows = con.execute("SELECT *, 'edit' lvl FROM profiles WHERE team_id=? AND deleted=0 ORDER BY id",
                                   (tid,)).fetchall()
            else:
                rows = con.execute(
                    "SELECT p.*, CASE WHEN p.created_by=? THEN 'edit' ELSE s.level END lvl FROM profiles p "
                    "LEFT JOIN shares s ON s.profile_id=p.id AND s.user_id=? WHERE p.team_id=? AND p.deleted=0 "
                    "AND (p.created_by=? OR s.user_id IS NOT NULL) ORDER BY p.id",
                    (user["id"], user["id"], tid, user["id"])).fetchall()
            return [profile_out(con, r, r["lvl"]) for r in rows]

    @app.post("/teams/{tid}/profiles")
    def create_profile(tid: int, body: ProfileIn, user=Depends(current_user)):
        now = time.time()
        with _db() as con:
            need_role(con, tid, user)
            pid = con.execute(
                "INSERT INTO profiles(team_id,name,os,folder_id,notes,tags,proxy,start_url,created_by,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (tid, body.name, body.os, body.folder_id, body.notes, json.dumps(body.tags),
                 json.dumps(body.proxy) if body.proxy else None, body.start_url, user["id"], now, now)).lastrowid
            blobs.put(f"fp-{pid}.json", json.dumps(body.fingerprint).encode())
            audit(con, tid, user["id"], "profile.created", pid, body.name)
            return profile_out(con, con.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone(), "edit")

    @app.get("/profiles/{pid}")
    def get_profile(pid: int, user=Depends(current_user)):
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            return profile_out(con, p, lvl)

    @app.get("/profiles/{pid}/launch")
    def launch_info(pid: int, user=Depends(current_user)):
        """Everything a client needs to start the browser, incl. full proxy creds (run access is enough)."""
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            out = profile_out(con, p, "edit")
        fp = blobs.get(f"fp-{pid}.json")
        out["fingerprint"] = json.loads(fp) if fp else None
        out["access"] = lvl
        return out

    @app.patch("/profiles/{pid}")
    def patch_profile(pid: int, body: ProfilePatch, user=Depends(current_user)):
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            sets, vals = [], []
            for k in ("name", "folder_id", "notes", "start_url"):
                v = getattr(body, k)
                if v is not None:
                    sets.append(f"{k}=?"); vals.append(v)
            if body.tags is not None:
                sets.append("tags=?"); vals.append(json.dumps(body.tags))
            if body.clear_proxy:
                sets.append("proxy=NULL")
            elif body.proxy is not None:
                sets.append("proxy=?"); vals.append(json.dumps(body.proxy))
            sets.append("updated=?"); vals.append(time.time())
            con.execute(f"UPDATE profiles SET {','.join(sets)} WHERE id=?", (*vals, pid))
            audit(con, p["team_id"], user["id"], "profile.edited", pid,
                  ",".join(k for k, v in body.model_dump().items() if v not in (None, False)))
            return profile_out(con, con.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone(), lvl)

    @app.delete("/profiles/{pid}")
    def delete_profile(pid: int, user=Depends(current_user)):
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            con.execute("UPDATE profiles SET deleted=1, updated=? WHERE id=?", (time.time(), pid))
            con.execute("DELETE FROM locks WHERE profile_id=?", (pid,))
            audit(con, p["team_id"], user["id"], "profile.deleted", pid, p["name"])
        return {"ok": True}

    # ------------------------------------------------------------ sharing
    @app.get("/profiles/{pid}/shares")
    def shares(pid: int, user=Depends(current_user)):
        with _db() as con:
            _, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            rows = con.execute("SELECT s.user_id,s.level,u.email,u.name FROM shares s JOIN users u ON u.id=s.user_id "
                               "WHERE profile_id=?", (pid,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/profiles/{pid}/shares")
    def share(pid: int, body: ShareIn, user=Depends(current_user)):
        if body.level not in LEVELS:
            raise HTTPException(400, "level must be run or edit")
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            if not role_in(con, p["team_id"], body.user_id):
                raise HTTPException(400, "can only share with members of the same team")
            con.execute("INSERT OR REPLACE INTO shares VALUES(?,?,?)", (pid, body.user_id, body.level))
            audit(con, p["team_id"], user["id"], "profile.shared", pid, f"user {body.user_id}:{body.level}")
        return {"ok": True}

    @app.delete("/profiles/{pid}/shares/{uid}")
    def unshare(pid: int, uid: int, user=Depends(current_user)):
        with _db() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            con.execute("DELETE FROM shares WHERE profile_id=? AND user_id=?", (pid, uid))
            audit(con, p["team_id"], user["id"], "profile.unshared", pid, f"user {uid}")
        return {"ok": True}

    # ------------------------------------------------------------ run lock
    @app.post("/profiles/{pid}/lock")
    def lock(pid: int, body: LockIn, user=Depends(current_user)):
        now = time.time()
        with _db() as con:
            p, _ = profile_access(con, pid, user)
            l = con.execute("SELECT l.*,u.email FROM locks l JOIN users u ON u.id=l.user_id WHERE profile_id=?",
                            (pid,)).fetchone()
            if l and l["expires"] > now and not (l["user_id"] == user["id"] and l["device"] == body.device):
                raise HTTPException(409, f"profile is running: {l['email']} on {l['device']}")
            tok = secrets.token_urlsafe(16)
            con.execute("INSERT OR REPLACE INTO locks VALUES(?,?,?,?,?)", (pid, user["id"], body.device, tok, now + LOCK_TTL))
            audit(con, p["team_id"], user["id"], "profile.started", pid, body.device)
        return {"lock": tok, "ttl": LOCK_TTL, "version": p["version"]}

    @app.post("/profiles/{pid}/lock/heartbeat")
    def heartbeat(pid: int, x_lock: str = Header(default=""), user=Depends(current_user)):
        with _db() as con:
            profile_access(con, pid, user)
            check_lock(con, pid, user, x_lock)
            con.execute("UPDATE locks SET expires=? WHERE profile_id=?", (time.time() + LOCK_TTL, pid))
        return {"ok": True}

    @app.delete("/profiles/{pid}/lock")
    def unlock(pid: int, x_lock: str = Header(default=""), force: bool = False, user=Depends(current_user)):
        with _db() as con:
            p, _ = profile_access(con, pid, user)
            if force:
                need_role(con, p["team_id"], user, "owner", "admin")
            else:
                check_lock(con, pid, user, x_lock)
            con.execute("DELETE FROM locks WHERE profile_id=?", (pid,))
            audit(con, p["team_id"], user["id"], "profile.force_unlocked" if force else "profile.stopped", pid)
        return {"ok": True}

    # ------------------------------------------------------------ data sync
    @app.put("/profiles/{pid}/data")
    async def upload(pid: int, request: Request, x_lock: str = Header(default=""),
                     x_base_version: int = Header(default=-1), user=Depends(current_user)):
        data = await request.body()
        with _db() as con:
            p, _ = profile_access(con, pid, user)
            check_lock(con, pid, user, x_lock)
            if x_base_version != p["version"]:
                raise HTTPException(409, f"stale base version {x_base_version}; server has {p['version']}")
            v = p["version"] + 1
            sha = hashlib.sha256(data).hexdigest()
            blobs.put(f"data-{pid}-{v}.zip", data)
            con.execute("INSERT INTO versions VALUES(?,?,?,?,?,?)", (pid, v, sha, len(data), user["id"], time.time()))
            con.execute("UPDATE profiles SET version=?, updated=? WHERE id=?", (v, time.time(), pid))
            old = con.execute("SELECT version FROM versions WHERE profile_id=? ORDER BY version DESC LIMIT -1 OFFSET 5",
                              (pid,)).fetchall()
            for o in old:  # keep last 5 snapshots
                blobs.delete(f"data-{pid}-{o['version']}.zip")
                con.execute("DELETE FROM versions WHERE profile_id=? AND version=?", (pid, o["version"]))
            audit(con, p["team_id"], user["id"], "profile.synced", pid, f"v{v} {len(data)}B")
        return {"version": v, "sha256": sha}

    @app.get("/profiles/{pid}/data")
    def download(pid: int, version: Optional[int] = None, user=Depends(current_user)):
        with _db() as con:
            p, _ = profile_access(con, pid, user)
            v = version or p["version"]
        if v == 0:
            return Response(status_code=204, headers={"X-Version": "0"})
        data = blobs.get(f"data-{pid}-{v}.zip")
        if data is None:
            raise HTTPException(404, "version not found")
        return Response(data, media_type="application/zip", headers={"X-Version": str(v)})

    @app.get("/profiles/{pid}/versions")
    def versions(pid: int, user=Depends(current_user)):
        with _db() as con:
            profile_access(con, pid, user)
            rows = con.execute("SELECT v.version,v.size,v.sha256,v.created,u.email FROM versions v "
                               "JOIN users u ON u.id=v.created_by WHERE profile_id=? ORDER BY version DESC",
                               (pid,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def app_factory() -> FastAPI:  # for `uvicorn --factory loft.server:app_factory`
    return create_app()
