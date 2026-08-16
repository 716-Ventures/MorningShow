from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from morning_radio.models import (
    AudioMetadata,
    Cluster,
    EditorialProfile,
    EditorialStyle,
    GlobalNewsProfile,
    Interest,
    LocationProfile,
    Rundown,
    RundownSegment,
    ShowFormat,
    VoicePreferences,
)


def test_persisted_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Cluster.model_validate(
            {
                "cluster_id": "cluster-001",
                "canonical_title": "Story",
                "candidate_ids": ["a"],
                "source_count": 1,
                "fingerprint": "abc",
                "unexpected": True,
            }
        )


def test_show_format_requires_target_inside_bounds() -> None:
    with pytest.raises(ValidationError, match="target_minutes"):
        ShowFormat(
            target_minutes=20,
            minimum_minutes=5,
            maximum_minutes=15,
            allow_variable_length=True,
            headline_open=True,
            watch_list_close=True,
            host_count=1,
        )


def test_profile_requires_unique_interests() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    with pytest.raises(ValidationError, match="unique"):
        EditorialProfile(
            created_at=now,
            updated_at=now,
            location=LocationProfile(home="Buffalo, NY", local_scope="Western New York"),
            interests=[
                Interest(name="AI", priority=5, depth="normal"),
                Interest(name="ai", priority=4, depth="normal"),
            ],
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


def test_cluster_source_count_must_match_candidate_ids() -> None:
    with pytest.raises(ValidationError, match="source_count"):
        Cluster(
            cluster_id="cluster-001",
            canonical_title="Story",
            candidate_ids=["a", "b"],
            source_count=1,
            fingerprint="abc",
        )


def test_rundown_planned_seconds_must_match_segment_sum() -> None:
    with pytest.raises(ValidationError, match="planned_seconds"):
        Rundown(
            show_date=date(2026, 8, 15),
            target_seconds=600,
            planned_seconds=600,
            segments=[
                RundownSegment(
                    segment_id="story-1",
                    type="story",
                    title="Story",
                    cluster_ids=["cluster-001"],
                    planned_seconds=120,
                    purpose="test",
                )
            ],
        )


def test_audio_metadata_requires_positive_duration(tmp_path) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        AudioMetadata(
            voice="tone",
            text_hash="abc",
            duration_seconds=0,
            path=tmp_path / "voice.wav",
        )
