# Root compute and unified statistics calculator

Date: 2026-10-14.
Status: adversarially reviewed; ready for user review before implementation planning.
Scope: behavior, ownership, recovery, and acceptance criteria. No implementation is authorized by this document.

## 1. Outcome and precedence

Process recent native root trees together. Centralize derived costs and daily/weekly activity in one calculator.
Remove continuous repair discovery and repeated whole-journal scans.
Keep foreground writes and the main asyncio loop responsive.

This document supersedes the compute selection and aggregate mechanisms in the
[previous optimization specification](2026-10-13-content-and-tree-aggregate-optimization-spec.md).
Its previous implementation plan is not the plan for this replacement.

No compatibility bridge is required for the experimental journal or its worktree database.
Normal application upgrades still require valid schema migrations and persistent compute invalidation.
Schema changes must not introduce a long historical data migration at startup.

## 2. Fixed scope

Included:

- Root-based background compute ordering and completion signals.
- A persistent append-only work queue and an event-driven consumer.
- Session own costs, subtree costs, project costs, and workflow costs.
- Daily and weekly activity, including their message and session counts.
- Selective own-cost recalculation using an existing session timestamp.
- Replacement of dispersed aggregate writes and associated frontend refresh behavior.

## 3. Source compute and native trees

Background compute operates on data already present in the database.
Failed imports outside the database do not block compute or statistics.
Do not gate either mechanism on `initial_sync_succeeded`, including another provider's inventory result.

Select the most recently modified eligible native root, then process its complete database tree.
Do not independently rank that root's old subagents among unrelated roots.
Keep the existing source stability delay, retry behavior, and bounded worker admission.
A source changed during compute remains eligible for automatic retry. No user restart is required.

Process known subagent levels deepest first. Within one level, use descending source `mtime`.
Process the root last. Skip member sources whose compute version is already current.
Finish application of required descendants before final root application.
A failed or superseded required member must not produce a false successful tree-completion signal.
Retry blocked trees without preventing other eligible roots from progressing.

Native membership follows the existing root association in `Session.parent_session_id`.
The launcher hierarchy follows `AgentLink.session_id -> AgentLink.agent_id`.
These relationships have different roles. Normal imports flatten native membership to the root.
Independent sessions linked through `spawned_by` are not native subagents.

New imports can discover launcher links during source compute.
Missing undiscovered links must not prevent first-time source preparation.
The deepest-first cost calculation uses the hierarchy available after metadata application.
Valid hierarchy changes enqueue affected native roots, even when their own item costs do not change.
An unlinked native member still contributes its own cost once to its native root.
Do not lose a database member because its launcher link is unavailable.

## 4. Persistent work queue

Use an append-only database queue. Each entry identifies one native root requiring derived recalculation.
Keep multiple entries for the same root. Deduplicate roots inside the consumer's captured batch.
An absent queued root is an absent calculation input. Continue processing surviving captured roots.
Preserve the captured acknowledgement boundary and retry surviving publication failures.

The entry ID must use Django `BigAutoField`, backed by a SQLite signed 64-bit integer.
IDs must increase and must not reuse deleted committed entry IDs.
The available positive range is 1 through 9,223,372,036,854,775,807.
Do not use a 32-bit counter or a frontend JavaScript number for queue boundaries.
Queue IDs and boundaries remain backend-internal.
Do not periodically reset the sequence.

Each entry stores the root identity and `force_cost_session_ids`, a JSON list of native member IDs requiring a full own-cost sum.
Live entries use an empty list. Capture unions these lists across rows inside the captured ID boundary.
The queue does not store item batches or historical proofs.
Do not add journal incarnation, per-entry generation, old-parent inventories, or per-item obligations.
Resolve a subagent enqueue to its native root.

Append work in the same database transaction as the relevant committed source change.
Rollbacks must not leave queue work for rolled-back mutations.
Emit the wake event only after commit.
Background compute enqueues once when the entire selected root tree has finished application.
Do not enqueue for each background item, metadata slice, or completed subagent.
Final tree completion and its root enqueue commit atomically.
Affected member publications set their own-cost markers to NULL without adding separate background queue entries.
The compute worker collects recalculated member IDs in a process-local set for each native root.
Final tree completion passes this set to the durable queue entry. The set is released after the writer confirms successful final tree completion.
A refused final attempt retains the set for its retry.

For watcher ingestion, enqueue at most once per affected root in each committed ingestion batch.
Collect relevant changes across the batch, then append work with its source transaction.
Fifteen ingested lines with several cost contributions produce one entry for that root, not fifteen entries.
Multiple relevant field changes in one batch do not create additional entries for the same root.

Enqueue when derived outputs can change:

- New item cost contributions or background cost recomputation.
- Daily/weekly user-message or session-count contributions, including cost-free items.
- Relevant hide/unhide changes.
- Launcher links that change subtree attribution.
- Relevant workflow metadata or phase attribution changes.

An arbitrary cost-free debug line does not require work unless another aggregate contribution changes.
Preserve the current aggregate eligibility rules.

## 5. One event-driven consumer

One consumer serves all enabled providers. Only one derived calculation batch runs at a time.
All runtime authors of the included derived fields must use this consumer.
Watchers and compute write source metadata and queue work, not derived cost or activity replacements or deltas.

At startup, inspect existing queue work without requiring a wake signal.
After draining available work, wait on an `asyncio.Event`.
Signals indicate that work may exist; they do not carry root identity or count entries.
Coalesced signals are safe because the database retains every committed entry.
An enqueue during calculation or immediately before waiting must not lose a wakeup.
Clear the event before draining, rather than after draining.

Do not repeatedly scan an unchanged queue while idle.
Do not periodically recheck tree readiness. The consumer does not need a fully computed tree.
It calculates from the database as it exists. Final compute publication queues another recalculation.
Intermediate statistics can be incomplete. They must converge after source work and queued calculations finish.

Recover processing failures with bounded retry delays. Keep queue entries until their publication succeeds.
Do not require an external signal or restart to recover transient calculation failures.
Failure retries are distinct from idle polling. Report failures without silently stopping the consumer.

## 6. Batch capture and acknowledgement

Use a fixed two-second collection window before capturing each available batch.
Do not wait for a minimum number of roots. New arrivals do not extend the window.
If more work remains after publication, begin the next window.

Capture the largest committed queue ID as `batch_max_id`.
Read and deduplicate all entries with IDs at or below that boundary.
New entries above the boundary belong to a later batch, including repeated root IDs.
Do not delete queue entries by root identity.

After every output of the captured batch is successfully committed, delete entries with IDs at or below its boundary.
If the batch fails or is interrupted, retain its entries.
Retry uses exact replacements, not accumulated deltas. Repeating a partly published batch must not double contributions.

Example: batch 101 contains root A. A changes during calculation and receives entry 102.
Successful acknowledgement deletes 101, but leaves 102. The next batch updates A again.

## 7. Selective own-cost cache

Reuse `Session.last_updated_at`. It contains the timestamp of the last timestamped source item.
It is not the database write time. Do not use file `mtime` for this cache.

Add nullable `Session.items_cost_computed_through_at`.
Its meaning is the observed source timestamp covered by the published own-cost sum.
Recalculate a member's own cost when the marker is NULL, its ID is in the captured forced list,
or `last_updated_at` is newer than the marker.
If the source timestamp is unavailable, do not treat an unavailable marker as a valid cache hit.
Otherwise reuse the persisted `self_cost` without reading that member's raw item costs.

Capture the source timestamp and contributing items in a consistent database read snapshot.
Publish the captured timestamp with the corresponding own cost.
Do not use publication wall time or a later source timestamp as the cache marker.
New source timestamps must remain visible for the next queued pass.

Background compute resets the marker when it recalculates item cost metadata, including final source publication.
It also collects member IDs for the final forced queue entry.
A concurrent prepared calculation may publish an older result and overwrite a NULL marker.
The current batch still completes and acknowledges its captured boundary.
A later forced queue entry remains above that boundary and recalculates the member regardless of its marker.
Do not keep process-local modification counters, reject this batch for overlapping cost writes,
or add conditional marker writes or conflict retry delays. Unexpected publication failures retain normal retry behavior.

### Accepted interruption limitation

The user accepts the narrow interruption race where an older calculation overwrites a NULL marker,
then the application stops before final tree enqueue. The local forced-ID set is lost.
An already completed member with a non-NULL marker and no newer timestamp may retain an older own cost after restart.
Surviving NULL markers still force full sums. Do not add further recovery machinery for this accepted race.

### Accepted timestamp limitation

The user explicitly accepts missed immediate recalculations for equal or regressing source timestamps.
Database audit found equal timestamps in both providers and older timestamps in Claude records.
This acceptance is not a guarantee of strictly increasing source timestamps.
An ordinary enqueue does not override a timestamp cache hit. A forced member ID does override it.
A later advancing timestamp or background cache invalidation recalculates the complete own-cost sum.

Do not introduce a line-number cache marker, hidden revision scheme, periodic repair, or fallback full-history scan.

## 8. Hierarchical costs

Own costs use persisted `SessionItem.cost`, never content parsing or decompression.
Keep current decimal precision and NULL/zero output conventions.
Group sums for members needing recalculation. Do not rescan cached siblings' items.

Load cached and recomputed own costs and the launcher hierarchy for each affected tree.
Calculate from the deepest subagents toward the root:

- `self_cost`: sum of that session's own item costs.
- `subagents_cost`: sum of total costs of direct launcher children.
- `total_cost`: own cost plus subtree cost.

Every descendant contributes once. A subagent includes its entire subtree.
Recalculate ancestor totals from session-level values, not descendant item history.
The root includes all native members, including members without a known launcher link.
Independent spawned sessions do not contribute as native descendants.

Deduplicate projects across the batch.
Recalculate each affected project's total once from top-level session totals, after required session totals are published.
Preserve existing project eligibility and worktree presentation rules.
Do not sum subtree-inclusive totals from every node into a project total.

Centralize derived workflow cost and phase-cost publication here, after the relevant session totals.
Agent cost changes must refresh affected workflow costs even when the workflow envelope does not change.
Count each agent contribution once, including nested agents, and preserve existing phase attribution semantics.
Do not blindly sum overlapping subtree totals for a workflow.
Workflow REST and CLI responses overlay published Session costs on their agent records.
Keep the stored source envelope unchanged. Read Session totals without aggregating item history in request handlers.

## 9. Daily and weekly activity

Tree IDs identify affected dates; they do not limit the inputs of shared daily totals.
Include source item dates and root creation dates needed for session-count contributions.
Discover surviving source dates after visibility changes, then apply eligibility filters during daily calculation.
Deduplicate UTC day/project/provider keys across all captured roots.

Recalculate each distinct daily key once per batch from all applicable database contributions.
Keep current filters:

- Message counts use visible top-level user messages.
- Session counts use eligible top-level sessions on their creation day.
- Item costs include the session types and visibility currently included in activity costs.
- Global rows retain `project=NULL` and remain provider-specific.

Calculate each distinct UTC week from its daily rows, after the batch's affected days are published.
Do not repeat raw item sums for weeks.
Days untouched by the batch retain their current values. They do not need a global readiness barrier.

Deduplication does not require one large SQL statement.
Use date-range queries that preserve SQLite index use, according to implementation benchmarks.

## 10. Publication and notifications

Historical reads and subtree preparation execute outside writer ownership and outside the main asyncio thread.
Publish derived results through the existing unique writer, using bounded short transactions.
Foreground work has priority between publications. Never hold the writer across a historical aggregate read.

Ordinary live source changes during preparation enqueue a later pass.
Intermediate results are acceptable; a single consumer prevents competing aggregate writers.
Use the forced queue list and NULL-marker behavior specified in section 7.
Do not restore broad tree certificates or all-provider inventory guards.

After successful publication, send session/project updates, workflow refresh notifications, and an activity refresh signal.
Load and serialize notification payloads outside the main asyncio thread. Send WebSocket messages on the loop.
Reuse the existing workflow notification contract so open workflow views receive recalculated costs.
The frontend must refresh affected displayed statistics, including global activity.
Do not rely solely on startup's 15-second home polling.
Preserve existing external field names and payload meanings, except corrected subtree costs.

## 11. Removal and rollout

Remove `ComputeAggregateRepair`, its mutation callers, repair discovery, and `TreeAggregateScheduler` aggregate orchestration.
Remove aggregate-only proof and revision plumbing that no longer has a required caller.
Preserve content-storage guards, writer coordination, source supersession protection, and automatic compute retries.

Provide normal schema changes for the queue and cache marker.
Use persistent compute invalidation so existing users rebuild the required metadata after upgrade.
Do not move historical data in a blocking Django migration.
Do not implement compatibility with experimental worktree repair entries.
Worktree database/search reset and server startup remain explicit user operations.

## 12. Required validation

The implementation plan must validate:

1. Roots progress in recent-root order; old descendants do not wait behind unrelated recent roots.
2. Initial imports without launcher links still prepare sources and resolve hierarchical costs.
3. Known nested costs include subtrees once; native unlinked members remain counted.
4. Cost-only, cost-free activity, visibility, hierarchy, and workflow changes enqueue their required outputs.
   A watcher batch enqueues once per affected root. Background compute enqueues once per completed root tree.
5. Source writes and enqueues commit atomically; rolled-back writes do not wake a nonexistent queue entry.
6. Queue IDs are monotonic 64-bit values, including values above signed 32-bit range.
7. Repeated root entries batch correctly; enqueues during processing survive acknowledgement.
8. Startup resumes work, interrupted batches repeat safely, and transient failures retry without new signals.
9. Idle work does not poll; event timing does not lose a committed enqueue.
10. Partial source compute is accepted; final enqueue restores converged costs and activity.
11. Cached own costs avoid unchanged item scans; forced queue IDs recalculate members despite current markers.
    Overlapping background writes do not abort the current batch. The accepted interruption race is documented.
12. Equal/regressing timestamp cases retain the explicitly accepted cache behavior.
13. Daily keys deduplicate across roots; weeks derive from daily rows with existing filters and UTC boundaries.
14. Session, project, workflow, and activity views receive refreshed derived values after consumer publication.
15. Foreground writer access and asyncio responsiveness remain available on large trees and batches.

Compare published values with direct sums of stored cost metadata and explicit subtree membership.
Benchmark small, medium, large, and very large trees, repeated updates to one member, and roots sharing dates/projects.
Measure reader preparation separately from writer occupancy, including commit and acknowledgement work.
Benchmarks must cover first-pass reads and cached repeated reads. Do not equate cached results with cold-read guarantees.

## 13. Evidence and known trade-offs

The worktree cost prototype covers 499, 9,753, 59,004, and 458,192 items, plus a depth-three tree.
With warmed data and SQLite WAL/NORMAL, complete cost recalculation measured about 3 to 98 milliseconds.
The largest first-pass own-cost query measured about 5.56 seconds. It did not hold writer ownership.
Selective cached own costs address repeated scans of unchanged subagents.

Five-day cost queries measured about 38 to 52 milliseconds with indexed per-day queries.
A grouped OR query measured about 373 to 438 milliseconds because SQLite scanned the broader index.
These are prototype measurements, not service latency guarantees.

The design accepts a two-second collection delay and temporarily incomplete results during source compute.
It accepts the timestamp cache limitation. It does not accept lost queue entries or permanent stopped calculation work.

## 14. Specification review

Two independent internal reviewers reviewed the specification, then reviewed its corrected version.
The review clarifies queue boundaries, recovery, hierarchical costs, and workflow cost refresh after session cost publication.
Both reviewers approved the final behavioral contracts. They did not prescribe implementation details.
