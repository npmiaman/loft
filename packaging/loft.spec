# PyInstaller spec for the Loft desktop app.
#   uv sync --extra desktop --extra build
#   uv run pyinstaller packaging/loft.spec --noconfirm
# The Camoufox browser itself (~1.3 GB) is not bundled: the app downloads it on first run.
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [("../loft/ui.html", "loft"), ("../loft/viewer.html", "loft"),
                            ("../loft/pigeon/assets", "loft/pigeon/assets")], [], []  # Pigeon's content script + shim
for pkg in ("camoufox", "playwright", "fpgen", "language_tags", "ua_parser", "uvicorn", "webview"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d; binaries += b; hidden += h

a = Analysis(["../loft/__main__.py"], pathex=[".."], datas=datas, binaries=binaries,
             hiddenimports=hidden + ["loft.server", "loft.desktop", "loft.runner", "loft.engine", "loft.session", "loft.control", "loft.pigeon.attach",
                                     "loft.pigeon.agent", "loft.pigeon.ops", "loft.pigeon.rotate", "loft.pigeon.runner"],
             excludes=["tkinter", "matplotlib", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Loft", console=sys.platform.startswith("linux"))
coll = COLLECT(exe, a.binaries, a.datas, name="Loft")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Loft.app", bundle_identifier="app.loft.desktop",
                 info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})
