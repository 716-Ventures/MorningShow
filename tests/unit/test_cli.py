from __future__ import annotations

from typer.testing import CliRunner

from morning_radio.cli import app
from morning_radio.pipeline import MorningPipelineError


def test_help_lists_required_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("configure", "morning", "feedback", "doctor"):
        assert command in result.stdout


def test_command_help_exits_zero() -> None:
    runner = CliRunner()
    for command in ("configure", "morning", "feedback", "doctor"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_morning_failure_prints_stage_and_action(monkeypatch) -> None:
    import morning_radio.pipeline

    def fail_run(*args, **kwargs):
        raise MorningPipelineError(
            "fixture failure",
            run_id="run-123",
            failed_stage="mixing",
            action="Inspect mix logs.",
        )

    monkeypatch.setattr(morning_radio.pipeline, "run_morning", fail_run)
    runner = CliRunner()

    result = runner.invoke(app, ["morning", "--date", "2026-08-15"])

    assert result.exit_code == 1
    assert "Morning run failed" in result.stdout
    assert "Failed stage:" in result.stdout
    assert "mixing" in result.stdout
    assert "run-123" in result.stdout
    assert "Inspect mix logs." in result.stdout
