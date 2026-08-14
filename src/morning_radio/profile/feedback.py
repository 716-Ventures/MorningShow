from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from morning_radio import db
from morning_radio.profile.compiler import memory_path
from morning_radio.settings import repo_root

console = Console()


def record_feedback(run_id: str | None = None, root: Path | None = None) -> None:
    base = root or repo_root()
    db_path = base / "data" / "app.db"
    resolved_run = run_id or db.latest_completed_run(db_path)
    if resolved_run is None:
        raise typer.BadParameter("No completed run found. Run ./show morning first or pass a run id.")

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
    addition = (
        f"\n- Feedback for {resolved_run}: overall={answers['overall']}; "
        f"do more: {answers['worked']}; change: {answers['change']}; "
        f"avoid: {answers['should_not_include']}; missing: {answers['missing_story']}; "
        f"length: {answers['length_feedback']}; production: {answers['production_feedback']}\n"
    )
    backup = mem_path.with_suffix(".md.bak")
    if mem_path.exists():
        backup.write_text(previous, encoding="utf-8")
    mem_path.write_text(previous.rstrip() + "\n" + addition, encoding="utf-8")
    console.print(f"[green]Recorded feedback for {resolved_run}[/green]")
