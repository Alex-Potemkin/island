"""Background data sources: media, radios, OCR, system stats, weather, downloads, Claude Code hooks.

Media, radios and OCR have a Windows (WinRT) and a Linux (playerctl / nmcli / tesseract) variant;
the public names Media, Radios and ocr_png point to the one for this OS."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from . import bridge, osapi

# ---------------------------------------------------------------- media (Windows SMTC)


class _WinMedia(QObject):
    """Now playing via GlobalSystemMediaTransportControls — works with Spotify, browsers, players."""
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.state = {"active": False}
        self._mgr = None
        self._art_key = None
        self._busy = False
        self._timer = QTimer(self, interval=1000, timeout=self.poll)
        self._timer.start()
        self.poll()

    def poll(self):
        if self._busy:
            return
        self._busy = True
        bridge.run_async(self._read(), self._got, self._fail)

    def _fail(self, _exc):
        self._busy = False

    async def _session(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        if self._mgr is None:
            self._mgr = await M.request_async()
        return self._mgr.get_current_session()

    async def _read(self):
        s = await self._session()
        if s is None:
            return {"active": False}
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        out = {
            "active": True,
            "title": props.title or "",
            "artist": props.artist or props.album_artist or "",
            "album": props.album_title or "",
            "playing": int(info.playback_status) == 4,
            "app": s.source_app_user_model_id or "",
            "position": _seconds(tl.position),
            "duration": _seconds(tl.end_time),
            "stamp": time.time(),
        }
        key = (out["title"], out["artist"], out["album"])
        if key != self._art_key:
            out["art"] = await _read_stream(props.thumbnail) if props.thumbnail else b""
            out["art_key"] = key
        return out

    def _got(self, st):
        self._busy = False
        if "art_key" in st:
            self._art_key = st.pop("art_key")
        elif st.get("active"):
            st["art"] = self.state.get("art", b"")
        if not st.get("active"):
            self._art_key = None
        if _media_sig(st) != _media_sig(self.state) or abs(st.get("position", 0) - self.state.get("position", 0)) > 2:
            self.state = st
            self.changed.emit(st)
        else:
            self.state = st

    def _control(self, name, key):
        async def go():
            s = await self._session()
            if s is None:
                osapi.media(key)
                return
            await getattr(s, name)()
        bridge.run_async(go(), lambda _r: QTimer.singleShot(250, self.poll), lambda _e: osapi.media(key))

    def toggle(self):
        self._control("try_toggle_play_pause_async", "play")

    def next(self):
        self._control("try_skip_next_async", "next")

    def prev(self):
        self._control("try_skip_previous_async", "prev")


class _LinuxMedia(QObject):
    """Now playing via MPRIS (playerctl): Spotify, browsers, VLC, any MPRIS player."""
    changed = Signal(object)
    SEP = "\x1f"
    FMT = SEP.join(["{{status}}", "{{xesam:title}}", "{{xesam:artist}}", "{{xesam:album}}", "{{mpris:artUrl}}",
                    "{{position}}", "{{mpris:length}}", "{{playerName}}"])

    def __init__(self):
        super().__init__()
        self.state = {"active": False}
        self._art_key = None
        self._art = b""
        self._busy = False
        self._timer = QTimer(self, interval=1000, timeout=self.poll)
        if osapi.have("playerctl"):
            self._timer.start()
            self.poll()

    def poll(self):
        if not self._busy:
            self._busy = True
            bridge.run_thread(self._read, self._got, lambda _e: setattr(self, "_busy", False))

    def _read(self):
        parts = osapi.run(["playerctl", "metadata", "--format", self.FMT], 2).split(self.SEP)
        if len(parts) < 8 or parts[0] not in ("Playing", "Paused"):
            return {"active": False}
        status, title, artist, album, art, pos, length, player = parts[:8]
        out = {"active": True, "title": title, "artist": artist, "album": album, "playing": status == "Playing",
               "app": player, "position": _num(pos) / 1e6, "duration": _num(length) / 1e6, "stamp": time.time()}
        key = (title, artist, album)
        if key != self._art_key:
            self._art_key = key
            self._art = _fetch_art(art)
        out["art"] = self._art
        return out

    def _got(self, st):
        self._busy = False
        if _media_sig(st) != _media_sig(self.state) or abs(st.get("position", 0) - self.state.get("position", 0)) > 2:
            self.state = st
            self.changed.emit(st)
        else:
            self.state = st

    def _control(self, key):
        osapi.media(key)
        QTimer.singleShot(250, self.poll)

    def toggle(self):
        self._control("play")

    def next(self):
        self._control("next")

    def prev(self):
        self._control("prev")


def _num(v):
    try:
        return float(v)
    except ValueError:
        return 0.0


def _fetch_art(url):
    try:
        if url.startswith("file://"):
            with open(urllib.parse.unquote(url[7:]), "rb") as f:
                return f.read()
        if url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=4) as r:
                return r.read(4_000_000)
    except (OSError, ValueError):
        pass
    return b""


def _media_sig(st):
    return (st.get("active"), st.get("title"), st.get("artist"), st.get("playing"))


def _seconds(v):
    try:
        return v.total_seconds()
    except AttributeError:
        try:
            return float(v.duration) / 1e7
        except Exception:
            return 0.0


async def _read_stream(ref):
    try:
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
        stream = await ref.open_read_async()
        size = int(stream.size)
        if not size:
            return b""
        buf = Buffer(size)
        res = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        return bytes(memoryview(res))[: res.length]
    except Exception:
        return b""


# ---------------------------------------------------------------- radios (Wi-Fi / Bluetooth)

RADIO_WIFI, RADIO_BT = 1, 3


class _WinRadios(QObject):
    changed = Signal(object)   # {kind: bool}

    def __init__(self):
        super().__init__()
        self.state = {}
        self._access = False
        self.refresh()

    async def _radios(self):
        from winrt.windows.devices.radios import Radio
        if not self._access:
            await Radio.request_access_async()
            self._access = True
        return list(await Radio.get_radios_async())

    def refresh(self):
        async def go():
            return {int(r.kind): int(r.state) == 1 for r in await self._radios()}
        bridge.run_async(go(), self._set, lambda _e: None)

    def _set(self, st):
        self.state = st
        self.changed.emit(st)

    def set(self, kind, on):
        async def go():
            from winrt.windows.devices.radios import RadioState
            for r in await self._radios():
                if int(r.kind) == kind:
                    await r.set_state_async(RadioState.ON if on else RadioState.OFF)
            return {int(r.kind): int(r.state) == 1 for r in await self._radios()}
        bridge.run_async(go(), self._set, lambda _e: self.refresh())


class _LinuxRadios(QObject):
    """Wi-Fi through NetworkManager (nmcli), Bluetooth through bluetoothctl (or rfkill)."""
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.state = {}
        self.refresh()

    @staticmethod
    def _read():
        st = {}
        wifi = osapi.run(["nmcli", "radio", "wifi"], 2)
        if wifi in ("enabled", "disabled"):
            st[RADIO_WIFI] = wifi == "enabled"
        bt = osapi.run(["bluetoothctl", "show"], 2)
        if "Powered:" in bt:
            st[RADIO_BT] = "Powered: yes" in bt
        return st

    def refresh(self):
        bridge.run_thread(self._read, self._set, lambda _e: None)

    def _set(self, st):
        self.state = st
        self.changed.emit(st)

    def set(self, kind, on):
        def work():
            if kind == RADIO_WIFI:
                osapi.run(["nmcli", "radio", "wifi", "on" if on else "off"], 5)
            elif kind == RADIO_BT:
                if not osapi.run(["bluetoothctl", "power", "on" if on else "off"], 5):
                    osapi.run(["rfkill", "unblock" if on else "block", "bluetooth"], 3)
            time.sleep(0.5)
            return self._read()
        bridge.run_thread(work, self._set, lambda _e: self.refresh())


# ---------------------------------------------------------------- OCR (Windows.Media.Ocr, offline)

def _win_ocr_png(png_bytes, done, failed):
    async def go():
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(bytes(png_bytes))
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bmp = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Нет языков распознавания Windows")
        result = await engine.recognize_async(bmp)
        return "\n".join(line.text for line in result.lines)
    bridge.run_async(go(), done, failed)


def _linux_ocr_png(png_bytes, done, failed):
    def work():
        if not osapi.have("tesseract"):
            raise RuntimeError("установите tesseract-ocr")
        langs = osapi.run(["tesseract", "--list-langs"], 5).split()
        lang = "+".join(code for code in ("rus", "eng") if code in langs) or "eng"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            path = f.name
        try:
            out = subprocess.run(["tesseract", path, "stdout", "-l", lang], capture_output=True, text=True,
                                 timeout=60)
            return out.stdout.strip()
        finally:
            os.remove(path)
    bridge.run_thread(work, done, failed)


if sys.platform == "win32":
    Media, Radios, ocr_png = _WinMedia, _WinRadios, _win_ocr_png
else:
    Media, Radios, ocr_png = _LinuxMedia, _LinuxRadios, _linux_ocr_png


# ---------------------------------------------------------------- system monitor


class Monitor(QObject):
    tick = Signal()

    def __init__(self):
        super().__init__()
        self.cpu = [0.0] * 60
        self.ram = [0.0] * 60
        self.down = [0.0] * 60
        self.up = [0.0] * 60
        self.info = {}
        psutil.cpu_percent(None)
        self._net = psutil.net_io_counters()
        self._t = time.time()
        self._timer = QTimer(self, interval=1000, timeout=self._sample)
        self._timer.start()

    def _sample(self):
        now = time.time()
        dt = max(0.2, now - self._t)
        net = psutil.net_io_counters()
        down = (net.bytes_recv - self._net.bytes_recv) / dt
        up = (net.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._t = net, now
        vm = psutil.virtual_memory()
        for arr, v in ((self.cpu, psutil.cpu_percent(None)), (self.ram, vm.percent), (self.down, down), (self.up, up)):
            arr.pop(0)
            arr.append(v)
        info = {"ram_used": vm.used, "ram_total": vm.total, "uptime": now - psutil.boot_time()}
        try:
            du = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
            info["disk"] = (du.used, du.total)
        except OSError:
            pass
        try:
            b = psutil.sensors_battery()
            if b:
                info["battery"] = (b.percent, b.power_plugged, b.secsleft)
        except Exception:
            pass
        self.info = info
        self.tick.emit()

    def top_processes(self, n=5):
        procs = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
            try:
                procs.append((p.info["cpu_percent"] or 0.0, p.info["memory_info"].rss if p.info["memory_info"] else 0,
                              p.info["name"] or "?"))
            except (psutil.Error, AttributeError):
                pass
        procs.sort(reverse=True)
        return procs[:n]


def human_bytes(n, per_sec=False):
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    n = float(n)
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    s = f"{n:.0f}" if n >= 100 or i == 0 else f"{n:.1f}"
    return f"{s} {units[i]}" + ("/с" if per_sec else "")


# ---------------------------------------------------------------- weather (open-meteo, no key)

WMO = {0: "Ясно", 1: "Преим. ясно", 2: "Переменная облачность", 3: "Пасмурно", 45: "Туман", 48: "Изморозь",
       51: "Морось", 53: "Морось", 55: "Сильная морось", 61: "Небольшой дождь", 63: "Дождь", 65: "Ливень",
       66: "Ледяной дождь", 67: "Ледяной дождь", 71: "Небольшой снег", 73: "Снег", 75: "Сильный снег",
       77: "Снежная крупа", 80: "Ливень", 81: "Ливень", 82: "Сильный ливень", 85: "Снегопад", 86: "Снегопад",
       95: "Гроза", 96: "Гроза с градом", 99: "Гроза с градом"}


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Island/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Weather(QObject):
    changed = Signal(object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.state = {}
        self._timer = QTimer(self, interval=20 * 60 * 1000, timeout=self.refresh)
        self._timer.start()
        QTimer.singleShot(1500, self.refresh)

    def refresh(self):
        if not self.settings.get("weather.enabled", True):
            return
        bridge.run_thread(self._fetch, self._done, lambda _e: None, self.settings.get("weather.city", ""))

    @staticmethod
    def _fetch(city):
        if city:
            q = urllib.parse.urlencode({"name": city, "count": 1, "language": "ru"})
            g = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + q)["results"][0]
            lat, lon, name = g["latitude"], g["longitude"], g["name"]
        else:
            g = _get_json("https://ipwho.is/")
            lat, lon, name = g["latitude"], g["longitude"], g.get("city", "")
        q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "timezone": "auto",
                                    "current": "temperature_2m,weather_code,apparent_temperature,wind_speed_10m",
                                    "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": 1})
        w = _get_json("https://api.open-meteo.com/v1/forecast?" + q)
        cur = w["current"]
        return {"city": name, "temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]),
                "wind": cur.get("wind_speed_10m"), "text": WMO.get(cur["weather_code"], ""),
                "max": round(w["daily"]["temperature_2m_max"][0]), "min": round(w["daily"]["temperature_2m_min"][0])}

    def _done(self, st):
        self.state = st
        self.changed.emit(st)


# ---------------------------------------------------------------- keyboard layout, caps, privacy, fullscreen, downloads

PARTIAL_EXT = (".crdownload", ".part", ".partial", ".download", ".opdownload", ".!ut", ".tmp")


class SysWatch(QObject):
    layout_changed = Signal(str)
    caps_changed = Signal(bool)
    privacy_changed = Signal(list, list)
    fullscreen_changed = Signal(bool)
    downloads_changed = Signal(list)       # [(name, size)]
    download_finished = Signal(str)        # full path

    def __init__(self):
        super().__init__()
        self.layout = osapi.keyboard_layout()
        self.caps = osapi.caps_lock()
        self.mic, self.cam = [], []
        self.fullscreen = False
        self.pid = os.getpid()
        self.dl_dir = osapi.user_dir("download")
        self._partials = {}
        self._dl_names = set(self._list_dl())
        self._fast = QTimer(self, interval=150, timeout=self._poll_fast)
        self._fast.start()
        self._slow = QTimer(self, interval=1500, timeout=self._poll_slow)
        self._slow.start()

    def _poll_fast(self):
        lay = osapi.keyboard_layout()
        if lay and lay != self.layout and lay != "??":
            self.layout = lay
            self.layout_changed.emit(lay)
        caps = osapi.caps_lock()
        if caps != self.caps:
            self.caps = caps
            self.caps_changed.emit(caps)

    def _poll_slow(self):
        mic, cam = osapi.privacy_state()
        mic = [m for m in mic if "island" not in m.lower()]
        if (mic, cam) != (self.mic, self.cam):
            self.mic, self.cam = mic, cam
            self.privacy_changed.emit(mic, cam)
        fs = osapi.foreground_is_fullscreen(self.pid)
        if fs != self.fullscreen:
            self.fullscreen = fs
            self.fullscreen_changed.emit(fs)
        self._poll_downloads()

    def _list_dl(self):
        try:
            return os.listdir(self.dl_dir)
        except OSError:
            return []

    def _poll_downloads(self):
        names = self._list_dl()
        partials = {}
        for n in names:
            if n.lower().endswith(PARTIAL_EXT):
                try:
                    partials[n] = os.path.getsize(os.path.join(self.dl_dir, n))
                except OSError:
                    pass
        if partials.keys() != self._partials.keys() or partials:
            shown = [(re.sub(r"\.(crdownload|part|partial|download|opdownload)$", "", n, flags=re.I)
                      .replace("Неподтвержденный", "Загрузка"), s) for n, s in partials.items()]
            if partials or self._partials:
                self.downloads_changed.emit(shown)
        gone = set(self._partials) - set(partials)
        if gone:
            done = {n for n in set(names) - self._dl_names
                    if not n.lower().endswith(PARTIAL_EXT) and not n.lower().endswith(".ini")}
            for g in gone:  # Firefox keeps the final name next to "name.part"
                base = os.path.splitext(g)[0]
                if base in names:
                    done.add(base)
            for n in done:
                self.download_finished.emit(os.path.join(self.dl_dir, n))
        self._partials = partials
        self._dl_names = set(names)


# ---------------------------------------------------------------- Claude Code hooks + generic notify endpoint


class HookServer(QObject):
    """POST http://127.0.0.1:<port>/claude with a Claude Code hook payload, or /notify {"title","text"}."""
    claude = Signal(object)
    notify = Signal(str, str)

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.server = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
                raw = self.rfile.read(n) if n else b""
                try:
                    data = json.loads(raw.decode("utf-8", "replace") or "{}")
                except ValueError:
                    data = {"text": raw.decode("utf-8", "replace")}
                path = self.path.split("?")[0].rstrip("/")
                if path == "/claude":
                    bridge.on_main(owner.claude.emit, data if isinstance(data, dict) else {})
                elif path == "/notify":
                    bridge.on_main(owner.notify.emit, str(data.get("title", "")), str(data.get("text", "")))
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(204)
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Island is listening".encode())

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        except OSError:
            self.server = None

    def stop(self):
        if self.server:
            self.server.shutdown()


# ---------------------------------------------------------------- brightness (laptops)

def get_brightness(done):
    bridge.run_thread(osapi.get_brightness, done, lambda _e: done(None))


def set_brightness(value):
    bridge.run_thread(osapi.set_brightness, None, None, value)


def png_bytes(qimage):
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, "PNG")
    return bytes(buf.data())


def decode_qr(qimage):
    """All QR/barcode payloads found in the image."""
    from PIL import Image
    from pyzbar import pyzbar
    img = Image.open(io.BytesIO(png_bytes(qimage))).convert("L")
    found = []
    for scale in (1, 2):
        im = img if scale == 1 else img.resize((img.width * 2, img.height * 2))
        for sym in pyzbar.decode(im):
            text = sym.data.decode("utf-8", "replace")
            if text not in found:
                found.append(text)
        if found:
            break
    return found

