"""Checks for the screen tools and parsers that need no user input.

    python tests/check_tools.py
"""
import datetime as dt
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ISLAND_HOME", os.path.join(tempfile.gettempdir(), "island-test"))

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import bridge, capture, commands, config, services, widgets  # noqa: E402
from island_app.pages import make_qr  # noqa: E402
from island_app.theme import Theme  # noqa: E402

results = []


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main():
    app = QApplication(sys.argv)
    bridge.init()
    widgets.set_theme(Theme(config.Settings()))

    # parsers
    now = dt.datetime(2026, 10, 26, 14, 0)
    body, when, _ = commands.parse_reminder("позвонить маме завтра в 9", now)
    check("reminder: tomorrow at 9", body == "позвонить маме" and when == dt.datetime(2026, 10, 27, 9, 0), f"{body} {when}")
    body, when, _ = commands.parse_reminder("чай через 20 минут", now)
    check("reminder: in 20 minutes", body == "чай" and when == now + dt.timedelta(minutes=20), f"{body} {when}")
    body, when, _ = commands.parse_reminder("call bob tomorrow at 9pm", now)
    check("reminder: english", body == "call bob" and when == dt.datetime(2026, 10, 27, 21, 0))
    check("duration: 1ч 30м", commands.parse_duration("1ч 30м") == 5400)
    check("calc: 2^10 + 15% от 200", commands.calculate("2^10 + 15% от 200") == "1 054")
    check("convert: 100 f в c", commands.convert("100 f в c") == "37.7778 °C", commands.convert("100 f в c"))

    # QR round trip
    img = make_qr("https://islandapp.pro")
    found = services.decode_qr(img)
    check("QR generate + decode", found == ["https://islandapp.pro"], found)

    # OCR (Windows.Media.Ocr)
    canvas = QImage(900, 160, QImage.Format_RGB32)
    canvas.fill(QColor("white"))
    p = QPainter(canvas)
    f = QFont("Segoe UI")
    f.setPixelSize(56)
    p.setFont(f)
    p.setPen(QColor("black"))
    p.drawText(canvas.rect(), Qt.AlignCenter, "Island OCR test 2026")
    p.end()
    state = {}

    def ocr_ok(text):
        state["ocr"] = text

    def ocr_fail(exc):
        state["ocr"] = f"ERROR {exc}"
    services.ocr_png(services.png_bytes(canvas), ocr_ok, ocr_fail)

    # screenshot overlay: full screen, one arrow, render
    screen, pm = capture.grab_screen_under_cursor()
    ov = capture.ShotOverlay(screen, pm, full=True)
    s = capture.Shape("arrow", "#ff3b30", 3, QPoint(50, 50))
    s.points.append(QPoint(300, 200))
    ov.shapes.append(s)
    out = ov.render_image()
    check("screenshot render keeps full resolution", out.width() == pm.width() and out.height() == pm.height(),
          f"{out.width()}x{out.height()} vs {pm.width()}x{pm.height()}")
    path = os.path.join(tempfile.gettempdir(), "island_shot_check.png")
    out.save(path)
    ov.deleteLater()

    # colour picker samples the frozen image
    pk = capture.PickerOverlay(screen, pm)
    c = pk.color_at(QPoint(10, 10))
    ref = QColor(pm.toImage().pixel(int(10 * pk.dpr), int(10 * pk.dpr)))
    check("colour picker reads the frozen pixel", c == ref, c.name())
    pk.deleteLater()

    started = time.monotonic()

    def finish():
        # OCR runs on a worker; slow machines (virtual Intel Macs on CI) need more than a few seconds
        if "ocr" not in state and time.monotonic() - started < 40:
            QTimer.singleShot(250, finish)
            return
        text = state.get("ocr", "")
        check("OCR recognises rendered text", "Island" in text and "2026" in text,
              f"{text!r} in {time.monotonic() - started:.1f} s")
        print(f"\n{sum(results)}/{len(results)} passed", flush=True)
        app.exit(0 if all(results) else 1)
    QTimer.singleShot(500, finish)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
