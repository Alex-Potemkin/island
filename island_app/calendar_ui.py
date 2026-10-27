"""Calendar tab: agenda, week and month views with navigation, plus an editor for your own events.

Google and iCal events are read-only (opened in the browser); events of the local calendar ("Мой")
can be created, changed, repeated and deleted. Clicking an empty slot in the week view or a day in
the month view starts a new event there.
"""
import datetime as dt
import webbrowser

from PySide6.QtCore import QDate, QPointF, QRectF, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)

from . import commands
from . import widgets as W
from .pages import MONTHS, MONTHS_GEN, EventRow, Page, _day_title
from .widgets import IconButton, button, hbox, label

LOCAL_TZ = dt.datetime.now().astimezone().tzinfo
SHORT_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def at(d, h=0, m=0):
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=LOCAL_TZ)


def _layout_day(events):
    """Side-by-side columns for overlapping events: [(event, col, ncols)]."""
    events = sorted(events, key=lambda e: (e["start"], -(e["end"] - e["start"]).total_seconds()))
    out, cluster, cols, cluster_end = [], [], [], None

    def flush():
        n = max(1, len(cols))
        out.extend((e, c, n) for e, c in cluster)

    for e in events:
        if cluster_end is not None and e["start"] >= cluster_end:
            flush()
            cluster, cols, cluster_end = [], [], None
        for i, last in enumerate(cols):
            if last <= e["start"]:
                cols[i] = e["end"]
                cluster.append((e, i))
                break
        else:
            cols.append(e["end"])
            cluster.append((e, len(cols) - 1))
        cluster_end = max(cluster_end or e["end"], e["end"])
    flush()
    return out


class _View(QWidget):
    """Shared bits of the painted views."""
    open_event = Signal(dict)
    new_event = Signal(object)      # aware datetime (start)

    def __init__(self, cal):
        super().__init__()
        self.cal = cal
        self.anchor = dt.date.today()
        self.hits = []              # (QRectF, event or datetime)
        self.setMouseTracking(True)
        self._hover = None

    def color(self, e, alpha=255):
        T = W.T
        c = QColor(T.c["inv_bg"]) if e.get("local") else QColor(T.c["active"])
        c.setAlpha(alpha)
        return c

    def mousePressEvent(self, ev):
        for rect, what in self.hits:
            if rect.contains(ev.position()):
                if isinstance(what, dict):
                    self.open_event.emit(what)
                else:
                    self.new_event.emit(what)
                return

    def mouseMoveEvent(self, ev):
        hover = next((r for r, w in self.hits if r.contains(ev.position())), None)
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()


class WeekView(_View):
    GUTTER = 44

    def title(self):
        a = monday(self.anchor)
        b = a + dt.timedelta(days=6)
        if a.month == b.month:
            return f"{a.day}–{b.day} {MONTHS_GEN[b.month - 1]} {b.year}"
        return f"{a.day} {MONTHS_GEN[a.month - 1]} – {b.day} {MONTHS_GEN[b.month - 1]} {b.year}"

    def step(self, n):
        self.anchor += dt.timedelta(days=7 * n)

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.hits = []
        r = QRectF(self.rect())
        start = monday(self.anchor)
        days = [start + dt.timedelta(days=i) for i in range(7)]
        events = self.cal.between(at(days[0]), at(days[-1]) + dt.timedelta(days=1))
        timed = [e for e in events if not e["all_day"]]
        allday = [e for e in events if e["all_day"]]
        h0 = min([7] + [e["start"].hour for e in timed if e["start"].date() in days])
        h1 = max([22] + [min(24, e["end"].hour + (1 if e["end"].minute else 0)) for e in timed
                         if e["end"].date() in days])
        gx = T.s(self.GUTTER)
        cw = (r.width() - gx) / 7
        head = T.s(34)
        strip = T.s(22) if allday else 0
        top = head + strip + T.s(4)
        hh = (r.height() - top) / max(1, h1 - h0)
        today = dt.date.today()
        # headers
        for i, d in enumerate(days):
            x = gx + i * cw
            p.setFont(T.font(10.5))
            p.setPen(T.col("fg3") if d.weekday() >= 5 else T.col("fg2"))
            p.drawText(QRectF(x, 0, cw, T.s(14)), Qt.AlignCenter, SHORT_DAYS[i])
            num = QRectF(x + cw / 2 - T.s(11), T.s(14), T.s(22), T.s(20))
            if d == today:
                p.setPen(Qt.NoPen)
                p.setBrush(T.col("inv_bg"))
                p.drawRoundedRect(num, T.s(10), T.s(10))
                p.setPen(T.col("inv_fg"))
            else:
                p.setPen(T.col("fg"))
            p.setFont(T.tabular(12.5))
            p.drawText(num, Qt.AlignCenter, str(d.day))
        # grid
        p.setPen(QPen(T.col("line"), 1))
        for h in range(h0, h1 + 1):
            y = top + (h - h0) * hh
            p.drawLine(QPointF(gx, y), QPointF(r.width(), y))
            if h < h1:
                p.setPen(T.col("fg3"))
                p.setFont(T.tabular(10, QFont.Normal))
                p.drawText(QRectF(0, y - T.s(1), gx - T.s(6), T.s(14)), Qt.AlignRight | Qt.AlignTop, f"{h:02d}:00")
                p.setPen(QPen(T.col("line"), 1))
        for i in range(8):
            x = gx + i * cw
            p.drawLine(QPointF(x, head), QPointF(x, r.height()))
        # empty slots -> new event (half-hour)
        for i, d in enumerate(days):
            for half in range((h1 - h0) * 2):
                y = top + half * hh / 2
                self.hits.append((QRectF(gx + i * cw, y, cw, hh / 2), at(d, h0) + dt.timedelta(minutes=30 * half)))
        # all-day strip
        for e in allday:
            for i, d in enumerate(days):
                if e["start"].date() <= d < max(e["end"].date(), e["start"].date() + dt.timedelta(days=1)):
                    rect = QRectF(gx + i * cw + T.s(2), head + T.s(2), cw - T.s(4), strip - T.s(4))
                    self._block(p, rect, e, small=True)
        # timed events
        for i, d in enumerate(days):
            mine = [e for e in timed if e["start"].date() <= d <= (e["end"] - dt.timedelta(seconds=1)).date()]
            for e, col, ncol in _layout_day(mine):
                s = max(e["start"], at(d, h0))
                f = min(e["end"], at(d, h1) if h1 < 24 else at(d) + dt.timedelta(days=1))
                y0 = top + (s - at(d, h0)).total_seconds() / 3600 * hh
                y1 = top + (f - at(d, h0)).total_seconds() / 3600 * hh
                w = (cw - T.s(4)) / ncol
                rect = QRectF(gx + i * cw + T.s(2) + col * w, y0 + 1, w - T.s(1), max(T.s(14), y1 - y0 - 2))
                self._block(p, rect, e)
        # now line
        now = dt.datetime.now(LOCAL_TZ)
        if now.date() in days and h0 <= now.hour < h1:
            i = days.index(now.date())
            y = top + (now - at(now.date(), h0)).total_seconds() / 3600 * hh
            p.setPen(QPen(T.col("inv_bg"), T.s(2)))
            p.drawLine(QPointF(gx + i * cw, y), QPointF(gx + (i + 1) * cw, y))
            p.setBrush(T.col("inv_bg"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(gx + i * cw, y), T.s(3.5), T.s(3.5))

    def _block(self, p, rect, e, small=False):
        T = W.T
        hover = self._hover is not None and self._hover == rect
        p.setPen(Qt.NoPen)
        p.setBrush(self.color(e, 255 if hover else (230 if e.get("local") else 255)))
        p.drawRoundedRect(rect, T.r(6), T.r(6))
        fg = T.col("inv_fg") if e.get("local") else T.col("fg")
        p.setPen(fg)
        p.setFont(T.font(10.5, 600))
        fm = p.fontMetrics()
        inner = rect.adjusted(T.s(5), T.s(2), -T.s(4), -T.s(2))
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, fm.elidedText(e["title"], Qt.ElideRight, int(inner.width())))
        if not small and inner.height() > T.s(28):
            p.setFont(T.font(9.5))
            c = QColor(fg)
            c.setAlpha(170)
            p.setPen(c)
            p.drawText(inner.adjusted(0, T.s(14), 0, 0), Qt.AlignLeft | Qt.AlignTop,
                       f"{e['start']:%H:%M}–{e['end']:%H:%M}")
        self.hits.insert(0, (rect, e))


class MonthView(_View):
    def title(self):
        return f"{MONTHS[self.anchor.month - 1]} {self.anchor.year}"

    def step(self, n):
        m = self.anchor.month - 1 + n
        self.anchor = dt.date(self.anchor.year + m // 12, m % 12 + 1, 1)

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.hits = []
        r = QRectF(self.rect())
        first = self.anchor.replace(day=1)
        start = monday(first)
        head = T.s(18)
        cw = r.width() / 7
        ch = (r.height() - head) / 6
        events = self.cal.between(at(start), at(start) + dt.timedelta(days=42))
        today = dt.date.today()
        p.setFont(T.font(10.5))
        for i, d in enumerate(SHORT_DAYS):
            p.setPen(T.col("fg3") if i >= 5 else T.col("fg2"))
            p.drawText(QRectF(i * cw, 0, cw, head), Qt.AlignCenter, d)
        line_h = T.s(15)
        for k in range(42):
            d = start + dt.timedelta(days=k)
            cell = QRectF((k % 7) * cw, head + (k // 7) * ch, cw, ch)
            inner = cell.adjusted(T.s(2), T.s(2), -T.s(2), -T.s(2))
            hover = self._hover is not None and self._hover == inner
            p.setPen(Qt.NoPen)
            p.setBrush(T.col("hover") if hover else (T.col("surface") if d.month == first.month else Qt.NoBrush))
            p.drawRoundedRect(inner, T.r(8), T.r(8))
            self.hits.append((inner, at(d, 9)))
            num = QRectF(inner.left() + T.s(4), inner.top() + T.s(3), T.s(20), T.s(17))
            if d == today:
                p.setBrush(T.col("inv_bg"))
                p.drawRoundedRect(num, T.s(8), T.s(8))
                p.setPen(T.col("inv_fg"))
            else:
                p.setPen(T.col("fg") if d.month == first.month else T.col("fg3"))
            p.setFont(T.tabular(11))
            p.drawText(num, Qt.AlignCenter, str(d.day))
            day_events = [e for e in events
                          if e["start"].date() <= d <= (e["end"] - dt.timedelta(seconds=1)).date()]
            fit = max(0, int((inner.height() - T.s(22)) // line_h))
            y = inner.top() + T.s(22)
            for j, e in enumerate(day_events):
                if j >= fit or (j == fit - 1 and len(day_events) > fit):
                    p.setPen(T.col("fg3"))
                    p.setFont(T.font(9.5))
                    p.drawText(QRectF(inner.left() + T.s(5), y, inner.width(), line_h), Qt.AlignLeft | Qt.AlignVCenter,
                               f"ещё {len(day_events) - j}")
                    break
                row = QRectF(inner.left() + T.s(3), y, inner.width() - T.s(6), line_h - T.s(2))
                p.setPen(Qt.NoPen)
                p.setBrush(self.color(e, 255))
                p.drawRoundedRect(row, T.r(4), T.r(4))
                p.setPen(T.col("inv_fg") if e.get("local") else T.col("fg"))
                p.setFont(T.font(9.5, 500))
                text = ("" if e["all_day"] else f"{e['start']:%H:%M} ") + e["title"]
                p.drawText(row.adjusted(T.s(4), 0, -T.s(2), 0), Qt.AlignLeft | Qt.AlignVCenter,
                           p.fontMetrics().elidedText(text, Qt.ElideRight, int(row.width() - T.s(6))))
                self.hits.insert(0, (row, e))
                y += line_h


class EventEditor(QWidget):
    """Create / change / delete a local event; shows Google and iCal events read-only."""
    done = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cal = app.calendar
        self.lid = None
        self.remote = None
        T = W.T
        self.head = label("Новое событие", "h2")
        self.quick = QLineEdit()
        self.quick.setPlaceholderText("Быстро: «обед с Сашей завтра в 13», «созвон в пятницу в 18:30»")
        self.quick.returnPressed.connect(self._parse)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Название")
        self.date = QDateEdit()
        self.date.setDisplayFormat("dd.MM.yyyy")
        self.t0, self.t1 = QTimeEdit(), QTimeEdit()
        for t in (self.t0, self.t1):
            t.setDisplayFormat("HH:mm")
        self.allday = W.Toggle(False)
        self.allday.toggled.connect(lambda on: [t.setEnabled(not on) for t in (self.t0, self.t1)])
        self.where = QLineEdit()
        self.where.setPlaceholderText("Место (необязательно)")
        self.repeat_btns = {}
        rep = QHBoxLayout()
        rep.setContentsMargins(0, 0, 0, 0)
        rep.setSpacing(T.s(4))
        for k, t in self.cal.REPEATS.items():
            b = button(t, "chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=k: self._repeat(k))
            self.repeat_btns[k] = b
            rep.addWidget(b)
        rep.addStretch(1)
        repw = QWidget()
        repw.setLayout(rep)
        self.save = button("Сохранить", "primary", self._save)
        self.delete = button("Удалить", "ghost", self._delete)
        self.open = button("Открыть в браузере", "ghost", self._open)
        self.info = label("", "faint")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(self.head)
        lay.addWidget(self.quick)
        lay.addWidget(self.name)
        lay.addWidget(hbox(label("Дата"), self.date, 8, label("с"), self.t0, label("до"), self.t1, 8,
                           label("Весь день"), self.allday, "stretch", spacing=6))
        lay.addWidget(self.where)
        lay.addWidget(repw)
        lay.addStretch(1)
        lay.addWidget(hbox(self.info, "stretch", self.open, self.delete, button("Отмена", "ghost", self.done.emit),
                           self.save, spacing=6))

    def _repeat(self, k):
        for kk, b in self.repeat_btns.items():
            b.setChecked(kk == k)

    def _repeat_value(self):
        return next((k for k, b in self.repeat_btns.items() if b.isChecked()), "none")

    def _fill(self, title, start, end, all_day=False, location="", repeat="none"):
        self.name.setText(title)
        self.date.setDate(QDate(start.year, start.month, start.day))
        self.t0.setTime(QTime(start.hour, start.minute))
        self.t1.setTime(QTime(end.hour, end.minute))
        self.allday.setChecked(all_day)
        self.where.setText(location)
        self._repeat(repeat)

    def new(self, start):
        self.lid, self.remote = None, None
        self.head.setText("Новое событие")
        self.quick.clear()
        self._fill("", start, start + dt.timedelta(hours=1))
        self._editable(True)
        self.delete.hide()
        self.open.hide()
        self.info.setText("Сохранится в календарь «Мой»")

    def edit(self, e):
        if e.get("local"):
            rec = self.cal.local_record(e["lid"])
            self.lid, self.remote = e["lid"], None
            st = dt.datetime.fromisoformat(rec["start"]).astimezone(LOCAL_TZ)
            en = dt.datetime.fromisoformat(rec["end"]).astimezone(LOCAL_TZ)
            self.head.setText("Событие")
            self._fill(rec["title"], st, en, rec.get("all_day", False), rec.get("location", ""),
                       rec.get("repeat", "none"))
            self._editable(True)
            self.delete.show()
            self.open.hide()
            self.info.setText("Календарь «Мой»" + (" · повторяется" if rec.get("repeat", "none") != "none" else ""))
        else:
            self.lid, self.remote = None, e
            self.head.setText(e["title"])
            self._fill(e["title"], e["start"], e["end"], e["all_day"], e.get("location", ""))
            self._editable(False)
            self.delete.hide()
            self.open.setVisible(bool(e.get("url")))
            self.info.setText(f"{e['source']} · только чтение")
        self.quick.clear()

    def _editable(self, on):
        for w in (self.quick, self.name, self.date, self.t0, self.t1, self.allday, self.where, self.save,
                  *self.repeat_btns.values()):
            w.setEnabled(on)
        self.quick.setVisible(on)
        self.save.setVisible(on)

    def _parse(self):
        body, when, explicit = commands.parse_reminder(self.quick.text())
        if not self.quick.text().strip():
            return
        when = when.replace(tzinfo=LOCAL_TZ) if when.tzinfo is None else when
        self._fill(body, when, when + dt.timedelta(hours=1))
        self.info.setText("Проверьте и нажмите «Сохранить»" + ("" if explicit else " (время не найдено)"))

    def values(self):
        d = self.date.date().toPython()
        if self.allday.isChecked():
            st = at(d)
            en = st + dt.timedelta(days=1)
        else:
            t0, t1 = self.t0.time().toPython(), self.t1.time().toPython()
            st = at(d, t0.hour, t0.minute)
            en = at(d, t1.hour, t1.minute)
            if en <= st:
                en = st + dt.timedelta(hours=1)
        return {"title": self.name.text().strip(), "start": st, "end": en, "all_day": self.allday.isChecked(),
                "location": self.where.text().strip(), "repeat": self._repeat_value()}

    def _save(self):
        if self.quick.text().strip() and not self.name.text().strip():
            self._parse()
        v = self.values()
        if not v["title"]:
            self.name.setFocus()
            return
        self.cal.save_event(v, self.lid)
        self.app.alert("", v["title"], f"{v['start']:%d.%m %H:%M}" if not v["all_day"] else "весь день")
        self.done.emit()

    def _delete(self):
        if self.lid:
            self.cal.delete_event(self.lid)
        self.done.emit()

    def _open(self):
        if self.remote and self.remote.get("url"):
            webbrowser.open(self.remote["url"])


class CalendarPage(Page):
    key, glyph, title, height = "calendar", "", "Календарь", 470
    VIEWS = [("agenda", "Список"), ("week", "Неделя"), ("month", "Месяц")]

    def build(self):
        T = self.T
        cal = self.app.calendar
        self.view_btns = {}
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(T.s(4))
        for k, t in self.VIEWS:
            b = button(t, "chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=k: self.set_view(k))
            self.view_btns[k] = b
            chips.addWidget(b)
        cw = QWidget()
        cw.setLayout(chips)
        self.prev = IconButton("", "Назад", 30, 12, lambda: self._step(-1))
        self.next = IconButton("", "Вперёд", 30, 12, lambda: self._step(1))
        self.today = button("Сегодня", "ghost", self._today)
        self.head = label("", "h2")
        self.add = IconButton("", "Новое событие", 32, 13, lambda: self.new_event(None), filled=True)
        self.agenda = QListWidget()
        self.agenda.setSelectionMode(QAbstractItemView.NoSelection)
        self.agenda.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.agenda.itemClicked.connect(self._agenda_click)
        self.week = WeekView(cal)
        self.month = MonthView(cal)
        self.editor = EventEditor(self.app)
        self.editor.done.connect(lambda: self.set_view(self.view))
        for v in (self.week, self.month):
            v.open_event.connect(self.editor_for)
            v.new_event.connect(self.new_event)
        self.month.new_event.disconnect()
        self.month.new_event.connect(self._month_day)
        self.stack = QStackedWidget()
        for w in (self.agenda, self.week, self.month, self.editor):
            self.stack.addWidget(w)
        self.status = label("", "faint")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        self.toolbar = hbox(cw, 10, self.prev, self.today, self.next, 6, self.head, "stretch", self.add, spacing=2)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.stack, 1)
        lay.addWidget(hbox(self.status, "stretch",
                           button("Аккаунты", "ghost", lambda: self.app.open_settings("accounts")),
                           button("Обновить", "ghost", cal.refresh), spacing=2))
        self.rows = []
        self.view = self.app.settings.get("calendar_view", "week")
        self.listen(cal.changed, self.refresh)
        self.set_view(self.view if self.view in dict(self.VIEWS) else "week")

    # -------- navigation
    def set_view(self, k):
        self.view = k
        if self.app.settings.get("calendar_view") != k:
            self.app.settings.set("calendar_view", k)
        for kk, b in self.view_btns.items():
            b.setChecked(kk == k)
        self.stack.setCurrentWidget({"agenda": self.agenda, "week": self.week, "month": self.month}[k])
        for w in (self.prev, self.next, self.today):
            w.setVisible(k != "agenda")
        self.refresh()

    def _current(self):
        return {"week": self.week, "month": self.month}.get(self.view)

    def _step(self, n):
        v = self._current()
        if v:
            v.step(n)
            self.refresh()

    def _today(self):
        for v in (self.week, self.month):
            v.anchor = dt.date.today()
        self.refresh()

    def _month_day(self, when):
        self.week.anchor = when.date()
        self.set_view("week")

    def new_event(self, start):
        if start is None:
            now = dt.datetime.now(LOCAL_TZ)
            start = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        self.editor.new(start)
        self.stack.setCurrentWidget(self.editor)
        self.head.setText("")
        self.app.island.focus_input(self.editor.quick)

    def editor_for(self, e):
        self.editor.edit(e)
        self.stack.setCurrentWidget(self.editor)
        self.head.setText("")

    # -------- content
    def refresh(self):
        cal = self.app.calendar
        if self.stack.currentWidget() is self.editor:
            return
        v = self._current()
        self.head.setText(v.title() if v else "Ближайшие две недели")
        if v:
            v.update()
        else:
            self._fill_agenda()
        parts = []
        if not cal.has_accounts():
            parts.append("только «Мой» календарь — подключите Google или iCal в «Аккаунтах»")
        elif cal.updated:
            parts.append("обновлено " + dt.datetime.fromtimestamp(cal.updated).strftime("%H:%M"))
        if cal.errors:
            parts.append("ошибка: " + "; ".join(f"{k}: {e}" if k else e for k, e in cal.errors.items())[:100])
        self.status.setText(" · ".join(parts))

    def _fill_agenda(self):
        self.agenda.clear()
        self.rows = []
        now = dt.datetime.now(LOCAL_TZ)
        day = None
        for e in self.app.calendar.upcoming(14):
            d = max(e["start"].date(), dt.date.today())
            if d != day:
                day = d
                it = QListWidgetItem(_day_title(d))
                it.setFlags(Qt.NoItemFlags)
                it.setFont(W.T.font(12, 600))
                it.setForeground(W.T.col("fg2"))
                self.agenda.addItem(it)
                self.rows.append(None)
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, W.T.s(46)))
            self.agenda.addItem(it)
            self.agenda.setItemWidget(it, EventRow(e, now))
            self.rows.append(e)
        if not self.rows:
            it = QListWidgetItem("На две недели вперёд событий нет — нажмите + чтобы добавить")
            it.setFlags(Qt.NoItemFlags)
            self.agenda.addItem(it)

    def _agenda_click(self, it):
        row = self.agenda.row(it)
        e = self.rows[row] if row < len(self.rows) else None
        if e:
            self.editor_for(e)
