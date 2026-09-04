from __future__ import annotations

import json
import re
from pathlib import Path

from morning_radio.artifacts.io import atomic_write_json, atomic_write_text
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
from morning_radio.llm.prompts import SCRIPT_SYSTEM
from morning_radio.models import EditorialProfile, Rundown, StoryDossier

DIRECTIVE_PATTERNS = {
    "host": re.compile(r"^\[(HOST|HOST 2)]$"),
    "pause": re.compile(r"^\[PAUSE:\s*(\d{2,5})]$"),
    "music": re.compile(r"^\[MUSIC:\s*(OPENING|CLOSING)]$"),
    "bumper": re.compile(r"^\[BUMPER:\s*([A-Za-z0-9 _-]{1,80})]$"),
    "bed": re.compile(r"^\[BED:\s*([A-Za-z0-9 _-]{1,80}|STOP)]$"),
}
DEFAULT_WPM = 155
SCRIPT_DURATION_TOLERANCE = 0.2
MIN_PAUSE_MS = 100
MAX_PAUSE_MS = 5000
HEADLINE_PREVIEW_LIMIT = 4
INTERNAL_EDITORIAL_LANGUAGE = (
    "added because",
    "configured editorial profile",
    "current discovery window",
    "discovery window",
    "editorial profile",
    "final score",
    "included because",
    "matched interests",
    "model output",
    "pipeline",
    "prompt",
    "relevance score",
    "score of",
    "scored",
    "scoring",
    "selected story",
    "validation",
    "why it was selected",
    "why this story was added",
    "why this was added",
    "why this was selected",
)
GENERIC_DOSSIER_COPY = (
    "this is part of today's latest source set.",
    "it gives useful context for the morning ahead.",
)
SOURCE_BOILERPLATE_PATTERNS = (
    re.compile(r"\s+-\s+Published\s+", re.IGNORECASE),
    re.compile(r"\bPublished\s+", re.IGNORECASE),
    re.compile(r"\bYou're reading a newsletter from [^.]+\.?", re.IGNORECASE),
)
STORY_TRANSITIONS = (
    "First up",
    "Also this morning",
    "In the wider picture",
    "Closer to the day ahead",
    "Another story to know",
)


class ScriptError(RuntimeError):
    pass


def write_script(
    profile: EditorialProfile,
    rundown: Rundown,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> str:
    if llm is not None and not has_upstream_model_fallback(run_dir, llm):
        try:
            script = llm.generate_text(
                SCRIPT_SYSTEM,
                json.dumps(
                    {
                        "profile": profile.model_dump(mode="json"),
                        "rundown": rundown.model_dump(mode="json"),
                        "dossiers": [item.model_dump(mode="json") for item in dossiers],
                    },
                    ensure_ascii=False,
                ),
                stage="writing",
                prompt_type="script",
            )
            validate_script(script)
            script = adjust_script_duration_if_needed(script, rundown, llm)
            atomic_write_text(run_dir / "script-draft.md", script)
            return script
        except (LLMError, ScriptError) as exc:
            atomic_write_json(
                run_dir / "logs" / "script-fallback.json",
                {
                    "model": llm.model,
                    "reason": str(exc),
                    "fixture_fallback": allows_fixture_fallback(llm),
                },
            )
    dossier_by_id = {item.cluster_id: item for item in dossiers}
    lines = ["[MUSIC: OPENING]", "[HOST]"]
    lines.append(
        f"Good morning. This is your personal morning radio for {rundown.show_date.strftime('%B %-d, %Y')}."
    )
    if profile.show_format.headline_open:
        headline_titles = [clean_spoken_copy(item.working_headline) for item in dossiers[:HEADLINE_PREVIEW_LIMIT]]
        if len(dossiers) > HEADLINE_PREVIEW_LIMIT:
            headline_titles.append("a few other stories worth watching")
        lines.append("Ahead this morning: " + join_for_radio(headline_titles) + ".")
        lines.append("[PAUSE: 500]")
    story_index = 0
    for segment in rundown.segments:
        if segment.type != "story":
            continue
        story_index += 1
        dossier = dossier_by_id[segment.cluster_ids[0]]
        lines.extend(["[HOST]", story_script_paragraph(dossier, story_index)])
        caution = uncertainty_sentence(dossier.uncertainties)
        if caution:
            lines.append(caution)
        lines.append("[PAUSE: 650]")
    if profile.show_format.watch_list_close:
        lines.extend(["[HOST]", watch_list_sentence(dossiers)])
    lines.extend(["[HOST]", "That's the show for now. Have a good morning.", "[MUSIC: CLOSING]"])
    script = "\n\n".join(lines) + "\n"
    validate_script(script)
    atomic_write_text(run_dir / "script-draft.md", script)
    return script


def has_upstream_model_fallback(run_dir: Path, llm: LLMClient) -> bool:
    if allows_fixture_fallback(llm):
        return False
    logs_dir = run_dir / "logs"
    if any(
        (logs_dir / filename).exists()
        for filename in (
            "scoring-fallback.json",
            "rundown-fallback.json",
            "verification-fallback.json",
        )
    ):
        return True
    dossier_dir = run_dir / "dossiers"
    return dossier_dir.exists() and any(dossier_dir.glob("*-fallback.json"))


def validate_script(script: str) -> None:
    current_host = False
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        directive = parse_directive(line)
        if directive is not None:
            current_host = directive in {"HOST", "HOST 2"}
            continue
        if line.startswith("- ") or re.match(r"^\d+\.", line):
            raise ScriptError("Spoken copy may not contain markdown lists.")
        if not current_host:
            raise ScriptError(f"Spoken paragraph is not under a host marker: {line[:40]}")
        validate_listener_facing_copy(line)


def validate_listener_facing_copy(line: str) -> None:
    lowered = line.casefold()
    for phrase in INTERNAL_EDITORIAL_LANGUAGE:
        if phrase in lowered:
            raise ScriptError(f"Spoken copy may not mention internal editorial machinery: {phrase}")


def story_script_paragraph(dossier: StoryDossier, story_index: int) -> str:
    title = clean_spoken_copy(dossier.working_headline)
    what_happened = remove_redundant_lead(clean_spoken_copy(dossier.what_happened), title)
    what_is_new = meaningful_dossier_copy(dossier.what_is_new_today)
    why_it_matters = meaningful_dossier_copy(dossier.why_it_matters)
    transition = STORY_TRANSITIONS[min(story_index - 1, len(STORY_TRANSITIONS) - 1)]
    sentences = [f"{transition}: {title}."]
    if what_happened:
        sentences.append(what_happened)
    if what_is_new:
        sentences.append(f"The latest: {what_is_new}")
    if why_it_matters:
        sentences.append(f"The bigger point: {why_it_matters}")
    return " ".join(sentences)


def meaningful_dossier_copy(text: str) -> str:
    cleaned = clean_spoken_copy(text)
    if cleaned.casefold() in GENERIC_DOSSIER_COPY:
        return ""
    return cleaned


def clean_spoken_copy(text: str) -> str:
    cleaned = " ".join(text.split())
    for pattern in SOURCE_BOILERPLATE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return " ".join(cleaned.split()).strip()


def remove_redundant_lead(text: str, title: str) -> str:
    if not text:
        return ""
    normalized_text = text.casefold()
    normalized_title = title.casefold()
    if normalized_text == normalized_title:
        return ""
    if normalized_text.startswith(normalized_title):
        trimmed = text[len(title) :].lstrip(" .:-")
        return trimmed
    return text


def uncertainty_sentence(uncertainties: list[str]) -> str | None:
    useful = [
        clean_spoken_copy(item)
        for item in uncertainties
        if "single-source story" not in item.casefold()
    ]
    if not useful:
        return None
    return "One note of caution: " + " ".join(useful)


def watch_list_sentence(dossiers: list[StoryDossier]) -> str:
    if len(dossiers) <= 1:
        return "I'll keep an eye on how this develops."
    watch = join_for_radio([clean_spoken_copy(item.working_headline) for item in dossiers[:3]])
    return f"I'll keep an eye on {watch} as the day develops."


def join_for_radio(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{'; '.join(cleaned[:-1])}; and {cleaned[-1]}"


def parse_directive(line: str) -> str | None:
    if not line.startswith("["):
        return None
    host = DIRECTIVE_PATTERNS["host"].match(line)
    if host:
        return host.group(1)
    pause = DIRECTIVE_PATTERNS["pause"].match(line)
    if pause:
        milliseconds = int(pause.group(1))
        if not MIN_PAUSE_MS <= milliseconds <= MAX_PAUSE_MS:
            raise ScriptError(f"Pause directive is outside {MIN_PAUSE_MS}-{MAX_PAUSE_MS}ms: {line}")
        return "PAUSE"
    for name in ("music", "bumper", "bed"):
        match = DIRECTIVE_PATTERNS[name].match(line)
        if match:
            if len(match.groups()) == 1 and not match.group(1).strip():
                raise ScriptError(f"Directive argument may not be empty: {line}")
            return name.upper()
    if line.endswith("]"):
        raise ScriptError(f"Unsupported production directive: {line}")
    raise ScriptError(f"Malformed production directive: {line}")


def estimate_spoken_seconds(script: str, wpm: int = DEFAULT_WPM) -> int:
    word_count = sum(len(re.findall(r"\b[\w']+\b", text)) for _, text in spoken_blocks(script))
    pause_seconds = 0.0
    for raw_line in script.splitlines():
        line = raw_line.strip()
        pause = DIRECTIVE_PATTERNS["pause"].match(line)
        if pause:
            pause_seconds += int(pause.group(1)) / 1000
    return round((word_count / wpm) * 60 + pause_seconds)


def adjust_script_duration_if_needed(script: str, rundown: Rundown, llm: LLMClient) -> str:
    estimate = estimate_spoken_seconds(script)
    lower = rundown.planned_seconds * (1 - SCRIPT_DURATION_TOLERANCE)
    upper = rundown.planned_seconds * (1 + SCRIPT_DURATION_TOLERANCE)
    if lower <= estimate <= upper:
        return script
    adjusted = llm.generate_text(
        SCRIPT_SYSTEM,
        json.dumps(
            {
                "task": "Adjust script length without adding facts.",
                "estimated_seconds": estimate,
                "target_seconds": rundown.planned_seconds,
                "script": script,
            },
            ensure_ascii=False,
        ),
        stage="writing",
        prompt_type="script_duration_adjustment",
    )
    validate_script(adjusted)
    return adjusted


def spoken_blocks(script: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current = "HOST"
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"[HOST]", "[HOST 2]"}:
            current = line.strip("[]")
            continue
        if line.startswith("["):
            continue
        blocks.append((current, line))
    return blocks
