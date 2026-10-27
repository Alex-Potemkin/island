"""Calendar views and local events, notification sounds, other apps' notifications.

    python tests/check_calendar.py
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ISLAND_HOME"] = tempfile.mkdtemp(prefix="island-cal-")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import integrations, sounds  # noqa: E402

results = []
LOCAL = dt.datetime.now().astimezone().tzinfo


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def fake_wpn(path):
    """A database with the same tables Windows keeps notifications in."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);
        CREATE TABLE HandlerAssets (HandlerId INTEGER, AssetKey TEXT, AssetValue TEXT);
        CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB, ArrivalTime INTEGER);
        INSERT INTO NotificationHandler VALUES (1, 'TelegramDesktop.TelegramDesktop_t4vj0pshhgkwm!Telegram');
        INSERT INTO NotificationHandler VALUES (2, 'Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_com.vk');
        INSERT INTO NotificationHandler VALUES (3, 'C:\\Program Files\\Island\\Island.exe');
        INSERT INTO HandlerAssets VALUES (2, 'DisplayName', 'VK');
        INSERT INTO Notification VALUES (10, 1, 'toast', '<toast><visual><binding><text>Старое</text></binding></visual></toast>', 1);
    """)
    con.commit()
    return con


def main():
    # ---- sounds
    for key in sounds.SOUNDS:
        p = sounds.sound_file(key, 60)
        if key in ("system", "none"):
            continue
        with wave.open(p) as w:
            ok = w.getframerate() == 44100 and 0.05 < w.getnframes() / 44100 < 2.6   # melodies ring up to ~2.2 s
        check(f"sound «{sounds.SOUNDS[key]}» generated", ok, os.path.basename(p))
    quiet, loud = sounds.sound_file("bell", 10), sounds.sound_file("bell", 100)
    with wave.open(quiet) as a, wave.open(loud) as b:
        qa = max(abs(int.from_bytes(a.readframes(2000)[i:i + 2], "little", signed=True)) for i in range(0, 4000, 2))
        qb = max(abs(int.from_bytes(b.readframes(2000)[i:i + 2], "little", signed=True)) for i in range(0, 4000, 2))
    check("volume changes the loudness", qb > qa * 5, (qa, qb))

    # ---- other apps' notifications
    db = os.path.join(tempfile.mkdtemp(), "wpndatabase.db")
    con = fake_wpn(db)
    top, rows = integrations.read_windows_toasts(db, None)
    check("first read starts from now (no replay of old toasts)", top == 10 and rows == [])
    con.execute("INSERT INTO Notification VALUES (11, 1, 'toast', ?, 2)",
                ("<toast><visual><binding><text>Маша</text><text>Привет! &amp; как дела?</text></binding></visual></toast>",))
    con.execute("INSERT INTO Notification VALUES (12, 2, 'toast', ?, 3)",
                ("<toast><visual><binding><text>Новое сообщение</text><text>от Пети</text></binding></visual></toast>",))
    con.execute("INSERT INTO Notification VALUES (13, 1, 'badge', '<badge value=\"3\"/>', 4)")
    con.execute("INSERT INTO Notification VALUES (14, 3, 'toast', '<toast><visual><binding><text>Своё</text></binding></visual></toast>', 5)")
    con.commit()
    top, rows = integrations.read_windows_toasts(db, top)
    check("new toasts are read with app names", [(r[1], r[2], r[3]) for r in rows] ==
          [("Telegram", "Маша", "Привет! & как дела?"), ("VK", "Новое сообщение", "от Пети")], rows)
    check("badges and Island's own toasts are skipped", top == 14 and len(rows) == 2)
    con.close()
    dbus = integrations.parse_dbus_notify("""method call time=1 sender=:1.9 -> destination=:1.2 serial=5 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Thunderbird"
   uint32 0
   string "thunderbird"
   string "Новое письмо"
   string "Счёт за октябрь"
""".splitlines())
    check("Linux D-Bus notifications are parsed", dbus == [("Thunderbird", "Новое письмо", "Счёт за октябрь")], dbus)

    # ---- the app: local calendar, views, sounds routing, mirrored notifications
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app.app import App
    played = []
    sounds.play = lambda name, volume=80, custom="": played.append((name, volume))
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    cal = app.calendar
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())

    def at(d, h, m=0):
        return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=LOCAL)

    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    def local_events():
        lid = cal.save_event({"title": "Бассейн", "start": at(monday, 19), "end": at(monday, 20), "repeat": "weekly"})
        cal.save_event({"title": "Отпуск", "start": at(monday + dt.timedelta(days=3), 0),
                        "end": at(monday + dt.timedelta(days=4), 0), "all_day": True})
        swims = [e for e in cal.events if e["title"] == "Бассейн"]
        check("weekly event repeats across the window", len(swims) >= 15 and all(
            e["start"].weekday() == 0 and e["start"].hour == 19 for e in swims), len(swims))
        cal.save_event({"title": "Бассейн (вт)", "start": at(monday + dt.timedelta(days=1), 18),
                        "end": at(monday + dt.timedelta(days=1), 19), "repeat": "none"}, lid)
        swims = [e for e in cal.events if e["title"].startswith("Бассейн")]
        check("editing a local event replaces its series", len(swims) == 1 and swims[0]["start"].weekday() == 1)
        cal.delete_event(lid)
        check("deleting a local event", not any(e["title"].startswith("Бассейн") for e in cal.events))
        check("local events persist in data.json", any(e["title"] == "Отпуск" for e in app.store["events"]))
        cal.save_event({"title": "A", "start": at(monday + dt.timedelta(days=2), 10), "end": at(monday + dt.timedelta(days=2), 12)})
        cal.save_event({"title": "B", "start": at(monday + dt.timedelta(days=2), 11), "end": at(monday + dt.timedelta(days=2), 13)})
        app.island.expand("calendar")

    def views():
        page = app.island.panel.page("calendar")
        for v in ("week", "month", "agenda"):
            page.set_view(v)
            qapp.processEvents()
            img = page.grab()
            check(f"{v} view renders", not img.isNull() and img.width() > 300)
        page.set_view("week")
        page.week.anchor = monday
        page.refresh()
        page.week.grab()
        blocks = {e["title"]: r for r, e in page.week.hits if isinstance(e, dict)}
        a, b = blocks.get("A"), blocks.get("B")
        check("overlapping events sit side by side", a and b and abs(a.width() - b.width()) < 2
              and a.right() <= b.left() + 2, (a, b))
        check("all-day event drawn in the top strip", "Отпуск" in blocks and blocks["Отпуск"].top() < a.top())
        slot = next(t for r, t in page.week.hits if not isinstance(t, dict))
        check("week view: empty slots start new events", isinstance(slot, dt.datetime))
        page.week.step(1)
        check("week navigation", page.week.anchor == monday + dt.timedelta(days=7))
        page.set_view("month")
        m0 = page.month.anchor.month
        page.month.step(1)
        check("month navigation", page.month.anchor.month == (m0 % 12) + 1, page.head.text())
        page.month.step(-1)
        page.refresh()
        page.month.grab()
        cells = [t for r, t in page.month.hits if not isinstance(t, dict)]
        check("month grid has 6 weeks", len(cells) == 42)
        page.month.new_event.emit(cells[10])
        check("clicking a day opens its week", page.view == "week" and page.week.anchor == cells[10].date())
        page.new_event(None)
        ed = page.editor
        ed.quick.setText("ужин с Олей завтра в 20:30")
        ed._parse()
        v = ed.values()
        check("quick add parses text into the form", v["title"] == "ужин с Олей" and v["start"].hour == 20
              and v["start"].minute == 30 and v["start"].date() == today + dt.timedelta(days=1), v)
        ed._save()
        check("saved from the editor", any(e["title"] == "ужин с Олей" for e in cal.events)
              and page.stack.currentWidget() is page.week)

    def sounds_routing():
        app.island.collapse(restore_focus=True)     # cards wait while the panel is open
        nc = app.notifications
        app.settings.set("notifications", {"style": "card", "sound": "chime", "volume": 40,
                                           "sounds": {"github": "pop", "calendar": "none"}})
        played.clear()
        nc.push("scripts", "x")
        app.island.dismiss_toast()
        nc.push("github", "y")
        app.island.dismiss_toast()
        nc.push("calendar", "z")
        app.island.dismiss_toast()
        check("default sound and volume", played[0] == ("chime", 40), played)
        check("per-source sound", played[1] == ("pop", 40), played)
        check("per-source silence", played[2] == ("none", 40), played)
        app.settings.set("notifications", {"sound": True})
        check("old on/off sound setting still works", nc.cfg["sound"] == "drop")

    def mirrored():
        sysn = app.system_notifications
        app.settings.set("notifications", {"style": "card", "sound": "none", "muted_apps": ["Spam"]})
        sysn._deliver([("Telegram", "Маша", "Привет!"), ("Spam", "Купи", "сейчас")])
        n = app.island.toast
        check("other apps' notifications come to the pill", n and n["title"] == "Маша" and n["app"] == "Telegram", n)
        check("muted apps are skipped", not any(h["app"] == "Spam" for h in app.notifications.history))
        check("seen apps are listed for muting", {"Telegram", "Spam"} <= sysn.seen_apps)
        app.island.dismiss_toast()
        app.settings.set("notifications", {"apps": False})
        before = len(app.notifications.history)
        sysn._deliver([("Telegram", "ещё", "")])
        check("switching mirroring off", len(app.notifications.history) == before)

    step(500, local_events)
    step(800, views)
    step(500, sounds_routing)
    step(300, mirrored)

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
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
