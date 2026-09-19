from __future__ import annotations

import json
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
from morning_radio.showgen import script as script_module
from morning_radio.showgen.script import (
    ScriptError,
    estimate_spoken_seconds,
    finalize_script,
    has_headline_lead,
    normalize_script_format,
    remove_redundant_lead,
    spoken_blocks,
    spoken_word_count,
    story_script_paragraph,
    story_word_limit,
    validate_script,
    validate_script_quality,
    write_script,
)


def test_headline_removal_preserves_sentence_subject():
    body = "Apple announced a new phone today."
    assert not has_headline_lead(body, "Apple")
    assert remove_redundant_lead(body, "Apple") == body
    assert remove_redundant_lead("Apple: " + body, "Apple") == body
    assert remove_redundant_lead("Apple. " + body, "Apple.") == body


class FailingScriptLLM:
    model = "small-local-model"

    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, *args, **kwargs):
        raise LLMError("timed out")

    def generate_structured(self, *args, **kwargs):
        self.calls += 1
        raise LLMError("timed out")


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


@pytest.mark.parametrize(
    "copy",
    [
        "The system prompt instructed the model to include this story.",
        "The editorial pipeline selected this item.",
        "The story scored highly in relevance scoring.",
        "The script validation check passed.",
    ],
)
def test_contextual_internal_editorial_language_fails(copy: str) -> None:
    with pytest.raises(ScriptError, match="internal editorial machinery"):
        validate_script(f"[HOST]\n{copy}\n")


@pytest.mark.parametrize(
    "copy",
    [
        "The proposal is prompting debate among residents.",
        "Josh Allen scored on the opening drive.",
        "The oil pipeline will remain closed today.",
        "The treatment is awaiting clinical validation.",
    ],
)
def test_internal_editorial_guard_does_not_match_parts_of_listener_words(copy: str) -> None:
    validate_script(f"[HOST]\n{copy}\n")


def test_unresolved_spoken_placeholder_fails() -> None:
    with pytest.raises(ScriptError, match="placeholders"):
        validate_script("[HOST]\nGood morning from [HOST NAME].\n")


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


def test_normalize_script_format_splits_inline_host_marker() -> None:
    normalized = normalize_script_format(
        "```text\n[HOST] This is [HOST NAME] with news from Open, AI.\n```\n"
    )

    assert normalized == "[HOST]\n\nHere is news from OpenAI.\n"
    validate_script(normalized)


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
    claims = [
        "The company released a new artificial intelligence system for software developers.",
        "The release includes tools for testing code and tracing failures across services.",
        "Developers can run the system locally or connect it to an existing hosted project.",
        "The company says the change is intended to shorten debugging and review cycles.",
        "Pricing and broad availability have not yet been announced for every customer tier.",
    ]
    return StoryDossier(
        cluster_id="cluster-001",
        working_headline="Artificial intelligence developer tools",
        what_happened=" ".join(claims[:2]),
        what_is_new_today=claims[2],
        why_it_matters=claims[3],
        background_needed=claims[4],
        facts=[
            DossierFact(claim=claim, supporting_candidate_ids=["source-001"]) for claim in claims
        ],
        recommended_seconds=180,
        source_ids=["source-001"],
    )


def test_recoverable_script_model_error_uses_fallback(tmp_path) -> None:
    llm = FailingScriptLLM()
    script = write_script(profile(), rundown(), [dossier()], tmp_path, llm)

    assert llm.calls == 1
    assert "Good morning" in script
    assert (tmp_path / "script-draft.md").exists()
    diagnostic = (tmp_path / "logs" / "script-fallback.json").read_text(encoding="utf-8")
    assert "small-local-model" in diagnostic
    assert "timed out" in diagnostic


def test_model_script_is_adjusted_validated_and_persisted(tmp_path):
    body = story_script_paragraph(dossier())
    copy = f"[MUSIC: OPENING]\n[HOST]\nGood morning.\n[HOST]\n{body}\n[HOST]\nI'll keep an eye on how this develops.\n[HOST]\nThat's the show for now.\n[MUSIC: CLOSING]\n"

    class Model:
        model = "test"

        def __init__(self):
            self.calls = []

        def generate_text(self, *args, **kwargs):
            raise AssertionError("structured calls only")

        def generate_structured(self, *args, **kwargs):
            self.calls.append((json.loads(args[1]), kwargs["prompt_type"]))
            return args[2](script=copy)

    model = Model()
    result = write_script(profile(), rundown(), [dossier()], tmp_path, model)
    assert [kind for _, kind in model.calls] == ["script", "script_duration_adjustment"]
    assert model.calls[1][0]["target_seconds"] == 180
    assert body in result
    assert (tmp_path / "script-draft.md").read_text() == result
    assert not (tmp_path / "logs/script-fallback.json").exists()


def test_duration_in_range_does_not_call_model():
    text = "[HOST]\n" + "word " * 450
    plan = rundown()
    plan.planned_seconds = estimate_spoken_seconds(text)
    assert script_module.adjust_script_duration_if_needed(text, plan, FailingScriptLLM()) == text


@pytest.mark.parametrize("host", ["HOST", "HOST 2"])
def test_production_cues_preserve_multi_paragraph_story_and_host(host):
    body = story_script_paragraph(dossier())
    continuation = "The schedule provides some breathing room for the participants."
    source = (
        f"[MUSIC: OPENING]\n[{host}]\nGood morning.\n\n{body}\n\n{continuation}\n"
        "[PAUSE: 650]\n[HOST]\nI'll keep an eye on how this develops.\n"
        "[HOST]\nThat's the show for now.\n[MUSIC: CLOSING]\n"
    )
    result = finalize_script(source, profile(), [dossier()])
    validate_script(result)
    script_module.validate_production_directives(result, profile(), [dossier()])
    assert f"[BUMPER: Headlines]\n\n[{host}]\n\n{body}" in result
    assert result.index(continuation) < result.index("[BUMPER: What to Watch]")
    assert finalize_script(result, profile(), [dossier()]) == result


@pytest.mark.parametrize("ending", ["[HOST]\nThat's the show for now.\n[MUSIC: CLOSING]\n", ""])
def test_missing_watch_list_gets_separate_close_not_story_interruption(ending):
    body = story_script_paragraph(dossier())
    source = f"[MUSIC: OPENING]\n[HOST]\n{body}\n\nAdditional details remain uncertain.\n{ending}"
    result = finalize_script(source, profile(), [dossier()])
    validate_script(result)
    script_module.validate_production_directives(result, profile(), [dossier()])
    assert result.index("Additional details") < result.index("[BUMPER: What to Watch]")
    assert finalize_script(result, profile(), [dossier()]) == result


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], ""),
        (["one", ""], "one"),
        (["one", "two"], "one and two"),
        (["one", "two", "three"], "one; two; and three"),
    ],
)
def test_radio_list_joining(items, expected):
    assert script_module.join_for_radio(items) == expected


def test_story_opening_and_production_validation_reject_regressions():
    with pytest.raises(ScriptError, match="headline-style"):
        script_module.validate_story_openings("[HOST]\nFirst up: A story.", [dossier()])
    with pytest.raises(ScriptError, match="reading its headline"):
        script_module.validate_story_openings(
            "[HOST]\nArtificial intelligence developer tools. Further details.", [dossier()]
        )
    with pytest.raises(ScriptError, match="Production directive"):
        script_module.validate_production_directives("[HOST]\nNews.", profile(), [dossier()])
    script_module.validate_production_directives("[HOST]\nNews.", profile(), [])
    script_module.validate_script_quality("[HOST]\nNews.", rundown(), [])


def test_quality_rejects_missing_topic_and_overlong_story():
    with pytest.raises(ScriptError, match="covers only"):
        validate_script_quality("[HOST]\n" + "zebra " * 90, rundown(), [dossier()])
    with pytest.raises(ScriptError, match="too long"):
        validate_script_quality(
            "[HOST]\n" + story_script_paragraph(dossier()) * 15, rundown(), [dossier()]
        )


def test_fallback_fits_short_story_and_preserves_uncertainty(tmp_path) -> None:
    short_rundown = rundown()
    for segment in short_rundown.segments:
        if segment.type == "story":
            segment.planned_seconds = 30
    source = dossier().model_copy(update={"uncertainties": ["Availability is not confirmed."]})
    script = write_script(profile(), short_rundown, [source], tmp_path, FailingScriptLLM())
    sections = [text for _, text in spoken_blocks(script) if "One note of caution:" in text]
    assert len(sections) == 1
    assert "Availability is not confirmed." in sections[0]
    assert spoken_word_count(sections[0]) <= story_word_limit(30)
    assert sections[0].endswith(".")
    validate_script_quality(script, short_rundown, [source])


def test_fallback_does_not_clip_or_skip_an_oversized_lead() -> None:
    with pytest.raises(ScriptError, match="lead and its cautions"):
        story_script_paragraph(dossier(), maximum_words=2)


def test_fallback_word_budget_keeps_complete_sentence_prefix() -> None:
    source = dossier()
    full = story_script_paragraph(source)
    fitted = story_script_paragraph(source, maximum_words=70)
    assert full.startswith(fitted)
    assert fitted.endswith(".")
    assert spoken_word_count(fitted) <= 70


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

    assert "I've got 1 story for you this morning." in script
    assert "What is new today:" not in script
    assert "Why it matters:" not in script
    assert "One caution:" not in script
    assert "Single-source story" not in script
    assert "Published" not in script
    assert "UN votes to adopt new world map to reflect Africa's true size" not in script
    assert "The UN General Assembly has voted to replace the traditional world map." in script
    assert "[BED: bed]" in script
    assert "[BED: STOP]" in script
    assert "[BUMPER: Headlines]" in script
    assert "[BUMPER: What to Watch]" in script


def test_upstream_model_fallback_does_not_skip_script_llm(tmp_path) -> None:
    fallback_path = tmp_path / "dossiers" / "cluster-001-fallback.json"
    fallback_path.parent.mkdir()
    fallback_path.write_text("{}", encoding="utf-8")

    llm = FailingScriptLLM()
    script = write_script(profile(), rundown(), [dossier()], tmp_path, llm)

    assert llm.calls == 1
    assert "Good morning" in script
    assert (tmp_path / "script-draft.md").exists()


def test_script_quality_rejects_headline_reader_copy() -> None:
    shallow = (
        "[HOST]\nGood morning.\n\n[HOST]\n"
        "Artificial intelligence developer tools. The company released a new system.\n"
    )

    with pytest.raises(ScriptError, match="headline"):
        validate_script_quality(shallow, rundown(), [dossier()])


def test_finalize_script_removes_story_titles_and_places_all_asset_classes() -> None:
    second = dossier().model_copy(
        update={
            "cluster_id": "cluster-002",
            "working_headline": "Cloud platform release",
            "what_happened": "The company released a cloud platform for software teams.",
        }
    )
    raw = """[MUSIC: OPENING]

[HOST]
Good morning. Two stories are coming up.

[HOST]
First up: Artificial intelligence developer tools. The company released a new artificial intelligence system for software developers.

[PAUSE: 650]

[HOST]
Also this morning: Cloud platform release. The company released a cloud platform for software teams.

[PAUSE: 650]

[HOST]
I'll keep an eye on these stories.

[HOST]
That's the show for now. Have a good morning.

[MUSIC: CLOSING]
"""

    script = finalize_script(raw, profile(), [dossier(), second])

    assert "First up:" not in script
    assert "Also this morning:" not in script
    assert "Artificial intelligence developer tools." not in script
    assert "Cloud platform release." not in script
    assert script.count("[BED: bed]") == 1
    assert script.count("[BED: STOP]") == 1
    assert script.count("[BUMPER: Headlines]") == 1
    assert script.count("[BUMPER: bumper]") == 1
    assert script.count("[BUMPER: What to Watch]") == 1
