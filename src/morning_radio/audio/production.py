from __future__ import annotations

import json
import re
from pathlib import Path

from morning_radio.audio.tts import ToneTTS
from morning_radio.models import AudioMetadata
from morning_radio.settings import ProductionSettings
from morning_radio.showgen.script import spoken_blocks


def synthesize_script(
    script: str, production: ProductionSettings, run_dir: Path
) -> list[AudioMetadata]:
    voice = production.tts.voice or "tone"
    secondary = production.tts.secondary_voice or voice
    adapter = ToneTTS()
    manifest: list[AudioMetadata] = []
    for index, (host, text) in enumerate(spoken_blocks(script), start=1):
        selected_voice = secondary if host == "HOST 2" else voice
        if selected_voice != "tone":
            raise RuntimeError(
                f"Configured voice '{selected_voice}' is unavailable in fallback TTS. "
                "Install Kokoro or set config/production.yaml tts.voice to null."
            )
        path = run_dir / "raw-audio" / f"{index:03d}-{host.lower().replace(' ', '-')}.wav"
        manifest.append(adapter.synthesize(text, "tone", path, speed=production.tts.speed))
    (run_dir / "raw-audio" / "manifest.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in manifest], indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_production_plan(script: str, audio: list[AudioMetadata], run_dir: Path, no_assets: bool) -> list[dict]:
    plan: list[dict] = []
    audio_index = 0
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[PAUSE:"):
            milliseconds = int(re.findall(r"\d+", line)[0])
            plan.append({"type": "pause", "milliseconds": milliseconds})
        elif line.startswith(("[MUSIC:", "[BUMPER:", "[BED:")):
            if no_assets:
                continue
            plan.append({"type": "asset", "directive": line, "optional": True, "skipped": True})
        elif line in {"[HOST]", "[HOST 2]"} or line.startswith("["):
            continue
        else:
            metadata = audio[audio_index]
            audio_index += 1
            plan.append({"type": "speech", "path": str(metadata.path), "duration_seconds": metadata.duration_seconds})
    (run_dir / "production-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return plan
