"""Reproducible isolated fixture runs and opt-in local model/voice measurements.

Run `python -m morning_radio.benchmark --help`. Live runs never unload a model or
modify operator configuration/history. First-use and repeat measurements are
reported separately; Ollama's load_duration establishes whether it was cold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import httpx

from morning_radio.artifacts.io import atomic_write_json, atomic_write_text
from morning_radio.audio.elevenlabs import ElevenLabsTTS
from morning_radio.audio.production import resolve_voices
from morning_radio.audio.tts import build_tts_adapter, prepare_tts_text
from morning_radio.evaluation import evaluate_live, load_corpus
from morning_radio.llm.client import OllamaClient
from morning_radio.models import Interest
from morning_radio.performance import peak_memory
from morning_radio.pipeline import run_morning
from morning_radio.profile.compiler import default_profile, save_profile
from morning_radio.settings import load_app_settings, load_production_settings, repo_root


def fixture_worker(output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="morning-benchmark-") as directory:
        root = Path(directory)
        shutil.copytree(repo_root() / "config", root / "config")
        save_profile(default_profile(), root)
        # Explicit mode keeps operator history/assets and network out of fixtures.
        os.environ.update(
            MORNING_RADIO_FIXTURE_RUN="1",
            MORNING_RADIO_FAKE_LLM="1",
            MORNING_RADIO_FAKE_TTS="1",
            MORNING_RADIO_FIXTURE_DIR=str(repo_root() / "tests/fixtures/morning-run"),
        )
        os.environ.pop("MORNING_RADIO_FAKE_VERIFICATION_FAIL", None)
        production = load_production_settings(root)
        production.generate_audio = True
        atomic_write_text(
            root / "config/production.yaml", json.dumps(production.model_dump(mode="json"))
        )
        started = time.monotonic()
        result = run_morning(date(2026, 8, 14), minutes=10, no_assets=True, root=root)
        run_dir = root / Path(result.script).parent
        atomic_write_json(
            output,
            {
                "elapsed_ms": (time.monotonic() - started) * 1000,
                "performance": json.loads((run_dir / "performance.json").read_text()),
                **peak_memory(),
            },
        )


def episode_worker(output: Path) -> None:
    """Full real-adapter pipeline on frozen source fixtures, with retained evidence."""
    root = output.parent / f"{output.stem}-workspace"
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(repo_root() / "config", root / "config")
    profile = default_profile()
    profile.interests = [
        Interest(name=name, priority=5, depth="normal") for name in load_corpus().interests
    ]
    save_profile(profile, root)
    settings = load_app_settings(root)
    settings.selection.maximum_selected_stories = 3
    atomic_write_text(root / "config/app.yaml", json.dumps(settings.model_dump(mode="json")))
    production = load_production_settings(root)
    production.generate_audio = True
    atomic_write_text(
        root / "config/production.yaml", json.dumps(production.model_dump(mode="json"))
    )
    os.environ["MORNING_RADIO_FIXTURE_RUN"] = "1"
    os.environ["MORNING_RADIO_FIXTURE_DIR"] = str(repo_root() / "tests/fixtures/editorial-episode")
    for key in (
        "MORNING_RADIO_FAKE_LLM",
        "MORNING_RADIO_FAKE_TTS",
        "MORNING_RADIO_FAKE_VERIFICATION_FAIL",
    ):
        os.environ.pop(key, None)
    started = time.monotonic()
    try:
        result = run_morning(date(2026, 9, 9), minutes=5, no_assets=True, root=root)
        if result.stories != len(load_corpus().interests):
            raise RuntimeError("Acceptance episode must cover all three supplied interest stories.")
    except Exception as exc:
        atomic_write_json(
            output,
            {
                "error": str(exc),
                "workspace": str(root),
                "elapsed_ms": (time.monotonic() - started) * 1000,
                **peak_memory(),
            },
        )
        raise
    run_dir = root / Path(result.script).parent
    atomic_write_json(
        output,
        {
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "workspace": str(root),
            "result": result.model_dump(mode="json"),
            "performance": json.loads((run_dir / "performance.json").read_text()),
            **peak_memory(),
        },
    )


def run_benchmark(
    output: Path, repeats: int = 3, *, live: bool = False, thinking: bool | None = None
) -> Path:
    if not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10")
    output.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    app = load_app_settings()
    production = load_production_settings()
    if thinking is not None:
        app.llm.thinking = thinking
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": "live" if live else "fixture",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "model": app.llm.model,
        "thinking": app.llm.thinking,
        "production": production.model_dump(mode="json"),
        "corpus_sha256": hashlib.sha256(corpus.model_dump_json().encode()).hexdigest(),
        "implementation_sha256": hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted(Path(__file__).parent.rglob("*.py")))
        ).hexdigest(),
        "memory_scope": "Process/subprocess lifetime high-water marks, not concurrent sum; external Ollama size is reported separately.",
        "samples": [],
    }
    samples: list[dict[str, object]] = []
    report["samples"] = samples
    path = output / "benchmark.json"
    if not live:
        for index in range(repeats):
            sample_path = output / f"fixture-{index + 1}.json"
            subprocess.run(
                [sys.executable, "-m", "morning_radio.benchmark", "--worker", str(sample_path)],
                check=True,
                timeout=600,
            )
            samples.append(json.loads(sample_path.read_text()))
            atomic_write_json(path, report)
        return path
    for key in ("MORNING_RADIO_FAKE_LLM", "MORNING_RADIO_FAKE_TTS", "MORNING_RADIO_FIXTURE_RUN"):
        if os.environ.get(key) == "1":
            raise ValueError(f"Unset {key} before a live benchmark")
    if app.llm.provider != "ollama":
        raise ValueError(
            "Live benchmarks currently require Ollama; cloud benchmarks are not supported."
        )
    client = OllamaClient(app.llm, output)
    adapter = None
    voice, _ = resolve_voices(production)
    try:
        for index in range(repeats):
            sample_dir = output / f"live-{index + 1}"
            sample_dir.mkdir(exist_ok=True)
            started = time.monotonic()
            results = evaluate_live(client, sample_dir)
            llm_ms = (time.monotonic() - started) * 1000
            tts_started = time.monotonic()
            if adapter is None:
                adapter = build_tts_adapter(production.tts.engine, settings=production.tts)
            prepared = prepare_tts_text(corpus.speech, production.tts.pronunciation_overrides)
            audio = adapter.synthesize(
                prepared, voice, sample_dir / "speech.wav", speed=production.tts.speed
            )
            sample: dict[str, object] = {
                "index": index + 1,
                "phase": "first-use" if index == 0 else "repeat",
                "llm_ms": llm_ms,
                "tts_ms": (time.monotonic() - tts_started) * 1000,
                "audio_seconds": audio.duration_seconds,
                "evaluation_passed": all(item.passed for item in results),
                "evaluation": [item.model_dump() for item in results],
                **peak_memory(),
            }
            try:
                response = httpx.get(f"{str(app.llm.base_url).rstrip('/')}/api/ps", timeout=5)
                response.raise_for_status()
                sample["ollama_loaded_models"] = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                sample["ollama_memory_error"] = str(exc)
            samples.append(sample)
            atomic_write_json(path, report)
    except Exception as exc:
        report["error"] = str(exc)
        atomic_write_json(path, report)
        raise
    finally:
        client.close()
        if isinstance(adapter, ElevenLabsTTS):
            adapter.close()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("runs/benchmarks") / str(time.time_ns())
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--episode",
        action="store_true",
        help="One full real-Qwen/TTS pipeline run on fixed source fixtures; retains its isolated workspace.",
    )
    args = parser.parse_args()
    if args.worker:
        fixture_worker(args.worker)
    elif args.episode:
        args.output.mkdir(parents=True, exist_ok=True)
        episode_worker(args.output / "episode.json")
        print(args.output / "episode.json")
    else:
        print(run_benchmark(args.output, args.repeats, live=args.live, thinking=args.thinking))


if __name__ == "__main__":
    main()
