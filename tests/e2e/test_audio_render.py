from __future__ import annotations

import math
import shutil
import struct
import wave
from collections import Counter
from itertools import pairwise
from pathlib import Path

import pytest

from morning_radio.audio import master
from morning_radio.settings import ProductionSettings


@pytest.mark.e2e
def test_real_asset_render_preserves_signal_and_reuses_measurements(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), "Install FFmpeg to run audio integration tests."
    source = tmp_path / "signal.wav"
    master.run_command(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(source)],
        tmp_path,
        "fixture",
    )
    calls: Counter[Path] = Counter()
    original = master.measure_loudness

    def measured(binary, path, root):
        calls[path] += 1
        return original(binary, path, root)

    monkeypatch.setattr(master, "measure_loudness", measured)
    production = ProductionSettings.model_validate(
        {
            "tts": {"engine": "tone", "speed": 1},
            "audio": {
                "bitrate_kbps": 128,
                "sample_rate_hz": 44100,
                "channels": 2,
                "loudness_target_lufs": -16,
            },
            "assets": {
                "opening_optional": False,
                "closing_optional": False,
                "bumpers_optional": False,
                "beds_optional": False,
            },
        }
    )
    plan = [
        {"type": "music", "directive": "[MUSIC: OPENING]", "path": source, "optional": False},
        {"type": "speech", "path": source},
        {"type": "bumper", "directive": "[BUMPER]", "path": source, "optional": False},
        {"type": "bed_start", "directive": "[BED: bed]", "path": source, "optional": False},
        {"type": "speech", "path": source},
        {"type": "pause", "milliseconds": 650},
        {"type": "bed_stop"},
        {"type": "music", "directive": "[MUSIC: CLOSING]", "path": source, "optional": False},
    ]
    episode = master.mix_and_master(plan, production, tmp_path)
    assert episode.stat().st_size > 1000
    assert calls == {source.resolve(): 1}

    def samples(path):
        with wave.open(str(path), "rb") as handle:
            assert handle.getsampwidth() == 2
            return [
                value[0] / 32768
                for value in struct.iter_unpack("<h", handle.readframes(handle.getnframes()))
            ]

    raw = samples(source)
    spoken = samples(tmp_path / "mix/rendered-002-speech.wav")

    def rms(values):
        return math.sqrt(sum(value * value for value in values) / len(values))

    assert rms(spoken) > rms(raw) * 1.2
    assert max(abs(value) for value in spoken) < 0.85
    # A clean 440 Hz fixture should remain smooth, not turn into broadband static.
    for rendered in (tmp_path / "mix").glob("rendered-*.wav"):
        signal = samples(rendered)[8820:-8820]
        assert signal and rms(signal) > 0.02
        assert max(abs(right - left) for left, right in pairwise(signal)) < 0.1

    # The voice/pause budget excludes standalone assets; music must not falsely
    # trigger an overlong-episode error when it exceeds 20% of that budget.
    long_run = tmp_path / "long"
    music = [plan[0]] * 10
    master.mix_and_master(
        [{"type": "pause", "milliseconds": 61000}, *music],
        production,
        long_run,
        planned_seconds=61,
    )
