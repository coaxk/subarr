# OpenAI-compatible chat completions research (2026-10-31)

Method note: doc pages were read via WebFetch (a small model summarises the page, so "quotes" from WebFetch are as returned by that tool and should be treated as near-verbatim, not guaranteed byte-exact). Source-code quotes (Unsloth, llama.cpp) were read directly from raw files with grep/sed.

Pinned commits: unslothai/unsloth `c06ae15b0f17d2db559000e8fadf06af353eae38` (main); ggml-org/llama.cpp `0ec4027a2a0183de87c007069adddc06e665f7de` (master).

## 1. Unsloth Studio

Code lives under `studio/backend/` (`models/inference.py` = request/response schemas, `routes/inference.py` = routes, `core/inference/grammar_constraint.py`).
Base: https://github.com/unslothai/unsloth/blob/c06ae15b0f17d2db559000e8fadf06af353eae38/studio/backend/

### response_format - YES (json_object, json_schema, text)
`models/inference.py` ~L2858:
```
response_format: Optional[Dict[str, Any]] = Field(None, description = ('Guided decoding contract, typically `{"type": "json_object"}` or a `json_schema`. `{"type": "text"}` names the default and constrains nothing. Anything else needs a grammar engine, and a backend without one rejects the request rather than answering text that ignores the contract.'
```
`core/inference/grammar_constraint.py`:
```
SUPPORTED_RESPONSE_FORMAT_TYPES = ("text", "json_object", "json_schema")
MISSING_ENGINE_MESSAGE = ("response_format needs a grammar engine, and llguidance did not load. Install or repair it with `pip install llguidance==1.8.0`, or load a GGUF model to use llama.cpp's.")
```
`routes/inference.py` ~L45826 (GGUF path forwards to llama-server): `"""llama-server reads a json_schema schema only from ``json_schema.schema`` (tools/server/server-common.cpp:1170)."""` and ~L38748 builds `{"type": "json_schema", "json_schema": {"name": ..., "schema": ...}}`. So the standard OpenAI nested shape `response_format:{type:"json_schema", json_schema:{name,schema,strict}}` is the safe one.
Note: for GGUF models constraint = llama.cpp grammar; for MLX/safetensors = llguidance. Whether it works with every loaded model: UNKNOWN (no runtime test; read-only).

### Reasoning in replies - `reasoning_content` field (OpenAI/DeepSeek style)
`models/inference.py` L3726 (streaming ChoiceDelta) and L3763 (CompletionMessage): `reasoning_content: Optional[str] = None`.
`routes/inference.py` ~L30213: `"""Native thinking arrives as a leading <think> block in content (the Studio chat's form); OpenAI clients read it from reasoning_content. ..."""` i.e. server extracts `<think>` into `reasoning_content` for OpenAI clients. Exact behaviour when a template does not open `<think>` ("else answers become reasoning_content", L4460 comment: `# Prefill only when the generation prompt opens <think>; else answers become reasoning_content.`) is a known edge: with a template that opens the think block, an uncapped/truncated reply can land entirely in reasoning_content with empty content. Client should read content only and treat empty content + non-empty reasoning_content as "truncated while thinking".

### Disable thinking per request - YES, three ways
`models/inference.py` L2982 and L2742:
```
enable_thinking: Optional[bool] = Field(None, description = "[x-unsloth] Enable/disable thinking/reasoning mode for supported models",
reasoning_effort: Optional[Literal["none","minimal","low","medium","high","max","xhigh"]] ... "[x-unsloth] Reasoning effort level ..."
```
`routes/inference.py` ~L38786: `# chat_template_kwargs arrives in model_extra; lift it so both chat paths see it.` then `_chat_template_reasoning_kwargs(payload)` -> `chat_kwargs.update(...)`, `if "enable_thinking" in _nested_reasoning:` - so `chat_template_kwargs: {"enable_thinking": false}` is also honoured. Also Anthropic-style `thinking` maps onto enable_thinking (`resolve_thinking_onto_enable_thinking`).
Docs (https://unsloth.ai/docs/integrations/connect-curl-and-http-to-unsloth): "`false` to disable thinking. `true` by default" / "To disable thinking pass `enable_thinking: false` in your request." (top-level body field, port 8888 in examples). Server flag: "Use `--reasoning off` to turn thinking off".
Whether `reasoning_effort:"none"` actually disables thinking on Gemma 4: UNKNOWN (Gemma 4 template reasoning_style likely `enable_thinking`; `models/inference.py` L1757 defines styles `enable_thinking | reasoning_effort | enable_thinking_effort`). Use enable_thinking.
Unsloth bundles `studio/backend/assets/chat_templates/gemma-4.jinja` and `gemma-4-edge.jinja`.

### Image input - YES
Docs: "Attach an image as an `image_url` content part in the user message." "The URL can be HTTPS or a base64 `data:` URI:" example `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,$IMG"}}`; "The loaded model must be multimodal." "If you load a text-only model the request succeeds structurally but the model won't process the image." (silent failure mode!). Code: `models/inference.py` L2491 `type: Literal["image_url"]`.

### /v1/models - YES
Docs: `curl http://localhost:8888/v1/models` ("Use the `id` field whenever a request needs a `"model"` value"). Code `routes/inference.py` L36324 `async def openai_list_models(`; entries carry `owned_by: "unsloth-studio"` (L35511). Auth: Bearer key prefixed `sk-unsloth-` (per search summary of docs).

## 2. llama.cpp llama-server
README: https://github.com/ggml-org/llama.cpp/blob/0ec4027a2a0183de87c007069adddc06e665f7de/tools/server/README.md ; code: tools/server/server-common.cpp

### response_format - YES, but json_schema shape has a README/code mismatch
README L1445: "The `response_format` parameter supports both plain JSON output (e.g. `{"type": "json_object"}`) and schema-constrained JSON (e.g. `{"type": "json_object", "schema": {...}}` or `{"type": "json_schema", "schema": {...}}`)".
CODE (server-common.cpp ~L1385-1397) is authoritative:
```
if (response_type == "json_object") {
    if (response_format.contains("schema") || json_schema.empty()) {
        json_schema = json_value(response_format, "schema", json::object());
    }
} else if (response_type == "json_schema") {
    auto schema_wrapper = json_value(response_format, "json_schema", json::object());
    json_schema = json_value(schema_wrapper, "schema", json::object());
} else if (!response_type.empty() && response_type != "text") {
    throw std::invalid_argument("response_format type must be one of \"text\" or \"json_object\", but got: " + response_type);
```
=> For `json_schema` the schema MUST be at `response_format.json_schema.schema` (standard OpenAI shape); the README example with top-level `schema` under type json_schema is WRONG for current code (it would yield an empty schema => "any object"). Top-level `schema` works only with `json_object`. Also native `json_schema` body param and `grammar` are mutually exclusive ("Cannot use both json_schema and grammar"). Unsupported schema features fall back/err per json-schema-to-grammar; exact list UNKNOWN (README points at tests/test-json-schema-to-grammar.cpp).

### Reasoning - `reasoning_content`, controlled by `--reasoning-format`
README L236: "`--reasoning-format FORMAT` | controls whether thought tags are allowed and/or extracted from the response, and in which format they're returned; one of: - none: leaves thoughts unparsed in `message.content` - deepseek: puts thoughts in `message.reasoning_content` - deepseek-legacy: keeps `<think>` tags in `message.content` while also populating `message.reasoning_content` (default: auto)". Per-request `reasoning_format` param: README L1451 "The reasoning format to be parsed. If set to `none`, it will output the raw generated text." L1567: "The server supports parsing and returning reasoning via the `reasoning_content` field, similar to Deepseek API."
Also server flags: `-rea, --reasoning` "Use reasoning/thinking in the chat ('on', 'off', or 'auto', default: 'auto' (detect from template))" and `--reasoning-budget N` "token budget for thinking: -1 for unrestricted, 0 for immediate end, N>0 for token budget (default: -1)" (via earlier WebFetch of the README; I did not re-grep these two lines in the raw file).

### chat_template_kwargs - YES
README L1447: "`chat_template_kwargs`: Allows sending additional parameters to the json templating system. For example: `{"enable_thinking": false}`". Code server-common.cpp L1487-1490: `// parse the "enable_thinking" kwarg to override the default value` then `json_value(inputs.chat_template_kwargs, "enable_thinking", std::string(""))` compared to "true"/"false". NOTE: the kwarg is parsed as a STRING compare (value must arrive as JSON bool or string "true"/"false"; json_value with std::string on a bool - behaviour on bool UNKNOWN from this read; the README example uses a bool, so assume works). Not verified at runtime.

### image_url - YES (needs mmproj / multimodal build)
Code L1277-1288: `if (type == "image_url")` ... replaced with a media marker. README: "`image_url.url` can be a remote URL, base64 (raw or URI-encoded via `data:image/...;base64`) or path to local file" (path to local file requires `--media-path` per embeddings section). A text-only model load rejects/ignores images: exact error UNKNOWN.

### /v1/models - YES
README: "The returned list always has one single element." (non-router mode); router mode forwards loaded model info.

### max_tokens
server-common.cpp L1189/1549: `max_tokens` is accepted and overridden by `n_predict` if present. `max_completion_tokens` in server-common.cpp: grep found NO match => UNKNOWN/probably ignored by llama-server; send `max_tokens`.

## 3. OpenAI API
- max_tokens: reference (https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create): "This value is now deprecated in favor of `max_completion_tokens`, and is not compatible with [o-series models]". `max_completion_tokens`: "An upper bound for the number of tokens that can be generated for a completion, including visible output tokens and reasoning tokens."
- Which models reject max_tokens: official reference only names o-series. Community reports (OpenAI forum/GitHub, not official; via search) that gpt-5 / gpt-5-mini return 400 `Unsupported parameter: 'max_tokens' ... use 'max_completion_tokens'`; a .NET SDK issue says gpt-5-chat did not. Authoritative per-model list: UNKNOWN. Safe rule: send `max_completion_tokens` to api.openai.com always.
- Does max_completion_tokens work on all current chat models: official ref presents it as the replacement for all; no doc statement of an exception found. Practically yes for Chat Completions on api.openai.com. NOT supported by many local servers (llama.cpp see above) - hence per-provider param name.
- Non-default temperature: official latest-model guide (https://developers.openai.com/api/docs/guides/latest-model): "When reasoning effort is not `none`, remove `temperature`, `top_p`, and `top_logprobs`." (page describes a "GPT-6" family; it "does not say the API rejects them"). Community reports: gpt-5 family and o-series 400 with "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported" (sources: https://community.openai.com/t/temperature-in-gpt-5-models/1337133, https://bbs.pyvideotrans.com/show/4887). Exact model list: UNKNOWN. Safe rule: omit temperature for reasoning/gpt-5+ models, send only for gpt-4.x/4o non-reasoning.
- response_format json_schema: yes. Structured outputs guide (https://developers.openai.com/api/docs/guides/structured-outputs): "only supported with the `gpt-4o-mini`, `gpt-4o-mini-2024-07-18`, and `gpt-4o-2024-08-06` model snapshots and later." Older (gpt-4-turbo) use json_object. Shape for Chat Completions: `{"type":"json_schema","json_schema":{name,strict,schema}}`; json_object = "the older JSON mode ... ensures the message the model generates is valid JSON" (prompt must mention JSON - from OpenRouter/OpenAI general knowledge, not re-quoted here). Refusal: response has a `refusal` field that may not match schema. Strict-mode schema requirements (additionalProperties:false, all properties required): not stated in prose in the fetched portion (first 100k chars only) - UNKNOWN from this read, but all examples follow it.
- 429: https://developers.openai.com/api/docs/guides/rate-limits - error identified by status/type/code: type `rate_limit_error`, code `slow_down` ("Your request rate increased too quickly."); full JSON body example not shown on that page (standard shape `{"error":{"message","type","param","code"}}` is the general API shape, not quoted from this page). Headers: x-ratelimit-limit-requests, -limit-tokens, -remaining-requests, -remaining-tokens, -reset-requests, -reset-tokens, plus x-ratelimit-{limit,remaining,reset}-project-tokens (when a project limit applies). Retry-After: "The minimum number of seconds to wait before retrying a temporary rate-limit error, when present." Appears on 429 (temporary limits) and 503 (temporary overload); "treat the value as a minimum and add a small random delay". Not always present => must fall back to backoff, and x-ratelimit-reset-* are duration strings (e.g. "1s", "6m0s" per OpenAI convention - format NOT confirmed in fetched text).
- Insufficient quota also returns 429 (code `insufficient_quota`) - from general knowledge, NOT verified in this session: UNKNOWN here.

## 4. OpenRouter
- max_tokens / max_completion_tokens (https://openrouter.ai/docs/api-reference/parameters): max_tokens: "This sets the upper limit for the number of tokens the model can generate in response." For reasoning models "this limit covers reasoning tokens and visible output together on most providers." max_completion_tokens: "This parameter shares `max_tokens` semantics, including the reasoning-token caveat above." => both accepted.
- Reasoning (https://openrouter.ai/docs/guides/best-practices/reasoning-tokens): response field `choices[].message.reasoning` (plus `reasoning_details` array; streaming `delta.reasoning`/`delta.reasoning_details`). Request: `reasoning: {effort: max|xhigh|high|medium|low|minimal|none, max_tokens, exclude, enabled}` ("Set one of `effort` or `max_tokens`, not both"). `exclude`: "The model will still use reasoning, but it won't be returned in the response." (Chat Completions only). `include_reasoning`: "include_reasoning: false is equivalent to reasoning: { exclude: true }" (legacy). Parameters page: `reasoning_effort` OpenAI-style, "`include_reasoning`: Deprecated alias for reasoning.exclude."
- Disable reasoning: "Set `reasoning.effort` to `"none"`"; "Some models always reason. If a model's `reasoning.mandatory` is `true`, don't send `effort: "none"`, because the model rejects it." Also `reasoning:{enabled:false}` is mentioned only as the mapping from Anthropic `thinking:{type:"disabled"}`. IMPORTANT: `exclude:true` only hides, it still spends and bills tokens against the cap.
- response_format (https://openrouter.ai/docs/guides/features/structured-outputs): "Structured outputs are supported by select models" and per-endpoint (check `structured_outputs` in the model's Providers section). Set `provider: {require_parameters: true}` to route only to compatible endpoints; unsupported => "will fail with an error indicating lack of support". Without require_parameters, overview page says for unsupported parameters "the parameter is ignored. The rest are forwarded to the underlying model API." => silent ignoring of response_format is possible; json_object is also listed. Parameters page: json_object advice "instruct the model to produce JSON in a system or user message".
- Headers (https://openrouter.ai/docs/api-reference/overview): both OPTIONAL. `HTTP-Referer` "Identifies your app on openrouter.ai" ("Optional. Site URL for rankings"); `X-OpenRouter-Title` "Sets/modifies your app's title (`X-Title` also accepted)." Auth is `Authorization: Bearer`.
- 429 (https://openrouter.ai/docs/api-reference/errors): `{"error":{"code":429,"message":"Rate limit exceeded","metadata":{"error_type":"rate_limit_exceeded","provider_code":"rate_limited"}}}`; "may include a standard HTTP `Retry-After` response header indicating how many seconds to wait before retrying" (429, 503, some 402; example `Retry-After: 60`). Note: OpenRouter can also return HTTP 200 with an `error` object mid-stream/for some provider failures - not verified here: UNKNOWN.

## 5. Gemma 4 thinking toggle
Official: https://ai.google.dev/gemma/docs/capabilities/thinking
- "pass `enable_thinking=True`, the processor will insert the correct thinking tokens into the prompt" (chat-template kwarg).
- Template when thinking ON: "`<|turn>system\n<|think|><turn|>\n<|turn>user\n[Prompt]<turn|>\n<|turn>model`" - i.e. the `<|think|>` token at the start of the system turn is the switch. HF model card (https://huggingface.co/google/gemma-4-12b-it): "Thinking is enabled by including the `<|think|>` token at the start of the system prompt." Remove it to disable; model card example passes `enable_thinking=False` to `apply_chat_template`.
- Thinking OFF differs by size: E2B/E4B: `<|turn>user\n[Prompt]<turn|>\n<|turn>model` (no empty thought channel); 12B/26B/31B: "`<|turn>user\n[Prompt]<turn|>\n<|turn>model\n<|channel>thought\n<channel|>`" - docs: "We've added an empty thinking token to the chat template for `gemma-4-12B-it`, `gemma-4-26B-A4B-it` and `gemma-4-31B-it`. This stabilizes model output by suppressing "ghost" thought channels that may appear even when thinking is deactivated."
- Implication for servers: `chat_template_kwargs:{"enable_thinking":false}` (llama-server, Unsloth, vLLM) or Unsloth top-level `enable_thinking:false` toggles it through the jinja template; a client cannot rely on a system-prompt edit unless it controls the template. Reasoning output form: `<|channel>thought ... <channel|>` (parsed into reasoning_content by llama-server/Unsloth when the parser knows the format - whether `--reasoning-format auto` parses Gemma 4's channel markers in current master: UNKNOWN, not verified).
- Community note (search result, unverified): thinking-capable 26B/31B templates wrap every assistant turn in an empty thought channel when thinking disabled.

## Items explicitly UNKNOWN / contradictory
1. llama.cpp README json_schema example (top-level `schema`) contradicts code (needs nested `json_schema.schema`).
2. llama-server `max_completion_tokens` handling: no match in server-common.cpp.
3. Authoritative OpenAI list of models rejecting `max_tokens` / non-default `temperature`: only community evidence; official docs name o-series for max_tokens and the (GPT-6-named) latest-model guide for temperature.
4. Unsloth `reasoning_effort:"none"` effect on Gemma 4; Unsloth behaviour for images on text-only model is silent ignore (docs).
5. OpenAI strict-schema constraints and 429 JSON body not re-quoted from fetched pages.
6. LM Studio and vLLM were NOT researched (not in the numbered questions).
