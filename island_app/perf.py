"""Economy mode for weak PCs: the same features, polled and animated less often.

settings.json → "performance": "auto" (default: economy on ≤ 4 logical CPUs or < 6 GB RAM), "eco" or "normal".
Every periodic timer registers its normal and economy interval here, so switching applies at once.
"""
import weakref

import psutil

ECO = False
_timers = []          # (weakref to QTimer, normal ms, eco ms)


def weak_machine():
    try:
        return (psutil.cpu_count() or 1) <= 4 or psutil.virtual_memory().total < 6 * 2**30
    except Exception:
        return False


def configure(settings):
    global ECO
    mode = settings.get("performance", "auto")
    ECO = mode == "eco" or (mode not in ("eco", "normal") and weak_machine())
    for ref, normal, eco in list(_timers):
        t = ref()
        if t is None:
            _timers.remove((ref, normal, eco))
            continue
        try:
            t.setInterval(eco if ECO else normal)
        except RuntimeError:          # Qt object already deleted
            _timers.remove((ref, normal, eco))
    return ECO


def timer(qtimer, normal_ms, eco_ms):
    """Register a periodic QTimer; returns it with the interval for the current mode."""
    _timers.append((weakref.ref(qtimer), normal_ms, eco_ms))
    qtimer.setInterval(eco_ms if ECO else normal_ms)
    return qtimer


def ms(normal_ms, eco_ms):
    return eco_ms if ECO else normal_ms
