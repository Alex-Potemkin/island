"""The island window: a transparent, topmost, non-activating window that paints the pill
and hosts the panel. All shape changes run through a small spring simulation."""
import math
import os
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QComboBox, QGraphicsOpacityEffect, QHBoxLayout,
                               QLineEdit, QMenu, QPlainTextEdit, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from . import layout, osapi, pages
from .widgets import IconButton, TabButton

HOTKEY_IDS = {}


class Spring:
    """Critically-ish damped spring; slightly underdamped for a liquid feel."""

    def __init__(self, value, stiffness=420.0, damping=0.78):
        self.v = float(value)
        self.target = float(value)
        self.vel = 0.0
        self.k = stiffness
        self.c = 2 * math.sqrt(stiffness) * damping

    def step(self, dt):
        a = self.k * (self.target - self.v) - self.c * self.vel
        self.vel += a * dt
        self.v += self.vel * dt

    def settled(self):
        return abs(self.target - self.v) < 0.25 and abs(self.vel) < 2

    def snap(self):
        self.v, self.vel = self.target, 0.0


INPUT_TYPES = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox)


class Panel(QWidget):
    """Tab bar + pages. Tabs follow the user's layout; pages that aren't tabs (welcome, command bar,
    a tab the user switched off but opened from the command bar) are created on first use."""

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        app = island.app
        T = app.theme
        self.setObjectName("panel")
        self.pages = {}
        self.stack = QStackedWidget()
        self.tabs = {}
        self.tab_keys = layout.current(app.settings)["tabs"] + ["settings"]
        tabrow = QHBoxLayout()
        tabrow.setContentsMargins(0, 0, 0, 0)
        tabrow.setSpacing(T.s(2))
        for key in self.tab_keys:
            if key == "settings":
                tabrow.addStretch(1)
            glyph, title = (layout.TABS[key][:2] if key in layout.TABS else ("\uE713", "Настройки"))
            b = TabButton(glyph, title)
            b.clicked.connect(lambda _=False, k=key: island.show_page(k))
            self.tabs[key] = b
            tabrow.addWidget(b)
        self.pin = IconButton("\uE718", "Не сворачивать", 30, 13, checkable=True)
        tabrow.addWidget(self.pin)
        self.tabbar = QWidget()
        self.tabbar.setLayout(tabrow)
        lay = QVBoxLayout(self)
        m = T.s(16)
        lay.setContentsMargins(m, T.s(12), m, m)
        lay.setSpacing(T.s(10))
        lay.addWidget(self.tabbar)
        lay.addWidget(self.stack, 1)
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        self.current = None
        self.command = self.page("command")

    def page(self, key):
        """The page for key, created on first use."""
        if key not in self.pages:
            from . import settings_ui
            cls = (pages.PAGES.get(key) or {"settings": settings_ui.SettingsPage, "welcome": settings_ui.WelcomePage,
                                            "command": pages.CommandPage}[key])
            self.pages[key] = cls(self.island.app)
            self.stack.addWidget(self.pages[key])
        return self.pages[key]

    def teardown(self):
        for p in self.pages.values():
            p.teardown()

    def select(self, key):
        page = self.page(key)
        if self.current is not page:
            if self.current:
                self.current.on_hide()
            self.current = page
            self.stack.setCurrentWidget(page)
        for k, b in self.tabs.items():
            b.setChecked(k == key)
        self.tabbar.setVisible(key not in ("command", "welcome"))
        page.on_show()

    def content_height(self):
        T = self.island.app.theme
        h = T.s(self.current.page_height()) + T.s(12) + T.s(16)
        if self.tabbar.isVisibleTo(self):
            h += T.s(34) + T.s(10)
        return h


class Island(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.app = app
        self.T = app.theme          # panel
        self.P = app.pill_theme     # the pill: UI scale × pill size
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setWindowTitle("Island")
        self.mode = "idle"            # idle | hidden | live | expanded
        self.page_key = "home"
        self.input_mode = False
        self.dragging = False
        self.prev_hwnd = 0
        self._hold = 0
        self._hover_since = None
        self._leave_since = None
        self._suppress_until = 0.0
        self._fs_hidden = False
        self._last_live = None
        self._art_cache = (None, None)
        self._fg_tick = 0
        T = self.T
        self.pad = T.s(34)
        self.max_w = T.s(760)
        self.max_h = T.s(600)
        self.resize(self.max_w + 2 * self.pad, self.max_h + self.pad)
        k = T.stiffness or 420.0
        self.springs = {"w": Spring(T.s(124), k), "h": Spring(T.s(30), k), "y": Spring(self._top(), k),
                        "r": Spring(T.s(15), k), "o": Spring(0, 260, 1.0)}
        self.panel = Panel(self)
        self.panel.hide()
        self._anim = QTimer(self, interval=15, timeout=self._step)
        self._last_t = time.monotonic()
        self._live_anim = QTimer(self, interval=33, timeout=self.update)
        self._poll = QTimer(self, interval=40, timeout=self._poll_cursor)
        self._poll.start()
        self._clock = QTimer(self, interval=5000, timeout=self._clock_tick)
        self._clock.start()
        self.place()
        self.retarget(snap=True)

    # ------------------------------------------------------------ geometry

    def _top(self):
        return float(self.T.s(self.app.settings.get("top_offset", 6)))

    def screen_obj(self):
        screens = QApplication.screens()
        idx = int(self.app.settings.get("screen", 0) or 0)
        return screens[idx] if 0 < idx < len(screens) else QApplication.primaryScreen()

    def place(self):
        g = self.screen_obj().geometry()
        margin = self.T.s(12)
        x = {"left": g.x() + margin - self.pad,
             "right": g.x() + g.width() - self.width() + self.pad - margin}.get(
            self.T.position, g.x() + (g.width() - self.width()) // 2)
        self.move(x, g.y())

    def _x(self, w):
        """Left edge of a shape of width w inside the window, for the chosen position."""
        if self.T.position == "left":
            return float(self.pad)
        if self.T.position == "right":
            return self.width() - self.pad - w
        return (self.width() - w) / 2

    def shape_rect(self):
        s = self.springs
        w, h = max(s["w"].v, 4), max(s["h"].v, 2)
        return QRectF(self._x(w), s["y"].v, w, h)

    def target_rect(self):
        s = self.springs
        return QRectF(self._x(s["w"].target), s["y"].target, s["w"].target, s["h"].target)

    def retarget(self, snap=False):
        T, P = self.T, self.P
        s = self.springs
        snap = snap or not T.stiffness          # "Без анимации"
        live = self.app.current_live()
        if self.mode == "expanded":
            w, h = self.max_w, min(self.panel.content_height(), self.max_h)
            r, y, o = max(T.s(8), T.r(30)), self._top(), 1.0
        elif self.mode == "live" and live:
            w, h = self.live_width(live), P.s(36)
            r, y, o = h / 2, self._top(), 0.0
        elif self.mode == "hidden":
            w, h, r, y, o = P.s(120), T.s(5), T.s(2.5), 0.0, 0.0
        else:
            w, h, r, y, o = self.idle_width(), P.s(30), P.s(15), self._top(), 0.0
        s["w"].target, s["h"].target, s["r"].target, s["y"].target, s["o"].target = w, h, r, y, o
        if self.mode == "expanded":
            tr = self.target_rect()
            self.panel.setGeometry(tr.toRect())
            if not self.panel.isVisible():
                self.panel.show()
        if snap:
            for sp in s.values():
                sp.snap()
            self._apply_panel_opacity()
            self._update_mask()
            self.update()
        elif not self._anim.isActive():
            self._last_t = time.monotonic()
            self._anim.start()
        self._update_live_anim()

    def relayout(self):
        if self.mode == "expanded":
            self.retarget()

    def _step(self):
        now = time.monotonic()
        dt = min(0.033, now - self._last_t)
        self._last_t = now
        # integrate in small sub-steps so a stiff spring stays stable
        for _ in range(3):
            for sp in self.springs.values():
                sp.step(dt / 3)
        self._apply_panel_opacity()
        self._update_mask()
        self.update()
        if all(sp.settled() for sp in self.springs.values()):
            for sp in self.springs.values():
                sp.snap()
            self._apply_panel_opacity()
            self._anim.stop()
            if self.mode != "expanded":
                self.panel.hide()
            self.update()

    def _update_mask(self):
        """Windows passes clicks through transparent pixels by itself; X11 needs an explicit shape."""
        if osapi.IS_WINDOWS:
            return
        r = self.shape_rect().adjusted(-1, -1, 1, 1)
        rad = min(self.springs["r"].v, r.height() / 2) + 1
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _apply_panel_opacity(self):
        if self.mode == "expanded":
            # content fades in once the shape is mostly open
            tr = self.target_rect()
            cur = self.shape_rect()
            k = min(cur.width() / max(tr.width(), 1), cur.height() / max(tr.height(), 1))
            self.panel.effect.setOpacity(max(0.0, min(1.0, (k - 0.72) / 0.24)))
        else:
            self.panel.effect.setOpacity(0.0)

    # ------------------------------------------------------------ painting

    def paintEvent(self, _e):
        T = self.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._fs_hidden:
            return
        r = self.shape_rect()
        rad = min(self.springs["r"].v, r.height() / 2)
        big = r.height() > T.s(60)
        # soft shadow
        if (big or not T.dark) and T.shadow and osapi.IS_WINDOWS:
            layers = 10 if big else 4
            base = T.c["shadow"] if big else T.c["shadow"] // 3
            for i in range(layers, 0, -1):
                grow = T.s(2.6) * i
                c = QColor(0, 0, 0, int(base * (1 - i / (layers + 1)) ** 2 / 3))
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r.adjusted(-grow, -grow * 0.4, grow, grow * 1.1), rad + grow, rad + grow)
        p.setBrush(T.bg())
        p.setPen(QPen(T.col("border"), 1) if not T.dark else Qt.NoPen)
        p.drawRoundedRect(r, rad, rad)
        if self.mode in ("live", "idle") and r.height() > T.s(20):
            self.paint_live(p, r)

    def _clock_tick(self):
        if self.mode == "idle" and self.idle_text():
            if abs(self.springs["w"].target - self.idle_width()) > 1:
                self.retarget()
            self.update()

    def _content_alpha(self, r):
        tr = self.target_rect()
        if self.mode == "expanded":
            return 0.0
        dw = abs(r.width() - tr.width()) / max(tr.width(), 1)
        return max(0.0, min(1.0, 1 - dw * 6))

    def live_width(self, live):
        T = self.P
        h = T.s(36)
        fm = QFontMetrics(T.font(12.5, 600))
        dots = self._dots_width()
        kind = live["kind"]
        if kind == "music":
            text = min(fm.horizontalAdvance(live.get("title", "")), T.s(200))
            return h + T.s(10) + text + T.s(14) + T.s(30) + T.s(14) + dots
        if kind == "timer":
            return T.s(196) + dots
        text = fm.horizontalAdvance(live.get("title", ""))
        sub = QFontMetrics(T.font(12)).horizontalAdvance(live.get("sub", "")) if live.get("sub") else 0
        right = T.s(30) if kind in ("claude",) else 0
        w = T.s(14) + T.s(22) + T.s(10) + text + (T.s(8) + sub if sub else 0) + right + T.s(18) + dots
        return max(T.s(150), min(T.s(480), w))

    def idle_text(self):
        idle = layout.current(self.app.settings)["idle"]
        now = time.localtime()
        if idle == "clock":
            return time.strftime("%H:%M", now)
        if idle == "date":
            days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            return f"{days[now.tm_wday]}, {now.tm_mday}  ·  {time.strftime('%H:%M', now)}"
        return ""

    def idle_width(self):
        text = self.idle_text()
        if not text:
            return self.P.s(124)
        return max(self.P.s(124), QFontMetrics(self.P.tabular(13)).horizontalAdvance(text) + self.P.s(44))

    def _dots_width(self):
        st = self.app.privacy
        n = (1 if st[0] else 0) + (1 if st[1] else 0)
        return self.P.s(8 + 12 * n) if n else 0

    def paint_live(self, p, r):
        T = self.P
        live = self.app.current_live()
        alpha = self._content_alpha(r)
        mic, cam = self.app.privacy
        if alpha <= 0.02:
            return
        p.save()
        p.setOpacity(alpha)
        clip = QPainterPath()
        clip.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipPath(clip)
        pad = T.s(8)
        right = r.right() - pad
        # privacy dots (always on the right edge)
        for on, col in ((cam, "cam"), (mic, "warn")):
            if on:
                d = T.s(7)
                p.setPen(Qt.NoPen)
                p.setBrush(T.col(col))
                p.drawEllipse(QRectF(right - d - T.s(3), r.center().y() - d / 2, d, d))
                right -= T.s(12)
        if live and self.mode == "live":
            getattr(self, "_live_" + live["kind"], self._live_alert)(p, r, live, right)
        elif self.mode == "idle":
            text = self.idle_text()
            if text:
                p.setFont(T.tabular(13))
                p.setPen(T.col("fg"))
                p.drawText(r, Qt.AlignCenter, text)
        p.restore()

    def _icon(self, p, rect, glyph, color="fg", px=15):
        p.setFont(self.P.icon_font(px))
        p.setPen(self.P.col(color))
        p.drawText(rect, Qt.AlignCenter, glyph)

    def _live_music(self, p, r, live, right):
        T = self.P
        size = r.height() - T.s(10)
        art = QRectF(r.left() + T.s(5), r.top() + T.s(5), size, size)
        pm = self._art_pixmap(live.get("art"))
        if pm:
            path = QPainterPath()
            path.addRoundedRect(art, T.s(9), T.s(9))
            p.save()
            p.setClipPath(path, Qt.IntersectClip)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(art.toRect(), pm)
            p.restore()
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(T.col("raised"))
            p.drawRoundedRect(art, T.s(9), T.s(9))
            self._icon(p, art, "", "fg2", 12)
        # equalizer
        bars, bw, gap = 4, T.s(3), T.s(3)
        ex = right - T.s(6) - bars * bw - (bars - 1) * gap
        playing = live.get("playing")
        t = time.monotonic()
        p.setPen(Qt.NoPen)
        p.setBrush(T.col("fg"))
        for i in range(bars):
            if playing:
                v = 0.35 + 0.65 * abs(math.sin(t * (2.6 + i * 0.9) + i * 1.7)) * (0.6 + 0.4 * math.sin(t * 1.3 + i))
            else:
                v = 0.18
            bh = max(T.s(3), (r.height() - T.s(18)) * v)
            p.drawRoundedRect(QRectF(ex + i * (bw + gap), r.center().y() - bh / 2, bw, bh), bw / 2, bw / 2)
        # title
        x = art.right() + T.s(10)
        p.setFont(T.font(12.5, 600))
        p.setPen(T.col("fg"))
        fm = p.fontMetrics()
        avail = ex - T.s(12) - x
        p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(live.get("title", ""), Qt.ElideRight, int(avail)))

    def _art_pixmap(self, data):
        if not data:
            return None
        if self._art_cache[0] is not data:
            pm = QPixmap()
            pm.loadFromData(data)
            self._art_cache = (data, pm if not pm.isNull() else None)
        return self._art_cache[1]

    def _live_timer(self, p, r, live, right):
        T = self.P
        ring = QRectF(r.left() + T.s(8), r.top() + T.s(7), r.height() - T.s(14), r.height() - T.s(14))
        p.setPen(QPen(T.col("active"), T.s(2.5)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(ring)
        prog = live.get("progress", 0)
        if prog:
            p.setPen(QPen(T.col("fg"), T.s(2.5), Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-360 * 16 * (1 - prog)))
        if not live.get("running"):
            self._icon(p, ring, "", "fg2", 9)
        x = ring.right() + T.s(10)
        p.setFont(T.font(12, 500))
        p.setPen(T.col("fg2"))
        p.drawText(QRectF(x, r.top(), T.s(90), r.height()), Qt.AlignVCenter | Qt.AlignLeft, live.get("title", ""))
        p.setFont(T.tabular(15))
        p.setPen(T.col("fg"))
        p.drawText(QRectF(r.left(), r.top(), right - T.s(8) - r.left(), r.height()),
                   Qt.AlignVCenter | Qt.AlignRight, live.get("value", ""))

    def _live_alert(self, p, r, live, right):
        T = self.P
        x = r.left() + T.s(14)
        icon = QRectF(x, r.top(), T.s(22), r.height())
        if live.get("swatch"):
            d = T.s(18)
            p.setPen(QPen(T.col("line"), 1))
            p.setBrush(QColor(live["swatch"]))
            p.drawRoundedRect(QRectF(x + T.s(2), r.center().y() - d / 2, d, d), T.s(6), T.s(6))
        elif live.get("glyph"):
            self._icon(p, icon, live["glyph"], "fg", 14)
        x = icon.right() + T.s(10)
        if live["kind"] == "claude":
            right -= T.s(30)
            c = QRectF(right + T.s(8), r.center().y() - T.s(8), T.s(16), T.s(16))
            if live.get("busy"):
                a = (time.monotonic() * 360) % 360
                p.setPen(QPen(T.col("active"), T.s(2)))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c)
                p.setPen(QPen(T.col("fg"), T.s(2), Qt.SolidLine, Qt.RoundCap))
                p.drawArc(c, int(-a * 16), 90 * 16)
            else:
                self._icon(p, c, live.get("state_glyph", ""), "fg", 12)
        p.setFont(T.font(12.5, 600))
        p.setPen(T.col("fg"))
        fm = p.fontMetrics()
        avail = right - T.s(8) - x
        title = fm.elidedText(live.get("title", ""), Qt.ElideRight, int(avail))
        p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignVCenter | Qt.AlignLeft, title)
        if live.get("sub"):
            x2 = x + fm.horizontalAdvance(title) + T.s(8)
            p.setFont(T.font(12))
            p.setPen(T.col("fg2"))
            fm2 = p.fontMetrics()
            avail2 = right - T.s(8) - x2
            if avail2 > T.s(20):
                p.drawText(QRectF(x2, r.top(), avail2, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                           fm2.elidedText(live["sub"], Qt.ElideRight, int(avail2)))

    _live_claude = _live_alert
    _live_download = _live_alert
    _live_privacy = _live_alert

    def _update_live_anim(self):
        live = self.app.current_live()
        animated = self.mode == "live" and live and (
            (live["kind"] == "music" and live.get("playing")) or (live["kind"] == "claude" and live.get("busy")))
        if animated and not self._live_anim.isActive():
            self._live_anim.start()
        elif not animated and self._live_anim.isActive():
            self._live_anim.stop()

    # ------------------------------------------------------------ state changes

    def resting_mode(self):
        if self.app.current_live():
            return "live"
        if any(self.app.privacy):
            return "live"
        return "hidden" if self.app.settings.get("autohide") else "idle"

    def live_changed(self):
        if self.mode != "expanded":
            new = self.resting_mode()
            live = self.app.current_live()
            sig = (new, live and live["kind"], live and live.get("title"), live and live.get("sub"),
                   live and live.get("value"), live and live.get("playing"), live and live.get("busy"))
            if sig != self._last_live:
                self._last_live = sig
                self.mode = new
                self.retarget()
            else:
                self.update()
        self._update_live_anim()

    def show_page(self, key):
        self.page_key = key if key != "command" else self.page_key
        self.panel.select(key)
        self.retarget()

    def expand(self, key=None, focus=False):
        if self._fs_hidden:
            self.set_fullscreen_hidden(False, force=True)
        key = key or self.page_key
        self.mode = "expanded"
        self._leave_since = None
        self.panel.select(key)
        if key != "command":
            self.page_key = key
        self.retarget()
        if focus:
            target = self.panel.current.focus_target()
            if target:
                QTimer.singleShot(10, lambda: self.focus_input(target))

    def collapse(self, restore_focus=False):
        if self.mode != "expanded":
            return
        if self.panel.pin.isChecked() and not restore_focus:
            return
        if self.panel.current:
            self.panel.current.on_hide()
        if self.input_mode:
            self.input_mode = False
            osapi.noactivate(self, True)
            if restore_focus and osapi.is_window(self.prev_hwnd):
                osapi.focus_window(self.prev_hwnd)
        self.mode = self.resting_mode()
        self._last_live = None
        self._hover_since = None
        self._suppress_until = time.monotonic() + 0.35
        self.retarget()

    def toggle(self):
        if self.mode == "expanded":
            self.collapse(restore_focus=True)
        else:
            self.expand()

    def hold(self, on):
        self._hold += 1 if on else -1
        self._hold = max(0, self._hold)

    def focus_input(self, widget=None):
        """Let the panel take keyboard focus (only while the user is typing in it)."""
        if not self.input_mode:
            fg = osapi.foreground()
            if fg and not osapi.is_own(fg):
                self.prev_hwnd = fg
            self.input_mode = True
            osapi.noactivate(self, False)
        self.activateWindow()
        osapi.activate(self)
        if widget is not None:
            widget.setFocus(Qt.MouseFocusReason)

    def set_fullscreen_hidden(self, hidden, force=False):
        if hidden and self.mode == "expanded" and not force:
            return
        self._fs_hidden = hidden
        self.setVisible(not hidden)
        if not hidden:
            self.ensure_topmost()

    def ensure_topmost(self):
        osapi.ensure_topmost(self)

    # ------------------------------------------------------------ hover logic

    def _global_shape(self):
        r = self.shape_rect().toRect()
        return QRect(self.mapToGlobal(r.topLeft()), r.size())

    def _poll_cursor(self):
        if self._fs_hidden or not self.isVisible() or self.app.demo:
            return
        self._fg_tick += 1
        if osapi.IS_WINDOWS or self._fg_tick % 12 == 0:  # on Linux this spawns xdotool, so poll slower
            fg = osapi.foreground()
            if fg and not osapi.is_own(fg):
                self.prev_hwnd = fg
        pos = QCursor.pos()
        T = self.T
        shape = self._global_shape()
        now = time.monotonic()
        if self.mode == "expanded":
            zone = shape.adjusted(-T.s(16), -T.s(40), T.s(16), T.s(18))
            busy = (self._hold or self.dragging or self.input_mode or self.panel.pin.isChecked()
                    or self.panel.current.key == "welcome"
                    or QApplication.activePopupWidget() is not None
                    or QApplication.mouseButtons() != Qt.NoButton)
            if zone.contains(pos) or busy:
                self._leave_since = None
            elif self._leave_since is None:
                self._leave_since = now
            elif (now - self._leave_since) * 1000 >= self.app.settings.get("collapse_delay_ms", 450):
                self.collapse()
            return
        top = self.mapToGlobal(QPoint(0, 0)).y()
        zone = QRect(shape.left() - T.s(10), top, shape.width() + T.s(20), shape.bottom() - top + T.s(6))
        if self.mode == "hidden":
            zone = QRect(shape.left() - T.s(40), top, shape.width() + T.s(80), T.s(8))
        if zone.contains(pos) and now > self._suppress_until and QApplication.mouseButtons() == Qt.NoButton:
            if not self.app.settings.get("expand_on_hover", True):
                if self.mode == "hidden":
                    self.mode = "idle"
                    self.retarget()
                return
            if self._hover_since is None:
                self._hover_since = now
            elif (now - self._hover_since) * 1000 >= self.app.settings.get("hover_delay_ms", 140):
                self._hover_since = None
                self.expand(self._page_for_live())
        else:
            self._hover_since = None
            if self.mode == "idle" and self.app.settings.get("autohide") and not any(self.app.privacy):
                self.mode = "hidden"
                self.retarget()

    def _page_for_live(self):
        live = self.app.current_live()
        if live and live["kind"] == "timer":
            return "timer"
        if live and live["kind"] in ("music", "claude"):
            return "home"
        return None

    # ------------------------------------------------------------ input events

    def mousePressEvent(self, e):
        if not self.shape_rect().contains(e.position()):
            e.ignore()
            return
        if e.button() == Qt.RightButton:
            self.context_menu(e.globalPosition().toPoint())
            return
        if self.mode != "expanded":
            live = self.app.current_live()
            if live and live.get("action"):
                live["action"]()
                self.app.clear_alert()
            else:
                self.expand(self._page_for_live())

    def context_menu(self, pos):
        m = QMenu(self)
        m.addAction("Командная строка", self.app.open_command)
        m.addAction("Скриншот", self.app.screenshot)
        m.addAction("Настройки", lambda: self.expand("settings"))
        m.addSeparator()
        m.addAction("Скрыть остров", self.app.toggle_visible)
        m.addAction("Выход", self.app.quit)
        self.hold(True)
        m.exec(pos)
        self.hold(False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.collapse(restore_focus=True)
            return
        if e.modifiers() & Qt.ControlModifier and Qt.Key_1 <= e.key() <= Qt.Key_9:
            keys = self.panel.tab_keys
            i = e.key() - Qt.Key_1
            if i < len(keys):
                self.show_page(keys[i])
            return
        super().keyPressEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.input_mode:
            if QApplication.activePopupWidget() is None and not self._hold:
                QTimer.singleShot(0, self._deactivated)
        super().changeEvent(e)

    def _deactivated(self):
        if self.isActiveWindow() or QApplication.activePopupWidget() is not None or self._hold:
            return
        self.input_mode = False
        osapi.noactivate(self, True)
        if not self.shape_rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.collapse()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.dragging = True
            if self.mode != "expanded" or self.panel.current.key != "shelf":
                self.expand("shelf")

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.dragging = False

    def dropEvent(self, e):
        self.dragging = False
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.panel.page("shelf").add(paths)
            self.app.alert("", f"На полке: {len(paths)}", os.path.basename(paths[0]))

    # ------------------------------------------------------------ hotkeys

    def register_hotkeys(self, mapping):
        osapi.unregister_hotkeys(self, list(HOTKEY_IDS))
        HOTKEY_IDS.clear()
        ids, failed = osapi.register_hotkeys(self, mapping)
        HOTKEY_IDS.update(ids)
        return failed

    def nativeEvent(self, event_type, message):
        hid = osapi.native_hotkey(event_type, message)
        if hid is not None:
            name = HOTKEY_IDS.get(hid)
            if name:
                QTimer.singleShot(0, lambda: self.app.hotkey(name))
            return True, 0
        return False, 0

    def showEvent(self, e):
        super().showEvent(e)
        if not self.input_mode:  # on X11 letting the panel take focus re-shows the window
            osapi.noactivate(self, True)
        self.ensure_topmost()


def is_input(obj):
    return isinstance(obj, INPUT_TYPES) or (obj.parent() is not None and isinstance(obj.parent(), INPUT_TYPES))
