"""Clock in the pill after upgrades, pinned apps, Bambu Lab printer (fake MQTT broker), DeskDash (fake agent).

    python tests/check_desk.py [--shots DIR]
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="island-desk-")
os.environ["ISLAND_HOME"] = HOME
SHOTS = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None

# settings written by an older version: "Стандарт" preset with the clock off, scale 90 %
with open(os.path.join(HOME, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "scale": 90, "theme": "sand",
               "layout": {"tabs": ["home", "clipboard", "notes"], "home": ["music", "clock"],
                          "quick": ["screenshot"], "idle": "empty"}}, f)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import devices  # noqa: E402

results = []


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


# ---------------------------------------------------------------- fake Bambu printer (MQTT, no TLS)

class FakePrinter:
    CODE = "12345678"
    SERIAL = "01P00A000000001"

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.conn = None
        self.got_pushall = threading.Event()
        self.subscribed = None
        self.logins = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            c, _ = self.srv.accept()
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            t, _f, body = devices.mqtt_read(c)
            # CONNECT: protocol name, level, flags, keepalive, client id, user, password
            i = 2 + struct.unpack("!H", body[:2])[0] + 4
            parts = []
            while i < len(body):
                n = struct.unpack("!H", body[i:i + 2])[0]
                parts.append(body[i + 2:i + 2 + n].decode())
                i += 2 + n
            user, pwd = parts[1], parts[2]
            self.logins.append((user, pwd))
            ok = user == "bblp" and pwd == self.CODE
            c.sendall(b"\x20\x02\x00" + (b"\x00" if ok else b"\x05"))
            if not ok:
                c.close()
                return
            self.conn = c
            while True:
                t, f, body = devices.mqtt_read(c)
                if t == 8:
                    self.subscribed = body[4:4 + struct.unpack("!H", body[2:4])[0]].decode()
                    c.sendall(b"\x90\x03" + body[:2] + b"\x00")
                elif t == 3:
                    topic, payload = devices.mqtt_parse_publish(f, body)
                    if b"pushall" in payload and topic.endswith("/request"):
                        self.got_pushall.set()
                elif t == 12:
                    c.sendall(b"\xd0\x00")
        except (ConnectionError, OSError):
            pass

    def report(self, **fields):
        msg = json.dumps({"print": fields}).encode()
        self.conn.sendall(devices.mqtt_publish(f"device/{self.SERIAL}/report", msg))


# ---------------------------------------------------------------- fake DeskDash agent

class FakeDeskDash:
    TOKEN = "abcd1234"

    def __init__(self):
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.headers.get("X-Token") != outer.TOKEN:
                    return self._send(401, {"ok": False, "error": "bad token"})
                outer.calls.append(("GET", self.path))
                if self.path == "/api/info":
                    return self._send(200, {"name": "zenbook", "spotify": True, "macros": [
                        {"id": "lock", "label": "Блок", "icon": "lock", "confirm": False},
                        {"id": "sleep", "label": "Сон", "icon": "sleep", "confirm": True},
                        {"id": "macro_1", "label": "Telega", "icon": "desktop", "confirm": False}]})
                if self.path == "/api/spotify/state":
                    return self._send(200, {"devices": [
                        {"id": "d1", "name": "Pixel", "type": "Smartphone", "active": True},
                        {"id": "d2", "name": "ZENBOOK", "type": "Computer", "active": False}],
                        "now": {"title": "Midnight City", "artist": "M83", "playing": True, "device": "Pixel"}})
                self._send(404, {"ok": False})

            def do_POST(self):
                if self.headers.get("X-Token") != outer.TOKEN:
                    return self._send(401, {"ok": False, "error": "bad token"})
                outer.calls.append(("POST", self.path))
                self._send(200, {"ok": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.dir = tempfile.mkdtemp(prefix="deskdash-")
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.TOKEN, "name": "zenbook", "macros": []}, f)


def main():
    printer = FakePrinter()
    agent = FakeDeskDash()
    devices.BambuPrinter.PORT = printer.port
    devices.BambuPrinter.TLS = False
    devices.deskdash_dir = lambda: agent.dir

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app import commands, layout
    from island_app import services
    from island_app.app import App
    services.Media.poll = lambda self: None     # whatever plays on this PC must not get into the pill
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    def shot(name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            app.island.retarget(snap=True)       # finish the animation now
            qapp.processEvents()
            app.island.grab().save(os.path.join(SHOTS, name + ".png"))

    # ---- the clock
    def clock():
        lay = layout.current(app.settings)
        check("old settings: the clock is back in the pill", lay["idle"] == "clock")
        check("new tabs added once: apps, printer, deskdash", lay["tabs"][-3:] == ["apps", "printer", "deskdash"],
              lay["tabs"])
        check("upgrade runs once", app.settings.get("rev") == 2)
        isl = app.island
        check("pill at 90 % shows the time", isl.mode == "idle" and isl.idle_text() == time.strftime("%H:%M"),
              (isl.mode, isl.idle_text()))
        w = isl.idle_width()
        from PySide6.QtGui import QFontMetrics
        check("time fits the pill", QFontMetrics(isl.P.tabular(13)).horizontalAdvance(isl.idle_text()) < w)
        app.set_layout(idle="date")
        app.apply_preset("standard")

    def preset_keeps_idle():
        check("a preset keeps the pill choice", layout.current(app.settings)["idle"] == "date")
        check("preset still detected", layout.matching_preset(layout.current(app.settings)) == "standard",
              layout.current(app.settings))
        app.set_layout(idle="clock")
        app.apply_preset("full")
        shot("pill_clock")

    # ---- pinned apps
    def apps():
        tmp = tempfile.mkdtemp(prefix="pins-")
        exe = os.path.join(tmp, "Tool.exe")
        open(exe, "wb").close()
        a = app.apps
        check("pin a file", a.add_path(exe) and a.items[-1]["name"] == "Tool")
        check("no duplicates", a.add_path(exe) is False and len(a.items) == 1)
        check("pin a folder", a.add_path(tmp) and a.items[-1]["name"] == os.path.basename(tmp))
        check("pin a site", a.add_path("https://www.github.com/x") and a.items[-1]["name"] == "github.com")
        a.add("Калькулятор", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "appid")
        first = a.items[0]["id"]
        a.move(first, 1)
        check("move right", a.items[1]["id"] == first)
        app.island.expand("apps")
        page = app.island.panel.page("apps")
        from island_app.desk_ui import AppTile
        tiles = page.findChildren(AppTile)
        check("tiles on the tab", len([t for t in tiles if t.isVisibleTo(page)]) == 4, len(tiles))
        icon_ok = a.icon(a.items[0]) is not None or not sys.platform.startswith("win")
        check("file icon loaded", icon_ok)
        added = page.add_paths([os.path.join(tmp, "missing.lnk")])
        check("drop onto the tab pins it", len(added) == 1 and len(a.items) == 5)
        a.remove(a.items[-1]["id"])
        app.store.flush(force=True)
        from island_app import config
        check("pins survive a restart", [x["target"] for x in config.Store()["apps"]] == [x["target"] for x in a.items])
        shot("apps")
        page.show_add(True)

    def apps_picker():
        page = app.island.panel.page("apps")
        app.index.apps = [("Notepad", "C:/fake/Notepad.lnk", "lnk"), ("Paint", "C:/fake/Paint.lnk", "lnk")]
        page.search.setText("note")
        check("picker finds apps", page.list.count() >= 1 and "Notepad" in page.list.item(0).text())
        page._pin_first()
        check("picker pins", app.apps.has("C:/fake/Notepad.lnk") and page.list.item(0).text().startswith("✓"))
        res = commands.build_results(app, "закрепить paint")
        r = next((r for r in res if r["sub"] == "Закрепить на острове"), None)
        check("command bar «закрепить …»", r is not None)
        r["action"]()
        check("pinned from the command bar", app.apps.has("C:/fake/Paint.lnk"))
        res = commands.build_results(app, "paint")
        check("pinned apps come first in search", res and res[0]["sub"] == "Закреплено на острове", res[:1])
        page.show_add(False)
        app.island.expand("home")

    # ---- printer
    def printer_setup():
        app.island.collapse()
        app.printer.configure("127.0.0.1", printer.SERIAL, "wrong-code", "X1C")

    def printer_bad_code():
        check("wrong access code is reported", "код доступа" in app.printer.error, app.printer.error)
        check("access code is not stored in plain text on Windows",
              not sys.platform.startswith("win") or app.settings.get("accounts.bambu.code") != "wrong-code")
        app.printer.configure("127.0.0.1", printer.SERIAL, printer.CODE, "X1C")

    def printer_connected():
        check("connected with the access code", app.printer.connected, app.printer.error)
        check("subscribed to the report topic", printer.subscribed == f"device/{printer.SERIAL}/report",
              printer.subscribed)
        check("asked for the full state (pushall)", printer.got_pushall.is_set())
        printer.report(gcode_state="PREPARE", subtask_name="Benchy.gcode.3mf")
        QTimer.singleShot(150, lambda: printer.report(gcode_state="RUNNING", mc_percent=47, mc_remaining_time=72,
                                                      layer_num=120, total_layer_num=300, nozzle_temper=219.6,
                                                      nozzle_target_temper=220, bed_temper=60, bed_target_temper=60))

    def printer_running():
        pr = app.printer
        check("progress and time left", pr.percent == 47 and pr.remaining == 72 and pr.job == "Benchy",
              (pr.percent, pr.remaining, pr.job))
        live = app.current_live()
        check("pill shows the print", live and live["kind"] == "print" and live["value"] == "47% · 1 ч 12 мин", live)
        check("«print started» card shown in the pill first", app.island.mode == "toast")
        while app.island.toast:
            app.island.dismiss_toast()
        check("then the pill shows the print", app.island.mode == "live", app.island.mode)
        check("«print started» notification", any(n["source"] == "printer" and "началась" in n["title"]
                                                  for n in app.notifications.history))
        shot("pill_print")
        app.island.expand("printer")
        page = app.island.panel.page("printer")
        check("printer tab shows it", page.pct.text() == "47%" and "1 ч 12 мин" in page.left.text()
              and "120 из 300" in page.layer.text(), (page.pct.text(), page.left.text(), page.layer.text()))
        shot("printer")
        res = commands.build_results(app, "принтер")
        check("command bar «принтер»", any("47%" in r["title"] for r in res))
        printer.report(mc_percent=48)            # P1/A1 send only what changed

    def printer_delta():
        check("partial reports are merged", app.printer.percent == 48 and app.printer.remaining == 72)
        printer.report(gcode_state="FINISH", mc_percent=100, mc_remaining_time=0)

    def printer_done():
        check("«print finished» notification", any(n["source"] == "printer" and "готова" in n["title"]
                                                   for n in app.notifications.history))
        live = app.current_live()
        check("pill no longer shows the print", not live or live["kind"] != "print")
        app.island.collapse()

    # ---- DeskDash
    def deskdash():
        dd = app.deskdash
        dd.poll()

    def deskdash_online():
        dd = app.deskdash
        check("DeskDash agent found through its config.json", dd.online and dd.name == "zenbook", dd.error)
        check("macros listed", [m["id"] for m in dd.macros] == ["lock", "sleep", "macro_1"])
        check("Spotify on the phone", dd.remote_playing() and dd.now["device"] == "Pixel")
        live = app.current_live()
        check("phone's Spotify in the pill when the PC plays nothing",
              live and live["kind"] == "music" and live["title"] == "Midnight City", live)
        app.island.expand("deskdash")
        page = app.island.panel.page("deskdash")
        from island_app.desk_ui import MacroButton
        btns = page.findChildren(MacroButton)
        check("macro buttons", len(btns) == 3)
        btns[0].click()
        sleep = next(b for b in btns if b.macro["id"] == "sleep")
        sleep.click()
        check("«confirm» macros wait for a second click", sleep.armed
              and ("POST", "/api/macro/sleep") not in agent.calls)
        res = commands.build_results(app, "teleg")
        r = next((r for r in res if r["sub"] == "Макрос DeskDash"), None)
        check("macro from the command bar", r is not None)
        r["action"]()
        shot("deskdash")

    def deskdash_calls():
        check("macros ran on the agent", ("POST", "/api/macro/lock") in agent.calls
              and ("POST", "/api/macro/macro_1") in agent.calls, agent.calls)
        page = app.island.panel.page("deskdash")
        page._transfer({"id": "d2", "name": "ZENBOOK"})

    def deskdash_transfer():
        check("Spotify Connect: play here", ("POST", "/api/spotify/transfer/d2") in agent.calls)
        app.island.expand("home")
        app.set_layout(home=["music", "printer", "deskdash", "apps"])

    def home_cards():
        app.island.expand("home")
        check("home cards: printer, DeskDash, apps", app.island.mode == "expanded")
        shot("home")
        agent.srv.shutdown()
        agent.srv.server_close()
        app.deskdash.poll()

    def deskdash_offline():
        check("DeskDash closed: shown as offline", not app.deskdash.online and app.deskdash.error)
        check("phone's Spotify gone from the pill", not app.deskdash.remote_playing())
        app.island.collapse()

    step(600, clock)
    step(700, preset_keeps_idle)
    step(900, apps)
    step(500, apps_picker)
    step(300, printer_setup)
    step(1500, printer_bad_code)
    step(4500, printer_connected)       # the retry after a failure waits 3 s
    step(1600, printer_running)
    step(1400, printer_delta)
    step(1400, printer_done)
    step(200, deskdash)
    step(1200, deskdash_online)
    step(900, deskdash_calls)
    step(900, deskdash_transfer)
    step(900, home_cards)
    step(3500, deskdash_offline)        # Windows reports a refused local connection after ~2 s

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
            app.printer.stop()
            qapp.exit(0 if all(results) else 1)
            return
        d, fn = steps[i]

        def go():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"step {fn.__name__} ran", False, exc)
            run(i + 1)
        QTimer.singleShot(d, go)
    run()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
