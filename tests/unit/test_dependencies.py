from __future__ import annotations

import subprocess

import httpx
import pytest
import respx
from pydantic import HttpUrl

from morning_radio import dependencies
from morning_radio.dependencies import DependencyCheck, DependencyPreflightError, morning_preflight
from morning_radio.settings import (
    AppSettings,
    LLMSettings,
    NewsSettings,
    ProductionSettings,
    SelectionSettings,
    VerificationSettings,
)


def app_settings() -> AppSettings:
    return AppSettings(
        llm=LLMSettings(base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5),
        news=NewsSettings(
            request_timeout_seconds=5,
            max_html_bytes=10240,
            minimum_article_words=1,
            minimum_usable_articles=1,
            candidate_max_age_hours=72,
            max_candidates=10,
            max_articles_to_extract=10,
            concurrency=2,
        ),
        selection=SelectionSettings(
            major_news_importance_threshold=80,
            dossier_source_preference=2,
            maximum_selected_stories=3,
        ),
        verification=VerificationSettings(maximum_correction_cycles=2),
    )


def production_settings() -> ProductionSettings:
    return ProductionSettings.model_validate(
        {
            "tts": {"engine": "tone", "voice": None, "secondary_voice": None, "speed": 1.0},
            "audio": {
                "bitrate_kbps": 128,
                "sample_rate_hz": 44100,
                "channels": 2,
                "loudness_target_lufs": -16,
            },
            "assets": {
                "opening_optional": True,
                "closing_optional": True,
                "bumpers_optional": True,
                "beds_optional": True,
            },
        }
    )


def test_morning_preflight_raises_with_actionable_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        "morning_radio.dependencies.check_llm",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "morning_radio.dependencies.check_tts",
        lambda production: [],
    )
    monkeypatch.setattr(
        "morning_radio.dependencies.check_ffmpeg",
        list,
    )
    assert morning_preflight(app_settings(), production_settings()) == []


def test_morning_preflight_failure_includes_corrective_action(monkeypatch) -> None:
    monkeypatch.setattr("morning_radio.dependencies.check_llm", lambda settings: [])
    monkeypatch.setattr("morning_radio.dependencies.check_tts", lambda production: [])
    monkeypatch.setattr(
        "morning_radio.dependencies.check_ffmpeg",
        lambda: [
            DependencyCheck(
                "ffmpeg",
                False,
                "not found on PATH",
                "Install FFmpeg and ensure ffmpeg/ffprobe are on PATH.",
            )
        ],
    )
    try:
        morning_preflight(app_settings(), production_settings())
    except DependencyPreflightError as exc:
        assert "Install FFmpeg" in str(exc)
    else:
        raise AssertionError("Expected DependencyPreflightError")


def test_morning_preflight_skips_audio_dependencies_when_disabled(monkeypatch) -> None:
    production = production_settings().model_copy(update={"generate_audio": False})
    monkeypatch.setattr("morning_radio.dependencies.check_llm", lambda settings: [])
    monkeypatch.setattr(
        "morning_radio.dependencies.check_tts",
        lambda settings: (_ for _ in ()).throw(AssertionError("TTS should not be checked")),
    )
    monkeypatch.setattr(
        "morning_radio.dependencies.check_ffmpeg",
        lambda: (_ for _ in ()).throw(AssertionError("FFmpeg should not be checked")),
    )

    assert morning_preflight(app_settings(), production) == []


@pytest.mark.parametrize(
    "payload",
    [[], {"models": None}, {"models": [None]}, {"models": [{"name": "test"}]}, {"models": []}],
)
def test_llm_diagnostic_validates_envelope(payload):
    with respx.mock as router:
        router.get("http://ollama.test/api/tags").mock(httpx.Response(200, json=payload))
        result = dependencies.check_llm(app_settings())
    assert all(item.ok for item in result) == (payload == {"models": [{"name": "test"}]})


@pytest.mark.parametrize(
    "error",
    [
        OSError("cannot execute"),
        subprocess.TimeoutExpired("ffmpeg", 30),
        subprocess.CalledProcessError(1, "ffmpeg"),
    ],
)
def test_binary_failures_are_actionable(monkeypatch, error):
    monkeypatch.setattr(dependencies.shutil, "which", lambda name: name)

    def fail(*args, **kwargs):
        assert kwargs["timeout"] > 0
        raise error

    monkeypatch.setattr(dependencies.subprocess, "run", fail)
    assert all(not check.ok and check.action for check in dependencies.check_ffmpeg())


def test_missing_binaries(monkeypatch):
    monkeypatch.setattr(dependencies.shutil, "which", lambda name: None)
    assert all(not check.ok for check in dependencies.check_ffmpeg())


def test_kokoro_missing_and_invalid_voice(monkeypatch):
    production = production_settings()
    production.tts.engine = "kokoro"
    monkeypatch.setattr(dependencies, "kokoro_importable", lambda: False)
    assert not dependencies.check_tts(production)[0].ok
    monkeypatch.setattr(dependencies, "kokoro_importable", lambda: True)

    class Voices:
        def available_voices(self):
            return ["af_bella", "af_heart"]

    monkeypatch.setattr(dependencies, "build_tts_adapter", lambda engine: Voices())
    production.tts.secondary_voice = "missing"
    assert any(not check.ok for check in dependencies.check_tts(production))
