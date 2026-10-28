"""Application controller: wires services, live activities, tray, hotkeys and actions together."""
import os
import sys
import time
import zlib

from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import (bridge, capture, config, devices, features, integrations, layout, osapi, perf, power, recorder,
               services)
from .notifications import Notifications
from . import widgets as W
from .island import Island, is_input
from .theme import Theme

SERVER_NAME = "IslandApp-" + (os.environ.get("USERNAME") or "user")
if os.environ.get("ISLAND_HOME"):  # a test profile runs as its own instance
    SERVER_NAME += "-" + str(zlib.crc32(os.environ["ISLAND_HOME"].encode()))

BUBBLE_KINDS = ("music", "print", "timer", "download", "claude")
PRIORITY = {"alert": 100, "rec": 98, "level": 95, "claude_wait": 80, "timer": 60, "claude": 50, "download": 40, "print": 35, "music": 30,
            "caps": 20}


def make_icon(size=64, dark=True):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(255, 255, 255, 60) if dark else Qt.NoPen)
    p.setBrush(QColor("#000000"))
    h = size * 0.42
    p.drawRoundedRect(QRectF(size * 0.06, (size - h) / 2, size * 0.88, h), h / 2, h / 2)
    p.setBrush(QColor("#ffffff"))
    p.setPen(Qt.NoPen)
    d = h * 0.32
    p.drawEllipse(QRectF(size * 0.94 - h / 2 - d / 2 - size * 0.04, size / 2 - d / 2, d, d))
    p.end()
    return QIcon(pm)


def _upgrade_settings(settings):
    """One-time changes for settings written by older versions."""
    rev = settings.get("rev", 1)
    if not isinstance(rev, int) or rev >= 3:
        return
    if rev < 2:
        _upgrade_v2(settings)
    # 3: the Claude chat is gone — its tab, hot key and stored key
    lay = settings.get("layout")
    if isinstance(lay, dict) and isinstance(lay.get("tabs"), list) and "ai" in lay["tabs"]:
        lay["tabs"] = [t for t in lay["tabs"] if t != "ai"]
    for path in ("ai", "hotkeys.ai"):
        node, *rest = path.split(".")
        if rest:
            if isinstance(settings.data.get(node), dict):
                settings.data[node].pop(rest[0], None)
        else:
            settings.data.pop(node, None)
    settings.set("rev", 3)


def _upgrade_v2(settings):
    lay = settings.get("layout")
    if isinstance(lay, dict):
        if lay.get("idle") in (None, "empty"):
            lay["idle"] = "clock"        # older presets switched the clock off without saying so
        tabs = lay.get("tabs")
        if isinstance(tabs, list):
            tabs.extend(t for t in ("apps", "printer", "deskdash") if t not in tabs)


class App(QObject):
    def __init__(self, qapp, args=()):
        super().__init__()
        self.qapp = qapp
        self.args = list(args)
        self.demo = "--demo" in self.args
        self.hard_exit = True   # tests turn this off to keep their own exit code
        bridge.init()
        config.migrate_old_data()
        self.settings = config.Settings()
        _upgrade_settings(self.settings)
        perf.configure(self.settings)
        self.store = config.Store()
        self.activities = {}
        self.privacy = ([], [])
        self.overlay = None
        self.ui_state = {}          # survives UI rebuilds (settings section, scroll position)
        # services
        self.media = services.Media()
        self.radios = services.Radios()
        self.monitor = services.Monitor()
        self.weather = services.Weather(self.settings)
        self.sys = services.SysWatch()
        self.timer = features.TimerService(self.settings)
        self.reminders = features.Reminders(self.store)
        self.clipboard = features.ClipboardHistory(self.settings, self.store)
        self.index = features.Index()
        self.apps = features.PinnedApps(self.store)
        self.notifications = Notifications(self)
        self.calendar = integrations.CalendarService(self.settings, self.notifications.push, self.store)
        self.github = integrations.GitHubService(self.settings, self.notifications.push)
        self.system_notifications = integrations.SystemNotifications(self.settings, self.notifications.push)
        self.printer = devices.BambuPrinter(self.settings, self.notifications.push)
        self.deskdash = devices.DeskDash(self.settings)
        self.power = power.PowerWatch(self)
        self.recorder = recorder.Recorder(self.settings)
        self.recorder.changed.connect(self._recording)
        self.recorder.saved.connect(self._recording_saved)
        self.recorder.failed.connect(lambda msg: self.alert("\uE7BA", "Запись экрана", msg, ttl=5, important=True))
        self.hooks = services.HookServer(int(self.settings.get("hooks.port", 47811))) \
            if self.settings.get("hooks.enabled", True) else None
        self._alert_timer = QTimer(self, singleShot=True, timeout=self.clear_alert)
        self._claude_clear = QTimer(self, singleShot=True, timeout=lambda: self._set_activity("claude", None))
        self.build_ui()
        self.connect_signals()
        self.make_tray()
        self._flush = QTimer(self, interval=1000, timeout=self.store.flush)
        self._flush.start()
        self.watcher = QFileSystemWatcher([config.SETTINGS_PATH], self)
        self.watcher.fileChanged.connect(self._settings_file_changed)
        qapp.installEventFilter(self)
        qapp.aboutToQuit.connect(self._shutdown)
        self._rebuild_timer = QTimer(self, singleShot=True, interval=450, timeout=self.rebuild)
        self._style_timer = QTimer(self, singleShot=True, interval=40, timeout=self.restyle)
        self._layout_timer = QTimer(self, singleShot=True, interval=350, timeout=self.relayout_panel)
        if not self.settings.get("onboarded") and not self.demo:
            QTimer.singleShot(700, self._welcome)
        if bool(self.settings.get("autostart")) != osapi.autostart_enabled():
            try:
                osapi.set_autostart(bool(self.settings.get("autostart")))
            except OSError:
                pass

    # ------------------------------------------------------------ UI

    def build_ui(self):
        self.theme = Theme(self.settings)
        self.pill_theme = Theme(self.settings, pill=True)
        W.set_theme(self.theme)
        self.qapp.setStyleSheet(self.theme.stylesheet())
        self.island = Island(self)
        self.island.show()
        osapi.register_power_notifications(self.island)    # brightness, battery, power source
        failed = self.island.register_hotkeys(self.settings.get("hotkeys", {}))
        if failed:
            QTimer.singleShot(1500, lambda: self.alert("", "Занято сочетание", ", ".join(failed), ttl=5))
        self.on_live()

    def rebuild(self):
        """The interface scale changed (sizes are baked into every widget): build a new window.
        The new one opens already expanded on the same page before the old one goes, so nothing jumps."""
        perf.configure(self.settings)
        old = self.island
        page = old.panel.current.key if old.mode == "expanded" and old.panel.current else old.page_key
        expanded = old.mode == "expanded"
        old.register_hotkeys({})
        osapi.unregister_power_notifications(old)
        old.destroy_backdrop()
        old.panel.teardown()
        self.build_ui()
        if expanded:
            self.island.expand(page)
            self.island.retarget(snap=True)
        old.hide()
        old.deleteLater()
        self.tray.setIcon(make_icon(64))

    def restyle(self):
        """Theme, colours, corners, shadow, animation, font, pill size, position: applied in place."""
        perf.configure(self.settings)
        self.theme.__init__(self.settings)
        self.pill_theme.__init__(self.settings, pill=True)
        W.set_theme(self.theme)
        self.qapp.setStyleSheet(self.theme.stylesheet())
        self.island.restyle()

    def relayout_panel(self):
        """Tabs, home cards, quick buttons: rebuilt inside the open panel."""
        self.island.panel.rebuild_layout()
        self.island.live_changed()

    def make_tray(self):
        self.tray = QSystemTrayIcon(make_icon(64), self)
        self.tray.setToolTip("Island")
        m = QMenu()
        m.addAction("Открыть панель", lambda: self.island.expand())
        m.addAction("Командная строка\tAlt+Space", self.open_command)
        m.addAction("Скриншот области", self.screenshot)
        m.addAction("Пипетка", self.color_picker)
        m.addSeparator()
        self.a_hide = QAction("Скрыть остров", m, checkable=True)
        self.a_hide.toggled.connect(lambda on: self.island.setVisible(not on))
        m.addAction(self.a_hide)
        self.a_auto = QAction("Запускать с Windows", m, checkable=True)
        self.a_auto.setChecked(osapi.autostart_enabled())
        self.a_auto.toggled.connect(self.set_autostart)
        m.addAction(self.a_auto)
        m.addAction("Настройки", lambda: self.island.expand("settings"))
        m.addAction("settings.json", lambda: osapi.open_path(config.SETTINGS_PATH))
        m.addSeparator()
        m.addAction("Выход", self.quit)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.island.toggle())
        self.tray.show()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and hasattr(obj, "window"):
            try:
                if obj.window() is self.island:
                    self.island.note_typing()
            except RuntimeError:
                pass
        # clicking a text field in the (non-activating) panel lets it take keyboard focus
        if e.type() == QEvent.MouseButtonPress and hasattr(obj, "window"):
            try:
                if obj.window() is self.island and is_input(obj) and not self.island.input_mode:
                    self.island.focus_input()
            except RuntimeError:
                pass
        return False

    # ------------------------------------------------------------ signals

    def connect_signals(self):
        self.media.changed.connect(lambda _s: self.on_live())
        self.timer.changed.connect(self.on_live)
        self.timer.finished.connect(self._timer_finished)
        self.reminders.fired.connect(self._reminder_fired)
        self.printer.changed.connect(self.on_live)
        self.notifications.changed.connect(self._unread_changed)
        self.deskdash.spotify_changed.connect(self.on_live)
        self.sys.layout_changed.connect(self._layout)
        self.sys.caps_changed.connect(self._caps)
        self.sys.privacy_changed.connect(self._privacy)
        self.sys.fullscreen_changed.connect(self._fullscreen)
        self.sys.downloads_changed.connect(self._downloads)
        self.sys.download_finished.connect(self._download_done)
        if self.hooks:
            self.hooks.claude.connect(self._claude)
            self.hooks.notify.connect(lambda t, x: self.notifications.push("scripts", t or "Уведомление", x))

    def _settings_file_changed(self, path):
        if path not in self.watcher.files():
            self.watcher.addPath(path)
        if self.settings.changed_on_disk() and self.settings.load():
            QTimer.singleShot(50, self.rebuild)      # edited by hand: anything may have changed

    # ------------------------------------------------------------ live activities

    def _unread_changed(self):
        isl = getattr(self, "island", None)
        if isl is not None and isl.mode == "idle":
            isl.retarget()          # the unread counter changes the pill's width

    def _set_activity(self, key, value):
        if value is None:
            self.activities.pop(key, None)
        else:
            self.activities[key] = value
        self.on_live()

    def current_live(self):
        lives = self.live_list()
        if lives:
            return lives[0]
        if any(self.privacy) and self.settings.get("live.privacy", True):
            mic, cam = self.privacy
            what = "Камера и микрофон" if mic and cam else ("Камера" if cam else "Микрофон")
            return {"kind": "privacy", "glyph": "\uE722" if cam else "\uE720", "title": what,
                    "sub": _app_name((cam or mic)[0])}
        if self.sys.caps and self.settings.get("live.caps", True):
            return {"kind": "alert", "glyph": "\uE752", "title": "Caps Lock"}
        return None

    def second_live(self):
        """A second long-running activity (printing while music plays…), shown in a small bubble next to
        the pill. Short alerts never go there."""
        if not self.settings.get("live.split", True):
            return None
        rest = [x for x in self.live_list()[1:] if x["kind"] in BUBBLE_KINDS]
        return rest[0] if rest else None

    def live_list(self):
        """Live activities, most important first."""
        live = dict(self.activities)
        st = self.media.state
        if st.get("active") and self.settings.get("live.music", True) and (st.get("playing") or "music" in live):
            live["music"] = {"kind": "music", "title": st.get("title") or st.get("artist") or "",
                             "playing": st.get("playing"), "art": st.get("art")}
        elif self.settings.get("live.music", True) and self.deskdash.remote_playing():
            now = self.deskdash.remote_playing()       # Spotify on the phone / a speaker, via DeskDash
            live["music"] = {"kind": "music", "title": now.get("title", ""), "playing": True, "art": None,
                             "device": now.get("device", "")}
        if self.settings.get("live.print", True) and self.printer.active:
            pr = self.printer
            live["print"] = {"kind": "print", "title": pr.job or pr.cfg()["name"], "progress": pr.percent / 100,
                             "value": f"{pr.percent}% · {devices.fmt_minutes(pr.remaining)}",
                             "paused": pr.gcode_state == "PAUSE", "action": lambda: self.island.expand("printer")}
        if self.settings.get("live.timer", True) and self.timer.active:
            live["timer"] = {"kind": "timer", "title": self.timer.label or "Таймер", "value": self.timer.display(),
                             "progress": self.timer.progress(), "running": self.timer.running,
                             "action": lambda: self.island.expand("timer")}
        return [live[k] for k in sorted(live, key=lambda k: -PRIORITY.get(k, 0))]

    def on_live(self):
        if hasattr(self, "island"):
            self.island.live_changed()

    def alert(self, glyph, title, sub="", ttl=2.4, swatch=None, action=None, important=False):
        if self.settings.get("dnd") and not important:
            return
        self.activities["alert"] = {"kind": "alert", "glyph": glyph, "title": title, "sub": sub, "swatch": swatch,
                                    "action": action}
        self._alert_timer.start(int(ttl * 1000))
        self.on_live()

    def clear_alert(self):
        self._alert_timer.stop()
        self._set_activity("alert", None)

    def _layout(self, lang):
        if self.settings.get("live.layout", True):
            self.alert("", lang, "", ttl=1.1)

    def _caps(self, on):
        if self.settings.get("live.caps", True):
            self.alert("", "Caps Lock " + ("включён" if on else "выключен"), ttl=1.3)
        self.on_live()

    def _privacy(self, mic, cam):
        self.privacy = (mic, cam) if self.settings.get("live.privacy", True) else ([], [])
        self.on_live()

    def _fullscreen(self, fs):
        if self.settings.get("hide_fullscreen", True):
            self.island.set_fullscreen_hidden(fs)

    def _downloads(self, items):
        if not self.settings.get("live.downloads", True):
            return
        if items:
            name, size = items[0]
            more = f" +{len(items) - 1}" if len(items) > 1 else ""
            self._set_activity("download", {"kind": "download", "glyph": "", "title": name[:60] + more,
                                            "sub": services.human_bytes(size)})
        else:
            self._set_activity("download", None)

    def _download_done(self, path):
        self.notifications.push("downloads", "Загружено", os.path.basename(path),
                                action=lambda: osapi.open_path(path) if os.path.exists(path) else None)

    def _claude(self, data):
        if not self.settings.get("live.claude", True):
            return
        ev = data.get("hook_event_name") or data.get("event") or ""
        proj = os.path.basename((data.get("cwd") or "").rstrip("\\/")) or "Claude Code"
        if ev in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart"):
            tool = data.get("tool_name") or ""
            self._claude_clear.stop()
            self._set_activity("claude", {"kind": "claude", "glyph": "", "title": proj,
                                          "sub": f"работает · {tool}" if tool else "работает", "busy": True})
            self.activities.pop("claude_wait", None)
        elif ev == "Notification":
            msg = data.get("message") or "ждёт ответа"
            self.activities.pop("claude", None)
            self._set_activity("claude_wait", {"kind": "claude", "glyph": "", "title": proj, "sub": msg,
                                               "busy": False, "state_glyph": ""})
            self.notifications.push("claude", proj, msg)
        elif ev == "Stop":
            self.activities.pop("claude_wait", None)
            self._set_activity("claude", {"kind": "claude", "glyph": "", "title": proj, "sub": "готово",
                                          "busy": False, "state_glyph": ""})
            self._claude_clear.start(8000)
        elif ev in ("SessionEnd",):
            self.activities.pop("claude_wait", None)
            self._set_activity("claude", None)

    def _timer_finished(self, label):
        self.notifications.push("timer", label, "Время вышло" if self.timer.mode != "pomodoro" else "Pomodoro",
                                action=lambda: self.island.expand("timer"))

    def _reminder_fired(self, r):
        self.notifications.push("reminders", r["text"], "Напоминание",
                                action=lambda: self.island.expand("reminders"))
        if not self.island.isVisible():
            self.tray.showMessage("Напоминание", r["text"], make_icon(64), 10000)

    # ------------------------------------------------------------ actions (used by pages and the command bar)

    def hotkey(self, name):
        actions = {"command": self.open_command, "toggle": self.island.toggle,
                   "clipboard": lambda: self.island.expand("clipboard", focus=True),
                   "screenshot": self.screenshot, "picker": self.color_picker, "qr": self.scan_qr, "ocr": self.ocr,
                   "record": self.record,
                   "notes": lambda: self.island.expand("notes", focus=True),
                   "timer": lambda: self.island.expand("timer"),
                   "show": lambda: self.island.expand(), "settings": lambda: self.island.expand("settings"),
                   "calendar": lambda: self.island.expand("calendar"), "inbox": lambda: self.island.expand("inbox"),
                   "apps": lambda: self.island.expand("apps"), "printer": lambda: self.island.expand("printer"),
                   "deskdash": lambda: self.island.expand("deskdash"),
                   "quit": self.quit}
        fn = actions.get(name)
        if fn:
            fn()

    def quick_action(self, key):
        {"screenshot": self.screenshot, "fullshot": lambda: self.screenshot(full=True),
         "picker": self.color_picker, "qr": self.scan_qr, "ocr": self.ocr,
         "timer5": lambda: self.start_timer(300, "Таймер"), "pomodoro": self.timer.start_pomodoro,
         "note": lambda: self.open_page("notes", new_note=True), "command": self.open_command,
         "record": self.record,
         "lock": self.lock}[key]()

    def open_settings(self, section):
        """Open the settings page on a section: main, look, builder, notify, accounts, more."""
        from .settings_ui import SettingsPage
        keys = [k for k, _t in SettingsPage.SECTIONS]
        self.ui_state["settings_section"] = keys.index(section) if section in keys else 0
        self.ui_state["settings_scroll"] = 0
        self.island.expand("settings")
        self.island.panel.page("settings")._section(self.ui_state["settings_section"])

    def _welcome(self):
        if not self.settings.get("onboarded"):
            self.island.expand("welcome")

    def set_layout(self, **changes):
        """Layout editor changes: save now, rebuild the panel once the user pauses."""
        lay = layout.current(self.settings)
        lay.update(changes)
        self.settings.set("layout", lay)
        self._layout_timer.start()

    def apply_preset(self, key):
        lay = dict(layout.PRESETS[key]["layout"])
        lay["idle"] = layout.current(self.settings)["idle"]     # a preset doesn't change what the pill shows
        self.settings.set("layout", lay)
        self.alert("\uE7C4", "Набор «" + layout.PRESETS[key]["title"] + "»", ttl=1.6)
        self.relayout_panel()

    def reset_settings(self):
        keep = {"onboarded": True}
        self.settings.data = config._merge(config.DEFAULTS, keep)
        self.settings.save()
        QTimer.singleShot(30, self.rebuild)

    def open_command(self):
        if self.island.mode == "expanded" and self.island.panel.current.key == "command":
            self.island.collapse(restore_focus=True)
            return
        self.island.expand("command", focus=True)

    def open_page(self, key, new_note=False):
        self.island.expand(key, focus=key in ("clipboard", "reminders"))
        if new_note:
            self.island.panel.page("notes").new_note()

    def pin_app(self, name, target, kind):
        if self.apps.add(name, target, kind):
            self.alert("", "Закреплено", name, ttl=1.6, action=lambda: self.open_page("apps"))

    def copy_text(self, text):
        self.clipboard.set_text(text)
        self.alert("", "Скопировано", text[:60].replace("\n", " "))

    def paste_clip(self, item):
        """Copy the entry and paste it into the window that was active before the island."""
        self.clipboard.copy(item)
        target = self.island.prev_hwnd
        typing = self.island.input_mode
        self.island.collapse(restore_focus=True)

        def go():
            if typing and osapi.is_window(target):
                osapi.focus_window(target)
            osapi.wait_keys_released()
            osapi.paste()
        QTimer.singleShot(90 if typing else 30, go)

    def start_timer(self, secs, label="Таймер"):
        self.timer.start(secs, label)
        self.alert("", f"Таймер {features.fmt_secs(secs)}", "запущен", ttl=1.4)

    def add_reminder(self, text, when):
        self.reminders.add(text, when)
        self.alert("", text, features.fmt_when(when.timestamp()), ttl=2.5)

    def add_note(self, text):
        import uuid
        self.store["notes"].insert(0, {"id": uuid.uuid4().hex[:10], "text": text, "t": time.time()})
        self.store.touch()
        self.island.panel.page("notes").refresh()
        self.alert("", "Заметка сохранена", text[:50])

    def set_setting(self, path, value, rebuild=False):
        """rebuild=True: the setting changes how the island looks. Only the interface scale needs a new
        window; everything else is applied in place."""
        self.settings.set(path, value)
        if path.startswith("live"):
            self.power.apply_settings()
        if rebuild:
            if path == "scale":
                QTimer.singleShot(30, self.rebuild)
            else:
                self._style_timer.start()
        else:
            self.on_live()
            if path == "autohide":
                self.island.live_changed()
                self.island._last_live = None
                if self.island.mode != "expanded":
                    self.island.mode = self.island.resting_mode()
                    self.island.retarget()

    def set_autostart(self, on):
        try:
            osapi.set_autostart(on)
            self.settings.set("autostart", bool(on))
        except OSError:
            pass
        if hasattr(self, "a_auto") and self.a_auto.isChecked() != bool(on):
            self.a_auto.setChecked(bool(on))

    def set_dnd(self, on):
        self.settings.set("dnd", bool(on))
        if on:
            self.clear_alert()
        self.alert("", "Не беспокоить " + ("вкл" if on else "выкл"), important=True)

    def toggle_dnd(self):
        self.set_dnd(not self.settings.get("dnd"))

    def toggle_theme(self):
        self.set_setting("theme", "light" if self.theme.dark else "dark", rebuild=True)

    def set_appearance(self, **changes):
        """Appearance options: save now, rebuild once the user pauses (sliders fire often)."""
        from .theme import appearance
        a = appearance(self.settings)
        a.update(changes)
        self.settings.set("appearance", a)
        self._style_timer.start()

    def toggle_visible(self):
        self.a_hide.setChecked(self.island.isVisible())

    def lock(self):
        osapi.lock_workstation()

    def quit(self):
        self.qapp.quit()

    def _shutdown(self):
        self.store.flush(force=True)
        if self.hooks:
            self.hooks.stop()
        self.island.register_hotkeys({})
        self.tray.hide()
        # Skip interpreter, Qt and DLL teardown: destroying the WinRT projection after Qt crashes
        # (in the GC or in ExitProcess). Everything is saved by now, so end the process here.
        if not self.hard_exit:
            return
        import gc
        gc.disable()
        sys.stdout and sys.stdout.flush()
        osapi.terminate_self(0)

    # ------------------------------------------------------------ screen tools

    def _with_screen(self, make, on_done, delay=180):
        """Hide the island, grab the screen under the cursor, open an overlay."""
        if self.overlay is not None:
            return
        self.island.collapse()
        was_visible = self.island.isVisible()
        self.island.hide()

        def go():
            screen, pm = capture.grab_screen_under_cursor()
            if was_visible:
                self.island.show()
            ov = make(screen, pm)
            self.overlay = ov
            ov.done.connect(on_done)
            ov.destroyed.connect(lambda: setattr(self, "overlay", None))
            ov.present()
        QTimer.singleShot(delay, go)

    def screenshot(self, full=False):
        def done(res):
            kind, val = res
            if kind == "saved":
                self.alert("", "Скриншот сохранён", os.path.basename(val), ttl=4,
                           action=lambda: osapi.open_path(val))
            else:
                self.alert("", "Скриншот скопирован", f"{val.width()}×{val.height()}")
        self._with_screen(lambda s, pm: capture.ShotOverlay(s, pm, full=full), done)

    def record(self):
        """Start recording a region of the screen, or stop the recording that runs."""
        rec = self.recorder
        if rec.active:
            rec.stop()
            return
        if rec.busy:
            self.alert("\uE714", "Запись ещё сохраняется", ttl=2)
            return

        def done(res):
            screen, rect = res
            QTimer.singleShot(150, lambda: rec.start(screen, rect))    # let the overlay vanish first
        self._with_screen(lambda s, pm: capture.RecordRegionOverlay(s, pm), done)

    def _recording(self):
        rec = self.recorder
        if rec.active:
            self.activities["rec"] = {"kind": "rec", "title": "Запись", "value": recorder.fmt_clock(rec.elapsed()),
                                      "action": rec.stop}
        elif rec.busy:
            self.activities["rec"] = {"kind": "rec", "title": "Сохраняю…", "value": "", "busy": True}
        else:
            self.activities.pop("rec", None)
        self.on_live()

    def _recording_saved(self, path):
        try:
            self.island.panel.page("shelf").add([path])
        except Exception:  # noqa: BLE001 - the shelf is a convenience
            pass
        self.notifications.push("tools", "Запись сохранена", os.path.basename(path),
                                action=lambda: osapi.reveal(path), glyph="\uE714")

    def color_picker(self):
        def done(hexcol):
            self.clipboard.set_text(hexcol)
            cols = self.store["colors"]
            if hexcol in cols:
                cols.remove(hexcol)
            cols.insert(0, hexcol)
            del cols[30:]
            self.store.touch()
            self.island.panel.page("tools").refresh_colors()
            c = QColor(hexcol)
            self.alert("", hexcol, f"rgb({c.red()}, {c.green()}, {c.blue()}) · скопирован", ttl=3,
                       swatch=hexcol)
        self._with_screen(lambda s, pm: capture.PickerOverlay(s, pm), done)

    def scan_qr(self):
        def done(img):
            try:
                found = services.decode_qr(img)
            except Exception as exc:  # noqa: BLE001
                self.alert("", "Ошибка распознавания", str(exc)[:60], ttl=4)
                return
            if not found:
                self.alert("", "QR-код не найден", "выделите код точнее", ttl=3)
                return
            text = "\n".join(found)
            self.clipboard.set_text(text)
            self.island.panel.page("tools").show_result(text)
            url = found[0] if found[0].startswith(("http://", "https://")) else None
            import webbrowser
            self.alert("", found[0][:60], "скопировано" + (" · клик — открыть" if url else ""), ttl=6,
                       action=(lambda: webbrowser.open(url)) if url else (lambda: self.island.expand("tools")))
        self._with_screen(lambda s, pm: capture.RegionOverlay(s, pm, "QR: выделите код или кликните — весь экран"),
                          done)

    def ocr(self):
        def done(img):
            self.alert("", "Распознаю текст…", ttl=10)

            def ok(text):
                self.clear_alert()
                if not text.strip():
                    self.alert("", "Текст не найден", ttl=3)
                    return
                self.clipboard.set_text(text)
                self.island.panel.page("tools").show_result(text)
                self.alert("", "Текст скопирован", text[:50].replace("\n", " "), ttl=4,
                           action=lambda: self.island.expand("tools"))

            def fail(exc):
                self.alert("", "OCR недоступен", str(exc)[:60], ttl=4)
            services.ocr_png(services.png_bytes(img), ok, fail)
        self._with_screen(lambda s, pm: capture.RegionOverlay(s, pm, "Текст: выделите область"), done)

    def show_qr_for(self, text):
        from .pages import make_qr
        img = make_qr(text)
        self.island.expand("tools")
        self.island.panel.page("tools").show_result(text, img)
        QApplication.clipboard().setImage(img)
        self.alert("", "QR-код скопирован как картинка", text[:40])


def _app_name(path):
    base = os.path.basename(path.replace("\\", "/"))
    if "_" in base and "." not in base:  # packaged app id like Microsoft.WindowsCamera_8wekyb3d8bbwe
        base = base.split("_")[0].split(".")[-1]
    return base.replace(".exe", "")


def single_instance(message=b"show"):
    """Returns a QLocalServer for the first instance, or None after passing `message` to the running one."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        sock.write(message)
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)
    return server


def _serve(server, app):
    def on_conn():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(300)
        msg = bytes(conn.readAll()).decode("utf-8", "replace").strip()
        conn.disconnectFromServer()
        app.hotkey(msg[7:] if msg.startswith("action:") else "show")
    server.newConnection.connect(on_conn)


def main():
    import faulthandler
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _crash_log = open(os.path.join(config.DATA_DIR, "crash.log"), "w", encoding="utf-8")
    faulthandler.enable(_crash_log)
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        # Wayland doesn't let clients place or raise their windows; XWayland does
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    action = sys.argv[sys.argv.index("--action") + 1] if "--action" in sys.argv[:-1] else None
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Island")
    osapi.prepare_app(qapp)
    server = single_instance(f"action:{action}".encode() if action else b"show")
    if server is None:
        return 0
    app = App(qapp, sys.argv[1:])
    _serve(server, app)
    if action:
        QTimer.singleShot(800, lambda: app.hotkey(action))
    qapp._island = app
    if "--demo" in sys.argv:
        from . import selftest
        selftest.run(app, sys.argv)
    return qapp.exec()  # _shutdown ends the process on quit
