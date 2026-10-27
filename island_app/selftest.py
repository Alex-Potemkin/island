"""`python main.py --demo [--out DIR] [--exit]`: walks through the island's states and saves screenshots.
Used to check the app visually; run with ISLAND_HOME pointing to a scratch folder to keep real data untouched."""
import datetime as dt
import os
import time

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication


def run(app, argv):
    out = argv[argv.index("--out") + 1] if "--out" in argv else os.path.join(os.getcwd(), "docs", "screens")
    os.makedirs(out, exist_ok=True)
    steps = []
    app.settings.set("weather.city", "Москва")
    app.weather.refresh()

    isl = lambda: app.island  # noqa: E731 - the window is recreated on theme/scale changes

    def step(delay, fn=None, snap=None, full=False):
        steps.append((delay, fn, snap, full))

    def snap(name, full=False):
        """Render the island window itself over a neutral gradient (no desktop content leaks in)."""
        w = isl()
        h = int(w.shape_rect().bottom() + w.pad * 0.8) if not full else w.height()
        pm = w.grab(QRect(0, 0, w.width(), max(60, h)))
        img = QImage(pm.size(), QImage.Format_RGB32)
        img.setDevicePixelRatio(pm.devicePixelRatio())
        p = QPainter(img)
        g = QLinearGradient(0, 0, img.width(), img.height())
        g.setColorAt(0, QColor("#9aa5ae") if app.theme.dark else QColor("#5b6670"))
        g.setColorAt(1, QColor("#6f7a84") if app.theme.dark else QColor("#3c454d"))
        p.fillRect(QRect(0, 0, w.width(), max(60, h)), g)
        p.drawPixmap(0, 0, pm)
        p.end()
        img.save(os.path.join(out, name + ".png"))
        print("snap", name, flush=True)

    def fake_music(playing=True):
        app.media.state = {"active": True, "title": "Midnight City", "artist": "M83", "album": "Hurry Up",
                           "playing": playing, "position": 95, "duration": 243, "stamp": time.time(), "art": b""}
        app.media.changed.emit(app.media.state)

    # live states
    step(1200, None, "01_idle")
    step(300, lambda: app._layout("EN"), None)
    step(700, None, "02_layout")
    step(1600, lambda: app._caps(True), None)
    step(700, None, "03_caps")
    step(300, lambda: app._caps(False), None)
    step(1700, lambda: app.start_timer(25 * 60, "Фокус"), None)
    step(2200, None, "04_timer")
    step(200, app.timer.reset, None)
    step(200, lambda: (app.media._timer.stop(), fake_music()), None)
    step(900, None, "05_music")
    step(200, lambda: app._claude({"hook_event_name": "PreToolUse", "cwd": r"C:\dev\island", "tool_name": "Bash"}), None)
    step(900, None, "06_claude")
    step(200, lambda: app._claude({"hook_event_name": "Stop", "cwd": r"C:\dev\island"}), None)
    step(200, lambda: app._set_activity("claude", None), None)
    step(200, lambda: app.alert("\uEF3C", "#1E1E1E", "rgb(30, 30, 30) · скопирован", swatch="#1E1E1E", ttl=3), None)
    step(800, None, "07_color")
    step(200, app.clear_alert, None)
    # sample data
    step(100, lambda: _sample_data(app), None)
    # notifications: a card, a GitHub card, and the one-line style
    step(400, lambda: app.notifications.push("calendar", "Созвон по дизайну", "через 10 мин · 15:00 · Zoom"), None)
    step(900, None, "60_toast_calendar")
    step(100, lambda: app.island.dismiss_toast(), None)
    step(600, lambda: app.notifications.push("github", "Fix tray icon on Linux", "octo/island · pull request"), None)
    step(900, None, "61_toast_github")
    step(100, lambda: app.island.dismiss_toast(), None)
    step(600, lambda: (app.settings.set("notifications", {"style": "pill", "sound": "none"}),
                       app.notifications.push("scripts", "Сборка готова", "island · 2 мин 14 с")), None)
    step(900, None, "62_pill_notify")
    step(100, lambda: (app.clear_alert(), app.settings.set("notifications", {"sound": "none"})), None)
    # pages
    for i, key in enumerate(["home", "clipboard", "shelf", "notes", "timer", "reminders", "control", "tools",
                             "system", "ai", "settings"]):
        step(300, lambda k=key: isl().expand(k), None)
        step(1300 if key != "system" else 3200, None, f"1{i:01d}_{key}" if i < 10 else f"2{i - 10}_{key}")
    for view, name in (("week", "26_calendar_week"), ("month", "28_calendar_month"), ("agenda", "29_calendar_list")):
        step(300, lambda v=view: (isl().expand("calendar"), isl().panel.page("calendar").set_view(v)), None)
        step(1300, None, name)
    step(300, lambda: isl().panel.page("calendar").new_event(None), None)
    step(1000, lambda: (isl().panel.page("calendar").editor.quick.setText("обед с Сашей завтра в 13"),
                        isl().panel.page("calendar").editor._parse()), None)
    step(900, None, "2a_calendar_new")
    step(300, lambda: (isl().panel.page("calendar").set_view("week"), isl().expand("inbox")), None)
    step(1300, None, "27_inbox")
    step(200, lambda: isl().collapse(), None)
    # settings sections and first run
    for i, name in ((1, "23_settings_look"), (2, "21_settings_builder"), (3, "24_settings_notify"),
                    (4, "25_settings_accounts"), (5, "22_settings_more")):
        step(300, lambda i=i: (isl().expand("settings"), isl().panel.page("settings")._section(i)), None)
        step(1200, None, name)
    step(200, lambda: isl().collapse(), None)
    step(600, lambda: isl().expand("welcome"), None)
    step(1300, None, "08_welcome")
    step(200, lambda: isl().collapse(), None)
    # command bar
    for name, q in (("30_cmd_empty", ""), ("31_cmd_timer", "таймер 10 мин"), ("32_cmd_remind", "напомни позвонить маме завтра в 9"),
                    ("33_cmd_calc", "2^10 + 15% от 200"), ("34_cmd_conv", "10 км в мили"), ("35_cmd_app", "блокнот")):
        step(600, lambda q=q: (isl().expand("command"), isl().panel.command.input.setText(q)), None)
        step(1200, None, name)
    step(200, lambda: isl().collapse(), None)
    # light theme and scale
    step(600, lambda: app.set_setting("theme", "light", rebuild=True), None)
    step(1200, lambda: isl().expand("home"), None)
    step(1300, None, "40_light_home")
    step(200, lambda: isl().expand("control"), None)
    step(1300, None, "41_light_control")
    step(200, lambda: isl().collapse(), None)
    step(200, lambda: (app.media._timer.stop(), fake_music()), None)
    step(1000, None, "42_light_music")
    step(300, lambda: app.set_setting("theme", "dark", rebuild=False), None)
    step(100, lambda: app.set_setting("scale", 150, rebuild=True), None)
    step(1300, lambda: isl().expand("home"), None)
    step(1300, None, "43_scale150_home")
    step(200, lambda: app.set_setting("scale", 100, rebuild=True), None)
    # appearance: themes, accent, small pill, position
    looks = [("50_graphite_blue", "graphite", dict(accent="blue")),
             ("51_midnight_violet", "midnight", dict(accent="violet", corners="round")),
             ("52_sand_orange", "sand", dict(accent="orange", corners="sharp")),
             ("53_fog_green_scale70", "fog", dict(accent="green"))]
    for name, th, look in looks:
        def apply(th=th, look=look, name=name):
            app.settings.set("theme", th, save=False)
            app.settings.set("scale", 70 if "scale70" in name else 100, save=False)
            app.settings.set("appearance", dict(look))
            app.rebuild()
        step(300, apply, None)
        step(1000, lambda: isl().expand("control"), None)
        step(1300, None, name)
    step(300, lambda: (app.settings.set("theme", "dark", save=False), app.settings.set("scale", 100, save=False),
                       app.settings.set("appearance", dict(pill="small", position="left", accent="pink")),
                       app.rebuild()), None)
    step(900, lambda: (isl().panel.pin.setChecked(False), isl().collapse()), None)
    step(1200, None, "54_small_pill_left")
    step(300, lambda: (app.settings.set("appearance", {}), app.rebuild()), None)
    step(900, lambda: (isl().panel.pin.setChecked(False), isl().collapse(), app.media.state.update(active=False, playing=False),
                       app.media.changed.emit(app.media.state), app.set_layout(idle="clock")), None)
    step(900, lambda: isl().collapse(), None)
    step(1500, None, "09_idle_clock")
    step(200, lambda: app.apply_preset("minimal"), None)
    step(900, lambda: isl().expand("home"), None)
    step(1300, None, "44_minimal_home")
    step(200, lambda: isl().collapse(), None)
    step(1000, None, None)

    def runner(i=0):
        if i >= len(steps):
            print("demo done", flush=True)
            if "--exit" in argv:
                QApplication.instance().quit()
            return
        delay, fn, name, full = steps[i]

        def go():
            try:
                if fn:
                    fn()
                if name:
                    snap(name, full)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print("step failed", i, exc, flush=True)
            runner(i + 1)
        QTimer.singleShot(delay, go)
    runner()


def _sample_events(app):
    import datetime as dt
    now = dt.datetime.now().astimezone().replace(second=0, microsecond=0)
    day = now.replace(hour=0, minute=0)

    def ev(d, h, m, mins, title, loc="", src="Google"):
        st = day + dt.timedelta(days=d, hours=h, minutes=m)
        return {"title": title, "start": st, "end": st + dt.timedelta(minutes=mins), "all_day": False,
                "location": loc, "source": src, "url": "", "id": title + str(d)}
    soon = now + dt.timedelta(minutes=25)
    evs = [{"title": "Созвон по дизайну", "start": soon, "end": soon + dt.timedelta(minutes=45), "all_day": False,
            "location": "Zoom", "source": "Google", "url": "", "id": "s"},
           ev(0, 23, 0, 30, "Ревью PR", "", "GitHub-календарь"),
           ev(1, 9, 30, 15, "Планёрка, команда", "Переговорная 2", "Работа · iCal"),
           ev(1, 13, 0, 60, "Обед с Сашей", "Кофейня"),
           ev(2, 18, 0, 90, "Тренировка", "", "Яндекс · iCal")]
    cal = app.calendar
    cal.has_accounts = lambda: True
    cal.refresh = lambda: None
    cal.remote = evs + [ev(d, 10, 0, 60, "Стендап", "", "Работа · iCal") for d in range(-3, 4) if d != 0]
    cal.updated = __import__("time").time()
    if not cal.local:
        cal.save_event({"title": "Бассейн", "start": day + dt.timedelta(days=1, hours=19),
                        "end": day + dt.timedelta(days=1, hours=20), "repeat": "weekly"})
        cal.save_event({"title": "Отпуск", "start": day + dt.timedelta(days=4), "end": day + dt.timedelta(days=5),
                        "all_day": True})
    cal._merge()


def _sample_data(app):
    _sample_events(app)
    import uuid
    if not app.store["notes"]:
        app.store["notes"].extend([
            {"id": uuid.uuid4().hex[:10], "text": "Идеи для Island\n— виджет погоды на неделю\n— OCR в таблицу",
             "t": time.time()},
            {"id": uuid.uuid4().hex[:10], "text": "Список покупок\nмолоко, хлеб, кофе", "t": time.time() - 3600}])
    for t in ("git commit -m \"Island: первая версия\"", "https://islandapp.pro",
              "Встреча в 15:00 — обсудить дизайн пилюли", "#1E1E1E"):
        app.clipboard.set_text(t)
        QApplication.processEvents()
    if not app.reminders.items:
        now = dt.datetime.now()
        app.reminders.add("Позвонить маме", now.replace(hour=9, minute=0) + dt.timedelta(days=1))
        app.reminders.add("Созвон по дизайну", now + dt.timedelta(hours=2))
    if not app.store["shelf"]:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app.store["shelf"].extend([os.path.join(here, "README.md"), os.path.join(here, "main.py"),
                                   os.path.join(here, "island_app")])
        app.island.panel.page("shelf").refresh()
    app.island.panel.page("notes").refresh()
    app.store.touch()
