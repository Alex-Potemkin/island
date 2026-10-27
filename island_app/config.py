"""Settings (settings.json, hand-editable, live-reloaded) and app data (data.json)."""
import base64
import copy
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

from . import layout, osapi
from .theme import APPEARANCE

APP_NAME = "Island"
APP_VERSION = "1.0.0"

# %APPDATA%/Island is also used by another app called Island (an Electron app), so Windows data lives in
# its own folder; files from older versions are moved over once (see migrate_old_data).
DATA_DIR = os.environ.get("ISLAND_HOME") or osapi.data_dir("IslandDesk" if osapi.IS_WINDOWS else APP_NAME)
OLD_DATA_DIR = None if os.environ.get("ISLAND_HOME") or not osapi.IS_WINDOWS else osapi.data_dir(APP_NAME)
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
DATA_PATH = os.path.join(DATA_DIR, "data.json")
CLIP_DIR = os.path.join(DATA_DIR, "clips")
SHOTS_DIR = os.path.join(osapi.user_dir("pictures"), "Island")

DEFAULTS = {
    "onboarded": False,         # the welcome screen was completed
    "performance": "auto",      # auto | eco | normal (economy mode for weak PCs, see perf.py)
    "layout": dict(layout.DEFAULT),   # panel tabs, home cards, quick buttons, resting pill (see layout.py)
    "theme": "dark",            # dark | graphite | midnight | light | fog | sand | system
    "appearance": dict(APPEARANCE),   # accent, corners, opacity, shadow, animation, pill, position, font
    "scale": 100,               # 80..200 %
    "autohide": False,          # idle pill shrinks to a thin line at the top edge
    "hide_fullscreen": True,    # hide while a fullscreen app (game, video) is in front
    "autostart": False,
    "screen": 0,                # 0 = primary, 1.. = other monitors
    "top_offset": 6,
    "expand_on_hover": True,
    "hover_delay_ms": 140,
    "collapse_delay_ms": 450,
    "dnd": False,               # do not disturb: no transient alerts
    "live": {"music": True, "timer": True, "layout": True, "caps": True,
             "privacy": True, "downloads": True, "claude": True, "print": True},
    "clipboard": {"max_items": 200, "save_images": True},
    "hotkeys": {
        "command": "alt+space",
        "toggle": "ctrl+alt+i",
        "clipboard": "ctrl+alt+v",
        "screenshot": "ctrl+alt+s",
        "picker": "ctrl+alt+c",
        "qr": "ctrl+alt+q",
        "ocr": "ctrl+alt+o",
        "notes": "ctrl+alt+n",
        "timer": "ctrl+alt+t",
        "ai": "ctrl+alt+a",
    },
    "ai": {"model": "claude-sonnet-5-5", "api_key": "", "translate_to": "ru"},
    "weather": {"enabled": True, "city": ""},
    "hooks": {"enabled": True, "port": 47811},
    "notifications": {"style": "card", "duration": 6, "sound": "drop", "volume": 70, "sound_file": "",
                      "event_lead": 10, "quiet": {"enabled": False, "from": 22, "to": 8}, "sources": {}, "sounds": {},
                      "apps": True, "muted_apps": []},
    "accounts": {"google": {"client_id": "", "client_secret": "", "refresh": "", "email": ""},
                 "github": {"token": ""}, "ics": [],
                 "bambu": {"host": "", "serial": "", "code": "", "name": ""}},
    "deskdash": {"enabled": True, "spotify": True},   # macros and Spotify Connect through the DeskDash agent
    "pomodoro": {"work": 25, "short": 5, "long": 15, "cycles": 4},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    if not osapi.IS_WINDOWS:
        os.chmod(tmp, 0o600)  # may hold the API key in plain text on Linux
    for _ in range(20):  # the file watcher / an editor may hold the target for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class Settings:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self.last_text = ""
        self.load()

    def load(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                text = f.read()
            self.last_text = text
            self.data = _merge(DEFAULTS, json.loads(text))
            return True
        except FileNotFoundError:
            self.save()
            return True
        except (OSError, ValueError):
            return False  # keep the previous values while the user is mid-edit

    def save(self):
        _atomic_write(SETTINGS_PATH, self.data)
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                self.last_text = f.read()
        except OSError:
            pass

    def changed_on_disk(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return f.read() != self.last_text
        except OSError:
            return False

    def get(self, path, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path, value, save=True):
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if save:
            self.save()

    @property
    def scale(self):
        try:
            return max(0.6, min(2.0, float(self.data.get("scale", 100)) / 100.0))
        except (TypeError, ValueError):
            return 1.0


def migrate_old_data():
    """Move settings/data written by older versions from %APPDATA%/Island into DATA_DIR (once)."""
    if not OLD_DATA_DIR or os.path.exists(SETTINGS_PATH):
        return False
    old_settings = os.path.join(OLD_DATA_DIR, "settings.json")
    try:
        with open(old_settings, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict) or not ("layout" in old or "hotkeys" in old):
        return False     # not ours
    import shutil
    os.makedirs(DATA_DIR, exist_ok=True)
    for name in ("settings.json", "data.json", "clips", "sounds"):
        src = os.path.join(OLD_DATA_DIR, name)
        if os.path.exists(src):
            shutil.move(src, os.path.join(DATA_DIR, name))
    return True


LISTS = ("notes", "reminders", "clips", "shelf", "colors", "notifications", "events", "apps")


def _clean_notes(notes):
    """Fix types and drop junk: notes with no text at all, or a stray character or two with no letters."""
    out = []
    for n in notes if isinstance(notes, list) else []:
        if not isinstance(n, dict):
            continue
        text = n.get("text")
        text = text if isinstance(text, str) else ""
        stripped = text.strip()
        if not stripped or (len(stripped) <= 2 and not any(ch.isalnum() for ch in stripped)):
            continue
        try:
            t = float(n.get("t") or 0)
        except (TypeError, ValueError):
            t = 0.0
        out.append({"id": str(n.get("id") or os.urandom(5).hex()), "text": text, "t": t or time.time()})
    return out


class Store:
    """Notes, reminders, clipboard history, shelf and colour history. Saved with a debounce."""

    def __init__(self):
        self.data = {k: [] for k in LISTS}
        self._lock = threading.Lock()
        self._dirty_at = 0.0
        self.cleaned = 0
        try:
            with open(DATA_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass
        for k in LISTS:  # a hand-edited or damaged file must not break a whole tab
            if not isinstance(self.data.get(k), list):
                self.data[k] = []
        self.data["apps"] = [a for a in self.data["apps"] if isinstance(a, dict) and isinstance(a.get("target"), str)
                             and a["target"] and isinstance(a.get("id"), str)]
        for a in self.data["apps"]:
            a["name"] = a["name"] if isinstance(a.get("name"), str) and a["name"] else a["target"]
            a["kind"] = a["kind"] if a.get("kind") in ("lnk", "appid", "desktop") else "lnk"
        notes = _clean_notes(self.data["notes"])
        self.cleaned = len(self.data["notes"]) - len(notes)
        if notes != self.data["notes"]:
            if os.path.exists(DATA_PATH):
                import shutil
                shutil.copy2(DATA_PATH, DATA_PATH + ".bak")   # keep what was there before the clean-up
            self.data["notes"] = notes
            self.touch()

    def __getitem__(self, key):
        return self.data[key]

    def touch(self):
        self._dirty_at = time.time()

    def flush(self, force=False):
        if not self._dirty_at:
            return
        if not force and time.time() - self._dirty_at < 1.0:
            return
        with self._lock:
            self._dirty_at = 0.0
            _atomic_write(DATA_PATH, self.data)


# --- DPAPI: the API key typed into the UI is stored encrypted for the current Windows user ---

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text):
    if not text or sys.platform != "win32":
        return ""
    src, _keep = _blob(text.encode("utf-8"))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Island", None, None, None, 0,
                                                  ctypes.byref(out)):
        return ""
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return "dpapi:" + base64.b64encode(data).decode()


def unprotect(value):
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        return value  # plain key typed straight into settings.json
    try:
        src, _keep = _blob(base64.b64decode(value[6:]))
        out = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                                        ctypes.byref(out)):
            return ""
        data = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return data.decode("utf-8")
    except Exception:
        return ""
