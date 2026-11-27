"""Storage backends: SQLite or Postgres for metadata, a directory or S3 for blobs.

Queries are written once with `?` placeholders in the SQL subset both databases
share (ON CONFLICT upserts, RETURNING). Races are handled with atomic
statements (conditional UPDATE / upsert ... WHERE) instead of relying on
SQLite's single-writer lock, so Postgres under concurrency behaves the same.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id {pk}, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL,
  vault_salt TEXT NOT NULL, pubkey TEXT, privkey TEXT, created {real} NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id {int} NOT NULL, created {real} NOT NULL);
CREATE TABLE IF NOT EXISTS teams(id {pk}, name TEXT NOT NULL, created {real} NOT NULL,
  key_epoch {int} NOT NULL DEFAULT 1, rotating_by {int}, rotating_epoch {int});
CREATE TABLE IF NOT EXISTS members(team_id {int}, user_id {int}, role TEXT NOT NULL, PRIMARY KEY(team_id, user_id));
CREATE TABLE IF NOT EXISTS team_keys(team_id {int}, user_id {int}, epoch {int} NOT NULL, kind TEXT NOT NULL,
  wrapped TEXT NOT NULL, PRIMARY KEY(team_id, user_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, team_id {int} NOT NULL, role TEXT NOT NULL,
  created_by {int} NOT NULL, expires {real} NOT NULL, used_by {int});
CREATE TABLE IF NOT EXISTS folders(id {pk}, team_id {int} NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(id {pk}, team_id {int} NOT NULL, name TEXT NOT NULL, os TEXT NOT NULL,
  folder_id {int}, notes TEXT DEFAULT '', tags TEXT DEFAULT '[]', proxy TEXT, start_url TEXT DEFAULT '',
  canvas TEXT NOT NULL DEFAULT 'noise', fingerprint TEXT NOT NULL, created_by {int} NOT NULL,
  created {real} NOT NULL, updated {real} NOT NULL, version {int} NOT NULL DEFAULT 0, deleted {int} NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS profiles_team ON profiles(team_id);
CREATE TABLE IF NOT EXISTS shares(profile_id {int}, user_id {int}, level TEXT NOT NULL, PRIMARY KEY(profile_id, user_id));
CREATE TABLE IF NOT EXISTS versions(profile_id {int}, version {int}, sha256 TEXT NOT NULL, size {int} NOT NULL,
  created_by {int} NOT NULL, created {real} NOT NULL, PRIMARY KEY(profile_id, version));
CREATE TABLE IF NOT EXISTS locks(profile_id {int} PRIMARY KEY, user_id {int} NOT NULL, device TEXT NOT NULL,
  token TEXT NOT NULL, expires {real} NOT NULL);
CREATE TABLE IF NOT EXISTS templates(id {pk}, team_id {int} NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL,
  created_by {int} NOT NULL, created {real} NOT NULL);
CREATE TABLE IF NOT EXISTS runners(id {pk}, team_id {int} NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
  secret TEXT NOT NULL, created_by {int} NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id {pk}, team_id {int}, user_id {int}, profile_id {int}, action TEXT NOT NULL,
  detail TEXT DEFAULT '', at {real} NOT NULL);
CREATE INDEX IF NOT EXISTS audit_team ON audit(team_id, id);
CREATE TABLE IF NOT EXISTS pigeon_events(id {pk}, team_id {int} NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
  subreddit TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
  profile_id {int}, user_id {int}, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', at {real} NOT NULL, updated {real} NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS pigeon_live ON pigeon_events(team_id, kind, target) WHERE status IN ('claimed','done','blocked');
CREATE INDEX IF NOT EXISTS pigeon_by_team ON pigeon_events(team_id, kind, subreddit, at);
CREATE TABLE IF NOT EXISTS pigeon_accounts(team_id {int} NOT NULL, profile_id {int} PRIMARY KEY, username TEXT NOT NULL,
  updated {real} NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS pigeon_acct_user ON pigeon_accounts(team_id, username);
CREATE TABLE IF NOT EXISTS pigeon_config(team_id {int} PRIMARY KEY, setup TEXT NOT NULL DEFAULT '', limits TEXT NOT NULL DEFAULT '{{}}',
  version {int} NOT NULL, updated {real} NOT NULL, updated_by {int});
CREATE TABLE IF NOT EXISTS rotation_stage(team_id {int}, profile_id {int}, epoch {int} NOT NULL,
  fingerprint TEXT NOT NULL, proxy TEXT, has_data {int} NOT NULL, PRIMARY KEY(team_id, profile_id));
"""

TYPES = {
    "sqlite": {"pk": "INTEGER PRIMARY KEY", "int": "INTEGER", "real": "REAL"},
    "postgres": {"pk": "BIGSERIAL PRIMARY KEY", "int": "BIGINT", "real": "DOUBLE PRECISION"},
}


class Conflict(Exception):
    """A unique constraint failed."""


class Con:
    """One transaction. execute() takes `?` placeholders on both backends."""

    def __init__(self, raw, dialect: str):
        self.raw, self.dialect = raw, dialect

    def execute(self, sql: str, params: tuple | list = ()):
        if self.dialect == "postgres":
            sql = sql.replace("?", "%s")
        try:
            return self.raw.execute(sql, tuple(params))
        except Exception as e:
            if _is_unique_violation(e):
                raise Conflict(str(e)) from e
            raise

    def insert(self, sql: str, params: tuple | list = ()) -> int:
        """INSERT ... returning the new id."""
        return self.execute(sql + " RETURNING id", params).fetchone()["id"]


def _is_unique_violation(e: Exception) -> bool:
    if isinstance(e, sqlite3.IntegrityError) and "UNIQUE" in str(e):
        return True
    return type(e).__name__ == "UniqueViolation"


class Database:
    def __init__(self, url: str):
        """url: a filesystem path / sqlite:///path, or postgresql://..."""
        if url.startswith(("postgres://", "postgresql://")):
            import psycopg  # noqa: F401  (fail early if missing)
            self.dialect, self.url = "postgres", url
        else:
            self.dialect, self.url = "sqlite", url.removeprefix("sqlite:///")
        ddl = SCHEMA.format(**TYPES[self.dialect])
        with self.tx() as con:
            for stmt in filter(str.strip, ddl.split(";")):
                con.execute(stmt)

    @contextmanager
    def tx(self) -> Iterator[Con]:
        if self.dialect == "sqlite":
            raw = sqlite3.connect(self.url, timeout=30, isolation_level=None)
            raw.row_factory = sqlite3.Row
            try:
                raw.execute("BEGIN IMMEDIATE")
                yield Con(raw, "sqlite")
                raw.execute("COMMIT")
            except BaseException:
                raw.execute("ROLLBACK")
                raise
            finally:
                raw.close()
        else:
            import psycopg
            from psycopg.rows import dict_row
            with psycopg.connect(self.url, row_factory=dict_row) as raw:  # commits on success, rolls back on error
                yield Con(raw, "postgres")


# ---------------------------------------------------------------- blobs
class FileBlobs:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        if "/" in key or ".." in key or "\\" in key:
            raise ValueError("bad blob key")
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

    def copy(self, src: str, dst: str) -> None:
        self.put(dst, self._p(src).read_bytes())

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith(prefix) and not p.name.endswith(".tmp"))


class S3Blobs:
    """Any S3-compatible store (AWS, R2, MinIO, B2). Credentials come from the usual AWS env/config.
    url: s3://bucket/optional/prefix ; LOFT_S3_ENDPOINT for non-AWS endpoints."""

    def __init__(self, url: str, endpoint: Optional[str] = None, client: Any = None):
        import os
        import boto3
        rest = url.removeprefix("s3://")
        self.bucket, _, prefix = rest.partition("/")
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.s3 = client or boto3.client("s3", endpoint_url=endpoint or os.environ.get("LOFT_S3_ENDPOINT") or None)

    def _k(self, key: str) -> str:
        if "/" in key or ".." in key:
            raise ValueError("bad blob key")
        return self.prefix + key

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def get(self, key: str) -> Optional[bytes]:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._k(key))

    def copy(self, src: str, dst: str) -> None:
        self.s3.copy_object(Bucket=self.bucket, Key=self._k(dst), CopySource={"Bucket": self.bucket, "Key": self._k(src)})

    def keys(self, prefix: str = "") -> list[str]:
        out, token = [], None
        while True:
            kw = dict(Bucket=self.bucket, Prefix=self.prefix + prefix)
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            out += [o["Key"][len(self.prefix):] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return sorted(out)
            token = r["NextContinuationToken"]


def open_blobs(url_or_path: str | Path):
    s = str(url_or_path)
    return S3Blobs(s) if s.startswith("s3://") else FileBlobs(Path(s))
