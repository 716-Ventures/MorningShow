# Jev Editorial Evaluation

Jev is an optional TypeSafe cloud decision model, separate from script and speech
providers. This first integration supports **shadow mode only**: current clustering,
scores, topic coverage, writing, verification, and audio remain authoritative.
It does not yet evaluate novelty or script quality, or replace verification.

## Enable

1. Copy `config/decisions.example.yaml` to `config/decisions.local.yaml`.
2. Add `TYPESAFE_API_KEY=your-key` to the project `.env`. Never put keys in YAML.
3. Run `./show morning` with your usual script and speech settings.
4. Inspect `runs/<date>/<run-id>/logs/jev-shadow.json`.

The local YAML file is ignored by Git. It overrides the optional `decisions`
section in `config/app.yaml`; without either, the mode defaults to `off`.
Set `mode: off` to disable it. This setting is independent of `./show setup` and
does not change when you change script or speech providers. Even local script
and speech configurations send the selected evaluation inputs to TypeSafe when
shadow mode is enabled. No API key means evaluation is skipped, not a failed show.

The existing credential reader loads `.env` without shell execution or variable
interpolation. An explicitly exported variable takes precedence, including an
empty one. The integration uses the documented REST endpoint through existing
HTTPX dependencies; no extra installation is required.

## What Gets Compared

- Ambiguous article pairs that reached the existing clustering decision boundary:
  a Noul question returns the probability they describe the same specific event.
- Highest-ranked production clusters: separate Score questions evaluate each
  configured interest, including subtopics and inclusion/exclusion notes.
  Scores run from 0 (unrelated) to 3 (central subject), not 0 to 100.
- Logs retain baseline decisions, answer values, available score probabilities,
  confidence, response model ID, request hash, elapsed time and token usage.
  Baselines are **not ground-truth labels**. No automatic merge threshold is used.

Each article input includes its title, publication date and first 2,000 extracted
characters. Scoring includes at most three sources per cluster. Interest names
and notes are transmitted; entire profiles, credentials, and editorial memory
are not. Truncated evidence can miss important details and should be considered
when labeling disagreements. Logs omit article text and API response/error bodies,
but contain interest names and must remain private with other run artifacts.

## Limits and Failures

`max_calls_per_stage` defaults to 20: up to 20 pair calls and 20 scoring calls.
Calls share a pooled connection and run sequentially after production scoring.
This initial evaluation therefore **adds latency and cost**, not speedups.
There are no automatic retries. The first HTTP, validation, or input-reading
failure stops further evaluation for that episode; completed observations remain.
Per-request HTTP timeouts default to five seconds. These are network-operation
timeouts, not a hard total episode deadline. A report can be `complete`, `skipped`,
or `incomplete`; truncation flags identify sample limits.

Fixture runs always skip TypeSafe, even with a key configured. Unit tests use
mocked HTTP responses. API availability and model quality require a live test.
The default alias is `jev-latest`; set a supported version ID from your TypeSafe
account before collecting a reproducible evaluation dataset.

## Evaluation Before Activation

Review the same stored candidate/extraction artifacts alongside the shadow log.
Label ambiguous pairs as same event or different event, and score each sampled
cluster against every interest using the four rubric levels. Include hard cases:
same company/different event, substantive follow-ups, incidental topic mentions,
and stories relevant to more than one interest.

Track false merges, missed duplicates, per-interest relevance errors, uncertain
answers, incomplete evaluations, observed latency and reported input tokens.
Sampling is deterministic and favors production's highest-ranked clusters;
it cannot establish relevance recall across all discovered articles. Increase
the sample cap when evaluating missed topics and review unsampled candidates.
Do not infer accuracy from confidence, or treat agreement with the old model as
proof. Select risk-specific thresholds from human labels before adding an active
mode. Test story coverage and selection end to end before making it authoritative.

TypeSafe's published customer agreement restricts publishing performance and
benchmark information. Confirm your applicable agreement before sharing results
or committing them to the public repository. This document contains no measured
TypeSafe performance results.

References: [API quick start](https://docs.typesafe.ai/introduction/quickstart),
[Score rubrics](https://docs.typesafe.ai/primitives/score),
[confidence](https://docs.typesafe.ai/confidence),
[customer agreement](https://typesafe.ai/legal/mca).
