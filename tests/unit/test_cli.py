from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from morning_radio.cli import app
from morning_radio.models import RunMorningResult
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


def test_morning_script_only_run_prints_script_without_episode(monkeypatch) -> None:
    import morning_radio.pipeline

    monkeypatch.setattr(
        morning_radio.pipeline,
        "run_morning",
        lambda *args, **kwargs: RunMorningResult(
            script="runs/2026-08-15/run-123/script-final.md",
            sources="runs/2026-08-15/run-123/sources.html",
            run_id="run-123",
            stories=3,
            target_minutes=10,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["morning", "--date", "2026-08-15"])

    assert result.exit_code == 0
    assert "Script:" in result.stdout
    assert "script-final.md" in result.stdout
    assert "Episode:" not in result.stdout


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
    uv = shutil.which("uv")
    if uv is None:
        user_installs = sorted((Path.home() / "Library/Python").glob("*/bin/uv"))
        uv = str(user_installs[-1]) if user_installs else None
    assert uv is not None, "Install uv and add it to PATH to run the packaging test."
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
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
    assert "morning_radio/evaluation-cases.json" in names


@pytest.mark.parametrize("valid", [True, False])
def test_doctor_isolated_config_and_dependencies(monkeypatch, tmp_path, valid):
    import morning_radio.cli as cli
    from morning_radio.dependencies import DependencyCheck

    if valid:
        shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)

    def check(*args):
        return [DependencyCheck("test dependency", True, "available")]

    monkeypatch.setattr(cli, "check_llm", check)
    monkeypatch.setattr(cli, "check_tts", check)
    monkeypatch.setattr(cli, "check_ffmpeg", check)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == (0 if valid else 1)
    assert "Config files" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["morning", "--minutes", "4"],
        ["morning", "--minutes", "91"],
        ["morning", "--date", "bad-date"],
    ],
)
def test_bad_options_are_rejected(args):
    assert CliRunner().invoke(app, args).exit_code == 2


def test_configure_passes_existing_profile(monkeypatch):
    import morning_radio.cli as cli
    from morning_radio.profile.compiler import default_profile

    profile = default_profile()
    received = []
    monkeypatch.setattr(cli, "load_profile", lambda: profile)
    monkeypatch.setattr(cli, "run_interview", received.append)
    assert CliRunner().invoke(app, ["configure"]).exit_code == 0
    assert received == [profile]


def test_voice_preview_needs_no_ollama_or_news(monkeypatch, tmp_path):
    import morning_radio.cli as cli

    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    result = CliRunner().invoke(app, ["voice-preview"])
    assert result.exit_code == 0
    assert "Voice sample:" in result.output
    assert len(list((tmp_path / "runs/voice-previews").glob("*/raw-audio/*.wav"))) == 1


def test_voice_preview_missing_key_is_actionable(monkeypatch, tmp_path):
    import morning_radio.cli as cli

    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.delenv("MORNING_RADIO_FAKE_TTS", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["voice-preview"])
    assert result.exit_code == 1
    assert "ELEVENLABS_API_KEY" in result.output
    assert not list(tmp_path.glob("runs/**/*.wav"))


@pytest.mark.parametrize("text", ["", "x" * 501])
def test_voice_preview_bounds_paid_text(text):
    assert CliRunner().invoke(app, ["voice-preview", "--text", text]).exit_code == 2
