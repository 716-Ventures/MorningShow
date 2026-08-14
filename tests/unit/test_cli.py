from __future__ import annotations

from typer.testing import CliRunner

from morning_radio.cli import app


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
