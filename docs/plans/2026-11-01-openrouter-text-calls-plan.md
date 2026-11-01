# OpenRouter Text Calls (Qwen for Titles and Speech) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Qwen3.8 27B (OpenRouter)" as a fixed extra choice for titles and for the speech adaptation, on top of a reusable OpenRouter text call, with a fallback to the Claude/Codex agents.

**Architecture:** A new module `src/twicc/openrouter_text.py` makes one OpenRouter chat call (streaming or whole answer, provider forced, one error type, one log line). The speech adaptation gains a `qwen` engine: `AdaptedParts` switches to an agent engine when Qwen fails before its first part, and the views try two cache keys. Titles gain a `qwen` model: `suggest_title` tries Qwen once, then the agents' chain. The frontend shows the choice in both settings, enabled only when the existing `key_present` flag of the TTS payload is true.

**Tech Stack:** Python 3.13, Django 6 ASGI, httpx (async, SSE parsed by hand), orjson, pytest; Vue 3 `<script setup>`, Pinia, Web Awesome 3, `node --test`.

**Spec:** `docs/plans/2026-11-01-openrouter-text-calls-design.md` (read it first; it extends `docs/plans/2026-11-01-tts-speech-rewrite-design.md`).

## Global Constraints

- Work on `main`. Prefix every shell command with `cd /home/twidi/dev/twicc-poc && `. Never `uv pip`, never `--active`.
- Python tests: `uv run pytest <files> -q`. Lint: `uvx ruff check <paths>`. Frontend tests: `cd frontend && npm test -- <files>` (`npm test` is `node --test`: plain `node:test` + `node:assert/strict`, no DOM, no component mount, no Pinia store import). Follow the existing pattern: behaviour lives in pure functions (`constants.js`, `utils/*.js`) imported and tested directly; a component or a store is tested by reading its source (`readFileSync`) and asserting with regexes that it calls those functions (see `frontend/src/components/settings/textToSpeechSettings.test.js`).
- Log tests route the module logger straight to caplog, as `tests/test_tts_adapt.py::test_a_failure_logs_one_line_without_the_text` does (`disabled=False`, `propagate=False`, `handlers=[caplog.handler]`): `settings_test` disables existing loggers.
- Python style: `orjson`, never stdlib `json`; `NamedTuple` for immutable data; no import alias without a name collision.
- **Never read `~/.twicc/.env`, never print, log or put in a command line the OpenRouter key.** The key is read only through `twicc.paths.get_env_secret(OPENROUTER_API_KEY_ENV)`. No log line holds a prompt, a message text or an adapted text (title logs keep showing the title, as today).
- No real network or model call in tests: OpenRouter goes through `httpx.MockTransport` (the `_make_client` factory pattern of `src/twicc/tts/openrouter.py`); the agents and Qwen are faked by monkeypatching. Only Task 6 makes real calls.
- Fixed values (spec): model `qwen/qwen3.8-27b`; provider `Cerebras`, forced (`provider: {"order": ["Cerebras"], "allow_fallbacks": false}`); reasoning off (`reasoning: {"effort": "none"}`); display name `Qwen3.8 27B (OpenRouter)`; stored value `qwen` in both settings; Qwen speech limits about 5 s for the first text and 5 s between texts; Qwen title: one attempt, about 10 s.
- Never the default: no stored choice keeps today's default in both settings.
- A stored `qwen` choice is never rewritten; without the key it works as no choice (speech) or as `provider` (titles).
- Frontend: Web Awesome icon slots `slot="start"`/`slot="end"`; no new `wa-*` component without its import in `frontend/src/main.js`; never import `router.js` statically from utils/stores.
- Commits: one or more per task, Conventional Commit subject, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`. `git add` each file by name. No CHANGELOG entry. Never stash, never push.

## Review Focus

1. **OpenRouter answers from another provider** (a 200 with `provider` other than `Cerebras`, despite `allow_fallbacks: false`): the call is a failure (`wrong_provider`), so the reading or the title falls back to an agent. Test in Task 1.
2. **A stop while Qwen runs, or right when it gives way to the agent**: the HTTP request ends, and no agent call starts after the stop. Tests in Task 1 (stream closed) and Task 2 (`aclose` during Qwen, and after the switch).
3. **The key is not set while `qwen` is stored**: no OpenRouter request at all; speech uses the first running agent, titles use the `provider` route. Tests in Task 2 and Task 3.
4. **A title asked without fallback with Qwen, and Qwen fails**: the answer is `generation_failed`, and no agent is called. Test in Task 3.
5. **The cache with Qwen chosen**: a cached Haiku reading plays when there is no Qwen entry; a Qwen entry wins over it. Test in Task 2.

---

### Task 1: The OpenRouter text call

**Files:**
- Create: `src/twicc/openrouter_text.py`
- Create: `tests/test_openrouter_text.py`
- Modify: `README.md` (the `TWICC_OPENROUTER_API_KEY` row of the `.env` table, ~line 223)

**Interfaces:**
- Consumes: `twicc.paths.OPENROUTER_API_KEY_ENV`, `twicc.paths.get_env_secret`.
- Produces (used by Tasks 2 and 3):
  - `class TextModel(NamedTuple)`: `model: str`, `provider: str`, `options: dict` (extra body fields).
  - `QWEN_MODEL = TextModel("qwen/qwen3.8-27b", "Cerebras", {"reasoning": {"effort": "none"}, "max_tokens": 8000})`.
  - `class OpenRouterTextError(Exception)`, built as `OpenRouterTextError(reason, status=None)`, with `reason: str` (one of `"no_key"`, `"http"`, `"network"`, `"timeout"`, `"wrong_provider"`, `"cut"`, `"declined"`, `"stream_error"`, `"bad_answer"`) and `status: int | None` (the HTTP status for `"http"`). `"declined"` is the spec's "a refusal": a 200 answer whose `finish_reason` is `content_filter` or `error`, or whose message carries a non-empty `refusal`.
  - `def openrouter_key_set() -> bool`.
  - `async def stream_openrouter_text(model: TextModel, prompt: str, *, system_prompt: str | None = None) -> AsyncIterator[str]` — an async generator of content deltas. No time limit of its own: the caller bounds each step (as `AdaptedParts` does).
  - `async def complete_openrouter_text(model: TextModel, prompt: str, *, system_prompt: str | None = None, timeout_seconds: float) -> str` — the whole answer; a `TimeoutError` becomes `OpenRouterTextError("timeout")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_openrouter_text.py`. A fixture installs `openrouter_text._make_client` returning `httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)` and sets the key through `monkeypatch.setattr(openrouter_text, "get_env_secret", lambda name: "sk-test")`. SSE bodies are built from `data: <orjson>\n\n` lines ending with `data: [DONE]\n\n`, with a `: OPENROUTER PROCESSING` comment line first.
  - `test_the_request_forces_the_provider_and_sends_the_options`: the handler captures the request; assert URL `https://openrouter.ai/api/v1/chat/completions`, header `Authorization: Bearer sk-test`, and body `model == "qwen/qwen3.8-27b"`, `provider == {"order": ["Cerebras"], "allow_fallbacks": False}`, `usage == {"include": True}`, `reasoning == {"effort": "none"}`, `max_tokens == 8000`, `messages == [{"role": "user", "content": "hi"}]`, `stream is True` (stream mode) / `False` (complete mode); with `system_prompt="sys"`, `messages[0] == {"role": "system", "content": "sys"}`.
  - `test_stream_yields_the_content_deltas`: chunks with `provider: "Cerebras"` and contents `"Hel"`, `"lo"`, an empty content, a chunk with `finish_reason: "stop"`, then a usage-only chunk with `choices: []` and `usage: {"cost": 0.0012}` (as OpenAI-compatible streams send it) → `["Hel", "lo"]`, no failure, and the log line has the cost.
  - `test_complete_returns_the_whole_answer`: JSON `{"provider": "Cerebras", "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}], "usage": {"cost": 0.001}}` → `"Hello"`.
  - `test_failures` (parametrized over both modes), each raising `OpenRouterTextError` with the given reason: no key (`get_env_secret` → None, and the handler is never called) → `no_key`; status 401 → `http` with `status == 401`; `httpx.ConnectError` raised by the handler → `network`; `provider: "Groq"` → `wrong_provider`; `finish_reason: "length"` → `cut`; `finish_reason: "content_filter"` or `"error"`, or a `refusal` field → `declined`; a stream chunk `{"error": {"message": "x"}}` → `stream_error` (stream mode); a `data:` payload (stream) or a body (complete) that is not JSON → `bad_answer`; a complete answer with no `choices` → `bad_answer` (in the stream, a chunk with no or empty `choices` is not a failure: it is skipped, its `usage` read).
  - `test_complete_times_out`: a handler that sleeps longer than `timeout_seconds=0.05` → `timeout`; with the logger routed to caplog, exactly one record from `twicc.openrouter_text`, holding `outcome=timeout` (never `cancelled`).
  - `test_closing_the_stream_ends_the_request`: the handler answers with `stream=` a small `httpx.AsyncByteStream` subclass that yields one SSE chunk then waits on an `asyncio.Event`, and records its `aclose()`; take the first delta, `await stream.aclose()`; assert `aclose()` was recorded.
  - `test_one_log_line_without_the_text` (logger routed to caplog, both modes, a success and a `wrong_provider` failure): exactly one record from `twicc.openrouter_text`, holding the model, `Cerebras`, the outcome and the cost when given; neither the prompt (`"secret prompt"`) nor the answer text nor `sk-test` appears in any record.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_openrouter_text.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.openrouter_text`).

- [ ] **Step 3: Implement `src/twicc/openrouter_text.py`**

`CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"`. `_make_client(timeout: httpx.Timeout) -> httpx.AsyncClient` is the single factory (tests replace it). The body is `{"model", "messages", "stream", "provider": {"order": [model.provider], "allow_fallbacks": False}, "usage": {"include": True}, **model.options}`. A non-200 status raises `http` (read the body only to log its status). The stream parses SSE lines itself: skip blank lines and `:` comments, stop at `data: [DONE]`, `orjson.loads` each `data:` payload (a decode error raises `bad_answer`); a chunk with `error` raises `stream_error`; a chunk with no or empty `choices` yields nothing (its `usage` is read); the first chunk with a `provider` other than `model.provider` raises `wrong_provider`; `choices[0].delta.content` is yielded when non-empty; `choices[0].finish_reason == "length"` raises `cut`; `finish_reason` `content_filter` or `error`, or a non-empty `delta.refusal` (stream) / `message.refusal` (complete), raises `declined`; `usage.cost` is kept for the log. `httpx.HTTPError` (transport) becomes `network`. The log line is written once, in a `finally`, as `logger.info` on success and `logger.warning` on failure: `"OpenRouter text call: model=%s provider=%s mode=%s outcome=%s first_ms=%s total_ms=%s cost=%s"` (`outcome` is `ok`, the failure reason, or `cancelled` on `GeneratorExit`/`CancelledError`, which are re-raised; the whole-answer time limit is caught as `TimeoutError` outside the request and logs `timeout`, never `cancelled`). Use `httpx.Timeout(10.0, read=60.0)`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_openrouter_text.py -q && uvx ruff check src/twicc/openrouter_text.py tests/test_openrouter_text.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Update the README key row**

The `TWICC_OPENROUTER_API_KEY` row says it is the OpenRouter API key for everything that goes through OpenRouter: Text to Speech, and the Qwen choice for titles and for the speech adaptation. Keep "Restart TwiCC after a change".

- [ ] **Step 6: Commit**

```bash
git add src/twicc/openrouter_text.py tests/test_openrouter_text.py README.md
git commit  # feat(openrouter): add a reusable OpenRouter text call
```

### Task 2: Qwen in the speech adaptation (backend)

**Files:**
- Modify: `src/twicc/tts/config.py` (`ADAPT_ENGINES`, `ADAPT_REASONS`, `_last_test`)
- Modify: `src/twicc/tts/adapt.py`
- Modify: `src/twicc/tts/views.py` (`_open_speech`, `_adaptation_fields`)
- Test: `tests/test_tts_config.py`, `tests/test_tts_adapt.py`, `tests/test_tts_views.py`

**Interfaces:**
- Consumes (Task 1): `QWEN_MODEL`, `OpenRouterTextError` (`reason`, `status`), `openrouter_key_set()`, `stream_openrouter_text(model, prompt)`.
- Produces (used by Task 4 through the last-test record and tts.json):
  - `config.ADAPT_ENGINES = ("haiku", "luna", "qwen")` (so `ADAPT_CHOICES` gains `"qwen"`); `config.ADAPT_REASONS` gains `"refused"`.
  - The last-test record gains `fallback_reason` (one of `ADAPT_REASONS`, or None): why Qwen failed before an agent took over.
  - `adapt.Engine(name: str, provider: Provider | None)`; `adapt.QWEN_ENGINE = Engine("qwen", None)`; `ENGINES` stays the two agents.
  - `adapt.fallback_engine(engine: Engine) -> Engine | None`: for `QWEN_ENGINE`, the first agent of `ENGINES` whose provider runs; else None.
  - `AdaptedParts(text, engine, limit, first, *, fallback, label, then: Engine | None = None)`; new attributes `fallback_from: str | None`, `fallback_reason: str | None`; label keys `fallback_from`, `fallback_reason`.

- [ ] **Step 1: Write the failing tests**
  - Existing tests that pin the old shape and must be updated: the `LAST_TEST` dict of `tests/test_tts_config.py` (~line 307) and every exact last-test dict compared with it gain `fallback_reason`; the exact last-test key set of `tests/test_tts_views.py` (~lines 1250–1253) gains `fallback_reason`; the autouse `short_limits` fixture of `tests/test_tts_adapt.py` also sets `QWEN_FIRST_TEXT_SECONDS` and `QWEN_NEXT_TEXT_SECONDS` to 2.0 (else a Qwen stall waits 5 s and hits the tests' `asyncio.timeout(5)`).
  - `tests/test_tts_config.py`: `test_adapt_accepts_qwen` (`normalize_tts_config({"adapt": "qwen", ...})["adapt"] == "qwen"`); `test_last_test_keeps_the_fallback_reason` (`"stalled"` and `"refused"` kept, `"bogus"` → None, absent → None); the existing `test_last_test_keeps_every_adapt_reason` gains `"refused"` for `adapt_reason`; a last test with `adapted_by: "qwen"` keeps it through the normalization (`adapted_by` is gated by `ADAPT_ENGINES`).
  - `tests/test_tts_adapt.py` (the existing `fake_call` fixture fakes `_open_stream`; extend `FakeCall` so that a list of step tuples gives one stream per call, in order, and `calls` records each `(engine, prompt)`):
    - `test_qwen_resolves_only_with_the_key` (monkeypatch `adapt.openrouter_key_set` and the running providers): with the key, `resolve_engine("qwen", "t") == (QWEN_ENGINE, None)` with no provider running, with Claude only, and with both; without the key, `(HAIKU, None)` when Claude runs and `(None, "no_provider")` when nothing runs; `"too_long"` still wins first; `"off"` stays off.
    - `test_fallback_engine`: `fallback_engine(QWEN_ENGINE)` is HAIKU when Claude runs, LUNA when only Codex runs, None when none runs; `fallback_engine(HAIKU) is None`.
    - `test_open_stream_routes_qwen_to_openrouter`: monkeypatch `adapt.stream_openrouter_text` with a fake generator; `_open_stream(QWEN_ENGINE, "p")` yields its deltas and passes `(QWEN_MODEL, "p")`.
    - `test_qwen_limits` (call `monkeypatch.undo()` first, as `test_the_stall_limits` does, since the autouse fixture patches them): the Qwen first-text and next-text limits are `adapt.QWEN_FIRST_TEXT_SECONDS == 5.0` and `adapt.QWEN_NEXT_TEXT_SECONDS == 5.0`; a Qwen stream that stalls past them (patched short) is a stall, while an agent keeps `FIRST_TEXT_SECONDS`/`NEXT_TEXT_SECONDS`.
    - `test_a_qwen_failure_before_the_first_part_switches_to_the_agent` (parametrized: an `OpenRouterTextError("http", 500)`, a stall, an empty answer): first stream fails, second (HAIKU) streams a text; parts are the HAIKU text's parts; `calls` engines are `[QWEN_ENGINE, HAIKU]` with the same prompt; `adapted_by == "haiku"`; `fallback_from == "qwen"`; `fallback_reason` is `"failed"`, `"stalled"`, `"empty"`; `adapt_reason is None`; `adapt_first_ms` counts from the creation (≥ the Qwen wait); label `adapted == "haiku"`, `fallback_from == "qwen"`, `fallback_reason` set. Add a case where Qwen streams some text (less than a first part) and then stalls: after the switch, `adapt_first_ms` is the agent's first text (larger than Qwen's), `adapted_text` is the agent's text only, `adapt_reason is None`.
    - `test_a_refused_qwen_call_is_reported_as_refused`: `OpenRouterTextError("http", 401)` (and 402, 403) → `fallback_reason == "refused"`; `OpenRouterTextError("no_key")` → `"refused"` too.
    - `test_qwen_then_agent_both_fail_reads_the_original`: both streams fail → parts are `FALLBACK`; `adapted_by is None`; `adapt_reason` is the agent's reason; `fallback_reason` is Qwen's.
    - `test_qwen_without_an_agent_reads_the_original`: `then=None`, Qwen fails → `FALLBACK`, `adapt_reason` set, `fallback_reason is None`.
    - `test_a_qwen_failure_after_the_first_part_stops_the_reading`: as the existing late-failure test, with Qwen: the tail, then `SpeechError(ADAPT_STOPPED)`; the agent is never called.
    - `test_aclose_during_qwen_starts_no_agent_call`: start `next_part()` as a task while the Qwen stream waits (`HANG`); `await source.aclose()`; the Qwen fake is closed, `calls` holds only the Qwen call, and the `next_part()` task returns None.
    - `test_aclose_after_the_switch_closes_the_agent_call`: Qwen fails at once; the agent stream waits (`HANG`); start `next_part()` as a task, wait until `calls` has two entries, `await source.aclose()`; the agent fake is closed and the task returns None.
  - `tests/test_tts_views.py` (follow its existing adaptation tests: they patch the engine resolution and `AdaptedParts` streams; `RecordedAdaptedParts.__init__`, ~line 1848, takes `then=None` too and records `then` (its name or None)):
    - `test_qwen_choice_passes_the_agent_as_then`: stored `adapt` `"qwen"`, key set, Claude running → the source gets `then == HAIKU`; with no provider running, the source still adapts with Qwen and gets `then is None`.
    - `test_qwen_cache_lookup_tries_qwen_then_the_agent`: with only a `(request, "haiku")` entry, a cached read with Qwen chosen plays it (no call, label `adapted == "haiku"`); with both `(request, "qwen")` and `(request, "haiku")`, the Qwen entry plays.
    - `test_qwen_without_the_key_never_calls_openrouter`: stored `"qwen"`, `openrouter_key_set` False → the engine is the first running agent: the views' adaptation fixture records only that agent's call (e.g. `adaptation.calls == ["haiku"]`), never `"qwen"`.
    - `test_a_fallback_reading_is_cached_under_the_agent`: a cached read with Qwen chosen where Qwen fails and Haiku adapts → after the end, the cache holds `(request, "haiku")` and no `(request, "qwen")`.
    - `test_last_test_records_the_fallback_reason`: a settings test where Qwen failed and Haiku adapted → the record has `adapted_by == "haiku"`, `fallback_reason == "failed"`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_tts_config.py tests/test_tts_adapt.py tests/test_tts_views.py -q`
Expected: FAIL (no `QWEN_ENGINE`, no `then`, no `fallback_reason`).

- [ ] **Step 3: Implement**
  - `config.py`: the three constants above; `_last_test` normalizes `fallback_reason` with `_one_of(value.get("fallback_reason"), ADAPT_REASONS)`.
  - `adapt.py`:
    - `resolve_engine`: after `off` and `too_long`, `choice == "qwen"` returns `(QWEN_ENGINE, None)` when `openrouter_key_set()`, else it resolves as `None`. Its docstring says so.
    - `_open_stream`: `QWEN_ENGINE` streams `stream_openrouter_text(QWEN_MODEL, prompt)`.
    - The step limits come from the current engine: Qwen uses `QWEN_FIRST_TEXT_SECONDS`/`QWEN_NEXT_TEXT_SECONDS`, an agent `FIRST_TEXT_SECONDS`/`NEXT_TEXT_SECONDS` (read at call time, so tests can patch them).
    - `_run` maps `OpenRouterTextError` to `"refused"` when its `reason == "no_key"` or its `status` is 401, 402 or 403, else (`declined` included) to `"failed"`; it logs nothing more (the call logged its line).
    - The switch: in `next_part`, where a failure before the first returned part calls `_fall_back()` today, a pending `then` instead calls a new `_switch()`: it sets `fallback_from` (the failed engine's name) and `fallback_reason`, the label keys `fallback_from`, `fallback_reason` and `adapted = f"pending:{then.name}"`, resets the call state (a new `PartSplitter(limit, first)`, no parts, no received text, no tail, no end, no failure, `_first_from_feed = None`, `_task = None`), and every trace of the Qwen run: `adapt_first_ms = None`, `adapt_reason = None`, and the label's `adapt_first_ms` and `adapt_reason` removed (a Qwen run can have received text before it failed); then it sets `_engine = then`, `adapted_by = then.name`, `_then = None`, then the loop goes on (the next turn starts the agent's task, unless `aclose()` ran). `adapt_first_ms` keeps counting from `_created`. The old task is done when its failure is seen (`_fail` runs at its end).
    - `_fall_back()` after a switch keeps `fallback_from`/`fallback_reason` in the label and on the object.
    - The class docstring lists `then`, `fallback_from`, `fallback_reason`.
  - `views.py` `_open_speech`: `then = fallback_engine(engine) if engine == QWEN_ENGINE else None`; the cache lookup tries `(request, engine.name)` then `(request, then.name)` when `then` is set, and a hit sets `label["adapted"]` to the name of the entry that played; `AdaptedParts(..., then=then)`. `_adaptation_fields` adds `fallback_reason` (`source.fallback_reason`, None without a source). The docstrings say it.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_adapt.py tests/test_tts_views.py tests/test_tts_config.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/tts/config.py src/twicc/tts/adapt.py src/twicc/tts/views.py tests/test_tts_config.py tests/test_tts_adapt.py tests/test_tts_views.py
git commit  # feat(tts): adapt the text with Qwen on OpenRouter, falling back to an agent
```

### Task 3: Qwen for titles (backend)

**Files:**
- Create: `src/twicc/title_openrouter.py`
- Create: `tests/test_title_openrouter.py`
- Modify: `src/twicc/core/services/title_suggestion.py`
- Modify: `src/twicc/asgi.py` (`_handle_suggest_title`: the `title_suggested` frame, and the payload docstring ~line 1895)
- Modify: `src/twicc/cli/settings/_keys.py` (`titleSuggestionModel` description)
- Test: `tests/test_title_suggestion_service.py`, `tests/test_title_suggestion_routing.py`, `tests/test_session_availability.py`, `tests/test_settings_cli.py` (only if it pins the description text)

**Interfaces:**
- Consumes (Task 1): `QWEN_MODEL`, `OpenRouterTextError`, `openrouter_key_set()`, `complete_openrouter_text(model, prompt, *, timeout_seconds)`; `twicc.title_transcript.build_title_prompt`, `unwrap_title_suggestion`, `title_rejection_reasons`.
- Produces (used by Task 5 through the WS frame):
  - `title_openrouter.QWEN_TITLE_TIMEOUT_SECONDS = 10`; `async def generate_openrouter_title(user_message: str, system_prompt: str, *, current_title: str | None = None) -> str | None`.
  - `title_suggestion.QWEN_TITLE_MODEL = "qwen"`.
  - `TitleSuggestionResult` gains `requested_model: str | None = None` and `title_model: str | None = None` (after the existing fields): the model asked for (`"qwen"` when Qwen was asked and the key is set, else the model of `requested_provider`) and the model that made the title (`"qwen"`, else the model of `title_provider`; None when none did). The model of a provider is the inverse of `TITLE_SUGGESTION_MODEL_PROVIDERS` (`claude_code` → `"haiku"`, `codex` → `"luna"`). Every result `suggest_title` returns sets both (`no_prompt` and `no_provider_available` included).
  - The `title_suggested` frame gains `requestedModel` and `titleModel` (same values); `requestedProvider`/`titleProvider` stay. The early `session_unavailable` frame sends `requestedModel` = the asked `titleSuggestionModel` when it is `haiku`, `luna` or `qwen` (qwen only with the key), else the model of the session provider, and `titleModel` = None.

- [ ] **Step 1: Write the failing tests**
  - `tests/test_title_openrouter.py` (monkeypatch `title_openrouter.complete_openrouter_text`):
    - `test_builds_the_agents_prompt_and_unwraps_the_answer`: the call gets `(QWEN_MODEL, build_title_prompt(system_prompt, message, current_title))` and `timeout_seconds == 10`; the returned title equals `unwrap_title_suggestion(answer)` for an answer wrapped in `<current_title>…</current_title>` (the only wrapper it removes) and for a plain answer.
    - `test_a_rejected_title_is_none`: an answer that `title_rejection_reasons` rejects (two sentences) → None, one warning log (logger routed to caplog).
    - `test_a_failed_call_is_none`: `OpenRouterTextError("timeout")` → None; any other exception → None; one attempt only (the fake counts calls == 1).
  - `tests/test_title_suggestion_service.py` (extend the `routing` fixture: monkeypatch `title_suggestion.generate_openrouter_title` with a fake whose outcome the test sets, and `title_suggestion.openrouter_key_set`):
    - `test_qwen_makes_the_title`: `title_model="qwen"`, key set, Qwen answers → suggestion from Qwen, `title_model == "qwen"`, `requested_model == "qwen"`, `title_provider is None`, no agent called.
    - `test_qwen_failure_falls_back_like_match_session_provider` (parametrized over the session provider): Qwen fails → the session provider's agent first, then the other; `title_model` is that agent's model; `requested_model == "qwen"`; `current_title` reaches Qwen and the agents.
    - `test_qwen_without_fallback_fails_alone`: `no_fallback=True`, Qwen fails → `error == "generation_failed"`, no agent called.
    - `test_qwen_failed_and_no_agent_runs_is_generation_failed`: no running provider, Qwen fails → the Qwen fake was called once, and `error == "generation_failed"` (not `no_provider_available`).
    - `test_qwen_works_without_any_agent`: no running provider, Qwen answers → the suggestion is Qwen's, `title_model == "qwen"` (the Qwen try comes before any provider availability check).
    - `test_qwen_without_the_key_routes_as_provider`: key not set → Qwen never called; same result as `title_model="provider"`, with `requested_model` the session provider's model.
    - In `tests/test_session_availability.py` (the `session_unavailable` frame, ~line 272): the frame carries `requestedModel` (the session provider's model for no asked model) and `titleModel is None`; with `titleSuggestionModel: "qwen"`, `requestedModel == "qwen"` when the key is set (monkeypatch `openrouter_key_set` where `asgi` reads it) and the session provider's model when it is not.
    - `test_agent_models_are_reported`: `title_model="haiku"` → `requested_model == "haiku"`, `title_model == "haiku"`; a fallback to Codex → `title_model == "luna"`.
  - Update the existing `TitleSuggestionResult(...)` assertions of `tests/test_title_suggestion_service.py` (lines ~40–95) with the two model values of each case (e.g. line 56: `requested_model="luna"`, `title_model="haiku"`).
  - `tests/test_title_suggestion_routing.py`: add `requestedModel`/`titleModel` to every expected frame, and `test_qwen_title_frame` (Qwen answers: `titleProvider is None`, `titleModel == "qwen"`, `requestedModel == "qwen"`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_title_openrouter.py tests/test_title_suggestion_service.py tests/test_title_suggestion_routing.py tests/test_session_availability.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - `title_openrouter.py`: one attempt, as `_call_haiku` does in `src/twicc/providers/claude_code/title_suggest.py` (same log lines with `source="openrouter"`), through `complete_openrouter_text(QWEN_MODEL, full_prompt, timeout_seconds=QWEN_TITLE_TIMEOUT_SECONDS)`; no system prompt (the agents send the built prompt as the user prompt).
  - `title_suggestion.suggest_title`: with `title_model == QWEN_TITLE_MODEL` and `openrouter_key_set()`, try `generate_openrouter_title` first (a raised exception counts as a failure, logged); a title returns at once; else, with `no_fallback`, return `generation_failed`; else run the existing chain from the session provider, and a chain with no running provider after a Qwen try reports `generation_failed`. Without the key, `qwen` routes as `provider`. The model names come from the inverse of `TITLE_SUGGESTION_MODEL_PROVIDERS`.
  - `asgi.py`: the frame adds `requestedModel`/`titleModel`; the docstring lists `"qwen"`.
  - `_keys.py`: "Model used for title suggestions: provider, haiku, luna, or qwen (Qwen3.8 27B on OpenRouter, needs TWICC_OPENROUTER_API_KEY). A failing model falls back to the agents (Claude, Codex)."
  - Search `SKILLS-AND-CLI.md` and `src/twicc/agent/plugin/` for the old value list "provider, haiku, or luna"; update any copy (bump the plugin `version` only if a `SKILL.md` changes).

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_title_*.py tests/test_session_availability.py tests/test_settings_cli.py -q && uvx ruff check src/twicc/title_openrouter.py src/twicc/core/services/title_suggestion.py src/twicc/asgi.py src/twicc/cli/settings/_keys.py tests/test_title_openrouter.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/title_openrouter.py tests/test_title_openrouter.py src/twicc/core/services/title_suggestion.py src/twicc/asgi.py src/twicc/cli/settings/_keys.py tests/test_title_suggestion_service.py tests/test_title_suggestion_routing.py tests/test_session_availability.py
git commit  # feat(titles): make titles with Qwen on OpenRouter, falling back to the agents
```

### Task 4: Qwen in the speech adaptation (frontend and help)

**Files:**
- Modify: `frontend/src/constants.js` (`TITLE_SUGGESTION_MODEL_LABELS` gains the Qwen label; the adaptation reads its labels from there)
- Modify: `frontend/src/utils/ttsConfig.js` (the text adaptation block, ~lines 340–420)
- Modify: `frontend/src/components/settings/TextToSpeechSettings.vue` (the `tts-adapt-group`, ~lines 60–70 and 405–420)
- Modify: `frontend/src/stores/textToSpeech.js` (the comments that list the `adapt` values, ~lines 279 and 391: add `'qwen'`)
- Modify: `frontend/public/help/text-to-speech.md` (*Adapting the text*, and the OpenRouter cost sentence of *OpenRouter configs*)
- Test: `frontend/src/utils/ttsConfig.test.js` (its `adaptOptions` deep-equals, ~lines 568–584, gain the Qwen option: disabled when `keyPresent` is not passed), `frontend/src/components/settings/textToSpeechSettings.test.js` (its regexes on `effectiveAdapt`/`adaptOptions`/`noAdaptProvider` and on the two hints, ~lines 186–196, change to the new calls and template), `frontend/src/stores/settingsTitleSuggestionModel.test.js` (its label assertion, ~line 64, gains the Qwen label)

**Interfaces:**
- Consumes (Task 2): the stored `adapt` value `"qwen"`; the last-test fields `adapted_by` (may be `"qwen"`), `adapt_reason`, `fallback_reason`; the reason `"refused"`. The TTS store's `keyPresent` (already sent by `tts_client_payload`).
- Produces (Task 5 reads the label):
  - `TITLE_SUGGESTION_MODEL.QWEN = 'qwen'` and `TITLE_SUGGESTION_MODEL_LABELS.qwen = 'Qwen3.8 27B (OpenRouter)'` (Task 5 adds the title routing; this task adds only the value and the label, which `resolveTitleSuggestionModel` then accepts: Task 5 owns what titles do with it).
  - `ttsConfig.js`: `QWEN_ADAPT = 'qwen'`; `normalizeAdapt` accepts it; `effectiveAdapt(stored, enabledProviders, keyPresent = false)`; `adaptOptions(enabledProviders, keyPresent = false)`; `adaptHint(choice, enabledProviders) -> 'no_provider' | 'openrouter' | null` (the hint under the control for the effective `choice`); `ttsAdaptationFacts(lastTest)` handles Qwen and `fallback_reason`.

- [ ] **Step 1: Write the failing tests**
  - `settingsTitleSuggestionModel.test.js`: `resolveTitleSuggestionModel('qwen') === 'qwen'` (the value list gates the stored setting).
  - `ttsConfig.test.js`:
    - `normalizeAdapt('qwen') === 'qwen'`;
    - `effectiveAdapt('qwen', [claude], true)` → `'qwen'`; `effectiveAdapt('qwen', [], true)` → `'qwen'` (no agent needed); `effectiveAdapt('qwen', [claude, codex], false)` → `'haiku'` (as no choice); `effectiveAdapt(null, [claude], true)` → `'haiku'` (never the default); `effectiveAdapt('qwen', [], false)` → `'off'`.
    - `adaptOptions([claude, codex], true)` values in order `['haiku', 'luna', 'qwen', 'off']`, Qwen enabled with label `'Qwen3.8 27B (OpenRouter)'`; with `keyPresent` false the Qwen option is `disabled` with title `'Qwen3.8 27B (OpenRouter): the OpenRouter key is not set'`.
    - `ttsAdaptationFacts({adapted_by: 'qwen', adapt_first_ms: 300})` → `'Adapted by Qwen3.8 27B (OpenRouter) · first text 0.3 s'`; `({adapted_by: 'haiku', adapt_first_ms: 6200, fallback_reason: 'stalled'})` → `'Adapted by Claude Haiku 5.5 · first text 6.2 s · Qwen3.8 27B (OpenRouter) failed: the model did not answer in time'`; `({adapted_by: null, adapt_reason: 'failed', fallback_reason: 'refused'})` → `'Not adapted: the model failed · Qwen3.8 27B (OpenRouter) failed: OpenRouter refused the call (check the key and the credits)'`.
    - `adaptHint('qwen', [])` → `'openrouter'`; `adaptHint('off', [])` → `'no_provider'`; `adaptHint('haiku', [claude])` → null; `adaptHint('off', [claude])` → null (Off chosen by the user).
  - `textToSpeechSettings.test.js` (source assertions, as its existing tests do): the source calls `effectiveAdapt(store.adapt, enabledProviders.value, store.keyPresent)` and `adaptOptions(enabledProviders.value, store.keyPresent)`; a computed `adaptHintKind = computed(() => adaptHint(adaptChoice.value, enabledProviders.value))`; the template, under the control, is exactly: `<span v-if="adaptHintKind === 'no_provider'" class="setting-group-hint">No Claude or Codex provider is enabled.</span>` then `<template v-else>` holding today's `<span class="setting-group-hint">A small model rewrites the message for the ear before it is read.</span>` and `<span v-if="adaptHintKind === 'openrouter'" class="setting-group-hint">The text goes to OpenRouter and Cerebras, paid on your OpenRouter account.</span>`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/utils/ttsConfig.test.js src/components/settings/textToSpeechSettings.test.js src/stores/settingsTitleSuggestionModel.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - `ttsConfig.js`: Qwen is not in `ADAPT_PROVIDERS` (it has no provider); `effectiveAdapt` returns `'qwen'` for a stored `'qwen'` when `keyPresent`, else resolves it as no choice; `adaptOptions` adds the Qwen option between the agents and `off`; `ADAPT_REASON_TEXTS.refused = 'OpenRouter refused the call (check the key and the credits)'`; `ttsAdaptationFacts` appends ` · Qwen3.8 27B (OpenRouter) failed: <reason text>` when `fallback_reason` is set. Update the block's header comment and docstrings.
  - `adaptHint`: `'openrouter'` for `'qwen'`; `'no_provider'` when the choice is `'off'` because no agent is enabled (today's `noAdaptProvider` condition, which this replaces); null otherwise. Today's hint shows whenever no agent is enabled, even with a stored `'off'`: keep that (the second case of the test is `adaptHint('off', [])` → `'no_provider'`), only Qwen hides it.
  - `TextToSpeechSettings.vue`: pass `store.keyPresent` to `effectiveAdapt` and `adaptOptions`; the hints come from `adaptHint(adaptChoice, enabledProviders)`. Four full labels (about 400 px) cannot fit one line at 320 px, and the segments CSS forbids a wrap today (`flex: none`, `flex-wrap: nowrap`, ~lines 944–957): inside `.tts-adapt-group` only, let the segments wrap onto a second line under a container query (the format controls of the config cards keep their one-line rule), and keep the full labels. Add a source assertion on that rule to `textToSpeechSettings.test.js`.
  - Help page, *Adapting the text*: the four choices; Qwen needs the OpenRouter key, sends the message to OpenRouter and Cerebras, is paid on the OpenRouter account and is not in a test's shown cost; a Qwen failure before the reading starts hands the text to Claude, else Codex; with Qwen chosen and the key set, Qwen adapts even when no provider runs; a Qwen choice without the key gives way to the default and comes back once the key is set and TwiCC restarted. Keep the existing sentences true for every choice (spec, *Settings and help*). In *OpenRouter configs*, "After a test, the config shows what the test really cost" becomes the cost of the speech.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd frontend && npm test -- src/utils/ttsConfig.test.js src/components/settings/textToSpeechSettings.test.js src/stores/textToSpeech.test.js src/stores/settingsTitleSuggestionModel.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/constants.js frontend/src/utils/ttsConfig.js frontend/src/stores/textToSpeech.js frontend/src/components/settings/TextToSpeechSettings.vue frontend/public/help/text-to-speech.md frontend/src/utils/ttsConfig.test.js frontend/src/components/settings/textToSpeechSettings.test.js frontend/src/stores/settingsTitleSuggestionModel.test.js
git commit  # feat(tts): offer Qwen on OpenRouter for the text adaptation
```

### Task 5: Qwen for titles (frontend)

**Files:**
- Modify: `frontend/src/constants.js` (`resolveEffectiveTitleSuggestionModel`, `titleModelAlternatives`)
- Modify: `frontend/src/stores/settings.js` (`getEffectiveTitleSuggestionModel`)
- Modify: `frontend/src/stores/data.js` (the `title_suggested` handler, ~line 6873: keep `requestedModel`, `titleModel`)
- Modify: `frontend/src/components/app/SettingsPopover.vue` (Title suggestion section, ~lines 513–532 and 1946–1990)
- Modify: `frontend/src/components/session/detail/SessionRenameDialog.vue` (~lines 60–80 and 125–135)
- Modify: `frontend/src/utils/titleSuggestion.js` (two pure helpers for the rename dialog)
- Test: `frontend/src/stores/settingsTitleSuggestionModel.test.js` (it already tests the constants' title-model functions), `frontend/src/utils/titleSuggestion.test.js`, `frontend/src/stores/titleSuggestionModels.test.js` (new)

**Interfaces:**
- Consumes (Task 3): the frame fields `requestedModel`, `titleModel`; (Task 4): `TITLE_SUGGESTION_MODEL.QWEN`, its label; the TTS store's `keyPresent`.
- Produces: `resolveEffectiveTitleSuggestionModel(value, enabledProviders, openrouterKeySet = false)`; `titleModelAlternatives(currentModel, enabledProviders, openrouterKeySet = false)`; in `titleSuggestion.js`, `suggestionModel(entry) -> string | null` (the model that made the suggestion: `entry.titleModel`, else the model of `entry.titleProvider`) and `isFallbackEntry(entry) -> boolean`.

- [ ] **Step 1: Write the failing tests**
  - In `settingsTitleSuggestionModel.test.js`, the constants: `resolveEffectiveTitleSuggestionModel('qwen', [claude], true)` → `'qwen'`; `('qwen', [], true)` → `'qwen'` (no agent needed); `('qwen', [claude], false)` → `'provider'`; `('haiku', [codex], true)` → `'luna'` (a displaced agent model still gives way to the other agent, as today); `titleModelAlternatives('haiku', [claude, codex], true)` → `['luna', 'qwen']`; `('qwen', [claude, codex], true)` → `['haiku', 'luna']`; `('haiku', [claude, codex], false)` → `['luna']`.
  - `settingsTitleSuggestionModel.test.js`, source assertions on `../stores/settings.js`: the getter `getEffectiveTitleSuggestionModel` calls `resolveEffectiveTitleSuggestionModel(state.titleSuggestionModel, this.enabledProviders, useTextToSpeechStore().keyPresent)`; no write to `titleSuggestionModel` depends on the key.
  - `titleSuggestion.test.js`: `buildTitleSuggestionRequest({..., titleSuggestionModel: 'qwen'})` sends `'qwen'`; `suggestionModel({titleModel: 'qwen', titleProvider: null})` → `'qwen'`, `suggestionModel({titleModel: null, titleProvider: 'codex'})` → `'luna'`, `suggestionModel({})` → null; `isFallbackEntry` is true for `{suggestion: 't', requestedModel: 'qwen', titleModel: 'haiku'}`, false when the models match, and compares `requestedProvider`/`titleProvider` as today when a model field is missing; false without a suggestion.
  - `titleSuggestionModels.test.js` (new), source assertions on `../components/app/SettingsPopover.vue`: a `wa-radio` with `:value="TITLE_SUGGESTION_MODEL.QWEN"` whose `:disabled` depends on the key, the note `The OpenRouter key is not set.`, the fallback hint `TwiCC generates the title with another model`, the OpenRouter sentence shown when Qwen is selected, and the Qwen "comes back once the OpenRouter key is set and TwiCC restarted" sentence; and on `./data.js`: the `title_suggested` handler destructures `requestedModel` and `titleModel` and stores `requestedModel: requestedModel || null`, `titleModel: titleModel || null`; and on `../components/session/detail/SessionRenameDialog.vue`: it uses `suggestionModel(` and `isFallbackEntry(` and passes the key to `titleModelAlternatives(`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/stores/settingsTitleSuggestionModel.test.js src/utils/titleSuggestion.test.js src/stores/titleSuggestionModels.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - `constants.js`: the two functions above; `TITLE_SUGGESTION_MODEL_PROVIDERS` stays agents only.
  - `settings.js`: `getEffectiveTitleSuggestionModel` passes `useTextToSpeechStore().keyPresent` (a one-way static import of `./textToSpeech`, which imports no store).
  - `data.js`: store `requestedModel`/`titleModel` (null when absent) next to the provider fields; update the docstring.
  - `SettingsPopover.vue`: a fourth radio `Qwen3.8 27B (OpenRouter) for every session`, disabled with the note `— The OpenRouter key is not set.` when the key is absent; the fallback hint says "TwiCC generates the title with another model" (not "the other provider"); when Qwen is selected, the hint adds that the user messages go to OpenRouter and Cerebras, paid on the OpenRouter account; the "choice is kept" sentence covers Qwen: "comes back once the OpenRouter key is set and TwiCC restarted" for a displaced Qwen choice, today's sentence otherwise.
  - `SessionRenameDialog.vue`: `displayedTitleModel` uses `suggestionModel(suggestionEntry)`; `isFallbackSuggestion` uses `isFallbackEntry(suggestionEntry)`; `alternativeTitleModels` passes `keyPresent`; "Try with Qwen3.8 27B (OpenRouter)" then asks for `'qwen'` with `noFallback`, as the other alternatives do.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd frontend && npm test`
Expected: PASS (whole suite: the title code is shared).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/constants.js frontend/src/stores/settings.js frontend/src/stores/data.js frontend/src/utils/titleSuggestion.js frontend/src/components/app/SettingsPopover.vue frontend/src/components/session/detail/SessionRenameDialog.vue frontend/src/stores/settingsTitleSuggestionModel.test.js frontend/src/utils/titleSuggestion.test.js frontend/src/stores/titleSuggestionModels.test.js
git commit  # feat(titles): offer Qwen on OpenRouter in the title settings and the rename dialog
```

### Task 6: Live checks

Real calls: OpenRouter (paid, about $0.001 a call), Claude and Codex subscriptions. The user authorizes them for this task only. Never read the `.env`, never print the key.

- [ ] **Step 1 (agent, throwaway script in the session scratch dir, deleted after):**
  - Both modes of `openrouter_text` against `QWEN_MODEL`: deltas arrive, the whole answer comes back, the log line has the cost; a `TextModel` with a provider that does not serve the model fails (not served elsewhere); closing the stream after the first delta ends the request.
  - The script sets `DJANGO_SETTINGS_MODULE=twicc.settings` and calls `django.setup()` (it reads the prod DB for the title checks: read only, never a write, never `migrate`), then `twicc.paths.ensure_env_loaded()` (TwiCC's own loader: it reads the key into memory without printing it; this is allowed, reading or printing the file yourself is not).
  - A throwaway script sees no running provider (`is_provider_running` is False outside the server): pass `then=HAIKU` to `AdaptedParts` directly, and monkeypatch `twicc.core.services.title_suggestion.is_provider_running` to `lambda provider: True` for the title checks.
  - `AdaptedParts` with `QWEN_ENGINE` and `then=HAIKU` on the long test messages: first part under 1 s; with a wrong key (monkeypatch `twicc.openrouter_text.get_env_secret` to return `"sk-wrong"`: OpenRouter answers 401), Haiku adapts and `fallback_reason == "refused"`; with the Qwen model id patched to a wrong one (patch the name where it is used: `twicc.tts.adapt.QWEN_MODEL` and `twicc.title_openrouter.QWEN_MODEL`, e.g. `TextModel("qwen/does-not-exist", "Cerebras", QWEN_MODEL.options)`), Haiku adapts and `fallback_reason == "failed"`; with `QWEN_FIRST_TEXT_SECONDS` patched to 0.01, a stall hands over to Haiku; `aclose()` right after the switch leaves no `claude` process (`pgrep -af -- '--no-session-persistence'`).
  - Titles: `suggest_title` with `title_model="qwen"` on real sessions read from the DB (a first prompt, a long session, an automatic title check with a current title): titles pass `title_rejection_reasons` as often as Haiku's on the same sessions; with the wrong key, the agent makes the title; with `twicc.title_openrouter.QWEN_TITLE_TIMEOUT_SECONDS` patched to 0.01 (a forced stall), the agent makes the title too; report the time of each fallback.
  - Report: latencies, outcomes, costs, any failure.
- [ ] **Step 2 (user checkpoint, after `cd /home/twidi/dev/twicc-poc && uv run ./devctl.py restart back` and a frontend reload):** Titles setting shows the Qwen radio (disabled without the key); a rename dialog suggestion with Qwen, then "Try with" another model; a reading with Qwen chosen starts at once; the settings test shows "Adapted by Qwen3.8 27B (OpenRouter)"; the adaptation control at 320 px.
