"""Volume and brightness in the pill, battery and Bluetooth devices (Windows).

  volume       the default speakers' level, read through Core Audio (~0.1 ms) 5 times a second
  brightness   WM_POWERBROADCAST from Windows when it changes (no polling)
  battery      the same notifications for the charge level and the power source
  Bluetooth    a DeviceWatcher on paired devices: connected / disconnected; the charge Windows knows
               for a device ("{104EA319-…} 2") is looked up when it connects
"""
import re

from PySide6.QtCore import QObject, QTimer

from . import bridge, osapi, perf

GLYPH_VOLUME = ("", "", "", "")    # Volume0..3
GLYPH_MUTE = ""
GLYPH_BRIGHTNESS = ""
GLYPH_BT = ""
GLYPH_HEADPHONES = ""
HEADPHONE_WORDS = re.compile(r"pods|buds|headphone|headset|наушн|jbl|sony|wh-|wf-|beats|tune|bose|sennheiser|"
                             r"marshall|soundcore|galaxy buds|freebuds|redmi buds", re.I)


def battery_glyph(percent, charging):
    """Segoe Fluent battery glyphs: Battery0..9 E850–E859, Battery10 E83F, charging E85A–E862, E83E."""
    step = max(0, min(10, int(round(percent / 10))))
    if charging:
        return "" if step >= 10 else chr(0xE85A + min(step, 8))
    return "" if step >= 10 else chr(0xE850 + step)


def fmt_left(seconds):
    if not seconds:
        return ""
    h, m = divmod(int(seconds) // 60, 60)
    return f"≈ {h} ч {m} мин" if h else f"≈ {m} мин"


class PowerWatch(QObject):
    """Turns volume, brightness, battery and Bluetooth changes into pill activities and notifications."""
    LEVEL_TTL = 1.6

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.volume = None            # (level, muted)
        self.brightness = None
        self.battery = None           # percent
        self.source = None            # 0 AC, 1 battery
        self._warned = set()
        self._level_timer = QTimer(self, singleShot=True, timeout=self._clear_level)
        self._vol_timer = perf.timer(QTimer(self, timeout=self._poll_volume), 200, 400)
        self.bt = BluetoothWatch(self) if osapi.IS_WINDOWS else None
        self.apply_settings()

    def apply_settings(self):
        if self.app.settings.get("live.volume", True) and osapi.volume_state() is not None:
            self.volume = osapi.volume_state()
            self._vol_timer.start()
        else:
            self._vol_timer.stop()
        if self.bt is not None:
            self.bt.set_enabled(bool(self.app.settings.get("live.battery", True)))

    # ---------------------------------------------------------------- level OSD
    def _poll_volume(self):
        st = osapi.volume_state()
        if st is None or st == self.volume:
            return
        first = self.volume is None
        self.volume = st
        if not first:
            level, muted = st
            glyph = GLYPH_MUTE if muted or level == 0 else GLYPH_VOLUME[min(3, level * 4 // 101)]
            self.show_level("Громкость", glyph, level, "без звука" if muted else f"{level}%", muted)

    def show_level(self, title, glyph, level, text, muted=False):
        self.app.activities["level"] = {"kind": "level", "title": title, "glyph": glyph, "progress": level / 100,
                                        "value": text, "muted": muted}
        self._level_timer.start(int(self.LEVEL_TTL * 1000))
        self.app.on_live()

    def _clear_level(self):
        self.app.activities.pop("level", None)
        self.app.on_live()

    # ---------------------------------------------------------------- power notifications
    def on_power(self, name, value):
        """("brightness" | "battery" | "power_source", value) from WM_POWERBROADCAST. Windows sends the
        current value right after registering; only changes are shown."""
        if name == "brightness":
            first, self.brightness = self.brightness is None, value
            if not first and self.app.settings.get("live.volume", True):
                self.show_level("Яркость", GLYPH_BRIGHTNESS, value, f"{value}%")
        elif name == "power_source":
            prev, self.source = self.source, value
            if prev is not None and prev != value and self.app.settings.get("live.battery", True):
                self._source_changed(value)
        elif name == "battery":
            prev, self.battery = self.battery, value
            if prev is not None and self.app.settings.get("live.battery", True):
                self._level_changed(prev, value)

    def _source_changed(self, source):
        info = osapi.battery() or {}
        pct = info.get("percent", self.battery or 0)
        if source == 0:
            self._warned.clear()
            self.app.alert(battery_glyph(pct, True), "Зарядка", f"{pct}%", ttl=2.5)
        else:
            self.app.alert(battery_glyph(pct, False), "От батареи", f"{pct}%  {fmt_left(info.get('seconds_left'))}",
                           ttl=2.5)

    def _level_changed(self, prev, pct):
        charging = self.source == 0
        if charging and prev < 100 <= pct:
            self.app.notifications.push("battery", "Заряжено", "100% — зарядку можно отключить",
                                        glyph=battery_glyph(100, True))
            return
        if charging:
            return
        for threshold in (20, 10, 5):
            if prev > threshold >= pct and threshold not in self._warned:
                self._warned.add(threshold)
                left = fmt_left((osapi.battery() or {}).get("seconds_left"))
                self.app.notifications.push("battery", f"Мало заряда: {pct}%",
                                            ("Подключите зарядку" + (f" · {left}" if left else "")),
                                            glyph=battery_glyph(pct, False))
                break

    # ---------------------------------------------------------------- Bluetooth
    def device_changed(self, name, connected, percent=None):
        glyph = GLYPH_HEADPHONES if HEADPHONE_WORDS.search(name) else GLYPH_BT
        if connected:
            sub = f"подключено · {percent}%" if percent is not None else "подключено"
        else:
            sub = "отключено"
        self.app.alert(glyph, name, sub, ttl=3)


_AEP_CONNECTED = "System.Devices.Aep.IsConnected"
_BT_BATTERY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"
_AQS_PAIRED = ('(System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}" OR '
               'System.Devices.Aep.ProtocolId:="{bb7bb05e-5972-42b5-94fc-76eaa7084d49}") AND '
               'System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True')
_AQS_BT_DEVICES = 'System.Devices.ClassGuid:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}"'


def _unbox(obj, kind):
    import winrt.system as ws
    try:
        return getattr(ws, "unbox_" + kind)(obj)
    except Exception:  # noqa: BLE001 - not a boxed value of that type
        return None


class BluetoothWatch(QObject):
    """Paired Bluetooth devices (headphones, mice…): an event when one connects or disconnects."""

    def __init__(self, power):
        super().__init__()
        self.power = power
        self.devices = {}          # id -> {"name", "connected"}
        self._watcher = None
        self._ready = False        # the first batch of events describes the current state: no alerts

    def set_enabled(self, on):
        if on and self._watcher is None:
            try:
                self._start()
            except Exception:  # noqa: BLE001 - no Bluetooth stack / WinRT package: stay quiet
                self._watcher = None
        elif not on and self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:  # noqa: BLE001
                pass
            self._watcher = None

    def _start(self):
        from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
        w = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
            _AQS_PAIRED, [_AEP_CONNECTED], DeviceInformationKind.ASSOCIATION_ENDPOINT)
        # WinRT calls these on its own threads
        w.add_added(lambda _w, d: bridge.on_main(self._added, d.id, d.name,
                                                  _unbox(d.properties.lookup(_AEP_CONNECTED), "boolean")
                                                  if d.properties.has_key(_AEP_CONNECTED) else None))
        w.add_updated(lambda _w, u: bridge.on_main(self._updated, u.id,
                                                    _unbox(u.properties.lookup(_AEP_CONNECTED), "boolean")
                                                    if u.properties.has_key(_AEP_CONNECTED) else None))
        w.add_removed(lambda _w, u: bridge.on_main(self.devices.pop, u.id, None))
        self._watcher = w
        w.start()
        QTimer.singleShot(1500, self._settle)     # the initial burst comes in within ~50 ms

    def _settle(self):
        self._ready = True

    def _added(self, did, name, connected):
        self.devices[did] = {"name": name or "Устройство", "connected": bool(connected)}

    def _updated(self, did, connected):
        dev = self.devices.get(did)
        if dev is None or connected is None or bool(connected) == dev["connected"]:
            return
        dev["connected"] = bool(connected)
        if not self._ready or not dev["name"].strip():
            return
        if connected:
            name = dev["name"]
            bridge.run_async(self._battery_of(name), lambda pct: self.power.device_changed(name, True, pct),
                             lambda _e: self.power.device_changed(name, True))
        else:
            self.power.device_changed(dev["name"], False)

    @staticmethod
    async def _battery_of(name):
        """The charge Windows shows for this device (headsets report it a moment after connecting)."""
        import asyncio
        from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(2)
            devs = await DeviceInformation.find_all_async_with_kind_aqs_filter_and_additional_properties(
                _AQS_BT_DEVICES, [_BT_BATTERY], DeviceInformationKind.DEVICE)
            for d in devs:
                if (d.name or "").startswith(name) and d.properties.has_key(_BT_BATTERY):
                    pct = _unbox(d.properties.lookup(_BT_BATTERY), "uint8")
                    if pct is not None:
                        return int(pct)
        return None
