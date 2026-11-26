import pytest

from loft.client import LoftError
from loft.engine import pack, unpack
from tests.conftest import FP


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
    assert seen["access"] == "run" and seen["proxy"] == {"server": "http://1.2.3.4:8080"}
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
