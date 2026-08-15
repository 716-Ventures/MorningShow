from __future__ import annotations

import hashlib
import math
import os
import shutil
import wave
from pathlib import Path
from typing import Protocol

from morning_radio.models import AudioMetadata


class TTSError(RuntimeError):
    pass


class TTSAdapter(Protocol):
    def available_voices(self) -> list[str]:
        ...

    def synthesize(self, text: str, voice: str, output_path: Path, *, speed: float = 1.0) -> AudioMetadata:
        ...


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
    except ImportError:
        return False
    return True


class KokoroTTS:
    def __init__(self):
        try:
            from kokoro import KPipeline  # type: ignore
        except ImportError as exc:
            raise TTSError("Kokoro is not importable. Install the local Kokoro TTS backend.") from exc
        self._pipeline_factory = KPipeline

    def available_voices(self) -> list[str]:
        return [
            "af_heart",
            "af_bella",
            "af_nicole",
            "am_adam",
            "am_michael",
            "bf_emma",
            "bm_george",
        ]

    def synthesize(self, text: str, voice: str, output_path: Path, *, speed: float = 1.0) -> AudioMetadata:
        try:
            import soundfile as sf  # type: ignore
        except ImportError as exc:
            raise TTSError("Kokoro synthesis requires the soundfile package.") from exc
        if voice not in self.available_voices():
            raise TTSError(f"Voice unavailable: {voice}")
        normalized = " ".join(text.split())
        text_hash = hashlib.sha256(f"{voice}:{speed}:{normalized}".encode()).hexdigest()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline = self._pipeline_factory(lang_code="a")
        chunks = []
        for _, _, audio in pipeline(normalized, voice=voice, speed=speed):
            chunks.append(audio)
        if not chunks:
            raise TTSError("Kokoro returned no audio.")
        import numpy as np  # type: ignore

        waveform = np.concatenate(chunks)
        sf.write(output_path, waveform, 24000)
        duration = len(waveform) / 24000
        return AudioMetadata(voice=voice, text_hash=text_hash, duration_seconds=duration, path=output_path)


def build_tts_adapter(engine: str) -> TTSAdapter:
    if os.environ.get("MORNING_RADIO_FAKE_TTS") == "1" or engine == "tone":
        return ToneTTS()
    if engine == "kokoro":
        return KokoroTTS()
    raise TTSError(f"Unsupported TTS engine: {engine}")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
