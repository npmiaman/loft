"""Loft cloud server: accounts, teams, roles, encrypted profile sync, run locks, key rotation, audit.

Metadata: SQLite (default) or Postgres.  Blobs: a directory (default) or S3.
The server never sees plaintext fingerprints, proxies, templates, runner keys or
browser data: clients encrypt them with the team key (see crypto.py / client.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .storage import Conflict, Database, open_blobs

ROLES = ("owner", "admin", "member")
LEVELS = ("run", "edit")  # per-profile share level for plain members
LOCK_TTL = 120  # seconds; clients heartbeat every ~30s
KEEP_VERSIONS = 5
# Profile columns without the (large) fingerprint ciphertext.
PCOLS = ("id, team_id, name, os, folder_id, notes, tags, proxy, start_url, canvas, created_by, created, updated, "
         "version, deleted")


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
    proxy: Optional[str] = None  # client-encrypted JSON
    start_url: str = ""
    canvas: str = "noise"  # noise | real
    fingerprint: str  # client-encrypted JSON


class ProfilePatch(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    proxy: Optional[str] = None  # client-encrypted JSON
    clear_proxy: bool = False
    start_url: Optional[str] = None
    canvas: Optional[str] = None


class ShareIn(BaseModel):
    user_id: int
    level: str = "run"


class KeyIn(BaseModel):
    wrapped: str
    epoch: int


class KeypairIn(BaseModel):
    pubkey: str
    privkey: str  # wrapped with the user's vault key


class TemplateIn(BaseModel):
    name: str
    payload: str  # client-encrypted settings JSON


class RunnerIn(BaseModel):
    name: str
    url: str
    secret: str  # client-encrypted runner API key


class LockIn(BaseModel):
    device: str = "unknown"


class StageIn(BaseModel):
    fingerprint: str
    proxy: Optional[str] = None


class Grant(BaseModel):
    user_id: int
    sealed: str  # new team key sealed to the member's public key


class Reenc(BaseModel):
    id: int
    payload: str


class CommitIn(BaseModel):
    epoch: int
    self_wrapped: str  # new team key wrapped with the rotator's vault key
    grants: list[Grant] = []
    templates: list[Reenc] = []
    runners: list[Reenc] = []


# ---------------------------------------------------------------- app
def create_app(data_dir: str | Path | None = None, open_signup: Optional[bool] = None,
               db_url: Optional[str] = None, blobs_url: Optional[str] = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("LOFT_DATA", "~/.loft-server")).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    db = Database(db_url or os.environ.get("LOFT_DB") or str(data / "loft.db"))
    blobs = open_blobs(blobs_url or os.environ.get("LOFT_BLOBS") or data / "blobs")
    if open_signup is None:
        open_signup = os.environ.get("LOFT_OPEN_SIGNUP", "0") == "1"

    app = FastAPI(title="Loft", version="0.2")
    app.state.db, app.state.blobs = db, blobs

    def audit(con, team_id, user_id, action, profile_id=None, detail=""):
        con.execute("INSERT INTO audit(team_id,user_id,profile_id,action,detail,at) VALUES(?,?,?,?,?,?)",
                    (team_id, user_id, profile_id, action, detail, time.time()))

    def issue_token(con, user_id: int) -> str:
        tok = secrets.token_urlsafe(32)
        con.execute("INSERT INTO tokens(hash,user_id,created) VALUES(?,?,?)", (_sha(tok), user_id, time.time()))
        return tok

    def current_user(authorization: str = Header(default="")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        with db.tx() as con:
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

    def team_row(con, tid: int):
        return con.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()

    def not_rotating(con, tid: int) -> None:
        if team_row(con, tid)["rotating_by"] is not None:
            raise HTTPException(423, "team key rotation in progress; try again in a minute")

    def check_epoch(con, tid: int, header: str) -> None:
        """Ciphertext writes must be made with the team's current key."""
        epoch = team_row(con, tid)["key_epoch"]
        if str(header).strip() != str(epoch):
            raise HTTPException(409, f"team key rotated (now epoch {epoch}); refresh the key and retry")

    def profile_access(con, pid: int, user: dict) -> tuple[dict, str]:
        """Return (profile, level) where level is 'edit' or 'run'. 404 if no access."""
        p = con.execute(f"SELECT {PCOLS} FROM profiles WHERE id=? AND deleted=0", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "profile not found")
        role = role_in(con, p["team_id"], user["id"])
        if role in ("owner", "admin") or (role and p["created_by"] == user["id"]):
            return p, "edit"
        if role:
            s = con.execute("SELECT level FROM shares WHERE profile_id=? AND user_id=?", (pid, user["id"])).fetchone()
            if s:
                return p, s["level"]
        raise HTTPException(404, "profile not found")

    def need_edit(level: str):
        if level != "edit":
            raise HTTPException(403, "you can run this profile but not edit it")

    def profile_out(con, p, level) -> dict:
        lock = con.execute("SELECT l.user_id,l.device,l.expires,u.name,u.email FROM locks l JOIN users u "
                           "ON u.id=l.user_id WHERE profile_id=? AND expires>?", (p["id"], time.time())).fetchone()
        return {
            "id": p["id"], "team_id": p["team_id"], "name": p["name"], "os": p["os"],
            "folder_id": p["folder_id"], "notes": p["notes"], "tags": json.loads(p["tags"]),
            "proxy": p["proxy"], "start_url": p["start_url"], "canvas": p["canvas"],
            "created_by": p["created_by"], "updated": p["updated"], "version": p["version"], "access": level,
            "running": ({"user_id": lock["user_id"], "by": lock["name"] or lock["email"],
                         "device": lock["device"]} if lock else None),
        }

    def check_lock(con, pid: int, user: dict, lock_token: str):
        l = con.execute("SELECT * FROM locks WHERE profile_id=? AND expires>?", (pid, time.time())).fetchone()
        if not l or l["user_id"] != user["id"] or not secrets.compare_digest(l["token"], lock_token or ""):
            raise HTTPException(423, "you must hold the run lock to upload profile data")

    def owner_count(con, tid: int) -> int:
        return con.execute("SELECT COUNT(*) c FROM members WHERE team_id=? AND role='owner'", (tid,)).fetchone()["c"]

    def use_invite(con, code: str, uid: int):
        inv = con.execute("UPDATE invites SET used_by=? WHERE code=? AND used_by IS NULL AND expires>? "
                          "RETURNING team_id, role", (uid, code, time.time())).fetchone()
        if not inv:
            raise HTTPException(400, "invite invalid or expired")
        not_rotating(con, inv["team_id"])
        return inv

    # ------------------------------------------------------------ auth
    @app.post("/auth/register")
    def register(body: Register):
        email = body.email.strip().lower()
        with db.tx() as con:
            first = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
            if not body.invite and not (open_signup or first):
                raise HTTPException(403, "signup needs an invite")
            salt = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")
            try:
                uid = con.insert("INSERT INTO users(email,name,pw_hash,vault_salt,created) VALUES(?,?,?,?,?)",
                                 (email, body.name or email.split("@")[0], _hash_pw(body.password), salt, time.time()))
            except Conflict:
                raise HTTPException(409, "email already registered")
            inv = None
            if body.invite:
                inv = use_invite(con, body.invite, uid)
                con.execute("INSERT INTO members(team_id,user_id,role) VALUES(?,?,?)", (inv["team_id"], uid, inv["role"]))
                audit(con, inv["team_id"], uid, "member.joined", detail=inv["role"])
            return {"token": issue_token(con, uid), "user_id": uid, "vault_salt": salt,
                    "team_id": inv["team_id"] if inv else None}

    @app.post("/auth/login")
    def login(body: Login):
        with db.tx() as con:
            u = con.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
            if not u or not _check_pw(body.password, u["pw_hash"]):
                raise HTTPException(401, "wrong email or password")
            return {"token": issue_token(con, u["id"]), "user_id": u["id"], "vault_salt": u["vault_salt"]}

    @app.post("/auth/logout")
    def logout(authorization: str = Header(default="")):
        with db.tx() as con:
            con.execute("DELETE FROM tokens WHERE hash=?", (_sha(authorization[7:]),))
        return {"ok": True}

    @app.get("/me")
    def me(user=Depends(current_user)):
        with db.tx() as con:
            u = con.execute("SELECT pubkey, privkey FROM users WHERE id=?", (user["id"],)).fetchone()
            teams = con.execute("SELECT t.id,t.name,m.role,t.key_epoch,t.rotating_by FROM teams t JOIN members m "
                                "ON m.team_id=t.id WHERE m.user_id=? ORDER BY t.id", (user["id"],)).fetchall()
        return {**user, "pubkey": u["pubkey"], "privkey": u["privkey"],
                "teams": [{**dict(t), "rotating": t["rotating_by"] is not None} for t in teams]}

    @app.put("/me/keypair")
    def put_keypair(body: KeypairIn, user=Depends(current_user)):
        with db.tx() as con:
            r = con.execute("UPDATE users SET pubkey=?, privkey=? WHERE id=? AND pubkey IS NULL RETURNING id",
                            (body.pubkey, body.privkey, user["id"])).fetchone()
        if not r:
            raise HTTPException(409, "keypair already set")
        return {"ok": True}

    # ------------------------------------------------------------ teams
    @app.post("/teams")
    def create_team(body: TeamIn, user=Depends(current_user)):
        with db.tx() as con:
            tid = con.insert("INSERT INTO teams(name,created) VALUES(?,?)", (body.name, time.time()))
            con.execute("INSERT INTO members(team_id,user_id,role) VALUES(?,?,?)", (tid, user["id"], "owner"))
            audit(con, tid, user["id"], "team.created", detail=body.name)
        return {"id": tid, "name": body.name, "role": "owner", "key_epoch": 1}

    @app.get("/teams/{tid}/members")
    def members(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            rows = con.execute("SELECT u.id,u.email,u.name,m.role FROM members m JOIN users u ON u.id=m.user_id "
                               "WHERE m.team_id=? ORDER BY u.id", (tid,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/teams/{tid}/invites")
    def invite(tid: int, body: InviteIn, user=Depends(current_user)):
        if body.role not in ("admin", "member"):
            raise HTTPException(400, "role must be admin or member")
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            not_rotating(con, tid)
            code = secrets.token_urlsafe(12)
            con.execute("INSERT INTO invites(code,team_id,role,created_by,expires) VALUES(?,?,?,?,?)",
                        (code, tid, body.role, user["id"], time.time() + body.days * 86400))
            audit(con, tid, user["id"], "invite.created", detail=body.role)
            epoch = team_row(con, tid)["key_epoch"]
        return {"code": code, "role": body.role, "epoch": epoch}

    @app.post("/invites/{code}/accept")
    def accept(code: str, user=Depends(current_user)):
        with db.tx() as con:
            pending = con.execute("SELECT team_id FROM invites WHERE code=?", (code,)).fetchone()
            if pending and role_in(con, pending["team_id"], user["id"]):
                raise HTTPException(409, "already a member")
            inv = use_invite(con, code, user["id"])
            con.execute("INSERT INTO members(team_id,user_id,role) VALUES(?,?,?)", (inv["team_id"], user["id"], inv["role"]))
            audit(con, inv["team_id"], user["id"], "member.joined", detail=inv["role"])
        return {"team_id": inv["team_id"], "role": inv["role"]}

    @app.patch("/teams/{tid}/members/{uid}")
    def set_role(tid: int, uid: int, body: RoleIn, user=Depends(current_user)):
        if body.role not in ROLES:
            raise HTTPException(400, "bad role")
        with db.tx() as con:
            mine = need_role(con, tid, user, "owner", "admin")
            theirs = role_in(con, tid, uid)
            if theirs is None:
                raise HTTPException(404, "not a member")
            if "owner" in (body.role, theirs) and mine != "owner":
                raise HTTPException(403, "only an owner can grant or remove owner")
            if theirs == "owner" and body.role != "owner" and owner_count(con, tid) <= 1:
                raise HTTPException(400, "team needs at least one owner")
            con.execute("UPDATE members SET role=? WHERE team_id=? AND user_id=?", (body.role, tid, uid))
            audit(con, tid, user["id"], "member.role", detail=f"user {uid} -> {body.role}")
        return {"ok": True}

    @app.delete("/teams/{tid}/members/{uid}")
    def remove_member(tid: int, uid: int, user=Depends(current_user)):
        with db.tx() as con:
            mine = need_role(con, tid, user) if uid == user["id"] else need_role(con, tid, user, "owner", "admin")
            theirs = role_in(con, tid, uid)
            if theirs is None:
                raise HTTPException(404, "not a member")
            if theirs == "owner":
                if mine != "owner":
                    raise HTTPException(403, "only an owner can remove an owner")
                if owner_count(con, tid) <= 1:
                    raise HTTPException(400, "team needs at least one owner")
            con.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, uid))
            con.execute("DELETE FROM team_keys WHERE team_id=? AND user_id=?", (tid, uid))
            con.execute("DELETE FROM shares WHERE user_id=? AND profile_id IN (SELECT id FROM profiles WHERE team_id=?)",
                        (uid, tid))
            con.execute("DELETE FROM locks WHERE user_id=? AND profile_id IN (SELECT id FROM profiles WHERE team_id=?)",
                        (uid, tid))
            audit(con, tid, user["id"], "member.removed", detail=f"user {uid}")
        return {"ok": True, "rotate_recommended": True}

    @app.get("/teams/{tid}/audit")
    def get_audit(tid: int, limit: int = 200, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            rows = con.execute("SELECT a.*,u.email FROM audit a LEFT JOIN users u ON u.id=a.user_id "
                               "WHERE team_id=? ORDER BY a.id DESC LIMIT ?", (tid, min(limit, 1000))).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ folders
    @app.get("/teams/{tid}/folders")
    def folders(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            return [dict(r) for r in con.execute("SELECT id,name FROM folders WHERE team_id=? ORDER BY name", (tid,))]

    @app.post("/teams/{tid}/folders")
    def make_folder(tid: int, body: FolderIn, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            fid = con.insert("INSERT INTO folders(team_id,name) VALUES(?,?)", (tid, body.name))
        return {"id": fid, "name": body.name}

    # ------------------------------------------------------------ profiles
    @app.get("/teams/{tid}/profiles")
    def list_profiles(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            role = need_role(con, tid, user)
            if role in ("owner", "admin"):
                rows = con.execute(f"SELECT {PCOLS}, 'edit' lvl FROM profiles WHERE team_id=? AND deleted=0 ORDER BY id",
                                   (tid,)).fetchall()
            else:
                cols = ", ".join("p." + c.strip() for c in PCOLS.split(","))
                rows = con.execute(
                    f"SELECT {cols}, CASE WHEN p.created_by=? THEN 'edit' ELSE s.level END lvl FROM profiles p "
                    "LEFT JOIN shares s ON s.profile_id=p.id AND s.user_id=? WHERE p.team_id=? AND p.deleted=0 "
                    "AND (p.created_by=? OR s.user_id IS NOT NULL) ORDER BY p.id",
                    (user["id"], user["id"], tid, user["id"])).fetchall()
            return [profile_out(con, r, r["lvl"]) for r in rows]

    @app.post("/teams/{tid}/profiles")
    def create_profile(tid: int, body: ProfileIn, x_key_epoch: str = Header(default=""), user=Depends(current_user)):
        if body.canvas not in ("noise", "real"):
            raise HTTPException(400, "canvas must be noise or real")
        now = time.time()
        with db.tx() as con:
            need_role(con, tid, user)
            not_rotating(con, tid)
            check_epoch(con, tid, x_key_epoch)
            pid = con.insert(
                "INSERT INTO profiles(team_id,name,os,folder_id,notes,tags,proxy,start_url,canvas,fingerprint,"
                "created_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, body.name, body.os, body.folder_id, body.notes, json.dumps(body.tags), body.proxy,
                 body.start_url, body.canvas, body.fingerprint, user["id"], now, now))
            audit(con, tid, user["id"], "profile.created", pid, body.name)
            return profile_out(con, con.execute(f"SELECT {PCOLS} FROM profiles WHERE id=?", (pid,)).fetchone(), "edit")

    @app.get("/profiles/{pid}")
    def get_profile(pid: int, user=Depends(current_user)):
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            return profile_out(con, p, lvl)

    @app.get("/profiles/{pid}/launch")
    def launch_info(pid: int, user=Depends(current_user)):
        """Everything a client needs to start the browser (run access is enough)."""
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            out = profile_out(con, p, "edit")
            out["fingerprint"] = con.execute("SELECT fingerprint FROM profiles WHERE id=?", (pid,)).fetchone()["fingerprint"]
            out["key_epoch"] = team_row(con, p["team_id"])["key_epoch"]
        out["access"] = lvl
        return out

    @app.patch("/profiles/{pid}")
    def patch_profile(pid: int, body: ProfilePatch, x_key_epoch: str = Header(default=""), user=Depends(current_user)):
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            sets, vals = [], []
            if body.canvas is not None and body.canvas not in ("noise", "real"):
                raise HTTPException(400, "canvas must be noise or real")
            for k in ("name", "folder_id", "notes", "start_url", "canvas"):
                v = getattr(body, k)
                if v is not None:
                    sets.append(f"{k}=?")
                    vals.append(v)
            if body.tags is not None:
                sets.append("tags=?")
                vals.append(json.dumps(body.tags))
            if body.clear_proxy:
                sets.append("proxy=NULL")
            elif body.proxy is not None:
                not_rotating(con, p["team_id"])
                check_epoch(con, p["team_id"], x_key_epoch)
                sets.append("proxy=?")
                vals.append(body.proxy)
            sets.append("updated=?")
            vals.append(time.time())
            con.execute(f"UPDATE profiles SET {','.join(sets)} WHERE id=?", (*vals, pid))
            audit(con, p["team_id"], user["id"], "profile.edited", pid,
                  ",".join(k for k, v in body.model_dump().items() if v not in (None, False)))
            return profile_out(con, con.execute(f"SELECT {PCOLS} FROM profiles WHERE id=?", (pid,)).fetchone(), lvl)

    @app.delete("/profiles/{pid}")
    def delete_profile(pid: int, user=Depends(current_user)):
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            con.execute("UPDATE profiles SET deleted=1, updated=? WHERE id=?", (time.time(), pid))
            con.execute("DELETE FROM locks WHERE profile_id=?", (pid,))
            audit(con, p["team_id"], user["id"], "profile.deleted", pid, p["name"])
        return {"ok": True}

    # ------------------------------------------------------------ sharing
    @app.get("/profiles/{pid}/shares")
    def shares(pid: int, user=Depends(current_user)):
        with db.tx() as con:
            _, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            rows = con.execute("SELECT s.user_id,s.level,u.email,u.name FROM shares s JOIN users u ON u.id=s.user_id "
                               "WHERE profile_id=?", (pid,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/profiles/{pid}/shares")
    def share(pid: int, body: ShareIn, user=Depends(current_user)):
        if body.level not in LEVELS:
            raise HTTPException(400, "level must be run or edit")
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            if not role_in(con, p["team_id"], body.user_id):
                raise HTTPException(400, "can only share with members of the same team")
            con.execute("INSERT INTO shares(profile_id,user_id,level) VALUES(?,?,?) "
                        "ON CONFLICT(profile_id,user_id) DO UPDATE SET level=excluded.level",
                        (pid, body.user_id, body.level))
            audit(con, p["team_id"], user["id"], "profile.shared", pid, f"user {body.user_id}:{body.level}")
        return {"ok": True}

    @app.delete("/profiles/{pid}/shares/{uid}")
    def unshare(pid: int, uid: int, user=Depends(current_user)):
        with db.tx() as con:
            p, lvl = profile_access(con, pid, user)
            need_edit(lvl)
            con.execute("DELETE FROM shares WHERE profile_id=? AND user_id=?", (pid, uid))
            audit(con, p["team_id"], user["id"], "profile.unshared", pid, f"user {uid}")
        return {"ok": True}

    # ------------------------------------------------------------ run lock
    @app.post("/profiles/{pid}/lock")
    def lock(pid: int, body: LockIn, user=Depends(current_user)):
        now = time.time()
        tok = secrets.token_urlsafe(16)
        with db.tx() as con:
            p, _ = profile_access(con, pid, user)
            not_rotating(con, p["team_id"])
            # Atomic: take the lock if free, expired, or already ours on this device.
            got = con.execute(
                "INSERT INTO locks(profile_id,user_id,device,token,expires) VALUES(?,?,?,?,?) "
                "ON CONFLICT(profile_id) DO UPDATE SET user_id=excluded.user_id, device=excluded.device, "
                "token=excluded.token, expires=excluded.expires "
                "WHERE locks.expires <= ? OR (locks.user_id = excluded.user_id AND locks.device = excluded.device) "
                "RETURNING token", (pid, user["id"], body.device, tok, now + LOCK_TTL, now)).fetchone()
            if not got:
                l = con.execute("SELECT l.device,u.email FROM locks l JOIN users u ON u.id=l.user_id WHERE profile_id=?",
                                (pid,)).fetchone()
                raise HTTPException(409, f"profile is running: {l['email']} on {l['device']}")
            audit(con, p["team_id"], user["id"], "profile.started", pid, body.device)
            epoch = team_row(con, p["team_id"])["key_epoch"]
        return {"lock": tok, "ttl": LOCK_TTL, "version": p["version"], "key_epoch": epoch}

    @app.post("/profiles/{pid}/lock/heartbeat")
    def heartbeat(pid: int, x_lock: str = Header(default=""), user=Depends(current_user)):
        with db.tx() as con:
            profile_access(con, pid, user)
            check_lock(con, pid, user, x_lock)
            con.execute("UPDATE locks SET expires=? WHERE profile_id=?", (time.time() + LOCK_TTL, pid))
        return {"ok": True}

    @app.delete("/profiles/{pid}/lock")
    def unlock(pid: int, x_lock: str = Header(default=""), force: bool = False, user=Depends(current_user)):
        with db.tx() as con:
            p, _ = profile_access(con, pid, user)
            if force:
                need_role(con, p["team_id"], user, "owner", "admin")
            else:
                check_lock(con, pid, user, x_lock)
            con.execute("DELETE FROM locks WHERE profile_id=?", (pid,))
            audit(con, p["team_id"], user["id"], "profile.force_unlocked" if force else "profile.stopped", pid)
        return {"ok": True}

    # ------------------------------------------------------------ data sync
    def prune_versions(con, pid: int) -> list[str]:
        old = con.execute("SELECT version FROM versions WHERE profile_id=? ORDER BY version DESC LIMIT 100000 OFFSET ?",
                          (pid, KEEP_VERSIONS)).fetchall()
        for o in old:
            con.execute("DELETE FROM versions WHERE profile_id=? AND version=?", (pid, o["version"]))
        return [f"data-{pid}-{o['version']}.zip" for o in old]

    @app.put("/profiles/{pid}/data")
    async def upload(pid: int, request: Request, x_lock: str = Header(default=""), x_base_version: int = Header(default=-1),
                     x_key_epoch: str = Header(default=""), user=Depends(current_user)):
        data = await request.body()
        sha = hashlib.sha256(data).hexdigest()
        with db.tx() as con:
            p, _ = profile_access(con, pid, user)
            check_lock(con, pid, user, x_lock)
            check_epoch(con, p["team_id"], x_key_epoch)
            row = con.execute("UPDATE profiles SET version=version+1, updated=? WHERE id=? AND version=? RETURNING version",
                              (time.time(), pid, x_base_version)).fetchone()
            if not row:
                raise HTTPException(409, f"stale base version {x_base_version}; server has {p['version']}")
            v = row["version"]
            blobs.put(f"data-{pid}-{v}.zip", data)  # inside the tx: a failed write rolls the version back
            con.execute("INSERT INTO versions(profile_id,version,sha256,size,created_by,created) VALUES(?,?,?,?,?,?)",
                        (pid, v, sha, len(data), user["id"], time.time()))
            drop = prune_versions(con, pid)
            audit(con, p["team_id"], user["id"], "profile.synced", pid, f"v{v} {len(data)}B")
        for k in drop:
            blobs.delete(k)
        return {"version": v, "sha256": sha}

    @app.get("/profiles/{pid}/data")
    def download(pid: int, version: Optional[int] = None, user=Depends(current_user)):
        with db.tx() as con:
            p, _ = profile_access(con, pid, user)
            v = version or p["version"]
        if v == 0:
            return Response(status_code=204, headers={"X-Version": "0"})
        data = blobs.get(f"data-{pid}-{v}.zip")
        if data is None:
            raise HTTPException(404, "version not found")
        return Response(data, media_type="application/octet-stream", headers={"X-Version": str(v)})

    @app.get("/profiles/{pid}/versions")
    def versions(pid: int, user=Depends(current_user)):
        with db.tx() as con:
            profile_access(con, pid, user)
            rows = con.execute("SELECT v.version,v.size,v.sha256,v.created,u.email FROM versions v "
                               "JOIN users u ON u.id=v.created_by WHERE profile_id=? ORDER BY version DESC",
                               (pid,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ team keys
    @app.get("/teams/{tid}/keys/me")
    def my_key(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            epoch = team_row(con, tid)["key_epoch"]
            r = con.execute("SELECT epoch, kind, wrapped FROM team_keys WHERE team_id=? AND user_id=?",
                            (tid, user["id"])).fetchone()
        if not r or r["epoch"] != epoch:
            raise HTTPException(404, "no current team key for you: ask an admin to rotate the key or re-invite you")
        return dict(r)

    @app.put("/teams/{tid}/keys/me")
    def put_key(tid: int, body: KeyIn, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            if body.epoch != team_row(con, tid)["key_epoch"]:
                raise HTTPException(409, "stale key epoch")
            con.execute("INSERT INTO team_keys(team_id,user_id,epoch,kind,wrapped) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(team_id,user_id) DO UPDATE SET epoch=excluded.epoch, kind=excluded.kind, "
                        "wrapped=excluded.wrapped", (tid, user["id"], body.epoch, "vault", body.wrapped))
        return {"ok": True}

    # ------------------------------------------------------------ key rotation
    def rotator(con, tid: int, user: dict, epoch: Optional[int] = None):
        t = team_row(con, tid)
        if t["rotating_by"] != user["id"]:
            raise HTTPException(409, "no rotation in progress by you")
        if epoch is not None and t["rotating_epoch"] != epoch:
            raise HTTPException(409, "rotation epoch mismatch")
        return t

    @app.post("/teams/{tid}/rotation/start")
    def rotation_start(tid: int, user=Depends(current_user)):
        now = time.time()
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            live = con.execute("SELECT p.name, l.device FROM locks l JOIN profiles p ON p.id=l.profile_id "
                               "WHERE p.team_id=? AND l.expires>?", (tid, now)).fetchall()
            if live:
                raise HTTPException(409, "stop running profiles first: " +
                                    ", ".join(f"{r['name']} ({r['device']})" for r in live))
            t = con.execute("UPDATE teams SET rotating_by=?, rotating_epoch=key_epoch+1 WHERE id=? AND rotating_by IS NULL "
                            "RETURNING rotating_epoch", (user["id"], tid)).fetchone()
            if not t:
                raise HTTPException(409, "a rotation is already in progress")
            con.execute("DELETE FROM rotation_stage WHERE team_id=?", (tid,))
            profiles = [dict(r) for r in con.execute("SELECT id, version FROM profiles WHERE team_id=? AND deleted=0 "
                                                     "ORDER BY id", (tid,))]
            mems = [dict(r) for r in con.execute("SELECT u.id user_id, u.pubkey FROM members m JOIN users u "
                                                 "ON u.id=m.user_id WHERE m.team_id=?", (tid,))]
            audit(con, tid, user["id"], "team.key_rotation_started", detail=f"epoch {t['rotating_epoch']}")
        return {"epoch": t["rotating_epoch"], "profiles": profiles, "members": mems}

    @app.put("/teams/{tid}/rotation/profiles/{pid}")
    def rotation_stage_profile(tid: int, pid: int, body: StageIn, user=Depends(current_user)):
        with db.tx() as con:
            t = rotator(con, tid, user)
            if not con.execute("SELECT 1 FROM profiles WHERE id=? AND team_id=? AND deleted=0", (pid, tid)).fetchone():
                raise HTTPException(404, "profile not in team")
            has = 1 if blobs.get(f"stage-{tid}-{t['rotating_epoch']}-{pid}.zip") is not None else 0
            con.execute("INSERT INTO rotation_stage(team_id,profile_id,epoch,fingerprint,proxy,has_data) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(team_id,profile_id) DO UPDATE SET epoch=excluded.epoch, "
                        "fingerprint=excluded.fingerprint, proxy=excluded.proxy, has_data=excluded.has_data",
                        (tid, pid, t["rotating_epoch"], body.fingerprint, body.proxy, has))
        return {"ok": True, "has_data": bool(has)}

    @app.put("/teams/{tid}/rotation/profiles/{pid}/data")
    async def rotation_stage_data(tid: int, pid: int, request: Request, user=Depends(current_user)):
        data = await request.body()
        with db.tx() as con:
            t = rotator(con, tid, user)
            if not con.execute("SELECT 1 FROM profiles WHERE id=? AND team_id=? AND deleted=0", (pid, tid)).fetchone():
                raise HTTPException(404, "profile not in team")
        blobs.put(f"stage-{tid}-{t['rotating_epoch']}-{pid}.zip", data)
        return {"ok": True}

    @app.post("/teams/{tid}/rotation/commit")
    def rotation_commit(tid: int, body: CommitIn, user=Depends(current_user)):
        now = time.time()
        drop: list[str] = []
        with db.tx() as con:
            t = rotator(con, tid, user, body.epoch)
            live = {r["id"]: r for r in con.execute(f"SELECT {PCOLS} FROM profiles WHERE team_id=? AND deleted=0", (tid,))}
            staged = {r["profile_id"]: r for r in con.execute("SELECT * FROM rotation_stage WHERE team_id=? AND epoch=?",
                                                              (tid, body.epoch))}
            missing = sorted(set(live) - set(staged))
            if missing:
                raise HTTPException(400, f"profiles not re-encrypted: {missing}")
            for pid, p in live.items():
                if p["version"] > 0 and not staged[pid]["has_data"]:
                    raise HTTPException(400, f"profile {pid} has data but none was staged")
            tpl_ids = {r["id"] for r in con.execute("SELECT id FROM templates WHERE team_id=?", (tid,))}
            run_ids = {r["id"] for r in con.execute("SELECT id FROM runners WHERE team_id=?", (tid,))}
            if {x.id for x in body.templates} != tpl_ids or {x.id for x in body.runners} != run_ids:
                raise HTTPException(400, "every template and runner must be re-encrypted")
            mems = {r["user_id"]: r["pubkey"] for r in con.execute(
                "SELECT u.id user_id, u.pubkey FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=?", (tid,))}
            grants = {g.user_id: g.sealed for g in body.grants}
            if set(grants) - set(mems):
                raise HTTPException(400, "grant for a non-member")
            for pid, st in staged.items():
                if pid not in live:
                    continue
                con.execute("UPDATE profiles SET fingerprint=?, proxy=?, updated=? WHERE id=?",
                            (st["fingerprint"], st["proxy"], now, pid))
                if st["has_data"]:
                    v = con.execute("UPDATE profiles SET version=version+1 WHERE id=? RETURNING version", (pid,)).fetchone()["version"]
                    src = f"stage-{tid}-{body.epoch}-{pid}.zip"
                    data = blobs.get(src)
                    blobs.put(f"data-{pid}-{v}.zip", data)
                    old = con.execute("SELECT version FROM versions WHERE profile_id=?", (pid,)).fetchall()
                    drop += [f"data-{pid}-{o['version']}.zip" for o in old] + [src]
                    con.execute("DELETE FROM versions WHERE profile_id=?", (pid,))  # old ones are under the old key
                    con.execute("INSERT INTO versions(profile_id,version,sha256,size,created_by,created) VALUES(?,?,?,?,?,?)",
                                (pid, v, hashlib.sha256(data).hexdigest(), len(data), user["id"], now))
            for x in body.templates:
                con.execute("UPDATE templates SET payload=? WHERE id=?", (x.payload, x.id))
            for x in body.runners:
                con.execute("UPDATE runners SET secret=? WHERE id=?", (x.payload, x.id))
            con.execute("DELETE FROM team_keys WHERE team_id=?", (tid,))
            con.execute("INSERT INTO team_keys(team_id,user_id,epoch,kind,wrapped) VALUES(?,?,?,?,?)",
                        (tid, user["id"], body.epoch, "vault", body.self_wrapped))
            for uid, sealed in grants.items():
                if uid != user["id"]:
                    con.execute("INSERT INTO team_keys(team_id,user_id,epoch,kind,wrapped) VALUES(?,?,?,?,?)",
                                (tid, uid, body.epoch, "sealed", sealed))
            revoked = con.execute("DELETE FROM invites WHERE team_id=? AND used_by IS NULL RETURNING code", (tid,)).fetchall()
            con.execute("DELETE FROM rotation_stage WHERE team_id=?", (tid,))
            con.execute("UPDATE teams SET key_epoch=?, rotating_by=NULL, rotating_epoch=NULL WHERE id=?", (body.epoch, tid))
            no_key = sorted(u for u in mems if u != user["id"] and u not in grants)
            audit(con, tid, user["id"], "team.key_rotated",
                  detail=f"epoch {body.epoch}; {len(grants)} members re-keyed; {len(revoked)} invites revoked"
                         + (f"; no key for users {no_key}" if no_key else ""))
        for k in drop:
            blobs.delete(k)
        return {"epoch": body.epoch, "rekeyed": sorted(grants), "without_key": no_key, "invites_revoked": len(revoked)}

    @app.post("/teams/{tid}/rotation/abort")
    def rotation_abort(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            role = need_role(con, tid, user)
            t = team_row(con, tid)
            if t["rotating_by"] is None:
                return {"ok": True}
            if t["rotating_by"] != user["id"] and role != "owner":
                raise HTTPException(403, "only the rotator or an owner can abort")
            epoch = t["rotating_epoch"]
            con.execute("DELETE FROM rotation_stage WHERE team_id=?", (tid,))
            con.execute("UPDATE teams SET rotating_by=NULL, rotating_epoch=NULL WHERE id=?", (tid,))
            audit(con, tid, user["id"], "team.key_rotation_aborted", detail=f"epoch {epoch}")
        for k in blobs.keys(f"stage-{tid}-{epoch}-"):
            blobs.delete(k)
        return {"ok": True}

    # ------------------------------------------------------------ templates
    @app.get("/teams/{tid}/templates")
    def templates(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            return [dict(r) for r in con.execute("SELECT id,name,payload FROM templates WHERE team_id=? ORDER BY name",
                                                 (tid,))]

    @app.post("/teams/{tid}/templates")
    def add_template(tid: int, body: TemplateIn, x_key_epoch: str = Header(default=""), user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            not_rotating(con, tid)
            check_epoch(con, tid, x_key_epoch)
            i = con.insert("INSERT INTO templates(team_id,name,payload,created_by,created) VALUES(?,?,?,?,?)",
                           (tid, body.name, body.payload, user["id"], time.time()))
            audit(con, tid, user["id"], "template.created", detail=body.name)
        return {"id": i, "name": body.name}

    @app.delete("/templates/{xid}")
    def del_template(xid: int, user=Depends(current_user)):
        with db.tx() as con:
            r = con.execute("SELECT * FROM templates WHERE id=?", (xid,)).fetchone()
            if not r:
                raise HTTPException(404, "template not found")
            need_role(con, r["team_id"], user, "owner", "admin")
            con.execute("DELETE FROM templates WHERE id=?", (xid,))
            audit(con, r["team_id"], user["id"], "template.deleted", detail=r["name"])
        return {"ok": True}

    # ------------------------------------------------------------ cloud runners
    @app.get("/teams/{tid}/runners")
    def runners(tid: int, user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user)
            return [dict(r) for r in con.execute("SELECT id,name,url,secret FROM runners WHERE team_id=? ORDER BY id", (tid,))]

    @app.post("/teams/{tid}/runners")
    def add_runner(tid: int, body: RunnerIn, x_key_epoch: str = Header(default=""), user=Depends(current_user)):
        with db.tx() as con:
            need_role(con, tid, user, "owner", "admin")
            not_rotating(con, tid)
            check_epoch(con, tid, x_key_epoch)
            i = con.insert("INSERT INTO runners(team_id,name,url,secret,created_by) VALUES(?,?,?,?,?)",
                           (tid, body.name, body.url.rstrip("/"), body.secret, user["id"]))
            audit(con, tid, user["id"], "runner.added", detail=f"{body.name} {body.url}")
        return {"id": i}

    @app.delete("/runners/{rid}")
    def del_runner(rid: int, user=Depends(current_user)):
        with db.tx() as con:
            r = con.execute("SELECT * FROM runners WHERE id=?", (rid,)).fetchone()
            if not r:
                raise HTTPException(404, "runner not found")
            need_role(con, r["team_id"], user, "owner", "admin")
            con.execute("DELETE FROM runners WHERE id=?", (rid,))
            audit(con, r["team_id"], user["id"], "runner.removed", detail=r["name"])
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True, "db": db.dialect, "blobs": type(blobs).__name__}

    return app


def app_factory() -> FastAPI:  # for `uvicorn --factory loft.server:app_factory`
    return create_app()
