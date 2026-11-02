# Read a Selection, and Skip the Model for Simple Text — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Read button to the collapsed selection widget, and a decision step before the speech adaptation that reads a simple text directly (Markdown cleaned by TwiCC, no model call).

**Architecture:** A new pure module `src/twicc/tts/simple_text.py` classifies a text (given its origin) as simple or complex and cleans a simple one. `_open_speech` in `src/twicc/tts/views.py` calls it before the engine resolution: a simple text becomes a direct reading (cleaned parts, no engine, own cache identity `"direct"`). The frontend store gains a selection playback source and the widget gains a Read/Stop button fed by the consumers that know the selection origin.

**Tech Stack:** Python 3.13, Django 6 ASGI, pytest; Vue 3 `<script setup>`, Pinia, Web Awesome 3, `node --test`.

**Spec:** `docs/plans/2026-11-02-tts-read-selection-design.md` (read it first).

## Global Constraints

- Work on `main`. Prefix every shell command with `cd /home/twidi/dev/twicc-poc && `. Never `uv pip`, never `--active`.
- Python tests: `uv run pytest <files> -q`. Lint: `uvx ruff check <paths>`. Frontend tests: `cd frontend && npm test -- <files>`; `npm test` is `node --test` (plain `node:test` + `node:assert/strict`, no DOM, no component mount): pure functions are imported and tested directly; a component is tested by reading its source (`readFileSync`) and asserting with regexes (see `frontend/src/components/settings/textToSpeechSettings.test.js`); the TTS store is tested with the real store, as `frontend/src/stores/textToSpeech.test.js` does (its `sentForPlay`-style helpers capture the request body).
- Python style: `orjson`, never stdlib `json`; `NamedTuple` for immutable data. Log tests route the module logger to caplog as `tests/test_tts_adapt.py::test_a_failure_logs_one_line_without_the_text` does. No log line holds a text, cleaned or not.
- Fixed values (spec): a short list has at most 5 items, each under 150 characters, no sub-list; a long run of digits is 8 digits or more; the direct cache identity is the engine name `"direct"`; the request body field is `origin`, one of `"rendered"` (a selection from rendered content), `"file"` (a selection from a file); absent means a message (judged on its Markdown source); any other value is a 400.
- The decision never applies to: the option Off (text read as written, no cleanup), a settings test (`config_id` set: it always adapts). It applies before the size limit and the engine check.
- Frontend: Web Awesome icon slots `slot="start"`/`slot="end"`; never import `router.js` statically from utils/stores; the TTS store imports no other store.
- Commits: one or more per task, Conventional Commit subject, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`. `git add` each file by name. No CHANGELOG entry. Never stash, never push.

## Review Focus

1. **A rendered table or code line selected on one line** (no pipe, fence or bullet survives rendering): judged as simple prose and read directly. Accepted by the spec; the test pins it so nobody "fixes" it silently.
2. **Markdown that is almost a table or a list** (a `|` in prose, a hyphen at a line start inside a paragraph, `1.` in a sentence): must not crash the rules and must not flip a prose paragraph to complex without a real marker. Tests in Task 1.
3. **A cleaned text with nothing left to say** (`[](x)`, a lone `1.`): simple, cleaned to nothing, then a 400 `NOTHING_TO_SAY` before any stream. Test in Task 2.
4. **A direct reading and an as-written reading of the same text** never serve each other from the cache. Test in Task 2.
5. **The Read button state when the same text is a message and a selection**: Stop only on the button whose reading is playing. Test in Task 3.

---

### Task 1: The simple/complex rules and the cleanup

**Files:**
- Create: `src/twicc/tts/simple_text.py`
- Create: `tests/test_tts_simple_text.py`

**Interfaces:**
- Produces (Task 2):
  - `ORIGINS = ("rendered", "file")`.
  - `def direct_text(text: str, origin: str | None = None) -> str | None` — the text cleaned of its Markdown when it is simple, else `None` (complex: the model decides). `origin` is `None` for a message (judged on its Markdown source), `"rendered"` or `"file"`. Pure and instant; never raises on any string.

- [ ] **Step 1: Write the failing tests** in `tests/test_tts_simple_text.py` (parametrized tables of `(text, expected)`):
  - Simple, cleaned (`origin=None`): `"Hello there."` → itself; `"This is **bold**, *italic* and ~~gone~~ text."` → `"This is bold, italic and gone text."`; `"# Title\n\nSome words."` → `"Title\n\nSome words."`; `"> quoted line"` → `"quoted line"`; `"See [the docs](https://example.org/a) now."` → `"See the docs now."`; a short bulleted list (`"- one\n- two\n- three"`) → `"one\ntwo\nthree"`; a numbered list (`"1. a\n2. b"`) → `"a\nb"`; a `|` or `1.` or hyphen inside a prose sentence ("a | b is not a table", "in 2024. then", "well - maybe") stays simple and unchanged; `"*italic* start"` and `"---"` at a line start are not list items (italic stays emphasis); an escaped marker (`"\\*not italic\\*"`) is cleaned to the unescaped characters `"*not italic*"`.
  - Complex → `None`: a pipe table with a separator row; a fenced block (```` ``` ````, ```` ```mermaid ````); inline code (`` `x` ``); a bare URL (`https://x.org/a`, but NOT the URL of a `[text](url)` link); a file path (`src/twicc/tts/views.py`, `/home/me/a.txt`, `./x`); a hash (7+ hex chars with at least one digit and one letter) and a UUID; a run of 8+ digits; a list of 6 items; a list with one item of 150+ characters; a list with a sub-list (indented marker); an HTML-looking diagram is covered by the fence case.
  - A loose list (`"- a\n\n- b\n\n- c\n\n- d\n\n- e\n\n- f"`, 6 items separated by blank lines) → `None`; the same with 5 items → simple.
  - A thematic break line (`"first\n\n---\n\nsecond"`) is removed by the cleanup (the result has no `---`).
  - Boundaries: a list of exactly 5 items under 150 characters is simple; an item of exactly 150 characters is complex (limit is "under 150"); 7 digits is simple, 8 is complex.
  - Origins: `origin="file"` → `None` for any text, even `"Hello."`; `origin="rendered"` → `None` when the text holds `"\n"` or `"\t"` (a rendered table or list), the cleaned text for a one-line plain text; a one-line rendered table cell (`"42"`) is simple (the accepted limit, pinned by this test with a comment).
  - Robustness: `""`, whitespace, only symbols (`"---"`, `"|"`) never raise (the result is not asserted beyond being `str | None`); a 100,000-character prose text returns quickly (< 0.5 s).
  - Cleaned-to-nothing: `"[](x)"` and a lone `"1."` (marker at the end of the line) are simple and clean to a string without letters or digits (Task 2 refuses them); `"**"` likewise.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_tts_simple_text.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/twicc/tts/simple_text.py`**

Regex-based, line-oriented. Complex when any of: a table separator row (`^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$`) or a line starting and ending with `|`; a fence (```` ``` ```` or `~~~`); a backtick; a bare URL (`https?://` or `www.` not inside the `(…)` of a Markdown link — strip links to their text first, then look for URLs); a path (a token with `/` and a letter on both sides or leading `/`, `./`, `~/`, `../`; but not `and/or`-style words: require at least one more `/` or a file extension of 1–5 letters after a dot, and no space); a hash/UUID; a run of 8+ digits; a list that is not short. A list is a run of lines starting with `-`, `*`, `+` or `N.`/`N)` followed by whitespace or the end of the line (after optional indentation); runs of list lines separated only by blank lines count as one list (a loose list), so its items add up for the 5-item limit; an indented marker is a sub-list (complex). Prose lines containing a hyphen or `N.` not at a line start are not list items. Then, for a simple text: remove thematic-break lines (`---`, `***`, `___`), strip heading marks (`^#{1,6}\s+`), quote marks (`^>\s?`), list markers, emphasis (`**x**`, `__x__`, `*x*`, `_x_` at word boundaries, `~~x~~`), and links to their text; keep the line structure; trim trailing spaces. `origin="file"` returns `None` first; `origin="rendered"` returns `None` when the text holds `\n` or `\t`, else goes on. In doubt (a construct not recognised with certainty, e.g. HTML tags `<...>`, footnotes, `$$`), return `None`. Module docstring: say that the thresholds are the spec's and the rules are instant and conservative.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_tts_simple_text.py -q && uvx ruff check src/twicc/tts/simple_text.py tests/test_tts_simple_text.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/tts/simple_text.py tests/test_tts_simple_text.py
git commit  # feat(tts): decide whether a text is simple enough to be read directly
```

### Task 2: The direct reading in the open step

**Files:**
- Modify: `src/twicc/tts/views.py` (`parse_speech_request`/`_open_script_speech` bodies, `_open_speech`, `_end_steps` if needed)
- Modify: `src/twicc/tts/audio_cache.py` (docstring: the engine name `"direct"`)
- Test: `tests/test_tts_views.py` (new tests, and the existing ones listed in Step 3b)

**Interfaces:**
- Consumes (Task 1): `simple_text.direct_text(text, origin)`, `simple_text.ORIGINS`.
- Produces (Task 3): the speech body accepts `origin` (`"rendered"` | `"file"`); a direct reading answers as any speech; the TTS log label has `adapted="direct"`; the cache key's engine name is `"direct"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tts_views.py` (follow its `adaptation` fixture and upstream doubles):
  - `test_a_simple_text_is_read_directly`: stored adapt `None` (or `"haiku"`), a provider running; text `"Hello **world**."` → the upstream pump got the cleaned parts (`"Hello world."`), no engine call (`adaptation.calls == []`), label `adapted == "direct"`, and the cache holds `(request, "direct")` after the end.
  - `test_a_complex_text_still_goes_through_the_model`: a text with a table → `adaptation.calls` has the engine call.
  - `test_off_reads_as_written_without_the_decision`: adapt `"off"`, text `"Hello **world**."` → parts are the raw text, label `adapted == "no"`, `adapt_reason == "off"`.
  - `test_direct_comes_before_the_limit_and_the_engine`: a simple text of 13,000 characters (over `ADAPT_MAX_CHARS`) and a case with no provider running → both read directly (cleaned), no `too_long` / `no_provider` reason in the label; "no engine" is a script config (no key needed) with no provider running.
  - `test_a_settings_test_always_adapts`: body with `config_id` and a simple text → the engine is called (not direct), `last_test` records the adaptation fields as today.
  - `test_direct_and_as_written_never_share_the_cache`: read `"Hello **world**."` direct (cached), then with adapt `"off"` and the same text → not served from the direct entry (the pump runs, the parts are raw); and the reverse order.
  - `test_origin_is_validated_and_applied`: `origin: "file"` with a simple text → goes to the model; `origin: "rendered"` with `"a\nb"` → model; `origin: "rendered"` with `"one line"` → direct; `origin: "nope"` → 400; `origin: 3` → 400; absent → message rules.
  - `test_cleaned_to_nothing_is_refused`: a text such as `"[](x)"` passes `has_speech` (it holds the letter `x`) and is simple, and its cleaned form holds no letter or digit → a 400 `NOTHING_TO_SAY`, no stream started, no upstream call.
  - `test_a_direct_reading_works_for_a_script_config`: the script open step gets the cleaned parts too.
  - Log: no test message text appears in any record of the open step (logger routed to caplog).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_tts_views.py -q -k "direct or origin or cleaned"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - Import the function by name in `views.py` (`from twicc.tts.simple_text import ORIGINS, direct_text`), so tests can patch `views.direct_text`.
  - Parse `origin` once (a helper `_origin(data)` returning `None`/`"rendered"`/`"file"`, raising `ValueError("'origin' must be one of: rendered, file")` for any other present value, a JSON `null` counts as absent); call it inside the existing `try`/`except ValueError` of `tts_speech` (next to `parse_speech_request`) and of `_open_script_speech` (next to `_speech_text`), so a bad value is a 400, and pass it to `_open_speech` as a new keyword `origin`.
  - In `_open_speech`: after computing `use_cache`, `choice = await sync_to_async(_stored_adapt_choice)()`; if `choice != "off"` and `config_id is None`: `cleaned = direct_text(speech_request.text, origin)`; if `cleaned is not None`: if `not has_speech(cleaned)` return `_error(NOTHING_TO_SAY, 400)`; else direct path: `engine = None`, `adapt_reason = None`, `label["adapted"] = "direct"`, the cache lookup uses the single name `"direct"`, `parts = split(speech_request._replace(text=cleaned))`, no `AdaptedParts` source, and `_end_steps(..., source=None, adapt_reason=None, cache_name="direct")`. The cache write uses the tag `"direct"` (extend `_end_steps` with a `cache_name` parameter defaulting to the current `source.adapted_by` logic). The non-direct path is unchanged.
  - A direct reading records no `last_test` (it never has a `config_id`).
  - **Step 3b, existing tests.** The new step changes what a simple text does, so the existing tests of `tests/test_tts_views.py` that send the simple text of `VALID_PAYLOAD` ("Hello there") without `config_id` and expect an engine, an adaptation reason or a `(request, None)` cache key must keep their meaning: give those tests a complex text (a pipe table row pair with a separator is enough) through a constant, or patch `views.direct_text` to return `None` in the autouse `_no_adaptation_model` fixture (which reaches every test, the cache tests around lines 912, 957, 1128 and 1188 included) and let the new direct tests undo that patch. Affected: the `adapt`-related tests around lines 1083+ (cache), 1892–1975 (`test_an_adapted_openrouter_reading_gets_an_adapted_source`, `test_an_adapted_script_reading_gets_an_adapted_source`, `test_a_reading_not_adapted_gets_todays_parts_and_says_why`, `test_the_engine_follows_the_choice_and_the_running_providers`, `test_the_cache_key_carries_the_adaptation`), and the script, fallback and OpenRouter-`then` variants up to ~2330; find them by running the whole file after the change: every failure is one of them. The `_no_adaptation_model` autouse fixture (~133) stays.
  - Update the `_open_speech` docstring (the decision, the order, the cache identity) and the `audio_cache.py` module docstring (`"direct"`).

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_views.py tests/test_tts_simple_text.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/tts/views.py src/twicc/tts/audio_cache.py tests/test_tts_views.py
git commit  # feat(tts): read a simple text directly, with no model call
```

### Task 3: The Read button and the selection playback

**Files:**
- Modify: `frontend/src/stores/textToSpeech.js` (`play`, the new getter, `applyConfig`)
- Modify (test): `frontend/src/components/session/detail/TextSelectionComment.test.js` (it stubs each import of the widget by string replacement)
- Modify: `frontend/src/components/session/detail/TextSelectionComment.vue`
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue`, `frontend/src/components/files/FilePane.vue` (the two consumers of the collapsed widget)
- Create: `frontend/src/utils/selectionRead.js`, `frontend/src/utils/selectionRead.test.js`
- Test: `frontend/src/stores/textToSpeech.test.js`, `frontend/src/components/session/detail/textSelectionRead.test.js` (new, source assertions)

**Interfaces:**
- Consumes (Task 2): the speech body field `origin`.
- Produces:
  - `selectionRead.js`: `export const READ_TITLES = { idle: 'Read aloud', loading: 'Cancel the request', playing: 'Stop reading' }`; `export function readIcon(status)` → `'volume-up'` for idle, `'stop'` otherwise; `export function canShowRead({ collapsed, persistent, readOrigin, available, busy })` → true when the widget is collapsed, not persistent, `readOrigin` is `'rendered'` or `'file'`, and (`available` or `busy`).
  - Store: `play(text, { origin = null } = {})` sends `origin` in the body when set and records `source: 'selection'` for it (else `'message'`); `statusForText(text)` stays message-only; new getter `statusForSelection(text)` (`source === 'selection'` and equal text).
  - `TextSelectionComment` prop `readOrigin: { type: String, default: null }` (`null` = no Read button).

- [ ] **Step 1: Write the failing tests**
  - `selectionRead.test.js`: `canShowRead` truth table (collapsed/expanded, persistent, null/unknown origin, available/busy, `available` false with `busy` true shows); `readIcon`; `READ_TITLES` values.
  - `textToSpeech.test.js` (the real store, `sentForPlay`-style): `play(text, { origin: 'rendered' })` sends `origin: 'rendered'` in the body and sets `playback.source === 'selection'`; `play(text)` sends no `origin` and keeps `'message'`; `statusForSelection(text)` is the playback status only for a selection reading of that text and `'idle'` for a message reading of the same text; `statusForText` is the reverse; a `'selection'` reading is stopped when `applyConfig` loses availability (as a `'message'` one is).
  - `TextSelectionComment.test.js`: add the stubs for `../../../stores/textToSpeech` (a `useTextToSpeechStore` returning `{ available, statusForSelection, play, stop }`) and `../../../utils/selectionRead` (a `data:` module cannot resolve a relative import, so stub it, or inline its three constants in the stub), and `readOrigin: null` in `setupComponent`'s default props; all existing tests of the file keep passing.
  - `textSelectionRead.test.js` (source assertions): the widget declares `readOrigin`; the Read button sits inside the `!expanded` collapsed row, after Copy, with `@mousedown.prevent` and `@click.stop`; its handler calls `store.stop()` while busy, else `store.play(props.selectedText, { origin: props.readOrigin })`, toasts the error when `!result.ok && !result.aborted` and again when the late `result.ended` resolves with an error (mirror `TextToSpeechButton.vue`), and does NOT call `close()` nor `clearSourceSelection`; SessionItemsList passes `read-origin="rendered"`, FilePane's file-selection widget passes `read-origin="file"`, and the persistent FilePane card, the BrowserPane widgets and the terminal widget pass none. (FilePane's selection widget also serves selections in the rendered Markdown preview: it passes `file` for all of them, which is safe since a file origin always goes through the model; do not split the origin.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/utils/selectionRead.test.js src/stores/textToSpeech.test.js src/components/session/detail/textSelectionRead.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - `selectionRead.js` as above (pure).
  - Store: as in Interfaces; `startPlayback` already takes a `source`; add the optional `origin` to its body.
  - Widget: import the store and the helpers; computed `busy` = `store.statusForSelection(props.selectedText) !== 'idle'` and `status`; show the button when `canShowRead(...)`; button `variant="neutral" appearance="filled" size="small"`, `<wa-icon>` from `readIcon(status)`, `title`/`aria-label` from `READ_TITLES[status]`, same classes as the other triggers. Stop while busy (`store.stop()`), else `play` and toast as `TextToSpeechButton.vue` does (read it). Never close the widget. In the store, `applyConfig` also stops a `'selection'` playback when availability is lost. Update the store's `playback` comment (`source`: `'message' | 'selection' | 'test' | null`).
  - Consumers: pass the prop as listed.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd frontend && npm test`
Expected: PASS (whole suite).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/selectionRead.js frontend/src/utils/selectionRead.test.js frontend/src/stores/textToSpeech.js frontend/src/stores/textToSpeech.test.js frontend/src/components/session/detail/TextSelectionComment.test.js frontend/src/components/session/detail/TextSelectionComment.vue frontend/src/components/session/detail/SessionItemsList.vue frontend/src/components/files/FilePane.vue frontend/src/components/session/detail/textSelectionRead.test.js
git commit  # feat(tts): add a Read button to the text selection widget
```

### Task 4: Help page and live checks

**Files:**
- Modify: `frontend/public/help/text-to-speech.md`

- [ ] **Step 1: Help page.** Add a short section: the Read button of a selection (where it shows, Stop, the sound goes on when the widget closes); how a text is chosen for the model (simple text is read at once with its Markdown removed; tables, code, links as addresses, paths, hashes, long lists and multi-line selections go through the model; the option Off reads as written; the test of a config always uses the model). Keep the existing sentences true (the *Adapting the text* section says the model rewrites a message: adjust it to say "when the text is not simple").
- [ ] **Step 2 (agent, throwaway script in the session scratch dir, deleted after; no model and no OpenRouter call):** with `DJANGO_SETTINGS_MODULE=twicc.settings` and `django.setup()` (the prod database is read only), call `simple_text.direct_text` on the 10 messages of `/home/twidi/.twicc/scratch/4d40cf22-f398-49d8-9077-841cb8e6b520/cerebras/texts.py` (`TEXTS`) and a set of real assistant and user messages read from the prod DB (read only): report the share read directly, list each direct text with its cleaned form, and flag any cleaned text that still holds a symbol or syntax that would be spoken. No model call is needed.
- [ ] **Step 3 (user checkpoint after `cd /home/twidi/dev/twicc-poc && uv run ./devctl.py restart back` and a reload):** select prose in a session → Read → Stop; select a table → the model path; close the widget while it reads; the same in a file; the settings test still shows the adaptation.

- [ ] **Commit**

```bash
git add frontend/public/help/text-to-speech.md
git commit  # docs(tts): document the Read button and the direct reading
```
