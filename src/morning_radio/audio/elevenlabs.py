"""ElevenLabs REST adapter. Secrets never enter settings, hashes, or diagnostics."""

from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from morning_radio.audio.tts import TTSError, speech_hash
from morning_radio.credentials import read_credential
from morning_radio.models import AudioMetadata
from morning_radio.settings import TTSSettings

API_BASE = "https://api.elevenlabs.io/v1"
SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = 32_000_000


class VoiceInfo(BaseModel):
    voice_id: str
    name: str


class ElevenLabsTTS:
    def __init__(self, settings: TTSSettings, *, client: httpx.Client | None = None):
        key = read_credential("ELEVENLABS_API_KEY")
        if not key:
            raise TTSError(
                "Set ELEVENLABS_API_KEY in the project .env file before using ElevenLabs."
            )
        self.settings = settings.elevenlabs
        self._voice_ids = list(
            dict.fromkeys(voice for voice in (settings.voice, settings.secondary_voice) if voice)
        )
        self.engine_version = "pcm24000-v1:" + self.settings.model_dump_json()
        self._headers = {"xi-api-key": key}
        self._client = (
            client
            if client is not None
            else httpx.Client(
                timeout=self.settings.timeout_seconds, trust_env=False, follow_redirects=False
            )
        )
        self._owns_client = client is None
        self._voices: list[str] | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def available_voices(self) -> list[str]:
        if self._voices is None:
            voices = []
            for voice in self._voice_ids:
                try:
                    response = self._client.get(
                        f"{API_BASE}/voices/{voice}",
                        headers=self._headers,
                        timeout=self.settings.timeout_seconds,
                        follow_redirects=False,
                    )
                    _check_status(response)
                    info = VoiceInfo.model_validate(response.json())
                    if info.voice_id != voice:
                        raise TTSError("ElevenLabs returned a different Voice ID.")
                    voices.append(info.voice_id)
                except httpx.HTTPError:
                    raise TTSError(
                        "ElevenLabs voice lookup failed. Check your network and retry."
                    ) from None
                except (ValueError, ValidationError):
                    raise TTSError("ElevenLabs returned invalid voice metadata.") from None
            self._voices = voices
        return list(self._voices)

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata:
        if voice not in self._voice_ids:
            raise TTSError("Requested ElevenLabs voice is not configured.")
        if not 0.7 <= speed <= 1.2:
            raise TTSError("ElevenLabs speed must be between 0.7 and 1.2.")
        normalized = " ".join(text.split())
        if not normalized or len(normalized) > 10000:
            raise TTSError("ElevenLabs speech chunks must contain 1-10,000 characters.")
        payload = {
            "text": normalized,
            "model_id": self.settings.model_id,
            "voice_settings": {
                "stability": self.settings.stability,
                "similarity_boost": self.settings.similarity_boost,
                "style": self.settings.style,
                "use_speaker_boost": self.settings.use_speaker_boost,
                "speed": speed,
            },
        }
        audio = bytearray()
        try:
            # Do not automatically retry a paid POST: a lost response may already be billed.
            with self._client.stream(
                "POST",
                f"{API_BASE}/text-to-speech/{voice}",
                params={"output_format": "pcm_24000"},
                headers=self._headers,
                json=payload,
                timeout=self.settings.timeout_seconds,
                follow_redirects=False,
            ) as response:
                _check_status(response)
                content_type = response.headers.get("content-type", "").lower()
                if "json" in content_type or content_type.startswith("text/"):
                    raise TTSError("ElevenLabs returned text instead of PCM audio.")
                for chunk in response.iter_bytes():
                    if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                        raise TTSError("ElevenLabs audio exceeded the per-chunk size limit.")
                    audio.extend(chunk)
        except httpx.HTTPError:
            raise TTSError(
                "ElevenLabs speech request failed or timed out. No automatic retry was made; "
                "check account usage before retrying."
            ) from None
        if not audio or len(audio) % 2:
            raise TTSError("ElevenLabs returned empty or incomplete 16-bit PCM audio.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, suffix=".wav", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with wave.open(str(temporary), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(audio)
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)
        return AudioMetadata(
            voice=voice,
            text_hash=speech_hash("elevenlabs", self.engine_version, voice, speed, normalized),
            duration_seconds=len(audio) / (2 * SAMPLE_RATE),
            path=output_path,
        )


def _check_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    guidance = {
        401: "Check ELEVENLABS_API_KEY.",
        403: "Check API key permissions and access to the selected voice/model.",
        404: "Check the configured Voice ID and add the voice to My Voices if necessary.",
        402: "Check your ElevenLabs credits or subscription.",
        429: "Check your quota and rate limits before retrying.",
    }.get(response.status_code, "Check the voice/model settings or ElevenLabs service status.")
    try:
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            body.extend(chunk)
            if len(body) > 16_384:
                break
        data = json.loads(body) if len(body) <= 16_384 else None
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, dict):
            code = detail.get("status")
            if isinstance(code, str) and code in {
                "missing_permissions",
                "insufficient_permissions",
            }:
                message = detail.get("message", "")
                if isinstance(message, str) and "voices_read" in message:
                    guidance = "This key lacks Voices Read (voices_read). Enable Voices > Read in its ElevenLabs API key permissions."
                elif isinstance(message, str) and "text_to_speech" in message:
                    guidance = "This key lacks Text to Speech permission. Enable it in the ElevenLabs API key settings."
                else:
                    guidance = "This key lacks a required permission. Enable Voices Read and Text to Speech in its ElevenLabs API key settings."
            elif code == "quota_exceeded":
                guidance = "Your ElevenLabs quota is exhausted. Check credits and the key's character limit."
    except (ValueError, httpx.HTTPError):
        pass
    # Never echo remote bodies or HTTP exception reprs; they may contain supplied secrets.
    raise TTSError(f"ElevenLabs returned HTTP {response.status_code}. {guidance}")
