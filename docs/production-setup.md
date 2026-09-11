# Production setup

Run `./show setup` to choose how this installation produces episodes. The first
interactive `./show morning` or `./show voice-preview` opens the same wizard when
no saved provider preferences exist. Unattended runs fail with setup instructions
instead of prompting or silently selecting a billable service. Fixture runs skip
setup. `./show providers` displays hardware and supported options without changes.

Setup groups machine details, provider choices, and billing notes into separate
sections. Menus accept either an option number or its name; Enter accepts the
marked default. Colors follow terminal capabilities and `NO_COLOR`. Redirected
output remains readable without ANSI color codes.

## Supported combinations

| Stage | Local | Cloud |
| --- | --- | --- |
| Research, clustering, writing, verification, feedback | Ollama: Qwen3 4B or 8B | OpenAI: GPT-4.1 mini or GPT-4.1 |
| Speech | Kokoro: Bella and six other built-in voices | ElevenLabs: an account-accessible Voice ID |

Choose local, cloud, or mixed. Mixed selects each stage independently, for example
OpenAI scripts with local Bella speech. Audio can also be disabled entirely.
Existing users can choose `keep` to adopt current settings without changing models.
`./show configure` still edits editorial interests, not provider preferences.

## Machine recommendations

Setup reads OS, architecture, physical RAM, CPU count, and free disk locally. It
does not upload machine details. Current conservative sizing policy, with 8K
context and room for speech synthesis and the OS:

- Under 12 GiB RAM, unknown RAM, or under 8 GiB free disk: no confident recommendation.
- 12-31 GiB RAM: Qwen3 4B with thinking disabled.
- At least 32 GiB RAM and 16 GiB free disk: Qwen3 8B.
- Larger RAM but 8-15 GiB free disk: Qwen3 4B.

A 16 GB Apple Silicon Mac gets Qwen3 4B and Kokoro Bella. These are memory-budget
heuristics, not measured latency or quality guarantees. CPU-only machines may be
slow; discrete GPU acceleration and VRAM are not currently inspected. Unknown
hardware is reported as unknown. Re-run setup after moving the installation.

No model is automatically downloaded. Setup prints the selected `ollama pull`
command and local speech installation command. Local inference is not fully
offline operation: news requires internet and first-use models must be downloaded.

## Storage and credentials

Preferences live in ignored `config/providers.local.yaml`. This file overrides
the complete `llm` and `tts` sections and `generate_audio` from the base config.
Audio mixing, assets, news selection, and the editorial profile stay unchanged.
Re-run setup to change providers; advanced settings can be edited in this local
file. Removing it restores base settings and triggers setup on the next run.

Cloud keys can be entered through hidden, repeated prompts. They are written only
after final confirmation, to the ignored project `.env`, with owner-only access.
Existing dotenv entries are preserved. Shell environment values take precedence,
including empty values; setup warns when an overriding variable exists. Cancel
before saving leaves preferences and credentials unchanged.

## Billing, privacy, and checks

OpenAI receives the text needed for each newsroom operation, potentially including
profile information. ElevenLabs receives speech text. API accounts need appropriate
credits, permissions, and model or voice access. ChatGPT subscriptions do not pay
for OpenAI API usage. An ElevenLabs API key does not unlock paid Voice Library
voices. Setup never buys credits or makes inference requests.

After setup, run `./show doctor`. OpenAI's check verifies credential presence only,
not account eligibility or billing. ElevenLabs metadata access likewise does not
guarantee paid synthesis access. Generation reports HTTP failures without exposing
raw provider error bodies or retrying failed billable requests. OpenAI schema
validation can make up to three successful, billable requests to repair bad JSON;
an episode includes multiple newsroom calls. There is no application-enforced
spending cap; configure limits with the provider.

`./show voice-preview` uses the selected speech provider and can consume cloud
credits. The live benchmark remains Ollama-only and rejects cloud configuration.

## Validation

Tests cover local/cloud/mixed/keep flows, cancellation, secret preservation,
headless first-run blocking, hardware sizing, provider routing, malformed output,
and HTTP failures using mocked cloud responses. No paid cloud generation is part
of the test suite. Run `.venv/bin/pytest`, `.venv/bin/ruff check .`, and
`.venv/bin/pyright` from the project root.

Provider references: [Qwen3 4B](https://ollama.com/library/qwen3:4b),
[Qwen3 8B](https://ollama.com/library/qwen3:8b),
[GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini),
[OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat).
