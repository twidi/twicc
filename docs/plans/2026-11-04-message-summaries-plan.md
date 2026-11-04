# Message Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans when implementation is authorized. This document authorizes planning only. Steps use checkboxes for progress tracking.

**Goal:** Add one saved logical-message summary shared by chat, timeline views, and timeline speech, plus temporary selection summaries.

**Architecture:** Reuse native source grouping, synced settings, hermetic/OpenRouter calls, and the existing TTS player and part pipeline. Add a summary-specific service with one small in-process run registry and one Pinia store. Persist successful message results in one Django model. Do not build a general job system.

**Tech Stack:** Django ASGI, SQLite, existing markdown-it-py, Vue 3, Pinia, Web Awesome, existing provider runners and TTS streaming.

**Spec:** `docs/plans/2026-11-04-message-summaries-spec.md`.

## Global Constraints

- The minimum message length is **1,000 content characters**.
- The generation target is approximately **one third of the source**, with a ceiling of **140 words**.
- Summarize the assembled message used by timeline speech, rather than its separate storage lines.
- Save message summaries regardless of whether a manual action or timeline speech requests them.
- Offer a summary action and an original/summary switch in timeline message views.
- Produce text ready for speech. Never use another LLM call to adapt a summary for TTS.
- Stream the visible summary and its speech without waiting for the full result.
- Prepare queued summaries while another message plays.
- Never announce that the audio is a summary.
- Preserve the existing scope of original-content actions: original chat block actions target that block; original timeline speech targets its assembled snapshot.
- Keep timeline admission, ordering, muted inclusion, manual priority, announcements, and actual speech-service failures unchanged except for summary text preparation.
- Reuse existing code and dependencies. No generic queue framework, plugin, second player, or new package.
- Follow AGENTS.md and AGENTS.local.md. Planning changes only until implementation is explicitly authorized.
- During later implementation, do not create a branch/worktree, apply migrations, restart servers, install packages, or modify unrelated files without the required user instruction.
- Tests use `twicc.settings_test`; never set `TWICC_DATA_DIR` on pytest commands.
- When later committing a completed task, stage its exact files only. Use a conventional lowercase subject, explanatory body, and the current model's required co-author trailer. Never hardcode a future executor's model.

## Existing code to reuse

| Existing code | Planned reuse |
| --- | --- |
| `src/twicc/core/services/timeline_messages.py`: `_read`, `_canonical`, `_groups` | Extract a small common source-group reader. Timeline keeps its final-message admission logic. |
| `src/twicc/providers/{claude_code,codex}/helpers.py`: `completion_record_refs`, `extract_timeline_text_from_parsed` | Native identity and exact assembled message text; no second provider-specific parser. |
| `src/twicc/core/content_storage/readers.py`: `iter_current_items` | Read current content without assuming `SessionItem.content` is authoritative. |
| `src/twicc/inline_artifacts/publications.py`: `parse_inline_artifact_blocks` | Remove publication descriptors from summarizer input and content counts; never read artifact source files. |
| `src/twicc/synced_settings.py`, settings WebSocket/bootstrap, frontend settings store | Two ordinary synced keys, rather than a new JSON file or settings endpoint. |
| `src/twicc/tts/adapt.py`: engine selection, fallback, stream opener, time limits | Extract only these shared text-call primitives. Keep adaptation-specific state and original-text fallback in adaptation. |
| `src/twicc/tts/parts.py`: `PartSplitter` | Cut streaming summary text into existing speech parts. |
| `src/twicc/tts/part_source.py`: `PartSource`, `PrefixedParts` | Feed the same pumps and prepend current project/session announcements. |
| `src/twicc/tts/streams.py` | Reuse lifecycle conventions, not its audio-byte storage as a new text framework. |
| `frontend/src/stores/textToSpeech.js` | Keep one player, gesture priming, cancellation generation, owner token, and audio release. |
| `frontend/src/stores/timelineSpeech.js` | Extend its existing pending queue; no second admission feed or timeline queue. |
| `frontend/src/components/session/detail/SessionItemsList.vue`: `scrollToLineNum` | Reveal the canonical group anchor when an action starts from a later block. |
| `frontend/src/components/settings/TextToSpeechSettings.vue`, `frontend/src/utils/ttsConfig.js` | Existing model labels, effective provider availability, OpenRouter key-presence hints and controls. |

## Review Focus

1. Incomplete versus intermediate messages: complete commentary/tool-adjacent messages stay manually eligible; a growing native source does not.
2. Shared identity: a timeline snapshot and changed live source must never receive each other's summary.
3. Shared lifetime: navigation cannot cancel a manual request; closing a selection cannot leak its generation or saved data.
4. Streaming failure: fallback replaces provisional text before audio commitment; after commitment, no restart or original-text substitution occurs.
5. Timeline integration: preparation cannot advance the audible watermark, reorder messages, bypass muted inclusion, or steal manual playback priority.

Each item has explicit regression assertions in its owning tasks below.

## Decisions and interface contracts

These names are planning decisions. Keep them consistent across tasks. Do not create extra wrapper layers around them.

### Settings and source identity

Add synced keys `summaryModel: 'auto'` and `timelineSummaryEnabled: true`.
`summaryModel` accepts `auto`, `haiku`, `luna`, and `openrouter`; it never accepts `off`.
Map `auto` to existing default-agent selection. The automatic timeline switch does not disable manual summaries.

A source request is exactly one of:

- `{session_id: string, line_num: positive integer}` for a stored chat message.
- `{event_id: positive integer}` for an assistant timeline snapshot.
- `{text: nonempty string, purpose: 'selection' | 'test'}` for temporary work.

Never trust a client-supplied assembled message or client-supplied eligibility count for a saved summary.
Selections and settings tests cannot enter saved-message deduplication.

A resolved message descriptor contains:

- `source_key`: opaque logical identity, scoped to provider, session, storage incarnation/history revision, and native message identity.
- `source_hash`: hash of the exact canonical source snapshot before prose cleanup.
- `policy_key`: generation version plus requested model policy and relevant model identities.
- `session_id`, `first_line`, and `text_lines`: anchor and constituent text-row line numbers for chat rendering. `first_line` is the first nonempty assistant text row in `text_lines`, not the first native record or timeline `source_line`. A preceding thinking/tool-use row never becomes the summary host.
- `content_chars`, `eligible`, and a short `unavailable_reason` when needed.
- `saved`: current matching saved-result metadata or null; `previous`: metadata for an older policy on the same source revision or null. Both include `result_hash`, an exact generated-text fingerprint, distinct from `source_hash`.
- `running`: whether matching generation already exists. No generation starts during lookup.

Current source revision always includes exact assembled text, even when local normalization produces the same visible prose.
Keep canonical text for identity, and `input_text` with artifact descriptors removed for generation and routing. Counting normalization does not replace the source fingerprint.
A timeline snapshot uses its saved `data.text` and `source_refs`, never whatever text is currently stored at `source_line`.
For older snapshots without source-incarnation evidence, share with a live source only after validating its native identity and exact source text against current records. Otherwise use a snapshot-specific identity and keep the saved text usable. Never rewrite historical events to manufacture identity evidence.
Chat source resolution does not require a timeline event.

### Backend resources

Use these authenticated, non-public routes in `src/twicc/urls.py`:

| Request | Contract |
| --- | --- |
| `POST /api/summaries/resolve/` with `{sources: [source request, ...]}` | Read-only batch resolution, in input order. Return per-source descriptors/errors. Do not start generation. |
| `POST /api/summaries/` with `{source: source request, regenerate?: boolean, start?: boolean}` | Resolve again, acquire a consumer handle, and reuse/join/start the current result. `start: false` joins only existing work/results. Return `{id, descriptor, state}`. |
| `GET /api/summaries/<id>/` | NDJSON stream of full small text snapshots: `{revision, state, text, engine, fallback_reason, first_text_ms, total_ms, error, saved_id}`. Terminal state ends this response. |
| `DELETE /api/summaries/<id>/` | Release only this consumer handle. It must not release another view's or playback's handle. Idempotent. |
| `GET /api/summaries/saved/<saved_id>/` | Read a saved result with source/policy provenance; no generation. |

`state` is `running`, `complete`, `failed`, or `cancelled`.
A fallback increases `revision` and replaces `text`; it never appends a second attempt to the first.
Send the current snapshot immediately to a joining reader, then subsequent changes.
No heartbeat protocol, polling endpoint, generic event log, or reconnect cursor is needed.
A failed network read releases its own handle. Rejoining uses source resolution and a new handle.

Each acquisition has its own random handle. Equivalent saved-message requests share one internal run.
The registry remains private to summaries and uses the existing single ASGI event-loop convention.
Expire unclaimed handles and abandoned clients with the existing audio lifecycle pattern, including a bounded safety expiry.
Do not make registry contents durable; backend restart retains successful DB rows, not unfinished work.

Keep internal functions in `src/twicc/summaries/service.py`:

- `resolve_sources(sources: list[dict]) -> list[dict]`: synchronous ORM/source lookup for the batch route.
- `async acquire_summary(source: dict, *, regenerate: bool = False, start: bool = True) -> SummaryHandle`.
- `async release_summary(handle_id: str) -> None`.
- `async summary_snapshots(handle_id: str) -> AsyncIterator[dict]`.
- `async acquire_summary_reader(handle_id: str) -> SummaryReader`: independent internal ownership for TTS.

`SummaryReader` supplies current snapshots, an audio-commit marker, and `aclose()`.
The marker means the first body part has entered the speech pump. It blocks future fallback for the shared attempt.
It does not mark a project/session announcement as summary commitment.
Task 3 implements the `PartSource` adapter; the summary service remains independent of TTS errors and pumps.

### Saved model

Add `MessageSummary` to `src/twicc/core/models.py` with one generated migration.
Use `session` FK with `CASCADE`, `source_key`, `source_hash`, `policy_key`, `requested_model`, `engine`, `text`, `metadata`, and `completed_at`.
Uniqueness is `(session, source_key, source_hash, policy_key)`.
`metadata` contains timing/fallback provenance and `result_hash`, not another source-text copy.
Every loaded saved body includes the same fingerprint, computed from the exact saved text. It changes on replacement with different generated text.
Only successful completion inserts or replaces a row. Failed regeneration leaves the previous row intact.
Changing source/policy creates a different variant; no eager rewrite or destructive migration of old results.

Use an existing Channels broadcast convention for `summary_saved`, with source/policy keys and saved ID only.
Frontend consumers invalidate matching lookup metadata and refresh on demand. They do not receive source text in broadcasts.
Also invalidate current chat descriptors on existing `session_items_added`, source/content updates, relevant `session_updated`, and `process_state` events. Include storage-incarnation/history changes. Refresh only descriptors needed by active views and queued listeners. A mounted line can change source/completeness without changing its coordinates.
Invalidate immediately: an old descriptor is not valid current eligibility or content while the refreshed lookup is pending. An old snapshot remains separately bound to its original identity/hash.
Ordinary lookup after reload also discovers saved results, so correctness never depends on receiving a notification.
Do not add summary events to `TimelineEvent`.

### Frontend public surface

One Pinia store: `useSummariesStore()` in `frontend/src/stores/summaries.js`.
Runtime controllers, streams and ownership records stay outside serializable state, scoped per Pinia instance.

- `resolve(sources)` batches/deduplicates read-only descriptor lookups for currently needed sources.
- `request(source, {manual = true, regenerate = false, start = true} = {})` returns a subscription with `id`, reactive `entry`, `ready`, and `release()`.
- `entry` holds descriptor, source request, state, text, revision, error, engine and saved-result provenance.
- `ready` resolves when its backend handle is acquired, not when the full text completes.
- `loadSaved(savedId)` loads a completed/previous result without generation.
- `cancel(entry)` cancels the manual request's interest, not unrelated consumers.
- Manual message requests own their stream until completion or explicit cancellation, even after navigation.
- Temporary selection/test owners release on close/unmount. Neither creates a manual persistent interest.

Add `useTextToSpeechStore().playSummary(summaryRef, {identity, owner = null, announcement = ''})`.
`summaryRef` is `{id: handleId}` for acquired live work, `{saved_id: savedId, result_hash: hash}` for an explicitly selected saved result, or a promise resolving to either shape.
Accepting that promise lets the player reserve ownership before backend handle acquisition finishes, without a separate reservation API.
This includes a previous-policy result: reading it must not open a new generation under current settings.
`identity` is `{sourceKey, sourceHash, variant: 'summary'}` for a message and a temporary selection identity otherwise.
Add `statusForSummary(identity)`; retain `statusForText` and original block behavior.
`playSummary` reserves/loads the shared player synchronously before awaiting its summary reference or the network, as `play` does.
A rejection or obsolete resolution of that promise returns the existing aborted/error result and cannot start audio.
It sends a validated `summary_id` or a `saved_summary_id` with `summary_result_hash`, never a generic client `skip_adaptation` flag.
Saved playback atomically checks the expected result hash while capturing the saved text. A mismatch returns HTTP 409 before audio generation. The frontend refreshes the displayed result and asks for a new explicit read action; it never reads replacement B while displaying A. No saved-result history table is needed.

## Task 1: Resolve native message sources, count content, and persist results

**Files**

- Create `src/twicc/summaries/__init__.py`, `src/twicc/summaries/sources.py`.
- Create `src/twicc/core/services/message_sources.py` by extracting grouping primitives from `timeline_messages.py`.
- Modify `src/twicc/core/services/timeline_messages.py`, `src/twicc/core/models.py`.
- Create one migration using the next available number at implementation time.
- Modify `src/twicc/synced_settings.py`, `src/twicc/cli/settings/_keys.py`.
- Create `tests/test_summary_sources.py`; extend `tests/test_timeline_messages.py` and `tests/test_settings_cli.py` where needed.

**Interfaces**

- `message_sources.py` owns the existing native read/canonical/group functions; timeline imports them without changing final-message admission.
- `sources.py`: `resolve_source(source: dict) -> SummarySource`, `content_text(text: str) -> str`, `content_length(text: str) -> int`.
- Immutable `SummarySource` is a `NamedTuple` holding source/identity keys, canonical text, input text, descriptor coordinates and eligibility; no ORM object escapes into async state.
- Produce the settings and model contracts above for Task 2.

- [ ] **Step 1 — Add source regression tests.** Use real provider fixture shapes and isolated ORM data. Assert two Claude records with one message ID assemble in order; distinct native IDs never merge; a Codex commentary message remains eligible manually. Assert chat/timeline descriptors share identity/hash for the same source and differ for a modified snapshot. Test a historical saved group, a growing live source, a message with no timeline event, and session storage/history replacement.
- [ ] **Step 2 — Pin counting and lifetime rules.** Assert 999/1,000 normalized Unicode characters give false/true eligibility; code and table text count; Markdown/link destinations and artifact descriptors do not. Assert a source made only of artifact markup is empty. Assert no artifact file is read. Add model persistence/purge tests: a session purge cascades, while an unrelated session's rows remain.
- [ ] **Step 3 — Verify RED.** Run `uv run pytest tests/test_summary_sources.py -q`; expect failures for missing summary resolver/model, not fixture/import setup mistakes.
- [ ] **Step 4 — Implement the extraction and resolver.** Reuse `iter_current_items`, native refs and text extractors. Reuse the timeline duplicate-UUID handling and native order. Filter reasoning/tools from assembled text. Use native stop/completion evidence for active sources; do not equate a non-final response with an incomplete response. Historical sources without evidence are permitted when that native message has no active write. Use the existing session run/native publication information; a busy session alone must not reject every older message.
- [ ] **Step 5 — Implement normalization, settings and model.** Use installed markdown-it-py to extract text/code tokens and labels. Remove recognized publication ranges through `parse_inline_artifact_blocks` before parsing; never regex-parse whole Markdown. Collapse whitespace and use Python Unicode length. Include raw canonical text in the source fingerprint. Add the two ordinary synced defaults and their CLI descriptions; normalize invalid stored summary choices to `auto` at use time without rewriting the user's stored value.
- [ ] **Step 6 — Verify GREEN and migration consistency.** Run `uv run pytest tests/test_summary_sources.py tests/test_timeline_messages.py tests/test_settings_cli.py -q`. Run `uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test`. Expected: focused tests pass; no uncreated model change. Generating the migration uses test settings too; do not run `migrate`.
- [ ] **Step 7 — Self-review and commit task files when execution is authorized.** Verify grouping extraction does not change timeline capture. Suggested subject: `feat(summaries): resolve logical message sources and save results`. Record exact changed files and checks for review.

## Task 2: Stream and share summary generation through the existing text engines

**Files**

- Create `src/twicc/summaries/service.py`, `src/twicc/summaries/views.py`.
- Create `src/twicc/providers/text_calls.py` for the small extracted common engine primitives.
- Modify `src/twicc/tts/adapt.py`, `src/twicc/urls.py`; use existing Channels broadcast helpers/conventions.
- Create `tests/test_summary_service.py`, `tests/test_summary_views.py`; extend `tests/test_tts_adapt.py` for unchanged adaptation behavior.

**Interfaces**

- Consume Task 1's resolver, settings and model. Implement all backend resources and service signatures above.
- `text_calls.py` exposes `Engine`, `resolve_engine(choice, text)`, `fallback_engine(engine)`, `stream_text(engine, prompt)`, and engine step limits, by moving the current equivalents rather than duplicating them.
- `adapt.py` retains compatible imports/re-exports for existing callers and its adaptation-specific behavior. Engine selection and limits keep their existing values.

- [ ] **Step 1 — Test actual streaming behavior.** Use controlled async text producers with gates. Assert the first NDJSON update arrives while the producer remains blocked before completion. Assert two equivalent message handles start one producer; joining receives current text; release of one handle keeps the other alive. Assert two equal temporary texts remain independent and never read/write `MessageSummary`.
- [ ] **Step 2 — Test terminal states and provenance.** Assert success saves once; failed/empty/truncated/cancelled output saves nothing; failed regeneration preserves the old row. Assert fallback changes revision and clears provisional text, and fallback is blocked after body audio commitment. Assert current-policy and previous-policy lookup behavior, a source revision change, backend registry reset with DB reuse, and last-owner cancellation closing the producer.
- [ ] **Step 3 — Verify RED.** Run `uv run pytest tests/test_summary_service.py tests/test_summary_views.py -q`; expect missing service/API behavior.
- [ ] **Step 4 — Extract engine primitives and generate summaries.** Preserve hermetic configuration and model constants. Resolve `auto` as the current first enabled agent; preserve the 60,000-character OpenRouter routing and its fallback. Delimit source as data. Prompt for source-language, speech-ready paragraphs, approximately one third length and at most 140 words, preserving decisions/questions/limitations. Do not hard-truncate output or run a repair LLM. Empty or uncompleted results fail. Use existing engine first/inter-delta timeouts and cancellation, with no text in logs.
- [ ] **Step 5 — Add the small run registry and routes.** One run per saved-message revision/policy, separate random consumer handles, current snapshot plus change notification, no generic scheduler. Freeze requested policy and source revision at acquisition. Regenerate joins an already running matching regeneration rather than racing two writes. Use `sync_to_async` for ORM and synced settings. Add request shape/status tests, existing authenticated API routing, idempotent release, unclaimed expiry and stale-handle cleanup. `start: false` must not start new work after a lookup/open race.
- [ ] **Step 6 — Save and notify.** Atomically save only successful current-run completion. Keep prior successful rows during retries. Broadcast `summary_saved` on completion using existing app update conventions. Return per-source resolve errors without failing unrelated entries; cap each lookup batch at 100 requests and let frontend split larger sets. Do not cap or truncate message input beyond existing transport limits and engine routing.
- [ ] **Step 7 — Verify GREEN and commit.** Run `uv run pytest tests/test_summary_sources.py tests/test_summary_service.py tests/test_summary_views.py tests/test_tts_adapt.py -q`. Suggested subject: `feat(summaries): share streamed generation with message persistence`. Report the tested API shape and remaining integration boundaries.

## Task 3: Feed summary streams to the existing TTS pipeline

**Files**

- Create `src/twicc/summaries/speech.py` for the summary-to-PartSource adapter only.
- Modify `src/twicc/tts/views.py`, `src/twicc/tts/audio_cache.py`, `frontend/src/stores/textToSpeech.js`, `frontend/src/components/ui/TextToSpeechButton.vue`.
- Create `tests/test_summary_speech.py`; extend `tests/test_tts_views.py`, `frontend/src/stores/textToSpeech.test.js`, `frontend/src/components/ui/textToSpeechButton.test.js`.

**Interfaces**

- Consume Task 2's independent `SummaryReader` and revision/commit behavior.
- `SummaryParts(reader, limit, first)` implements existing `PartSource`: `next_part()`, `aclose()`, and `split`.
- Implement the `playSummary`/`statusForSummary` frontend contracts. Original `play(text, options)` remains compatible.
- Speech requests add exactly one of `summary_id` or `saved_summary_id` and retain valid voice/script fields. Saved references also require `summary_result_hash`.
Server obtains authoritative summary text from that handle or saved row. Saved results use static parts; live results use `SummaryParts`.

- [ ] **Step 1 — Test streaming speech before completion.** Gate a summary after more than the first part size. Assert the existing speech pump receives its first part before summary completion, `stream_text` starts once, and adaptation is never invoked, even with adaptation enabled. Assert no summary-name announcement is added. Assert a previous-policy saved result reads directly without current-policy generation. Display saved A, replace its DB row with B, and assert the old hash receives 409 with no audio or LLM call. Assert the UI refreshes before a new read can play B.
- [ ] **Step 2 — Test source cleanup and compatibility.** Assert audio owns its own reader after the visual handle releases, stopping audio releases only its reader, and stopping the last consumer cancels generation. Assert fallback reset before the first body part, interrupted text ending on a usable sentence, and no body restart after commitment. Assert scripts with no per-run max wait for completion while visual text still streams.
- [ ] **Step 3 — Verify RED.** Run `uv run pytest tests/test_summary_speech.py -q` and `cd frontend && node --test src/stores/textToSpeech.test.js src/components/ui/textToSpeechButton.test.js` with the new summary cases. Expected: missing summary path, while existing original cases remain valid.
- [ ] **Step 4 — Integrate the adapter.** Use `PartSplitter`, existing request-specific adapted limits, pumps, `PrefixedParts`, and stream ready/error/release flow. Select summary source before simple-text detection/adaptation. Bypass those transformations only for server-resolved summaries. Cancelled or unknown handles return a clear error, not original speech. Reject summary use for TTS configuration tests.
- [ ] **Step 5 — Preserve identity and audio caching.** Bind original source/revision and summary variant in frontend playback state. Preserve status/stop access after a view switch and prevent selecting another view from starting audio. A complete saved summary can reuse audio keyed by its exact text, voice/script fields and announcement. A successful regeneration with different text misses that audio cache. Never cache interrupted summary audio as complete. Do not deduplicate temporary selection audio with saved-message summary resources.
- [ ] **Step 6 — Verify GREEN and commit.** Run `uv run pytest tests/test_summary_speech.py tests/test_tts_views.py tests/test_tts_part_source.py tests/test_tts_parts.py tests/test_tts_audio_cache.py -q` and the two frontend files above. Suggested subject: `feat(tts): read summary streams without another rewrite`. Verify existing original/audio announcement tests still pass.

## Task 4: Add the summary store, settings, and temporary selection panel

**Files**

- Create `frontend/src/stores/summaries.js`, `frontend/src/components/settings/SummarySettings.vue`.
- Create `frontend/src/components/ui/SummaryContent.vue` and `frontend/src/components/ui/SelectionSummaryDialog.vue`.
- Modify `frontend/src/stores/settings.js`, `frontend/src/composables/useWebSocket.js`, `frontend/src/components/app/SettingsPopover.vue`, `frontend/src/components/session/detail/TextSelectionComment.vue`, `frontend/src/App.vue`.
- Create `frontend/src/stores/summaries.test.js`, `frontend/src/components/settings/summarySettings.test.js`, `frontend/src/components/session/detail/textSelectionSummary.test.js`.
- Extend `tests/test_synced_settings_ws.py` for the two ordinary synced keys.

**Interfaces**

- Implement the `useSummariesStore` public contract above. Use Task 3's `playSummary` for summary audio.
- `SummaryContent` receives an entry and emits cancel/retry/show-original; it renders text as plain paragraphs without nested summary actions.
- The temporary dialog is mounted once under `App.vue`, independently of source selection components. No second copy of generation state lives in the dialog.

- [ ] **Step 1 — Test the store lifecycle.** Assert streamed NDJSON split across arbitrary byte boundaries, Unicode text, revised fallback snapshots, terminal failure, saved lookup, joined subscriptions, and `start: false`. Assert navigation/unmount does not release a manually requested message; explicit cancellation releases that interest only. Assert batched lookup of repeated lines shares requests and splits more than 100 sources. Network failures must become visible errors rather than unhandled promises.
- [ ] **Step 2 — Test settings and selection isolation.** Assert default `auto` and timeline On; disabled providers and absent OpenRouter key match effective choices; a stored unavailable preference stays stored. Assert a selection action appears for rendered/file origins without TTS, excludes persistent element mode, captures text before selection clears, survives source widget unmount, and closes without DB/cache persistence. Closing or retrying a panel cannot publish a previous request's late response into its replacement.
- [ ] **Step 3 — Verify RED.** Run `cd frontend && node --test src/stores/summaries.test.js src/components/settings/summarySettings.test.js src/components/session/detail/textSelectionSummary.test.js`. Expected: missing summary UI/store behavior.
- [ ] **Step 4 — Implement settings using existing sync.** Add keys to frontend state, validators, setters and outgoing synced collection. Reuse existing bootstrap/settings update paths and model labels. Reuse adaptation option/hint helpers with Off filtered out and `auto` mapped to its default-agent choice; do not offer model settings beyond the existing choices. Add the dedicated settings section with threshold/target hints, default-on timeline switch and temporary streamed test text. Use backend key-presence information already supplied by TTS bootstrap even when no voice config is active.
- [ ] **Step 5 — Implement the store and shared content view.** Keep one stream per matching entry per Pinia instance and independent subscription ownership. Store a manual request independently of mounted components. Add `summary_saved` handling to invalidate descriptors across app views. Reuse existing item/session/process notifications for source, storage/history and completion invalidation; add no second notification feed. Do not statically import router/stores in a way that introduces cycles. Load summary bodies only when needed; metadata lookup must never generate. Use an explicit previous-policy label and allow viewing the prior result until a new successful result exists.
- [ ] **Step 6 — Add the fourth selection action and responsive dialog.** Follow current touch tap fallback. Capture source text first, open the app-level temporary dialog, then clear/dismiss the source selection. Use regular Web Awesome components already imported in `main.js`; add explicit imports if any are new. Offer short source excerpt, streamed text, Copy, Read aloud, Retry, Cancel/Close. Close discards temporary text and ownership. Keep keyboard/Escape/focus and mobile sizing consistent with existing dialogs.
- [ ] **Step 7 — Verify GREEN and commit.** Run the frontend command above plus `src/components/session/detail/TextSelectionComment.test.js` and `src/components/session/detail/textSelectionRead.test.js`. Run `uv run pytest tests/test_synced_settings_ws.py tests/test_settings_cli.py -q`. Suggested subject: `feat(summaries): add settings and temporary selection summaries`.

## Task 5: Show shared summaries in chat and timeline messages

**Files**

- Modify `frontend/src/components/ui/MarkdownContent.vue`, `frontend/src/components/session/detail/items/TextContent.vue`.
- Modify `frontend/src/components/session/detail/SessionItem.vue`, `SessionItemsList.vue`, `items/claude_code/ContentList.vue`, `items/codex/AssistantMessage.vue` and `items/codex/Message.vue` only for necessary source-context propagation.
- Modify `frontend/src/components/timeline/TimelineEventRow.vue`, `TimelineMessage.vue`.
- Create `frontend/src/composables/useMessageSummary.js`, `frontend/src/components/session/detail/items/MessageSummary.vue` and focused `messageSummary.test.js` / `timelineSummary.test.js` files.

**Interfaces**

- `useMessageSummary(sourceRequest)` uses Task 4's batched resolver/store and returns descriptor, selected view, entry and summarize/show-original actions. It never assembles source text itself.
- `SessionItem` provides summary context only for real assistant rows; no synthetic user, tool, reasoning, compact-summary or streamed-publication rows.
- `MarkdownContent` accepts an optional summary action/context. Keep it opt-in independently of `speakable`.
- One `MessageSummary` host lives on `descriptor.first_line`. All constituent `text_lines` consult the same group view state.

- [ ] **Step 1 — Test grouped rendering and source scope.** Use two assistant rows with one logical descriptor, plus an independent tool between them. Assert only one summary host, a later block's Summarize targets the full native message, and original Copy/TTS still target their own blocks. Assert summary view hides constituent text only, while tools/reasoning/media remain subject to their existing visibility rules. Test Codex proposed-plan before/plan/after segments: one source identity and no duplicate summary hosts. Test a Claude native group starting with a thinking/tool-use row: the first nonempty assistant text row hosts the summary, not the nontext row.
- [ ] **Step 2 — Test saved-result and virtualization behavior.** Assert original default on page reload; a saved summary becomes available without a generation call; a background completion does not switch an already displayed original. Assert group view state survives ordinary virtualized unmounts during the current visit, while a recycled component receiving another session/line resets bindings. Assert a click on a later block reveals the group's first-line anchor using existing `scrollToLineNum`; scroll/height correction does not force the user to the session bottom. Live synthetic sources remain ineligible. Complete a persisted native message at the same session/line without remounting and assert descriptor refresh enables its action. Replace source content/history at those same coordinates and assert the old source hash/result is invalidated before it can be presented as current.
- [ ] **Step 3 — Verify RED.** Run `cd frontend && node --test src/components/session/detail/messageSummary.test.js src/components/timeline/timelineSummary.test.js` with the new rendering fixtures. Expected: missing action/context/shared host.
- [ ] **Step 4 — Propagate source coordinates and add chat actions.** Pass session and row coordinates through the existing rendering boundary; use `getParsedContent` when accessing store items. Register visible source requests with the batched resolver; do not change metadata serializers to issue per-item queries. Render the host once at the canonical anchor; hide only corresponding text blocks when Summary is active. Explicitly expose original controls in loading/error states. For source/policy changes, detach obsolete subscriptions and show the appropriate current/previous result without aliasing revisions.
- [ ] **Step 5 — Add timeline action and active-view speech.** Keep Summarize beside local TTS on assistant rows, including collapsed rows. It expands directly into Summary and joins existing work. Original reads assembled `data.text`; Summary reads its validated handle/result through `playSummary`. Collapse/expand preserves the chosen view during the current mounted row; a different event resets to Original. Selection remains separately available in rendered text.
- [ ] **Step 6 — Preserve playback and notification semantics.** Original/Summary switching does not change audio. Keep a busy Stop control bound to the reading variant/source even if its text view is hidden. A result from another surface uses the same canonical descriptor and source hash. Do not enable message summaries for a changed live source from an older snapshot. Keep non-assistant Markdown consumers unchanged.
- [ ] **Step 7 — Verify GREEN and commit.** Run the new files plus existing `src/components/timeline/TimelineMessage.test.js`, `src/components/timeline/TimelineEventRow.test.js` and `src/components/ui/textToSpeechButton.test.js` with `node --test` from `frontend`. Run `cd frontend && npm run build`. Suggested subject: `feat(summaries): share message views across chat and timeline`.

## Task 6: Prepare timeline summaries ahead and verify the complete feature

**Files**

- Modify `frontend/src/stores/timelineSpeech.js`, `timelineSpeech.test.js`, `timelineSpeech.integration.test.js`.
- Modify `frontend/public/help/text-to-speech.md`; add or extend summary integration tests only where needed.
- Update summary code only for concrete integration defects found by this task's checks.

**Interfaces**

- Extend the current activation with summary subscriptions for admitted pending events and a two-slot speculative preparation count.
- Use `summaries.request({event_id}, {manual: false})` after read-only eligibility resolution.
- Select playback from the existing pending queue. For a summary reading, call `playSummary` immediately with the subscription readiness promise and original event identity/owner/announcement.
Do not await handle acquisition before reserving the player; the selected reading remains occupied during that preparation.
- No new timeline feed, player, or queue module.

- [ ] **Step 1 — Test speculative preparation and ordering.** Gate two summary producers while another message plays; admit a third event and assert only two speculative calls. Resolve them out of order and assert playback remains pending-event order. Confirm summaries save even when generated only for timeline. Assert preparation does not update watermark/context or lock a speculative event ahead of earlier admissible arrivals.
- [ ] **Step 2 — Test priority, setting changes and removal.** Assert manual speech reserves the player before awaiting a summary, interrupts automatic speech, and cannot be overtaken by a queue wake-up. Assert original/manual playback is unchanged by the timeline-summary switch. Gate handle acquisition itself and confirm its delay cannot admit another automatic reading or lose manual priority. Turn it Off during preparation: pending messages read originals, selected reading continues, unneeded speculative owners release. Turn it On for pending work: qualifying messages use current policy. Assert a muted session stays admitted and keeps generation when `includeMuted` is true; hide/scope removals release otherwise unowned work. Stopping/switching listening prevents obsolete continuations from starting audio and retains a manually joined generation.
- [ ] **Step 3 — Test failure behavior and full paths.** Assert failed automatic summary skips that message, reports the error, and continues without reading the original or disabling a working voice. Actual speech-service refusal keeps existing disabling behavior. Assert a failed audio stream after partial summary does not replay or persist unfinished output. Test a >60,000-character OpenRouter-choice source routing through an enabled agent, with unchanged input. Assert local timeline Original/Summary TTS is independent of automatic policy and no summary-specific announcement occurs.
- [ ] **Step 4 — Verify RED, then implement in the current coordinator.** Run `cd frontend && node --test src/stores/timelineSpeech.test.js src/stores/timelineSpeech.integration.test.js`. Initially only new summary cases fail. Add preparation scheduling after admission and purge, including while the player is occupied. Slots count only running speculative work; a selected or manually requested message can acquire priority independently. Do not cancel an in-flight selected reading merely to restore date order. Snapshot summary/voice settings at its existing selection boundary.
- [ ] **Step 5 — Explain usage.** Update English help for model choice/fallback, 1,000-character threshold, shared message persistence, temporary selections, active-view local speech, streamed playback and the timeline switch. Keep existing help manifest conventions. No CHANGELOG entry.
- [ ] **Step 6 — Verify combined behavior.** Run `uv run pytest tests/test_summary_sources.py tests/test_summary_service.py tests/test_summary_views.py tests/test_summary_speech.py tests/test_tts_adapt.py tests/test_tts_views.py tests/test_tts_part_source.py tests/test_tts_audio_cache.py tests/test_timeline_messages.py tests/test_synced_settings_ws.py tests/test_settings_cli.py -q`. Run `cd frontend && npm test`, then `npm run build` from that directory. Run migration consistency check under `twicc.settings_test`. Broaden backend tests only for a named regression risk or new failure.
- [ ] **Step 7 — Validate rendered UI when the environment permits.** Use an existing dev server and available browser tools for chat, timeline, selection and settings, including a narrow viewport. Never restart/apply migrations solely to complete this task. If a newly required backend migration prevents live validation, report the exact limitation and user operations needed. Tests and a build do not establish that a stale running backend exercises this feature. Provider/model constants and SDKs are unchanged, so no paid live diagnostic is automatically required; if implementation changes those, follow AGENTS.md mandatory diagnostics.
- [ ] **Step 8 — Self-review and commit.** Suggested subject: `feat(timeline): prepare shared summaries ahead of speech`. Record precise test evidence and any live-validation limits. Review the complete feature independently after all task reviews, resolve material findings, and keep unrelated changes out of every commit.

## Plan checks and execution boundary

Before later implementation, run the selected execution skill's preflight and create its progress ledger/task list.
The current request is only to write and adversarially review this plan. Do not dispatch implementation agents now.
The earlier scratch SDD directory contains no completed implementation and is not evidence that a task ran.

Self-review the plan against every specification section before sending it to independent reviewers.
Reviewers must examine implementation feasibility, missing integration, conflicting contracts, test value, and unnecessary complexity.
They must not expand scope with generic infrastructure or optional polish.
Record material corrections and repeat review until no material issue remains.

## Adversarial review record

Two independent internal reviewers examined feasibility and reuse, then rechecked the corrected plan.

| Finding | Correction |
| --- | --- |
| Player acquisition could occur after asynchronous summary-handle acquisition | `playSummary` accepts the readiness promise and reserves playback before awaiting it; delayed-acquisition regression test. |
| A saved row can be regenerated while another view displays its previous text | Exact `result_hash` in saved references, atomic validation before audio, explicit 409/refresh behavior; concurrent A/B regression test. |
| Source coordinates can remain unchanged while content/completeness changes | Immediate targeted invalidation through existing item/session/process updates, then active-consumer refresh; same-coordinate regression tests. |
| Native group starts with a nontext record and cannot host an assistant-text summary there | `first_line` is the first contributing text row; thinking/tool-first grouping regression test. |

Both reviewers report no remaining material plan issue after correction.
Their review adds no generic job framework, result-history system, or new dependency.
No implementation, migration, backend restart, package installation, or commit occurs during this planning task.
