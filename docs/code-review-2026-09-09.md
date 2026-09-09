# Project Code Review - 2026-09-09

## Scope and Verdict

Reviewed the CLI, configuration/domain schemas, persistence and artifacts, profile setup and feedback, LLM integration, every newsroom stage, script/rundown/verification, TTS preparation and mixing, tests, packaging, CI, and operator documentation. Concrete findings were fixed in focused commits with regression coverage. Existing operator edits to `config/app.yaml`, `data/profile.json`, and `data/profile.md` were intentionally left untouched.

The codebase now has a clean automated baseline and materially stronger failure handling and tests. This is not a claim that every branch is covered, that all original work was test-driven, or that the POC is production-certified. Changes during this review used failing regressions where practical; existing historical tests are not retroactively evidence of TDD. No broad dependency upgrades or framework replacements were needed.

## Fixed Findings

| Priority | Finding | Implemented correction and evidence |
| --- | --- | --- |
| P1 | Verifier exceptions/oversized context could be reported as successful verification. | Fail closed with a high-severity unavailable-verification result. `test_verify.py` covers error and oversized-context behavior. |
| P2 | Offline MP3 fixture failed because fake TTS was asked to use the real configured voice. | Explicit fake mode selects tone voices. Isolated E2E fixtures now cover both script-only and MP3 modes without exclusions. |
| P2 | Sources matching depended on spoken headlines that were intentionally removed. | Match final story bodies and preserve their script order. `test_sources.py` includes body-only copy. |
| P2 | Article/feed byte limits were enforced after downloading complete responses. | Bound streaming decoded bodies; close streams on failure. Article regression verifies early termination and cleanup. |
| P2 | DNS checks and article parsing blocked concurrent async extraction. | Offload those synchronous operations to threads while retaining the configured extraction semaphore. No end-to-end speedup percentage is claimed. |
| P2 | SQLite transaction contexts did not guarantee connection close; replacing run rows could disrupt references. | A context manager now owns close/commit/rollback. Run updates use UPSERT rather than replacement. Regression checks rollback and closed handles. |
| P2 | Workflow-owned Ollama HTTP pools could remain open. | Close concrete owned clients in pipeline/feedback `finally` blocks. Caller-injected clients remain caller-owned. |
| P2 | Invalid Ollama response envelopes could leak untyped errors or coerce non-text responses. | Validate envelope and response text; raise the typed model-response error. Tests cover list, null, and numeric payloads. |
| P2 | Text such as "not the same event" could merge separate stories. | Recognize explicit affirmative/negative decisions; ambiguous decision text remains invalid. |
| P2 | Asset containment used a string prefix, allowing sibling-directory symlink escapes. | Use resolved path ancestry, literal stem matching, and regular-file checks. Test covers `assets` versus `assets-private`. |
| P2 | `--no-assets` could retain an orphan bed-stop event. | Omit all asset events while retaining structural validation. Regression asserts a speech-only plan. |
| P2 | Targeted profile edits restarted the complete interview and lost the edit. | Collect once, then review/edit the current profile. Test asserts one collection and the exact saved edit. |
| P2 | Accepting setup defaults overwrote news/familiarity/exclusion preferences; variable duration boundaries could be invalid. | Reuse setup/edit collectors, preserve defaults, enforce a nonempty variable interval, and clamp dependent defaults. Tests cover every targeted category, cancel, retained preferences, numeric retries, and upper bounds. |
| P2 | CLI accepted out-of-range minutes; doctor accepted unsupported Python versions. | Enforce 5-90 minutes at CLI and pipeline boundaries; align doctor with Python 3.12/3.13 packaging support. |
| P2 | Temporary text files could leak when writing failed before cleanup knew the path. | Register the temporary path before writing; retain atomic replacement/cleanup. |
| P2 | Asset loudness was measured repeatedly, including once for validation and again for normalization. | Cache measurements by resolved path for one render only. A real-render test verifies one measurement when the same source fills multiple audio roles. |
| P2 | FFmpeg/FFprobe subprocesses had no wall-clock bound, and command logs relied on implicit closure. | Use 600/30-second timeouts, typed failures with diagnostic files, and explicit log contexts. Timeout regressions cover both subprocess paths. |
| P2 | Final duration validation ignored standalone music and bumpers. | Add their rendered durations to the speech/pause budget. Real FFmpeg regression renders 61 seconds of pause plus ten music clips that previously failed the 20% upper bound. |
| P2 | HTML escaping alone did not prevent executable source-link schemes. | Only HTTP(S) URLs with a host become links; other titles remain escaped text. Test covers `javascript:`. |
| P2 | Model dossiers could use the wrong cluster or sources not supplied in the prompt; alias restoration bypassed validation. | Enforce cluster identity and prompt-source membership; revalidate the restored dossier. Test covers colliding aliases. |
| P2 | Headline stripping could delete the subject from a real sentence sharing a short title. | Require a headline boundary; strip source boilerplate after recognizing the original boundary. Regression preserves "Apple announced..." while removing "Apple: ...". |
| P3 | Packaging test hardcoded one developer's executable path; formatting was inconsistent and unenforced. | Discover uv from PATH (with a user-install fallback), format the repository, and add CI format checks and locked sync. Coverage output is gitignored. |
| P3 | README incorrectly described MP3 generation as disabled. | Correct setup/runtime/output paths and document architecture, ownership, failure semantics, and test expectations. |

## Verification

Executed locally on macOS with Python 3.12.14 and real FFmpeg/FFprobe:

- Full suite: **207 passed**, no exclusions or skipped failures. Initial baseline was 176 passed and 2 failed.
- Branch-aware combined coverage: **83.67%**, above the existing 70% CI floor.
- Ruff lint and formatting checks: clean.
- Pyright: zero errors/warnings, using the project's existing **basic** mode, not strict mode.
- `uv lock --check`: passed; no dependency upgrade was required.
- Real audio integration verifies gain increase, bounded peaks, smooth test-tone signals through each rendered asset role, final encoding, measurement reuse, and music-aware duration accounting.

The FFmpeg checks use deterministic tones, not human speech. They catch signal-processing regressions but do not certify pronunciation, natural pacing, musical quality, or the user's actual recordings. No new live Ollama/Kokoro episode or full latency benchmark was run as part of this review. Hosted GitHub CI completion is not implied by local success. The wheel test may need network access to populate its build-backend cache on a fresh machine.

## Remaining Work and Limits

### P2: Bed Continuity

`audio/master.py` starts the bed again for each speech chunk and emits silence during explicit pauses. The behavior is documented, but it is not a continuous radio bed.

Fix direction: model one bed region over the production timeline, or retain a sample-accurate bed cursor across speech and pause items. Preserve the existing pause lengths and opening-only silence trimming. Add a non-repeating, time-varying test bed; assert it does not restart at speech boundaries and remains audible through pauses. Do not validate this with a constant sine alone, which cannot reveal restarts. This change was left separate from the gain/static safeguards while the operator checks source assets.

### P2: Editorial Quality Requires an Evaluation Corpus

Coverage and deduplication use lexical heuristics; source-ID validity is not proof that a claim follows from an article. Model verification can also make mistakes. Tests do not guarantee adequate coverage for all interests on every news day.

Fix direction: add a frozen, human-labeled corpus covering multiple interests, same-event paraphrases, distinct events sharing entities, missing-source days, and unsupported claims. Assert story identities and supported facts, not just word counts or model schema compliance. Keep live acceptance results keyed by run ID and model configuration.

### P2 Before Untrusted Hosting: Network Trust Boundary

Article redirect destinations receive public-address checks, but validation and HTTP connection resolution are separate operations. This is not DNS-rebinding-proof isolation. Configured feeds are operator-trusted inputs, not a hardened arbitrary-URL API.

Fix direction before accepting untrusted remote users: use an egress-restricted proxy or a transport that connects only to the validated addresses while preserving hostname/TLS checks, and apply the same policy to feeds and redirects. Test private IPv4/IPv6, redirects, rebinding, and encoded/compressed response limits. Do not present the local CLI's URL checks as a server security boundary.

### P3: Coverage and Type-Checking Depth

Overall coverage masks thinner areas: CLI 42%, dependency checks 47%, extraction 51%, and feedback 66%. Profile interview coverage improved to 73%, but not every interactive path is exercised. Basic Pyright also permits unknown/loosely typed boundaries.

Fix direction: add isolated doctor/dependency failure tests, redirect/decompression/parser failures, feedback-client ownership tests, and full prompt interaction tests. Tighten types module-by-module, starting at external JSON boundaries; do not disable diagnostics or add blanket `Any` merely to advertise strict mode. Raise the coverage floor only after covering meaningful paths.

### P3: Runtime Performance Has Not Been Fully Profiled

This review removes repeated measurement work and event-loop blocking, but model generation and synthesis may still dominate on a 16 GB machine. There is no evidence for an "as fast as possible" claim.

Fix direction: record stage timings for a fixed candidate corpus and script, separate cold model loads from warm inference, and report wall time plus peak memory. Compare changes under identical model, voice, input, and machine conditions before introducing parallel inference, persistent synthesis caches, or model/provider changes.

## Maintenance Decision

Keep the current small Python architecture, validated models, adapters, and lockfile. Prefer targeted regression tests and measured improvements over cosmetic abstraction or dependency churn. Read the maintainer guide before changing persistence, publication verification, or audio timing contracts.
