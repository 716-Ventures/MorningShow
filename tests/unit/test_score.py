from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from morning_radio import db
from morning_radio.llm.client import LLMError
from morning_radio.models import (
    Cluster,
    EditorialProfile,
    EditorialStyle,
    ExtractionResult,
    GlobalNewsProfile,
    Interest,
    LocationProfile,
    ShowFormat,
    StoryScore,
    VoicePreferences,
)
from morning_radio.newsroom.score import score_stories


class CapturingScoringLLM:
    model = "test"

    def __init__(self, score: int = 80) -> None:
        self.payload = {}
        self.score = score

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        self.payload = json.loads(args[1])
        return response_model(
            scores=[
                StoryScore(
                    cluster_id="cluster-001",
                    relevance=80,
                    importance=80,
                    freshness=80,
                    locality=20,
                    novelty=80,
                    confidence=90,
                    reason="fixture",
                    final_score=self.score,
                )
            ]
        )


class ProgrammingErrorLLM:
    model = "buggy"

    def generate_text(self, *args, **kwargs) -> str:
        return ""

    def generate_structured(self, *args, **kwargs):
        raise KeyError("programming bug")


class FixtureFallbackLLM:
    model = "fixture"
    fixture_fallback = True

    def generate_text(self, *args, **kwargs) -> str:
        return ""

    def generate_structured(self, *args, **kwargs):
        raise LLMError("fixture model unavailable")


class RecoverableScoringErrorLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs) -> str:
        return ""

    def generate_structured(self, *args, **kwargs):
        raise LLMError("schema drift")


class ExplodingScoringLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs) -> str:
        return ""

    def generate_structured(self, *args, **kwargs):
        raise AssertionError("LLM should not be called for oversized scoring prompts")


def profile(*, negative_preferences: list[str] | None = None) -> EditorialProfile:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return EditorialProfile(
        created_at=now,
        updated_at=now,
        location=LocationProfile(home="Buffalo, NY", local_scope="Western New York"),
        interests=[Interest(name="AI", priority=5, depth="normal", subtopics=["agents"])],
        global_news=GlobalNewsProfile(include_major_us=True, include_major_world=True),
        negative_preferences=negative_preferences or [],
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


def cluster(*, title: str = "OpenAI launches coding agents", fingerprint: str = "fingerprint") -> Cluster:
    return Cluster(
        cluster_id="cluster-001",
        canonical_title=title,
        candidate_ids=["candidate-001"],
        source_count=1,
        topic_hints=["ai"],
        fingerprint=fingerprint,
    )


def numbered_cluster(index: int) -> Cluster:
    return Cluster(
        cluster_id=f"cluster-{index:03d}",
        canonical_title=f"Large Evidence Story {index}",
        candidate_ids=[f"candidate-{index:03d}"],
        source_count=1,
        topic_hints=["general"],
        fingerprint=f"fingerprint-{index:03d}",
    )


def extraction() -> ExtractionResult:
    return ExtractionResult(
        candidate_id="candidate-001",
        url="https://example.com/story",
        title="OpenAI launches coding agents",
        text="Evidence text about the coding agents.",
        word_count=7,
        extraction_status="usable",
    )


def numbered_extraction(index: int) -> ExtractionResult:
    return ExtractionResult(
        candidate_id=f"candidate-{index:03d}",
        url=f"https://example.com/story-{index}",
        title=f"Large Evidence Story {index}",
        text="Large evidence sentence. " * 200,
        word_count=600,
        extraction_status="usable",
    )


def test_scoring_payload_includes_memory_evidence_and_history(tmp_path: Path) -> None:
    memory_path = tmp_path / "editorial-memory.md"
    memory_path.write_text("Prefer practical developer impact.", encoding="utf-8")
    db_path = tmp_path / "app.db"
    db.initialize(db_path)
    db.update_story_history(db_path, [("fingerprint", "OpenAI launches coding agents")], "2026-08-14")
    llm = CapturingScoringLLM()

    score_stories(
        [cluster()],
        profile(),
        tmp_path,
        llm,
        extractions=[extraction()],
        editorial_memory_path=memory_path,
        db_path=db_path,
    )

    assert llm.payload["editorial_memory"] == "Prefer practical developer impact."
    assert llm.payload["extraction_evidence"]["cluster-001"][0]["candidate_id"] == "candidate-001"
    assert llm.payload["story_history"]["fingerprint"]["include_count"] == 1


def test_negative_preference_is_persisted_as_modifier(tmp_path: Path) -> None:
    scores = score_stories(
        [cluster(title="Celebrity lifestyle brand launches")],
        profile(negative_preferences=["celebrity"]),
        tmp_path,
        llm=None,
    )
    assert scores[0].raw_final_score is not None
    assert scores[0].final_score == max(0, scores[0].raw_final_score - 35)
    assert scores[0].modifiers[0].name == "negative_preference"


def test_repeat_story_history_reduces_score_and_novelty(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    db.initialize(db_path)
    db.update_story_history(db_path, [("fingerprint", "OpenAI launches coding agents")], "2026-08-14")
    scores = score_stories([cluster()], profile(), tmp_path, llm=None, db_path=db_path)

    assert scores[0].raw_final_score is not None
    assert scores[0].final_score == max(0, scores[0].raw_final_score - 25)
    assert scores[0].novelty == 25
    assert scores[0].modifiers[0].name == "repeat_story"


def test_scoring_programming_error_propagates(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="programming bug"):
        score_stories([cluster()], profile(), tmp_path, ProgrammingErrorLLM())


def test_scoring_expected_fixture_error_uses_explicit_fallback(tmp_path: Path) -> None:
    scores = score_stories([cluster()], profile(), tmp_path, FixtureFallbackLLM())

    assert scores
    assert scores[0].reason.startswith("Matched")


def test_scoring_recoverable_model_error_uses_fallback_with_diagnostic(tmp_path: Path) -> None:
    scores = score_stories([cluster()], profile(), tmp_path, RecoverableScoringErrorLLM())

    assert scores
    assert scores[0].reason.startswith("Matched")
    diagnostic = (tmp_path / "logs" / "scoring-fallback.json").read_text(encoding="utf-8")
    assert "small-local-model" in diagnostic
    assert "schema drift" in diagnostic


def test_oversized_scoring_prompt_skips_llm_with_diagnostic(tmp_path: Path) -> None:
    scores = score_stories(
        [numbered_cluster(index) for index in range(80)],
        profile(),
        tmp_path,
        ExplodingScoringLLM(),
        extractions=[numbered_extraction(index) for index in range(80)],
    )

    assert len(scores) == 80
    diagnostic = (tmp_path / "logs" / "scoring-fallback.json").read_text(encoding="utf-8")
    assert "Scoring prompt exceeded" in diagnostic
    assert "input_character_count" in diagnostic
