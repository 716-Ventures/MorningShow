from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from morning_radio.models import StageStatus
from morning_radio.pipeline import run_morning

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "morning-run"


def isolated_root(tmp_path: Path) -> Path:
    root = tmp_path / "morning-radio-root"
    for dirname in ("config", "assets"):
        shutil.copytree(REPO_ROOT / dirname, root / dirname)
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    for filename in ("profile.json", "profile.md", "editorial-memory.md"):
        shutil.copy2(REPO_ROOT / "data" / filename, data_dir / filename)
    return root


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg required")
def test_fixture_morning_run_completes_in_isolated_root(monkeypatch, tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_DIR", str(FIXTURE_DIR))
    monkeypatch.setenv("MORNING_RADIO_FAKE_LLM", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    result = run_morning(date(2026, 8, 14), minutes=10, no_assets=True, root=root)

    assert str(result["episode"]).endswith("episode.mp3")
    assert (root / str(result["episode"])).exists()
    assert (root / "data" / "app.db").exists()
    stories = result["stories"]
    assert isinstance(stories, int)
    assert stories > 0

    run_dir = root / "runs" / "2026-08-14" / str(result["run_id"])
    extracted = sorted((run_dir / "extracted").glob("*.json"))
    statuses = {
        json.loads(path.read_text(encoding="utf-8"))["extraction_status"]
        for path in extracted
    }
    assert {"usable", "too_short"}.issubset(statuses)


def test_fixture_morning_run_records_verification_failure(monkeypatch, tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_DIR", str(FIXTURE_DIR))
    monkeypatch.setenv("MORNING_RADIO_FAKE_LLM", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_VERIFICATION_FAIL", "1")

    with pytest.raises(RuntimeError, match="Verification failed"):
        run_morning(date(2026, 8, 15), minutes=10, no_assets=True, root=root)

    run_jsons = list((root / "runs" / "2026-08-15").glob("*/run.json"))
    assert len(run_jsons) == 1
    run_record = json.loads(run_jsons[0].read_text(encoding="utf-8"))
    assert run_record["status"] == StageStatus.FAILED.value
    assert run_record["failed_stage"] == StageStatus.VERIFYING.value
    run_dir = run_jsons[0].parent
    assert (run_dir / "verification.json").exists()
    assert not (run_dir / "raw-audio" / "001-host.wav").exists()
