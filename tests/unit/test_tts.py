from __future__ import annotations

from pathlib import Path

from morning_radio.audio.tts import ToneTTS


def test_tone_tts_writes_playable_wav(tmp_path: Path) -> None:
    path = tmp_path / "voice.wav"
    metadata = ToneTTS().synthesize("Good morning from the fixture show.", "tone", path)
    assert path.exists()
    assert path.stat().st_size > 44
    assert metadata.duration_seconds > 0
