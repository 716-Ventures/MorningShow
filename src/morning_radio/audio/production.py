from __future__ import annotations

import json
import os
import re
from pathlib import Path

from morning_radio.audio.tts import build_tts_adapter
from morning_radio.models import AudioMetadata
from morning_radio.settings import ProductionSettings
from morning_radio.showgen.script import spoken_blocks


class ProductionPlanError(RuntimeError):
    pass


def synthesize_script(
    script: str, production: ProductionSettings, run_dir: Path
) -> list[AudioMetadata]:
    voice = production.tts.voice or "tone"
    secondary = production.tts.secondary_voice or voice
    adapter = build_tts_adapter(production.tts.engine)
    available = set(adapter.available_voices())
    manifest: list[AudioMetadata] = []
    for index, (host, text) in enumerate(spoken_blocks(script), start=1):
        selected_voice = secondary if host == "HOST 2" else voice
        if (
            production.tts.engine == "kokoro"
            and selected_voice == "tone"
            and os.environ.get("MORNING_RADIO_FAKE_TTS") != "1"
        ):
            selected_voice = "af_heart"
        if selected_voice not in available:
            raise RuntimeError(f"Configured voice '{selected_voice}' is unavailable.")
        path = run_dir / "raw-audio" / f"{index:03d}-{host.lower().replace(' ', '-')}.wav"
        manifest.append(adapter.synthesize(text, selected_voice, path, speed=production.tts.speed))
    (run_dir / "raw-audio" / "manifest.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in manifest], indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_production_plan(
    script: str,
    audio: list[AudioMetadata],
    run_dir: Path,
    no_assets: bool,
    production: ProductionSettings,
    assets_dir: Path,
) -> list[dict]:
    plan: list[dict] = []
    audio_index = 0
    bed_active = False
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[PAUSE:"):
            milliseconds = int(re.findall(r"\d+", line)[0])
            plan.append({"type": "pause", "milliseconds": milliseconds})
        elif line.startswith("[MUSIC:"):
            asset_name = line.removeprefix("[MUSIC:").removesuffix("]").strip().lower()
            if asset_name not in {"opening", "closing"}:
                raise ProductionPlanError(f"Unknown music asset class: {asset_name}")
            maybe_add_asset(
                plan,
                assets_dir / asset_name,
                "music",
                line,
                no_assets,
                getattr(production.assets, f"{asset_name}_optional"),
            )
        elif line.startswith("[BUMPER:"):
            maybe_add_asset(
                plan,
                assets_dir / "bumpers",
                "bumper",
                line,
                no_assets,
                production.assets.bumpers_optional,
                line.removeprefix("[BUMPER:").removesuffix("]").strip(),
            )
        elif line.startswith("[BED:"):
            bed_name = line.removeprefix("[BED:").removesuffix("]").strip()
            if bed_name == "STOP":
                if not bed_active:
                    raise ProductionPlanError("BED STOP appeared without an active bed.")
                bed_active = False
                plan.append({"type": "bed_stop"})
            else:
                if bed_active:
                    raise ProductionPlanError("Nested beds are not supported.")
                bed_active = True
                maybe_add_asset(
                    plan,
                    assets_dir / "beds",
                    "bed_start",
                    line,
                    no_assets,
                    production.assets.beds_optional,
                    bed_name,
                )
        elif line in {"[HOST]", "[HOST 2]"} or line.startswith("["):
            continue
        else:
            metadata = audio[audio_index]
            audio_index += 1
            plan.append(
                {
                    "type": "speech",
                    "path": str(metadata.path),
                    "duration_seconds": metadata.duration_seconds,
                }
            )
    if bed_active:
        raise ProductionPlanError("A bed was started but never stopped.")
    (run_dir / "production-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return plan


def maybe_add_asset(
    plan: list[dict],
    asset_dir: Path,
    item_type: str,
    directive: str,
    no_assets: bool,
    optional: bool,
    name: str | None = None,
) -> None:
    if no_assets:
        return
    path = resolve_asset(asset_dir, name)
    if path is None:
        if optional:
            plan.append({"type": item_type, "directive": directive, "optional": True, "skipped": True})
            return
        raise ProductionPlanError(f"Missing required asset for {directive}")
    plan.append({"type": item_type, "directive": directive, "path": str(path), "optional": optional})


def resolve_asset(asset_dir: Path, name: str | None = None) -> Path | None:
    root = asset_dir.resolve()
    if name:
        if "/" in name or "\\" in name or ".." in Path(name).parts:
            raise ProductionPlanError(f"Invalid asset name: {name}")
        candidates = list(root.glob(f"{name}.*"))
    else:
        candidates = [path for path in root.iterdir() if path.is_file()] if root.exists() else []
    for candidate in sorted(candidates):
        resolved = candidate.resolve()
        if not str(resolved).startswith(str(root)):
            raise ProductionPlanError(f"Asset escapes configured directory: {candidate}")
        if resolved.suffix.lower() in {".wav", ".mp3", ".m4a", ".aiff", ".aac"}:
            return resolved
    return None
