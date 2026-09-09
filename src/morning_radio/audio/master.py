from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from morning_radio.artifacts.io import atomic_write_json, atomic_write_text
from morning_radio.audio.production import (
    PRODUCTION_PLAN_ADAPTER,
    BedStartItem,
    BedStopItem,
    BumperItem,
    MusicItem,
    PauseItem,
    ProductionItem,
    SpeechItem,
)
from morning_radio.settings import ProductionSettings


class AudioMasterError(RuntimeError):
    pass


SILENCE_THRESHOLD_DB = -60
SILENCE_DETECTION_SECONDS = 0.08
LEADING_SILENCE_SECONDS = 0.03
TRAILING_SILENCE_SECONDS = 0.08
PEAK_LIMITER = "alimiter=limit=0.84:level=false:latency=true"


def measure_loudness(ffmpeg: str, source: Path, run_dir: Path) -> float:
    label = f"measure-{source.stem}"
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(source),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
        ],
        run_dir,
        label,
    )
    stderr = (run_dir / "mix" / f"{label}-stderr.txt").read_text()
    try:
        measurement, _ = json.JSONDecoder().raw_decode(stderr[stderr.rfind("{") :])
        return float(measurement["input_i"])
    except (ValueError, KeyError) as exc:
        raise AudioMasterError(f"Cannot measure loudness of {source}") from exc


def usable_asset(ffmpeg: str, source: Path, optional: bool, run_dir: Path) -> bool:
    level = measure_loudness(ffmpeg, source, run_dir)
    if math.isfinite(level) and level >= -40:
        return True
    message = f"Audio asset {source} measures {level:.1f} LUFS; re-export at normal music level."
    if not optional:
        raise AudioMasterError(message)
    logging.getLogger(__name__).warning("Skipping optional asset: %s", message)
    atomic_write_text(run_dir / "mix" / f"skipped-{source.stem}.txt", message)
    return False


def write_silence(
    path: Path, milliseconds: int, sample_rate: int = 44100, channels: int = 1
) -> None:
    frames = int(sample_rate * (milliseconds / 1000))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames * channels)


def mix_and_master(
    plan: list[ProductionItem] | list[dict[str, Any]],
    production: ProductionSettings,
    run_dir: Path,
    *,
    planned_seconds: int | None = None,
    episode_title: str = "Personal Morning Radio",
    episode_date: str | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise AudioMasterError("FFmpeg and FFprobe are required to export episode.mp3.")
    typed_plan = coerce_production_plan(plan)
    concat_list = run_dir / "mix" / "concat.txt"
    files: list[Path] = []
    pause_index = 0
    rendered_index = 0
    active_bed: Path | None = None
    trim_next_speech_leading = False
    for item in typed_plan:
        if isinstance(item, SpeechItem):
            if item.path is None:
                raise AudioMasterError("Speech item has no synthesized audio path.")
            rendered_index += 1
            rendered = run_dir / "mix" / f"rendered-{rendered_index:03d}-speech.wav"
            if active_bed is None:
                normalize_audio(
                    ffmpeg,
                    item.path,
                    rendered,
                    production,
                    run_dir,
                    trim_leading=trim_next_speech_leading,
                )
            else:
                mix_bed_under_speech(
                    ffmpeg,
                    item.path,
                    active_bed,
                    rendered,
                    production,
                    run_dir,
                    trim_speech_leading=trim_next_speech_leading,
                )
            trim_next_speech_leading = False
            files.append(rendered)
        elif isinstance(item, PauseItem):
            pause_index += 1
            path = run_dir / "mix" / f"pause-{pause_index:03d}.wav"
            write_silence(
                path,
                item.milliseconds,
                production.audio.sample_rate_hz,
                production.audio.channels,
            )
            files.append(path)
        elif isinstance(item, MusicItem | BumperItem):
            if item.skipped:
                continue
            if item.path is None:
                raise AudioMasterError(f"Production item has no asset path: {item.directive}")
            if not usable_asset(ffmpeg, item.path, item.optional, run_dir):
                continue
            rendered_index += 1
            rendered = run_dir / "mix" / f"rendered-{rendered_index:03d}-{item.type}.wav"
            is_opening = isinstance(item, MusicItem) and item.directive.casefold() == (
                "[music: opening]"
            )
            normalize_audio(
                ffmpeg,
                item.path,
                rendered,
                production,
                run_dir,
                trim_trailing=is_opening,
            )
            if is_opening:
                trim_next_speech_leading = True
            files.append(rendered)
        elif isinstance(item, BedStartItem):
            if item.skipped:
                active_bed = None
                continue
            if item.path is None:
                raise AudioMasterError(f"Bed item has no asset path: {item.directive}")
            if not usable_asset(ffmpeg, item.path, item.optional, run_dir):
                active_bed = None
                continue
            rendered_index += 1
            rendered = run_dir / "mix" / f"rendered-{rendered_index:03d}-bed.wav"
            normalize_audio(ffmpeg, item.path, rendered, production, run_dir)
            active_bed = rendered
        elif isinstance(item, BedStopItem):
            active_bed = None
        else:
            raise AudioMasterError(f"Unsupported production item: {item!r}")
    if not files:
        raise AudioMasterError("Production plan did not render any audio.")
    atomic_write_text(
        concat_list,
        "".join(f"file '{escape_concat_path(path.resolve())}'\n" for path in files),
    )
    intermediate = run_dir / "mix" / "program.wav"
    episode = run_dir / "episode.mp3"
    run_command(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ar",
            str(production.audio.sample_rate_hz),
            "-ac",
            str(production.audio.channels),
            str(intermediate),
        ],
        run_dir,
        "concat",
    )
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(intermediate),
            "-af",
            PEAK_LIMITER,
            "-ar",
            str(production.audio.sample_rate_hz),
            "-ac",
            str(production.audio.channels),
            "-b:a",
            f"{production.audio.bitrate_kbps}k",
            *metadata_args(episode_title, episode_date),
            str(episode),
        ],
        run_dir,
        "master",
    )
    probe_json = run_probe(ffprobe, episode, run_dir)
    atomic_write_json(run_dir / "mix" / "final-ffprobe.json", probe_json)
    validate_final_mp3(episode, probe_json, planned_seconds, production)
    return episode


def metadata_args(episode_title: str, episode_date: str | None) -> list[str]:
    values = {
        "title": episode_title,
        "album": "Personal Morning Radio",
        "show": "Personal Morning Radio",
        "comment": "Generated by Personal Morning Radio",
    }
    if episode_date is not None:
        values["date"] = episode_date
    args: list[str] = []
    for key, value in values.items():
        args.extend(["-metadata", f"{key}={value}"])
    return args


def coerce_production_plan(
    plan: list[ProductionItem] | list[dict[str, Any]],
) -> list[ProductionItem]:
    return PRODUCTION_PLAN_ADAPTER.validate_python(plan)


def normalize_audio(
    ffmpeg: str,
    source: Path,
    output: Path,
    production: ProductionSettings,
    run_dir: Path,
    *,
    trim_leading: bool = False,
    trim_trailing: bool = False,
) -> None:
    filters = [
        silence_trim_filter(leading=trim_leading, trailing=trim_trailing),
        loudness_filter(production, measure_loudness(ffmpeg, source, run_dir)),
    ]
    audio_filter = ",".join(item for item in filters if item is not None)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-af",
            audio_filter,
            "-ar",
            str(production.audio.sample_rate_hz),
            "-ac",
            str(production.audio.channels),
            str(output),
        ],
        run_dir,
        f"normalize-{output.stem}",
    )


def silence_trim_filter(*, leading: bool, trailing: bool) -> str | None:
    filters: list[str] = []
    leading_filter = (
        "silenceremove="
        "start_periods=1:"
        f"start_duration={SILENCE_DETECTION_SECONDS}:"
        f"start_threshold={SILENCE_THRESHOLD_DB}dB:"
        f"start_silence={LEADING_SILENCE_SECONDS}"
    )
    if leading:
        filters.append(leading_filter)
    if trailing:
        trailing_filter = (
            "silenceremove="
            "start_periods=1:"
            f"start_duration={SILENCE_DETECTION_SECONDS}:"
            f"start_threshold={SILENCE_THRESHOLD_DB}dB:"
            f"start_silence={TRAILING_SILENCE_SECONDS}"
        )
        filters.extend(["areverse", trailing_filter, "areverse"])
    return ",".join(filters) or None


def loudness_filter(production: ProductionSettings, input_lufs: float = -24) -> str:
    gain = min(12.0, production.audio.loudness_target_lufs - input_lufs)
    if not math.isfinite(input_lufs):
        gain = 0.0
    return f"volume={gain:.3f}dB,{PEAK_LIMITER}"


def mix_bed_under_speech(
    ffmpeg: str,
    speech: Path,
    bed: Path,
    output: Path,
    production: ProductionSettings,
    run_dir: Path,
    *,
    trim_speech_leading: bool = False,
) -> None:
    layout = "mono" if production.audio.channels == 1 else "stereo"
    speech_filters = [
        silence_trim_filter(leading=trim_speech_leading, trailing=False),
        loudness_filter(production, measure_loudness(ffmpeg, speech, run_dir)),
    ]
    speech_filter = ",".join(item for item in speech_filters if item is not None)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(speech),
            "-stream_loop",
            "-1",
            "-i",
            str(bed),
            "-filter_complex",
            (
                f"[0:a]{speech_filter}[speech];"
                "[1:a]volume=0.18[bed];"
                f"[speech][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"{PEAK_LIMITER},"
                f"aresample={production.audio.sample_rate_hz},"
                f"aformat=channel_layouts={layout}[out]"
            ),
            "-map",
            "[out]",
            "-ar",
            str(production.audio.sample_rate_hz),
            "-ac",
            str(production.audio.channels),
            str(output),
        ],
        run_dir,
        f"bed-{output.stem}",
    )


def run_command(command: list[str], run_dir: Path, label: str) -> None:
    command_log = run_dir / "mix" / "ffmpeg-commands.jsonl"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.open("a", encoding="utf-8").write(
        json.dumps({"label": label, "command": command}, ensure_ascii=False) + "\n"
    )
    stderr_path = run_dir / "mix" / f"{label}-stderr.txt"
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        atomic_write_text(stderr_path, str(exc))
        raise AudioMasterError(
            f"FFmpeg command could not start for {label}; see {stderr_path}: {exc}"
        ) from exc
    atomic_write_text(stderr_path, result.stderr or "")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise AudioMasterError(
            f"FFmpeg command failed for {label}; see {stderr_path}: " + "\n".join(tail)
        )


def run_probe(ffprobe: str, episode: Path, run_dir: Path) -> dict[str, Any]:
    command = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(episode)]
    command_log = run_dir / "mix" / "ffmpeg-commands.jsonl"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.open("a", encoding="utf-8").write(
        json.dumps({"label": "ffprobe", "command": command}, ensure_ascii=False) + "\n"
    )
    stderr_path = run_dir / "mix" / "ffprobe-stderr.txt"
    try:
        probe = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        atomic_write_text(stderr_path, str(exc))
        raise AudioMasterError(f"FFprobe could not start; see {stderr_path}: {exc}") from exc
    atomic_write_text(stderr_path, probe.stderr or "")
    if probe.returncode != 0:
        tail = (probe.stderr or "").strip().splitlines()[-8:]
        raise AudioMasterError(f"FFprobe failed; see {stderr_path}: " + "\n".join(tail))
    try:
        parsed = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise AudioMasterError(f"FFprobe returned invalid JSON; see {stderr_path}") from exc
    if not isinstance(parsed, dict):
        raise AudioMasterError(f"FFprobe returned unexpected JSON; see {stderr_path}")
    return parsed


def escape_concat_path(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def validate_final_mp3(
    episode: Path,
    probe_json: dict[str, Any],
    planned_seconds: int | None = None,
    production: ProductionSettings | None = None,
) -> None:
    if not episode.exists() or episode.stat().st_size == 0:
        raise AudioMasterError("Final MP3 was not created.")
    streams = probe_json.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioMasterError("Final MP3 has no audio stream.")
    codec = audio_streams[0].get("codec_name")
    if codec not in {"mp3", "mp3float"}:
        raise AudioMasterError(f"Final audio codec is not MP3-compatible: {codec}")
    try:
        duration = float(probe_json.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise AudioMasterError("Final MP3 duration is missing or nonnumeric.") from exc
    if not math.isfinite(duration):
        raise AudioMasterError("Final MP3 duration is missing or nonnumeric.")
    if planned_seconds is None and duration <= 1:
        raise AudioMasterError("Final MP3 duration is too short to be valid.")
    if planned_seconds is not None and planned_seconds >= 60:
        if duration <= 60:
            raise AudioMasterError("Final MP3 duration must be greater than 60 seconds.")
        lower = planned_seconds * 0.8
        upper = planned_seconds * 1.2
        if not lower <= duration <= upper:
            raise AudioMasterError(
                f"Final MP3 duration {duration:.1f}s is outside expected range for "
                f"{planned_seconds}s planned."
            )
    if production is not None:
        stream = audio_streams[0]
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
        if sample_rate != production.audio.sample_rate_hz:
            raise AudioMasterError(
                f"Final MP3 sample rate {sample_rate} did not match "
                f"{production.audio.sample_rate_hz}."
            )
        if channels != production.audio.channels:
            raise AudioMasterError(
                f"Final MP3 channel count {channels} did not match {production.audio.channels}."
            )
        bit_rate = stream.get("bit_rate") or probe_json.get("format", {}).get("bit_rate")
        if bit_rate is None:
            raise AudioMasterError("Final MP3 bitrate metadata is missing.")
