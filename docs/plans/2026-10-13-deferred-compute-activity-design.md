# Deferred compute activity repair

Date: 2026-10-13
Baseline: `b072478b`, branch `fix/content-writer-priority`.
Status: the user approves the independently reviewed design and implementation plan.
Product implementation and measured validation accompany this branch.
Final whole-topic review approves spec compliance, code quality, and readiness to merge.
No Critical or Important finding remains. Two Minor evidence findings receive documentation corrections.
Scoped re-review of those corrections remains pending and belongs to the controller.

## Decision

**Recommend option A: defer background metadata repairs with a durable obligation journal.**
Remove aggregate repair from each background metadata chunk.
Repair session costs, message counts, parent costs, and activity after all source metadata commits.

Temporary incorrect caches and shared activity amounts are permitted during compute.
The final transaction must restore the completed target's exact persisted aggregates.
It must also restore every affected activity bucket against current persisted source.

Keep foreground priority at every writer grant.
Keep current-version live deltas and existing foreground exact repair paths.
Add only the journal integrations required to preserve deferred obligations.

## Correctness contract

The reference is the existing `Session.recalculate_costs()` and `PeriodicActivity.recalculate()` behavior.
Retain its provider partition, UTC dates, daily dates, and Monday weekly dates.
Retain project and global scopes.

- Session `self_cost` sums its persisted item costs.
- Session `subagents_cost` sums persisted item costs from direct children.
- Session `total_cost` keeps the existing nullable and positive-total rules.
- Target `user_message_count` counts persisted `USER_MESSAGE` items.
- Activity cost includes every session type, including hidden sessions.
- Activity messages include visible root sessions only.
- Activity session count uses visible root sessions with cached `user_message_count > 0` and their creation date.
- Empty activity buckets disappear.
- Raw SQLite cost sums must match the existing exact-repair reference, including fractional legacy values.

**Completed** means successful compute final publication, not agent-turn completion.
The source guard must accept the target incarnation, history epoch, observed line bound, and offset.
All source phases must finish before final repair.

At final commit, affected buckets equal full reference recalculation over that transaction's persisted source.
Unfinished peers contribute their currently persisted partial metadata and their currently persisted cached session counts.
Their semantic completion is not promised.

A later peer chunk may make a shared bucket temporarily incorrect again.
A later peer repair may incorporate other partial peers.
There is no frozen publication snapshot or per-session published contribution store.

For prepared noncreation buckets, no foreign commit may occur between their repair and final publication.
This allows their earlier exact values to remain exact at final commit.
Otherwise final publication repairs every required key in its own transaction.

## Existing behavior and cost

`BaseSessionCompute.apply_session_items_chunk()` writes bounded metadata under a revision guard.
It then calls `apply_contribution_changes(..., repair=needs_repair(session))`.
The incomplete compute version normally makes `repair=True`.

That repair performs work beyond the metadata row budget:

- It recalculates direct children's cached costs when the target is their parent.
- It recalculates target and direct-parent costs from persisted history.
- It recounts target user messages.
- It reads the target's complete item contribution history.
- It recalculates project/global daily/weekly activity buckets.

The operation's `activity_repair_keys` records old and new timestamp obligations after commit.
That inventory is memory-only.
Current per-chunk exact repairs mask the loss of this inventory after a crash.

`activity_preparation.py` already repairs one exact bucket per background grant after source completion.
It records a fixed physical connection and fixed `PRAGMA main.data_version`.
Final admission excludes old and new creation buckets and falls back to exact repair without proof.

Creation exclusions remain necessary.
Prepared bucket session counts use the target's old cached message count.
Final repair first updates that cached count, then repairs creation buckets.

`live_aggregates.py` already uses changed-row deltas for ordinary current-version live ingestion.
A stale parent can amplify a child write into full repair.
This amplification remains outside the first lot.

## Options

| Option | Scope | Benefit | Cost and risk |
|---|---|---|---|
| **A: durable deferred obligations** | Background metadata only; one small journal table; narrow foreground preservation hooks | Removes repeated target/history/bucket repairs from each metadata chunk | Adds recovery scheduling and mutation-path obligations |
| B: journal plus stale-live deferral | A plus initial-sync skips and pending/stale live-write deferral | Reduces foreground repair amplification during compute | Broadens temporary-cache behavior and couples live source invalidation to recovery |
| C: final SQL aggregation overhaul | A plus grouped aggregation across many buckets | Reduces repeated scans across sessions and final fallback holds | Changes numerical and query semantics; needs a separate aggregation proof |

Choose A first.
Measure B's specific amplification before changing live behavior.
Keep C outside this lot.

## Persistent journal

Add one small model, provisionally `ComputeAggregateRepair`.
Do not add columns or indexes to `SessionItem`.
Do not materialize per-session published contributions.

Each journal row represents one source incarnation's unresolved obligations.
Use a plain session ID and incarnation string, with a unique pair.
Do not use cascading session or project foreign keys.

Proposed fields:

| Field | Meaning |
|---|---|
| `id` | Stable row identity; changes when a deleted journal row is recreated |
| `session_id` | Plain target ID, retained after target deletion |
| `incarnation` | Source incarnation associated with these obligations |
| `generation` | Monotonic revision for every journal mutation |
| `activity_keys` | Deduplicated serialized key union |
| `parent_ids` | Deduplicated plain old/new direct-parent IDs |

A key contains activity kind (`day` or `week`), nullable project ID, provider, and ISO date.
Do not serialize Django model classes.
Global scope uses an explicit null value.

Store **obligations**, never aggregate values or publication proof.
Keep the union until exact repair and conditional settlement succeed.
History epoch changes do not erase old keys from the same incarnation.

A single JSON key union minimizes schema and per-chunk inserts.
Read/merge/write it only under the writer transaction.
It grows with unique dates/scopes, not item count.

The journal inventory has no correctness cap.
The existing 4,096-key limit applies only to optional preparation proof.
Large inventories use exact fallback; they never discard obligations.

If measurement shows JSON rewrite cost is material, normalize keys into a separate small child table later.
Do not add that table without evidence.
The initial schema migration creates the new table only.
It performs no data migration, history scan, or blocking startup rebuild.

## Transaction boundaries

### Background source publication

`record_compute_repair(session, before_items, after_items, prospective_fields, affected_days)` requires an active writer transaction.
It returns the journal identity and committed generation observation.

For each guarded metadata chunk:

1. Acquire the existing background writer grant.
2. Start the existing write-first guarded transaction.
3. Read bounded before-item contributions and current session contribution metadata.
4. Apply metadata and content changes.
5. Read bounded after-item contributions.
6. Merge old/new item keys and old/current creation keys into the journal.
7. Merge current parent identity and worker-provided affected-day keys.
8. Increment the journal generation.
9. Commit source and journal changes together.
10. Release the grant before another background step.

No session, child, parent, or activity repair runs in this metadata step.
Do not rely on `on_commit()` to create durable obligations.
Commit callbacks may update private mirrors only.

A failed source transaction commits neither source nor obligations.
A committed source chunk always commits its old-key evidence.
A failed, cancelled, or superseded result leaves its journal intact.

Seed operation-prospective creation/scoping keys with the first deferred source transaction.
Record any additional final keys before final source fields change, within the final transaction.
Persist worker affected-day obligations no later than the first deferred source mutation.
An empty-source result records these obligations in its guarded final transaction.

The complete final inventory also includes current history timestamps.
Enumerate those timestamps after source completion using existing bounded preparation pages where available.
Missing preparation proof still uses full current-history enumeration in exact fallback.

### Successful final publication

`load_compute_repair(session_id, incarnation)` reads the durable union.
The operation adopts it rather than replacing it with its own key set.

Within the guarded final transaction:

1. Apply existing final source/lifecycle/link/fact writes.
2. Add current, old, prospective, and worker keys to the durable union.
3. Read journal row identity and generation under writer ownership.
   Admit preparation against the prefinal observation; any newly added obligation disables prepared proof.
4. Recalculate target costs and target user-message count.
5. Recalculate direct-child cached costs when finalizing a parent.
6. Recalculate current and retained old direct-parent costs from raw items.
7. Repair every required activity bucket, or consume admitted exact preparation proof.
8. Publish the compute version and existing final readiness state.
9. Delete only the journal row matching its ID, incarnation, and captured generation.
10. Commit all final writes and journal settlement together.

A conditional-clear mismatch must never silently report a fully settled final.
Under normal serialized writer ownership, a mismatch indicates an invalid journal observation.
Reload and repair the enlarged inventory before successful settlement, or abort and retry final publication.

Final rollback preserves the previous journal and incomplete compute state.
A callback failure after commit cannot resurrect deleted SQL obligations or invalidate committed exact repair.
Callbacks must not decide whether SQL repair happened.

### Identity and generation

An observation contains `(journal_id, session_id, incarnation, generation)`.
Never clear by session ID alone.
Never clear by the operation's stale in-memory generation.

A reused session ID can have a different source incarnation.
Repair the old incarnation's orphan obligations independently.
Do not apply an old result to the new incarnation.

Source revision guards remain separate from journal generation.
A journal generation fences obligation changes.
It does not replace source incarnation, history epoch, upper-line, offset, or active-session guards.

## Preparation reuse

Reuse the existing optional activity preparation architecture.
Do not repair buckets during metadata application.
Begin preparation after every source phase commits.

Seed required keys with the journal union and operation obligations.
Add current history timestamps through bounded enumeration.
Adoption captures journal identity/generation alongside existing source identity.

Before each bucket publication, retain the existing guarded write-first transaction and physical connection checks.
Also validate the captured journal identity/generation.
A journal change disables shortcut proof.
A metadata source transaction increments generation even when its key union stays unchanged.
A final inventory merge without new keys or parents need not mutate the journal.
A final merge with new obligations takes exact fallback.

Each preparation step writes an exact DB bucket.
It does not clear journal keys.
Cancellation after several repaired buckets leaves the complete durable union for recovery.

Final cache admission retains these fences:

- All source phases complete; no pending batch or final item overrides.
- Same worker and same physical connection.
- Same fixed `PRAGMA main.data_version`, never refreshed after a foreign commit.
- Stable project, provider, type, and hidden metadata.
- Exact committed membership for every required key.
- Unchanged adopted journal identity/generation.
- No old or new creation day/week key in the admitted cache.

Own preparation commits do not advance SQLite `data_version` on that physical connection.
Foreign commits invalidate proof, even if unrelated to the target.
This conservative rule remains unchanged.

Known own final cached-count and lifecycle writes are not foreign source mutations.
Validate the captured journal generation before those known writes.
Creation exclusions cover their activity session-count effects.
Metadata scope changes still disable prepared proof through existing metadata admission checks.
Unsupported SQL on the pinned connection remains outside the preparation contract.

The final transaction updates target cached message count before creation-bucket repair.
It does not derive an activity delta from a stale precompute cached count.
Exact fallback receives `repaired_activity_buckets=None` and the complete durable union.

The final helper currently reads full item contributions even with prepared keys.
That final history scan remains once per successful final publication.
Removing it requires a separate proof and is outside this lot.

## Supported mutations during compute

Foreground writes keep their current aggregate behavior.
They can temporarily repair buckets using partial compute source.
The journal remains a conservative obligation union until complete settlement.

| Mutation | Required first-lot behavior |
|---|---|
| Current-version live append without journal | Keep existing deltas unchanged |
| Live append to pending target | Keep existing repair/delta behavior; retain existing journal and record relevant changed keys |
| Live append invalidates source observation | Reject old compute final; next compute adopts journal |
| Hidden/type/project/provider changes | Preserve old and new historical scopes and creation keys for pending journal; retain existing exact foreground repair |
| Creation date change | Preserve both creation day/week scopes; reject prepared creation shortcuts |
| Parent relation change | Retain old and new parent IDs; final/recovery repairs both existing parents |
| Codex replacement | Merge pending journal with removed-history keys before deletion; retain old keys through epoch change |
| Delete target or descendant tree | Include journals for every deleted descendant in deletion's exact repair, including removed timestamps absent from current items |
| Missing target at compute apply | Keep journal; enqueue orphan recovery instead of discarding obligations |
| Mark stale flag only | Keep behavior; stale is not an activity eligibility filter |

The hooks belong at supported source mutation boundaries.
A generic postwrite hook cannot recover old project scopes or deleted timestamps.
Before-history deletion and before-scoping changes must preserve old keys.

Foreground deletion may settle journals atomically when it repairs their complete union and retained parents.
Alternatively it leaves them for background orphan repair.
Choose atomic settlement where existing deletion already performs exact repair.
Do not return early merely because the session row is already missing and a journal remains.

Codex replacement currently repairs removed current item keys.
Its repair must additionally include previously removed keys from pending journal entries.
Retain the journal across replacement unless that transaction proves complete settlement.
New raw chunks require later compute before source completion.

A moved project retains plain old project IDs in the journal.
If an old project row disappears, delete its obsolete scoped activity rows rather than inserting invalid foreign keys.
Global keys still receive exact repair.
This is orphan cleanup, not a new project-deletion feature.

Audit each actual writer entry point before implementation.
Relevant paths include `compute_base.py`, `db_writer.py`, `session_visibility.py`, and `codex/rollout_migration.py`.
Do not extend this scope to unsupported direct SQL mutations.

## Retry and restart recovery

`next_compute_repair()` selects a bounded journal work page.
The scheduler services it through existing background grants.
Startup does not synchronously repair journals or scan all history.

- Existing stale target: the normal recompute adopts its journal.
- Existing current-version target with a journal: enqueue exact aggregate recovery without unnecessary metadata recompute.
- Existing target with a different incarnation: recover old journal keys as orphan obligations.
- Missing target: repair retained bucket keys and retained existing parents, then conditionally clear the orphan journal.
- Deleted parent: skip its cache update; its own journal retains activity obligations independently.

For A, recovery performs one exact repair-and-settlement transaction per journal row.
That transaction repairs the complete key union and retained existing parents, then conditionally deletes the journal.
Existing current-version targets also receive exact target/direct-child cost repair and a target message recount.
Old-incarnation and missing-target recovery never updates a replacement target's session caches.

Recovery uses the ordinary background writer worker and its physical connection.
It never injects unrelated SQL onto the pending application's pinned preparation connection.
Such a recovery commit invalidates pending preparation through the existing foreign-commit fence.

No transaction, iterator, or read snapshot survives a recovery step.
One recovery transaction can remain expensive; retain diagnostics for this remaining hold.
Per-bucket orphan recovery is a later optimization requiring its own final proof.

Do not clear individual keys after independent bucket commits.
A later peer mutation can invalidate their exactness before final settlement.
The journal records unresolved repair work, not valid cached results.

An interrupted recovery leaves the journal complete.
Repeated repair is idempotent.
A row changed after recovery observation remains pending through generation checks.
Foreground demand wins before every recovery grant.

### Discovery, fairness, and shutdown

Recovery discovery belongs to the writer lifetime, independently of provider metadata-compute tasks.
It starts when the writer accepts background work.
It runs even when every provider reports zero stale metadata targets.
It continues after provider compute tasks finish and after new orphan obligations appear.

Use bounded discovery pages and a finite sweep cursor over stable journal row IDs.
Each sweep captures a maximum row ID and advances through that finite range.
Advance past selected, failed, backed-off, stale, and otherwise ineligible rows.
After the range ends, start a later sweep that includes newly created rows.
Do not repeatedly select the first unresolved row.

Bound recovery's queued work and deduplicate pending observations by journal identity.
Discovery can inspect later rows even when an earlier row cannot run.
A continuously failing or stale row cannot hide an eligible later row.
Discovery with no eligible work waits for a notification or a positive polling interval.
It never spins through empty sweeps.

Each failed repair retains its durable journal and receives a positive retry delay.
Repeated failures increase that delay up to a bounded maximum.
Do not retry a failed row from inside the same writer drain iteration.
Backoff may be process-local; restart can retry immediately once without losing durable obligations.
A newer journal generation supersedes a queued observation; rediscover it without clearing either generation's evidence.

Recovery excludes any target with a pending source application, regardless of its cached compute version.
This exclusion includes applications in preparation, source publication, bucket preparation, final publication, or terminal cleanup.
Recheck the exclusion under background admission immediately before recovery's first guarded write.
An application admitted after discovery therefore prevents stale queued recovery from clearing its journal mid-source.
Release or skip that recovery turn; retain the journal and advance fair discovery.
An existing stale target awaits normal recompute and does not create a recovery retry loop.

Recovery does not delay provider ACK, alter compute terminal readiness, or advance compute version.
Recovery uses the ordinary writer worker only.
It never adds SQL work to the pinned application worker.

Shutdown follows a finite sequence:

1. Close recovery discovery and retry admission before final writer drainage starts.
2. Cancel or stop the discovery timer/task and await its termination.
3. Reject discovery callbacks or retry callbacks that arrive after closure.
4. Drain the finite set of already admitted recovery and ordinary writer work.
5. Permit each admitted recovery observation at most one remaining attempt; do not enqueue its retry during shutdown.
6. Close worker resources through the existing shutdown sequence.

The final drain never discovers journal rows or starts new sweeps.
Failed, excluded, queued-but-unadmitted, and undiscovered obligations remain in SQL for the next writer start.
Cancellation before repair settlement also retains the journal.
An already admitted exact row transaction can delay shutdown until that transaction settles.
No strict shutdown-time bound follows from the existing atomic repair hold.

## Parents and children

When a child completes, its own costs and count become exact.
The direct parent's cost caches also become exact against current persisted child items.
The parent can remain an unfinished metadata target.

When a parent completes, preserve existing direct-child cost cache repair.
Use raw direct-child item sums for parent `subagents_cost`.
Do not trust unfinished children's cached costs.

Do not recursively repair all descendants' metadata or message counts.
The existing cost contract is direct-child based.
Child activity messages remain excluded; child costs remain included.

Old parent IDs survive relation changes and child deletion in the journal.
Repair every retained existing parent at settlement.
No per-chunk child or parent repair remains in background metadata.

## Optional narrow initial-sync skip

Raw initial-sync inserts use content-only items without timestamp, kind, or cost metadata.
A newly created, empty session has no activity contribution and no existing history.
That specific creation can skip exact aggregate repair if all these facts are proven inside its transaction.

Do not skip appends to existing stale sessions.
Their raw new items have no activity, but older metadata and cached session aggregates can require repair.
Do not include this optional skip in A's acceptance requirement.
Measure it separately after the background deferral passes review.

## Acceptance tests

Use disposable test databases only.
Compare final state to an independent full reference recalculation over persisted source.
Inspect raw SQLite costs and endpoint-style sums as well as ORM values.

| Test | Required evidence |
|---|---|
| Many metadata chunks | No per-chunk target-history, child, parent, or activity aggregate repair |
| Timestamp removal/move | Old last timestamp bucket disappears or keeps correct peer contribution |
| Creation change | Old/new dates, null dates, hidden roots, children, and zero/nonzero count transitions remain exact |
| Crash after committed chunk | Restart adopts removed timestamp and old creation keys from SQL journal |
| Source rollback | Neither source metadata nor new journal generation commits |
| Supersession/live append | Old result fails guard; next successful result repairs accumulated keys |
| Project/provider/type/hidden mutation | Old and new scoped buckets equal reference; prepared proof rejects unstable metadata |
| Codex replacement after partial compute | Previously removed keys survive replacement and final recompute |
| Root/child/tree deletion | Missing-source journals retain keys; direct-parent costs and global buckets remain exact |
| Reused session ID | Old incarnation repair cannot clear new incarnation journal |
| Generation mismatch | Recovery/final cannot clear enlarged obligations using stale observation |
| Journal-only boot | Current-version and orphan journals recover without any stale metadata targets |
| Recovery after provider completion | Discovery continues after provider compute returns and finds later orphan journals |
| Fair discovery and backoff | An ineligible or failing first row cannot starve an eligible later row; retries do not spin |
| Active pending target | Discovery and admission exclusion prevent recovery settlement during the target's source application |
| Recovery shutdown | Discovery closes before final drain; admitted work drains once; remaining journals survive restart |
| Shared buckets | Completing target repairs bucket over current persisted peers, including unfinished peers |
| Child completion | Direct-parent costs equal raw child sums despite stale parent and stale sibling caches |
| Parent completion | Target and direct-child costs match existing direct-child reference behavior |
| Preparation failure | Capacity, foreign commit, connection replacement, cancellation, callback error, and missing membership force exact fallback |
| Fractional legacy costs | Raw sums retain exact-repair precision; stale cache deltas cannot preserve normalization errors |
| Admission priority | Foreground commits before every next metadata, bucket, recovery, and final background grant |

Keep existing provider ACK and compute terminal ordering tests.
A journal does not acknowledge incomplete source or advance compute version early.

## Measurable comparison

Compare the clean baseline and proposed change on identical isolated data.
Record `DEBUG=False` separately from any `DEBUG=True` measurements.
No conclusion about the live database follows from synthetic timing alone.

Workloads:

1. One large root across many metadata chunks, with several direct children.
2. Many sessions repeatedly touching the same day/week project/global buckets.
3. A long history spanning many days, including removed timestamps and creation-date changes.
4. A stale parent with live child writes during parent compute.
5. Concurrent foreground writes during metadata, bucket preparation, recovery, and exact final fallback.
6. Crash/restart and replacement/deletion after partial metadata publication.

Record source rows, metadata chunks, unique keys, journal bytes/writes, and retry counts.
Count full-history scans and exact bucket repairs, including repeated repairs of the same bucket across sessions.
Record total compute time, foreground completion time, maximum grant hold, and final transaction hold.

Expected A result: background metadata performs zero aggregate full-history scans and zero bucket repairs.
Successful final still performs target/child/parent repair once, plus exact bucket preparation or fallback.
Repeated sessions sharing a bucket still cause repeated final bucket scans.
Do not claim A eliminates that separate cost.

## Remaining holds and exclusions

An individual exact bucket repair can scan many peers and hold the writer for a long time.
Exact final fallback can repair many buckets atomically.
Final target, direct-child, and parent cost repair still scans history.
Foreground stale-parent and existing-session initial-sync amplification remain.

Foreground priority applies at grants; it does not preempt an admitted transaction.
No strict latency bound follows from this design.
Persistent JSON journal merging can add overhead for very large key unions.
Measure that overhead before choosing normalized journal keys.

Excluded: SQL `GROUP BY` overhaul, huge-table indexes, new dependencies, content/cold-storage changes, scratch tools, API changes, and changelog changes.
No installation, server restart, production migration, or merge belongs to this design task.

## Design review evidence

Independent adversarial review accepts the correctness contract and transaction model.
It initially identifies one Important recovery lifecycle gap.
The amendment adds independent discovery, finite fair sweeps, retry backoff, pending-application exclusion,
and discovery shutdown before final writer drainage.
Scoped re-review marks that finding addressed, with no new Critical or Important findings.

That review stage changes only the design. The user then approves the written design and implementation plan.
Product implementation and measured validation now accompany this branch.
Validation evidence is in `docs/plans/2026-10-13-deferred-compute-activity-plan.md`.
Final whole-topic review approves spec compliance, code quality, and readiness to merge.
No Critical or Important finding remains. Two Minor evidence findings receive documentation corrections.
Scoped re-review of those corrections remains pending and belongs to the controller.
