from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from morning_radio import db
from morning_radio.artifacts.io import atomic_write_text
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback, build_llm_client
from morning_radio.llm.prompts import SCORING_SYSTEM
from morning_radio.llm.schemas import FeedbackMemoryResponse
from morning_radio.profile.compiler import memory_path
from morning_radio.settings import ConfigError, load_app_settings, repo_root

console = Console()


def record_feedback(
    run_id: str | None = None, root: Path | None = None, llm: LLMClient | None = None
) -> None:
    base = root or repo_root()
    db_path = base / "data" / "app.db"
    resolved_run = run_id or db.latest_completed_run(db_path)
    if resolved_run is None:
        raise typer.BadParameter("No completed run found. Run ./show morning first or pass a run id.")
    if not db.completed_run_exists(db_path, resolved_run):
        raise typer.BadParameter(f"Run id is not a completed run: {resolved_run}")

    answers = {
        "run_id": resolved_run,
        "overall": typer.prompt("Overall: better, same, or worse?"),
        "worked": typer.prompt("What worked?"),
        "change": typer.prompt("What should change?"),
        "should_not_include": typer.prompt("Any story that should not have been included?"),
        "missing_story": typer.prompt("Any missing story or topic?"),
        "length_feedback": typer.prompt("Too short, right length, or too long?"),
        "production_feedback": typer.prompt("Voice or production feedback?"),
    }
    db.insert_feedback(db_path, answers)
    mem_path = memory_path(base)
    previous = mem_path.read_text(encoding="utf-8") if mem_path.exists() else "# Editorial Memory\n"
    updated = update_editorial_memory(previous, answers, base, llm)
    if mem_path.exists():
        atomic_write_text(mem_path.with_suffix(".md.bak"), previous)
    atomic_write_text(mem_path, updated)
    console.print(f"[green]Recorded feedback for {resolved_run}[/green]")


def update_editorial_memory(
    previous: str,
    answers: dict[str, str | None],
    root: Path,
    llm: LLMClient | None = None,
) -> str:
    client = llm
    if client is None:
        try:
            client = build_llm_client(load_app_settings(root).llm, root / "data")
        except (ConfigError, RuntimeError):
            client = None
    if client is not None:
        try:
            response = client.generate_structured(
                SCORING_SYSTEM,
                (
                    "Rewrite editorial memory as concise durable Markdown rules. "
                    "Do not change identity, location, or show duration settings.\n\n"
                    f"Previous memory:\n{previous}\n\nRaw feedback:\n{answers}"
                ),
                FeedbackMemoryResponse,
                stage="feedback",
                prompt_type="editorial_memory_update",
            )
            return normalize_memory(response.editorial_memory_markdown)
        except (LLMError, ValidationError):
            if not allows_fixture_fallback(client):
                raise
    return normalize_memory(previous.rstrip() + "\n" + fallback_memory_addition(answers))


def fallback_memory_addition(answers: dict[str, str | None]) -> str:
    return (
        f"\n- Feedback for {answers['run_id']}: overall={answers['overall']}; "
        f"do more: {answers['worked']}; change: {answers['change']}; "
        f"avoid: {answers['should_not_include']}; missing: {answers['missing_story']}; "
        f"length: {answers['length_feedback']}; production: {answers['production_feedback']}\n"
    )


def normalize_memory(markdown: str) -> str:
    text = markdown.strip()
    if not text.startswith("# Editorial Memory"):
        text = "# Editorial Memory\n\n" + text
    return text + "\n"
