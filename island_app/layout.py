"""What the panel can be built from, and ready-made presets.

The user's choice lives in settings.json under "layout":
    tabs   ordered list of panel tabs            (settings is always last and can't be hidden)
    home   ordered list of cards on the home tab
    quick  quick-action buttons under the home cards
    idle   what the resting pill shows: empty | clock | date
"""

TABS = {  # key: (glyph, title, description)
    "home": ("", "Главная", "Музыка, часы, погода, календарь"),
    "clipboard": ("", "Буфер", "История копирования с поиском"),
    "shelf": ("", "Полка", "Временное место для файлов"),
    "notes": ("", "Заметки", "Быстрые заметки"),
    "timer": ("", "Таймер", "Таймер, Pomodoro, секундомер"),
    "reminders": ("", "Напоминания", "«Позвонить завтра в 9»"),
    "control": ("", "Управление", "Wi-Fi, Bluetooth, громкость, яркость"),
    "tools": ("", "Инструменты", "Скриншот, пипетка, QR, текст с экрана"),
    "system": ("", "Система", "Процессор, память, сеть"),
    "ai": ("", "Claude", "Чат и перевод"),
    "calendar": ("", "Календарь", "События из Google и iCal-календарей"),
    "inbox": ("", "Уведомления", "История всплывающих уведомлений"),
    "apps": ("", "Приложения", "Закреплённые программы, папки и сайты"),
    "printer": ("", "Принтер", "3D-принтер Bambu Lab: прогресс и время"),
    "deskdash": ("", "DeskDash", "Макросы и Spotify Connect из DeskDash"),
}

CARDS = {
    "music": ("", "Музыка", "Что играет, управление"),
    "clock": ("", "Часы и погода", "Время, дата, погода, ближайшее событие"),
    "calendar": ("", "Календарь", "Месяц с отметками напоминаний"),
    "system": ("", "Система", "Графики процессора и памяти"),
    "reminders": ("", "Напоминания", "Три ближайших"),
    "note": ("", "Заметка", "Последняя заметка"),
    "agenda": ("", "События", "Ближайшие встречи из календарей"),
    "apps": ("", "Приложения", "Закреплённые программы"),
    "printer": ("", "3D-принтер", "Прогресс и сколько осталось"),
    "deskdash": ("", "DeskDash", "Кнопки макросов"),
}

QUICK = {  # key: (glyph, title)
    "screenshot": ("", "Скриншот"),
    "fullshot": ("", "Весь экран"),
    "picker": ("", "Пипетка"),
    "qr": ("", "QR с экрана"),
    "ocr": ("", "Текст с экрана"),
    "timer5": ("", "Таймер 5 мин"),
    "pomodoro": ("", "Pomodoro"),
    "note": ("", "Новая заметка"),
    "command": ("", "Командная строка"),
    "lock": ("", "Блокировка"),
}

IDLE = {"empty": "Пустая", "clock": "Часы", "date": "Дата и время"}

PRESETS = {
    "minimal": {
        "title": "Минимум", "text": "Только самое нужное: главная, буфер, таймер",
        "layout": {"tabs": ["home", "clipboard", "timer"], "home": ["music", "clock"],
                   "quick": ["screenshot", "command"], "idle": "clock"},
    },
    "standard": {
        "title": "Стандарт", "text": "Повседневный набор без системных графиков и ИИ",
        "layout": {"tabs": ["home", "clipboard", "shelf", "notes", "timer", "reminders", "calendar", "control",
                            "tools", "inbox", "apps"],
                   "home": ["music", "clock", "agenda"],
                   "quick": ["screenshot", "picker", "qr", "ocr", "timer5", "note", "command"], "idle": "clock"},
    },
    "full": {
        "title": "Всё", "text": "Все вкладки и карточки, часы в пилюле",
        "layout": {"tabs": list(TABS), "home": ["music", "clock", "calendar", "agenda", "reminders", "note"],
                   "quick": ["screenshot", "fullshot", "picker", "qr", "ocr", "timer5", "pomodoro", "note",
                             "command"], "idle": "clock"},
    },
}

DEFAULT = PRESETS["full"]["layout"]


def current(settings):
    """The user's layout with unknown keys dropped (hand-edited settings.json can't break the UI)."""
    lay = settings.get("layout") or {}
    tabs = [t for t in lay.get("tabs", DEFAULT["tabs"]) if t in TABS] or ["home"]
    home = [c for c in lay.get("home", DEFAULT["home"]) if c in CARDS]
    quick = [q for q in lay.get("quick", DEFAULT["quick"]) if q in QUICK]
    idle = lay["idle"] if lay.get("idle") in IDLE else "clock"
    return {"tabs": list(dict.fromkeys(tabs)), "home": list(dict.fromkeys(home)),
            "quick": list(dict.fromkeys(quick)), "idle": idle}


def matching_preset(lay):
    for key, p in PRESETS.items():
        if all(lay.get(k) == v for k, v in p["layout"].items() if k != "idle"):
            return key
    return None
