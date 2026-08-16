from __future__ import annotations

from pathlib import Path

import pytest

from morning_radio.audio.master import AudioMasterError, coerce_production_plan, validate_final_mp3
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


def probe(duration: str, *, sample_rate: str = "44100", channels: int = 2, bit_rate: str = "128000"):
    return {
        "format": {"duration": duration, "bit_rate": bit_rate},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "mp3",
                "sample_rate": sample_rate,
                "channels": channels,
                "bit_rate": bit_rate,
            }
        ],
    }


def episode_file(tmp_path: Path) -> Path:
    path = tmp_path / "episode.mp3"
    path.write_bytes(b"mp3")
    return path


def test_unknown_production_plan_item_fails_before_rendering() -> None:
    with pytest.raises(ValueError):
        coerce_production_plan([{"type": "unknown"}])


def test_final_mp3_rejects_one_second_fixture_output(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError):
        validate_final_mp3(episode_file(tmp_path), probe("1.0"))


def test_final_mp3_rejects_sub_minute_real_output(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="greater than 60"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("59.0"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_less_than_eighty_percent_of_plan(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="outside expected range"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("79.0"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_more_than_one_hundred_twenty_percent_of_plan(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="outside expected range"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("121.0"),
            planned_seconds=100,
            production=production_settings(),
        )


@pytest.mark.parametrize("duration", ["80.0", "100.0", "120.0"])
def test_final_mp3_accepts_plan_boundaries(tmp_path: Path, duration: str) -> None:
    validate_final_mp3(
        episode_file(tmp_path),
        probe(duration),
        planned_seconds=100,
        production=production_settings(),
    )


def test_final_mp3_rejects_nonnumeric_duration(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="nonnumeric"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("not-a-number"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="sample rate"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("100.0", sample_rate="48000"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_missing_bitrate(tmp_path: Path) -> None:
    payload = probe("100.0")
    payload["format"].pop("bit_rate")
    payload["streams"][0].pop("bit_rate")
    with pytest.raises(AudioMasterError, match="bitrate"):
        validate_final_mp3(
            episode_file(tmp_path),
            payload,
            planned_seconds=100,
            production=production_settings(),
        )
