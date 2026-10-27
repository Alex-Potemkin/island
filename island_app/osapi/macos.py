"""macOS implementation of the platform layer.

The island is an NSPanel (what Qt creates for a Qt.Tool window) raised above the menu bar, on every
Space, that doesn't activate the app when clicked; the app itself has no Dock icon (accessory app).
Native pieces go through PyObjC (AppKit, Quartz) and ctypes (Carbon hot keys, keyboard layout,
CoreGraphics key events, CoreAudio):

  global hot keys   Carbon RegisterEventHotKey: no extra permission needed
  paste             a ⌘V key event — needs System Settings → Privacy & Security → Accessibility
  screenshots       QScreen.grabWindow — needs Screen Recording (first use asks)
  now playing       AppleScript to Spotify and Music (Apple closed the system-wide "now playing"
                    to other apps in macOS 15.4); first use asks for Automation permission
  Bluetooth         `blueutil` from Homebrew, if installed
"""
import ctypes
import ctypes.util
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time

NAME = "macOS"
IS_WINDOWS = False
IS_MAC = True
HAS_GLOBAL_HOTKEYS = True
SETTINGS_LABEL = "Системные настройки"
TASKS_LABEL = "Мониторинг системы"
AUTOSTART_LABEL = "Открывать при входе в систему"
ICON_FAMILIES = ()
UI_FAMILIES = ("SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue")
DISPLAY_FAMILIES = ("SF Pro Display", ".AppleSystemUIFont", "Helvetica Neue")
CAN_PASTE = True
CAN_SET_DARK = True
CAN_SILENCE_BANNERS = False


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


def osascript(script, timeout=3):
    return run(["osascript", "-e", script], timeout)


def _framework(name):
    return ctypes.CDLL(f"/System/Library/Frameworks/{name}.framework/{name}")


try:
    _cf = _framework("CoreFoundation")
    _cg = _framework("CoreGraphics")
    _carbon = _framework("Carbon")
except OSError:          # imported on another OS by a test or a linter
    _cf = _cg = _carbon = None


def _fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")


# ---------------------------------------------------------------- application and windows


def prepare_app(qapp):
    """No Dock icon and no menu bar of our own: the island is all there is (LSUIElement does the same
    for the built .app; this covers running from source)."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)   # NSApplicationActivationPolicyAccessory
    except Exception:  # noqa: BLE001 - PyObjC missing: the app still works, with a Dock icon
        pass


def _nswindow(widget):
    try:
        import objc
        view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
        return view.window()
    except Exception:  # noqa: BLE001
        return None


NON_ACTIVATING = 1 << 7          # NSWindowStyleMaskNonactivatingPanel
STATUS_LEVEL = 25                # NSStatusWindowLevel: above the menu bar


def _setup(widget, nonactivating):
    w = _nswindow(widget)
    if w is None:
        return
    w.setLevel_(STATUS_LEVEL)
    # every Space, stays put when switching Spaces, also over full-screen apps, not in ⌘-Tab / Exposé
    w.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 6) | (1 << 8))
    w.setHidesOnDeactivate_(False)
    w.setHasShadow_(False)
    try:
        mask = w.styleMask()
        w.setStyleMask_(mask | NON_ACTIVATING if nonactivating else mask & ~NON_ACTIVATING)
        w.setBecomesKeyOnlyIfNeeded_(True)     # a click in a text field still gets the keyboard
    except AttributeError:
        pass                                   # an NSWindow, not an NSPanel


def foreground():
    """pid of the frontmost app."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else 0
    except Exception:  # noqa: BLE001
        return 0


def is_own(handle):
    return handle == os.getpid()


def _running(pid):
    try:
        from AppKit import NSRunningApplication
        return NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid)) if pid else None
    except Exception:  # noqa: BLE001
        return None


def is_window(handle):
    return _running(handle) is not None


def focus_window(handle):
    app = _running(handle)
    if app is None:
        return False
    return bool(app.activateWithOptions_(1 << 1))     # NSApplicationActivateIgnoringOtherApps


def noactivate(widget, on=True):
    _setup(widget, on)


def activate(widget):
    """The panel takes the keyboard (command bar, search): activate the app for that moment."""
    _setup(widget, False)
    widget.show()
    widget.raise_()
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        w = _nswindow(widget)
        if w is not None:
            w.makeKeyAndOrderFront_(None)
    except Exception:  # noqa: BLE001
        pass
    widget.activateWindow()


def ensure_topmost(widget):
    from PySide6.QtCore import Qt
    _setup(widget, bool(widget.windowFlags() & Qt.WindowDoesNotAcceptFocus))
    widget.raise_()


# ---------------------------------------------------------------- global hot keys (Carbon)

_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
    "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "space": 49, "enter": 36, "return": 36, "tab": 48, "esc": 53, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
}
_MODS = {"cmd": 256, "win": 256, "meta": 256, "shift": 512, "alt": 2048, "option": 2048, "ctrl": 4096,
         "control": 4096}


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_hk = {"handler": None, "refs": {}, "widget": None, "next": 1}
_SIGNATURE = _fourcc("ISLD")


def _hotkey_event(_call, event, _data):
    hk = _HotKeyID()
    err = _carbon.GetEventParameter(ctypes.c_void_p(event), _fourcc("----"), _fourcc("hkid"), None,
                                    ctypes.sizeof(hk), None, ctypes.byref(hk))
    widget = _hk["widget"]
    if err == 0 and hk.signature == _SIGNATURE and widget is not None:
        widget.nativeEvent(b"island-hotkey", int(hk.id))
    return 0


def _install_handler():
    if _hk["handler"] is not None or _carbon is None:
        return _hk["handler"] is not None
    _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    _carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_uint32,
                                            ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
    _carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    _carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    handler = _HANDLER(_hotkey_event)
    spec = _EventTypeSpec(_fourcc("keyb"), 5)        # kEventClassKeyboard, kEventHotKeyPressed
    ref = ctypes.c_void_p()
    if _carbon.InstallEventHandler(_carbon.GetApplicationEventTarget(), handler, 1, ctypes.byref(spec), None,
                                   ctypes.byref(ref)) != 0:
        return False
    _hk["handler"] = handler          # keep the callback alive
    return True


def parse_hotkey(text):
    """"ctrl+alt+v" -> (keycode, carbon modifiers) or None."""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts or parts[-1] not in _KEYCODES:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    return _KEYCODES[parts[-1]], mods


def register_hotkeys(widget, mapping):
    """{name: "ctrl+alt+v"} -> ({id: name}, [failed combos])."""
    _hk["widget"] = widget
    ids, failed = {}, []
    if not mapping:
        return ids, failed
    if not _install_handler():
        return ids, [c for c in mapping.values() if c]
    for name, combo in mapping.items():
        parsed = parse_hotkey(combo)
        if parsed is None:
            if combo:
                failed.append(combo)
            continue
        hid = _hk["next"]
        _hk["next"] += 1
        ref = ctypes.c_void_p()
        err = _carbon.RegisterEventHotKey(parsed[0], parsed[1], _HotKeyID(_SIGNATURE, hid),
                                          _carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if err == 0:
            _hk["refs"][hid] = ref
            ids[hid] = name
        else:
            failed.append(combo)
    return ids, failed


def unregister_hotkeys(widget, ids):
    for hid in ids:
        ref = _hk["refs"].pop(hid, None)
        if ref is not None:
            _carbon.UnregisterEventHotKey(ref)


def native_hotkey(event_type, message):
    return message if event_type == b"island-hotkey" else None


# ---------------------------------------------------------------- keyboard and media


def paste():
    """⌘V into the frontmost app (needs the Accessibility permission)."""
    if _cg is None:
        return
    _cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    _cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    _cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    for down in (True, False):
        ev = _cg.CGEventCreateKeyboardEvent(None, 9, down)
        _cg.CGEventSetFlags(ev, 1 << 20)         # kCGEventFlagMaskCommand
        _cg.CGEventPost(0, ev)                   # kCGHIDEventTap
        _cf.CFRelease(ev)


def wait_keys_released(timeout=0.6):
    time.sleep(0.12)


last_player = "Music"     # set by the media service: the app that plays now


def media(name):
    if name in ("play", "next", "prev"):
        cmd = {"play": "playpause", "next": "next track", "prev": "previous track"}[name]
        osascript(f'tell application "{last_player}" to {cmd}')
    elif name == "mute":
        osascript("set volume output muted not (output muted of (get volume settings))")
    elif name in ("vol_down", "vol_up"):
        step = -6 if name == "vol_down" else 6
        osascript(f"set volume output volume ((output volume of (get volume settings)) + {step})")


_layout = ["", 0.0]


def keyboard_layout():
    """Two letters of the current input source's language ("RU", "EN")."""
    if time.time() - _layout[1] < 0.6 or _carbon is None:
        return _layout[0]
    _layout[1] = time.time()
    try:
        _carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
        _carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
        _carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _cf.CFArrayGetCount.restype = ctypes.c_long
        _cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        _cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        _cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        _cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        key = ctypes.c_void_p.in_dll(_carbon, "kTISPropertyInputSourceLanguages")
        src = _carbon.TISCopyCurrentKeyboardInputSource()
        if not src:
            return _layout[0]
        try:
            langs = _carbon.TISGetInputSourceProperty(src, key)
            if langs and _cf.CFArrayGetCount(langs) > 0:
                buf = ctypes.create_string_buffer(16)
                if _cf.CFStringGetCString(_cf.CFArrayGetValueAtIndex(langs, 0), buf, 16, 0x08000100):
                    _layout[0] = buf.value.decode()[:2].upper()
        finally:
            _cf.CFRelease(src)
    except (OSError, ValueError, AttributeError):
        pass
    return _layout[0]


def caps_lock():
    if _cg is None:
        return False
    _cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
    _cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
    return bool(_cg.CGEventSourceFlagsState(0) & (1 << 16))     # kCGEventFlagMaskAlphaShift


def foreground_is_fullscreen(own_pid):
    """The frontmost app's window covers its whole screen (a video, a game, a full-screen Space)."""
    try:
        import Quartz
        from AppKit import NSScreen
        pid = foreground()
        if not pid or pid == own_pid:
            return False
        wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly
                                                 | Quartz.kCGWindowListExcludeDesktopElements, 0) or []
        sizes = {(int(s.frame().size.width), int(s.frame().size.height)) for s in NSScreen.screens()}
        for w in wins:
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
                b = w.get("kCGWindowBounds") or {}
                return (int(b.get("Width", 0)), int(b.get("Height", 0))) in sizes
    except Exception:  # noqa: BLE001
        pass
    return False


class _PropAddr(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


_coreaudio = None


def _audio_u32(obj, selector):
    global _coreaudio
    if _coreaudio is None:
        _coreaudio = _framework("CoreAudio")
        _coreaudio.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_PropAddr), ctypes.c_uint32,
                                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                                                          ctypes.c_void_p]
    addr = _PropAddr(_fourcc(selector), _fourcc("glob"), 0)
    value, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
    err = _coreaudio.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                                ctypes.byref(value))
    return value.value if err == 0 else None


def privacy_state():
    """(microphone users, camera users). macOS doesn't say which app records, only that the default
    microphone is in use; the camera isn't visible to other apps."""
    try:
        dev = _audio_u32(1, "dIn ")                 # kAudioHardwarePropertyDefaultInputDevice
        if dev and _audio_u32(dev, "gone"):         # kAudioDevicePropertyDeviceIsRunningSomewhere
            return ["Микрофон"], []
    except OSError:
        pass
    return [], []


# ---------------------------------------------------------------- theme, autostart, power


def system_dark():
    return run(["defaults", "read", "-g", "AppleInterfaceStyle"], 1) == "Dark"


def set_system_dark(dark):
    osascript('tell application "System Events" to tell appearance preferences to set dark mode to '
              + ("true" if dark else "false"), 5)


_AGENT = os.path.expanduser("~/Library/LaunchAgents/pro.island.app.plist")


def _program():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main.py")
    return [sys.executable, main]


def autostart_command():
    return " ".join(shlex.quote(p) for p in _program())


def cli_command():
    return autostart_command()


def set_autostart(on):
    if on:
        os.makedirs(os.path.dirname(_AGENT), exist_ok=True)
        with open(_AGENT, "wb") as f:
            plistlib.dump({"Label": "pro.island.app", "ProgramArguments": _program(), "RunAtLoad": True,
                           "ProcessType": "Interactive"}, f)
    elif os.path.exists(_AGENT):
        os.remove(_AGENT)


def autostart_enabled():
    return os.path.exists(_AGENT)


_caffeinate = None


def keep_awake(on):
    global _caffeinate
    if on and _caffeinate is None:
        _caffeinate = subprocess.Popen(["caffeinate", "-di"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif not on and _caffeinate is not None:
        _caffeinate.terminate()
        _caffeinate = None


def terminate_self(code=0):
    keep_awake(False)
    os._exit(code)


def lock_workstation():
    # ⌃⌘Q, the system "Lock Screen" shortcut (needs Accessibility); else just put the display to sleep
    if not osascript('tell application "System Events" to keystroke "q" using {control down, command down}'):
        run(["pmset", "displaysleepnow"])


def beep():
    from PySide6.QtWidgets import QApplication
    QApplication.beep()


# ---------------------------------------------------------------- files and folders


def open_path(path):
    spawn(["open", path])


def reveal(path):
    spawn(["open", "-R", path])


def open_system_settings():
    spawn(["open", "x-apple.systempreferences:"])


def open_task_manager():
    spawn(["open", "-a", "Activity Monitor"])


_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "download": "Downloads", "pictures": "Pictures",
            "music": "Music", "videos": "Movies"}


def user_dir(kind):
    return os.path.join(os.path.expanduser("~"), _FOLDERS[kind])


def data_dir(app_name):
    return os.path.join(os.path.expanduser("~/Library/Application Support"), app_name)


def get_brightness():
    return None      # no public API for the built-in display; the slider is hidden


def set_brightness(value):
    pass


def get_banner(_app_id):
    return None


def set_banner(_app_id, _value):
    pass
