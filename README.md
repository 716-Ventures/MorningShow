# Personal Morning Radio POC

Personal Morning Radio is a deliberately rough, local-first CLI that creates a radio-style morning news episode from an editorial profile.

The operator interface is the repository-root `./show` command:

```bash
./show setup
./show providers
./show configure
./show morning
./show morning --minutes 20
./show morning --date 2026-08-14
./show feedback
./show doctor
./show voice-preview
./show codex-login
./show codex-status
```

## Setup

The intended runtime is macOS on Apple Silicon with Python 3.12 or 3.13 and `uv`. Python 3.14 is not supported. Run `./show setup` to choose local, cloud, mixed, or script-only production. It inspects this machine and recommends a conservative local model. Script providers are Ollama, OpenAI API, or ChatGPT subscription via Codex. Speech providers are Kokoro or ElevenLabs. MP3 generation also needs FFmpeg and FFprobe.

```bash
uv sync --locked --dev --python 3.12
./show setup
./show doctor
./show configure
./show morning --minutes 10 --no-assets
```

Generated scripts and intermediate artifacts are written under `runs/YYYY-MM-DD/<run-id>/`, not `data/runs/`. The final command prints the episode and sources paths for audio runs, or script and sources paths for script-only runs.

Setup saves ignored machine-specific preferences in `config/providers.local.yaml`; these override the base `llm`, `tts`, and `generate_audio` settings. Re-run setup to change providers or disable audio. The first interactive generation opens setup automatically; unattended generation requires completing setup first. Your editorial profile is unchanged. See [production setup](docs/production-setup.md) for the provider catalog, recommendation policy, credential handling, and billing limitations.

`--no-assets` excludes music, bumpers, and beds; it does not disable speech synthesis or MP3 export. Install FFmpeg with `brew install ffmpeg`. For local scripts, pull the selected model with `ollama pull MODEL_NAME` and keep Ollama running.

Speech preparation is configured under `tts` in `config/providers.local.yaml` after setup (otherwise `config/production.yaml`). Add names or terms to
`pronunciation_overrides` using the exact written form as the key and a phonetic, listener-facing
form as the value. `inter_block_pause_ms` controls the short pause inserted between adjacent host
paragraphs. Long paragraphs are split at sentence boundaries using `max_chunk_words`, with
`sentence_pause_ms` inserted between those groups. Explicit `[PAUSE: ...]` directives are left
unchanged.

### ChatGPT Subscription via Codex

With the Codex CLI installed on PATH, run `./show setup`, choose `mixed`, select
`codex` for scripts, and choose `kokoro` for local speech (or `none` for scripts
only). Setup offers browser sign-in and lists models reported by Codex. This uses
your eligible ChatGPT plan's Codex allowance, not `OPENAI_API_KEY`. Speech remains
separate, and subscription limits or account credit policies still apply.

`./show codex-login` connects or reconnects the account. `./show codex-status`
checks sign-in, reported allowance, and the model catalog without generation.
`./show codex-logout` disconnects only MorningShow. Auth is stored in ignored,
owner-only `data/codex/home`, separate from your normal Codex installation. See
[production setup](docs/production-setup.md#chatgpt-subscription-via-codex) for
privacy, runtime requirements, and validation limitations.

### ElevenLabs Speech

The base configuration uses Voice ID `AkzTpEeeEWvyZf4umyCJ` (Nathaniel C) and `eleven_multilingual_v2`; setup can select a different provider or voice. Voice Library voices require a paid plan for API use. Creating a key or adding a voice does not remove that requirement. Setup accepts keys through hidden prompts, or you can put your key in `.env` at the project root (next to `show`):

```dotenv
ELEVENLABS_API_KEY=your-key-here
```

The file is loaded automatically, even when the command is launched from another directory. No shell export is needed. `.env.example` provides a blank template for new checkouts. Then run:

```bash
./show voice-preview
```

The preview prints a WAV path and does not run discovery, Ollama, or editorial verification. It uses ElevenLabs credits. `./show doctor` checks voice access without generating speech; the key needs Voices Read and Text to Speech permissions. Add Nathaniel C to My Voices if your account cannot access it. After the preview, run `./show morning` normally.

Never put the key in YAML, a profile, a command argument, or Git. `.env` is Git-ignored; keep it private (file permissions `600`). An explicitly set environment variable takes precedence over the file, including an empty value; unset an old `ELEVENLABS_API_KEY` export to use the file. Credentials are read without modifying process environment or executing/interpolating file contents. Edits take effect on the next command.

Only speech text is sent to ElevenLabs, not the profile or article source files. Hosted requests are billed and subject to ElevenLabs' retention policies; zero-retention mode is not enabled. Speech failures stop the episode, with no silent fallback or automatic paid-request retry. A timeout can occur after billing, so inspect account usage before retrying.

The adapter uses the [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), wraps 24 kHz mono 16-bit PCM in a real WAV container, and lets the existing mixer perform final resampling, loudness processing, music, and MP3 encoding. Larger sentence-aligned chunks and zero extra chunk pauses let the provider handle phrasing; explicit story pauses remain unchanged. Model/voice settings are under `tts.elevenlabs`, while voice IDs and speed remain under `tts`. The configured speed range is 0.7-1.2. To restore local speech, set `tts.engine: kokoro`, `tts.voice: af_bella`, and install `uv sync --locked --dev --extra tts`.

## Local Dependencies

`./show doctor` checks the required local tools and configuration:

- Python version
- writable `data/`
- `config/app.yaml`, `config/feeds.yaml`, and `config/production.yaml`
- SQLite state
- Ollama reachability and configured model, OpenAI credential presence (not billing/model eligibility), or Codex ChatGPT sign-in and model availability
- FFmpeg and FFprobe when audio generation is enabled
- TTS adapter availability when audio generation is enabled
- at least one enabled feed

The POC uses live public feeds by default. Automated tests use fixtures and fake model/audio adapters so orchestration can be verified without internet access or a live local model.

## Offline Fixture Mode

The normal morning command requires live feeds and the configured inference providers. For reproducible development tests, the pipeline also supports an explicit fixture mode:

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

The suite includes isolated script-only and MP3 fixture runs, a wheel-build check, real FFmpeg signal/bed-continuity tests, and a versioned editorial evaluation corpus. It does not need a running Ollama instance or download TTS weights. Pyright checks the whole project, with strict mode on configuration, LLM responses, URL validation, downloads, and network transport. CI enforces the lockfile, formatting, lint, type checks, and at least 90% branch-aware combined coverage. Install the `tts` extra to include the real PCM writer test; without those optional dependencies that test is skipped.

Every episode writes `performance.json` with monotonic stage durations and process/subprocess memory high-water marks. Model-call logs separate Ollama loading from token-generation time. These measurements do not include the external Ollama process in Python's RSS.

Reproducible performance and editorial checks:

`--live` and `--episode` use the configured speech provider. With ElevenLabs selected, they send text to ElevenLabs and consume credits. Only the default fixture benchmark is offline and free of inference charges.

```bash
# Three isolated full-pipeline fixture runs, without real inference.
uv run python -m morning_radio.benchmark --repeats 3

# Live Qwen/Bella evaluation and first-use/repeat inference measurements.
uv run python -m morning_radio.benchmark --live --repeats 2

# A full real-model/voice episode using fixed synthetic source material.
uv run python -m morning_radio.benchmark --episode
```

Benchmarks write to unique directories under `runs/benchmarks/` and never update your normal profile or story history. The episode benchmark retains its isolated workspace and diagnostics. `--live` supports `--thinking` and `--no-thinking` for controlled comparisons; normal episodes retain the model's default unless `llm.thinking` is explicitly set in `config/app.yaml`. First-use does not mean a guaranteed cold model load: the benchmark never unloads an already-running model.

LLM requests explicitly use an 8,192-token context by default (`llm.context_tokens`), and scoring considers at most six stories per request. This avoids relying on a smaller server default for multi-story prompts. Larger contexts use more memory; keep this setting conservative on a 16 GB machine.

The corpus contains synthetic, source-checkable expected outcomes, not independently human-graded listening evaluations. It tests multiple-interest selection, missing-topic days, duplicated/paraphrased events, shared entities, unsupported claims, source prompt injection, and the actual publication verifier.

See [Maintainer Guide](docs/maintainer-guide.md) for module responsibilities, configuration, failure handling, and testing conventions. See [Code Review](docs/code-review-2026-09-09.md) for the original findings and [Follow-up Results](docs/review-followup-2026-09-09.md) for fixes, measured verification, and remaining limitations.
