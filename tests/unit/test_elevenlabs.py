from __future__ import annotations

import json
import wave

import httpx
import pytest
from pydantic import ValidationError

from morning_radio.audio.elevenlabs import ElevenLabsTTS
from morning_radio.audio.production import resolve_voices, synthesize_script
from morning_radio.audio.tts import TTSError, build_tts_adapter
from morning_radio.dependencies import check_tts
from morning_radio.settings import TTSSettings, load_production_settings

VOICE = "AkzTpEeeEWvyZf4umyCJ"


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret-never-log")
    monkeypatch.delenv("MORNING_RADIO_FAKE_TTS", raising=False)
    return TTSSettings(engine="elevenlabs", voice=VOICE, speed=1)


def test_pcm_request_writes_exact_wav_and_caches_voice_lookup(settings, tmp_path):
    calls = []
    pcm = b"\x01\x00" * 24000

    def respond(request):
        calls.append(request)
        assert request.headers["xi-api-key"] == "test-secret-never-log"
        if request.method == "GET":
            return httpx.Response(200, json={"voice_id": VOICE, "name": "Nathaniel C"})
        assert request.url.params["output_format"] == "pcm_24000"
        payload = json.loads(request.content)
        assert payload["model_id"] == "eleven_multilingual_v2"
        assert payload["voice_settings"]["speed"] == 0.9
        assert payload["text"] == "Hello AI."
        return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = ElevenLabsTTS(settings, client=client)
        assert adapter.available_voices() == [VOICE]
        assert adapter.available_voices() == [VOICE]
        path = tmp_path / "speech.wav"
        result = adapter.synthesize("Hello AI.", VOICE, path, speed=0.9)
        assert result.duration_seconds == 1
        with wave.open(str(path)) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
            assert wav.readframes(24000) == pcm
        assert len(calls) == 2
        adapter.close()
        assert not client.is_closed
        assert "test-secret" not in adapter.engine_version


@pytest.mark.parametrize("status", [401, 402, 403, 404, 422, 429, 500, 302])
def test_http_failures_are_sanitized_not_retried_and_leave_no_audio(settings, tmp_path, status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"secret": "test-secret-never-log"},
            headers={"location": "https://other.invalid"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = ElevenLabsTTS(settings, client=client)
        with pytest.raises(TTSError, match=f"HTTP {status}") as error:
            adapter.synthesize("Hello.", VOICE, tmp_path / "speech.wav")
        assert "test-secret" not in str(error.value)
        assert len(calls) == 1
        assert not list(tmp_path.iterdir())


def test_timeout_never_echoes_http_exception_or_retries(settings, tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-secret-never-log", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = ElevenLabsTTS(settings, client=client)
        with pytest.raises(TTSError, match="No automatic retry") as error:
            adapter.synthesize("Hello.", VOICE, tmp_path / "speech.wav")
        assert "test-secret" not in str(error.value)
        assert len(calls) == 1


@pytest.mark.parametrize(
    "body,content_type", [(b"", "audio/pcm"), (b"a", "audio/pcm"), (b"{}", "application/json")]
)
def test_bad_audio_does_not_replace_existing_file(settings, tmp_path, body, content_type):
    path = tmp_path / "speech.wav"
    path.write_bytes(b"existing")
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=body, headers={"content-type": content_type})
            )
        ) as client,
        pytest.raises(TTSError),
    ):
        ElevenLabsTTS(settings, client=client).synthesize("Hello.", VOICE, path)
    assert path.read_bytes() == b"existing"
    assert len(list(tmp_path.iterdir())) == 1


def test_bounded_audio(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("morning_radio.audio.elevenlabs.MAX_AUDIO_BYTES", 2)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"abcd"))
        ) as client,
        pytest.raises(TTSError, match="size limit"),
    ):
        ElevenLabsTTS(settings, client=client).synthesize("Hello.", VOICE, tmp_path / "speech.wav")


@pytest.mark.parametrize("payload", [{"voice_id": "other", "name": "Wrong"}, {}, []])
def test_voice_metadata_must_match_configured_id(settings, payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client,
        pytest.raises(TTSError),
    ):
        ElevenLabsTTS(settings, client=client).available_voices()


def test_missing_key_is_actionable_before_news_work(settings, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY"):
        build_tts_adapter("elevenlabs", settings=settings)
    checks = check_tts(load_production_settings())
    assert not checks[0].ok
    assert "ELEVENLABS_API_KEY" in checks[0].detail


@pytest.mark.parametrize(
    "updates",
    [
        {"speed": 2},
        {"speed": 0.6},
        {"voice": None},
        {"voice": "../bad"},
        {"secondary_voice": "af_bella"},
        {"elevenlabs": {"api_key": "not-allowed"}},
    ],
)
def test_invalid_provider_configuration_rejected(settings, updates):
    with pytest.raises(ValidationError):
        TTSSettings.model_validate(settings.model_dump() | updates)


def test_model_and_voice_settings_affect_hash(settings):
    with httpx.Client() as client:
        first = ElevenLabsTTS(settings, client=client)
        changed = settings.model_copy(deep=True)
        changed.elevenlabs.model_id = "eleven_flash_v2_5"
        second = ElevenLabsTTS(changed, client=client)
        assert first.engine_version != second.engine_version


def test_owned_client_closes_on_synthesis_failure(settings, monkeypatch, tmp_path):
    import morning_radio.audio.production as production

    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401)))
    adapter = ElevenLabsTTS(settings, client=client)
    adapter._owns_client = True
    monkeypatch.setattr(production, "build_tts_adapter", lambda *args, **kwargs: adapter)
    with pytest.raises(TTSError):
        synthesize_script("[HOST]\nHello.\n", load_production_settings(), tmp_path)
    assert client.is_closed


def test_elevenlabs_does_not_inherit_kokoro_secondary_voice():
    from morning_radio.profile.compiler import default_profile

    profile = default_profile()
    profile.voice_preferences.secondary_voice = "af_bella"
    assert resolve_voices(load_production_settings(), profile) == (VOICE, VOICE)
