"""Sound sources for the simulated cars: the professor's dataset folders when they are there,
otherwise synthetic 5 s clips of the seven classes, so the simulator always runs.

    datasets/<class>/*.wav   any WAV (PCM 16-bit, any rate, mono or stereo); converted to 16 kHz mono
    synthetic(cls, rng)      a recognizable stand-in: siren sweep, crash, glass, scream, horn, traffic, wind
"""
from __future__ import annotations

import io
import random
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

RATE = 16000
SECONDS = 5
CLASSES = ("siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental")


def to_wav(samples: np.ndarray, rate: int = RATE) -> bytes:
    pcm = np.clip(samples, -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def read_wav(path: Path) -> Optional[np.ndarray]:
    """A 16-bit PCM WAV as float mono 16 kHz, or None when the format is not supported."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return None
            rate, channels = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    except (wave.Error, EOFError, OSError):
        return None
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(data):
        x = np.linspace(0, len(data) - 1, int(len(data) * RATE / rate))
        data = np.interp(x, np.arange(len(data)), data).astype(np.float32)
    return data


def fit(data: np.ndarray, rng: random.Random) -> np.ndarray:
    """Cut or pad to exactly 5 s (a random 5 s window of a longer file)."""
    n = RATE * SECONDS
    if len(data) >= n:
        start = rng.randint(0, len(data) - n)
        return data[start:start + n]
    out = np.zeros(n, dtype=np.float32)
    offset = rng.randint(0, n - len(data))
    out[offset:offset + len(data)] = data
    return out


def _noise(n: int, rng: np.random.Generator, color: str = "white") -> np.ndarray:
    white = rng.standard_normal(n).astype(np.float32)
    if color == "brown":
        x = np.cumsum(white)
        x -= np.convolve(x, np.ones(800) / 800, mode="same")
        return x / (np.abs(x).max() + 1e-9)
    if color == "pink":
        spectrum = np.fft.rfft(white)
        spectrum /= np.sqrt(np.arange(1, len(spectrum) + 1))
        x = np.fft.irfft(spectrum, n)
        return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)
    return white / 4


def synthetic(cls: str, rng: random.Random) -> np.ndarray:
    """A 5 s clip of the class: 2 s of street background, then the sound (the agent's 2 s pre-roll)."""
    g = np.random.default_rng(rng.randint(0, 2**31))
    n = RATE * SECONDS
    t = np.arange(n) / RATE
    out = 0.05 * _noise(n, g, "brown")
    start = int(2.0 * RATE)
    s = t[start:] - t[start]
    if cls == "siren":
        freq = 950 + 400 * np.sin(2 * np.pi * 0.5 * s)
        phase = 2 * np.pi * np.cumsum(freq) / RATE
        out[start:] += 0.5 * np.sign(np.sin(phase)) * 0.6 + 0.2 * np.sin(phase)
    elif cls == "crash":
        env = np.exp(-s * 2.5)
        thud = np.sin(2 * np.pi * 60 * s) * np.exp(-s * 8)
        out[start:] += 0.9 * env * _noise(len(s), g) * 4 * 0.6 + 0.7 * thud
    elif cls == "glass_break":
        burst = _noise(len(s), g) * 4 * np.exp(-s * 6)
        tinkles = sum(np.sin(2 * np.pi * f * s) * np.exp(-np.maximum(s - d, 0) * 9) * (s > d)
                      for f, d in ((4200, 0.05), (5300, 0.21), (6100, 0.38), (3700, 0.52), (4800, 0.7)))
        out[start:] += 0.5 * burst + 0.15 * tinkles
    elif cls == "scream":
        f0 = 900 + 120 * np.sin(2 * np.pi * 6 * s) + 200 * np.minimum(s, 0.5)
        phase = 2 * np.pi * np.cumsum(f0) / RATE
        env = np.minimum(1, s * 8) * np.exp(-np.maximum(s - 1.6, 0) * 3)
        out[start:] += 0.5 * env * (np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.2 * np.sin(3 * phase))
    elif cls == "honking":
        env = ((s % 1.2) < 0.6).astype(np.float32)
        out[start:] += 0.35 * env * (np.sign(np.sin(2 * np.pi * 420 * s)) + np.sign(np.sin(2 * np.pi * 500 * s)))
    elif cls == "traffic":
        out = 0.35 * _noise(n, g, "brown") + 0.08 * np.sin(2 * np.pi * 95 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t))
    else:  # environmental: wind and birds
        out = 0.25 * _noise(n, g, "pink")
        for k in range(4):
            c = rng.uniform(0.3, 4.5)
            chirp = (t > c) & (t < c + 0.15)
            out[chirp] += 0.25 * np.sin(2 * np.pi * (3000 + 4000 * (t[chirp] - c)) * (t[chirp] - c))
    peak = np.abs(out).max() + 1e-9
    return (out / peak * 0.85).astype(np.float32)


class SoundBank:
    """Picks the next clip: a dataset file of a random class when the folder has one, else synthetic."""

    def __init__(self, datasets_dir: Optional[str], rng: random.Random, weights: Optional[Dict[str, float]] = None) -> None:
        self.rng = rng
        self.files: Dict[str, List[Path]] = {}
        if datasets_dir and Path(datasets_dir).is_dir():
            for cls in CLASSES:
                folder = Path(datasets_dir) / cls
                if folder.is_dir():
                    self.files[cls] = sorted(folder.rglob("*.wav"))
        self.weights = weights or {"traffic": 4, "environmental": 3, "honking": 2, "siren": 1.5, "glass_break": 1,
                                   "scream": 1, "crash": 0.7}

    def pick(self, cls: Optional[str] = None) -> Tuple[str, np.ndarray, str]:
        """(class, samples, source) with source 'dataset:<file>' or 'synthetic'."""
        if cls is None:
            classes = list(self.weights)
            cls = self.rng.choices(classes, [self.weights[c] for c in classes])[0]
        for _ in range(3):
            files = self.files.get(cls)
            if not files:
                break
            path = self.rng.choice(files)
            data = read_wav(path)
            if data is not None and len(data) > RATE // 2:
                return cls, fit(data, self.rng), f"dataset:{path.name}"
        return cls, synthetic(cls, self.rng), "synthetic"
