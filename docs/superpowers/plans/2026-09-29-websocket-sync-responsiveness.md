# WebSocket Responsiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep WebSocket transport responsive during session synchronization while preserving exact historical relationships and metadata.

**Architecture:** Use one dedicated heavy-work executor and an authenticated heartbeat receive pump. Build compact history facts through normal provider compute versions. Process live backlog through bounded transactions and a fair watcher queue.

**Tech Stack:** Python 3.13+, Django 6, Channels, ASGI, SQLite WAL, asyncio, orjson, VueUse, pytest, node:test.

**Spec:** [Approved design](../specs/2026-09-29-websocket-sync-responsiveness-design.md). Read its [review corrections](../specs/2026-09-29-websocket-sync-responsiveness-review.md) before implementation.

**Status:** Implementation in progress with Subagent-Driven Development, authorized by the user on 2026-09-29. Task completion is tracked below; each task requires an independent review.
**Baseline:** `07da2743` on local `main`. Recheck source changes before each task.
**Adversarial review:** See [findings and rechecks](2026-09-29-websocket-sync-responsiveness-review.md). Corrections below form part of the plan.

## Global Constraints

- Historical reconstruction uses normal provider compute versions only. No independent builder, readiness field, or index generation.
- Keep SQLite, the single backend process, and existing provider behavior.
- Keep heartbeat interval 30,000 ms, pong timeout 5,000 ms, and reconnect delay 1,000 ms unchanged.
- Heavy executor: `max_workers=1`; retain existing writer admission, nested leases, shielding, and shutdown drainage.
- Heartbeat fast path: at most 1 KiB of UTF-8 text; FIFO: 32 events and 16 MiB, plus one pending upstream event.
- Allow one oversized FIFO event only when empty, within the upstream server's existing frame limit.
- Live slices: initially 500 nonempty records and 4 MiB; target 100 ms, with no cutoff inside prepared two-pass work.
- Preserve exact prior-line semantics, identifier reuse, the 30-second late-announcement rule, and batch orphan matching's combined latest-50 limit.
- Stay on the existing branch. No new branch or worktree without the user's explicit request.
- Preserve unrelated changes. Do not restart servers, migrate the running instance, or install dependencies without a user request.
- English code, comments, tests, and documents. French live communication.
- Use `orjson`, immutable `NamedTuple` records, and the existing provider architecture.
- No new dependencies. Use installed pytest extras and existing frontend test tools.
- Commands below assume `/home/twidi/dev/twicc-poc`. If the user later requests a worktree, prefix commands with its `cd` and use `TWICC_DATA_DIR=$PWD` for Django.

## Review Focus

1. A disconnected client fails a send while business work holds a writer lease: no lost admitted write or surviving reader task. Tasks 1–2.
2. Reused identifiers occur inside one inserted batch or an outdated partially indexed session: never select future or older evidence. Tasks 3–6.
3. A rollback follows one-shot diff enrichment while a new cache entry arrives: preserve both retry evidence and the replacement. Task 7.
4. A file is rewritten, deleted, or appended while its path is queued: preserve provider exclusion and completion semantics. Tasks 8–9.
5. An unfinished UTF-8/JSONL tail receives no further filesystem event: no offset corruption or busy loop. Tasks 8–9.

---

## File responsibilities and delivery order

| Unit | New files | Existing integration points |
| --- | --- | --- |
| Heavy executor | `src/twicc/providers/compute_executor.py` | `providers/db_writer.py`, `providers/sessions_watcher.py` |
| Heartbeat transport | `src/twicc/websocket_transport.py` | `asgi.py`, frontend heartbeat regression tests |
| Facts storage and querying | `src/twicc/providers/history_facts.py` | `core/models.py`, new schema migration |
| Provider fact extraction | `src/twicc/providers/claude_code/history_facts.py`, `src/twicc/providers/codex/history_facts.py` | Each provider's `compute.py` |
| Compute publication and lookup | No additional subsystem | `providers/compute_base.py`, provider compute, `settings.py`, Codex rollout replacement |
| Enrichment ownership | `src/twicc/providers/enrichment_cache.py` | Both original-file cache modules and their compute consumers |
| Live record boundaries | `src/twicc/providers/live_sync.py` | `compute_base.py` and its callers |
| Bounded aggregate maintenance | `src/twicc/providers/live_aggregates.py` | `compute_base.py`, `core/models.py`, activity helpers, schema indexes |
| Fair path scheduling | `src/twicc/providers/session_change_queue.py` | Base and Codex watchers |
| Performance reproduction | `scripts/benchmark_session_sync.py` | Opt-in disposable database workload |

Paths without `src/twicc/` in the integration column are relative to that package.
Keep large existing modules intact except for the specific integration points above.

Delivery order: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10**.
Tasks 1–2 protect transport. Tasks 3–6 publish and use facts. Tasks 7–9 make live slices safe and fair.
Do not deploy intermediate facts commits as a completed indexed implementation; task 6 activates authoritative reads and version bumps together.

## Verification and commit conventions

Run each task's focused tests before its commit. Tests use isolated pytest databases.
Async database tests requiring multiple connections use `pytest.mark.django_db(transaction=True)` and the existing asyncio test style.
Use latches/events to prove ordering. Timeouts only bound a failed test; they do not assert machine-specific performance.

Every task ends with `git diff --check`, an explicit staged-path review, and a Conventional Commit.
Include a descriptive body and the exact running model in the required Codex co-author trailer.
Do not use `git add .`. Commit only the task's reviewed paths.
The commit subjects below are suggestions; bodies explain behavior and validation.

### Task 1: Isolate heavy synchronous database work

**Files:** Create `src/twicc/providers/compute_executor.py` and `tests/test_compute_executor.py`.
Modify `src/twicc/providers/db_writer.py`, `src/twicc/providers/sessions_watcher.py`, and `tests/test_db_write_lock_reentrance.py`.

**Interfaces:**
- `start_compute_executor() -> None`: create one worker; reject duplicate start consistently with DB writer startup.
- `async stop_compute_executor() -> None`: close worker-owned connections and terminate after admitted work drains.
- `async run_compute_sync[T](function: Callable[..., T], /, *args, **kwargs) -> T`: database adapter using the dedicated executor.
- Keep `async _flush_pending_activities(provider: Provider, pending_activity_days: dict[str, set]) -> None` as an adapter in task 1. Move its synchronous transaction to `_flush_pending_activities_sync` and call it through `run_compute_sync`; remove its shared-executor decorator.
- This helper does not acquire a writer lease. Its callers retain existing `run_under_db_write_lock` ownership.
- Preserve public `start_db_writer()` and `stop_db_writer()` behavior, including cancellation after completed shutdown.

- [x] **Write failing concurrency tests.** `test_heavy_work_does_not_block_channels_cleanup` holds a worker latch and completes shared-executor cleanup before release. `test_finalize_activity_flush_does_not_block_channels_cleanup` invokes the real compute finalization path and blocks its activity flush, then completes Channels cleanup before release. Cover threshold, successful finalization, and abandoned-run flush adapters. `test_cancelled_write_keeps_lease_until_thread_finishes` proves a second writer cannot enter early. `test_stop_drains_closes_worker_connection_and_allows_restart` verifies actual worker termination and fresh lifecycle state.
- [x] **Run the tests.** `uv run pytest tests/test_compute_executor.py tests/test_db_write_lock_reentrance.py -q`. New isolation tests fail against the shared executor.
- [x] **Implement lifecycle and adapter.** Use `database_sync_to_async(..., thread_sensitive=False, executor=...)`. Shut down after the existing external-holder drain and before clearing writer lifecycle state. Close connections on their owning worker; never block the event loop waiting for active jobs.
- [x] **Route heavy boundaries.** Replace adapters in `_apply_compute_items_in_chunks`, final `apply_session_complete`, `_settle_async_job`, synchronous `_apply_*_payload` calls in `_process_thread_message`, watcher live compute, and `_flush_pending_activities`. The latter runs from the compute-result batch threshold, `_finalize_compute_run`, and `_finalize_abandoned_run`; all three paths must use the dedicated worker while retaining their writer admission and shutdown drainage. Keep database writes inside their existing admitted factories. Inspect provider `try_handle_async_job` implementations and route their heavy application callbacks through the same helper. Audit decorator-style adapters as well as call-site wrappers; do not leave full activity aggregation on the shared executor. Keep lightweight authentication and ordinary reads outside it.
- [x] **Keep thread ownership explicit.** Load live Session objects inside the worker from IDs. Return fully loaded scalar fields/results; refresh watcher state after completion. Do not carry deferred ORM attributes or lazy QuerySets into another thread. Audit mutable provider caches accessed from the event loop; guard compound access where necessary. Task 7 adds transactional enrichment ownership.
- [x] **Verify and commit.** Run the focused command again and `uv run pytest tests/test_compute_apply_signals.py tests/test_background_compute_retries.py -q`. Commit `refactor(sync): isolate heavy database work from websocket dispatch`.

### Task 2: Let authenticated heartbeat bypass suspended business work

**Files:** Create `src/twicc/websocket_transport.py`, `tests/test_websocket_transport.py`, and `frontend/src/composables/websocketHeartbeat.test.js`.
Modify only `WSConsumer` integration in `src/twicc/asgi.py`; preserve other routes.

**Interfaces:** `HeartbeatTransport(receive, send, encode_json)` owns the upstream callables and encoder.
- `async run(application: Callable[[receive, send], Awaitable[None]]) -> None`: own the reader and its cleanup.
- `async receive() -> dict` and `async send(event: dict) -> None`: consumer-facing FIFO and serialized send.
- `enable_heartbeat() -> None`: called explicitly only after successful authorization and awaited accept.
- `disable_heartbeat() -> None`: idempotent gate closure on disconnect, close, or transport error.

- [x] **Write failing transport tests.** Use a tiny ASGI business app with event latches. `test_ping_overtakes_blocked_session_viewed` receives `'{"type": "pong"}'` before writer release. `test_ping_during_initial_snapshot` proves snapshot order remains serial. `test_auth_failure_accept_never_enables_heartbeat` verifies no pong on the temporary accept path.
- [x] **Add lifecycle and bounds cases.** Assert FIFO command order; independent 32-event and 16-MiB saturation; oversized single-event admission; malformed/binary/over-1-KiB forwarding; Unicode byte counting; upstream receive failure; raw send failure; cancellation during writer admission; and no pong after close or surviving reader after completion. Receive/send failures produce one synthetic `websocket.disconnect` after admitted events, not a receiver exception. Use internal code 1006 only as a received event, never as a wire close frame; retain exception provenance separately for logging. A swallowed sender error still marks transport failed.
- [x] **Run the failing tests.** `uv run pytest tests/test_websocket_transport.py -q`.
- [x] **Implement the adapter.** One upstream reader, one bounded FIFO, one send lock. Recognize only small valid object pings after gate activation. Recheck the gate under the send lock. Record terminal receive state separately from queue capacity so producer exit cannot strand a receiver. A send failure stops and joins the upstream reader even when that reader waits for another frame. The consumer-facing receiver yields its single terminal event through normal Channels dispatch. Cancel and join the reader in `run` cleanup. Preserve already admitted, shielded business writes.
- [x] **Integrate in `WSConsumer.__call__`.** Bind the adapter around `super().__call__` while retaining middleware outside the consumer. Enable it after the successful accept branch. Encode pong with `encode_json`, then use raw adapter send, bypassing exception-swallowing `send_json`. Normal messages remain on the existing Channels dispatch path. Add `async _cleanup_connection() -> None`, shared by `disconnect` and a `__call__` finally block. Make updates-group removal idempotent, safe before full setup, and drained despite caller cancellation. Keep intentionally detached agent-control work alive.
- [x] **Test the actual Channels consumer, not only the tiny ASGI fixture.** For receive failure, swallowed send failure, ordinary disconnect, partial connect failure, and repeated cancellation, assert no updates-group membership, transport reader, or `channel_receive` task remains after admitted writes drain. The installed `await_many_dispatch` can leak its second receive task when the first receiver raises; the synthetic disconnect prevents that failure path.
- [x] **Pin frontend compatibility.** In node:test, exercise the installed VueUse `useWebSocket` with a fake WebSocket and reactive scope. Configure the same compact response string as the app; deliver the server's spaced pong and assert `onMessage` runs. Close the scope/socket to clear timers. The backend test asserts the exact server string. Do not change heartbeat settings or app behavior to make the test pass.
- [x] **Verify and commit.** `uv run pytest tests/test_websocket_transport.py tests/test_websocket_origin.py tests/test_codex_ws_responses.py tests/test_claude_ws_question_responses.py tests/test_claude_ws_approval_permissions.py -q`; `cd frontend && node --test src/composables/websocketHeartbeat.test.js`. Commit `fix(websocket): answer heartbeat independently of blocked handlers`.

### Task 3: Add compact fact storage and explicit history readers

**Files:** Create `src/twicc/providers/history_facts.py` and `tests/test_history_facts.py`.
Modify `src/twicc/core/models.py`; create the next `session_history_fact` migration after the actual current migration leaf.
At the investigated baseline, the leaf is `0146_agent_runs.py`; do not hardcode an obsolete dependency.

**Interfaces:**
- `HistoryFact(NamedTuple)`: `line_num: int`, `kind: str`, `key: str`, `data: dict`.
- `HistoryFactKind(TextChoices)`: the eleven kinds listed in spec §6, counting goal context and goal update separately.
- `iter_history_facts(session_id: str, kind: str, key: str, *, before_line: int) -> Iterator[HistoryFact]`: newest first, strict prior-line bound.
- `iter_history_items(session_id: str, *, before_line: int, page_size: int = 128) -> Iterator[tuple[int, str]]`: reverse keyset pages of source line/content; no `LIKE` and no OFFSET pagination.
- `replace_history_facts(session_id: str, facts: Sequence[HistoryFact]) -> None`: caller-owned transaction; replace that session's complete fact set.
- `append_history_facts(session_id: str, facts: Sequence[HistoryFact]) -> None`: caller-owned transaction, idempotent unique rows.
- `history_facts_are_current(session: Session) -> bool`: compare the normal provider compute version; no new readiness state.

- [x] **Write failing storage tests.** `test_newest_fact_is_strictly_before_current_line`, `test_reused_keys_retain_both_occurrences`, `test_session_delete_cascades`, and `test_duplicate_source_fact_is_idempotent`. Assert keyset paging crosses gaps, malformed content, and page boundaries without searching text.
- [x] **Run the tests.** `uv run pytest tests/test_history_facts.py -q`.
- [x] **Implement model and readers.** Use session FK, positive line number, closed kind, exact key, and JSON data. Unique constraint `(session, kind, key, line_num)` supplies the lookup B-tree. Do not duplicate that index. Stream fact results in bounded pages when multiple candidates need validation. Context facts use key `"context"`; target facts use `"patch"` or `"mcp"`.
- [x] **Generate the schema migration without applying it to the running instance.** Use Django `makemigrations core --name session_history_fact` through `uv run` with `--settings=twicc.settings`. Inspect the generated dependency and SQL shape. Historical data rebuilding is absent from the migration.
- [x] **Verify and commit.** Run the focused tests; inspect SQLite `EXPLAIN QUERY PLAN` for exact-key/prior-line lookup. Commit `feat(compute): add computed session history facts`.

### Task 4: Extract facts once in batch and live compute

**Files:** Create both provider `history_facts.py` modules from the file map and `tests/test_history_fact_extraction.py`.
Modify `src/twicc/providers/compute_base.py` and both provider `compute.py` modules.

**Interfaces:**
- Provider side-effect-free helper `extract_history_facts(parsed: dict, *, line_num: int, history: HistoryFactContext) -> list[HistoryFact]` consumes the normalized record and read-only chronological evidence.
- Base hook `extract_history_facts(self, parsed: dict, *, line_num: int, history: HistoryFactContext) -> list[HistoryFact]` delegates to the provider helper.
- `HistoryFactContext` is defined in the shared `providers/history_facts.py`. `lookup_tool_call(call_id: str, *, before_line: int) -> tuple[dict, int] | None` returns payload and source line. `lookup_code_cell(cell_id: str, *, before_line: int) -> tuple[str, int] | None` returns owner call ID and announcement line.
- The batch context reads earlier accumulated evidence. The live context reads earlier current-batch evidence, then current-version persisted facts or outdated raw history. In tasks 4–5, persisted reads remain legacy-only until task 6 activates the version gate. All implementations use explicit `before_line`; they never expose future items.
- Reuse already computed owner/classification evidence when normalizing records. Do not rerun stateful `analyze_content` to populate facts.
- Normal `session_complete` payload gains `history_facts`, a list of dictionaries with the four `HistoryFact` fields.
- Live chronological state can cache extracted facts for the current batch; every consumer still enforces `before_line`.

- [x] **Write extraction tests.** Cover every fact family, malformed identifiers, repeated call IDs, multiple calls in one Claude line, ignored `wait_agent`, process/cell announcements, exact spawn paths, and goal/token context. Feed identical process-output records after `exec_command` and `write_stdin`: only the valid starter produces ownership evidence. Test reused call IDs and a `wait → cell → exec` announcement chain. Assert no copied script, output, or full source JSON appears in `data`.
- [x] **Run the tests.** `uv run pytest tests/test_history_fact_extraction.py -q`.
- [x] **Implement extraction from normalized records.** Store classification, identifiers, and source pointers. Validate process owner chains through `HistoryFactContext`; the current output alone cannot distinguish a starter from a continuation. Preserve existing bounds literally: the wait/cell/owner queries in `_lookup_exec_command_call_id` all use the process-output candidate line, including when an owner call ID is reused after the cell announcement. Do not invent running-state facts. Group same-line duplicate keys deterministically. Extract patch/MCP target metadata once from the parsed call. Reuse source-aware predicates for normalized records retaining `twiccOriginalContent` or `twiccOriginalEntry`. Large context payloads use source pointers.
- [x] **Wire both compute paths.** Batch accumulation follows chronological parsing and travels in `session_complete`. Register a record's extracted facts only after its extraction finishes. Live extraction registers earlier facts before their consumers and persists with items. Account for the existing first transformation pass and second linkage pass: a transformed earlier item is available through current-batch state before raw bulk insertion; a future item never is. Do not activate indexed persisted reads or bump versions yet.
- [x] **Verify parity.** Extend replay assertions for Claude and Codex to compare extracted facts from batch, whole-chunk live, and per-line live processing. `uv run pytest tests/test_history_fact_extraction.py tests/test_codex_agent_runs_live.py tests/test_nested_agent_compute.py -q`.
- [x] **Commit.** `feat(compute): extract historical facts alongside existing metadata`.

### Task 5: Publish facts atomically through normal compute

**Files:** Modify `src/twicc/providers/compute_base.py`, `src/twicc/providers/codex/rollout_migration.py`, and `tests/test_compute_apply_signals.py`.
Create `tests/test_history_fact_compute_apply.py`.

**Interfaces:** Consume task 3 append/replace helpers and task 4 `history_facts` payload.
Keep existing `apply_session_complete` results, revision guard, run IDs, and retry signaling unchanged.

- [ ] **Write failing publication tests.** A successful final apply publishes facts and metadata with the version. Superseded and missing sessions publish nothing. A failure after fact replacement rolls back facts and version together. Item pre-apply chunks cannot advertise a current fact index. Cancelling the caller allows an admitted successful transaction to finish atomically.
- [ ] **Add replacement and lifecycle tests.** Replacing a Codex rollout removes old facts in the same replacement transaction. New empty/live-created sessions have complete facts for all committed rows before current-version lookups are permitted. An initial-sync raw-only session stays outdated until normal compute completes.
- [ ] **Run the tests.** `uv run pytest tests/test_history_fact_compute_apply.py tests/test_compute_apply_signals.py -q`.
- [ ] **Implement final publication.** Keep `guard_compute_revision` first. Apply full facts inside `apply_session_complete` before its version write. Do not publish during item chunks. Delete facts during rollout replacement and every existing same-session raw-history replacement path found by searching `SessionItem` deletion. Preserve current rebuild scheduling and retry machinery.
- [ ] **Verify and commit.** Run the focused command plus `uv run pytest tests/test_background_compute_retries.py tests/test_codex_recompute_persistence.py tests/test_search_compute_readiness.py -q`. Commit `feat(compute): rebuild history facts through normal compute versions`.

### Task 6: Switch historical resolvers and activate provider versions

**Files:** Modify `src/twicc/providers/compute_base.py`, both provider `compute.py` files, `src/twicc/providers/history_facts.py`, and `src/twicc/settings.py`.
Create `tests/test_history_fact_resolution.py`; extend `tests/test_codex_agent_runs_live.py` and `tests/test_codex_agent_runs_live_process.py`.

**Interfaces:** Existing resolver names, parameters, and return values remain unchanged.
Internally consume task 3 readers and task 4 current-batch facts.
Cache readiness only within one synchronous operation; refresh Session state at the next operation.

- [ ] **Write resolution tests.** For each resolver family, compare indexed/current and legacy/outdated results. Seed a misleading partial fact, then a newer unindexed reuse: outdated lookup must return the newer raw evidence. Test absent IDs, invalid candidate before valid candidate, same-batch future matches, and control calls omitted from visible tool maps.
- [ ] **Pin existing semantics.** Assert process end handling and the 30-second late-announcement behavior. Test code-cell announcement type, exact agent path, turn-start bounds, goal/plan/token contexts, and orphan patch/MCP matching. Batch keeps the combined latest 50 candidates across both target families; live retains exhaustive matching plus its existing recency fallback.
- [ ] **Run the tests.** `uv run pytest tests/test_history_fact_resolution.py tests/test_codex_agent_runs_live_process.py -q`.
- [ ] **Replace generic resolvers.** Update `create_tool_result_link_live`, `create_agent_link_from_tool_result`, and matching spawn/completion lookups. Use indexed source pointers and existing predicates. Keep Claude queue-operation recovery on bounded raw pages where its semantics are not represented by existing fact kinds; explicitly document that residual path in the test and implementation notes.
- [ ] **Replace Codex resolvers.** Cover `_lookup_task_started_line`, `_lookup_tool_call`, `_lookup_spawn_for_agent_path`, `_lookup_orphan_end_exec_call_id`, `_lookup_code_cell_call_id`, `_lookup_exec_command_call_id`, `_lookup_prev_plan_context`, `_lookup_prev_goal_context_state`, and `_lookup_prev_total_tokens`. Audit `agent_tool_candidates_query` callers and replace matching spawn lookups. Do not alter image retrieval.
- [ ] **Enforce the readiness branch and bump versions.** Current version: indexed absence is final. Outdated version: only bounded raw pages for persisted history, with exhaustive parsing when necessary. Current-batch evidence is usable only with strict chronology. Advance both provider versions from their actual current values in the same commit as this switch; baseline values are Claude 110 and Codex 50. No second version or builder.
- [ ] **Verify SQL and behavior.** Use `CaptureQueriesContext` to reject `content LIKE` on current-version covered lookups and reject unbounded raw history reads in stale fallback. Run all task 3–6 tests plus existing batch/live agent-run and nested-agent tests. Commit `perf(compute): resolve historical ownership from computed facts`.

### Task 7: Preserve enrichment and replay state across failed slices

**Files:** Create `src/twicc/providers/enrichment_cache.py` and `tests/test_live_sync_rollback.py`.
Modify both original-file cache modules, both provider compute modules, `src/twicc/providers/compute_base.py`, and same-session history-replacement cleanup in Codex rollout migration.

**Interfaces:**
- `BorrowedEnrichment[T](NamedTuple)`: `key: tuple[str, str]`, `source_line: int`, `value: T`, `token: object`.
- `EnrichmentCache[T]` owns a small thread lock, current entries, and temporary retry reservations keyed by `(session_id, source_line, call_id)`. Expose `put(key, value)`, `borrow(key, *, source_line: int) -> BorrowedEnrichment[T] | None`, `commit(borrowed) -> None`, `rollback(borrowed) -> None`, `clear_session(session_id)`, and `cleanup_expired()`.
- Keep provider public `cache_original_file(s)` APIs; their implementation delegates to the shared cache. Keep `pop_original_file(s)` compatibility for nontransactional callers if any remain.
- `BaseSessionCompute.live_state_transaction(self, session_id: str) -> Iterator[None]`, decorated with `contextmanager`, surrounds one live transaction. It snapshots only mutable replay state touched by that session and owns borrowed enrichment until outcome is known.

- [ ] **Write failing rollback tests.** Inject failure after first-pass enrichment and after second-pass relation creation. Retry and assert identical full diff content, facts, counters, offset, and links. Test `borrow(A) → put(B) → rollback → retry`: the same source record receives A, while B remains current for a later source record. In one slice, a second record with the same call ID cannot borrow A again; it can borrow a newly inserted B. Also test replacement followed by commit, reservation expiry, explicit clear during a borrow, and successful raw-history replacement. An explicit clear must not be undone by a failed borrower.
- [ ] **Run the tests.** `uv run pytest tests/test_live_sync_rollback.py -q`.
- [ ] **Implement borrowed ownership.** Borrow first checks the exact source-record retry reservation, then the current entry. Atomically claim each token for one source record on its first borrow; other source records cannot borrow that token, including in the same uncommitted slice. Reserve the claimed token/value outside the current-entry slot so a later `put(B)` cannot replace A's retry evidence. A retry of the same source record can reuse A; B remains independently claimable. Pin active borrows against expiry. Commit removes the reservation and consumes the current entry only if its token still matches. Rollback retains the reservation for the same source record with a 300-second retry TTL, without overwriting B. Explicit `clear_session` invalidates both slots and reservations; late commit/rollback callbacks cannot resurrect cleared state. Expired unused reservations are removed; guarantees do not extend beyond the existing bounded cache lifetime. Register successful cleanup through `transaction.on_commit`. Clear reservations on committed raw-history replacement. Restore nonreconstructible replay state on exception, and invalidate only caches fully reconstructible from committed evidence.
- [ ] **Guard cross-thread access.** Protect compound put/borrow/consume/clear/expiry operations with the cache's thread lock. Never hold it across ORM work. Update both modules' inaccurate single-event-loop access comments. Snapshot per-session mutable provider maps before either live pass, including ownership, ended processes, pending context, and token baselines; do not copy database connections or whole compute instances.
- [ ] **Verify and commit.** Run focused tests plus provider live replay suites. Test outer-transaction rollback by making the live entry point own the outer transaction; do not return a committed result from inside a caller-owned transaction. Commit `fix(sync): preserve enrichment and replay state on rollback`.

### Task 8: Introduce bounded complete-record live transactions

**Files:** Create `src/twicc/providers/live_sync.py` and `tests/test_live_sync_slices.py`.
Create `src/twicc/providers/live_aggregates.py` and `tests/test_live_sync_aggregates.py`.
Modify `src/twicc/providers/compute_base.py`, `src/twicc/providers/db_writer.py`, `src/twicc/providers/sessions_watcher.py`, `src/twicc/core/models.py`, `src/twicc/settings.py`, existing activity-update helpers, and direct live-sync callers/tests. Add a schema migration for the indexes below.

**Interfaces:**
- `LiveSyncLimits(NamedTuple)`: `max_lines: int = 500`, `max_bytes: int = 4 * 1024 * 1024`.
- `RawLiveSlice(NamedTuple)`: `records: list[bytes]`, `end_offset: int`, `has_more: bool`, `bytes_consumed: int`.
- `read_live_slice(path: Path, *, offset: int, limits: LiveSyncLimits) -> RawLiveSlice`: select complete newline-terminated records before transformation; blank records count toward bytes, not nonempty-line limit.
- `LiveSyncUpdates(NamedTuple)`: name the existing ten result fields, retaining their current types: `new_line_nums`, `modified_line_nums`, `agent_link_updates`, `workflow_link_updates`, `tool_result_updates`, `agent_stopped_updates`, `found_compact_summary`, `agent_interaction_updates`, `agent_run_state_updates`, `agents_resumed`.
- `LiveSyncResult(NamedTuple)`: `updates: LiveSyncUpdates`, `has_more: bool`, `lines_processed: int`, `bytes_consumed: int`, `elapsed_ms: float`.
- `BaseSessionCompute.sync_session_slice(self, session_id: str, file_path: Path, *, limits: LiveSyncLimits) -> LiveSyncResult`: reload Session, own the outer transaction, process the whole selected slice.
- `ItemContribution(NamedTuple)` in `live_aggregates.py`: `session_id`, `project_id`, `provider`, `timestamp`, `kind`, `cost`, using existing model field types and persisted six-decimal `Decimal` costs.
- `SessionContribution(NamedTuple)`: `session_id`, `project_id`, `provider`, `parent_session_id`, `type`, `hidden`, `created_at`, `user_message_count`.
- `apply_contribution_changes(before_items: Sequence[ItemContribution], after_items: Sequence[ItemContribution], *, before_sessions: Sequence[SessionContribution], after_sessions: Sequence[SessionContribution], repair: bool) -> None`: caller-owned transaction; update existing session/parent/activity aggregates exactly. `repair=True` recalculates affected aggregates from stored rows; `False` applies bounded before/after deltas.

- [ ] **Write boundary tests.** With 501 short nonempty records, the first result processes 500 and leaves ready backlog. An oversized first complete record advances once. Split UTF-8 and incomplete JSON tails leave the offset at the last newline. Malformed complete records and blank lines preserve existing numbering rules. Unchanged mtime with increased size still advances.
- [ ] **Write transactional parity tests.** Compare batch, whole-history live, and repeated slices with limits 1, 2, and 500. Place each call/result, process start/end, and agent run boundary across a slice boundary. Force elapsed time beyond 100 ms during pass two and assert all prepared rows commit. Reuse task 7 rollback cases.
- [ ] **Write aggregate and scaling tests.** Use a long history with many non-null costs and repeated message IDs, then append a small slice. Assert dedup never materializes the historical ID set and current-version appends do not issue whole-session item `SUM`/`COUNT` queries. Verify null versus zero, six-decimal cost, hidden and subagent eligibility, UTC day/week boundaries, and a first user message arriving in a later slice than session metadata. Test a committed compute chunk followed by failed/superseded final apply, then a live append. Also test a current parent with an outdated child chunk and unrelated sessions sharing a global activity bucket.
- [ ] **Run the tests.** `uv run pytest tests/test_live_sync_slices.py tests/test_live_sync_rollback.py tests/test_live_sync_aggregates.py -q`.
- [ ] **Implement bounded reading.** Stop selecting before a later record exceeds the byte budget, except the first oversized record. Detect a complete next record without consuming its committed offset. `has_more` is false for a trailing incomplete record. Avoid loading the entire remaining file to detect readiness. Do not requeue incomplete tails without new bytes.
- [ ] **Extract the transactional entry point.** Reuse existing two-pass logic, with task 7 state protection. Facts, items, links, costs, metadata, and offset commit together. Refresh session state for subsequent watcher serialization. Replace positional result access with named fields. Search all `sync_session_items_from_file` callers and migrate them; fixture helpers explicitly drain slices when they mean full-file replay.
- [ ] **Bound message deduplication before enabling small slices.** Add the `(session, message_id, line_num)` index. For each distinct Claude message ID in the selected slice, use indexed `EXISTS` with `line_num < current_line`, then cache the answer within that slice. Never load every historical ID or fetch all matching rows through `IN`. Preserve chronological in-slice deduplication without rerunning transformations. Codex keeps its existing total-token baseline and needs no message-ID history query.
- [ ] **Maintain aggregates from changed contributions.** For current-version sessions, read only before/after rows changed by the transaction. Update self cost, the direct parent's subagent cost, and user count by exact deltas. Apply matching project/global daily and weekly deltas, preserving `PeriodicActivity.recalculate` eligibility: costs include all sessions; message/session counts exclude hidden sessions and subagents. Session-count bucket changes use `created_at`, including the transition from no user messages to the first user message. Delete all-zero activity rows. Metadata-only `group_tail` changes have zero contribution.
- [ ] **Preserve nullable cost semantics and all writer paths.** Use existing partial index `idx_item_cost_session` for a bounded non-null-cost existence check; add a partial `Session.parent_session` index with `self_cost IS NOT NULL` for parent nullability. A parent's full baseline repair must first recalculate and persist its direct children's cost fields from their stored items in the same transaction, without advancing child compute versions. Otherwise an old partial child apply can leave that index inconsistent with raw costs. Test a child whose cached `self_cost=None` but whose raw item cost is `Decimal(0)`, and the inverse. Preserve `total_cost=None` when the sum is nonpositive. Route background `apply_session_items_chunk` and other item contribution mutations through the same helper in their own transactions, including replacement/deletion and changed old timestamps/kinds. A chunk cannot commit new item costs while leaving parent/global totals stale. Current sessions use deltas; outdated sessions perform complete affected session, parent, and activity-bucket repairs in that transaction. Final apply retains exact full repair before publishing its version.
- [ ] **Activate exact baselines through normal compute.** Bump both normal provider compute versions again when this aggregate change lands, from the actual then-current values. This repairs any partial applications produced by older code; it adds no separate readiness field or builder. Read version and session state inside each worker operation. Current-version deltas rely on the final repair just described; outdated sessions keep full repairs and do not use adaptive shrinking. Full repair also covers the session-count creation-date buckets, not only newly observed item dates. Add a regression proving a current parent's/global bucket's totals stay correct while an outdated child or peer chunk commits.
- [ ] **Retire duplicate post-apply activity flushes.** Once every successful item/final-apply transaction maintains activities directly, remove `pending_activity_days` collection and threshold/final/abandoned activity flushes from DB writer run state. Preserve project broadcasts, run completion, failed-run accounting, and worker drainage. Keep `affected_days` payload information needed by the in-transaction repair. Replace task 1's legacy-flush regression with a latch on the actual final-apply aggregate path, and assert run finalization performs no duplicate aggregate recalculation.
- [ ] **Bridge to the existing watcher until task 9.** The watcher can temporarily drain slices using its current callback scheduling, clearly without claiming fairness. Each slice releases its SQL transaction; keep admission safe. This bridge preserves behavior while task 9 removes retained outer locks.
- [ ] **Verify and commit.** Run focused tests plus existing provider live and compute parity suites. Commit `refactor(sync): process live records in bounded transactions`.

### Task 9: Schedule session paths fairly and retain completion semantics

**Files:** Create `src/twicc/providers/session_change_queue.py` and `tests/test_session_change_queue.py`.
Modify base and Codex `sessions_watcher.py`, `src/twicc/providers/codex/background_compute.py`, and their callback wiring in the Codex orchestrator; add `tests/test_watcher_slice_fairness.py`.

**Interfaces:**
- `SessionChangeQueue` owns coalesced ready paths and drain waiters. It is event-loop-owned, with no database state.
- `enqueue(path: Path, change: Change) -> None`: record a dirty path and queue it once.
- `QueuedSessionChange(NamedTuple)`: `path: Path`, `change: Change`, `token: int`.
- `async next_change() -> QueuedSessionChange`: dequeue one turn without clearing events that arrive afterward.
- `finish(turn: QueuedSessionChange, *, has_more: bool, deferred: bool = False, failed: bool = False) -> None`: settle that turn and requeue only ready backlog or a newer event. Deferred paths also react to explicit migration-release outcomes below, including failures without replay.
- `PathDrainTarget(NamedTuple)`: `source_generation: object`, `end_offset: int`. This is a process-local source identity/replacement token and finite complete-record watermark, not a database or index version.
- `async wait_drained(path: Path, *, target: PathDrainTarget) -> None`: settle when the committed offset in that source generation reaches the captured target, independently of later appends. Shield shared work from waiter cancellation.
- `close() -> None`: reject new admission, wake blocked consumers, and settle outstanding waiters with cancellation after admitted work drains.
- `async process_path(path: Path) -> None` remains the watcher's public API. It captures the last complete record present at request time and waits for that finite target, not a moving EOF.
- `MigrationRelease(NamedTuple)`: `session_id: str`, `path: Path | None`, `outcome: Literal['ready', 'failed', 'cancelled']`, `replay: bool`, `error: str | None`, `release_token: int`.
- `watcher.notify_migration_released(release: MigrationRelease) -> None`: synchronous event-loop callback invoked after unmarking/releasing every migration lease, independently of successful replay.
- Each watcher callback reports disposition (`drained`, `ready`, `deferred`, or `failed`) and its committed source offset/generation. Codex overrides propagate the result while retaining rewrite/migration checks. Keep this distinct from optional `IndexingRequest`, so a subagent or non-indexable slice still reports ready backlog.
- One consumer task drains the queue. The filesystem producer only enqueues; it never waits for a large path to drain.

- [ ] **Write fairness tests.** Queue a large and small path; assert the small path commits before the large backlog drains. Assert one slice per turn, writer lease released between turns, provider `_change_lock` released between turns, and no lost append while a path is in flight.
- [ ] **Write lifecycle tests.** Repeated events coalesce. `process_path` returns once its captured complete-record target commits even while newer appends keep `has_more=True`. An incomplete tail beyond that target cannot prevent return. Deferred migration failure with `replay=False` settles waiters without a new filesystem event. Test release after the deferral check but before `finish(deferred=True)`. Cancellation of one waiter does not cancel shared processing. Stop settles all waiters and drains admitted writes without orphan tasks. File error completes waiters using the existing logged-error contract instead of hanging or tight retrying.
- [ ] **Add event precedence tests.** Deletion followed by recreation triggers a fresh provider parse. Truncation/rewrite detection runs before append slicing. An incomplete tail parks the path. A filesystem append, explicit replay, or replacement signal can wake it. Deletion or replacement invalidates old-generation wait targets with the logged-error outcome; never compare an old byte target with the new file. A coordinator's subsequent replay captures a fresh target. Never busy-requeue an error or migration-deferred path.
- [ ] **Run the tests.** `uv run pytest tests/test_session_change_queue.py tests/test_watcher_slice_fairness.py -q`.
- [ ] **Implement scheduling.** Move awatch and catch-up discovery to producers. The queue consumer executes one provider callback/slice at a time. Track events arriving during processing with a dirty token; clear only the token captured for the completed turn. Requeue ready backlog behind already-ready paths. Reparse after deletion/recreation; do not let a cached `ParsedSessionFile` bypass Codex migration exclusion.
- [ ] **Make migration release total and race-safe.** Preserve the path before removing `migration_paths`. Notify on every release, including benign `replay=False` cases, failure, and cancellation. `ready/replay=True` schedules normal replay; `ready/replay=False` wakes only already deferred/dirty paths; `failed` logs and settles current waiters without automatic retry; `cancelled` cancels them. Make notifications idempotent by release token and keep the latest outcome through a callback in flight. `finish(deferred=True)` must consume a newer release outcome instead of parking after its only wake. Keep existing successful replay callbacks coalesced with this notification.
- [ ] **Move lock boundaries.** Retain `run_under_db_write_lock` around one slice and its established committed-update handling. Release writer and provider callback locks before requeue. Keep Tantivy operations after the committed transaction and preserve search-readiness gating. Do not relax Codex migration/rewrite exclusion within a slice. Preserve compaction, resumed/stopped agents, shell-event, and activity callbacks per committed update.
- [ ] **Tune future slices only.** Start each path at 500 lines. Enable adaptation only on the current-version bounded dedup/aggregate path from task 8. If that completed slice exceeds 100 ms, halve its next line limit, minimum 1. Below 50 ms with ready backlog, double toward 500. Outdated fallback retains 500 lines and the 4-MiB cap; repeated full repairs must not trigger ever-smaller slices. Discard tuning state when drained; never shrink a prepared slice.
- [ ] **Verify and commit.** Run queue/fairness tests, slice tests, `tests/test_search_compute_readiness.py`, and `tests/test_codex_recompute_persistence.py`. Commit `perf(sync): schedule live session backlog fairly`.

### Task 10: Validate the combined system and document deployment

**Files:** Create `tests/test_websocket_sync_integration.py` and `scripts/benchmark_session_sync.py`.
Modify focused logging sites in executor, transport, watcher, and final compute apply.
Update this plan's checkboxes, the approved spec's status, and only the current `CHANGELOG.md` Unreleased section.

**Interfaces:**
- Benchmark CLI: `--data-dir PATH --lines 150000 --payload-bytes 1500000000 --report PATH`.
- The script refuses the normal user data directory and any preexisting database. It operates only in an explicitly empty disposable directory.
- It seeds and migrates only its disposable database programmatically, never a running instance. It starts its test ASGI server on an ephemeral loopback port and always shuts it down. It writes an orjson report with workload, machine, query plans, latency samples, counts, and completion timings.

- [ ] **Write combined regression tests.** Start real writer lifecycle with a controlled heavy job and the actual `WSConsumer`. Verify auth/initialization remains possible and pings pass a blocked business writer. Combine current and outdated fact resolution with large and small slice queues. Verify no semantic differences in resulting links, process ownership, costs, and checkpoints.
- [ ] **Run tests before observability changes.** `uv run pytest tests/test_websocket_sync_integration.py -q`. Failures must identify coupling or lifecycle defects, not timing noise.
- [ ] **Add low-volume timing diagnostics.** Measure executor queue wait and synchronous run time; writer-lock wait where admission boundaries already exist; slices/final apply above 100 ms; and handshake above one second. Include provider/session identifiers and sizes, never message payloads. Rate-limit repeated slow messages per session. Log unexpected transport termination once per connection, not every failed subsequent send or heartbeat.
- [ ] **Implement and run the disposable benchmark.** Include reused identifiers, true misses, unrelated large payloads, and a concurrent small-session workload. Include 150,000 message-ID/cost-bearing rows, whole-backlog replay, and a small append to an established large session. Measure cold/warm fact lookups and outdated fallback separately. Count prefix-row visits or SQLite VM steps across slice sizes to detect quadratic dedup/aggregate work; timing alone is insufficient. Assert indexed query plans and scaling; record authenticated loopback WebSocket pong samples during backlog. Target p95 below one second and report maximum latency. Use scratch space, not repository files, for the disposable DB and generated payloads. Set the disposable data directory before importing TwiCC and verify Django resolves its database there. Use `127.0.0.1` and an ephemeral port; never the running instance's port, credentials, or provider homes.
- [ ] **Run integrated verification.** `uv run pytest`; `cd frontend && npm test`. Run targeted ruff with `uvx ruff check` on changed Python files if lint verification is selected. Do not install tools into the active environment. Record existing unrelated failures separately with baseline evidence; do not claim a clean suite if they remain.
- [ ] **Run the real-client check when the user enables the changed running instance.** Localhost reconnect during catch-up; remote reconnect; phone background/foreground and tab switching. Verify the indicator recovers, session data catches up, and no false visibility-probe reconnect occurs. This step remains explicitly pending if no restart is authorized or no phone is available. Automated ASGI tests do not substitute for the real-client result.
- [ ] **Review and document.** Obtain the execution method's independent review, fix concrete findings, and recheck affected tests. Record remaining limits from spec §10. Re-read the changelog top before adding an Unreleased entry. Give the user the required `devctl.py` restart/migration instructions; do not run them without a request.
- [ ] **Commit.** `test(sync): validate websocket responsiveness under session backlog`, with actual automated and manual validation status in the body.

## Plan self-review and acceptance map

| Spec requirement | Owning tasks | Verification |
| --- | --- | --- |
| Heavy executor and cancellation-safe shutdown | 1 | Worker latch, lease retention, thread-owned connection close, lifecycle restart |
| Authenticated heartbeat bypass and wire compatibility | 2 | Blocked handler/snapshot, auth rejection, exact pong, VueUse callback |
| FIFO bounds, backpressure, and termination | 2 | Both limits, oversized event, failure and cancellation cases |
| Compact facts and strict historical lookup | 3–4 | Reused IDs, same-batch chronology, compact payload assertions |
| Normal compute-only rebuilding | 5–6 | Atomic version publication, stale result rejection, no partial-index authority |
| Provider semantics and history replacement | 4–6 | Batch/live parity, process and orphan rules, rollout invalidation |
| Enrichment-safe rollback | 7 | Failure after borrowing, concurrent replacement, explicit clear |
| Complete-record transactional slices | 8 | Exact offsets, malformed/blank records, UTF-8/tails, oversized line |
| Bounded per-slice deduplication and exact aggregates | 8 | Indexed EXISTS, null/zero, partial compute writes, child and global consistency |
| Fair scheduling and provider coordination | 9 | Large/small progress, requeue without event, migration/deletion/stop |
| Diagnostics and workload evidence | 10 | Isolated large workload, query plans, latency distribution |
| Running-instance validation | 10 | Explicit pending/manual state until authorized deployment and client check |

Self-review completed for spec coverage, interface naming, step scope, and five review-focus cases.
No implementation tasks are checked complete when this plan is published.
The approved design remains authoritative if a task's detail conflicts with it.

## Execution handoff

Recommended method: **native execution in this session, with independent review of the completed change**.
The tasks share provider state and transaction contracts; retaining their context reduces repeated investigation and interface drift.
The plan supports subagent-driven execution if the user prefers separate implementation and review gates per task.
No execution method is selected by this document alone.
