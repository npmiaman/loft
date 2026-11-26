import pytest

from loft.client import LoftError
from loft.engine import pack, unpack
from tests.conftest import FP

# Every server test runs twice: SQLite + files, and real Postgres + S3 (moto).
pytestmark = pytest.mark.usefixtures("backend")


def team_of_three(mk):
    """owner, admin and member in one team. Signup is invite-only after the first user."""
    owner = mk()
    owner.register("owner@x.io", "password1", "Owner")
    tid = owner.create_team("Agency")["id"]
    admin, member = mk(), mk()
    admin.register("admin@x.io", "password1", invite=owner.invite(tid, "admin")["code"])
    member.register("mem@x.io", "password1", invite=owner.invite(tid, "member")["code"])
    ids = {m["email"]: m["id"] for m in owner.members(tid)}
    return tid, owner, admin, member, ids


def test_signup_is_invite_only_after_first_user(mk):
    mk().register("a@x.io", "password1")
    with pytest.raises(LoftError) as e:
        mk().register("b@x.io", "password1")
    assert e.value.status == 403


def test_login_and_bad_password(mk):
    mk().register("a@x.io", "password1")
    c = mk()
    c.login("A@x.io", "password1")
    assert c.me()["email"] == "a@x.io"
    with pytest.raises(LoftError) as e:
        mk().login("a@x.io", "nope-nope")
    assert e.value.status == 401


def test_roles_and_visibility(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = admin.create_profile(tid, name="Client A", fingerprint=FP,
                             proxy={"server": "http://1.2.3.4:8080", "username": "u", "password": "secret"})
    # member cannot see an unshared profile
    assert member.profiles(tid) == []
    with pytest.raises(LoftError) as e:
        member.profile(p["id"])
    assert e.value.status == 404
    # share run-only: visible, proxy password hidden, cannot edit
    admin.share(p["id"], ids["mem@x.io"], "run")
    seen = member.profiles(tid)[0]
    assert seen["access"] == "run" and seen["proxy"]["password"] == "secret"  # decrypted client-side
    with pytest.raises(LoftError) as e:
        member.patch_profile(p["id"], name="hijack")
    assert e.value.status == 403
    # but run access gets full launch info (needed to start the browser)
    li = member.launch_info(p["id"])
    assert li["proxy"]["password"] == "secret" and li["fingerprint"] == FP
    # edit share allows edits
    admin.share(p["id"], ids["mem@x.io"], "edit")
    assert member.patch_profile(p["id"], notes="ok")["notes"] == "ok"
    # members can't invite or read audit
    for call in (lambda: member.invite(tid), lambda: member.audit(tid)):
        with pytest.raises(LoftError) as e:
            call()
        assert e.value.status == 403


def test_member_sees_own_profiles(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    mine = member.create_profile(tid, name="mine", fingerprint=FP)
    assert [p["id"] for p in member.profiles(tid)] == [mine["id"]]
    assert member.profiles(tid)[0]["access"] == "edit"
    assert len(owner.profiles(tid)) == 1  # owner sees everything


def test_outsider_cannot_touch_team(mk):
    tid, owner, *_ = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    outsider = mk()
    outsider.register("out@x.io", "password1", invite=owner.invite(owner.create_team("Other")["id"])["code"])
    for call in (lambda: outsider.profiles(tid), lambda: outsider.profile(p["id"]),
                 lambda: outsider.launch_info(p["id"]), lambda: outsider.download(p["id"])):
        with pytest.raises(LoftError) as e:
            call()
        assert e.value.status == 404


def test_last_owner_protected_and_admin_cannot_demote_owner(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    with pytest.raises(LoftError) as e:
        admin.set_role(tid, ids["owner@x.io"], "member")
    assert e.value.status == 403
    with pytest.raises(LoftError) as e:
        owner.set_role(tid, ids["owner@x.io"], "member")
    assert e.value.status == 400


def test_removed_member_loses_shares(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    owner.share(p["id"], ids["mem@x.io"])
    owner.remove_member(tid, ids["mem@x.io"])
    with pytest.raises(LoftError):
        member.profile(p["id"])


def test_run_lock_blocks_second_runner(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    owner.share(p["id"], ids["mem@x.io"])
    lk = owner.lock(p["id"])
    assert owner.profile(p["id"])["running"]["by"] == "Owner"
    with pytest.raises(LoftError) as e:
        member.lock(p["id"])
    assert e.value.status == 409
    # a member can't force-unlock; an admin can
    with pytest.raises(LoftError) as e:
        member.unlock(p["id"], force=True)
    assert e.value.status == 403
    admin.unlock(p["id"], force=True)
    member.lock(p["id"])
    # the old holder's token is now dead
    with pytest.raises(LoftError) as e:
        owner.heartbeat(p["id"], lk["lock"])
    assert e.value.status == 423


def test_sync_requires_lock_and_current_base(mk, tmp_path):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    pid = p["id"]
    assert owner.download(pid) == (0, None)
    with pytest.raises(LoftError) as e:
        owner.upload(pid, "nolock", 0, b"zip")
    assert e.value.status == 423
    lk = owner.lock(pid)["lock"]
    assert owner.upload(pid, lk, 0, b"v1")["version"] == 1
    with pytest.raises(LoftError) as e:  # stale base
        owner.upload(pid, lk, 0, b"v1-again")
    assert e.value.status == 409
    assert owner.upload(pid, lk, 1, b"v2")["version"] == 2
    assert owner.download(pid) == (2, b"v2")
    assert owner.download(pid, 1) == (1, b"v1")
    owner.unlock(pid, lk)
    # retention: only last 5 versions kept
    lk = owner.lock(pid)["lock"]
    for base in range(2, 9):
        owner.upload(pid, lk, base, f"v{base + 1}".encode())
    assert [v["version"] for v in owner.versions(pid)] == [9, 8, 7, 6, 5]


def test_audit_records_actions(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    lk = owner.lock(p["id"])["lock"]
    owner.unlock(p["id"], lk)
    actions = [a["action"] for a in admin.audit(tid)]
    for a in ("team.created", "member.joined", "profile.created", "profile.started", "profile.stopped"):
        assert a in actions


def test_pack_skips_caches_and_unpack_guards_paths(tmp_path):
    src = tmp_path / "p"
    (src / "cache2").mkdir(parents=True)
    (src / "cache2" / "junk").write_text("x")
    (src / "storage" / "default").mkdir(parents=True)
    (src / "storage" / "default" / "ls.sqlite").write_text("keep")
    (src / "cookies.sqlite").write_text("cookies")
    (src / "parent.lock").write_text("")
    dst = tmp_path / "out"
    unpack(pack(src), dst)
    files = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert files == ["cookies.sqlite", "storage/default/ls.sqlite"]

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil", "x")
    with pytest.raises(ValueError):
        unpack(buf.getvalue(), tmp_path / "evil-out")


def test_server_only_sees_ciphertext(mk, tmp_path):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint={"navigator": {"userAgent": "SECRET-UA"}},
                             proxy={"server": "http://h:1", "username": "u", "password": "PROXYPW"})
    raw = owner.j("GET", f"/profiles/{p['id']}/launch")
    assert "PROXYPW" not in raw["proxy"] and "SECRET-UA" not in raw["fingerprint"]
    lk = owner.lock(p["id"])["lock"]
    owner.upload(p["id"], lk, 0, b"COOKIE-DATA")
    blob = owner.req("GET", f"/profiles/{p['id']}/data").content
    assert b"COOKIE-DATA" not in blob
    assert owner.download(p["id"]) == (1, b"COOKIE-DATA")
    # every file the server wrote is ciphertext too
    for f in (tmp_path / "srv").rglob("*"):
        if f.is_file():
            b = f.read_bytes()
            assert b"PROXYPW" not in b and b"SECRET-UA" not in b and b"COOKIE-DATA" not in b, f


def test_new_device_recovers_team_key_with_password(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP, proxy={"server": "http://h:1"})
    fresh = mk()
    fresh.login("mem@x.io", "password1")
    owner.share(p["id"], ids["mem@x.io"])
    assert fresh.launch_info(p["id"])["fingerprint"] == FP


def test_invite_without_key_part_cannot_decrypt(mk):
    owner = mk()
    owner.register("o@x.io", "password1")
    tid = owner.create_team("T")["id"]
    owner.create_profile(tid, name="x", fingerprint=FP, proxy={"server": "http://h:1"})
    code = owner.invite(tid)["code"].split(".")[0]  # server part only
    m = mk()
    m.register("m@x.io", "password1", invite=code)
    with pytest.raises(LoftError) as e:
        m.team_key(tid)
    assert e.value.status == 403


def test_templates_and_runners_are_encrypted(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    owner.add_template(tid, "US desktop", {"os": "windows", "proxy": "http://u:TPW@h:1"})
    assert member.templates(tid)[0]["settings"]["proxy"] == "http://u:TPW@h:1"
    assert "TPW" not in str(owner.j("GET", f"/teams/{tid}/templates"))
    with pytest.raises(LoftError):
        member.add_template(tid, "nope", {})
    owner.add_runner(tid, "eu-1", "https://runner.example:4420", "RUNNERKEY")
    assert member.runners(tid)[0]["secret"] == "RUNNERKEY"
    assert "RUNNERKEY" not in str(owner.j("GET", f"/teams/{tid}/runners"))


# ---------------------------------------------------------------- key rotation
from loft import crypto  # noqa: E402
from loft.client import rotate_team_key  # noqa: E402


def test_rotation_locks_out_removed_member(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP, proxy={"server": "http://h:1", "username": "u", "password": "PW"})
    lk = owner.lock(p["id"])["lock"]
    owner.upload(p["id"], lk, 0, b"SESSION-1")
    owner.unlock(p["id"], lk)
    owner.add_template(tid, "t", {"os": "linux"})
    owner.add_runner(tid, "r", "https://r:1", "RKEY")
    owner.share(p["id"], ids["mem@x.io"], "run")
    old_key = member.team_key(tid)  # the member has the key...
    owner.remove_member(tid, ids["mem@x.io"])  # ...and keeps it after being removed
    out = rotate_team_key(owner, tid, log=lambda m: None)
    assert out["epoch"] == 2 and set(out["rekeyed"]) == {ids["admin@x.io"]}
    # everything on the server is now under the new key: the old one opens nothing
    raw = owner.j("GET", f"/profiles/{p['id']}/launch")
    blob = owner.req("GET", f"/profiles/{p['id']}/data").content
    for ct, aad in ((raw["fingerprint"], f"fp:{tid}"), (raw["proxy"], f"proxy:{tid}")):
        with pytest.raises(Exception):
            crypto.dec_text(old_key, ct, aad)
    with pytest.raises(Exception):
        crypto.decrypt(old_key, blob, f"data:{tid}:{p['id']}")
    assert len(owner.versions(p["id"])) == 1  # old-key snapshots are gone
    # the owner and the remaining admin carry on, admin via the sealed grant
    assert owner.download(p["id"])[1] == b"SESSION-1"
    assert admin.download(p["id"])[1] == b"SESSION-1"  # admin had the old key cached: refreshes by itself
    assert admin.launch_info(p["id"])["proxy"]["password"] == "PW"
    assert admin.templates(tid)[0]["settings"] == {"os": "linux"} and admin.runners(tid)[0]["secret"] == "RKEY"
    fresh = mk()
    fresh.login("admin@x.io", "password1")  # new device: sealed grant was converted to a vault copy
    assert fresh.launch_info(p["id"])["fingerprint"] == FP


def test_rotation_refused_while_running_and_blocks_locks(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)
    lk = admin.lock(p["id"])["lock"]
    with pytest.raises(LoftError, match="stop running profiles first"):
        rotate_team_key(owner, tid, log=lambda m: None)
    admin.unlock(p["id"], lk)
    owner.j("POST", f"/teams/{tid}/rotation/start")
    with pytest.raises(LoftError) as e:
        admin.lock(p["id"])
    assert e.value.status == 423
    with pytest.raises(LoftError) as e:  # half-done rotation can't be committed
        owner.j("POST", f"/teams/{tid}/rotation/commit", {"epoch": 2, "self_wrapped": "x"})
    assert "not re-encrypted" in e.value.detail
    owner.j("POST", f"/teams/{tid}/rotation/abort")
    admin.lock(p["id"])
    assert owner.team_epoch(tid) == 1


def test_stale_key_writes_rejected_then_retried(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    admin.team_key(tid)  # cache epoch-1 key
    rotate_team_key(owner, tid, log=lambda m: None)
    assert admin.keys[tid][0] == 1  # still stale in memory
    # a raw write with the old epoch is refused...
    with pytest.raises(LoftError, match="team key rotated"):
        admin.j("POST", f"/teams/{tid}/profiles", {"name": "x", "fingerprint": "x"}, headers={"X-Key-Epoch": "1"})
    # ...and the client recovers transparently, writing under the new key
    p = admin.create_profile(tid, name="after", fingerprint=FP, proxy={"server": "http://h:1"})
    assert admin.keys[tid][0] == 2
    assert owner.launch_info(p["id"])["fingerprint"] == FP


def test_rotation_revokes_old_invites(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    code = owner.invite(tid)["code"]
    rotate_team_key(owner, tid, log=lambda m: None)
    with pytest.raises(LoftError, match="invite invalid"):
        mk().register("late@x.io", "password1", invite=code)
    new = owner.invite(tid)["code"]
    late = mk()
    late.register("late@x.io", "password1", invite=new)
    assert late.team_epoch(tid) == 2


def test_concurrent_lock_has_one_winner(mk):
    import threading
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="race", fingerprint=FP)
    clients = []
    for i in range(12):
        c = mk(owner.token)
        c.device = f"dev-{i}"
        clients.append(c)
    wins, losses, start = [], [], threading.Barrier(len(clients))

    def go(c):
        start.wait()
        try:
            c.lock(p["id"])
            wins.append(c.device)
        except LoftError as e:
            losses.append(e.status)

    ts = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1 and losses == [409] * 11


# ---------------------------------------------------------------- team backup / dashboard
from loft.engine import export_team, import_team  # noqa: E402


def test_team_backup_restore_roundtrip(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p1 = owner.create_profile(tid, name="A", os="windows", canvas="noise",
                              proxy={"server": "http://h:1", "username": "u", "password": "PW"}, fingerprint=FP)["id"]
    owner.create_profile(tid, name="B", os="macos", canvas="noise", fingerprint={"navigator": {"userAgent": "B"}, "os": "macos"})
    lk = owner.lock(p1)["lock"]
    owner.upload(p1, lk, 0, b"A-SESSION")
    owner.unlock(p1, lk)
    owner.add_template(tid, "tpl", {"os": "windows"})

    blob = export_team(owner, tid, "backup pass", log=lambda m: None)
    assert blob[:4] == b"LFTT" and b"A-SESSION" not in blob and b"PW" not in blob

    dest = owner.create_team("Restored")["id"]
    with pytest.raises(LoftError, match="wrong passphrase"):
        import_team(owner, dest, blob, "nope", log=lambda m: None)
    out = import_team(owner, dest, blob, "backup pass", log=lambda m: None)
    assert out["profiles"] == 2 and out["templates"] == 1
    names = {p["name"]: p for p in owner.profiles(dest)}
    assert set(names) == {"A", "B"}
    assert owner.launch_info(names["A"]["id"])["proxy"]["password"] == "PW"
    assert owner.download(names["A"]["id"])[1] == b"A-SESSION"
    assert owner.launch_info(names["B"]["id"])["os"] == "macos"


def test_rotation_preview_and_overview(mk):
    tid, owner, admin, member, ids = team_of_three(mk)
    p = owner.create_profile(tid, name="x", fingerprint=FP)["id"]
    lk = owner.lock(p)["lock"]
    owner.upload(p, lk, 0, b"DATA")
    owner.unlock(p, lk)
    owner.invite(tid)  # one open invite

    pv = owner.rotation_preview(tid)
    assert pv["profiles"] == 1 and pv["invites_to_revoke"] == 1
    assert {m["email"] for m in pv["rekey"]} == {"admin@x.io", "mem@x.io"} and pv["without_key"] == []

    ov = owner.overview(tid)
    assert ov["profiles"] == 1 and ov["members"] == 3 and ov["versions"] == 1 and ov["bytes"] >= 4
    assert ov["open_invites"] == 1 and ov["key_epoch"] == 1 and ov["members_without_key"] == []
    assert any(a["action"] == "profile.synced" for a in ov["recent"])
    # a member can't see the dashboard or preview
    for call in (lambda: member.overview(tid), lambda: member.rotation_preview(tid)):
        with pytest.raises(LoftError) as e:
            call()
        assert e.value.status == 403
    # running profile shows up and blocks rotation preview's "running" list
    lk = admin.lock(p)["lock"]
    assert owner.overview(tid)["running"][0]["name"] == "x"
    assert owner.rotation_preview(tid)["running"][0]["device"]
    admin.unlock(p, lk)
