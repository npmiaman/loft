# Loft

Team browser profiles on [Camoufox](https://github.com/daijro/camoufox). Each profile is a separate browser identity: one fingerprint for its whole life, its own cookies and storage, and an optional proxy. Profiles live on a server you host, so a team can share them, and only one person can run a given profile at a time.

The design follows Donut Browser (profile list, per-profile fingerprint and proxy), but none of its code is used, so the AGPL does not apply. Camoufox is MPL-2.0.

## Parts

| | |
|---|---|
| `loft/server.py` | Cloud server (FastAPI). Users, teams, roles, invites, folders, profiles, per-profile sharing, run locks, versioned profile-data sync, audit log. SQLite plus a blob directory. |
| `loft/engine.py` | Local side. Generates a fingerprint (BrowserForge via Camoufox), packs and unpacks the Firefox profile directory (caches skipped, zip-slip guarded), and runs lock → pull → launch → wait for close → push → unlock. |
| `loft/desktop.py` + `ui.html` | Desktop app: a web UI bound to 127.0.0.1. Each profile launches as its own subprocess. |
| `loft/cli.py` | The same features from the terminal. |

## Quick start

```bash
uv sync
uv run python -m camoufox fetch          # ~1.3 GB browser download, one time

uv run loft server                        # cloud server on :4400 (data in ~/.loft-server)
uv run loft app                           # desktop app on :4410
```

From the CLI:

```bash
uv run loft register you@team.com --server http://127.0.0.1:4400   # the first user needs no invite
uv run loft team create "My team"
uv run loft new "Client A" --os windows --proxy http://user:pass@1.2.3.4:8080 --start-url https://example.com
uv run loft ls
uv run loft run 1                         # opens the browser; syncs when you close the window
uv run loft invite --role member          # give the code to a teammate
uv run loft share 1 teammate@team.com --level run
uv run loft audit
```

A teammate runs `loft register them@team.com --server URL --invite CODE`. After that, `loft run 1` on their machine pulls the latest data and opens the same identity.

## Model

- **Roles.** `owner` > `admin` > `member`. Owners and admins see every profile in the team. Members see only profiles they created or that were shared with them. Admins invite people, change roles (except owner), read the audit log, and force-release stuck locks. A team always keeps at least one owner.
- **Share levels.** `run` opens the profile; the proxy password is hidden in listings but sent to the launcher. `edit` also allows changing settings and sharing.
- **Run lock.** Taken at launch and held by a heartbeat every 30 s, with a 120 s TTL. A second person gets "in use by X on device Y". If a laptop dies mid-session, the lock expires on its own, or an admin can force-release it.
- **Sync.** An upload needs the lock and the current base version, otherwise you get 409/423 and a local `unsynced-N.zip` is kept. The server keeps the last 5 snapshots (`loft versions ID`).
- **Fingerprint.** Generated once at creation and stored on the server. Each launch passes the same fingerprint to Camoufox, so the UA, screen, WebGL, fonts and canvas seed stay the same on every machine. With a proxy, `geoip` sets timezone, locale and geolocation to match the proxy's exit IP.
- **Signup.** Invite-only after the first user. Set `LOFT_OPEN_SIGNUP=1` only for local testing.

## Tests

```bash
uv run pytest -q
```

- `test_server.py`: roles, visibility, sharing, locks, sync and versions, audit, packing.
- `test_desktop.py`: the local UI API, including blocking foreign hosts and requests without the `X-Loft` header.
- `test_browser.py`: real Camoufox, headless. Two simulated devices share one profile; checks that the cookie and localStorage survive and the fingerprint is the same, and that a second profile's fingerprint differs.

## Not built yet

- **Encryption at rest.** Profile archives contain session cookies, and proxy passwords are stored in plain text in SQLite. Client-side encryption with a team key is next.
- **Postgres and S3 backends.** The `_db()` and `BlobStore` seams are there.
- **TLS.** Put the server behind Caddy or nginx before exposing it beyond localhost.
- **Folders.** Supported in the API, not shown in the UI.
- **Bulk create and import/export, a public automation API with API keys, rollback to a snapshot from the UI, a native desktop shell (Tauri).**
