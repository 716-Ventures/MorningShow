from __future__ import annotations

from pathlib import Path

import pytest

from morning_radio.audio.production import (
    MusicItem,
    PauseItem,
    ProductionPlanError,
    SpeechItem,
    attach_audio_to_plan,
    build_production_plan,
    build_text_production_plan,
    resolve_asset,
)
from morning_radio.models import AudioMetadata
from morning_radio.settings import ProductionSettings


def test_asset_symlink_cannot_escape_to_sibling_directory(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    sibling = tmp_path / "assets-private"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret.wav").write_bytes(b"audio")
    (root / "bed.wav").symlink_to(sibling / "secret.wav")
    with pytest.raises(ProductionPlanError, match="escapes"):
        resolve_asset(root, "bed")


def test_no_assets_removes_every_asset_event(tmp_path: Path) -> None:
    plan = build_text_production_plan(
        "[BED: bed]\n[HOST]\nHello.\n[BED: STOP]\n",
        tmp_path,
        True,
        production_settings(),
        tmp_path / "assets",
    )
    assert [item.type for item in plan] == ["speech"]


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
    audio = [
        AudioMetadata(voice="tone", text_hash="abc", duration_seconds=1, path=tmp_path / "a.wav")
    ]
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
    audio = [
        AudioMetadata(voice="tone", text_hash="abc", duration_seconds=1, path=tmp_path / "a.wav")
    ]
    with pytest.raises(ProductionPlanError):
        build_production_plan(
            "[BED: low]\n\n[HOST]\nHello.\n",
            audio,
            tmp_path,
            False,
            production_settings(),
            tmp_path / "assets",
        )


def test_text_production_plan_validates_assets_before_audio(tmp_path: Path) -> None:
    plan = build_text_production_plan(
        "[MUSIC: OPENING]\n\n[HOST]\nHello.\n",
        tmp_path,
        False,
        production_settings(),
        tmp_path / "assets",
    )
    assert isinstance(plan[1], SpeechItem)
    assert plan[1].text == "Hello."
    assert plan[1].path is None


def test_attach_audio_to_plan_requires_matching_speech_count(tmp_path: Path) -> None:
    plan = build_text_production_plan(
        "[HOST]\nHello.\n",
        tmp_path,
        True,
        production_settings(),
        tmp_path / "assets",
    )
    with pytest.raises(ProductionPlanError):
        attach_audio_to_plan(plan, [])


def test_adjacent_spoken_blocks_receive_a_short_pause(tmp_path: Path) -> None:
    plan = build_text_production_plan(
        "[HOST]\nGood morning.\n\n[HOST]\nHere are today's stories.\n",
        tmp_path,
        True,
        production_settings(),
        tmp_path / "assets",
    )

    assert [item.type for item in plan] == ["speech", "pause", "speech"]
    assert isinstance(plan[1], PauseItem)
    assert plan[1].milliseconds == 220


def test_explicit_pause_is_not_duplicated_between_spoken_blocks(tmp_path: Path) -> None:
    plan = build_text_production_plan(
        "[HOST]\nGood morning.\n\n[PAUSE: 500]\n\n[HOST]\nHere are today's stories.\n",
        tmp_path,
        True,
        production_settings(),
        tmp_path / "assets",
    )

    assert [item.type for item in plan] == ["speech", "pause", "speech"]
    assert isinstance(plan[1], PauseItem)
    assert plan[1].milliseconds == 500


def test_long_spoken_block_is_split_at_sentence_boundary(tmp_path: Path) -> None:
    production = production_settings().model_copy(
        update={
            "tts": production_settings().tts.model_copy(
                update={"max_chunk_words": 15, "sentence_pause_ms": 140}
            )
        }
    )
    script = (
        "[HOST]\n"
        "This first sentence contains exactly eight useful spoken words. "
        "This second sentence also contains eight useful spoken words.\n"
    )
    plan = build_text_production_plan(
        script,
        tmp_path,
        True,
        production,
        tmp_path / "assets",
    )

    assert [item.type for item in plan] == ["speech", "pause", "speech"]
    assert isinstance(plan[1], PauseItem)
    assert plan[1].milliseconds == 140
