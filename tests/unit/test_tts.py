from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from morning_radio.audio.production import (
    attach_audio_to_plan,
    build_text_production_plan,
    resolve_voices,
    synthesize_script,
    tts_spoken_blocks,
)
from morning_radio.audio.tts import KokoroTTS, ToneTTS, TTSError, prepare_tts_text
from morning_radio.models import AudioMetadata, VoicePreferences
from morning_radio.profile.compiler import default_profile
from morning_radio.settings import ProductionSettings


def test_tone_tts_writes_playable_wav(tmp_path: Path) -> None:
    path = tmp_path / "voice.wav"
    metadata = ToneTTS().synthesize("Good morning from the fixture show.", "tone", path)
    assert path.exists()
    assert path.stat().st_size > 44
    assert metadata.duration_seconds > 0


def production_settings(**tts_overrides: object) -> ProductionSettings:
    tts: dict[str, object] = {
        "engine": "tone",
        "voice": None,
        "secondary_voice": None,
        "speed": 1.0,
    }
    tts.update(tts_overrides)
    return ProductionSettings.model_validate(
        {
            "tts": tts,
            "audio": {
                "bitrate_kbps": 128,
                "sample_rate_hz": 44100,
                "channels": 1,
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


class CountingAdapter:
    engine_version = "counting-v1"

    def __init__(self, voices: list[str] | None = None):
        self.calls: list[tuple[str, str, Path, float]] = []
        self._voices = voices or ["tone", "profile-primary", "profile-secondary"]

    def available_voices(self) -> list[str]:
        return self._voices

    def synthesize(
        self, text: str, voice: str, output_path: Path, *, speed: float = 1.0
    ) -> AudioMetadata:
        self.calls.append((text, voice, output_path, speed))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF")
        return AudioMetadata(
            voice=voice,
            text_hash=f"hash-{len(self.calls)}",
            duration_seconds=1.0,
            path=output_path,
        )


def test_prepare_tts_text_expands_common_spoken_forms() -> None:
    prepared = prepare_tts_text(
        "OpenAI discussed AI spending of A$155bn in NSW, up 26%. No. 2 follows R&D.",
        {"OpenAI": "Open A I"},
    )

    assert prepared == (
        "Open A I discussed A I spending of 155 billion Australian dollars in N S W, "
        "up 26 percent. number 2 follows R and D."
    )


def test_prepare_tts_text_applies_custom_name_override_case_insensitively() -> None:
    assert prepare_tts_text("TERNUS spoke today.", {"Ternus": "TURN-us"}) == (
        "TURN-us spoke today."
    )


def test_synthesize_script_sends_prepared_copy_to_adapter(tmp_path: Path) -> None:
    adapter = CountingAdapter()
    settings = production_settings(pronunciation_overrides={"OpenAI": "Open A I"})

    synthesize_script("[HOST]\nOpenAI announced AI tools.\n", settings, tmp_path, adapter=adapter)

    assert adapter.calls[0][0] == "Open A I announced A I tools."


def test_prepare_tts_text_expands_compact_dollar_amounts() -> None:
    assert prepare_tts_text("The company raised $3.5B and another $20m.") == (
        "The company raised 3.5 billion dollars and another 20 million dollars."
    )


def test_tts_spoken_blocks_split_long_paragraphs_without_losing_host() -> None:
    script = "[HOST 2]\nOne two three four five. Six seven eight nine ten.\n"

    assert tts_spoken_blocks(script, max_chunk_words=6) == [
        ("HOST 2", "One two three four five."),
        ("HOST 2", "Six seven eight nine ten."),
    ]


def test_chunked_synthesis_matches_chunked_production_plan(tmp_path: Path) -> None:
    adapter = CountingAdapter()
    settings = production_settings(max_chunk_words=15)
    script = (
        "[HOST]\n"
        "This first sentence contains exactly eight useful spoken words. "
        "This second sentence also contains eight useful spoken words.\n"
    )

    audio = synthesize_script(script, settings, tmp_path, adapter=adapter)
    plan = build_text_production_plan(
        script,
        tmp_path,
        True,
        settings,
        tmp_path / "assets",
    )
    attached = attach_audio_to_plan(plan, audio)

    assert len(audio) == 2
    assert [item.type for item in attached] == ["speech", "pause", "speech"]


def test_synthesize_script_reuses_duplicate_current_run_audio(tmp_path: Path) -> None:
    adapter = CountingAdapter()
    script = "[HOST]\nSame text.\n\n[HOST]\nSame text.\n"

    manifest = synthesize_script(script, production_settings(), tmp_path, adapter=adapter)

    assert len(adapter.calls) == 1
    assert len(manifest) == 2
    assert manifest[0].path == manifest[1].path


def test_profile_voice_preferences_fill_missing_production_defaults() -> None:
    profile = default_profile().model_copy(
        deep=True,
        update={
            "voice_preferences": VoicePreferences(
                primary_voice="profile-primary",
                secondary_voice="profile-secondary",
                pace="normal",
            )
        },
    )

    assert resolve_voices(production_settings(), profile) == (
        "profile-primary",
        "profile-secondary",
    )
    assert resolve_voices(production_settings(voice="tone"), profile) == (
        "tone",
        "profile-secondary",
    )


def test_synthesize_script_rejects_unavailable_voice(tmp_path: Path) -> None:
    adapter = CountingAdapter(voices=["other"])

    with pytest.raises(RuntimeError, match="unavailable"):
        synthesize_script(
            "[HOST]\nHello.\n", production_settings(voice="missing"), tmp_path, adapter=adapter
        )


def test_kokoro_reuses_pipeline_per_language(tmp_path: Path) -> None:
    factory_calls: list[str] = []

    class FakePipeline:
        def __call__(self, text: str, *, voice: str, speed: float):
            yield None, None, [0.1, 0.2]

    def factory(*, lang_code: str) -> FakePipeline:
        factory_calls.append(lang_code)
        return FakePipeline()

    def writer(path: Path, chunks: list[Any], sample_rate: int) -> float:
        path.write_bytes(b"RIFF")
        return len(chunks[0]) / sample_rate

    adapter = KokoroTTS(pipeline_factory=factory, audio_writer=writer)
    adapter.synthesize("First.", "af_heart", tmp_path / "one.wav")
    adapter.synthesize("Second.", "af_heart", tmp_path / "two.wav")

    assert factory_calls == ["a"]


def test_kokoro_rejects_empty_audio(tmp_path: Path) -> None:
    class EmptyPipeline:
        def __call__(self, text: str, *, voice: str, speed: float):
            return iter(())

    adapter = KokoroTTS(pipeline_factory=lambda *, lang_code: EmptyPipeline())

    with pytest.raises(TTSError, match="no audio"):
        adapter.synthesize("Silent.", "af_heart", tmp_path / "silent.wav")
