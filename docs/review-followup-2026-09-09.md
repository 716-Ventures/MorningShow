# Review Follow-up - 2026-09-09

## Resolution

All five engineering workstreams from the prior review are implemented:

1. **Continuous beds:** concatenate each explicitly delimited region before one overlay. Speech boundaries no longer restart music; explicit pauses retain their duration and carry the bed. A changing-pitch real-FFmpeg regression proves continuity and BED STOP behavior.
2. **Editorial evaluation:** ship a versioned synthetic corpus with explicit provenance. Test selected identities and uncovered interests, paraphrased/syndicated events, shared entities, repeated headlines on different dates, supported/unsupported claims, and source prompt injection. The live runner also invokes the actual publication verifier. Fixed weak-topic padding, incomplete multi-interest coverage reporting, and merges of identical headlines outside the time window. Strong deterministic topic relevance still protects against a bad zero model score.
3. **Network boundary:** connect only to validated numeric public IPs while retaining the original host for TLS, HTTP, and pooling. Feeds and articles share redirect checks. Reject private IPv4/IPv6, mixed DNS answers, unsupported encodings, malformed streams, oversized compressed or decoded bodies, and slow transfers beyond the total deadline. Tests simulate DNS rebinding and inspect the actual destination, TLS hostname, and Host header.
4. **Coverage and types:** expand doctor/dependency diagnostics, parser failures, feedback ownership, profile prompt interactions, configuration boundaries, and benchmark tests. Strict Pyright covers five external-boundary modules. Fix malformed model-list handling, missing binary deadlines, and secondary-voice validation. Raise the combined branch-aware coverage floor from 70% to 85%, without excluding failures or weakening type diagnostics.
5. **Performance measurement:** every run records stage timings and process/subprocess memory high-water marks. Model logs include load/generation durations and token counts. Add isolated full-pipeline fixture benchmarking, live Qwen/Bella microbenchmarks, and a full fixed-source real-adapter episode mode. Bound reusable title-token caches to 4096 immutable entries and avoid repeated bed/loudness work.

## Automated Verification

- **292 tests passed**, with no skipped or excluded failures.
- **89.32% branch-aware combined coverage**, above the new 85% floor.
- Previously thin areas now report: CLI 90%, dependency checks 93%, extraction 92%, feedback 93%, profile interview 99%.
- Ruff lint/formatting and Pyright pass. Strict mode covers the five external-boundary modules; the whole project remains type-checked.
- The lockfile remains reproducible; HTTPCore is now an explicit direct dependency because the project uses its public transport interfaces.

## Live Results

Local configuration: Qwen `qwen3:8b` (Q4_K_M), Kokoro `af_bella`, macOS/Python 3.12.14. Reports retain configuration and input fingerprints under `runs/benchmarks/`; no normal listener profile, asset, or episode history was changed.

- Three fixture pipeline runs: 1.31, 1.36, and 1.37 seconds; approximately 68-70 MiB Python process high-water RSS. This measures plumbing, not inference.
- Initial 15-case live evaluation: all cases passed with thinking disabled and enabled, on both first-use and repeat passes. Repeat model evaluation totals were 11.43 seconds and 10.31 seconds respectively. This small sequential comparison does **not** establish that disabling thinking improves speed or quality; the default was preserved.
- Expanded 20-case evaluation including the production publication gate: **20/20 passed twice**. Model evaluation totals were 63.16 seconds first-use and 49.40 seconds on repeat.
- Bella's fixed 16.575-second passage: 14.52 seconds first-use and 2.03 seconds on repeat in the expanded evaluation. Python peak RSS was approximately 1.93 GiB. Ollama separately reported approximately 4.93 GiB loaded for the model; these are not a simultaneous total-system memory measurement.
- Full real Qwen/Bella episode **`20260909T125153-29fc0b`** completed with all three synthetic source stories and no uncovered interests. The production verifier passed. FFprobe confirmed MP3, 44,100 Hz, stereo, 170.95 seconds. Wall time was 397.08 seconds: scoring 75.85, research 95.33, planning 91.38, writing 58.81, verification 36.93, synthesis 33.80, and mixing 4.91 seconds. Python peak RSS was approximately 2.40 GiB, excluding the Ollama server. Evidence is retained in `runs/benchmarks/review-episode-final/episode.json` and its isolated workspace.
- That episode used a requested five-minute budget with no music assets. Its actual audio length was 2 minutes 51 seconds, not five minutes. This acceptance establishes complete three-topic generation/publication, not exact requested runtime or human approval of the fallback prose. The writer's malformed host formatting triggered the documented grounded fallback; the time-budgeted fallback then passed the normal quality and factual gates. Real asset behavior is separately exercised by FFmpeg signal and continuous-bed tests.
- Tests and benchmark artifacts do not imply that arbitrary future model output is factual, that every news day has usable articles for every interest, or that subjective voice quality has human approval. The corpus is openly labeled as synthetic/agent-authored, not fabricated human evaluation.

## Additional Full-Pipeline Findings

The initial real-adapter episode exposed schema-valid all-zero scoring responses claiming that supplied source content was missing. The server was using a 4096-token context with a large 18-story scoring prompt, consistent with context loss. The client now explicitly requests 8192 tokens, batches scoring into groups of six, rejects missing/duplicate identities, and retains grounded local scores when a whole response reports no evidence. Regression tests cover these cases.

A short-episode acceptance run also revealed that the fixed 75-second topic slot could exclude the third available interest. Coverage slots now scale down with the actual budget, and the episode acceptance check requires all three supplied interests. Planning now respects an explicit minutes override instead of applying the profile's unrelated normal minimum duration and repeatedly rejecting the requested plan.

The next run exposed unbounded fallback story copy. The grounded fallback now shares the validator's per-story word limit, reserves room for uncertainty notes, and keeps a prefix of complete sentences. It never cuts a sentence mid-claim or skips an oversized lead to substitute unrelated details; an impossible lead-plus-caution budget still fails explicitly. Regression tests cover short slots, retained cautions, and oversized leads.

A real HTTPS request through the pinned transport and bounded downloader returned HTTP 200 from `https://www.python.org/`, in addition to the isolated DNS/TLS/redirect tests.

The full-episode source set is separate from the terse plumbing fixtures: three substantive, clearly labeled synthetic articles provide enough evidence for actual script generation and verification. Neither fixture set is presented as real current news.

## Reproduction

Implementation commits, pushed in focused batches: `87d1155` (beds), `9547b3c` (network/types), `b09b376` (evaluation/metrics/regressions), `8b906d2` (full-episode/scoring), `81697ea` (topic budgets/duration overrides), and `75af404` (fallback time budgets). Operator changes in `config/app.yaml`, `data/profile.json`, and `data/profile.md` were not included.

```bash
uv run python -m morning_radio.benchmark --repeats 3
uv run python -m morning_radio.benchmark --live --no-thinking --repeats 2
uv run python -m morning_radio.benchmark --live --thinking --repeats 2
uv run python -m morning_radio.benchmark --episode
uv run pytest --cov=morning_radio --cov-branch --cov-report=term-missing -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
```

Each benchmark defaults to a fresh output directory. An episode workspace is never overwritten. The full-episode benchmark uses synthetic sources and real inference, not today's actual news. First-use may encounter an already-loaded Ollama model; server load-duration metrics distinguish that from startup costs without disrupting other model users.

The remaining human task is listening and accepting the voice/editorial style. No code change can truthfully substitute for that sign-off. The implemented regression/evaluation and measurement tools make failures reproducible rather than claiming universal model correctness or unlimited optimization.
