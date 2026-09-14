from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
import re
import shutil
import unicodedata
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from morning_radio.models import AudioMetadata
from morning_radio.settings import TTSSettings


class TTSError(RuntimeError):
    pass


def prepare_tts_text(text: str, pronunciation_overrides: dict[str, str] | None = None) -> str:
    spoken = unicodedata.normalize("NFKC", text)
    spoken = spoken.replace("\u2018", "'").replace("\u2019", "'")
    spoken = spoken.replace("\u201c", '"').replace("\u201d", '"')
    spoken = re.sub(r"\s*[\u2013\u2014]\s*", ", ", spoken)
    spoken = spoken.replace("&", " and ")

    protected_replacements: dict[str, str] = {}
    for index, (source, replacement) in enumerate(
        sorted(
            (pronunciation_overrides or {}).items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
    ):
        placeholder = f"pronunciationoverride{index}token"
        protected_replacements[placeholder] = replacement
        pattern = rf"(?<![\w]){re.escape(source)}(?![\w])"
        spoken = re.sub(
            pattern, lambda _match, value=placeholder: value, spoken, flags=re.IGNORECASE
        )

    spoken = re.sub(
        r"\bA\$(\d+(?:\.\d+)?)\s*(b|bn|m)\b",
        lambda match: _spoken_amount(match.group(1), match.group(2), "Australian dollars"),
        spoken,
        flags=re.IGNORECASE,
    )
    spoken = re.sub(
        r"\$(\d+(?:\.\d+)?)\s*(b|bn|m)\b",
        lambda match: _spoken_amount(match.group(1), match.group(2), "dollars"),
        spoken,
        flags=re.IGNORECASE,
    )
    spoken = re.sub(r"(?<=\d)%", " percent", spoken)
    spoken = re.sub(r"\bNo\.\s*(\d+)", r"number \1", spoken, flags=re.IGNORECASE)
    for placeholder, replacement in protected_replacements.items():
        spoken = spoken.replace(placeholder, replacement)
    spoken = re.sub(
        r"\b(?:[A-Z]\s+){1,4}[A-Z]\b",
        lambda match: "".join(match.group(0).split()),
        spoken,
    )
    return " ".join(spoken.split())


def _spoken_amount(number: str, suffix: str, currency: str) -> str:
    magnitude = "billion" if suffix.casefold() in {"b", "bn"} else "million"
    return f"{number} {magnitude} {currency}"


class TTSAdapter(Protocol):
    engine_version: str

    def available_voices(self) -> list[str]: ...

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata: ...


class ToneTTS:
    """Deterministic local fallback used when Kokoro is unavailable."""

    engine_version = "tone-v1"

    def available_voices(self) -> list[str]:
        return ["tone"]

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata:
        if voice not in self.available_voices():
            raise TTSError(f"Voice unavailable: {voice}")
        normalized = " ".join(text.split())
        text_hash = speech_hash("tone", self.engine_version, voice, speed, normalized)
        duration = max(1.0, min(18.0, len(normalized.split()) / (2.4 * speed)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_tone_wav(output_path, duration)
        return AudioMetadata(
            voice=voice, text_hash=text_hash, duration_seconds=duration, path=output_path
        )


def _write_tone_wav(path: Path, duration_seconds: float, sample_rate: int = 44100) -> None:
    frames = int(duration_seconds * sample_rate)
    samples = array(
        "h",
        (
            int(12000 * math.sin(2 * math.pi * 220 * (index / sample_rate)))
            for index in range(frames)
        ),
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def kokoro_importable() -> bool:
    try:
        __import__("kokoro")
    except ImportError:
        return False
    return True


AudioWriter = Callable[[Path, list[Any], int], float]
PipelineFactory = Callable[..., Any]


class KokoroTTS:
    def __init__(
        self,
        pipeline_factory: PipelineFactory | None = None,
        audio_writer: AudioWriter | None = None,
    ):
        if pipeline_factory is None:
            try:
                from kokoro import KPipeline  # type: ignore
            except ImportError as exc:
                raise TTSError(
                    "Kokoro is not importable. Install the local Kokoro TTS backend."
                ) from exc
            pipeline_factory = KPipeline
        if pipeline_factory is None:
            raise TTSError("Kokoro pipeline factory is unavailable.")
        self._pipeline_factory: PipelineFactory = pipeline_factory
        self._audio_writer = audio_writer or write_kokoro_audio
        self._pipelines: dict[str, Any] = {}
        try:
            self.engine_version = importlib.metadata.version("kokoro")
        except importlib.metadata.PackageNotFoundError:
            self.engine_version = "kokoro-unknown"

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

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata:
        if voice not in self.available_voices():
            raise TTSError(f"Voice unavailable: {voice}")
        normalized = " ".join(text.split())
        text_hash = speech_hash("kokoro", self.engine_version, voice, speed, normalized)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline = self._pipeline_for("a")
        chunks = []
        for _, _, audio in pipeline(normalized, voice=voice, speed=speed):
            chunks.append(audio)
        if not chunks:
            raise TTSError("Kokoro returned no audio.")
        duration = self._audio_writer(output_path, chunks, 24000)
        return AudioMetadata(
            voice=voice, text_hash=text_hash, duration_seconds=duration, path=output_path
        )

    def _pipeline_for(self, lang_code: str) -> Any:
        if lang_code not in self._pipelines:
            self._pipelines[lang_code] = self._pipeline_factory(lang_code=lang_code)
        return self._pipelines[lang_code]


def write_kokoro_audio(output_path: Path, chunks: list[Any], sample_rate: int) -> float:
    try:
        import soundfile as sf  # type: ignore
    except ImportError as exc:
        raise TTSError("Kokoro synthesis requires the soundfile package.") from exc
    import numpy as np  # type: ignore

    waveform = np.concatenate(chunks)
    sf.write(output_path, waveform, sample_rate)
    return len(waveform) / sample_rate


def speech_hash(
    engine: str, engine_version: str, voice: str, speed: float, normalized_text: str
) -> str:
    return hashlib.sha256(
        f"{engine}:{engine_version}:{voice}:{speed}:{normalized_text}".encode()
    ).hexdigest()


def build_tts_adapter(engine: str, *, settings: TTSSettings | None = None) -> TTSAdapter:
    if os.environ.get("MORNING_RADIO_FAKE_TTS") == "1" or engine == "tone":
        return ToneTTS()
    if engine == "kokoro":
        return KokoroTTS()
    if engine == "elevenlabs":
        from morning_radio.audio.elevenlabs import ElevenLabsTTS

        if settings is None:
            raise TTSError("ElevenLabs requires TTS settings and a Voice ID.")
        return ElevenLabsTTS(settings)
    if engine == "vercel":
        from morning_radio.audio.vercel import VercelTTS

        if settings is None:
            raise TTSError("Vercel speech requires TTS settings and a voice.")
        return VercelTTS(settings)
    raise TTSError(f"Unsupported TTS engine: {engine}")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
