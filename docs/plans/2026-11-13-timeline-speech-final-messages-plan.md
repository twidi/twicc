# Final-message-only Timeline Speech Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a saved, default-on option that reads only the exact final message of an officially ended turn.

**Architecture:** Capture the selected message event ID in the existing final-publication path. Store its resolution as small metadata on the owning ending event. Existing Web and native readers resolve these IDs in batches and admit speech only after this proof exists.

**Tech Stack:** Django, SQLite, Channels, Vue 3/Pinia, existing summaries/TTS, Android Kotlin policy module.

**Spec:** [Timeline speech: final messages only](2026-11-13-timeline-speech-final-messages-spec.md). Read both documents before implementation.

Status: adversarial plan review complete. Awaiting approval of the metadata clarification below. Product code is not changed by this plan.

## Global Constraints

- “No new DB table, schema migration, compute-version bump, or historical timeline rebuild.”
- “Keep source preparation outside DB writer admission.”
- “No separate watcher, timer, durable speech delivery queue, or model call.”
- “Apply eligibility before requesting automatic summaries, adapting text, or generating audio.”
- “Store one global preference in the existing synced settings.” Default: on.
- Do not change classification prompts, candidate selection, completion state transitions, notifications, or timeline message text/timestamps.
- Stay on this shared checkout. Preserve unrelated files. English artifacts; all interface copy uses i18n.
- Do not restart servers, migrate, install dependencies, publish an APK, or change CHANGELOG during implementation without a separate request.
- Commit each finished task, with a Conventional Commit body and the actual model co-author trailer. Stage explicit task-owned paths only.

## Narrow spec clarification required before execution

The spec requires late associations, durable proof after decision cleanup, no new table, and unchanged historical events. A strictly immutable ending cannot receive a link discovered later. Adding another event kind requires a choices migration and expands the feed; keeping links only on decisions loses them at cleanup.

This plan therefore proposes one explicit exception: **resolve only the new `final_message` metadata of a newly recorded ending, once**. Its original fact, dates, identity, background-work snapshot, and other payload fields remain unchanged. No historical backfill or other event mutation is allowed. This is a plan proposal for approval, not an interpretation that permits silently rewriting events.

## Review Focus

1. A thinking-only last native block can select an already published earlier message; both event records may already exist. Task 1 must link that exact selection.
2. Final-only to ordinary mode while a final waits: Task 4/5 must retain its ending-based eligibility without replaying other old messages.
3. Another session speaks a newer answer before this final resolves: Task 4/5 must bypass the ordinary five-second message watermark.
4. Native backend session expires during a short outage: Task 5 must not replace the final activation boundary with the advanced ordinary checkpoint.
5. A resolution commits without a new timeline ID: Task 2/4/5 must wake waiting readers even when the normal `latest_id` does not advance.

## File map

| Area | Files and responsibility |
| --- | --- |
| Final selection | `src/twicc/core/services/timeline_messages.py`: return the already selected event ID, not a second selection. |
| Official ending | `src/twicc/agent/timeline_hooks.py`, `src/twicc/core/services/timeline_decisions.py`: initialize/resolve final metadata in live and recovered endings. |
| Event recorder | `src/twicc/core/timeline.py`, `src/twicc/core/services/session_timeline.py`: optional payload and guarded, one-time resolution through the recording service. |
| Read/notification | New `src/twicc/core/services/timeline_final_messages.py`; existing `timeline_signal.py`, `views.py`, `urls.py`, `frontend/src/composables/useWebSocket.js`. |
| Preference/control | Existing `synced_settings.py`, `cli/settings/_keys.py`, `frontend/src/constants.js`, `frontend/src/stores/settings.js`, `TimelineFilterBand.vue`, five locale catalogs and timeline-speech help files. |
| Web policy | `frontend/src/stores/timelineSpeech.js`: add final admission to the existing coordinator, not a second scheduler. |
| Native policy | `src/twicc/tts/native_timeline.py`, `native_protocol.py`; Android `policy/.../tts/TtsState.kt` and `TtsProtocol.kt`: preserve final obligations across session replacement. |
| Tests | Existing timeline, settings, Web speech and native speech suites; focused new backend association/read tests. |

## Shared contracts

### Ending metadata

Add optional `TurnEndedData.final_message: dict | None = None`. Increment only `core.timeline.SCHEMA_VERSION` for this payload extension; this is not `compute_version` and triggers no rebuild.

```text
null or absent                              legacy/unproven: never guess
{"state": "pending", "storage_incarnation": "...", "history_epoch": 0}
                                           newly confirmed normal ending, unresolved
{"state": "none", "storage_incarnation": "...", "history_epoch": 0}
                                           selection proves no readable final
{"state": "ready", "event_id": 123,
 "storage_incarnation": "...", "history_epoch": 0}
                                           exact selected assistant timeline event
```

Provider/session identity comes from the owning events. Pending retains the ending's captured source scope; scope members can remain null when existing source proof is not yet bound. Ready uses the existing proven final selection's scope. Do not add another source-binding protocol. A known replaced scope returns unavailable; an unknown pending scope cannot authorize speech. Initialize pending only for normal endings that use the existing final-publication obligation. Killed/error endings and legacy records remain unproven.

### Resolution result and read API

- `FinalMessageResult(NamedTuple)` in `timeline_messages.py`: `resolved: bool`, `event_id: int | None`. `False, None` means retry existing publication; `True, None` means no eligible final; `True, id` means ready.
- `_fulfill_final_message(ending) -> FinalMessageResult`. Keep public `fulfill_final_message_obligation(*, ending_id: str) -> bool` compatible with existing callers.
- Extend `record_event_sync(..., final_message_resolution: dict | None = None)`. Only an exact dedupe match for `turn.ended` with pending metadata can accept this optional resolution. With this argument, return the owning row on successful or identical terminal resolution; return `None` on failure/conflict/absent ending. Never create a row through this mode. Existing calls keep their current dedupe behavior. Fulfillment checks this result before clearing retry ownership.
- `resolve_final_messages(ending_ids: Sequence[int], *, project_ids: Sequence[str] = (), include_muted: bool = False) -> dict` in the new read service. Maximum 100 distinct positive IDs per batch. Empty scope means the existing global scope.
- `GET /api/timeline/final-messages/?ending_ids=1,2&projects=...&include_muted=1` calls this service under existing application authentication. No new authorization scheme.

Response shape: `{"items": [{"ending_id": 12, "state": "ready", "occurred_at": "...", "message": {serialized event}}], "sessions": {existing feed session table}}`. States are `pending`, `none`, `ready`, and `unavailable`. Non-ready entries have `message: null`. Unavailable entries also have `occurred_at: null` and expose no session metadata. Other entries retain the official ending time. Check event kind, same provider/session/project, saved scope, current session scope/visibility and requested project membership. Never read `SessionItem` or semantic decisions in this service.

### Notification and reader entries

Extend existing `timeline_changed` with optional `finalized_ending_ids: [int, ...]`. Collect/deduplicate them using the existing on-commit throttle. Rollback sends nothing. Resolution can emit this field even when `latest_id` is unchanged. No message content or session identity is broadcast.

Readers consume their intersection with known waiting ending IDs. They read a new ending once, refresh exact pending IDs on matching resolution signals, and reconcile pending IDs on reconnect/resume. Keep an in-flight batch dirty when a matching signal arrives; run one coalesced follow-up read so an older pending response cannot lose that signal. They do not refresh every old ending on an unrelated feed notification.

Keep ordinary message snapshots unchanged. A final queue entry wraps its message with `endingId` and `speechTime` (the official ending time). The message event ID remains the summary/manual/player identity. Separate feed observation from speech consumption. A final obligation retains this classification across preference changes.

## Task 1: Capture and store the authoritative final association

**Files:** Modify the first three backend areas in the file map. Test `tests/test_timeline_messages.py`, `tests/test_semantic_writer_boundaries.py`, and new `tests/test_timeline_final_messages.py`.

**Consumes:** Existing prepared source reads, accepted ending, canonical final-publication selection.

**Produces:** `FinalMessageResult`, populated ending metadata, unchanged public fulfillment boolean.

- [ ] **1. Write failing tests.** Add the following assertions using existing provider fixtures:
  - `test_final_link_uses_authoritative_selection`: published progress A, final B, official ending E; E links B for Claude and Codex.
  - `test_late_thinking_terminal_links_existing_message`: E initially waits; later source proof selects already published B. Its association becomes ready without creating another message or ending.
  - `test_empty_final_resolves_none`: no eligible text yields `state=none`, never a previous progress message.
  - `test_final_link_is_one_way`: pending → ready/none once; replay is a no-op; a conflicting target, another session, or changed source scope cannot retarget it. Other ending fields stay identical.
  - `test_final_link_survives_decision_cleanup`: delete settled decision rows after resolution; the ending still contains its exact proof.
- [ ] **2. Run the tests and observe the intended failures.** `cd /home/twidi/dev/twicc-poc && timeout -k 5 180 uv run pytest -q tests/test_timeline_final_messages.py tests/test_semantic_writer_boundaries.py`.
- [ ] **3. Extend `_fulfill_final_message` without changing its selection loop.** Convert each existing pending/none/success return to the result contract. Return IDs already obtained from publication or `_matching_event`. Do not rehydrate or reselect for TTS.
- [ ] **4. Add pending metadata to new normal live/recovered ending records.** In public fulfillment, resolve its exact ending event by the existing `(session_id, dedupe_key)` identity. For a new pending ending, commit its resolution before clearing `force_pending`; a storage failure preserves retry ownership. Legacy endings without this metadata, or intentionally absent timeline records, keep the existing publication/fulfillment behavior and remain unproven for final speech. Do not upgrade/backfill them. Test an upgrade with a legacy ending and `force_pending=True`: existing publication finishes and its obligation clears.
- [ ] **5. Implement the recording exception narrowly.** Only the named metadata can change, only pending → terminal, under existing writer admission. Resolve by known event identity; validate the exact owning ending and selected event/scope. Keep normal dedupe returns unchanged and use the opt-in result contract above. Do not add a general upsert/update API. Idempotent replay emits no update. Inject storage failure and assert a new ending's `force_pending` stays true.
- [ ] **6. Verify.** Run Task 1 tests plus `tests/test_timeline_service.py`, `tests/test_completion_integration.py`, `tests/test_timeline_decision_task.py`. Existing writer-blocking regressions must still pass. Assert at most one extra ending metadata UPDATE per completed resolution, no transcript reads added by link storage.
- [ ] **7. Commit.** `feat(tts): capture authoritative final message links`.

## Task 2: Expose batched final snapshots and resolution invalidations

**Files:** New read service; `timeline_signal.py`, `views.py`, `urls.py`, `frontend/src/composables/useWebSocket.js`. Tests: new `tests/test_timeline_final_messages_read.py` and `frontend/src/composables/websocketTimeline.test.js`, existing `tests/test_timeline_signal.py`.

**Consumes:** Task 1 metadata. **Produces:** shared read API and `finalized_ending_ids` transport.

- [ ] **1. Write failing read tests.** `test_resolve_batch_reads_saved_text_only` resolves 100 mixed ready/pending/none IDs with no `SessionItem`/decision reads. At most three SELECT statements per batch: endings, linked messages, current sessions; skip unneeded queries. Include a ready message older than its ending and outside the caller's ordinary feed page.
- [ ] **2. Write failure/visibility tests.** Invalid/duplicate IDs, wrong kind, mismatched scope/provider/session, hidden sessions, muted exclusion, deleted data, legacy metadata, and scope departure cannot expose or select another text. Include pending metadata after source replacement: a known obsolete scope returns unavailable without decision reads. Unknown pending scope never permits speech. An empty request performs no DB work. Query count is independent of transcript length and per-batch item count.
- [ ] **3. Write signal tests.** `test_final_resolution_signals_without_new_event_id`, rollback suppression, coalesced IDs, and replay/no-op. Existing signals without the added field keep their behavior.
- [ ] **4. Run the new tests red.** `cd /home/twidi/dev/twicc-poc && timeout -k 5 180 uv run pytest -q tests/test_timeline_final_messages_read.py tests/test_timeline_signal.py`.
- [ ] **5. Implement the read contract.** Reuse `visible_sessions_q`, `serialize_event`, existing scope expansion and error/i18n conventions. Fetch by PK batches; do not modify generic feed SQL, filters, pagination, or rendered events. Use an existing small view in `views.py`, not a new HTTP framework.
- [ ] **6. Extend the existing signal.** Add `finalized_ending_ids` to `timeline_signal.poke_on_commit`/`poke` as an optional sequence. Task 1 resolution requests it through the event recorder. Preserve all IDs collected during the existing 0.3-second throttle. Forward them through the existing `twicc:timeline-changed` browser event; do not rely on `notifyLatest` when the ID stays equal.
- [ ] **7. Run read/signal tests green.** Also run `cd /home/twidi/dev/twicc-poc/frontend && timeout -k 5 120 node --test src/composables/websocketTimeline.test.js src/utils/timeline/feed.test.js src/utils/timeline/liveEvents.test.js`. Assert unchanged-ID metadata reaches the reader and zero association queries for an ordinary display-only feed page. No global scan, schema/index change, or extra polling is introduced.
- [ ] **8. Commit.** `feat(tts): expose final message snapshots by ending id`.

## Task 3: Add the saved option and timeline control

**Files:** `src/twicc/synced_settings.py`, `src/twicc/cli/settings/_keys.py`, `frontend/src/constants.js`, `frontend/src/stores/settings.js`, `frontend/src/components/timeline/TimelineFilterBand.vue`; `frontend/src/i18n/locales/{en,fr,de,es,pt-BR}.json`; `frontend/public/help/{timeline-speech.md,fr/timeline-speech.md,de/timeline-speech.md,es/timeline-speech.md,pt-BR/timeline-speech.md}`.

**Consumes:** Existing generic settings sync. **Produces:** `timelineSpeechFinalOnly: boolean`, default `true`, available through the settings store/backend settings snapshot.

- [ ] **1. Write failing persistence/control tests.** Add `frontend/src/stores/settingsTimelineSpeech.test.js`; extend `TimelineFilterBand.test.js`, `tests/test_settings_cli.py` and `tests/test_synced_settings_ws.py`. Assert default-on, saved-off, local reload plus backend override, sync broadcast, accessible control, visible only for the active listening page including pause, hidden when stopped. Toggling invokes the existing settings action and does not restart listening.
- [ ] **2. Run those tests red.** Use `uv run pytest` with the Python files above and `cd /home/twidi/dev/twicc-poc/frontend && timeout -k 5 120 node --test src/stores/settingsTimelineSpeech.test.js src/components/timeline/TimelineFilterBand.test.js`.
- [ ] **3. Add the preference using the existing boolean setting pattern.** Update defaults, `SYNCED_SETTINGS_KEYS`, schema, validator, `setTimelineSpeechFinalOnly(value)`, outgoing settings union and CLI description. Readers treat an uninitialized setting as on (`!== false` / `is not False`). Do not add a separate storage file or per-page preference.
- [ ] **4. Add the switch below the heading row.** Use the existing Web Awesome switch pattern and explicit imports already registered in `main.js`. Copy must match the spec exactly for English/French, with translations in the other catalogs. Update the short timeline speech help to explain that visible progress can remain silent.
- [ ] **5. Run the focused tests green.** Run `npm run lint:i18n` and existing settings/control tests. The switch must not alter display filters, active listening scope, or manual controls.
- [ ] **6. Commit.** `feat(tts): add saved final messages only option`.

## Task 4: Gate Web speech on the final association

**Files:** `frontend/src/stores/timelineSpeech.js`, `frontend/src/stores/timelineSpeech.test.js`, `timelineSpeech.integration.test.js`, `frontend/src/stores/nativeTts.integration.test.js` if native-page test wiring needs the settings preference.

**Consumes:** Tasks 2–3. **Produces:** final queue entries, batched resolution, existing ordinary mode unchanged.

- [ ] **1. Write failing behavior tests.** Reuse the current speech harness; add final snapshots/ending events and count summary/speech calls. Verify both arrival orders, previously observed YES, NO then forced publication, Codex shell hold/accept, empty final, old-message/new-ending, cross-session ten-minute skew, and an older turn resolved after a newer start. Every case asserts the exact spoken message ID/text and count, not private collections.
- [ ] **2. Write change/replay tests.** Toggle on while progress waits, toggle off while a final waits, duplicate ending/ready signals, a signal during an in-flight pending read, a message already selected in ordinary mode, reconnect, changed source scope, hidden/muted/scope departure, stop, and long-outage reset. A selected reading finishes; an unselected progress preparation is released. Ordinary off-mode keeps its five-second tolerance and message-date order.
- [ ] **3. Run Web speech tests red.** `cd /home/twidi/dev/twicc-poc/frontend && timeout -k 5 180 node --test src/stores/timelineSpeech.test.js src/stores/timelineSpeech.integration.test.js`.
- [ ] **4. Add final admission inside the existing coordinator.** Track only unresolved/ready ending obligations and consumed message IDs for this activation. Do not retain every intermediate message text. Resolve new eligible ending IDs once, then exact pending intersections from signals; chunk requests by 100. Known legacy/none/unavailable results settle without substitution. Pending results wait for existing signals.
- [ ] **5. Preserve separate clocks.** Ordinary entries use original message time; final entries use ending time for activation eligibility and ordering. Do not apply the ordinary watermark to a confirmed final. Sort by speech time then owning ending/message ID as applicable. Keep the message object unchanged for summaries and playback identity. Update the ordinary watermark using only ordinary readings so final times do not change off-mode eligibility.
- [ ] **6. Reconcile preference changes before `prepare`.** Release only unselected ineligible progress work. Keep final obligations through either preference change. Deduplication must not confuse feed-seen IDs with consumed message IDs. Retain current activation tokens to fence async results on stop, scope replacement, and fresh recovery.
- [ ] **7. Handle resolution read failure without losing the obligation.** Surface the existing localized load failure and retain unresolved IDs for the existing reconnect/resume retry path. Do not mark them consumed, request audio, or add a retry timer. An obsolete HTTP result cannot revive a stopped activation.
- [ ] **8. Run Web tests green.** Preserve existing tests by explicitly choosing off-mode in fixtures that assert legacy eligibility; do not weaken their assertions. Add final-only cases to the existing shared native policy fixture only where both consumers can exercise the same inputs.
- [ ] **9. Commit.** `feat(tts): read confirmed final messages in web timeline`.

## Task 5: Apply the policy to native background listening and recovery

**Files:** `src/twicc/tts/native_timeline.py`, `native_protocol.py`; `tests/test_native_tts_timeline.py`, `tests/test_native_tts_protocol.py`; Android `android/policy/src/main/kotlin/com/twidi/twicc/tts/{TtsState.kt,TtsProtocol.kt}` and `android/policy/src/test/kotlin/com/twidi/twicc/tts/{TtsStateTest.kt,TtsRecoveryTest.kt}`.

**Consumes:** Tasks 1–3; Task 4 behavioral cases. **Produces:** identical admission with no visible page, plus lossless short native-session replacement for final obligations.

The existing Android checkpoint advances to five seconds behind the newest started message. It prunes older excluded IDs. Reusing it as the final activation boundary loses late finals. Keep ordinary recovery unchanged and add only the metadata needed for final recovery.

- [ ] **1. Write failing native parity tests.** Exercise the Task 4 final cases through `TimelineListener`, including settings broadcast while the page is absent, same-ID resolution notification, selected playback policy, summary gating, current visibility, and mixed ordinary/final ordering.
- [ ] **2. Write the short-expiry regression.** An ending E waits at time 10; another session speaks at 600; the backend native session is replaced during a short outage; E resolves afterwards. E's exact message is read once even though ordinary recovery starts near 595. Add a ready final already started before replacement and assert no replay. Also read A in ordinary mode, advance its ordinary checkpoint, switch on, replace the backend, then resolve A's final association: A stays consumed. Finally, switch off after ignored progress, replace the backend, and assert old progress stays silent while subsequent unread ordinary messages remain recoverable. After a long outage, neither old ending returns.
- [ ] **3. Run native Python and Android policy tests red.** Python: `cd /home/twidi/dev/twicc-poc && timeout -k 5 180 uv run pytest -q tests/test_native_tts_timeline.py tests/test_native_tts_protocol.py`. Android policy command, reused for green verification:

  ```bash
  cd /home/twidi/dev/twicc-poc/android && JAVA_HOME=/home/twidi/.local/share/twicc-android/jdk/jdk-17.0.21+9 GRADLE_USER_HOME=/home/twidi/.local/share/twicc-android/gradle timeout -k 5 300 ./gradlew --configure-on-demand :policy:test
  ```

  Do not assemble/publish an APK for this test step.
- [ ] **4. Extend the listener in place.** Read `timelineSpeechFinalOnly` through `_read_policy`. Reuse Task 2's service directly, not loopback HTTP. Mirror the Web obligation/consumption rules, using pending wrappers without changing message snapshots. Match ready invalidation IDs; batch at 100. Recheck preference and visibility before summaries/selection, without retargeting a selected reading.
- [ ] **5. Add explicit recovery metadata to protocol version 1.** Optional `listen` fields: `final_boundary` (canonical UTC timestamp), `ending_cursor` and `ordinary_after_id` (nonnegative timeline event IDs), and `pending_final_ending_ids` (positive event ID array). Reuse `excluded_event_ids` for consumed message IDs across both modes. Listening snapshots expose these values and all unresolved or ready-but-unconsumed final ending IDs together. Candidate frames carry optional `final_ending_id` and ending time for final readings; `event_id` still identifies the assistant message. Validate added fields and keep legacy commands parseable. This needs an updated Android app for complete new recovery behavior; no bridge playback command is replaced.
- [ ] **6. Preserve final recovery state in `TtsState`.** Keep the final boundary independent of the ordinary checkpoint. Retain consumed message IDs across both modes for this activation, following existing selection/discard rules; feed observation never consumes. Do not prune them with the five-second ordinary window. Keep existing `checkpointEvents` only for ordinary checkpoint calculation; final readings do not advance it. Retain cursor, cutoff, and outstanding ending IDs from the same owned backend snapshot, including across session expiry. Clear this state on fresh activation, explicit stop/scope replacement, or long-outage reset. Existing pause/resume starts after the Resume click; reset both boundaries and final backlog accordingly. Ordinary off-mode checkpointing stays unchanged.
- [ ] **7. Preserve the ordinary admission cutoff.** Default `ordinary_after_id` to zero. Advance it to the processed feed cursor while final-only is on; freeze it while ordinary is on. Persist it with the owned snapshot, including when the setting changes during backend absence. Ordinary recovery keeps its existing time checkpoint but excludes messages at or below this cutoff. Final obligations ignore it. Subsequent ordinary messages not yet consumed remain recoverable. Never advance beyond a successfully processed range.
- [ ] **8. Recover only necessary final metadata after session replacement.** Restore known outstanding IDs directly. When final-only is on, use existing `turns` filter/keyset facilities after `ending_cursor` to recover missed endings; never rescan the whole activation. Advance the cursor only after processing the captured range and retaining its outstanding obligations in the same snapshot. An initial activation or older client without a cursor uses its activation-boundary bootstrap once, then publishes its cursor. Fetch failure preserves the cursor and outstanding IDs. Resolve linked text only for eligible, unconsumed ready endings. Ordinary catch-up keeps its existing boundary. With final-only off, restore known outstanding obligations but do not retrospectively admit new final obligations. No new periodic task.
- [ ] **9. Run Python/Android policy tests green.** Test the wire round trip, expired backend while the page stays closed, stale snapshot ownership, short/long pause, settings mode change, and excluded-ID persistence. Keep fixtures for ordinary behavior explicitly off. Do not skip native-session replacement coverage because ordinary reconnect already passes.
- [ ] **10. Commit.** `feat(tts): preserve final speech policy in native recovery`.

## Task 6: Verify the integration and resource bounds

**Files:** Extend focused tests only where a cross-component assertion is missing. No new benchmarking subsystem or copied production transcript corpus.

- [ ] **1. Add one end-to-end backend fixture per provider.** Use the existing completion manager + real writer + saved timeline fixtures: intermediate message, pending shell classification, NO, final publication, then accepted official ending. Assert only the authoritative final can be read. Include both new-message and already-published-final links.
- [ ] **2. Prove DB bounds with instrumentation.** A 100-ending batch uses at most three SELECTs and no transcript/decision reads. A repeated no-change signal causes no resolution fetch. Unrelated ready IDs cause no waiting-link query. Repeated native replacement reads only metadata after its acknowledged cursor plus outstanding IDs. Pause source preparation and prove watcher ingestion still completes. Resolving metadata performs one guarded ending UPDATE and does not re-open a source search.
- [ ] **3. Run focused regression tests.** `cd /home/twidi/dev/twicc-poc && timeout -k 5 300 uv run pytest -q tests/test_timeline_final_messages.py tests/test_timeline_final_messages_read.py tests/test_semantic_writer_boundaries.py tests/test_completion_integration.py tests/test_timeline_decision_task.py tests/test_native_tts_timeline.py tests/test_native_tts_protocol.py tests/test_settings_cli.py tests/test_synced_settings_ws.py`.
- [ ] **4. Run `cd /home/twidi/dev/twicc-poc/frontend && timeout -k 5 300 npm test`.** Run existing Android policy tests, `npm run lint:i18n`, and `uvx ruff check` on changed Python files. Confirm no model/SDK change requires a live hermetic model diagnostic.
- [ ] **5. Check model state without writing migrations.** Run `cd /home/twidi/dev/twicc-poc && timeout -k 5 120 uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test`; expected no changes. No `migrate`, production data command, or compute rebuild. Run `cd /home/twidi/dev/twicc-poc && git diff --check`.
- [ ] **6. Product review on already-running servers if available.** Verify switch position and saved setting, silence for progress, exact final after delayed confirmation, and manual reading. Do not restart servers or build an APK without the user's explicit request. Report unavailable live Web/Android checks separately from automated results.
- [ ] **7. Commit task-owned verification changes.** `test(tts): cover final speech ordering and database bounds`. Skip an empty commit if previous tasks already contain these tests.

## Scope exclusions and handoff

No classifier prompt changes, new timeline rows, new storage table/index, general event mutation API, generic scheduler abstraction, cross-tab speech coordination, model benchmark, or new retry loop.

The implementer must not work around a missing proof with chronology or another history search. Stop and report a required change to the spec/contract instead.

Before implementation, obtain approval for the one-time metadata clarification above. Then execute the tasks in dependency order. Final delivery reports commits, actual tests, any unperformed live checks, required backend restart, and the native APK update required for full recovery support.

## Plan review record

Two independent adversarial reviewers checked behavior and resource costs. Corrections cover legacy fulfillment, storage failure/replay, cross-mode deduplication, incremental native recovery, and skipped-progress replay after mode changes. Both reviewers report no remaining important blocker after re-review. The explicit metadata exception still needs approval. Reviews inspect the plan and existing code; they do not substitute for implementation tests.
