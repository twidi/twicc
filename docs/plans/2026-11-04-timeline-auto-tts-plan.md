# Automatic Timeline TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read new `assistant_message.sent` events from one selected timeline, independently of navigation and display filters.

**Architecture:** Keep the existing global TTS player and timeline feed. Add one in-memory listening store that owns a feed and a pending queue. Add spoken names through the common backend part-source path, outside text adaptation.

**Tech Stack:** Existing Django ASGI, Python, Vue, Pinia, timeline REST/WebSocket invalidation, OpenRouter/script TTS, pytest, and Node test harnesses.

**Spec:** [2026-11-04-timeline-auto-tts-spec.md](2026-11-04-timeline-auto-tts-spec.md). Read both documents before execution.

**Status:** Approved and implemented. Targeted verification passes. Live browser and audio validation remains pending.

The unchecked steps below preserve the reviewed execution template. They do not represent current task status.

## Global Constraints

- Read only existing `assistant_message.sent` events and their complete saved text.
- Ignore all timeline display filters.
- Keep one automatic listening scope per browser tab.
- Keep listening state and pending messages in memory.
- Add no database table, migration, durable queue, language detector, new text-model call, or new audio player.
- Activation records the click time as the starting boundary. Only messages dated strictly after that boundary can qualify.
- An event exactly five seconds behind the watermark remains eligible.
- The watermark never moves backwards. Accepted messages do not expire while waiting.
- Do not intentionally delay playback. Do not restart an already selected TTS preparation to reorder arrivals.
- Announcements remain outside message reformulation and use the active TTS configuration.
- Follow `AGENTS.md` and `AGENTS.local.md`. Stay in the current checkout and preserve unrelated changes.
- Do not install packages, restart servers, run migrations, update CHANGELOG, or modify provider skills for this feature.
- Write code, UI copy, comments, and documents in English.
- During execution, maintain the task list with `update_plan`. Commit each completed task using explicit file paths only.
- Commit subjects use `type(scope): lowercase summary`. Include a body and the executor's exact current model trailer.
- Historical planning restriction: do not execute commit commands during planning. Do not stage unrelated documents already in `docs/plans/`.

## Review Focus

1. A slow activation spans several feed pages: collect every qualifying arrival without moving the click boundary. Task 4 and Task 6.
2. A manual replacement crosses transient idle state: no automatic message starts between manual stop and start. Task 2 and Task 6.
3. A stale generation completes after disable, scope replacement, or logout: it must never start audio. Task 4 and Task 6.
4. A project joins a workspace, or a session becomes visible: do not replay its existing history. Task 3 and Task 4.
5. Identical text uses different announcements or adaptation fallbacks: cached audio must contain exactly the requested context. Task 1 and Task 6.

## File map

| File | Responsibility |
| --- | --- |
| `src/twicc/tts/part_source.py` | Prefix existing message parts with plain spoken context |
| `src/twicc/tts/views.py` | Parse announcement; compose parts; pass announcement through cache lookup/write |
| `src/twicc/tts/audio_cache.py` | Describe announcement-aware cache keys; retain existing keys for ordinary speech |
| `frontend/src/stores/textToSpeech.js` | Playback ownership, guarded automatic admission, atomic replacement, reusable priming |
| `frontend/src/utils/timeline/feed.js` | Optional accepted-page notifications and cursor-seeded scope refresh |
| `frontend/src/utils/timeline/eventTime.js` | Existing timestamp ordering plus a precision-preserving time shift |
| `frontend/src/stores/timelineSpeech.js` (new) | One listening scope, live feed, pending messages, context, and lifecycle |
| `frontend/src/App.vue` | Initialize the listening store independently of timeline components |
| `frontend/src/router.js` | Disable listening before existing login-route speech teardown |
| `frontend/src/components/timeline/TimelineFilterBand.vue` | Accessible audio toggle immediately beside the title |
| `frontend/src/components/sidebar/SidebarRail.vue` | Audio overlay and navigation to listening scope |
| `frontend/src/utils/sidebarRail.js` | Dynamic timeline label through the existing rail definition |
| `frontend/public/help/text-to-speech.md` | Explain automatic listening and manual priority |

Tests extend the existing files named in each task. New store and integration tests use existing test helpers.
Do not split out a generic scheduler, event bus, persistent delivery system, or a second timeline implementation.

## Task 1: Add context speech to the common backend TTS pipeline

**Files:**
- Modify: `src/twicc/tts/part_source.py`, `src/twicc/tts/views.py`, `src/twicc/tts/audio_cache.py`
- Test: `tests/test_tts_part_source.py`, `tests/test_tts_views.py`, `tests/test_tts_audio_cache.py`

**Interfaces:**
- Request: optional `announcement: str`, absent or empty for existing clients. Reject non-string values with HTTP 400.
- Add `PrefixedParts(prefix: Sequence[str], body: PartSource)` in `part_source.py`.
- It implements `PartSource.next_part() -> str | None`, `aclose() -> None`, and `split`.
- `_open_speech(..., origin=None)` reads the optional announcement for both OpenRouter and scripts.
- `_end_steps(..., cache_name=None, announcement="")` receives the same normalized announcement used for lookup.
- Keep `(speech_request, engine)` keys for empty announcements. Use `(speech_request, engine, announcement)` otherwise.

- [ ] **Step 1: Add failing backend tests.** Use existing async pump and adaptation fixtures, without live models.

  Test `test_prefixed_parts_preserves_body_and_closes_it`: prefix parts precede body parts; `split` is true before the first audio part.
  Closing before or during body adaptation closes the body. An empty prefix delegates the body's split state.

  Test `test_announcement_outside_adaptation`: the adaptation prompt receives only message text, never the announcement.
  Consume the captured `PartSource`; assert its output equals `["Project. Session.", "Adapted message."]`.

  Parameterize direct, adaptation-off, adapted, and fallback paths. Include OpenRouter and validated script requests.
  Add `test_announcement_cache_isolation` with this assertion matrix:

  ```python
  # Same original message and speech fields in every request.
  assert repeated_announcement_uses_cache is True
  assert changed_announcement_calls_pump is True
  assert omitted_announcement_does_not_play_previous_prefix is True
  assert fallback_cache_uses_actual_engine_and_announcement is True
  ```

  Add a long-title case whose announcement exceeds the configured script `max_chars`.
  Assert every announcement part respects existing splitting limits.
  Assert non-string announcements return 400 on both request paths, without a pump call.

- [ ] **Step 2: Run failing tests.**

  Run from checkout: `uv run pytest tests/test_tts_part_source.py tests/test_tts_views.py tests/test_tts_audio_cache.py -q`.
  Expected: new announcement tests fail; existing tests still describe the unchanged baseline.

- [ ] **Step 3: Implement the prefix and cache changes.**

  Split the plain announcement with the selected request's existing `split` function.
  Wrap `as_part_source(message_text, source_or_parts)` with `PrefixedParts` only when spoken prefix parts exist.
  The wrapper returns prefix parts, then delegates to the original source. Both pumps remain unchanged.
  Keep the actual `AdaptedParts` reference for adaptation reports and fallback cache identity.
  Put the normalized announcement in both cache lookup and successful cache writes.
  Do not send announcement text through `direct_text` or `AdaptedParts`.
  Keep empty-announcement requests behavior-compatible. Do not change public settings or cache retention.

- [ ] **Step 4: Verify the targeted tests pass.** Reuse Step 2's command. Expected: all tests pass.
- [ ] **Step 5: Commit only Task 1 files.** Subject: `feat(tts): support spoken context before messages`.
  Body: explain shared part-source composition, unchanged adaptation input, and cache separation. Include the model trailer.

## Task 2: Give existing speech playback an owner and atomic replacement

**Files:**
- Modify: `frontend/src/stores/textToSpeech.js`
- Test: `frontend/src/stores/textToSpeech.test.js`

**Interfaces:**
- Extend playback state with `owner: string | null`. `null` means manual; an activation token means automatic.
- `prime(): void` reuses both existing players' priming. It does nothing while `status !== 'idle'`.
- `play(text, { origin=null, owner=null, announcement="" }={})` preserves its current start/end result contract.
- An owned `play` refuses to replace a non-idle playback and resolves `{ ok: false, aborted: true, busy: true }`.
  Only this pre-admission refusal has `busy: true`; a reading canceled after admission keeps the existing aborted result.
- `playWith(...)` remains manual and preserves its existing signature and settings behavior.
- `stop({ owner=null }={})` stops any playback when owner is null; otherwise stops only that matching owner.
- Keep `source: 'message' | 'selection' | 'test' | null` unchanged. Automatic speech is a message, with original text.
- `statusForText` continues matching original message text. Announcement text must not change message-button state.

- [ ] **Step 1: Add failing tests using existing fake players.**

  `test('owned play waits rather than replacing busy manual speech')`: no second open request; manual owner stays null.
  `test('manual replacement never publishes an intermediate idle playback')`: observe state synchronously during replacement.
  `test('guarded stop leaves another owner and manual speech untouched')`: no release or player stop for a mismatch.
  `test('priming an idle listener reuses players and priming busy speech is a no-op')`: no new player instance or interruption.
  `test('announcement request preserves original text identity and owner through loading and playing')`: request carries the prefix.
  Assert owner returns to null on success, failure, abort, and empty audio.
  Assert a replaced request's late audio ID is released and never played.

- [ ] **Step 2: Run failing tests.**

  Run: `cd frontend && node --test src/stores/textToSpeech.test.js`.
  Expected: new ownership tests fail on missing behavior.

- [ ] **Step 3: Implement ownership inside the existing store.**

  Move cancellation internals into a private helper used by `startPlayback` and public `stop`.
  Replacement cancels old resources without publishing idle or calling public `stop`.
  Keep unconditional internal priming during playback start. The new public `prime()` busy guard applies only to listening activation.
  Publish the new loading state synchronously before the open request.
  Preserve the existing generation checks, aborted promises, player reuse, release, and ending requests.
  Carry owner and original text through all playback-state assignments.
  Include nonempty announcement in `_openSpeech` payloads for both active configuration kinds.
  Do not introduce another player, public event bus, or stop-reason framework.

- [ ] **Step 4: Run Step 2's tests and existing button tests.**

  Run: `cd frontend && node --test src/stores/textToSpeech.test.js src/components/ui/textToSpeechButton.test.js`.
  Expected: pass, including existing manual and selection behaviors.

- [ ] **Step 5: Commit only Task 2 files.** Subject: `refactor(tts): preserve playback ownership across replacements`.
  Explain manual priority, atomic replacement, and existing player reuse. Include the model trailer.

## Task 3: Expose accepted feed pages without changing display behavior

**Files:**
- Modify: `frontend/src/utils/timeline/feed.js`, `frontend/src/utils/timeline/eventTime.js`
- Test: `frontend/src/utils/timeline/feed.test.js`, `frontend/src/utils/timeline/eventTime.test.js`

**Interfaces:**
- Extend `createTimelineFeed(options)` with optional `onPage({ phase, body }): void`, default no-op.
- `phase` is `'first' | 'before' | 'live' | 'backfill'`. `body` is the unchanged endpoint response.
- First, before, and backfill notifications follow stale-generation validation and accepted state application.
- Live notification follows stale-generation validation and `mergeSessions(body.sessions)`, but precedes row-window filtering.
  It includes every raw arrival and exposes fresh titles before any speech admission can start.
- A truncated live response notifies before the existing reset; its partial events are never admitted for speech.
- A callback may dispose the feed. Recheck staleness before continuation, backfill scheduling, or truncation reset.
- `setScope(projectIds, query, { afterId=null }={})` keeps the current two-argument behavior.
- With a non-null seed, initialize ready state and live cursor, skip the historical first page, and force an incremental fetch.
- Add `shiftEventTime(timestamp: string, deltaMs: number): string` in `eventTime.js` for canonical wire timestamps.
  Preserve the three sub-millisecond digits when shifting by an integer number of milliseconds.

- [ ] **Step 1: Add failing feed tests with the existing in-memory server.**

  `test('page observer sees accepted raw arrivals even outside the rendered window')`: live callback gets omitted old-date rows.
  `test('observer distinguishes first before live and visibility backfill')`: phase sequence and untouched bodies match actual requests.
  `test('stale scope responses never notify observers')`: a held old request cannot enqueue anything after replacement.
  `test('truncation callback can dispose before reset')`: one notification; no automatic history request after disposal.
  `test('scope reseed fetches incrementally from the preserved cursor')`: `afterId` equals the old live cursor, with new project IDs.
  Run the existing no-callback tests unchanged to prove display compatibility.

  In `eventTime.test.js`, assert:

  ```javascript
  assert.equal(shiftEventTime('2026-11-04T10:30:40.123456Z', -5000), '2026-11-04T10:30:35.123456Z')
  ```

- [ ] **Step 2: Run failing tests.**

  Run: `cd frontend && node --test src/utils/timeline/feed.test.js src/utils/timeline/eventTime.test.js`.
  Expected: failures for observer, seed, and shift behavior.

- [ ] **Step 3: Add these small extensions to the existing feed.**

  Preserve the serialized fetch chain, `latest_id` safety, hidden-session handling, pagination cursors, and coalesced invalidation.
  Do not add a new backend timeline endpoint, WebSocket payload, polling interval, or feed implementation.
  Empty project IDs retain their existing global meaning; null still means no scope.
  Do not send new display filters for speech. Task 4 always supplies `{}`.

- [ ] **Step 4: Run feed and composable regression tests.**

  Run: `cd frontend && node --test src/utils/timeline/feed.test.js src/utils/timeline/eventTime.test.js src/composables/useTimelineFeed.test.js`.
  Expected: pass with unchanged existing display expectations.

- [ ] **Step 5: Commit only Task 3 files.** Subject: `feat(timeline): expose accepted feed pages for live consumers`.
  Explain opt-in callbacks and cursor reuse without changing timeline display. Include the model trailer.

## Task 4: Implement one independent automatic-listening store

**Files:**
- Create: `frontend/src/stores/timelineSpeech.js`, `frontend/src/stores/timelineSpeech.test.js`
- Modify: `frontend/src/App.vue`, `frontend/src/router.js`

**Interfaces:**
- Export `useTimelineSpeechStore` with actions `initialize({ dataStore, workspacesStore, connectionStatus, eventTarget=window })`, `enable(pageId)`, `disable()`, `dispose()`.
- `connectionStatus` is the existing reactive `wsStatus` ref from `App.vue`; its connected value is `'OPEN'`.
- `enable(pageId): void` captures click time and primes idle audio synchronously, before any asynchronous import or request.
- State exposed to UI: `activePageId: string | null`, `activeLabel: string`, `pendingCount: number`.
- `initialize` installs one application-lifetime set of scope, player, availability, and stop-action subscriptions; repeated calls are harmless.
- `_fetchPage(params)` lazily delegates to existing `fetchTimelinePage`; tests override this network seam.
- `_now(): number` returns `Date.now()`; tests replace it with a fixed clock. Use canonical six-digit UTC timestamps for comparisons.
- Consume Task 2's `play`, `stop`, playback owner/status, and Task 3's feed callbacks/seed.
- Use `resolveTimelineScope`, `bindTimelineEvents`, `compareNewestFirst`, `shiftEventTime`, and existing naming helpers.

- [ ] **Step 1: Add failing store tests with fake clock, EventTarget, real Pinia, and held fetch/play promises.**

  Use fixed activation time `2026-11-04T10:30:00.000000Z`.
  Test names and required assertions:

  | Test name | Assertions |
  | --- | --- |
  | `activation spans multiple pages without losing arrivals` | More than two full pages after click are retained; old dates excluded; oldest pending message selected first |
  | `one second late is accepted but more than five seconds late is rejected` | After 40 starts, accept 39 and 35; reject 34; watermark stays 40 after 39 |
  | `tolerance is applied at admission not at dequeue` | Accepted messages survive a long manual reading |
  | `selected preparation is never reordered` | 39 arriving during preparation of 40 does not abort 40 |
  | `display filters and navigation do not affect listening` | Only scope IDs and empty query reach the feed; no component ownership |
  | `manual playback preempts only the current automatic message` | A aborted, B/C retained; manual speech runs before B/C |
  | `context follows project and session identity` | Initial both; same neither; session change session; moved session project; both change both |
  | `manual audio and failed automatic speech reset context` | Full next announcement; failed manual start without audio does not itself establish context |
  | `scope replacement and disable invalidate old continuations` | No old request starts audio; unrelated manual speech survives |
  | `visibility backfill and joining projects do not replay history` | Historical backfill ignored; retained-project arrivals not lost during membership change |
  | `shown-session barrier filters ordinary catch-up and bootstrap pages` | Message before `hidden.ended` excluded; message after it retained, even in the same body |
  | `first live message uses newly received session metadata` | Previously unknown session announces the response title, not an ID fallback |
  | `disconnect retains pending readings until successful catch-up` | No pending TTS requests while socket is closed; catch-up completes before drain resumes |
  | `bootstrap load failure retries on next invalidation` | Failed first and before requests both retry without a new activation or timer |
  | `scope invalidation availability loss and truncation disable listening` | Queue cleared; no global fallback; explicit error on incomplete catch-up |
  | `explicit automatic stop differs from manual stop` | Automatic stop disables queue; manual stop allows queue to continue |
  | `reload pagehide logout and disposal cannot restart speech` | All owned listeners and late callbacks become inert; no audio restart |

- [ ] **Step 2: Run failing store tests.**

  Run: `cd frontend && node --test src/stores/timelineSpeech.test.js`.
  Expected: failure until the store exists and the listed behaviors are implemented.

- [ ] **Step 3: Implement lifecycle and initial synchronization in the new store.**

  Keep one feed per active listener, one activation token, one seen-ID set, one sorted pending array, and one current message.
  Keep the visibility barriers and per-project join boundaries only for this activation; clear them when disabling.
  Create the feed with the existing fetch function and raw-page callback. Bind existing window invalidation/reconnection events.
  Capture the click boundary before lazy network loading. Do not use the first response time as a replacement boundary.
  Consume qualifying first-page events. Load older pages only while the pagination cursor remains strictly after the activation boundary.
  Stop bootstrap pagination when that boundary is reached or `hasMore` is false.
  During bootstrap, admit live arrivals too, but do not select a reading until initial qualifying pages are collected.
  This is synchronization, not an intentional reordering timer. Do not enumerate earlier history.
  Ignore `backfill` pages. Consume `before` pages only during this initial bootstrap.
  Bind an invalidation adapter through `bindTimelineEvents`, using its existing `notifyLatest` and `refresh` interface.
  Before forwarding either invalidation, call `feed.retry()` if bootstrap has a first/before `loadError`.
  On initial/before request failure, retain accepted messages and bootstrap state. Resume bootstrap after that retry succeeds.
  Do not add retry timers. A reconnect must retain the activation boundary and queue.
  Disable immediately on truncated live catch-up; dispose the feed before its display-oriented history reset.
  Observe existing `connectionStatus`. While it is not `'OPEN'`, do not select new automatic readings.
  Let any existing audio follow the existing TTS ending/error behavior; do not discard the other pending messages.
  After reconnection, refresh the feed and complete successful catch-up before resuming pending readings.
  A failed catch-up keeps the resume gate closed until a later successful feed refresh. Reuse live invalidation to retry.

- [ ] **Step 4: Implement admission, dynamic scope, and queue processing.**

  Use original event IDs and dates. Validate `assistant_message.sent`, nonempty text, membership, and visibility.
  Admission uses strict activation boundary and an inclusive `shiftEventTime(watermark, -5000)` lower bound.
  Sort pending messages with the existing date/ID comparator reversed. Do not recheck lateness on dequeue.
  Start only when bootstrap is complete, connection catch-up is complete, the listener is current, and TTS is available and idle.
  Remove the selected message from pending before `tts.play(text, { owner: token, announcement })`.
  Set a local processing guard through start and `ended`, even if playback briefly reports idle during ending.
  Verify the activation token after every await. Feed/bootstrap continuations also verify their captured feed is still current.
  Use the owned-play busy guard as the final manual-priority check.
  A result with `busy: true` puts the unstarted message back; an actually interrupted reading is discarded.
  Advance the watermark only after actual audio start and only to a greater event date.
  Preserve original text for message buttons. Build announcement from event project/session IDs and existing readable labels.
  Use `dataStore.getProjectDisplayName` and `sessionMention(id, feed.state.sessions).title` from `utils/timeline/sessions.js`.
  Task 3 merges response metadata before live admission, so first arrivals already have their current titles.
  Observe actual manual `playing` state to invalidate context. Also invalidate context after automatic failure or interruption.
  Show existing error toasts once per failed reading. Continue after message errors; disable on unavailable TTS or `REFUSED` browser playback.

  Recompute scope with the same facts as `TimelinePanel.vue`, without its filters or active-page flag.
  Retain each existing member's activation boundary. Record current time as the boundary for newly joined projects.
  Purge queued entries that leave scope. After bootstrap, seed replacement scope with its previous live cursor.
  During unfinished bootstrap, restart first-page synchronization with the original activation boundary and retained seen-ID set.
  Never seed past initialization pages not yet consumed. Test membership changes during a held bootstrap page too.
  New members admit only events dated after their join boundary; never replay their prior messages.
  Hidden-session notifications and current loaded session flags remove pending hidden-session messages.
  Before admitting any first, before, or live page, inspect its raw `hidden.ended` events.
  Retain the greatest show-event ID per session for this activation.
  Purge pending messages at or below their session's show-event ID, and apply the same limit to subsequent admissions.
  This excludes historical messages in ordinary live catch-up too, while keeping messages after the show event.
  Do not add a visibility-history query, persistent ledger, or reconstruction of native message visibility.
  Do not stop an already selected reading merely because membership changes; explicit disabling still stops owned audio.
  A missing or empty scope disables listening. Never pass its empty membership as the global `[]` scope.

- [ ] **Step 5: Wire explicit stop and application teardown without circular imports.**

  Subscribe to existing Pinia TTS actions with `$onAction`; observe public `stop` before its body.
  For a stop that targets the active automatic owner, clear listening without recursively calling `stop` again.
  Ignore owner-mismatched stops. Manual `stop` leaves listening enabled.
  `disable()` first invalidates the token, clears state, and unbinds feed events; then calls guarded `tts.stop({ owner: oldToken })`.
  That ordering prevents stop subscriptions or pending promises from reviving the queue.
  Initialize from `App.vue` using existing data/workspace stores and `wsStatus`. Do not mount from `TimelinePanel`.
  On application teardown and `pagehide`, disable listening before any idle continuation can run.
  In the existing login router hook, lazily import this store and disable it before the existing `tts.stop()`.
  Do not import the router into a store. Do not add static data-store/workspace-store import cycles.

- [ ] **Step 6: Run store and dependency tests.**

  Run: `cd frontend && node --test src/stores/timelineSpeech.test.js src/stores/textToSpeech.test.js src/utils/timeline/feed.test.js`.
  Expected: pass, with every promise and listener cleaned up after each test.

- [ ] **Step 7: Commit only Task 4 files.** Subject: `feat(timeline): queue assistant speech independently of navigation`.
  Explain click-time eligibility, five-second tolerance, context, and manual priority. Include the model trailer.

## Task 5: Add the timeline toggle and reuse the sidebar rail destination

**Files:**
- Modify: `frontend/src/components/timeline/TimelineFilterBand.vue`, `frontend/src/components/sidebar/SidebarRail.vue`
- Modify: `frontend/src/utils/sidebarRail.js`, `frontend/public/help/text-to-speech.md`
- Test: `frontend/src/components/timeline/TimelineFilterBand.test.js`, `frontend/src/components/sidebar/SidebarRail.touch.test.js`
- Test: `frontend/src/utils/sidebarRail.test.js`, `frontend/src/components/ui/textToSpeechButton.test.js`

**Interfaces:**
- Toggle reads `activePageId` and existing `tts.available`; invokes `enable(props.pageId)` or `disable()` directly inside its click handler.
- Idle label: `Read new assistant messages aloud`. Active label: `Stop reading new assistant messages aloud`.
- Use a native accessible pressed state. Existing `wa-button` and `wa-icon` imports already provide the required components.
- Pass optional `listeningTimelineLabel` to `resolveRailItems(state)`; timeline label becomes `Listening timeline: ${label}` when set.
- Rail navigation maps `activePageId` through the existing global/workspace/project navigation conventions.

- [ ] **Step 1: Add failing UI tests using existing component and rail harnesses.**

  Assert the toggle follows the title and precedes filter controls, with correct label and `aria-pressed`.
  Assert unavailable TTS cannot enable listening; an active control remains usable for stopping.
  Assert activation stays synchronous through the click and passes only `pageId`, not display filters.
  Extend the band's width model for the title-plus-toggle group; preserve line, stacked, and compact layouts.
  Assert rail overlay persists while the player is idle or manual, as long as listening is enabled.
  Assert rail navigation targets global, workspace, project, and worktree listening scopes without toggling them.
  Assert disabled listening preserves the existing All Projects action and label.
  Assert the existing global stop and message stop reach public `tts.stop()` and retain keyboard focus behavior.

- [ ] **Step 2: Run failing UI tests.**

  Run: `cd frontend && node --test src/components/timeline/TimelineFilterBand.test.js src/components/sidebar/SidebarRail.touch.test.js src/utils/sidebarRail.test.js src/components/ui/textToSpeechButton.test.js`.
  Expected: new toggle/overlay tests fail before wiring.

- [ ] **Step 3: Wire the existing components and help.**

  Group the title and toggle without replacing the sticky band or filter controls.
  Use the same audio icon vocabulary as existing TTS buttons. Do not add a separate global control.
  Keep the rail button a navigation action. Its audio overlay is decorative; the dynamic label supplies accessible meaning.
  Reuse `createRailScopeNavigation` and workspace ID helpers. Preserve current empty-workspace query handling.
  Explain navigation independence, ignored display filters, manual priority, native-date cutoff, and five-second late tolerance in help.
  Do not change `TextToSpeechButton.vue` unless its existing stop behavior cannot use Task 4's ownership handling.

- [ ] **Step 4: Run UI tests and production build.**

  Reuse Step 2's command. Expected: pass.
  Run: `cd frontend && npm run build`. Expected: all existing Vite build commands complete successfully.
  No package installation or server restart is part of this task.

- [ ] **Step 5: Commit only Task 5 files.** Subject: `feat(timeline): expose automatic speech in timeline and rail`.
  Explain toggle, destination overlay, accessibility, and help updates. Include the model trailer.

## Task 6: Verify the whole listening path without live text-model calls

**Files:**
- Create: `frontend/src/stores/timelineSpeech.integration.test.js`
- Extend only if a missing assertion needs it: the test files from Tasks 1–5.

**Interfaces:**
- Compose the real timeline-speech store, real TTS store, real feed, and existing fake audio-player factories.
- Replace only network requests, clock, event target, and external speech generation.
- Do not replace queue processing, owner arbitration, or feed page interpretation in these integration tests.

- [ ] **Step 1: Write integration cases and run them.**

  `test('slow activation preserves all click-time arrivals and live overlap')`: hold first response, exceed page size, signal more arrivals.
  Assert all eligible IDs are selected once in pending date order; first response's global cursor does not skip initialization arrivals.

  `test('manual replacement and explicit stop preserve queue ownership')`: play automatic A with B pending, then manual M, then manual N.
  Assert no automatic open request occurs between M and N; A never resumes; B starts after N ends.
  Stop B through the same public action as the rail; assert listening becomes disabled and no further open request occurs.

  `test('disable and immediate reenable defeat stale audio and fetch completions')`: hold both fetch and speech responses.
  Assert old audio IDs are released, old arrivals cannot enter the new activation, and a manual owner survives automatic disable.

  `test('announcements follow audible context through backend request payloads')`: inspect requests for two sessions and manual interruption.
  Assert names appear only in `announcement`; original text and active speech configuration stay unchanged.
  Cover a script configuration as well as the OpenRouter configuration.

  Run: `cd frontend && node --test src/stores/timelineSpeech.integration.test.js`.
  Expected: pass. Investigate failures in owning tasks; do not mask production races by adding sleeps to tests.

- [ ] **Step 2: Run final targeted regression checks.**

  Run: `uv run pytest tests/test_tts_part_source.py tests/test_tts_views.py tests/test_tts_audio_cache.py tests/test_tts_simple_text.py tests/test_tts_script.py tests/test_tts_openrouter.py -q`.
  Run: `cd frontend && node --test src/stores/textToSpeech.test.js src/stores/timelineSpeech.test.js src/stores/timelineSpeech.integration.test.js src/utils/timeline/feed.test.js src/utils/timeline/eventTime.test.js src/components/timeline/TimelineFilterBand.test.js src/components/timeline/TimelinePanel.test.js src/components/sidebar/SidebarRail.touch.test.js src/utils/sidebarRail.test.js src/components/ui/textToSpeechButton.test.js`.
  Expected: pass. Existing test settings isolate provider homes and data. Do not set `TWICC_DATA_DIR` for pytest.
  Run `git diff --check`. Expected: no whitespace errors.
  Broaden testing only if these checks expose a new concern. No live LLM diagnostic is required: no model or SDK changes.

- [ ] **Step 3: Validate the interface on existing development servers when available.**

  Do not start or restart servers without a user request. Backend changes require the user's restart before browser validation.
  If a suitable server is available, activate a project timeline, navigate away, receive two assistant events, and verify sequential audio.
  Interrupt with manual speech, return through the rail, switch to All Projects, then disable. Verify queue ownership and announcements.
  Verify narrow and compact-height layouts, keyboard toggle operation, and overlay labels.
  Use an existing validated TTS configuration. Do not alter user settings or send new agent work solely to generate test messages.
  On mobile browsers, check existing MP3/PCM paths if a device is available. Record unavailable validation honestly.

- [ ] **Step 4: Commit integration tests and directly related corrections only.** Subject: `test(timeline): cover automatic speech ownership and catch-up`.
  Record targeted verification in the commit body and include the model trailer.

## Historical execution handoff

This handoff records the planning-stage instructions. The user subsequently authorizes implementation, and all six tasks are implemented.
Live browser and audio validation remains pending because browser tooling fails before page access.

- Tasks run in order: 1 → 2 → 3 → 4 → 5 → 6. Do not run concurrent implementers against shared files.
- Read the spec defaults before implementation: empty-scope disable, automatic message-level stop, and preparation selection order.
- The initial planning request authorizes writing and reviewing the plan only. The user reviews the plan before authorizing implementation.
- For every implementation task, run its verification before its commit and stage only that task's named files.
- Preserve the reviewed spec and this plan together in version control after implementation approval; do not stage unrelated planning files.
- Report actual checks and any browser-validation limits. Remind the user about the required backend restart; do not perform it unasked.

## Plan review record

Two independent internal reviewers completed two adversarial review rounds against the specification and existing code.
The first round identified four integration corrections:

- Merge newly received session metadata before live admission, so announcements use the correct title.
- Exclude shown-session history in ordinary catch-up and bootstrap, not only explicit backfill pages.
- Retry failed bootstrap pages through the existing invalidation path.
- Preserve pending messages during disconnection and finish catch-up before selecting another reading.

The plan includes each correction and its targeted test.
Both reviewers found no blocking issues in the second round.
Self-review checked specification coverage, cross-task signatures, file paths, task dependencies, and test ownership.
No product code, server state, dependency, or user configuration changed during planning.
