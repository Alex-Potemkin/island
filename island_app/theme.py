"""Palettes, accents, scale and the other appearance options, plus the Qt stylesheet for the panel.

Everything here is driven by settings.json: "theme" picks a palette, "scale" sizes the whole UI,
and "appearance" holds the rest (accent, corners, opacity, shadow, animation, pill size, position, font).
"""
import os
import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase

from . import osapi


def _palette(bg, surface, raised, hover, active, line, fg, fg2, fg3, border, shadow, dark):
    return {"bg": bg, "surface": surface, "raised": raised, "hover": hover, "active": active, "line": line,
            "fg": fg, "fg2": fg2, "fg3": fg3, "inv_bg": fg, "inv_fg": bg, "border": border, "shadow": shadow,
            "warn": "#ff9f0a" if dark else "#e08600", "cam": "#30d158" if dark else "#1fa347", "dark": dark}


# key: (title, palette). All monochrome; colour comes only from the optional accent.
THEMES = {
    "dark": ("Чёрная", _palette("#000000", "#0d0d0d", "#161616", "#1c1c1c", "#2a2a2a", "#1f1f1f",
                                "#f4f4f4", "#9a9a9a", "#5c5c5c", "#000000", 110, True)),
    "graphite": ("Графит", _palette("#1c1c1e", "#262628", "#2e2e31", "#353538", "#434347", "#333336",
                                    "#f2f2f2", "#a1a1a6", "#6e6e73", "#2c2c2e", 120, True)),
    "midnight": ("Полночь", _palette("#0b1220", "#121b2d", "#18233a", "#1e2a44", "#2a3856", "#1c2740",
                                     "#e8eefc", "#93a0bb", "#5b6782", "#0b1220", 120, True)),
    "light": ("Белая", _palette("#ffffff", "#f6f6f6", "#f0f0f0", "#ececec", "#e0e0e0", "#e6e6e6",
                                "#0a0a0a", "#6b6b6b", "#a3a3a3", "#dcdcdc", 45, False)),
    "fog": ("Туман", _palette("#eceef1", "#e2e5e9", "#dadee3", "#d3d8de", "#c8ced6", "#d5dae0",
                              "#15181c", "#5d6670", "#98a0aa", "#cfd4da", 45, False)),
    "sand": ("Песок", _palette("#f5f0e8", "#ece6dc", "#e6dfd3", "#e0d8cb", "#d4cbbb", "#e2dacd",
                               "#2a241c", "#776c5d", "#ab9f8e", "#ddd4c6", 45, False)),
}

ACCENTS = {  # key: (title, colour or None for monochrome)
    "mono": ("Монохром", None), "blue": ("Синий", "#0a84ff"), "violet": ("Фиолетовый", "#8e6cff"),
    "green": ("Зелёный", "#30d158"), "orange": ("Оранжевый", "#ff9f0a"), "pink": ("Розовый", "#ff5fa2"),
    "red": ("Красный", "#ff453a"),
}
CORNERS = {"sharp": ("Строгие", 0.45), "normal": ("Обычные", 1.0), "round": ("Круглые", 1.4)}
ANIMATION = {"fast": ("Быстро", 760.0), "normal": ("Обычно", 420.0), "slow": ("Плавно", 210.0), "off": ("Без анимации", 0)}
PILL = {"small": ("Маленькая", 0.82), "normal": ("Обычная", 1.0), "large": ("Большая", 1.18)}
POSITION = {"left": "Слева", "center": "По центру", "right": "Справа"}
FONTS = ["auto", "Segoe UI", "Bahnschrift", "Inter", "Calibri", "Noto Sans", "Ubuntu", "Consolas"]

APPEARANCE = {"accent": "mono", "corners": "normal", "opacity": 100, "shadow": True, "animation": "normal",
              "pill": "normal", "position": "center", "font": "auto"}

_bundled = None


def asset(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "island_app", "assets", name)


def bundled_icon_family():
    """IslandIcons.otf: Material icons at the Segoe Fluent codepoints (Linux, or Windows without Fluent)."""
    global _bundled
    if _bundled is None:
        fid = QFontDatabase.addApplicationFont(asset("IslandIcons.otf"))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        _bundled = fams[0] if fams else "Island Icons"
    return _bundled


def appearance(settings):
    """Appearance options with defaults filled in and junk values replaced."""
    a = dict(APPEARANCE)
    a.update(settings.get("appearance") or {})
    for key, table in (("accent", ACCENTS), ("corners", CORNERS), ("animation", ANIMATION), ("pill", PILL),
                       ("position", POSITION)):
        if a[key] not in table:
            a[key] = APPEARANCE[key]
    try:
        a["opacity"] = max(60, min(100, int(a["opacity"])))
    except (TypeError, ValueError):
        a["opacity"] = 100
    return a


def theme_key(settings):
    mode = settings.get("theme", "dark")
    if mode == "system":
        mode = "dark" if osapi.system_dark() else "light"
    return mode if mode in THEMES else "dark"


def accent_colors(palette, accent):
    """inv_bg / inv_fg for selected tabs, switches and primary buttons."""
    if not accent:
        return palette["fg"], palette["bg"]
    c = QColor(accent)
    lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    return accent, ("#000000" if lum > 0.55 else "#ffffff")


class Theme:
    def __init__(self, settings, pill=False):
        """pill=True: the scale for the pill itself (UI scale × pill size)."""
        self.key = theme_key(settings)
        self.a = appearance(settings)
        self.c = dict(THEMES[self.key][1])
        self.dark = self.c["dark"]
        self.c["inv_bg"], self.c["inv_fg"] = accent_colors(self.c, ACCENTS[self.a["accent"]][1])
        self.k = settings.scale * (PILL[self.a["pill"]][1] if pill else 1.0)
        self.rk = CORNERS[self.a["corners"]][1]
        self.opacity = self.a["opacity"] / 100.0
        self.shadow = bool(self.a["shadow"])
        self.stiffness = ANIMATION[self.a["animation"]][1]
        self.position = self.a["position"]
        fams = set(QFontDatabase.families())
        default = QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
        native = () if os.environ.get("ISLAND_BUNDLED_ICONS") else osapi.ICON_FAMILIES  # preview the Linux look
        self.icon_family = next((f for f in native if f in fams), None) or bundled_icon_family()
        self.ui_family = next((f for f in osapi.UI_FAMILIES if f in fams), default)
        self.display_family = next((f for f in osapi.DISPLAY_FAMILIES if f in fams), self.ui_family)
        if self.a["font"] != "auto" and self.a["font"] in fams:
            self.ui_family = self.display_family = self.a["font"]

    def s(self, v):
        return int(round(v * self.k))

    def r(self, v):
        """Corner radius: scaled and adjusted by the corners option."""
        return max(2, int(round(v * self.k * self.rk)))

    def col(self, name, alpha=None):
        c = QColor(self.c[name])
        if alpha is not None:
            c.setAlpha(alpha)
        return c

    def bg(self):
        """Pill/panel fill with the chosen opacity."""
        return self.col("bg", int(255 * self.opacity))

    def css(self, name):
        """Stylesheet colour for card-like surfaces, following the panel opacity."""
        c = QColor(self.c[name])
        if self.opacity >= 1:
            return c.name()
        return f"rgba({c.red()},{c.green()},{c.blue()},{int(255 * min(1, self.opacity + 0.15))})"

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
        c, s, r = self.c, self.s, self.r
        hover_primary = QColor(c["inv_bg"]).lighter(118).name() if c["inv_bg"] != c["fg"] else c["fg2"]
        surface, raised = self.css("surface"), self.css("raised")
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
        QFrame[role="card"] {{ background: {surface}; border-radius: {r(16)}px; }}
        QFrame[role="sep"] {{ background: {c['line']}; max-height: 1px; min-height: 1px; }}
        QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QDateTimeEdit, QSpinBox, QComboBox {{
            background: {surface}; border: 1px solid {c['line']}; border-radius: {r(12)}px;
            padding: {s(7)}px {s(11)}px; selection-background-color: {c['inv_bg']};
            selection-color: {c['inv_fg']}; }}
        QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: {s(8)}px; }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QSpinBox:focus,
        QComboBox:focus {{ border-color: {c['fg3']}; }}
        QLineEdit[role="big"] {{ font-size: {s(17)}px; padding: {s(10)}px {s(14)}px; border-radius: {r(16)}px; }}
        QComboBox::drop-down, QDateTimeEdit::drop-down {{ border: none; width: {s(18)}px; }}
        QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['line']};
            selection-background-color: {c['active']}; }}
        QDateTimeEdit::up-button, QDateTimeEdit::down-button, QSpinBox::up-button, QSpinBox::down-button {{
            width: 0; border: none; }}
        QPushButton {{ background: {raised}; border: none; border-radius: {r(12)}px;
            padding: {s(7)}px {s(14)}px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:pressed {{ background: {c['active']}; }}
        QPushButton[role="primary"] {{ background: {c['inv_bg']}; color: {c['inv_fg']}; font-weight: 600; }}
        QPushButton[role="primary"]:hover {{ background: {hover_primary}; }}
        QPushButton[role="ghost"] {{ background: transparent; color: {c['fg2']}; }}
        QPushButton[role="ghost"]:hover {{ background: {c['hover']}; color: {c['fg']}; }}
        QPushButton[role="chip"] {{ background: transparent; border: 1px solid {c['line']}; color: {c['fg2']};
            border-radius: {r(14)}px; padding: {s(5)}px {s(12)}px; }}
        QPushButton[role="chip"]:hover {{ color: {c['fg']}; border-color: {c['fg3']}; }}
        QPushButton[role="chip"]:checked {{ background: {c['inv_bg']}; color: {c['inv_fg']}; border-color: {c['inv_bg']}; }}
        QListWidget {{ background: transparent; border: none; }}
        QListWidget::item {{ border-radius: {r(12)}px; padding: {s(2)}px; margin: 0; }}
        QListWidget::item:hover {{ background: {surface}; }}
        QListWidget::item:selected {{ background: {raised}; color: {c['fg']}; }}
        QScrollBar:vertical {{ background: transparent; width: {s(6)}px; margin: {s(2)}px; }}
        QScrollBar::handle:vertical {{ background: {c['active']}; border-radius: {s(3)}px; min-height: {s(30)}px; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
            background: none; height: 0; }}
        QScrollBar:horizontal {{ height: 0; }}
        QSlider::groove:horizontal {{ height: {s(4)}px; background: {c['active']}; border-radius: {s(2)}px; }}
        QSlider::sub-page:horizontal {{ background: {c['inv_bg']}; border-radius: {s(2)}px; }}
        QSlider::handle:horizontal {{ background: {c['fg']}; width: {s(14)}px; height: {s(14)}px;
            margin: -{s(5)}px 0; border-radius: {s(7)}px; }}
        QToolTip {{ background: {c['raised']}; color: {c['fg']}; border: 1px solid {c['line']};
            border-radius: {r(6)}px; padding: {s(4)}px {s(8)}px; }}
        QMenu {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: {r(10)}px;
            padding: {s(5)}px; }}
        QMenu::item {{ padding: {s(6)}px {s(18)}px; border-radius: {r(6)}px; }}
        QMenu::item:selected {{ background: {c['active']}; }}
        QMenu::separator {{ height: 1px; background: {c['line']}; margin: {s(4)}px {s(6)}px; }}
        QCheckBox {{ spacing: {s(8)}px; }}
        """
