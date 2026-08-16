from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from morning_radio.artifacts.io import atomic_write_json
from morning_radio.audio.tts import TTSAdapter, build_tts_adapter, speech_hash
from morning_radio.models import AudioMetadata, EditorialProfile
from morning_radio.settings import ProductionSettings
from morning_radio.showgen.script import spoken_blocks


class ProductionPlanError(RuntimeError):
    pass


class SpeechItem(BaseModel):
    type: Literal["speech"] = "speech"
    path: Path | None = None
    duration_seconds: float | None = None
    text: str | None = None
    host: str | None = None


class PauseItem(BaseModel):
    type: Literal["pause"] = "pause"
    milliseconds: int = Field(gt=0)


class MusicItem(BaseModel):
    type: Literal["music"] = "music"
    directive: str
    path: Path | None = None
    optional: bool
    skipped: bool = False


class BumperItem(BaseModel):
    type: Literal["bumper"] = "bumper"
    directive: str
    path: Path | None = None
    optional: bool
    skipped: bool = False


class BedStartItem(BaseModel):
    type: Literal["bed_start"] = "bed_start"
    directive: str
    path: Path | None = None
    optional: bool
    skipped: bool = False


class BedStopItem(BaseModel):
    type: Literal["bed_stop"] = "bed_stop"


ProductionItem = Annotated[
    SpeechItem | PauseItem | MusicItem | BumperItem | BedStartItem | BedStopItem,
    Field(discriminator="type"),
]

PRODUCTION_PLAN_ADAPTER = TypeAdapter(list[ProductionItem])


def synthesize_script(
    script: str,
    production: ProductionSettings,
    run_dir: Path,
    profile: EditorialProfile | None = None,
    adapter: TTSAdapter | None = None,
) -> list[AudioMetadata]:
    voice, secondary = resolve_voices(production, profile)
    adapter = adapter or build_tts_adapter(production.tts.engine)
    available = set(adapter.available_voices())
    manifest: list[AudioMetadata] = []
    cache: dict[str, AudioMetadata] = {}
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
        normalized = " ".join(text.split())
        text_hash = speech_hash(
            production.tts.engine,
            adapter.engine_version,
            selected_voice,
            production.tts.speed,
            normalized,
        )
        if text_hash in cache:
            manifest.append(cache[text_hash])
            continue
        path = run_dir / "raw-audio" / f"{index:03d}-{host.lower().replace(' ', '-')}.wav"
        metadata = adapter.synthesize(text, selected_voice, path, speed=production.tts.speed)
        cache[text_hash] = metadata
        manifest.append(metadata)
    atomic_write_json(
        run_dir / "raw-audio" / "manifest.json",
        [item.model_dump(mode="json") for item in manifest],
    )
    return manifest


def resolve_voices(
    production: ProductionSettings, profile: EditorialProfile | None = None
) -> tuple[str, str]:
    primary = production.tts.voice or profile_voice(profile, "primary_voice") or "tone"
    secondary = production.tts.secondary_voice or profile_voice(profile, "secondary_voice") or primary
    return primary, secondary


def profile_voice(profile: EditorialProfile | None, field_name: Literal["primary_voice", "secondary_voice"]) -> str | None:
    if profile is None:
        return None
    return getattr(profile.voice_preferences, field_name)


def build_production_plan(
    script: str,
    audio: list[AudioMetadata],
    run_dir: Path,
    no_assets: bool,
    production: ProductionSettings,
    assets_dir: Path,
) -> list[ProductionItem]:
    plan = build_text_production_plan(script, run_dir, no_assets, production, assets_dir)
    plan = attach_audio_to_plan(plan, audio)
    write_production_plan(plan, run_dir)
    return plan


def build_text_production_plan(
    script: str,
    run_dir: Path,
    no_assets: bool,
    production: ProductionSettings,
    assets_dir: Path,
) -> list[ProductionItem]:
    plan: list[ProductionItem] = []
    bed_active = False
    current_host = "HOST"
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[PAUSE:"):
            milliseconds = int(re.findall(r"\d+", line)[0])
            plan.append(PauseItem(milliseconds=milliseconds))
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
                plan.append(BedStopItem())
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
            if line in {"[HOST]", "[HOST 2]"}:
                current_host = line.strip("[]")
            continue
        else:
            plan.append(SpeechItem(text=line, host=current_host))
    if bed_active:
        raise ProductionPlanError("A bed was started but never stopped.")
    write_production_plan(plan, run_dir)
    return plan


def attach_audio_to_plan(
    plan: list[ProductionItem], audio: list[AudioMetadata]
) -> list[ProductionItem]:
    audio_index = 0
    attached: list[ProductionItem] = []
    for item in plan:
        if isinstance(item, SpeechItem):
            if audio_index >= len(audio):
                raise ProductionPlanError("Not enough synthesized audio for speech plan.")
            metadata = audio[audio_index]
            audio_index += 1
            attached.append(
                item.model_copy(
                    update={
                        "path": metadata.path,
                        "duration_seconds": metadata.duration_seconds,
                    }
                )
            )
        else:
            attached.append(item)
    if audio_index != len(audio):
        raise ProductionPlanError("Synthesized audio count does not match speech plan.")
    return attached


def write_production_plan(plan: list[ProductionItem], run_dir: Path) -> None:
    atomic_write_json(
        run_dir / "production-plan.json",
        PRODUCTION_PLAN_ADAPTER.dump_python(plan, mode="json"),
    )


def maybe_add_asset(
    plan: list[ProductionItem],
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
            plan.append(_asset_item(item_type, directive, None, optional, skipped=True))
            return
        raise ProductionPlanError(f"Missing required asset for {directive}")
    plan.append(_asset_item(item_type, directive, path, optional, skipped=False))


def _asset_item(
    item_type: str,
    directive: str,
    path: Path | None,
    optional: bool,
    *,
    skipped: bool,
) -> MusicItem | BumperItem | BedStartItem:
    if item_type == "music":
        return MusicItem(directive=directive, path=path, optional=optional, skipped=skipped)
    if item_type == "bumper":
        return BumperItem(directive=directive, path=path, optional=optional, skipped=skipped)
    if item_type == "bed_start":
        return BedStartItem(directive=directive, path=path, optional=optional, skipped=skipped)
    raise ProductionPlanError(f"Unknown production item type: {item_type}")


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
