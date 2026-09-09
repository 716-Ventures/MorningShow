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

The intended runtime is macOS on Apple Silicon with Python 3.12 or 3.13, `uv`, and Ollama. Python 3.14 is not supported. Audio generation is enabled in the committed production configuration; it requires Kokoro, FFmpeg, and FFprobe.

```bash
uv sync --locked --dev --extra tts --python 3.12
./show doctor
./show configure
./show morning --minutes 10 --no-assets
```

Generated scripts and intermediate artifacts are written under `runs/YYYY-MM-DD/<run-id>/`, not `data/runs/`. The final command prints the episode and sources paths for audio runs, or script and sources paths for script-only runs.

For script-only development, set `generate_audio: false` in `config/production.yaml`. Set it back to `true` to generate MP3s. `--no-assets` excludes music, bumpers, and beds; it does not disable speech synthesis or MP3 export. Install FFmpeg with `brew install ffmpeg`. Set the Ollama model name in `config/app.yaml`, pull that exact model with `ollama pull MODEL_NAME`, and keep Ollama running.

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

Fixture mode is a plumbing check, not a script or voice-quality demonstration. Do not export the three fixture variables in your shell for normal runs. For isolated fixtures that do not change your local run history, use the automated tests below.

## Development

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest --cov=morning_radio --cov-branch --cov-report=term-missing -q
```

The suite includes isolated script-only and MP3 fixture runs, a wheel-build check, and real FFmpeg signal tests. It does not need a running Ollama instance or download TTS weights. Type checking currently uses Pyright's basic mode; CI enforces the committed lockfile, formatting, lint, type checks, and at least 70% branch-aware combined coverage.

See [Maintainer Guide](docs/maintainer-guide.md) for module responsibilities, configuration, failure handling, and testing conventions. See [Code Review](docs/code-review-2026-09-09.md) for findings, measured verification, and remaining limitations.
