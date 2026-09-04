from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from morning_radio.artifacts.io import atomic_write_json
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
from morning_radio.llm.prompts import RUNDOWN_SYSTEM
from morning_radio.llm.schemas import RundownResponse
from morning_radio.models import EditorialProfile, Rundown, RundownSegment, StoryDossier


def build_rundown(
    show_date: date,
    target_minutes: int,
    profile: EditorialProfile,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> Rundown:
    if llm is not None and not has_upstream_model_fallback(run_dir):
        validation_errors: list[str] = []
        for _attempt in range(2):
            try:
                response = llm.generate_structured(
                    RUNDOWN_SYSTEM,
                    json.dumps(
                        {
                            "show_date": show_date.isoformat(),
                            "target_minutes": target_minutes,
                            "profile": profile.model_dump(mode="json"),
                            "dossiers": [item.model_dump(mode="json") for item in dossiers],
                            "validation_errors": validation_errors,
                        },
                        ensure_ascii=False,
                    ),
                    RundownResponse,
                    stage="planning",
                    prompt_type="rundown",
                )
                candidate = recalculate_planned_seconds(response.rundown)
                validation_errors = validate_rundown(candidate, profile, dossiers, target_minutes * 60)
                if not validation_errors:
                    return _persist_rundown(candidate, run_dir)
            except (LLMError, ValidationError) as exc:
                atomic_write_json(
                    run_dir / "logs" / "rundown-fallback.json",
                    {
                        "model": llm.model,
                        "reason": str(exc),
                        "fixture_fallback": allows_fixture_fallback(llm),
                    },
                )
                break
    segments: list[RundownSegment] = [
        RundownSegment(
            segment_id="open",
            type="opening",
            title="Opening",
            cluster_ids=[],
            planned_seconds=45,
            purpose="Set expectations and preview the most important stories.",
        )
    ]
    if profile.show_format.headline_open:
        segments.append(
            RundownSegment(
                segment_id="headlines",
                type="headlines",
                title="Headlines",
                cluster_ids=[item.cluster_id for item in dossiers[:5]],
                planned_seconds=60,
                purpose="Brief top-of-show rundown.",
            )
        )
    for index, dossier in enumerate(dossiers, start=1):
        segments.append(
            RundownSegment(
                segment_id=f"story-{index}",
                type="story",
                title=dossier.working_headline,
                cluster_ids=[dossier.cluster_id],
                planned_seconds=dossier.recommended_seconds,
                purpose=dossier.why_it_matters,
            )
        )
    if profile.show_format.watch_list_close:
        segments.append(
            RundownSegment(
                segment_id="watch-list",
                type="watch_list",
                title="What to Watch",
                cluster_ids=[item.cluster_id for item in dossiers[:3]],
                planned_seconds=60,
                purpose="Close with forward-looking items to monitor.",
            )
        )
    segments.append(
        RundownSegment(
            segment_id="closing",
            type="closing",
            title="Closing",
            cluster_ids=[],
            planned_seconds=30,
            purpose="End the program cleanly.",
        )
    )
    planned = sum(item.planned_seconds for item in segments)
    rundown = Rundown(
        show_date=show_date,
        target_seconds=target_minutes * 60,
        planned_seconds=planned,
        segments=segments,
    )
    rundown = fit_rundown_to_target(rundown, profile, target_minutes * 60)
    return _persist_rundown(rundown, run_dir)


def has_upstream_model_fallback(run_dir: Path) -> bool:
    if (run_dir / "logs" / "scoring-fallback.json").exists():
        return True
    dossier_dir = run_dir / "dossiers"
    return dossier_dir.exists() and any(dossier_dir.glob("*-fallback.json"))


def validate_rundown(
    rundown: Rundown,
    profile: EditorialProfile,
    dossiers: list[StoryDossier],
    target_seconds: int,
) -> list[str]:
    errors: list[str] = []
    known = {item.cluster_id for item in dossiers}
    story_references: list[str] = []
    for segment in rundown.segments:
        unknown = sorted(set(segment.cluster_ids) - known)
        if unknown:
            errors.append(f"segment {segment.segment_id} references unknown cluster ids: {unknown}")
        if segment.type == "story":
            if len(segment.cluster_ids) != 1:
                errors.append(f"story segment {segment.segment_id} must reference exactly one cluster")
            story_references.extend(segment.cluster_ids)
    duplicates = sorted({cluster_id for cluster_id in story_references if story_references.count(cluster_id) > 1})
    if duplicates:
        errors.append(f"duplicate full-story cluster coverage: {duplicates}")
    missing = sorted(known - set(story_references))
    if missing:
        errors.append(f"missing full-story cluster coverage: {missing}")
    segment_sum = sum(segment.planned_seconds for segment in rundown.segments)
    if rundown.planned_seconds != segment_sum:
        errors.append(
            f"planned_seconds {rundown.planned_seconds} does not equal segment sum {segment_sum}"
        )
    if rundown.target_seconds != target_seconds:
        errors.append(f"target_seconds {rundown.target_seconds} does not equal requested {target_seconds}")
    lower, upper = duration_bounds(profile, target_seconds)
    if not lower <= segment_sum <= upper:
        errors.append(f"planned duration {segment_sum} is outside allowed range {lower}-{upper}")
    return errors


def duration_bounds(profile: EditorialProfile, target_seconds: int) -> tuple[int, int]:
    if profile.show_format.allow_variable_length:
        return profile.show_format.minimum_minutes * 60, profile.show_format.maximum_minutes * 60
    return round(target_seconds * 0.8), round(target_seconds * 1.2)


def recalculate_planned_seconds(rundown: Rundown) -> Rundown:
    return rundown.model_copy(
        update={"planned_seconds": sum(segment.planned_seconds for segment in rundown.segments)}
    )


def fit_rundown_to_target(rundown: Rundown, profile: EditorialProfile, target_seconds: int) -> Rundown:
    segment_sum = sum(segment.planned_seconds for segment in rundown.segments)
    lower, upper = duration_bounds(profile, target_seconds)
    if lower <= segment_sum <= upper:
        return recalculate_planned_seconds(rundown)
    story_segments = [segment for segment in rundown.segments if segment.type == "story"]
    if not story_segments:
        return recalculate_planned_seconds(rundown)
    fixed_seconds = sum(segment.planned_seconds for segment in rundown.segments if segment.type != "story")
    story_budget = max(45 * len(story_segments), min(upper, target_seconds) - fixed_seconds)
    per_story = max(45, round(story_budget / len(story_segments)))
    updated_segments = [
        segment.model_copy(update={"planned_seconds": per_story})
        if segment.type == "story"
        else segment
        for segment in rundown.segments
    ]
    return Rundown(
        show_date=rundown.show_date,
        target_seconds=target_seconds,
        planned_seconds=sum(segment.planned_seconds for segment in updated_segments),
        segments=updated_segments,
    )


def _persist_rundown(rundown: Rundown, run_dir: Path) -> Rundown:
    atomic_write_json(run_dir / "rundown.json", rundown.model_dump(mode="json"))
    return rundown
