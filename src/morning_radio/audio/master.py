from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

from morning_radio.settings import ProductionSettings


class AudioMasterError(RuntimeError):
    pass


def write_silence(path: Path, milliseconds: int, sample_rate: int = 44100) -> None:
    frames = int(sample_rate * (milliseconds / 1000))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def mix_and_master(
    plan: list[dict],
    production: ProductionSettings,
    run_dir: Path,
    *,
    planned_seconds: int | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise AudioMasterError("FFmpeg and FFprobe are required to export episode.mp3.")
    concat_list = run_dir / "mix" / "concat.txt"
    files: list[Path] = []
    pause_index = 0
    for item in plan:
        if item["type"] == "speech":
            files.append(Path(item["path"]))
        elif item["type"] == "pause":
            pause_index += 1
            path = run_dir / "mix" / f"pause-{pause_index:03d}.wav"
            write_silence(path, item["milliseconds"], production.audio.sample_rate_hz)
            files.append(path)
    with concat_list.open("w", encoding="utf-8") as handle:
        for path in files:
            handle.write(f"file '{path.resolve()}'\n")
    intermediate = run_dir / "mix" / "program.wav"
    episode = run_dir / "episode.mp3"
    subprocess.run(
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
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(intermediate),
            "-af",
            f"loudnorm=I={production.audio.loudness_target_lufs}:TP=-1.5:LRA=11",
            "-b:a",
            f"{production.audio.bitrate_kbps}k",
            "-metadata",
            "title=Personal Morning Radio",
            str(episode),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(episode)],
        check=True,
        capture_output=True,
        text=True,
    )
    probe_json = json.loads(probe.stdout)
    (run_dir / "mix" / "final-ffprobe.json").write_text(json.dumps(probe_json, indent=2) + "\n")
    validate_final_mp3(episode, probe_json, planned_seconds)
    return episode


def validate_final_mp3(episode: Path, probe_json: dict, planned_seconds: int | None = None) -> None:
    if not episode.exists() or episode.stat().st_size == 0:
        raise AudioMasterError("Final MP3 was not created.")
    streams = probe_json.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioMasterError("Final MP3 has no audio stream.")
    codec = audio_streams[0].get("codec_name")
    if codec not in {"mp3", "mp3float"}:
        raise AudioMasterError(f"Final audio codec is not MP3-compatible: {codec}")
    duration = float(probe_json.get("format", {}).get("duration") or 0)
    if duration <= 1:
        raise AudioMasterError("Final MP3 duration is too short to be valid.")
    if planned_seconds is not None and planned_seconds >= 60:
        lower = planned_seconds * 0.2
        upper = planned_seconds * 1.8
        if not lower <= duration <= upper:
            raise AudioMasterError(
                f"Final MP3 duration {duration:.1f}s is outside expected range for "
                f"{planned_seconds}s planned."
            )
