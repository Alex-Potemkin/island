"""Screen recording of a region to MP4 (H.264) or GIF.

Frames are grabbed on the UI thread (QScreen.grabWindow, a few ms each) and streamed to ffmpeg through
a pipe by a worker thread, so a long recording doesn't pile up in memory. ffmpeg comes with the
imageio-ffmpeg package (bundled in the builds) or from PATH. A GIF is made from the finished MP4 with a
two-pass palette, which looks far better than a frame-by-frame GIF.
"""
import datetime as dt
import os
import queue
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from . import bridge, config, perf

MAX_SECONDS = 15 * 60
MAX_WIDTH = 1920            # larger regions are scaled down (keeps files and CPU sane on 4K screens)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def ffmpeg_path():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - not installed / binary missing
        return shutil.which("ffmpeg")


def fmt_clock(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class Recorder(QObject):
    changed = Signal()            # started / stopped / every second while recording
    saved = Signal(str)           # path of the finished file
    failed = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.active = False
        self.busy = False         # encoding after stop
        self.started = 0.0
        self._timer = QTimer(self, timeout=self._grab)
        self._tick = QTimer(self, interval=1000, timeout=self.changed.emit)
        self._queue = None
        self._proc = None
        self._thread = None

    @property
    def fmt(self):
        f = self.settings.get("record.format", "mp4")
        return f if f in ("mp4", "gif") else "mp4"

    def elapsed(self):
        return time.monotonic() - self.started if self.active else 0.0

    # ---------------------------------------------------------------- start / stop
    def start(self, screen, rect):
        """rect: the region in the screen's logical coordinates."""
        if self.active or self.busy:
            return False
        exe = ffmpeg_path()
        if not exe:
            self.failed.emit("Нет ffmpeg — запись недоступна")
            return False
        self.screen, self.rect = screen, QRect(rect)
        dpr = screen.devicePixelRatio() or 1.0
        w, h = int(rect.width() * dpr), int(rect.height() * dpr)
        if w > MAX_WIDTH:
            h, w = int(h * MAX_WIDTH / w), MAX_WIDTH
        self.size = (max(2, w - w % 2), max(2, h - h % 2))     # H.264 wants even sizes
        self.fps = perf.ms(15, 10)
        os.makedirs(config.SHOTS_DIR, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        self.path_mp4 = os.path.join(config.SHOTS_DIR, f"Запись {stamp}.mp4")
        args = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
                "-s", f"{self.size[0]}x{self.size[1]}", "-r", str(self.fps), "-i", "-",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", self.path_mp4]
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        except OSError as exc:
            self.failed.emit(f"ffmpeg не запустился: {exc}")
            return False
        self._queue = queue.Queue(maxsize=self.fps * 4)
        self._thread = threading.Thread(target=self._write, args=(self._proc, self._queue), daemon=True)
        self._thread.start()
        self.active, self.started = True, time.monotonic()
        self._timer.start(int(1000 / self.fps))
        self._tick.start()
        self._grab()
        self.changed.emit()
        return True

    def _grab(self):
        if not self.active:
            return
        if self.elapsed() > MAX_SECONDS:
            self.stop()
            return
        r = self.rect
        pm = self.screen.grabWindow(0, r.x(), r.y(), r.width(), r.height())
        img = pm.toImage().convertToFormat(QImage.Format_RGB32)
        if (img.width(), img.height()) != self.size:
            img = img.scaled(self.size[0], self.size[1], Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        data = bytes(img.constBits())[:self.size[0] * self.size[1] * 4]
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            pass                  # the encoder is behind: drop a frame rather than freeze the UI

    @staticmethod
    def _write(proc, q):
        while True:
            data = q.get()
            if data is None:
                break
            try:
                proc.stdin.write(data)
            except OSError:
                break
        try:
            proc.stdin.close()
        except OSError:
            pass

    def stop(self):
        if not self.active:
            return
        self.active = False
        self._timer.stop()
        self._tick.stop()
        self.busy = True
        self.changed.emit()
        self._queue.put(None)
        bridge.run_thread(self._finish, self._done, self._fail)

    def _finish(self):
        self._thread.join(timeout=30)
        err = self._proc.communicate(timeout=120)[1].decode("utf-8", "replace").strip()
        if self._proc.returncode != 0 or not os.path.exists(self.path_mp4):
            raise RuntimeError(err.splitlines()[-1] if err else "ffmpeg завершился с ошибкой")
        if self.fmt != "gif":
            return self.path_mp4
        gif = self.path_mp4[:-4] + ".gif"
        width = min(self.size[0], 960)
        vf = (f"fps={min(self.fps, 12)},scale={width}:-1:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
        res = subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-i", self.path_mp4, "-vf", vf, gif],
                             capture_output=True, creationflags=NO_WINDOW, timeout=300)
        if res.returncode != 0 or not os.path.exists(gif):
            return self.path_mp4      # the MP4 is still there
        os.remove(self.path_mp4)
        return gif

    def _done(self, path):
        self.busy = False
        self.changed.emit()
        self.saved.emit(path)

    def _fail(self, exc):
        self.busy = False
        self.changed.emit()
        self.failed.emit(str(exc))
