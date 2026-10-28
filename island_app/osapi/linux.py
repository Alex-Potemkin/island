"""Linux implementation of the platform layer.

Works best on X11 (or XWayland: the app starts Qt with QT_QPA_PLATFORM=xcb). Everything optional
relies on common command-line tools and degrades quietly when one is missing:
  xdotool      paste into the previous window, focus tracking, fullscreen detection (+ xprop)
  playerctl    now playing / media keys (MPRIS)
  pactl        volume, microphone-in-use
  nmcli        Wi-Fi on/off          bluetoothctl / rfkill   Bluetooth on/off
  gsettings    dark theme (GNOME)    brightnessctl           brightness
  tesseract    OCR                   xkb-switch              keyboard layout name
Global hotkeys: bind desktop shortcuts to `island --action <name>` (see README).
"""
import glob
import os
import shlex
import shutil
import subprocess
import sys
import time

NAME = "Linux"
IS_WINDOWS = False
IS_MAC = False
HAS_GLOBAL_HOTKEYS = False
SETTINGS_LABEL = "Настройки системы"
TASKS_LABEL = "Системный монитор"
AUTOSTART_LABEL = "Запускать при входе в систему"
ICON_FAMILIES = ()
UI_FAMILIES = ("Inter", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
DISPLAY_FAMILIES = UI_FAMILIES


def have(cmd):
    return shutil.which(cmd) is not None


def run(args, timeout=3):
    """stdout of a command, or '' when it is missing / fails."""
    if not have(args[0]):
        return ""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def spawn(args):
    try:
        subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _first(*cmds):
    return next((c for c in cmds if have(c)), None)


CAN_PASTE = have("xdotool") or have("wtype")
CAN_SET_DARK = have("gsettings")
CAN_SILENCE_BANNERS = False   # the desktop's notification daemon shows its own pop-ups

# ---------------------------------------------------------------- windows and focus


def foreground():
    v = run(["xdotool", "getactivewindow"], 1)
    return int(v) if v.isdigit() else 0


def is_own(handle):
    v = run(["xdotool", "getwindowpid", str(handle)], 1) if handle else ""
    return v.isdigit() and int(v) == os.getpid()


def is_window(handle):
    return bool(handle)


def focus_window(handle):
    if handle and have("xdotool"):
        return bool(subprocess.run(["xdotool", "windowactivate", "--sync", str(handle)],
                                   capture_output=True, timeout=2).returncode == 0)
    return False


def noactivate(widget, on=True):
    from PySide6.QtCore import Qt
    if bool(widget.windowFlags() & Qt.WindowDoesNotAcceptFocus) == on:
        return
    visible = widget.isVisible()
    widget.setWindowFlag(Qt.WindowDoesNotAcceptFocus, on)  # re-creates the X window -> show again
    if visible:
        widget.show()


def activate(widget):
    widget.show()
    widget.raise_()
    widget.activateWindow()
    # Window managers treat an app's own activation request as focus stealing and may ignore it;
    # a request in the name of a pager/taskbar (what xdotool sends) is honoured.
    if have("xdotool"):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(30, lambda: subprocess.run(["xdotool", "windowactivate", str(int(widget.winId()))],
                                                     capture_output=True, timeout=2))


def ensure_topmost(widget):
    widget.raise_()


def register_hotkeys(widget, mapping):
    return {}, []   # no portable global hotkeys; desktop shortcuts call `island --action ...`


def unregister_hotkeys(widget, ids):
    pass


def native_hotkey(event_type, message):
    return None


# ---------------------------------------------------------------- keyboard and media

def paste():
    if have("xdotool"):
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=2)
    elif have("wtype"):
        subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], timeout=2)


def wait_keys_released(timeout=0.6):
    time.sleep(0.12)


def media(name):
    if name in ("play", "next", "prev"):
        run(["playerctl", {"play": "play-pause", "next": "next", "prev": "previous"}[name]])
    elif name == "mute":
        run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    elif name in ("vol_down", "vol_up"):
        run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%" if name == "vol_down" else "+5%"])


_layout = ["", 0.0]


def keyboard_layout():
    if time.time() - _layout[1] > 0.6:  # polled every 150 ms; don't spawn a process each time
        v = run(["xkb-switch", "-p"], 1)
        _layout[0], _layout[1] = (v[:2].upper() if v else ""), time.time()
    return _layout[0]


def caps_lock():
    for path in glob.glob("/sys/class/leds/*capslock*/brightness"):
        try:
            with open(path) as f:
                if f.read().strip() not in ("", "0"):
                    return True
        except OSError:
            pass
    return False


def foreground_is_fullscreen(own_pid):
    w = foreground()
    if not w or not have("xprop"):
        return False
    return "_NET_WM_STATE_FULLSCREEN" in run(["xprop", "-id", str(w), "_NET_WM_STATE"], 1) and not is_own(w)


_cam_tick = 0
_cam_cache = []


def privacy_state():
    """(microphone apps, camera apps) currently recording."""
    global _cam_tick, _cam_cache
    mic = []
    out = run(["pactl", "list", "source-outputs"], 2)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("application.name ="):
            mic.append(line.split("=", 1)[1].strip().strip('"'))
    _cam_tick += 1
    if _cam_tick % 3 == 1:  # walking /proc is the expensive part
        _cam_cache = _camera_users()
    return mic, list(_cam_cache)


def _camera_users():
    users = set()
    me = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != me:
                continue
            for fd in os.listdir(f"/proc/{pid}/fd"):
                if os.readlink(f"/proc/{pid}/fd/{fd}").startswith("/dev/video"):
                    with open(f"/proc/{pid}/comm") as f:
                        users.add(f.read().strip())
                    break
        except OSError:
            continue
    return sorted(users)


# ---------------------------------------------------------------- theme, autostart, power

def system_dark():
    v = run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], 1)
    if v:
        return "dark" in v
    return "dark" in run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], 1).lower()


def set_system_dark(dark):
    run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark" if dark else "default"])


_AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "autostart", "island.desktop")


def autostart_command():
    if getattr(sys, "frozen", False):
        return shlex.quote(sys.executable)
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main.py")
    return f"{shlex.quote(sys.executable)} {shlex.quote(main)}"


def cli_command():
    """How to call Island from a shell / desktop shortcut."""
    if not getattr(sys, "frozen", False) and have("island"):
        return "island"     # launcher created by linux/install.sh
    return autostart_command()


def set_autostart(on):
    if on:
        os.makedirs(os.path.dirname(_AUTOSTART), exist_ok=True)
        with open(_AUTOSTART, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Island\nComment=Dynamic Island\n"
                    f"Exec={autostart_command()}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n")
    elif os.path.exists(_AUTOSTART):
        os.remove(_AUTOSTART)


def autostart_enabled():
    return os.path.exists(_AUTOSTART)


_inhibit = None


def keep_awake(on):
    global _inhibit
    if on and _inhibit is None and have("systemd-inhibit"):
        _inhibit = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=Island", "--why=Не спать",
                                     "sleep", "infinity"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif not on and _inhibit is not None:
        _inhibit.terminate()
        _inhibit = None


def terminate_self(code=0):
    keep_awake(False)
    os._exit(code)


def lock_workstation():
    run(["loginctl", "lock-session"]) or run(["xdg-screensaver", "lock"])


def beep():
    from PySide6.QtWidgets import QApplication
    QApplication.beep()


# ---------------------------------------------------------------- files and folders

def open_path(path):
    if "://" in path:
        spawn(["xdg-open", path])
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path):
    uri = "file://" + os.path.abspath(path)
    if have("gdbus") and subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1", "--object-path",
             "/org/freedesktop/FileManager1", "--method", "org.freedesktop.FileManager1.ShowItems",
             f"['{uri}']", ""], capture_output=True, timeout=3).returncode == 0:
        return
    open_path(os.path.dirname(path))


def open_system_settings():
    cmd = _first("gnome-control-center", "systemsettings", "xfce4-settings-manager", "cinnamon-settings",
                 "mate-control-center", "lxqt-config")
    if cmd:
        spawn([cmd])


def open_task_manager():
    cmd = _first("gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "xfce4-taskmanager",
                 "mate-system-monitor", "lxtask")
    if cmd:
        spawn([cmd])


_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "download": "Downloads", "pictures": "Pictures",
            "music": "Music", "videos": "Videos"}


def user_dir(kind):
    """XDG user folder (localised names like ~/Загрузки are resolved by xdg-user-dir)."""
    v = run(["xdg-user-dir", kind.upper()], 1)
    return v if v and v != os.path.expanduser("~") else os.path.join(os.path.expanduser("~"), _FOLDERS[kind])


def data_dir(app_name):
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), app_name.lower())


def get_brightness():
    cur, mx = run(["brightnessctl", "get"], 1), run(["brightnessctl", "max"], 1)
    if cur.isdigit() and mx.isdigit() and int(mx):
        return round(int(cur) * 100 / int(mx))
    return None


def set_brightness(value):
    run(["brightnessctl", "set", f"{int(value)}%"])


def get_banner(_app_id):
    return None


def set_banner(_app_id, _value):
    pass


def prepare_app(qapp):
    """Platform setup right after the QApplication exists (macOS: no Dock icon)."""


def volume_state():
    return None


def set_volume(level):
    pass


def register_power_notifications(widget):
    pass


def unregister_power_notifications(widget):
    pass


def native_power(event_type, message):
    return None


def battery():
    return None


CAN_BLUR = False


def make_backdrop():
    return None


def window_rect(widget):
    return None
