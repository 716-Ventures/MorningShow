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

The intended runtime is macOS on Apple Silicon with Python 3.12, `uv`, and Ollama. Audio generation is currently disabled while script quality is being developed, so local TTS, FFmpeg, and FFprobe are optional.

```bash
uv sync
./show doctor
./show configure
./show morning --minutes 10 --no-assets
```

Generated scripts and intermediate artifacts are written under `runs/YYYY-MM-DD/<run-id>/`. The final command prints the paths to `script-final.md` and `sources.html` when successful.

To reactivate MP3 generation, set `generate_audio: true` in `config/production.yaml` and install the audio runtime with `uv sync --extra tts`. Audio-enabled runs additionally require Kokoro, FFmpeg, and FFprobe.

Speech preparation is configured under `tts` in `config/production.yaml`. Add names or terms to
`pronunciation_overrides` using the exact written form as the key and a phonetic, listener-facing
form as the value. `inter_block_pause_ms` controls the short pause inserted between adjacent host
paragraphs. Long paragraphs are split at sentence boundaries using `max_chunk_words`, with
`sentence_pause_ms` inserted between those groups. Explicit `[PAUSE: ...]` directives are left
unchanged.

## Local Dependencies

`./show doctor` checks the required local tools and configuration:

- Python version
- writable `data/`
- `config/app.yaml`, `config/feeds.yaml`, and `config/production.yaml`
- SQLite state
- Ollama reachability and configured model
- FFmpeg and FFprobe when audio generation is enabled
- TTS adapter availability when audio generation is enabled
- at least one enabled feed

The POC uses live public feeds by default. Automated tests use fixtures and fake model/audio adapters so orchestration can be verified without internet access or a live local model.

## Offline Fixture Mode

The normal morning command requires live feeds, Ollama, and the configured local model. For reproducible development tests, the pipeline also supports an explicit fixture mode:

```bash
MORNING_RADIO_FIXTURE_RUN=1 \
MORNING_RADIO_FAKE_LLM=1 \
MORNING_RADIO_FAKE_TTS=1 \
./show morning --minutes 10 --no-assets
```

This mode uses the offline fixture corpus under `tests/fixtures/morning-run/` and deterministic fake model responses. When audio generation is enabled, it also uses tone WAV speech and FFmpeg/FFprobe for the final MP3.
