"""Running work off the UI thread: a shared asyncio loop (WinRT calls) and plain worker threads."""
import asyncio
import threading
import traceback

from PySide6.QtCore import QObject, Signal


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn: fn())


_invoker = None
_loop = None


def init():
    """Call once from the UI thread after QApplication exists."""
    global _invoker, _loop
    _invoker = _Invoker()
    _loop = asyncio.new_event_loop()
    threading.Thread(target=_loop.run_forever, name="island-async", daemon=True).start()


def on_main(fn, *args, **kwargs):
    """Run fn on the UI thread (safe to call from any thread)."""
    _invoker.call.emit(lambda: fn(*args, **kwargs))


def run_async(coro, done=None, failed=None):
    """Schedule a coroutine on the shared loop; deliver its result (or error) on the UI thread."""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)

    def _done(f):
        try:
            result = f.result()
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            if failed:
                on_main(failed, exc)
            else:
                traceback.print_exc()
            return
        if done:
            on_main(done, result)
    fut.add_done_callback(_done)
    return fut


def run_thread(fn, done=None, failed=None, *args):
    def work():
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001
            if failed:
                on_main(failed, exc)
            else:
                traceback.print_exc()
            return
        if done:
            on_main(done, result)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t
