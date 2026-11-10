"""WAV decoding (stdlib wave + numpy) and the 16 kHz -> 48 kHz resampling CLAP needs."""
from __future__ import annotations

import io
import wave

import numpy as np
from scipy.signal import resample_poly

CLIP_RATE = 16000
CLAP_RATE = 48000


class ClipError(Exception):
    """The clip could not be fetched or decoded; the event is stored with inference.error."""


def decode_wav(data: bytes) -> np.ndarray:
    """16-bit PCM mono 16 kHz WAV -> float32 samples in [-1, 1]."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ClipError(f"undecodable WAV: {exc}") from exc
    if width != 2:
        raise ClipError(f"unsupported WAV: {width * 8}-bit (expected 16-bit PCM)")
    if rate != CLIP_RATE:
        raise ClipError(f"unsupported WAV: {rate} Hz (expected {CLIP_RATE})")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    if samples.size == 0:
        raise ClipError("empty WAV")
    return samples


def resample_to_48k(samples: np.ndarray, rate: int = CLIP_RATE) -> np.ndarray:
    """Polyphase resampling; 16 kHz -> 48 kHz is an exact 3:1 ratio."""
    if rate == CLAP_RATE:
        return samples.astype(np.float32)
    g = np.gcd(CLAP_RATE, rate)
    return resample_poly(samples, CLAP_RATE // g, rate // g).astype(np.float32)
