from __future__ import annotations

import base64
import io
import json
import shutil
import wave
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, HttpUrl, ValidationError
from typer.testing import CliRunner

from morning_radio.audio.production import resolve_voices, synthesize_script
from morning_radio.audio.tts import TTSError, build_tts_adapter
from morning_radio.audio.vercel import ENDPOINT, VercelTTS
from morning_radio.cli import app
from morning_radio.credentials import read_credential
from morning_radio.dependencies import check_llm, check_tts
from morning_radio.llm.client import LLMConnectionError, LLMInvalidResponseError, build_llm_client
from morning_radio.llm.vercel import VercelClient
from morning_radio.settings import (
    LLMSettings,
    TTSSettings,
    load_app_settings,
    load_production_settings,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "config",
        tmp_path / "config",
        ignore=shutil.ignore_patterns("*.local.yaml"),
    )
    for module in ("credentials", "settings", "cli"):
        monkeypatch.setattr(f"morning_radio.{module}.repo_root", lambda: tmp_path)
    for name in (
        "AI_GATEWAY_API_KEY",
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "MORNING_RADIO_FAKE_TTS",
        "MORNING_RADIO_FAKE_LLM",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def llm_settings(model="openai/gpt-4.1-mini"):
    return LLMSettings(
        provider="vercel",
        model=model,
        base_url=HttpUrl("https://untrusted.invalid"),
        timeout_seconds=30,
    )


def speech_settings():
    return TTSSettings(engine="vercel", voice="alloy", speed=1.0)


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 2400)
    return output.getvalue()


class Answer(BaseModel):
    answer: str


def test_gateway_text_endpoint_auth_and_schema(root, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gateway-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-key")
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url == "https://ai-gateway.vercel.sh/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer gateway-secret"
        assert payload["model"] == "openai/gpt-4.1-mini"
        assert payload["max_tokens"] == 12000
        assert "store" not in payload
        if len(requests) > 1:
            assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "{}" if len(requests) == 2 else '{"answer":"yes"}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "morning_radio.llm.openai.httpx.Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    client = build_llm_client(llm_settings(), root)
    assert isinstance(client, VercelClient)
    assert client.generate_text("s", "u", stage="test", prompt_type="test")
    assert (
        client.generate_structured("s", "u", Answer, stage="test", prompt_type="test").answer
        == "yes"
    )
    assert len(requests) == 3
    client.close()
    assert client._client.is_closed
    logs = (root / "logs/model-calls.jsonl").read_text()
    assert "gateway-secret" not in logs
    assert all(json.loads(line)["provider"] == "vercel" for line in logs.splitlines())


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 307])
def test_gateway_text_http_failure_not_retried(root, monkeypatch, status):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret")
    client = VercelClient(llm_settings(), root)
    client._client.close()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="secret body")

    client._client = httpx.Client(base_url=client.endpoint, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(
            LLMConnectionError, match=f"Vercel AI Gateway returned HTTP {status}"
        ) as error:
            client.generate_text("s", "u", stage="test", prompt_type="test")
        assert "secret body" not in str(error.value)
        assert len(requests) == 1
    finally:
        client.close()


def test_gateway_missing_keys_and_model_validation(root):
    with pytest.raises(LLMConnectionError, match="AI_GATEWAY_API_KEY"):
        VercelClient(llm_settings(), root)
    with pytest.raises(TTSError, match="AI_GATEWAY_API_KEY"):
        VercelTTS(speech_settings())
    with pytest.raises(TTSError, match="settings"):
        build_tts_adapter("vercel")
    for model in ("gpt-4.1-mini", "provider/", "a/b/c", "x/with space", "x/\n"):
        with pytest.raises(ValidationError, match="provider/model"):
            llm_settings(model)
    with pytest.raises(ValidationError, match="voice"):
        TTSSettings(engine="vercel", voice="af_bella", speed=1)
    with pytest.raises(ValidationError, match="speed"):
        TTSSettings(engine="vercel", voice="alloy", speed=0.1)


def test_gateway_speech_protocol_and_valid_wav(root, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "speech-secret")
    data = wav_bytes()
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer speech-secret"
        assert request.headers["ai-model-id"] == "openai/tts-1"
        assert request.headers["ai-speech-model-specification-version"] == "4"
        assert json.loads(request.content) == {
            "text": "Hello world",
            "voice": "alloy",
            "speed": 1.0,
            "outputFormat": "wav",
        }
        return httpx.Response(200, json={"audio": base64.b64encode(data).decode(), "warnings": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        adapter = VercelTTS(speech_settings(), client=http)
        assert "alloy" in adapter.available_voices()
        result = adapter.synthesize("Hello  world", "alloy", root / "audio.wav")
        assert result.duration_seconds == 0.1
        assert result.path.read_bytes() == data
        adapter.close()
        assert not http.is_closed
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"audio": "%%%"},
        {"audio": ""},
        {"audio": "aGVsbG8="},
        {"audio": 10},
        {"warnings": [{"secret": "private"}]},
    ],
)
def test_gateway_rejects_bad_audio_without_overwriting(root, monkeypatch, payload):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret")
    target = root / "audio.wav"
    target.write_bytes(b"previous")
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ) as http,
        pytest.raises(TTSError) as error,
    ):
        VercelTTS(speech_settings(), client=http).synthesize("Hi", "alloy", target)
    assert "private" not in str(error.value)
    assert target.read_bytes() == b"previous"


@pytest.mark.parametrize("failure", ["http", "network", "size", "truncated"])
def test_gateway_speech_failure_limits(root, monkeypatch, failure):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret")
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "network":
            raise httpx.ReadTimeout("private", request=request)
        if failure == "http":
            return httpx.Response(402, text="private")
        return httpx.Response(200, json={"audio": base64.b64encode(wav_bytes()[:-10]).decode()})

    if failure == "size":
        monkeypatch.setattr("morning_radio.audio.vercel.MAX_RESPONSE_BYTES", 2)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        pytest.raises(TTSError) as error,
    ):
        VercelTTS(speech_settings(), client=http).synthesize("Hi", "alloy", root / "audio.wav")
    assert "private" not in str(error.value)
    assert len(requests) == 1
    assert not (root / "audio.wav").exists()


def test_gateway_speech_inputs_and_owned_cleanup(root, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret")
    adapter = build_tts_adapter("vercel", settings=speech_settings())
    assert isinstance(adapter, VercelTTS)
    for text, voice, speed in [
        ("", "alloy", 1),
        ("x" * 4097, "alloy", 1),
        ("Hi", "bad", 1),
        ("Hi", "alloy", 0.1),
    ]:
        with pytest.raises(TTSError):
            adapter.synthesize(text, voice, root / "audio.wav", speed=speed)
    production = load_production_settings(root)
    production.tts = speech_settings()
    assert resolve_voices(production) == ("alloy", "alloy")
    monkeypatch.setattr(
        "morning_radio.audio.production.build_tts_adapter", lambda *args, **kwargs: adapter
    )
    monkeypatch.setattr(
        adapter, "synthesize", lambda *args, **kwargs: (_ for _ in ()).throw(TTSError("failed"))
    )
    with pytest.raises(TTSError):
        synthesize_script("[HOST]\nHello world", production, root)
    assert adapter._client.is_closed


@pytest.mark.parametrize("key", ["", "configured"])
def test_gateway_doctor_is_non_billable(root, monkeypatch, key):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", key)
    settings = load_app_settings(root)
    settings.llm = llm_settings()
    production = load_production_settings(root)
    production.tts = speech_settings()
    assert check_llm(settings)[0].ok is bool(key)
    assert check_tts(production)[0].ok is bool(key)
    if key:
        assert "not verified" in check_tts(production)[0].detail


@pytest.mark.parametrize("failure", ["network", "malformed", "empty", "schema"])
def test_gateway_text_invalid_output_and_network(root, monkeypatch, failure):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret")
    client = VercelClient(llm_settings(), root)
    client._client.close()
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "network":
            raise httpx.ReadTimeout("private", request=request)
        if failure == "malformed":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "" if failure == "empty" else "{}"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client._client = httpx.Client(base_url=client.endpoint, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises((LLMConnectionError, LLMInvalidResponseError)) as error:
            client.generate_structured("s", "u", Answer, stage="test", prompt_type="test")
        assert "private" not in str(error.value)
        assert len(requests) == (3 if failure == "schema" else 1)
    finally:
        client.close()


@pytest.mark.parametrize("audio", ["vercel", "kokoro", "none"])
def test_setup_gateway_scripts_and_independent_audio(root, audio):
    tail = {"vercel": "1\n1\n", "kokoro": "1\n", "none": ""}[audio]
    result = CliRunner().invoke(
        app, ["setup"], input=f"cloud\nvercel\n{audio}\n1\n{tail}y\nsecret-key\nsecret-key\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "secret-key" not in result.output
    assert "OPENAI_API_KEY" not in result.output
    assert load_app_settings(root).llm.provider == "vercel"
    production = load_production_settings(root)
    assert production.generate_audio is (audio != "none")
    if audio != "none":
        assert production.tts.engine == audio
    assert read_credential("AI_GATEWAY_API_KEY") == "secret-key"
    assert (root / ".env").stat().st_mode & 0o777 == 0o600


def test_gateway_custom_model_and_cancel(root):
    result = CliRunner().invoke(
        app, ["setup"], input="cloud\nvercel\nnone\ncustom\nexample/model\ny\nsecret\nsecret\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert "example/model" in result.output
    assert not (root / ".env").exists()
    assert not (root / "config/providers.local.yaml").exists()
