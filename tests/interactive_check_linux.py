"""Window behaviour on Linux/X11 (run under a window manager, e.g. Xvfb + openbox):

    xvfb-run -a -s "-screen 0 1920x1080x24" sh -c "openbox & sleep 1; python tests/interactive_check_linux.py"

Checks the same things as interactive_check.py does on Windows, through xdotool:
  * the window mask lets clicks through outside the pill
  * hovering expands the pill without taking focus from the active window
  * moving away collapses it
  * a clipboard entry is pasted into the previously active window
  * the command bar gets keyboard focus, Esc gives focus back
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ISLAND_HOME", tempfile.mkdtemp(prefix="island-x11-"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import osapi  # noqa: E402
from island_app.app import App  # noqa: E402

TARGET = r'''
import tkinter as tk, sys
root = tk.Tk(); root.title("IslandTarget"); root.geometry("420x200+200+300")
t = tk.Text(root); t.pack(fill="both", expand=True); t.focus_set()
root.bind("<FocusIn>", lambda e: t.focus_set())
def dump():
    open(sys.argv[1], "w", encoding="utf-8").write(t.get("1.0", "end-1c")); root.after(200, dump)
root.after(300, t.focus_force); dump(); root.mainloop()
'''

results = []


def check(name, ok, info=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def xdo(*args):
    return subprocess.run(["xdotool", *map(str, args)], capture_output=True, text=True, timeout=5).stdout.strip()


def window_under(x, y):
    xdo("mousemove", x, y)
    out = xdo("getmouselocation", "--shell")
    return int(dict(line.split("=") for line in out.splitlines()).get("WINDOW", 0))


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

    def own_windows():
        """Island's X window and the WM frame around it."""
        wid = int(isl.winId())
        ids = {wid}
        tree = subprocess.run(["xwininfo", "-id", str(wid), "-tree"], capture_output=True, text=True).stdout
        for line in tree.splitlines():
            if "Parent window id:" in line:
                ids.add(int(line.split(":")[1].split()[0], 16))
        return ids

    def focus_target():
        state["target"] = int(xdo("search", "--name", "IslandTarget").split()[0])
        xdo("windowactivate", "--sync", state["target"])

    def click_through():
        g = isl.geometry()
        mine = own_windows()
        corner = window_under(g.x() + 12, g.y() + g.height() - 12)
        check("outside the pill clicks go through", corner not in mine, f"under={corner} island={mine}")
        c = isl._global_shape().center()
        check("the pill itself receives clicks", window_under(c.x(), c.y()) in mine)

    def hover():
        c = isl._global_shape().center()
        xdo("mousemove", c.x(), c.y())

    def after_hover():
        check("hover expands the island", isl.mode == "expanded", isl.mode)
        fg = osapi.foreground()
        check("focus stays in the active window", fg == state["target"], f"fg={fg} target={state['target']}")

    def away():
        xdo("mousemove", 900, 800)

    def after_away():
        check("moving away collapses", isl.mode != "expanded", isl.mode)

    def paste():
        app.clipboard.set_text("hello from island")
        qapp.processEvents()
        item = next(c for c in app.clipboard.items if c.get("text") == "hello from island")
        isl.prev_hwnd = state["target"]
        app.paste_clip(item)

    def after_paste():
        text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        check("clipboard entry pasted into previous window", "hello from island" in text, repr(text[-40:]))

    def command():
        app.hotkey("command")   # what `island --action command` does

    def after_command():
        check("command bar opens", isl.mode == "expanded" and isl.panel.current.key == "command")
        check("command bar has keyboard focus", isl.input_mode and isl.isActiveWindow(),
              f"active={isl.isActiveWindow()} fg={osapi.foreground()}")
        xdo("type", "--delay", "40", "10")

    def esc():
        check("typing reaches the command bar", isl.panel.command.input.text() == "10",
              repr(isl.panel.command.input.text()))
        xdo("key", "Escape")

    def after_esc():
        fg = osapi.foreground()
        check("Esc collapses and returns focus", isl.mode != "expanded" and fg == state["target"],
              f"mode={isl.mode} fg={fg} target={state['target']}")

    step(1500, focus_target)
    step(600, click_through)
    step(300, hover)
    step(1200, after_hover)
    step(100, away)
    step(1400, after_away)
    step(300, focus_target)
    step(400, paste)
    step(1200, after_paste)
    step(300, focus_target)
    step(400, command)
    step(1200, after_command)
    step(800, esc)
    step(1200, after_esc)

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
