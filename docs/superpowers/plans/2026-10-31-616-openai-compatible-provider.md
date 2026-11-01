# OpenAI-compatible LLM provider (#616) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenAI-compatible LLM provider (local servers and hosted APIs) selectable instead of Ollama, serving title-language guess, Arena explanations and the vision check.

**Architecture:** A new `OpenAICompatClient` exposes the same methods callers already use on `OllamaClient`. A factory builds whichever provider `llm_provider` selects onto one `app.state.llm` attribute. Request shape comes from a server profile (`local` / `openai` / `openrouter`) plus per-model parameter negotiation; reply handling refuses empty answers.

**Tech Stack:** Python 3.12, FastAPI, httpx (`MockTransport` in tests), pytest-asyncio, React JSX bundled by esbuild (`npm run build:frontend`).

**Spec:** `docs/superpowers/specs/2026-10-31-616-openai-compatible-provider-design.md` (research: `docs/superpowers/specs/2026-10-31-616-openai-compat-research.md`). Read both before any task.

## Global Constraints

- Settings, env vars and defaults exactly as the spec's "Settings and env" table: `llm_provider`/`LLM_PROVIDER`/`ollama`, `openai_base_url`/`OPENAI_BASE_URL`/`""`, `openai_api_key`/`OPENAI_API_KEY`/`""`, `openai_model`/`OPENAI_MODEL`/`""`, `openai_vision`/`OPENAI_VISION`/`off`, `openai_server_type`/`OPENAI_SERVER_TYPE`/`auto`.
- Every persistable field is in both `_FIELD_COERCE` and `FIELD_ENV_VARS` (`test_every_persistable_field_is_env_guardable` must stay green).
- Profile table (spec, verbatim):
  - `local`: `max_tokens`; temperature sent; `reasoning_effort: "none"` AND `chat_template_kwargs: {"enable_thinking": false}`.
  - `openrouter`: `max_tokens`; temperature sent; `reasoning: {"effort": "none"}`.
  - `openai`: `max_completion_tokens`; temperature sent; `reasoning_effort` ladder `none` -> `minimal` -> `low`.
  - All: JSON via `response_format: {"type": "json_schema", "json_schema": {"name": "subarr_<feature>", "schema": <schema>, "strict": false}}`.
- `auto` profile: host `api.openai.com` -> `openai`; host ending `openrouter.ai` -> `openrouter`; else `local`.
- Negotiation: at most 3 retries per call; cache keyed `(base_url, model, profile)` at module level.
- Reasoning retry: once, cap `min(max(cap * 8, 1024), 2048)`, only on empty content + `finish_reason == "length"` + reasoning present.
- 429/5xx: honour `retry-after` once when <= 30 s. Breaker: `CircuitBreaker(name="llm", fail_threshold=5, cooldown_s=60.0)`.
- Timeouts: local `httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)`; hosted profiles connect 10.0, rest same.
- The API key never appears in a log line, an exception message, a GET response, or telemetry.
- Base caps unchanged: Arena 220, enrichment 120, vision 256.
- TDD per task. Ruff clean. Never kill processes by image name; stop only a PID you started.

## Review Focus

1. **Base URL typed without `/v1` or with a trailing slash.** Expect the trailing slash to be stripped, and Test connection to report "no /v1/models here; the URL usually ends in /v1" on a 404. Pinned in Task 2 and Task 7.
2. **A pasted key carrying whitespace or a newline.** Expect it stripped before use. Pinned in Task 1.
3. **A server or proxy that echoes the request, key included, in its error body.** Expect the key to be redacted from every `LLMError` message. Pinned in Task 2.
4. **An OpenRouter upstream that silently drops `response_format` and replies in prose.** Expect an `LLMError("reply was not valid JSON")`, never a guessed ISO code. Pinned in Task 8.
5. **`LLM_PROVIDER=openai` with no base URL or model.** Expect `is_configured()` False, every feature to report "not configured", and no crash at startup. Pinned in Task 6.

---

### Task 1: Settings fields

**Files:**
- Modify: `src/subarr/config.py` (Settings dataclass near `ollama_*` ~208-220; loader ~385-389; `FIELD_ENV_VARS` ~570; `_FIELD_COERCE` ~648)
- Test: `tests/test_llm_settings.py`

**Interfaces:**
- Produces: `settings.llm_provider: str`, `settings.openai_base_url: str`, `settings.openai_api_key: str`, `settings.openai_model: str`, `settings.openai_vision: str`, `settings.openai_server_type: str`.

- [ ] **Step 1: Write failing tests** `test_llm_defaults` (all six defaults as in Global Constraints with env unset), `test_llm_env_overrides` (monkeypatch each env var, reload settings, values match), `test_api_key_is_stripped` (`OPENAI_API_KEY=" sk-x\n"` -> `"sk-x"`), `test_new_fields_env_guarded` (each of the six attrs is in `FIELD_ENV_VARS` and `_FIELD_COERCE`). Follow how existing config tests reload settings (grep `tests/` for `FIELD_ENV_VARS`).
- [ ] **Step 2: Run** `pytest tests/test_llm_settings.py -v`. Expect FAIL (AttributeError).
- [ ] **Step 3: Implement** the six fields with `_env_or`. Strip whitespace on `openai_api_key` and `openai_base_url`. Lower-case `llm_provider`, `openai_server_type` and `openai_vision`'s keyword values (`off`/`same`). A model id passes through as typed.
- [ ] **Step 4: Run** the new file plus `pytest -k env_guardable -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(616): settings for the OpenAI-compatible provider`.

### Task 2: `OpenAICompatClient` core (profiles, request shape, reply handling)

**Files:**
- Create: `src/subarr/integrations/llm.py` (`LLMError`, `resolve_profile`)
- Create: `src/subarr/integrations/openai_compat.py`
- Modify: `src/subarr/integrations/ollama.py:25` (`class OllamaError(LLMError)`)
- Test: `tests/test_openai_compat_client.py`

**Interfaces:**
- Produces:
  - `class LLMError(RuntimeError)`
  - `resolve_profile(base_url: str, server_type: str) -> Literal["local", "openai", "openrouter"]`
  - `class OpenAICompatClient(base_url: str | None = None, api_key: str | None = None, model: str | None = None, vision: str | None = None, server_type: str | None = None, timeout: httpx.Timeout | None = None)`. `None` falls back to settings. Attributes: `profile`; `_client: httpx.AsyncClient` (replaceable in tests, the same pattern as `tests/test_ollama_think.py::_capturing_client`).
  - Methods matching `OllamaClient`: `is_configured() -> bool` (base_url and model both non-empty); property `model -> str`; `async generate(prompt: str, *, system: str | None = None, temperature: float = 0.0, num_predict: int = 64, keep_alive=None, format_schema: dict | None = None, schema_name: str = "subarr") -> str`; `async unload() -> None` (no-op); `async aclose()`.
  - Capability flags on BOTH clients: `supports_pull: bool`, `supports_unload: bool`, `is_local_gpu: bool`. Ollama: True/True/True. OpenAI-compatible: False/False/`profile == "local"`.

- [ ] **Step 1: Write failing tests** (MockTransport capturing bodies and headers):
  - `test_resolve_profile`: `("https://api.openai.com/v1","auto")->"openai"`, `("https://openrouter.ai/api/v1","auto")->"openrouter"`, `("http://unsloth:8000/v1","auto")->"local"`, `("http://proxy/v1","openai")->"openai"`.
  - `test_local_body`: body has `max_tokens == 120`, `temperature == 0.0`, `reasoning_effort == "none"`, `chat_template_kwargs == {"enable_thinking": False}`, no `max_completion_tokens`.
  - `test_openrouter_body`: `max_tokens`, `reasoning == {"effort": "none"}`, no `reasoning_effort`, no `chat_template_kwargs`.
  - `test_openai_body`: `max_completion_tokens == 120`, `reasoning_effort == "none"`, no `max_tokens`.
  - `test_json_schema_shape`: with `format_schema=S, schema_name="subarr_lang"`, `response_format == {"type": "json_schema", "json_schema": {"name": "subarr_lang", "schema": S, "strict": False}}`.
  - `test_system_message_first`: `messages[0] == {"role": "system", "content": ...}` when `system` is given.
  - `test_auth_header`: `Authorization: Bearer k` when a key is set; no Authorization header when the key is empty.
  - `test_trailing_slash_stripped`: base `http://x/v1/` posts to `/v1/chat/completions`.
  - `test_think_block_stripped`: content `"<think>abc</think>\nja"` returns `"ja"`.
  - `test_empty_length_raises`: content `""`, `finish_reason "length"`, no reasoning: raises `LLMError` whose message contains `"reply budget"`.
  - `test_empty_stop_raises`: content `""`, `finish_reason "stop"`: raises `LLMError`.
  - `test_401_message`: a 401 raises `LLMError` containing `"API key rejected"`. `test_404_message`: contains `"model not found"`.
  - `test_key_redacted_in_errors`: a 500 whose body contains the key: the raised message does not contain the key.
  - `test_ollama_error_is_llm_error`: `issubclass(OllamaError, LLMError)`.
- [ ] **Step 2: Run** `pytest tests/test_openai_compat_client.py -v`. Expect FAIL (ImportError).
- [ ] **Step 3: Implement.** One private `_chat(messages, *, cap, temperature, format_schema, schema_name) -> str` builds the body from the profile and parses `choices[0].message.content` and `finish_reason`. Timeouts per Global Constraints. Redact by replacing the key string in any message before raising. Never log the headers.
- [ ] **Step 4: Run** the file plus `pytest tests/test_ollama_think.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(616): OpenAI-compatible client core`.

### Task 3: Parameter negotiation and the reasoning retry

**Files:**
- Modify: `src/subarr/integrations/openai_compat.py`
- Test: `tests/test_openai_compat_negotiation.py`

**Interfaces:**
- Produces: module-level `_SHAPES: dict[tuple[str, str, str], dict]` (learned overrides) and `reset_negotiation_cache() -> None` (tests and Test connection use it). `generate(..., use_learned: bool = True)`; `False` starts from the profile defaults (Task 7 uses it).

- [ ] **Step 1: Write failing tests** (the handler returns a 400 with an OpenAI-style body `{"error": {"message": "Unsupported parameter: 'temperature' ...", "param": "temperature"}}`, matched on `param` when present, else the quoted name in `message`):
  - `test_drop_temperature_then_succeeds`: first 400 names `temperature`; the second body has no `temperature`; returns content; exactly 2 requests.
  - `test_swap_max_tokens_for_max_completion_tokens` and the reverse.
  - `test_reasoning_effort_value_ladder` (openai): "Unsupported value" on `none`, then `minimal` succeeds; the bodies carry `none`, then `minimal`.
  - `test_reasoning_effort_dropped_when_unsupported_parameter`.
  - `test_retry_cap`: four different params rejected in turn: raises `LLMError` after 4 requests total (1 + 3 retries).
  - `test_other_400_fails_immediately`: a 400 naming no known parameter: 1 request, raises.
  - `test_learned_shape_reused`: after a drop, a second `generate` sends the learned shape first (1 request); a different model on the same client starts from defaults.
  - `test_reasoning_retry_raises_cap`: first reply empty, `finish_reason "length"`, `reasoning` non-empty, cap 120: the retry body cap is 1024; the success is returned; a later call for that model starts at 1024.
  - `test_reasoning_retry_once_only`: still empty after the retry: raises the "reply budget" error after 2 requests.
  - `test_reasoning_content_field_counts_as_reasoning` (the llama-server/Unsloth field name).
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement** inside `_chat`, using the constraint values.
- [ ] **Step 4: Run** this file and the Task 2 file. Expect PASS.
- [ ] **Step 5: Commit** `feat(616): parameter negotiation and reasoning retry`.

### Task 4: Rate limits and circuit breaker

**Files:**
- Modify: `src/subarr/integrations/openai_compat.py`
- Test: `tests/test_openai_compat_ratelimit.py`

**Interfaces:**
- Consumes: `CircuitBreaker` (`src/subarr/circuit_breaker.py:33`, `allow()`, `record_success()`, `record_failure()`). Inject `clock` and a patchable `asyncio.sleep` so tests do not wait.
- Produces: `LLMError` subclass `LLMCoolingDown(LLMError)` raised when the breaker is open; the message contains `"cooling down"`.

- [ ] **Step 1: Write failing tests:** `test_429_retry_after_honoured` (`retry-after: 2`: one sleep of 2, then success), `test_429_retry_after_too_long` (`retry-after: 31`: raises, no sleep), `test_429_without_retry_after_raises`, `test_503_retry_after_honoured`, `test_breaker_opens_after_5_failures` (the 6th call raises `LLMCoolingDown` with no request sent), `test_breaker_recovers_after_cooldown` (advance the clock 60 s: a request is sent again), `test_client_errors_do_not_trip_breaker` (400 and 404: client errors are not provider failures). Decision 2026-11-01 (Judd, after the content-policy and region risk was raised): a 401 or 403 from `/chat/completions` counts as a breaker FAILURE, so a revoked hosted key opens the breaker instead of retrying forever; 400 and 404 stay success, and `models()` stays outside the breaker. Landed as a follow-up to Task 5 (`test_rejected_key_opens_breaker` and siblings).
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement.** Count 429, 5xx, transport errors and (since 2026-11-01) 401/403 as failures. A reply that negotiation resolves counts as one success.
- [ ] **Step 4: Run** and expect PASS.
- [ ] **Step 5: Commit** `feat(616): rate-limit handling and breaker for the LLM client`.

### Task 5: Vision and model listing

**Files:**
- Modify: `src/subarr/integrations/openai_compat.py`
- Test: `tests/test_openai_compat_vision.py`

**Interfaces:**
- Produces: `async models() -> list[str]` (`GET /models`, the `data[].id` values); `async vision_describe(*, image_url: str | None = None, image_b64: str | None = None, prompt: str, model: str | None = None, num_predict: int = 256) -> str`; property `vision_model -> str | None` (`off` -> None; `same` -> `self.model`; otherwise the id); `async resolve_vision_model() -> str | None` returning `vision_model`, so `routers/vision.py` can call it on either client; `reset_vision_cache()` (no-op).

- [ ] **Step 1: Write failing tests:**
  - `test_models_lists_ids`.
  - `test_vision_off_raises`: `LLMError` containing `"vision not configured"`.
  - `test_vision_same_uses_text_model`.
  - `test_vision_explicit_model`.
  - `test_vision_body_shape`: user content is `[{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<b64>"}}]`, with the profile's thinking-off keys present.
  - `test_vision_png_mime_detected`: b64 of PNG magic bytes gives `data:image/png;base64,`.
  - `test_vision_url_fetched_server_side`: given `image_url`, the client fetches it and sends a data URI, never the URL.
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement.** Mirror `OllamaClient.vision_describe`'s fetch (`integrations/ollama.py:330-340`, 30 s fetch timeout). The SSRF allowlist stays in the router, unchanged.
- [ ] **Step 4: Run** and expect PASS.
- [ ] **Step 5: Commit** `feat(616): vision and model listing for the OpenAI-compatible client`.

### Task 6: Wire one active LLM client into the app

**Files:**
- Modify: `src/subarr/integrations/llm.py` (add the factory)
- Modify: `src/subarr/app.py:621,626,1148` and the Arena explainer closure ~497
- Modify: `src/subarr/routers/onboarding.py:899-935` (`_rebuild_runtime_clients`)
- Modify: `src/subarr/routers/enrichment.py` (`/health`, `/lang`, `/lang/bulk`, `/unload`, `_gpu_busy` use)
- Modify: `src/subarr/routers/vision.py` (`/status`, `/pull`, `/check`)
- Modify: `src/subarr/routers/home.py:381-395`
- Modify: `src/subarr/routers/integrations.py` (`_probe` for the LLM, `OllamaProbeResult` use ~299-309)
- Modify: `src/subarr/arena_explain.py` (catch `LLMError` only if it narrows today's broad `except`; keep it best-effort)
- Modify: the 2 tests that reference `state.ollama` (find with `grep -rln "state\.ollama" tests`)
- Test: `tests/test_llm_wiring.py`

**Interfaces:**
- Consumes: Tasks 2-5.
- Produces: `build_llm_client() -> OllamaClient | OpenAICompatClient` (reads `settings.llm_provider`; an unknown value falls back to `ollama` with one warning log); `app.state.llm`. `app.state.ollama` no longer exists: every caller uses `app.state.llm`. Callers catch `LLMError`, not `OllamaError`.

- [ ] **Step 1: Write failing tests:**
  - `test_factory_selects_provider` (both values plus an unknown value).
  - `test_openai_unconfigured_startup` (`LLM_PROVIDER=openai`, no URL: the app starts; `/api/enrichment/health` returns `configured: False`; `/api/vision/check` 503; `/api/enrichment/lang` returns the existing not-configured response).
  - `test_rebuild_swaps_provider` (change `settings.llm_provider`, call `_rebuild_runtime_clients(state, reprobe=False)`: `state.llm` is the new type, and the old client's `aclose` was awaited).
  - `test_gpu_gate_skipped_for_hosted` (`is_local_gpu False`: `_gpu_busy` is not consulted).
  - `test_gpu_gate_kept_for_local_profile`.
  - `test_bulk_stops_when_cooling_down` (the 2nd item raises `LLMCoolingDown`: the response holds 1 result, and the remaining items are listed with error `"cooling down"`, never attempted).
  - `test_unload_noop_for_openai` (`/api/enrichment/unload` returns 200 with `unloaded: False`).
  - `test_vision_status_openai` (`vision_capable` reflects `openai_vision`, `provider == "openai"`, no `suggested_pull`).
  - `test_vision_pull_rejected_for_openai` (409, "not supported by this provider").
  - `test_health_uses_models_list` (`/api/enrichment/health` lists `models()` for openai).
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement.** Branch on the capability flags, never on `isinstance`. `OllamaProbeResult` stays for Ollama. For openai, `_probe` uses `models()` as liveness and the model list under the same probe name the UI reads (check `settings.jsx:429-434` for which keys it reads).
- [ ] **Step 4: Run** `pytest -q` (the full suite: this task touches many routers). Expect all green.
- [ ] **Step 5: Commit** `feat(616): one active LLM client on app.state.llm`.

### Task 7: Credentials API, mask skip, Test connection

**Files:**
- Modify: `src/subarr/routers/integrations.py:39-49` (`_CREDENTIAL_FIELDS`), `:93` (`CredentialsBody`), `save_credentials` (~142)
- Modify: `src/subarr/routers/onboarding.py:64-80` (`_resolve_masked_credential`), `:178` (test dispatch), plus a new `_test_openai`
- Test: `tests/test_llm_credentials.py`

**Interfaces:**
- Produces: integration `openai`: `{"url": ("openai_base_url", str), "api_key": ("openai_api_key", str), "model": ("openai_model", str), "vision": ("openai_vision", str), "server_type": ("openai_server_type", str)}`; integration `llm`: `{"provider": ("llm_provider", str)}`. `CredentialsBody` gains `vision`, `server_type` and `provider` (all `str | None`). `_test_openai(body) -> dict` returns `{"ok": bool, "steps": [{"name": str, "ok": bool, "detail": str}]}` with step names exactly `reachable`, `key_accepted`, `model_listed`, `json_honoured`, `thinking_off`, `vision` (the last only when vision is not `off`).

- [ ] **Step 1: Write failing tests:**
  - `test_openai_config_masks_key` (GET `/api/integrations/openai/config`: the key is shown as `••••` + last 4).
  - `test_save_skips_masked_secret` (PUT with `api_key="••••abcd"` leaves the stored key unchanged; the same holds for `sonarr`, to pin the generalised skip).
  - `test_save_provider_rebuilds_client` (PUT `/api/integrations/llm/credentials {"provider": "openai"}`: `app.state.llm` is `OpenAICompatClient`).
  - `test_provider_env_managed` (with `LLM_PROVIDER` set, the PUT reports `managed_by_env`).
  - `test_test_openai_all_steps` (MockTransport: models lists the model; the JSON reply `{"iso_code": "ja", ...}` for the fixed title "Shingeki no Kyojin"; vision `same` and the reply `"Red"` to the known red PNG: every step ok).
  - `test_test_openai_404_models_hint` (detail contains `"/v1"`).
  - `test_test_openai_thinking_leak_reported` (a reply carrying non-empty `reasoning`: `thinking_off.ok` False, the other steps still run).
  - `test_test_openai_uses_stored_key_when_masked`.
  - `test_test_openai_bypasses_learned_cache` (calls `generate(..., use_learned=False)`).
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement.** The red PNG is generated in code: a 64x64 solid `#ff0000` image, built with zlib+struct as in the spec's probe, no new dependency. Vision passes when the lower-cased reply contains `"red"`.
- [ ] **Step 4: Run** this file plus `pytest tests/test_integration_credentials.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(616): credentials and Test connection for the OpenAI-compatible provider`.

### Task 8: Title-language prompt, library name plus relative path, and the eval script

**Files:**
- Modify: `src/subarr/enrichment.py:35-43` (`_SYSTEM_PROMPT`, `_USER_TEMPLATE`, the `enrich_one` prompt build, catch `LLMError`), and pass `schema_name="subarr_lang"` to `generate`
- Create: `scripts/eval_title_language.py`
- Test: `tests/test_enrichment.py` (extend)

**Interfaces:**
- Consumes: `paths.library_for_canonical(canonical) -> Library` (`.name`) and `paths._split_canonical(canonical) -> (slug, rel)` (import it, or add a public `relative_part(canonical) -> str` in `paths.py`, then reuse it).
- Produces: `build_user_prompt(title: str, canonical_path: str) -> str` returning exactly `f"Title: {title}\nLibrary: {library.name}\nPath: {rel}"`.

- [ ] **Step 1: Write failing tests:**
  - `test_prompt_library_and_relative_path` (canonical `@anime/Show (2013)/S01E01.mkv`, library named "Anime": the prompt equals `"Title: T\nLibrary: Anime\nPath: Show (2013)/S01E01.mkv"`; it contains no `@anime/`).
  - `test_prompt_default_library` (no `@` head: the library-0 name and the full relative path).
  - `test_prose_reply_with_schema_raises` (the model replies `"It is Japanese."` while a schema was requested: no `iso_code` stored from a guess, and the stored row has `iso_code None`. Match the existing contract in `_parse_structured`; if that already falls back to `parse_iso` on bare text, keep that behaviour only for a bare ISO token, and treat a sentence as an error).
  - `test_enrich_catches_llm_error` (an `OpenAICompatClient` raising `LLMError`: the error row is stored, then re-raised as today).
- [ ] **Step 2: Run** `pytest tests/test_enrichment.py -v` and expect the new tests to FAIL.
- [ ] **Step 3: Implement.** In `_SYSTEM_PROMPT`, the phrase "and folder path" becomes "the library it is filed in and its path inside that library".
- [ ] **Step 4: Run** and expect PASS.
- [ ] **Step 5: Write the eval script.** CLI: `python scripts/eval_title_language.py --n 60 [--arm old|new|both]`. It reads Radarr and Sonarr URLs and keys from subarr settings. It takes `originalLanguage` as truth (ISO 639-1 mapped from the arr language name), N split half English and half not, at temperature 0, through the active provider (`build_llm_client()`). It prints per-arm `correct/N`, the English and foreign splits, and each disagreement. The `old` arm uses the pre-change template kept inside the script. It never writes to the enrichment store.
- [ ] **Step 6: Commit** `feat(616): language prompt sends library name and relative path; eval script`.

### Task 8b: Clear rows the old free-text fallback stored as a guess

Judd asked for this on 2026-11-01, after Task 8 (`d2b88d2`). Before Task 8, `_parse_structured` fell back to `parse_iso` on ANY non-JSON reply. That meant a prose reply was stored as its first word (`"It is Japanese."` became `it`), and a truncated JSON reply was stored as `iso`. Rows with a non-NULL `iso_code` are cached, so they are never asked again.

**Files:**
- Modify: `src/subarr/enrichment.py` (a `requeue_unparseable(store) -> int` helper, or an `EnrichmentStore` method)
- Wire: run it once at startup, after `run_migrations()`. Or ship it as a numbered migration if the migration runner can call Python; check `src/subarr/migrate.py` first and pick one.
- Test: a new `tests/test_enrichment_requeue.py`

**Rule:** every `lang_enrichment` row whose `iso_code` is not NULL is re-parsed from its stored `raw_response` with the CURRENT `_parse_structured`. Where that raises `NotJSONReply`, set `iso_code = NULL` and `error = "reply was not valid JSON"`, and keep `raw_response` (the row is then retried on the next walk). Every other row is untouched, including a row whose re-parse gives a different code than the one stored (never rewrite a code silently). The function is idempotent, and it logs how many rows it cleared.

**Edge cases to test:**
- the prose row (`it`) is cleared
- the truncated-JSON row (`iso`) is cleared
- valid JSON, a bare code (`ko\n`), `es</th>`, and an `und` row that is already NULL are all untouched
- an empty `raw_response` with a non-NULL `iso_code` is cleared
- a second run clears 0
- a cleared row is re-asked by `enrich_one` (not a cache hit)

- [ ] Steps: failing tests, implement, run, commit `fix(616): clear language guesses the old free-text fallback stored`.

### Task 9: Telemetry field

**Files:**
- Modify: `src/subarr/telemetry.py` (~560-576)
- Test: `tests/test_telemetry.py` (extend)
- Check first: the `coaxk/subarr-telemetry` ingest (`gh repo clone coaxk/subarr-telemetry` into a temp dir and grep its payload schema). If it rejects unknown fields, STOP and report back: that repo needs the field added first, in its own PR.

**Interfaces:**
- Produces: payload key `llm_provider` in `{"ollama", "openai-local", "openai-hosted"}`. The existing `ollama` boolean keeps its meaning (Ollama reachable, False when another provider is active).

- [ ] **Step 1: Write failing tests** `test_llm_provider_field` (all three values) and `test_payload_has_no_llm_secrets` (with the url `http://secret-host/v1`, key `sk-test-123` and model `m-secret` set, the serialised payload contains none of those strings).
- [ ] **Step 2-4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat(616): telemetry reports the LLM provider kind`.

### Task 10: Settings UI

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/settings.jsx` (fields map :165, `INTEGRATION_ORDER` :413, probe labels :429-434, `OllamaVisionCard` mount :1866, unload row :1953-1959)
- Regenerate: `settings.bundle.js` and `.map` via `npm run build:frontend`
- Test: the existing frontend test location (see memory `reference_subarr-frontend-component-tests`: React is VENDORED; find the current settings component tests with `grep -rl "settings" tests/frontend` or the vitest config) plus `npm run check:frontend`

**Interfaces:**
- Consumes: Task 7's endpoints and step names; Task 6's `/api/vision/status` `provider` key.

- [ ] **Step 1: Write failing component tests:**
  - Provider select renders both options.
  - Choosing "OpenAI-compatible" shows Base URL, API key (secret), Model, Vision (Off / Same as text model / other model id) and Server type (Auto / Local server / OpenAI / OpenRouter), and hides the Ollama vision pull card and the "Unload model" row.
  - The URL hint text contains `http://unsloth:8000/v1`, `https://api.openai.com/v1` and `https://openrouter.ai/api/v1`.
  - With a hosted profile, the line "Sent to this service: titles with library name and file path inside the library, Arena score summaries with the file name, and video frames when vision is on." is shown (exact copy).
  - Test connection renders one row per returned step.
- [ ] **Step 2: Run** and expect FAIL.
- [ ] **Step 3: Implement** within the existing rail and dirty-bar pattern. The provider save goes to `/api/integrations/llm/credentials`.
- [ ] **Step 4: Run** the frontend tests, then `npm run build:frontend`, then `npm run check:frontend`. Expect all green and no diff.
- [ ] **Step 5: Commit** `feat(616): settings UI for the OpenAI-compatible provider`.

### Task 11: Full gates, live verification, docs

Main session with Judd present. Not delegated.

- [ ] **Step 1:** `ruff check . && ruff format --check .`, `pytest -q`, frontend suite, `npm run check:frontend`. Expect all green. Record the counts.
- [ ] **Step 2: Local profile, live.** In a subarr-next-like test container (never the production `C:\Projects\subarr` checkout), set provider openai, base `http://ollama:11434/v1`, model `gemma4:12b-it-qat`, vision `same`. Test connection: every step ok. `/api/enrichment/lang` returns `ja` for "Shingeki no Kyojin". `/api/vision/check` parses a real Plex thumbnail. One Arena explanation is non-empty.
- [ ] **Step 3: OpenRouter, live.** Judd enters his key through the UI or env (never in chat). Run the same four checks with one reasoning model and one non-reasoning model. Record the negotiation outcome per model from the logs (shape learned, retries).
- [ ] **Step 4: Eval.** Run `scripts/eval_title_language.py --arm both` on gemma4. Acceptance: new >= old - 1.
- [ ] **Step 5: Docs.** Add a CHANGELOG Unreleased entry naming the servers tested live (Ollama `/v1`, OpenRouter) and those tested only against documentation (OpenAI direct, Unsloth Studio, llama-server). Add a README/docs section on configuring the provider and its env vars.
- [ ] **Step 6:** Open the PR and wait for CI 19/19. Merge only after Judd's OK.
