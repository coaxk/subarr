# #616: OpenAI-compatible LLM provider

Status: design, 2026-10-31. Issue: coaxk/subarr#616 (ErroneousBosch, Unsloth Studio).
Public promise already made on the issue (2026-10-21 comment); this design keeps it.

## Intent

subarr talks to an LLM through Ollama's native API only. Users who run a different
local server (Unsloth Studio, llama.cpp `llama-server`, LM Studio, vLLM) cannot use
the LLM features, and Judd expects users to want hosted APIs (OpenAI, OpenRouter)
next. Add one **OpenAI-compatible** provider that serves both local servers and
hosted APIs through the same code path, selectable instead of Ollama.

## Goals

1. Settings > Integrations offers **Provider: Ollama | OpenAI-compatible**. The
   OpenAI-compatible option takes a base URL, an optional API key, a text model and
   a vision setting.
2. All three LLM features work on it:
   - title-language guess (`enrichment.enrich_one`, structured JSON),
   - Arena explanations (`arena_explain.explain`, free text),
   - vision check (`routers/vision.py` `/api/vision/check`, image + JSON).
3. Works against Ollama's own `/v1`, Unsloth Studio, llama-server, OpenAI and
   OpenRouter without per-model configuration by the user.
4. A model that reasons by default (Gemma 4, gpt-5, etc.) never returns an empty
   answer silently (the `done_reason: length` trap, see Evidence).
5. The API key is a secret: masked on read, never logged, never in telemetry, never
   overwritten by its own mask.
6. The title-language prompt sends **library name + path relative to the library
   root** (both providers; decided by Judd 2026-10-31).

## Non-goals

- Anthropic, Gemini or other non-OpenAI-shaped APIs.
- Two providers at once (e.g. local for vision, hosted for text). One provider is
  active; my call, revisit only on demand.
- Pulling models, unloading models, or a server version readout for the
  OpenAI-compatible provider (no OpenAI equivalent). Those UI pieces are hidden.
- Streaming.
- Any change to what Ollama users experience, except the prompt change in goal 6.

## Anti-goals (would be a failure even if it "works")

- An empty or truncated LLM answer reported as success.
- A key that appears in a log line, an error string returned to the UI, telemetry,
  or a GET response.
- A hosted provider being hammered: retry loops on 429/5xx, or the bulk endpoint
  firing hundreds of calls with no back-off.
- Breaking existing Ollama installs, or their env-var configuration.

## Evidence gathered (2026-10-31)

Measured live against Ollama 0.x `/v1` with `gemma4:12b-it-qat` (scripts in the
session scratchpad, results quoted):

| Request | Result |
|---|---|
| `max_tokens: 60`, no thinking knob | content `''`, `finish_reason: length`, all 60 tokens in `message.reasoning` |
| `max_completion_tokens: 60` only | **ignored**: 275 tokens generated, `finish: stop` |
| `reasoning_effort: "none"` | thinking off, answer in 1.9 s |
| `chat_template_kwargs: {enable_thinking: false}` only | ignored by Ollama (empty, `length`) |
| top-level `enable_thinking: false` only | ignored by Ollama |
| `reasoning: {effort: "none"}` | thinking off |
| unknown param `bogus_xyz: 1` | ignored, 200 |
| `response_format` `json_schema` and `json_object` | both honoured, clean JSON |
| no `response_format` | JSON wrapped in a ```` ```json ```` fence |
| `image_url` with base64 data URI (64x64 red PNG) | `Red` |
| `GET /v1/models` | lists installed models |

Researched from source/docs (full notes with quotes:
`2026-10-31-616-openai-compat-research.md`, beside this file):

- **Unsloth Studio** (`studio/backend/`): honours `response_format` (json_object,
  json_schema); reasoning returned in `reasoning_content` (split from a leading
  `<think>` block); thinking off via `chat_template_kwargs.enable_thinking` or
  top-level `enable_thinking`; `reasoning_effort: "none"` effect on Gemma 4 UNKNOWN;
  base64 `image_url` works; `/v1/models` exists.
- **llama-server**: `response_format` needs the schema at
  `response_format.json_schema.schema` (README is wrong); reasoning in
  `reasoning_content`; `chat_template_kwargs.enable_thinking` works; reads
  `max_tokens` only (no `max_completion_tokens` handling found); base64 images work.
- **OpenAI**: `max_tokens` deprecated and rejected by reasoning models, which also
  reject non-default `temperature`; `max_completion_tokens` is the current field;
  unknown parameters are rejected with 400; 429 may carry `retry-after`.
- **OpenRouter**: accepts `max_tokens` and `max_completion_tokens`; reasoning in
  `message.reasoning`; `reasoning: {effort: "none"}` disables it (`exclude: true`
  only hides it, tokens still count); `response_format` support varies per upstream
  and unsupported params are silently dropped.
- **Gemma 4**: thinking is a chat-template kwarg (`enable_thinking`).

Conclusion: no single request shape works everywhere. Local servers ignore unknown
parameters, OpenAI rejects them. Hence server profiles plus negotiation (below).

## Design

### Settings and env

New persisted settings (in `_FIELD_COERCE`, each guarded in `FIELD_ENV_VARS`, the
#603 rule: a persistable field must be env-guardable, enforced by
`test_every_persistable_field_is_env_guardable`):

| Setting | Env | Default |
|---|---|---|
| `llm_provider` (`ollama` \| `openai`) | `LLM_PROVIDER` | `ollama` |
| `openai_base_url` | `OPENAI_BASE_URL` | `""` (unconfigured) |
| `openai_api_key` (secret) | `OPENAI_API_KEY` | `""` |
| `openai_model` | `OPENAI_MODEL` | `""` |
| `openai_vision` (`off` \| `same` \| a model id) | `OPENAI_VISION` | `off` |
| `openai_server_type` (`auto` \| `local` \| `openai` \| `openrouter`) | `OPENAI_SERVER_TYPE` | `auto` |

- Base URL is entered including `/v1` (e.g. `http://unsloth:8000/v1`,
  `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`). subarr appends
  `/chat/completions` and `/models`. The UI hint shows those three examples.
- `openai_vision`: `/v1/models` cannot say whether a model takes images, so the
  user says so. `same` = the text model also does vision.
- The key is masked exactly like the existing secrets (`routers/integrations.py`
  `_SECRET_KEYS`/`_mask`). New: the credentials save path must **skip a value that
  is itself a mask** (`••••` prefix) for this key, as `onboarding.py:280` already
  does. Today the settings UI never echoes a mask, but the API would store one.

### Server profiles (`auto` resolves by host)

`auto`: host `api.openai.com` -> `openai`; host ending `openrouter.ai` ->
`openrouter`; anything else -> `local`. The override exists for proxies and
gateways that front OpenAI under another host.

| Profile | Token cap | Temperature | Thinking off | JSON |
|---|---|---|---|---|
| `local` | `max_tokens` | sent | `reasoning_effort: "none"` **and** `chat_template_kwargs: {enable_thinking: false}` | `response_format` `json_schema` (schema at `json_schema.schema`) |
| `openrouter` | `max_tokens` | sent | `reasoning: {effort: "none"}` | `response_format` `json_schema` |
| `openai` | `max_completion_tokens` | sent | `reasoning_effort` at the lowest value the model accepts | `response_format` `json_schema` |

Why both knobs on `local`: Ollama only honours `reasoning_effort`, llama-server and
Unsloth honour `chat_template_kwargs`, and all three ignore the one they do not
know (measured for Ollama; documented for the other two).

### Parameter negotiation (all profiles)

A server may reject a parameter (OpenAI: `reasoning_effort` on a non-reasoning
model, `temperature` on a reasoning model; a strict local server: an unknown key).
On a **400 whose error body names one of the optional parameters we sent**
(`temperature`, `reasoning_effort`, `reasoning`, `chat_template_kwargs`,
`response_format`, `max_completion_tokens`/`max_tokens` swapped for the other):

1. drop (or swap) that one parameter,
2. retry **once per dropped parameter**, at most 3 retries per call in total,
3. remember the result per `(base_url, model)` in memory for the process lifetime,
   so later calls send the working shape first.

For `openai` `reasoning_effort`, the lowest value is tried in order `none`,
`minimal`, `low`; an "unsupported value" 400 moves to the next, an "unsupported
parameter" 400 drops it. Any other 400, and every 401/403/404, fails the call
immediately with a clear message (401/403: "API key rejected"; 404: "model not
found at this server").

### Reply handling (defence in depth)

1. Content = `message.content`. A leading `<think>...</think>` block is stripped
   (some servers inline reasoning). `reasoning` / `reasoning_content` are ignored.
2. **Empty content with `finish_reason == "length"`** raises a distinct error:
   "the model used its whole reply budget reasoning; turn thinking off on the
   server or choose a non-thinking model". Never returned as an empty success.
   Any other empty content is an error too.
3. JSON features parse as today (`enrichment._parse_structured` already finds the
   first `{`, which also handles a fenced block). If `response_format` was dropped
   by negotiation, the schema is described in the prompt instead and the reply is
   validated; a reply that does not parse is an error, not a guess.

### Hosted-service safety

- **429 and 5xx**: honour `retry-after` once when it is at most 30 s, otherwise
  fail. The client gets a `CircuitBreaker` (`circuit_breaker.py`, same use as
  `integrations/base.py`): 5 consecutive failures open it for 60 s, during which
  calls fail fast with "LLM provider cooling down".
- `/api/enrichment/lang/bulk` keeps its sequential loop and stops the batch when
  the breaker opens, returning the rows done so far plus the reason.
- The **GPU-busy gate** (enrichment waits while subgen transcribes) applies only to
  Ollama and the `local` profile; skipped for `openai`/`openrouter`.
- `keep_alive` / `unload` are no-ops on the OpenAI-compatible client.
- Timeouts: same as `_OLLAMA_TIMEOUT` (connect 3, read 120); hosted connect 10.
- Never log the key or the `Authorization` header. Error strings returned to the
  UI pass through the existing `safe_error`.

### What leaves the machine (shown in the UI for `openai`/`openrouter` profiles)

One line under the provider fields: "Sent to this service: titles with library
name and file path inside the library, Arena score summaries with the file name,
and video frames when vision is on." This is factual, not a consent gate.

### Prompt change (both providers)

`enrichment._USER_TEMPLATE` currently sends `Folder path: {canonical_path}`, i.e.
`@<slug>/<rel>` or `<rel>` for library 0 (canonical paths are already relative to
the library root; no machine-specific prefix leaves today). New form:

```
Title: {title}
Library: {library name}
Path: {path relative to the library root}
```

Library from `paths.library_for_canonical`. For library 0 the name is whatever the
user called it (often "default"); it is still sent, the model ignores noise. The
system prompt's "and folder path" wording follows.

### Evaluation script (prompt change acceptance)

`scripts/eval_title_language.py`, manual, not CI: pulls up to N (default 60, half
`originalLanguage` English, half not) movies from Radarr and series from Sonarr via
the configured instances, runs the old and new prompt through the active provider
at temperature 0, scores the ISO code against `originalLanguage`, prints both
scores and the disagreements. Acceptance: the new prompt is not worse than the old
by more than 1 item on Judd's live library with gemma4. Baseline from 2026-10-08:
gemma4 22/30 (English 15/15, foreign 7/15).

### Code shape

- New `integrations/openai_compat.py`: `OpenAICompatClient` with the methods the
  callers use today on `OllamaClient`: `is_configured()`, `model`, `generate(prompt,
  *, system, temperature, num_predict, keep_alive, format_schema)`,
  `vision_describe(*, image_url, image_b64, prompt, model, num_predict)`,
  `unload()` (no-op), `aclose()`, plus `models()` for `/v1/models`.
  `num_predict` maps to the profile's token-cap field.
- Capability flags on both clients, used by routers and UI instead of
  `isinstance` checks: `supports_pull`, `supports_unload`, `is_local_gpu`
  (Ollama: all true; OpenAI-compatible: false, false, `profile == local`).
- `app.state` holds one active LLM client built from `llm_provider`, rebuilt by the
  existing live-reload path (`routers/onboarding.py` `_rebuild_runtime_clients`).
  Whether the attribute stays named `ollama` or becomes `llm` is the builder's
  call, but every caller must go through one attribute, never both.
- Health/probe (`routers/integrations.py` `_probe`, `routers/enrichment.py`
  `/health`, `routers/home.py`): for OpenAI-compatible, `GET /v1/models` is
  liveness and the model list.
- **Test connection** (onboarding `_test_*` dispatch + settings) does a real check,
  not just reachability: `/v1/models`, then one JSON completion at the enrichment cap
  (the language schema on a fixed title, e.g. "Shingeki no Kyojin", expects `ja`), then, if vision is on, the known-answer
  image test (solid red PNG, expects "red"). It reports each step: reachable, key
  accepted, model listed, JSON honoured, thinking leaked (reasoning present or
  empty-with-length), vision answered correctly.
- Frontend `settings.jsx`: provider selector on the LLM rail; fields per provider;
  Ollama-only cards (`OllamaVisionCard` pull UI, "Unload model" row) hidden for
  OpenAI-compatible; vision select for OpenAI-compatible; the "sent to" line.
  Bundles rebuilt with `npm run build:frontend`; `check:frontend` must pass.
- Telemetry (`telemetry.py` ~560): reports provider kind and profile only
  (`ollama` / `openai-local` / `openai-hosted`), never URL, key or model.

## Constraints

- TDD: failing tests first. HTTP mocked with the repo's existing pattern (check
  `tests/test_ollama_think.py`, `tests/test_integrations_health_probe.py`). One
  test per row of the profile table, per negotiation path (drop, swap, value
  ladder, retry cap, remembered shape), per reply-handling rule, 429 with and
  without `retry-after`, breaker open, masked-key save skipped, env guard for every
  new field, telemetry has no URL/key.
- Live verification before release: against Ollama `/v1` on :9923 (local profile),
  all three features plus Test connection. Hosted profiles are verified live only if
  a key is available (see open questions).
- Ollama path regression: the existing Ollama tests pass unchanged except where the
  prompt template changed.
- Ruff clean. Full pytest and frontend suites green. CI 19/19.
- Release notes say plainly which servers were tested live and which only by
  documentation.

## Decisions settled after the builder check (designer's calls unless marked)

1. **Enrichment cache is left alone.** `enrich_one` serves a cached `iso_code` per
   canonical path whatever produced it. A title's original language does not
   change with the model, rows already record `model`, and invalidating would
   mean a mass re-run against a possibly paid API. New rows get the new prompt;
   old rows stay until a user re-runs them with `use_cache=False` (existing path).
2. **Credentials API.** `_CREDENTIAL_FIELDS` gains an `openai` integration:
   `url` -> `openai_base_url`, `api_key` -> `openai_api_key` (secret, matches
   `_SECRET_KEYS`), `model` -> `openai_model`, `vision` -> `openai_vision`,
   `server_type` -> `openai_server_type`; `CredentialsBody` gains optional
   `vision` and `server_type`. The provider choice is a separate `llm`
   integration with one key, `provider` -> `llm_provider`. The `ollama` entry is
   unchanged. The settings rail shows the `llm` selector, then the active
   provider's fields.
3. **Mask skip** goes into `save_credentials` for EVERY secret key (hardens the
   existing ones too), and `_resolve_masked_credential`
   (`routers/onboarding.py:64`) gains the `openai` entry so Test connection uses
   the stored key when the form holds a mask.
4. **Vision off / no vision model**: `/api/vision/check` answers 503 "vision not
   configured" (the same contract as Ollama with no vision model), and
   `/api/vision/status` reports `vision_capable: false` with `provider:
   "openai"`. No pull suggestion is shown for this provider.
5. **A model that reasons regardless** (thinking could not be switched off, e.g.
   an OpenAI reasoning model that accepts only `low`): on empty content with
   `finish_reason: length` AND reasoning present, retry ONCE with the cap raised
   to `max(cap * 8, 1024)`, bounded at 2048, and remember that per model. Still
   empty = the distinct error from Reply handling. Arena keeps 220, enrichment 120,
   vision 256 as the base caps.
6. **JSON schema** is sent as `response_format: {type: "json_schema",
   json_schema: {name: "subarr_<feature>", schema: <schema>, strict: false}}`.
   `strict: false` because the existing schemas use `minimum`/`maximum` and an
   optional `reasoning` key, which OpenAI strict mode rejects. subarr validates
   the reply itself regardless.
7. **Negotiation cache** lives at module level, keyed `(base_url, model,
   profile)`, so a client rebuild keeps it and a settings change naturally misses
   it. Test connection starts from profile defaults (bypasses the cache) and
   writes what it learns back.
8. **Breaker** belongs to the client and resets on rebuild (a settings save is a
   deliberate user action; resetting then is acceptable).
9. **Telemetry**: the existing `ollama` boolean keeps its meaning (Ollama
   reachable; false when another provider is active). A new field `llm_provider`
   carries `ollama` / `openai-local` / `openai-hosted`. Before adding it, the
   builder checks that the `coaxk/subarr-telemetry` ingest accepts an unknown
   field (and adds it there first if it validates strictly).
10. **Hosted live verification** (Judd, 2026-10-31): Judd has an OpenRouter
    account with credits. The `openrouter` profile is verified LIVE before release
    (all three features plus Test connection, at least one reasoning model and one
    non-reasoning model). The key is entered by Judd through the settings UI or
    env, never pasted into chat or a file in the repo. The direct `openai` profile
    has no key: it ships verified by unit tests against the documented behaviour,
    and the release notes say so.

Design approved by Judd 2026-10-31.

## Left to the builder

- Internal structure of `openai_compat.py`, naming of helpers, how the negotiation
  cache is keyed in code.
- Exact UI layout of the provider fields within the existing rail pattern.
- Error-message wording beyond the meanings fixed above.
- The `app.state` attribute name (single attribute rule stands).
