"""Thin ctypes layer over the Win32 calls Island needs."""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
import winreg

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_wchar_p, wt.UINT, wt.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]


def set_noactivate(hwnd, on=True):
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) if on else (style & ~WS_EX_NOACTIVATE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)


def foreground():
    return user32.GetForegroundWindow() or 0


def window_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_window(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def force_foreground(hwnd):
    """SetForegroundWindow that also works when another process owns the foreground."""
    if not is_window(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = foreground()
    cur = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_thread and fg_thread != cur:
        attached = bool(user32.AttachThreadInput(cur, fg_thread, True))
    if window_pid(fg) != os.getpid():
        # any input event lifts the foreground lock for this process; an unassigned key is harmless,
        # whereas the usual ALT tap would open the target window's menu bar
        user32.keybd_event(0x88, 0, 0, 0)
        user32.keybd_event(0x88, 0, 2, 0)
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.BringWindowToTop(hwnd)
    if attached:
        user32.AttachThreadInput(cur, fg_thread, False)
    if foreground() != hwnd:
        user32.SwitchToThisWindow(hwnd, True)
        ok = foreground() == hwnd
    return ok


# --- synthetic keyboard input ---

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def _key(vk, up=False):
    inp = INPUT()
    inp.type = 1
    inp.u.ki = KEYBDINPUT(vk, 0, 2 if up else 0, 0, 0)
    return inp


def send_keys(*vks):
    """Press the keys in order, then release them in reverse (a chord)."""
    seq = [_key(v) for v in vks] + [_key(v, True) for v in reversed(vks)]
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))


def release_modifiers():
    for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            send_keys_up(vk)


def send_keys_up(vk):
    arr = (INPUT * 1)(_key(vk, True))
    user32.SendInput(1, arr, ctypes.sizeof(INPUT))


def paste():
    release_modifiers()
    send_keys(0x11, 0x56)  # Ctrl+V


VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY = 0xB0, 0xB1, 0xB3


def media_key(vk):
    send_keys(vk)


# --- keyboard state ---

_LANG_NAMES = {}


def keyboard_layout():
    """Two-letter language of the foreground window's layout, e.g. 'EN', 'RU'."""
    hwnd = foreground()
    tid = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    hkl = user32.GetKeyboardLayout(tid) or 0
    langid = hkl & 0xFFFF
    if langid not in _LANG_NAMES:
        buf = ctypes.create_unicode_buffer(16)
        name = "??"
        if kernel32.GetLocaleInfoW(langid, 0x59, buf, 16):  # LOCALE_SISO639LANGNAME
            name = buf.value.upper()
        _LANG_NAMES[langid] = name
    return _LANG_NAMES[langid]


def caps_lock():
    return bool(user32.GetKeyState(0x14) & 1)


# --- fullscreen detection ---

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def foreground_is_fullscreen(own_pid):
    hwnd = foreground()
    if not hwnd or window_pid(hwnd) == own_pid:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


# --- camera / microphone in use (the same registry keys the Windows privacy indicator reads) ---

_CAP_ROOT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"


def _in_use(cap):
    def walk(path, depth):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return []
        apps = []
        with key:
            try:
                stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
                start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
                if start and stop == 0:
                    apps.append(path.rsplit("\\", 1)[-1].replace("#", "\\"))
            except OSError:
                pass
            if depth:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    apps += walk(path + "\\" + sub, depth - 1)
                    i += 1
        return apps
    return walk(_CAP_ROOT + "\\" + cap, 2)


def privacy_state():
    """(microphone apps, webcam apps) currently using the device."""
    return _in_use("microphone"), _in_use("webcam")


# --- system theme ---

_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def windows_dark():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def set_windows_dark(dark):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 0 if dark else 1)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 0 if dark else 1)
    res = ctypes.c_size_t()
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200, ctypes.byref(res))


# --- autostart ---

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.abspath(sys.argv[0])
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{main}"'


def cli_command():
    return autostart_command().replace("pythonw.exe", "python.exe")


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, "Island", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, "Island")
            except FileNotFoundError:
                pass


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN) as key:
            winreg.QueryValueEx(key, "Island")
            return True
    except OSError:
        return False


# --- misc ---

def keep_awake(on):
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    kernel32.SetThreadExecutionState(flags)


def terminate_self(code=0):
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)


def lock_workstation():
    user32.LockWorkStation()


def beep():
    try:
        import winsound
        winsound.MessageBeep(0x40)  # MB_ICONASTERISK
    except Exception:
        pass


_VK_NAMES = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
             "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
             "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
             "print": 0x2C, "printscreen": 0x2C, "`": 0xC0, "-": 0xBD, "=": 0xBB, ".": 0xBE, ",": 0xBC,
             "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD}


def parse_hotkey(text):
    """'ctrl+alt+v' -> (modifiers, vk) or None."""
    mods, vk = MOD_NOREPEAT, None
    for part in (text or "").lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "meta", "super"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part in _VK_NAMES:
            vk = _VK_NAMES[part]
        elif part:
            return None
    return (mods, vk) if vk else None


def register_hotkey(hwnd, hid, text):
    parsed = parse_hotkey(text)
    if not parsed:
        return False
    return bool(user32.RegisterHotKey(hwnd, hid, parsed[0], parsed[1]))


def unregister_hotkey(hwnd, hid):
    user32.UnregisterHotKey(hwnd, hid)


def wait_keys_released(timeout=0.6):
    """Hotkeys fire on key-down; wait until the user lets go before synthesising input."""
    end = time.time() + timeout
    while time.time() < end:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B)):
            return
        time.sleep(0.02)


# ================================================================ portable interface (mirrored in linux.py)


NAME = "Windows"
IS_WINDOWS = True
HAS_GLOBAL_HOTKEYS = True
CAN_PASTE = True
CAN_SET_DARK = True
SETTINGS_LABEL = "Параметры Windows"
TASKS_LABEL = "Диспетчер задач"
AUTOSTART_LABEL = "Запускать с Windows"
ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
UI_FAMILIES = ("Segoe UI Variable Text", "Segoe UI")
DISPLAY_FAMILIES = ("Segoe UI Variable Display", "Segoe UI")

system_dark = windows_dark
set_system_dark = set_windows_dark

_MEDIA_VK = {"play": VK_MEDIA_PLAY, "next": VK_MEDIA_NEXT, "prev": VK_MEDIA_PREV,
             "mute": VK_VOLUME_MUTE, "vol_down": VK_VOLUME_DOWN, "vol_up": VK_VOLUME_UP}


def have(cmd):
    import shutil
    return shutil.which(cmd) is not None


def run(args, timeout=3):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def spawn(args):
    try:
        subprocess.Popen(args, creationflags=0x08000000)
        return True
    except OSError:
        return False


def media(name):
    """Media / volume key by name: play, next, prev, mute, vol_down, vol_up."""
    media_key(_MEDIA_VK[name])


def noactivate(widget, on=True):
    """Keep the window from taking focus when clicked (True) or let it take keyboard focus (False)."""
    set_noactivate(int(widget.winId()), on)


def activate(widget):
    """Bring our own window to the foreground with keyboard focus."""
    force_foreground(int(widget.winId()))


def focus_window(handle):
    return force_foreground(handle)


def is_own(handle):
    return bool(handle) and window_pid(handle) == os.getpid()


def ensure_topmost(widget):
    # HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    user32.SetWindowPos(int(widget.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def register_hotkeys(widget, mapping):
    """Register global hotkeys on the widget's window. Returns ({id: name}, [failed combos])."""
    hwnd = int(widget.winId())
    ids, failed = {}, []
    for i, (name, combo) in enumerate(mapping.items(), start=1):
        if combo:
            if register_hotkey(hwnd, i, combo):
                ids[i] = name
            else:
                failed.append(combo)
    return ids, failed


def unregister_hotkeys(widget, ids):
    hwnd = int(widget.winId())
    for hid in ids:
        unregister_hotkey(hwnd, hid)


def native_hotkey(event_type, message):
    """Hotkey id from a nativeEvent() message, or None."""
    if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            return int(msg.wParam)
    return None


def open_path(path):
    os.startfile(path)


def reveal(path):
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def open_system_settings():
    os.startfile("ms-settings:")


def open_task_manager():
    subprocess.Popen(["taskmgr"])


_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "download": "Downloads", "pictures": "Pictures",
            "music": "Music", "videos": "Videos"}


def user_dir(kind):
    """Known folders: desktop, documents, download, pictures, music, videos."""
    return os.path.join(os.path.expanduser("~"), _FOLDERS[kind])


def data_dir(app_name):
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), app_name)


def _ps(cmd):
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                         capture_output=True, text=True, timeout=8, creationflags=0x08000000)
    return out.stdout.strip()


def get_brightness():
    """Display brightness 0..100 or None (desktop monitors usually don't expose it). Blocking."""
    v = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -EA Stop).CurrentBrightness")
    return int(v.split()[0]) if v and v.split()[0].isdigit() else None


def set_brightness(value):
    _ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
        f".WmiSetBrightness(1,{int(value)})")
