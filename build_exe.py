"""Builds a single-file Island binary next to this script (build on the target OS).

Windows -> Island.exe            Linux -> Island-linux-x86_64

    python -m pip install -r requirements.txt
    python build_exe.py
"""
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
IS_WIN = os.name == "nt"
os.makedirs(BUILD, exist_ok=True)


def make_icons():
    """Window/tray icon in the sizes each OS wants; returns the path for --icon (Windows) or None."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no display needed (CI)
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, HERE)
    from island_app.app import make_icon
    app = QApplication.instance() or QApplication([])
    pngs = []
    for size in (16, 24, 32, 48, 64, 256):
        p = os.path.join(BUILD, f"icon{size}.png")
        make_icon(size).pixmap(size, size).save(p)
        pngs.append(p)
    del app
    if not IS_WIN:
        return None
    from PIL import Image
    ico = os.path.join(BUILD, "island.ico")
    Image.open(pngs[-1]).save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 256)])
    return ico


def stamp_version():
    """ISLAND_VERSION (set by CI from the git tag) goes into island_app/_version.py for the About line."""
    version = os.environ.get("ISLAND_VERSION", "").strip().lstrip("v")
    path = os.path.join(HERE, "island_app", "_version.py")
    if version:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'VERSION = "{version}"\n')
    elif os.path.exists(path):
        os.remove(path)          # a local build shows the version from config.py
    return version


stamp_version()
ico = make_icons()
assets = os.path.join(HERE, "island_app", "assets")
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Island",
        "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "--add-data", f"{assets}{os.pathsep}island_app/assets",   # bundled icon font
        "--collect-binaries", "pyzbar",
        "--collect-data", "tzdata",                                # iCal time zones (Windows has no tz database)
        "--hidden-import", "qrcode.image.pil",
        "--exclude-module", "tkinter"]
if IS_WIN:
    args += ["--icon", ico, "--collect-all", "winrt"]   # SMTC, radios, OCR (.pyd per namespace)
    target = os.path.join(HERE, "Island.exe")
else:
    args += ["--exclude-module", "winrt"]
    target = os.path.join(HERE, f"Island-linux-{platform.machine()}")
subprocess.run(args + [os.path.join(HERE, "main.py")], check=True, cwd=HERE)
shutil.copy2(os.path.join(BUILD, "dist", "Island.exe" if IS_WIN else "Island"), target)
print("->", target)
