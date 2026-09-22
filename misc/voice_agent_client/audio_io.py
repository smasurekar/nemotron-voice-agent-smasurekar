"""Read, normalize, and write the mono PCM16 the Realtime wire format expects.

The gateway accepts mono signed PCM16 at 8, 16, or 24 kHz and defaults to
24 kHz. Anything else has to be converted before it reaches
`input_audio_buffer.append`, so this module downmixes to mono and resamples to
the session's wire rate.

Resampling matters for scores, not just for playback: the evaluator pins its
resampler (`librosa-soxr_hq-v0.10.2`) and records it in every run's provenance
because two resamplers give two different word error rates. This module uses
soxr's HQ band, the same kernel behind that pin, and reports which path it took
so a run here can be compared with one from the evaluator.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path


class AudioFormatError(ValueError):
    """The input audio cannot satisfy the Realtime wire contract."""


@dataclass(frozen=True, slots=True)
class WireAudio:
    """A gateway-ready mono PCM16 buffer and where it came from."""

    pcm16: bytes
    sample_rate_hz: int
    source_path: str
    source_sample_rate_hz: int
    source_channels: int
    resampler: str

    @property
    def duration_s(self) -> float:
        return len(self.pcm16) / 2 / self.sample_rate_hz

    def describe(self) -> str:
        """One line for the run report. Name the conversion only if there was one."""
        head = (
            f"{Path(self.source_path).name}   {self.duration_s:.2f}s mono PCM16 @ "
            f"{self.sample_rate_hz / 1000:g} kHz"
        )
        if self.resampler == "none":
            return head
        return f"{head}   (from {self.source_sample_rate_hz / 1000:g} kHz x{self.source_channels}, {self.resampler})"


def load_wav(path: str | Path, *, target_rate_hz: int) -> WireAudio:
    """Load a WAV file as mono PCM16 at ``target_rate_hz``."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise AudioFormatError(
            f"{path} is not a readable PCM WAV file ({exc}); convert it first:\n"
            f"    ffmpeg -i {path} -ac 1 -ar {target_rate_hz} -sample_fmt s16 out.wav"
        ) from exc

    if width != 2:
        raise AudioFormatError(
            f"{path} is {width * 8}-bit; the Realtime wire format is 16-bit signed PCM"
        )
    if not frames:
        raise AudioFormatError(f"{path} holds no audio frames")

    pcm16, resampler = _to_mono_pcm16(frames, channels=channels, rate=rate, target_rate_hz=target_rate_hz)
    return WireAudio(
        pcm16=pcm16,
        sample_rate_hz=target_rate_hz,
        source_path=str(path),
        source_sample_rate_hz=rate,
        source_channels=channels,
        resampler=resampler,
    )


def _to_mono_pcm16(
    frames: bytes,
    *,
    channels: int,
    rate: int,
    target_rate_hz: int,
) -> tuple[bytes, str]:
    """Downmix and resample, staying on the stdlib when nothing has to change."""
    if channels == 1 and rate == target_rate_hz:
        return frames, "none"

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise AudioFormatError(
            f"the input is {rate} Hz x{channels} and must be converted to "
            f"{target_rate_hz} Hz mono, which needs numpy (and soxr for the rate "
            "change). Install them, or pre-convert with:\n"
            f"    ffmpeg -i in.wav -ac 1 -ar {target_rate_hz} -sample_fmt s16 out.wav"
        ) from exc

    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        usable = len(samples) - (len(samples) % channels)
        # Equal-weight downmix in float so a loud stereo pair cannot wrap.
        samples = samples[:usable].reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        samples = samples.astype(np.float32)

    resampler = "downmix"
    if rate != target_rate_hz:
        try:
            import soxr
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise AudioFormatError(
                f"resampling {rate} -> {target_rate_hz} Hz needs the 'soxr' package. "
                "Install it, or pre-convert the file with ffmpeg."
            ) from exc
        samples = soxr.resample(samples, rate, target_rate_hz, quality="HQ")
        resampler = f"soxr-hq {rate}->{target_rate_hz}"

    clipped = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    return clipped.tobytes(), resampler


def write_wav(path: str | Path, pcm16: bytes, *, sample_rate_hz: int) -> Path:
    """Write mono PCM16 out so the reply can be played back or re-transcribed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(pcm16)
    return path


def silence(*, sample_rate_hz: int, duration_ms: int) -> bytes:
    """A PCM16 silence buffer, used to pad a turn so VAD can end it."""
    return b"\x00" * (int(sample_rate_hz * duration_ms / 1000) * 2)
