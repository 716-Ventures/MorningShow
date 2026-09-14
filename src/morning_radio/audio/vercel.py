"""Gateway speech beta: bounded JSON/base64 responses, validated WAV, no paid retries."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import wave
from pathlib import Path

import httpx

from morning_radio.audio.tts import TTSError, speech_hash
from morning_radio.credentials import read_credential
from morning_radio.models import AudioMetadata
from morning_radio.settings import VERCEL_VOICES, TTSSettings

ENDPOINT = "https://ai-gateway.vercel.sh/v4/ai/speech-model"
MAX_RESPONSE_BYTES = 32_000_000


class VercelTTS:
    def __init__(self, settings: TTSSettings, *, client: httpx.Client | None = None):
        key = read_credential("AI_GATEWAY_API_KEY")
        if not key:
            raise TTSError("Set AI_GATEWAY_API_KEY in the project .env file.")
        self.settings = settings.vercel
        self.engine_version = "wav-v1:" + self.settings.model_dump_json()
        self._headers = {
            "Authorization": f"Bearer {key}",
            "ai-gateway-protocol-version": "0.0.1",
            "ai-speech-model-specification-version": "4",
            "ai-model-id": self.settings.model_id,
        }
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(
                timeout=self.settings.timeout_seconds, trust_env=False, follow_redirects=False
            )
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def available_voices(self) -> list[str]:
        """Supported names, not a claim of account access; no synthesis during preflight."""
        return list(VERCEL_VOICES)

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata:
        normalized = " ".join(text.split())
        if voice not in VERCEL_VOICES or not 0.25 <= speed <= 3:
            raise TTSError("Unsupported Vercel speech voice or speed.")
        if not normalized or len(normalized) > 4096:
            raise TTSError("Vercel speech chunks must contain 1-4,096 characters.")
        body = bytearray()
        try:
            with self._client.stream(
                "POST",
                ENDPOINT,
                headers=self._headers,
                json={"text": normalized, "voice": voice, "outputFormat": "wav", "speed": speed},
                timeout=self.settings.timeout_seconds,
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    raise TTSError(
                        f"Vercel AI Gateway speech returned HTTP {response.status_code}. Check AI_GATEWAY_API_KEY, "
                        "gateway credits, model access, and speech beta access for your team. No request was retried."
                    )
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise TTSError("Vercel speech response exceeded the per-chunk size limit.")
                    body.extend(chunk)
        except httpx.HTTPError:
            raise TTSError(
                "Vercel speech request failed or timed out. No automatic retry; check account usage before retrying."
            ) from None
        try:
            result = json.loads(body)
            if result.get("warnings"):
                raise TTSError(
                    "Vercel speech reported unsupported options or provider warnings. No audio was saved; check model settings before retrying."
                )
            audio = base64.b64decode(result["audio"], validate=True)
            with wave.open(io.BytesIO(audio), "rb") as wav:
                frames, rate = wav.getnframes(), wav.getframerate()
                frame_bytes = wav.getnchannels() * wav.getsampwidth()
                if not frames or not rate or len(wav.readframes(frames)) != frames * frame_bytes:
                    raise ValueError
                duration = frames / rate
        except (ValueError, KeyError, TypeError, AttributeError, wave.Error, EOFError):
            raise TTSError(
                "Vercel speech returned invalid, empty, or incomplete WAV audio."
            ) from None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, suffix=".wav", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            temporary.write_bytes(audio)
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)
        return AudioMetadata(
            voice=voice,
            text_hash=speech_hash("vercel", self.engine_version, voice, speed, normalized),
            duration_seconds=duration,
            path=output_path,
        )
