from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from morning_radio.llm.client import FakeLLM, LLMError
from morning_radio.models import StageStatus
from morning_radio.pipeline import MorningPipelineError, run_morning
from morning_radio.profile.compiler import default_profile, save_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "morning-run"


def isolated_root(tmp_path: Path) -> Path:
    root = tmp_path / "morning-radio-root"
    shutil.copytree(REPO_ROOT / "config", root / "config")
    (root / "assets").mkdir()
    save_profile(default_profile(), root)
    return root


@pytest.mark.parametrize("generate_audio", [False, True])
def test_fixture_morning_run_completes_in_isolated_root(
    monkeypatch, tmp_path: Path, generate_audio: bool
) -> None:
    root = isolated_root(tmp_path)
    config_path = root / "config" / "production.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["generate_audio"] = generate_audio
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_DIR", str(FIXTURE_DIR))
    monkeypatch.setenv("MORNING_RADIO_FAKE_LLM", "1")
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    result = run_morning(date(2026, 8, 14), minutes=10, no_assets=True, root=root)

    assert (result.episode is not None) == generate_audio
    assert result.script.endswith("script-final.md")
    assert (root / result.script).exists()
    assert (root / "data" / "app.db").exists()
    stories = result.stories
    assert isinstance(stories, int)
    assert stories > 0

    run_dir = root / "runs" / "2026-08-14" / result.run_id
    assert (run_dir / "episode.mp3").exists() == generate_audio
    assert (run_dir / "production-plan.json").exists() == generate_audio
    metrics = json.loads((run_dir / "performance.json").read_text())
    assert metrics["run_id"] == result.run_id
    assert all(item["elapsed_ms"] >= 0 for item in metrics["stages"])
    extracted = sorted((run_dir / "extracted").glob("*.json"))
    statuses = {
        json.loads(path.read_text(encoding="utf-8"))["extraction_status"] for path in extracted
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
    metrics = json.loads((run_dir / "performance.json").read_text())
    assert metrics["stages"][-1]["status"] == "failed"
    assert metrics["stages"][-1]["stage"] == "verifying"


def test_verifier_timeout_reports_service_failure_not_unsupported_claims(monkeypatch, tmp_path):
    class TimeoutVerifier(FakeLLM):
        def generate_structured(self, *args, **kwargs):
            if kwargs.get("stage") == "verification":
                raise LLMError("timed out")
            return super().generate_structured(*args, **kwargs)

    root = isolated_root(tmp_path)
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    monkeypatch.setenv("MORNING_RADIO_FIXTURE_DIR", str(FIXTURE_DIR))
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    monkeypatch.setattr("morning_radio.pipeline.build_llm_client", lambda *_: TimeoutVerifier())
    with pytest.raises(MorningPipelineError, match="timed out") as caught:
        run_morning(date(2026, 8, 15), minutes=10, no_assets=True, root=root)
    assert "unsupported claims were not established" in caught.value.action
    assert "Ollama" in caught.value.action
    assert not list(root.glob("runs/**/episode.mp3"))
