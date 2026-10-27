"""Rough performance numbers: start-up, UI rebuild (every settings change), opening pages, idle CPU, memory.

    python tests/perf.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ISLAND_HOME"] = tempfile.mkdtemp(prefix="island-perf-")

import psutil  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main():
    t0 = time.perf_counter()
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app.app import App
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    qapp.processEvents()
    startup = time.perf_counter() - t0
    proc = psutil.Process()
    out = {}

    def measure():
        t = time.perf_counter()
        for _ in range(3):
            app.rebuild()
            qapp.processEvents()
        out["rebuild"] = (time.perf_counter() - t) / 3
        t = time.perf_counter()
        for key in ("home", "clipboard", "notes", "calendar", "settings", "system", "inbox"):
            app.island.expand(key)
            qapp.processEvents()
        out["pages"] = time.perf_counter() - t
        app.island.collapse()
        proc.cpu_percent(None)
        QTimer.singleShot(10000, idle)

    def idle():
        out["idle_cpu"] = proc.cpu_percent(None) / (psutil.cpu_count() or 1)
        out["rss"] = proc.memory_info().rss / 2**20
        print(f"startup      {startup * 1000:7.0f} ms")
        print(f"rebuild      {out['rebuild'] * 1000:7.0f} ms   (each settings change)")
        print(f"open 7 pages {out['pages'] * 1000:7.0f} ms")
        print(f"idle CPU     {out['idle_cpu']:7.2f} %    (whole machine share, 10 s)")
        print(f"memory       {out['rss']:7.0f} MB", flush=True)
        qapp.exit(0)

    QTimer.singleShot(3000, measure)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
