from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from morning_radio.llm.client import LLMError
from morning_radio.models import (
    DossierFact,
    EditorialProfile,
    EditorialStyle,
    GlobalNewsProfile,
    Interest,
    LocationProfile,
    Rundown,
    RundownSegment,
    ShowFormat,
    StoryDossier,
    VoicePreferences,
)
from morning_radio.showgen.script import (
    ScriptError,
    estimate_spoken_seconds,
    spoken_blocks,
    validate_script,
    write_script,
)


class FailingScriptLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs):
        raise LLMError("timed out")

    def generate_structured(self, *args, **kwargs):
        raise AssertionError("unused")


class ExplodingScriptLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs):
        raise AssertionError("writing LLM should be skipped")

    def generate_structured(self, *args, **kwargs):
        raise AssertionError("unused")


def test_valid_script_blocks() -> None:
    script = "[HOST]\nGood morning.\n\n[PAUSE: 500]\n\n[HOST]\nThat is the show.\n"
    validate_script(script)
    assert spoken_blocks(script) == [("HOST", "Good morning."), ("HOST", "That is the show.")]


def test_unknown_directive_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[LASERS]\n")


def test_spoken_list_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\n- Bullet copy\n")


def test_internal_editorial_language_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script(
            "[HOST]\nThis story was included because it scored highly against your editorial profile.\n"
        )


def test_invalid_pause_argument_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: nope]\n")


def test_pause_outside_allowed_range_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: 99999]\n")


def test_missing_bumper_name_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[BUMPER: ]\n")


def test_malformed_directive_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: 500\n")


def test_estimate_spoken_seconds_counts_words_and_pauses() -> None:
    script = "[HOST]\nOne two three four.\n\n[PAUSE: 1000]\n"
    assert estimate_spoken_seconds(script, wpm=60) == 5


def profile() -> EditorialProfile:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return EditorialProfile(
        created_at=now,
        updated_at=now,
        location=LocationProfile(home="Buffalo, NY", local_scope="Western New York"),
        interests=[Interest(name="AI", priority=5, depth="normal")],
        global_news=GlobalNewsProfile(include_major_us=True, include_major_world=True),
        editorial_style=EditorialStyle(
            context_level="context",
            assume_subject_familiarity=False,
            ongoing_story_policy="changes_only",
        ),
        show_format=ShowFormat(
            target_minutes=10,
            minimum_minutes=5,
            maximum_minutes=15,
            allow_variable_length=True,
            headline_open=True,
            watch_list_close=True,
            host_count=1,
        ),
        voice_preferences=VoicePreferences(),
    )


def rundown() -> Rundown:
    return Rundown(
        show_date=date(2026, 8, 15),
        target_seconds=600,
        planned_seconds=180,
        segments=[
            RundownSegment(
                segment_id="story-1",
                type="story",
                title="Story",
                cluster_ids=["cluster-001"],
                planned_seconds=180,
                purpose="test",
            )
        ],
    )


def dossier() -> StoryDossier:
    return StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[DossierFact(claim="Fact.", supporting_candidate_ids=["source-001"])],
        recommended_seconds=180,
        source_ids=["source-001"],
    )


def test_recoverable_script_model_error_uses_fallback(tmp_path) -> None:
    script = write_script(profile(), rundown(), [dossier()], tmp_path, FailingScriptLLM())

    assert "Good morning" in script
    assert (tmp_path / "script-draft.md").exists()
    diagnostic = (tmp_path / "logs" / "script-fallback.json").read_text(encoding="utf-8")
    assert "small-local-model" in diagnostic
    assert "timed out" in diagnostic


def test_fallback_script_uses_radio_copy_instead_of_dossier_labels(tmp_path) -> None:
    ugly_dossier = dossier().model_copy(
        update={
            "working_headline": "UN votes to adopt new world map to reflect Africa's true size",
            "what_happened": (
                "UN votes to adopt new world map to reflect Africa's true size - Published "
                "The UN General Assembly has voted to replace the traditional world map."
            ),
            "what_is_new_today": "This is part of today's latest source set.",
            "why_it_matters": "It gives useful context for the morning ahead.",
            "uncertainties": ["Single-source story; phrase cautiously."],
        }
    )

    script = write_script(profile(), rundown(), [ugly_dossier], tmp_path, FailingScriptLLM())

    assert "Ahead this morning:" in script
    assert "What is new today:" not in script
    assert "Why it matters:" not in script
    assert "One caution:" not in script
    assert "Single-source story" not in script
    assert "Published" not in script
    assert script.count("UN votes to adopt new world map to reflect Africa's true size") == 2
    assert "The UN General Assembly has voted to replace the traditional world map." in script


def test_upstream_model_fallback_skips_script_llm(tmp_path) -> None:
    fallback_path = tmp_path / "dossiers" / "cluster-001-fallback.json"
    fallback_path.parent.mkdir()
    fallback_path.write_text("{}", encoding="utf-8")

    script = write_script(profile(), rundown(), [dossier()], tmp_path, ExplodingScriptLLM())

    assert "Good morning" in script
    assert (tmp_path / "script-draft.md").exists()
