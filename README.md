# Loft

Team browser profiles on [Camoufox](https://github.com/daijro/camoufox), a Firefox build with fingerprint injection. Each profile is its own browser identity:
- one fingerprint for its whole life
- its own cookies and storage
- an optional proxy

Profiles sync through a server you host. They are end-to-end encrypted, shared with your team by role, and can be run locally or on a cloud runner that you view from any browser or phone.

The design follows Donut Browser, but none of its code is used, so the AGPL doesn't apply. Camoufox is MPL-2.0.

## What's in it

| | |
|---|---|
| **Profiles** | Stable fingerprint per profile (Camoufox/BrowserForge), proxy per profile with timezone, locale and geolocation matched to the exit IP, start URL, tags, notes, canvas mode |
| **Teams** | Owner / admin / member roles; per-profile sharing at `run` or `edit`; invites; audit log |
| **Sync** | Encrypted, versioned (last 5 kept); a run lock lets one person use a profile at a time; heartbeat, auto-expiry, admin force-unlock |
| **Encryption** | AES-256-GCM with a team key that only clients hold. The server stores only ciphertext of fingerprints, proxies, templates, runner keys and browser data. |
| **Bulk / templates** | Create up to 500 profiles in one go, with proxies assigned round-robin. Templates (encrypted) hold starting settings. |
| **Import / export** | `.loftprofile` bundles (fingerprint, proxy, cookies, storage), passphrase-encrypted by default |
| **Cookies** | Import Playwright JSON, Cookie-Editor or EditThisCookie JSON, or Netscape `cookies.txt`; export as JSON |
| **Cloud run** | `loft runner serve` on any server. Profiles run there headless and stream to a viewer page that works on desktop and mobile: click, scroll, type, URL bar, tabs. |
| **Automation** | A local REST API (`/v1`, with an API key) and a Python SDK that gives you a live Playwright context |
| **TLS** | `--self-signed` or `--tls-cert/--tls-key`, client CA pinning, and plain HTTP refused on non-local addresses. There's also a Caddy config for real certificates. |
| **Desktop app** | A native window (pywebview) built with PyInstaller for macOS, Windows and Linux |

## Quick start (from source)

```bash
uv sync                                   # dev install: everything (browser, server drivers, tests)
uv run loft fetch-browser                 # Camoufox, ~1.3 GB, one time

uv run loft server                        # cloud server on 127.0.0.1:4400
uv run loft app                           # desktop app (native window)
```

Installing from a wheel, pick the extra for the role:
- `pip install 'loft[client]'` — run profiles, cloud runner, CLI (includes the browser)
- `pip install 'loft[desktop]'` — the desktop app (client + native window)
- `pip install 'loft[server]'` — the cloud server only (Postgres + S3 drivers, **no browser**)

To deploy the server for a team (Docker or systemd, with Postgres, S3 and TLS), see [deploy/README.md](deploy/README.md).

On a real server:

```bash
uv run loft server --host 0.0.0.0 --self-signed loft.mycompany.com   # prints the cert path to give clients
# or put deploy/Caddyfile in front of a 127.0.0.1 bind for a Let's Encrypt cert
```

## CLI

```bash
loft register you@team.com --server https://loft.mycompany.com [--ca cert.pem]   # first user: no invite needed
loft team create "Agency"
loft invite --role member                 # prints code.key: send it privately, it carries the team key
loft register them@team.com --server URL --invite CODE.KEY

loft new "Client A" --os windows --proxy http://user:pass@1.2.3.4:8080 --start-url https://example.com
loft bulk 50 --name "Store {n}" --os windows --proxies proxies.txt --tag stores
loft template add "US desktop" --os windows --proxy http://u:p@us.proxy:8000 --tag us
loft new "Client B" --template "US desktop"
loft ls
loft run 1                                # window opens; close it (or Ctrl+C) and it syncs
loft share 1 them@team.com --level run
loft cookies import 1 cookies.txt
loft cookies export 1 out.json
loft export 1 client-a.loftprofile        # asks for a passphrase
loft import client-a.loftprofile --name "Client A copy"
loft audit
```

## Cloud runners

```bash
# on the runner machine (needs Camoufox installed):
loft runner serve --host 0.0.0.0 --self-signed runner-1.mycompany.com     # prints a runner key
# an admin registers it (the key is stored encrypted with the team key):
loft runner add eu-1 https://runner-1.mycompany.com:4420 RUNNER_KEY
# anyone with access to a profile:
loft cloud 12                             # prints a viewer link; open it on any device
```

To start a session, the client sends the runner its Loft token and the team key, over TLS. The runner then does what a desktop would: lock, pull, decrypt, run, and sync when stopped. Decrypted data lives in a temp folder that is deleted when the session ends. The viewer link holds a per-session secret, so treat it like a password.

## Automation

Local REST API, served by the desktop app (`loft app --no-window` runs it without a window):

```bash
KEY=$(loft apikey)
curl -H "X-API-Key: $KEY" http://127.0.0.1:4410/v1/profiles
curl -H "X-API-Key: $KEY" -X POST -d '{"name":"api","os":"windows"}' -H 'Content-Type: application/json' http://127.0.0.1:4410/v1/profiles
curl -H "X-API-Key: $KEY" -X POST -d '{"headless":true}' -H 'Content-Type: application/json' http://127.0.0.1:4410/v1/profiles/3/start
curl -H "X-API-Key: $KEY" http://127.0.0.1:4410/v1/profiles/3/status
curl -H "X-API-Key: $KEY" -X POST http://127.0.0.1:4410/v1/profiles/3/stop        # closes and syncs
```

The other endpoints are `POST /v1/profiles/bulk`, `PATCH`/`DELETE /v1/profiles/{id}`, `GET`/`POST /v1/profiles/{id}/cookies`, and `POST /v1/profiles/{id}/cloud`.

Python SDK, with full Playwright control:

```python
from loft.client import Client
from loft.engine import open_profile

with open_profile(Client(persist=True), 3, headless=True) as ctx:
    page = ctx.new_page()
    page.goto("https://example.com")
# closed, synced and unlocked here, even if the script raised
```

`loft exec 3 script.py` runs a script with `context` already set.

Selenium and Puppeteer aren't supported. They drive Chromium through its debugging protocol, and Camoufox is Firefox, controlled through Playwright.

## Model and limits

- **Roles.** Owners and admins see every profile. Members see only profiles they made or that are shared with them. Only owners and admins can invite, change roles, manage templates and runners, read the audit log, and force-unlock. A team always keeps at least one owner.
- **Encryption scope.** Profile names, notes, tags, OS and the audit log are readable by the server; everything sensitive (fingerprints, proxies, browser data, templates, runner keys) is ciphertext. Every team member holds the key, so `run` versus `edit` is enforced by the server, not by cryptography.
- **Key rotation.** Removing a member leaves them holding the old key, so `loft rotate-key` (or the Team tab) issues a new team key, re-encrypts everything, and seals the new key to each remaining member's X25519 public key — the removed person's key then opens nothing on the server. The CLI and UI show a preview first (who's re-keyed, who loses access, invites revoked). Data a member already downloaded stays theirs; no rotation can take that back.
- **Backups.** `loft backup FILE` (admin) writes one passphrase-encrypted archive of every profile and template; `loft restore FILE` adds them to a team. For the server itself, `pg_dump` plus the S3 bucket — see [deploy/README.md](deploy/README.md).
- **Admin dashboard.** The Overview tab (and `loft overview`) shows profiles, members, who's running what, storage, open invites, key epoch and last rotation, members missing keys, and recent activity.
- **Fingerprint checkers, measured on a MacBook Air (2026-11-27).** These results are for a macOS profile on a Mac, through a residential proxy (Webshare, NL exit), going through `open_profile`:

  | Checker | Result |
  |---|---|
  | Pixelscan | consistent, no proxy detected, no masking detected, no automated behaviour |
  | BrowserScan | 100%, Proxy: No, language nl-NL, timezone Europe/Amsterdam, WebRTC shows the proxy IP |
  | CreepJS | 0% headless, 0% like-headless, 0% stealth |

  What it took:
  - **Real installed fonts.** Camoufox hides system fonts for native identities, and Pixelscan's server reads that as masking (`osFontsStatus=false`).
  - **Real canvas.** Pixelscan's WebGL red-box test catches any canvas noise.
  - **IPv4 only.** One IP per profile.
  - **Exit IP measured through the proxy** and fed into WebRTC, timezone and geolocation.
  - **A per-profile locale from the exit country.** Camoufox drew a random weighted locale on every launch and gave `de-NL` for a Dutch IP.
- **The limit that follows.** Real canvas and real fonts only work when the profile's OS is the machine's OS, so that is now the default. A Windows profile on a Mac needs canvas noise and the bundled fonts, and Pixelscan flags that as masking. Run Windows profiles on a Windows machine, or on a Windows cloud runner.
- **IP reputation is outside the browser.** Whether a site calls an IP a proxy depends on IP databases and the provider (residential beats datacenter). Loft only makes sure the browser doesn't give the proxy away.
- **Cookies.** Expiry dates more than 399 days out are clamped, because Firefox silently drops them.

## Tests

```bash
uv run pytest -q          # 28 tests; the browser ones need Camoufox installed
```

| File | Covers |
|---|---|
| `test_server.py` | roles, visibility, sharing, locks, sync and versions, audit, ciphertext-only storage, key recovery on a new device, invites without the key part, encrypted templates and runners |
| `test_desktop.py` | the UI API, the local-only guard, bulk create, templates, the `/v1` API key, the TLS cert and bind rule |
| `test_browser.py` | real Camoufox: two devices share one identity (cookies, storage, fingerprint), canvas modes, cookie import and export, bundle round-trip, a crashing script still syncs |
| `test_runner.py` | real Camoufox cloud session: JPEG frames over websocket, typing reaches the page and syncs, temp data deleted |

**Checked by hand on macOS:**
- A TLS server with a self-signed cert: plain HTTP refused, untrusted cert rejected, pinned CA works.
- The `/v1` start and stop: it syncs.
- Ctrl+C on `loft run`: it syncs.
- The PyInstaller `Loft.app` (208 MB) driving a profile through its own API; the Overview dashboard rendered against the live API.
- **Real production path:** the `loft server` process on Postgres 17 + S3 (moto) + TLS, with register / team / profile / lock / sync / download / key-rotation, blobs stored as ciphertext in S3.
- **Windows** (`.github/workflows/windows-smoke.yml`, GitHub `windows-latest`): client install, font enumeration, and the real headless-Camoufox browser suite — this is what caught the cp1252 font-read bug.
- Desktop builds pass for macOS, Windows and Linux (`.github/workflows/desktop.yml`).

**Not checked:**
- The pywebview window on Windows/Linux, by eye (checked on macOS).
- A real cloud-host deployment (the config is verified locally, not on a live VPS).
- Real AWS/R2 (S3 tested against moto); real proxies beyond ad-hoc manual runs.

## Layout

```
loft/server.py    cloud server (FastAPI); SQLite+files or Postgres+S3 via storage.py
loft/storage.py   DB (SQLite/Postgres) and blob (file/S3) backends
loft/client.py    API client, config, team-key vault, client-side encryption, key rotation
loft/crypto.py    AES-GCM, scrypt, X25519 sealed boxes (rotation)
loft/engine.py    fingerprints, pack/unpack, open_profile/run_profile, cookies, bundles, bulk
loft/runner.py    cloud runner; viewer.html is its streaming page
loft/desktop.py   desktop app backend (UI API + /v1); ui.html is the UI
loft/cli.py       CLI
loft/tls.py       self-signed certs, bind rule
packaging/        PyInstaller spec    deploy/   Caddyfile    .github/workflows/desktop.yml
```
