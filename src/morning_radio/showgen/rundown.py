from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from morning_radio.models import EditorialProfile, Rundown, RundownSegment, StoryDossier


def build_rundown(
    show_date: date, target_minutes: int, profile: EditorialProfile, dossiers: list[StoryDossier], run_dir: Path
) -> Rundown:
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
    (run_dir / "rundown.json").write_text(
        json.dumps(rundown.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return rundown
