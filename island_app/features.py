"""Stateful features: timer/pomodoro/stopwatch, reminders, clipboard history, app & file index, Claude chat."""
import datetime as dt
import hashlib
import os
import subprocess
import threading
import time
import uuid

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage

from . import bridge, config, osapi

# ---------------------------------------------------------------- timer


def fmt_secs(s, force_hours=False):
    s = max(0, int(round(s)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h or force_hours else f"{m:02d}:{sec:02d}"


class TimerService(QObject):
    """One clock with three modes: countdown, pomodoro cycles and stopwatch."""
    changed = Signal()
    finished = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.mode = "timer"          # timer | pomodoro | stopwatch
        self.total = 0.0
        self.remaining = 0.0
        self.elapsed = 0.0
        self.running = False
        self.label = ""
        self.phase = "work"          # pomodoro: work | short | long
        self.cycle = 0
        self._last = 0.0
        self._tick = QTimer(self, interval=200, timeout=self._on_tick)

    @property
    def active(self):
        return self.running or (self.mode != "stopwatch" and 0 < self.remaining < self.total) or \
            (self.mode == "stopwatch" and self.elapsed > 0)

    def display(self):
        return fmt_secs(self.elapsed if self.mode == "stopwatch" else self.remaining)

    def progress(self):
        if self.mode == "stopwatch" or not self.total:
            return 0.0
        return 1.0 - self.remaining / self.total

    def start(self, seconds=None, label="", mode="timer"):
        self.mode = mode
        if mode == "stopwatch":
            self.elapsed = 0.0
        else:
            self.total = self.remaining = float(seconds)
        self.label = label
        self.resume()

    def start_pomodoro(self):
        self.cycle = 0
        self.phase = "work"
        self.start(self.settings.get("pomodoro.work", 25) * 60, "Фокус", "pomodoro")

    def resume(self):
        if self.mode != "stopwatch" and self.remaining <= 0:
            return
        self.running = True
        self._last = time.monotonic()
        self._tick.start()
        self.changed.emit()

    def pause(self):
        self.running = False
        self._tick.stop()
        self.changed.emit()

    def toggle(self):
        self.pause() if self.running else self.resume()

    def reset(self):
        self.running = False
        self._tick.stop()
        self.remaining = self.total = self.elapsed = 0.0
        self.label = ""
        self.changed.emit()

    def add(self, seconds):
        if self.mode == "stopwatch":
            return
        self.remaining += seconds
        self.total = max(self.total + seconds, self.remaining)
        if not self.running:
            self.resume()
        self.changed.emit()

    def _on_tick(self):
        now = time.monotonic()
        d = now - self._last
        self._last = now
        if self.mode == "stopwatch":
            self.elapsed += d
            self.changed.emit()
            return
        self.remaining -= d
        if self.remaining <= 0:
            self.remaining = 0
            self.running = False
            self._tick.stop()
            if self.mode == "pomodoro":
                self._next_phase()
            else:
                self.finished.emit(self.label or "Таймер завершён")
        self.changed.emit()

    def _next_phase(self):
        p = self.settings.get("pomodoro", {})
        if self.phase == "work":
            self.cycle += 1
            long_break = self.cycle % int(p.get("cycles", 4)) == 0
            self.phase = "long" if long_break else "short"
            self.finished.emit("Перерыв" if not long_break else "Длинный перерыв")
            self.total = self.remaining = float(p.get("long" if long_break else "short", 5)) * 60
            self.label = "Перерыв"
        else:
            self.phase = "work"
            self.finished.emit("Снова фокус")
            self.total = self.remaining = float(p.get("work", 25)) * 60
            self.label = "Фокус"
        self.resume()


# ---------------------------------------------------------------- reminders


class Reminders(QObject):
    changed = Signal()
    fired = Signal(object)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self._timer = QTimer(self, interval=1000, timeout=self._check)
        self._timer.start()

    @property
    def items(self):
        return self.store["reminders"]

    def add(self, text, when):
        item = {"id": uuid.uuid4().hex[:10], "text": text, "at": when.timestamp(), "done": False}
        self.items.append(item)
        self.items.sort(key=lambda r: r["at"])
        self.store.touch()
        self.changed.emit()
        return item

    def remove(self, rid):
        self.store.data["reminders"] = [r for r in self.items if r["id"] != rid]
        self.store.touch()
        self.changed.emit()

    def clear_done(self):
        self.store.data["reminders"] = [r for r in self.items if not r["done"]]
        self.store.touch()
        self.changed.emit()

    def upcoming(self):
        return [r for r in self.items if not r["done"]]

    def _check(self):
        now = time.time()
        for r in self.items:
            if not r["done"] and r["at"] <= now:
                r["done"] = True
                self.store.touch()
                self.fired.emit(r)
                self.changed.emit()


def fmt_when(ts):
    when = dt.datetime.fromtimestamp(ts)
    today = dt.date.today()
    if when.date() == today:
        day = "сегодня"
    elif when.date() == today + dt.timedelta(days=1):
        day = "завтра"
    elif when.date() == today - dt.timedelta(days=1):
        day = "вчера"
    else:
        day = when.strftime("%d.%m")
    return f"{day} в {when:%H:%M}"


# ---------------------------------------------------------------- clipboard history

_IGNORE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore",
                   "CanIncludeInClipboardHistory")


class ClipboardHistory(QObject):
    changed = Signal()

    def __init__(self, settings, store):
        super().__init__()
        self.settings = settings
        self.store = store
        self.cb = QGuiApplication.clipboard()
        self._ignore_next = False
        os.makedirs(config.CLIP_DIR, exist_ok=True)
        self.cb.dataChanged.connect(self._on_change)

    @property
    def items(self):
        return self.store["clips"]

    def _on_change(self):
        if self._ignore_next:
            self._ignore_next = False
            return
        md = self.cb.mimeData()
        if md is None:
            return
        fmts = " ".join(md.formats())
        if any(f in fmts for f in _IGNORE_FORMATS[:2]):
            return  # password managers mark secrets this way
        if md.hasImage() and not md.hasText():
            if not self.settings.get("clipboard.save_images", True):
                return
            img = self.cb.image()
            if img.isNull():
                return
            raw = img.constBits().tobytes() if hasattr(img.constBits(), "tobytes") else bytes(img.constBits())
            digest = hashlib.sha1(raw[:2_000_000]).hexdigest()[:16]
            path = os.path.join(config.CLIP_DIR, digest + ".png")
            if not os.path.exists(path):
                img.save(path, "PNG")
            self._push({"kind": "image", "path": path, "w": img.width(), "h": img.height(), "key": digest})
        elif md.hasUrls() and all(u.isLocalFile() for u in md.urls()):
            files = [u.toLocalFile() for u in md.urls()]
            self._push({"kind": "files", "files": files, "text": "\n".join(files), "key": "\n".join(files)})
        elif md.hasText():
            text = md.text()
            if not text.strip():
                return
            self._push({"kind": "text", "text": text[:100_000], "key": text[:100_000]})

    def _push(self, item):
        existing = next((c for c in self.items if c.get("key") == item["key"]), None)
        if existing:
            self.items.remove(existing)
            item["pinned"] = existing.get("pinned", False)
        item.setdefault("pinned", False)
        item["id"] = existing["id"] if existing else uuid.uuid4().hex[:10]
        item["t"] = time.time()
        self.items.insert(0, item)
        limit = int(self.settings.get("clipboard.max_items", 200))
        unpinned = [c for c in self.items if not c.get("pinned")]
        for extra in unpinned[limit:]:
            self.items.remove(extra)
            self._drop_file(extra)
        self.store.touch()
        self.changed.emit()

    def _drop_file(self, item):
        if item.get("kind") == "image" and not any(c is not item and c.get("path") == item.get("path")
                                                    for c in self.items):
            try:
                os.remove(item["path"])
            except OSError:
                pass

    def copy(self, item):
        """Put an entry back on the clipboard without duplicating it in the history."""
        self._ignore_next = True
        if item["kind"] == "image":
            self.cb.setImage(QImage(item["path"]))
        elif item["kind"] == "files":
            from PySide6.QtCore import QUrl
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(f) for f in item["files"]])
            md.setText(item["text"])
            self.cb.setMimeData(md)
        else:
            self.cb.setText(item["text"])
        self.items.remove(item)
        item["t"] = time.time()
        self.items.insert(0, item)
        self.store.touch()
        self.changed.emit()

    def set_text(self, text, remember=True):
        if not remember:
            self._ignore_next = True
        self.cb.setText(text)

    def pin(self, item):
        item["pinned"] = not item.get("pinned")
        self.store.touch()
        self.changed.emit()

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            self._drop_file(item)
            self.store.touch()
            self.changed.emit()

    def clear(self):
        for c in [c for c in self.items if not c.get("pinned")]:
            self.items.remove(c)
            self._drop_file(c)
        self.store.touch()
        self.changed.emit()

    def search(self, query):
        q = query.lower().strip()
        items = sorted(self.items, key=lambda c: (not c.get("pinned"), -c.get("t", 0)))
        if not q:
            return items
        return [c for c in items if q in (c.get("text") or "").lower() or (q in "картинка image" and c["kind"] == "image")]


# ---------------------------------------------------------------- app and file index for the command bar


class Index(QObject):
    ready = Signal()

    def __init__(self):
        super().__init__()
        self.apps = []   # (name, target, kind)  kind: lnk | appid
        self.files = []  # (name_lower, path)
        self._lock = threading.Lock()
        QTimer.singleShot(800, self.rebuild)
        self._timer = QTimer(self, interval=10 * 60 * 1000, timeout=self.rebuild)
        self._timer.start()

    def rebuild(self):
        bridge.run_thread(self._build, lambda _r: self.ready.emit())

    def _build(self):
        apps = _windows_apps() if osapi.IS_WINDOWS else _linux_apps()
        files = []
        home = os.path.expanduser("~")
        bases = [osapi.user_dir(k) for k in ("desktop", "documents", "download", "pictures", "music", "videos")]
        bases += [os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "Documents")]
        for base in dict.fromkeys(bases):
            if not os.path.isdir(base) or os.path.normpath(base) == os.path.normpath(home):
                continue
            base_depth = base.count(os.sep)
            for dirpath, dirs, fnames in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__",
                                                                                   "build", ".git", "venv", ".venv")]
                if dirpath.count(os.sep) - base_depth >= 4:
                    dirs[:] = []
                for d in dirs:
                    files.append((d.lower(), os.path.join(dirpath, d)))
                for f in fnames:
                    files.append((f.lower(), os.path.join(dirpath, f)))
                if len(files) > 60000:
                    break
        with self._lock:
            self.apps = sorted(apps.values(), key=lambda a: a[0].lower())
            self.files = files

    def search_apps(self, q, limit=6):
        q = q.lower().strip()
        if not q:
            return []
        alias = next((en for ru, en in ALIASES.items() if len(q) >= 3 and ru.startswith(q)), None)
        scored = []
        with self._lock:
            for name, target, kind in self.apps:
                s = max(fuzzy(q, name.lower()), fuzzy(alias, name.lower()) - 1 if alias else 0)
                if s:
                    scored.append((s, name, target, kind))
        scored.sort(key=lambda x: (-x[0], len(x[1])))
        return scored[:limit]

    def search_files(self, q, limit=6):
        q = q.lower().strip()
        if len(q) < 2:
            return []
        out = []
        with self._lock:
            for name, path in self.files:
                if q in name:
                    out.append((name.startswith(q), -len(name), path))
        out.sort(reverse=True)
        return [p for *_x, p in out[:limit]]


def _windows_apps():
    apps = {}
    roots = [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                name, ext = os.path.splitext(f)
                if ext.lower() in (".lnk", ".url", ".appref-ms") and "uninstall" not in name.lower() \
                        and "удал" not in name.lower():
                    apps.setdefault(name.lower(), (name, os.path.join(dirpath, f), "lnk"))
    try:  # Store / UWP apps (Settings, Calculator, ...)
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                              "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }"],
                             capture_output=True, timeout=20, creationflags=0x08000000)
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            if "\t" in line:
                name, appid = line.rsplit("\t", 1)
                name = name.strip()
                if name and name.lower() not in apps and "uninstall" not in name.lower():
                    apps[name.lower()] = (name, appid.strip(), "appid")
    except (OSError, subprocess.SubprocessError):
        pass
    return apps


def _linux_apps():
    """Visible .desktop entries from the XDG data dirs (system, user, Flatpak, Snap)."""
    import locale
    lang = (locale.getlocale()[0] or os.environ.get("LANG", "")).split("_")[0]
    dirs = [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share"),
             "/var/lib/snapd/desktop"]
    apps = {}
    for d in dirs:
        appdir = os.path.join(d, "applications")
        if not os.path.isdir(appdir):
            continue
        for dirpath, _dirs, files in os.walk(appdir):
            for f in files:
                if f.endswith(".desktop"):
                    entry = _parse_desktop(os.path.join(dirpath, f), lang)
                    if entry:
                        apps.setdefault(entry[0].lower(), entry)
    return apps


def _parse_desktop(path, lang):
    fields = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            section = None
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    section = line
                elif section == "[Desktop Entry]" and "=" in line:
                    k, v = line.split("=", 1)
                    fields.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    if fields.get("Type") != "Application" or fields.get("NoDisplay") == "true" or fields.get("Hidden") == "true":
        return None
    name = fields.get(f"Name[{lang}]") or fields.get("Name")
    return (name, path, "desktop") if name and fields.get("Exec") else None


# Russian names for apps that are English on an English Windows (and the other way round)
ALIASES = {"блокнот": "notepad", "калькулятор": "calculator", "проводник": "file explorer",
           "параметры": "settings", "настройки windows": "settings", "терминал": "terminal",
           "диспетчер задач": "task manager", "командная строка": "command prompt", "ножницы": "snipping tool",
           "фотографии": "photos", "почта": "outlook", "календарь": "calendar", "магазин": "microsoft store",
           "панель управления": "control panel", "часы": "clock", "камера": "camera", "браузер": "edge"}


def fuzzy(q, name):
    """Cheap launcher-style score: prefix > word prefix > substring > initials > subsequence."""
    if name.startswith(q):
        return 100 - len(name) * 0.1
    words = name.replace("-", " ").replace("_", " ").split()
    if any(w.startswith(q) for w in words):
        return 80 - len(name) * 0.1
    if q in name:
        return 60 - len(name) * 0.1
    if len(q) >= 2 and "".join(w[0] for w in words if w).startswith(q):
        return 50
    it = iter(name)
    if len(q) >= 3 and all(c in it for c in q):
        return 20 - len(name) * 0.1
    return 0


def launch_app(target, kind):
    if kind == "appid":
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target], creationflags=0x08000000)
    elif kind == "desktop":
        name = os.path.splitext(os.path.basename(target))[0]
        if not (osapi.have("gtk-launch") and osapi.spawn(["gtk-launch", name])):
            fields = {}
            with open(target, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "=" in line and not line.startswith("["):
                        k, v = line.strip().split("=", 1)
                        fields.setdefault(k, v)
            import re
            import shlex
            cmd = re.sub(r"%[a-zA-Z]", "", fields.get("Exec", "")).strip()
            if cmd:
                osapi.spawn(shlex.split(cmd))
    else:
        osapi.open_path(target)


# ---------------------------------------------------------------- Claude chat (Anthropic SDK)


class AIChat(QObject):
    """Streams Claude replies. The key comes from ANTHROPIC_API_KEY or the settings (DPAPI-encrypted)."""
    delta = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    SYSTEM = ("Ты встроенный ассистент Windows-утилиты Island. Отвечай кратко и по делу, на языке вопроса. "
              "Используй Markdown только когда он помогает.")

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.history = []
        self.busy = False

    def api_key(self):
        return os.environ.get("ANTHROPIC_API_KEY") or config.unprotect(self.settings.get("ai.api_key", ""))

    def ask(self, text, system=None, keep=True):
        key = self.api_key()
        if not key:
            self.failed.emit("Нет ключа API. Добавьте его в Настройках → ИИ или в переменную ANTHROPIC_API_KEY.")
            return
        if self.busy:
            return
        self.busy = True
        messages = (self.history if keep else []) + [{"role": "user", "content": text}]
        model = self.settings.get("ai.model", "claude-sonnet-5-5")
        threading.Thread(target=self._run, args=(key, model, system or self.SYSTEM, messages, keep, text),
                         daemon=True).start()

    def translate(self, text):
        lang = self.settings.get("ai.translate_to", "ru")
        system = (f"Ты переводчик. Переведи текст пользователя на язык '{lang}' "
                  f"(если текст уже на нём — на английский). Верни только перевод.")
        self.ask(text, system=system, keep=False)

    def _run(self, key, model, system, messages, keep, text):
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        out = []
        try:
            with client.beta.messages.stream(
                model=model,
                max_tokens=8000,
                system=system,
                messages=messages,
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default", "output_config": {"effort": "low"}},
            ) as stream:
                for chunk in stream.text_stream:
                    out.append(chunk)
                    bridge.on_main(self.delta.emit, chunk)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                bridge.on_main(self._fail, "Claude отказался отвечать на этот запрос.")
                return
            reply = "".join(out)
            if keep:
                self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            bridge.on_main(self._ok, reply)
        except anthropic.AuthenticationError:
            bridge.on_main(self._fail, "Неверный ключ API.")
        except anthropic.RateLimitError:
            bridge.on_main(self._fail, "Слишком много запросов, попробуйте через минуту.")
        except anthropic.APIStatusError as e:
            bridge.on_main(self._fail, f"Ошибка API {e.status_code}: {getattr(e, 'message', e)}")
        except anthropic.APIConnectionError:
            bridge.on_main(self._fail, "Нет соединения с api.anthropic.com.")

    def _ok(self, reply):
        self.busy = False
        self.done.emit(reply)

    def _fail(self, msg):
        self.busy = False
        self.failed.emit(msg)

    def reset(self):
        self.history = []
