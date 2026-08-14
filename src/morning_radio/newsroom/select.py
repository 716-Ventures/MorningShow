from __future__ import annotations

import json
from pathlib import Path

from morning_radio.models import EditorialProfile, SelectedStory, SelectionResult, StoryScore
from morning_radio.settings import AppSettings


def estimate_seconds(score: StoryScore, profile: EditorialProfile) -> int:
    if score.final_score >= 85:
        return 240 if profile.editorial_style.context_level == "analysis" else 180
    if score.final_score >= 70:
        return 150
    return 75


def select_stories(
    scores: list[StoryScore], profile: EditorialProfile, settings: AppSettings, run_dir: Path
) -> SelectionResult:
    reserved = 90
    if profile.show_format.headline_open:
        reserved += 60
    if profile.show_format.watch_list_close:
        reserved += 60
    target_seconds = profile.show_format.target_minutes * 60
    budget = max(180, target_seconds - reserved)
    selected: list[SelectedStory] = []
    rejected: list[SelectedStory] = []
    used_subjects: set[str] = set()
    spent = 0
    for score in scores:
        seconds = estimate_seconds(score, profile)
        subject = (score.matched_interests[0] if score.matched_interests else score.cluster_id).lower()
        ordinary_negative = score.negative_matches and score.importance < settings.selection.major_news_importance_threshold
        too_much_same_subject = subject in used_subjects and score.final_score < 82
        would_overrun = spent + seconds > budget and selected
        story = SelectedStory(
            cluster_id=score.cluster_id,
            reason=score.reason,
            estimated_seconds=seconds,
            score=score.final_score,
        )
        if (
            len(selected) >= settings.selection.maximum_selected_stories
            or score.final_score < 45
            or ordinary_negative
            or too_much_same_subject
            or would_overrun
        ):
            if score.final_score >= 60:
                rejected.append(story)
            continue
        selected.append(story)
        used_subjects.add(subject)
        spent += seconds
    if not selected:
        raise RuntimeError("No stories survived selection.")
    result = SelectionResult(selected=selected, not_selected_high_score=rejected)
    (run_dir / "selected-stories.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result
