from __future__ import annotations

from pathlib import Path

import pytest

from morning_radio.audio.production import (
    MusicItem,
    ProductionPlanError,
    SpeechItem,
    build_production_plan,
)
from morning_radio.models import AudioMetadata
from morning_radio.settings import ProductionSettings


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


def test_optional_missing_asset_is_skipped(tmp_path: Path) -> None:
    audio = [AudioMetadata(voice="tone", text_hash="abc", duration_seconds=1, path=tmp_path / "a.wav")]
    plan = build_production_plan(
        "[MUSIC: OPENING]\n\n[HOST]\nHello.\n",
        audio,
        tmp_path,
        False,
        production_settings(),
        tmp_path / "assets",
    )
    assert isinstance(plan[0], MusicItem)
    assert plan[0].skipped is True
    assert isinstance(plan[1], SpeechItem)


def test_unclosed_bed_fails(tmp_path: Path) -> None:
    audio = [AudioMetadata(voice="tone", text_hash="abc", duration_seconds=1, path=tmp_path / "a.wav")]
    with pytest.raises(ProductionPlanError):
        build_production_plan(
            "[BED: low]\n\n[HOST]\nHello.\n",
            audio,
            tmp_path,
            False,
            production_settings(),
            tmp_path / "assets",
        )
