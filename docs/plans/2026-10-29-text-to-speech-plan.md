# Text-to-speech for chat messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read chat messages aloud through the OpenRouter TTS API, with named speech configs in `tts.json`, a validation-by-ear flow, and one app-wide playback with a stop indicator in the sidebar rail.

**Architecture:** The backend holds `TWICC_OPENROUTER_API_KEY` in process memory only (never in `os.environ`), proxies `POST /api/tts/speech/` to OpenRouter (PCM wrapped in WAV), serves model suggestions, and syncs `tts.json` like `message-snippets.json` (bootstrap + WebSocket). The frontend has one Pinia store that owns configs, availability and the single playback (fetch, cache, `<audio>`); the message button, the rail indicator, the floating-toggle mark and the settings section are views of that store.

**Tech Stack:** Django 6 async views, `httpx.AsyncClient`, stdlib `wave`, `orjson`; Vue 3 `<script setup>`, Pinia, Web Awesome 3; tests with `pytest` (+ `django.test.AsyncClient`, `httpx.MockTransport`) and `node --test`.

**Spec:** `docs/plans/2026-10-29-text-to-speech-design.md` (read it first; this plan argues from it).

## Global Constraints

- Key variable name: `TWICC_OPENROUTER_API_KEY`, read from `<data_dir>/.env` only. The key never reaches the browser, a log line, an error body, or `os.environ`.
- OpenRouter endpoints: `POST https://openrouter.ai/api/v1/audio/speech`, `GET https://openrouter.ai/api/v1/models?output_modalities=speech`, `GET https://openrouter.ai/api/v1/generation?id=<id>`.
- Formats: `mp3` | `pcm`. UI default `mp3`. PCM is 16-bit little-endian; rate and channels come from the upstream `Content-Type` (`audio/pcm;rate=24000;channels=1`); fallback 24000 Hz mono with a warning.
- Upstream timeout: at least 120 s (this plan uses `httpx.Timeout(180.0, connect=15.0)`).
- The speech endpoint is an `async def` view awaiting `httpx.AsyncClient` in the request task (cancellation on client disconnect).
- The speech endpoint never answers `401`, and never lets Django render its error page.
- Generic upstream message `Provider returned 400` gets the hint `. Check the model, the voice, the format and the text length.`
- Cost lookup: first attempt ~15 s after the speech response, retry on 404.
- Browser cache: 10 min TTL, 50 MB total (`50 * 1024 * 1024` bytes), an audio larger than the cap plays but is not cached.
- Settings section: id `tts`, label `Text to Speech`, right after `usage`, `synced: true`.
- Rail indicator: first item of the bottom group, above New session; green = `--wa-color-success-60`.
- Use `orjson`, never stdlib `json` (backend). Web Awesome icon slots: `slot="start"` in buttons, `slot="icon"` in dropdown items. Import every new `wa-*` component in `frontend/src/main.js` (none is new in this plan: `wa-input`, `wa-textarea`, `wa-dropdown`, `wa-dropdown-item`, `wa-spinner`, `wa-badge`, `wa-tag`, `wa-callout`, `wa-radio-group`, `wa-radio` are already imported).
- All code, UI strings and comments in English.
- Commit messages: Conventional Commits, lowercase summary, a body, and the trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`.
- Never run `npm install`, `migrate`, or restart servers. Run Python tests with `cd /home/twidi/dev/twicc-poc/.worktrees/tts && uv run pytest …`, frontend tests with `cd /home/twidi/dev/twicc-poc/.worktrees/tts/frontend && npm test`.

## Review Focus

1. **Autoplay after a long request.** A click starts a request that can take 19 s; `audio.play()` after that wait can be refused by the browser (Firefox blocks audible autoplay outside a user gesture). Expected: the audio plays. The store primes the player synchronously inside the click (Task 6 test `primes the player before the request starts`), and Task 10 checks it in Firefox with a long text.
2. **A stale response after a newer click.** The user clicks message A, then message B before A's audio arrives. Expected: A's request is aborted and A's audio never plays, even if its response lands late (Task 6 test `ignores a response that lands after a newer play`).
3. **Abort is not an error.** Clicking the spinner aborts the fetch. Expected: back to idle, no toast (Task 6 test `stop during loading resolves as aborted without error`).
4. **OpenRouter refuses the key.** Upstream `401`. Expected: a normal error message, the user stays logged in (Task 4 test `test_upstream_401_is_not_relayed_as_401`).
5. **The active config changes on another device during a playback.** Expected: the message playback stops; a settings test playback does not (Task 6 tests `applyConfig stops a message playback when availability is lost` and `applyConfig keeps a test playback running`).

---

## File Structure

**Backend (create)**
- `src/twicc/tts/__init__.py` — package marker, empty docstring.
- `src/twicc/tts/config.py` — `tts.json` read/normalize/write, the client payload, the key accessor.
- `src/twicc/tts/openrouter.py` — OpenRouter client: speech request, WAV wrap, error messages, model suggestions cache, cost lookup task.
- `src/twicc/tts/views.py` — the two REST endpoints.
- `tests/test_tts_config.py`, `tests/test_tts_openrouter.py`, `tests/test_tts_views.py`.

**Backend (modify)**
- `src/twicc/paths.py` — secret keys held in memory; `get_tts_config_path()`.
- `src/twicc/terminal.py` — strip the secret keys from terminal PTYs and the tmux global env.
- `src/twicc/providers/claude_code/agent/hybrid/tmux.py` — strip them from the hybrid launch env.
- `src/twicc/providers/helpers.py` — `ProviderHelpersRegistry.purge_env_vars` also drops them.
- `src/twicc/asgi.py` — send `tts_config_updated` on connect; handle `update_tts_config`.
- `src/twicc/views.py` — `bootstrap` adds `tts`.
- `src/twicc/urls.py` — two routes.
- `src/twicc/data_backups.py`, `devctl.py`, `.gitignore` — register `tts.json`.
- `tests/test_env_loader.py`, `tests/test_terminal_env_sanitation.py`, `tests/test_provider_home_propagation.py`, `tests/test_data_backups.py`.

**Frontend (create)**
- `frontend/src/utils/ttsConfig.js` (+ `.test.js`) — pure config helpers.
- `frontend/src/utils/speechCache.js` (+ `.test.js`) — TTL + size-capped LRU of Blobs.
- `frontend/src/utils/speechPlayer.js` — the single `<audio>` element, with a swappable factory for tests.
- `frontend/src/stores/textToSpeech.js` (+ `.test.js`) — the app-wide store.
- `frontend/src/components/ui/TextToSpeechButton.vue` — the message toolbar button.
- `frontend/src/components/settings/TextToSpeechSettings.vue` — the settings section.

**Frontend (modify)**
- `frontend/src/main.js` — apply `bootstrapData.tts`.
- `frontend/src/composables/useWebSocket.js` — `tts_config_updated` case, `sendTtsConfig()`.
- `frontend/src/router.js` — stop the speech when entering the login page.
- `frontend/src/components/ui/MarkdownContent.vue` — `speakable` prop, busy-visible button.
- `frontend/src/components/session/detail/items/TextContent.vue`, `.../CompactSummary.vue` — opt in.
- `frontend/src/utils/sidebarRail.js` (+ test), `frontend/src/components/sidebar/SidebarRail.vue` — rail indicator.
- `frontend/src/views/ProjectView.vue` — floating toggle mark.
- `frontend/src/components/app/SettingsPopover.vue` (+ `settingsNavigation.test.js`) — the section.

---

### Task 1: Keep the OpenRouter key out of every inherited environment

**Files:**
- Modify: `src/twicc/paths.py` (constants near `PROVIDER_HOME_KEYS`, `ensure_env_loaded`, `_reset_env_loader`)
- Modify: `src/twicc/terminal.py` (`_terminal_env_keys_to_strip`)
- Modify: `src/twicc/providers/claude_code/agent/hybrid/tmux.py` (`_purged_env_names`)
- Modify: `src/twicc/providers/helpers.py` (`ProviderHelpersRegistry.purge_env_vars`)
- Test: `tests/test_env_loader.py`, `tests/test_terminal_env_sanitation.py`, `tests/test_provider_home_propagation.py`

**Interfaces:**
- Produces: `paths.OPENROUTER_API_KEY_ENV = "TWICC_OPENROUTER_API_KEY"`, `paths.ENV_SECRET_KEYS: tuple[str, ...] = (OPENROUTER_API_KEY_ENV,)`, `paths.get_env_secret(key: str) -> str | None` (None when absent or empty).

Mechanism: `ensure_env_loaded()` keeps a secret key's `.env` value in a module dict `_ENV_SECRETS` and never writes it to `os.environ`. An inherited value of a secret key is removed from `os.environ` (recorded in `_ENV_LOAD_UNDO`, warning `Ignoring inherited TWICC_OPENROUTER_API_KEY: not read from the environment` — never the value). Since every TwiCC process (backend, CLI, compute worker, migration child) loads the `.env` through this function, the key is never inheritable. The three purge points below are for tmux servers started by an older backend that still carry the key.

Test isolation: `import twicc` loads the developer's real `.env` before `twicc.settings_test` runs, and `settings_test` then calls `paths._undo_env_load()` ("the process behaves as if its `.env` were empty"). `_undo_env_load()` must therefore also clear `_ENV_SECRETS`, or the real key would stay reachable from every test.

- [ ] **Step 1: Write the failing tests** in `tests/test_env_loader.py` (reuse the `env_loader` fixture; it snapshots `os.environ`; also snapshot/restore `paths._ENV_SECRETS` in the fixture)

```python
def test_secret_key_is_held_in_memory_not_in_environ(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=sk-or-test\nTWICC_TEST_FOO=1\n")
    paths.ensure_env_loaded()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") == "sk-or-test"
    assert "TWICC_OPENROUTER_API_KEY" not in os.environ
    assert os.environ["TWICC_TEST_FOO"] == "1"

def test_inherited_secret_key_is_dropped_without_logging_its_value(env_loader):
    os.environ["TWICC_OPENROUTER_API_KEY"] = "sk-or-inherited"
    paths.ensure_env_loaded()
    assert "TWICC_OPENROUTER_API_KEY" not in os.environ
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None
    assert not any("sk-or-inherited" in w for w in paths.get_env_load_warnings())
    paths._undo_env_load()
    assert os.environ["TWICC_OPENROUTER_API_KEY"] == "sk-or-inherited"

def test_empty_secret_reads_as_absent(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=\n")
    paths.ensure_env_loaded()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None

def test_undo_forgets_the_secrets(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=sk-or-test\n")
    paths.ensure_env_loaded()
    paths._undo_env_load()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None

def test_the_test_settings_hold_no_secret():
    # Outside the env_loader fixture: the state settings_test left behind.
    assert paths._ENV_SECRETS == {}
```

In `tests/test_terminal_env_sanitation.py`:

```python
def test_sanitize_strips_the_openrouter_key():
    env = {"TWICC_OPENROUTER_API_KEY": "x", "TWICC_DEBUG": "1"}
    sanitize_terminal_env(env)
    assert env == {"TWICC_DEBUG": "1"}
```

In `tests/test_provider_home_propagation.py` (that file imports `hybrid_tmux` inside test methods only, so import inside the new tests too):

```python
def test_hybrid_launch_unsets_the_openrouter_key():
    from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux
    assert "TWICC_OPENROUTER_API_KEY" in hybrid_tmux._purged_env_names()

def test_registry_purge_drops_the_openrouter_key():
    from twicc.providers.helpers import get_provider_helpers_registry
    env = {"TWICC_OPENROUTER_API_KEY": "x"}
    get_provider_helpers_registry().purge_env_vars(env)
    assert "TWICC_OPENROUTER_API_KEY" not in env
```

- [ ] **Step 2: Run them, expect FAIL**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/tts && uv run pytest tests/test_env_loader.py tests/test_terminal_env_sanitation.py tests/test_provider_home_propagation.py -q`
Expected: the new tests fail (`get_env_secret` missing, key still present).

- [ ] **Step 3: Implement**
  - `paths.py`: the constants, `_ENV_SECRETS: dict[str, str] = {}`, `get_env_secret()`; in `ensure_env_loaded`, skip secret keys in the `os.environ` write loop, store non-empty values in `_ENV_SECRETS`, drop inherited ones as described; `_reset_env_loader` and `_undo_env_load` both clear `_ENV_SECRETS`. Update the docstrings. In `tests/test_env_loader.py`, the `env_loader` fixture snapshots `_ENV_SECRETS` and restores it after the test (it is `{}` under `settings_test`).
  - `terminal.py`: `_terminal_env_keys_to_strip` also returns keys in `ENV_SECRET_KEYS` (keep `_TERMINAL_ENV_STRIP_NAMES` free of `TWICC_` names: the existing guard test `test_strip_names_are_disjoint_from_kept_families` must keep passing). This covers both `sanitize_terminal_env` and `purge_tmux_global_env`.
  - `hybrid/tmux.py`: `_purged_env_names` adds `ENV_SECRET_KEYS`.
  - `helpers.py`: `ProviderHelpersRegistry.purge_env_vars` pops every `ENV_SECRET_KEYS` entry (before re-applying the overlay).

- [ ] **Step 4: Run them, expect PASS**

Same command. Expected: all pass, including the pre-existing tests of the three files.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/paths.py src/twicc/terminal.py src/twicc/providers/claude_code/agent/hybrid/tmux.py src/twicc/providers/helpers.py tests/test_env_loader.py tests/test_terminal_env_sanitation.py tests/test_provider_home_propagation.py
git commit  # "feat(tts): keep the OpenRouter key out of inherited environments"
```

---

### Task 2: `tts.json` storage and sync

**Files:**
- Create: `src/twicc/tts/__init__.py`, `src/twicc/tts/config.py`
- Modify: `src/twicc/paths.py` (add `get_tts_config_path()` next to `get_message_snippets_config_path`)
- Modify: `src/twicc/asgi.py` (connect block near line 733, `receive_json` dispatch near line 968, new handler next to `_handle_update_message_snippets`)
- Modify: `src/twicc/views.py` (`bootstrap`: add `"tts": …`)
- Modify: `src/twicc/data_backups.py` (`COVERED_FILENAMES`), `devctl.py` (`SYNCED_CONFIG_FILENAMES` and the `devctl help` list near line 1389), `.gitignore` (the "TwiCC config files" block)
- Test: `tests/test_tts_config.py`, `tests/test_data_backups.py::test_covered_names`

**Interfaces:**
- Consumes: `paths.get_env_secret`, `paths.OPENROUTER_API_KEY_ENV`, `paths.get_env_path`.
- Produces (in `twicc.tts.config`):
  - `TTS_RESPONSE_FORMATS = ("mp3", "pcm")`, `SPEECH_FIELDS = ("model", "voice", "response_format", "speed", "instructions")`
  - `empty_tts_config() -> dict` → `{"active_id": None, "configs": []}`
  - `normalize_tts_config(raw: object) -> dict`
  - `read_tts_config() -> dict`, `write_tts_config(config: object) -> dict` (normalizes, writes atomically with `atomic_write_json`, returns the normalized dict)
  - `openrouter_api_key() -> str | None`
  - `tts_client_payload(config: dict) -> dict` → `{"config": config, "key_present": bool, "env_path": str}`
  - WebSocket: server → client `{"type": "tts_config_updated", **tts_client_payload(...)}`; client → server `{"type": "update_tts_config", "config": {...}}`.
  - Bootstrap: `"tts": tts_client_payload(read_tts_config())`.

Normalized config entry: `{"id": str, "name": str, "model": str, "voice": str, "response_format": "mp3"|"pcm", "speed": float|None, "instructions": str, "validated": dict|None}`; `validated` is either `None` or a dict with exactly the five `SPEECH_FIELDS` (normalized the same way). Rules: drop entries that are not dicts or have no non-empty string `id`; drop duplicate ids (first wins); unknown `response_format` → `"mp3"`; `speed` must be a finite positive number else `None`; strings default to `""`; `active_id` not matching an entry → `None`. The backend does not enforce "active must be validated" (the frontend availability rule checks it).

- [ ] **Step 1: Write the failing tests** in `tests/test_tts_config.py`

```python
def test_missing_or_invalid_file_reads_as_empty(tmp_data_dir): ...
    # no file → empty_tts_config(); b"not json" → empty_tts_config()

def test_normalize_keeps_a_valid_entry_unchanged():
    entry = {"id": "a", "name": "Gemini", "model": "google/gemini-3.8-flash-lite-tts", "voice": "Kore",
             "response_format": "pcm", "speed": None, "instructions": "", "validated": None}
    assert normalize_tts_config({"active_id": "a", "configs": [entry]}) == {"active_id": "a", "configs": [entry]}

def test_normalize_repairs_fields():
    out = normalize_tts_config({"active_id": "zz", "configs": [
        {"id": "a", "response_format": "ogg", "speed": -1, "name": 3},
        {"id": "a"}, {"name": "no id"}, "junk",
    ]})
    assert out["active_id"] is None
    assert [c["id"] for c in out["configs"]] == ["a"]
    assert out["configs"][0]["response_format"] == "mp3"
    assert out["configs"][0]["speed"] is None
    assert out["configs"][0]["name"] == ""

def test_validated_snapshot_is_normalized_or_dropped():
    # validated with the five fields → kept (normalized); validated missing a field or not a dict → None

def test_write_then_read_round_trips(tmp_data_dir): ...

def test_client_payload_reports_key_presence_without_the_key(monkeypatch):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: "sk-or-secret")
    payload = tts_client_payload(empty_tts_config())
    assert payload["key_present"] is True
    assert "sk-or-secret" not in orjson.dumps(payload).decode()
    assert payload["env_path"].endswith(".env")
```

(`tmp_data_dir`: use the isolated data dir of `twicc.settings_test`; delete `tts.json` before and after each test with a fixture.) In `tests/test_data_backups.py::test_covered_names`, add `"tts.json"` to the covered tuple.

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/tts && uv run pytest tests/test_tts_config.py tests/test_data_backups.py -q`
Expected: import error for `twicc.tts.config`; `tts.json` not covered.

- [ ] **Step 3: Implement** `config.py`, `get_tts_config_path()` (`get_data_dir() / "tts.json"`), the backup / devctl / `.gitignore` entries (and add `tts.json` to the `devctl help` file list), then the wiring:
  - `asgi.py` connect block: `if self._should_send("tts_config_updated"):` send `{"type": "tts_config_updated", **tts_client_payload(await sync_to_async(read_tts_config)())}`.
  - `asgi.py` dispatch `update_tts_config` → `_handle_update_tts_config`: reject a non-dict `config` with a warning (as the snippets handler), `write_tts_config`, then `group_send("updates", {"type": "broadcast", "data": {"type": "tts_config_updated", **tts_client_payload(normalized)}})`.
  - `views.bootstrap`: `tts_config = await asyncio.to_thread(read_tts_config)`, add `"tts": tts_client_payload(tts_config)`.

- [ ] **Step 4: Run, expect PASS** (same command), then the whole backend suite: `uv run pytest -q` → no new failure.

- [ ] **Step 5: Commit** — `feat(tts): store speech configs in tts.json and sync them` (stage each file by name).

---

### Task 3: OpenRouter client

**Files:**
- Create: `src/twicc/tts/openrouter.py`
- Test: `tests/test_tts_openrouter.py`

**Interfaces:**
- Produces (in `twicc.tts.openrouter`):
  - Constants `SPEECH_URL`, `MODELS_URL`, `GENERATION_URL` (Global Constraints values), `SPEECH_TIMEOUT = httpx.Timeout(180.0, connect=15.0)`, `MODELS_CACHE_TTL = 600.0`, `GENERIC_PROVIDER_ERROR = "Provider returned 400"`, `GENERIC_PROVIDER_HINT = ". Check the model, the voice, the format and the text length."`
  - `class SpeechRequest(NamedTuple)`: `text: str`, `model: str`, `voice: str`, `response_format: str`, `speed: float | None`, `instructions: str`
  - `class SpeechAudio(NamedTuple)`: `content: bytes`, `content_type: str`, `generation_id: str | None`
  - `class SpeechError(Exception)`: `__init__(self, message: str, upstream_status: int | None = None)`; attributes `message`, `upstream_status`
  - `build_payload(request: SpeechRequest) -> dict` — `{"model", "input": text, "response_format"}`, plus `voice` when non-empty, `speed` when not None, `instructions` when non-empty
  - `parse_pcm_format(content_type: str) -> tuple[int, int] | None` — `(rate, channels)`; channels default 1; None when no `rate`
  - `wrap_pcm_as_wav(pcm: bytes, rate: int, channels: int) -> bytes` — stdlib `wave`, sample width 2
  - `upstream_error_message(status: int, body: bytes) -> str` — `error.message` from the JSON body, else `f"OpenRouter returned HTTP {status}"`; append `GENERIC_PROVIDER_HINT` when the message equals `GENERIC_PROVIDER_ERROR`
  - `async synthesize(request: SpeechRequest, api_key: str) -> SpeechAudio` — raises `SpeechError`
  - `async fetch_speech_models() -> list[dict]` — parses `payload["data"]` (as `pricing.py` does) and maps each entry to `{"id": e["id"], "name": e.get("name") or e["id"], "voices": e.get("supported_voices") or []}`; cached `MODELS_CACHE_TTL` seconds on success only, `[]` on any failure. The test mock uses this real upstream shape (`{"data": [{"id", "name", "supported_voices", ...}]}`).
  - `_clear_models_cache() -> None` — tests only
  - `schedule_cost_log(generation_id: str, api_key: str) -> None` — fire-and-forget task (strong refs in a module set, like `external_notifications._send_tasks`)
  - `async _log_generation_cost(generation_id: str, api_key: str, *, first_delay: float = 15.0, retry_delay: float = 5.0, attempts: int = 4) -> None` — **never raises** (it runs fire-and-forget): its own client timeout `httpx.Timeout(15.0)`; GET `GENERATION_URL` with `params={"id": generation_id}` and the bearer key; 404 → wait `retry_delay` and retry, up to `attempts`; 2xx → read `payload["data"]["total_cost"]` / `["provider_name"]` (a missing value logs as `None`) and log the cost line; any other status → debug log and stop; `except Exception` (not `CancelledError`) → debug log without the exception text, and stop.
  - `_make_client(timeout) -> httpx.AsyncClient` — the single factory tests monkeypatch with `httpx.MockTransport`

`synthesize` decision table on the upstream response:
- non-2xx → `SpeechError(upstream_error_message(status, body), upstream_status=status)`
- 2xx, `Content-Type` starts with `audio/pcm` → WAV-wrapped, `content_type="audio/wav"`; `parse_pcm_format` None → 24000/1 and `logger.warning`
- 2xx, other `audio/*` → unchanged, upstream type
- 2xx, not audio → `SpeechError(upstream_error_message(status, body) or the status text, upstream_status=status)`
- `httpx.TimeoutException` → `SpeechError("OpenRouter did not answer in time")`; other `httpx.HTTPError` → `SpeechError("Could not reach OpenRouter")`. Never put the exception text or headers in the message.

One `logger.info` line per call: model, voice, format, `len(text)`, audio bytes, duration in ms, generation id, outcome (`ok` / `upstream_error <status>` / `cancelled` on `asyncio.CancelledError`, re-raised). Cost line: `logger.info("TTS cost %s: $%s (provider %s)", generation_id, total_cost, provider_name)`; give up silently (debug log) after the attempts.

- [ ] **Step 1: Write the failing tests** (`asyncio.run`, `monkeypatch.setattr(openrouter, "_make_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout))`; an autouse fixture calls `openrouter._clear_models_cache()` — a module function the implementation provides — before and after each test; every test that reads `caplog` first calls `caplog.set_level(logging.INFO, logger="twicc.tts.openrouter")`, as `tests/test_compute_shutdown.py` does)

```python
def test_build_payload_omits_empty_optional_fields():
    r = SpeechRequest("Hi", "m", "", "mp3", None, "")
    assert build_payload(r) == {"model": "m", "input": "Hi", "response_format": "mp3"}
    r = SpeechRequest("Hi", "m", "Kore", "pcm", 1.2, "calm")
    assert build_payload(r) == {"model": "m", "input": "Hi", "response_format": "pcm",
                                "voice": "Kore", "speed": 1.2, "instructions": "calm"}

def test_parse_pcm_format():
    assert parse_pcm_format("audio/pcm;rate=24000;channels=1") == (24000, 1)
    assert parse_pcm_format("audio/pcm; rate=16000") == (16000, 1)
    assert parse_pcm_format("audio/pcm") is None

def test_wrap_pcm_as_wav_header():
    wav = wrap_pcm_as_wav(b"\x00\x01" * 24000, 24000, 1)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 24000)

def test_synthesize_sends_bearer_and_wraps_pcm():
    # handler asserts request.headers["authorization"] == "Bearer k" and the JSON body;
    # returns 200, content-type "audio/pcm;rate=24000;channels=1", x-generation-id "gen-1"
    # → SpeechAudio(content=<valid WAV>, content_type="audio/wav", generation_id="gen-1")

def test_synthesize_passes_mp3_through():  # content-type audio/mpeg → same bytes, same type

def test_synthesize_decides_from_upstream_type_not_requested_format():
    # request pcm, upstream answers audio/mpeg → unchanged mp3

def test_pcm_without_rate_falls_back_to_24k_mono(caplog): ...

def test_upstream_error_message_and_hint():
    body = orjson.dumps({"error": {"message": "Provider returned 400", "code": 400}})
    assert upstream_error_message(400, body) == "Provider returned 400. Check the model, the voice, the format and the text length."
    assert upstream_error_message(500, b"<html>") == "OpenRouter returned HTTP 500"

def test_non_audio_success_is_an_error():  # 200 + application/json → SpeechError

def test_network_error_message_hides_details():
    # handler raises httpx.ConnectError("secret-ish detail") → SpeechError("Could not reach OpenRouter")

def test_logs_never_contain_the_key(caplog):
    # run a successful and a failing call with api_key "sk-or-XYZ";
    # assert the request log lines are present (e.g. "gen-1" in caplog.text), then "sk-or-XYZ" not in caplog.text

def test_models_are_shaped_cached_and_failures_not_cached(monkeypatch):
    # first call → 1 upstream hit, shape [{"id","name","voices"}]; second call → 0 hits;
    # with the cache cleared and the handler failing → [] and a later success is fetched

def test_cost_lookup_retries_on_404(caplog):
    # handler: 404, 404, 200 {"data": {"total_cost": 0.008, "provider_name": "Together"}}; delays 0
    # → caplog contains "0.008" and "Together"; 3 calls made

def test_cost_lookup_never_raises():
    # handler raises httpx.ConnectError; then a 500; then a 200 without "data"
    # → each asyncio.run(_log_generation_cost(..., first_delay=0, retry_delay=0)) returns None
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_tts_openrouter.py -q` → import error.
- [ ] **Step 3: Implement** `openrouter.py` per the Interfaces and the decision table.
- [ ] **Step 4: Run, expect PASS** — same command.
- [ ] **Step 5: Commit** — `feat(tts): add the OpenRouter speech client`.

---

### Task 4: REST endpoints

**Files:**
- Create: `src/twicc/tts/views.py`
- Modify: `src/twicc/urls.py` (`from .tts import views as tts_views`; `path("api/tts/speech/", tts_views.tts_speech)`, `path("api/tts/models/", tts_views.tts_models)`)
- Test: `tests/test_tts_views.py`

**Interfaces:**
- Consumes: `openrouter.SpeechRequest`, `synthesize`, `SpeechError`, `fetch_speech_models`, `schedule_cost_log`; `config.openrouter_api_key`, `config.TTS_RESPONSE_FORMATS`.
- Produces:
  - `parse_speech_request(data: object) -> SpeechRequest` — raises `ValueError(<message>)`. Rules: `text` non-empty string (not stripped away: whitespace-only is invalid); `model` non-empty string after `strip()`; `voice`, `instructions` strings (default `""`, stripped); `response_format` in `TTS_RESPONSE_FORMATS`; `speed` `None` or a finite positive number.
  - `POST /api/tts/speech/` body `{"text", "model", "voice", "response_format", "speed", "instructions"}` → `200` audio body (`Content-Type` from `SpeechAudio`, `Cache-Control: no-store`); errors are `JsonResponse({"error": str}, status=…)`:
    - Order: method, body size, JSON, payload validation, **then** the key check (an invalid payload is a 400 even without a key).
    - The body is read (`request.body`) inside the view's `try`: an oversized body raises `RequestDataTooBig` → 413 `"The text is too long to send"`. `DATA_UPLOAD_MAX_MEMORY_SIZE` is 12 MB (`src/twicc/settings.py:179`), far above any chat message, so no settings change.
    - non-POST → 405; invalid JSON or `ValueError` → 400; no key → 503 `"TWICC_OPENROUTER_API_KEY is not set. Add it to <env path> and restart TwiCC."`; `SpeechError` → 502 with `"upstream_status"` when known (an upstream 401/403 stays 502); any other exception → `logger.exception(...)` and 500 `"Text-to-speech failed"`.
    - On success with a generation id: `schedule_cost_log(generation_id, key)`.
  - `GET /api/tts/models/` → `{"models": [...]}` (405 otherwise).

- [ ] **Step 1: Write the failing tests** (`AsyncClient` with `settings.TWICC_PASSWORD_HASH = ""`, as in `tests/test_peer_messages.py`; monkeypatch `twicc.tts.views.synthesize`, `twicc.tts.views.openrouter_api_key`, `twicc.tts.views.schedule_cost_log`)

```python
def test_rejects_invalid_payloads(client): ...
    # missing text, blank model, response_format "ogg", speed 0 → 400 each

def test_no_key_returns_503_without_upstream_call(client, monkeypatch): ...

def test_success_returns_audio_and_schedules_cost(client, monkeypatch):
    # synthesize → SpeechAudio(b"ID3...", "audio/mpeg", "gen-1"); assert 200, body, content-type,
    # cache-control no-store, schedule_cost_log called with ("gen-1", "k")

def test_upstream_401_is_not_relayed_as_401(client, monkeypatch):
    # synthesize raises SpeechError("No auth credentials found", upstream_status=401)
    # → status 502, json {"error": "No auth credentials found", "upstream_status": 401}

def test_unexpected_exception_returns_own_json_500(client, monkeypatch, settings):
    settings.DEBUG = True
    # synthesize raises RuntimeError("sk-or-leak") → 500, json {"error": "Text-to-speech failed"},
    # "sk-or-leak" not in the body, content-type application/json

def test_models_endpoint(client, monkeypatch): ...  # fetch_speech_models → [..] → {"models": [..]}

def test_oversized_body_returns_413_json(client, settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10
    # POST a JSON body longer than 10 bytes → 413, json {"error": "The text is too long to send"}
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_tts_views.py -q`.
- [ ] **Step 3: Implement** `views.py` (both views `async def`; the upstream call is awaited directly, no `sync_to_async`) and the two routes.
- [ ] **Step 4: Run, expect PASS**, then `uv run pytest -q` and `uvx ruff check src/twicc/tts tests/test_tts_*.py`.
- [ ] **Step 5: Commit** — `feat(tts): add the speech and model suggestion endpoints`.

---

### Task 5: Frontend pure helpers (config rules, cache)

**Files:**
- Create: `frontend/src/utils/ttsConfig.js`, `frontend/src/utils/ttsConfig.test.js`
- Create: `frontend/src/utils/speechCache.js`, `frontend/src/utils/speechCache.test.js`

**Interfaces:**
- Produces (`ttsConfig.js`):
  - `SPEECH_FIELDS = ['model', 'voice', 'response_format', 'speed', 'instructions']`
  - `DEFAULT_TEST_TEXT = 'Hello! This is a TwiCC text-to-speech test.'`
  - `speechFieldsOf(config) -> { model, voice, response_format, speed, instructions }`
  - `sameSpeechFields(a, b) -> boolean`
  - `isConfigValidated(config) -> boolean` — `validated` non-null and `sameSpeechFields(config, config.validated)`
  - `createTtsConfig(id) -> config` — `{ id, name: 'New voice', model: '', voice: '', response_format: 'mp3', speed: null, instructions: '', validated: null }`
  - `patchTtsConfig(config, patch) -> config` — new object; `validated` becomes `null` when a speech field changes; a `name`-only patch keeps it
  - `duplicateTtsConfig(config, id) -> config` — `name: \`${config.name} (copy)\``, keeps `validated`
  - `isTtsAvailable({ keyPresent, configs, activeId }) -> boolean`
  - `speechCacheKey(text, fields) -> string` — `JSON.stringify([text, ...SPEECH_FIELDS.map(f => fields[f])])`
- Produces (`speechCache.js`): `class SpeechCache { constructor({ maxBytes = 50 * 1024 * 1024, ttlMs = 10 * 60 * 1000, now = () => Date.now() } = {}); get(key) -> Blob | null; set(key, blob) -> boolean; clear(); get totalBytes }` — `get` refreshes recency and drops an expired entry; `set` returns `false` and stores nothing when `blob.size > maxBytes`; evicts least-recently-used entries until the total fits.

- [ ] **Step 1: Write the failing tests**

```js
test('a speech field edit clears the validation, a rename keeps it', () => {
    const v = { ...createTtsConfig('a'), model: 'm', voice: 'v' }
    const validated = { ...v, validated: speechFieldsOf(v) }
    assert.equal(isConfigValidated(validated), true)
    assert.equal(patchTtsConfig(validated, { voice: 'w' }).validated, null)
    assert.equal(isConfigValidated(patchTtsConfig(validated, { name: 'x' })), true)
})
test('availability needs the key, an active config, and its validation', () => { /* 4 cases */ })
test('duplicate keeps the validation and is named "(copy)"', () => { /* … */ })
test('cache key changes with any speech field', () => { /* … */ })

test('cache expires entries after the TTL', () => {
    let t = 0
    const cache = new SpeechCache({ ttlMs: 1000, now: () => t })
    cache.set('a', new Blob(['x']))
    t = 999; assert.ok(cache.get('a'))
    t = 2000; assert.equal(cache.get('a'), null)
})
test('cache evicts least recently used to fit the size cap', () => { /* maxBytes 10, three 4-byte blobs, touch the first → the second is evicted */ })
test('cache refuses an audio larger than the cap', () => {
    const cache = new SpeechCache({ maxBytes: 3 })
    assert.equal(cache.set('big', new Blob(['1234'])), false)
    assert.equal(cache.get('big'), null)
})
```

- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc/.worktrees/tts/frontend && node --test src/utils/ttsConfig.test.js src/utils/speechCache.test.js`
- [ ] **Step 3: Implement** both modules (cache: a `Map` in recency order).
- [ ] **Step 4: Run, expect PASS** — same command.
- [ ] **Step 5: Commit** — `feat(tts): add speech config and audio cache helpers`.

---

### Task 6: The app-wide speech store

**Files:**
- Create: `frontend/src/utils/speechPlayer.js`
- Create: `frontend/src/stores/textToSpeech.js`, `frontend/src/stores/textToSpeech.test.js`
- Modify: `frontend/src/composables/useWebSocket.js` (case `tts_config_updated` with a lazy store import, like `message_snippets_updated`, but passing the **whole message**: `useTextToSpeechStore().applyConfig(msg)` — `key_present` and `env_path` sit next to `config`; export `sendTtsConfig(config)` sending `{ type: 'update_tts_config', config }`)
- Modify: `frontend/src/main.js` (next to the snippets line: `useTextToSpeechStore().applyConfig(bootstrapData.tts)`)
- Modify: `frontend/src/router.js` (a new `router.afterEach`: when `to.name === 'login'`, lazily import the store and call `stop()`)

**Interfaces:**
- Consumes: Task 5 helpers; `apiFetch` (`frontend/src/utils/api.js`) — **only through a lazy `import()` inside the `_requestSpeech` / `_requestModels` actions**: `api.js` imports `'../stores/auth'` without an extension, which `node --test` cannot resolve (`ERR_MODULE_NOT_FOUND`; same limit documented in `frontend/src/composables/useTimelineFeed.test.js`). Every static import in the store and in `speechPlayer.js` carries an explicit `.js` extension (`../utils/ttsConfig.js`, `../utils/speechCache.js`, `../utils/speechPlayer.js`, `../utils/crypto.js`).
- Produces:
  - `speechPlayer.js`: `createSpeechPlayer() -> { prime(): void, play(blob: Blob, onEnded: () => void, onError: () => void): Promise<void>, stop(): void }` and `setSpeechPlayerFactory(factory | null)` (tests). Ownership rule: `createSpeechPlayer()` returns `factory ? factory() : <the real player>`; `setSpeechPlayerFactory` only sets that override; the **store** holds the instance it got from `createSpeechPlayer()` on its first play, and `_resetSpeechForTests()` drops it. The real player wraps one `HTMLAudioElement` for the app. `prime()` (synchronous, in the click) sets a tiny silent WAV data URI as `src` and calls `play()` ignoring its rejection, so later playback on the same element is user-activated. `play()` revokes the previous object URL, sets a new one, wires `ended` → `onEnded` and the media `error` event → `onError`, awaits `audio.play()`. `stop()` pauses, clears `src`, revokes the URL.
  - Store `useTextToSpeechStore` (`defineStore('textToSpeech', …)`), state: `configs: []`, `activeId: null`, `keyPresent: false`, `envPath: ''`, `models: []`, `playback: { text: null, status: 'idle', source: null }`.
  - Getters: `activeConfig`, `available` (via `isTtsAvailable`), `status` (global `playback.status`), `statusForText(text) -> 'idle' | 'loading' | 'playing'` (only for `source === 'message'` and the same text).
  - Actions:
    - `applyConfig(payload)` — `payload = { config, key_present, env_path }`; afterwards, if `playback.source === 'message'` and `!this.available`, `stop()`.
    - `addConfig() -> id` (id from `generateUUID()` in `frontend/src/utils/crypto.js`; `crypto.randomUUID` is missing on plain-HTTP LAN access), `updateConfig(id, patch)`, `duplicateConfig(id) -> id`, `deleteConfig(id)` (clears `activeId` when it was active), `activateConfig(id) -> boolean` (refuses an unvalidated config), `deactivateConfig()`, `markValidated(id, testedFields) -> boolean` (sets `validated` only when the config's current speech fields equal `testedFields`). Every mutation sends `{ active_id, configs }` with `sendTtsConfig`.
    - `loadModels()` — `GET /api/tts/models/` (through `_requestModels()`) once per call, fills `models`; never rejects: a rejected request, a non-OK response or a bad body leaves `models` as `[]`.
    - `_requestSpeech(body: object, signal: AbortSignal) -> Promise<Response>` and `_requestModels() -> Promise<Response>` — the only places that `await import('../utils/api.js')` and call `apiFetch`; tests replace them on the store instance.
    - Module export `_resetSpeechForTests()` — aborts the controller, drops the player, clears the cache, and **increments** the generation counter (never back to 0: a leftover continuation from a previous test must not match the next test's first play). Every store test calls it first and awaits every `play()` / `playWith()` promise it starts.
    - `play(text) -> Promise<{ ok: true } | { ok: false, error: string, aborted?: boolean }>` — the active config, `source: 'message'`, cache on.
    - `playWith(text, fields, { useCache = false, source = 'test' } = {})` — same result shape.
    - `stop()` — aborts the in-flight request or stops the audio; back to idle.
  - Play algorithm (module-level, non-reactive: `controller`, `player` — obtained from `createSpeechPlayer()` on the first play, never at import, so the store loads under `node --test` — a `generation` counter, one `SpeechCache`): call `stop()`; `const mine = ++generation`; `player.prime()` synchronously; set `playback = { text, status: 'loading', source }`; cache hit → play it; else `this._requestSpeech({ text, ...fields }, controller.signal)` (POST `/api/tts/speech/`, JSON, `Content-Type: application/json`); after every `await`, return `{ ok: false, aborted: true }` if `generation !== mine`; non-OK → `{ ok: false, error: json.error || \`Request failed (${status})\` }` and idle; OK → `blob()`, cache it when `useCache`, `status: 'playing'`, `player.play(blob, onEnded → idle, onError → idle)` (the callbacks check `generation === mine` first). `stop()` increments `generation`, aborts the controller and stops the player when they exist (`controller?.abort()`, `player?.stop()`: the router hook calls it on a cold app too), sets idle. An `AbortError` — from the fetch or from `player.play()` interrupted by a stop — resolves `{ ok: false, aborted: true }`. Any other `player.play()` rejection → `{ ok: false, error: 'The browser refused to play the audio' }` and idle. **`play()` / `playWith()` never reject:** the whole sequence after `prime()` sits in one `try`; an `AbortError` or `generation !== mine` → `{ ok: false, aborted: true }`; any other error (a rejected `_requestSpeech` such as `TypeError: Failed to fetch`, a failing `blob()`) → `{ ok: false, error: 'Could not reach TwiCC' }`; a non-OK response whose body is not JSON → `` `Request failed (${status})` ``. The status returns to idle only when `generation === mine` (a newer play owns it otherwise).

- [ ] **Step 1: Write the failing tests** (`beforeEach`: `_resetSpeechForTests()`, `setActivePinia(createPinia())`, `setSpeechPlayerFactory(() => fakePlayer)`; replace `store._requestSpeech` / `store._requestModels` with stubs returning `new Response(...)`. Mutations send through a `_sendConfig()` action that lazily imports `sendTtsConfig`, as `messageSnippets.js` does; tests replace `store._sendConfig` with a recorder. Below, "fetch" means a call to the `_requestSpeech` stub.)

```js
test('primes the player before the request starts', async () => { /* record order: prime, then fetch */ })
test('play goes loading → playing → idle on end', async () => { /* fake player calls onEnded */ })
test('stop during loading resolves as aborted without error', async () => {
    // fetch never resolves until its signal aborts (reject with DOMException 'AbortError')
    const p = store.play('hello'); store.stop()
    assert.deepEqual(await p, { ok: false, aborted: true }); assert.equal(store.status, 'idle')
})
test('ignores a response that lands after a newer play', async () => {
    // first fetch resolves late, after play('b') started → its audio is never played
})
test('stop while audio.play() is pending is not an error', async () => {
    // fake player.play rejects with DOMException('', 'AbortError') when stopped → { ok: false, aborted: true }
})
test('a media error during playback returns to idle', async () => { /* fake player calls onError → idle */ })
test('a network failure resolves as an error and returns to idle', async () => {
    // _requestSpeech rejects with new TypeError('Failed to fetch')
    // → resolves { ok: false, error: 'Could not reach TwiCC' }, status 'idle', no rejection
})
test('a non-JSON error body falls back to the status', async () => { /* 502 text/html → error 'Request failed (502)' */ })
test('a cached text plays without a second request', async () => { /* 1 fetch for 2 plays */ })
test('a test playback bypasses the cache', async () => { /* playWith twice → 2 fetches */ })
test('an error body becomes the result error', async () => { /* 502 {"error":"Provider returned 400 …"} */ })
test('applyConfig stops a message playback when availability is lost', async () => { /* playing, then applyConfig with key_present false → idle */ })
test('applyConfig keeps a test playback running', async () => { /* playWith source test, availability lost → still playing */ })
test('activate refuses an unvalidated config; markValidated checks the tested fields', () => { /* … */ })
test('stop() on a fresh store is a no-op', () => {
    assert.doesNotThrow(() => useTextToSpeechStore().stop())
    assert.equal(useTextToSpeechStore().status, 'idle')
})
test('a fresh store is not available (share pages never feed it)', () => {
    assert.equal(useTextToSpeechStore().available, false)
})
```

- [ ] **Step 2: Run, expect FAIL** — `node --test src/stores/textToSpeech.test.js`
- [ ] **Step 3: Implement** `speechPlayer.js`, the store, the WebSocket case and sender, the bootstrap line, the router hook (lazy `import('./stores/textToSpeech')` inside the hook).
- [ ] **Step 4: Run, expect PASS** — then `npm test` (whole frontend suite).
- [ ] **Step 5: Commit** — `feat(tts): add the app-wide speech store`.

---

### Task 7: The message toolbar button

**Files:**
- Create: `frontend/src/components/ui/TextToSpeechButton.vue`
- Modify: `frontend/src/components/ui/MarkdownContent.vue` (prop, template after the Copy button, toolbar CSS)
- Modify: `frontend/src/components/session/detail/items/TextContent.vue`, `frontend/src/components/session/detail/items/CompactSummary.vue`
- Test: `frontend/src/components/ui/textToSpeechButton.test.js` (source-text assertions, like `settingsNavigation.test.js`)
- Modify test: `frontend/src/composables/useMarkdownToolbar.test.js` (its regex at line ~129 pins the closed-toolbar selector `wa-button:not(.markdown-toolbar-toggle)` and the rule order at line ~127; update both for the new selector and the new rule)

**Interfaces:**
- Consumes: `useTextToSpeechStore().statusForText`, `.available`, `.play`, `.stop`; `toast` from `frontend/src/composables/useToast`.
- Produces: `MarkdownContent` prop `speakable: { type: Boolean, default: false }`; `<TextToSpeechButton :text="source" />` whose root element is a `wa-button` (it lives inside the toolbar's `wa-button-group`).

The root `wa-button` uses `size="small" appearance="filled"`, like the Copy button next to it, so it fits the vertical group. The toolbar sizes only its icons (`.markdown-toolbar wa-button wa-icon { width: .6rem; }`): add `.markdown-toolbar wa-button wa-spinner { font-size: .6rem; }` so the loading state keeps the button's size.

Behaviour: `MarkdownContent` renders `<TextToSpeechButton v-if="speakable" :text="source" />` and never touches the store itself (`MarkdownContent.render.test.js` executes the setup code between `const blocks = ref([])` and `const showRaw = ref(false)` with a fixed set of dependencies: a store call there would throw). The button's own root `v-if` is `store.available || store.statusForText(text) !== 'idle'`. States per the spec table: idle → `wa-icon name="volume-up"`, `variant="neutral"`, title `Read aloud`; loading → `wa-spinner` in place of the icon, title `Cancel the request`; playing → `wa-icon name="stop"`, `variant="brand"`, title `Stop reading`. Click: idle → `store.play(text)` then `toast.error(result.error, { duration: 4000 })` when `!result.ok && !result.aborted`; otherwise `store.stop()`. When busy, the button carries the class `markdown-toolbar-speech--busy`; the CSS keeps it visible while the toolbar is closed (`.markdown-toolbar[data-open="false"] wa-button:not(.markdown-toolbar-toggle):not(.markdown-toolbar-speech--busy)`) and keeps the toolbar opaque when it holds a busy button with `.markdown-content-wrapper .markdown-toolbar:has(.markdown-toolbar-speech--busy) { opacity: 1; }`, placed **after** the `.markdown-content-wrapper:hover .markdown-toolbar { opacity: 0.3 }` rule (same specificity, so order decides; a shorter selector would lose to the hover rule and fade the stop button under the pointer). `:has()` is supported in Firefox 121+. `TextContent` and `CompactSummary` pass `speakable`. No other host does.

- [ ] **Step 1: Write the failing test**

```js
test('MarkdownContent declares speakable off by default and renders the button only when speakable', () => {
    assert.match(markdownSource, /speakable:\s*\{\s*type:\s*Boolean,\s*default:\s*false/)
    assert.match(markdownSource, /<TextToSpeechButton[^>]*v-if="speakable/)
})
test('only TextContent and CompactSummary opt in', () => {
    assert.match(textContentSource, /<MarkdownContent[^>]*\sspeakable/)
    assert.match(compactSummarySource, /<MarkdownContent[^>]*\sspeakable/)
    // scan every other .vue under src/ for "<MarkdownContent" with "speakable": none
})
test('a busy speech button stays visible with the toolbar closed', () => {
    assert.match(markdownSource, /:not\(\.markdown-toolbar-speech--busy\)/)
    const hover = markdownSource.indexOf('.markdown-content-wrapper:hover .markdown-toolbar')
    const busy = markdownSource.indexOf('.markdown-content-wrapper .markdown-toolbar:has(.markdown-toolbar-speech--busy)')
    assert.ok(hover !== -1 && busy > hover, 'the busy rule follows the hover rule')
})
```

- [ ] **Step 2: Run, expect FAIL** — `node --test src/components/ui/textToSpeechButton.test.js`
- [ ] **Step 3: Implement** the component and the edits. For the busy-visible rule, the visibility condition reads the store state for the message text even when `available` turns false mid-request (the store aborts it in that case, Task 6).
- [ ] **Step 4: Run, expect PASS**; `npm test`.
- [ ] **Step 5: Commit** — `feat(tts): add the read-aloud button to chat messages`.

---

### Task 8: Rail indicator and floating-toggle mark

**Files:**
- Modify: `frontend/src/utils/sidebarRail.js`, `frontend/src/utils/sidebarRail.test.js`
- Modify: `frontend/src/components/sidebar/SidebarRail.vue`
- Modify: `frontend/src/views/ProjectView.vue` (the `#sidebar-toggle-button` near line 1712, plus its scoped CSS)
- Modify tests pinned on the changed markup: `frontend/src/views/ProjectViewRail.test.js` (line ~94 pins the exact inner markup of `#sidebar-toggle-button`), `frontend/src/styles/rail-button.test.js` (lines ~56-85: the short-height fallback counts the fixed rail controls), `frontend/src/components/sidebar/SidebarRail.test.js` (line ~44 pins `@container rail (height < 25rem)`; lines ~15-19 and ~74 pin the bottom-group structure, which stays valid only if `speech` uses the generic branch), `frontend/src/components/sidebar/SidebarRail.touch.test.js` (executes the component setup with a fixed context)

**Interfaces:**
- Consumes: `useTextToSpeechStore().status`, `.stop()`.
- Produces: a rail definition `{ id: 'speech', icon: 'volume-up', group: 'bottom', requires: state => state.speechStatus === 'loading' || state.speechStatus === 'playing', label: state => state.speechStatus === 'loading' ? 'Cancel the text-to-speech request' : 'Stop reading aloud' }`, placed **before** `new-session` in `RAIL_ITEM_DEFINITIONS`; the resolver state gains `speechStatus` (default handling: missing → hidden).

`SidebarRail.vue` passes `speechStatus: tts.status` in the `resolveRailItems` state and renders `speech` **in the existing generic `v-else` branch** (the one rendering palette / inbox / toggle), so the structure pinned by `SidebarRail.test.js` stays valid: add `:class="{ 'rail-speech--playing': item.id === 'speech' && tts.status === 'playing' }"` on the button (icon colour `var(--wa-color-success-60)`), and `<wa-spinner v-if="item.id === 'speech' && tts.status === 'loading'" class="rail-speech-spinner" />` inside it, next to the existing `PeerInboxBadge` line; `activate(item)` calls `tts.stop()` for `speech` instead of emitting. `.rail-button` is a centred grid (`frontend/src/styles/rail-button.css`): an in-flow spinner would add a grid row, so position it like the inbox badge — `.rail-speech-spinner { position: absolute; inset: 0; margin: auto; font-size: calc(var(--rail-icon-size) * 1.6); pointer-events: none; }` — a ring around the icon. `ProjectView.vue`: inside `#sidebar-toggle-button`, after the `PeerInboxBadge`, `<wa-icon v-if="tts.status !== 'idle'" name="volume-up" class="sidebar-toggle-speech-mark" aria-hidden="true"></wa-icon>`, positioned on the bottom-inline-end corner so it never covers the `PeerInboxBadge` (top-inline-end); the button's click handler is unchanged.

Short-height fallback: during a playback the rail holds 10 fixed controls instead of 9. 10 controls need 328 px at a 12 px root and 436 px at a 16 px root, above the current `@container rail (height < 25rem)` threshold (300 / 400 px). Raise it to `27.5rem` in `SidebarRail.vue`.

- [ ] **Step 1: Write the failing tests** in `sidebarRail.test.js` (add `speechStatus: 'idle'` to the shared `state` so the existing order test stays as is)

```js
test('shows the speech indicator first in the bottom group only while busy', () => {
    assert.equal(resolveRailItems(state).some(item => item.id === 'speech'), false)
    for (const [speechStatus, label] of [['loading', 'Cancel the text-to-speech request'], ['playing', 'Stop reading aloud']]) {
        const items = resolveRailItems({ ...state, speechStatus })
        const bottom = items.filter(item => item.group === 'bottom').map(item => item.id)
        assert.deepEqual(bottom, ['speech', 'new-session', 'palette', 'inbox', 'settings', 'toggle'])
        assert.equal(items.find(item => item.id === 'speech').label, label)
    }
})
```

Update the pinned tests:
- `ProjectViewRail.test.js`: the toggle regex becomes `<wa-icon name="angles-right"><\/wa-icon>\s*<PeerInboxBadge v-if="peersStore.inboxCount > 0" :count="peersStore.inboxCount" \/>\s*<wa-icon v-if="tts.status !== 'idle'" name="volume-up" class="sidebar-toggle-speech-mark" aria-hidden="true"><\/wa-icon>\s*<\/button>\s*<AppTooltip for="sidebar-toggle-button" placement="top">`.
- `rail-button.test.js` (short-height fallback): pass `speechStatus: 'playing'` to `resolveRailItems`, expect a count of `10` ("maximum fixed controls includes New session and the speech indicator"), and `required === 328` at the 12 px root / `0.5rem` gap (the `288 <` check stays).
- `SidebarRail.test.js`: the container query pin becomes `@container rail (height < 27.5rem)`.
- `SidebarRail.touch.test.js`: it runs the whole `<script setup>` of `SidebarRail.vue` in `runInNewContext` with the imports stripped and a fixed context (lines ~33-45); add `useTextToSpeechStore: () => ({ status: 'idle', stop() {} })` to that context, or the new store call throws `ReferenceError` in every test of the file.

- [ ] **Step 2: Run, expect FAIL** — `node --test src/utils/sidebarRail.test.js src/views/ProjectViewRail.test.js src/styles/rail-button.test.js src/components/sidebar/SidebarRail.test.js src/components/sidebar/SidebarRail.touch.test.js` (the touch test passes before Step 3 and must still pass after it)
- [ ] **Step 3: Implement** the definition, the generic-branch additions, the toggle mark and its CSS, the `27.5rem` threshold.
- [ ] **Step 4: Run, expect PASS**; `npm test`.
- [ ] **Step 5: Commit** — `feat(tts): show the speech state in the sidebar rail`.

---

### Task 9: Settings section

**Files:**
- Create: `frontend/src/components/settings/TextToSpeechSettings.vue`
- Modify: `frontend/src/components/app/SettingsPopover.vue` (import; `{ id: 'tts', label: 'Text to Speech', synced: true }` right after the `usage` entry; `<TextToSpeechSettings v-if="activeSection === 'tts'" />` next to the other section components)
- Modify: `frontend/src/components/app/settingsNavigation.test.js` (insert `"id: 'tts'"` after `"id: 'usage'"`)

**Interfaces:**
- Consumes: the store (configs, `activeId`, `keyPresent`, `envPath`, `models`, `loadModels`, mutations, `playWith`, `status`), `isConfigValidated`, `speechFieldsOf`, `sameSpeechFields`, `DEFAULT_TEST_TEXT`, `SegmentedControl`.

Content, top to bottom (wording is final unless marked):
0. The wrapper every section component uses (`frontend/src/components/app/NotificationSettings.vue`, `frontend/src/components/mcp/McpSettings.vue`): `<section class="settings-section">` with `<h3 class="settings-section-title">Text to Speech <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>` (the synced mark, as in the Layouts section of `SettingsPopover.vue`). The `.synced-icon` colour rule of `SettingsPopover.vue` is scoped and does not reach a child component: declare `.synced-icon { color: var(--wa-color-brand); }` in this component's scoped style, as `frontend/src/components/app/ProviderSettingsSection.vue` and `NotificationSettings.vue` do.
1. `wa-callout`: key present → `variant="success"`, "`TWICC_OPENROUTER_API_KEY` is loaded. After you change it in `{{ envPath }}`, restart TwiCC."; absent → `variant="warning"`, "Add `TWICC_OPENROUTER_API_KEY` to `{{ envPath }}`, then restart TwiCC. Testing is disabled until then."
2. Hint with links: [OpenRouter TTS guide](https://openrouter.ai/docs/guides/overview/multimodal/tts) and [speech models](https://openrouter.ai/models?output_modalities=speech) (`target="_blank" rel="noopener"`).
3. Test text: one `wa-textarea` for the section with the visible label "Test text", initial `DEFAULT_TEST_TEXT`, local state (not persisted).

Sizing and appearance follow the Apprise targets (`frontend/src/components/app/NotificationSettings.vue`): every `wa-input`, `wa-textarea`, `wa-button` and `wa-tag` of the section uses `size="small"`; Delete is `appearance="plain" variant="danger"`; Activate, Deactivate, Duplicate, Test, Yes, No, New config and the two suggestion triggers are `appearance="outlined"`.

Labels and hints use the shared unscoped classes of `SettingsPopover.vue` (`.setting-group`, `.setting-group-label`, `.setting-group-hint`, styled under `.settings-sections`), as `NotificationSettings.vue` does. Every config field below has a visible label in the same style as "Format": "Name", "Model", "Voice", "Format", "Speed", "Instructions". Icon-only buttons carry a `title`: "Suggested models", "Suggested voices", "Delete this config".
4. One card per config (layout like the Apprise targets in `NotificationSettings.vue`):
   - row 1: name `wa-input` (commit on `change`, trimmed); `wa-tag` "Active" when active; `wa-tag variant="success"` "Validated" when `isConfigValidated`; buttons Activate (disabled unless validated; hidden when active), Deactivate (only when active), Duplicate, Delete (`variant="danger"`, trash icon).
   - row 2: model `wa-input` + a `wa-dropdown` suggestion button listing `models` (item text `name`, value `id`); voice `wa-input` + a `wa-dropdown` listing the voices of the model whose `id` equals the typed model (button disabled when the list is empty). Picking a suggestion fills the input and commits. Typing is never restricted.
   - row 3: a visible label "Format" (the `SegmentedControl` `label` prop is only its accessible name), then `SegmentedControl` label "Format", options `[{ value: 'mp3', label: 'MP3' }, { value: 'pcm', label: 'PCM' }]`; speed `wa-input type="number" step="0.1" min="0.1"` placeholder "Default" — its value is a string: empty → commit `null`; otherwise `Number(value)`, committed only when finite and `> 0` (else the field reverts to the stored value); instructions `wa-textarea` placeholder "Optional delivery instructions" (empty → `''`).
   - row 4: **Test** button (spinner while this config's test loads; disabled without key, without a model, or while another test of this section is in flight). Starting a Test clears this card's error and prompt. Right after `await store.playWith(...)` — whatever the result — the spinner and the in-flight flag are cleared (`playWith` never rejects, Task 6). On `{ ok: false, aborted: true }` (the test was cancelled by another play or by the rail indicator) clear the spinner and show neither an error nor a prompt. On `{ ok: false, error }` show the error under the card (no toast). On `{ ok: true }` show "Did you hear it correctly?" with **Yes** / **No** — only if `sameSpeechFields(speechFieldsOf(config), testedFields)` still holds (a field edited while the request loaded means no prompt); keep `testedFields = speechFieldsOf(config)` captured *before* the request; Yes → `markValidated(id, testedFields)`, then hide the prompt; No → hide the prompt; a `watch` on the config's speech fields hides the prompt when they differ from `testedFields`. When an active config is not validated: `wa-callout variant="warning"` in the card, "This config changed since its last test: the read-aloud buttons are hidden until a new test is confirmed."
5. **New config** button (`plus` icon, `slot="start"`) → `addConfig()`.

On mount: `store.loadModels()`. Every commit goes through `updateConfig(id, patch)` (which clears the validation on a speech field change, Task 5).

- [ ] **Step 1: Update the failing test** — the navigation order test with `"id: 'tts'"` after `"id: 'usage'"`.
- [ ] **Step 2: Run, expect FAIL** — `node --test src/components/app/settingsNavigation.test.js`
- [ ] **Step 3: Implement** the component and the popover edits.
- [ ] **Step 4: Run, expect PASS**; `npm test`.
- [ ] **Step 5: Commit** — `feat(tts): add the Text to Speech settings section`.

---

### Task 10: Build and end-to-end check

**Files:** none (verification only; fix in the owning task's files if something fails, with its own commit).

- [ ] **Step 1: Full test suites** — `cd /home/twidi/dev/twicc-poc/.worktrees/tts && uv run pytest -q` and `cd frontend && npm test`. Expected: no failure.
- [ ] **Step 2: Lint** — `cd /home/twidi/dev/twicc-poc/.worktrees/tts && uvx ruff check src/twicc/tts src/twicc/paths.py tests/test_tts_*.py`. Expected: no new error.
- [ ] **Step 3: Build** (the share bundle reuses `MarkdownContent` and `TextContent`) — `cd /home/twidi/dev/twicc-poc/.worktrees/tts/frontend && npm run build`. Expected: success.
- [ ] **Step 4: Manual check — hand over to the user.** Starting the worktree servers is the user's call. Prerequisite: the key reaches the worktree `.env` through `.worktrees-env/default.env` (devctl never copies `<data_dir>/.env`). Checklist for the user (Firefox):
  1. Settings → Text to Speech: callout green; suggestions list models; pick `hexgrad/kokoro-82m`, voice `af_bella`, MP3; Test → audio, Yes → Validated; Activate.
  2. A PCM config (`google/gemini-3.8-flash-lite-tts`, `Kore`, PCM) → Test plays correctly (WAV); the same with MP3 → the Gemini error shows under the card.
  3. A chat message → speaker in the toolbar; click → spinner (toolbar closed elsewhere, spinner stays) → audio; rail shows the green indicator; click it → stops.
  4. A long message (several thousand characters) → the audio plays after the wait (autoplay unlock, Review Focus 1).
  5. Click message A, then B before A plays → only B plays.
  6. Navigate to the home, the artifacts mode and another session while playing → audio continues; rail indicator everywhere.
  7. Disable "rail visible when closed", close the sidebar → the floating toggle shows the audio mark.
  8. Edit the active config's voice → buttons disappear; the section shows the warning callout.
  9. `env` in a TwiCC terminal → no `TWICC_OPENROUTER_API_KEY`.
  10. Backend log: one TTS line per request, then a cost line ~15 s later; no key in the log.
