from __future__ import annotations

import json
import re
from pathlib import Path

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
            (run_dir / "script-draft.md").write_text(script, encoding="utf-8")
            return script
        except (LLMError, ScriptError):
            if not allows_fixture_fallback(llm):
                raise
    dossier_by_id = {item.cluster_id: item for item in dossiers}
    lines = ["[MUSIC: OPENING]", "[HOST]"]
    lines.append(
        f"Good morning. This is your personal morning radio for {rundown.show_date.strftime('%B %-d, %Y')}."
    )
    if profile.show_format.headline_open:
        headline_titles = [item.working_headline for item in dossiers[:5]]
        lines.append("Here is the shape of the morning: " + "; ".join(headline_titles) + ".")
        lines.append("[PAUSE: 500]")
    for segment in rundown.segments:
        if segment.type != "story":
            continue
        dossier = dossier_by_id[segment.cluster_ids[0]]
        lines.extend(
            [
                "[HOST]",
                f"Now, {dossier.working_headline}. {dossier.what_happened}",
                f"What is new today: {dossier.what_is_new_today}",
                f"Why it matters: {dossier.why_it_matters}",
            ]
        )
        if dossier.uncertainties:
            lines.append("One caution: " + " ".join(dossier.uncertainties))
        lines.append("[PAUSE: 650]")
    if profile.show_format.watch_list_close:
        watch = "; ".join(item.working_headline for item in dossiers[:3])
        lines.extend(["[HOST]", f"What to watch next: {watch}."])
    lines.extend(["[HOST]", "That is the show. Have a good morning.", "[MUSIC: CLOSING]"])
    script = "\n\n".join(lines) + "\n"
    validate_script(script)
    (run_dir / "script-draft.md").write_text(script, encoding="utf-8")
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
