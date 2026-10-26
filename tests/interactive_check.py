"""End-to-end check of the window behaviour that screenshots can't show.

    set ISLAND_HOME=%TEMP%\\island-test   (keeps your real data untouched)
    python tests/interactive_check.py

Moves the mouse and sends keys for ~15 seconds. Checks:
  * transparent parts of the island window let clicks through (WindowFromPoint)
  * hovering expands the pill without stealing focus from the active window
  * moving away collapses it
  * a clipboard entry is pasted into the previously active window
  * Alt+Space opens the command bar with keyboard focus, Esc gives focus back
"""
import ctypes
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ISLAND_HOME", os.path.join(tempfile.gettempdir(), "island-test"))

from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app.osapi import windows as winapi  # noqa: E402
from island_app.app import App  # noqa: E402

user32 = ctypes.windll.user32
TARGET = r'''
import tkinter as tk, sys
root = tk.Tk(); root.title("IslandTarget"); root.geometry("420x200+200+300")
t = tk.Text(root); t.pack(fill="both", expand=True); t.focus_set()
def dump():
    open(sys.argv[1], "w", encoding="utf-8").write(t.get("1.0", "end-1c")); root.after(200, dump)
root.bind("<FocusIn>", lambda e: t.focus_set())
# Tk binds Ctrl+V by keysym, which breaks under non-Latin layouts; real apps use the key code
def ctrl(e):
    if e.keycode == 86:
        t.event_generate("<<Paste>>")
        return "break"
t.bind("<Control-Key>", ctrl)
root.after(300, t.focus_force); dump(); root.mainloop()
'''

results = []


def title(hwnd):
    buf = ctypes.create_unicode_buffer(200)
    user32.GetWindowTextW(hwnd, buf, 200)
    cls = ctypes.create_unicode_buffer(100)
    user32.GetClassNameW(hwnd, cls, 100)
    return f"{buf.value!r}/{cls.value}"


def check(name, ok, info=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main():
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    app = App(qapp, [])
    app.hard_exit = False
    app.settings.set("onboarded", True)
    isl = app.island
    out = os.path.join(tempfile.gettempdir(), "island_target.txt")
    script = os.path.join(tempfile.gettempdir(), "island_target.py")
    open(script, "w", encoding="utf-8").write(TARGET)
    proc = subprocess.Popen([sys.executable, script, out])
    state = {}
    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    def target_hwnd():
        return user32.FindWindowW(None, "IslandTarget")

    def focus_target():
        h = target_hwnd()
        state["target"] = h
        winapi.force_foreground(h)

    def click_through():
        g = isl.geometry()
        pt = QPoint(g.x() + 12, g.y() + g.height() - 12)  # transparent corner of the island window
        r = isl.screen().devicePixelRatio()
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(int(pt.x() * r), int(pt.y() * r)))
        root = user32.GetAncestor(hwnd, 2)
        check("transparent area passes clicks through", root != int(isl.winId()), f"hit={root}")
        sr = isl._global_shape().center()
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(int(sr.x() * r), int(sr.y() * r)))
        check("pill itself receives clicks", user32.GetAncestor(hwnd, 2) == int(isl.winId()))

    def hover():
        c = isl._global_shape().center()
        r = isl.screen().devicePixelRatio()
        user32.SetCursorPos(int(c.x() * r), int(c.y() * r))

    def after_hover():
        check("hover expands the island", isl.mode == "expanded", isl.mode)
        check("focus stays in the active window", winapi.foreground() == state["target"],
              f"fg={winapi.foreground()} target={state['target']}")

    def away():
        user32.SetCursorPos(900, 700)

    def after_away():
        check("moving away collapses", isl.mode != "expanded", isl.mode)

    def paste():
        winapi.send_keys(0x41)
        app.clipboard.set_text("hello from island")
        qapp.processEvents()
        item = next(c for c in app.clipboard.items if c.get("text") == "hello from island")
        isl.expand("clipboard")
        state["fg_before_paste"] = winapi.foreground()
        app.paste_clip(item)

    def after_paste():
        text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        check("clipboard entry pasted into previous window", "hello from island" in text,
              f"{text[-40:]!r} fg_before={state.get('fg_before_paste')} target={state['target']} clip={qapp.clipboard().text()!r}")

    def alt_space():
        winapi.send_keys(0x12, 0x20)

    def after_alt_space():
        check("Alt+Space opens the command bar", isl.mode == "expanded" and isl.panel.current.key == "command")
        check("command bar has keyboard focus", isl.input_mode and winapi.foreground() == int(isl.winId()))
        winapi.send_keys(0x31, 0x30)  # type "10"

    def esc():
        check("typing reaches the command bar", isl.panel.command.input.text() == "10",
              repr(isl.panel.command.input.text()))
        winapi.send_keys(0x1B)

    def after_esc():
        check("Esc collapses and returns focus", isl.mode != "expanded" and winapi.foreground() == state["target"],
              f"mode={isl.mode} fg={title(winapi.foreground())} prev={isl.prev_hwnd} target={state['target']}")

    def click_at(x, y):
        r = isl.screen().devicePixelRatio()
        user32.SetCursorPos(int(x * r), int(y * r))
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up

    def open_clipboard():
        hover()

    def click_search():
        check("hover opened the panel again", isl.mode == "expanded", isl.mode)
        isl.show_page("clipboard")

    def click_search2():
        field = isl.panel.page("clipboard").search
        c = field.mapToGlobal(field.rect().center())
        click_at(c.x(), c.y())

    def leave_after_click():
        check("clicking a text field lets the panel take keyboard input", isl.input_mode, isl.input_mode)
        away()

    def after_leave():
        check("moving the mouse away still collapses after a click in a field", isl.mode != "expanded", isl.mode)
        check("…and gives focus back to the previous window", winapi.foreground() == state["target"],
              title(winapi.foreground()))

    def settings_fit():
        isl.expand("settings")

    def after_settings():
        p = isl.panel
        check("settings page fits in the panel", p.stack.geometry().bottom() <= p.height(),
              f"stack={p.stack.geometry()} panel={p.size()}")
        isl.collapse()

    step(1500, focus_target)
    step(600, click_through)
    step(300, hover)
    step(900, after_hover)
    step(100, away)
    step(1200, after_away)
    step(300, paste)
    step(900, after_paste)
    step(300, focus_target)
    step(400, alt_space)
    step(700, after_alt_space)
    step(500, esc)
    step(800, after_esc)
    step(300, focus_target)
    step(300, open_clipboard)
    step(1000, click_search)
    step(700, click_search2)
    step(600, leave_after_click)
    step(2600, after_leave)
    step(200, settings_fit)
    step(900, after_settings)

    def run(i=0):
        if i >= len(steps):
            proc.terminate()
            failed = [n for n, ok in results if not ok]
            print(f"\n{len(results) - len(failed)}/{len(results)} passed", flush=True)
            qapp.exit(1 if failed else 0)
            return
        d, fn = steps[i]
        QTimer.singleShot(d, lambda: (fn(), run(i + 1)))
    run()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
