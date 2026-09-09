from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from morning_radio import benchmark
from morning_radio.audio.tts import ToneTTS
from morning_radio.evaluation import EvaluationResult


def test_isolated_fixture_worker_records_real_stage_metrics(monkeypatch, tmp_path: Path):
    for key in (
        "MORNING_RADIO_FIXTURE_RUN",
        "MORNING_RADIO_FAKE_LLM",
        "MORNING_RADIO_FAKE_TTS",
        "MORNING_RADIO_FIXTURE_DIR",
        "MORNING_RADIO_FAKE_VERIFICATION_FAIL",
    ):
        monkeypatch.setenv(key, "test")
    path = tmp_path / "fixture.json"
    benchmark.fixture_worker(path)
    payload = json.loads(path.read_text())
    assert payload["elapsed_ms"] > 0
    assert payload["process_peak_rss_bytes"] > 0
    stages = payload["performance"]["stages"]
    assert {item["stage"] for item in stages} >= {"writing", "verifying", "mixing"}
    assert all(item["elapsed_ms"] >= 0 and item["status"] == "completed" for item in stages)


@pytest.mark.parametrize("fails", [True, False])
def test_live_driver_reports_measurements_and_closes_client(monkeypatch, tmp_path: Path, fails):
    for key in ("MORNING_RADIO_FIXTURE_RUN", "MORNING_RADIO_FAKE_LLM", "MORNING_RADIO_FAKE_TTS"):
        monkeypatch.delenv(key, raising=False)

    class Client:
        closed = False

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(benchmark, "OllamaClient", lambda *args: client)
    monkeypatch.setattr(benchmark, "build_tts_adapter", lambda engine: ToneTTS())
    monkeypatch.setattr(benchmark, "resolve_voices", lambda production: ("tone", "tone"))
    monkeypatch.setattr(
        benchmark.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200, json={"models": []}, request=httpx.Request("GET", "http://localhost")
        ),
    )

    def evaluate(*args):
        if fails:
            raise RuntimeError("evaluation unavailable")
        return [EvaluationResult(case="test", passed=True, expected=True, actual=True, mode="test")]

    monkeypatch.setattr(benchmark, "evaluate_live", evaluate)
    if fails:
        with pytest.raises(RuntimeError):
            benchmark.run_benchmark(tmp_path, repeats=2, live=True)
    else:
        benchmark.run_benchmark(tmp_path, repeats=2, live=True)
    assert client.closed
    payload = json.loads((tmp_path / "benchmark.json").read_text())
    if fails:
        assert "evaluation unavailable" in payload["error"]
    else:
        assert [sample["phase"] for sample in payload["samples"]] == ["first-use", "repeat"]
        assert all(sample["tts_ms"] > 0 for sample in payload["samples"])


def test_fixture_driver_uses_isolated_subprocesses(monkeypatch, tmp_path):
    calls = []

    def worker(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 600
        Path(command[-1]).write_text('{"elapsed_ms": 1}')

    monkeypatch.setattr(benchmark.subprocess, "run", worker)
    result = benchmark.run_benchmark(tmp_path, repeats=2)
    assert len(calls) == 2
    assert len(json.loads(result.read_text())["samples"]) == 2


@pytest.mark.parametrize("repeats", [0, 11])
def test_invalid_repeat_count(tmp_path, repeats):
    with pytest.raises(ValueError):
        benchmark.run_benchmark(tmp_path, repeats=repeats)


def test_live_mode_rejects_fake_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MORNING_RADIO_FAKE_TTS", "1")
    with pytest.raises(ValueError, match="Unset"):
        benchmark.run_benchmark(tmp_path, live=True)
