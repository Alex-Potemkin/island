"""Notification sounds: synthesised into small WAV files on first use (no bundled audio, no licences),
or the user's own .wav. Playback is asynchronous: winsound on Windows, paplay/pw-play/aplay on Linux."""
import array
import math
import os
import shutil
import subprocess
import sys
import wave

from . import config, osapi

RATE = 44100

SOUNDS = {  # key: title
    "drop": "Капля", "bell": "Колокольчик", "chime": "Перезвон", "marimba": "Маримба", "pop": "Щелчок",
    "pulse": "Пульс", "soft": "Мягкий", "system": "Системный", "none": "Без звука",
}


def _env(n, attack=0.004, decay=6.0):
    """Exponential decay with a short linear attack."""
    a = int(RATE * attack)
    return [min(1.0, i / a if a else 1.0) * math.exp(-decay * i / RATE) for i in range(n)]


def _tone(freqs, dur, decay=6.0, sweep=None):
    """Sum of partials [(freq, amp)], optional linear frequency sweep to `sweep`× by the end."""
    n = int(RATE * dur)
    env = _env(n, decay=decay)
    out = [0.0] * n
    for f, amp in freqs:
        ph = 0.0
        for i in range(n):
            k = 1 + (sweep - 1) * i / n if sweep else 1
            ph += 2 * math.pi * f * k / RATE
            out[i] += amp * math.sin(ph) * env[i]
    return out


def _seq(*parts, gap=0.0):
    out = []
    for p in parts:
        out += p + [0.0] * int(RATE * gap)
    return out


def synth(name):
    if name == "drop":
        return _tone([(1250, 1.0), (2500, 0.15)], 0.22, decay=16, sweep=0.55)
    if name == "bell":
        return _tone([(880, 1.0), (1760, 0.45), (2640, 0.25), (3520, 0.1)], 1.1, decay=4.5)
    if name == "chime":
        return _seq(_tone([(1318.5, 1.0), (2637, 0.2)], 0.14, decay=9),
                    _tone([(1975.5, 1.0), (3951, 0.15)], 0.6, decay=6))
    if name == "marimba":
        return _seq(*[_tone([(f, 1.0), (f * 4, 0.12)], 0.11 if i < 2 else 0.45, decay=14 if i < 2 else 9)
                      for i, f in enumerate((1046.5, 1318.5, 1568.0))])
    if name == "pop":
        return _tone([(420, 1.0), (840, 0.3)], 0.07, decay=60, sweep=0.6)
    if name == "pulse":
        return _seq(_tone([(1000, 1.0)], 0.07, decay=20), _tone([(1000, 1.0)], 0.07, decay=20), gap=0.07)
    if name == "soft":
        return _tone([(523.3, 1.0), (784, 0.3)], 0.55, decay=5)
    return []


def sound_file(name, volume=80):
    """Path of a WAV for a built-in sound at this volume, generated once into the data folder."""
    folder = os.path.join(config.DATA_DIR, "sounds")
    path = os.path.join(folder, f"{name}_{int(volume)}.wav")
    if os.path.exists(path):
        return path
    samples = synth(name)
    if not samples:
        return None
    os.makedirs(folder, exist_ok=True)
    peak = max(1e-9, max(abs(x) for x in samples))
    gain = 0.85 * (max(0, min(100, volume)) / 100.0) ** 1.6 / peak   # perceptual-ish volume curve
    pcm = array.array("h", (int(max(-1.0, min(1.0, x * gain)) * 32767) for x in samples))
    if sys.byteorder == "big":
        pcm.byteswap()
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return path


def play(name, volume=80, custom=""):
    """Play a sound without blocking. name: one of SOUNDS, or "file" with custom = path to a .wav."""
    if name == "none":
        return
    if name == "system":
        osapi.beep()
        return
    path = custom if name == "file" else sound_file(name, volume)
    if not path or not os.path.exists(path):
        osapi.beep()
        return
    if osapi.IS_WINDOWS:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return
    for cmd in (["pw-play", path], ["paplay", path], ["aplay", "-q", path]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    osapi.beep()
