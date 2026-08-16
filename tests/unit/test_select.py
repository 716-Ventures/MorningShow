from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl

from morning_radio.models import (
    EditorialProfile,
    EditorialStyle,
    GlobalNewsProfile,
    Interest,
    LocationProfile,
    ShowFormat,
    StoryScore,
    VoicePreferences,
)
from morning_radio.newsroom.select import select_stories
from morning_radio.settings import (
    AppSettings,
    LLMSettings,
    NewsSettings,
    SelectionSettings,
    VerificationSettings,
)


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
            target_minutes=25,
            minimum_minutes=5,
            maximum_minutes=30,
            allow_variable_length=True,
            headline_open=True,
            watch_list_close=True,
            host_count=1,
        ),
        voice_preferences=VoicePreferences(),
    )


def settings() -> AppSettings:
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
            maximum_selected_stories=10,
        ),
        verification=VerificationSettings(maximum_correction_cycles=2),
    )


def score(cluster_id: str, final_score: int = 86, matched_interest: str | None = None) -> StoryScore:
    return StoryScore(
        cluster_id=cluster_id,
        relevance=90,
        importance=80,
        freshness=80,
        locality=20,
        novelty=80,
        confidence=90,
        matched_interests=[matched_interest or cluster_id],
        reason="fixture",
        final_score=final_score,
    )


def test_selection_uses_target_minutes_override_without_mutating_profile(tmp_path: Path) -> None:
    editorial_profile = profile()
    result = select_stories(
        [score("one"), score("two"), score("three")],
        editorial_profile,
        settings(),
        tmp_path,
        target_minutes=10,
    )
    assert [item.cluster_id for item in result.selected] == ["one", "two"]
    assert result.not_selected_high_score[0].cluster_id == "three"
    assert result.not_selected_high_score[0].rejection_reason == "duration_budget"
    assert editorial_profile.show_format.target_minutes == 25


def test_selection_records_specific_rejection_reason(tmp_path: Path) -> None:
    result = select_stories(
        [score("one", matched_interest="ai"), score("one-again", final_score=70, matched_interest="ai")],
        profile(),
        settings(),
        tmp_path,
        target_minutes=10,
    )
    assert result.not_selected_high_score[0].rejection_reason == "subject_diversity"
