"""Loft was renamed to Pigeon. These pin down what was promised: an install that predates the rename keeps working."""
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from pigeon.compat import default_dir, env, server_db_file
from pigeon.cli import build_parser
from pigeon.desktop import make_app
from pigeon.server import create_app

ROOT = Path(__file__).resolve().parent.parent


def _python(code, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("PIGEON_", "LOFT_"))}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**e, **extra}, check=True).stdout.strip()


def test_env_reads_the_new_name_then_the_old_one(monkeypatch):
    monkeypatch.delenv("PIGEON_X", raising=False)
    monkeypatch.delenv("LOFT_X", raising=False)
    assert env("X", "dflt") == "dflt"
    monkeypatch.setenv("LOFT_X", "old")
    assert env("X", "dflt") == "old"           # an old LOFT_* variable still counts
    monkeypatch.setenv("PIGEON_X", "new")
    assert env("X", "dflt") == "new"           # and the new one wins when both are set


def test_old_home_variable_and_folder_are_still_found(tmp_path):
    assert _python("from pigeon.client import HOME; print(HOME)", LOFT_HOME=str(tmp_path / "old")) == str(tmp_path / "old")
    assert _python("from pigeon.client import HOME; print(HOME)", PIGEON_HOME=str(tmp_path / "new")) == str(tmp_path / "new")
    # No install anywhere: the new default. An empty ~/.loft is not an install.
    home = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    code = "from pigeon.client import HOME; print(HOME)"
    assert _python(code, **home) == str(tmp_path / ".pigeon-app")
    (tmp_path / ".loft").mkdir()
    assert _python(code, **home) == str(tmp_path / ".pigeon-app")
    # A real old install is found, and kept in use.
    (tmp_path / ".loft" / "config.json").write_text("{}")
    assert _python(code, **home) == str(tmp_path / ".loft")
    # ~/.pigeon belongs to other Pigeon tooling (it has its own config.json): it is never used, and must not hide ~/.loft.
    (tmp_path / ".pigeon").mkdir()
    (tmp_path / ".pigeon" / "config.json").write_text('{"token": "not ours"}')
    assert _python(code, **home) == str(tmp_path / ".loft")
    # Once the new folder has its own config, it wins.
    (tmp_path / ".pigeon-app").mkdir()
    (tmp_path / ".pigeon-app" / "config.json").write_text("{}")
    assert _python(code, **home) == str(tmp_path / ".pigeon-app")


def test_default_dir_prefers_the_new_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_dir(".pigeon-server", ".loft-server", ("loft.db",)) == tmp_path / ".pigeon-server"
    (tmp_path / ".loft-server").mkdir()
    assert default_dir(".pigeon-server", ".loft-server", ("loft.db",)) == tmp_path / ".pigeon-server"   # no database in it: not an install
    (tmp_path / ".loft-server" / "loft.db").write_text("")
    assert default_dir(".pigeon-server", ".loft-server", ("loft.db",)) == tmp_path / ".loft-server"


def test_an_existing_server_keeps_its_loft_db(tmp_path):
    first = tmp_path / "srv"
    create_app(first, open_signup=True)
    assert (first / "pigeon.db").exists() and not (first / "loft.db").exists()   # a fresh server uses the new name
    (first / "pigeon.db").rename(first / "loft.db")                                # ...an old one has the old name
    assert server_db_file(first) == first / "loft.db"
    create_app(first, open_signup=True)
    assert (first / "loft.db").exists() and not (first / "pigeon.db").exists()   # and is reused, not replaced by an empty database


def test_the_desktop_api_accepts_the_old_header_too(tmp_path, monkeypatch):
    import pigeon.client as pc
    monkeypatch.setattr(pc, "HOME", tmp_path)                     # an empty, signed-out home: never the real one
    monkeypatch.setattr(pc, "CONFIG", tmp_path / "config.json")
    c = TestClient(make_app(), base_url="http://127.0.0.1")
    assert c.get("/api/state").status_code == 403                                  # still needs a header
    assert c.get("/api/state", headers={"X-Pigeon": "1"}).status_code == 200
    assert c.get("/api/state", headers={"X-Loft": "1"}).status_code == 200


def test_the_old_agent_command_name_still_parses():
    p = build_parser()
    assert p.parse_args(["agent", "status", "1"]).pcmd == "status"
    assert p.parse_args(["pigeon", "status", "1"]).pcmd == "status"               # was `loft pigeon status 1`


def test_both_command_names_are_installed():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"pigeon": "pigeon.cli:entry", "loft": "pigeon.cli:entry"}


def test_nothing_that_protects_existing_data_was_renamed():
    # these labels are inside ciphertext, bundles and backups already in the wild: renaming them would make old data unreadable
    assert b"loft-seal-v1" in (ROOT / "pigeon" / "crypto.py").read_bytes()
    engine = (ROOT / "pigeon" / "engine.py").read_text()
    assert 'BUNDLE_MAGIC = b"LFTB"' in engine and '"loft-profile/1"' in engine and '"loft-team/1"' in engine
