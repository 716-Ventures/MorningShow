from __future__ import annotations

import json
import re
from pathlib import Path

from morning_radio.llm.client import LLMClient
from morning_radio.llm.prompts import SCRIPT_SYSTEM
from morning_radio.models import EditorialProfile, Rundown, StoryDossier

ALLOWED_DIRECTIVES = {"MUSIC", "BUMPER", "BED", "PAUSE", "HOST", "HOST 2"}


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
            (run_dir / "script-draft.md").write_text(script, encoding="utf-8")
            return script
        except Exception:
            if llm.model != "fake-local-fixture":
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
        if line.startswith("[") and line.endswith("]"):
            directive = line[1:-1].split(":", 1)[0].strip()
            if directive not in ALLOWED_DIRECTIVES:
                raise ScriptError(f"Unsupported production directive: {line}")
            current_host = directive in {"HOST", "HOST 2"}
            continue
        if line.startswith("- ") or re.match(r"^\d+\.", line):
            raise ScriptError("Spoken copy may not contain markdown lists.")
        if not current_host:
            raise ScriptError(f"Spoken paragraph is not under a host marker: {line[:40]}")


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
