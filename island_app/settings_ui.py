"""Settings in three plain sections, the layout editor ("Конструктор") and the first-run welcome screen."""
import json

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea,
                               QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from . import config, layout, osapi
from . import widgets as W
from .pages import Page
from .widgets import IconButton, button, hbox, label, vbox

ACTIONS = {"command": "Командная строка", "toggle": "Показать / свернуть", "clipboard": "Буфер обмена",
           "screenshot": "Скриншот", "picker": "Пипетка", "qr": "QR с экрана", "ocr": "Текст с экрана",
           "notes": "Заметки", "timer": "Таймер", "ai": "Claude"}


class PresetCard(QAbstractButton):
    """Big clickable card: title, one line of description, a check mark when active."""

    def __init__(self, title, text, glyph=""):
        super().__init__()
        self.title, self.text, self.glyph = title, text, glyph
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(W.T.s(88))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = False

    def sizeHint(self):
        return QSize(W.T.s(200), W.T.s(88))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setBrush(T.col("inv_bg") if on else (T.col("hover") if self._hover else T.col("surface")))
        p.setPen(Qt.NoPen if on else QPen(T.col("line"), 1))
        p.drawRoundedRect(r, T.s(16), T.s(16))
        fg, sub = (T.col("inv_fg"), T.col("inv_fg", 160)) if on else (T.col("fg"), T.col("fg2"))
        pad = T.s(14)
        p.setPen(fg)
        p.setFont(T.font(14, 600))
        p.drawText(QRectF(pad, pad - T.s(2), r.width() - 2 * pad, T.s(20)), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        if on:
            p.setFont(T.icon_font(12))
            p.drawText(QRectF(r.width() - pad - T.s(16), pad - T.s(2), T.s(16), T.s(20)), Qt.AlignCenter, "")
        p.setPen(sub)
        p.setFont(T.font(11.5))
        p.drawText(QRectF(pad, pad + T.s(22), r.width() - 2 * pad, r.height() - pad * 2 - T.s(20)),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.text)


class ReorderList(QWidget):
    """Rows of (glyph, title, hint, ↑, ↓, switch). Emits the enabled keys in order."""
    changed = Signal(list)

    def __init__(self, items, enabled, locked=()):
        super().__init__()
        self.items = items                          # key -> (glyph, title, hint)
        on = [k for k in enabled if k in items]
        self.order = on + [k for k in items if k not in on]
        self.enabled = set(on)
        self.locked = set(locked)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(W.T.s(4))
        self._render()

    def _render(self):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        T = W.T
        for i, key in enumerate(self.order):
            glyph, title, hint = self.items[key]
            row = W.card()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(T.s(12), T.s(6), T.s(8), T.s(6))
            rl.setSpacing(T.s(10))
            ic = label(glyph, "icon")
            ic.setFixedWidth(T.s(20))
            rl.addWidget(ic)
            rl.addWidget(vbox(label(title), label(hint, "faint"), spacing=0), 1)
            up = IconButton("", "Выше", 28, 11, lambda _=False, k=key: self._move(k, -1))
            down = IconButton("", "Ниже", 28, 11, lambda _=False, k=key: self._move(k, 1))
            up.setEnabled(i > 0)
            down.setEnabled(i < len(self.order) - 1)
            rl.addWidget(up)
            rl.addWidget(down)
            sw = W.Toggle(key in self.enabled)
            sw.setEnabled(key not in self.locked)
            sw.toggled.connect(lambda on, k=key: self._toggle(k, on))
            rl.addWidget(sw)
            self.lay.addWidget(row)

    def _move(self, key, d):
        i = self.order.index(key)
        j = max(0, min(len(self.order) - 1, i + d))
        self.order[i], self.order[j] = self.order[j], self.order[i]
        self._render()
        self._emit()

    def _toggle(self, key, on):
        (self.enabled.add if on else self.enabled.discard)(key)
        self._emit()

    def _emit(self):
        self.changed.emit([k for k in self.order if k in self.enabled])


def chip_group(options, current, on_pick, multi=False):
    """Row of chips. options: [(key, title)]. Single choice unless multi (then current is a set)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(W.T.s(4))
    btns = {}
    for key, title in options:
        b = button(title, "chip")
        b.setCheckable(True)
        b.setChecked(key in current if multi else key == current)
        btns[key] = b
        lay.addWidget(b)
    for key, b in btns.items():
        if multi:
            b.toggled.connect(lambda _on: on_pick({k for k, x in btns.items() if x.isChecked()}))
        else:
            b.clicked.connect(lambda _=False, k=key: ([x.setChecked(kk == k) for kk, x in btns.items()], on_pick(k)))
    lay.addStretch(1)
    return box


def chip_grid(options, current, on_pick, cols=4):
    """Multi-select chips wrapped in a grid; on_pick gets the selected keys in the options' order."""
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(W.T.s(4))
    btns = {}
    for i, (key, title) in enumerate(options):
        b = button(title, "chip")
        b.setCheckable(True)
        b.setChecked(key in current)
        btns[key] = b
        grid.addWidget(b, i // cols, i % cols)
    for b in btns.values():
        b.toggled.connect(lambda _on: on_pick([k for k, _t in options if btns[k].isChecked()]))
    return box


class _Form(QWidget):
    """Vertical form with section titles and label/control rows."""

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, W.T.s(10), W.T.s(6))
        self.lay.setSpacing(W.T.s(10))

    def title(self, text, hint=""):
        if self.lay.count():
            self.lay.addSpacing(W.T.s(6))
        self.lay.addWidget(label(text, "h2"))
        if hint:
            self.lay.addWidget(label(hint, "faint", wrap=True))

    def row(self, title, hint, widget):
        self.lay.addWidget(hbox(vbox(label(title), label(hint, "faint") if hint else None, spacing=0), "stretch",
                                widget, spacing=10))

    def add(self, widget):
        self.lay.addWidget(widget)

    def done(self):
        self.lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self)
        return scroll


class SettingsPage(Page):
    key, glyph, title, height = "settings", "", "Настройки", 480
    SECTIONS = [("main", "Основное"), ("builder", "Конструктор"), ("more", "Дополнительно")]

    def build(self):
        T = self.T
        self.stack = QStackedWidget()
        self.scrolls = []
        for key, _title in self.SECTIONS:
            scroll = getattr(self, "_" + key)().done()
            self.scrolls.append(scroll)
            self.stack.addWidget(scroll)
        state = self.app.ui_state
        idx = state.get("settings_section", 0)
        tabs = chip_group([(str(i), t) for i, (_k, t) in enumerate(self.SECTIONS)], str(idx),
                          lambda k: self._section(int(k)))
        self.section_chips = tabs.findChildren(QAbstractButton)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(10))
        lay.addWidget(tabs)
        lay.addWidget(self.stack, 1)
        self._section(idx)
        QTimer.singleShot(0, lambda: self.scrolls[idx].verticalScrollBar().setValue(state.get("settings_scroll", 0)))
        for i, sc in enumerate(self.scrolls):
            sc.verticalScrollBar().valueChanged.connect(
                lambda v, i=i: self.stack.currentIndex() == i and state.__setitem__("settings_scroll", v))

    def _section(self, i):
        for j, chip in enumerate(getattr(self, "section_chips", [])):
            chip.setChecked(j == i)
        self.stack.setCurrentIndex(i)
        self.app.ui_state["settings_section"] = i
        self.app.ui_state["settings_scroll"] = self.scrolls[i].verticalScrollBar().value()

    # ------------------------------------------------------------ Основное

    def _main(self):
        s = self.app.settings
        f = _Form()
        f.title("Готовые наборы", "Один клик — и панель собрана. Потом всё можно поменять в «Конструкторе».")
        current = layout.matching_preset(layout.current(s))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(W.T.s(8))
        for i, (key, p) in enumerate(layout.PRESETS.items()):
            pc = PresetCard(p["title"], p["text"])
            pc.setChecked(key == current)
            pc.clicked.connect(lambda _=False, k=key: self.app.apply_preset(k))
            grid.addWidget(pc, 0, i)
        gw = QWidget()
        gw.setLayout(grid)
        f.add(gw)

        f.title("Вид")
        f.row("Тема", "", chip_group([("dark", "Тёмная"), ("light", "Светлая"), ("system", "Как в системе")],
                                     s.get("theme"), lambda k: self.app.set_setting("theme", k, rebuild=True)))
        sc = QSlider(Qt.Horizontal)
        sc.setRange(8, 20)
        sc.setValue(int(s.get("scale", 100)) // 10)
        sc.setFixedWidth(W.T.s(200))
        val = label(f"{s.get('scale', 100)}%", "muted")
        val.setFixedWidth(W.T.s(44))
        sc.valueChanged.connect(lambda v: val.setText(f"{v * 10}%"))
        sc.sliderReleased.connect(lambda: self.app.set_setting("scale", sc.value() * 10, rebuild=True))
        f.row("Размер", "80–200 %", hbox(sc, val, spacing=8))

        f.title("Поведение")
        f.row("Открывать панель", "", chip_group(
            [("hover", "Наведением"), ("click", "Кликом")], "hover" if s.get("expand_on_hover", True) else "click",
            lambda k: self.app.set_setting("expand_on_hover", k == "hover")))
        f.row("Прятать в полоску", "Когда ничего не происходит", self._toggle("autohide"))
        at = W.Toggle(osapi.autostart_enabled())
        at.toggled.connect(self.app.set_autostart)
        f.row(osapi.AUTOSTART_LABEL, "", at)
        return f

    def _toggle(self, path, rebuild=False):
        t = W.Toggle(bool(self.app.settings.get(path)))
        t.toggled.connect(lambda on: self.app.set_setting(path, on, rebuild=rebuild))
        return t

    # ------------------------------------------------------------ Конструктор

    def _builder(self):
        lay = layout.current(self.app.settings)
        f = _Form()
        f.title("Вкладки панели", "Включите нужные и расставьте стрелками. «Настройки» всегда последние.")
        tabs = ReorderList({k: v for k, v in layout.TABS.items()}, lay["tabs"], locked=())
        tabs.changed.connect(lambda keys: self.app.set_layout(tabs=keys or ["home"]))
        f.add(tabs)

        f.title("Карточки на главной", "До шести штук; по три в ряд.")
        cards = ReorderList(layout.CARDS, lay["home"])
        cards.changed.connect(lambda keys: self.app.set_layout(home=keys))
        f.add(cards)

        f.title("Кнопки под карточками")
        f.add(chip_grid([(k, t) for k, (_g, t) in layout.QUICK.items()], lay["quick"],
                        lambda keys: self.app.set_layout(quick=keys)))

        f.title("Пилюля в покое", "Что показывать, когда ничего не происходит.")
        f.add(chip_group(list(layout.IDLE.items()), lay["idle"], lambda k: self.app.set_layout(idle=k)))

        f.title("Живые события", "Что пилюля показывает сама.")
        live = self.app.settings.get("live", {})
        opts = [("music", "Музыка"), ("timer", "Таймер"), ("layout", "Раскладка"), ("caps", "Caps Lock"),
                ("privacy", "Микрофон и камера"), ("downloads", "Загрузки"), ("claude", "Claude Code")]
        f.add(chip_grid(opts, [k for k, _t in opts if live.get(k, True)],
                        lambda keys: self.app.set_setting("live", {k: k in keys for k, _t in opts})))
        f.add(hbox("stretch", button("Вернуть как было", "ghost", lambda: self.app.apply_preset("full"))))
        return f

    # ------------------------------------------------------------ Дополнительно

    def _more(self):
        s = self.app.settings
        f = _Form()
        f.title("Claude (ИИ)", "Нужен ключ Anthropic API. Он хранится только у вас"
                + (", зашифрованный средствами Windows." if osapi.IS_WINDOWS else " (файл доступен только вам)."))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setFixedWidth(W.T.s(280))
        self.key.setPlaceholderText("ключ сохранён — введите новый, чтобы заменить" if self.app.ai.api_key()
                                    else "sk-ant-…")
        self.key.returnPressed.connect(self._save_key)
        f.row("Ключ API", "или переменная ANTHROPIC_API_KEY",
              hbox(self.key, button("Сохранить", None, self._save_key), spacing=6))
        lang = QLineEdit(s.get("ai.translate_to", "ru"))
        lang.setFixedWidth(W.T.s(80))
        lang.editingFinished.connect(lambda: self.app.set_setting("ai.translate_to", lang.text().strip() or "ru"))
        f.row("Переводить на", "код языка: ru, en, de…", lang)

        f.title("Разное")
        city = QLineEdit(s.get("weather.city", ""))
        city.setPlaceholderText("определить по IP")
        city.setFixedWidth(W.T.s(200))
        city.editingFinished.connect(lambda: (self.app.set_setting("weather.city", city.text().strip()),
                                              self.app.weather.refresh()))
        f.row("Город для погоды", "", city)
        screens = QComboBox()
        for i, scr in enumerate(self.app.qapp.screens()):
            screens.addItem(f"{i + 1}: {scr.name()} {scr.size().width()}×{scr.size().height()}")
        screens.setCurrentIndex(min(int(s.get("screen", 0)), screens.count() - 1))
        screens.currentIndexChanged.connect(lambda i: self.app.set_setting("screen", i, rebuild=True))
        f.row("Экран", "", screens)
        f.row("Прятать в полноэкранных приложениях", "Игры, видео, презентации", self._toggle("hide_fullscreen"))

        f.title("Горячие клавиши")
        if osapi.HAS_GLOBAL_HOTKEYS:
            f.add(label("Меняются в settings.json → hotkeys.", "faint"))
            hk = s.get("hotkeys", {})
            for k, v in hk.items():
                f.row(ACTIONS.get(k, k), "", label(v.title(), "muted"))
        else:
            f.add(label("В Linux глобальные сочетания назначаются в настройках системы "
                        "(Клавиатура → Свои сочетания). Команды:", "faint", wrap=True))
            cmd = osapi.autostart_command()
            for k, name in ACTIONS.items():
                f.row(name, "", label(f"{cmd} --action {k}", "muted", selectable=True))

        f.title("Claude Code", f"Пилюля показывает, что делает Claude Code (hooks → 127.0.0.1:{s.get('hooks.port')}).")
        f.add(hbox(button("Скопировать конфиг hooks", None, self._copy_hooks), "stretch"))

        f.title("Файлы")
        f.add(hbox(button("Открыть settings.json", None, lambda: osapi.open_path(config.SETTINGS_PATH)),
                   button("Папка данных", "ghost", lambda: osapi.open_path(config.DATA_DIR)),
                   button("Сбросить все настройки", "ghost", self.app.reset_settings), "stretch",
                   label(f"Island {config.APP_VERSION} · {osapi.NAME}", "faint"), spacing=6))
        return f

    def _save_key(self):
        key = self.key.text().strip()
        if not key:
            return
        self.app.settings.set("ai.api_key", config.protect(key) or key)
        self.key.clear()
        self.key.setPlaceholderText("ключ сохранён — введите новый, чтобы заменить")
        self.app.alert("", "Ключ сохранён")

    def _copy_hooks(self):
        port = self.app.settings.get("hooks.port")
        cmd = f"curl -s -m 2 -X POST http://127.0.0.1:{port}/claude --data-binary @-"
        hook = [{"hooks": [{"type": "command", "command": cmd}]}]
        cfg = {"hooks": {e: hook for e in ("UserPromptSubmit", "PreToolUse", "Notification", "Stop", "SessionEnd")}}
        self.app.copy_text(json.dumps(cfg, indent=2))


class WelcomePage(Page):
    """First run: three questions, then the island is ready."""
    key, glyph, title, height = "welcome", "", "Добро пожаловать", 400

    def build(self):
        T = self.T
        s = self.app.settings
        self.choice = {"preset": "standard", "theme": s.get("theme", "dark"),
                       "hover": bool(s.get("expand_on_hover", True))}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.s(6), T.s(4), T.s(6), 0)
        lay.setSpacing(T.s(10))
        h = label("Добро пожаловать в Island", "h1")
        lay.addWidget(h)
        lay.addWidget(label("Три вопроса — и готово. Всё можно поменять потом в настройках.", "muted"))
        lay.addSpacing(T.s(4))
        lay.addWidget(label("1. Что положить в панель?", "h2"))
        cards = {}
        row = QHBoxLayout()
        row.setSpacing(T.s(8))
        for key, p in layout.PRESETS.items():
            pc = PresetCard(p["title"], p["text"])
            pc.setChecked(key == self.choice["preset"])
            cards[key] = pc
            row.addWidget(pc)
        for key, pc in cards.items():
            pc.clicked.connect(lambda _=False, k=key: ([c.setChecked(kk == k) for kk, c in cards.items()],
                                                       self.choice.__setitem__("preset", k)))
        rw = QWidget()
        rw.setLayout(row)
        lay.addWidget(rw)
        lay.addWidget(label("2. Тема", "h2"))
        lay.addWidget(chip_group([("dark", "Тёмная"), ("light", "Светлая"), ("system", "Как в системе")],
                                 self.choice["theme"], lambda k: self.choice.__setitem__("theme", k)))
        lay.addWidget(label("3. Как открывать панель", "h2"))
        lay.addWidget(chip_group([("hover", "Наведением на пилюлю"), ("click", "Кликом")],
                                 "hover" if self.choice["hover"] else "click",
                                 lambda k: self.choice.__setitem__("hover", k == "hover")))
        lay.addStretch(1)
        go = button("Готово", "primary", self._finish)
        go.setMinimumWidth(T.s(140))
        hint = "Alt+Space — командная строка" if osapi.HAS_GLOBAL_HOTKEYS else "Сочетания клавиш — в Настройки → Дополнительно"
        lay.addWidget(hbox(label(hint, "faint"), "stretch", go))

    def _finish(self):
        s = self.app.settings
        s.set("layout", dict(layout.PRESETS[self.choice["preset"]]["layout"]), save=False)
        s.set("theme", self.choice["theme"], save=False)
        s.set("expand_on_hover", self.choice["hover"], save=False)
        s.set("onboarded", True)
        self.app.island.collapse()
        QTimer.singleShot(400, self.app.rebuild)
