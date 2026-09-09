from __future__ import annotations

import math
import shutil
import struct
import wave
from itertools import pairwise
from pathlib import Path

import pytest

from morning_radio.audio.master import mix_and_master, write_silence
from morning_radio.settings import load_production_settings


@pytest.mark.e2e
def test_bed_is_one_continuous_region_across_speech_and_pauses(tmp_path: Path) -> None:
    assert shutil.which("ffmpeg") and shutil.which("ffprobe")
    production = load_production_settings()
    rate = production.audio.sample_rate_hz
    bed = tmp_path / "bed.wav"
    # Three different pitches expose restarts that a repeating fixture cannot.
    with wave.open(str(bed), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    round(
                        8000 * math.sin(2 * math.pi * (220, 440, 880)[index // rate] * index / rate)
                    ),
                )
                for index in range(3 * rate)
            )
        )
    speech = tmp_path / "speech.wav"
    write_silence(speech, 1000, rate)
    plan = [
        {"type": "bed_start", "directive": "[BED: bed]", "path": bed, "optional": False},
        {"type": "speech", "path": speech},
        {"type": "pause", "milliseconds": 1000},
        {"type": "speech", "path": speech},
        {"type": "bed_stop"},
        {"type": "pause", "milliseconds": 1000},
    ]
    mix_and_master(plan, production, tmp_path)
    with wave.open(str(tmp_path / "mix/program.wav"), "rb") as handle:
        assert handle.getnframes() == 4 * rate
        channels = handle.getnchannels()
        values = [
            value[0] for value in struct.iter_unpack("<h", handle.readframes(handle.getnframes()))
        ][::channels]
    for second, frequency in enumerate((220, 440, 880)):
        segment = values[second * rate + rate // 4 : second * rate + 3 * rate // 4]
        crossings = sum(left <= 0 < right for left, right in pairwise(segment))
        assert abs(crossings * 2 - frequency) <= 2
        assert max(segment) > 100
    assert max(abs(value) for value in values[3 * rate :]) == 0
