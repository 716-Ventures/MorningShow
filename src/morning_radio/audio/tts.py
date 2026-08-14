from __future__ import annotations

import hashlib
import math
import shutil
import wave
from pathlib import Path

from morning_radio.models import AudioMetadata


class TTSError(RuntimeError):
    pass


class ToneTTS:
    """Deterministic local fallback used when Kokoro is unavailable."""

    def available_voices(self) -> list[str]:
        return ["tone"]

    def synthesize(self, text: str, voice: str, output_path: Path, *, speed: float = 1.0) -> AudioMetadata:
        if voice not in self.available_voices():
            raise TTSError(f"Voice unavailable: {voice}")
        normalized = " ".join(text.split())
        text_hash = hashlib.sha256(f"{voice}:{speed}:{normalized}".encode()).hexdigest()
        duration = max(1.0, min(18.0, len(normalized.split()) / (2.4 * speed)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_tone_wav(output_path, duration)
        return AudioMetadata(voice=voice, text_hash=text_hash, duration_seconds=duration, path=output_path)


def _write_tone_wav(path: Path, duration_seconds: float, sample_rate: int = 44100) -> None:
    frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            value = int(12000 * math.sin(2 * math.pi * 220 * (index / sample_rate)))
            handle.writeframesraw(value.to_bytes(2, "little", signed=True))


def kokoro_importable() -> bool:
    try:
        __import__("kokoro")
    except Exception:
        return False
    return True


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
