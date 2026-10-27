"""Notes, data clean-up / migration, economy mode.

    python tests/check_notes.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="island-notes-")
os.environ["ISLAND_HOME"] = HOME

# a data.json damaged the way the old "+" button did it, plus other junk
JUNK = {"notes": [{"id": "a", "text": False, "t": 1}, {"id": "b", "text": "\\", "t": 2},
                  {"id": "c", "text": "   ", "t": 3}, {"id": "d", "text": 123, "t": 4}, "not a note",
                  {"id": "e", "text": "Купить хлеб\nи молоко", "t": 5}, {"id": "f", "text": "ok", "t": "bad"}],
        "clips": "broken", "reminders": None}
with open(os.path.join(HOME, "data.json"), "w", encoding="utf-8") as f:
    json.dump(JUNK, f)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import config, perf  # noqa: E402

results = []


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main():
    # ---- clean-up on load
    store = config.Store()
    texts = [n["text"] for n in store["notes"]]
    check("junk notes removed (False, '\\', blanks, numbers)", sorted(texts) == ["ok", "Купить хлеб\nи молоко"], texts)
    check("all note fields have the right types", all(isinstance(n["text"], str) and isinstance(n["t"], float)
                                                      and isinstance(n["id"], str) for n in store["notes"]))
    check("damaged lists reset instead of crashing", store["clips"] == [] and store["reminders"] == [])
    check("backup kept before the clean-up", os.path.exists(config.DATA_PATH + ".bak"))
    store.flush(force=True)

    # ---- migration from the old shared folder
    old = tempfile.mkdtemp(prefix="old-island-")
    new = tempfile.mkdtemp(prefix="new-island-")
    os.rmdir(new)
    json.dump({"layout": {}, "theme": "dark"}, open(os.path.join(old, "settings.json"), "w"))
    json.dump({"notes": [{"id": "x", "text": "из старой папки", "t": 1.0}]}, open(os.path.join(old, "data.json"), "w"))
    os.makedirs(os.path.join(old, "Cache"))          # the other app's files stay where they are
    saved = config.OLD_DATA_DIR, config.DATA_DIR, config.SETTINGS_PATH
    config.OLD_DATA_DIR, config.DATA_DIR, config.SETTINGS_PATH = old, new, os.path.join(new, "settings.json")
    moved = config.migrate_old_data()
    check("old data moved to the new folder", moved and os.path.exists(os.path.join(new, "data.json"))
          and not os.path.exists(os.path.join(old, "data.json")) and os.path.isdir(os.path.join(old, "Cache")))
    check("migration runs once", config.migrate_old_data() is False)
    other = tempfile.mkdtemp(prefix="other-app-")
    json.dump({"windowBounds": {}}, open(os.path.join(other, "settings.json"), "w"))
    config.OLD_DATA_DIR, config.SETTINGS_PATH = other, os.path.join(tempfile.mkdtemp(), "settings.json")
    check("another app's settings.json is left alone", config.migrate_old_data() is False
          and os.path.exists(os.path.join(other, "settings.json")))
    config.OLD_DATA_DIR, config.DATA_DIR, config.SETTINGS_PATH = saved

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app.app import App
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    app.settings.set("onboarded", True)
    steps = []

    class _Isl:   # the window is recreated by rebuild(); always talk to the current one
        def __getattr__(self, name):
            return getattr(app.island, name)
    isl = _Isl()

    def step(delay, fn):
        steps.append((delay, fn))

    def page():
        return isl.panel.page("notes")

    def plus_button():
        isl.expand("notes")
        p = page()
        from island_app.widgets import IconButton
        plus = next(b for b in p.findChildren(IconButton) if b.toolTip() == "Новая заметка")
        n0 = len(app.store["notes"])
        plus.click()                                  # the real button: this is what broke before
        n = p._current()
        check("«+» makes a note with text, not False", n is not None and n["text"] == ""
              and isinstance(n["text"], str), n)
        plus.click()
        check("«+» again reuses the empty note", len(app.store["notes"]) == n0 + 1)
        p.edit.insertPlainText("Позвонить в сервис\nзавтра до 12")
        qapp.processEvents()
        n = p._current()
        check("typing saves into the note", n["text"].startswith("Позвонить в сервис"))
        check("first line becomes the title", p.list.currentItem().text() == "Позвонить в сервис")
        plus.click()
        check("new empty note while another has text", len(app.store["notes"]) == n0 + 2)
        isl.show_page("home")                         # leave the tab without typing
        check("empty note dropped when leaving the tab", len(app.store["notes"]) == n0 + 1
              and all(x["text"].strip() for x in app.store["notes"]))

    def switch_and_search():
        isl.expand("notes")
        p = page()
        titles = [p.list.item(i).text() for i in range(p.list.count())]
        check("notes listed newest first", titles[0] == "Позвонить в сервис", titles)
        p.list.setCurrentRow(1)
        qapp.processEvents()
        check("switching shows the other note", p.edit.toPlainText() == app.store["notes"][1]["text"])
        p.search.setText("хлеб")
        qapp.processEvents()
        check("search filters notes", p.list.count() == 1 and p.edit.toPlainText().startswith("Купить хлеб"))
        p.search.setText("нет такого")
        check("search with no match", p.list.count() == 0 and not p.edit.isEnabled())
        p.search.clear()

    def delete_undo():
        p = page()
        p.list.setCurrentRow(0)
        victim = p._current()["text"]
        n0 = len(app.store["notes"])
        p._delete()
        check("delete removes the note", len(app.store["notes"]) == n0 - 1)
        alert = app.activities.get("alert")
        check("delete offers undo", alert and alert.get("action"))
        alert["action"]()
        check("undo brings it back", any(x["text"] == victim for x in app.store["notes"]))

    def other_entry_points():
        n0 = len(app.store["notes"])
        app.quick_action("note")
        check("quick button «Новая заметка» works", page()._current() is not None
              and isinstance(page()._current()["text"], str))
        page().edit.insertPlainText("из быстрой кнопки")
        from island_app import commands
        r = next(r for r in commands.build_results(app, "заметка список дел на субботу") if r["sub"] == "Новая заметка")
        r["action"]()
        check("command bar «заметка …» adds a note",
              any(x["text"] == "список дел на субботу" for x in app.store["notes"]) and len(app.store["notes"]) == n0 + 2)
        app.store.flush(force=True)
        again = config.Store()
        check("notes survive a restart", {x["text"] for x in again["notes"]} == {x["text"] for x in app.store["notes"]})

    def eco_on():
        app.settings.set("performance", "eco")
        app.rebuild()

    def eco_check():
        check("economy mode on", perf.ECO and app.island._poll.interval() == 70 and app.media._timer.interval() == 2000)
        isl.expand("calendar")

    def eco_open():
        check("economy: panel opens and shows content", app.island.mode == "expanded" and app.island.panel.isVisible())
        check("economy: no fade effect", not app.island.panel.effect.isEnabled())
        app.island.collapse()
        app.settings.set("performance", "normal")
        app.rebuild()

    def eco_off():
        check("back to normal mode", not perf.ECO and app.island._poll.interval() == 40
              and app.media._timer.interval() == 1000)

    step(400, plus_button)
    step(500, switch_and_search)
    step(300, delete_undo)
    step(300, other_entry_points)
    step(300, eco_on)
    step(500, eco_check)
    step(1500, eco_open)
    step(600, eco_off)

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
            qapp.exit(0 if all(results) else 1)
            return
        d, fn = steps[i]

        def go():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"step {fn.__name__} ran", False, exc)
            run(i + 1)
        QTimer.singleShot(d, go)
    run()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
