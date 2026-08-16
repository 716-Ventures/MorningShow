from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime
from typing import Annotated

import typer
from rich.console import Console

from morning_radio import db
from morning_radio.dependencies import check_ffmpeg, check_llm, check_tts
from morning_radio.profile.compiler import ProfileError, load_profile
from morning_radio.profile.feedback import record_feedback
from morning_radio.profile.interview import run_interview
from morning_radio.settings import (
    ConfigError,
    load_app_settings,
    load_feed_settings,
    load_production_settings,
    repo_root,
)

app = typer.Typer(no_args_is_help=True, help="Generate a local personal morning radio episode.")
console = Console()


@app.command()
def configure() -> None:
    """Run the interactive editorial-profile setup interview."""
    existing = None
    try:
        existing = load_profile()
    except ProfileError:
        pass
    run_interview(existing)


@app.command()
def doctor() -> None:
    """Check local runtime dependencies and configuration."""
    base = repo_root()
    checks: list[tuple[str, bool, str]] = []

    checks.append(
        (
            "Python 3.12+",
            sys.version_info >= (3, 12),
            f"found {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    data_dir = base / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("Writable data directory", True, str(data_dir)))
    except OSError as exc:
        checks.append(("Writable data directory", False, str(exc)))

    try:
        app_settings = load_app_settings(base)
        load_production_settings(base)
        feeds = load_feed_settings(base)
        checks.append(("Config files", True, "valid"))
    except ConfigError as exc:
        app_settings = None
        feeds = None
        checks.append(("Config files", False, str(exc)))

    try:
        db.initialize(base / "data" / "app.db")
        checks.append(("SQLite initialization", True, "data/app.db ready"))
    except sqlite3.Error as exc:
        checks.append(("SQLite initialization", False, str(exc)))

    if app_settings is not None:
        checks.extend((check.name, check.ok, check.detail) for check in check_llm(app_settings))

    checks.extend((check.name, check.ok, check.detail) for check in check_ffmpeg())

    prod = None
    try:
        prod = load_production_settings(base)
        checks.extend((check.name, check.ok, check.detail) for check in check_tts(prod))
    except ConfigError as exc:
        checks.append(("TTS backend", False, str(exc)))

    if feeds is not None:
        enabled = [feed for feed in feeds.feeds if feed.enabled]
        checks.append(("Enabled feeds", bool(enabled), f"{len(enabled)} enabled"))

    failed = False
    for name, ok, detail in checks:
        failed = failed or not ok
        color = "green" if ok else "red"
        marker = "PASS" if ok else "FAIL"
        console.print(f"[{color}]{marker}[/{color}] {name}: {detail}")
    raise typer.Exit(1 if failed else 0)


@app.command()
def morning(
    minutes: Annotated[int | None, typer.Option(help="Override profile target duration.")] = None,
    date_: Annotated[str | None, typer.Option("--date", help="Episode date as YYYY-MM-DD.")] = None,
    no_assets: Annotated[bool, typer.Option(help="Disable music, bumpers, and beds.")] = False,
) -> None:
    """Create a complete morning episode and print the MP3 path."""
    from morning_radio.pipeline import MorningPipelineError, run_morning

    try:
        requested_date = date.fromisoformat(date_) if date_ else datetime.now().astimezone().date()
    except ValueError as exc:
        raise typer.BadParameter("--date must be YYYY-MM-DD") from exc
    try:
        result = run_morning(requested_date=requested_date, minutes=minutes, no_assets=no_assets)
    except Exception as exc:
        console.print(f"[red]Morning run failed:[/red] {exc}")
        if isinstance(exc, MorningPipelineError):
            console.print(f"[red]Failed stage:[/red] {exc.failed_stage}")
            console.print(f"[red]Run ID:[/red] {exc.run_id}")
            console.print(f"[yellow]Action:[/yellow] {exc.action}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Episode:[/green] {result['episode']}")
    console.print(f"[green]Sources:[/green] {result['sources']}")
    console.print(json.dumps({k: v for k, v in result.items() if k not in {'episode', 'sources'}}, indent=2))


@app.command()
def feedback(run_id: Annotated[str | None, typer.Argument(help="Run id to attach feedback to.")] = None) -> None:
    """Record post-listening feedback and update editorial memory."""
    record_feedback(run_id)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
