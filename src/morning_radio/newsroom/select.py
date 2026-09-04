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
    selected_ids: set[str] = set()
    assigned_interests: set[str] = set()
    used_subjects: dict[str, int] = {}
    spent = 0
    has_profile_matches = any(score.matched_interests for score in scores)
    ordered_interests = sorted(
        enumerate(profile.interests),
        key=lambda item: (-item[1].priority, item[0]),
    )
    coverage_slots = min(len(ordered_interests), settings.selection.maximum_selected_stories)
    coverage_seconds_cap = max(75, budget // max(1, coverage_slots))

    for _, interest in ordered_interests:
        if len(selected) >= settings.selection.maximum_selected_stories:
            break
        interest_key = interest.name.casefold()
        candidate = next(
            (
                score
                for score in scores
                if score.cluster_id not in selected_ids
                and any(name.casefold() == interest_key for name in score.matched_interests)
                and not (
                    score.negative_matches
                    and score.importance < settings.selection.major_news_importance_threshold
                )
            ),
            None,
        )
        if candidate is None:
            continue
        seconds = min(estimate_seconds(candidate, profile), coverage_seconds_cap)
        if spent + seconds > budget and selected:
            continue
        selected.append(as_selected_story(candidate, seconds))
        selected_ids.add(candidate.cluster_id)
        assigned_interests.add(interest_key)
        subject = interest_key
        used_subjects[subject] = used_subjects.get(subject, 0) + 1
        spent += seconds

    for score in scores:
        if score.cluster_id in selected_ids:
            continue
        seconds = estimate_seconds(score, profile)
        subject = (
            score.matched_interests[0] if score.matched_interests else score.cluster_id
        ).lower()
        ordinary_negative = (
            score.negative_matches
            and score.importance < settings.selection.major_news_importance_threshold
        )
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
        story = as_selected_story(score, seconds)
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
        selected_ids.add(score.cluster_id)
        used_subjects[subject] = used_subjects.get(subject, 0) + 1
        spent += seconds
    if not selected:
        raise RuntimeError("No stories survived selection.")
    uncovered_interests = [
        interest.name
        for interest in profile.interests
        if interest.name.casefold() not in assigned_interests
    ]
    result = SelectionResult(
        selected=selected,
        not_selected_high_score=rejected,
        uncovered_interests=uncovered_interests,
    )
    atomic_write_json(run_dir / "selected-stories.json", result.model_dump(mode="json"))
    return result


def subject_allowance(score: StoryScore, profile: EditorialProfile) -> int:
    if not score.matched_interests:
        return 1
    priority_by_interest = {
        interest.name.casefold(): interest.priority for interest in profile.interests
    }
    priority = max(
        (priority_by_interest.get(name.casefold(), 1) for name in score.matched_interests),
        default=1,
    )
    return max(2, min(4, priority + 1))


def as_selected_story(score: StoryScore, seconds: int) -> SelectedStory:
    return SelectedStory(
        cluster_id=score.cluster_id,
        reason=score.reason,
        estimated_seconds=seconds,
        score=score.final_score,
    )
