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
    "pulse": "Пульс", "soft": "Мягкий",
    # short melodies
    "arpeggio": "Арпеджио", "kalimba": "Калимба", "harp": "Арфа", "musicbox": "Музыкальная шкатулка",
    "doorbell": "Дверной звонок", "glass": "Хрусталь", "sunrise": "Рассвет", "success": "Успех",
    "breeze": "Бриз", "lullaby": "Колыбельная",
    "system": "Системный", "none": "Без звука",
}

# note -> Hz (A4 = 440)
_NOTES = {n: 440.0 * 2 ** ((i - 9) / 12 + (o - 4)) for o in range(3, 8)
          for i, base in enumerate("C C# D D# E F F# G G# A A# B".split()) for n in (f"{base}{o}",)}
TIMBRES = {  # partials (ratio, amp) and decay per second: soft instruments, no harsh overtones
    "piano": ([(1, 1.0), (2, 0.35), (3, 0.12), (4, 0.05)], 3.2),
    "bell": ([(1, 1.0), (2.76, 0.32), (5.4, 0.12), (8.93, 0.04)], 2.4),
    "kalimba": ([(1, 1.0), (5.9, 0.08), (2.0, 0.12)], 5.0),
    "glass": ([(1, 1.0), (2.32, 0.18), (4.25, 0.06)], 1.6),
    "pad": ([(1, 1.0), (2, 0.2), (0.5, 0.25)], 1.1),
    "box": ([(1, 1.0), (3.0, 0.22), (6.0, 0.05)], 4.0),
}


def _pluck(f, dur):
    """Karplus-Strong string: a harp/guitar-like pluck."""
    import random
    rnd = random.Random(int(f))
    period = max(2, int(RATE / f))
    buf = [rnd.uniform(-1, 1) for _ in range(period)]
    out = []
    for i in range(int(RATE * dur)):
        v = buf[i % period]
        nxt = buf[(i + 1) % period]
        buf[i % period] = 0.996 * 0.5 * (v + nxt)
        out.append(v)
    return out


def _melody(notes, timbre="piano", echo=0.22, length=None):
    """notes: [(start s, note name or Hz, duration s)] mixed together, with a soft attack and a short echo."""
    partials, decay = TIMBRES.get(timbre, TIMBRES["piano"])
    end = max(t + d for t, _n, d in notes)
    total = int(RATE * ((length or end) + (0.36 if echo else 0)))
    out = [0.0] * total
    for t, note, d in notes:
        f = _NOTES[note] if isinstance(note, str) else note
        start, n = int(RATE * t), int(RATE * d)
        attack = int(RATE * (0.05 if timbre == "pad" else 0.006))
        if timbre == "harp":
            wave_ = _pluck(f, d)
            for i, v in enumerate(wave_):
                if start + i < total:
                    out[start + i] += 0.8 * v * min(1.0, i / 30)
            continue
        for ratio, amp in partials:
            fr = f * ratio
            if fr > RATE / 2.2:
                continue
            w = 2 * math.pi * fr / RATE
            k = decay * (1 + 0.6 * (ratio - 1))     # higher partials fade faster: a rounder sound
            for i in range(n):
                j = start + i
                if j >= total:
                    break
                env = min(1.0, i / attack) * math.exp(-k * i / RATE)
                if i > n - 400:                          # no click at the end of a note
                    env *= (n - i) / 400
                out[j] += amp * env * math.sin(w * i)
    if echo:
        lag = int(RATE * 0.12)
        for j in range(total - 1, lag - 1, -1):
            out[j] += echo * out[j - lag]
    return out



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
    if name == "arpeggio":
        return _melody([(0, "C5", 1.2), (0.09, "E5", 1.1), (0.18, "G5", 1.0), (0.27, "C6", 1.1)], "piano")
    if name == "kalimba":
        return _melody([(0, "A5", 0.7), (0.13, "E6", 0.7), (0.26, "C#6", 0.9)], "kalimba")
    if name == "harp":
        return _melody([(i * 0.055, n, 1.1) for i, n in enumerate(("C5", "D5", "E5", "G5", "A5", "C6"))],
                       "harp", echo=0.15)
    if name == "musicbox":
        return _melody([(0, "E6", 0.5), (0.16, "G6", 0.5), (0.32, "C7", 0.5), (0.48, "B6", 0.5),
                        (0.64, "G6", 0.9)], "box")
    if name == "doorbell":
        return _melody([(0, "E5", 1.1), (0.42, "C5", 1.4)], "bell", echo=0.12)
    if name == "glass":
        return _melody([(0, "E6", 1.4), (0.11, "B6", 1.4)], "glass", echo=0.18)
    if name == "sunrise":
        return _melody([(0, "F4", 1.6), (0.12, "A4", 1.5), (0.24, "C5", 1.4), (0.36, "F5", 1.4)], "pad", echo=0.2)
    if name == "success":
        return _melody([(0, "G5", 0.35), (0.1, "C6", 0.9), (0.1, "E6", 0.9)], "piano", echo=0.15)
    if name == "breeze":
        return _melody([(0, "D6", 0.9), (0.18, "A5", 0.9), (0.36, "F#6", 1.1)], "glass", echo=0.25)
    if name == "lullaby":
        return _melody([(0, "G5", 0.6), (0.22, "E5", 0.6), (0.44, "G5", 0.6), (0.66, "C5", 1.2)], "box", echo=0.2)
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
    if name == "file":
        _play_path(custom)
        return
    cached = os.path.join(config.DATA_DIR, "sounds", f"{name}_{int(volume)}.wav")
    if os.path.exists(cached):
        _play_path(cached)
    else:   # a melody takes up to a second to synthesise the first time: not on the UI thread
        import threading
        threading.Thread(target=lambda: _play_path(sound_file(name, volume)), daemon=True).start()


def _play_path(path):
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
