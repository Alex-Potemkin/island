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
IS_MAC = False
HAS_GLOBAL_HOTKEYS = True
CAN_PASTE = True
CAN_SET_DARK = True
CAN_SILENCE_BANNERS = True
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


# ---------------------------------------------------------------- process list in one system call

_ntdll = ctypes.WinDLL("ntdll")
_buf = None


def process_snapshot():
    """{pid: (name, cpu_time_100ns, working_set_bytes)} for every process, from one
    NtQuerySystemInformation(SystemProcessInformation) call — psutil needs one per process."""
    global _buf
    size = ctypes.c_ulong(0)
    for _ in range(4):
        if _buf is None or len(_buf) < size.value:
            _buf = ctypes.create_string_buffer(max(size.value + 65536, 512 * 1024))
        status = _ntdll.NtQuerySystemInformation(5, _buf, len(_buf), ctypes.byref(size))
        if status == 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH
            return {}
    else:
        return {}
    out, off, base = {}, 0, ctypes.addressof(_buf)
    while True:
        p = base + off
        nxt = ctypes.c_ulong.from_address(p).value
        user = ctypes.c_longlong.from_address(p + 40).value
        kernel = ctypes.c_longlong.from_address(p + 48).value
        name_len = ctypes.c_ushort.from_address(p + 56).value
        name_ptr = ctypes.c_void_p.from_address(p + 64).value
        pid = ctypes.c_void_p.from_address(p + 80).value or 0
        ws = ctypes.c_size_t.from_address(p + 144).value
        name = ctypes.wstring_at(name_ptr, name_len // 2) if name_ptr and name_len else "System Idle Process"
        out[pid] = (name, user + kernel, ws)
        if not nxt:
            break
        off += nxt
    return out


# ---------------------------------------------------------------- notification banners (per app)

_NOTIFY_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"


def get_banner(app_id):
    """The app's own "show banners" value (0/1), or None when Windows uses its default (on)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _NOTIFY_SETTINGS + "\\" + app_id) as key:
            return int(winreg.QueryValueEx(key, "ShowBanner")[0])
    except OSError:
        return None


def set_banner(app_id, value):
    """value 0 hides the app's pop-up banners (notifications still reach the notification centre,
    which Island mirrors); None restores Windows' default."""
    path = _NOTIFY_SETTINGS + "\\" + app_id
    if value is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "ShowBanner")
        except OSError:
            pass
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ShowBanner", 0, winreg.REG_DWORD, int(value))


def prepare_app(qapp):
    """Platform setup right after the QApplication exists (macOS: no Dock icon)."""


# ---------------------------------------------------------------- volume (Core Audio) and power events

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        import uuid
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


_ole32 = ctypes.OleDLL("ole32")
_CLSID_MMDeviceEnumerator = _GUID.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
_IID_IMMDeviceEnumerator = _GUID.parse("a95664d2-9614-4f35-a746-de8db63617e6")
_IID_IAudioEndpointVolume = _GUID.parse("5cdf2c82-841e-4546-9722-0cf74078229a")
_audio = {"endpoint": None, "at": 0.0}


def _vcall(obj, index, restype, *args):
    """Call method number `index` of a COM interface pointer."""
    types = tuple(ctypes.c_void_p if type(a).__name__ == "CArgObject" else type(a) for a in args)  # byref()
    key = (obj.value if isinstance(obj, ctypes.c_void_p) else obj, index, restype, types)
    fn = _protos.get(key)
    if fn is None:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = _protos[key] = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *types)(vtbl[index])
        if len(_protos) > 64:            # objects come and go (default device lookups)
            _protos.clear()
            _protos[key] = fn
    return fn(obj, *args)


_protos = {}


def _release(obj):
    if obj:
        _vcall(obj, 2, ctypes.c_ulong)


def _endpoint():
    """IAudioEndpointVolume of the default output device; looked up again now and then (headphones)."""
    now = time.monotonic()
    if _audio["endpoint"] is not None and now - _audio["at"] < 3.0:
        return _audio["endpoint"]
    _release(_audio["endpoint"])
    _audio["endpoint"], _audio["at"] = None, now
    enum, dev, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _ole32.CoInitializeEx(None, 2)       # Qt has done it on the UI thread; harmless then
    except OSError:
        pass
    try:
        _ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, 0x17,
                                ctypes.byref(_IID_IMMDeviceEnumerator), ctypes.byref(enum))
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eMultimedia)
        if _vcall(enum, 4, ctypes.HRESULT, ctypes.c_int(0), ctypes.c_int(1), ctypes.byref(dev)) == 0:
            # IMMDevice::Activate(IID_IAudioEndpointVolume, CLSCTX_ALL)
            _vcall(dev, 3, ctypes.HRESULT, ctypes.byref(_IID_IAudioEndpointVolume), ctypes.c_uint32(0x17),
                   ctypes.c_void_p(), ctypes.byref(vol))
            _audio["endpoint"] = vol
    except OSError:
        _audio["endpoint"] = None
    finally:
        _release(dev)
        _release(enum)
    return _audio["endpoint"]


def volume_state():
    """(volume 0..100, muted) of the default speakers, or None. A few microseconds: polled."""
    vol = _endpoint()
    if vol is None:
        return None
    level, muted = ctypes.c_float(), wt.BOOL()
    try:
        _vcall(vol, 9, ctypes.HRESULT, ctypes.byref(level))       # GetMasterVolumeLevelScalar
        _vcall(vol, 15, ctypes.HRESULT, ctypes.byref(muted))      # GetMute
    except OSError:
        _audio["at"] = 0.0          # the device went away: look it up again next time
        return None
    return int(round(level.value * 100)), bool(muted.value)


def set_volume(level):
    vol = _endpoint()
    if vol is not None:
        _vcall(vol, 7, ctypes.HRESULT, ctypes.c_float(max(0, min(100, level)) / 100), ctypes.c_void_p())


WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE = 0x0218, 0x8013
_POWER_GUIDS = {
    "brightness": "8ffee2c6-2d01-46be-adb9-398addc5b4ff",     # GUID_VIDEO_CURRENT_MONITOR_BRIGHTNESS, %
    "battery": "a7ad8041-b45a-4cae-87a3-eecbb468a9e1",        # GUID_BATTERY_PERCENTAGE_REMAINING, %
    "power_source": "5d3e9a59-e9d5-4b00-a6bd-ff34ff516548",   # GUID_ACDC_POWER_SOURCE: 0 AC, 1 DC, 2 UPS
}
_power_handles = {}
user32.RegisterPowerSettingNotification.restype = ctypes.c_void_p
user32.RegisterPowerSettingNotification.argtypes = [wt.HANDLE, ctypes.POINTER(_GUID), wt.DWORD]
user32.UnregisterPowerSettingNotification.argtypes = [ctypes.c_void_p]


def register_power_notifications(widget):
    """Windows then sends WM_POWERBROADCAST when brightness, battery level or the power source change
    (and once right away with the current value). No polling."""
    hwnd = int(widget.winId())
    for h in _power_handles.pop(hwnd, []):
        user32.UnregisterPowerSettingNotification(h)
    handles = []
    for guid in _POWER_GUIDS.values():
        g = _GUID.parse(guid)
        h = user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(g), 0)
        if h:
            handles.append(h)
    _power_handles[hwnd] = handles


def unregister_power_notifications(widget):
    for h in _power_handles.pop(int(widget.winId()), []):
        user32.UnregisterPowerSettingNotification(h)


class _PowerSetting(ctypes.Structure):
    _fields_ = [("PowerSetting", _GUID), ("DataLength", wt.DWORD), ("Data", wt.DWORD)]


_GUID_NAMES = {bytes(_GUID.parse(g)): name for name, g in _POWER_GUIDS.items()}


def native_power(event_type, message):
    """("brightness" | "battery" | "power_source", value) from a nativeEvent() message, or None."""
    if event_type not in (b"windows_generic_MSG", "windows_generic_MSG"):
        return None
    msg = wt.MSG.from_address(int(message))
    if msg.message != WM_POWERBROADCAST or msg.wParam != PBT_POWERSETTINGCHANGE or not msg.lParam:
        return None
    ps = _PowerSetting.from_address(msg.lParam)
    name = _GUID_NAMES.get(bytes(ps.PowerSetting))
    return (name, int(ps.Data)) if name and ps.DataLength >= 1 else None


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


def battery():
    """{"percent", "plugged", "seconds_left"} or None on a PC without a battery."""
    st = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)) or st.BatteryFlag & 128 \
            or st.BatteryLifePercent == 255:
        return None
    left = st.BatteryLifeTime if st.BatteryLifeTime != 0xFFFFFFFF else None
    return {"percent": int(st.BatteryLifePercent), "plugged": st.ACLineStatus == 1, "seconds_left": left}


# ---------------------------------------------------------------- acrylic backdrop under the panel

CAN_BLUR = sys.getwindowsversion().build >= 17134      # acrylic blur-behind: Windows 10 1803+


class _AccentPolicy(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int), ("GradientColor", wt.DWORD),
                ("AnimationId", ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


class Backdrop:
    """A plain (not layered) window with Windows' acrylic blur, kept right under the island while the
    panel is open: the island itself is a layered window, and DWM blurs only non-layered ones. It never
    takes focus and is covered by the panel, so clicks don't reach it."""
    _class = None

    def __init__(self):
        self.hwnd = None
        self.shown = False
        self._geom = None

    @classmethod
    def _register(cls):
        if cls._class is None:
            class WNDCLASSW(ctypes.Structure):
                _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                            ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                            ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                            ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p).value
            wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            wc.hbrBackground = ctypes.windll.gdi32.GetStockObject(4)      # BLACK_BRUSH: alpha 0 -> the blur shows
            wc.lpszClassName = "IslandBackdrop"
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW(ctypes.byref(wc))
            cls._class = wc
        return cls._class

    def _create(self, tint):
        self._register()
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                           ctypes.c_void_p]
        ex = 0x00000080 | 0x08000000 | 0x00000008 | 0x00000020   # TOOLWINDOW | NOACTIVATE | TOPMOST | TRANSPARENT
        self.hwnd = user32.CreateWindowExW(ex, "IslandBackdrop", "", 0x80000000, 0, 0, 10, 10, None, None,
                                           ctypes.windll.kernel32.GetModuleHandleW(None), None)
        policy = _AccentPolicy(4, 2, tint, 0)          # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = _CompositionData(19, ctypes.cast(ctypes.byref(policy), ctypes.c_void_p), ctypes.sizeof(policy))
        user32.SetWindowCompositionAttribute(wt.HWND(self.hwnd), ctypes.byref(data))

    def show(self, above, x, y, w, h, radius, tint):
        """Place it at (x, y, w, h) in physical pixels, rounded, right under the window `above`."""
        if self.hwnd is None:
            self._create(tint)
        geom = (x, y, w, h, radius)
        if geom != self._geom:
            self._geom = geom
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
            user32.SetWindowRgn(wt.HWND(self.hwnd), rgn, True)        # the window owns the region now
        # SWP_NOACTIVATE | SWP_SHOWWINDOW, inserted after (= below) the island
        user32.SetWindowPos(wt.HWND(self.hwnd), wt.HWND(above), x, y, w, h, 0x0010 | 0x0040)
        self.shown = True

    def hide(self):
        if self.hwnd is not None and self.shown:
            user32.ShowWindow(wt.HWND(self.hwnd), 0)
        self.shown = False

    def destroy(self):
        if self.hwnd is not None:
            user32.DestroyWindow(wt.HWND(self.hwnd))
        self.hwnd, self.shown, self._geom = None, False, None


def make_backdrop():
    return Backdrop() if CAN_BLUR else None


def window_rect(widget):
    """(left, top, width, height) of a top-level widget in physical pixels."""
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(int(widget.winId())), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top
