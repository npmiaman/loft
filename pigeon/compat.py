"""Loft was renamed to Pigeon. Existing installs keep working: every PIGEON_* setting also reads its old LOFT_* spelling, and the
data folders and the server's database file fall back to their old names when only those exist. Nothing is moved or copied."""
import os
from pathlib import Path
from typing import Optional


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """PIGEON_<name>, else the old LOFT_<name>, else the default."""
    return os.environ.get("PIGEON_" + name) or os.environ.get("LOFT_" + name) or default


def default_dir(new: str, old: str, markers: tuple[str, ...] = ()) -> Path:
    """`~/<new>`, unless this machine has the old `~/<old>` holding our data and the new one does not.

    A folder only counts as ours if it contains one of `markers` (e.g. config.json): `~/.pigeon-app` can belong to something else entirely, and
    must not hide the data in `~/.loft`. With no markers, any existing old folder counts."""
    n, o = Path("~/" + new).expanduser(), Path("~/" + old).expanduser()
    ours = lambda d: d.is_dir() and (not markers or any((d / m).exists() for m in markers))
    return o if ours(o) and not ours(n) else n


def server_db_file(data: Path) -> Path:
    """The server's SQLite file: `pigeon.db`, or the old `loft.db` if that is what this data folder already has."""
    old, new = data / "loft.db", data / "pigeon.db"
    return old if old.exists() and not new.exists() else new
