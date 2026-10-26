"""Small reusable widgets drawn in the monochrome style."""
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Property, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

T = None  # current Theme, set by the app before building the UI


def set_theme(theme):
    global T
    T = theme


def label(text="", role=None, wrap=False, selectable=False):
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def hbox(*items, spacing=8, margins=0):
    w = QWidget()
    lay = QHBoxLayout(w)
    _fill(lay, items, spacing, margins)
    return w


def vbox(*items, spacing=8, margins=0):
    w = QWidget()
    lay = QVBoxLayout(w)
    _fill(lay, items, spacing, margins)
    return w


def _fill(lay, items, spacing, margins):
    lay.setSpacing(T.s(spacing))
    m = T.s(margins) if isinstance(margins, (int, float)) else margins
    if isinstance(m, int):
        lay.setContentsMargins(m, m, m, m)
    else:
        lay.setContentsMargins(*[T.s(x) for x in m])
    for it in items:
        if it is None or it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(T.s(it))
        else:
            lay.addWidget(it)


def button(text, role=None, slot=None, tip=None):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if role:
        b.setProperty("role", role)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def card():
    f = QFrame()
    f.setProperty("role", "card")
    return f


def sep():
    f = QFrame()
    f.setProperty("role", "sep")
    return f


class IconButton(QAbstractButton):
    """Round glyph button (Segoe Fluent Icons) with a soft hover fill."""

    def __init__(self, glyph, tip="", size=32, icon_px=15, slot=None, checkable=False, filled=False):
        super().__init__()
        self.glyph = glyph
        self._size = size
        self._icon_px = icon_px
        self.filled = filled
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(T.s(size), T.s(size))
        if tip:
            self.setToolTip(tip)
        if slot:
            self.clicked.connect(slot)
        self._hover = False

    def setGlyph(self, glyph):
        self.glyph = glyph
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def sizeHint(self):
        return QSize(T.s(self._size), T.s(self._size))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked() or self.filled
        if on:
            p.setBrush(T.col("inv_bg"))
        elif self.isDown():
            p.setBrush(T.col("active"))
        elif self._hover:
            p.setBrush(T.col("hover"))
        else:
            p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(T.icon_font(self._icon_px))
        fg = T.col("inv_fg") if on else (T.col("fg") if self._hover else T.col("fg2"))
        if not self.isEnabled():
            fg = T.col("fg3")
        p.setPen(fg)
        p.drawText(r, Qt.AlignCenter, self.glyph)


class TabButton(QAbstractButton):
    """Panel tab: just the glyph, or glyph + title on an inverted pill when selected."""

    def __init__(self, glyph, title):
        super().__init__()
        self.glyph, self.title = glyph, title
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(title)
        self.setFixedHeight(T.s(34))
        self._hover = False
        self.toggled.connect(lambda _on: self.setFixedWidth(self.sizeHint().width()))
        self.setFixedWidth(self.sizeHint().width())

    def sizeHint(self):
        w = T.s(34)
        if self.isChecked():
            w += QFontMetrics(T.font(12.5, 600)).horizontalAdvance(self.title) + T.s(12)
        return QSize(w, T.s(34))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        if on or self._hover:
            p.setBrush(T.col("inv_bg") if on else T.col("hover"))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(T.col("inv_fg") if on else (T.col("fg") if self._hover else T.col("fg2")))
        p.setFont(T.icon_font(15))
        p.drawText(QRectF(0, 0, T.s(34), r.height()), Qt.AlignCenter, self.glyph)
        if on:
            p.setFont(T.font(12.5, 600))
            p.drawText(QRectF(T.s(30), 0, r.width() - T.s(30), r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.title)


class Toggle(QAbstractButton):
    """iOS-like switch, monochrome."""

    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(T.s(38), T.s(22))
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def getPos(self):
        return self._pos

    def setPos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, getPos, setPos)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off, on = T.col("active"), T.col("inv_bg")
        mix = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * self._pos,
                              off.greenF() + (on.greenF() - off.greenF()) * self._pos,
                              off.blueF() + (on.blueF() - off.blueF()) * self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - T.s(6)
        x = r.left() + T.s(3) + (r.width() - d - T.s(6)) * self._pos
        p.setBrush(T.col("inv_fg") if self._pos > 0.5 else T.col("fg"))
        p.drawEllipse(QRectF(x, r.top() + T.s(3), d, d))


class Tile(QAbstractButton):
    """Control-centre tile: glyph, title, state text. Checked = inverted."""

    def __init__(self, glyph, title, sub="", checkable=True):
        super().__init__()
        self.glyph, self.title, self.sub = glyph, title, sub
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(T.s(64))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = False

    def setSub(self, text):
        self.sub = text
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def sizeHint(self):
        return QSize(T.s(140), T.s(64))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(T.col("inv_bg") if on else (T.col("hover") if self._hover else T.col("surface")))
        p.drawRoundedRect(r, T.s(16), T.s(16))
        fg = T.col("inv_fg") if on else T.col("fg")
        sub = T.col("inv_fg", 150) if on else T.col("fg2")
        pad = T.s(14)
        p.setPen(fg)
        p.setFont(T.icon_font(17))
        p.drawText(QRectF(pad, 0, T.s(22), r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.glyph)
        x = pad + T.s(32)
        p.setFont(T.font(13, 600))
        p.drawText(QRectF(x, r.height() / 2 - T.s(17), r.width() - x - pad, T.s(18)),
                   Qt.AlignLeft | Qt.AlignVCenter, self.title)
        p.setPen(sub)
        p.setFont(T.font(11.5))
        p.drawText(QRectF(x, r.height() / 2 + T.s(1), r.width() - x - pad, T.s(16)),
                   Qt.AlignLeft | Qt.AlignVCenter, self.sub)


class Sparkline(QWidget):
    def __init__(self, title, unit_fmt, height=70, max_value=None):
        super().__init__()
        self.title = title
        self.fmt = unit_fmt
        self.values = []
        self.max_value = max_value
        self.setMinimumHeight(T.s(height))

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(T.col("surface"))
        p.drawRoundedRect(r, T.s(14), T.s(14))
        pad = T.s(12)
        p.setFont(T.font(11))
        p.setPen(T.col("fg2"))
        p.drawText(QRectF(pad, pad - T.s(2), r.width(), T.s(14)), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        cur = self.values[-1] if self.values else 0
        p.setFont(T.tabular(15))
        p.setPen(T.col("fg"))
        p.drawText(QRectF(0, pad - T.s(4), r.width() - pad, T.s(18)), Qt.AlignRight | Qt.AlignVCenter, self.fmt(cur))
        if len(self.values) < 2:
            return
        top = pad + T.s(20)
        area = QRectF(pad, top, r.width() - 2 * pad, r.height() - top - pad * 0.7)
        mx = self.max_value or max(max(self.values), 1e-9) * 1.15
        n = len(self.values)
        path = QPainterPath()
        for i, v in enumerate(self.values):
            pt = QPointF(area.left() + area.width() * i / (n - 1), area.bottom() - area.height() * min(v, mx) / mx)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(area.right(), area.bottom())
        fill.lineTo(area.left(), area.bottom())
        fill.closeSubpath()
        p.setBrush(T.col("fg", 28))
        p.drawPath(fill)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(T.col("fg"), T.s(1.6)))
        p.drawPath(path)


class Art(QWidget):
    """Rounded album art (or a glyph placeholder)."""

    def __init__(self, size=56, radius=12, glyph=""):
        super().__init__()
        self.pix = None
        self.radius = radius
        self.glyph = glyph
        self.setFixedSize(T.s(size), T.s(size))

    def set_bytes(self, data):
        pm = QPixmap()
        self.pix = pm if data and pm.loadFromData(data) else None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, T.s(self.radius), T.s(self.radius))
        p.setClipPath(path)
        if self.pix:
            pm = self.pix.scaled(self.size() * self.devicePixelRatioF(), Qt.KeepAspectRatioByExpanding,
                                 Qt.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            p.drawPixmap(0, 0, pm)
        else:
            p.fillRect(r, T.col("raised"))
            p.setPen(T.col("fg3"))
            p.setFont(T.icon_font(self.width() / T.k * 0.38))
            p.drawText(r, Qt.AlignCenter, self.glyph)


class Swatch(QAbstractButton):
    def __init__(self, color, size=26):
        super().__init__()
        self.color = QColor(color)
        self.setFixedSize(T.s(size), T.s(size))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.color.name().upper())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(T.col("line"), 1))
        p.setBrush(self.color)
        p.drawRoundedRect(r, T.s(8), T.s(8))


class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)
