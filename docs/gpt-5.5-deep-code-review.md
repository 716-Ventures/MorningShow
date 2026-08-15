# Deep Code Review and GPT-5.5 Remediation Brief

Review date: 2026-08-15  
Reviewed branch: `agent/personal-morning-radio-poc` at `3ff8246`  
Specification reviewed: `vector-sigma-personal-morning-radio-poc.zip`, including all Canon sections and POC-001 through POC-025  
Review scope: correctness, specification coverage, safety, code hygiene, modern Python practices, performance, testing, and operational readiness

## Executive result

The current repository is a useful skeleton and narrow vertical slice, but it is not a complete implementation of the execution package and must not be represented as one. The default lint configuration passes and the small unit suite passes, but the suite does not exercise most ticket acceptance criteria. The sole end-to-end test is skipped on this machine because FFmpeg is absent.

The most serious defects are behavioral, not cosmetic:

1. Audio synthesis receives the unverified draft instead of `script-final.md` after a correction cycle.
2. The mixer silently ignores every music, bumper, and bed operation in the production plan.
3. Source-grounding validation accepts facts with no supporting candidate IDs.
4. Redirect safety is checked only after `httpx` has already followed the redirect, so it does not prevent a request to a private destination.
5. Final MP3 duration validation implements 20%-180% of plan instead of the required +/-20%, and accepts one-second output instead of requiring more than 60 seconds.

GPT-5.5 should fix the findings in the implementation order below. Do not collapse ticket responsibilities into one LLM prompt, weaken acceptance checks to make tests pass, or mark POC-024 complete without a real target-machine run.

## Measured baseline

Commands executed during this review:

```text
uv run pytest -q --durations=20
uv run ruff check .
uv run ruff check . --select ALL --output-format concise
uv build
./show doctor
```

Observed results:

- Default Ruff configuration: pass.
- Tests: 23 passed, 1 skipped; 24 tests collected.
- E2E: the only E2E test skipped because FFmpeg/FFprobe are unavailable.
- Build: sdist and wheel build successfully.
- Strict Ruff experiment: 682 findings repository-wide, 544 under `src/`. Most are documentation/style rules, but it also identifies complexity, missing annotations, blanket ignores, and implicit namespace packages.
- Doctor: Python/config/SQLite/feed-count checks pass. Ollama, `qwen3:30b`, FFmpeg, FFprobe, and Kokoro are unavailable.
- No `tests/integration/` or `tests/fixtures/` corpus exists.
- The real Mac Studio smoke test is explicitly recorded as not run.

The strict Ruff count is diagnostic, not a recommendation to enable every Ruff rule. Configure a deliberate rule set instead of mechanically chasing all 682 messages.

## Required implementation order

1. Correct the final-script, grounding, verification, and SSRF defects before generating any more audio.
2. Replace the audio plan/mixer boundary so all planned operations are actually rendered and validated.
3. Implement the missing newsroom behavior: concurrent intake, real clustering, scoring memory, balanced selection, and dossier backfill.
4. Make rundown/script duration contracts deterministic and enforce the CLI override throughout the pipeline.
5. Complete feedback/editorial memory, repeat-story memory, fixtures, integration tests, and E2E failure scenarios.
6. Harden types, error handling, logs, packaging, and performance.
7. Install the real local stack, run POC-024, then conduct POC-025. Do not reverse these last two steps.

## P0: correctness, factual integrity, and safety

### PMR-001: Synthesis uses the draft after verification corrections

Evidence: `src/morning_radio/pipeline.py:104-121` stores the draft in `script`, calls `verify_script`, and then passes the same `script` value to `synthesize_script`. `src/morning_radio/showgen/verify.py:20-35` may write a different corrected script to `script-final.md`, but returns only `VerificationResult`.

Impact: a verifier can correctly remove an unsupported claim, write a passing final script, and the application will still speak the unsupported draft. This defeats the central factual-safety gate.

Fix directions:

1. Introduce a typed result such as `VerifiedScript` containing both `verification: VerificationResult` and `script: str`.
2. Make `verify_script` return the exact text that passed the final verification cycle.
3. Validate that exact text immediately before writing `script-final.md`.
4. In `pipeline.py`, replace the draft variable after verification and pass only the returned verified text to production planning and TTS.
5. Make production planning read or receive the final script explicitly; use names such as `draft_script` and `final_script` so accidental reuse is obvious.

Required tests:

- Add an integration test where cycle one returns a corrected sentence and cycle two passes. Assert the WAV manifest text hash corresponds to the corrected sentence and not the draft.
- Assert `production-plan.json` represents `script-final.md` exactly.
- Assert a failed verification creates no WAV and no MP3.

Done when: no path from a failed or superseded draft can reach TTS.

### PMR-002: Dossier grounding accepts empty and meaningless citations

Evidence: `src/morning_radio/models.py:205-223` permits an empty `supporting_candidate_ids` list and empty facts. `src/morning_radio/newsroom/dossier.py:102-105` checks only set inclusion, for which an empty set always passes. It also does not require each dossier `source_id` to be used by a fact or each fact claim to be nonblank.

Impact: the model can produce uncited facts while the dossier is marked safe, allowing unsupported material into the script.

Fix directions:

1. Add Pydantic constraints: nonblank claim, at least one supporting ID per fact, at least one meaningful fact for a safe dossier, and nonempty `source_ids` for a safe dossier.
2. Add a model-level validator to `StoryDossier` that enforces `safe_for_scripting=True` only when all grounding invariants hold.
3. Change `_valid_source_ids` to require every fact ID to be known and every fact to have at least one ID.
4. Reject duplicate IDs and optionally normalize order while preserving deterministic output.
5. Persist unsafe model output with a clear reason, but never append it to the scripting list.

Required tests:

- Empty citation list is rejected.
- Unknown citation ID is rejected.
- Blank fact is rejected.
- Dossier with no facts is unsafe.
- A valid multi-source dossier passes.

Done when: every scripted factual claim in every dossier has at least one real extracted candidate reference.

### PMR-003: Redirect SSRF defense runs after the unsafe request

Evidence: `src/morning_radio/newsroom/extract.py:35-60` creates a client with `follow_redirects=True`, then checks `response.url` only after the client has completed the redirect chain. `src/morning_radio/newsroom/fetch.py:12-26` validates DNS results, but cannot prevent a redirect already followed by `httpx`.

Impact: a public article URL can redirect the process to loopback, link-local, or private network services. The check may label the result failed after the private request has already occurred.

Fix directions:

1. Set `follow_redirects=False` for extraction requests.
2. Follow redirects manually with a small configured maximum, such as five.
3. Resolve and validate each next URL before issuing its request, including relative `Location` headers via `urljoin`.
4. Reject URLs containing credentials and reject malformed ports.
5. Resolve all A and AAAA records for each hop and reject non-global addresses using a clearly tested policy.
6. Consider DNS rebinding: connect using a transport strategy that preserves validation, or document the residual single-user POC risk and re-resolve immediately before each request.
7. Keep a visited URL set to detect redirect loops.

Required tests:

- Public URL redirecting to `127.0.0.1` is rejected before the second request.
- Public URL redirecting to RFC1918 IPv4, IPv6 loopback, link-local, and metadata-like destinations is rejected.
- Safe relative redirect succeeds.
- Redirect loop and redirect limit produce `fetch_failed` artifacts.

Done when: the HTTP mock proves no request is sent to the unsafe redirect destination.

### PMR-004: The mixer drops music, bumpers, and beds

Evidence: `src/morning_radio/audio/production.py:45-122` writes `music`, `bumper`, `bed_start`, and `bed_stop` operations. `src/morning_radio/audio/master.py:40-47` consumes only `speech` and `pause`; all other operations are silently ignored.

Impact: a run can report a successful produced episode while omitting every production asset. Bed state and gain/fade requirements are not implemented at all.

Fix directions:

1. Replace `list[dict]` with a discriminated Pydantic union for all six production item types.
2. Make the master exhaustively dispatch every item type. Unknown types must raise `AudioMasterError`.
3. Normalize every source to configured sample rate/channel layout before assembly.
4. Render opening, closing, and bumpers sequentially.
5. Convert active bed ranges into FFmpeg filter-graph inputs mixed under speech at a configurable gain.
6. Add conservative fades for opening, closing, and bed boundaries.
7. Record the exact FFmpeg command/filter graph and stderr in `mix/` for diagnosis.
8. Ensure `--no-assets` removes asset operations while retaining speech and pauses.

Required tests:

- Speech-only, opening, bumper, bed-under-speech, pause, and closing fixtures.
- Assert each supplied asset measurably changes output duration or signal.
- Assert an unknown plan operation fails instead of disappearing.
- Assert bed gain is lower than host signal and transitions do not clip.

Done when: every operation in `production-plan.json` is either rendered or explicitly marked skipped before mixing.

### PMR-005: Final audio validity thresholds are materially wrong

Evidence: `src/morning_radio/audio/master.py:113-123` accepts duration greater than one second and checks a range of `planned * 0.2` through `planned * 1.8`. POC-017 requires duration over 60 seconds and within +/-20%, which is `0.8` through `1.2`.

Impact: severely truncated or almost double-length episodes can be marked valid.

Fix directions:

1. Require final duration strictly greater than 60 seconds for real episodes.
2. Check `planned_seconds * 0.8 <= duration <= planned_seconds * 1.2`.
3. Keep a separate, explicit fixture-only threshold only if tests need sub-minute synthetic audio; do not disable validation through an environment branch in production code.
4. Validate sample rate, channel count, codec, bitrate presence, and finite numeric duration from FFprobe.
5. Add peak/clipping validation or parse loudness-normalization output to verify the master met its target.

Required tests:

- Reject 1 second, 59 seconds, 79% of plan, and 121% of plan.
- Accept 60-plus-second outputs at 80%, 100%, and 120% boundaries as defined by the final chosen inclusive policy.
- Reject missing or nonnumeric FFprobe duration.

Done when: the validator implements POC-017 literally and cannot be bypassed by fixture mode.

### PMR-006: Verification can pass internally inconsistent or unvalidated output

Evidence: `src/morning_radio/models.py:251-254` allows `status="pass"` with high-severity issues or `corrected_script_required=True`. `src/morning_radio/showgen/verify.py:24-27` writes `corrected_script` on a pass without validating it. Deterministic checks cover only script structure, URLs, and unsafe dossiers; the verifier is not given extracted source artifacts, profile, or rundown as required by Canon.

Impact: malformed verifier output can pass; a corrected script can introduce invalid directives; the verification prompt lacks required evidence and editorial context.

Fix directions:

1. Add model-level consistency validation to `VerificationResult`: pass means no unresolved high issue and no required correction.
2. Pass profile, rundown, selected extraction evidence, and dossiers to the verifier.
3. Validate every corrected script before another model cycle and before final persistence.
4. If a verifier returns pass plus corrected text, run a fresh verification cycle on that corrected text rather than accepting it immediately.
5. Add deterministic checks for rundown story coverage, duplicate full-story coverage, forbidden URL/directive/list syntax, host markers, enabled format sections, and numeric/date tokens that can be compared against dossier facts.
6. Persist each correction response and the final decision without overwriting audit evidence.

Required tests:

- Pass with high issue is rejected by schema.
- Invalid corrected directive is rejected.
- A correction that introduces a new claim is reverified.
- Profile/rundown/extraction evidence is visible in the fake verifier payload.

Done when: only the exact script that passed a complete, internally consistent verification can become final.

## P1: missing ticket behavior

### PMR-007: Story clustering does not implement the required algorithm

Evidence: `src/morning_radio/newsroom/cluster.py:20-60` groups stories by the alphabetically first three title tokens. It has no similarity score, time blocking, high-confidence merge, ambiguous LLM adjudication, union-find, publisher-suffix normalization, or conservative follow-up handling. `SameEventDecision` exists in `llm/schemas.py` but is unused.

Impact: unrelated stories sharing early alphabetic terms can merge, while semantically identical headlines with different vocabulary remain separate. Story history fingerprints inherit these errors.

Fix directions:

1. Normalize publisher suffixes and punctuation, retain high-information terms, and calculate token similarity.
2. Block pairs using similarity/shared uncommon terms plus publication-time proximity.
3. Directly merge only exact and very high-confidence pairs.
4. Send ambiguous blocked pairs to `LLMClient.generate_structured(..., SameEventDecision)`.
5. Build transitive components with union-find.
6. Generate a stable fingerprint from normalized canonical event identity, not cluster sequence.
7. Make output ordering deterministic before assigning display IDs.

Required tests: every POC-008 fixture case, including same event/different headline, same company/different event, next-day follow-up, syndication, singleton, and fake-LLM ambiguity.

### PMR-008: Feed discovery and extraction ignore configured concurrency and feed priority

Evidence: `src/morning_radio/newsroom/feeds.py:73-117` fetches enabled feeds serially and can exhaust the global candidate cap before later feeds run. `src/morning_radio/newsroom/extract.py:26-40` ranks only by category-hint count/date presence and fetches serially. `NewsSettings.concurrency` and `FeedConfig.priority` are not used.

Impact: ordinary runs waste network time and systematically bias candidates toward config order. On 150 articles at a 15-second timeout, worst-case serial latency is unacceptable.

Fix directions:

1. Use `httpx.AsyncClient` with one shared client and an `asyncio.Semaphore(settings.news.concurrency)`.
2. Fetch all enabled feeds fairly, then apply deterministic global deduplication/capping.
3. Preserve deterministic artifact order independent of completion order.
4. Rank extraction by feed priority, profile title/subtopic overlap, freshness, and useful metadata.
5. Bound extraction concurrency with the same configured limit.
6. Detect malformed/bozo feeds and non-feed responses as per-feed failures.

Required tests: bounded concurrency, fair cap behavior, feed priority ranking, deterministic output under reordered completion, malformed feed, and partial failure.

### PMR-009: Scoring omits evidence, editorial memory, story history, and modifiers

Evidence: `src/morning_radio/newsroom/score.py:12-82` sends only profile and bare cluster objects. It does not include publishers, extracted excerpts, editorial memory, or prior history. Novelty is hard-coded to 70 in fallback. No raw-versus-adjusted score is persisted. `story_history` is only written in `pipeline.py:143-147` and never read.

Impact: POC-009 and POC-022 are not implemented. Repeated unchanged stories cannot be down-ranked, and feedback cannot influence selection.

Fix directions:

1. Add typed history query methods returning exact fingerprint matches and conservative title/entity candidates.
2. Build a compact scoring evidence payload from cluster candidates, publishers, dates, and selected excerpts.
3. Load `editorial-memory.md` and include it in the scoring payload.
4. Apply deterministic negative-preference and repeat penalties after model scoring.
5. Persist raw model dimensions, each modifier with rationale, final score, and novelty rationale.
6. Apply `ongoing_story_policy`; require evidence of new development for `changes_only` while preserving major-breaking overrides.

Required tests: all POC-009 cases plus the four two-day POC-022 simulations.

### PMR-010: Selection ignores the CLI duration override and major-news requirements

Evidence: `src/morning_radio/newsroom/select.py:18-27` derives target seconds from `profile.show_format.target_minutes`, not the `target_minutes` resolved by `run_morning`. Its subject-diversity rule is a single used-subject set. It does not explicitly ensure required major U.S./world inclusion, configurable reserved time, multi-source preference, or profile-priority concentration.

Impact: `./show morning --minutes 10` can select for a 25-minute budget and later plan for 10 minutes. Required major news can be omitted.

Fix directions:

1. Pass a typed `EpisodeRequest` or explicit target seconds into selection.
2. Move reserved production durations into typed configuration.
3. Implement major U.S./world eligibility and override rules explicitly.
4. Use cluster topic/evidence for subject diversity, with a documented concentration allowance for priority 5 interests.
5. Prefer multi-source consequential stories when comparable.
6. Persist distinct rejection reasons, not the original score reason.

Required tests: all POC-010 cases and a regression proving `--minutes` changes selection budget without mutating the saved profile.

### PMR-011: Unsafe dossiers are dropped without required backfill

Evidence: `src/morning_radio/newsroom/dossier.py:62-98` silently omits unsafe dossiers and fails only if none remain. It never asks selection for a high-scoring unused candidate and does not update selected-story artifacts to match the final researched set.

Impact: fixed-duration shows shrink unpredictably, selected artifacts disagree with scripted stories, and POC-011 backfill behavior is absent.

Fix directions:

1. Make research consume the full `SelectionResult`, not only `selected`.
2. On an unsafe dossier, persist the rejection and pull one unused high-score candidate.
3. Bound attempts by available candidates and prevent retrying a failed cluster.
4. Return a typed `ResearchResult` with final selected stories, dropped stories, dossiers, and backfill history.
5. Rewrite `selected-stories.json` or create an explicit post-research selection artifact whose ownership is unambiguous.

Required tests: unsafe backfill succeeds, no backfill shortens variable show, fixed-show behavior is explicit, and no candidate loops.

### PMR-012: Rundown validity is largely unchecked

Evidence: `src/morning_radio/showgen/rundown.py:38-44` checks only that referenced IDs are known. It does not prevent duplicate full stories, require coverage, verify `planned_seconds` equals segment sum, enforce fixed/variable duration ranges, retry a duration correction, or guarantee opening/closing feature consistency. The deterministic fallback can be far outside target.

Impact: invalid model plans are persisted and later used as authoritative production plans.

Fix directions:

1. Implement `validate_rundown(rundown, profile, dossiers, target_seconds)` with all POC-012 invariants.
2. On invalid model output, retry once with exact validation failures.
3. If retry fails, produce a deterministic duration-valid fallback that trims or reallocates useful segments without padding.
4. Recalculate `planned_seconds` from segments; reject a conflicting model value.
5. Enforce optional headlines/watch-list settings and story-reference cardinality.

Required tests: valid, unknown ID, duplicate story, mismatched sum, corrected over-duration, no empty optional section, and deterministic fallback.

### PMR-013: Script duration and content contracts are not implemented

Evidence: `src/morning_radio/showgen/script.py:18-77` has no WPM duration estimator or controlled length-adjustment cycle. It does not load editorial memory. `validate_script` checks only broad directive names and lists; malformed directives such as invalid pause values can pass until production parsing.

Impact: generated scripts can be materially off-duration or fail late, after expensive model work.

Fix directions:

1. Parse directives with a typed parser and anchored patterns for each exact syntax.
2. Reject missing arguments, unexpected arguments, invalid pause ranges, malformed brackets, and spoken text outside host blocks.
3. Add configurable WPM and estimate only spoken words.
4. Perform one constrained adjustment when outside tolerance, then validate again.
5. Include editorial memory, profile summary, rundown, and dossiers in the script prompt.
6. Validate required opening/headline/watch-list behavior against profile flags.

Required tests: all POC-013 cases, exact directive grammar, pause limits, duration estimation, adjustment, and no factual expansion during adjustment.

### PMR-014: Pipeline stage order and dependency checks violate the Canon

Evidence: `src/morning_radio/pipeline.py:120-132` synthesizes audio before building the production plan, although Canon requires asset resolution before synthesis/mixing and orders `production_plan` before `synthesize`. `run_morning` does not preflight Ollama/TTS/FFmpeg; FFmpeg can fail only after all news/model/TTS work completes.

Impact: missing required assets are discovered after costly TTS. Missing FFmpeg is discovered at the final stage. Stage status and failure guidance are less accurate.

Fix directions:

1. Parse and resolve `script-final.md` into a text-level timeline before TTS.
2. Validate all assets and voices before synthesis.
3. Synthesize speech items and attach resulting WAV metadata to the typed plan.
4. Add a reusable dependency-check service shared by `doctor` and `morning`.
5. Preserve the exact Canon stage order and emit a completed log event for each stage.

Required tests: required asset failure creates no WAV; missing TTS/FFmpeg fails with stage and corrective action; exact stage order is asserted.

### PMR-015: Feedback does not use the model and is not consumed by later runs

Evidence: `src/morning_radio/profile/feedback.py:15-45` appends raw answers directly to Markdown. It neither calls the LLM nor filters hard-setting mutations. Search shows editorial memory is not loaded by scoring, rundown, or script.

Impact: POC-020 is absent despite the CLI appearing to support it. Raw feedback accumulates without becoming concise durable preferences and has no effect on future output.

Fix directions:

1. Add a typed feedback-update model call that receives prior memory and raw answers.
2. Validate output as concise actionable Markdown rules and reject attempts to change location, identity, or duration.
3. Write backup and replacement atomically.
4. Validate an explicit run ID exists and is complete before attaching feedback.
5. Include memory in scoring, rundown, and script payloads.
6. Keep raw SQLite answers unchanged for auditability.

Required tests: every POC-020 case, invalid run ID, model failure preserving prior memory, and atomic update behavior.

### PMR-016: Offline fixtures and E2E coverage do not meet POC-023

Evidence: `src/morning_radio/pipeline.py:164-230` embeds a synthetic corpus in production code. `FakeLLM` returns a real fixture only for verification and relies on deterministic fallbacks for other stages. `tests/e2e/test_fixture_run.py` has one success test, asserts only episode suffix/story count, writes into the real repository root, and is skipped without FFmpeg. There are no blocker or partial-failure scenarios.

Impact: the suite cannot prove artifact contracts, verifier blocking, stage behavior, or partial retrieval resilience. Test runs can mutate local `runs/`, SQLite, and story history.

Fix directions:

1. Move fixture feeds, HTML, LLM JSON, and audio setup under `tests/fixtures/`.
2. Make pipeline dependencies injectable through a small runtime/dependency container; stop selecting test behavior through three global environment variables in core logic.
3. Run E2E against an isolated temporary repository root and database.
4. Implement success, verification-blocked, and partial-retrieval scenarios.
5. Assert every required artifact, final run state, failure state, no-MP3 blocker behavior, and FFprobe validity.
6. Keep live tests opt-in and offline tests network-free.

Required tests: literal POC-023 acceptance criteria and the three required E2E scenarios.

### PMR-017: The source page is not tied to the actual final script

Evidence: `src/morning_radio/artifacts/sources.py:9-46` walks rundown segments and dossiers, not stories surviving `script-final.md`. It omits run ID, can repeat a story once for headlines and again for its full segment, and includes all cluster candidates rather than only dossier-supported sources.

Impact: the audit page can claim a story was spoken when verification removed it, duplicate sections, or list sources that did not support dossier facts.

Fix directions:

1. Persist script-to-cluster trace metadata or segment markers outside spoken copy.
2. Build the page from final scripted cluster order, deduplicated while retaining first full-story order.
3. Include run ID and episode date.
4. List only source IDs actually used by the final dossier unless the page labels additional reading separately.
5. Keep escaping and no-remote-script behavior.

Required tests: corrected script removes a story, headline/full-story deduplication, run ID presence, unselected exclusion, and full-body nonleakage.

### PMR-018: Default Kokoro runtime is not reproducible and is inefficient

Evidence: `config/production.yaml` defaults to Kokoro, but `pyproject.toml` and `uv.lock` contain no Kokoro, NumPy, or SoundFile dependency and README gives no exact installation command. `src/morning_radio/audio/tts.py:91` creates a new `KPipeline` for every spoken block. No current-run audio reuse is implemented despite the Canon requirement.

Impact: `uv sync` cannot produce a runnable default environment, and real synthesis repeatedly reloads an expensive pipeline/model.

Fix directions:

1. Add a documented optional dependency group for the tested Kokoro stack with compatible bounds for the target Python version.
2. Instantiate one Kokoro pipeline per adapter/language and reuse it across blocks.
3. Cache WAVs within a run by hash of normalized text, voice, speed, engine, and engine version.
4. Reuse files safely through copy/hard-link or repeated manifest references.
5. Integrate profile voice preferences with production defaults using an explicit precedence rule.
6. Test output sample rate/channel conversion before mixing.

Required tests: cache hit makes one synthesis call, voice precedence, unavailable voice, no-audio response, and adapter reuse.

### PMR-019: MP3 mastering lacks required production behavior and diagnostics

Evidence: `src/morning_radio/audio/master.py:53-90` performs concat plus one-pass `loudnorm`, sets only a title tag, and suppresses FFmpeg stderr into an exception that the CLI does not render usefully. Episode date and generated episode title tags are absent. The concat demuxer path writer does not escape apostrophes in paths.

Impact: loudness may be inconsistent, failures are hard to diagnose, metadata is incomplete, and valid filesystem paths can break assembly.

Fix directions:

1. Use two-pass loudness normalization or a verified single-pass strategy with measured output.
2. Add title, album/show title, date, and generated episode title metadata.
3. Use a robust FFmpeg input strategy or correctly escape concat paths.
4. Convert `CalledProcessError` into `AudioMasterError` containing the relevant stderr tail and artifact path to full logs.
5. Write commands and complete stderr under `mix/`.

Required tests: apostrophe path, failed FFmpeg diagnostics, ID3 fields, loudness target, and clipping/peak bounds.

### PMR-020: Run logs and CLI failure output are incomplete

Evidence: `src/morning_radio/artifacts/runs.py:47-52` logs stage starts but no stage completions except the final run. `src/morning_radio/cli.py:150-154` prints only the exception text, not failed stage or corrective action. Pipeline progress is not printed despite POC-019.

Impact: operators cannot reliably identify where time was spent, what completed, warning counts, or what action resolves a fatal failure.

Fix directions:

1. Represent stage execution with a context manager that logs start, completion, elapsed time, warnings, and failure.
2. Persist typed failure codes and user-facing corrective actions alongside concise errors.
3. Print Rich progress per stage and a final duration/story/source summary.
4. Ensure optional feed/article/asset failures emit warning records and counts.
5. Keep full details in logs and concise text in `run.json`.

Required tests: log event sequence, warning continuation, fatal stage/action display, and no silent critical-stage omission.

## P2: hygiene, typing, and maintainability

### PMR-021: Persisted model invariants are too permissive

Evidence: models in `src/morning_radio/models.py` generally accept unknown fields and omit cross-field validation. `ShowFormat` allows target outside min/max and allows equal variable min/max, contrary to POC-003. Profiles can have no interests or duplicate interests. Cluster source counts can disagree with candidate IDs; rundown planned total can disagree with segments.

Fix directions:

1. Create a strict persisted-model base with `ConfigDict(extra="forbid")` where forward compatibility does not require extras.
2. Add `model_validator` checks for profile, duration, cluster, dossier, rundown, verification, and audio metadata invariants.
3. Use constrained strings and positive durations/counts.
4. Add schema-version dispatch for future persisted-profile migrations rather than silently accepting unknown versions.

Required tests: one focused valid and invalid case for every cross-field invariant.

### PMR-022: Production and pipeline APIs rely on untyped dictionaries and untyped parameters

Evidence: `pipeline._run_pipeline` leaves profile/settings/LLM parameters unannotated. Production uses `list[dict]` and returns `dict[str, str | int]`. `LLMClient` run directory is untyped. Settings loaders use broad `BaseModel` plus return-value ignores.

Fix directions:

1. Add concrete annotations to every public and orchestration boundary.
2. Use discriminated unions for production items and a Pydantic result model for `run_morning`.
3. Make `_validated` generic with `TypeVar` so loader ignores disappear.
4. Add `Path` annotations to LLM adapters and exact fake signatures.
5. Add Pyright or mypy in the dev group and CI, with a documented strictness baseline.

Required tests/checks: type checker passes over `src/` without blanket ignores; targeted third-party ignores use exact codes.

### PMR-023: Broad exception handling obscures programming errors

Evidence: scoring, dossier, rundown, script, verification, pipeline, and CLI catch `Exception`. Several branches fall back only when `llm.model == "fake-local-fixture"`, coupling production behavior to a magic model string.

Impact: fake mode can hide real bugs such as `KeyError`, `IndexError`, or incorrect response handling. Production diagnostics lose error taxonomy.

Fix directions:

1. Catch only typed adapter errors and expected validation errors.
2. Make fallback behavior an explicit adapter capability or injected fixture policy, not a model-name comparison.
3. Allow programming errors to fail tests immediately.
4. Define stage-specific exception classes carrying actionable context.

Required tests: unexpected programming error propagates; expected fake response failure uses explicit fallback; production LLM errors never silently degrade.

### PMR-024: Database migration and integrity mechanisms are incomplete

Evidence: `src/morning_radio/db.py:7-59` executes one monolithic `CREATE TABLE IF NOT EXISTS` script and records version 1. This is a version marker, not an incremental migration mechanism. Feedback has no enforced run foreign key, status values are unconstrained, and explicit feedback run IDs are not checked.

Fix directions:

1. Store ordered migration functions/scripts and apply only versions above the current maximum in one transaction.
2. Enable foreign keys on every connection and add appropriate constraints/indexes.
3. Validate run status through application enums and database checks where practical.
4. Add a read API for story history and completed-run validation.
5. Configure a reasonable SQLite busy timeout; WAL is optional for this single-user POC but should be measured before adoption.

Required tests: migrate empty DB, migrate prior version, rerun idempotently, reject orphan feedback, and roll back failed migration.

### PMR-025: Atomic writes are inconsistent

Evidence: run/profile JSON uses a temporary rename, but extraction files, dossiers, rundown, scripts, verification, source page, feedback memory, logs, and manifests use direct `write_text`/append operations. Feedback backup and replacement can split if interrupted.

Fix directions:

1. Provide reusable atomic text, JSON, and JSONL-finalization helpers.
2. Use atomic writes for stage artifacts that downstream stages consume.
3. For append-only logs, flush each event and tolerate a truncated final line when reading.
4. Ensure temporary files are cleaned on exceptions and optionally `fsync` critical profile/run state before replacement.

Required tests: simulated write failure preserves the prior destination and leaves no misleading complete artifact.

### PMR-026: Package structure and launcher behavior need cleanup

Evidence: subdirectories under `src/morning_radio/` lack `__init__.py`, producing implicit-namespace warnings. `show:14` builds `PYTHONPATH` from `pwd`, so fallback execution fails when invoked by absolute path outside the repository. The launcher fallback can run Python below the declared 3.12 minimum.

Fix directions:

1. Add explicit package initializers to source subpackages.
2. Resolve the launcher directory from the script path and use that for project/PYTHONPATH resolution.
3. Check Python 3.12 before fallback execution and print an installation action if unavailable.
4. Keep `uv run` as the primary path and verify the wheel includes all subpackages.

Required tests: invoke `show --help` from another working directory, inspect wheel contents, and test unsupported Python failure messaging.

### PMR-027: Ruff and test quality gates are too weak

Evidence: `pyproject.toml` configures only line length and target version. Default Ruff therefore misses complexity, typing, bug-risk, and security checks. There is no type checker or coverage reporting. Several core modules have no direct tests: extraction, scoring, selection, dossier, rundown, audio master, feedback, DB migration, interview, and pipeline failure behavior.

Fix directions:

1. Select a practical Ruff ruleset such as `E`, `F`, `W`, `I`, `UP`, `B`, `SIM`, `C4`, `PERF`, `RUF`, and targeted `S`; configure explicit test exemptions.
2. Add formatting checks and a type checker.
3. Add coverage measurement with branch coverage. Start with an honest baseline and raise thresholds as P0/P1 tests land.
4. Split tests into unit, integration, E2E, and opt-in live markers.
5. Add CI running lockfile sync/check, lint, type check, unit/integration, and E2E with FFmpeg.

Done when: quality gates catch the defects described in this report rather than merely formatting regressions.

### PMR-028: Several hot paths do unnecessary repeated work

Evidence: feed/article HTTP is serial; Ollama calls use top-level `httpx.post`, creating a new client/connection per call; Kokoro is initialized per block; tone WAV writes one frame per Python loop iteration; `pipeline.py:145` rebuilds the dossier-ID set inside a cluster comprehension.

Fix directions:

1. Reuse one `httpx.Client`/`AsyncClient` per adapter lifetime with explicit close/context ownership.
2. Implement bounded async feed and article I/O as directed in PMR-008.
3. Reuse Kokoro and current-run WAV cache as directed in PMR-018.
4. Generate test tones in buffered chunks using `array`, NumPy when already available, or bulk byte assembly.
5. Precompute selected/dossier ID sets once before filtering clusters.
6. Add elapsed-time metrics per stage and benchmark a 20-feed/150-article fixture before and after changes.

Done when: fixture outputs remain deterministic and the benchmark shows reduced intake and TTS setup time without unbounded concurrency.

## Ticket status audit

This table is based on code and tests present at the reviewed commit, not file names or stated intent.

| Ticket | Status | Principal gap |
|---|---|---|
| POC-001 | Partial | Layout lacks fixture/integration structure; default Kokoro runtime is not installable from project dependencies. |
| POC-002 | Partial | Migration mechanism and stage completion logging are incomplete. |
| POC-003 | Partial | Required integration tests absent; existing-profile and cross-field behavior are insufficiently protected. |
| POC-004 | Partial | Core checks exist, but they are not reused by morning preflight and real target stack does not pass. |
| POC-005 | Partial | No concurrency, fixture corpus, malformed-feed coverage, or fair cap behavior. |
| POC-006 | Partial | No concurrency/profile ranking; redirect SSRF check is ineffective. |
| POC-007 | Partial | Basic adapter works; fake fixture outputs and complete retry test matrix are absent. |
| POC-008 | Not implemented to spec | Required two-pass clustering and LLM adjudication are absent. |
| POC-009 | Not implemented to spec | Evidence, memory, history, raw scores, and deterministic modifiers are absent. |
| POC-010 | Partial | Duration override, major-news guarantees, and auditable rejection reasons are incomplete. |
| POC-011 | Partial | Grounding invariants and backfill are missing. |
| POC-012 | Partial | Duration and structural validation/retry are missing. |
| POC-013 | Partial | Duration estimation/adjustment, memory, and exact directive parsing are missing. |
| POC-014 | Unsafe partial | Corrected final script is not passed to synthesis; required evidence and consistency checks are missing. |
| POC-015 | Partial | Adapter boundary exists; installability, reuse cache, profile voice mapping, and real tests are missing. |
| POC-016 | Partial | Parser exists but uses untyped dicts, late asset resolution, incomplete warnings, and fragile grammar. |
| POC-017 | Not implemented to spec | Assets/beds are ignored; validation and metadata are wrong/incomplete. |
| POC-018 | Partial | Run ID and final-script traceability are missing. |
| POC-019 | Unsafe partial | Stage order, preflight, progress, final-script flow, and E2E failure tests are missing. |
| POC-020 | Not implemented to spec | No model memory update, filtering, or downstream memory use. |
| POC-021 | Unverified partial | Six feeds are configured, but live parseability and dedicated category coverage have not been proven. |
| POC-022 | Not implemented | History is written but never read or used in novelty scoring. |
| POC-023 | Not implemented to spec | One embedded fixture scenario exists; golden stage outputs and two required scenarios are absent. |
| POC-024 | Not complete | Documentation states the real target-stack smoke test has not run. |
| POC-025 | Procedure only | Procedure exists; seven-day execution and results have not occurred. |

## GPT-5.5 execution constraints

When implementing this remediation:

1. Preserve the terminal-only, local-first architecture and the Canon technology choices.
2. Make small, reviewable commits grouped by finding or tightly coupled findings. Push each completed commit to the existing branch and keep the PR current.
3. Add failing regression tests before or with every correctness fix.
4. Never loosen a schema, duration check, verification rule, or test assertion to accommodate current output.
5. Keep fixture behavior outside normal production decisions. Prefer injected adapters/runtime dependencies over environment-variable branches.
6. Do not commit generated runs, databases, audio, caches, build output, model weights, or local credentials.
7. Preserve existing public CLI commands and artifact names unless a migration is explicitly documented and tested.
8. After each phase, run the relevant focused tests, then `uv run pytest`, lint, type checking, and package build.
9. Do not mark POC-024 complete until `./show doctor`, both required morning runs, listening checks, sources audit, and feedback loop succeed on the actual Mac Studio.
10. Do not claim technical completion until every POC-001 through POC-024 acceptance criterion has evidence in tests or the real smoke-test record.

## Final verification checklist

GPT-5.5 should produce evidence for all of the following before declaring the remediation complete:

- Corrected verified text is the only text synthesized.
- Unsafe redirect destinations receive zero requests.
- Every scripted dossier fact has a valid source ID.
- All six production operation types are rendered or explicitly skipped.
- Final audio is over 60 seconds and within +/-20% of plan.
- Feed and article concurrency is bounded and deterministic.
- Clustering golden corpus passes, including ambiguous LLM adjudication.
- Editorial memory and story history visibly affect subsequent model payloads and scores.
- Rundown and script duration validation pass fixed and variable modes.
- Success, verification-blocked, and partial-retrieval E2E scenarios pass offline.
- Source page reflects final scripted stories and includes run ID.
- `uv sync`, lint, type check, full tests, package build, and `./show doctor` pass on the target stack.
- The real smoke-test result contains actual machine/run metrics and listening notes.

Until that evidence exists, this repository should be described as an incomplete POC implementation with a passing narrow unit suite, not as the completed execution package.
