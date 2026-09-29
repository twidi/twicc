# WebSocket responsiveness and indexed session history

Date: 2026-09-29
Status: Approved by the user on 2026-09-29. Product implementation has not started.
Source baseline: `fb51f74e` on local `main`.
Implementation plan: [Task sequence and validation](../plans/2026-09-29-websocket-sync-responsiveness.md).

## 1. Objective and constraints

Keep `/ws/` connections responsive while large session histories synchronize or recompute.
Preserve exact tool, background process, and subagent linkage.
Let small sessions advance while a large session catches up.

The user explicitly requires historical index construction through **normal compute version processing**.
The index is computed metadata, like existing links and run evidence.
A provider compute-version bump rebuilds it with the rest of session metadata.

There is no independent index builder, queue, readiness column, generation number, or historical backfill service.
The existing compute revision guard, retries, and provider coordination remain authoritative.

Keep SQLite, the single backend process, and existing provider behavior.
Do not require Redis, PostgreSQL, or another deployed service.
Do not change heartbeat timeouts to conceal backend delays.

## 2. Investigation and confidence

The failure occurs through both localhost and the remote tunnel.
Server logs show batches of connections becoming accepted together after long waits.
At 00:58:45 on September 29, one batch contains 22 localhost and 12 remote connections.
HTTP traffic continues during some WebSocket waits.

The largest investigated Codex session contains 145,950 lines and approximately 1,521 MB of stored content.
September 28 adds approximately 347 MB.
Several history lookups search JSON text with `content__contains` and then use `iterator(chunk_size=10)`.
SQLite can scan the remaining history while collecting ten rows, before Python can return the first valid candidate.

Read-only reproductions on that session:

| Operation | Result |
| --- | --- |
| Actual `_lookup_tool_call`, result line 143300, call line 143297 | 1.724 s; 1.721 s inside `fetchmany` |
| Four equivalent history searches | Approximately 1.5–1.7 s each |
| Agent interaction lookup, adjacent call | 1.344 s |
| `agent_run_states` for 413 linked agents | 72 ms |
| Synthetic ownership resolution for all 413 agents | 133 ms |

The September 25 background-process change adds historical owner recovery and validation.
One real process lookup takes 2.775 s before that change and finds nothing.
The current lookup takes 5.261 s and finds the correct owner.
These results have different semantics; this is not a like-for-like speed comparison.

September 27 agent-control support adds more callers of historical lookups.
However, the parsed September 28 range of the large session contains no subagent activity or owner-abort evidence.
Do not attribute every overnight incident directly to that new path.

The shared executor explains how slow synchronous work can delay otherwise lightweight WebSocket dispatch.
Channels calls `aclose_old_connections()` before dispatch; this uses the shared thread-sensitive executor.
A dependency-only experiment queues 350 ms of synchronous work:

| Worker location | Channels dispatch latency |
| --- | --- |
| Shared executor | 350.39 ms |
| Dedicated executor | 1.02 ms |

These measurements demonstrate a blocking mechanism and expensive production-data queries.
They do not capture the complete stack of a particular live outage.
Disconnected-client errors and channel expiry can amplify an incident after the original delay.

## 3. Selected architecture

Use four complementary changes:

1. Move heavy synchronous ingestion and compute application to one dedicated executor.
2. Let authenticated heartbeat messages bypass a suspended `/ws/` business handler.
3. Resolve historical relationships from compact, computed facts.
4. Process live catch-up in bounded, fairly scheduled transactions.

An executor alone cannot protect heartbeat messages behind `session_viewed`, which awaits the write lock.
An index alone does not protect connections during stale-history fallback or other expensive work.
Batching alone does not remove repeated searches through gigabytes of JSON.

Rejected alternatives:

- Increasing timeouts: retains the underlying stalls and delays failure detection.
- Only replacing `iterator()` with `first()`: helps a matching query, but not absent or invalid candidates.
- A fixed recent-history window: can silently lose legitimate old owners.
- One `ThreadSensitiveContext` per connection: creates reconnect-dependent threads and complicated detached-task shutdown.
- Unrestricted `thread_sensitive=False`: loses controlled thread ownership and does not preserve write serialization.

## 4. Dedicated heavy-work executor

Own one `ThreadPoolExecutor(max_workers=1)` through the DB writer lifecycle.
Expose one internal async helper based on:

```python
database_sync_to_async(function, thread_sensitive=False, executor=compute_executor)
```

Route these synchronous boundaries through the helper:

- Live `sync_session_items_from_file`.
- Initial-sync database application.
- Compute item-chunk application and final session-complete application.
- Other heavy application boundaries identified in the same DB writer dispatch paths.

Audit these boundaries explicitly. Moving only the main watcher call is incomplete.
Keep quick authentication and ordinary lightweight reads outside this executor.

Each transaction remains entirely inside one synchronous invocation.
Pass IDs, immutable arguments, or detached values across the boundary; never pass connections or cursors.
Avoid passing a lazily evaluated QuerySet or an object that triggers deferred ORM reads in another thread.
Clean Django connections before and after each invocation through the database adapter.

Keep `run_under_db_write_lock` admission, nested leases, cancellation shielding, and drain semantics.
A cancelled caller cannot release the write lock while its thread still writes.
This lock does not currently cover every REST write; do not claim global serialization of all SQLite writers.

Shutdown first stops new admission, then drains admitted work and closes worker-owned connections.
Only then shut down the executor. Never block the event loop in `shutdown(wait=True)` with active jobs.
Startup and shutdown remain idempotent under existing DB writer lifecycle rules.

## 5. Heartbeat transport for `/ws/`

### Receive ownership and authentication

Add a small transport adapter dedicated to `WSConsumer`, outside its sequential Channels dispatch.
One task owns the original ASGI `receive` callable.
It forwards ordinary events into a bounded FIFO read by the existing consumer.

Only small, valid text JSON heartbeat objects bypass that FIFO.
Recognize an object with `type == "ping"`; cap the fast-path text at 1 KiB.
Larger messages, other JSON values, malformed JSON, binary frames, and ordinary commands follow the existing consumer path.
This preserves existing validation behavior and prevents expensive JSON parsing in the fast path.

The consumer explicitly enables the heartbeat gate after successful authorization and completed `await accept()`.
The temporary accept used to deliver `auth_failure` never enables it.
Keep the existing session middleware and public-origin checks outside this adapter.
Do not change terminal or shared-session WebSocket routes.

### Send ordering and wire compatibility

Serialize every ASGI send with one connection-owned async lock.
This includes accept, normal data, pong, and close.
Close disables the gate before waiting for that lock.
A pong rechecks the gate inside the lock before sending.

Use the existing JSON encoder for pong, preserving `{"type": "pong"}`.
Encode through the consumer's existing `encode_json`, then use the adapter-owned raw send path.
Do not route pong through the current `WSConsumer.send_json`, which catches and suppresses send exceptions.
The raw send wrapper observes failures for every sender and signals transport termination before any caller can suppress them.
The frontend's compact `responseMessage` can filter an exactly matching pong before `onMessage`.
The visibility probe uses `onMessage` to observe connection activity.
Changing whitespace without updating that interaction can introduce false reconnects.

Business messages and snapshots retain their current sequential execution and ordering.
Pong is the only message allowed to overtake pending business work.
Do not detach snapshot generation or run ordinary commands concurrently.

### Queue limits and termination

Start with a FIFO limit of 32 events and 16 MiB of pending payload.
At most one additional upstream event can be held while awaiting FIFO admission.
Apply backpressure when either limit is reached. Never silently drop or coalesce commands.
An individually oversized event follows a defined single-event admission exception when the FIFO is empty.
This avoids deadlock without introducing a lower frame-size limit than the existing server.
The upstream server frame limit remains the upper bound for that exception.

When a disconnect is observed, disable heartbeat immediately.
Forward its terminal event after previously admitted business events, preserving serial handling.
Stop upstream reads. On a receive/send failure, deliver one synthetic `websocket.disconnect` after admitted events.
Keep exception provenance separately for logging; do not raise it from the consumer-facing receiver.
The installed Channels dispatch cleanup can leave its channel receive task alive after a receiver exception.
Never leave the consumer waiting on an empty FIFO whose producer has exited.

On consumer completion, failure, or cancellation, cancel and join the reader task and release queued payloads.
Preserve existing shielded database writes and intentionally detached process-control tasks.
Do not let a failed pong sender leave the reader or consumer waiting forever.
Run idempotent updates-group cleanup from both normal disconnect and the consumer's final cleanup, including partial setup and cancellation.

### Guarantee and limits

With available queue capacity and a writable transport, authenticated pings do not wait for compute or suspended business handlers.
This includes a handler waiting for the database write lock and an initial snapshot waiting after accept.
It does not promise progress during network backpressure, FIFO saturation, or event-loop starvation.
A healthy heartbeat indicates transport liveness, not completion of session synchronization.

## 6. Computed history facts

### Storage and lookup contract

Add one computed model, provisionally `SessionHistoryFact`:

| Field | Meaning |
| --- | --- |
| `session` | Session foreign key, cascade on session deletion |
| `line_num` | Source SessionItem line number |
| `kind` | Closed enum of supported fact kinds |
| `key` | Exact provider identifier or declared context key |
| `data` | Compact validated JSON payload |

Use uniqueness on `(session, kind, key, line_num)`.
Its B-tree supports exact-key lookup ordered by descending `line_num`.
Do not add another identical index.
If a source line has several same-kind facts, the extraction contract combines them under a stable key.

All historical lookups enforce `line_num < current_line`.
Retain separate occurrences of reused identifiers.
Select the newest valid occurrence using the existing provider predicates.
Never infer that a newer raw row in the same inserted batch is already available historically.

Store only fields needed to resolve or validate ownership and context.
Do not copy full commands, scripts, outputs, images, or transcript JSON.
Fetch the original item by indexed `(session, line_num)` only when its full payload is needed.
Fields that require full provider context can use a source pointer instead of duplicating that payload.

### Initial fact families

| Kind | Key | Purpose |
| --- | --- | --- |
| `tool_call` | Call ID | Tool name, source pointer, background flags, validated call classification |
| `process_start` | Process ID | Validated starter call ID and line |
| `code_cell` | Cell ID | Owner call announced by the relevant custom tool output |
| `agent_spawn` | Agent path | Announcing spawn call and agent identity |
| `turn_start` | Turn ID | Exact start evidence for turn-end resolution |
| `code_exec_target` | Target family | Compact patch/MCP target evidence for orphan completion resolution |
| `turn_context` | Declared singleton key | Latest prior provider turn context pointer |
| `plan_marker` | Declared singleton key | Latest prior plan-mode marker |
| `goal_context` / `goal_update` | Declared singleton key | Existing goal-state reconstruction inputs |
| `token_usage` | Declared singleton key | Previous total-token context |

These are historical evidence, not mutable runtime state.
Keep `_process_owners`, `_ended_processes`, and the existing 30-second late-announcement behavior.
Do not replace those rules with “the latest process row is running.”

`tool_call` extraction must include valid control calls excluded from visible tool entries, including `wait_agent`.
Use the existing provider classifiers. Visible `ToolUseEntry` lists alone are insufficient.
Keep custom-tool announcement restrictions for cell ownership and exact agent-path matching.
Preserve the current batch orphan-target limit of the latest 50 combined candidates and the current live search semantics.
Any semantic harmonization is a separate change, not a performance optimization.

### Extraction and live use

Use provider hooks that extract facts from already parsed data.
Share these hooks between full compute and live ingestion.
Do not call stateful `analyze_content` twice to obtain facts.
Supply a read-only chronological lookup context where validation needs earlier calls or cell announcements.
The current process-output record alone cannot identify a validated starter.

Full compute collects facts alongside existing tool, agent, and run-evidence maps.
Live sync registers facts in chronological order, before downstream consumers need earlier evidence.
Persist new facts with items, relations, session metadata, and checkpoint in the same transaction.
In-memory caches remain an optimization; restart cannot remove required historical evidence.

### Normal compute-version reconstruction

Bump `CLAUDE_CODE_COMPUTE_VERSION` and `CODEX_COMPUTE_VERSION` when each provider's extraction and resolver switch are complete.
At this baseline they are 110 and 50. Choose the next versions from the actual source at implementation time.
Do not introduce an index-specific version.

Include the complete computed fact set in the normal session-complete result.
The existing revision guard remains the first write in final apply.
Inside that transaction, replace/diff the session's facts, apply relations, and advance `Session.compute_version` together.
A missing session, superseded revision, or rolled-back transaction cannot publish a complete index.
Caller cancellation waits for an admitted transaction to finish under the existing shielding contract.
That transaction can succeed and atomically publish facts, metadata, and the current version despite caller cancellation.

Existing item pre-apply chunks do not publish index readiness.
Keep facts in final atomic application initially; their payload is compact relative to raw history.
Measure final-apply duration. Do not add a separate generation protocol speculatively.

Normal compute retries handle stale results.
Preserve provider-specific scheduling, including Codex compute-only processing versus active-session rollout migration gates.
Do not add a universal “wait until inactive” rule or block live ingestion until reconstruction finishes.

Raw-history replacement must invalidate/delete facts together with replaced SessionItems.
In particular, update the Codex rollout-replacement transaction.
A cascade on Session deletion alone cannot handle replacement inside the same session.

### Sessions with outdated compute versions

For a session at the provider's current compute version, indexed absence is authoritative.
Do not fall back to JSON text scanning after an indexed miss.

For an outdated session, use the legacy resolver exclusively for persisted history.
A partial fact-table hit is unsafe: an unindexed, newer reuse of the identifier can exist.
A cache of validated occurrences from the current chronological batch may still accelerate that batch.
Publish full indexed lookup only through the normal final compute-version update.

Replace unbounded `LIKE + iterator` fallback reads with explicit reverse line-number pages.
Select a bounded number of raw rows using indexed session/line predicates, then parse and validate candidates in Python.
Continue across pages until a valid match or history exhaustion. Do not impose a correctness-breaking history horizon.
Use byte-aware subpages where practical; a single large source row remains an unavoidable indivisible input.

Pagination bounds one read, not total fallback work.
An absent match can still traverse the full history of an outdated session.
The dedicated executor and heartbeat transport protect connection liveness during that transition.

### Scope boundary

Replace the live ownership/linkage lookups in `compute_base.py` and Codex compute, including their cold-context helpers above.
Audit spawn-candidate selection and Claude completion recovery for reuse of indexed facts where their semantics match.
Image retrieval and unrelated user-requested full-text search are outside this index.
Do not claim that all JSON text searches disappear from the application.

## 7. Bounded live sync and fair scheduling

Select a raw live slice before transformation, with initial limits of 500 nonempty lines and 4 MiB of source bytes.
Process and commit every selected record together through the existing two-pass algorithm.
Target 100 ms per slice as a tuning objective; do not stop halfway through the prepared second pass.
The first pass has already mutated provider state for all selected records.
Discarding an uncommitted suffix would corrupt that state unless every effect were restored.
Reduce future slice sizes when measured durations exceed the target; never change the current slice's prepared prefix.
One complete line may exceed the byte or time target and must still make progress.
An expensive legacy lookup cannot be preempted midway by a soft deadline.

Read complete byte-delimited JSONL records from the committed `last_offset`.
Commit only the offset of fully consumed records, not the file size observed at entry.
Do not decode a UTF-8 character across independently committed partial records.
Keep existing invalid-byte replacement and blank/malformed-line numbering behavior for complete records.
Leave an incomplete trailing record uncommitted until more bytes arrive; never busy-loop on that tail.

The transaction covers items, facts, relations, runtime metadata, costs, and the exact checkpoint.
Emit broadcasts and search updates only from committed results, using the existing post-commit flow.
On rollback, restore provider state mutated during the failed slice.
Invalidate only caches whose complete contents can be reconstructed from committed evidence.
Some enrichment caches hold one-shot original-file contents absent from the JSONL source.
Codex `pop_original_files` and Claude `pop_original_file` consume these during the first pass.
For these caches, borrow without consuming, then consume matching entries only after commit, or restore borrowed entries on rollback.
Preserve entries added or replaced concurrently; cleanup must match the borrowed value or identity.
Retry must retain the same full diff enrichment, not just linkage and counters.
Keep failed-attempt enrichment reserved for its exact source record, separately from any newer entry with the same call ID.
A token belongs to one source record from its first borrow; another record in the same slice cannot consume it again.
Explicit session clear or committed history replacement invalidates those reservations; unused reservations retain a bounded 300-second retry lifetime.
Do not retain an advanced in-memory offset or ownership map after a failed transaction.

Bound the work repeated by each slice, not only its input size.
Use indexed existence checks for the slice's message IDs, rather than loading all historical IDs.
For current-version sessions, maintain exact cost, message, and activity aggregates from changed-row contributions.
Preserve existing eligibility, UTC buckets, persisted Decimal precision, and null-versus-zero behavior.
Every committed item-mutation transaction must leave its session, parent, and shared activity aggregates consistent.
This includes background pre-apply chunks that may outlive a failed final apply.
Before relying on child cached costs for parent nullability, full parent repair synchronizes direct-child costs from stored items.
That repair does not advance child compute versions.
Outdated sessions use complete affected-aggregate repair inside each such transaction until normal compute establishes a current baseline.
Use another normal provider compute-version bump when this change lands; never add independent aggregate readiness state.
Keep outdated slices at their initial size; expensive fallback/repair must not trigger increasingly small slices.

Return an explicit result containing committed updates and whether another complete-record slice is ready.
Use a named result type rather than adding more positional slots to the existing long tuple.

The watcher schedules ready files through a coalesced FIFO work queue.
After one slice, release both the DB write lock and the provider callback lock.
Place a file with remaining complete records behind other ready files.
Requeue it without requiring another filesystem event.
Re-read Session state before the next slice.

Do not implement a private `while has_more` loop that retains `_change_lock` or the writer lease.
Coalesce repeated events without losing an append that arrives during processing.
Deletion, truncation, and replacement must follow existing provider handling before another slice is admitted.
Preserve Codex `defer_session_change`, migration exclusion, and explicit `process_path` behavior.
An explicit `process_path` waiter captures the finite complete-record backlog present when requested.
It completes after that source generation's target offset commits; later appends must not extend its target indefinitely.
Incomplete trailing bytes are outside the target. Replacement/deletion terminates obsolete targets rather than reusing their offsets.
Notify the queue when migration releases a path, including failure or cancellation without successful replay.
Drain waiters must settle on these terminal outcomes, even without another filesystem event.
Retain release outcomes across an in-flight deferral check so the queue cannot lose its only wake-up.

Existing compute item chunks release their SQL transactions, but retain the outer writer lease for the full message.
Do not describe those chunks as fairness between writers.
Changing compute-message lease granularity is outside this first design; measure its remaining effect on business-write latency.
Transport heartbeat remains independent while that lease is held.

## 8. Delivery sequence

These are design-level lots, not a command-by-command implementation plan.

1. **Connection protection:** dedicated executor, `/ws/` heartbeat adapter, lifecycle tests, minimal timing diagnostics.
2. **Computed facts:** schema migration, shared extraction, normal compute integration, indexed resolvers, safe outdated-session fallback.
3. **Live fairness:** bounded transactional slices, coalesced scheduling, cache rollback handling, cross-slice regression tests.
4. **Integration validation:** large-history replay, concurrent small-session progress, reconnect and mobile visibility scenarios.

Each lot keeps existing linkage semantics and can be validated independently.
The facts lot is incomplete until both batch and live paths agree for each affected provider.
Schema deployment uses the project's normal migration mechanism.
The user controls running-instance restart and migration; this design stage performs neither.

## 9. Validation and observability

### Deterministic automated checks

- Block the heavy executor with a latch; a new authenticated connection still completes lightweight initialization.
- Hold the write lock; send `session_viewed`, then ping; receive pong before releasing the lock.
- Block snapshot work after accept; receive pong without running snapshot handlers concurrently.
- Reject authorization; receive no pong, including after the temporary `auth_failure` accept.
- Preserve command order, origin rejection, session middleware behavior, terminal routes, and share routes.
- Verify pong encoding reaches the existing frontend visibility probe.
- Saturate each FIFO limit; verify bounded retention, backpressure, and no silent command loss.
- Close or fail each side; verify no late pong, reader leak, or hung consumer.
- With the real Channels consumer, verify channel-receive tasks and updates-group membership disappear on every exit path.
- Cancel an admitted database job; retain the write lock until the real write completes.
- Compare full compute and live replay across slice boundaries for both providers.
- Cover reused IDs, absent owners, invisible control calls, late process announcements, and orphan target matching.
- Reject stale compute results without publishing facts or current version; test rollback and retry.
- Cancel an admitted final apply; allow atomic successful publication, while preserving writer-lock drainage.
- Replace a Codex rollout; verify old facts cannot survive as authoritative evidence.
- For current-version lookup, assert indexed SQL shape and no historical `content LIKE` fallback.
- With a large and small ready session, verify both advance before the large backlog drains.
- Cover split UTF-8, incomplete tails, blank lines, malformed complete records, same-mtime appends, deletion, and truncation.
- Fail after borrowing original-file enrichment; retry without losing full diff contents.
- Replace the cache entry between borrow and rollback; the old source record retains its reservation and the newer value remains available.
- Exceed the slice time target during the second pass; commit all selected records without losing speculative state.
- Fail a migration without replay, or release it during a deferral check; settle the original path waiters.
- Continuously append beyond a requested path watermark; let that finite waiter finish while later backlog continues.
- Verify bounded message-ID lookup and exact aggregates across live and partial compute transactions, including parent/global buckets.

Use synchronization primitives to test independence, not fragile millisecond assertions in unit tests.
Build on existing compute replay, compute-apply, writer-lock, origin, and provider WebSocket tests.
Run the relevant Python and frontend suites when implementation exists; documentation-only work needs no product test run.

### Performance and operational validation

Use a disposable synthetic/replay dataset near 150,000 lines and 1.5 GB, including reused identifiers and missing matches.
Never benchmark by writing to the running instance's production database.
Record warm and cold lookup behavior and backlog completion, separately from transport latency.

On a controlled localhost run, target p95 authenticated pong latency below one second during heavy sync.
Report the machine, workload, sample count, and maximum latency; this is an experiment target, not a scheduling guarantee.
Indexed owner resolution should depend on matching facts, not unrelated JSON payload volume.
Verify query plans and workload scaling, rather than requiring a single absolute query duration.

Add low-volume diagnostics for slow slices and final apply: provider, session ID, lines, bytes, elapsed time, and backlog.
Separate write-lock wait, executor wait, and synchronous execution time where boundaries permit measurement.
Record handshake duration and unexpected transport termination without logging message contents or emitting every heartbeat.
These measurements must distinguish a responsive connection from delayed session data.

## 10. Remaining limits

Outdated sessions can remain expensive until normal compute succeeds at a stable revision.
Large individual lines and absent legacy matches can exceed slice time targets.
Final compute application can still delay business writes while its writer lease is held.
Python CPU contention, event-loop blocking elsewhere, network stalls, and saturated clients remain possible failure sources.

The design removes the demonstrated shared-executor coupling and repeated indexed-history scans after reconstruction.
Validation must still confirm how much of the observed incident rate those mechanisms explain.

## 11. Implementation reference map

Paths below are relative to the repository root. Line numbers refer to the investigated baseline.

| Area | Source |
| --- | --- |
| WS authentication and initial snapshots | `src/twicc/asgi.py`, `WSConsumer.connect`, line 465 |
| Existing ping dispatch and error swallowing | `src/twicc/asgi.py`, `receive_json` and `send_json`, lines 776 and 938 |
| Handler waiting for writer admission | `src/twicc/asgi.py`, `_handle_session_viewed`, line 2141 |
| Route and middleware boundaries | `src/twicc/asgi.py`, lines 2372–2428 |
| Visibility probe and heartbeat configuration | `frontend/src/composables/useWebSocket.js`, lines 122 and 1006 |
| Writer lifecycle and cancellation contract | `src/twicc/providers/db_writer.py`, `stop_db_writer` and `run_under_db_write_lock` |
| Compute application chunks | `src/twicc/providers/db_writer.py`, `_apply_compute_items_in_chunks`, line 1977 |
| Compute extraction and atomic application | `src/twicc/providers/compute_base.py`, `compute_session_metadata` and `apply_session_complete` |
| Live ingestion transaction | `src/twicc/providers/compute_base.py`, `sync_session_items_from_file`, line 3633 |
| Watcher scheduling and callback lock | `src/twicc/providers/sessions_watcher.py`, `sync_and_broadcast`, `_process_parsed_session_change`, and `process_path` |
| Codex lookup family | `src/twicc/providers/codex/compute.py`, `_lookup_task_started_line`, `_lookup_tool_call`, and associated `_lookup_*` helpers |
| Rollout replacement invalidation | `src/twicc/providers/codex/rollout_migration.py` |
| Provider compute versions | `src/twicc/settings.py`, lines 412–413 |

Review findings and their dispositions are recorded in the companion `2026-09-29-websocket-sync-responsiveness-review.md`.
Plan adversarial-review clarifications are recorded in [the plan review](../plans/2026-09-29-websocket-sync-responsiveness-review.md).
