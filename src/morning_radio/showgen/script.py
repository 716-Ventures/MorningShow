from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from morning_radio.artifacts.io import atomic_write_json, atomic_write_text
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
from morning_radio.llm.prompts import SCRIPT_SYSTEM
from morning_radio.llm.schemas import ScriptResponse
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
    "relevance score",
    "score of",
    "selected story",
    "why it was selected",
    "why this story was added",
    "why this was added",
    "why this was selected",
)
CONTEXTUAL_EDITORIAL_LANGUAGE = {
    "pipeline": re.compile(
        r"\b(?:editorial|generation|newsroom|writing) pipeline\b|"
        r"\bpipeline (?:generated|included|ranked|selected)\b"
    ),
    "prompt": re.compile(
        r"\b(?:editorial|model|system|writing) prompt\b|"
        r"\b(?:the|this) prompt\b|"
        r"\bprompt (?:asked|instructed|required|requested|said)\b"
    ),
    "scoring": re.compile(
        r"\b(?:article|item|story|topic) (?:has |was )?scored\b|"
        r"\b(?:editorial|relevance|story) scoring\b|"
        r"\bscoring (?:criteria|model|process|system)\b"
    ),
    "validation": re.compile(
        r"\b(?:editorial|model|output|schema|script) validation\b|"
        r"\bvalidation (?:check|error|gate|process)\b"
    ),
}
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
MIN_WORDS_PER_STORY = 55


class ScriptError(RuntimeError):
    pass


def write_script(
    profile: EditorialProfile,
    rundown: Rundown,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> str:
    if llm is not None:
        try:
            response = llm.generate_structured(
                SCRIPT_SYSTEM,
                json.dumps(
                    {
                        "task": (
                            "Write the complete morning radio script. Give every story a substantive "
                            "brief with multiple grounded details and useful context."
                        ),
                        "profile": profile.model_dump(mode="json"),
                        "rundown": rundown.model_dump(mode="json"),
                        "story_word_targets": {
                            segment.cluster_ids[0]: round(
                                segment.planned_seconds * DEFAULT_WPM / 60
                            )
                            for segment in rundown.segments
                            if segment.type == "story" and segment.cluster_ids
                        },
                        "dossiers": [item.model_dump(mode="json") for item in dossiers],
                    },
                    ensure_ascii=False,
                ),
                ScriptResponse,
                stage="writing",
                prompt_type="script",
            )
            script = finalize_script(response.script, profile, dossiers)
            validate_script(script)
            validate_script_quality(script, rundown, dossiers)
            validate_production_directives(script, profile, dossiers)
            script = adjust_script_duration_if_needed(script, rundown, llm)
            script = finalize_script(script, profile, dossiers)
            validate_script(script)
            validate_script_quality(script, rundown, dossiers)
            validate_production_directives(script, profile, dossiers)
            atomic_write_text(run_dir / "script-draft.md", script)
            return script
        except (LLMError, ScriptError, ValidationError) as exc:
            atomic_write_json(
                run_dir / "logs" / "script-fallback.json",
                {
                    "model": llm.model,
                    "reason": str(exc),
                    "fixture_fallback": allows_fixture_fallback(llm),
                },
            )
    dossier_by_id = {item.cluster_id: item for item in dossiers}
    lines = ["[MUSIC: OPENING]"]
    if dossiers:
        lines.append("[BED: bed]")
    lines.append("[HOST]")
    lines.append(
        f"Good morning. This is your personal morning radio for {rundown.show_date.strftime('%B %-d, %Y')}."
    )
    if profile.show_format.headline_open:
        story_word = "story" if len(dossiers) == 1 else "stories"
        lines.append(f"I've got {len(dossiers)} {story_word} for you this morning.")
        lines.append("[PAUSE: 500]")
    story_index = 0
    for segment in rundown.segments:
        if segment.type != "story":
            continue
        story_index += 1
        dossier = dossier_by_id[segment.cluster_ids[0]]
        if story_index == 1:
            lines.extend(["[BED: STOP]", "[BUMPER: Headlines]"])
        else:
            lines.append("[BUMPER: bumper]")
        caution = uncertainty_sentence(dossier.uncertainties)
        maximum_words = story_word_limit(segment.planned_seconds)
        caution_words = spoken_word_count(caution or "")
        lines.extend(["[HOST]", story_script_paragraph(dossier, maximum_words - caution_words)])
        if caution:
            lines.append(caution)
        lines.append("[PAUSE: 650]")
    if profile.show_format.watch_list_close:
        lines.extend(["[BUMPER: What to Watch]", "[HOST]", watch_list_sentence(dossiers)])
    lines.extend(["[HOST]", "That's the show for now. Have a good morning.", "[MUSIC: CLOSING]"])
    script = "\n\n".join(lines) + "\n"
    validate_script(script)
    validate_script_quality(script, rundown, dossiers)
    validate_production_directives(script, profile, dossiers)
    atomic_write_text(run_dir / "script-draft.md", script)
    return script


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
    if "[" in line or "]" in line:
        raise ScriptError("Spoken copy may not contain unresolved bracketed placeholders.")
    lowered = line.casefold()
    for phrase in INTERNAL_EDITORIAL_LANGUAGE:
        pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
        if re.search(pattern, lowered):
            raise ScriptError(f"Spoken copy may not mention internal editorial machinery: {phrase}")
    for name, pattern in CONTEXTUAL_EDITORIAL_LANGUAGE.items():
        if pattern.search(lowered):
            raise ScriptError(f"Spoken copy may not mention internal editorial machinery: {name}")


def normalize_script_format(script: str) -> str:
    script = re.sub(
        r"\bThis is\s+\[HOST NAME]\s+with\b",
        "Here is",
        script,
        flags=re.IGNORECASE,
    )
    script = re.sub(r"\bOpen,\s+AI\b", "OpenAI", script, flags=re.IGNORECASE)
    script = re.sub(r"\bHugging,\s+Face\b", "Hugging Face", script, flags=re.IGNORECASE)
    normalized_lines: list[str] = []
    unwrapped = script.replace("```text", "").replace("```markdown", "").replace("```", "")
    for raw_line in unwrapped.splitlines():
        line = raw_line.strip()
        inline_host = re.match(r"^(\[HOST(?: 2)?])\s+(.+)$", line)
        if inline_host:
            normalized_lines.extend([inline_host.group(1), "", inline_host.group(2)])
        else:
            normalized_lines.append(raw_line)
    return "\n".join(normalized_lines).strip() + "\n"


def finalize_script(
    script: str,
    profile: EditorialProfile,
    dossiers: list[StoryDossier],
) -> str:
    normalized = normalize_script_format(script)
    without_headlines = remove_story_headline_leads(normalized, dossiers)
    return add_standard_production_directives(without_headlines, profile, dossiers)


def remove_story_headline_leads(script: str, dossiers: list[StoryDossier]) -> str:
    titles = sorted(
        (clean_spoken_copy(dossier.working_headline) for dossier in dossiers),
        key=len,
        reverse=True,
    )
    cleaned_lines: list[str] = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("["):
            cleaned_lines.append(raw_line)
            continue
        for transition in STORY_TRANSITIONS:
            prefix = f"{transition}:"
            if line.casefold().startswith(prefix.casefold()):
                line = line[len(prefix) :].lstrip()
                break
        for title in titles:
            if not has_headline_lead(line, title):
                continue
            remainder = line[len(title) :]
            line = remainder.lstrip(" .:!?-")
            break
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip() + "\n"


def add_standard_production_directives(
    script: str,
    profile: EditorialProfile,
    dossiers: list[StoryDossier],
) -> str:
    tokens = [
        line.strip()
        for line in script.splitlines()
        if line.strip()
        and not line.strip().startswith("[BUMPER:")
        and not line.strip().startswith("[BED:")
    ]
    if not dossiers:
        return "\n\n".join(tokens) + "\n"

    matching_script = "\n\n".join(tokens) + "\n"
    story_sections = match_story_sections(matching_script, dossiers)
    section_indexes = {
        cluster_id: tokens.index(section)
        for cluster_id, section in story_sections.items()
        if section in tokens
    }
    ordered_story_indexes = sorted(section_indexes.values())
    if not ordered_story_indexes:
        return matching_script

    insert_before: dict[int, list[str]] = {}
    insert_after: dict[int, list[str]] = {}
    opening_index = next(
        (index for index, item in enumerate(tokens) if item == "[MUSIC: OPENING]"),
        None,
    )
    if opening_index is not None:
        insert_after[opening_index] = ["[BED: bed]"]

    for story_number, story_index in enumerate(ordered_story_indexes):
        marker_index = preceding_host_index(tokens, story_index)
        directives = (
            ["[BED: STOP]", "[BUMPER: Headlines]"] if story_number == 0 else ["[BUMPER: bumper]"]
        )
        insert_before.setdefault(marker_index, []).extend(directives)

    if profile.show_format.watch_list_close:
        closing_index = next(
            (index for index, item in enumerate(tokens) if item == "[MUSIC: CLOSING]"),
            len(tokens),
        )
        candidates = [
            index
            for index in range(ordered_story_indexes[-1] + 1, closing_index)
            if not tokens[index].startswith("[")
            and not tokens[index].casefold().startswith(("that's the show", "that is the show"))
        ]
        if candidates:
            marker_index = preceding_host_index(tokens, candidates[0])
            insert_before.setdefault(marker_index, []).append("[BUMPER: What to Watch]")

    output: list[str] = []
    for index, item in enumerate(tokens):
        output.extend(insert_before.get(index, []))
        output.append(item)
        output.extend(insert_after.get(index, []))
    return "\n\n".join(output) + "\n"


def preceding_host_index(tokens: list[str], spoken_index: int) -> int:
    if spoken_index > 0 and tokens[spoken_index - 1] in {"[HOST]", "[HOST 2]"}:
        return spoken_index - 1
    return spoken_index


def validate_story_openings(script: str, dossiers: list[StoryDossier]) -> None:
    titles = [clean_spoken_copy(dossier.working_headline).casefold() for dossier in dossiers]
    for _host, text in spoken_blocks(script):
        lowered = text.casefold()
        if any(lowered.startswith(f"{transition.casefold()}:") for transition in STORY_TRANSITIONS):
            raise ScriptError("Story copy may not begin with a headline-style transition.")
        if any(has_headline_lead(lowered, title) for title in titles):
            raise ScriptError("Story copy may not begin by reading its headline.")


def validate_production_directives(
    script: str,
    profile: EditorialProfile,
    dossiers: list[StoryDossier],
) -> None:
    if not dossiers:
        return
    required_counts = {
        "[BED: bed]": 1,
        "[BED: STOP]": 1,
        "[BUMPER: Headlines]": 1,
        "[BUMPER: bumper]": max(0, len(dossiers) - 1),
        "[BUMPER: What to Watch]": int(profile.show_format.watch_list_close),
    }
    for directive, expected in required_counts.items():
        actual = sum(line.strip() == directive for line in script.splitlines())
        if actual != expected:
            raise ScriptError(
                f"Production directive {directive} appeared {actual} times; expected {expected}."
            )


def validate_script_quality(
    script: str,
    rundown: Rundown,
    dossiers: list[StoryDossier],
) -> None:
    if not dossiers:
        return
    validate_story_openings(script, dossiers)
    spoken_words = sum(len(re.findall(r"\b[\w']+\b", text)) for _, text in spoken_blocks(script))
    minimum_words = len(dossiers) * MIN_WORDS_PER_STORY
    if spoken_words < minimum_words:
        raise ScriptError(
            f"Script is too shallow for {len(dossiers)} stories: "
            f"{spoken_words} spoken words; requires at least {minimum_words}."
        )
    story_sections = match_story_sections(script, dossiers)
    if len(story_sections) < len(dossiers):
        raise ScriptError(
            f"Script covers only {len(story_sections)} of {len(dossiers)} dossier topics."
        )
    segment_seconds = {
        segment.cluster_ids[0]: segment.planned_seconds
        for segment in rundown.segments
        if segment.type == "story" and segment.cluster_ids
    }
    for dossier in dossiers:
        section = story_sections[dossier.cluster_id]
        section_words = len(re.findall(r"\b[\w']+\b", section))
        if section_words < 40:
            raise ScriptError(
                f"Story {dossier.cluster_id} is too shallow: {section_words} spoken words."
            )
        planned_seconds = segment_seconds.get(dossier.cluster_id, dossier.recommended_seconds)
        maximum_words = story_word_limit(planned_seconds)
        if section_words > maximum_words:
            raise ScriptError(
                f"Story {dossier.cluster_id} is too long: {section_words} spoken words; "
                f"maximum {maximum_words}."
            )


def spoken_word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def story_word_limit(planned_seconds: float) -> int:
    return max(100, round(planned_seconds * DEFAULT_WPM / 60 * 1.15))


def story_script_paragraph(dossier: StoryDossier, maximum_words: int | None = None) -> str:
    title = clean_spoken_copy(dossier.working_headline)
    what_happened = clean_spoken_copy(
        remove_redundant_lead(dossier.what_happened, dossier.working_headline)
    )
    what_is_new = meaningful_dossier_copy(dossier.what_is_new_today)
    why_it_matters = meaningful_dossier_copy(dossier.why_it_matters)
    details = _distinct_sentences([what_happened, what_is_new, why_it_matters], title)
    if sum(len(sentence.split()) for sentence in details) < 80:
        details = _distinct_sentences(
            [*details, *(clean_spoken_copy(fact.claim) for fact in dossier.facts)],
            title,
        )
    if maximum_words is None:
        return " ".join(details)
    # Reserve uncertainty copy in the caller; never clip a sentence or skip the lead.
    fitted: list[str] = []
    remaining = maximum_words
    for sentence in details:
        words = spoken_word_count(sentence)
        if words > remaining:
            break
        fitted.append(sentence)
        remaining -= words
    if not fitted:
        raise ScriptError("The grounded story lead and its cautions exceed the time budget.")
    return " ".join(fitted)


def _distinct_sentences(candidates: list[str], title: str) -> list[str]:
    selected: list[str] = []
    normalized_selected: list[str] = []
    for candidate in candidates:
        for sentence in re.split(r"(?<=[.!?])\s+", candidate):
            cleaned = remove_redundant_lead(clean_spoken_copy(sentence), title)
            if not cleaned:
                continue
            normalized = re.sub(r"[^a-z0-9]+", " ", cleaned.casefold()).strip()
            if any(_sentences_overlap(normalized, prior) for prior in normalized_selected):
                continue
            selected.append(cleaned)
            normalized_selected.append(normalized)
    return selected


def _sentences_overlap(left: str, right: str) -> bool:
    if left == right or left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.65


def _title_mentioned(title: str, script: str) -> bool:
    significant = {
        word.casefold() for word in re.findall(r"[A-Za-z0-9']+", title) if len(word) >= 4
    }
    if not significant:
        return True
    script_words = {word.casefold() for word in re.findall(r"[A-Za-z0-9']+", script)}
    required = min(2, len(significant))
    return len(significant & script_words) >= required


def match_story_sections(
    script: str,
    dossiers: list[StoryDossier],
) -> dict[str, str]:
    """Match spoken bodies to dossiers without requiring a headline readout."""
    sections = [text for _, text in spoken_blocks(script)]
    available = set(range(len(sections)))
    matches: dict[str, str] = {}
    for dossier in dossiers:
        reference_terms = _dossier_reference_terms(dossier)
        required_overlap = min(2, len(reference_terms))
        ranked = sorted(
            available,
            key=lambda index: _reference_overlap(reference_terms, sections[index]),
            reverse=True,
        )
        if (
            not ranked
            or _reference_overlap(reference_terms, sections[ranked[0]]) < required_overlap
        ):
            continue
        best_index = ranked[0]
        matches[dossier.cluster_id] = sections[best_index]
        available.remove(best_index)
    return matches


def _dossier_reference_terms(dossier: StoryDossier) -> set[str]:
    reference = f"{dossier.working_headline} {dossier.what_happened}"
    return {word.casefold() for word in re.findall(r"[A-Za-z0-9']+", reference) if len(word) >= 4}


def _reference_overlap(reference_terms: set[str], text: str) -> int:
    text_words = {word.casefold() for word in re.findall(r"[A-Za-z0-9']+", text)}
    return len(reference_terms & text_words)


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


def has_headline_lead(text: str, title: str) -> bool:
    """Require a headline boundary, not merely a shared sentence subject."""
    if not title or not text.casefold().startswith(title.casefold()):
        return False
    remainder = text[len(title) :].lstrip()
    return not remainder or title[-1] in ".:!?" or remainder[0] in ".:!?-"


def remove_redundant_lead(text: str, title: str) -> str:
    if not text:
        return ""
    if has_headline_lead(text, title):
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
    return "I'll keep an eye on how these stories develop through the day."


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
    response = llm.generate_structured(
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
        ScriptResponse,
        stage="writing",
        prompt_type="script_duration_adjustment",
    )
    adjusted = normalize_script_format(response.script)
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
