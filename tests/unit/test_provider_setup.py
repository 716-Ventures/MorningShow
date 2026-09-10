from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from pydantic import BaseModel, HttpUrl
from typer.testing import CliRunner

from morning_radio import provider_setup as setup
from morning_radio.cli import app
from morning_radio.credentials import save_credentials
from morning_radio.dependencies import check_llm
from morning_radio.llm.client import LLMConnectionError, LLMInvalidResponseError, build_llm_client
from morning_radio.llm.openai import OpenAIClient
from morning_radio.settings import LLMSettings, load_app_settings, load_production_settings


@pytest.fixture
def root(tmp_path, monkeypatch):
    original = Path(__file__).resolve().parents[2]
    shutil.copytree(
        original / "config", tmp_path / "config", ignore=shutil.ignore_patterns("*.local.yaml")
    )
    monkeypatch.setattr("morning_radio.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr("morning_radio.credentials.repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        setup, "detect_hardware", lambda root: setup.Hardware("Darwin", "arm64", 16, 100, 10)
    )
    monkeypatch.delenv("MORNING_RADIO_FIXTURE_RUN", raising=False)
    monkeypatch.delenv("MORNING_RADIO_FAKE_LLM", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("memory", "disk", "expected"),
    [
        (None, 100, None),
        (8, 100, None),
        (16, 4, None),
        (16, 100, "qwen3:4b"),
        (24, 100, "qwen3:4b"),
        (32, 100, "qwen3:8b"),
        (32, 10, "qwen3:4b"),
    ],
)
def test_recommendations(memory, disk, expected):
    assert setup.recommend_model(setup.Hardware("Darwin", "arm64", memory, disk, 10)) == expected


def test_local_setup_preserves_base_files(root):
    before = {p: p.read_bytes() for p in (root / "config").glob("*.yaml")}
    result = CliRunner().invoke(app, ["setup"], input="local\ny\n\n\ny\n")
    assert result.exit_code == 0, result.output
    assert load_app_settings(root).llm.model == "qwen3:4b"
    assert load_app_settings(root).llm.thinking is False
    assert load_production_settings(root).tts.voice == "af_bella"
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (root / ".env").exists()
    setup.ensure_setup(root)


def test_cancel_writes_nothing(root):
    result = CliRunner().invoke(app, ["setup"], input="local\ny\n\n\nn\n")
    assert result.exit_code == 0
    assert not (root / "config/providers.local.yaml").exists()


def test_cloud_setup_hidden_keys_and_script_only(root):
    result = CliRunner().invoke(
        app, ["setup"], input="cloud\nn\n\ny\ntest-secret\ntest-secret\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "test-secret" not in result.output
    assert load_app_settings(root).llm.provider == "openai"
    assert not load_production_settings(root).generate_audio
    assert dotenv_values(root / ".env")["OPENAI_API_KEY"] == "test-secret"
    assert (root / ".env").stat().st_mode & 0o777 == 0o600


def test_mixed_setup(root):
    result = CliRunner().invoke(app, ["setup"], input="mixed\nopenai\nkokoro\n\n\nn\ny\n")
    assert result.exit_code == 0, result.output
    assert load_app_settings(root).llm.provider == "openai"
    assert load_production_settings(root).tts.engine == "kokoro"


def test_cloud_speech_and_keep(root):
    result = CliRunner().invoke(app, ["setup"], input="cloud\ny\n\nTestVoice123\nn\nn\ny\n")
    assert result.exit_code == 0, result.output
    assert load_production_settings(root).tts.voice == "TestVoice123"
    before = (root / "config/providers.local.yaml").read_bytes()
    result = CliRunner().invoke(app, ["setup"], input="keep\nn\nn\ny\n")
    assert result.exit_code == 0
    assert before == (root / "config/providers.local.yaml").read_bytes()


def test_headless_first_run_blocks_generation(root, monkeypatch):
    monkeypatch.setattr(
        "morning_radio.pipeline.run_morning", lambda **kwargs: pytest.fail("must not generate")
    )
    result = CliRunner().invoke(app, ["morning"])
    assert result.exit_code == 1
    assert "./show setup" in result.output


def test_interactive_first_run_and_cancel(root, monkeypatch):
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(setup, "run_setup", lambda root: False)
    with pytest.raises(RuntimeError, match="cancelled"):
        setup.ensure_setup(root)
    monkeypatch.setattr(setup, "run_setup", lambda root: True)
    setup.ensure_setup(root)


def test_fixture_skips_setup(root, monkeypatch):
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    setup.ensure_setup(root)


def test_catalog_is_read_only(root):
    result = CliRunner().invoke(app, ["providers"])
    assert result.exit_code == 0
    assert "qwen3:4b" in result.output
    assert "paid plan" in result.output
    assert not (root / "config/providers.local.yaml").exists()


def test_credentials_preserve_existing(root):
    (root / ".env").write_text("# existing\nELEVENLABS_API_KEY='old'\n")
    save_credentials(root, {"OPENAI_API_KEY": "dollar$and'quote"})
    assert dotenv_values(root / ".env", interpolate=False) == {
        "ELEVENLABS_API_KEY": "old",
        "OPENAI_API_KEY": "dollar$and'quote",
    }
    with pytest.raises(ValueError):
        save_credentials(root, {"UNSUPPORTED": "x"})


def client_for(root, monkeypatch, handler):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = build_llm_client(
        LLMSettings(
            provider="openai",
            base_url=HttpUrl("http://untrusted.invalid"),
            model="gpt-4.1-mini",
            timeout_seconds=30,
        ),
        root,
    )
    assert isinstance(client, OpenAIClient)
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)
    )
    return client


class Answer(BaseModel):
    answer: str


def test_openai_structured_retry(root, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.url.host == "api.openai.com"
        content = "{}" if len(requests) == 1 else '{"answer":"yes"}'
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    client = client_for(root, monkeypatch, handler)
    try:
        assert (
            client.generate_structured(
                "system", "user", Answer, stage="test", prompt_type="test"
            ).answer
            == "yes"
        )
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert len(requests) == 2
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_openai_errors_sanitized_no_retry(root, monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private provider error test-key")

    client = client_for(root, monkeypatch, handler)
    try:
        with pytest.raises(LLMConnectionError, match=f"HTTP {status}") as error:
            client.generate_text("system", "user", stage="test", prompt_type="test")
        assert "test-key" not in str(error.value)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
    ],
)
def test_openai_rejects_bad_output(root, monkeypatch, response):
    client = client_for(root, monkeypatch, lambda request: httpx.Response(200, json=response))
    try:
        with pytest.raises(LLMInvalidResponseError):
            client.generate_text("s", "u", stage="test", prompt_type="test")
    finally:
        client.close()


def test_cloud_preflight_does_not_contact_ollama(root, monkeypatch):
    settings = load_app_settings(root)
    settings.llm.provider = "openai"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("no network"))
    assert not check_llm(settings)[0].ok
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert check_llm(settings)[0].ok
    assert "not verified" in check_llm(settings)[0].detail


@pytest.mark.parametrize("fails", [False, True])
def test_hardware_mac_probe(tmp_path, monkeypatch, fails):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")

    def probe(command, **kwargs):
        assert command == ["/usr/sbin/sysctl", "-n", "hw.memsize"]
        assert kwargs["timeout"] == 3
        if fails:
            raise subprocess.TimeoutExpired(command, 3)
        return subprocess.CompletedProcess(command, 0, stdout=str(16 * 1024**3))

    monkeypatch.setattr(setup.subprocess, "run", probe)
    hardware = setup.detect_hardware(tmp_path)
    assert hardware.memory_gib == (None if fails else 16)
    assert hardware.free_disk_gib > 0


def test_hardware_linux_and_unverified_gpu(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        setup.os, "sysconf", lambda name: {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 4194304}[name]
    )
    assert setup.show_providers(tmp_path).memory_gib == 16
    assert "GPU acceleration is not verified" in capsys.readouterr().out


def test_cancel_after_entering_key_preserves_secrets(root):
    (root / ".env").write_text("# retain\nOPENAI_API_KEY='previous'\n")
    result = CliRunner().invoke(app, ["setup"], input="cloud\nn\n\ny\nnew-secret\nnew-secret\nn\n")
    assert result.exit_code == 0
    assert "new-secret" not in result.output
    assert dotenv_values(root / ".env")["OPENAI_API_KEY"] == "previous"
    assert not (root / "config/providers.local.yaml").exists()


def test_openai_fixed_origin_and_missing_key(root, monkeypatch):
    settings = LLMSettings(
        provider="openai",
        base_url=HttpUrl("http://untrusted.invalid"),
        model="gpt-4.1-mini",
        timeout_seconds=30,
    )
    with pytest.raises(LLMConnectionError, match="OPENAI_API_KEY"):
        OpenAIClient(settings, root)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = OpenAIClient(settings, root)
    try:
        assert str(client._client.base_url) == "https://api.openai.com/v1/"
        assert client._client.headers["Authorization"] == "Bearer test-key"
    finally:
        client.close()


def test_openai_network_error_no_secret(root, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("test-key", request=request)

    client = client_for(root, monkeypatch, handler)
    try:
        with pytest.raises(LLMConnectionError, match="network request failed") as error:
            client.generate_text("s", "u", stage="test", prompt_type="test")
        assert "test-key" not in str(error.value)
    finally:
        client.close()


def test_openai_validation_exhaustion(root, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    client = client_for(root, monkeypatch, handler)
    try:
        with pytest.raises(LLMInvalidResponseError, match="three attempts"):
            client.generate_structured("s", "u", Answer, stage="test", prompt_type="test")
        assert len(calls) == 3
    finally:
        client.close()
