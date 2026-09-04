from __future__ import annotations

from pathlib import Path

from morning_radio.artifacts.io import atomic_write_json
from morning_radio.models import EditorialProfile, SelectedStory, SelectionResult, StoryScore
from morning_radio.settings import AppSettings


def estimate_seconds(score: StoryScore, profile: EditorialProfile) -> int:
    if score.final_score >= 85:
        return 240 if profile.editorial_style.context_level == "analysis" else 180
    if score.final_score >= 70:
        return 150
    return 75


def select_stories(
    scores: list[StoryScore],
    profile: EditorialProfile,
    settings: AppSettings,
    run_dir: Path,
    *,
    target_minutes: int | None = None,
) -> SelectionResult:
    reserved = 90
    if profile.show_format.headline_open:
        reserved += 60
    if profile.show_format.watch_list_close:
        reserved += 60
    target_seconds = (target_minutes or profile.show_format.target_minutes) * 60
    budget = max(180, target_seconds - reserved)
    selected: list[SelectedStory] = []
    rejected: list[SelectedStory] = []
    used_subjects: dict[str, int] = {}
    spent = 0
    has_profile_matches = any(score.matched_interests for score in scores)
    for score in scores:
        seconds = estimate_seconds(score, profile)
        subject = (score.matched_interests[0] if score.matched_interests else score.cluster_id).lower()
        ordinary_negative = score.negative_matches and score.importance < settings.selection.major_news_importance_threshold
        unrelated_padding = (
            profile.editorial_style.avoid_padding
            and has_profile_matches
            and not score.matched_interests
            and score.importance < settings.selection.major_news_importance_threshold
            and score.locality < settings.selection.major_news_importance_threshold
        )
        too_much_same_subject = (
            used_subjects.get(subject, 0) >= subject_allowance(score, profile)
            and score.final_score < settings.selection.major_news_importance_threshold
        )
        would_overrun = spent + seconds > budget and selected
        story = SelectedStory(
            cluster_id=score.cluster_id,
            reason=score.reason,
            estimated_seconds=seconds,
            score=score.final_score,
        )
        rejection_reason = None
        if len(selected) >= settings.selection.maximum_selected_stories:
            rejection_reason = "maximum_selected_stories"
        elif score.final_score < 45:
            rejection_reason = "score_below_threshold"
        elif ordinary_negative:
            rejection_reason = "negative_preference_below_major_news_threshold"
        elif unrelated_padding:
            rejection_reason = "profile_mismatch_padding"
        elif too_much_same_subject:
            rejection_reason = "subject_diversity"
        elif would_overrun:
            rejection_reason = "duration_budget"
        if rejection_reason is not None:
            if score.final_score >= 60:
                rejected.append(story.model_copy(update={"rejection_reason": rejection_reason}))
            continue
        selected.append(story)
        used_subjects[subject] = used_subjects.get(subject, 0) + 1
        spent += seconds
    if not selected:
        raise RuntimeError("No stories survived selection.")
    result = SelectionResult(selected=selected, not_selected_high_score=rejected)
    atomic_write_json(run_dir / "selected-stories.json", result.model_dump(mode="json"))
    return result


def subject_allowance(score: StoryScore, profile: EditorialProfile) -> int:
    if not score.matched_interests:
        return 1
    priority_by_interest = {interest.name.casefold(): interest.priority for interest in profile.interests}
    priority = max((priority_by_interest.get(name.casefold(), 1) for name in score.matched_interests), default=1)
    return max(2, min(4, priority + 1))
