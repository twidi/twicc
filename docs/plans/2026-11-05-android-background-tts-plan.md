# Android Background TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continue all TwiCC TTS through one native Android service, including independent timeline listening with the screen off.

**Architecture:** A common Android service owns playback, arbitration, foreground lifetime, and an authenticated WebSocket. An in-memory backend session selects timeline events and reuses existing speech and summary services. Vue controls this session and mirrors native state; both engine flavors share the audio implementation.

**Tech Stack:** Existing Django 6, Channels, Python 3.13+, Vue 3, Pinia, Kotlin/JVM 17, Android API 29+, target SDK 35. Add common OkHttp 4.12.0 and Media3 ExoPlayer 1.9.0. Keep current GeckoView, AndroidX WebKit, Gradle, and SDK versions.

**Spec:** [Android background TTS](2026-11-05-android-background-tts-spec.md), committed in `0466129de`. Also read [timeline auto-TTS](2026-11-04-timeline-auto-tts-spec.md). The background spec overrides Android ownership and stop semantics.

## Global Constraints

- This document is a plan. Do not execute it without the user's implementation instruction.
- Stay on the current branch. Preserve other agents' changes. Do not restart servers or install packages without authorization.
- Keep `com.twidi.twicc` for both builds. Keep `minSdk = 29` and `targetSdk = 35`.
- All Android TTS uses one native output, in foreground and background. Ordinary browser TTS keeps its existing players.
- Support messages, selections, summaries, configuration tests, and automatic timeline readings.
- Preserve server voices, scripts, adaptation, summaries, announcements, cache, and streaming.
- One `audio_id` already combines all generated fragments. Do not add a fragment playlist.
- Preserve MP3, WAV script configurations, and raw PCM. Script WAV already becomes PCM in `script_audio.py`.
- Skip cancels only the current reading. Stop all also disables listening and clears its queue. Add no pause or seek.
- Manual speech takes priority before any asynchronous preparation. Timeline disable preserves unrelated manual speech.
- Retain native speech across navigation, reload, Activity recreation, and recent-task dismissal. Respect explicit Android stop.
- Do not restore playback after process death, reboot, or backend-session loss. Require explicit activation.
- One active instance and one listening scope per installation. Other devices remain independent.
- Use memory only for coordination. Add no database migration, broker, scheduler, external service, or push infrastructure.
- Keep at most two pending, unsaved summary generations, as today. Do not pre-generate queued audio.
- Use real foreground work. Never use synthetic silence, a fake microphone, or a data-sync timeout workaround.
- Screen-off acceptance includes at least thirty silent minutes before the next eligible event.
- Keep ordinary notification sounds and app-wide WebSocket reliability outside this change.
- Documentation describes the current resulting behavior. Do not write a migration narrative or CHANGELOG entry.

## Review Focus

1. **Immediate backgrounding during preparation:** native ownership must precede every network or summary wait. Tasks 1, 5, 8, 9.
2. **Late replies after offline cancellation:** neither reconnect nor old-instance work may revive discarded speech. Tasks 3, 5, 6.
3. **False audio completion:** fragment gaps, backend generation completion, and player buffering must not end a reading. Tasks 2, 7, 9.
4. **Frontend reload during listening:** restore native scope and muted-session choice before watchers can overwrite them. Tasks 4, 8.
5. **Long silent screen-off waiting:** retain a valid foreground lifetime independently of Media3 inactivity. Tasks 1, 6, 9.

---

## Scope and execution order

This is one feature with three necessary integration boundaries. Separate independently shippable frontend/backend/native projects would leave ownership incomplete.
Execute Tasks 1–9 in order. Keep the native capability disabled while Tasks 1–7 remain incomplete.
Enable it in integration test APKs during Task 8, so Task 9 can test the real frontend path.
Do not describe background support as validated until Task 9 acceptance gates pass.
Tests use fake generation, clocks, sockets, and players. They must not call paid providers or production data.

Task checkpoints use Conventional Commit subjects with a body and the running model's required co-author trailer.
Commit only when the user authorizes implementation commits. Stage each task's explicit paths; never `git add .`.
Until then, finish each task with its diff and test evidence ready for review.

## File responsibilities

Paths below are relative to the repository root. New symbols and files are proposed implementation names.
Existing files must be re-read at execution time because this checkout is shared.

| Files | Responsibility |
| --- | --- |
| `src/twicc/tts/service.py` (new), `src/twicc/tts/views.py` | Callable speech opening and existing HTTP wrappers; one generation pipeline |
| `src/twicc/tts/native_protocol.py` (new) | Versioned native session messages and validation |
| `src/twicc/tts/native_sessions.py` (new) | In-memory ownership, command ordering, resource leases, resume, expiry |
| `src/twicc/tts/native_timeline.py` (new) | Server timeline selection, summary preparation, announcements |
| `src/twicc/tts/native_consumer.py`, `src/twicc/tts/native_views.py` (new) | Authenticated WS control and owned HTTP resources |
| `src/twicc/core/services/timeline_feed.py` | Small reusable activation/catch-up helpers; preserve existing feed semantics |
| `src/twicc/summaries/service.py` | Minimal reference-retention helper for native-owned manual summaries |
| `src/twicc/asgi.py`, `src/twicc/urls.py`, `src/twicc/cli/run.py` | Routing and existing server-lifetime shutdown cleanup only |
| `android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt`, `TtsProtocol.kt` (new) | Pure shared state transitions and wire values |
| `android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt`, `TtsConnection.kt`, `TtsPlayer.kt`, `PcmExtractor.kt`, `TtsNotification.kt` (new) | Service, network, one ExoPlayer, raw PCM extraction, notification |
| `android/app/src/main/java/com/twidi/twicc/browser/BrowserEngine.kt` | Engine-neutral, asynchronous cookie export contract |
| `android/app/src/{webview,gecko}/java/com/twidi/twicc/engines/{webview,gecko}/*BrowserSession.kt` | Export the active profile's credentials; no audio implementation |
| `android/app/src/gecko/assets/android-app-bridge/{manifest.json,background.js,content.js}` | Privileged Gecko cookie handoff and current page bridge |
| `android/app/src/main/java/com/twidi/twicc/{AndroidAppBridge.kt,BrowserActivity.kt,MainActivity.kt}` | Origin-scoped commands, state queries, instance lifecycle hooks |
| `frontend/src/utils/nativeTtsClient.js` (new), `androidAppClient.js` | Small native command adapter and state transport |
| `frontend/src/stores/{textToSpeech.js,timelineSpeech.js}` | Existing store API; select Web or native owner |
| `frontend/src/composables/useMessageSummary.js`, `frontend/src/components/ui/TextToSpeechButton.vue` | Transferable summary source/reference, independent of a deferred page promise |
| `frontend/src/components/{ui/MarkdownContent.vue,timeline/TimelineEventRow.vue}` | Pass the summary request descriptor to existing speech controls |
| `frontend/src/App.vue`, `frontend/src/router.js`, `frontend/src/components/sidebar/SidebarRail.vue` | State attachment, actual logout cleanup, Skip control |
| `android/app/src/main/AndroidManifest.xml`, `android/app/build.gradle.kts`, `android/build.sh`, `android/README.md` | Permissions, common dependencies, checks, documented operation |

Use focused tests listed in each task. Do not move unrelated existing TTS, summary, or timeline code.
If a listed existing path changes before execution, use `rg --files` to locate its current owner and update this map.

## Shared contracts

### Authentication and transport

Reuse the active instance's browser cookie session, including HttpOnly and tunnel cookies.
Export credentials through native engine integration, never through page JavaScript or `document.cookie`.
Copy only cookies matching the exact HTTPS instance and its request paths. Store them in the native client's memory.
Apply native `Set-Cookie` updates to that in-memory jar. Reconnection does not require a mounted page.
Do not persist or log copied cookies. Do not send them across origins or follow authentication redirects silently.

WebView uses its existing profile-specific `CookieManager`, not the global default manager.
Use `CookieManagerCompat.getCookieInfo` when `GET_COOKIE_INFO` is supported, so domain/path/expiry metadata stays available.
If that feature is absent, fail native capability negotiation explicitly; do not fabricate metadata from `getCookie` strings.
Gecko uses a privileged built-in extension background script and a background-only `addon_parent` cookie adapter.
Pinned GeckoView 159 exposes string session-context cookie stores that `browser.cookies` cannot resolve correctly.
Capture the calling tab's actual top-level document principal `originAttributes` before Activity teardown.
Read `Services.cookies` with those exact attributes; never parse or reconstruct a context or store identifier.
Keep the real two-profile HTTPS/WSS export test as a required GeckoView update regression check.
Use `browser.runtime` ports to request the export. Validate the native session, top-level URL, instance UUID, and document generation.
Do not guess a cookie-store identifier from `contextId`. Task 1 proves the exact pinned Gecko API path.
Cookie results travel directly from the privileged extension to native code. The content script receives no cookie values.
Retain the validated store mapping and export operation in the engine runtime until the result transfers to the service.
Closing the Activity must not cancel an already accepted export. No audio work depends on keeping the browser session alive.

Use existing `request_allowed`, `scope_allowed`, remote-access, public-origin, and WS-origin checks.
HTTP remains behind current middleware. WS uses `SessionMiddlewareStack` and the same password fingerprint rule.
Recheck authorization on control commands and heartbeats, including after the password changes or logout invalidates the session.
Reload the Django session from storage for those checks. Reusing cached `scope["session"]` fields cannot detect logout/flush.
Pass the fresh values to the existing authorization helpers; preserve password-fingerprint and remote-access checks.
Do not make existing `/api/tts/` public or grant RPC tokens new permissions.
Cookie expiry or an interactive tunnel challenge ends the session with an actionable authorization error.

Endpoints:

| Endpoint | Contract |
| --- | --- |
| `GET /api/tts/native/capabilities/` | `{protocol:1, available:true}` only when this protocol is implemented |
| `WSS /ws/tts/` | Authenticated native control socket, not the UI `/ws/` socket |
| `GET /api/tts/native/<session_id>/audio/<audio_id>/` | Ownership check, then delegate existing `tts_audio` streaming |
| `GET /api/tts/native/<session_id>/audio/<audio_id>/ending/` | Ownership check, then delegate existing ending result |
| `DELETE /api/tts/native/<session_id>/audio/<audio_id>/` | Idempotent release of this session's audio only |

Audio HTTP requests also send `X-TwiCC-TTS-Session: <resume_secret>`.
The secret binds resources to one in-memory native session. It never replaces instance authentication.
Use a random UUID session ID and `secrets.token_urlsafe(32)` secret. Send the secret only over the instance's HTTPS/WSS origin.
Create these values on the backend after authentication. Never put the secret in URLs, logs, notifications, or frontend state.

### Wire protocol version 1

Use JSON text control frames. Never send audio bytes over WS.
Every frame has `protocol:1` and `type`. Commands also have `seq`, a strictly increasing installation-session integer.
Every selected reading has a native-generated UUID `reading_id` and a monotonic native `generation`.
Every server snapshot has a monotonic `revision`. Each transport has a fresh connection ID.
Ignore callbacks from old connections, generations, instances, and document owners.

| Direction / type | Required payload and meaning |
| --- | --- |
| native → `open` | `client_id` (random process-session UUID); create a fresh session only after a user activation |
| server → `opened_session` | `session_id`, `resume_secret`, `revision`; process-local session now exists |
| native → `resume` | Session ID/secret, last acknowledged command sequence, unacknowledged commands in sequence, and current reading ID/generation/audio ID/phase/started flag; reconcile before offering work |
| native → `listen` | `activation_id`, `scope:{kind,id}`, `page_id`, `include_muted`, `boundary` (UTC, six fractional digits) |
| native → `set_muted` | Existing activation ID and the new boolean; no boundary reset |
| native → `disable` | Existing activation ID; clear only its automatic work |
| native → `manual` | `reading_id`, `generation`, `request:ManualRequest`; replace current reading, preserve listening |
| server → `candidate` | `activation_id`, `event_id`, `identity`, display text; no opened audio yet |
| native → `select` | Candidate event ID plus new `reading_id`/`generation`; backend removes it from pending exactly once |
| server → `audio` | Reading ID/generation, `audio_id`, `content_type`, reading identity/text/source/config ID |
| native → `started` | Reading ID/generation; first actual audio output, not generation completion or player readiness |
| native → `finished` | Reading ID/generation, `outcome:complete|failed|aborted`, optional error |
| native → `skip` | Reading ID/generation; cancel that work and discard its selected event, including an unacknowledged selection |
| native → `stop_all` | End this activation and all current/pending work |
| native → `ping` / server → `pong` | Current authorization check and liveness; use the existing heartbeat conventions |
| server → `state` | `revision`, `ack_seq`, listening descriptor, pending count, preparing/selected identity, configuration availability, error |
| server → `error` | `code`, message, optional reading ID; `session_lost`, `unauthorized`, and `catchup_incomplete` stop affected listening |

`ManualRequest` is a discriminated JSON object:

- Text: `{kind:'text', speech:SpeechBody, source:'message'|'selection'|'test', identity, text, config_id}`.
  `SpeechBody` uses the existing `/api/tts/speech/` fields, including script test fields, cache, origin, and announcement.
- Summary: `{kind:'summary', speech:SpeechBody, summary:SummaryRequest, identity, text, config_id}`.
  `SummaryRequest` is `{saved_id,result_hash}` or `{source,source_key,source_hash,policy_key,handle_id?}`.
  Before a temporary descriptor exists, it can be `{source:{text,purpose},requested_model}`.
  This form permits only temporary selection/test sources and a valid frozen summary-model choice.
  The backend derives its source hash and policy from the frozen text, choice, and current implementation version.
  It acquires an independent reader through the shared summary service. Never supply arbitrary hashes, policy keys, or handles in this form.
  A pending source always remains independently reconstructible. A handle ID is only a reuse hint, never the sole reference.
  Preserve saved-reference mismatch `409/summary_changed/saved_id` semantics.

Use `source` descriptors already accepted by `summaries.service`: event/item sources or temporary `{text,purpose}` sources.
For a still-valid running handle, retain a separate service reader before acknowledging ownership.
If the page releases that handle first, independently acquire the supplied source and verify its frozen descriptor.
Reject a source/hash/policy mismatch explicitly. Never wait for a page promise to reconstruct the request.
Page release of its own subscription cannot release the service's reader.

Keep unacknowledged control commands in memory until `ack_seq` advances. Replay uses the same sequence values.
Do not queue new manual playback during an outage: report unreachable and retain local cancellation of the previous reading.
Skip/Stop all change local state immediately. Their retained commands reconcile the server before any resumed candidate.
Duplicate commands return the current state without repeating preparation. Unknown/expired sessions return `session_lost`, never create automatically.
On resume, apply retained commands first, then reconcile the native current reading against server ownership.
If native has not received an opened resource, re-send the same `audio` reference without calling `open_speech` again.
If native already owns or started that resource, keep its player running and reconcile start feedback idempotently.
If the resource expired before native claimed it, fail that reading and release it; do not regenerate it silently.
A resumed `audio` reference for the same playing resource is metadata only. It cannot call `play` again.

Heartbeat: ping every 30 seconds, pong deadline 5 seconds. Reconnect after 1, 2, 4, 8, 16, then 30 seconds, with ±20% jitter.
Backend resources survive a temporary disconnect for 120 seconds. A disconnected session then cancels tasks, readers, and owned streams.
A connected session without a valid heartbeat for 75 seconds closes and starts that expiry interval.
Stop all closes the session after releasing resources. These are engineering defaults, not a new user setting.

### Native authority and UI state

`NativeTtsSnapshot` contains `version`, `revision`, `instanceId`, `sessionStatus`, `playback`, `listening`, `pendingCount`, and `error`.
`sessionStatus` is `idle|starting|connected|reconnecting|stopped|error`.
`playback` contains reading ID/generation, existing source/text/config/identity, and `idle|loading|playing` UI status.
Internal phases also distinguish buffering and settling. A late frame never regresses a newer local revision.
`listening` contains activation ID, page ID, scope kind/ID/label, boundary, and include-muted choice, or null.
Never expose credentials in this snapshot.

Extend app bridge `getState` with `capabilities.nativeTts = 1` only after native gates pass.
Add `ttsState`, `ttsManual`, `ttsListen`, `ttsSetMuted`, `ttsDisable`, `ttsSkip`, `ttsStopAll`.
A start command returns an immediate accepted/rejected result and operation ID; it never waits for speech generation.
Use the current request/reply bridge. Poll `ttsState` once per second only while the page is visible, plus after commands and visibility return.
This avoids adding two engine-specific push transports. Polling stops when hidden; it never drives backend work or service lifetime.
Raise the limit only for TTS payload commands to the existing backend body limit, `12 * 1024 * 1024` bytes.
Keep the existing 16,384-byte limit for menu commands. Reject oversized TTS explicitly; never truncate a selection.

The frontend adapter preserves `{ok, ended}` promises and existing `speechOutcome.js` results for mounted callers.
Expose `results` in the snapshot: terminal operation ID, whether sound started, start result, and existing `ended` outcome.
This resolves a short reading that starts and finishes between two state polls.
Keep terminal results by operation ID while that page can still be attached; bound the service result cache to 64 entries.
Reloaded pages mirror state; they do not recreate promises for vanished controls or restart speech.
Unsupported capability combinations use current foreground Web behavior. Once a native command is accepted, no Web fallback is permitted.

## Task 1: Prove the engine authentication and foreground-lifetime boundaries

**Files:**
- Modify: `android/app/src/main/java/com/twidi/twicc/browser/BrowserEngine.kt`.
- Modify: both flavor `*BrowserSession.kt` files in the map; Gecko bridge assets.
- Create: `android/app/src/androidTest/java/com/twidi/twicc/tts/TtsPlatformBoundaryTest.kt`.
- Modify: `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`.
- Create: initial `android/app/src/main/java/com/twidi/twicc/tts/TtsConnection.kt` with the common cookie jar and HTTP/WSS probe client. Task 5 completes this same client.
- Create: minimal `android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt`, completed in Task 6.

**Interfaces:**
- Consumes: `Instance`, `BrowserSession`, existing validated bridge sender and profile identity.
- Produces: `BrowserSession.exportTtsCookies(urls: List<String>, callback: (Result<List<TtsCookie>>) -> Unit)`.
- Define common `TtsCookie(name,value,domain,path,hostOnly,secure,httpOnly,expiresAt)` in `TtsConnection.kt`; no engine types.

**Anchor test:** `export_keeps_same_name_path_cookies_and_profile_isolation`.

```kotlin
assertEquals(setOf("/", "/api/"), exported.filter { it.name == "auth" }.map { it.path }.toSet())
assertFalse(exported.any { it.value == "other-instance-secret" })
```

- [ ] **Write failing boundary tests.** With a local HTTPS test server, set distinct HttpOnly cookies in two instance profiles. Export HTTP and WS-path cookies from one profile; assert the other profile and page JS cannot read them. Close the Activity, then complete authenticated native HTTP and WSS requests using the copied cookies. Test a path-scoped tunnel cookie, expired cookie, same-name cookies on different paths, redirect to another origin, and logout invalidation. Close the Activity while export is pending, not only after it completes.
- [ ] **Run RED:** `cd android && ./gradlew :app:connectedWebviewDebugAndroidTest`, then `cd android && ./gradlew :app:connectedGeckoDebugAndroidTest`. Execute the two flavor tasks in separate Gradle invocations because they share the application ID. Expect missing export/service contracts. Use a test-only certificate trust configuration; never alter production trust.
- [ ] **Implement cookie export and the small foreground probe.** WebView uses the active profile manager. Gecko uses its privileged background API and actual tab cookie store. Validate sender/session/document identity before accepting the result. Add common OkHttp 4.12.0 now, with MockWebServer 4.12.0 for tests. This is the transport completed in Task 5. Pin AndroidX test runner 1.6.2, core 1.6.1, and ext JUnit 1.2.1. Use the existing app's test conventions if present; do not install a separate framework.
- [ ] **Verify the service declaration.** Use one non-exported service with `stopWithTask="false"`, `foregroundServiceType="mediaPlayback|specialUse"`, and the special-use property below. Start it from the user click before cookie export. Establish both declared foreground type bits at this visible start, so later playback needs no background service launch. On API 29–33 use only the supported media-playback bit. Declare `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MEDIA_PLAYBACK`, `FOREGROUND_SERVICE_SPECIAL_USE`, and `POST_NOTIFICATIONS` in this task. Grant notification permission to the test probe. Use `START_NOT_STICKY`; add no boot receiver.

  Special-use explanation: `User-enabled live timeline speech: wait for assistant messages and read them aloud while the screen is off.`

- [ ] **Run GREEN and the probe.** Verify both exports on the pinned engine versions. Leave the probe silently active beyond 10 minutes on API 35; confirm no Media3 idle service policy controls it. Record success or failure. If Gecko cannot export the correct cookies or this service cannot retain its justified lifetime, stop this implementation path and revise the plan before product integration. Do not substitute a page relay or silent audio.
- [ ] **Checkpoint:** `feat(android): add authenticated native tts boundary`. Include only the listed files and tests. The bridge must still advertise no native TTS capability.

## Task 2: Expose the existing speech pipeline without changing browser behavior

**Files:**
- Create: `src/twicc/tts/service.py`.
- Modify: `src/twicc/tts/views.py`, `src/twicc/summaries/service.py`.
- Test: `tests/test_tts_service.py` (new), existing `tests/test_tts_views.py`, `tests/test_tts_script_audio.py`, `tests/test_summary_service.py`.

**Interfaces:**
- Produces: `async open_speech(data: dict) -> OpenedSpeech`.
- `OpenedSpeech` is a `NamedTuple(audio_id: str, content_type: str)`.
- `SpeechError` carries `status: int`, `message: str`, `extra: dict`.
- Produces: `async retain_summary_for_speech(reference: dict) -> SummaryReader`, using current reader ownership.
- Consumes: existing parsing, cache, scripts, adaptation, stream registry, summary snapshots and saved result hashes.

**Anchor test:** `test_saved_summary_hash_mismatch_preserves_http_error`.

```python
assert error.status == 409
assert error.extra == {"summary_changed": True, "saved_id": saved_id}
```

- [ ] **Write failing service tests.** Open ordinary text, script MP3/WAV/PCM, saved summary, and running summary through the callable API. Assert identical IDs/content types, cache options, test telemetry, announcement fields, and errors to existing HTTP behavior. Cancel while adapting or awaiting a later fragment; assert tasks/readers close. Saved hash mismatch preserves `409`, `summary_changed`, and saved ID.
- [ ] **Run RED:** `uv run pytest tests/test_tts_service.py tests/test_tts_views.py tests/test_summary_service.py -q`. Expect the callable API to be absent.
- [ ] **Extract `open_speech`.** Move only the preparation currently inside `tts_speech`, `_open_speech`, and `_open_script_speech`. Return data or typed errors instead of `JsonResponse`. Keep existing HTTP status/body/header behavior in thin view wrappers. Retain one stream and existing `tts_audio`/ending/release code. Keep `424` generation failures distinct from instance `401`.
- [ ] **Run GREEN:** repeat the RED command, then `uv run pytest tests/test_tts_streams.py tests/test_tts_part_audio.py tests/test_tts_script_audio.py -q`. Expect all tests to pass; no provider network calls.
- [ ] **Checkpoint:** `refactor(tts): expose shared speech preparation`.

## Task 3: Add authenticated native sessions, ordering, resume, and resource expiry

**Files:**
- Create: `src/twicc/tts/{native_protocol.py,native_sessions.py,native_consumer.py,native_views.py}`.
- Modify: `src/twicc/asgi.py`, `src/twicc/urls.py`, `src/twicc/cli/run.py`.
- Test: `tests/test_native_tts_protocol.py`, `tests/test_native_tts_sessions.py`, `tests/test_native_tts_transport.py` (new).

**Interfaces:**
- Consumes: Task 2 `open_speech`, `OpenedSpeech`, `SpeechError`; existing HTTP stream views and auth helpers.
- Produces: `NativeSession.apply(command: dict) -> None` (async), `snapshot() -> dict`, `close() -> None` (async).
- Produces: `NativeSessionRegistry.open(client_id: str, auth_binding: tuple) -> NativeSession`, `resume(session_id: str, secret: str, auth_binding: tuple) -> NativeSession`.
- Registry `shutdown()` is async. Auth binding comes from validated session identity/fingerprint, not client JSON.
- Produces: `parse_native_command(data: object) -> dict` in `native_protocol.py`; reject invalid frames before session mutation.
- Produces: `NativeTtsConsumer(AsyncJsonWebsocketConsumer)` in `native_consumer.py`.
- Timeline slots initially remain empty; Task 4 supplies the coordinator.

**Anchor test:** `test_resume_resends_unclaimed_resource_without_regeneration`.

```python
assert resumed_audio["audio_id"] == first_audio["audio_id"]
assert open_speech_mock.await_count == 1
assert old_audio_id not in other_session_owned_audio
```

- [ ] **Write failing protocol/session tests.** Validate enums, identifiers, required fields, sizes, and sequence ordering. Assert duplicate `manual` opens once; superseding generation releases late audio. Replay offline select→skip and stop-all before any candidate. Assert session A cannot resume, finish, or release B's resource. At 119 seconds disconnected resources remain; at 120 seconds they close. Unknown resume returns `session_lost` without a fresh session. Drop the `audio` frame after opening, then resume: receive the same audio ID with one preparation. Drop a `started` acknowledgement: resume must not restart playback or repeat context updates. Expiry before claim fails that reading explicitly.
- [ ] **Write failing transport tests.** Exercise authenticated HTTP/WSS, missing cookie, changed password fingerprint, foreign Origin, remote no-password restrictions, wrong resume secret, ownership failures, and ordinary `/ws/` compatibility. Invalidate the Django session after WS establishment; assert a fresh-storage heartbeat/control check closes the native session. Cached session fields must not authorize it. Heartbeat and shutdown release all resources. Ensure a client cannot replace auth binding through command fields.
- [ ] **Run RED:** `uv run pytest tests/test_native_tts_protocol.py tests/test_native_tts_sessions.py tests/test_native_tts_transport.py -q`.
- [ ] **Implement the shared contracts.** One registry in the existing ASGI process owns sessions and disconnect expiry timers. Serialize state transitions per session, but run cancellable speech preparation outside that lock. Mark cancellation before awaiting cleanup. Process resume commands before exposing resumed state/candidates. Use command sequence acknowledgement and reading/generation matching. Resume reconciles current playback and re-sends an unreceived resource reference without restarting generation or an existing player. Protect native audio wrappers with auth plus ownership; delegate existing streaming/ending semantics. Add a small `GET` capability view. Call registry shutdown from the existing server cleanup in `cli/run.py`; do not add a second lifespan owner.
- [ ] **Run GREEN:** repeat RED. Run `uv run pytest tests/test_tts_views.py tests/test_timeline_transports.py -q`. Expect no auth regression or new durable state.
- [ ] **Checkpoint:** `feat(tts): add independent native reading sessions`.

## Task 4: Move Android automatic coordination to the backend

**Files:**
- Create: `src/twicc/tts/native_timeline.py`, `tests/test_native_tts_timeline.py`.
- Modify: `src/twicc/tts/native_sessions.py`, `src/twicc/tts/native_consumer.py`, `src/twicc/core/services/timeline_feed.py`.
- Create: `tests/fixtures/native_tts_timeline_cases.json`; modify `frontend/src/stores/timelineSpeech.test.js` to exercise common policy cases.

**Interfaces:**
- Produces: `TimelineListener(session, *, clock)` with async `enable(activation: dict)`, `set_muted(value: bool)`, `refresh()`, `disable()`, `select(event_id: int, reading_id: str, generation: int)`.
- Produces: `started(reading_id: str)` and async `finished(reading_id: str, outcome: str)` for actual playback feedback.
- Consumes: existing `fetch_feed(FeedRequest)`, `project_scope_ids`, workspace membership, saved message snapshots, TTS configs, synced summary settings, summary service, Task 3 session delivery.

**Anchor test:** `test_disconnection_catchup_never_replays_truncated_history`.

```python
assert state["listening"] is None
assert state["error"]["code"] == "catchup_incomplete"
assert played_event_ids == []
```

- [ ] **Write failing selection tests.** Use frozen clocks and events. Cover global/workspace/project/worktree; strictly after click; arrival during initialization; late delivery inside/outside five seconds; equal dates sorted by ID; dedup; stable selected event; hidden then shown session; muted value changes; empty scope; membership changes with no historical replay. Compare shared deterministic ordering/boundary fixtures against existing Web coordinator behavior.
- [ ] **Write failing lifecycle tests.** Manual preemption discards selected auto only. Disable preserves unrelated manual. A selected policy stays frozen while pending settings update. Keep two unsaved summary preparations maximum. Saved summaries do not consume these slots. Announcement context changes on `started`, and manual audio invalidates it. Test message failures continue, unavailable configuration stops, and scope deletion stops.
- [ ] **Write failing catch-up tests.** Preserve boundary, cursor, seen IDs, pending data, and retained include-muted on a short disconnect. Respect existing `AFTER_ID_CAP = 500`, including rows excluded by display filtering. A truncated result stops with `catchup_incomplete`; never reload historical speech. Backend restart returns session loss. Switching devices does not share ownership.
- [ ] **Run RED:** `uv run pytest tests/test_native_tts_timeline.py -q`; `cd frontend && node --test src/stores/timelineSpeech.test.js`.
- [ ] **Implement `TimelineListener`.** Resolve named scopes on the server using `src/twicc/projects.py` and `src/twicc/workspaces.py`; do not freeze a frontend project-ID array. Use the existing `updates` group as an invalidation source. Ignore unrelated payloads. Reconcile on timeline, project, workspace, session visibility, TTS-config, and synced-settings changes. Use the existing feed's source snapshot and visibility rules. Add a bounded click-boundary bootstrap helper in `timeline_feed.py` where necessary; capture its high-water ID before asynchronous preparation. Inspect events after the click even when they arrive before WS/session initialization. New project members get a current membership boundary. Never equate an empty named scope to the global scope.
- [ ] **Implement bounded preparation and offers.** Backend owns pending events and summary references. Offer only the next eligible event. Native selection consumes it once; only then open its speech. Open no queued audio in advance. Freeze config/summary/announcement policy at selection. Release dropped/disabled preparations. Update spoken context only on actual start feedback. Reuse existing summary readers, saved hashes, availability checks, and `timelineSummaryEnabled`.
- [ ] **Run GREEN:** repeat RED; run `uv run pytest tests/test_timeline_api.py tests/test_timeline_muted_sessions.py tests/test_summary_service.py -q`. Expect the baseline rules and native command differences to pass.
- [ ] **Checkpoint:** `feat(tts): coordinate native timeline speech on the server`.

## Task 5: Implement the common native state machine and network client

**Files:**
- Create: policy `tts/{TtsState.kt,TtsProtocol.kt}`, tests `android/policy/src/test/kotlin/com/twidi/twicc/tts/TtsStateTest.kt`.
- Complete: `android/app/src/main/java/com/twidi/twicc/tts/TtsConnection.kt`.
- Create: `android/app/src/test/java/com/twidi/twicc/tts/TtsConnectionTest.kt`.
- Reuse: OkHttp 4.12.0 and MockWebServer 4.12.0 from Task 1; no second client or dependency version.

**Interfaces:**
- Produces: `TtsController.dispatch(command: TtsCommand): List<TtsEffect>` and `snapshot(): NativeTtsSnapshot` in the pure policy module.
- `TtsCommand` covers manual/listen/disable/skip/stop-all/server frame/player feedback/instance switch/authorization loss.
- `TtsEffect` covers foreground requirement, connect/send, prepare/play/cancel/release, notification update, and service stop.
- Produces: `TtsConnection.connect(credentials, resume)`, `send(frame)`, `openAudio(resource)`, `readEnding(resource)`, `release(resource)`, `close()`.
- Network results return onto the service's single state owner. Use OkHttp's WS and HTTP; keep engine APIs out of this file.

**Anchor test:** `offlineStopAllCannotBeReversedByOldAudio`.

```kotlin
assertNull(snapshot.listening)
assertEquals("idle", snapshot.playback.status)
assertEquals(0, effectsThatStartAudio.size)
```

- [ ] **Write failing pure tests.** Reserve manual generation before cancelling automatic work. Test manual→manual, automatic→manual, toggle replacement, disable during manual, local offline Skip/Stop all, old socket callbacks, stale document commands, instance switch, and settling-before-idle. Assert one output and monotonically advancing state revisions. Stop-all on an idle listener still closes it. A stale page's message stop cannot stop a different reading.
- [ ] **Run RED:** `cd android && ./gradlew :policy:test`.
- [ ] **Implement state and protocol values.** Keep all live state in process memory. Retain unacknowledged cancellation commands and resend unchanged sequences. Native current-reading generation always wins over late server replies. Apply scoped manual stop only when its reading ID/identity still matches. Do not persist an activation or resurrect it after process death.
- [ ] **Write and run network RED:** `cd android && ./gradlew :app:testWebviewDebugUnitTest`. MockWebServer tests prove cookie path/domain/expiry matching, WS authentication, same-origin URLs, redirect refusal, heartbeat, backoff reset, and resume-before-candidates. Explicitly reject HTML tunnel challenges. No request retries an audio stream from byte zero after playback starts.
- [ ] **Implement `TtsConnection` and run GREEN.** Verify hostname/certificate normally. Use the Task 1 jar. Set streamed-audio read timeout to zero so a generation gap is not a timeout; retain cancel, heartbeat, and connection-failure handling. Do not enable OkHttp automatic audio-body retry. Repeat policy and app unit commands for both flavors.
- [ ] **Checkpoint:** `feat(android): add shared native tts control and transport`.

## Task 6: Complete the foreground service, notification, and battery behavior

**Files:**
- Complete: common `tts/TtsService.kt`; create `tts/TtsNotification.kt`.
- Modify: `AndroidAppBridge.kt`, `BrowserActivity.kt`, `MainActivity.kt`, manifest, `android/app/src/main/res/values/strings.xml`.
- Test: extend `TtsPlatformBoundaryTest.kt`; create `android/app/src/test/java/com/twidi/twicc/tts/TtsServicePolicyTest.kt`.

**Interfaces:**
- Consumes: Task 5 controller/effects/connection; Task 1 exported credentials; Task 7 player through a fake until available.
- Produces: service-local binder `dispatch(command): Acceptance`, `snapshot(): NativeTtsSnapshot`.
- `Acceptance` contains `accepted`, operation ID, or an actionable error. It excludes generation/network completion.
- Produces: notification actions `com.twidi.twicc.tts.SKIP` and `.STOP_ALL`, immutable explicit PendingIntents bound to the current native session.

**Anchor test:** `blockedNotificationChannelRejectsActivation`.

```kotlin
assertFalse(acceptance.accepted)
assertEquals(0, openedNativeSessions)
assertFalse(wakeLock.isHeld)
```

- [ ] **Write failing service tests.** Start on user action before authentication export/network. Assert notification appears within Android's foreground deadline. Loading, buffering, settling, listening, and reconnecting retain it. Manual completion without listening removes it only after cleanup. Task dismissal and Activity destruction leave it running; explicit stop does not restart. Old notification intents cannot start or control a new instance session.
- [ ] **Run RED:** `cd android && ./gradlew :app:testWebviewDebugUnitTest`; run the targeted connected boundary test on API 35.
- [ ] **Implement service effects and notification.** Use the plain `Service` from Task 1, not `MediaSessionService` automatic foreground management. Keep a low-importance `tts_playback` notification channel. Show instance and Preparing/Reading/Waiting/Reconnecting. Provide Skip only with current work, Stop all while needed, and reopen via `MainActivity` routing to the source instance. Expose no pause or seek. Non-exported service commands validate active session identity. Return `START_NOT_STICKY`. Bind/unbind never determines lifetime.
- [ ] **Implement platform conditions.** Keep Task 1 foreground/notification permissions and add `WAKE_LOCK`. Ask notification permission from the visible Activity on the first TTS activation on API 33+. Check app notification enablement and the `tts_playback` channel importance on every activation. If permission is denied or the channel is blocked, reject activation with a Settings action. Do not accept work without background-accessible Skip/Stop all. A rejected preflight starts no session. After a settings change, require a fresh visible user action. Recheck availability on service state updates and heartbeats; stop cleanly if controls become unavailable.
- [ ] **Implement screen-off support.** Check `PowerManager.isIgnoringBatteryOptimizations` before enabling continuous listening. When exemption is absent, show the reason and open `ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS` from the visible Activity. Do not claim activation succeeded while required conditions remain unmet. Manual TTS can start without this exemption; document weaker long-wait guarantees. Hold a partial wake lock while preparing, buffering, playing, or settling. While continuous listening is enabled, retain it for the direct real-time socket requirement, including silent waits. Release it at Stop all/session failure. Do not keep the display on or add Wi-Fi locks without measured need.
- [ ] **Run GREEN:** run both flavor unit/connected tests. Test permission denied, channel blocked, exemption denied, notification stop during outage, recent-task dismissal, and `adb shell am force-stop com.twidi.twicc`. Expect no automatic resurrection or leaked wake lock. Special-use is a declared user function, not a claim of Play Store approval.
- [ ] **Checkpoint:** `feat(android): retain native tts with foreground controls`.

## Task 7: Read the full combined audio stream through one player

**Files:**
- Create: `android/app/src/main/java/com/twidi/twicc/tts/{TtsPlayer.kt,PcmExtractor.kt}`.
- Modify: service/player wiring and `android/app/build.gradle.kts` for common `androidx.media3:media3-exoplayer:1.9.0`.
- Test: `android/app/src/test/java/com/twidi/twicc/tts/PcmExtractorTest.kt` and `android/app/src/androidTest/java/com/twidi/twicc/tts/TtsPlaybackTest.kt`.
- Create: small deterministic MP3/PCM/WAV-origin fixtures under `tests/fixtures/native_tts_audio/` and test-server support in `tests/native_tts_fixture_server.py`.

**Interfaces:**
- Produces: `TtsPlayer.play(resource: AudioResource, listener: PlaybackListener)`, `cancel(readingId: String)`, `close()`.
- `PlaybackListener` has `onStarted(readingId)`, `onEnded(readingId)`, `onFailed(readingId,error)`.
- `AudioResource` carries reading ID/generation, owned audio URL, content type, and ending/release references. Credentials stay in the data source.
- Consumes: Task 5 HTTP source and identity checks; common service thread owns transitions.

**Anchor test:** `threeDelayedFragmentsProduceOneCompleteReading`.

```kotlin
assertEquals(expectedFrameCount, renderedFrameCount)
assertEquals(1, startedCount)
assertEquals(1, completedCount)
assertEquals(1, releasedResourceCount)
```

- [ ] **Write failing extraction tests.** Raw PCM signed 16-bit little-endian, declared rate/channels, arbitrary odd byte chunks, aligned frame timestamps, final incomplete frame, cancellation, unsupported format. Assert sample count and timestamps from byte count, not network arrival time. WAV-script fixtures must traverse current backend header stripping and produce the same PCM samples.
- [ ] **Run RED:** `cd android && ./gradlew :app:testWebviewDebugUnitTest`.
- [ ] **Implement one ExoPlayer output.** Supply the owned authenticated data source. Use standard MP3 extraction and a small raw-PCM `Extractor` selected from response content type; no second player, synthetic WAV length, or buffering the whole message. `PcmExtractor` emits `audio/raw` with PCM-16 format. Preserve existing server MP3 header cleanup. Disable automatic seeking/restart and HTTP range-based retry after partial consumption.
- [ ] **Write failing playback tests.** Serve one audio ID with three generated fragments and long delayed gaps. Assert one logical play, all expected samples/duration, no premature end, no duplicate prefix, and one final release. Test generation success while buffered audio remains, failure after audible start, Skip mid-gap, unknown ending after backend loss, the existing completion truth table, and audio focus loss. No assertion equates `STATE_READY` to audible start.
- [ ] **Run playback RED:** targeted `TtsPlaybackTest` for each flavor on an emulator with audio output.
- [ ] **Wire start/completion and focus.** Use Media3 audio output advancement feedback for `started`. On player end, query existing ending status. Apply the existing `speechEndResult` truth table from `frontend/src/utils/speechOutcome.js`: backend failure fails; a drained player with backend still running fails; a fully drained player with missing/unknown ending can succeed. Backend-session loss separately disables listening. A player/network error never becomes success because generation completed. Never replay from the start. Always release the resource in settlement. Configure speech/media audio attributes and request focus through ExoPlayer. On non-duckable focus loss, cancel current reading and keep an enabled listener; on system suppression, do not label suppressed audio as playing. A permanent focus denial reports a reading failure. Notification Stop all remains usable.
- [ ] **Run GREEN:** unit tests, both flavor playback tests, and existing backend audio tests. Confirm WAV config support through its existing PCM normalization, not a new encoder.
- [ ] **Checkpoint:** `feat(android): stream complete tts readings natively`.

## Task 8: Route existing Vue controls to the native owner

**Files:**
- Create: `frontend/src/utils/nativeTtsClient.js`, `frontend/src/utils/nativeTtsClient.test.js`.
- Modify: `frontend/src/utils/androidAppClient.js`, `frontend/src/stores/{textToSpeech.js,timelineSpeech.js}`, `frontend/src/composables/useMessageSummary.js`.
- Modify: `TextToSpeechButton.vue`, summary descriptor call sites in the file map, `App.vue`, `router.js`, actual rail component.
- Modify: `AndroidAppBridge.kt` and both flavor bridge integrations only as needed for command size/ack handling.
- Test: existing `textToSpeech.test.js`, `timelineSpeech.test.js`, `timelineSpeech.integration.test.js`, `components/ui/textToSpeechButton.test.js`; create `frontend/src/stores/nativeTts.integration.test.js`.

**Interfaces:**
- Produces: `createNativeTtsClient({appClient, document, timers})` with `attach()`, `state()`, `manual(request)`, `listen(activation)`, `setMuted(value)`, `disable()`, `skip(readingId?)`, `stopAll()`, `detach()`.
- Consumes: bridge capability 1, Task 6 acceptance/snapshot, existing store status and speech outcome contracts.
- Extend summary UI context with `nativeSpeechRequest`, a plain snapshot. Keep existing `playbackReference` for ordinary Web playback.
- Extend `playSummary(reference, options)` with optional `options.nativeRequest`; native code must never await the Web reference promise.

**Anchor test:** `native_summary_survives_page_release_before_socket_ready`.

```javascript
assert.equal(nativeRequests[0].request.summary.source_hash, frozenDescriptor.source_hash)
assert.equal(webPlayerCalls.length, 0)
assert.deepEqual(await result.ended, { ok: true })
```

- [ ] **Write failing frontend tests.** Cover every entry point: message, selection, saved/pending summary, OpenRouter config test, script config test, and automatic timeline. Release a running summary page handle during delayed cookie export/socket startup; the backend still acquires the source and completes speech. Assert native reserve happens before any deferred summary/network wait. Assert native mode creates no HTMLAudio/AudioContext playback, no Web automatic coordinator, and no pagehide audio DELETE. Large selections above 16 KB remain intact. Accepted native failures never fall back to Web audio.
- [ ] **Write failing reattach tests.** Reload/recreate returns current identity, source, phase, scope, pending count, and error. Restore include-muted into the listening scope's filter entry before enabling its watcher. Explicit later filter changes send `set_muted`; unrelated filters/navigation do not. Old snapshot/operation responses do not regress state. Hidden-page polling stops but service audio continues. Message-level stop compares the current reading identity; rail stop is Skip; timeline disable preserves manual speech.
- [ ] **Write failing compatibility/logout tests.** Old app, old server, and ordinary browser retain existing foreground behavior and no native-background claim. Native session failure shows stopped/error rather than enabling a Web coordinator. Page reload and temporary login-route navigation do not stop healthy native work. Actual logout, instance switch, and authorization loss do stop it. Opening instance management alone does not.
- [ ] **Run RED:** `cd frontend && node --test src/utils/nativeTtsClient.test.js src/stores/nativeTts.integration.test.js`.
- [ ] **Implement the small owner adapter.** Select native mode only after both capability checks; cache negotiation before controls permit activation. Preserve current store actions, status identities, configuration snapshot, origin/announcement, and `{ok,ended}` results. Native snapshot polling is UI synchronization only. Map operation terminal results once, never toast the same failure each poll. Explicit logout invokes native Stop all before navigating away. Ordinary pagehide/dispose only detaches the mirror. Modify existing login cleanup so it cannot accidentally cancel the native owner on reload.
- [ ] **Transfer summaries independently.** `useMessageSummary` captures saved ID/hash or source+descriptor policy at the click. Pending requests always include source and frozen descriptor, even when a handle ID is available. Native accepts this plain descriptor immediately and backend owns preparation/readers. Do not invoke the lazy Web promise first. Keep summary-changed responses wired to the existing UI refresh. Preserve selection identity and source snapshots. Do not add a new summary engine or general frontend backend-selection framework.
- [ ] **Run GREEN:** repeat RED; `cd frontend && node --test src/stores/textToSpeech.test.js src/stores/timelineSpeech.test.js src/stores/timelineSpeech.integration.test.js src/components/ui/textToSpeechButton.test.js`; `cd frontend && npm run build`. Build is necessary for production bundle compatibility, not a server restart.
- [ ] **Checkpoint:** `feat(android): route existing tts controls to the native service`.

## Task 9: Verify the complete feature and document supported conditions

**Files:**
- Extend: `tests/test_native_tts_transport.py`, native playback/platform tests and fixture server.
- Modify: `android/build.sh`, `android/README.md`.
- Create: `docs/plans/2026-11-05-android-background-tts-validation.md` with actual evidence during implementation, not planned passes.
- Modify: shared version fields in `android/app/build.gradle.kts` only when preparing replacement APKs; inspect current version first.

**Interfaces:**
- Consumes: full protocol, shared service/player, both flavors, ordinary Web controls.
- Produces: installable APKs for both engines and a recorded acceptance matrix with device/API, battery settings, exact APK/version/hash, and actual results.

**Anchor test:** `screenOffArrivalAfterThirtySilentMinutes`.

```kotlin
assertTrue(silentWaitMillis >= 30 * 60 * 1000L)
assertEquals(1, newEventStartedCount)
assertFalse(activityWasReopened)
```

- [ ] **Write failing end-to-end scenarios.** Use the fixture server to enable listening, background immediately, suspend/close page execution, generate three slow fragments, disconnect, cancel offline, reconnect, and assert later eligible speech without duplicate audio. Include thirty-minute silent waiting and instance switch with delayed old replies. Verify service controls with no Activity. Add these scenarios to existing native instrumentation tests rather than creating a general automation framework.
- [ ] **Run RED:** execute those instrumented scenarios for the unfinished integration. Record the actual failing boundary, then repair only that integration. If earlier task tests already prove a scenario, reuse them instead of duplicating it.
- [ ] **Run focused automated verification:** `uv run pytest tests/test_native_tts_protocol.py tests/test_native_tts_sessions.py tests/test_native_tts_transport.py tests/test_native_tts_timeline.py tests/test_tts_service.py tests/test_tts_views.py tests/test_summary_service.py -q`; run the frontend GREEN commands from Task 8. Ensure no test touches real TwiCC provider homes or calls a live speech provider.
- [ ] **Build both variants:** update `android/build.sh` to include common policy and flavor app unit tests, then run `./android/build.sh webview` and `./android/build.sh gecko`. Expect tests, lint, assembly, and checksum collection to pass. Retain the same signing key and application ID. Install variants sequentially with `adb install -r`; never uninstall just to switch an engine.
- [ ] **Run the physical-phone matrix.** On each flavor, test manual immediately-backgrounded preparation; all audio formats; slow multi-fragment reading; reload; Activity recreation; recent-task dismissal; Skip and Stop all offline; manual priority; timeline disable during manual; actual logout; changed password; transient tunnel loss; backend restart; independent second device; headphone/audio-focus interruption; explicit Android service stop. Confirm notification controls work without page JS.
- [ ] **Run the screen-off gate.** On a physical phone, enable continuous listening with the documented battery exemption. Lock it, leave it silently waiting for at least 30 minutes, then create a new eligible event. Hear that event without opening the app. Repeat with a forced-Doze emulator test for diagnostics. Record actual wake-lock/notification/service state and device/OEM settings. A forced-Doze pass alone is not physical acceptance. Denied exemption must show the documented non-active outcome.
- [ ] **Update `android/README.md` and evidence.** Describe native TTS ownership, notification commands, battery setting, auth expiry, supported formats, two-build behavior, and explicit-stop/session-loss limits. Keep normal browser behavior clear. Replace stale claims that all app audio is web-managed. Do not claim universal OEM reliability or a guaranteed permanent socket. Mark any untested physical condition as untested; do not enable/advertise the capability as validated if a required gate fails.
- [ ] **Checkpoint:** `feat(android): verify background timeline speech`. Include exact test results and remaining device limits in the commit body. Do not add CHANGELOG entries.

## Plan verification and review record

Before implementation, review these concrete cross-task conditions:

- Every protocol type has a validator, producer, consumer, cancellation path, and test owner.
- No opened native stream or summary reader belongs to page lifetime.
- A server snapshot cannot overwrite a newer local cancellation or restart a lost session.
- Native selection, manual reserve, audible start, backend completion, and audible end remain distinct events.
- The native cookie/session path works after Activity destruction on both pinned engine builds.
- A service waiting silently has a justified foreground lifetime; media-player inactivity does not govern it.
- Every acceptance example in the spec maps to Tasks 1–9, including muted reattach and offline stop.

### Adversarial review results — 2026-11-05

Two independent internal reviewers inspect the plan in two rounds.
One reviews backend/frontend contracts and existing timeline/summary behavior.
The other reviews Android lifecycle, engine integration, credentials, playback, and platform documentation.

Round 1 identifies six consequential corrections:

1. Reconcile a lost audio reference and lost started acknowledgement without regeneration or replay.
2. Transfer reconstructible summary sources instead of relying on page-owned handles.
3. Reload session authorization from storage so logout invalidates an established native socket.
4. Require notification permission and an enabled channel before accepting native work.
5. Include the probe's transport and foreground permissions in Task 1, rather than later tasks.
6. Export actual WebView cookie metadata with a checked `GET_COOKIE_INFO` capability.

The revised plan adds test cases for every correction.
Both reviewers inspect it again in Round 2 and report no remaining consequential blocker.
The Android reviewer also confirms the declared multi-type foreground approach is consistent with the platform rules.
This is plan review, not a successful cookie-export probe, completed product implementation, or physical battery validation.

Author self-review checks spec coverage, task ordering, names, test ownership, and proportion.
It also fixes the actual rail path, existing server cleanup owner, completion truth table, and terminal-result polling race.

## Platform basis

The following sources establish API behavior, not a guarantee for every device or Play Store review:

- [Android foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types): `specialUse` declaration, permission, and manifest explanation.
- [Android background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start): establish foreground work from the user's visible action.
- [Media3 background playback](https://developer.android.com/media/media3/session/background-playback) and [MediaSessionService timeout](https://developer.android.com/reference/androidx/media3/session/MediaSessionService#setForegroundServiceTimeoutMs(long)): do not delegate silent lifetime to idle media-service policy.
- [Doze and battery exemptions](https://developer.android.com/training/monitoring-device-state/doze-standby): direct network delivery needs documented power conditions.
- [Mozilla cookies API](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/cookies/getAll): privileged permission and matching cookie store, separate from page JS. Verify availability against the pinned Gecko build in Task 1.
- [Media3 1.9.0](https://android-developers.googleblog.com/2025/12/media3-190-whats-new.html): use a pinned release, without an unrelated SDK upgrade.
