from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from morning_radio.cli import app
from morning_radio.pipeline import MorningPipelineError

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_show_help_runs_from_other_working_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "show"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Generate a local personal morning radio episode" in result.stdout


def test_wheel_includes_explicit_subpackages(tmp_path: Path) -> None:
    subprocess.run(
        ["/Users/chrisjdavis/Library/Python/3.9/bin/uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheel = next(tmp_path.glob("*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "morning_radio/audio/__init__.py" in names
    assert "morning_radio/newsroom/__init__.py" in names
    assert "morning_radio/showgen/__init__.py" in names
