from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import morning_radio.audio.master as master_module
from morning_radio.audio.master import (
    AudioMasterError,
    coerce_production_plan,
    escape_concat_path,
    loudness_filter,
    measure_loudness,
    metadata_args,
    mix_and_master,
    mix_bed_under_speech,
    normalize_audio,
    run_command,
    run_probe,
    silence_trim_filter,
    validate_final_mp3,
)
from morning_radio.audio.production import BedStartItem, MusicItem, SpeechItem
from morning_radio.settings import ProductionSettings


@pytest.fixture(autouse=True)
def mock_loudness_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_module, "measure_loudness", lambda *args: -24.0)


def test_loudness_gain_is_bounded_and_silence_is_not_amplified() -> None:
    assert "volume=12.000dB" in loudness_filter(production_settings(), -55)
    assert "volume=0.000dB" in loudness_filter(production_settings(), float("-inf"))


def test_measurement_accepts_ffmpeg_output_after_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], root: Path, label: str) -> None:
        (root / "mix").mkdir()
        (root / "mix" / f"{label}-stderr.txt").write_text(
            'FFmpeg statistics\n{"input_i": "-24.1"}\nFinal output statistics\n'
        )

    monkeypatch.setattr(master_module, "run_command", fake_run)
    assert measure_loudness("ffmpeg", tmp_path / "speech.wav", tmp_path) == -24.1


def test_quiet_assets_are_skipped_or_fail_when_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(master_module, "measure_loudness", lambda *args: -52.0)
    source = tmp_path / "bed.wav"
    assert not master_module.usable_asset("ffmpeg", source, True, tmp_path)
    assert (tmp_path / "mix" / "skipped-bed.txt").exists()
    with pytest.raises(AudioMasterError, match="re-export"):
        master_module.usable_asset("ffmpeg", source, False, tmp_path)


def production_settings() -> ProductionSettings:
    return ProductionSettings.model_validate(
        {
            "tts": {"engine": "tone", "voice": None, "secondary_voice": None, "speed": 1.0},
            "audio": {
                "bitrate_kbps": 128,
                "sample_rate_hz": 44100,
                "channels": 2,
                "loudness_target_lufs": -16,
            },
            "assets": {
                "opening_optional": True,
                "closing_optional": True,
                "bumpers_optional": True,
                "beds_optional": True,
            },
        }
    )


def probe(
    duration: str, *, sample_rate: str = "44100", channels: int = 2, bit_rate: str = "128000"
):
    return {
        "format": {"duration": duration, "bit_rate": bit_rate},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "mp3",
                "sample_rate": sample_rate,
                "channels": channels,
                "bit_rate": bit_rate,
            }
        ],
    }


def episode_file(tmp_path: Path) -> Path:
    path = tmp_path / "episode.mp3"
    path.write_bytes(b"mp3")
    return path


@pytest.mark.parametrize(
    "probe_data",
    [
        {"streams": []},
        {"streams": [{"codec_type": "audio", "codec_name": "aac"}]},
        probe("nan"),
        probe("inf"),
        probe("90", channels=1),
    ],
)
def test_final_mp3_rejects_invalid_stream_metadata(tmp_path, probe_data):
    with pytest.raises(AudioMasterError):
        validate_final_mp3(episode_file(tmp_path), probe_data, 90, production_settings())


@pytest.mark.parametrize("exists", [True, False])
def test_final_mp3_rejects_missing_or_empty_file(tmp_path, exists):
    path = tmp_path / "empty.mp3"
    if exists:
        path.touch()
    with pytest.raises(AudioMasterError, match="not created"):
        validate_final_mp3(path, probe("90"))


@pytest.mark.parametrize(
    "item",
    [
        SpeechItem(text="Hello", host="HOST"),
        MusicItem(directive="[MUSIC: OPENING]", optional=True),
        BedStartItem(directive="[BED: bed]", optional=True),
    ],
)
def test_master_requires_audio_paths(tmp_path, monkeypatch, item):
    monkeypatch.setattr(master_module.shutil, "which", lambda name: f"/fake/{name}")
    with pytest.raises(AudioMasterError, match=r"no .*path"):
        mix_and_master([item], production_settings(), tmp_path)


@pytest.mark.parametrize("kind", ["music", "bed"])
@pytest.mark.parametrize("skipped", [False, True])
def test_master_skips_optional_silent_and_missing_assets(tmp_path, monkeypatch, kind, skipped):
    monkeypatch.setattr(master_module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(master_module, "measure_loudness", lambda *args: -60)
    cls = MusicItem if kind == "music" else BedStartItem
    item = cls(
        directive="[BED: bed]" if kind == "bed" else "[MUSIC: OPENING]",
        path=tmp_path / "silent.wav",
        skipped=skipped,
        optional=True,
    )
    with pytest.raises(AudioMasterError, match="did not render any audio"):
        mix_and_master([item], production_settings(), tmp_path)


def test_master_missing_ffmpeg_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(master_module.shutil, "which", lambda name: None)
    with pytest.raises(AudioMasterError, match="FFmpeg and FFprobe"):
        mix_and_master([], production_settings(), tmp_path)


@pytest.mark.parametrize("is_probe", [False, True])
def test_subprocess_start_failure_retains_diagnostics(tmp_path, monkeypatch, is_probe):
    def fail(*args, **kwargs):
        raise OSError("cannot execute binary")

    monkeypatch.setattr(master_module.subprocess, "run", fail)
    with pytest.raises(AudioMasterError, match="could not start"):
        if is_probe:
            run_probe("/missing/ffprobe", tmp_path / "audio.mp3", tmp_path)
        else:
            run_command(["/missing/ffmpeg"], tmp_path, "test")
    name = "ffprobe" if is_probe else "test"
    assert "cannot execute" in (tmp_path / "mix" / f"{name}-stderr.txt").read_text()


@pytest.mark.parametrize(
    ("code", "output", "message"), [(1, "", "FFprobe failed"), (0, "[]", "unexpected JSON")]
)
def test_probe_rejects_failure_or_wrong_shape(tmp_path, monkeypatch, code, output, message):
    monkeypatch.setattr(
        master_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], code, stdout=output, stderr="probe diagnostic"
        ),
    )
    with pytest.raises(AudioMasterError, match=message):
        run_probe("/fake/ffprobe", tmp_path / "audio.mp3", tmp_path)
    assert (tmp_path / "mix/ffprobe-stderr.txt").read_text() == "probe diagnostic"


def test_unknown_production_plan_item_fails_before_rendering() -> None:
    with pytest.raises(ValueError):
        coerce_production_plan([{"type": "unknown"}])


def test_final_mp3_rejects_one_second_fixture_output(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError):
        validate_final_mp3(episode_file(tmp_path), probe("1.0"))


def test_final_mp3_rejects_sub_minute_real_output(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="greater than 60"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("59.0"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_less_than_eighty_percent_of_plan(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="outside expected range"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("79.0"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_more_than_one_hundred_twenty_percent_of_plan(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="outside expected range"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("121.0"),
            planned_seconds=100,
            production=production_settings(),
        )


@pytest.mark.parametrize("duration", ["80.0", "100.0", "120.0"])
def test_final_mp3_accepts_plan_boundaries(tmp_path: Path, duration: str) -> None:
    validate_final_mp3(
        episode_file(tmp_path),
        probe(duration),
        planned_seconds=100,
        production=production_settings(),
    )


def test_final_mp3_rejects_nonnumeric_duration(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="nonnumeric"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("not-a-number"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match="sample rate"):
        validate_final_mp3(
            episode_file(tmp_path),
            probe("100.0", sample_rate="48000"),
            planned_seconds=100,
            production=production_settings(),
        )


def test_final_mp3_rejects_missing_bitrate(tmp_path: Path) -> None:
    payload = probe("100.0")
    payload["format"].pop("bit_rate")
    payload["streams"][0].pop("bit_rate")
    with pytest.raises(AudioMasterError, match="bitrate"):
        validate_final_mp3(
            episode_file(tmp_path),
            payload,
            planned_seconds=100,
            production=production_settings(),
        )


def test_concat_path_escapes_apostrophes() -> None:
    assert escape_concat_path(Path("/tmp/host's voice.wav")) == "/tmp/host'\\''s voice.wav"


def test_metadata_args_include_episode_fields() -> None:
    args = metadata_args("Morning Show 2026-08-15", "2026-08-15")
    pairs = list(zip(args[0::2], args[1::2], strict=True))

    assert all(flag == "-metadata" for flag, _ in pairs)
    assert ("-metadata", "title=Morning Show 2026-08-15") in pairs
    assert ("-metadata", "album=Personal Morning Radio") in pairs
    assert ("-metadata", "show=Personal Morning Radio") in pairs
    assert ("-metadata", "date=2026-08-15") in pairs


def test_master_command_preserves_configured_audio_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = production_settings()
    commands: list[tuple[str, list[str]]] = []

    def fake_run_command(command: list[str], _run_dir: Path, label: str) -> None:
        commands.append((label, command))
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"audio")

    def fake_probe(_ffprobe: str, episode: Path, _run_dir: Path):
        episode.write_bytes(b"mp3")
        return probe("100.0")

    monkeypatch.setattr(master_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    monkeypatch.setattr(master_module, "run_probe", fake_probe)

    mix_and_master(
        [{"type": "pause", "milliseconds": 1000}],
        production,
        tmp_path,
        planned_seconds=100,
    )

    master_command = dict(commands)["master"]
    assert master_command[master_command.index("-ar") + 1] == "44100"
    assert master_command[master_command.index("-ac") + 1] == "2"


def test_master_trims_only_opener_to_first_speech_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    normalizations: list[tuple[str, bool, bool]] = []

    def fake_normalize(
        _ffmpeg: str,
        source: Path,
        output: Path,
        _production: ProductionSettings,
        _run_dir: Path,
        *,
        trim_leading: bool = False,
        trim_trailing: bool = False,
        input_lufs: float | None = None,
    ) -> None:
        normalizations.append((source.name, trim_leading, trim_trailing))
        output.parent.mkdir(parents=True, exist_ok=True)
        master_module.write_silence(output, 1000)

    def fake_run_command(command: list[str], _run_dir: Path, _label: str) -> None:
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"audio")

    def fake_probe(_ffprobe: str, episode: Path, _run_dir: Path):
        episode.write_bytes(b"mp3")
        return probe("100.0")

    monkeypatch.setattr(master_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(master_module, "normalize_audio", fake_normalize)
    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    monkeypatch.setattr(master_module, "run_probe", fake_probe)

    mix_and_master(
        [
            {
                "type": "music",
                "directive": "[MUSIC: OPENING]",
                "path": tmp_path / "opener.wav",
                "optional": False,
            },
            {"type": "speech", "path": tmp_path / "first.wav"},
            {"type": "pause", "milliseconds": 650},
            {"type": "speech", "path": tmp_path / "second.wav"},
            {
                "type": "music",
                "directive": "[MUSIC: CLOSING]",
                "path": tmp_path / "closer.wav",
                "optional": False,
            },
        ],
        production_settings(),
        tmp_path,
    )

    assert normalizations == [
        ("opener.wav", False, True),
        ("first.wav", True, False),
        ("second.wav", False, False),
        ("closer.wav", False, False),
    ]


def test_normalize_audio_preserves_boundaries_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _run_dir: Path, _label: str) -> None:
        commands.append(command)

    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    normalize_audio(
        "/usr/bin/ffmpeg",
        tmp_path / "source.wav",
        tmp_path / "output.wav",
        production_settings(),
        tmp_path,
    )

    command = commands[0]
    audio_filter = command[command.index("-af") + 1]
    assert audio_filter == loudness_filter(production_settings())


def test_normalize_audio_can_trim_trailing_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _run_dir: Path, _label: str) -> None:
        commands.append(command)

    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    normalize_audio(
        "/usr/bin/ffmpeg",
        tmp_path / "source.wav",
        tmp_path / "output.wav",
        production_settings(),
        tmp_path,
        trim_trailing=True,
    )

    command = commands[0]
    audio_filter = command[command.index("-af") + 1]
    trailing_filter = silence_trim_filter(leading=False, trailing=True)
    assert audio_filter == f"{trailing_filter},{loudness_filter(production_settings())}"
    assert audio_filter.count("silenceremove=") == 1
    assert "areverse" in audio_filter


def test_bed_mix_preserves_speech_boundaries_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _run_dir: Path, _label: str) -> None:
        commands.append(command)

    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    mix_bed_under_speech(
        "/usr/bin/ffmpeg",
        tmp_path / "speech.wav",
        tmp_path / "bed.wav",
        tmp_path / "output.wav",
        production_settings(),
        tmp_path,
    )

    command = commands[0]
    audio_filter = command[command.index("-filter_complex") + 1]
    assert f"[0:a]{loudness_filter(production_settings())}[speech]" in audio_filter
    assert "[speech][bed]amix=" in audio_filter
    assert "normalize=0" in audio_filter


def test_bed_mix_can_trim_leading_speech_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _run_dir: Path, _label: str) -> None:
        commands.append(command)

    monkeypatch.setattr(master_module, "run_command", fake_run_command)
    mix_bed_under_speech(
        "/usr/bin/ffmpeg",
        tmp_path / "speech.wav",
        tmp_path / "bed.wav",
        tmp_path / "output.wav",
        production_settings(),
        tmp_path,
        trim_speech_leading=True,
    )

    command = commands[0]
    audio_filter = command[command.index("-filter_complex") + 1]
    leading_filter = silence_trim_filter(leading=True, trailing=False)
    assert f"[0:a]{leading_filter},{loudness_filter(production_settings())}[speech]" in audio_filter


def test_run_command_writes_stderr_diagnostics(tmp_path: Path) -> None:
    with pytest.raises(AudioMasterError, match=r"see .*fail-stderr.txt"):
        run_command(
            ["/bin/sh", "-c", "echo detailed failure >&2; exit 7"],
            tmp_path,
            "fail",
        )

    assert "detailed failure" in (tmp_path / "mix" / "fail-stderr.txt").read_text(encoding="utf-8")


def test_run_probe_rejects_invalid_json_with_diagnostics(tmp_path: Path) -> None:
    episode = episode_file(tmp_path)

    with pytest.raises(AudioMasterError, match="invalid JSON"):
        run_probe("/bin/echo", episode, tmp_path)

    assert (tmp_path / "mix" / "ffprobe-stderr.txt").exists()


@pytest.mark.parametrize("probe_command", [False, True])
def test_subprocess_timeouts_have_diagnostics(tmp_path, monkeypatch, probe_command):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] > 0
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(master_module.subprocess, "run", timeout)
    with pytest.raises(AudioMasterError, match="timed out"):
        if probe_command:
            run_probe("ffprobe", tmp_path / "episode.mp3", tmp_path)
        else:
            run_command(["ffmpeg"], tmp_path, "timeout")
    assert list((tmp_path / "mix").glob("*-stderr.txt"))
