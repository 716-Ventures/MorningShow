from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console

from morning_radio.models import EditorialProfile
from morning_radio.profile.compiler import default_profile, render_profile_summary, save_profile

console = Console()


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = typer.prompt(f"{prompt}{suffix}", default=default or "", show_default=False).strip()
    return value or (default or "")


def _confirm(prompt: str, default: bool = True) -> bool:
    return typer.confirm(prompt, default=default)


def _choice(prompt: str, choices: set[str], default: str) -> str:
    while True:
        value = _ask(prompt, default).lower().replace(" ", "_")
        if value in choices:
            return value
        console.print(f"Choose one of: {', '.join(sorted(choices))}")


def _int(prompt: str, minimum: int, maximum: int, default: int) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            console.print("Enter a number.")
            continue
        if minimum <= value <= maximum:
            return value
        console.print(f"Enter a number from {minimum} to {maximum}.")


def run_interview(existing: EditorialProfile | None = None) -> EditorialProfile | None:
    base = existing or default_profile()
    profile = _collect_profile(base, existing is not None)
    while True:
        console.print(render_profile_summary(profile))
        answer = _choice("Confirm profile: yes, edit, or cancel", {"yes", "edit", "cancel"}, "yes")
        if answer == "cancel":
            console.print("Canceled. Existing profile left untouched.")
            return None
        if answer == "yes":
            save_profile(profile)
            console.print("[green]Wrote data/profile.json and data/profile.md[/green]")
            return profile
        profile = _targeted_edit(profile)


def _collect_profile(base: EditorialProfile, has_existing: bool) -> EditorialProfile:
    now = datetime.now().astimezone()
    console.print("[bold]Personal Morning Radio setup[/bold]")
    console.print("Short answers are enough. Type 'done' when a list is finished.")

    home = _ask("Where are you based?", base.location.home)
    local_scope = _ask("What counts as local for you?", base.location.local_scope)

    interests = []
    seen = set()
    console.print("\nAdd interests one at a time.")
    while True:
        name = _ask("Interest name, or done")
        if name.lower() == "done":
            break
        if not name:
            console.print("Interest name cannot be empty.")
            continue
        key = name.casefold()
        if key in seen:
            console.print("That interest is already listed.")
            continue
        seen.add(key)
        priority = _int(f"Priority for {name} (1-5)", 1, 5, 3)
        depth = _choice(
            f"Depth for {name}: major_only, normal, or deep",
            {"major_only", "normal", "deep"},
            "deep" if priority >= 4 else "normal",
        )
        subtopics = []
        if priority >= 4:
            console.print("Add subtopics for high-priority interests.")
            while True:
                subtopic = _ask("Subtopic, or done")
                if subtopic.lower() == "done":
                    break
                if subtopic:
                    subtopics.append(subtopic)
        interests.append(
            {
                "name": name,
                "priority": priority,
                "depth": depth,
                "subtopics": subtopics,
                "inclusion_notes": _ask("Anything to include for this interest?", ""),
                "exclusion_notes": _ask("Anything to avoid for this interest?", ""),
            }
        )

    if not interests:
        interests = [item.model_dump() for item in base.interests]

    include_us = _confirm("Include major U.S. news outside stated interests?", True)
    include_world = _confirm("Include major world news outside stated interests?", True)

    negative_preferences = []
    while True:
        item = _ask("Subject to exclude, or done")
        if item.lower() == "done":
            break
        if item:
            negative_preferences.append(item)

    context_level = _choice(
        "Desired depth: summary, context, or analysis",
        {"summary", "context", "analysis"},
        base.editorial_style.context_level,
    )
    assume_familiarity = _confirm("Assume familiarity with high-priority subjects?", True)
    ongoing = _choice(
        "Ongoing-story policy: changes_only, brief_updates, or always_context",
        {"changes_only", "brief_updates", "always_context"},
        base.editorial_style.ongoing_story_policy,
    )
    variable = _confirm("Allow variable show length?", base.show_format.allow_variable_length)
    if variable:
        minimum = _int("Minimum minutes", 5, 90, base.show_format.minimum_minutes)
        maximum = _int("Maximum minutes", minimum, 90, base.show_format.maximum_minutes)
        target = _int("Normal target minutes", minimum, maximum, base.show_format.target_minutes)
    else:
        target = _int("Fixed show minutes", 5, 90, base.show_format.target_minutes)
        minimum = target
        maximum = target

    headline_open = _confirm(
        "Include top-of-show headline rundown?", base.show_format.headline_open
    )
    watch_list_close = _confirm(
        "Include closing what-to-watch section?", base.show_format.watch_list_close
    )
    host_count = _int("One host or two hosts?", 1, 2, base.show_format.host_count)

    profile = EditorialProfile.model_validate(
        {
            "schema_version": 1,
            "created_at": base.created_at if has_existing else now,
            "updated_at": now,
            "location": {"home": home, "local_scope": local_scope},
            "interests": interests,
            "global_news": {
                "include_major_us": include_us,
                "include_major_world": include_world,
                "threshold": "major_only",
            },
            "negative_preferences": negative_preferences,
            "editorial_style": {
                "context_level": context_level,
                "assume_subject_familiarity": assume_familiarity,
                "ongoing_story_policy": ongoing,
                "tone": "conversational_intelligent",
                "avoid_padding": True,
            },
            "show_format": {
                "target_minutes": target,
                "minimum_minutes": minimum,
                "maximum_minutes": maximum,
                "allow_variable_length": variable,
                "headline_open": headline_open,
                "watch_list_close": watch_list_close,
                "host_count": host_count,
            },
            "voice_preferences": base.voice_preferences.model_dump(),
        }
    )

    return profile


def _targeted_edit(profile: EditorialProfile) -> EditorialProfile:
    category = _choice(
        "Edit category: location, interests, news, exclusions, style, format, or voice",
        {"location", "interests", "news", "exclusions", "style", "format", "voice"},
        "interests",
    )
    data = profile.model_dump()
    now = datetime.now().astimezone()
    data["updated_at"] = now
    if category == "location":
        data["location"] = {
            "home": _ask("Where are you based?", profile.location.home),
            "local_scope": _ask("What counts as local for you?", profile.location.local_scope),
        }
    elif category == "interests":
        console.print("Re-enter interests for the profile.")
        data["interests"] = _collect_interests(profile)
    elif category == "news":
        data["global_news"] = {
            "include_major_us": _confirm(
                "Include major U.S. news outside stated interests?",
                profile.global_news.include_major_us,
            ),
            "include_major_world": _confirm(
                "Include major world news outside stated interests?",
                profile.global_news.include_major_world,
            ),
            "threshold": "major_only",
        }
    elif category == "exclusions":
        data["negative_preferences"] = _collect_exclusions(profile.negative_preferences)
    elif category == "style":
        data["editorial_style"]["context_level"] = _choice(
            "Desired depth: summary, context, or analysis",
            {"summary", "context", "analysis"},
            profile.editorial_style.context_level,
        )
        data["editorial_style"]["assume_subject_familiarity"] = _confirm(
            "Assume familiarity with high-priority subjects?",
            profile.editorial_style.assume_subject_familiarity,
        )
        data["editorial_style"]["ongoing_story_policy"] = _choice(
            "Ongoing-story policy: changes_only, brief_updates, or always_context",
            {"changes_only", "brief_updates", "always_context"},
            profile.editorial_style.ongoing_story_policy,
        )
    elif category == "format":
        data["show_format"] = _collect_format(profile)
    elif category == "voice":
        data["voice_preferences"] = {
            "primary_voice": _ask(
                "Primary voice, blank for default", profile.voice_preferences.primary_voice or ""
            )
            or None,
            "secondary_voice": _ask(
                "Secondary voice, blank for default",
                profile.voice_preferences.secondary_voice or "",
            )
            or None,
            "pace": _choice(
                "Voice pace: slow, normal, or fast",
                {"slow", "normal", "fast"},
                profile.voice_preferences.pace,
            ),
        }
    return EditorialProfile.model_validate(data)


def _collect_interests(base: EditorialProfile) -> list[dict]:
    interests = []
    seen = set()
    while True:
        name = _ask("Interest name, or done")
        if name.lower() == "done":
            break
        if not name:
            console.print("Interest name cannot be empty.")
            continue
        key = name.casefold()
        if key in seen:
            console.print("That interest is already listed.")
            continue
        seen.add(key)
        priority = _int(f"Priority for {name} (1-5)", 1, 5, 3)
        depth = _choice(
            f"Depth for {name}: major_only, normal, or deep",
            {"major_only", "normal", "deep"},
            "deep" if priority >= 4 else "normal",
        )
        subtopics = []
        if priority >= 4:
            console.print("Add subtopics for high-priority interests.")
            while True:
                subtopic = _ask("Subtopic, or done")
                if subtopic.lower() == "done":
                    break
                if subtopic:
                    subtopics.append(subtopic)
        interests.append(
            {
                "name": name,
                "priority": priority,
                "depth": depth,
                "subtopics": subtopics,
                "inclusion_notes": _ask("Anything to include for this interest?", ""),
                "exclusion_notes": _ask("Anything to avoid for this interest?", ""),
            }
        )
    if not interests:
        interests = [item.model_dump() for item in base.interests]
    return interests


def _collect_exclusions(defaults: list[str]) -> list[str]:
    negative_preferences = []
    console.print("Re-enter excluded subjects one at a time.")
    while True:
        item = _ask("Subject to exclude, or done")
        if item.lower() == "done":
            break
        if item:
            negative_preferences.append(item)
    return negative_preferences or defaults


def _collect_format(base: EditorialProfile) -> dict:
    variable = _confirm("Allow variable show length?", base.show_format.allow_variable_length)
    if variable:
        minimum = _int("Minimum minutes", 5, 90, base.show_format.minimum_minutes)
        maximum = _int("Maximum minutes", minimum, 90, base.show_format.maximum_minutes)
        target = _int("Normal target minutes", minimum, maximum, base.show_format.target_minutes)
    else:
        target = _int("Fixed show minutes", 5, 90, base.show_format.target_minutes)
        minimum = target
        maximum = target
    return {
        "target_minutes": target,
        "minimum_minutes": minimum,
        "maximum_minutes": maximum,
        "allow_variable_length": variable,
        "headline_open": _confirm(
            "Include top-of-show headline rundown?", base.show_format.headline_open
        ),
        "watch_list_close": _confirm(
            "Include closing what-to-watch section?", base.show_format.watch_list_close
        ),
        "host_count": _int("One host or two hosts?", 1, 2, base.show_format.host_count),
    }
