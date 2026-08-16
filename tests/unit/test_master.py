from __future__ import annotations

from pathlib import Path

import pytest

from morning_radio.audio.master import (
    AudioMasterError,
    coerce_production_plan,
    escape_concat_path,
    metadata_args,
    run_command,
    run_probe,
    validate_final_mp3,
)
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


def test_concat_path_escapes_apostrophes() -> None:
    assert escape_concat_path(Path("/tmp/host's voice.wav")) == "/tmp/host'\\''s voice.wav"


def test_metadata_args_include_episode_fields() -> None:
    args = metadata_args("Morning Show 2026-08-15", "2026-08-15")
    pairs = list(zip(args[0::2], args[1::2], strict=True))

    assert all(flag == "-metadata" for flag, _ in pairs)
    assert ("-metadata", "title=Morning Show 2026-08-15") in pairs
    assert ("-metadata", "album=Personal Morning Radio") in pairs
    assert ("-metadata", "show=Personal Morning Radio") in pairs
    assert ("-metadata", "date=2026-08-15") in pairs


def test_run_command_writes_stderr_diagnostics(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="see .*fail-stderr.txt"):
        run_command(
            ["/bin/sh", "-c", "echo detailed failure >&2; exit 7"],
            tmp_path,
            "fail",
        )

    assert "detailed failure" in (tmp_path / "mix" / "fail-stderr.txt").read_text(encoding="utf-8")


def test_run_probe_rejects_invalid_json_with_diagnostics(tmp_path: Path) -> None:
    episode = episode_file(tmp_path)

    with pytest.raises(AudioMasterError, match="invalid JSON"):
        run_probe("/bin/echo", episode, tmp_path)

    assert (tmp_path / "mix" / "ffprobe-stderr.txt").exists()
