"""Command bar: natural-language parsing (RU/EN) and result providers."""
import ast
import datetime as dt
import math
import operator
import os
import re
import urllib.parse
import webbrowser

from . import features

# ---------------------------------------------------------------- durations

_UNIT_SECONDS = [
    (r"(?:ч|час|часа|часов|h|hr|hrs|hour|hours)", 3600),
    (r"(?:м|мин|минута|минуты|минут|минуту|m|min|mins|minute|minutes)", 60),
    (r"(?:с|сек|секунда|секунды|секунд|секунду|s|sec|secs|second|seconds)", 1),
]


def parse_duration(text):
    """'10 мин', '1ч 30м', '90с', '1:30', '25' (minutes) -> seconds or None."""
    t = text.strip().lower().replace(",", ".")
    if not t:
        return None
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", t)
    if m:
        a, b, c = m.groups()
        return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t) * 60
    total, pos = 0.0, 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-zа-яё]+)", t):
        unit = m.group(2)
        for pat, mult in _UNIT_SECONDS:
            if re.fullmatch(pat, unit):
                total += float(m.group(1)) * mult
                break
        else:
            return None
        pos = m.end()
    if t[pos:].strip() or not total:
        return None
    return total


# ---------------------------------------------------------------- reminder time phrases

_WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3, "пятницу": 4, "пятница": 4,
             "субботу": 5, "суббота": 5, "воскресенье": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
             "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def parse_reminder(text, now=None):
    """'купить хлеб завтра в 9' -> (text, datetime, explicit_time). Unknown time -> +1 hour."""
    now = now or dt.datetime.now()
    s = " " + text.strip() + " "
    day = None
    hour = minute = None
    delta = None

    def cut(pattern):
        nonlocal s
        m = re.search(pattern, s, flags=re.I)
        if m:
            s = s[:m.start()] + " " + s[m.end():]
        return m

    m = cut(r"\s(?:через|in)\s+(\d+(?:[.,]\d+)?)?\s*(час(?:а|ов)?|ч|минут[уы]?|мин|м|секунд[уы]?|сек|дн(?:я|ей)|день|"
            r"hours?|h|minutes?|mins?|m|seconds?|secs?|days?|d)\b")
    if m:
        n = float((m.group(1) or "1").replace(",", "."))
        unit = m.group(2).lower()
        if unit.startswith(("ч", "h")):
            delta = dt.timedelta(hours=n)
        elif unit.startswith(("д", "d")):
            delta = dt.timedelta(days=n)
        elif unit.startswith(("с", "s")):
            delta = dt.timedelta(seconds=n)
        else:
            delta = dt.timedelta(minutes=n)
    if cut(r"\s(?:послезавтра|day after tomorrow)\s"):
        day = now.date() + dt.timedelta(days=2)
    elif cut(r"\s(?:завтра|tomorrow)\s"):
        day = now.date() + dt.timedelta(days=1)
    elif cut(r"\s(?:сегодня|today|tonight)\s"):
        day = now.date()
    m = cut(r"\s(?:в|во|on)?\s*(" + "|".join(_WEEKDAYS) + r")\s")
    if m:
        wd = _WEEKDAYS[m.group(1).lower()]
        ahead = (wd - now.weekday()) % 7 or 7
        day = now.date() + dt.timedelta(days=ahead)
    m = cut(r"\s(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s")
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (int(y) + (2000 if len(y) == 2 else 0)) if y else now.year
        try:
            day = dt.date(year, mo, d)
            if not y and day < now.date():
                day = dt.date(year + 1, mo, d)
        except ValueError:
            pass
    m = cut(r"\s(?:в|во|at|@)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи|am|pm)?(?=\s)")
    if not m:
        m = cut(r"\s(\d{1,2}):(\d{2})\s*(утра|дня|вечера|ночи|am|pm)?(?=\s)")
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        suffix = (m.group(3) or "").lower()
        if suffix in ("дня", "вечера", "pm") and hour < 12:
            hour += 12
        if suffix in ("ночи", "am") and hour == 12:
            hour = 0
    s = re.sub(r"^\s*(?:мне|me|to|что|that|о том,? что|про|about)\s+", " ", s, flags=re.I)
    body = re.sub(r"\s+", " ", s).strip(" ,.-") or "Напоминание"

    if delta is not None:
        return body, now + delta, True
    if hour is not None and 0 <= hour < 24 and 0 <= minute < 60:
        when = dt.datetime.combine(day or now.date(), dt.time(hour, minute))
        if day is None and when <= now:
            when += dt.timedelta(days=1)
        return body, when, True
    if day is not None:
        return body, dt.datetime.combine(day, dt.time(9, 0)), True
    return body, now + dt.timedelta(hours=1), False


# ---------------------------------------------------------------- calculator

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10",
                                        "log2", "exp", "floor", "ceil", "factorial", "radians", "degrees")}
_FUNCS.update(abs=abs, round=round, min=min, max=max)
_CONST = {"pi": math.pi, "e": math.e, "tau": math.tau, "пи": math.pi}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _CONST:
        return _CONST[node.id]
    raise ValueError("unsupported")


def calculate(text):
    t = text.strip().rstrip("=").strip()
    if not t or not re.search(r"\d|pi|пи", t) or not re.search(r"[-+*/^%()]|sqrt|sin|cos|log|pi|пи", t):
        return None
    t = t.replace("^", "**").replace("×", "*").replace("÷", "/").replace(",", ".")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*(?:от|of)\s*(\d+(?:\.\d+)?)", r"(\1/100*\2)", t)
    try:
        v = _eval(ast.parse(t, mode="eval"))
    except Exception:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        v = round(v, 10)
        if v.is_integer() and abs(v) < 1e15:
            v = int(v)
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else str(v)


# ---------------------------------------------------------------- unit converter

_UNITS = {
    # length (m)
    "мм": ("len", 0.001), "mm": ("len", 0.001), "см": ("len", 0.01), "cm": ("len", 0.01), "м": ("len", 1),
    "m": ("len", 1), "км": ("len", 1000), "km": ("len", 1000), "дюйм": ("len", 0.0254), "in": ("len", 0.0254),
    "inch": ("len", 0.0254), "фут": ("len", 0.3048), "ft": ("len", 0.3048), "ярд": ("len", 0.9144),
    "yd": ("len", 0.9144), "миля": ("len", 1609.344), "мили": ("len", 1609.344), "mi": ("len", 1609.344),
    "mile": ("len", 1609.344), "miles": ("len", 1609.344), "nmi": ("len", 1852),
    # mass (kg)
    "мг": ("mass", 1e-6), "mg": ("mass", 1e-6), "г": ("mass", 0.001), "g": ("mass", 0.001), "кг": ("mass", 1),
    "kg": ("mass", 1), "т": ("mass", 1000), "t": ("mass", 1000), "lb": ("mass", 0.45359237),
    "lbs": ("mass", 0.45359237), "фунт": ("mass", 0.45359237), "oz": ("mass", 0.028349523), "унция": ("mass", 0.028349523),
    # volume (l)
    "мл": ("vol", 0.001), "ml": ("vol", 0.001), "л": ("vol", 1), "l": ("vol", 1), "gal": ("vol", 3.785411784),
    "галлон": ("vol", 3.785411784), "floz": ("vol", 0.0295735), "м3": ("vol", 1000), "m3": ("vol", 1000),
    # area (m2)
    "м2": ("area", 1), "m2": ("area", 1), "км2": ("area", 1e6), "km2": ("area", 1e6), "га": ("area", 1e4),
    "ha": ("area", 1e4), "сотка": ("area", 100), "соток": ("area", 100), "сотки": ("area", 100), "сотку": ("area", 100), "acre": ("area", 4046.8564224),
    "ft2": ("area", 0.09290304),
    # speed (m/s)
    "м/с": ("speed", 1), "m/s": ("speed", 1), "км/ч": ("speed", 1 / 3.6), "kmh": ("speed", 1 / 3.6),
    "km/h": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "уз": ("speed", 0.514444), "kn": ("speed", 0.514444),
    # data (bytes)
    "b": ("data", 1), "б": ("data", 1), "kb": ("data", 1024), "кб": ("data", 1024), "mb": ("data", 1024 ** 2),
    "мб": ("data", 1024 ** 2), "gb": ("data", 1024 ** 3), "гб": ("data", 1024 ** 3), "tb": ("data", 1024 ** 4),
    "тб": ("data", 1024 ** 4), "bit": ("data", 1 / 8), "бит": ("data", 1 / 8),
    # time (s)
    "сек": ("time", 1), "s": ("time", 1), "мин": ("time", 60), "min": ("time", 60), "ч": ("time", 3600),
    "h": ("time", 3600), "сут": ("time", 86400), "день": ("time", 86400), "дней": ("time", 86400),
    "day": ("time", 86400), "days": ("time", 86400), "нед": ("time", 604800), "week": ("time", 604800),
    "год": ("time", 31557600), "year": ("time", 31557600),
    # energy / power
    "дж": ("energy", 1), "j": ("energy", 1), "кдж": ("energy", 1000), "kj": ("energy", 1000),
    "кал": ("energy", 4.184), "cal": ("energy", 4.184), "ккал": ("energy", 4184), "kcal": ("energy", 4184),
    "квтч": ("energy", 3.6e6), "kwh": ("energy", 3.6e6), "вт": ("power", 1), "w": ("power", 1),
    "квт": ("power", 1000), "kw": ("power", 1000), "лс": ("power", 735.49875), "hp": ("power", 745.69987),
}
_TEMPS = {"c": "c", "°c": "c", "с°": "c", "°с": "c", "цельсий": "c", "f": "f", "°f": "f", "фаренгейт": "f",
          "k": "k", "кельвин": "k"}


def _to_c(v, u):
    return v if u == "c" else (v - 32) * 5 / 9 if u == "f" else v - 273.15


def _from_c(v, u):
    return v if u == "c" else v * 9 / 5 + 32 if u == "f" else v + 273.15


def convert(text):
    m = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*([^\s\d]+)\s+(?:в|во|to|in|->|=)\s+([^\s\d]+)\s*", text.lower())
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    a, b = m.group(2).strip("."), m.group(3).strip(".")
    if a in _TEMPS and b in _TEMPS:
        r = _from_c(_to_c(v, _TEMPS[a]), _TEMPS[b])
        return f"{_num(r)} °{_TEMPS[b].upper()}"
    if a in _UNITS and b in _UNITS and _UNITS[a][0] == _UNITS[b][0]:
        return f"{_num(v * _UNITS[a][1] / _UNITS[b][1])} {m.group(3)}"
    return None


def _num(v):
    if abs(v) >= 1e9 or (abs(v) < 1e-4 and v):
        return f"{v:.4g}"
    s = f"{v:,.4f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


# ---------------------------------------------------------------- result building

def R(icon, title, sub, action, keep=False):
    return {"icon": icon, "title": title, "sub": sub, "action": action, "keep": keep}


def build_results(app, query):
    """Return a list of results for the command bar; the first one runs on Enter."""
    q = query.strip()
    low = q.lower()
    out = []
    if not q:
        return [
            R("", "таймер 10 мин", "Запустить таймер", lambda: app.start_timer(600, "Таймер")),
            R("", "напомни позвонить маме завтра в 9", "Напоминание обычным языком", None),
            R("", "2^10 + 15%", "Калькулятор", None),
            R("", "tr hello world", "Перевод через Claude", None),
            R("", "? как переименовать ветку git", "Вопрос Claude", None),
            R("", "скриншот", "Область с разметкой", lambda: app.screenshot()),
        ]

    m = re.match(r"^(?:таймер|timer|t)\s+(.+)$", low)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            out.append(R("", f"Таймер на {features.fmt_secs(secs)}", "Enter — запустить",
                         lambda: app.start_timer(secs, "Таймер")))
    if low in ("таймер", "timer", "pomodoro", "помодоро", "секундомер", "stopwatch"):
        out.append(R("", "Pomodoro", "25 / 5 минут", lambda: app.timer.start_pomodoro()))
        out.append(R("", "Секундомер", "Запустить", lambda: app.timer.start(0, "Секундомер", "stopwatch")))

    m = re.match(r"^(?:напомни(?:ть)?|напоминание|remind(?:\s+me)?|r)\s+(.+)$", q, flags=re.I)
    if m:
        body, when, explicit = parse_reminder(m.group(1))
        sub = features.fmt_when(when.timestamp()) + ("" if explicit else " (время не указано — через час)")
        out.append(R("", body, "Напоминание " + sub, lambda: app.add_reminder(body, when)))

    m = re.match(r"^(?:заметка|note|n)\s+(.+)$", q, flags=re.I)
    if m:
        out.append(R("", m.group(1), "Новая заметка", lambda t=m.group(1): app.add_note(t)))

    m = re.match(r"^(?:\?|ai|ии|claude|спроси)\s*(.+)$", q, flags=re.I)
    if m and m.group(1).strip():
        out.append(R("", m.group(1), "Спросить Claude", lambda t=m.group(1): app.ask_ai(t), keep=True))

    m = re.match(r"^(?:tr|перевод|переведи|translate)\s+(.+)$", q, flags=re.I)
    if m:
        out.append(R("", m.group(1), "Перевести через Claude", lambda t=m.group(1): app.translate(t), keep=True))

    calc = calculate(q)
    if calc is not None:
        out.append(R("", calc, "Калькулятор · Enter — скопировать", lambda: app.copy_text(calc)))
    conv = convert(q)
    if conv is not None:
        out.append(R("", conv, "Конвертер · Enter — скопировать", lambda: app.copy_text(conv)))

    if re.match(r"^(https?://|www\.)\S+$", low) or re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", low):
        url = q if low.startswith("http") else "https://" + q
        out.append(R("", q, "Открыть сайт", lambda: webbrowser.open(url)))

    for title, sub, icon, words, fn in _builtin(app):
        if any(w.startswith(low) or low in w for w in words) and len(low) >= 2:
            out.append(R(icon, title, sub, fn))

    for _score, name, target, kind in app.index.search_apps(q):
        out.append(R("", name, "Приложение", lambda t=target, k=kind: features.launch_app(t, k)))
    if not re.match(r"^(?:таймер|timer|напомни|remind|tr|\?|ai)\b", low):
        for path in app.index.search_files(q, 5):
            out.append(R("" if os.path.isdir(path) else "", os.path.basename(path),
                         path, lambda p=path: os.startfile(p)))

    m = re.match(r"^(?:g|google|гугл|поиск|yt|youtube)\s+(.+)$", q, flags=re.I)
    term = m.group(1) if m else q
    if m and low.startswith(("yt", "youtube")):
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(term)
    else:
        url = "https://www.google.com/search?q=" + urllib.parse.quote(term)
    out.append(R("", f"Искать «{term}»", "В браузере", lambda: webbrowser.open(url)))
    return out


def _builtin(app):
    return [
        ("Скриншот области", "С разметкой", "", ["скриншот", "screenshot", "снимок"], app.screenshot),
        ("Скриншот экрана", "Весь экран", "", ["скриншот экрана", "screen"], lambda: app.screenshot(full=True)),
        ("Пипетка", "Цвет с экрана", "", ["пипетка", "цвет", "color", "picker"], app.color_picker),
        ("QR с экрана", "Распознать код", "", ["qr", "кюар", "штрихкод"], app.scan_qr),
        ("Текст с экрана", "OCR", "", ["ocr", "текст с экрана", "распознать"], app.ocr),
        ("Буфер обмена", "История", "", ["буфер", "clipboard"], lambda: app.open_page("clipboard")),
        ("Полка файлов", "Drag & drop", "", ["полка", "shelf", "файлы"], lambda: app.open_page("shelf")),
        ("Заметки", "", "", ["заметки", "notes"], lambda: app.open_page("notes")),
        ("Напоминания", "", "", ["напоминания", "reminders"], lambda: app.open_page("reminders")),
        ("Центр управления", "Wi-Fi, Bluetooth, тема", "", ["центр", "control", "wifi", "bluetooth"],
         lambda: app.open_page("control")),
        ("Система", "CPU, память, сеть", "", ["система", "монитор", "cpu", "system"],
         lambda: app.open_page("system")),
        ("Чат с Claude", "", "", ["чат", "chat", "claude", "ии"], lambda: app.open_page("ai")),
        ("Настройки Island", "", "", ["настройки", "settings"], lambda: app.open_page("settings")),
        ("Заблокировать компьютер", "Win+L", "", ["блок", "lock"], app.lock),
        ("Не беспокоить", "Переключить", "", ["не беспокоить", "dnd"], app.toggle_dnd),
        ("Тема Island", "Светлая / тёмная", "", ["тема", "theme"], app.toggle_theme),
        ("Выйти из Island", "", "", ["выход", "exit", "quit"], app.quit),
    ]
