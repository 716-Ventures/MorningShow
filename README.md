# Personal Morning Radio POC

Personal Morning Radio is a deliberately rough, local-first CLI that creates a radio-style morning news episode from an editorial profile.

The operator interface is the repository-root `./show` command:

```bash
./show configure
./show morning
./show morning --minutes 20
./show morning --date 2026-08-14
./show feedback
./show doctor
```

## Setup

The intended runtime is macOS on Apple Silicon with Python 3.12, `uv`, Ollama, local TTS, FFmpeg, and FFprobe.

```bash
uv sync
# Include the local Kokoro TTS stack used by the default production config:
uv sync --extra tts
./show doctor
./show configure
./show morning --minutes 10 --no-assets
```

Generated episodes and intermediate artifacts are written under `runs/YYYY-MM-DD/<run-id>/`. The final command prints the path to `episode.mp3` and `sources.html` when successful.

## Local Dependencies

`./show doctor` checks the required local tools and configuration:

- Python version
- writable `data/`
- `config/app.yaml`, `config/feeds.yaml`, and `config/production.yaml`
- SQLite state
- Ollama reachability and configured model
- FFmpeg and FFprobe
- TTS adapter availability
- at least one enabled feed

The POC uses live public feeds by default. Automated tests use fixtures and fake model/audio adapters so orchestration can be verified without internet access or a live local model.

## Offline Fixture Mode

The normal morning command requires live feeds, Ollama, the configured local model, Kokoro, FFmpeg, and FFprobe. For reproducible development tests, the pipeline also supports an explicit fixture mode:

```bash
MORNING_RADIO_FIXTURE_RUN=1 \
MORNING_RADIO_FAKE_LLM=1 \
MORNING_RADIO_FAKE_TTS=1 \
./show morning --minutes 10 --no-assets
```

This mode uses the offline fixture corpus under `tests/fixtures/morning-run/`, deterministic fake model responses, and tone WAV speech. It still uses FFmpeg/FFprobe for the final MP3 when available.
