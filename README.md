# Personal Morning Radio

Personal Morning Radio is a local-first command-line app that turns news matching your interests into a radio-style episode, with a written script, source links, and optional speech and music. Choose local models, hosted providers, or a combination of both.

**Developer alpha:** expect setup work and occasional failed runs. Generated scripts can contain mistakes; review the sources before relying on or sharing an episode. Automated tests do not establish editorial or voice quality.

## Quick Start

The intended platform is **macOS on Apple Silicon**, with Python 3.12 or 3.13. Python 3.14 is not supported. These instructions use Python 3.12 and assume Git, Homebrew, and `uv` are installed. Run commands from the repository root:

```bash
git clone https://github.com/716-Ventures/MorningShow.git
cd MorningShow
uv sync --locked --dev --python 3.12
./show setup
```

Setup chooses **script generation and audio output separately**. Cloud scripts do not automatically enable local speech. Select `kokoro` for local audio, `elevenlabs` or `vercel` for hosted audio, or `none` for scripts without an MP3. The review confirms what will be produced.

Install the dependencies for your choices before continuing:

- **Any MP3 output:** `brew install ffmpeg` installs FFmpeg and FFprobe.
- **Local Kokoro speech:** `uv sync --locked --dev --extra tts --python 3.12`. Model files may download on first use; local inference does not require API credits.
- **Local Ollama scripts:** install and start Ollama, then run the `ollama pull` command printed by setup for your selected model.
- **Codex scripts:** install the Codex CLI on PATH before selecting it in setup; complete browser sign-in when prompted.
- **API-backed providers:** enter the corresponding key in setup and ensure your account has access and sufficient credits. Creating a key alone is not enough.

Then check the installation, configure your interests, and create an episode:

```bash
./show doctor
./show configure
./show morning --minutes 10 --no-assets
```

Generated scripts and intermediate artifacts are written under `runs/YYYY-MM-DD/<run-id>/`, not `data/runs/`. The final command prints the episode and sources paths for audio runs, or script and sources paths for script-only runs.

`--no-assets` skips music, bumpers, and beds; it does not disable speech synthesis or MP3 export. The requested duration is a target, not a guaranteed episode length. Run `./show morning` again to create another run using your saved preferences.

## Provider Setup

| Stage | Choices |
| --- | --- |
| Scripts and editorial processing | Local Ollama, OpenAI API, ChatGPT via Codex, Vercel AI Gateway |
| Speech | Local Kokoro, ElevenLabs, Vercel AI Gateway (beta), or none |
| Music mixing and MP3 export | Local FFmpeg |

`mixed` shows all script providers together; audio remains an independent choice in every mode. Use `./show providers` to inspect options without changing settings. Use `./show configure` to change interests, not providers.

Setup saves ignored machine-specific preferences in `config/providers.local.yaml`; these override the base `llm`, `tts`, and `generate_audio` settings. Re-run setup to change providers or disable audio. The first interactive generation opens setup automatically; unattended generation requires completing setup first. Your editorial profile is unchanged. See [production setup](docs/production-setup.md) for the provider catalog, recommendation policy, credential handling, and billing limitations.

Speech preparation is configured under `tts` in `config/providers.local.yaml` after setup (otherwise `config/production.yaml`). Add names or terms to
`pronunciation_overrides` using the exact written form as the key and a phonetic, listener-facing
form as the value. `inter_block_pause_ms` controls the short pause inserted between adjacent host
paragraphs. Long paragraphs are split at sentence boundaries using `max_chunk_words`, with
`sentence_pause_ms` inserted between those groups. Explicit `[PAUSE: ...]` directives are left
unchanged.

### Jev Editorial Evaluation

Optional Jev shadow mode compares duplicate-story decisions and interest relevance
without changing your episodes. It uses TypeSafe's cloud API independently of
script and speech providers, adds evaluation time and API usage, and is off by
default. Put `TYPESAFE_API_KEY` in `.env` and follow the
[setup and evaluation guide](docs/jev.md). Results stay in ignored run artifacts.

### Vercel AI Gateway

Run `./show setup` and choose **cloud → vercel** for scripts. For audio, choose
**vercel** for gateway speech, **kokoro** for local speech, or **none** for no MP3.
Setup offers text models, speech models, and voices, then prompts once for
`AI_GATEWAY_API_KEY` and saves it in ignored `.env` on confirmation.

Gateway speech is beta and may not be enabled for your Vercel team. Both text and
speech use gateway credits, not your ChatGPT subscription. `./show doctor` checks
credential presence only; `./show voice-preview` generates billable speech.
See [gateway setup and limitations](docs/production-setup.md#vercel-ai-gateway).

### ChatGPT Subscription via Codex

With the Codex CLI installed on PATH, run `./show setup`, choose `cloud`, select
`codex` for scripts, and choose `kokoro` for local speech (or `none` for scripts
only, with no MP3). Audio is always a separate choice, even with cloud scripts.
Setup offers browser sign-in and lists models reported by Codex. This uses
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

The adapter wraps 24 kHz mono 16-bit PCM in a WAV container, and the local mixer handles music, loudness processing, and MP3 encoding. Model settings are under `tts.elevenlabs`; voice and speed remain under `tts`. To restore local speech, re-run setup, choose Kokoro and Bella, and install the `tts` extra using the Quick Start command.

### Keys and Privacy

OpenAI uses `OPENAI_API_KEY`, Vercel uses `AI_GATEWAY_API_KEY`, and ElevenLabs uses `ELEVENLABS_API_KEY`. Setup accepts these through hidden prompts and saves them to ignored `.env` only after confirmation. See [.env.example](.env.example) for the supported names. Do not share `.env` or `data/codex/`.

The entire `data/` directory is private runtime state and is Git-ignored, including
profiles, editorial feedback, backups, databases, and auth files. `.env.*` files
are also ignored except the blank `.env.example`. New checkouts must run
`./show configure`; illustrative profiles live separately in [examples/](examples/README.md).
Existing users should back up `data/` before updating across this change: Git may
remove previously tracked, unmodified starter files during checkout. Restore
personal backups into `data/` afterward; do not force-add runtime files to Git.

Hosted text generation sends article and relevant editorial context to the selected service. Hosted speech sends speech text. Run artifacts can contain personal preferences and source material; review diagnostics before sharing them.

`./show voice-preview` uses the configured speech provider even when normal episode audio is disabled. Hosted previews consume credits. MorningShow does not automatically retry failed speech requests or hosted text HTTP failures, but OpenAI and Vercel schema-validation failures can trigger up to three billable attempts. Provider-side retry policies are separate; a timeout may occur after billing.

## Audio Assets

Optional music lives under `assets/opening/`, `assets/closing/`, `assets/bumpers/`, and `assets/beds/`. Run `./show morning` without `--no-assets` to include applicable assets. See [Audio Assets](assets/README.md) for export-level guidance.

## Troubleshooting

- **No MP3:** inspect the saved audio choice. `none` means scripts only.
- **A short tone:** unset `MORNING_RADIO_FAKE_TTS` and the other fixture variables before a normal run.
- **Hosted-provider errors:** check the key, credits, model or voice access, and rate limits before retrying a paid request.
- **Failed episode:** use the printed run ID, failed stage, and suggested diagnostics to inspect the correct directory. A failed run may not contain an MP3.

Run `./show --help`, `./show morning --help`, or `./show feedback --help` for command options.

## Local Dependencies

`./show doctor` checks the required local tools and configuration:

- Python version
- writable `data/`
- `config/app.yaml`, `config/feeds.yaml`, and `config/production.yaml`
- SQLite state
- Ollama reachability and configured model, OpenAI or Vercel credential presence (not billing/model eligibility), or Codex ChatGPT sign-in and model availability
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
uv sync --locked --dev --extra tts --python 3.12
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest --cov=morning_radio --cov-branch --cov-report=term-missing -q
```

The suite includes isolated script-only and MP3 fixture runs, a wheel-build check, real FFmpeg signal/bed-continuity tests, and a versioned editorial evaluation corpus. It does not need a running Ollama instance or download TTS weights. Pyright checks the whole project, with strict mode on configuration, LLM responses, URL validation, downloads, and network transport. CI enforces the lockfile, formatting, lint, type checks, and at least 90% branch-aware combined coverage. Install the `tts` extra to include the real PCM writer test; without those optional dependencies that test is skipped.

Every episode writes `performance.json` with monotonic stage durations and process/subprocess memory high-water marks. Model-call logs separate Ollama loading from token-generation time. These measurements do not include the external Ollama process in Python's RSS.

Reproducible performance and editorial checks:

`--live` currently requires Ollama for scripts. Live benchmarks and `--episode` can consume hosted-provider credits, including Vercel or ElevenLabs speech. These are opt-in developer tools, not cross-provider acceptance tests. Only the default fixture benchmark is offline and free of inference charges.

```bash
# Three isolated full-pipeline fixture runs, without real inference.
uv run python -m morning_radio.benchmark --repeats 3

# Live Ollama evaluation; speech uses the configured provider.
uv run python -m morning_radio.benchmark --live --repeats 2

# A full real-model/voice episode using fixed synthetic source material.
uv run python -m morning_radio.benchmark --episode
```

Benchmarks write to unique directories under `runs/benchmarks/` and never update your normal profile or story history. The episode benchmark retains its isolated workspace and diagnostics. `--live` supports `--thinking` and `--no-thinking` for controlled comparisons. Normal Ollama episodes use `llm.thinking` from the effective configuration: local provider preferences override `config/app.yaml`. First-use does not mean a guaranteed cold model load: the benchmark never unloads an already-running model.

Ollama requests use an 8,192-token context by default (`llm.context_tokens`); this setting does not control hosted-provider context limits. Scoring considers at most six stories per request. Larger local contexts use more memory; keep this setting conservative on a 16 GB machine.

The corpus contains synthetic, source-checkable expected outcomes, not independently human-graded listening evaluations. It tests multiple-interest selection, missing-topic days, duplicated/paraphrased events, shared entities, unsupported claims, source prompt injection, and the actual publication verifier.

See [Maintainer Guide](docs/maintainer-guide.md) for module responsibilities, configuration, failure handling, and testing conventions. See [Code Review](docs/code-review-2026-09-09.md) for the original findings and [Follow-up Results](docs/review-followup-2026-09-09.md) for fixes, measured verification, and remaining limitations.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE)
(SPDX: `Apache-2.0`). Third-party dependencies, model weights, news content, and
user-supplied audio assets remain subject to their respective licenses and terms.
