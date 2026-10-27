"""Things on the desk: a Bambu Lab 3D printer (local MQTT) and the DeskDash agent (local HTTP).

Bambu Lab printers publish their state over MQTT on port 8883 (TLS, self-signed), user "bblp",
password = the access code from the printer's screen. The report topic is device/<serial>/report;
a "pushall" request on device/<serial>/request makes the printer send its full state (P1/A1 send
only changes after that). The MQTT 3.1.1 client below is the few packets that needs, so no extra
dependency.

DeskDash (the phone dashboard) runs an agent on the PC: http://127.0.0.1:<port>/api/... with the
token from its config.json. Island reads that file and calls the same API the phone uses: macros
and Spotify Connect. DeskDash itself is not changed.
"""
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from . import bridge, config, perf

# ================================================================ minimal MQTT 3.1.1


def _varint(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s):
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def mqtt_packet(ptype, flags, body):
    return bytes([(ptype << 4) | flags]) + _varint(len(body)) + body


def mqtt_connect(client_id, user, password, keepalive=60):
    body = _str("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", keepalive) + _str(client_id) + _str(user) + _str(password)
    return mqtt_packet(1, 0, body)


def mqtt_subscribe(topic, pid=1):
    return mqtt_packet(8, 2, struct.pack("!H", pid) + _str(topic) + b"\x00")


def mqtt_publish(topic, payload):
    return mqtt_packet(3, 0, _str(topic) + payload)


PINGREQ = b"\xc0\x00"


def mqtt_read(sock):
    """One packet: (type, flags, body). Raises ConnectionError when the socket closes."""
    def exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("соединение закрыто")
            buf += chunk
        return buf
    head = exact(1)[0]
    n, mult = 0, 1
    while True:
        b = exact(1)[0]
        n += (b & 0x7F) * mult
        mult *= 128
        if not b & 0x80:
            break
    return head >> 4, head & 0x0F, exact(n) if n else b""


def mqtt_parse_publish(flags, body):
    tlen = struct.unpack("!H", body[:2])[0]
    topic = body[2:2 + tlen].decode("utf-8", "replace")
    rest = body[2 + tlen:]
    if (flags >> 1) & 3:          # QoS 1/2 carry a packet id
        rest = rest[2:]
    return topic, rest


# ================================================================ Bambu Lab

STATES = {"RUNNING": "Печать", "PREPARE": "Подготовка", "SLICING": "Нарезка", "PAUSE": "Пауза",
          "FINISH": "Готово", "FAILED": "Ошибка", "IDLE": "Ожидание", "INIT": "Ожидание"}


def fmt_minutes(m):
    m = max(0, int(m or 0))
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h} ч {mm} мин"
    return f"{h} ч" if h else f"{mm} мин"


class BambuPrinter(QObject):
    """Settings: accounts.bambu = {host, serial, code (protected), name}."""
    PORT = 8883
    TLS = True
    changed = Signal()

    def __init__(self, settings, notify):
        super().__init__()
        self.settings = settings
        self.notify = notify
        self.state = {}          # merged "print" object from the reports
        self.connected = False
        self.error = ""
        self.updated = 0.0
        self._gen = 0            # bumps on reconfigure; an old thread sees it and stops
        self._sock = None
        self._prev_state = None
        self._emit_at = 0.0
        self._pending = False
        QTimer.singleShot(1500, self.start)

    # ---------------------------------------------------------------- config
    def cfg(self):
        c = self.settings.get("accounts.bambu") or {}
        return {"host": (c.get("host") or "").strip(), "serial": (c.get("serial") or "").strip().upper(),
                "code": config.unprotect(c.get("code") or "").strip(), "name": c.get("name") or "Принтер"}

    @property
    def configured(self):
        c = self.cfg()
        return bool(c["host"] and c["serial"] and c["code"])

    def configure(self, host, serial, code, name=""):
        code = code.strip()
        self.settings.set("accounts.bambu", {"host": host.strip(), "serial": serial.strip().upper(),
                                             "code": (config.protect(code) or code) if code else "",
                                             "name": name.strip() or "Принтер"})
        self.state, self.error, self.connected, self._prev_state = {}, "", False, None
        self.start()
        self.changed.emit()

    def forget(self):
        self.settings.set("accounts.bambu", {})
        self.stop()
        self.state, self.error, self.connected = {}, "", False
        self.changed.emit()

    def start(self):
        self.stop()
        if not self.configured:
            return
        gen = self._gen
        threading.Thread(target=self._run, args=(gen, self.cfg()), name="bambu-mqtt", daemon=True).start()

    def stop(self):
        self._gen += 1
        s, self._sock = self._sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass

    # ---------------------------------------------------------------- derived values
    @property
    def gcode_state(self):
        return (self.state.get("gcode_state") or "").upper()

    @property
    def active(self):
        return self.connected and self.gcode_state in ("RUNNING", "PREPARE", "PAUSE", "SLICING")

    @property
    def percent(self):
        try:
            return max(0, min(100, int(self.state.get("mc_percent") or 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def remaining(self):
        """Minutes left, counted down between reports (the printer sends it once a minute)."""
        try:
            m = float(self.state.get("mc_remaining_time") or 0)
        except (TypeError, ValueError):
            return 0
        if self.gcode_state == "RUNNING" and self.updated:
            m -= (time.time() - self.updated) / 60
        return max(0, int(round(m)))

    @property
    def job(self):
        name = self.state.get("subtask_name") or self.state.get("gcode_file") or ""
        name = os.path.basename(str(name))
        for ext in (".gcode.3mf", ".3mf", ".gcode"):
            if name.lower().endswith(ext):
                name = name[:-len(ext)]
        return name

    def summary(self):
        """Short status line for the pill and the card."""
        if not self.configured:
            return "Не подключён"
        if not self.connected:
            return self.error or "Подключение…"
        st = self.gcode_state
        if st in ("RUNNING", "PAUSE"):
            left = self.remaining
            text = f"{self.percent}% · осталось {fmt_minutes(left)}"
            return ("Пауза · " + text) if st == "PAUSE" else text
        return STATES.get(st, st.capitalize() or "Ожидание")

    def finish_at(self):
        left = self.remaining
        return time.strftime("%H:%M", time.localtime(time.time() + left * 60)) if left else ""

    def temps(self):
        def num(k):
            try:
                return round(float(self.state.get(k)))
            except (TypeError, ValueError):
                return None
        return num("nozzle_temper"), num("nozzle_target_temper"), num("bed_temper"), num("bed_target_temper")

    def layers(self):
        try:
            return int(self.state.get("layer_num") or 0), int(self.state.get("total_layer_num") or 0)
        except (TypeError, ValueError):
            return 0, 0

    # ---------------------------------------------------------------- network thread
    def _run(self, gen, c):
        delay = 3
        while gen == self._gen:
            try:
                self._session(gen, c)
                delay = 3
            except Exception as exc:  # noqa: BLE001 - shown in the UI, then retried
                if gen != self._gen:
                    return
                bridge.on_main(self._set_error, gen, _bambu_error(exc))
            time.sleep(delay)
            delay = min(delay * 2, 60)

    def _session(self, gen, c):
        raw = socket.create_connection((c["host"], self.PORT), timeout=8)
        if self.TLS:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE        # the printer's certificate is self-signed
            sock = ctx.wrap_socket(raw, server_hostname=c["host"])
        else:
            sock = raw
        self._sock = sock
        try:
            sock.sendall(mqtt_connect(f"island-{os.getpid()}-{int(time.time()) % 10000}", "bblp", c["code"]))
            t, _f, body = mqtt_read(sock)
            if t != 2 or len(body) < 2:
                raise ConnectionError("неожиданный ответ")
            if body[1] != 0:
                raise PermissionError("неверный код доступа" if body[1] in (4, 5) else f"отказ ({body[1]})")
            sock.sendall(mqtt_subscribe(f"device/{c['serial']}/report"))
            push = json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}).encode()
            sock.sendall(mqtt_publish(f"device/{c['serial']}/request", push))
            bridge.on_main(self._set_connected, gen)
            sock.settimeout(30)
            last_ping = time.monotonic()
            while gen == self._gen:
                try:
                    t, flags, body = mqtt_read(sock)
                except socket.timeout:
                    t = None
                if t == 3:
                    _topic, payload = mqtt_parse_publish(flags, body)
                    try:
                        msg = json.loads(payload.decode("utf-8", "replace"))
                    except ValueError:
                        msg = {}
                    if isinstance(msg.get("print"), dict):
                        bridge.on_main(self._merge, gen, msg["print"])
                if time.monotonic() - last_ping > 25:
                    sock.sendall(PINGREQ)
                    last_ping = time.monotonic()
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # ---------------------------------------------------------------- UI thread
    def _set_connected(self, gen):
        if gen == self._gen:
            self.connected, self.error = True, ""
            self.changed.emit()

    def _set_error(self, gen, text):
        if gen == self._gen:
            self.connected, self.error = False, text
            self.changed.emit()

    def _merge(self, gen, data):
        if gen != self._gen:
            return
        self.state.update(data)
        if "mc_remaining_time" in data or "mc_percent" in data:
            self.updated = time.time()
        st = self.gcode_state
        prev, self._prev_state = self._prev_state, st
        name = self.cfg()["name"]
        if prev and prev != st:
            if st == "FINISH":
                self.notify("printer", f"{name}: печать готова", self.job)
            elif st == "FAILED":
                self.notify("printer", f"{name}: печать прервана", self.job)
            elif st == "PAUSE":
                self.notify("printer", f"{name}: пауза", self.job)
            elif st == "RUNNING" and prev in ("PREPARE", "IDLE", "FINISH", "FAILED"):
                self.notify("printer", f"{name}: печать началась",
                            f"{self.job} · {fmt_minutes(self.remaining)}" if self.remaining else self.job)
        # reports come several times a second while printing: repaint at most once a second
        now = time.monotonic()
        if now - self._emit_at > 1.0:
            self._emit_at = now
            self.changed.emit()
        elif not self._pending:
            self._pending = True
            QTimer.singleShot(1000, self._flush)

    def _flush(self):
        self._pending = False
        self._emit_at = time.monotonic()
        self.changed.emit()


def _bambu_error(exc):
    if isinstance(exc, PermissionError):
        return f"Принтер: {exc}"
    if isinstance(exc, socket.timeout):
        return "Принтер не отвечает"
    if isinstance(exc, ssl.SSLError):
        return "Ошибка TLS"
    if isinstance(exc, OSError):
        return "Принтер недоступен в сети"
    return f"Принтер: {exc}"


# ================================================================ DeskDash

DESKDASH_GLYPHS = {  # DeskDash icon names -> glyphs of this app's icon font
    "bolt": "", "web": "", "settings": "", "open": "", "lock": "",
    "mute": "", "vol_up": "", "vol_down": "", "play": "", "pause": "",
    "next": "", "prev": "", "desktop": "", "sleep": "", "screenshot": "",
    "power": "", "music": "", "eye_off": "",
}


def deskdash_dir():
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "DeskDash")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "deskdash")


class DeskDash(QObject):
    """Talks to the DeskDash agent on this PC. Only reads DeskDash's config.json."""
    HOST = "127.0.0.1"
    changed = Signal()
    spotify_changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.dir = deskdash_dir()
        self.online = False
        self.name = ""
        self.macros = []
        self.spotify = False       # DeskDash is signed in to Spotify
        self.devices = []
        self.now = {}              # what Spotify plays on any device
        self.error = ""
        self._busy = False
        self._sp_busy = False
        self._timer = perf.timer(QTimer(self, timeout=self.poll), 30 * 1000, 60 * 1000)
        self._sp_timer = perf.timer(QTimer(self, timeout=self.poll_spotify), 8 * 1000, 20 * 1000)
        if self.enabled:
            self._timer.start()
            QTimer.singleShot(2500, self.poll)

    @property
    def enabled(self):
        return bool(self.settings.get("deskdash.enabled", True))

    @property
    def installed(self):
        return os.path.exists(os.path.join(self.dir, "config.json"))

    def _cfg(self):
        try:
            with open(os.path.join(self.dir, "config.json"), encoding="utf-8") as f:
                c = json.load(f)
            return int(c.get("port") or 8765), str(c.get("token") or "")
        except (OSError, ValueError, TypeError):
            return None, None

    def _call(self, method, path, timeout=4):
        port, token = self._cfg()
        if port is None:
            raise FileNotFoundError("DeskDash не установлен")
        req = urllib.request.Request(f"http://{self.HOST}:{port}{path}", method=method,
                                     data=b"" if method == "POST" else None, headers={"X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8")).get("error")
            except ValueError:
                err = None
            raise RuntimeError(err or f"HTTP {e.code}") from e

    # ---------------------------------------------------------------- polling
    def set_enabled(self, on):
        self.settings.set("deskdash.enabled", bool(on))
        if on:
            self._timer.start()
            self.poll()
        else:
            self._timer.stop()
            self._sp_timer.stop()
            self.online, self.now = False, {}
            self.changed.emit()
            self.spotify_changed.emit()

    def poll(self):
        if self._busy or not self.enabled or not self.installed:
            return
        self._busy = True
        bridge.run_thread(lambda: self._call("GET", "/api/info", 3), self._info, self._offline)

    def _info(self, info):
        self._busy = False
        self.online, self.error = True, ""
        self.name = info.get("name", "")
        self.macros = [m for m in info.get("macros", []) if isinstance(m, dict) and m.get("id")]
        self.spotify = bool(info.get("spotify"))
        if self.spotify and self.settings.get("deskdash.spotify", True):
            if not self._sp_timer.isActive():
                self._sp_timer.start()
                self.poll_spotify()
        else:
            self._sp_timer.stop()
        self.changed.emit()

    def _offline(self, exc):
        self._busy = False
        self.online = False
        self.error = "DeskDash не запущен" if isinstance(exc, (OSError, urllib.error.URLError)) else str(exc)
        self._sp_timer.stop()
        if self.now:
            self.now = {}
            self.spotify_changed.emit()
        self.changed.emit()

    def poll_spotify(self):
        if self._sp_busy or not self.online:
            return
        self._sp_busy = True
        bridge.run_thread(lambda: self._call("GET", "/api/spotify/state", 8), self._spotify_state,
                          self._spotify_fail)

    def _spotify_state(self, res):
        self._sp_busy = False
        self.devices = res.get("devices") or []
        now = res.get("now") or {}
        if now != self.now:
            self.now = now
            self.spotify_changed.emit()
        self.changed.emit()

    def _spotify_fail(self, _exc):
        self._sp_busy = False

    # ---------------------------------------------------------------- actions
    def run_macro(self, mid, done=None):
        def ok(_r):
            if done:
                done(True, "")
        def fail(exc):
            if done:
                done(False, str(exc))
        bridge.run_thread(lambda: self._call("POST", "/api/macro/" + urllib.parse.quote(mid, safe=""), 15), ok, fail)

    def spotify_cmd(self, name, done=None):
        bridge.run_thread(lambda: self._call("POST", "/api/spotify/cmd/" + name, 8),
                          lambda _r: (done and done(True, ""), QTimer.singleShot(600, self.poll_spotify)),
                          lambda e: done and done(False, str(e)))

    def transfer(self, device_id, done=None):
        bridge.run_thread(lambda: self._call("POST", "/api/spotify/transfer/" + urllib.parse.quote(device_id, safe=""), 10),
                          lambda _r: (done and done(True, ""), QTimer.singleShot(1500, self.poll_spotify)),
                          lambda e: done and done(False, str(e)))

    def show_window(self):
        """Asks a running DeskDash.exe to open its window (the same call its second instance makes)."""
        port, _t = self._cfg()
        if port is None:
            return False
        try:
            urllib.request.urlopen(f"http://{self.HOST}:{port}/api/show", timeout=2).read()
            return True
        except OSError:
            return False

    def remote_playing(self):
        """Spotify on another device (phone, speaker) while nothing plays on this PC."""
        n = self.now
        return n if n.get("title") and n.get("playing") else None
