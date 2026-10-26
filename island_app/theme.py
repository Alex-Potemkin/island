"""Monochrome palette, scale and the Qt stylesheet for the panel."""
from PySide6.QtGui import QColor, QFont, QFontDatabase

from . import winapi

DARK = {
    "bg": "#000000", "surface": "#0d0d0d", "raised": "#161616", "hover": "#1c1c1c", "active": "#2a2a2a",
    "line": "#1f1f1f", "fg": "#f4f4f4", "fg2": "#9a9a9a", "fg3": "#5c5c5c", "inv_bg": "#f4f4f4",
    "inv_fg": "#000000", "border": "#000000", "shadow": 110, "warn": "#ff9f0a", "cam": "#30d158",
}
LIGHT = {
    "bg": "#ffffff", "surface": "#f6f6f6", "raised": "#f0f0f0", "hover": "#ececec", "active": "#e0e0e0",
    "line": "#e6e6e6", "fg": "#0a0a0a", "fg2": "#6b6b6b", "fg3": "#a3a3a3", "inv_bg": "#0a0a0a",
    "inv_fg": "#ffffff", "border": "#dcdcdc", "shadow": 45, "warn": "#e08600", "cam": "#1fa347",
}


class Theme:
    def __init__(self, settings):
        mode = settings.get("theme", "dark")
        if mode == "system":
            mode = "dark" if winapi.windows_dark() else "light"
        self.dark = mode != "light"
        self.c = dict(DARK if self.dark else LIGHT)
        self.k = settings.scale
        fams = set(QFontDatabase.families())
        self.icon_family = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"
        self.ui_family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
        self.display_family = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fams else "Segoe UI"

    def s(self, v):
        return int(round(v * self.k))

    def col(self, name, alpha=None):
        c = QColor(self.c[name])
        if alpha is not None:
            c.setAlpha(alpha)
        return c

    def font(self, px, weight=QFont.Normal, display=False):
        f = QFont(self.display_family if display else self.ui_family)
        f.setPixelSize(max(6, self.s(px)))
        f.setWeight(weight if isinstance(weight, QFont.Weight) else QFont.Weight(int(weight)))
        f.setHintingPreference(QFont.PreferNoHinting)
        return f

    def tabular(self, px, weight=QFont.DemiBold):
        f = self.font(px, weight, display=True)
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except Exception:
            pass
        return f

    def icon_font(self, px):
        f = QFont(self.icon_family)
        f.setPixelSize(max(6, self.s(px)))
        return f

    def stylesheet(self):
        c, s = self.c, self.s
        return f"""
        * {{ color: {c['fg']}; font-family: '{self.ui_family}'; font-size: {s(13)}px; outline: none; }}
        QWidget#panel, QWidget#page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
            background: transparent; border: none; }}
        QLabel {{ background: transparent; }}
        QLabel[role="h1"] {{ font-size: {s(26)}px; font-weight: 600; font-family: '{self.display_family}'; }}
        QLabel[role="h2"] {{ font-size: {s(15)}px; font-weight: 600; }}
        QLabel[role="muted"] {{ color: {c['fg2']}; font-size: {s(12)}px; }}
        QLabel[role="faint"] {{ color: {c['fg3']}; font-size: {s(11)}px; }}
        QLabel[role="icon"] {{ font-family: '{self.icon_family}'; font-size: {s(16)}px; color: {c['fg2']}; }}
        QFrame[role="card"] {{ background: {c['surface']}; border-radius: {s(16)}px; }}
        QFrame[role="sep"] {{ background: {c['line']}; max-height: 1px; min-height: 1px; }}
        QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QDateTimeEdit, QSpinBox, QComboBox {{
            background: {c['surface']}; border: 1px solid {c['line']}; border-radius: {s(12)}px;
            padding: {s(7)}px {s(11)}px; selection-background-color: {c['inv_bg']};
            selection-color: {c['inv_fg']}; }}
        QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: {s(8)}px; }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QSpinBox:focus,
        QComboBox:focus {{ border-color: {c['fg3']}; }}
        QLineEdit[role="big"] {{ font-size: {s(17)}px; padding: {s(10)}px {s(14)}px; border-radius: {s(16)}px; }}
        QComboBox::drop-down, QDateTimeEdit::drop-down {{ border: none; width: {s(18)}px; }}
        QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['line']};
            selection-background-color: {c['active']}; }}
        QDateTimeEdit::up-button, QDateTimeEdit::down-button, QSpinBox::up-button, QSpinBox::down-button {{
            width: 0; border: none; }}
        QPushButton {{ background: {c['raised']}; border: none; border-radius: {s(12)}px;
            padding: {s(7)}px {s(14)}px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:pressed {{ background: {c['active']}; }}
        QPushButton[role="primary"] {{ background: {c['inv_bg']}; color: {c['inv_fg']}; font-weight: 600; }}
        QPushButton[role="primary"]:hover {{ background: {c['fg2']}; }}
        QPushButton[role="ghost"] {{ background: transparent; color: {c['fg2']}; }}
        QPushButton[role="ghost"]:hover {{ background: {c['hover']}; color: {c['fg']}; }}
        QPushButton[role="chip"] {{ background: transparent; border: 1px solid {c['line']}; color: {c['fg2']};
            border-radius: {s(14)}px; padding: {s(5)}px {s(12)}px; }}
        QPushButton[role="chip"]:hover {{ color: {c['fg']}; border-color: {c['fg3']}; }}
        QPushButton[role="chip"]:checked {{ background: {c['inv_bg']}; color: {c['inv_fg']}; border-color: {c['inv_bg']}; }}
        QListWidget {{ background: transparent; border: none; }}
        QListWidget::item {{ border-radius: {s(12)}px; padding: {s(2)}px; margin: 0; }}
        QListWidget::item:hover {{ background: {c['surface']}; }}
        QListWidget::item:selected {{ background: {c['raised']}; color: {c['fg']}; }}
        QScrollBar:vertical {{ background: transparent; width: {s(6)}px; margin: {s(2)}px; }}
        QScrollBar::handle:vertical {{ background: {c['active']}; border-radius: {s(3)}px; min-height: {s(30)}px; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
            background: none; height: 0; }}
        QScrollBar:horizontal {{ height: 0; }}
        QSlider::groove:horizontal {{ height: {s(4)}px; background: {c['active']}; border-radius: {s(2)}px; }}
        QSlider::sub-page:horizontal {{ background: {c['fg']}; border-radius: {s(2)}px; }}
        QSlider::handle:horizontal {{ background: {c['fg']}; width: {s(14)}px; height: {s(14)}px;
            margin: -{s(5)}px 0; border-radius: {s(7)}px; }}
        QToolTip {{ background: {c['raised']}; color: {c['fg']}; border: 1px solid {c['line']};
            border-radius: {s(6)}px; padding: {s(4)}px {s(8)}px; }}
        QMenu {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: {s(10)}px;
            padding: {s(5)}px; }}
        QMenu::item {{ padding: {s(6)}px {s(18)}px; border-radius: {s(6)}px; }}
        QMenu::item:selected {{ background: {c['active']}; }}
        QMenu::separator {{ height: 1px; background: {c['line']}; margin: {s(4)}px {s(6)}px; }}
        QCheckBox {{ spacing: {s(8)}px; }}
        """
