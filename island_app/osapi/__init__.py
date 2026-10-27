"""Platform layer: the same functions on Windows (Win32/WinRT), Linux (X11 tools, D-Bus CLIs) and macOS
(AppKit through PyObjC, Carbon / CoreGraphics through ctypes).

Everything the UI needs from the OS goes through this module, so the rest of the app is portable.
"""
import sys

if sys.platform == "win32":
    from .windows import *  # noqa: F401,F403
elif sys.platform == "darwin":
    from .macos import *  # noqa: F401,F403
else:
    from .linux import *  # noqa: F401,F403
