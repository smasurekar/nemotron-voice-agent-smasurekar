"""Shared paths and helpers for the TTS quality analysis. The Hub key is loaded in-process via
model-latency/bench_common.hub_key(); it is never printed or written."""

from __future__ import annotations

import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
AUDIO = RES / "audio"
sys.path.insert(0, str(HERE.parent / "model-latency"))

ARMS = {  # arm -> (label, native sample rate)
    "magpie_john": ("Magpie (local NeMo Speech), voice John", 22050),
    "gpt4omini_ash": ("gpt-4o-mini-tts (Hub), voice ash + persona instructions", 24000),
    "gpt4omini_coral": ("gpt-4o-mini-tts (Hub), voice coral + persona instructions", 24000),
}
REPEATS = 2


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wav_path(arm: str, sid: str, rep: int) -> Path:
    return AUDIO / arm / f"{sid}_r{rep}.wav"


def write_wav(path: Path, pcm16: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm16)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()


def to_16k(x: np.ndarray, rate: int) -> np.ndarray:
    from math import gcd

    from scipy.signal import resample_poly
    g = gcd(16000, rate)
    y = resample_poly(x.astype(np.float32), 16000 // g, rate // g)
    return np.clip(y, -32768, 32767).astype(np.int16)
