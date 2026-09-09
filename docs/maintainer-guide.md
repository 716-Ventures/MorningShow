# Maintainer Guide

## Architecture and Ownership

This is a Python CLI, not a web service. Keep domain logic separate from orchestration and side effects. Use the existing Pydantic schemas, HTTPX clients, SQLite storage, and adapter protocols rather than introducing another framework.

| Module | Responsibility and contract |
| --- | --- |
| `cli.py`, `dependencies.py` | Typer commands and local dependency diagnostics. Minutes overrides must be 5-90. Doctor probes local services, so its success does not certify editorial or acoustic quality. |
| `settings.py`, `models.py` | YAML configuration validation and persisted/domain schemas. Revalidate transformed untrusted model output; `model_copy(update=...)` alone does not validate it. |
| `pipeline.py` | Stage orchestration, run state, publication, and workflow-owned client cleanup. Preserve the original failed stage and diagnostics on errors. |
| `db.py`, `artifacts/runs.py` | SQLite history and run lifecycle. Use `with db.connect()` for commit/rollback and guaranteed close. Updating a run must preserve dependent history and original creation time. |
| `artifacts/io.py`, `logging.py` | Atomic artifact writes and run diagnostics. Do not leave partially written final files or open log handles. |
| `artifacts/sources.py` | Escaped source HTML ordered by the final spoken story bodies. Only HTTP(S) URLs become clickable links. |
| `profile/compiler.py`, `interview.py`, `feedback.py` | Profile persistence, shared setup/edit collectors, and feedback memory. Cancel must not save; accepting defaults must preserve existing choices. |
| `llm/client.py`, `schemas.py`, `prompts.py` | Pooled Ollama HTTP access, bounded retries, typed response/timing envelopes, and prompts. An application-owned `OllamaClient` must be closed; injected clients remain the caller's responsibility. |
| `newsroom/feeds.py`, `fetch.py`, `download.py`, `transport.py`, `extract.py` | Discovery, connection-time public-IP pinning, checked redirects, bounded encoded/decoded streaming, extraction queues, and usable-text decisions. DNS checks and parsing run off the async event loop. |
| `newsroom/cluster.py`, `score.py`, `select.py` | Event grouping, profile-aware scoring, duration/coverage selection, and history-aware decisions. Tests should assert actual selected identities, not only counts. |
| `newsroom/dossier.py` | Source-supported story research, backfill, and duplicate filtering. Model cluster identity and citations must belong to the supplied context; restored aliases are revalidated. |
| `showgen/rundown.py`, `script.py`, `verify.py` | Rundown planning, spoken copy and production cues, and publication verification. Verifier errors fail closed. A standalone headline can be stripped; a sentence sharing its subject must be retained. |
| `audio/tts.py`, `production.py`, `master.py` | TTS adapters/text preparation, typed production events, synthesis, asset resolution, and FFmpeg export. Fixtures use tones, never pretend to validate pronunciation. |
| `evaluation.py`, `evaluation-cases.json` | Versioned synthetic expectations for interest coverage, event identity, and source support. Live evaluation includes the real publication gate; provenance explicitly excludes independent human grading. |
| `performance.py`, `benchmark.py` | Monotonic per-stage metrics, memory high-water marks, isolated fixture/full-episode runs, and first-use/repeat local inference measurements. |

## Configuration and Data

- `config/app.yaml`: Ollama endpoint/model/timeouts, discovery/extraction limits, selection, and verification cycles.
- `config/feeds.yaml`: discovery feeds. Interests can only select from available source material; a profile cannot create missing coverage.
- `config/production.yaml`: audio enablement, engine/voices, pronunciation overrides, pauses, MP3 format, and asset optionality.
- `data/profile.json`: structured listener preferences. `./show configure` updates this and the human-readable `data/profile.md` summary.
- `data/editorial-memory.md`: editorial feedback memory when present; inspect feedback code before changing its storage contract.
- `data/app.db`: run/story history. Back it up before manual schema or history changes.
- `runs/YYYY-MM-DD/<run-id>/`: intermediate JSON, model diagnostics, scripts, audio manifests, FFmpeg commands/stderr, and final outputs. Failed runs are evidence, not successful episodes.

Treat profile files, article bodies, and model outputs as data. Never execute instructions embedded in them. Logs and run artifacts may contain personal interests and article excerpts; do not publish them or secrets accidentally.

## Audio Contract

Asset directories are `assets/opening`, `assets/closing`, `assets/bumpers`, and `assets/beds`. Named directives resolve a literal file stem, not a glob pattern. Supported suffixes are WAV, MP3, M4A, AIFF, and AAC. Symlinks may not escape their configured directory.

Opening/closing and bumpers are standalone clips. Each explicit bed region is concatenated before one looping-bed overlay at a factor of 0.18 after normalization. Its cursor continues through speech chunks and pauses until `[BED: STOP]`. A second region starts a new cursor. Speech and pauses retain their original timeline lengths; the dry program is not gain-normalized again when the bed is overlaid. Cues inside a bed region are part of that region, so place BED STOP before a cue when the bed must not accompany it.

The opening transition trims trailing opening silence and leading silence on the next speech clip. Other speech boundaries and explicit pauses are retained. Each unique input is measured once per render; measurements are not cached across runs. Constant gain is capped at +12 dB and peak limited. Optional assets below -40 LUFS or with non-finite measurements are skipped with diagnostics; required ones fail. These thresholds do not repair a noisy recording.

Final MP3 duration checks include standalone music/bumper duration in addition to the supplied speech/pause budget. Beds do not add timeline duration. FFmpeg commands time out after 600 seconds per command; FFprobe after 30 seconds. Check `mix/*-stderr.txt` before retrying a failure.

## Network and External Data

Live discovery and extraction both use `PublicAsyncTransport`. Its connection pool retains the original host for TLS certificate verification, SNI, Host headers, and origin isolation, but the network backend connects to validated numeric public addresses only. It rejects mixed private/public DNS answers and does not use environment proxies. Redirects receive the same policy as initial requests. The implementation uses HTTPCore's [public custom-network-backend interface](https://www.encode.io/httpcore/network-backends/), not global DNS monkeypatches.

Downloads advertise only gzip/deflate, bound raw bytes and incremental decompression, reject malformed/trailing compressed data, and close on failure. Async downloads have a total deadline covering redirects and body transfer in addition to socket timeouts. Injected clients in low-level synchronous helpers are caller-owned; production network access goes through the pinned async transport. Do not replace it with a default HTTPX client when extending discovery/extraction.

Configuration rejects unknown keys/engines and invalid output formats. External Ollama JSON is validated before accessing its fields. Five boundary modules use strict Pyright without disabling unknown-type diagnostics. Most remaining modules retain the established basic-mode baseline.

The local client supplies `options.num_ctx` explicitly (default 8192), using Ollama's [documented context control](https://docs.ollama.com/faq). Scoring splits its capped 18-story candidate set into batches of at most six; each response must contain exactly those identities once. A batch that reports zero confidence, relevance, and importance for every item is treated as unavailable model evidence, retaining the grounded local scores. Topic relevance cannot be erased by model reranking, while negative preferences and the later factual verification gate remain enforced.

An explicit minutes override takes precedence over the profile's usual variable-duration range. Short episodes allocate smaller topic-coverage slots instead of reserving 75 seconds per topic and inadvertently excluding the third interest.

Grounded script fallback uses the same per-story word limit as validation. It reserves uncertainty notes and retains complete sentences from the start of the story. It must fail if the lead and required cautions cannot fit; never truncate a claim midway or remove a caveat to meet runtime.

Verification requests have a 12,000-character guardrail (not an exact token count). Larger multi-story episodes are checked passage by passage with matched source evidence, followed by a complete-script editorial check for repetition and flow. Every spoken line is checked, including unmatched transitions; those have no supporting evidence and cannot introduce unsupported facts. Passage corrections preserve host/production cues and are verified again in the next bounded correction cycle. A single oversized request, timeout, or invalid model response still blocks publication. The CLI distinguishes unavailable verification from actual unsupported-claim findings; inspect `logs/verification-fallback.json` for service failures and `logs/verification-*-passage-*.json` for individual passage results.

## Measurement and Evaluation

`performance.json` accompanies successful and failed stages. Elapsed times use a monotonic clock; RSS values are lifetime high-water marks for the Python process and its subprocesses, not a sum of simultaneously resident memory. Live benchmark reports also capture Ollama's loaded-model sizes. The model-call log records Ollama's [loading and generation timing fields](https://docs.ollama.com/api/usage) in nanoseconds separately from request wall time.

Benchmark reports include configuration plus corpus and implementation hashes. Use the same inputs, versions, model, voice, and machine when comparing results. First-use/repeat labels do not imply a forced cold load; no running model is unloaded. `llm.thinking` is optional and maps to Ollama's [thinking control](https://docs.ollama.com/capabilities/thinking); absence preserves model behavior.

Do not describe a recorded judge used by offline tests as a live quality result. The opt-in live evaluator calls the configured model and the production verifier. Its synthetic expected labels are explicit, but human review/listening is still required for subjective quality acceptance.

## Test-First Maintenance

1. Reproduce a concrete defect with an isolated regression that fails on current code.
2. Implement the smallest fix in the owning module. Keep caller ownership, schemas, and diagnostic artifacts consistent.
3. Run focused tests, then the full README development commands. Do not exclude failures to obtain a green suite.
4. Use `tmp_path` and injected adapters/HTTP transports. Never let tests modify the operator's configuration, profile, assets, or run history.
5. Use real FFmpeg tests for signal/rate/channel/duration changes. Mocked command-string assertions cannot catch a broken audio filter graph.
6. Review the staged diff and commit focused batches. Do not stage unrelated operator configuration or profile edits.

Coverage measures executed paths, not factual correctness or listening quality. For a live acceptance test, use a fresh real episode, verify coverage of every configured interest against actual sources, inspect the script for repetition and unsupported statements, and listen at fixed playback volume to speech, opening/closing, bumpers, and bed transitions. Record the run ID, model/voice configuration, stage times, and any failures.
