"""Volume / brightness in the pill, battery and Bluetooth events, the acrylic panel, screen recording,
and that the old Claude chat is gone (also from older settings).

    python tests/check_extras.py
"""
import ctypes
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="island-extras-")
os.environ["ISLAND_HOME"] = HOME
SHOTS = tempfile.mkdtemp(prefix="island-shots-")

# settings from a version that still had the chat
with open(os.path.join(HOME, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "rev": 2, "ai": {"api_key": "sk-ant-old", "model": "x"},
               "hotkeys": {"ai": "ctrl+alt+a", "command": "alt+space"},
               "layout": {"tabs": ["home", "ai", "notes"], "home": ["music"], "quick": ["screenshot"],
                          "idle": "clock"}}, f)

from PySide6.QtCore import QRect, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import config, osapi, power  # noqa: E402

results = []


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main():
    config.SHOTS_DIR = SHOTS
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app import commands, layout, services
    from island_app.app import App
    services.Media.poll = lambda self: None
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    # ---- the chat is gone
    def no_chat():
        s = app.settings
        check("chat tab removed from older settings", "ai" not in layout.current(s)["tabs"]
              and "ai" not in layout.TABS, layout.current(s)["tabs"])
        check("stored API key and hot key removed", s.get("ai") is None and "ai" not in (s.get("hotkeys") or {}))
        res = commands.build_results(app, "? как дела")
        check("no «ask Claude» in the command bar", not any("Claude" in (r["sub"] or "") for r in res))
        check("settings migrated once", s.get("rev") == 3)
        try:
            import anthropic  # noqa: F401
            has = True
        except ImportError:
            has = False
        check("anthropic SDK is not a dependency", "anthropic" not in open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt"), encoding="utf-8").read(),
              "installed" if has else "")

    # ---- volume / brightness OSD
    def volume():
        pw = app.power
        seq = iter([(40, False), (52, False), (52, True)])
        osapi_volume = osapi.volume_state
        osapi.volume_state = lambda: next(seq, (52, True))
        pw.volume = None
        pw._poll_volume()                         # first read: just remembered
        check("first volume read shows nothing", "level" not in app.activities)
        pw._poll_volume()
        live = app.current_live()
        check("volume change shows a bar in the pill", live and live["kind"] == "level" and live["value"] == "52%"
              and abs(live["progress"] - 0.52) < 1e-6, live)
        check("pill switched to the level", app.island.mode == "live")
        pw._poll_volume()
        check("mute shows «без звука»", app.activities["level"]["value"] == "без звука"
              and app.activities["level"]["muted"])
        osapi.volume_state = osapi_volume
        pw.on_power("brightness", 40)             # sent right after registering: remembered
        pw.on_power("brightness", 41)
        live = app.current_live()
        check("brightness change shows a bar", live["kind"] == "level" and live["title"] == "Яркость"
              and live["value"] == "41%", live)
        app.island.retarget(snap=True)
        app.island.grab().save(os.path.join(SHOTS, "pill_level.png"))

    def level_gone():
        check("the bar goes away by itself", "level" not in app.activities)

    def real_volume():
        if not osapi.IS_WINDOWS:
            check("volume read (Windows only)", True)
            return
        st = osapi.volume_state()
        check("Core Audio: volume of the default speakers", st is None or (0 <= st[0] <= 100
                                                                           and isinstance(st[1], bool)), st)

    # ---- battery
    def battery():
        pw = app.power
        app.clear_alert()
        pw.source = pw.battery = None
        pw._warned.clear()
        pw.on_power("power_source", 1)
        pw.on_power("battery", 23)
        check("first power values are only remembered", "alert" not in app.activities)
        pw.on_power("power_source", 0)
        a = app.activities.get("alert") or {}
        check("plugging in shows «Зарядка»", a.get("title") == "Зарядка", a)
        pw.on_power("power_source", 1)
        n0 = len(app.notifications.history)
        pw.on_power("battery", 19)
        check("below 20 %: a notification", len(app.notifications.history) == n0 + 1
              and app.notifications.history[0]["title"] == "Мало заряда: 19%")
        pw.on_power("battery", 18)
        check("…only once", len(app.notifications.history) == n0 + 1)
        pw.on_power("power_source", 0)
        pw.on_power("battery", 99)
        pw.on_power("battery", 100)
        check("charged to 100 %: a notification", app.notifications.history[0]["title"] == "Заряжено")
        check("battery glyphs", power.battery_glyph(100, False) == "" and power.battery_glyph(0, False) == "")
        if osapi.IS_WINDOWS:
            b = osapi.battery()
            check("GetSystemPowerStatus", b is None or 0 <= b["percent"] <= 100, b)

    def power_message():
        """A WM_POWERBROADCAST message the way nativeEvent gets it."""
        if not osapi.IS_WINDOWS:
            check("power messages (Windows only)", True)
            return
        import ctypes.wintypes as wt
        from island_app.osapi import windows as win
        ps = win._PowerSetting()
        ps.PowerSetting = win._GUID.parse(win._POWER_GUIDS["brightness"])
        ps.DataLength, ps.Data = 4, 63
        msg = wt.MSG()
        msg.message, msg.wParam, msg.lParam = win.WM_POWERBROADCAST, win.PBT_POWERSETTINGCHANGE, \
            ctypes.addressof(ps)
        check("WM_POWERBROADCAST parsed", osapi.native_power(b"windows_generic_MSG", ctypes.addressof(msg))
              == ("brightness", 63))
        check("other messages ignored", osapi.native_power(b"other", 0) is None)

    # ---- Bluetooth
    def bluetooth():
        app.clear_alert()
        bt = app.power.bt
        if bt is None:
            check("Bluetooth events (Windows only)", True)
            return
        bt._ready = False
        bt._added("dev1", "AirPods Pro", False)
        bt._ready = True
        bt._battery_of = staticmethod(lambda name: _fake_battery(name))
        bt._updated("dev1", True)

    def bluetooth_check():
        if app.power.bt is None:
            return
        a = app.activities.get("alert") or {}
        check("headphones connected: name and charge", a.get("title") == "AirPods Pro"
              and a.get("sub") == "подключено · 80%" and a.get("glyph") == power.GLYPH_HEADPHONES, a)
        app.power.bt._updated("dev1", False)
        a = app.activities.get("alert") or {}
        check("disconnected", a.get("sub") == "отключено", a)

    # ---- acrylic
    def acrylic():
        app.clear_alert()
        app.activities.pop("level", None)
        app.set_appearance(material="acrylic")

    def acrylic_open():
        app.island.expand("home")

    def acrylic_check():
        isl = app.island
        if not osapi.CAN_BLUR:
            check("acrylic not offered here", app.theme.material == "solid")
            isl.collapse()
            return
        check("acrylic: blur window under the open panel", isl._backdrop_on and isl.backdrop.shown)
        geo = osapi.window_rect(isl)
        r = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetWindowRect(wt.HWND(isl.backdrop.hwnd), ctypes.byref(r))
        sr = isl.shape_rect()
        k = geo[2] / isl.width()
        check("…placed exactly under the panel", abs(r.left - (geo[0] + (sr.x() + 1) * k)) <= 2
              and abs((r.right - r.left) - (sr.width() - 2) * k) <= 2, (r.left, r.right, geo, sr))
        check("panel fill lets the blur through", app.theme.glass_bg().alpha() < 255)
        isl.collapse()

    def acrylic_closed():
        isl = app.island
        if osapi.CAN_BLUR:
            check("collapsing hides the blur", not isl.backdrop.shown and not isl._backdrop_on)
        app.set_appearance(material="solid")

    def acrylic_solid():
        check("solid material: no blur", app.theme.material == "solid" and app.theme.glass is None)

    # ---- screen recording
    def record():
        from island_app import recorder
        rec = app.recorder
        if not recorder.ffmpeg_path():
            check("ffmpeg available", False)
            return
        app.settings.set("record.format", "mp4")
        screen = qapp.primaryScreen()
        ok = rec.start(screen, QRect(0, 0, 320, 200))
        check("recording starts", ok and rec.active)
        live = app.current_live()
        check("the pill shows the recording", live and live["kind"] == "rec", live)

    def record_stop():
        rec = app.recorder
        if not rec.active:
            return
        rec.stop()
        check("stopping: «Сохраняю…» in the pill", app.activities.get("rec", {}).get("busy"))

    state = {"errors": []}
    app.recorder.failed.connect(state["errors"].append)

    def record_saved():
        rec = app.recorder
        if rec.busy and time.monotonic() - state.setdefault("wait", time.monotonic()) < 60:
            return "wait"                 # slow CI machines take a while to encode
        state.pop("wait", None)
        check("no recording errors", not state["errors"], state["errors"])
        hist = app.notifications.history
        saved = [n for n in hist if n["title"] == "Запись сохранена"]
        path = os.path.join(SHOTS, saved[0]["body"]) if saved else ""
        check("MP4 saved", bool(saved) and path.endswith(".mp4") and os.path.getsize(path) > 1000,
              path and os.path.getsize(path))
        check("the recording is on the shelf", path in app.store["shelf"])
        check("pill back to normal", "rec" not in app.activities and not rec.busy)
        state["n"] = len(saved)
        app.settings.set("record.format", "gif")
        rec.start(qapp.primaryScreen(), QRect(0, 0, 240, 160))

    def record_gif_stop():
        app.recorder.stop()

    def record_gif_saved():
        if app.recorder.busy and time.monotonic() - state.setdefault("wait", time.monotonic()) < 90:
            return "wait"
        check("no GIF errors", not state["errors"], state["errors"])
        saved = [n for n in app.notifications.history if n["title"] == "Запись сохранена"]
        path = os.path.join(SHOTS, saved[0]["body"]) if saved else ""
        check("GIF saved", len(saved) == state.get("n", 0) + 1 and path.endswith(".gif")
              and open(path, "rb").read(6) in (b"GIF89a", b"GIF87a"), path)
        res = commands.build_results(app, "запись")
        check("command bar «запись экрана»", any(r["title"] == "Запись экрана" for r in res))

    step(500, no_chat)
    step(200, volume)
    step(2200, level_gone)
    step(100, real_volume)
    step(100, battery)
    step(100, power_message)
    step(100, bluetooth)
    step(600, bluetooth_check)
    step(300, acrylic)
    step(300, acrylic_open)
    step(1500, acrylic_check)
    step(1200, acrylic_closed)
    step(300, acrylic_solid)
    step(200, record)
    step(1600, record_stop)
    step(500, record_saved)
    step(1200, record_gif_stop)
    step(500, record_gif_saved)

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
            qapp.exit(0 if all(results) else 1)
            return
        d, fn = steps[i]

        def go():
            try:
                if fn() == "wait":             # the step isn't ready yet: ask again shortly
                    QTimer.singleShot(250, go)
                    return
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"step {fn.__name__} ran", False, exc)
            run(i + 1)
        QTimer.singleShot(d, go)
    run()
    return qapp.exec()


async def _fake_battery(name):
    return 80


if __name__ == "__main__":
    sys.exit(main())
