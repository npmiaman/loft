# PyInstaller spec for the Pigeon desktop app.
#   uv sync --extra desktop --extra build
#   uv run pyinstaller packaging/pigeon.spec --noconfirm
# The Camoufox browser itself (~1.3 GB) is not bundled: the app downloads it on first run.
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [("../pigeon/ui.html", "pigeon"), ("../pigeon/viewer.html", "pigeon"),
                            ("../pigeon/agent/assets", "pigeon/agent/assets")], [], []  # Pigeon's content script + shim
import os
if os.path.isdir("../pigeon/web_ui"):  # the Next.js UI, built beforehand with `npm run build` in web/
    datas.append(("../pigeon/web_ui", "pigeon/web_ui"))
for pkg in ("camoufox", "playwright", "fpgen", "language_tags", "ua_parser", "uvicorn", "webview"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d; binaries += b; hidden += h

a = Analysis(["../pigeon/__main__.py"], pathex=[".."], datas=datas, binaries=binaries,
             hiddenimports=hidden + ["pigeon.server", "pigeon.desktop", "pigeon.runner", "pigeon.engine", "pigeon.session", "pigeon.control", "pigeon.agent.attach",
                                     "pigeon.agent.agent", "pigeon.agent.ops", "pigeon.agent.rotate", "pigeon.agent.runner"],
             excludes=["tkinter", "matplotlib", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Pigeon", console=sys.platform.startswith("linux"))
coll = COLLECT(exe, a.binaries, a.datas, name="Pigeon")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Pigeon.app", bundle_identifier="app.pigeon.desktop",
                 info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})
