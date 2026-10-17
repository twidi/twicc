# Compute publication and content maintenance

Date: 2026-10-16.
Status: adversarially reviewed in two rounds by three independent reviewers; implemented on the feature branch.
Implementation evidence: [Validation report](2026-10-16-compute-publication-and-content-maintenance-validation.md).
Source baseline: `feature/content-storage-compression`, commit `307cd0ba`.

## 1. Outcome

Keep foreground writes and the asyncio event loop available during background compute and content maintenance.
Remove repeated lifecycle work, fixed writer dispatch waits, repeated storage scans, and repeated maintenance metadata reads.
Split history-fact publication into independently committed slices.

Foreground work retains precedence at every background publication boundary.
A background preparation never reserves the writer while it waits or performs CPU work.
The writer retains ownership until its synchronous publication worker actually finishes, including after caller cancellation.

This specification changes five mechanisms in the existing content-storage implementation.
It supplements these documents where their publication contracts overlap:

- [Content storage and compression](2026-10-11-content-storage-and-compression.md).
- [Content writer priority](2026-10-12-content-writer-priority-design.md).
- [Content and tree aggregate optimization](2026-10-13-content-and-tree-aggregate-optimization-spec.md).
- [Root compute and unified statistics](2026-10-14-root-compute-and-unified-statistics-spec.md).
- [Compute selection and content work](2026-10-15-compute-selection-and-content-work-spec.md).

## 2. Evidence and performance contract

### 2.1 Existing measurements

The reference run starts on 2026-10-15 at 09:34:22 Europe/Paris.
Its source compute finishes at 13:22:42, after approximately 3 hours 48 minutes.

The run records 128,722 compute writer holds.
Their p50 is 19 ms, p95 is 108.7 ms, and p99 is 224.7 ms.
The maximum is 26,878.5 ms. There are 28 holds above one second.

| Mechanism | Observed evidence | Interpretation |
|---|---|---|
| Stopped-agent lifecycle | A final hold of 26.88 s includes 23.23 s in stopped-agent processing | Repeated lifecycle work dominates this outlier |
| Writer dispatch | Median preparation-to-publication gaps reach approximately 48–51 ms in several phases | The 50 ms idle poll adds repeated latency |
| Final readiness | Approximately 2.23 s for 145,950 items and 2.62 s for 186,496 items | Full-session checks remain expensive under writer ownership |
| Maintenance publication | Isolated 100-content publication takes 476–492 ms, with 800 SELECT and 100 UPDATE statements | Publication repeats metadata and content reads |
| History facts | Publishing 72,394 facts takes approximately 1.57 s in the real run | One complete replacement creates a long final hold |

An isolated lifecycle benchmark uses 612 events for 407 distinct agents.
Grouping reduces an unchanged-value case from 3,656 ms to 41 ms.
It reduces a changed-value case from 5,807 ms to 319 ms.
The item and content tables are empty in this benchmark.

An isolated maintenance benchmark uses 100 contents and approximately 333 KB of raw text.
Transaction-local metadata reuse reduces publication to approximately 140 ms and 206 SELECT statements.
Content-row prefetch is not included in that result.

These isolated benchmarks use SQLite in memory.
They exclude disk reads, WAL behavior, and durable commit costs.
They establish repeated work, rather than production latency guarantees.

The sum of measured preparation-to-publication gaps approaches 71 minutes.
This includes foreground interference, resource admission, and scheduling.
It is not a promise of 71 minutes saved.

### 2.2 Acceptance measurements

Measure writer ownership, SQL transaction duration, admission wait, preparation time, and dispatch wait separately.
Report p50, p95, p99, maximum, and counts above 100, 250, 500, and 1,000 ms.
Measure foreground request completion and watcher ingestion latency under the same workload.

Each changed publication phase must have bounded input and an explicit foreground scheduling boundary.
Target sub-100 ms normal publication slices on the reference machine.
A hold above 250 ms requires investigation before acceptance.
A repeatable hold of one second in a changed phase fails acceptance.

These are measured acceptance targets, not real-time guarantees for arbitrary disks or individual records.
An individual oversized record must still make progress and must appear explicitly in benchmark results.
Budget selection must consider bytes and SQL cost, rather than row count alone.

## 3. Shared execution contract

The existing compute application owns its worker, source observation, TEMP tables, and phase cursors until terminal cleanup.
Extend that operation instead of introducing a second publication framework.

Each background publication follows this sequence:

1. Prepare bounded work outside writer ownership.
2. Obtain the existing background admission and writer resources.
3. Acquire SQLite write ownership before authoritative reads that precede writes.
4. Validate the relevant source and publication observations.
5. Commit one bounded slice.
6. Advance operation-local state only after the commit succeeds.
7. Release resources and yield to the event loop before another slice.

Foreground work can run between slices.
Background work cannot preempt a transaction already executing.
Cancellation does not release resources while its synchronous worker still runs.

Source replacement, append, or ancestry changes use the existing superseded-result and retry paths.
A failed content candidate in maintenance does not invalidate unrelated candidates.
No new whole-batch retry protocol is needed for these optimizations.

## 4. Stopped-agent lifecycle publication

### 4.1 Current path

`BaseSessionCompute.compute_session` produces `agent_stopped` entries from recovered completion evidence and tool-result evidence.
`_apply_session_complete_final` currently applies each entry separately.
Each call passes through `apply_compute_lifecycle`, `update_lifecycle`, and `lifecycle_change`.

The chain reloads metadata and ancestry, captures plaintext state, performs an UPDATE, and compares lifecycle fields afterward.
Even an unchanged value causes the initial reads and UPDATE.
Repeated entries for one agent repeat this entire chain.

### 4.2 Required behavior

Treat stopped-agent entries as inputs to one lifecycle projection for the compute operation.
Retain the underlying historical event records and their normal occurrence semantics.

For each agent, select the greatest applicable `stopped_at` timestamp.
Fetch the agents' current lifecycle metadata in bounded groups.
Skip an agent when both stored values already equal the proposed `last_stopped_at` and `last_updated_at`.

Preserve this authoritative SQL predicate:

> Apply the historical stop only when current `last_updated_at` is NULL or does not exceed `stopped_at`.

This predicate prevents an old stop from overwriting a later live resume.
Recheck it in every publication slice, even after a preparation-time comparison.

Use parameterized individual UPDATE statements through `executemany` for accepted mutations.
Group before/after metadata and native-root resolution for the slice.
Invalidate each affected root once per slice and schedule committed cold-cache invalidation for actual changes only.

No-op entries must not update timestamps, increment epochs, schedule promotions, or invalidate caches.
Successful mutations must update the operation's expected lifecycle state and root epoch after commit.
Own committed changes must not make the operation supersede itself.
Each slice compares the expected epoch before its own mutation.
It may adopt the resulting epoch only after those guarded writes commit.
It must never absorb an external change by first replacing its expected epoch with the current database value.

### 4.3 Plaintext readiness and slicing

A root's plaintext certificate covers its own references, not every subagent's references.
Batching must preserve this distinction.

Lifecycle mutation must not perform an unbounded item scan for each changed agent under the writer.
Use valid existing preservation evidence when available.
When readiness is unknown, retain the existing targeted promotion obligation and perform its inspection through bounded work.
Do not publish a plaintext certificate merely because inspection was deferred.

Publish stopped-agent lifecycle changes before the operation's final storage validation completes.
If those changes alter effective placement requirements, reconciliation must satisfy the resulting requirements before completion.
Root-only readiness must not become a reason to restore every child's payload eagerly.

The agent loop becomes a resumable operation phase when the accepted changes exceed one publication budget.
The final transaction must not retain a second per-event lifecycle loop.
Already committed lifecycle slices remain valid if later source publication becomes superseded.
They retain the historical projection admitted by their source guard, like other previously committed compute slices.
The monotonic SQL predicate protects later live activity; it does not prove historical evidence survives every possible source replacement.
Source supersession does not retrospectively roll back these committed lifecycle projections.

### 4.4 Validation

Test repeated timestamps, repeated agents, unchanged values, and newer live activity.
Test roots shared by several agents and native ancestry at multiple depths.
Test rollback of a slice and cancellation while its worker runs.
Test that a real cold-policy change retains its promotion and invalidation effects.
Test interruption after a child-stop slice commits while the root remains outdated.
Restart must replay the root projection without recomputing an already current child.
The committed stop becomes a no-op, and root completion still publishes complete facts and readiness.

Benchmark 10, 100, 400, and 1,000 agents with unchanged, changed, and mixed values.
Include populated item/content tables and foreground writes between slices.
Measure SQL count and maximum hold, not only cumulative elapsed time.

## 5. Event-driven DB writer dispatch

### 5.1 Current path and additional finding

`_db_writer_loop_body` sleeps for 50 ms when `_drain_one` reports no immediate progress.
`_advance_pending_compute_body` reports no progress while its preparation task runs.
The task's completion does not currently wake the dispatch loop.

Compute IPC already uses `ComputeResultStream` and one receiver thread per run.
The receiver decodes complete frames and inserts them into a process-local `ComputeIngressQueue`.
Therefore, notifications can originate after successful ingress insertion.
A new IPC protocol or permanent IPC polling fallback is unnecessary.

### 5.2 Required behavior

Use one writer-lifetime notification mechanism for changes that can enable dispatch.
The notification carries no jobs and no authoritative queue state.
The queues, preparation tasks, coordinator, and stop state remain authoritative.

Notify after these changes:

- A successful enqueue into foreground, background-initialization, initial-sync, or compute ingress queues.
- Completion, failure, or cancellation of a pending preparation task.
- Completion of terminal notifications needed to settle the pending operation.
- A coordinator transition that can permit previously refused background admission.
- A stop or drain request.

Async producers notify on the owning loop.
Thread producers schedule notification with `loop.call_soon_threadsafe`.
Notify only after an item is successfully visible in its queue.
Preserve bounded queues and producer backpressure.
Cover direct `put` and `put_nowait` calls on the exposed queue APIs, including initial-sync producers.
Notification from the receiver thread alone does not cover every supported producer.

The loop clears its notification before checking authoritative work.
After the check, it waits only when no immediate progress is possible.
A notification arriving during the check must remain visible to the wait.

Queued but inadmissible background work must not create an active loop.
Coordinator release and foreground-demand release must wake the dispatcher when they permit another attempt.
The coordinator's existing drain-idle event does not represent every such transition.

Retain a cooperative yield after successful dispatch.
An exception backoff may remain distinct from normal idle waiting.
Queue-capacity waits and receiver-thread stop checks retain their existing cancellation and shutdown semantics.

Bind notifications to the writer lifetime that creates them.
Late callbacks from an old stream must not wake or enqueue work into a replacement writer instance.
Stop drains accepted messages after producers stop, using the same notifications rather than a 50 ms drain poll.
During drain, an already-set stop event must not cause repeated immediate wakeups while a worker or admission is pending.
Wait for the actual progress notification until accepted work settles.

### 5.3 Validation

Test enqueue and task completion immediately before, during, and after the idle-state check.
Use deterministic barriers to test lost-wakeup races rather than timing-dependent sleeps.
Test foreground arrival while preparation runs and while background admission is refused.
Test transport failure, cancellation, full queues, shutdown drain, and writer restart.
Test direct `put` and `put_nowait` on each exposed ingress queue.
Test stop with blocked preparation and occupied admission; drain must wait without spinning.
Verify zero busy polling when all remaining background work lacks admission.

Benchmark 10,000 short slices, idle periods, and concurrent foreground writes.
Compare preparation-to-publication latency, foreground latency, CPU, and dispatch wake counts.
The result must remove the repeated 50 ms latency mode without increasing idle CPU materially.

## 6. Progressive final storage validation

### 6.1 Current path

`validate_final_compute_operation` checks the source and searches for compressed references when plaintext is required.
`finish_storage_result` separately searches for incomplete placement.
It then calls a full plaintext metadata check where required.
`_finish_plaintext_root_storage` performs its own complete probe before publishing a certificate.

These probes inspect the same session's current and original references in several passes.
Their cost grows with session size and can retain the writer for seconds.

### 6.2 Selected design

Separate exhaustive metadata validation from the final metadata commit.
Validate placement and required plaintext together in bounded line-number pages.
Reuse equivalent checks already performed during reconciliation where they establish the same complete coverage.
Otherwise, use a dedicated bounded validation phase.

For each page, check:

- Empty legacy content and a present current reference.
- Present content rows for both occupied reference slots.
- Valid storage metadata for the required representation.
- Plain codec and no dictionary on both references when plaintext is required.

Inspect metadata only. Validation must not decompress payloads or recompute payload hashes.
The page is accepted under authoritative source and root checks.
Progress covers both slots of every item through the observed upper source line.

Retain only operation-local completion evidence and the existing source/root observations.
The evidence records the validated scope and representation requirement.
It is advanced after commit and discarded when the operation terminates.
It cannot authorize another session, another source revision, or a later independent operation.

The final transaction checks source identity, boundaries, ancestry, expected root state, effective policy, and completed phases.
It must not repeat a complete item/content scan.
It publishes storage readiness and, where applicable, the root's own plaintext certificate from the accepted operation evidence.
The private completion path accepts only its live owning operation, with no unpublished batch and complete validation coverage.
An arbitrary caller-supplied Boolean or source message cannot replace that operation state.
Independent public verification helpers retain their own authoritative validation contract.

### 6.3 Mutation coverage required for correctness

An unchanged root epoch alone does not establish unchanged representation.
`publish_representation` can change a shared content's codec without directly incrementing every owner's root epoch.
The following coverage makes progressive validation safe:

| Concurrent action | Required protection |
|---|---|
| Watcher appends source items | Offset/line observation rejects stale completion |
| Source replacement or history rebuild | History epoch and source identity reject stale completion |
| Reference assignment or ownership binding | Existing reference service invalidates the relevant root state; publication rechecks observations |
| Lifecycle or native ancestry mutation | Existing lifecycle invalidation and ancestry checks reject incompatible evidence |
| Runtime activity changes | Re-evaluate activity under content admission before final publication |
| Persistent decompression | Plaintext promotion preserves the placement/plaintext property already validated |
| Ingestion reuses existing content | `intern_contents` preserves its stored representation; reference changes remain guarded |
| Ingestion creates compressed content | It creates a new identity and binds it through the guarded reference path |
| Another compute compresses existing content | Only the current pending compute application publishes its slices; there is no concurrent compute application |
| Periodic maintenance compresses existing content | Exclude it for the full active or queued compute-application lifetime |

Enforce the existing periodic-compression gate at actual publication admission.
Provider selection completion alone must not reopen compression while an accepted application still has pending slices.
Use the existing pending application/ingress state in addition to the compute-work gate where needed.
A prepared maintenance candidate must wait and be revalidated after the compute application finishes.
Apply this gate to the complete periodic representation publication, including no-gain metadata updates.
Keep it closed until terminal worker drain and cleanup release the accepted application.
A maintenance slice already admitted before compute work arrives may finish under its existing lease.
Compute validation starts only after that slice releases its resources; the closed gate prevents new periodic publication admission.

Every transition that can reopen the complete gate must notify its maintenance waiters.
This includes applied, superseded, failed, and abandoned applications, and shutdown-drain completion.
The provider pending flag may already be false when the final application is released.
Therefore, a later `compute_work.changed` notification cannot be assumed.
Evaluate the complete gate and its notifications on the owning event loop, using the existing application/ingress state.

This gate reserves no writer resource between compute slices.
Foreground writes and required plaintext promotions continue normally.
No process-wide content mutation counter is added.

Each mutation path that can compress an existing identity must satisfy the table above.
The current paths are compute reconciliation and periodic representation publication.
A future such path must preserve the same exclusion or invalidate the applicable operation evidence.

### 6.4 Policy changes and own lifecycle mutations

Determine plaintext requirements from both observed and proposed lifecycle fields, plus current runtime activity.
Apply stopped-agent changes before accepting final validation completion.
Own lifecycle updates must settle the operation's expected root state without concealing intervening external writes.

Evidence validated for plaintext can satisfy a later cold requirement.
Evidence that permits compressed content cannot satisfy a later plaintext requirement.
If the requirement becomes stricter, previous pages accepted under the weaker requirement do not count as plaintext coverage.
Before final readiness, repair where needed and validate those pages under the stronger requirement through bounded work.
An incompatible source or lifecycle observation follows the existing superseded-result path.
A compatible operation that needs plaintext must finish its bounded reconciliation and validation before publishing readiness.

Wall-clock aging can permit compression without an epoch change.
Re-evaluate policy at publication; never turn an old plaintext certificate into current evidence using the epoch alone.
Compression exclusion during the operation prevents aging from invalidating already accepted plaintext pages.

### 6.5 Rollout and validation

First unify duplicate predicates so placement and plaintext checks have one definition.
Then introduce bounded validation and its private completion path.
Remove final whole-session scans only when concurrency tests cover the complete mutation table.

Test both reference slots, NULL metadata, missing content, legacy content, empty sessions, and page boundaries.
Test source append, reference changes, owner changes, resume, and ancestry changes between pages.
Test a cold threshold crossed during validation and a pending lifecycle change from cold to hot.
Test a periodic compression candidate prepared before compute admission and published afterward.
Test this race with a content shared between two different roots.
Test cancellation with a publication worker still running; compression exclusion must last until its drain completes.
Test a plaintext requirement introduced between pages; earlier cold-validated pages must be checked again.
Test a concurrent plaintext promotion and distinct root/child coverage.
Test an external root-epoch change between validation pages followed by an operation-owned lifecycle mutation.
The own mutation must reject the stale expected epoch instead of absorbing the external change.
Test that the last application release wakes maintenance when all provider pending flags are already false.
Test that source readiness and root-only certificates never escape an aborted final commit.

Benchmark 1,000, 10,000, 100,000, and 200,000 items, with cold and warm SQLite page caches.
Report cumulative validation time and foreground latency as well as each writer hold.
The optimization may trade a longer cumulative validation time for shorter writer holds.

## 7. Maintenance publication metadata reuse

### 7.1 Current path

Preparation already shares lifecycle reads through the committed cold cache.
Publication calls `_proof_matches`, which reconstructs owner/root metadata for every content.
It then calls `publish_representation`, which reloads that content and repeats cold-owner checks.

### 7.2 Required behavior

Build an authoritative publication context scoped to one admitted writer transaction.
Fetch candidate content rows once, with the existing row and byte limits.
Collect the union of owner IDs and resolve their metadata and native roots in bounded groups.
Reuse those rows and decisions across candidates in the transaction.

Use one activity snapshot protected by the existing content-admission contract.
The committed lifecycle cache may supply valid metadata through its existing invalidation rules.
Publication must still observe committed changes since preparation.
Do not treat preparation-time cold Booleans as authoritative publication decisions.

Each candidate retains its current checks:

- Actual owner membership equals the prepared observation.
- Applicable owner and root readiness still matches the required contract.
- Content identity, observed codec, dictionary, and required payload observation still match.
- Every owner permits compression for a compressed result.
- Encoded output metadata and dictionary policy remain valid.

Pass the already loaded content row and shared publication decisions to the write helper.
Remove the helper's duplicate content SELECT and per-content metadata reconstruction.
Do not reload or hash unchanged payloads repeatedly to construct the same metadata decision.
Retain exact observation comparisons that protect a prepared payload against an intervening representation change.

Skip an invalid candidate and continue with independent valid candidates.
Discard all publication-context data at transaction exit, including rollback.
Reuse across a later transaction requires a fresh authoritative context.

### 7.3 Validation

Extend maintenance-read tests to cover publication, not only preparation.
Verify bounded metadata query counts for identical owners, overlapping owners, and distinct owners.
Test a hot owner added after preparation and a committed resume before publication.
Test changed representation, unknown ancestry, rollback, and one invalid candidate among valid candidates.

Benchmark 100-content batches with approximately 333 KB, 8 MB, and oversized individual payloads.
Cover both compression and persistent plaintext publication where the helpers are shared.
Measure on-disk SELECT, UPDATE, commit, total hold, and foreground latency separately.
Transaction-local reuse must retain the isolated query-count gain with production guards enabled.

## 8. Bounded history-fact publication

### 8.1 Current contract

`prepare_compute_history_facts` validates and adapts facts into a connection-owned TEMP table.
It prepares pages of 128 outside the main writer.
`publish_compute_history_facts` replaces all durable facts inside the final transaction.

`history_facts_are_current` uses the provider's current compute version as the readiness gate.
`iter_resolver_items` reads raw source pages exclusively for outdated sessions.
Current production fact queries are concentrated in `history_facts.py`.
Codex rollout replacement also deletes facts as part of its source-replacement path.

### 8.2 Selected design

Keep TEMP preparation and its validation rules.
Add resumable deletion and insertion phases for durable facts before the final metadata commit.
The session remains outdated throughout both phases.
Verify this precondition before the first durable fact mutation and preserve it for subsequent slices.
The synchronous compatibility API can also receive a current session today.
Such a compatibility call retains its existing atomic fact-replacement path and source guards.
The progressive publication entry point rejects a current session before any partial durable fact mutation.
It must never expose partial facts under a current version or clear that version merely to enter this protocol.
Normal background dispatch uses progressive publication only for its outdated source rebuilds.

Delete old facts in bounded primary-key pages restricted to the source session.
Insert staged facts in bounded source-order pages.
Use row and serialized-data byte budgets for both operations.
Retain the existing first-valid-duplicate behavior and unique source-tuple semantics.

Check the source observation before each durable slice.
Advance the deletion/insertion cursor only after its transaction commits.
Use the last selected stage position, not inserted-row count, to advance across ignored duplicates.
The TEMP table stays on its owning connection and worker until cleanup.

After all facts and storage validation complete, publish normal final metadata and current `compute_version`.
Root completion and its statistics enqueue retain their existing final-commit relationship.
The progressive path's final transaction contains neither a full facts DELETE nor a full facts INSERT.

An empty fact set still completes the deletion phase and explicitly completes insertion.
Missing fact input remains an error and cannot publish a current compute version.

### 8.3 Partial publication and live writes

Partial durable facts are not readable as a complete history while the session is outdated.
Every production fact reader must use the readiness-aware resolver or enforce the same gate at operation entry.
Low-level fact iterators must be internal to this guarded contract.

Watcher's new facts remain valid writes, but they do not make an outdated rebuilt session current.
If watcher ingestion advances the source between slices, the old compute operation becomes superseded.
It must not publish its version or report completed source readiness.
The next compute reconstructs the complete fact set from the new source observation.

Accepted historical stop slices and partially replaced facts need not be rolled back across earlier commits.
Historical stop slices follow the admitted-projection and live-activity protection contract in section 4.3.
Partial facts remain hidden from complete-history readers through the outdated fact-reader gate.
On interruption, close TEMP resources after the worker finishes.
Do not perform an unbounded durable-fact cleanup as part of cancellation.

After restart, the existing outdated-session selection reconstructs the fact set.
The first deletion phase removes any incomplete previous attempt before inserting the new complete stage.
No persistent publication-generation table is required.

### 8.4 Validation

Retain complete-fact/version tests and adapt atomicity expectations to the new readiness contract.
Earlier durable slices may remain after a later failure; current-version publication may not.

Test partial deletion, partial insertion, empty facts, ignored duplicates, and missing fact input.
Test interrupted operations, restart reconstruction, source supersession, and watcher facts between slices.
Verify raw-history fallback during every partial phase for both providers.
Test worker connection affinity, rollback cursor behavior, and cancellation drain.
Test source replacement so old staged facts cannot become current after rollout replacement.
Test a current session passed through the compatibility API and a version change before a later fact slice.
No partial mutation may occur while that session is current.

Benchmark 1,000, 10,000, 40,000, and 75,000 facts with the real table indexes.
Measure DELETE, INSERT, and commits separately on an isolated WAL database.
Compare candidate row limits of 128, 256, and 1,000 with byte limits.
Inject foreground writes between slices and report their completion latency.

## 9. Integration and delivery sequence

Implement and benchmark these units separately so their effects remain attributable:

1. Lifecycle deduplication, no-op elimination, grouped reads, and bounded publication.
2. Transaction-local maintenance metadata and content-row reuse.
3. Writer notifications, including receiver-thread ingress and coordinator availability.
4. Bounded history-fact deletion/insertion with final version publication.
5. Unified, progressive storage validation with the compression-exclusion contract.

The detailed implementation plan must identify shared changes to the operation phase machine before parallel work begins.
Lifecycle slices precede accepted final validation.
Facts and storage-validation phases both finish before source completion is published.
Keep preparation, publication, and terminal cleanup on their existing resource owners.

Use existing schemas, source boundaries, storage epochs, and compute-version readiness.
The design requires no data migration or new durable publication state.

Run deterministic concurrency tests first, then on-disk isolated benchmarks, then an authorized full worktree compute run.
Keep the same SQLite durability settings for before/after measurements.
Use the same source snapshot for full-run comparisons.
Record phase names and budgets with each result.

Review measured maximum holds after the changes.
If another final subphase becomes dominant, report its evidence separately before extending this specification.

## 10. Remaining empirical decisions

The behavior and safety contracts above are fixed for planning.
Implementation benchmarks select the exact slice budgets within those contracts.

The following gains still require end-to-end measurement:

- Dispatch time recovered from removing the normal 50 ms poll.
- Lifecycle hold reduction with populated item and content tables.
- Additional maintenance gain from eliminating duplicate content-row loads.
- On-disk deletion and insertion budgets for large fact sets.
- Final validation throughput and foreground latency under cold page-cache conditions.

Report these as measurements, not estimates derived from in-memory speedups.

## 11. Adversarial review

Three independent reviewers examine lifecycle/facts, progressive storage safety, and dispatch/maintenance.
Two review rounds check the design against the source baseline and the relevant existing tests.
Review remains at specification level.

The revised contracts address these findings:

- Reject progressive fact mutation for current sessions; retain the atomic compatibility path.
- Notify maintenance when the complete compute gate reopens, including application cleanup without a provider-state change.
- Cover direct queue producers and avoid a shutdown drain that spins on an already-set stop event.
- Check expected epochs before own mutations and never absorb an external mutation into accepted validation evidence.
- Revalidate pages when representation requirements become stricter.
- Preserve source-projection semantics and root-only certificate scope across partial publication and interruption.

The second round identifies no remaining design blocker.
Exact budgets and on-disk performance remain the empirical decisions listed in section 10.
