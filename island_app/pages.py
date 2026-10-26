"""Panel pages shown when the island expands."""
import calendar
import datetime as dt
import os
import subprocess
import time

from PySide6.QtCore import QFileInfo, QMimeData, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QImage, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileIconProvider, QGridLayout, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QScrollArea,
                               QSlider, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import commands, config, features, services, winapi
from . import widgets as W
from .widgets import IconButton, button, card, hbox, label, vbox

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
          "Ноябрь", "Декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class Page(QWidget):
    key = ""
    glyph = ""
    title = ""
    height = 300

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.T = app.theme
        self.setObjectName("page")
        self._conns = []
        self.build()

    def build(self):
        pass

    def listen(self, signal, slot):
        """Connect to a long-lived service signal; undone in teardown() when the UI is rebuilt."""
        signal.connect(slot)
        self._conns.append((signal, slot))

    def teardown(self):
        for signal, slot in self._conns:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns = []

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def page_height(self):
        return self.height

    def focus_target(self):
        return None


def _card_layout(widget, margins=14, spacing=8, horizontal=False):
    lay = (QHBoxLayout if horizontal else QVBoxLayout)(widget)
    m = W.T.s(margins)
    lay.setContentsMargins(m, m, m, m)
    lay.setSpacing(W.T.s(spacing))
    return lay


def _elide(lab, text, width_px):
    fm = lab.fontMetrics()
    lab.setText(fm.elidedText(text, Qt.ElideRight, W.T.s(width_px)))
    lab.setToolTip(text if fm.horizontalAdvance(text) > W.T.s(width_px) else "")


# ================================================================ home


class MiniCalendar(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setMinimumSize(W.T.s(200), W.T.s(160))

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        today = dt.date.today()
        r = QRectF(self.rect())
        p.setFont(T.font(12.5, 600))
        p.setPen(T.col("fg"))
        p.drawText(QRectF(0, 0, r.width(), T.s(18)), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{MONTHS[today.month - 1]} {today.year}")
        cw = r.width() / 7
        top = T.s(24)
        rows = calendar.Calendar().monthdayscalendar(today.year, today.month)
        ch = (r.height() - top - T.s(16)) / max(len(rows), 1)
        p.setFont(T.font(10))
        p.setPen(T.col("fg3"))
        for i, d in enumerate("пн вт ср чт пт сб вс".split()):
            p.drawText(QRectF(i * cw, top, cw, T.s(14)), Qt.AlignCenter, d)
        marked = {dt.date.fromtimestamp(rm["at"]) for rm in self.app.reminders.upcoming()}
        top += T.s(16)
        p.setFont(T.tabular(11, QFont.Normal))
        for ri, week in enumerate(rows):
            for ci, day in enumerate(week):
                if not day:
                    continue
                cell = QRectF(ci * cw, top + ri * ch, cw, ch)
                d = dt.date(today.year, today.month, day)
                if d == today:
                    dd = min(cw, ch) - T.s(2)
                    p.setPen(Qt.NoPen)
                    p.setBrush(T.col("inv_bg"))
                    p.drawEllipse(cell.center(), dd / 2, dd / 2)
                    p.setPen(T.col("inv_fg"))
                else:
                    p.setPen(T.col("fg2") if ci < 5 else T.col("fg3"))
                p.drawText(cell, Qt.AlignCenter, str(day))
                if d in marked:
                    p.setPen(Qt.NoPen)
                    p.setBrush(T.col("inv_fg") if d == today else T.col("fg"))
                    p.drawEllipse(cell.center().x() - T.s(1.5), cell.bottom() - T.s(4), T.s(3), T.s(3))


class ProgressLine(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(W.T.s(4))

    def set(self, v):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(W.T.col("active"))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(W.T.col("fg"))
        p.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * self.value, r.height()), r.height() / 2, r.height() / 2)


class HomePage(Page):
    key, glyph, title, height = "home", "", "Главная", 268

    def build(self):
        T = self.T
        # now playing
        self.music = card()
        ml = _card_layout(self.music, 14, 10)
        self.art = W.Art(64, 14)
        self.m_title = label("Ничего не играет", "h2")
        self.m_artist = label("Включите музыку в любом плеере", "muted")
        self.m_pos = label("", "faint")
        top = hbox(self.art, vbox(self.m_title, self.m_artist, self.m_pos, spacing=2), "stretch", spacing=12)
        self.prog = ProgressLine()
        self.b_prev = IconButton("", "Назад", 34, 14, self.app.media.prev)
        self.b_play = IconButton("", "Пауза / воспроизведение", 40, 16, self.app.media.toggle, filled=True)
        self.b_next = IconButton("", "Вперёд", 34, 14, self.app.media.next)
        ml.addWidget(top)
        ml.addWidget(self.prog)
        ml.addWidget(hbox("stretch", self.b_prev, self.b_play, self.b_next, "stretch", spacing=10))
        # clock + weather + agenda
        self.clock = card()
        cl = _card_layout(self.clock, 14, 2)
        self.c_time = label("", "h1")
        self.c_time.setFont(T.tabular(30))
        self.c_date = label("", "muted")
        self.c_weather = label("", "muted")
        self.c_weather.setWordWrap(True)
        self.c_next = label("", "faint")
        self.c_next.setWordWrap(True)
        for w in (self.c_time, self.c_date, 6, self.c_weather, "stretch", self.c_next):
            cl.addSpacing(T.s(w)) if isinstance(w, int) else (cl.addStretch(1) if w == "stretch" else cl.addWidget(w))
        # calendar
        self.cal = card()
        _card_layout(self.cal, 14).addWidget(MiniCalendar(self.app))
        row = QHBoxLayout()
        row.setSpacing(T.s(10))
        row.addWidget(self.music, 13)
        row.addWidget(self.clock, 9)
        row.addWidget(self.cal, 9)
        # quick actions
        acts = [("", "Скриншот", self.app.screenshot), ("", "Пипетка", self.app.color_picker),
                ("", "QR с экрана", self.app.scan_qr), ("", "Текст с экрана", self.app.ocr),
                ("", "Таймер 5 мин", lambda: self.app.start_timer(300, "Таймер")),
                ("", "Новая заметка", lambda: self.app.open_page("notes", new_note=True)),
                ("", "Командная строка  Alt+Space", self.app.open_command)]
        quick = QHBoxLayout()
        quick.setSpacing(T.s(6))
        for g, tip, fn in acts:
            quick.addWidget(IconButton(g, tip, 34, 15, fn))
        quick.addStretch(1)
        self.c_sys = label("", "faint")
        quick.addWidget(self.c_sys)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(10))
        lay.addLayout(row, 1)
        lay.addLayout(quick)
        self._tick = QTimer(self, interval=1000, timeout=self.refresh)
        self.listen(self.app.media.changed, lambda _s: self.refresh_media())
        self.listen(self.app.weather.changed, lambda _s: self.refresh())

    def on_show(self):
        self.refresh_media()
        self.refresh()
        self._tick.start()

    def on_hide(self):
        self._tick.stop()

    def refresh_media(self):
        st = self.app.media.state
        if st.get("active"):
            _elide(self.m_title, st.get("title") or "Без названия", 230)
            _elide(self.m_artist, st.get("artist") or st.get("app", ""), 230)
            self.art.set_bytes(st.get("art"))
            self.b_play.setGlyph("" if st.get("playing") else "")
        else:
            self.m_title.setText("Ничего не играет")
            self.m_artist.setText("Включите музыку в любом плеере")
            self.art.set_bytes(None)
            self.b_play.setGlyph("")
            self.m_pos.setText("")
            self.prog.set(0)
        for b in (self.b_prev, self.b_next):
            b.setEnabled(bool(st.get("active")))

    def refresh(self):
        now = dt.datetime.now()
        self.c_time.setText(now.strftime("%H:%M"))
        self.c_date.setText(f"{WEEKDAYS[now.weekday()].capitalize()}, {now.day} {MONTHS_GEN[now.month - 1]}")
        w = self.app.weather.state
        if w:
            t = f"{w['temp']:+d}°"
            self.c_weather.setText(f"{t}  {w['text']}\n{w['city']} · {w['min']:+d}°…{w['max']:+d}°")
        else:
            self.c_weather.setText("")
        nxt = []
        if self.app.timer.active:
            nxt.append(f"{self.app.timer.label or 'Таймер'}: {self.app.timer.display()}")
        up = self.app.reminders.upcoming()
        if up:
            nxt.append(f"{up[0]['text']} — {features.fmt_when(up[0]['at'])}")
        self.c_next.setText("\n".join(nxt))
        st = self.app.media.state
        if st.get("active") and st.get("duration"):
            pos = st.get("position", 0) + ((time.time() - st.get("stamp", time.time())) if st.get("playing") else 0)
            pos = min(pos, st["duration"])
            self.prog.set(pos / st["duration"])
            self.m_pos.setText(f"{features.fmt_secs(pos)} / {features.fmt_secs(st['duration'])}")
        mon = self.app.monitor
        self.c_sys.setText(f"CPU {mon.cpu[-1]:.0f}%   RAM {mon.ram[-1]:.0f}%   "
                           f"↓ {services.human_bytes(mon.down[-1], True)}")


# ================================================================ clipboard


class ClipRow(QWidget):
    def __init__(self, page, item):
        super().__init__()
        T = W.T
        self.page, self.item = page, item
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.s(10), T.s(6), T.s(6), T.s(6))
        lay.setSpacing(T.s(10))
        if item["kind"] == "image":
            thumb = QWidget()
            thumb.setFixedSize(T.s(64), T.s(40))
            pm = QPixmap(item["path"])
            lab = label()
            lab.setParent(thumb)
            lab.setFixedSize(thumb.size())
            if not pm.isNull():
                lab.setPixmap(pm.scaled(lab.size() * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                              .scaled(lab.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lab.setAlignment(Qt.AlignCenter)
            lay.addWidget(thumb)
            text = f"Изображение {item.get('w')}×{item.get('h')}"
        else:
            ic = label("" if item["kind"] == "files" else "", "icon")
            ic.setFixedWidth(T.s(18))
            lay.addWidget(ic)
            text = " ".join(item.get("text", "").split())
        t = label("", None)
        fm = t.fontMetrics()
        t.setText(fm.elidedText(text, Qt.ElideRight, T.s(470)))
        lay.addWidget(t, 1)
        ago = label(_ago(item.get("t", 0)), "faint")
        lay.addWidget(ago)
        self.pin = IconButton("" if not item.get("pinned") else "",
                              "Открепить" if item.get("pinned") else "Закрепить", 28, 12,
                              lambda: page.app.clipboard.pin(item))
        self.pin.setVisible(item.get("pinned", False))
        if item.get("pinned"):
            self.pin.setGlyph("")
        self.rm = IconButton("", "Удалить", 28, 11, lambda: page.app.clipboard.remove(item))
        self.rm.setVisible(False)
        lay.addWidget(self.pin)
        lay.addWidget(self.rm)
        self.setToolTip(item.get("text", "")[:600] if item["kind"] != "image" else "")

    def enterEvent(self, e):
        self.pin.setVisible(True)
        self.rm.setVisible(True)

    def leaveEvent(self, e):
        self.pin.setVisible(bool(self.item.get("pinned")))
        self.rm.setVisible(False)


def _ago(t):
    d = time.time() - t
    if d < 60:
        return "сейчас"
    if d < 3600:
        return f"{int(d // 60)} мин"
    if d < 86400:
        return f"{int(d // 3600)} ч"
    return f"{int(d // 86400)} д"


class ClipboardPage(Page):
    key, glyph, title, height = "clipboard", "", "Буфер обмена", 420

    def build(self):
        T = self.T
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск в истории буфера…  Enter — вставить первую")
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._paste_current)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.rows = []
        self.list.itemClicked.connect(lambda it: self.app.paste_clip(self.rows[self.list.row(it)]))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.hint = label("", "faint")
        clear = button("Очистить незакреплённые", "ghost", self.app.clipboard.clear)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addWidget(hbox(self.hint, "stretch", clear))
        self.listen(self.app.clipboard.changed, self._changed)
        self._dirty = True

    def _changed(self):
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == e.Type.KeyPress and e.key() in (Qt.Key_Down, Qt.Key_Up):
            row = self.list.currentRow() + (1 if e.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return True
        return False

    def focus_target(self):
        return self.search

    def on_show(self):
        if self._dirty:
            self.refresh()

    def refresh(self):
        self._dirty = False
        self.list.clear()
        items = self.app.clipboard.search(self.search.text())
        self.rows = items[:80]
        for item in self.rows:
            it = QListWidgetItem()
            row = ClipRow(self, item)
            it.setSizeHint(QSize(10, W.T.s(52 if item["kind"] == "image" else 40)))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        if self.list.count():
            self.list.setCurrentRow(0)
        total = len(self.app.clipboard.items)
        self.hint.setText(f"{total} записей · клик — вставить в активное окно · ПКМ — меню" if total
                          else "История пуста — скопируйте что-нибудь")

    def _paste_current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.rows):
            self.app.paste_clip(self.rows[row])

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        item = self.rows[self.list.row(it)]
        m = QMenu(self)
        m.addAction("Вставить", lambda: self.app.paste_clip(item))
        m.addAction("Только скопировать", lambda: (self.app.clipboard.copy(item), self.app.alert("", "Скопировано")))
        m.addAction("Открепить" if item.get("pinned") else "Закрепить", lambda: self.app.clipboard.pin(item))
        if item["kind"] == "text":
            m.addAction("Перевести", lambda: self.app.translate(item["text"]))
            m.addAction("QR-код из текста", lambda: self.app.show_qr_for(item["text"]))
        m.addSeparator()
        m.addAction("Удалить", lambda: self.app.clipboard.remove(item))
        self.app.island.hold(True)
        m.exec(self.list.mapToGlobal(pos))
        self.app.island.hold(False)


# ================================================================ shelf


class ShelfList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setIconSize(QSize(W.T.s(40), W.T.s(40)))
        self.setGridSize(QSize(W.T.s(104), W.T.s(92)))
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, items):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(i.data(Qt.UserRole)) for i in items])
        return md

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction


class ShelfPage(Page):
    key, glyph, title, height = "shelf", "", "Полка", 330

    def build(self):
        T = self.T
        self.icons = QFileIconProvider()
        self.list = ShelfList()
        self.list.itemDoubleClicked.connect(lambda it: _open(it.data(Qt.UserRole)))
        self.empty = label("Перетащите файлы на остров — они полежат здесь,\nпока вы не перетащите их дальше",
                           "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.stack = QStackedWidget()
        drop = card()
        dl = _card_layout(drop, 20)
        big = label("", "icon")
        big.setStyleSheet(f"font-size: {T.s(28)}px;")
        big.setAlignment(Qt.AlignCenter)
        dl.addStretch(1)
        dl.addWidget(big)
        dl.addWidget(self.empty)
        dl.addStretch(1)
        self.stack.addWidget(drop)
        self.stack.addWidget(self.list)
        self.info = label("", "faint")
        bar = hbox(self.info, "stretch",
                   button("Открыть", "ghost", self._open_sel),
                   button("Показать в папке", "ghost", self._reveal),
                   button("Копировать", "ghost", self._copy),
                   button("Убрать", "ghost", self._remove),
                   button("Очистить", "ghost", self._clear), spacing=2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(self.stack, 1)
        lay.addWidget(bar)
        self.refresh()

    def add(self, paths):
        shelf = self.app.store["shelf"]
        for p in paths:
            if p and p not in shelf:
                shelf.append(p)
        self.app.store.touch()
        self.refresh()

    def refresh(self):
        self.list.clear()
        shelf = [p for p in self.app.store["shelf"] if os.path.exists(p)]
        if len(shelf) != len(self.app.store["shelf"]):
            self.app.store.data["shelf"] = shelf
            self.app.store.touch()
        for p in shelf:
            it = QListWidgetItem(self.icons.icon(QFileInfo(p)), os.path.basename(p) or p)
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(it)
        self.stack.setCurrentIndex(1 if shelf else 0)
        size = 0
        for p in shelf:
            try:
                size += os.path.getsize(p) if os.path.isfile(p) else 0
            except OSError:
                pass
        self.info.setText(f"{len(shelf)} шт. · {services.human_bytes(size)} · тащите наружу"
                          if shelf else "")

    def _selected(self):
        sel = [i.data(Qt.UserRole) for i in self.list.selectedItems()]
        return sel or [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def _open_sel(self):
        for p in self.list.selectedItems():
            _open(p.data(Qt.UserRole))

    def _reveal(self):
        sel = self._selected()
        if sel:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(sel[0])])

    def _copy(self):
        paths = self._selected()
        if paths:
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(p) for p in paths])
            md.setText("\n".join(paths))
            self.app.qapp.clipboard().setMimeData(md)
            self.app.alert("", f"Скопировано файлов: {len(paths)}")

    def _remove(self):
        rm = {i.data(Qt.UserRole) for i in self.list.selectedItems()}
        self.app.store.data["shelf"] = [p for p in self.app.store["shelf"] if p not in rm]
        self.app.store.touch()
        self.refresh()

    def _clear(self):
        self.app.store.data["shelf"] = []
        self.app.store.touch()
        self.refresh()


def _open(path):
    try:
        os.startfile(path)
    except OSError:
        pass


# ================================================================ notes


class NotesPage(Page):
    key, glyph, title, height = "notes", "", "Заметки", 400

    def build(self):
        T = self.T
        self.list = QListWidget()
        self.list.setFixedWidth(T.s(200))
        self.list.currentRowChanged.connect(self._select)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Пишите — всё сохраняется само.\nПервая строка станет заголовком.")
        self.edit.textChanged.connect(self._changed)
        add = IconButton("", "Новая заметка", 30, 13, self.new_note)
        rm = IconButton("", "Удалить заметку", 30, 13, self._delete)
        cp = IconButton("", "Скопировать текст", 30, 13,
                        lambda: self.app.copy_text(self.edit.toPlainText()))
        left = vbox(hbox(label("Заметки", "h2"), "stretch", add, spacing=4), self.list, spacing=6)
        left.setFixedWidth(T.s(200))
        self.meta = label("", "faint")
        right = vbox(self.edit, hbox(self.meta, "stretch", cp, rm, spacing=4), spacing=6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(12))
        lay.addWidget(left)
        lay.addWidget(right, 1)
        self._loading = False
        self.refresh()

    @property
    def notes(self):
        return self.app.store["notes"]

    def focus_target(self):
        return self.edit

    def refresh(self, select=None):
        self._loading = True
        self.list.clear()
        self.notes.sort(key=lambda n: -n.get("t", 0))
        for n in self.notes:
            first = (n.get("text", "").strip().splitlines() or ["Пустая заметка"])[0][:40]
            it = QListWidgetItem(first)
            it.setData(Qt.UserRole, n["id"])
            self.list.addItem(it)
        self._loading = False
        if self.notes:
            idx = next((i for i, n in enumerate(self.notes) if n["id"] == select), 0)
            self.list.setCurrentRow(idx)
        else:
            self.edit.setPlainText("")
            self.edit.setEnabled(False)
            self.meta.setText("Нет заметок — нажмите +")

    def _current(self):
        it = self.list.currentItem()
        if not it:
            return None
        return next((n for n in self.notes if n["id"] == it.data(Qt.UserRole)), None)

    def _select(self, _row):
        if self._loading:
            return
        n = self._current()
        self._loading = True
        self.edit.setEnabled(n is not None)
        self.edit.setPlainText(n["text"] if n else "")
        self._loading = False
        if n:
            self.meta.setText(dt.datetime.fromtimestamp(n.get("t", time.time())).strftime("Изменена %d.%m %H:%M"))

    def _changed(self):
        if self._loading:
            return
        n = self._current()
        if not n:
            return
        n["text"] = self.edit.toPlainText()
        n["t"] = time.time()
        self.app.store.touch()
        it = self.list.currentItem()
        it.setText((n["text"].strip().splitlines() or ["Пустая заметка"])[0][:40])

    def new_note(self, text=""):
        import uuid
        n = {"id": uuid.uuid4().hex[:10], "text": text, "t": time.time()}
        self.notes.insert(0, n)
        self.app.store.touch()
        self.refresh(select=n["id"])
        self.app.island.focus_input(self.edit)
        self.edit.moveCursor(QTextCursor.End)

    def _delete(self):
        n = self._current()
        if n:
            self.notes.remove(n)
            self.app.store.touch()
            self.refresh()


# ================================================================ timer


class TimerDial(QWidget):
    def __init__(self, timer):
        super().__init__()
        self.timer = timer
        self.setMinimumSize(W.T.s(190), W.T.s(190))

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - T.s(8)
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        p.setPen(QPen(T.col("active"), T.s(5), Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        prog = self.timer.progress()
        if self.timer.mode == "stopwatch":
            prog = (self.timer.elapsed % 60) / 60
        if prog > 0:
            p.setPen(QPen(T.col("fg"), T.s(5), Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * (1 - prog if self.timer.mode != "stopwatch" else prog)))
        p.setPen(T.col("fg"))
        p.setFont(T.tabular(38 if len(self.timer.display()) <= 5 else 30, QFont.DemiBold))
        p.drawText(r, Qt.AlignCenter, self.timer.display())
        p.setPen(T.col("fg2"))
        p.setFont(T.font(11.5))
        sub = self.timer.label or {"timer": "Таймер", "pomodoro": "Pomodoro", "stopwatch": "Секундомер"}[self.timer.mode]
        if self.timer.mode == "pomodoro":
            sub += f" · цикл {self.timer.cycle + 1}"
        p.drawText(r.adjusted(0, side * 0.34, 0, 0), Qt.AlignHCenter | Qt.AlignVCenter, sub)


class TimerPage(Page):
    key, glyph, title, height = "timer", "", "Таймер", 300

    def build(self):
        T = self.T
        t = self.app.timer
        self.dial = TimerDial(t)
        self.dial.setFixedWidth(T.s(220))
        presets = QGridLayout()
        presets.setContentsMargins(0, 0, 0, 0)
        presets.setSpacing(T.s(6))
        for i, m in enumerate([1, 3, 5, 10, 15, 25, 45, 60]):
            b = button(f"{m} мин", "chip", lambda _=False, m=m: self.app.start_timer(m * 60, "Таймер"))
            presets.addWidget(b, i // 4, i % 4)
        pw = QWidget()
        pw.setLayout(presets)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Своё: 7, 1ч 20м, 90с, 1:30 — Enter")
        self.custom.returnPressed.connect(self._custom)
        self.b_start = button("Старт", "primary", self._toggle)
        self.b_start.setMinimumWidth(T.s(110))
        controls = hbox(self.b_start, button("+1 мин", None, lambda: t.add(60)),
                        button("Сброс", "ghost", t.reset), "stretch", spacing=6)
        modes = hbox(button("Pomodoro 25/5", "chip", t.start_pomodoro),
                     button("Секундомер", "chip", lambda: t.start(0, "Секундомер", "stopwatch")), "stretch", spacing=6)
        right = vbox(label("Быстрый старт", "muted"), pw, self.custom, modes, "stretch", controls, spacing=8)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(18))
        lay.addWidget(self.dial)
        lay.addWidget(right, 1)
        self.listen(t.changed, self._update)
        self._update()

    def _custom(self):
        secs = commands.parse_duration(self.custom.text())
        if secs:
            self.app.start_timer(secs, "Таймер")
            self.custom.clear()

    def _toggle(self):
        t = self.app.timer
        if t.mode != "stopwatch" and t.remaining <= 0 and not t.running:
            self.app.start_timer(300, "Таймер")
        else:
            t.toggle()

    def _update(self):
        self.dial.update()
        t = self.app.timer
        self.b_start.setText("Пауза" if t.running else ("Продолжить" if t.active else "Старт"))


# ================================================================ reminders


class ReminderRow(QWidget):
    def __init__(self, page, r):
        super().__init__()
        T = W.T
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.s(10), T.s(4), T.s(4), T.s(4))
        lay.setSpacing(T.s(10))
        ic = label("" if r["done"] else "", "icon")
        lay.addWidget(ic)
        t = label(r["text"])
        if r["done"]:
            t.setProperty("role", "muted")
        lay.addWidget(t, 1)
        lay.addWidget(label(features.fmt_when(r["at"]), "muted"))
        lay.addWidget(IconButton("", "Удалить", 28, 11, lambda: page.app.reminders.remove(r["id"])))


class RemindersPage(Page):
    key, glyph, title, height = "reminders", "", "Напоминания", 400

    def build(self):
        T = self.T
        self.input = QLineEdit()
        self.input.setPlaceholderText("Например: позвонить маме завтра в 9 · через 20 минут чай · в пятницу в 18:00")
        self.input.textChanged.connect(self._preview)
        self.input.returnPressed.connect(self._add)
        self.preview = label("", "faint")
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(hbox(self.input, button("Добавить", "primary", self._add), spacing=8))
        lay.addWidget(self.preview)
        lay.addWidget(self.list, 1)
        lay.addWidget(hbox("stretch", button("Убрать выполненные", "ghost", self.app.reminders.clear_done)))
        self.listen(self.app.reminders.changed, self.refresh)
        self.refresh()
        self._preview("")

    def focus_target(self):
        return self.input

    def _preview(self, text):
        if not text.strip():
            self.preview.setText("Пишите обычным языком — дата и время распознаются сами")
            return
        body, when, explicit = commands.parse_reminder(text)
        self.preview.setText(f"«{body}» — {features.fmt_when(when.timestamp())}"
                             + ("" if explicit else "  (время не найдено, через час)"))

    def _add(self):
        text = self.input.text().strip()
        if not text:
            return
        body, when, _ = commands.parse_reminder(text)
        self.app.add_reminder(body, when)
        self.input.clear()

    def refresh(self):
        self.list.clear()
        items = sorted(self.app.reminders.items, key=lambda r: (r["done"], r["at"] if not r["done"] else -r["at"]))
        for r in items:
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, W.T.s(40)))
            self.list.addItem(it)
            self.list.setItemWidget(it, ReminderRow(self, r))
        if not items:
            it = QListWidgetItem("Пока пусто. Также работает командная строка: «напомни … завтра в 9»")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)


# ================================================================ control centre


class ControlPage(Page):
    key, glyph, title, height = "control", "", "Центр управления", 270

    def build(self):
        T = self.T
        self.wifi = W.Tile("", "Wi-Fi", "…")
        self.bt = W.Tile("", "Bluetooth", "…")
        self.dark = W.Tile("", "Тёмная тема", "Windows")
        self.dnd = W.Tile("", "Не беспокоить", "Island")
        self.awake = W.Tile("", "Не спать", "Экран не гаснет")
        self.hide = W.Tile("", "Автоскрытие", "Пилюли")
        self.wifi.clicked.connect(lambda c: self.app.radios.set(services.RADIO_WIFI, c))
        self.bt.clicked.connect(lambda c: self.app.radios.set(services.RADIO_BT, c))
        self.dark.clicked.connect(self._dark)
        self.dnd.clicked.connect(lambda c: self.app.set_dnd(c))
        self.awake.clicked.connect(self._awake)
        self.hide.clicked.connect(lambda c: self.app.set_setting("autohide", c))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(T.s(8))
        for i, t in enumerate([self.wifi, self.bt, self.dark, self.dnd, self.awake, self.hide]):
            grid.addWidget(t, i // 3, i % 3)
        gw = QWidget()
        gw.setLayout(grid)
        vol = card()
        vl = _card_layout(vol, 10, 4, horizontal=True)
        vl.addWidget(label("", "icon"))
        vl.addWidget(label("Громкость", None))
        vl.addStretch(1)
        vl.addWidget(IconButton("", "Без звука", 30, 13, lambda: winapi.media_key(winapi.VK_VOLUME_MUTE)))
        vl.addWidget(IconButton("", "Тише", 30, 13, lambda: [winapi.media_key(winapi.VK_VOLUME_DOWN)] * 1))
        vl.addWidget(IconButton("", "Громче", 30, 13, lambda: winapi.media_key(winapi.VK_VOLUME_UP)))
        self.bright = card()
        bl = _card_layout(self.bright, 10, 10, horizontal=True)
        bl.addWidget(label("", "icon"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderReleased.connect(lambda: services.set_brightness(self.slider.value()))
        bl.addWidget(self.slider, 1)
        self.bright.setVisible(False)
        links = hbox(button("Параметры Windows", "ghost", lambda: os.startfile("ms-settings:")),
                     button("Диспетчер задач", "ghost", lambda: subprocess.Popen(["taskmgr"])),
                     button("Блокировка", "ghost", self.app.lock), "stretch", spacing=2)
        bottom = QHBoxLayout()
        bottom.setSpacing(T.s(8))
        bottom.addWidget(vol, 1)
        bottom.addWidget(self.bright, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(gw)
        lay.addLayout(bottom)
        lay.addWidget(links)
        self.listen(self.app.radios.changed, self._radios)
        self._awake_on = False
        self._radios(self.app.radios.state)

    def on_show(self):
        self.app.radios.refresh()
        self.dark.setChecked(winapi.windows_dark())
        self.dnd.setChecked(bool(self.app.settings.get("dnd")))
        self.hide.setChecked(bool(self.app.settings.get("autohide")))
        self.awake.setChecked(self._awake_on)
        services.get_brightness(self._brightness)

    def _brightness(self, v):
        self.bright.setVisible(v is not None)
        if v is not None:
            self.slider.setValue(v)

    def _radios(self, st):
        for tile, kind in ((self.wifi, services.RADIO_WIFI), (self.bt, services.RADIO_BT)):
            if kind in st:
                tile.setEnabled(True)
                tile.setChecked(st[kind])
                tile.setSub("Вкл" if st[kind] else "Выкл")
            else:
                tile.setChecked(False)
                tile.setSub("Нет адаптера" if st else "…")

    def _dark(self, on):
        try:
            winapi.set_windows_dark(on)
        except OSError:
            self.dark.setChecked(not on)
        if self.app.settings.get("theme") == "system":
            QTimer.singleShot(400, self.app.rebuild)

    def _awake(self, on):
        self._awake_on = on
        winapi.keep_awake(on)


# ================================================================ tools


class ToolsPage(Page):
    key, glyph, title, height = "tools", "", "Инструменты", 330

    def build(self):
        T = self.T
        tools = [("", "Область", "Скриншот с разметкой", self.app.screenshot),
                 ("", "Весь экран", "Скриншот с разметкой", lambda: self.app.screenshot(full=True)),
                 ("", "Пипетка", "HEX в буфер", self.app.color_picker),
                 ("", "QR с экрана", "Распознать", self.app.scan_qr),
                 ("", "Текст с экрана", "OCR, офлайн", self.app.ocr),
                 ("", "QR из буфера", "Сгенерировать", self._qr_from_clip)]
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(T.s(8))
        for i, (g, t, s, fn) in enumerate(tools):
            tile = W.Tile(g, t, s, checkable=False)
            tile.clicked.connect(fn)
            grid.addWidget(tile, i // 3, i % 3)
        gw = QWidget()
        gw.setLayout(grid)
        self.colors = QHBoxLayout()
        self.colors.setSpacing(T.s(6))
        cw = QWidget()
        cw.setLayout(self.colors)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("Здесь появится результат QR / OCR")
        self.result.setFixedHeight(T.s(64))
        self.qr = label()
        self.qr.setFixedSize(T.s(96), T.s(96))
        self.qr.setVisible(False)
        res_row = hbox(self.result, self.qr, vbox(
            IconButton("", "Скопировать", 30, 13, lambda: self.app.copy_text(self.result.toPlainText())),
            IconButton("", "Открыть ссылку", 30, 13, self._open_link), "stretch", spacing=4), spacing=8)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(10))
        lay.addWidget(gw)
        lay.addWidget(hbox(label("Цвета", "muted"), cw, "stretch", spacing=10))
        lay.addWidget(res_row)
        self.refresh_colors()

    def refresh_colors(self):
        while self.colors.count():
            w = self.colors.takeAt(0).widget()
            if w:
                w.deleteLater()
        cols = self.app.store["colors"][:14]
        if not cols:
            self.colors.addWidget(label("пипетка сохранит сюда историю", "faint"))
        for c in cols:
            sw = W.Swatch(c)
            sw.clicked.connect(lambda _=False, c=c: self.app.copy_text(c.upper()))
            self.colors.addWidget(sw)

    def show_result(self, text, qr_image=None):
        self.result.setPlainText(text)
        self.qr.setVisible(qr_image is not None)
        if qr_image is not None:
            self.qr.setPixmap(QPixmap.fromImage(qr_image).scaled(self.qr.size(), Qt.KeepAspectRatio,
                                                                 Qt.FastTransformation))

    def _open_link(self):
        t = self.result.toPlainText().strip()
        if t.startswith(("http://", "https://")):
            import webbrowser
            webbrowser.open(t)

    def _qr_from_clip(self):
        text = self.app.qapp.clipboard().text()
        if text.strip():
            self.app.show_qr_for(text)
        else:
            self.app.alert("", "В буфере нет текста")


def make_qr(text):
    import qrcode
    q = qrcode.QRCode(border=2, box_size=8)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    img = QImage(n, n, QImage.Format_RGB32)
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            img.setPixel(x, y, 0xFF000000 if v else 0xFFFFFFFF)
    return img.scaled(n * 8, n * 8)


# ================================================================ system


class SystemPage(Page):
    key, glyph, title, height = "system", "", "Система", 360

    def build(self):
        T = self.T
        hb = services.human_bytes
        self.cpu = W.Sparkline("Процессор", lambda v: f"{v:.0f}%", max_value=100)
        self.ram = W.Sparkline("Память", lambda v: f"{v:.0f}%", max_value=100)
        self.down = W.Sparkline("Загрузка", lambda v: hb(v, True))
        self.up = W.Sparkline("Отдача", lambda v: hb(v, True))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(T.s(8))
        for i, w in enumerate([self.cpu, self.ram, self.down, self.up]):
            grid.addWidget(w, 0, i)
        gw = QWidget()
        gw.setLayout(grid)
        self.info = label("", "muted")
        self.procs = label("", None)
        self.procs.setFont(T.font(12))
        pc = card()
        pl = _card_layout(pc, 12, 4)
        pl.addWidget(label("Топ процессов по CPU", "muted"))
        pl.addWidget(self.procs)
        pl.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(10))
        lay.addWidget(gw)
        lay.addWidget(self.info)
        lay.addWidget(pc, 1)
        self.listen(self.app.monitor.tick, self._tick)
        self._n = 0

    def _tick(self):
        if not self.isVisible():
            return
        m = self.app.monitor
        self.cpu.set_values(m.cpu)
        self.ram.set_values(m.ram)
        self.down.set_values(m.down)
        self.up.set_values(m.up)
        i = m.info
        parts = [f"Память {services.human_bytes(i.get('ram_used', 0))} из {services.human_bytes(i.get('ram_total', 0))}"]
        if "disk" in i:
            parts.append(f"Диск {services.human_bytes(i['disk'][0])} из {services.human_bytes(i['disk'][1])}")
        if "battery" in i:
            pct, plugged, _left = i["battery"]
            parts.append(f"Батарея {pct:.0f}%{' · заряжается' if plugged else ''}")
        up = int(i.get("uptime", 0))
        parts.append(f"Аптайм {up // 86400} д {up % 86400 // 3600} ч {up % 3600 // 60} м")
        self.info.setText("   ·   ".join(parts))
        self._n += 1
        if self._n % 2 == 1:
            ncpu = os.cpu_count() or 1
            rows = [f"{name[:34]:<36}{cpu / ncpu:>5.1f}%   {services.human_bytes(rss):>9}"
                    for cpu, rss, name in self.app.monitor.top_processes(8) if name not in ("System Idle Process", "System",
                                                                                   "Idle", "Registry")]
            self.procs.setText("\n".join(rows[:5]))
            self.procs.setFont(QFont("Consolas", max(6, T_s(11.5))))

    def on_show(self):
        self._n = 0
        self._tick()


def T_s(v):
    return W.T.s(v)


# ================================================================ AI chat


class AIPage(Page):
    key, glyph, title, height = "ai", "", "Claude", 440

    def build(self):
        T = self.T
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Спросите Claude…   (Enter — отправить)")
        self.input.returnPressed.connect(self._send)
        self.b_chat = button("Чат", "chip")
        self.b_tr = button("Перевод", "chip")
        for b in (self.b_chat, self.b_tr):
            b.setCheckable(True)
            b.setAutoExclusive(True)
        self.b_chat.setChecked(True)
        self.b_tr.toggled.connect(lambda on: self.input.setPlaceholderText(
            "Текст для перевода…" if on else "Спросите Claude…   (Enter — отправить)"))
        self.status = label("", "faint")
        top = hbox(self.b_chat, self.b_tr, self.status, "stretch",
                   button("Новый чат", "ghost", self._clear), spacing=6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(top)
        lay.addWidget(self.view, 1)
        lay.addWidget(hbox(self.input, IconButton("", "Отправить", 36, 14, self._send, filled=True), spacing=8))
        self.msgs = []      # (role, text)
        self._render_timer = QTimer(self, singleShot=True, interval=60, timeout=self._render)
        ai = self.app.ai
        self.listen(ai.delta, self._delta)
        self.listen(ai.done, self._done)
        self.listen(ai.failed, self._failed)
        self._render()

    def focus_target(self):
        return self.input

    def ask(self, text, translate=False):
        (self.b_tr if translate else self.b_chat).setChecked(True)
        self.input.setText(text)
        self._send()

    def _send(self):
        text = self.input.text().strip()
        if not text or self.app.ai.busy:
            return
        self.input.clear()
        self.msgs.append(("user", text))
        self.msgs.append(("assistant", ""))
        self.status.setText("думает…")
        self._render()
        if self.b_tr.isChecked():
            self.app.ai.translate(text)
        else:
            self.app.ai.ask(text)

    def _delta(self, chunk):
        if self.msgs and self.msgs[-1][0] == "assistant":
            self.msgs[-1] = ("assistant", self.msgs[-1][1] + chunk)
            self.status.setText("пишет…")
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _done(self, reply):
        self.status.setText(self.app.settings.get("ai.model", ""))
        if self.msgs and self.msgs[-1][0] == "assistant":
            self.msgs[-1] = ("assistant", reply)
        self._render()
        if not self.isVisible():
            self.app.alert("", "Claude ответил", reply[:80])

    def _failed(self, msg):
        self.status.setText("")
        if self.msgs and self.msgs[-1] == ("assistant", ""):
            self.msgs.pop()
        self.msgs.append(("error", msg))
        self._render()

    def _clear(self):
        self.app.ai.reset()
        self.msgs = []
        self._render()

    def _render(self):
        c = self.T.c
        if not self.msgs:
            key = "есть" if self.app.ai.api_key() else "не задан — Настройки → ИИ"
            md = (f"### Claude\n\nЧат и перевод на вашем ключе Anthropic API. Модель: "
                  f"`{self.app.settings.get('ai.model')}`. Ключ: {key}.\n\n"
                  "Из командной строки: `? вопрос`, `tr текст`.")
            self.view.setMarkdown(md)
            return
        parts = []
        for role, text in self.msgs:
            if role == "user":
                parts.append(f"**Вы**\n\n{text}\n")
            elif role == "assistant":
                parts.append(f"**Claude**\n\n{text or '…'}\n")
            else:
                parts.append(f"**Ошибка:** {text}\n")
        self.view.setMarkdown("\n\n---\n\n".join(parts))
        self.view.document().setDefaultStyleSheet(f"code {{ background: {c['raised']}; }}")
        self.view.moveCursor(QTextCursor.End)


# ================================================================ settings


class SettingsPage(Page):
    key, glyph, title, height = "settings", "", "Настройки", 470

    def build(self):
        T = self.T
        s = self.app.settings
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 0, T.s(8), 0)
        form.setSpacing(T.s(10))

        def row(title, sub, widget):
            r = hbox(vbox(label(title), label(sub, "faint") if sub else None, spacing=0), "stretch", widget,
                     spacing=10)
            form.addWidget(r)

        themes = hbox(spacing=4)
        for key, name in (("dark", "Тёмная"), ("light", "Светлая"), ("system", "Как в Windows")):
            b = button(name, "chip", lambda _=False, k=key: self.app.set_setting("theme", k, rebuild=True))
            b.setCheckable(True)
            b.setChecked(s.get("theme") == key)
            themes.layout().addWidget(b)
        row("Тема", "Монохром: чёрная или белая пилюля", themes)

        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(8, 20)
        self.scale.setValue(int(s.get("scale", 100)) // 10)
        self.scale.setFixedWidth(T.s(200))
        self.scale_val = label(f"{s.get('scale', 100)}%", "muted")
        self.scale_val.setFixedWidth(T.s(44))
        self.scale.valueChanged.connect(lambda v: self.scale_val.setText(f"{v * 10}%"))
        self.scale.sliderReleased.connect(lambda: self.app.set_setting("scale", self.scale.value() * 10, rebuild=True))
        row("Масштаб", "80–200 %", hbox(self.scale, self.scale_val, spacing=8))

        def toggle(path, title, sub, apply=None):
            t = W.Toggle(bool(s.get(path)))
            t.toggled.connect(lambda on: (apply or (lambda v: self.app.set_setting(path, v)))(on))
            row(title, sub, t)

        toggle("expand_on_hover", "Раскрывать при наведении", "Иначе — по клику")
        toggle("autohide", "Автоскрытие", "В покое пилюля прячется в тонкую полоску")
        toggle("hide_fullscreen", "Прятать в полноэкранных приложениях", "Игры, видео, презентации")
        at = W.Toggle(winapi.autostart_enabled())
        at.toggled.connect(self.app.set_autostart)
        row("Запускать с Windows", "", at)

        screens = QComboBox()
        for i, scr in enumerate(self.app.qapp.screens()):
            screens.addItem(f"{i + 1}: {scr.name()} {scr.size().width()}×{scr.size().height()}")
        screens.setCurrentIndex(min(int(s.get("screen", 0)), screens.count() - 1))
        screens.currentIndexChanged.connect(lambda i: self.app.set_setting("screen", i, rebuild=True))
        row("Экран", "", screens)

        form.addWidget(W.sep())
        form.addWidget(label("Живые события", "h2"))
        chips = hbox(spacing=4)
        for key, name in (("music", "Музыка"), ("timer", "Таймер"), ("layout", "Раскладка"), ("caps", "Caps Lock"),
                          ("privacy", "Микрофон/камера"), ("downloads", "Загрузки"), ("claude", "Claude Code")):
            b = button(name, "chip")
            b.setCheckable(True)
            b.setChecked(bool(s.get(f"live.{key}", True)))
            b.toggled.connect(lambda on, k=key: self.app.set_setting(f"live.{k}", on))
            chips.layout().addWidget(b)
        chips.layout().addStretch(1)
        form.addWidget(chips)

        form.addWidget(W.sep())
        form.addWidget(label("ИИ (Claude)", "h2"))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-ant-…  (хранится зашифрованным DPAPI)" if not self.app.ai.api_key()
                                    else "ключ сохранён — введите новый, чтобы заменить")
        self.key.setFixedWidth(T.s(300))
        self.key.returnPressed.connect(self._save_key)
        row("Ключ Anthropic API", "или переменная ANTHROPIC_API_KEY",
            hbox(self.key, button("Сохранить", None, self._save_key), spacing=6))
        model = QLineEdit(s.get("ai.model", "claude-sonnet-5-5"))
        model.setFixedWidth(T.s(220))
        model.editingFinished.connect(lambda: self.app.set_setting("ai.model", model.text().strip() or "claude-sonnet-5-5"))
        row("Модель", "", model)
        lang = QLineEdit(s.get("ai.translate_to", "ru"))
        lang.setFixedWidth(T.s(80))
        lang.editingFinished.connect(lambda: self.app.set_setting("ai.translate_to", lang.text().strip() or "ru"))
        row("Язык перевода", "код языка, напр. ru, en, de", lang)

        form.addWidget(W.sep())
        form.addWidget(label("Прочее", "h2"))
        city = QLineEdit(s.get("weather.city", ""))
        city.setPlaceholderText("авто по IP")
        city.setFixedWidth(T.s(200))
        city.editingFinished.connect(lambda: (self.app.set_setting("weather.city", city.text().strip()),
                                              self.app.weather.refresh()))
        row("Город для погоды", "Open-Meteo, без ключа", city)
        row("Claude Code hooks", f"POST http://127.0.0.1:{s.get('hooks.port')}/claude",
            button("Скопировать конфиг", None, self._copy_hooks))
        hk = s.get("hotkeys", {})
        names = {"command": "Командная строка", "toggle": "Показать/раскрыть", "clipboard": "Буфер",
                 "screenshot": "Скриншот", "picker": "Пипетка", "qr": "QR", "ocr": "OCR", "notes": "Заметки",
                 "timer": "Таймер", "ai": "Claude"}
        txt = "   ".join(f"{names.get(k, k)}: {v.title()}" for k, v in hk.items())
        hkl = label(txt, "muted", wrap=True)
        form.addWidget(label("Горячие клавиши", None))
        form.addWidget(hkl)
        form.addWidget(hbox(button("Открыть settings.json", None, lambda: os.startfile(config.SETTINGS_PATH)),
                            button("Папка данных", "ghost", lambda: os.startfile(config.DATA_DIR)),
                            "stretch", label(f"Island {config.APP_VERSION}", "faint"), spacing=6))
        form.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

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
        import json
        cfg = {"hooks": {e: hook for e in ("UserPromptSubmit", "PreToolUse", "Notification", "Stop", "SessionEnd")}}
        self.app.copy_text(json.dumps(cfg, indent=2))


# ================================================================ command bar


class CmdRow(QWidget):
    def __init__(self, r):
        super().__init__()
        T = W.T
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.s(12), T.s(4), T.s(12), T.s(4))
        lay.setSpacing(T.s(12))
        ic = label(r["icon"], "icon")
        ic.setFixedWidth(T.s(20))
        lay.addWidget(ic)
        t = label("")
        t.setFont(T.font(13.5, 500))
        t.setText(t.fontMetrics().elidedText(r["title"], Qt.ElideRight, T.s(420)))
        lay.addWidget(t)
        lay.addStretch(1)
        sub = label("", "muted")
        sub.setText(sub.fontMetrics().elidedText(r["sub"], Qt.ElideMiddle, T.s(260)))
        lay.addWidget(sub)


class CommandPage(Page):
    key, glyph, title, height = "command", "", "Команда", 120

    def build(self):
        T = self.T
        self.input = QLineEdit()
        self.input.setProperty("role", "big")
        self.input.setPlaceholderText("Программа, файл, 2+2, «таймер 10 мин», «напомни … завтра в 9», ? вопрос")
        self.input.textChanged.connect(self.refresh)
        self.input.returnPressed.connect(self.run_current)
        self.input.installEventFilter(self)
        self.list = QListWidget()
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(lambda it: self.run(self.results[self.list.row(it)]))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(self.input)
        lay.addWidget(self.list, 1)
        self.results = []
        self.listen(self.app.index.ready, lambda: self.isVisible() and self.refresh())

    def focus_target(self):
        return self.input

    def on_show(self):
        self.input.selectAll()
        self.refresh()

    def page_height(self):
        n = min(len(self.results), 7)
        return 50 + n * 44 + (8 if n else 0)

    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if e.key() == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if e.key() == Qt.Key_Escape:
                self.app.island.collapse(restore_focus=True)
                return True
        return False

    def refresh(self):
        self.results = commands.build_results(self.app, self.input.text())
        self.list.clear()
        self.results = self.results[:12]
        for r in self.results:
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, W.T.s(44)))
            self.list.addItem(it)
            self.list.setItemWidget(it, CmdRow(r))
        if self.list.count():
            self.list.setCurrentRow(0)
        self.app.island.relayout()

    def run_current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.results):
            self.run(self.results[row])

    def run(self, r):
        if r["action"] is None:
            self.input.setText(r["title"])
            self.input.setFocus()
            return
        keep = r.get("keep")
        if not keep:
            self.app.island.collapse(restore_focus=True)
        QTimer.singleShot(120 if not keep else 0, r["action"])
        self.input.clear()


PAGES = [HomePage, ClipboardPage, ShelfPage, NotesPage, TimerPage, RemindersPage, ControlPage, ToolsPage,
         SystemPage, AIPage, SettingsPage]
