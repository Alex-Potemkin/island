"""Builds Island for the OS it runs on (build on the target OS).

Windows -> Island.exe (one file)      Linux -> Island-linux-x86_64 (one file)
macOS   -> Island.app and Island-macos-<arm64|x86_64>.dmg

    python -m pip install -r requirements.txt
    python build_exe.py
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
os.makedirs(BUILD, exist_ok=True)


def make_icons():
    """Window/tray icon in the sizes each OS wants; returns the path for --icon (Windows/macOS) or None."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no display needed (CI)
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, HERE)
    from island_app.app import make_icon
    app = QApplication.instance() or QApplication([])
    pngs = {}
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        p = os.path.join(BUILD, f"icon{size}.png")
        make_icon(size).pixmap(size, size).save(p)
        pngs[size] = p
    del app
    if IS_WIN:
        from PIL import Image
        ico = os.path.join(BUILD, "island.ico")
        Image.open(pngs[256]).save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 256)])
        return ico
    if IS_MAC:
        iconset = os.path.join(BUILD, "Island.iconset")
        os.makedirs(iconset, exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            shutil.copy2(pngs[size], os.path.join(iconset, f"icon_{size}x{size}.png"))
            shutil.copy2(pngs[size * 2], os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
        icns = os.path.join(BUILD, "Island.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
        return icns
    return None


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


def finish_mac_app(app, version):
    """Info.plist for a menu-bar style app, ad-hoc signature, and a .dmg with an Applications link."""
    info = os.path.join(app, "Contents", "Info.plist")
    with open(info, "rb") as f:
        plist = plistlib.load(f)
    short = (version or "1.2.0").split("-")[0].split("+")[0]
    plist.update({
        "CFBundleName": "Island", "CFBundleDisplayName": "Island", "CFBundleIdentifier": "pro.island.app",
        "CFBundleShortVersionString": short, "CFBundleVersion": short,
        "LSUIElement": True,                       # no Dock icon, no app menu: the island is the UI
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Island показывает, что играет в Spotify и Музыке, и управляет ими.",
        "NSMicrophoneUsageDescription": "Island показывает, когда микрофон используется.",
    })
    with open(info, "wb") as f:
        plistlib.dump(plist, f)
    # the plist changed after PyInstaller signed the bundle: sign it again (ad-hoc, no Apple account)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app], check=True)
    stage = os.path.join(BUILD, "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    subprocess.run(["ditto", app, os.path.join(stage, "Island.app")], check=True)
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    dmg = os.path.join(HERE, f"Island-macos-{platform.machine()}.dmg")
    if os.path.exists(dmg):
        os.remove(dmg)
    subprocess.run(["hdiutil", "create", "-volname", "Island", "-srcfolder", stage, "-ov", "-format", "UDZO", dmg],
                   check=True)
    return dmg


version = stamp_version()
icon = make_icons()
assets = os.path.join(HERE, "island_app", "assets")
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "Island",
        "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "--add-data", f"{assets}{os.pathsep}island_app/assets",   # bundled icon font
        "--collect-binaries", "pyzbar",
        "--collect-binaries", "imageio_ffmpeg",                    # ffmpeg for screen recording
        "--collect-data", "tzdata",                                # iCal time zones (Windows has no tz database)
        "--hidden-import", "qrcode.image.pil",
        "--exclude-module", "tkinter"]
if IS_WIN:
    args += ["--onefile", "--icon", icon, "--collect-all", "winrt"]   # SMTC, radios, OCR (.pyd per namespace)
elif IS_MAC:
    # an .app is a folder anyway: one-folder mode starts faster than a one-file bundle
    args += ["--icon", icon, "--osx-bundle-identifier", "pro.island.app", "--exclude-module", "winrt",
             "--hidden-import", "AppKit", "--hidden-import", "Quartz", "--hidden-import", "Vision"]
    zbar = next((p for p in ("/opt/homebrew/lib/libzbar.dylib", "/usr/local/lib/libzbar.dylib")
                 if os.path.exists(p)), None)
    if zbar:          # QR codes: brew install zbar
        args += ["--add-binary", f"{os.path.realpath(zbar)}{os.pathsep}."]
else:
    args += ["--onefile", "--exclude-module", "winrt"]
subprocess.run(args + [os.path.join(HERE, "main.py")], check=True, cwd=HERE)

if IS_MAC:
    app = os.path.join(HERE, "Island.app")
    shutil.rmtree(app, ignore_errors=True)
    subprocess.run(["ditto", os.path.join(BUILD, "dist", "Island.app"), app], check=True)
    print("->", app)
    print("->", finish_mac_app(app, version))
else:
    target = os.path.join(HERE, "Island.exe" if IS_WIN else f"Island-linux-{platform.machine()}")
    shutil.copy2(os.path.join(BUILD, "dist", "Island.exe" if IS_WIN else "Island"), target)
    print("->", target)
