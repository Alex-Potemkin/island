"""Layout editor and first-run flow without the mouse.

    python tests/check_layout.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ISLAND_HOME"] = tempfile.mkdtemp(prefix="island-layout-")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import layout  # noqa: E402
from island_app.app import App  # noqa: E402

results = []


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main():
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    app = App(qapp, ["--demo"])     # --demo: no welcome popping up on its own
    app.hard_exit = False
    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    def welcome():
        check("fresh profile is not onboarded", not app.settings.get("onboarded"))
        app.island.expand("welcome")
        page = app.island.panel.page("welcome")
        page.choice.update(preset="minimal", theme="light", hover=False)
        page._finish()

    def after_welcome():
        s = app.settings
        check("welcome saves the answers", s.get("onboarded") and s.get("theme") == "light"
              and s.get("expand_on_hover") is False)
        check("minimal preset builds 3 tabs", app.island.panel.tab_keys == ["home", "clipboard", "timer", "settings"],
              app.island.panel.tab_keys)
        check("light theme applied", not app.theme.dark)

    def edit_tabs():
        app.set_layout(tabs=["timer", "home", "notes"], home=["clock", "note"], quick=["lock"], idle="date")

    def after_edit():
        p = app.island.panel
        check("tabs follow the editor order", p.tab_keys == ["timer", "home", "notes", "settings"], p.tab_keys)
        home = p.page("home")
        check("home shows the chosen cards", home.cards == ["clock", "note"] and home.quick == ["lock"])
        check("idle pill shows the date", "·" in app.island.idle_text(), app.island.idle_text())
        p.page("system")    # a switched-off tab still opens from the command bar
        check("hidden tab page can still be opened", "system" in p.pages and "system" not in p.tabs)

    def bad_json():
        app.settings.set("layout", {"tabs": ["nope", "home", "home"], "home": ["???"], "idle": "x"})
        app.rebuild()

    def after_bad():
        lay = layout.current(app.settings)
        check("unknown keys in settings.json are ignored", lay["tabs"] == ["home"] and lay["home"] == []
              and lay["idle"] == "empty", lay)

    def preset():
        app.apply_preset("full")

    def after_preset():
        check("full preset restores every tab", app.island.panel.tab_keys == list(layout.TABS) + ["settings"])

    def reset():
        app.settings.set("ai.translate_to", "de")
        app.reset_settings()

    def after_reset():
        s = app.settings
        check("reset keeps onboarding and AI settings", s.get("onboarded") and s.get("ai.translate_to") == "de"
              and s.get("theme") == "dark")

    def look():
        app.settings.set("theme", "graphite", save=False)
        app.settings.set("scale", 60, save=False)
        app.settings.set("appearance", {"accent": "blue", "pill": "small", "position": "left", "animation": "off",
                                        "opacity": 70, "corners": "nonsense", "font": "No Such Font"})
        app.rebuild()

    def after_look():
        t, isl = app.theme, app.island
        check("theme and accent applied", t.key == "graphite" and t.c["inv_bg"] == "#0a84ff")
        check("UI can shrink to 60 %", abs(t.k - 0.6) < 1e-6, t.k)
        check("small pill is smaller than the panel scale", app.pill_theme.k < t.k)
        check("pill sits on the left", abs(isl.shape_rect().x() - isl.pad) < 1, isl.shape_rect())
        check("no animation: shape is already in place", abs(isl.springs["w"].v - isl.springs["w"].target) < 0.5)
        check("opacity reaches the fill", t.bg().alpha() == int(255 * 0.7), t.bg().alpha())
        check("junk values fall back to defaults", t.a["corners"] == "normal" and t.a["font"] == "No Such Font"
              and t.ui_family != "No Such Font")
        app.settings.set("theme", "dark", save=False)
        app.settings.set("scale", 100, save=False)
        app.settings.set("appearance", {})
        app.rebuild()

    step(500, look)
    step(800, after_look)
    step(500, welcome)
    step(1200, after_welcome)
    step(100, edit_tabs)
    step(1000, after_edit)
    step(100, bad_json)
    step(600, after_bad)
    step(100, preset)
    step(600, after_preset)
    step(100, reset)
    step(600, after_reset)

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
            qapp.exit(0 if all(results) else 1)
            return
        d, fn = steps[i]
        QTimer.singleShot(d, lambda: (fn(), run(i + 1)))
    run()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
