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
| **Pigeon** | A Reddit outreach agent that runs inside each profile, with a shared team memory so accounts never double up, a one-at-a-time multi-account launcher, and a full panel per session. See [Pigeon](#pigeon-reddit-outreach-inside-every-browser-session) |

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

## Per-profile proxies (proxynet or any session proxy)

A profile's proxy is any HTTP/SOCKS proxy. To give **each browser its own stable exit** from a pool, point the proxy at your gateway and put `{pid}` (the profile id) or `{session}` (→ `loft-<pid>`) in the username — Loft expands it at launch, so every profile gets its own sticky session while sharing one gateway config.

This matches [proxynet](https://github.com/npmiaman/proxynet)'s username routing (`session-…` is sticky, `country-<cc>-session-…` pins a country, `node-<name>` a node):

```bash
# one template, reused for many profiles, each with its own sticky US exit:
loft template add proxynet-us --proxy "http://country-us-session-{pid}:GATEWAY_PASS@gateway.example:8899"
loft new "Store 1" --template proxynet-us
loft new "Store 2" --template proxynet-us   # different sticky session, same gateway
```

Verified end to end locally (2026-11-27): a Loft profile fetched through a real proxynet gateway → node, its proxy username expanded to `country-us-session-<pid>`. Templates are encrypted and survive key rotation, so the gateway password never reaches the server in clear.

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

## Pigeon: Reddit outreach inside every browser session

[Pigeon](https://github.com/npmiaman/pigeon) (the Reddit outreach Chrome/Firefox extension) can't load into Camoufox as-is, so Loft runs Pigeon's **unmodified content script** (`loft/pigeon/assets/reddit.js`: cursor, human typing and scrolling, DM and comment flows) in the profile's page through a small `chrome.*` shim, and ports the agent loop, brain, governor and every panel feature to Python. Each Reddit account gets its own fingerprint, proxy and cookies, and its Pigeon state (daily cap, warm-up clock, pauses, leads, drafts) lives *inside* the profile data, so it is end-to-end encrypted and synced like everything else, and follows the account between teammates.

### Two ways to run it

```bash
uv run loft pigeon config --deepseek-key sk-...      # stays in ~/.loft/pigeon.json, never synced

# A) attach to a session you already have open (log in by hand, browse as you like)
uv run loft run 1                                      # or the desktop app's Run button; log in to Reddit once by hand
uv run loft pigeon attach 1 --mode find --follow       # the agent works in a new tab of that browser
uv run loft pigeon detach 1                            # stop it; the browser stays open

# B) unattended: Loft opens the profile's own browser for the agent
uv run loft pigeon run 1 --mode full --minutes 30
```

Every other command below works either way. If the profile is open on this machine it talks to the live session; otherwise it locks the profile, does the work (opening a browser only when it needs one) and syncs. Several sessions can each have their own agent at once.

In the desktop app, every profile running here has a 🕊 row; **Open Pigeon** gives you the full panel for that session: Leads, Run, Comments, Messages, Setup, Settings and Brain.

### Many accounts, one at a time

```bash
uv run loft pigeon run-all --all --mode full --minutes 20          # every profile you can run, in turn
uv run loft pigeon run-all 3 1 2 --mode find --minutes 15          # these, in this order
uv run loft pigeon run-all --tag us --rounds 3 --round-gap 90      # three passes over the "us" accounts, 90 min apart
uv run loft pigeon run-all --all --dry-run                         # show what would run and what would be skipped
```

Each account gets a full session (lock → pull → browser → agent → close → push → unlock), and only then does the next one start: never two browsers at once. A random pause (`--gap-min/--gap-max`, default 30–120 s) separates launches. Before opening anything, Loft peeks at each account's saved state (read-only: no lock, no upload, no browser) and **skips** accounts with nothing to do: never set up, no product or subreddits (find/full), no leads waiting, paused, outside active hours, or daily cap reached (dm). Accounts that are in use by a teammate or already open here are skipped too, and one account failing doesn't stop the rest (`--stop-on-error` changes that). Ctrl-C stops the current account cleanly (its profile still syncs) and ends the whole run. `--max-total MIN` stops starting new accounts after a time budget, `--shuffle` randomises the order each round, `--headless` runs the browsers headless. A summary of what each account did is printed at the end. Nothing here runs on a schedule by itself; put it in cron or a launchd job if you want that.

### Everything the extension does, per session

| | CLI | Notes |
|---|---|---|
| Setup: product ICP, subreddit discovery with rules risk | `setup ID URL`, `discover ID`, `product ID [--set icp=...]`, `subs ID --enable/--disable/--add/--remove` | discovery keeps your on/off choices |
| Run: find leads / send DMs / both, upvotes, comment drafts | `run`, `attach`, `detach`, `status` | governor: warm-up ramp, daily cap, active hours, jittered gaps, auto-pause |
| Leads: write, edit, approve, send now, skip, mark replied | `leads`, `write`, `edit`, `approve`, `send`, `skip`, `replied` | AI drafts wait for approval by default |
| Page DMer / List DMer / CSV | `scan [--url] [--no-score]`, `import FILE`, `leads --export FILE`, `clear --yes` | scan reads the Reddit tab you have open |
| Replies | `replies ID` (also runs automatically at the start of every session; `set ID auto_check_replies=false` to stop) | two sources: Reddit's private-message inbox (JSON API) and the chat list, judged by who spoke last, so a reply counts whether or not it is unread |
| Templates: spintax, AI spin, preview, undo | `template ID --set/--spin/--preview/--undo` | |
| Comments: approve and post, skip | `opps ID [--post ID \| --skip ID]` | |
| Post ideas and posting | `ideas ID --generate`, `--submit ID [--force]`, `--history`, `--posted ID` | `--submit` has the agent post it (text post) on your explicit request only, never on autopilot. Guards: daily cap (`posts_per_day`, default 1), refuses a high-rules-risk subreddit without `--force`, claims the title with the team, and honours the team's post limits. It is judged by Reddit redirecting to the new thread, never by the click. `--posted` records a post you made yourself |
| Settings | `set ID daily_dm_cap=20 min_fit=65 ... [--restart-warmup]` | |

### The central brain: one memory for every session, account and teammate

All sessions report to the Loft server, so every account knows what the whole team has done, who the team's accounts are, and what has worked. Who/where/when (usernames, subreddits, titles, timestamps, statuses) is readable by the server because de-duplication, limits and counts depend on it; **message text is sealed with the team key**, and so is the shared setup (the limits are plain numbers, because the server enforces them). After a key rotation the shared setup is re-encrypted along with everything else, but message text sealed under the old key reads as `[encrypted]`.

**Never repeat or collide**
- **Claim before acting.** Before a DM, comment or upvote, the account *claims* that person, thread or post atomically (a unique index on the server). If another account already has it, the claim is refused and says who and when; nothing is sent. Two accounts racing for the same person: exactly one wins (tested).
- **Our own accounts.** Each profile registers the Reddit username it is logged in as (detected from the session, or `set ID my_username=NAME`). The server refuses any DM, queued lead or upvote aimed at one of the team's own accounts (including an account targeting itself), and they are never scored, imported or scanned as leads.
- **Shared leads.** When an account queues a lead it tells the team, so no other account scores or queues the same person (a lead nobody acts on for 7 days is free again). People already DMed are skipped too, before any model call is spent on them.
- **Upvotes** are claimed per post or comment, so several accounts never pile onto the same one.
- **Release on failure.** A rate limit, captcha, failed send or failed click releases the claim. A user who can't be messaged stays recorded as blocked so nobody wastes a try.
- **Fail closed.** If the server can't be reached, DMs and comments are not sent. Reading context fails open.

**One shared setup**
- An owner or admin shares a setup with `loft pigeon team publish ID` (that account's product/ICP, subreddit list, template and the shared settings: `min_fit`, `dm_mode`, `approve_ai_drafts`, `ai_guidance`, upvote and session settings). Every other account adopts it automatically the next time it runs, so a new account needs no `setup` of its own (`team pull ID` does it by hand).
- It is versioned. A local edit on an account survives until the team publishes something newer; `set ID use_team_setup=false` opts an account out. Per-account safety settings (daily cap, warm-up, active hours, username) and each account's browsing history are never shared or overwritten.

**Team-wide limits**
- `loft pigeon team limits --dm-per-day 40 --sub-dm-per-day 8 --comment-per-day 10 --post-per-day 2 --sub-post-per-day 1` (owners/admins; `0` removes one). They are enforced on the server over a rolling 24 hours across *all* accounts, on top of each account's own cap: a team-wide cap stops DMs for the session, a per-subreddit cap parks just that subreddit's leads while other subreddits carry on. Backfilled history never counts.

**What the team has learned**
- **Replies** are recorded (from reply checks, or when you mark one), with the message that earned it.
- **Prompt context.** DM, comment and post-idea prompts include what the whole team has done in that subreddit recently: counts, a per-account breakdown, the reply rate, our latest comments and posts (not to repeat), and examples of DMs that got a reply (to write in that spirit).

**See and manage it.** `loft pigeon brain` (totals by subreddit and account, replies, upvotes, limit usage), `--events [--kind dm|comment|post|reply|upvote|lead] [--sub NAME]`, `--who alice bob`, `team show`, `team accounts`, and the Brain tab in the desktop panel (with Pull / Publish buttons). `brain --release EVENT_ID` lets an admin free a claim stuck by a crash, and `backfill ID` pushes what an account did *before* the brain existed (with original dates). Claims are never auto-expired: a crash mid-send may have actually sent, and a duplicate is worse than a gap.

### One exit IP per account (proxynet)

Reddit links accounts that share an exit IP, so every Pigeon account should have its own. Use the per-profile proxy placeholders above: one gateway, one template, a sticky session per account.

```bash
loft template add reddit-us --proxy "http://country-us-session-{pid}:GATEWAY_PASS@gateway.example:8899"
loft new "Reddit 1" --template reddit-us --tag reddit
loft new "Reddit 2" --template reddit-us --tag reddit        # or: loft bulk 10 --name "Reddit {n}" --proxies one-line-file.txt --tag reddit
loft pigeon run-all --tag reddit --mode full
```

Pigeon launches accounts through `run_profile`, which expands `{pid}`/`{session}` at launch and measures the exit IP, so each account's timezone, locale and WebRTC match its own sticky exit. Before `run-all` starts it **checks the accounts it is about to run** and warns if any would share an exit: no proxy at all (they'd all use this machine's IP), or the same proxy session (the same literal username on one gateway). `--strict-proxies` refuses to start instead of warning. The check expands the placeholders exactly as launch does, and works on the decrypted proxies, so it never needs the gateway password in clear on the server.

### Team accounts: one unique proxy each, run one by one or one at a time

Each Reddit account is its own Loft profile (own fingerprint, own cookies) bound to **one proxy of its own**, stored encrypted with the team, so whoever may run an account runs it through that proxy and nobody sets proxies up per person.

```bash
# proxies.txt: one per line, in your provider's format (user:pass@host:port, or a full URL; a {pid} placeholder works too)
loft pigeon accounts add --proxies proxies.txt --name "Reddit {n}"    # tests every proxy, refuses duplicates, dead ones and shared exit IPs,
                                                                      # then creates one account per proxy (tagged "reddit")
loft pigeon accounts ls                                               # who is bound to what (never the password), registered Reddit user, who's running it
loft pigeon accounts check                                            # connect through every proxy now: working? every exit IP distinct?

loft pigeon run 3 --mode full             # ONE account, on its own
loft pigeon run-all --tag reddit --mode full   # ALL of them: one browser at a time, in turn, each through its own proxy
```

`add` stops before creating anything if two lines repeat, if a proxy doesn't connect, or if two proxies exit from the same IP (`--allow-duplicate-exits` overrides that last one). Checked with a real pool of 8 residential proxies: 8 working exits, 8 different IPs, 17 seconds.

### Identity consistency: the exit IP's country, English by default

The exit IP decides the **time zone, geolocation and region**. The browser **language defaults to English** (`en-US`, or the country's own variant: `en-GB` for UK exits, `en-IN` for India...), and the Accept-Language header, the Intl formats and the **default speech voice** all follow it: a Mac profile exiting in Italy reads `en-US`, `Europe/Rome` and `Samantha [en-US]`. `LOFT_LANGUAGE=country` makes profiles use the exit country's native language instead (it-IT with `Alice [it-IT]`), and `LOFT_LANGUAGE=it-IT` (any tag) pins one. This also stopped two inconsistencies: Camoufox marks no speech voice as the default, so macOS filled in *this machine's* (a Mac set to India showed `Rishi [en-IN]` on an Italian profile), and a profile without a proxy got a random locale per launch that could even disagree with itself.

On **every launch** Loft reads the language and country dependent signals back from the real browser (browser language and list, Intl locale, time zone, default voice and voices in the language, number and date formats) and compares them with what the IP and language imply. It logs `identity consistent with the exit IP and language`, or `WARNING identity mismatch: ...` naming the signal, and `context.loft_audit` has the detail. Tested in real Camoufox for nine countries in English, three in native-language mode, an explicit language, a proxy-less profile, and a deliberate regression showing the audit flags the original Rishi bug. Not covered: WebRTC (needs the network; BrowserScan shows it matching the proxy IP).

### First contact with real Reddit: `doctor` and snapshots

Pigeon's selectors are tested against a mock of Reddit's markup, so run this first on a new account (and again if something starts failing):

```bash
uv run loft pigeon doctor 1      # read-only: visits a feed, a post, a profile, the chat list and the submit page; sends nothing
```

It prints a ✓/✗ line per assumption (posts parse, upvote button, comment box, Chat button, chat rooms, submit page, "not logged in"...), with what each failure means, and saves a screenshot and the page's HTML for every failing page. The same snapshots (`~/.loft/profiles/ID/diagnostics/`, newest 20, local only, never synced, and they can contain personal data) are saved automatically whenever a real action fails: no posts read, a DM with no chat button or composer, a comment or post that didn't go through, an empty chat list.

### How attaching works

The process that owns the browser (`loft run`, the desktop app's subprocess for it, or an unattended `loft pigeon run`, which means `status`, `leads`, `approve` and `detach` work on those too) serves a control channel on `127.0.0.1` (random port and token in `~/.loft/profiles/ID/control.json`, outside the synced data, removed on close). Anything that touches Pigeon's state runs on that process's browser thread, so the agent and your edits can't race; browser jobs (run, setup, send, comment, scan, replies) run one at a time in a tab of their own. Stop aborts a command in flight, and closing the browser stops the agent and syncs its state. DMs and comments are never auto-retried after a lost page.

Update the vendored script after changing the extension: run `npm run sync:loft` in the extension repo (a test fails if the copy is stale).

**What is and isn't verified.** Everything runs in **real Camoufox** in the tests, against Pigeon's *mock* of Reddit's DOM with a fake DeepSeek and a real Loft server, and the brain's server tests run on **both SQLite and a real Postgres + S3**, like the rest of the server's. That includes: a live session using every feature through its control channel; an unattended run observed and stopped from another thread; the shared brain across accounts; replies against a chat page and an inbox; posting; `doctor` on healthy and deliberately broken markup; the real desktop app launching a real profile subprocess and the whole Pigeon panel driven in a browser; and the proxy safeguard on real encrypted profiles. The **Firefox build of the extension** is installed into Camoufox as a real add-on and its content script runs every command in Firefox's real extension sandbox (through a test-only message bridge that is compiled out of production builds, and checked to be absent from them). Running in Camoufox found and fixed real bugs: `MouseEvent`'s `view: window` throws in an isolated world, and plain `time.sleep` let `page.url` go stale.

Still **not proven**: anything against **real Reddit** (the DM, comment, reply and post selectors are text/aria based and tested only on the mock; `doctor` exists to find out in minutes what differs); the extension's **background page and panel in Firefox** (Playwright can't open `moz-extension://` pages); and **Windows** for the Pigeon parts. The DeepSeek key is stored in plaintext in `~/.loft/pigeon.json` (mode 0600). Synthetic events are `isTrusted: false`, same as the extension.

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
  - **Real canvas.** Pixelscan's WebGL red-box test catches any canvas noise. In this mode Firefox's own baseline fingerprint protection is switched off too: left on, it still randomized the canvas on web pages with a fresh key every launch (measured: 3 different values over 3 launches), so the canvas wasn't stable. Now it is the machine's true canvas, the same on every launch (`test_real_canvas_is_actually_stable_across_launches`).
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
| `test_central.py`, `test_team_brain.py` | Pigeon's shared memory on the server, on SQLite and Postgres: atomic claims, release, own accounts, sealed setup and message text (and a key rotation carrying the setup), team limits, shared leads/upvotes/replies, learning in prompts, two real-browser sessions |
| `test_pigeon.py`, `test_pigeon_full.py`, `test_rotate.py`, `test_doctor.py` | governor, brain, every Pigeon operation, the content script in real Camoufox, attach/control, one live session using every feature, the one-at-a-time launcher and its proxy safeguard, `doctor` on healthy and broken markup |
| `test_unattended.py`, `test_desktop_e2e.py`, `test_ui_pigeon.py` | an unattended run watched and stopped over its control channel; the real desktop app launching a real profile and driving the Pigeon panel; the panel against a stubbed API |
| `test_firefox_extension.py` | the Firefox build of the Pigeon extension installed as a real add-on in Camoufox |

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
loft/pigeon/      the Reddit agent: agent loop, brain, governor, ops, doctor, shared-memory client, multi-account launcher
loft/control.py   local control channel of a running session; loft/session.py hosts it
loft/tls.py       self-signed certs, bind rule
packaging/        PyInstaller spec    deploy/   Caddyfile    .github/workflows/desktop.yml
```
