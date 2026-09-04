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


def score(
    cluster_id: str, final_score: int = 86, matched_interest: str | None = None
) -> StoryScore:
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


def generic_score(cluster_id: str, final_score: int = 60) -> StoryScore:
    return StoryScore(
        cluster_id=cluster_id,
        relevance=20,
        importance=50,
        freshness=80,
        locality=20,
        novelty=80,
        confidence=70,
        matched_interests=[],
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


def test_selection_allows_repeated_profile_interest_before_generic_filler(tmp_path: Path) -> None:
    result = select_stories(
        [
            score("interest-one", final_score=70, matched_interest="AI"),
            score("interest-two", final_score=69, matched_interest="AI"),
            generic_score("generic-one", final_score=60),
        ],
        profile(),
        settings(),
        tmp_path,
        target_minutes=10,
    )
    assert [item.cluster_id for item in result.selected[:2]] == ["interest-one", "interest-two"]
    assert [item.cluster_id for item in result.selected] == ["interest-one", "interest-two"]
    assert result.not_selected_high_score[0].cluster_id == "generic-one"
    assert result.not_selected_high_score[0].rejection_reason == "profile_mismatch_padding"


def test_selection_still_limits_repeated_low_priority_subjects(tmp_path: Path) -> None:
    editorial_profile = profile().model_copy(
        update={"interests": [Interest(name="Sports", priority=1, depth="normal")]}
    )
    result = select_stories(
        [
            score("one", final_score=70, matched_interest="Sports"),
            score("two", final_score=70, matched_interest="Sports"),
            score("three", final_score=70, matched_interest="Sports"),
        ],
        editorial_profile,
        settings(),
        tmp_path,
        target_minutes=10,
    )
    assert result.not_selected_high_score[0].rejection_reason == "subject_diversity"


def test_selection_reserves_one_distinct_story_per_available_interest(tmp_path: Path) -> None:
    editorial_profile = profile().model_copy(
        update={
            "interests": [
                Interest(name="Bills Football", priority=1, depth="normal"),
                Interest(name="AI", priority=5, depth="normal"),
                Interest(name="Apple Incorporated", priority=3, depth="normal"),
            ]
        }
    )
    result = select_stories(
        [
            score("ai-one", final_score=90, matched_interest="AI"),
            score("ai-two", final_score=89, matched_interest="AI"),
            score("ai-three", final_score=88, matched_interest="AI"),
            score("apple", final_score=30, matched_interest="Apple Incorporated"),
            score("bills", final_score=45, matched_interest="Bills Football"),
        ],
        editorial_profile,
        settings(),
        tmp_path,
        target_minutes=10,
    )

    assert [item.cluster_id for item in result.selected] == ["ai-one", "apple", "bills"]
    assert result.uncovered_interests == []


def test_selection_reports_interest_without_an_eligible_story(tmp_path: Path) -> None:
    editorial_profile = profile().model_copy(
        update={
            "interests": [
                Interest(name="AI", priority=5, depth="normal"),
                Interest(name="Apple Incorporated", priority=3, depth="normal"),
            ]
        }
    )
    result = select_stories(
        [score("ai", final_score=90, matched_interest="AI")],
        editorial_profile,
        settings(),
        tmp_path,
        target_minutes=10,
    )

    assert result.uncovered_interests == ["Apple Incorporated"]


def test_selection_covers_requested_interest_despite_zero_model_score(tmp_path: Path) -> None:
    result = select_stories(
        [score("bills", final_score=0, matched_interest="AI")],
        profile(),
        settings(),
        tmp_path,
        target_minutes=10,
    )

    assert [item.cluster_id for item in result.selected] == ["bills"]
    assert result.uncovered_interests == []
