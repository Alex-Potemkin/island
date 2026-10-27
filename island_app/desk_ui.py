"""Pages and home cards for pinned apps, the 3D printer and DeskDash."""
import os
import webbrowser

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractButton, QGridLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from . import devices, osapi, perf
from . import widgets as W
from .pages import ClickableRow, Page, ProgressLine, _card_layout, _elide
from .widgets import IconButton, button, card, hbox, label, vbox

DESKDASH_URL = "https://github.com/Alex-Potemkin/DeskDash/releases/latest"


def _relayout(page):
    """Resize the panel to the page's new height, if the page is the one on screen."""
    isl = getattr(page.app, "island", None)
    if isl is not None and isl.panel.current is page:
        isl.relayout()


# ================================================================ pinned apps


class AppTile(QAbstractButton):
    """Icon with the name under it. Click opens, right click: move / show in folder / unpin."""

    def __init__(self, app, item, compact=False):
        super().__init__()
        self.app, self.item, self.compact = app, item, compact
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(item["name"] + ("" if item["kind"] == "appid" else "\n" + item["target"]))
        T = W.T
        self.setFixedSize(T.s(64 if compact else 82), T.s(62 if compact else 78))
        self.clicked.connect(self.launch)
        self._hover = False

    def launch(self):
        try:
            self.app.apps.launch(self.item)
            self.app.island.collapse()
        except OSError as exc:
            self.app.alert("", "Не открывается", str(exc)[:80], ttl=4)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def contextMenuEvent(self, e):
        a, apps = self.item, self.app.apps
        m = QMenu(self)
        m.addAction("Открыть", self.launch)
        if a["kind"] != "appid" and not a["target"].startswith(("http://", "https://")):
            m.addAction("Показать в папке", lambda: osapi.reveal(a["target"]))
        m.addSeparator()
        m.addAction("Левее", lambda: apps.move(a["id"], -1))
        m.addAction("Правее", lambda: apps.move(a["id"], 1))
        m.addSeparator()
        m.addAction("Открепить", lambda: apps.remove(a["id"]))
        self.app.island.hold(True)
        try:
            m.exec(e.globalPos())
        finally:
            self.app.island.hold(False)

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self._hover or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(T.col("active" if self.isDown() else "hover"))
            p.drawRoundedRect(r, T.r(14), T.r(14))
        size = T.s(26 if self.compact else 34)
        box = QRectF((r.width() - size) / 2, T.s(7 if self.compact else 9), size, size)
        if not self.app.apps.exists(self.item):
            p.setOpacity(0.4)
        icon = self.app.apps.icon(self.item)
        if icon is not None:
            dpr = self.devicePixelRatioF()
            pm = icon.pixmap(QSize(int(size * dpr), int(size * dpr)))
            pm.setDevicePixelRatio(dpr)
            p.drawPixmap(box.topLeft(), pm)
        else:   # Store apps and sites: a letter tile
            p.setPen(Qt.NoPen)
            p.setBrush(T.col("inv_bg"))
            p.drawRoundedRect(box, size * 0.28, size * 0.28)
            p.setPen(T.col("inv_fg"))
            p.setFont(T.font(14 if not self.compact else 11, 700))
            p.drawText(box, Qt.AlignCenter, (self.item["name"][:1] or "?").upper())
        p.setOpacity(1.0)
        p.setFont(T.font(10.5 if self.compact else 11))
        p.setPen(T.col("fg2"))
        fm = QFontMetrics(p.font())
        text_r = QRectF(T.s(3), box.bottom() + T.s(4), r.width() - T.s(6), fm.height() + 2)
        p.drawText(text_r, Qt.AlignHCenter | Qt.AlignTop, fm.elidedText(self.item["name"], Qt.ElideRight,
                                                                       int(text_r.width())))


class AppsPage(Page):
    key, glyph, title, height = "apps", "", "Приложения", 300
    COLS = 8

    def build(self):
        T = self.T
        self.count = label("", "faint")
        self.add_btn = button("Добавить", None, lambda: self.show_add(True))
        head = hbox(self.count, "stretch", button("Файл или папка…", "ghost", self.choose_file), self.add_btn,
                    spacing=6)
        self.stack = QStackedWidget()
        # pinned grid
        self.grid_w = QWidget()
        self.grid = QGridLayout(self.grid_w)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(T.s(4))
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        empty = card()
        el = _card_layout(empty, 20)
        hint = label("Закрепите программы, папки и сайты, чтобы открывать их с острова.\n"
                     "«Добавить» — выбрать из установленных, или перетащите ярлык, .exe или папку\n"
                     "на остров, пока открыта эта вкладка.", "muted")
        hint.setAlignment(Qt.AlignCenter)
        el.addStretch(1)
        el.addWidget(hint)
        el.addStretch(1)
        self.empty = empty
        grid_page = QWidget()
        gl = QVBoxLayout(grid_page)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(self.grid_w)
        gl.addWidget(empty, 1)
        gl.addStretch(0)
        # picker
        pick = QWidget()
        pl = QVBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(T.s(8))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти программу…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        self.search.returnPressed.connect(self._pin_first)
        self.list = QListWidget()
        self.list.setIconSize(QSize(T.s(20), T.s(20)))
        self.list.itemClicked.connect(self._pin_item)
        pl.addWidget(hbox(self.search, button("Готово", None, lambda: self.show_add(False)), spacing=6))
        pl.addWidget(self.list, 1)
        self.stack.addWidget(grid_page)
        self.stack.addWidget(pick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(head)
        lay.addWidget(self.stack, 1)
        self.listen(self.app.apps.changed, self.refresh)
        self.listen(self.app.index.ready, lambda: self.stack.currentIndex() == 1 and self._fill())
        self.refresh()

    def page_height(self):
        if self.stack.currentIndex() == 1:
            return 400
        n = len(self.app.apps.items)
        rows = max(1, -(-n // self.COLS))
        return max(220, 44 + rows * 82)

    def on_hide(self):
        if self.stack.currentIndex() == 1:
            self.show_add(False)

    def focus_target(self):
        return self.search if self.stack.currentIndex() == 1 else None

    def refresh(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        items = self.app.apps.items
        for i, a in enumerate(items):
            self.grid.addWidget(AppTile(self.app, a), i // self.COLS, i % self.COLS)
        self.grid_w.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.count.setText(f"Закреплено: {len(items)}" if items else "")
        if self.stack.currentIndex() == 1:
            self._fill()
        _relayout(self)

    def show_add(self, on):
        self.stack.setCurrentIndex(1 if on else 0)
        self.add_btn.setVisible(not on)
        if on:
            self.search.clear()
            self._fill()
            self.app.island.focus_input()
            self.search.setFocus()
        _relayout(self)

    def _fill(self):
        from PySide6.QtCore import QFileInfo
        from PySide6.QtWidgets import QFileIconProvider
        prov = QFileIconProvider()
        self.list.clear()
        found = self.app.apps.candidates(self.app.index, self.search.text(), 60)
        self._found = found[:200]
        for name, target, kind in self._found:
            pinned = self.app.apps.has(target)
            it = QListWidgetItem(("✓  " if pinned else "") + name)
            if kind == "lnk" and len(self._found) <= 60:   # icons only for a short list: they cost ~1 ms each
                it.setIcon(prov.icon(QFileInfo(target)))
            it.setData(Qt.UserRole, (name, target, kind))
            self.list.addItem(it)
        if not self._found:
            it = QListWidgetItem("Ничего не найдено" if self.search.text().strip() else "Список программ ещё собирается…")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)

    def _pin_item(self, it):
        data = it.data(Qt.UserRole)
        if not data:
            return
        name, target, kind = data
        if self.app.apps.has(target):
            a = next(x for x in self.app.apps.items if os.path.normcase(x["target"]) == os.path.normcase(target))
            self.app.apps.remove(a["id"])          # the list is rebuilt by refresh()
        elif self.app.apps.add(name, target, kind):
            self.app.alert("", "Закреплено", name, ttl=1.4)

    def _pin_first(self):
        if self.list.count() and self.list.item(0).data(Qt.UserRole):
            self._pin_item(self.list.item(0))

    def choose_file(self):
        from PySide6.QtWidgets import QFileDialog
        self.app.island.hold(True)
        try:
            path, _ = QFileDialog.getOpenFileName(None, "Закрепить файл или программу", os.path.expanduser("~"),
                                                  "Программы и ярлыки (*.exe *.lnk *.url *.bat *.cmd *.desktop "
                                                  "*.AppImage *.sh);;Все файлы (*)")
        finally:
            self.app.island.hold(False)
        if path:
            self.add_paths([path])

    def add_paths(self, paths):
        added = [p for p in paths if self.app.apps.add_path(p)]
        if added:
            self.app.alert("", "Закреплено" + (f": {len(added)}" if len(added) > 1 else ""),
                           os.path.basename(added[0].rstrip("\\/")), ttl=1.6)
        return added


def card_apps(home):
    """Home card: the first eight pins."""
    c = card()
    cl = _card_layout(c, 12, 4)
    cl.addWidget(ClickableRow("", "Приложения", lambda: home.app.open_page("apps")))
    grid_w = QWidget()
    grid = QGridLayout(grid_w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(0)
    grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    cl.addWidget(grid_w)
    hint = label("Пусто — закрепите программы на вкладке «Приложения»", "faint", wrap=True)
    cl.addWidget(hint)
    cl.addStretch(1)
    state = {"sig": None}

    def refresh():
        items = home.app.apps.items[:8]
        sig = [(a["id"], a["name"]) for a in items]
        if sig == state["sig"]:
            return
        state["sig"] = sig
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for i, a in enumerate(items):
            grid.addWidget(AppTile(home.app, a, compact=True), i // 4, i % 4)
        hint.setVisible(not items)
    home.refreshers.append(refresh)
    home.listen(home.app.apps.changed, refresh)
    return c


# ================================================================ 3D printer


class PrinterPage(Page):
    key, glyph, title, height = "printer", "", "3D-принтер", 236

    def build(self):
        T = self.T
        pr = self.app.printer
        self.stack = QStackedWidget()
        # status
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(T.s(10))
        c = card()
        cl = _card_layout(c, 16, 6)
        self.name = label("", "faint")
        self.job = label("", "h2")
        self.pct = label("", "h1")
        self.pct.setFont(T.tabular(30))
        self.left = label("", "muted")
        self.prog = ProgressLine()
        self.layer = label("", "faint")
        self.temps = label("", "faint")
        self.err = label("", "muted", wrap=True)
        cl.addWidget(hbox(self.name, "stretch", self.layer, spacing=6))
        cl.addWidget(self.job)
        cl.addWidget(hbox(self.pct, vbox("stretch", self.left, spacing=0), "stretch", spacing=12))
        cl.addWidget(self.prog)
        cl.addWidget(hbox(self.temps, "stretch", spacing=6))
        cl.addWidget(self.err)
        cl.addStretch(1)
        sl.addWidget(c, 1)
        sl.addWidget(hbox("stretch", button("Переподключить", "ghost", pr.start),
                          button("Настройки принтера", "ghost", lambda: self.show_setup(True)), spacing=6))
        # setup
        su = QWidget()
        ul = QVBoxLayout(su)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(T.s(8))
        ul.addWidget(label("Bambu Lab по локальной сети", "h2"))
        ul.addWidget(label("На экране принтера: Настройки → Сеть (WLAN) — IP-адрес и код доступа (Access Code). "
                           "Серийный номер: Настройки → Устройство или в Bambu Handy. Принтер и ПК — в одной сети.",
                           "muted", wrap=True))
        cfg = pr.cfg()
        self.f_host, self.f_serial, self.f_code, self.f_name = QLineEdit(cfg["host"]), QLineEdit(cfg["serial"]), \
            QLineEdit(), QLineEdit(cfg["name"] if pr.configured else "")
        self.f_host.setPlaceholderText("192.168.1.50")
        self.f_serial.setPlaceholderText("01P00A123456789")
        self.f_code.setPlaceholderText("сохранён" if cfg["code"] else "12345678")
        self.f_code.setEchoMode(QLineEdit.Password)
        self.f_name.setPlaceholderText("Принтер")
        for title, w in (("IP-адрес", self.f_host), ("Серийный номер", self.f_serial),
                         ("Код доступа", self.f_code), ("Название", self.f_name)):
            lab = label(title, "muted")
            lab.setFixedWidth(T.s(130))
            w.setFixedWidth(T.s(260))
            ul.addWidget(hbox(lab, w, "stretch", spacing=8))
        self.setup_msg = label("Если принтер отклоняет подключение — включите на нём «Режим LAN» "
                               "или «Режим разработчика».", "faint", wrap=True)
        ul.addWidget(self.setup_msg)
        ul.addStretch(1)
        self.b_forget = button("Отключить", "ghost", self._forget)
        self.b_cancel = button("Отмена", "ghost", lambda: self.show_setup(False))
        ul.addWidget(hbox(self.b_forget, "stretch", self.b_cancel, button("Подключить", "primary", self._save),
                          spacing=6))
        self.stack.addWidget(st)
        self.stack.addWidget(su)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self._tick = perf.timer(QTimer(self, timeout=self.refresh), 15000, 30000)
        self.listen(pr.changed, self.refresh)
        self.stack.setCurrentIndex(0 if pr.configured else 1)
        self.refresh()

    def page_height(self):
        return 360 if self.stack.currentIndex() == 1 else self.height

    def on_show(self):
        self.refresh()
        self._tick.start()

    def on_hide(self):
        self._tick.stop()

    def focus_target(self):
        return self.f_host if self.stack.currentIndex() == 1 else None

    def show_setup(self, on):
        self.stack.setCurrentIndex(1 if on else 0)
        self.refresh()
        _relayout(self)

    def _save(self):
        host, serial = self.f_host.text().strip(), self.f_serial.text().strip()
        code = self.f_code.text().strip() or self.app.printer.cfg()["code"]
        if not (host and serial and code):
            self.setup_msg.setText("Нужны все три поля: IP-адрес, серийный номер и код доступа.")
            return
        self.app.printer.configure(host, serial, code, self.f_name.text())
        self.f_code.clear()
        self.f_code.setPlaceholderText("сохранён")
        self.show_setup(False)

    def _forget(self):
        self.app.printer.forget()
        for w in (self.f_host, self.f_serial, self.f_code, self.f_name):
            w.clear()
        self.refresh()

    def refresh(self):
        pr = self.app.printer
        self.b_forget.setVisible(pr.configured)
        self.b_cancel.setVisible(pr.configured)
        if not pr.configured:
            if self.stack.currentIndex() == 0:
                self.stack.setCurrentIndex(1)
            return
        cfg = pr.cfg()
        st = pr.gcode_state
        self.name.setText(cfg["name"] + " · " + (devices.STATES.get(st, st.capitalize() or "Ожидание")
                                                 if pr.connected else "нет связи"))
        printing = pr.connected and st in ("RUNNING", "PAUSE", "PREPARE", "SLICING")
        _elide(self.job, pr.job or ("Сейчас не печатает" if pr.connected else "Подключение…"), 600)
        self.pct.setText(f"{pr.percent}%" if printing or st == "FINISH" else "")
        if printing and pr.remaining:
            self.left.setText(f"осталось {devices.fmt_minutes(pr.remaining)}\nготово в {pr.finish_at()}")
        elif st == "FINISH":
            self.left.setText("печать завершена")
        else:
            self.left.setText("")
        self.prog.set(pr.percent / 100 if printing or st == "FINISH" else 0)
        self.prog.setVisible(printing or st == "FINISH")
        cur, total = pr.layers()
        self.layer.setText(f"слой {cur} из {total}" if printing and total else "")
        n, nt, b, bt = pr.temps()
        parts = []
        if n is not None:
            parts.append(f"сопло {n}°" + (f" → {nt}°" if nt and abs(nt - n) > 2 else ""))
        if b is not None:
            parts.append(f"стол {b}°" + (f" → {bt}°" if bt and abs(bt - b) > 2 else ""))
        self.temps.setText("   ".join(parts) if pr.connected else "")
        self.err.setText(pr.error if not pr.connected else "")


def card_printer(home):
    c = card()
    cl = _card_layout(c, 14, 6)
    pr = home.app.printer
    cl.addWidget(ClickableRow("", pr.cfg()["name"] if pr.configured else "3D-принтер",
                              lambda: home.app.open_page("printer")))
    job = label("", None)
    pct = label("", "h1")
    pct.setFont(W.T.tabular(26))
    left = label("", "muted", wrap=True)
    prog = ProgressLine()
    cl.addWidget(job)
    cl.addWidget(pct)
    cl.addWidget(prog)
    cl.addWidget(left)
    cl.addStretch(1)

    def refresh():
        st = pr.gcode_state
        printing = pr.connected and st in ("RUNNING", "PAUSE", "PREPARE", "SLICING")
        if not pr.configured:
            job.setText("Не подключён")
            left.setText("Нажмите, чтобы ввести IP и код доступа")
        elif not pr.connected:
            job.setText("Нет связи")
            left.setText(pr.error or "Подключение…")
        else:
            _elide(job, pr.job or devices.STATES.get(st, "Ожидание"), 190)
            left.setText((f"осталось {devices.fmt_minutes(pr.remaining)} · до {pr.finish_at()}" if pr.remaining
                          else "") if printing else devices.STATES.get(st, ""))
        pct.setText(f"{pr.percent}%" if printing or st == "FINISH" else "")
        prog.setVisible(printing)
        prog.set(pr.percent / 100)
    home.refreshers.append(refresh)
    home.listen(pr.changed, refresh)
    return c


# ================================================================ DeskDash


class MacroButton(QAbstractButton):
    """A DeskDash macro: glyph and label. Macros marked "confirm" need a second click."""

    def __init__(self, app, macro, compact=False):
        super().__init__()
        self.app, self.macro, self.compact = app, macro, compact
        self.armed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(macro.get("label", ""))
        self.setMinimumHeight(W.T.s(40 if compact else 58))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clicked.connect(self.run)
        self._hover = False

    def sizeHint(self):
        return QSize(W.T.s(80 if self.compact else 110), W.T.s(40 if self.compact else 58))

    def run(self):
        m = self.macro
        if m.get("confirm") and not self.armed:
            self.armed = True
            self.update()
            QTimer.singleShot(3000, self._disarm)
            return
        self.armed = False
        self.update()

        def done(ok, err):
            if ok:
                self.app.alert(devices.DESKDASH_GLYPHS.get(m.get("icon"), ""), m.get("label", ""), "выполнено",
                               ttl=1.4)
            else:
                self.app.alert("", "DeskDash: " + m.get("label", ""), err[:80], ttl=4)
        self.app.deskdash.run_macro(m["id"], done)

    def _disarm(self):
        self.armed = False
        self.update()

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
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(T.col("inv_bg") if self.armed else T.col("active" if self.isDown() else
                                                             ("hover" if self._hover else "surface")))
        p.drawRoundedRect(r, T.r(14), T.r(14))
        fg = T.col("inv_fg") if self.armed else T.col("fg")
        glyph = devices.DESKDASH_GLYPHS.get(self.macro.get("icon"), "")
        text = "Ещё раз — выполнить" if self.armed else self.macro.get("label", "")
        p.setPen(fg)
        if self.compact:
            p.setFont(T.icon_font(13))
            p.drawText(QRectF(T.s(10), 0, T.s(18), r.height()), Qt.AlignCenter, glyph)
            p.setFont(T.font(11.5, 500))
            x = T.s(32)
        else:
            p.setFont(T.icon_font(16))
            p.drawText(QRectF(T.s(14), 0, T.s(20), r.height()), Qt.AlignCenter, glyph)
            p.setFont(T.font(12.5, 600))
            x = T.s(44)
        fm = p.fontMetrics()
        p.drawText(QRectF(x, 0, r.width() - x - T.s(8), r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(text, Qt.ElideRight, int(r.width() - x - T.s(8))))


DEVICE_GLYPHS = {"Computer": "", "Smartphone": "", "Tablet": "", "Speaker": ""}


class DeskDashPage(Page):
    key, glyph, title, height = "deskdash", "", "DeskDash", 330
    COLS = 4

    def build(self):
        T = self.T
        dd = self.app.deskdash
        self.status = label("", "faint")
        self.b_open = button("Открыть DeskDash", "ghost", self._open)
        head = hbox(self.status, "stretch", IconButton("", "Обновить", 28, 12, lambda _=False: dd.poll()),
                    self.b_open, spacing=6)
        self.grid_w = QWidget()
        self.grid = QGridLayout(self.grid_w)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(T.s(6))
        self.msg = label("", "muted", wrap=True)
        self.msg.setAlignment(Qt.AlignCenter)
        # Spotify Connect
        self.sp = QWidget()
        spl = QVBoxLayout(self.sp)
        spl.setContentsMargins(0, T.s(6), 0, 0)
        spl.setSpacing(T.s(6))
        self.sp_now = label("", "muted")
        self.sp_prev = IconButton("", "Назад", 30, 12, lambda _=False: dd.spotify_cmd("prev"))
        self.sp_play = IconButton("", "Пауза / играть", 34, 13, lambda _=False: self._play_pause(), filled=True)
        self.sp_next = IconButton("", "Вперёд", 30, 12, lambda _=False: dd.spotify_cmd("next"))
        spl.addWidget(hbox(label("", "icon"), label("Spotify", "h2"), self.sp_now, "stretch", self.sp_prev,
                           self.sp_play, self.sp_next, spacing=8))
        self.dev_row = QWidget()
        self.dev_lay = QGridLayout(self.dev_row)
        self.dev_lay.setContentsMargins(0, 0, 0, 0)
        self.dev_lay.setSpacing(T.s(6))
        spl.addWidget(self.dev_row)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.s(8))
        lay.addWidget(head)
        lay.addWidget(self.grid_w)
        lay.addWidget(self.msg, 1)
        lay.addWidget(self.sp)
        lay.addStretch(0)
        self._sig = None
        self._dev_sig = None
        self.listen(dd.changed, self.refresh)
        self.listen(dd.spotify_changed, self.refresh)
        self.refresh()

    def page_height(self):
        dd = self.app.deskdash
        rows = -(-len(dd.macros) // self.COLS) if dd.online else 0
        h = 44 + max(1, rows) * 64 + (110 if dd.online and dd.spotify else 0)
        return max(200, min(460, h))

    def on_show(self):
        self.app.deskdash.poll()

    def _open(self):
        dd = self.app.deskdash
        if dd.online and dd.show_window():
            self.app.island.collapse()
            return
        hits = self.app.index.search_apps("deskdash", 1)
        if hits:
            from . import features
            features.launch_app(hits[0][2], hits[0][3])
            QTimer.singleShot(4000, dd.poll)
        else:
            webbrowser.open(DESKDASH_URL)

    def _play_pause(self):
        dd = self.app.deskdash
        dd.spotify_cmd("pause" if dd.now.get("playing") else "play")

    def refresh(self):
        dd = self.app.deskdash
        if not dd.enabled:
            self.status.setText("Связь с DeskDash выключена")
            msg = "Включите её в Настройки → Аккаунты → DeskDash."
        elif not dd.installed:
            self.status.setText("DeskDash не найден")
            msg = ("DeskDash — дашборд для телефона на столе: часы, календарь, Spotify, кнопки-макросы для ПК.\n"
                   "Установите DeskDash.exe — его макросы и Spotify Connect появятся здесь.")
        elif not dd.online:
            self.status.setText(dd.error or "DeskDash не запущен")
            msg = "Запустите DeskDash.exe — кнопка «Открыть DeskDash» сверху."
        else:
            self.status.setText(f"Подключено к DeskDash · {dd.name}" if dd.name else "Подключено к DeskDash")
            msg = "" if dd.macros else "Макросов нет — добавьте их в DeskDash.exe → «Макросы»."
        self.msg.setText(msg)
        self.msg.setVisible(bool(msg))
        self.b_open.setText("Скачать DeskDash" if dd.enabled and not dd.installed else "Открыть DeskDash")
        sig = [(m["id"], m.get("label"), m.get("icon")) for m in dd.macros] if dd.online else []
        if sig != self._sig:
            self._sig = sig
            while self.grid.count():
                w = self.grid.takeAt(0).widget()
                if w:
                    w.hide()
                    w.deleteLater()
            if dd.online:
                for i, m in enumerate(dd.macros):
                    self.grid.addWidget(MacroButton(self.app, m), i // self.COLS, i % self.COLS)
            _relayout(self)
        self.grid_w.setVisible(bool(sig))
        show_sp = dd.online and dd.spotify
        self.sp.setVisible(show_sp)
        if show_sp:
            n = dd.now
            if n.get("title"):
                text = f"{n['title']} — {n.get('artist', '')}" + (f"  ·  {n['device']}" if n.get("device") else "")
            else:
                text = "ничего не играет"
            _elide(self.sp_now, text, 420)
            self.sp_play.setGlyph("" if n.get("playing") else "")
            dsig = [(d["id"], d.get("name"), d.get("active")) for d in dd.devices]
            if dsig != self._dev_sig:
                self._dev_sig = dsig
                while self.dev_lay.count():
                    w = self.dev_lay.takeAt(0).widget()
                    if w:
                        w.hide()
                        w.deleteLater()
                for i, d in enumerate(dd.devices[:8]):
                    t = W.Tile(DEVICE_GLYPHS.get(d.get("type"), ""), d.get("name", "?"),
                               "играет здесь" if d.get("active") else "переключить сюда")
                    t.setChecked(bool(d.get("active")))
                    t.setMinimumHeight(W.T.s(52))
                    t.clicked.connect(lambda _=False, d=d: self._transfer(d))
                    self.dev_lay.addWidget(t, i // 4, i % 4)
                _relayout(self)

    def _transfer(self, d):
        def done(ok, err):
            self.app.alert("" if ok else "", ("Spotify → " + d.get("name", "")) if ok else "Spotify",
                           "" if ok else err[:80], ttl=2 if ok else 4)
            self._dev_sig = None
            self.refresh()
        self.app.deskdash.transfer(d["id"], done)


def card_deskdash(home):
    c = card()
    cl = _card_layout(c, 12, 6)
    dd = home.app.deskdash
    cl.addWidget(ClickableRow("", "DeskDash", lambda: home.app.open_page("deskdash")))
    grid_w = QWidget()
    grid = QGridLayout(grid_w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(W.T.s(4))
    cl.addWidget(grid_w)
    hint = label("", "faint", wrap=True)
    cl.addWidget(hint)
    cl.addStretch(1)
    state = {"sig": None}

    def refresh():
        sig = [(m["id"], m.get("label"), m.get("icon")) for m in dd.macros[:6]] if dd.online else []
        hint.setText("" if sig else ("Запустите DeskDash.exe" if dd.installed else "DeskDash не установлен")
                     if not dd.online else "Макросов нет")
        if sig == state["sig"]:
            return
        state["sig"] = sig
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for i, m in enumerate(dd.macros[:6] if dd.online else []):
            grid.addWidget(MacroButton(home.app, m, compact=True), i // 2, i % 2)
    home.refreshers.append(refresh)
    home.listen(dd.changed, refresh)
    return c
