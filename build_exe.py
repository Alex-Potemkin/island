"""Builds a single-file Island.exe next to this script.

    python -m pip install -r requirements.txt
    python build_exe.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
os.makedirs(BUILD, exist_ok=True)


def make_ico(path):
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, HERE)
    from island_app.app import make_icon
    app = QApplication.instance() or QApplication([])
    pngs = []
    for size in (16, 24, 32, 48, 64, 256):
        p = os.path.join(BUILD, f"icon{size}.png")
        make_icon(size).pixmap(size, size).save(p)
        pngs.append(p)
    from PIL import Image
    big = Image.open(pngs[-1])
    big.save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 256)])
    del app


ico = os.path.join(BUILD, "island.ico")
make_ico(ico)
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Island", "--icon", ico,
        "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "--collect-all", "winrt",            # SMTC, radios, OCR (namespace package with .pyd per module)
        "--collect-binaries", "pyzbar",      # libzbar / libiconv DLLs
        "--hidden-import", "qrcode.image.pil",
        "--exclude-module", "tkinter",
        os.path.join(HERE, "main.py")]
subprocess.run(args, check=True, cwd=HERE)
shutil.copy2(os.path.join(BUILD, "dist", "Island.exe"), os.path.join(HERE, "Island.exe"))
print("->", os.path.join(HERE, "Island.exe"))
