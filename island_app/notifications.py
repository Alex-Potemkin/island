"""One place for everything that wants the user's attention.

Each source (calendar, GitHub, reminders, …) has a style the user picks in settings:
    card  — the pill grows into a card with title and text for a few seconds
    pill  — a one-line message inside the pill
    off   — only kept in the notification history
plus a sound switch, quiet hours and a global duration. History holds the last 100 items.
"""
import time
import uuid
import webbrowser

from PySide6.QtCore import QObject, Signal

from . import osapi

SOURCES = {  # key: (glyph, title)
    "calendar": ("", "Календарь"),
    "github": ("", "GitHub"),
    "reminders": ("", "Напоминания"),
    "timer": ("", "Таймер"),
    "downloads": ("", "Загрузки"),
    "claude": ("", "Claude Code"),
    "scripts": ("", "Скрипты и вебхуки"),
}
STYLES = {"card": "Карточка", "pill": "В пилюле", "off": "Выкл"}
DEFAULTS = {"style": "card", "duration": 6, "sound": True, "event_lead": 10,
            "quiet": {"enabled": False, "from": 22, "to": 8}, "sources": {}}
IMPORTANT = {"reminders", "timer"}   # still shown during quiet hours / do-not-disturb


class Notifications(QObject):
    changed = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app

    @property
    def cfg(self):
        c = dict(DEFAULTS)
        c.update(self.app.settings.get("notifications") or {})
        return c

    @property
    def history(self):
        return self.app.store.data.setdefault("notifications", [])

    def style_for(self, source):
        c = self.cfg
        style = (c.get("sources") or {}).get(source, "default")
        style = c["style"] if style == "default" else style
        return style if style in STYLES else "card"

    def quiet_now(self):
        q = self.cfg.get("quiet") or {}
        if not q.get("enabled"):
            return False
        h = time.localtime().tm_hour
        a, b = int(q.get("from", 22)), int(q.get("to", 8))
        return (a <= h < b) if a < b else (h >= a or h < b)

    def push(self, source, title, body="", url="", action=None, glyph=None):
        """Show (or just record) a notification. url opens on click; action is a callable for this session."""
        glyph = glyph or SOURCES.get(source, ("", ""))[0]
        n = {"id": uuid.uuid4().hex[:10], "source": source, "title": title, "body": body, "url": url,
             "glyph": glyph, "t": time.time()}
        self.history.insert(0, n)
        del self.history[100:]
        self.app.store.touch()
        self.changed.emit()
        style = self.style_for(source)
        silenced = (self.quiet_now() or self.app.settings.get("dnd")) and source not in IMPORTANT
        if style == "off" or silenced:
            return n
        n = dict(n, action=action)
        if style == "card":
            self.app.island.show_toast(n)
        else:
            self.app.alert(glyph, title, body, ttl=float(self.cfg["duration"]), action=lambda: self.open(n),
                           important=True)
        if self.cfg.get("sound", True):
            osapi.beep()
        return n

    def open(self, n):
        if n.get("action"):
            n["action"]()
        elif n.get("url"):
            webbrowser.open(n["url"])
        elif n.get("source") == "calendar":
            self.app.open_page("calendar")

    def clear(self):
        self.history.clear()
        self.app.store.touch()
        self.changed.emit()
