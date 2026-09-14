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
| Research, clustering, writing, verification, feedback | Ollama: Qwen3 4B or 8B | OpenAI API; ChatGPT via Codex; Vercel AI Gateway |
| Speech | Kokoro: Bella and six other built-in voices | ElevenLabs; Vercel AI Gateway OpenAI speech (beta) |

Choose local or cloud scripts, or mixed to see all script providers together.
Every mode then asks separately for audio output: local Kokoro, cloud ElevenLabs,
Vercel AI Gateway, or `none`. Cloud scripts can use local Bella speech without selecting mixed.
Choosing `none` disables all speech and MP3 generation; it does not select local
audio. The review explicitly lists whether an MP3 episode will be produced.
Existing users can choose `keep` to adopt current settings without changing models.
`./show configure` still edits editorial interests, not provider preferences.

## Vercel AI Gateway

Run `./show setup`, choose `cloud`, then `vercel` for scripts. Choose `vercel` again
for gateway speech, or select Kokoro, ElevenLabs, or no audio independently. You
can also use gateway speech with another script provider through `mixed`.

Setup offers these gateway text models: `openai/gpt-4.1-mini`,
`anthropic/claude-sonnet-4.5`, and `google/gemini-2.5-flash`. These IDs were present
in the public gateway catalog on September 14, 2026. `custom` accepts another
`provider/model` ID; it must support chat completions and JSON output. Catalog
presence and valid naming do not guarantee your team's access or model quality.

For speech, select `openai/tts-1` or `openai/tts-1-hd`, then alloy, echo, fable,
onyx, nova, or shimmer. Vercel speech is beta with gradual team access. A gateway
key alone does not guarantee access. Both stages use `AI_GATEWAY_API_KEY`, entered
once through a hidden setup prompt and saved only on confirmation to ignored
`.env` with owner-only permissions. Alternatively, edit `.env` directly:

```dotenv
AI_GATEWAY_API_KEY=your-gateway-key
```

The gateway does not use `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, or your ChatGPT
subscription for these requests. Account credits, billing, routing, and provider
access are managed in Vercel. MorningShow does not deploy to Vercel or require
the Vercel CLI. It pins requests to the documented HTTPS gateway endpoints;
`llm.base_url` cannot redirect credentials to another host.

`./show doctor` checks credential presence without billable inference. It explicitly
does not verify credits, model eligibility, or speech beta access. After saving:

```bash
./show doctor
./show voice-preview
./show morning
```

Voice preview generates billable speech. Skip it for script-only production.
No paid generation was performed during implementation; tests use mocked gateway
responses, including valid WAV, truncated audio, failures, and credential isolation.

Text requests use the gateway's OpenAI-compatible chat-completions endpoint.
Schema validation may make up to three billable attempts, as with direct OpenAI;
HTTP failures and timeouts are not retried by MorningShow. Gateway-side routing
and failover policies remain controlled by Vercel. Text and editorial context pass
through Vercel and its selected provider; do not assume zero data retention.

Speech uses the documented v4 speech endpoint with WAV output. Each request is
limited to 4,096 input characters and a 32 MB JSON response. The adapter decodes
base64, validates the WAV and frame count, and atomically saves it before the
existing local mixer adds pauses, music, fades, loudness processing, and MP3 export.
Provider warnings fail the chunk instead of silently ignoring requested options.
There are no automatic speech retries or fallbacks; a timeout may still be billed.
Secrets and raw provider error bodies are excluded from diagnostics. Speech model
and timeout settings live under `tts.vercel` in `config/providers.local.yaml`;
voice and speed remain under `tts`.

References: [gateway chat completions](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/rest-api),
[gateway speech and beta limitations](https://vercel.com/docs/ai-gateway/modalities/text-to-speech).

## ChatGPT subscription via Codex

The `codex` script provider integrates the Codex app-server using its managed
ChatGPT browser login. It is separate from the `openai` API-key provider; selecting
it never silently switches to API-key billing. An eligible ChatGPT account and
Codex allowance are required. The model list comes from `model/list` at setup,
not the OpenAI API catalog. Actual access remains subject to account restrictions.

1. Install a Codex CLI with app-server support on PATH. The integration was checked
   against CLI 0.146.0 on macOS. The subprocess transport targets macOS/Linux.
2. Run `./show setup`, choose `cloud`, then `codex` for scripts and `kokoro` for
   local speech, `elevenlabs` for cloud speech, or `none` for scripts without an MP3.
3. Accept browser sign-in when offered, finish authorization in your browser, and
   return to setup. Select a listed model and confirm the preferences.
4. Run `./show doctor`, then `./show morning`.

Separate account commands:

```bash
./show codex-login
./show codex-status
./show codex-logout
```

Login waits up to five minutes. Failed or interrupted login cancels the pending
flow and closes its server. Logout affects only MorningShow. Setup, login, and
status can contact OpenAI for authentication, models, and usage metadata but make
no generation requests. Login is a separate account action: cancelling setup
after successful login leaves the account connected without saving new preferences.

Codex stores its managed authentication under the ignored `data/codex/home`
directory, protected with owner-only directory permissions. Tokens are not copied
from your normal Codex login or stored in `.env`. The child process does not inherit
API keys, your usual `CODEX_HOME`, or your usual Codex configuration. Do not commit
or share the contents of `data/codex`. The provider's `llm.base_url` is unused.

Each generation gets an ephemeral thread in a separate workspace, with read-only
sandboxing, network-disabled tool execution, and shell, browser, app, hook, and
image-generation features disabled. The adapter rejects server tool/approval
requests. Article text and relevant editorial context are sent to OpenAI for
generation, under the account's Codex/ChatGPT policies. This is not local inference.

Completed final-answer events, not commentary or partial streaming deltas, supply
the script. Structured responses are validated with the same Pydantic schemas as
other providers. MorningShow adds no retries to Codex generation or validation
failures; Codex itself manages its service transport. Existing newsroom fallbacks
still apply, and verification must pass before an episode can be published.
The owned process closes at the end of a run or after a transport failure.

Subscription allowance is shared with other Codex use. `codex-status` displays the
reported remaining percentages, or explicitly says unavailable. It does not buy
credits, redeem resets, or guarantee sufficient allowance for a complete episode.
Account credit policies may allow additional usage; this app does not change them.
OpenAI API billing and speech-provider billing remain separate choices.

Validation includes offline login, catalog, output, failure, and real subprocess
protocol tests. A real installed-CLI handshake was checked without account login.
Live authenticated generation was not performed as part of implementation.

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
before saving leaves preferences and API keys unchanged. Codex browser sign-in is
a separate account action, as described above.

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
[OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat),
[Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Codex authentication](https://learn.chatgpt.com/docs/auth).
