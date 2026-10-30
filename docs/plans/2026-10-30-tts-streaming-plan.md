# Text-to-speech streaming playback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the text-to-speech audio with its first received bytes, for MP3 and PCM, with standard web features only.

**Architecture:** The backend keeps each speech as an in-memory `AudioStream` behind a random audio id. A background task pumps the OpenRouter response into it. The browser opens the speech (`POST`, JSON answer), reads it by id (`GET`, streamed: an `<audio>` element for MP3, Web Audio for PCM), asks how it ended (`GET …/ending/`), then releases it (`DELETE`). The audio cache moves from the browser to the backend.

**Tech Stack:** Django 6 async views and `StreamingHttpResponse` with an async iterator, `httpx.AsyncClient.stream`, `asyncio` tasks and `call_later`; Vue 3, Pinia, Web Audio API, `HTMLMediaElement`; tests with `pytest` (`httpx.MockTransport`, `django.test.AsyncClient`) and `node --test`.

**Spec:** `docs/plans/2026-10-30-tts-streaming-design.md` (read it first), on top of `docs/plans/2026-10-29-text-to-speech-design.md` (the "TTS spec").

## Global Constraints

- Work on `main` in `/home/twidi/dev/twicc-poc`. No branch, no worktree. Prefix every command with `cd /home/twidi/dev/twicc-poc && `.
- Python tests: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q`. Frontend tests: `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
- Never call OpenRouter in tests or reviews. Never read `~/.twicc/.env`. Never print the key. Never restart servers, never run `migrate`, never `npm install`.
- Other agents work on the same checkout: `git add` only the files of the task, by name.
- Backend: `orjson`, never stdlib `json`. `NamedTuple` for immutable data. Every new endpoint answers with its own JSON error, never with Django's error page, never `401` for an upstream error.
- Upstream timeout: `SPEECH_TIMEOUT = httpx.Timeout(180.0, connect=15.0)` (httpx applies it per wait, as the spec requires).
- PCM: 16-bit little-endian. The backend always sends `audio/pcm;rate=<R>;channels=<C>`; fallback `24000` Hz mono with a logged warning when missing or implausible (`parse_pcm_format` rules).
- Cache: key = text + speech fields, TTL `600` s, total `50 * 1024 * 1024` bytes, an audio larger than the cap is not stored. Only a complete audio enters it. The settings test neither reads nor fills it.
- Audio id lifetime: until released. Unclaimed expiry `30` s after the open step answers. Safety expiry `3 * 3600` s after the last activity (open, read, ending). Never close an upstream call because no reader is connected.
- Error messages (exact): `The browser refused to play the audio`, `Could not reach TwiCC` (both exist today), `The audio stopped before the end`, `The audio is no longer available`, plus the backend's upstream messages (`OpenRouter did not answer in time`, `Could not reach OpenRouter`, OpenRouter's own message with the generic-400 hint).
- Unchanged: the rail indicator, the floating-toggle mark, the visibility rule, the states `idle` / `loading` / `playing`, one app-wide playback, the stop on the login route (`frontend/src/router.js:184`).
- Commits: Conventional Commits, lowercase summary, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`.

## Review Focus

1. **A playback far longer than its download.** 3 079 characters: received in 12 s, played for 166 s. Expected: the ending step at 166 s answers `complete`, no false error (Task 3 test `test_a_finished_stream_lives_until_released`, Task 7 test `the ending step runs when the player ends, not when the download ends`).
2. **A media element that closes its read and resumes with a range.** Expected: the resumed read gets the bytes from its offset, and the upstream keeps running while no reader is connected (Task 4 tests `test_range_read_serves_received_bytes_from_the_offset`, `test_no_reader_does_not_stop_the_upstream`).
3. **A stop between the open answer and the first read.** Expected: the release closes the upstream at once; nothing is cached or recorded (Task 4 test `test_release_before_any_read_cancels_the_upstream`).
4. **An upstream failure after the first sound.** Expected: the read ends abnormally, the ending step says `failed` with the backend message, the caller gets a late error (Task 4 test `test_upstream_failure_cuts_the_read`, Task 5 table tests, Task 7 test `a late failure reaches the ended promise`).
5. **A cache hit while another tab evicts the entry.** Expected: the audio id that serves it keeps playing (Task 4 test `test_a_cache_hit_survives_eviction`).

---

## File Structure

**Backend (create)**
- `src/twicc/tts/audio_cache.py` — the server-side LRU audio cache.
- `src/twicc/tts/streams.py` — `AudioStream` and the in-memory registry (ids, timers, release, ending answer, the end-of-speech log line).
- `tests/test_tts_audio_cache.py`, `tests/test_tts_streams.py`.

**Backend (modify)**
- `src/twicc/tts/openrouter.py` — `pcm_content_type`, `pump_speech` (streaming upstream call), PCM in `audio_duration_seconds`; remove `synthesize`, `SpeechAudio`, `_audio_from_response`, `wrap_pcm_as_wav` once unused.
- `src/twicc/tts/config.py` — `first_audio_ms` in `_last_test`.
- `src/twicc/tts/views.py` — the open step replaces the speech view; new read/release and ending views; the end-of-speech policy (cache, last test, cost).
- `src/twicc/urls.py` — two new routes.
- `tests/test_tts_openrouter.py`, `tests/test_tts_config.py`, `tests/test_tts_views.py`.

**Frontend (create)**
- `frontend/src/utils/pcmAudio.js` (+ `.test.js`) — PCM type parsing, Int16 decoding with carry, WAV wrapping.
- `frontend/src/utils/speechOutcome.js` (+ `.test.js`) — the end-result table, the messages, the playback mode choice.
- `frontend/src/utils/pcmStreamPlayer.js` (+ `.test.js`) — Web Audio streaming player.

**Frontend (modify)**
- `frontend/src/utils/speechPlayer.js` — the `<audio>` player plays a URL or a blob, with handlers.
- `frontend/src/stores/textToSpeech.js` (+ `.test.js`) — open → read → ending → release, the two-part outcome, release on `pagehide`.
- `frontend/src/utils/ttsConfig.js` (+ `.test.js`) — first-audio figure.
- `frontend/src/components/ui/TextToSpeechButton.vue`, `frontend/src/components/settings/TextToSpeechSettings.vue` — late errors.
- Delete `frontend/src/utils/speechCache.js` and `frontend/src/utils/speechCache.test.js`.

---

### Task 1: Browser checks on a throwaway spike (user checkpoint)

The spec requires these checks before the build relies on them. They need the user's devices. Nothing from this task is committed.

**Files:**
- Create (outside the repo): `<scratch>/tts-spike/spike.py`, `<scratch>/tts-spike/index.html`, sample files. `<scratch>` is the session's scratch directory.

- [ ] **Step 1: Make the samples**

```bash
cd <scratch>/tts-spike
ffmpeg -f lavfi -i "sine=frequency=440:duration=120" -ac 1 -ar 44100 -f s16le tone.pcm
ffmpeg -f lavfi -i "sine=frequency=440:duration=120" -ac 1 -b:a 128k tone.mp3
ffmpeg -f lavfi -i "sine=frequency=440:duration=1200" -ac 1 -b:a 128k long.mp3
```

`long.mp3` (20 minutes, about 19 MB) is large enough that a browser stops buffering, closes its read, and resumes it later. The spike serves it as `/mp3?sample=long`.

A pure tone makes a click between PCM chunks audible.

- [ ] **Step 2: Write `spike.py`, a minimal Django ASGI app served by uvicorn**

This checks the same stack as TwiCC (Django 6 ASGI, uvicorn, `TwiccGZipMiddleware`), without TwiCC's data or servers.

- `django.conf.settings.configure(...)` inside the script:
  - `ROOT_URLCONF` = the script module;
  - `MIDDLEWARE = ["twicc.gzip_middleware.TwiccGZipMiddleware"]` (the only TwiCC middleware that can touch a body; the auth middlewares never do);
  - `ALLOWED_HOSTS = ["*"]`, `DEBUG = False`.
- Serve it with `uvicorn.run(get_asgi_application(), host="0.0.0.0", port=<free port>)`. Run it from the repo environment, with its own data dir, so that importing `twicc` never loads `~/.twicc/.env` (`twicc.cli` calls `ensure_env_loaded()` at import): `cd /home/twidi/dev/twicc-poc && TWICC_DATA_DIR=<scratch>/tts-spike uv run python <scratch>/tts-spike/spike.py`.
- Views:
  - `/` → `index.html`;
  - `/session` → `204`, and it sets a cookie `spike_session=1` with TwiCC's session attributes (`HttpOnly`, `SameSite=Lax`, `Path=/`, like `settings.py:214-224`). `index.html` calls it on load, so the cookie is also set through the Vite port;
  - `/mp3` and `/pcm` → `401` JSON without that cookie, like `PasswordAuthMiddleware`.
- **One simulated stream per playback.** `index.html` adds a random `id=<…>` to the audio URL on each click.
  - The spike keeps one simulated stream per `id`. It starts at the first request with that id, and "receives" the sample at the throttled pace: random chunk sizes between 1 and 32 KiB (odd sizes included), at 15× the audio rate.
  - Every later request with the same id reads that same stream and its state, as Task 4 does with an audio id.
  - `delay=<s>` (wait before the first byte), `gap=<s>@<offset>` (pause once at a byte offset) and `cut=<offset>` (the stream fails there) apply to the stream, not to each request.
- **Reads:** the read rules of Task 4, exactly ("Rules for the views", *Read*), through a `StreamingHttpResponse` over an async generator:
  - the same headers (`Cache-Control: no-store`, `Accept-Ranges: bytes`, `Content-Length` only once complete for a plain read);
  - `bytes=N-` and `bytes=N-M`, the wait for a first byte past `N`, `206` with `bytes N-L/*` or `/T`, and the same `416` rules;
  - a failed stream raises `ConnectionAbortedError` in the generator (the abnormal end Task 4 uses);
  - option `range=full` ignores `Range` and sends no `Accept-Ranges`.
- Each request logs its method, path, `id`, `Range` header, cookie presence and the time.
- Proxy `/session`, `/mp3` and `/pcm` through Vite (Step 2b).

- [ ] **Step 2b: Put the Vite dev proxy in front of it**

Write `<scratch>/tts-spike/vite.config.mjs`. It exports a plain object (`export default { … }`), without `import … from 'vite'`: the scratch directory has no `node_modules`.
- `root` = the spike directory;
- `server.host: true`;
- `server.proxy` sends `/session`, `/mp3` and `/pcm` to the spike port, like `frontend/vite.config.js` sends `/api`.

Run `cd /home/twidi/dev/twicc-poc/frontend && ./node_modules/.bin/vite --config <scratch>/tts-spike/vite.config.mjs --port <free port>`.

- [ ] **Step 2c: Check the server side with curl, directly and through Vite**

On both ports, run every curl with `-sSN --max-time 600 -b spike_session=1` and a fresh `id`. Run them in the background: the long one takes about 6 minutes.

- **Timing:** pipe `curl -sSN --max-time 600 -b spike_session=1 '<url>/pcm?id=<new>&delay=170&gap=170@500000'` into a small script (`uv run python -c '…'`). The script prints the time and size of each read on stdin. Do not use `%{time_starttransfer}`: the headers leave at once, and only the body waits.
- **Cut:** `curl -sSN --max-time 600 -b spike_session=1 -o /dev/null '<url>/pcm?id=<new>&cut=300000'; echo "exit=$?"`.

Expected:
- the first body chunk after about 170 s, then a pause of about 170 s after offset 500 000;
- the chunks spread over time, not in one block;
- the same through Vite;
- no gzip, no buffering;
- with `cut=300000`, `exit=18` (transfer closed with outstanding data), not `exit=0`.

- [ ] **Step 3: Write `index.html`**

Buttons, each primed inside the click:
- **MP3 `<audio>`**: `audio.src = '/mp3?…'`, then `play()`. It logs the `playing`, `waiting`, `stalled`, `ended` and `error` events, with `error.code`.
- **PCM Web Audio**: `fetch('/pcm?…')`, read the body, convert Int16LE to Float32, play chunks back to back (start buffer 0.3 s). It logs the start, the end and errors.
- A field for the query options.

- [ ] **Step 4: Ask the user to run the matrix and record the results here**

Give the user both LAN URLs: direct, and through Vite. On desktop, run each check through both. Matrix (mark ✅ / ❌ / not tested):

| Check | Chrome | Firefox | Safari macOS | Safari iOS/iPadOS | Chrome Android | Firefox Android |
|---|---|---|---|---|---|---|
| MP3 plays before the end of the download | | | | | | |
| MP3 with `delay=170` and `gap=170@200000` | | | | | | |
| PCM with `delay=170` and `gap=170@500000` | | | | | | |
| MP3 and PCM requests carry the cookie (the spike log shows it; no `401`) | | | | | | |
| MP3 `sample=long`: the log shows a closed read and a resumed `Range` read, and playback goes on (with the chosen range answer) | | | | | | |
| MP3 range answer needed (`206` partial or `200` full) | | | | | | |
| MP3 keeps playing with a locked screen / in the background | n/a | n/a | n/a | | | |
| PCM plays without clicks | | | | | | |
| PCM keeps playing with a locked screen / background / silent switch | n/a | n/a | n/a | | | |
| `cut=300000`: MP3 reports `error`, or `ended` | | | | | | |

- [ ] **Step 5: Decide, and write the decisions under this task in the plan**

- `RANGE_ANSWER` for Task 4: `"partial"` (default) or `"full"`. When browsers need different answers, keep the one most browsers need, and put the MP3 of the others in `BUFFERED_FORMATS`.
- The buffered-playback table for Task 5 (`BUFFERED_FORMATS`): default `{ ios: ['pcm'] }`, where `ios` covers iOS and iPadOS. Add every platform and format that fails a check (spec decision rule). When a desktop browser fails, add a browser key to `currentPlatform` (for example `'safari'`, `'firefox'`) and to its test.
- Untested cells keep the defaults. The final report lists them.
- **Stop gate.** Stop and report if, **on the direct port**, curl (Step 2c) shows buffering, a hang, or a clean end on a cut: the design's server side does not hold, and the plan needs a revision before Task 4. A clean end on a cut **only through Vite** is the spec's accepted limit: note it and go on. A cut that **hangs only through Vite** (Vite pipes the proxied response and does not forward an upstream abort; curl then stops at `--max-time`, exit 28) is a dev-only limit: note it in the report and in Task 9, and go on.

Stop the spike servers.

---

### Task 2: Backend building blocks — cache, PCM type, last-test field

**Files:**
- Create: `src/twicc/tts/audio_cache.py`, `tests/test_tts_audio_cache.py`
- Modify: `src/twicc/tts/openrouter.py`, `src/twicc/tts/config.py`, `tests/test_tts_openrouter.py`, `tests/test_tts_config.py`

**Interfaces:**
- Produces:
  - `class AudioCache(max_bytes: int = 50 * 1024 * 1024, ttl: float = 600.0, now: Callable[[], float] = time.monotonic)` with `get(key: SpeechRequest) -> CachedAudio | None`, `set(key: SpeechRequest, audio: CachedAudio) -> bool`, `clear() -> None`, property `total_bytes`. `class CachedAudio(NamedTuple): content: bytes; content_type: str`. Module instance `speech_cache = AudioCache()`. The key is the `SpeechRequest` itself (a hashable `NamedTuple` of the text and the speech fields).
  - `openrouter.pcm_content_type(content_type: str) -> str`: `"audio/pcm;rate=R;channels=C"`, with the current warning and fallback.
  - `openrouter.audio_duration_seconds(content: bytes, content_type: str) -> float | None` also reads `audio/pcm;rate=R;channels=C` (`len / (R * C * 2)`). The WAV branch stays: until Task 4, the current speech view still measures its WAV output with it. Task 4 removes the WAV branch, and its assertion, with `wrap_pcm_as_wav`.
  - `config._last_test` keeps `first_audio_ms: int | None` (non-negative int, else `None`).

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_audio_cache.py`, a port of `frontend/src/utils/speechCache.test.js` with a fake clock:
- `test_get_returns_a_stored_audio`;
- `test_entries_expire_after_the_ttl` (stored at t=0, `get` at t=600 → `None`);
- `test_reading_refreshes_recency_not_lifetime`;
- `test_total_size_cap_evicts_least_recently_used`;
- `test_an_audio_larger_than_the_cap_is_not_stored` (`set` returns `False`, `total_bytes == 0`).

`tests/test_tts_openrouter.py`:
- `test_pcm_content_type_is_explicit`: `"audio/pcm;rate=44100;channels=1"` stays as is; `"audio/pcm;rate=44100"` becomes `"audio/pcm;rate=44100;channels=1"`.
- `test_pcm_content_type_falls_back_to_24k_mono`, parametrized over `"audio/pcm"`, `"audio/pcm;rate=abc"`, `"audio/pcm;rate=999999"`: the result is `"audio/pcm;rate=24000;channels=1"` and a warning is logged. This replaces `test_malformed_pcm_header_falls_back_to_24k_mono` and `test_pcm_without_rate_falls_back_to_24k_mono`.
- `test_audio_duration_of_pcm_and_mp3`: 88 200 bytes at `rate=44100;channels=1` → `1.0`. Add it next to `test_audio_duration_of_wav_and_mp3`, which stays until Task 4.

`tests/test_tts_config.py`:
- `test_last_test_keeps_first_audio_ms`: `1160` is kept; `-1` and `"x"` give `None`; an old record without the field gives `None` and stays valid.
- Add `"first_audio_ms"` to the `LAST_TEST` constant of `tests/test_tts_config.py`. The exact-dict tests `test_last_test_is_normalized_or_dropped`, `test_a_client_write_keeps_the_recorded_last_test` and `test_record_last_test_and_its_cost` compare with it.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_audio_cache.py tests/test_tts_openrouter.py tests/test_tts_config.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.tts.audio_cache`, `AttributeError: pcm_content_type`, missing key `first_audio_ms`).

- [ ] **Step 3: Implement**

- `AudioCache`: an `OrderedDict` with the same rules as `SpeechCache` (`move_to_end` on `get`, expiry sweep on each call).
- `pcm_content_type`: built on `parse_pcm_format`.
- `audio_duration_seconds`: a PCM branch.
- `_last_test`: add `first_audio_ms`.

Leave `synthesize` and the WAV code in place: Task 4 removes them with their last caller.

- [ ] **Step 4: Run them, expect PASS**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/audio_cache.py src/twicc/tts/openrouter.py src/twicc/tts/config.py tests/test_tts_audio_cache.py tests/test_tts_openrouter.py tests/test_tts_config.py
git commit  # feat(tts): add the backend audio cache and explicit pcm types
```

---

### Task 3: Audio streams and the streaming upstream call

**Files:**
- Create: `src/twicc/tts/streams.py`, `tests/test_tts_streams.py`
- Modify: `src/twicc/tts/openrouter.py`, `tests/test_tts_openrouter.py`

**Interfaces:**
- Consumes: `pcm_content_type` (Task 2), `SpeechRequest`, `SpeechError`, `build_payload`, `_make_client`, `_auth_headers`, `upstream_error_message` (existing).
- Produces, in `streams.py`:
  - `class AudioStream` with these attributes:
    - `id: str`;
    - `label: dict` (model, voice, format, chars, for the log);
    - `content_type: str | None`;
    - `generation_id: str | None`;
    - `state: str`, one of `"opening"`, `"running"`, `"complete"`, `"failed"`, `"released"`;
    - `error: str | None`;
    - `upstream_status: int | None`;
    - `data: bytearray`;
    - `first_audio_ms: int | None`;
    - `generation_ms: int | None`;
    - `ready: asyncio.Future[None]`: resolved when the headers arrive. Before that, it fails with the `SpeechError` of `fail`, with the unexpected exception of the pump, or with `SpeechError("Text-to-speech stopped")` on a release, an expiry, or a cancellation of the pump task by anything else. The task wrapper settles a pending `ready` in a `finally`, so no waiter can hang. Every settle checks `ready.done()` first: a cancelled open view cancels the future it awaits, and a later settle must not raise `InvalidStateError`;
    - `claimed: bool`.

    And these sink methods for the pump:
    - `set_headers(content_type: str, generation_id: str | None) -> None`;
    - `append(chunk: bytes) -> None`;
    - `finish() -> None`;
    - `fail(error: SpeechError) -> None`.

    And these methods for the readers:
    - `async wait_for_more(offset: int) -> None`: returns when `len(data) > offset`, or when the state is no longer `opening` / `running` (`released` included: a release wakes every reader);
    - `touch() -> None`: re-arms the safety timer (called by the read, ending and release views).
  - `start_stream(pump: Callable[[AudioStream], Awaitable[None]], *, label: dict, on_end: Callable[[AudioStream], Awaitable[None]] | None = None) -> AudioStream`.
    - It creates the stream (id `secrets.token_urlsafe(24)`) and runs `pump(stream)` in a task with a strong reference.
    - **Timers** (with `loop.call_later`):
      - the unclaimed timer (`UNCLAIMED_TTL = 30.0`) is armed when `ready` resolves, not at creation: the wait for the headers can last up to the upstream timeout;
      - the safety timer (`SAFETY_TTL = 3 * 3600.0`) is armed at creation and re-armed by `touch()`, so it counts from the last activity.
    - **An unexpected pump exception** (not `CancelledError`), or a pump that returns without `finish()` / `fail()`: it is logged with its traceback, the stream becomes `failed` with `error = "Text-to-speech failed"`, and a pending `ready` gets the exception.
    - When the pump ends with the stream `complete` or `failed`, the same task awaits `on_end(stream)` (errors logged, never raised).
  - `stream_from_bytes(content: bytes, content_type: str, *, label: dict) -> AudioStream`: already `complete` with `ready` resolved, for a cache hit, with the same timers.
  - `get_stream(audio_id: str) -> AudioStream | None`.
  - `release_stream(audio_id: str, outcome: str = "released") -> None`. It is idempotent.
    - While the state is `opening` / `running`, it cancels the pump task (the `async with` closes the upstream). It then sets the state to `released`, settles a pending `ready`, and logs the speech with `outcome`.
    - After `complete` / `failed`, it leaves the task alone, so a running `on_end` still records and caches.
    - In both cases it wakes the readers, drops the stream from the registry and cancels its timers.
    - The expiries call it with `"expired_unread"` (unclaimed) or `"expired_unreleased"` (safety). The open view calls it with `"cancelled"` (Task 4).
  - `ending_of(audio_id: str) -> dict`: `{"state": "complete" | "failed" | "running" | "unknown", "error": str | None}`. `opening` reads as `running`. A missing id is `unknown`.
  - `_reset_for_tests() -> None`.
  - **The log line:** exactly one line per speech, at the first terminal event. It carries the `label`, the bytes, `first_audio_ms`, `generation_ms`, the generation id and the outcome:
    - `ok`;
    - `upstream_error <status>`;
    - `failed` (during the stream);
    - `released`, `cancelled`, `expired_unread`, `expired_unreleased` (from `release_stream`, only while the upstream still ran);
    - `cached` (logged by `stream_from_bytes`).
- Produces, in `openrouter.py`: `async def pump_speech(request: SpeechRequest, api_key: str, stream: AudioStream) -> None`. It runs `client.stream("POST", SPEECH_URL, …)` inside `_make_client(SPEECH_TIMEOUT)`:
  - non-2xx: `await response.aread()`, then `stream.fail(SpeechError(upstream_error_message(...), upstream_status=status))`;
  - a non-audio success: the same, with its body;
  - `audio/pcm…`: `stream.set_headers(pcm_content_type(type), generation_id)`;
  - other `audio/*`: `stream.set_headers(type, generation_id)`;
  - then `stream.append(chunk)` for each chunk of `response.aiter_bytes()`, then `stream.finish()`. Use `aiter_bytes`, not `aiter_raw`: httpx asks for gzip by default, and only `aiter_bytes` decodes, without re-chunking;
  - `httpx.TimeoutException`: `stream.fail(SpeechError("OpenRouter did not answer in time"))`;
  - `httpx.HTTPError`: `stream.fail(SpeechError("Could not reach OpenRouter"))`;
  - `asyncio.CancelledError` propagates.

  It never logs the key.

Every test runs its whole scenario inside one `asyncio.run(...)`: streams, futures and timers belong to the loop that created them.

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_streams.py`. A fake pump drives the sink from an `asyncio.Queue`. `UNCLAIMED_TTL` and `SAFETY_TTL` are monkeypatched to `0.05` where needed.
- `test_ready_resolves_on_headers_and_fails_before_them`;
- `test_append_wakes_waiting_readers`;
- `test_first_audio_and_generation_times_are_measured` (`first_audio_ms` set on the first non-empty `append`, `generation_ms` on `finish`);
- `test_a_finished_stream_lives_until_released`: after `finish`, a wait longer than `UNCLAIMED_TTL` on a claimed stream leaves it readable, and `ending_of` answers `complete`;
- `test_release_cancels_a_running_pump` (the pump sees `CancelledError`, a pending `ready` fails, `ending_of` answers `unknown`, `on_end` is not called);
- `test_release_wakes_a_waiting_reader` (`wait_for_more` returns, the state is `released`);
- `test_release_after_complete_lets_on_end_finish` (a slow `on_end` still completes);
- `test_unclaimed_timer_starts_when_ready_resolves` (with `UNCLAIMED_TTL = 0.05`, a pump that waits 0.2 s before its headers is not expired);
- `test_unclaimed_stream_expires_and_cancels_its_pump`;
- `test_safety_expiry_counts_from_the_last_touch`;
- `test_unexpected_pump_error_fails_ready_and_logs` (also for a pump that returns without `finish` / `fail`);
- `test_a_cancelled_pump_task_settles_ready` (the task is cancelled directly, not through `release_stream`);
- `test_a_settle_after_a_cancelled_waiter_does_not_raise` (cancel a task awaiting `ready`, then `set_headers`: no `InvalidStateError`);
- `test_on_end_runs_for_complete_and_failed_only`;
- `test_on_end_error_is_logged_not_raised`;
- `test_ending_of_maps_states` (opening and running → `running`; failed carries `error`);
- `test_one_log_line_per_speech_without_the_key` (one line for `ok`, for a release while running, for an expiry; no second line when a release follows `complete`).

`tests/test_tts_openrouter.py`, with `httpx.MockTransport` and a custom `httpx.AsyncByteStream` that yields the chunks of a queue:
- `test_pump_streams_chunks_as_they_arrive` (two chunks, appended one by one before the body ends);
- `test_pump_decodes_a_gzip_body` (`Content-Encoding: gzip` upstream → decoded bytes in `data`);
- `test_pump_sends_the_bearer_key_and_payload`: it also keeps `_capture_http_stack_logs` and the `key not in caplog.text` check of today's `test_logs_never_contain_the_key`;
- `test_pump_makes_pcm_types_explicit`;
- `test_pump_upstream_error_fails_before_ready` (`400 {"error":{"message":"Provider returned 400"}}` → `ready` fails with the hint);
- `test_pump_non_audio_success_is_an_error`;
- `test_pump_timeout_and_network_errors` (fail with the two exact messages, before or after the headers);
- `test_pump_cancellation_propagates`.

Delete the `synthesize` tests whose behaviour now lives in these (`test_synthesize_*`, `test_upstream_error_status_is_relayed`, `test_non_audio_success_*`, `test_network_error_message_hides_details`, `test_timeout_message`, `test_cancelled_request_is_logged_and_reraised`, `test_logs_never_contain_the_key`, `test_unexpected_error_is_logged_as_error`). Port their assertions to the pump tests above, and to `test_unexpected_pump_error_fails_ready_and_logs`. `synthesize` itself stays until Task 4 removes it with its last caller.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_streams.py tests/test_tts_openrouter.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.tts.streams`, `AttributeError: pump_speech`).

- [ ] **Step 3: Implement `streams.py` and `pump_speech`**

- **Waking readers:** `AudioStream` keeps one `asyncio.Event`. On each `append`, `finish` or `fail`, it sets that event and replaces it with a fresh one.
- **Registry:** a module dict. All views run on the one event loop, so no lock is needed.
- **Timers:** `release_stream` cancels the stream's `TimerHandle`s.

- [ ] **Step 4: Run them, expect PASS**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/streams.py src/twicc/tts/openrouter.py tests/test_tts_streams.py tests/test_tts_openrouter.py
git commit  # feat(tts): stream the openrouter speech into in-memory audio streams
```

---

### Task 4: The open, read, ending and release endpoints

**Files:**
- Modify: `src/twicc/tts/views.py`, `src/twicc/urls.py`, `src/twicc/tts/openrouter.py` (remove dead code), `tests/test_tts_views.py`, `tests/test_tts_openrouter.py`

**Interfaces:**
- Consumes: Task 2 (`speech_cache`, `CachedAudio`, `audio_duration_seconds`), Task 3 (`start_stream`, `stream_from_bytes`, `get_stream`, `release_stream`, `ending_of`, `pump_speech`), existing `parse_speech_request`, `_record_last_test`, `schedule_cost_log`.
- Produces, the HTTP contract for Task 7:
  - **Open:** `POST /api/tts/speech/`. Body: the current fields plus `config_id` (optional) and `cache: bool` (default `false`). Success: `200 {"audio_id": str, "content_type": str}`. Errors are unchanged: 400, 405, 413 and 503, a 502 `{"error", "upstream_status"?}` for upstream errors, a 500 with its own JSON.
  - **Read:** `GET /api/tts/audio/<audio_id>/`. Unknown id: `404 {"error": "The audio is no longer available"}`.
  - **Release:** `DELETE /api/tts/audio/<audio_id>/` → `204`, also for an unknown id.
  - **Ending:** `GET /api/tts/audio/<audio_id>/ending/` → `200 {"state", "error"}` (from `ending_of`).
  - `views.RANGE_ANSWER: str` (`"partial"` or `"full"`, from the Task 1 decision).

**Rules for the views:**
- **Open:**
  - **The cache applies** only when `cache` is true and `config_id` is not set: a settings test never reads nor fills the cache, whatever `cache` says.
  - On a cache hit (`speech_cache.get(request)`): `stream_from_bytes`, no upstream call.
  - Otherwise: `start_stream(lambda s: pump_speech(request, api_key, s), label=…, on_end=…)`, then `await stream.ready`.
    - On `SpeechError`: `release_stream(id)` (nothing to log twice: the stream is already `failed`), then the current 502 JSON.
    - On another exception: `release_stream(id)`, then the current own-JSON 500 (`Text-to-speech failed`).
    - On `CancelledError`: `release_stream(id, outcome="cancelled")`, re-raise.
- **`on_end` for a `complete` stream** runs three steps. Each one has its own `try` / `except Exception` with a logged error, as `views.py` does today, so one failure never skips the others:
  - `speech_cache.set(request, CachedAudio(bytes(stream.data), stream.content_type))` when the cache applies;
  - `_record_last_test(config_id, {"at", "generation_id", "chars", "generation_ms", "first_audio_ms", "audio_seconds", "cost": None})` when `config_id` is set, with `audio_seconds = audio_duration_seconds(bytes(stream.data), stream.content_type)`;
  - `schedule_cost_log(generation_id, api_key, on_cost)` when there is a generation id, with the current `on_cost` record. The lookup now starts at the complete audio.

  A failed stream does nothing.
- **Read:**
  - It marks the stream `claimed`, calls `touch()`, then returns a `StreamingHttpResponse` over an async generator.
  - Headers: `Content-Type: stream.content_type`, `Cache-Control: no-store`, `Accept-Ranges: bytes` (only with `RANGE_ANSWER == "partial"`), `Content-Length` only when the stream is already `complete` and the read is plain.
  - Plain read: yield the received bytes from offset 0, in slices of at most 64 KiB. Then `await stream.wait_for_more(offset)`, until one of these states:
    - `complete`: end the generator;
    - `released`: end the generator (the reader is gone, or does not matter any more);
    - `failed`: raise `ConnectionAbortedError`, so the ASGI server closes the connection without the final chunk. Uvicorn then logs `Exception in ASGI application` with a traceback, once per failed stream that still has a reader. This is expected and rare: note it in a comment.
  - Range read (`Range: bytes=N-` or `bytes=N-M`), with `RANGE_ANSWER == "partial"`:
    - wait until `len(data) > N` or the stream ends;
    - if there are bytes from `N`: answer `206`, `Content-Range: bytes N-L/T`, where `L` is the last byte received (capped by `M`), and `T` is the total once complete, else `*`. The body holds exactly those bytes, so it is never cut;
    - no byte from `N` on a `complete` stream: `416` with `Content-Range: bytes */T`;
    - no byte from `N` on a `failed` stream: `416` with `Content-Range: bytes */<received>`;
    - on a `released` stream: the unknown-id `404`.

    With `"full"`: ignore `Range`, use a plain read, and send no `Accept-Ranges` header (as the spike tested it).
- **Ending and release** call `touch()` before they answer (release then drops the stream).
  - A helper `parse_range(header: str | None) -> tuple[int, int | None] | None` (bytes unit, single range only; anything else is `None` and gives a plain read).

  Every view keeps the `except Exception` → own-JSON-500 guard before any byte is sent.
- **Remove** `synthesize`, `SpeechAudio`, `_audio_from_response`, `wrap_pcm_as_wav`, the WAV branch of `audio_duration_seconds`, and their remaining tests (`test_wrap_pcm_as_wav_header`, the WAV half of `test_audio_duration_of_wav_and_mp3`, which keeps its MP3 half). They have no caller left.

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_views.py`.
- **Fixtures:**
  - a fake `pump_speech`, set with `monkeypatch.setattr(views, "pump_speech", …)`, drives the stream from a queue. Every streaming test runs one `asyncio.run(scenario())` and reads `response.streaming_content` as an async iterator;
  - an autouse fixture calls `streams._reset_for_tests()` and `speech_cache.clear()`, in both `tests/test_tts_views.py` and `tests/test_tts_streams.py`;
  - a `streams_log` fixture routes the `twicc.tts.streams` logger to `caplog`, like `views_log` does for `twicc.tts.views` (`disable_existing_loggers` can disable it, depending on the import order).
- `test_cancellation_propagates` cancels the view task while it awaits `ready`. It no longer uses a fake that raises `CancelledError` itself.
- **Kept or adapted tests:**
  - every validation test, `413`, `405`, the no-key `503`, and `test_upstream_401_is_not_relayed_as_401`;
  - `test_unexpected_exception_returns_own_json_500`: the fake pump raises `RuntimeError` before its headers;
  - `test_cancellation_propagates`: it now also asserts one log line with `cancelled`, and that the pump was cancelled;
  - `test_a_settings_test_records_its_run_then_its_cost`: the record now comes from `on_end`, and carries `first_audio_ms`;
  - `test_a_message_read_records_no_run`;
  - `test_success_returns_audio_and_schedules_cost` becomes `test_a_complete_audio_schedules_the_cost_lookup` (scheduled at `finish`, not at open);
  - `test_success_without_generation_id_schedules_no_cost`;
  - `test_cost_log_failure_keeps_the_audio` becomes `test_on_end_steps_fail_independently` (a failing cache step still records and schedules the cost).
- **New tests:**
  - `test_open_returns_the_audio_id_and_type`;
  - `test_open_waits_for_headers_only` (the open answers while the pump still has chunks queued);
  - `test_read_streams_chunks_as_they_arrive` (the second chunk arrives after the first has been yielded);
  - `test_read_after_complete_has_a_content_length`;
  - `test_range_read_serves_received_bytes_from_the_offset` (`Range: bytes=5-` → `206`, `Content-Range: bytes 5-9/*`);
  - `test_range_beyond_a_complete_audio_is_416`;
  - `test_no_reader_does_not_stop_the_upstream` (a read closed early, the pump runs to `finish`, `ending` answers `complete`);
  - `test_upstream_failure_cuts_the_read` (the generator raises after the received bytes, and `ending` answers `failed` with the message);
  - `test_range_read_of_a_failed_stream_beyond_its_bytes_is_416`;
  - `test_release_before_any_read_cancels_the_upstream` (nothing cached, no record, no cost lookup);
  - `test_release_ends_a_connected_read` (a read waiting for more bytes ends when the id is released);
  - `test_release_after_complete_still_records_and_caches`;
  - `test_release_of_an_unknown_id_is_204`;
  - `test_read_of_an_unknown_id_is_404_json`;
  - `test_ending_of_an_unknown_id_is_unknown`;
  - `test_cache_hit_serves_without_upstream` (two opens with `cache: true`; the second one never calls the pump);
  - `test_a_cache_hit_survives_eviction` (open a cache hit, `speech_cache.clear()`, the read still returns the bytes);
  - `test_a_settings_test_never_uses_the_cache` (`config_id` with `cache: true`: no read, no fill);
  - `test_a_failed_or_released_audio_is_not_cached`;
  - `test_parse_range` (parametrized: `bytes=0-`, `bytes=5-9`, `bytes=-5` → `None`, `items=0-` → `None`, two ranges → `None`).

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_views.py -q`
Expected: FAIL (no `audio_id` key, 404 on the new routes, no `parse_range`).

- [ ] **Step 3: Implement the views and the routes**

In `src/twicc/urls.py`, next to the TTS routes:
- `path("api/tts/audio/<str:audio_id>/", tts_views.tts_audio)`;
- `path("api/tts/audio/<str:audio_id>/ending/", tts_views.tts_audio_ending)`.

- [ ] **Step 4: Run the whole TTS suite, expect PASS**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS, no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/views.py src/twicc/tts/openrouter.py src/twicc/urls.py tests/test_tts_views.py tests/test_tts_openrouter.py
git commit  # feat(tts): open, stream, end and release speech audio by id
```

---

### Task 5: Frontend pure helpers — PCM, outcome table, playback mode, first-audio figure

**Files:**
- Create: `frontend/src/utils/pcmAudio.js`, `frontend/src/utils/pcmAudio.test.js`, `frontend/src/utils/speechOutcome.js`, `frontend/src/utils/speechOutcome.test.js`
- Modify: `frontend/src/utils/ttsConfig.js`, `frontend/src/utils/ttsConfig.test.js`

**Interfaces:**
- Produces, in `pcmAudio.js`:
  - `parsePcmType(contentType: string) -> { rate: number, channels: number } | null` (only `audio/pcm` with an explicit rate and channels, as the backend sends);
  - `createPcm16Decoder(channels: number) -> { decode(bytes: Uint8Array): Float32Array[] }`: one array per channel, values in `[-1, 1)`. It keeps the trailing bytes of an incomplete frame for the next call;
  - `pcmToWavBlob(chunks: Uint8Array[], rate: number, channels: number) -> Blob` (`audio/wav`, 44-byte header, 16-bit).
- Produces, in `speechOutcome.js`:
  - message constants `REFUSED = 'The browser refused to play the audio'`, `UNREACHABLE = 'Could not reach TwiCC'`, `CUT = 'The audio stopped before the end'`, `GONE = 'The audio is no longer available'`;
  - `speechEndResult(player: { kind: 'ended' } | { kind: 'error', reason: 'refused' | 'network', started: boolean }, backend: { state, error } | null) -> { ok: true } | { ok: false, error: string }`, the table below;
  - `playbackMode(contentType: string, platform: string) -> 'media' | 'pcm-stream' | 'buffered'`:
    - `'buffered'` when the format (`'pcm'` for `audio/pcm…`, else `'mp3'`) is in `BUFFERED_FORMATS[platform]`;
    - else `'pcm-stream'` for PCM;
    - else `'media'`;
  - `BUFFERED_FORMATS` (from the Task 1 decision; default `{ ios: ['pcm'] }`);
  - `currentPlatform(nav = globalThis.navigator) -> string`: `'ios'` for iPhone, iPad or iPod user agents, and for `MacIntel` with `maxTouchPoints > 1` (iPadOS); `'android'` for an Android user agent; else `'desktop'`.
- Produces, in `ttsConfig.js`: `ttsTestStats` adds `` `1st audio ${s} s` `` (from `first_audio_ms`) as the last part; `ttsLastTestFacts` adds `` `first audio after ${s} s` `` after `generated in …`. Both are absent when `first_audio_ms` is `null`.

The end-result table (the spec's *How a playback ends*):

| player | backend | result |
|---|---|---|
| ended | complete | ok |
| ended | failed | error = backend.error |
| ended | running | error = CUT |
| ended | unknown / null | ok |
| error | failed | error = backend.error |
| error refused | complete / running | error = REFUSED |
| error network, started | complete / running | error = CUT |
| error network, not started | complete / running | error = UNREACHABLE |
| error | unknown | error = GONE |
| error | null | error = UNREACHABLE |

- [ ] **Step 1: Write the failing tests**

- `pcmAudio.test.js`:
  - `parses only explicit pcm types`;
  - `decodes int16 little-endian to floats` (`[0x00,0x80]` → `-1`, `[0xff,0x7f]` → `32767/32768`);
  - `keeps an odd byte for the next chunk`;
  - `splits interleaved stereo frames`;
  - `wraps chunks in a wav header` (size fields, rate, channels, byte rate, block align).
- `speechOutcome.test.js`: one test per table row; `playbackMode follows the buffered table`; `currentPlatform detects iOS, iPadOS and Android`.
- `ttsConfig.test.js`:
  - `stats and facts show the first audio`: `first_audio_ms: 1160` → `1st audio 1.2 s` and `first audio after 1.2 s`;
  - `old records without first_audio_ms show no figure`.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/pcmAudio.test.js src/utils/speechOutcome.test.js src/utils/ttsConfig.test.js`
Expected: FAIL (module not found, missing figure).

- [ ] **Step 3: Implement**

Use `DataView.getInt16(offset, true) / 32768`. Use `shortNumber` for the figures.

- [ ] **Step 4: Run them, expect PASS**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/pcmAudio.js frontend/src/utils/pcmAudio.test.js frontend/src/utils/speechOutcome.js frontend/src/utils/speechOutcome.test.js frontend/src/utils/ttsConfig.js frontend/src/utils/ttsConfig.test.js
git commit  # feat(tts): add pcm decoding, the playback end table and the first-audio figure
```

---

### Task 6: The players

**Files:**
- Modify: `frontend/src/utils/speechPlayer.js`
- Create: `frontend/src/utils/pcmStreamPlayer.js`, `frontend/src/utils/pcmStreamPlayer.test.js`

**Interfaces:**
- Consumes: `createPcm16Decoder`, `pcmToWavBlob`, `parsePcmType` (Task 5).
- Produces a common handler shape, `handlers = { onPlaying(), onEnded(), onError(reason: 'refused' | 'network') }`. Each handler fires at most once per play. Nothing fires after `stop()`.
- `speechPlayer.js`, the media element player, keeps `createSpeechPlayer()` and `setSpeechPlayerFactory(factory)`. The player has:
  - `prime()`, unchanged;
  - `playUrl(url: string, handlers)`;
  - `playBlob(blob: Blob, handlers)`;
  - `stop()`;
  - `play(blob, onEnded, onError)`, kept with its current body and contract (it resolves when the sound starts, and rejects like `HTMLMediaElement.play()`), because the store still calls it until Task 7. Task 7 removes it. This keeps message reads working on `main` between the two commits, while the backend still runs its pre-Task-4 code (nobody restarts it before Task 9).

  The `playing` event fires `onPlaying`, and `ended` fires `onEnded`. `error` fires `onError`: `'refused'` for `MEDIA_ERR_DECODE` or `MEDIA_ERR_SRC_NOT_SUPPORTED`, `'network'` otherwise. A rejected `play()` fires `'refused'` for `NotAllowedError` or `NotSupportedError`, and is ignored for `AbortError`.
- `pcmStreamPlayer.js`, the Web Audio player: `createPcmStreamPlayer({ fetchImpl = fetch, audioContextFactory = () => new AudioContext() } = {})` and `setPcmStreamPlayerFactory(factory)`. The player has:
  - `prime()`: create the context once, `resume()` it, inside the click;
  - `play(url: string, { rate, channels }, handlers)`:
    - fetch with an `AbortController` and read the body;
    - decode each chunk;
    - hold back until `START_BUFFER_SECONDS = 0.3` of audio is ready, or the body ends;
    - then schedule `AudioBuffer`s back to back at `next = max(next, ctx.currentTime)`;
    - `onPlaying` with the first scheduled buffer;
    - `onEnded` when the body is done and the last source ends;
    - a read error after the start: let the scheduled sound play out, then `onError('network')`;
    - before the start: `onError('network')` at once;
    - a non-2xx answer: `onError('network')`;
  - `stop()`: abort the fetch, stop and disconnect the sources.

  It does not close the context (it is reused).

- [ ] **Step 1: Write the failing tests**

`pcmStreamPlayer.test.js`, with a fake `AudioContext` (`currentTime`, `createBuffer`, `createBufferSource` returning sources that record `start(when)` and expose `onended`) and a fake `fetchImpl` whose body is a `ReadableStream` controlled by the test:
- `waits for the start buffer before scheduling`;
- `schedules buffers back to back` (the second `start(when)` equals the first `when` + the first duration);
- `catches up after an underrun` (the next buffer starts at `currentTime` when `next` is in the past);
- `fires onPlaying once and onEnded after the last source`;
- `a short body still plays` (a body smaller than the start buffer plays when it ends);
- `a read error after the start reports network after the scheduled sound`;
- `a failed fetch before the start reports network`;
- `stop aborts the fetch and silences every handler`.

The media element player is a thin layer over `HTMLMediaElement`, which `node --test` lacks. The store tests (Task 7) cover it through a fake factory. The browser check (Task 9) covers it for real.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/pcmStreamPlayer.test.js`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement both players**

- [ ] **Step 4: Run them, expect PASS**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/speechPlayer.js frontend/src/utils/pcmStreamPlayer.js frontend/src/utils/pcmStreamPlayer.test.js
git commit  # feat(tts): add the streaming players for media urls and pcm
```

---

### Task 7: The store: open, read, ending, release

**Files:**
- Modify: `frontend/src/stores/textToSpeech.js`, `frontend/src/stores/textToSpeech.test.js`, `frontend/src/utils/speechPlayer.js` (remove the legacy `play` method), `frontend/src/utils/ttsConfig.js`, `frontend/src/utils/ttsConfig.test.js` (remove `speechCacheKey` and its test: no caller left)
- Delete: `frontend/src/utils/speechCache.js`, `frontend/src/utils/speechCache.test.js`

**Interfaces:**
- Consumes:
  - Task 4 HTTP contract;
  - Task 5 (`speechEndResult`, `playbackMode`, `currentPlatform`, `parsePcmType`, `pcmToWavBlob`, messages);
  - Task 6 players.
- Produces, for Task 8:
  - `play(text)` and `playWith(text, fields, { useCache = false, source = 'test', configId = null })` resolve to one of:
    - `{ ok: true, ended: Promise<EndResult> }` when the sound starts;
    - `{ ok: false, error }` when it cannot start;
    - `ABORTED` (`{ ok: false, aborted: true }`).
  - `EndResult` is `{ ok: true }`, `{ ok: false, error }`, or `ABORTED` after a stop by the user or by the service.
  - `statusForText`, `status`, `available`, `stop()` keep their meaning.
  - Network seams, replaceable in tests:
    - `_openSpeech(body, signal) -> Response` (`POST /api/tts/speech/`);
    - `_fetchAudio(url, signal) -> Response` (the whole audio, for the `'buffered'` mode);
    - `_fetchEnding(audioId) -> Response` (`GET …/ending/`);
    - `_releaseAudio(audioId, { keepalive }) -> void` (`DELETE …`, errors ignored).

    All four use `apiFetch`, with the same lazy `import('../utils/api.js')` as today (`node --test` cannot load `api.js`).
  - `audioUrl(audioId) -> string`: `` `/api/tts/audio/${encodeURIComponent(audioId)}/` ``.
  - `installPageHideRelease(target) -> void`, exported. It adds one `pagehide` listener on `target` (once per target) that calls `_releaseAudio(currentId, { keepalive: true })` when an audio id is current. The store calls it with `window` on the first playback, when `window` exists.

**Flow of `startPlayback`:**
1. `store.stop()`, the generation counter, `prime()` of both players, synchronously in the click.
2. `loading`.
3. `_openSpeech({ text, ...fields, cache: useCache, config_id? })`. A non-2xx answer gives `{ ok: false, error }` (current `errorMessageOf`). A network error gives `UNREACHABLE`.
   3b. An open answer that lands after a newer play or a stop (stale generation): release its `audio_id` at once with `_releaseAudio`, then return `ABORTED`.
4. Choose the mode with `playbackMode(content_type, currentPlatform())`:
   - `'media'`: `player.playUrl(audioUrl(id), handlers)`;
   - `'pcm-stream'`: `pcmPlayer.play(audioUrl(id), parsePcmType(type), handlers)`;
   - `'buffered'`: `_fetchAudio(audioUrl(id), signal)` with its own `AbortController`; build a blob (`pcmToWavBlob` for PCM, the response blob otherwise); then `player.playBlob(blob, handlers)`. A fetch failure or a non-2xx answer in this mode counts as `onError('network')` before the start.
5. `onPlaying`: the state becomes `playing`, and the start promise resolves `{ ok: true, ended }`.
6. `onEnded` or `onError`, with the current generation only:
   - `_fetchEnding(id)`: a 2xx answer whose JSON has a known `state` (`complete`, `failed`, `running`, `unknown`) is the backend answer. A failed request, a non-2xx answer or another body is `null`;
   - `result = speechEndResult(...)`;
   - `_releaseAudio(id)`;
   - the state returns to `idle`;
   - if no `playing` event came yet: an error `result` resolves the start promise with `{ ok: false, error }`; an ok `result` (an empty audio that ended at once) resolves it with `{ ok: true, ended: Promise.resolve({ ok: true }) }`;
   - otherwise resolve `ended` with `result`.
7. `stop()`, and every service stop: release the current audio id (`_releaseAudio`), abort the open request and the buffered fetch, stop both players, resolve the pending promises with `ABORTED`. There is no ending step.
8. `installPageHideRelease(window)` on the first playback.

The browser cache (`SpeechCache`) goes away: the backend caches. `play(text)` sends `cache: true`, and `playWith` sends `cache: useCache`.

- [ ] **Step 1: Rewrite the store tests**

Keep the current cases with the new seams: `_openSpeech` answers `jsonResponse(200, { audio_id: 'a1', content_type: 'audio/mpeg' })`. The fake media player records `playUrl` and `playBlob` and exposes the handlers. The fake PCM player is set with `setPcmStreamPlayerFactory`.
- **Rename and adapt:**
  - `primes the player before the request starts` (both players);
  - `sends the text, the active speech fields and cache: true`;
  - `play goes loading → playing → idle on end`;
  - `stop during loading resolves as aborted without error`;
  - `ignores a response that lands after a newer play`: the stale open answer's audio id is released at once (step 3b below);
  - `a network failure resolves as an error and returns to idle`;
  - `a non-JSON error body falls back to the status`;
  - `an error body becomes the result error`;
  - the `applyConfig` cases;
  - the config and model cases, unchanged;
  - `a settings test sends its config id and cache: false, a message read does not send a config id`.
- **New:**
  - `an mp3 plays its audio url through the media player`;
  - `a pcm type streams through the pcm player with its rate`;
  - `a buffered platform plays pcm as a wav blob` (an iPhone `navigator` set with `Object.defineProperty(globalThis, 'navigator', { value, configurable: true })`, restored after the test: Node's `navigator` is a getter-only global; `_fetchAudio` answers the PCM bytes);
  - `stop aborts the buffered fetch`;
  - `the ending step runs when the player ends, not when the download ends` (no ending request before `onEnded`);
  - `a normal end with a complete answer ends ok and releases`;
  - `a late failure reaches the ended promise` (`onEnded` + `{ state: 'failed', error: 'Could not reach OpenRouter' }` → `ended` resolves that error);
  - `a player error before the start resolves the start with the end table result`;
  - `a refused play resolves REFUSED`;
  - `stop releases the audio id and resolves ended as aborted, without an ending request`;
  - `a newer play releases the previous audio id`;
  - `a stale player callback is ignored`;
  - `pagehide releases the current audio id with keepalive` (`installPageHideRelease(new EventTarget())`, then dispatch `new Event('pagehide')`);
  - `an ending answer that is not 2xx counts as no answer`.
- **Delete** the two browser-cache tests (`a cached text plays without a second request`, `a test playback bypasses the cache`). The backend now covers them in Task 4.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/textToSpeech.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement the store, delete the browser cache files, and remove the legacy `play` method of the media player (Task 6)**

- [ ] **Step 4: Run the whole frontend suite, expect PASS**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: PASS. No test imports `speechCache.js` any more.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/stores/textToSpeech.js frontend/src/stores/textToSpeech.test.js frontend/src/utils/speechPlayer.js frontend/src/utils/ttsConfig.js frontend/src/utils/ttsConfig.test.js && git rm frontend/src/utils/speechCache.js frontend/src/utils/speechCache.test.js
git commit  # feat(tts): play speech while it streams, with a late end result
```

---

### Task 8: Late errors in the message button and the settings test

**Files:**
- Modify: `frontend/src/components/ui/TextToSpeechButton.vue`, `frontend/src/components/settings/TextToSpeechSettings.vue`

**Interfaces:**
- Consumes: the two-part outcome (Task 7), and `ttsTestStats` / `ttsLastTestFacts` with the first-audio figure (Task 5: the strings already render). One template change: the `title` of the stats line (`TextToSpeechSettings.vue:347`) lists the parts of `ttsTestStats`; add the time to the first audio to it.

- **Button:**
  - After `const result = await store.play(text)`: keep the current toast for a start error.
  - When `result.ok`, `result.ended.then(end => { if (!end.ok && !end.aborted) toast.error(end.error, { duration: 4000 }) })`.
  - The toast is a module-level service, so it works even after the button unmounts.
- **Settings `testConfig`:**
  - On `result.ok`, keep the current prompt logic.
  - Then handle `result.ended` like the start errors (`TextToSpeechSettings.vue:282-284`). When `end` is neither ok nor aborted, and the config still exists in `store.configs`:
    - set `testErrors.value[id] = end.error`;
    - `delete prompts.value[id]`;
    - set `openIds.value[id] = true`, so the error is visible in the card.
  - A Yes given before the error already deleted the prompt and validated the config. It stands.

- [ ] **Step 1: Implement both handlers**

- [ ] **Step 2: Run the frontend suite and the build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test && npm run build`
Expected: PASS. The build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/ui/TextToSpeechButton.vue frontend/src/components/settings/TextToSpeechSettings.vue
git commit  # feat(tts): show errors that arrive after the speech started
```

---

### Task 9: End-to-end check (user checkpoint)

The user restarts the backend (the agent never does). Then, in Chrome and Firefox on desktop, directly on the backend port and through the Vite dev port, plus one phone check:

- [ ] **Step 1: Run the full suites**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && cd frontend && npm test`
Expected: PASS.

- [ ] **Step 2: Ask the user to restart the backend, then check with them**

The HeyGen free model (`heygen/heygen-voice-1-turbo:free`, PCM) costs nothing. Ask the user before any MP3 check on a paid model.

| Check | Expected |
|---|---|
| A settings test with a 3 000-character text | the sound starts after about 1 s; the last test run shows `1st audio` |
| Network tab during that test | open `POST` (JSON), read `GET` (streamed), then `ending` and `DELETE` at the end of the playback |
| Stop within about 5 s, while the download still runs | the sound stops; `DELETE` sent; the backend log line says `released`; no last test run update |
| Stop after the download (after about 15 s) | the sound stops; `DELETE` sent; the log line already said `ok`; the last test run is recorded |
| On a phone (iOS or Android), through the LAN URL of the Vite dev server, logged in with a password | a message read plays; no `401` on the read request in the backend log |
| A message read twice within 10 minutes | the second read starts at once; the log says `cached` |
| A long read, then reload the page | a `DELETE` may leave on `pagehide` (best effort); without it, the upstream runs to its end and the log says `ok` |
| Through the Vite port | the same streaming (no full wait). If Task 1 found that a cut hangs through Vite, note it as a dev-only limit |

- [ ] **Step 3: Report**

Report the results, the Task 1 matrix cells not tested, and the commits. Do not push.

---

## Spike decisions (filled in Task 1)

- `RANGE_ANSWER`: `"full"`. Chrome 156 on Android takes the end of a short `206 bytes 0-L/*` answer to its first `Range: bytes=0-` as the end of the media. "full" passes in Chrome and Firefox (a long read, closed, then resumed at ×4).
- `BUFFERED_FORMATS`: `{ ios: ['pcm', 'mp3'], safari: ['mp3'] }`. iOS/iPadOS and Safari are not tested: buffered keeps today's behaviour there.
- Read of a failed stream: a `410` (`{"error": "The audio stopped before the end"}`). Otherwise Chrome hangs on a retried cut read.
- Cells not tested: every iOS/iPadOS and Safari cell; desktop Chrome and Firefox (only Android phones were available); Firefox Android MP3 on the lock screen (app switch only).
- Measured (Android):
  - Firefox 160: every cell passes. Web Audio and `<audio>` keep playing in the background. A cut is reported as a normal `ended`.
  - Chrome 156: PCM passes every cell, lock screen included. MP3 passes with "full", lock screen included. A cut hangs (hence the `410`).
