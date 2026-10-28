"""Fullscreen overlays over a frozen screenshot: region capture with markup, colour picker, region select."""
import datetime as dt
import math
import os

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QWidget

from . import config, osapi
from . import widgets as W
from .widgets import IconButton

MARK_COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#000000"]


def grab_screen_under_cursor():
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    pm = screen.grabWindow(0)
    return screen, pm


class Overlay(QWidget):
    """Base: frozen screen, dimmed, fullscreen on the screen under the cursor."""
    done = Signal(object)
    cancelled = Signal()

    def __init__(self, screen, pixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.pix = pixmap
        self.img = pixmap.toImage()
        self.dpr = pixmap.devicePixelRatio() or 1.0
        self.setGeometry(screen.geometry())
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    def present(self):
        self.show()
        self.activateWindow()
        self.raise_()
        osapi.activate(self)
        self.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancel()

    def cancel(self):
        self.cancelled.emit()
        self.close()

    def crop(self, rect):
        r = QRect(int(rect.x() * self.dpr), int(rect.y() * self.dpr), int(rect.width() * self.dpr),
                  int(rect.height() * self.dpr))
        return self.img.copy(r)


class RegionOverlay(Overlay):
    """Drag to select a region; a click without dragging selects the whole screen."""

    def __init__(self, screen, pixmap, hint=""):
        super().__init__(screen, pixmap)
        self.screen_ref = screen
        self.hint = hint
        self.origin = None
        self.sel = QRect()

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if not self.sel.isNull():
            p.drawPixmap(QRectF(self.sel), self.pix, QRectF(self.sel.x() * self.dpr, self.sel.y() * self.dpr,
                                                     self.sel.width() * self.dpr, self.sel.height() * self.dpr))
            p.setPen(QPen(QColor(255, 255, 255), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.sel.adjusted(0, 0, -1, -1))
            self._tag(p, f"{int(self.sel.width() * self.dpr)} × {int(self.sel.height() * self.dpr)}",
                      self.sel.bottomLeft() + QPoint(0, T.s(8)))
        if self.hint:
            self._tag(p, self.hint, QPoint(self.width() // 2, T.s(18)), center=True)

    def _tag(self, p, text, at, center=False):
        T = W.T
        p.setFont(T.font(12, 600))
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + T.s(20), T.s(26)
        x = at.x() - w // 2 if center else at.x()
        r = QRectF(x, at.y(), w, h)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 220))
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignCenter, text)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.cancel()
            return
        self.origin = e.position().toPoint()
        self.sel = QRect(self.origin, self.origin)

    def mouseMoveEvent(self, e):
        if self.origin is not None:
            self.sel = QRect(self.origin, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if self.origin is None:
            return
        self.origin = None
        rect = self.sel if self.sel.width() > 4 and self.sel.height() > 4 else self.rect()
        self.finish(rect)

    def finish(self, rect):
        img = self.crop(rect)
        self.done.emit(img)
        self.close()


class RecordRegionOverlay(RegionOverlay):
    """Pick the region to record: emits (screen, rect in the screen's logical coordinates)."""

    def __init__(self, screen, pixmap):
        super().__init__(screen, pixmap, "Выделите область для записи — или кликните, чтобы записать весь экран. "
                                         "Esc — отмена")

    def finish(self, rect):
        self.done.emit((self.screen_ref, QRect(rect)))
        self.close()


# ---------------------------------------------------------------- screenshot with markup


class Shape:
    def __init__(self, tool, color, width, start):
        self.tool, self.color, self.width = tool, QColor(color), width
        self.points = [start]
        self.text = ""

    def draw(self, p, base=None, dpr=1.0):
        pen = QPen(self.color, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setBrush(Qt.NoBrush)
        a, b = self.points[0], self.points[-1]
        if self.tool == "pen":
            p.setPen(pen)
            path = QPainterPath(self.points[0])
            for pt in self.points[1:]:
                path.lineTo(pt)
            p.drawPath(path)
        elif self.tool == "marker":
            c = QColor(self.color)
            c.setAlpha(90)
            p.setPen(QPen(c, self.width * 5, Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin))
            path = QPainterPath(self.points[0])
            for pt in self.points[1:]:
                path.lineTo(pt)
            p.drawPath(path)
        elif self.tool == "rect":
            p.setPen(pen)
            p.drawRoundedRect(QRectF(a, b).normalized(), 4, 4)
        elif self.tool == "arrow":
            p.setPen(pen)
            p.drawLine(a, b)
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            size = 6 + self.width * 3
            pts = [b, QPointF(b.x() - size * math.cos(ang - 0.45), b.y() - size * math.sin(ang - 0.45)),
                   QPointF(b.x() - size * math.cos(ang + 0.45), b.y() - size * math.sin(ang + 0.45))]
            p.setBrush(self.color)
            p.drawPolygon(QPolygonF(pts))
        elif self.tool == "blur" and base is not None:
            r = QRectF(a, b).normalized().toRect()
            if r.width() > 2 and r.height() > 2:
                src = base.copy(QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr)))
                small = src.scaled(max(1, src.width() // 14), max(1, src.height() // 14), Qt.IgnoreAspectRatio,
                                   Qt.SmoothTransformation)
                p.drawImage(r, small.scaled(r.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation))
        elif self.tool == "text" and self.text:
            f = QFont(W.T.ui_family)
            f.setPixelSize(int(12 + self.width * 4))
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(self.color)
            p.drawText(a, self.text)


class ShotOverlay(RegionOverlay):
    """Select a region, then annotate it; Enter/Ctrl+C copies, Ctrl+S saves."""
    saved = Signal(str)

    def __init__(self, screen, pixmap, full=False):
        super().__init__(screen, pixmap, "Выделите область · Esc — отмена")
        self.shapes = []
        self.cur = None
        self.tool = "arrow"
        self.color = MARK_COLORS[0]
        self.pen = 3
        self.editing = False
        self.bar = None
        self.textedit = None
        if full:
            self.sel = self.rect()
            self._edit_mode()

    def finish(self, rect):
        self.sel = rect
        self._edit_mode()

    def _edit_mode(self):
        self.editing = True
        self.hint = ""
        self.setCursor(Qt.CrossCursor)
        T = W.T
        bar = QWidget(self)
        bar.setObjectName("bar")
        bar.setStyleSheet(f"QWidget#bar {{ background: {T.c['bg']}; border-radius: {T.s(18)}px; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(T.s(6), T.s(4), T.s(6), T.s(4))
        lay.setSpacing(T.s(2))
        self.tool_btns = {}
        for key, glyph, tip in (("arrow", "", "Стрелка"), ("rect", "", "Рамка"), ("pen", "", "Карандаш"),
                                ("marker", "", "Маркер"), ("text", "", "Текст"),
                                ("blur", "", "Скрыть (пиксели)")):
            b = IconButton(glyph, tip, 32, 14, checkable=True)
            b.setChecked(key == self.tool)
            b.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            self.tool_btns[key] = b
            lay.addWidget(b)
        lay.addSpacing(T.s(6))
        for c in MARK_COLORS:
            sw = W.Swatch(c, 20)
            sw.clicked.connect(lambda _=False, c=c: self._set_color(c))
            lay.addWidget(sw)
        lay.addSpacing(T.s(6))
        lay.addWidget(IconButton("", "Отменить (Ctrl+Z)", 32, 14, self._undo))
        lay.addWidget(IconButton("", "Сохранить (Ctrl+S)", 32, 14, self.save))
        lay.addWidget(IconButton("", "Копировать (Enter)", 32, 14, self.copy, filled=True))
        lay.addWidget(IconButton("", "Закрыть (Esc)", 32, 14, self.cancel))
        bar.adjustSize()
        x = max(T.s(8), min(self.sel.center().x() - bar.width() // 2, self.width() - bar.width() - T.s(8)))
        y = self.sel.bottom() + T.s(10)
        if y + bar.height() > self.height() - T.s(8):
            y = max(T.s(8), self.sel.top() - bar.height() - T.s(10))
            if y < T.s(8) or self.sel.height() > self.height() - 2 * bar.height():
                y = self.sel.bottom() - bar.height() - T.s(12)
        bar.move(x, y)
        bar.show()
        self.bar = bar
        self.update()

    def _set_tool(self, key):
        self.tool = key
        for k, b in self.tool_btns.items():
            b.setChecked(k == key)

    def _set_color(self, c):
        self.color = c

    def _undo(self):
        if self.shapes:
            self.shapes.pop()
            self.update()

    def paintEvent(self, e):
        if not self.editing:
            return super().paintEvent(e)
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        p.drawPixmap(QRectF(self.sel), self.pix, QRectF(self.sel.x() * self.dpr, self.sel.y() * self.dpr,
                                                 self.sel.width() * self.dpr, self.sel.height() * self.dpr))
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipRect(self.sel)
        for s in self.shapes + ([self.cur] if self.cur else []):
            s.draw(p, self.img, self.dpr)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.sel.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e):
        if not self.editing:
            return super().mousePressEvent(e)
        if e.button() == Qt.RightButton:
            self.cancel()
            return
        pt = e.position()
        if self.tool == "text":
            self._text_at(pt.toPoint())
            return
        self.cur = Shape(self.tool, self.color, self.pen, pt)

    def mouseMoveEvent(self, e):
        if not self.editing:
            return super().mouseMoveEvent(e)
        if self.cur:
            pt = e.position()
            if self.cur.tool in ("pen", "marker"):
                self.cur.points.append(pt)
            else:
                self.cur.points[1:] = [pt]
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.editing:
            return super().mouseReleaseEvent(e)
        if self.cur and len(self.cur.points) > 1:
            self.shapes.append(self.cur)
        self.cur = None
        self.update()

    def _text_at(self, pt):
        if self.textedit:
            self._commit_text()
        ed = QLineEdit(self)
        ed.setStyleSheet(f"background: rgba(0,0,0,160); color: {self.color}; border: 1px solid {self.color};"
                         f"border-radius: 6px; padding: 2px 6px; font-size: {int(12 + self.pen * 4)}px;")
        ed.move(pt - QPoint(4, int(12 + self.pen * 4)))
        ed.resize(W.T.s(240), int(22 + self.pen * 4))
        ed.show()
        ed.setFocus()
        ed.returnPressed.connect(self._commit_text)
        self.textedit = ed
        self._text_pos = QPointF(pt)

    def _commit_text(self):
        ed = self.textedit
        self.textedit = None
        if ed and ed.text().strip():
            s = Shape("text", self.color, self.pen, self._text_pos)
            s.text = ed.text()
            self.shapes.append(s)
        if ed:
            ed.deleteLater()
        self.setFocus()
        self.update()

    def render_image(self):
        if self.textedit:
            self._commit_text()
        out = self.crop(self.sel)
        out.setDevicePixelRatio(1.0)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.dpr, self.dpr)
        p.translate(-self.sel.x(), -self.sel.y())
        for s in self.shapes:
            s.draw(p, self.img, self.dpr)
        p.end()
        return out

    def copy(self):
        img = self.render_image()
        QApplication.clipboard().setImage(img)
        self.done.emit(("copied", img))
        self.close()

    def save(self):
        img = self.render_image()
        os.makedirs(config.SHOTS_DIR, exist_ok=True)
        path = os.path.join(config.SHOTS_DIR, dt.datetime.now().strftime("Island %Y-%m-%d %H-%M-%S.png"))
        img.save(path, "PNG")
        QApplication.clipboard().setImage(img)
        self.done.emit(("saved", path))
        self.close()

    def keyPressEvent(self, e):
        if self.editing and self.textedit is None:
            ctrl = e.modifiers() & Qt.ControlModifier
            if e.key() in (Qt.Key_Return, Qt.Key_Enter) or (ctrl and e.key() == Qt.Key_C):
                self.copy()
                return
            if ctrl and e.key() == Qt.Key_S:
                self.save()
                return
            if ctrl and e.key() == Qt.Key_Z:
                self._undo()
                return
            keys = {Qt.Key_A: "arrow", Qt.Key_R: "rect", Qt.Key_P: "pen", Qt.Key_M: "marker", Qt.Key_T: "text",
                    Qt.Key_B: "blur"}
            if e.key() in keys:
                self._set_tool(keys[e.key()])
                return
        if e.key() == Qt.Key_Escape and self.textedit:
            self.textedit.deleteLater()
            self.textedit = None
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- colour picker


class PickerOverlay(Overlay):
    def __init__(self, screen, pixmap):
        super().__init__(screen, pixmap)
        self.pos = self.mapFromGlobal(QCursor.pos())
        self.setCursor(Qt.BlankCursor)

    def color_at(self, pt):
        x = max(0, min(self.img.width() - 1, int(pt.x() * self.dpr)))
        y = max(0, min(self.img.height() - 1, int(pt.y() * self.dpr)))
        return QColor(self.img.pixel(x, y))

    def paintEvent(self, _e):
        T = W.T
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        cx, cy = int(self.pos.x() * self.dpr), int(self.pos.y() * self.dpr)
        n, z = 11, T.s(11)
        src = self.img.copy(cx - n // 2, cy - n // 2, n, n)
        size = n * z
        ox = self.pos.x() + T.s(24)
        oy = self.pos.y() + T.s(24)
        if ox + size > self.width():
            ox = self.pos.x() - T.s(24) - size
        if oy + size + T.s(40) > self.height():
            oy = self.pos.y() - T.s(24) - size - T.s(40)
        lens = QRectF(ox, oy, size, size)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(lens, T.s(14), T.s(14))
        p.save()
        p.setClipPath(path)
        p.drawImage(lens, src.scaled(size, size, Qt.IgnoreAspectRatio, Qt.FastTransformation))
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawRoundedRect(lens, T.s(14), T.s(14))
        mid = QRectF(ox + (n // 2) * z, oy + (n // 2) * z, z, z)
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.drawRect(mid.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawRect(mid)
        col = self.color_at(self.pos)
        tag = QRectF(ox, oy + size + T.s(8), size, T.s(30))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 230))
        p.drawRoundedRect(tag, T.s(15), T.s(15))
        p.setBrush(col)
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.drawEllipse(QRectF(tag.left() + T.s(8), tag.top() + T.s(7), T.s(16), T.s(16)))
        p.setPen(QColor(255, 255, 255))
        p.setFont(T.tabular(12.5))
        p.drawText(tag.adjusted(T.s(30), 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, col.name().upper())
        # crosshair cursor
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawEllipse(QPointF(self.pos), T.s(5), T.s(5))

    def mouseMoveEvent(self, e):
        self.pos = e.position().toPoint()
        self.update()

    def keyPressEvent(self, e):
        d = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}.get(e.key())
        if d:
            QCursor.setPos(self.mapToGlobal(self.pos + QPoint(*d)))
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.pick()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.cancel()
            return
        self.pos = e.position().toPoint()
        self.pick()

    def pick(self):
        self.done.emit(self.color_at(self.pos).name().upper())
        self.close()
