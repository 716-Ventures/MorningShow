from __future__ import annotations

import os

import httpx
import pytest

from morning_radio import credentials
from morning_radio.audio.elevenlabs import ElevenLabsTTS
from morning_radio.settings import ConfigError, TTSSettings


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials, "repo_root", lambda: tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return tmp_path


def test_dotenv_key_works_from_other_directory_without_export(root, monkeypatch):
    (root / ".env").write_text('ELEVENLABS_API_KEY="private-test-key" # comment\n')
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("ELEVENLABS_API_KEY=wrong-project\n")
    monkeypatch.chdir(elsewhere)
    assert credentials.read_credential("ELEVENLABS_API_KEY") == "private-test-key"
    assert "ELEVENLABS_API_KEY" not in os.environ


@pytest.mark.parametrize("value", ["shell-key", ""])
def test_explicit_environment_takes_precedence(root, monkeypatch, value):
    (root / ".env").write_text("ELEVENLABS_API_KEY=file-key\n")
    monkeypatch.setenv("ELEVENLABS_API_KEY", value)
    assert credentials.read_credential("ELEVENLABS_API_KEY") == value


@pytest.mark.parametrize("contents", [None, "", "ELEVENLABS_API_KEY=\n", "ELEVENLABS_API_KEY\n"])
def test_missing_or_empty_key(root, contents):
    if contents is not None:
        (root / ".env").write_text(contents)
    assert credentials.read_credential("ELEVENLABS_API_KEY") == ""


def test_values_are_literal_and_file_is_not_executed(root):
    (root / ".env").write_text('ELEVENLABS_API_KEY="${HOME}$(touch unwanted)"\n')
    assert credentials.read_credential("ELEVENLABS_API_KEY") == "${HOME}$(touch unwanted)"
    assert not (root / "unwanted").exists()


def test_reload_observes_key_edits(root):
    path = root / ".env"
    path.write_text("ELEVENLABS_API_KEY=first\n")
    assert credentials.read_credential("ELEVENLABS_API_KEY") == "first"
    path.write_text("ELEVENLABS_API_KEY=second\n")
    assert credentials.read_credential("ELEVENLABS_API_KEY") == "second"


def test_invalid_encoding_does_not_leak_contents(root):
    (root / ".env").write_bytes(b"private-test-secret\xff")
    with pytest.raises(ConfigError, match="Cannot read") as error:
        credentials.read_credential("ELEVENLABS_API_KEY")
    assert "private-test-secret" not in str(error.value)


def test_adapter_reads_key_from_file(root):
    (root / ".env").write_text("ELEVENLABS_API_KEY=from-file\n")
    voice = "AkzTpEeeEWvyZf4umyCJ"

    def respond(request):
        assert request.headers["xi-api-key"] == "from-file"
        return httpx.Response(200, json={"voice_id": voice, "name": "Nathaniel C"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = ElevenLabsTTS(
            TTSSettings(engine="elevenlabs", voice=voice, speed=1), client=client
        )
        assert adapter.available_voices() == [voice]
        assert "from-file" not in adapter.engine_version
