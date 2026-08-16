from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

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
from morning_radio.showgen.rundown import build_rundown, validate_rundown


class CorrectingRundownLLM:
    model = "test"

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        self.payloads.append(json.loads(args[1]))
        if len(self.payloads) == 1:
            return response_model(
                rundown=Rundown(
                    show_date=date(2026, 8, 15),
                    target_seconds=600,
                    planned_seconds=100,
                    segments=[
                        segment("story-1", "story", ["missing"], 100),
                    ],
                )
            )
        return response_model(
            rundown=Rundown(
                show_date=date(2026, 8, 15),
                target_seconds=600,
                planned_seconds=300,
                segments=[
                    segment("story-1", "story", ["cluster-001"], 300),
                ],
            )
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
            target_minutes=10,
            minimum_minutes=5,
            maximum_minutes=15,
            allow_variable_length=True,
            headline_open=False,
            watch_list_close=False,
            host_count=1,
        ),
        voice_preferences=VoicePreferences(),
    )


def dossier(cluster_id: str = "cluster-001") -> StoryDossier:
    return StoryDossier(
        cluster_id=cluster_id,
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[DossierFact(claim="Fact.", supporting_candidate_ids=["source-001"])],
        recommended_seconds=180,
        source_ids=["source-001"],
    )


def segment(
    segment_id: str,
    segment_type: Literal[
        "opening",
        "headlines",
        "story",
        "quick_hits",
        "local",
        "sports",
        "watch_list",
        "closing",
    ],
    cluster_ids: list[str],
    seconds: int,
) -> RundownSegment:
    return RundownSegment(
        segment_id=segment_id,
        type=segment_type,
        title=segment_id,
        cluster_ids=cluster_ids,
        planned_seconds=seconds,
        purpose="test",
    )


def test_validate_rundown_reports_unknown_duplicate_and_sum_errors() -> None:
    rundown = Rundown.model_construct(
        show_date=date(2026, 8, 15),
        target_seconds=600,
        planned_seconds=999,
        segments=[
            segment("story-1", "story", ["cluster-001"], 120),
            segment("story-2", "story", ["cluster-001", "missing"], 120),
        ],
    )
    errors = validate_rundown(rundown, profile(), [dossier()], 600)
    assert any("unknown cluster" in item for item in errors)
    assert any("duplicate full-story" in item for item in errors)
    assert any("planned_seconds" in item for item in errors)


def test_build_rundown_retries_with_validation_errors(tmp_path: Path) -> None:
    llm = CorrectingRundownLLM()
    rundown = build_rundown(date(2026, 8, 15), 10, profile(), [dossier()], tmp_path, llm)
    assert rundown.segments[0].cluster_ids == ["cluster-001"]
    assert llm.payloads[1]["validation_errors"]
    assert (tmp_path / "rundown.json").exists()
