"""Rough performance numbers: start-up, UI rebuild (every settings change), opening pages, idle CPU, memory,
CPU while music plays (the equaliser animates).

    python tests/perf.py [--eco | --normal]
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
    from island_app import config, perf
    from island_app.app import App
    if "--eco" in sys.argv or "--normal" in sys.argv:   # force a mode before the app reads its settings
        st = config.Settings()
        st.set("performance", "eco" if "--eco" in sys.argv else "normal")
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
        out["t_idle"] = (sum(proc.cpu_times()[:2]), time.perf_counter())
        QTimer.singleShot(15000, idle)

    def idle():
        c0, t0 = out["t_idle"]
        out["idle_cpu"] = 1000 * (sum(proc.cpu_times()[:2]) - c0) / (time.perf_counter() - t0)
        # music playing: the equaliser animates in the pill all the time
        app.media._timer.stop()
        app.media.state = {"active": True, "title": "Midnight City", "artist": "M83", "playing": True,
                           "position": 0, "duration": 200, "stamp": time.time(), "art": b""}
        app.media.changed.emit(app.media.state)
        out["t_music"] = (sum(proc.cpu_times()[:2]), time.perf_counter())
        QTimer.singleShot(15000, music)

    def music():
        c0, t0 = out["t_music"]
        out["music_cpu"] = 1000 * (sum(proc.cpu_times()[:2]) - c0) / (time.perf_counter() - t0)
        out["rss"] = proc.memory_info().rss / 2**20
        report()

    def report():
        print(f"mode         {'economy' if perf.ECO else 'normal'}")
        print(f"startup      {startup * 1000:7.0f} ms")
        print(f"rebuild      {out['rebuild'] * 1000:7.0f} ms   (each settings change)")
        print(f"open 7 pages {out['pages'] * 1000:7.0f} ms")
        print(f"idle CPU     {out['idle_cpu']:7.1f} ms/s (CPU time per second, 15 s)")
        print(f"memory       {out['rss']:7.0f} MB", flush=True)
        print(f"music CPU    {out['music_cpu']:7.1f} ms/s (equaliser animating, 15 s)", flush=True)
        qapp.exit(0)

    QTimer.singleShot(3000, measure)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
