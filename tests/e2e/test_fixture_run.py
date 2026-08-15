from __future__ import annotations

import shutil
from datetime import date

import pytest

from morning_radio.pipeline import run_morning


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg required")
def test_fixture_morning_run_completes(monkeypatch) -> None:
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_LLM", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    result = run_morning(date(2026, 8, 14), minutes=10, no_assets=True)
    assert str(result["episode"]).endswith("episode.mp3")
    assert result["stories"] > 0
