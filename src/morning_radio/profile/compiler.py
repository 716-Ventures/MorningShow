from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from morning_radio.artifacts.runs import atomic_write_json
from morning_radio.models import EditorialProfile
from morning_radio.settings import repo_root


class ProfileError(RuntimeError):
    pass


def profile_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / "profile.json"


def profile_summary_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / "profile.md"


def memory_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / "editorial-memory.md"


def load_profile(root: Path | None = None) -> EditorialProfile:
    path = profile_path(root)
    if not path.exists():
        raise ProfileError("Missing data/profile.json. Run ./show configure first.")
    with path.open("r", encoding="utf-8") as handle:
        return EditorialProfile.model_validate_json(handle.read())


def save_profile(profile: EditorialProfile, root: Path | None = None) -> None:
    base = root or repo_root()
    atomic_write_json(profile_path(base), profile.model_dump(mode="json"))
    summary_path = profile_summary_path(base)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = summary_path.with_suffix(".tmp")
    tmp.write_text(render_profile_summary(profile), encoding="utf-8")
    tmp.replace(summary_path)
    mem = memory_path(base)
    if not mem.exists():
        mem.write_text("# Editorial Memory\n\nNo durable feedback yet.\n", encoding="utf-8")


def render_profile_summary(profile: EditorialProfile) -> str:
    interests = "\n".join(
        f"- {item.name} (priority {item.priority}, {item.depth})"
        + (f": {', '.join(item.subtopics)}" if item.subtopics else "")
        for item in profile.interests
    )
    exclusions = ", ".join(profile.negative_preferences) or "None"
    return f"""# Editorial Profile

Updated: {profile.updated_at.isoformat()}

Home: {profile.location.home}
Local scope: {profile.location.local_scope}

## Interests

{interests}

## General News

- Major U.S. news: {'yes' if profile.global_news.include_major_us else 'no'}
- Major world news: {'yes' if profile.global_news.include_major_world else 'no'}
- Threshold: {profile.global_news.threshold}

## Style

- Depth: {profile.editorial_style.context_level}
- Assume familiarity: {'yes' if profile.editorial_style.assume_subject_familiarity else 'no'}
- Ongoing stories: {profile.editorial_style.ongoing_story_policy}
- Avoid padding: {'yes' if profile.editorial_style.avoid_padding else 'no'}

## Format

- Target minutes: {profile.show_format.target_minutes}
- Variable length: {'yes' if profile.show_format.allow_variable_length else 'no'}
- Range: {profile.show_format.minimum_minutes}-{profile.show_format.maximum_minutes} minutes
- Headline open: {'yes' if profile.show_format.headline_open else 'no'}
- Watch-list close: {'yes' if profile.show_format.watch_list_close else 'no'}
- Hosts: {profile.show_format.host_count}

## Exclusions

{exclusions}
"""


def default_profile() -> EditorialProfile:
    now = datetime.now().astimezone()
    return EditorialProfile.model_validate(
        {
            "schema_version": 1,
            "created_at": now,
            "updated_at": now,
            "location": {"home": "Buffalo, NY", "local_scope": "Western New York"},
            "interests": [
                {
                    "name": "Artificial intelligence",
                    "priority": 5,
                    "depth": "deep",
                    "subtopics": ["AI products", "software development", "AI companies"],
                    "inclusion_notes": "Include meaningful developments that affect builders.",
                    "exclusion_notes": "Skip trivial benchmark churn.",
                }
            ],
            "global_news": {
                "include_major_us": True,
                "include_major_world": True,
                "threshold": "major_only",
            },
            "negative_preferences": ["celebrity news", "entertainment gossip"],
            "editorial_style": {
                "context_level": "analysis",
                "assume_subject_familiarity": True,
                "ongoing_story_policy": "changes_only",
                "tone": "conversational_intelligent",
                "avoid_padding": True,
            },
            "show_format": {
                "target_minutes": 25,
                "minimum_minutes": 15,
                "maximum_minutes": 30,
                "allow_variable_length": True,
                "headline_open": True,
                "watch_list_close": True,
                "host_count": 1,
            },
            "voice_preferences": {"primary_voice": None, "secondary_voice": None, "pace": "normal"},
        }
    )


def export_profile_json(profile: EditorialProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), indent=2)
