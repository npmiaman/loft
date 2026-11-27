# Running Loft + Pigeon on Windows

**Read this first: the Pigeon parts have never been run on Windows.** Loft's own parts (install, sync, locks, a real Camoufox profile, font enumeration) have a Windows CI job (`.github/workflows/windows-smoke.yml`, manual). Everything Pigeon added (the agent inside a session, the team brain, `accounts`, `run-all`, the identity audit) was built and tested on a Mac. The code avoids Mac-only calls, but "should work" is not "works". The last section says what to send back if it breaks.

All commands are PowerShell. Use `uv run ...` so you never have to activate a virtualenv (that avoids the PowerShell execution-policy error).

---

## 1. What you need

| | |
|---|---|
| Windows 10 or 11, 64-bit | |
| Python 3.12 or newer | `uv` installs it for you if you don't have it |
| `git` and `uv` | `winget install Git.Git` and `winget install astral-sh.uv`, then **open a new PowerShell** so they are on PATH |
| About 3 GB free | Camoufox is ~1.3 GB, plus Python packages and profile data |
| A Loft server | Either your team's, or run your own (step 3) |
| A DeepSeek API key | Only for Pigeon's writing (comments, DMs, ideas) |
| One proxy per Reddit account | Webshare-style `user:pass@host:port` lines work (step 5) |
| Edge WebView2 runtime | Only for the desktop app. Already on Windows 11 and most Windows 10 |

## 2. Install

```powershell
git clone https://github.com/npmiaman/loft
cd loft
git checkout pigeon-integration     # skip once the Pigeon PR is merged into main
setx PYTHONUTF8 1                   # then open a NEW PowerShell, see "Make sure of" #1
uv sync                             # everything, including the test tools
uv run loft fetch-browser           # Camoufox, ~1.3 GB, one time
uv run loft --help                  # should list `pigeon`
```

If Windows Defender or an antivirus quarantines the Camoufox download, allow it and run `fetch-browser` again. It is a modified Firefox, which some scanners dislike.

## 3. Server and your account

Using your team's server (you were invited):

```powershell
# you were invited: register with the code you were sent
uv run loft register you@team.com --server https://loft.example.com --invite CODE.KEY
```

Running your own server on this PC (single machine, or for testing):

```powershell
uv run loft server                   # 127.0.0.1:4400. Windows Firewall will ask: allow private networks only if teammates need to connect
uv run loft register you@team.com --server http://127.0.0.1:4400
uv run loft team create "Agency"
uv run loft invite --role member     # prints CODE.KEY. Send it privately: it carries the team key
```

Anything exposed beyond this PC must use TLS (`--self-signed`, or the Caddy config in `deploy/`). Plain HTTP is refused on non-local addresses. See `deploy/README.md`.

## 4. Pigeon key

```powershell
uv run loft pigeon config --deepseek-key YOUR_KEY
# or, for one session only:
$env:DEEPSEEK_API_KEY = "YOUR_KEY"
```

The key is saved in plain text in `%USERPROFILE%\.loft\pigeon.json`. On macOS and Linux Loft sets that file to mode 0600. **On Windows that call does nothing**, so the file is only as private as your user folder. Don't share the Windows login, and don't put the folder in a synced or shared directory. The same applies to `config.json` (your Loft login token) in that folder.

## 5. One browser, one proxy, one Reddit account

`proxies.txt`: one proxy per line. **Save it as UTF-8** (Notepad: Save As, Encoding: UTF-8). Do not create it with `echo ... > proxies.txt` in Windows PowerShell 5.1, which writes UTF-16 and Loft can't read it. A UTF-8 BOM is fine.

```
user-1:pass@p.webshare.io:80
user-2:pass@p.webshare.io:80
```

```powershell
uv run loft pigeon accounts add --proxies proxies.txt --name "Reddit {n}"   # tests every proxy, refuses dead ones and shared exits
uv run loft pigeon accounts ls
uv run loft pigeon accounts check          # re-tests: every account should show a different exit IP
```

Then log each profile into its own Reddit account once (open it, sign in, close it so it syncs):

```powershell
uv run loft run 1
```

Run one account:

```powershell
uv run loft pigeon run 1 --mode find       # leads only. Use `--mode dm` or `full` when ready
```

Run all of them, one browser at a time:

```powershell
uv run loft pigeon run-all --all --dry-run                        # what would run, and what would be skipped
uv run loft pigeon run-all --tag reddit --mode full --minutes 20 --strict-proxies
```

`--strict-proxies` refuses to start if two accounts would share an exit IP or have no proxy.

## 6. Make sure of (the Windows-specific list)

1. **UTF-8.** The UI pages contain non-ASCII characters (arrows, a dove emoji). On Windows Python reads files in the system code page (cp1252) unless told otherwise, and that fails on those pages. The code now reads them as UTF-8 explicitly, but set `setx PYTHONUTF8 1` anyway and open a new terminal. Cost: nothing. Symptom if it's wrong: `UnicodeDecodeError` or `'charmap' codec can't decode`.
2. **Run Windows profiles on Windows.** Real canvas and real fonts only work when the profile's OS is the machine's OS, which is why `accounts add` defaults to `--os windows` here. A macOS or Linux profile on a Windows PC needs canvas noise, and Pixelscan flags that.
3. **One proxy per account, all different.** Two accounts on one exit IP get linked. `accounts check` must show N accounts and N distinct exits before you run anything.
4. **Check the identity line at launch.** Each launch prints an identity audit (IP, time zone, language, default voice). It should start with `identity is consistent` and show ✓ on every line. `IDENTITY MISMATCH` with a ✗ line means the browser would show something that contradicts the proxy's country. Don't send anything from that account until it's explained.
5. **Close profiles properly.** Close the browser window, use **Stop** in the desktop app, or press Ctrl+C **once** in the terminal. Each of these syncs and releases the run lock. Closing the PowerShell window, killing `python.exe` or shutting the PC down mid-run skips the sync. The lock then expires by itself after its heartbeat times out, or an admin can run `uv run loft unlock ID`. Cookies from that session are lost if they weren't pushed.
6. **Don't let the PC sleep during `run-all`.** Set Power and sleep to Never while plugged in. A sleeping laptop stalls the run and drops the proxy connections.
7. **Never run the same profile on two machines.** The lock prevents it. If you see "already running", someone else (or an old crashed run of yours) holds it.
8. **Windows file locking.** If a run fails with `PermissionError` or "file in use" on something under `%USERPROFILE%\.loft\profiles\`, a Firefox process from a crashed run is still alive. Find it in Task Manager (`firefox.exe` or `camoufox.exe`), end it, and run again.
9. **Long paths.** If the Camoufox download or a profile fails with a path-too-long error, enable long paths (Group Policy: Enable Win32 long paths) or set `$env:LOFT_HOME = "C:\loft"` to use a short folder. Not seen happening, but untested here.

## 7. Desktop app (optional)

```powershell
uv run loft app                                              # run from source
uv sync --extra desktop --extra build
uv run pyinstaller packaging\loft.spec --noconfirm           # builds dist\Loft\Loft.exe
```

The built app has no console window, so errors don't print anywhere visible. If it won't open, run `uv run loft app` from PowerShell instead to see the error. The pywebview window on Windows has never been looked at by eye. Only macOS has.

Windows SmartScreen will warn about an unsigned `Loft.exe`. That is expected for a build you made yourself.

## 8. Verify it works

In order, stopping at the first thing that fails:

```powershell
uv run pytest -q tests/test_server.py tests/test_desktop.py -k "not postgres"   # no browser needed
uv run python -c "from loft.engine import host_font_families as f; x=f(); print(len(x), 'Arial' in x, 'Segoe UI' in x)"
uv run pytest -q tests/test_browser.py                                          # real Camoufox, headless
uv run pytest -q tests/test_pigeon.py tests/test_rotate.py tests/test_central.py -k "not postgres"
uv run pytest -q tests/test_identity.py tests/test_proxynet.py
```

- Tests that need Postgres are skipped on Windows: the test setup looks for the binaries in macOS and Linux paths and uses a `/tmp` socket. That is a gap in the tests, not in Loft.
- Browser tests download nothing extra, but need `fetch-browser` first.
- `tests/test_ui_pigeon.py` needs `$env:LOFT_TEST_CHROMIUM = "C:\Program Files\Google\Chrome\Application\chrome.exe"`.

Then, with a real profile:

```powershell
uv run loft pigeon doctor 1      # read-only: visits a feed, a post, a profile, chats, the submit page; sends nothing
```

Open <https://pixelscan.net>, <https://abrahamjuliot.github.io/creepjs/> and <https://browserscan.net/bot-detection> in the profile (`uv run loft run 1`). With a Windows profile on a Windows PC and a working proxy, Pixelscan should not flag masking. That result is from a Mac with a macOS profile. Nobody has checked it on Windows.

## 9. Known unknowns

- Everything Pigeon adds, on Windows (see the top).
- The default speech voice. On macOS Loft picks the voice that matches the identity's language. For a Windows profile it picks the first voice for that language from Camoufox's Windows list, and nobody has compared that to what a real Windows PC reports.
- Nothing has been tested against real Reddit on any OS. Only a mock of it. Run `doctor` first.
- The control channel between the agent and the browser is a local `127.0.0.1` port with a token file under `%USERPROFILE%\.loft\profiles\`. The token file's permissions are not restricted on Windows. Other programs under your own user account can read it.

## 10. If it breaks, send back

1. The exact command and the **full** terminal output (not a screenshot of the last line).
2. `uv run python --version` and `winver` (the Windows version).
3. Whether `setx PYTHONUTF8 1` was done and a new terminal opened.
4. The output of `uv run loft pigeon status 1` if a run was involved.
5. Redact: proxy usernames and passwords, your DeepSeek key, invite codes (`CODE.KEY`), emails.

| Symptom | Likely cause |
|---|---|
| `UnicodeDecodeError`, `charmap codec` | UTF-8 not set (item 1), or `proxies.txt` saved as UTF-16 (section 5) |
| `loft` not found | New terminal needed after installing `uv`; always use `uv run loft ...` |
| `proxy not working` | Wrong credentials, or the proxy host blocks your network; test it in a browser first |
| `already running on this machine` / lock held | A crashed earlier run (item 5); end `firefox.exe`/`camoufox.exe`, wait, or `loft unlock` |
| Page loads but Pigeon never acts | Run `doctor`, then paste its output |
| Identity audit mismatch | Item 4. Send the audit lines |
