# Research before content and activity optimization

This document complements `2026-10-13-content-extraction-performance-analysis.md`.
It records research and proposed boundaries. It does not contain product implementation.
DevCTL remains stopped. The worktree database and search index remain intact.

## 1. Cold cache invalidation

### Existing boundaries

`content_storage/lifecycle.py` routes lifecycle mutations through `lifecycle_change`,
`update_lifecycle`, and `save_lifecycle`. It observes old and new native roots.
`agent/base_manager.py` registers a real agent under storage admission, then persists start timestamps.
`providers/db_writer.py` provides the current active-agent set to guarded content work.
`content_storage/policy.py` defines 15/30-day content rules and the separate scratch rule.

`content_state_epoch` changes for lifecycle mutations and content reference mutations.
It is therefore unsuitable as the sole cold-cache key: content extraction itself would invalidate it repeatedly.
The existing storage guards still need that epoch. The cold decision cache need not share its lifetime.

### Recommended cache

- Cache root lifecycle fields and their evaluated cold result. Cache native session-to-root mappings separately.
- Update or evict lifecycle entries after a successful commit that changes a relevant field.
- Invalidate mappings on creation, deletion, reparenting, or type changes. Include old and new roots.
- Expire a hot result at its next age threshold. A monotonic deadline can support scheduling,
  but the authoritative date comparison retains wall-clock semantics.
- Treat active-agent protection as a current overlay, captured once per publication under admission.
  Do not persist an active-agent boolean indefinitely in the lifecycle cache.
- Apply pending compute lifecycle values as an operation-local overlay. They are not committed cache values yet.
- Missing parents and invalid graphs remain ineligible for compression. Creation can invalidate prior missing results.
- Reset caches with the writer/process lifetime and database identity. Keep scratch policy independent.

No new Session column is required for this cache.
Direct Session writes that bypass lifecycle helpers require audit or explicit cache invalidation before enabling it.
The proposal does not assume that process-local caches detect unsupported external edits to Session rows.

## 2. Detecting complete native trees

### Existing graph and scheduling

Claude initial sync attaches discovered subagent files to their native root.
Codex initial sync calls `subagent_roots.resolve_flat_parent_id` before inserting a subagent.
Both normal imports therefore flatten native subagents to the root, including nested launchers.
Agent launch relationships and `spawned_by` are separate concepts. They must not define content/activity inheritance.

The content resolver already handles unflattened ancestry and cycles conservatively.
Both provider orchestrators wait for `initial_sync_done` before starting normal background compute.
This event differs from the compute coordinator's initial classification event.

The individual Session compute version advances only on successful source/storage publication.
Worker completion alone does not establish database readiness.
Each provider admits at most two outstanding sessions. Delaying per-session application acknowledgments until
the whole tree finishes would prevent dispatch of the children needed to finish that tree.

### Recommended completion boundary

1. Keep individual source application and acknowledgments independent of tree aggregate publication.
2. When a node finishes application, resolve its root and re-evaluate pending members of that native tree.
3. A tree can publish aggregate work when all known members have current provider compute versions
   and no member has a pending source application that could change its contribution.
4. A missing, failed, superseded, or deferred member keeps that tree pending. Other roots continue.
5. A later child insertion, reparenting, or source change adds new obligations and invalidates the prior tree observation.
6. Validate the tree observation again before publishing prepared root costs or clearing its obligations.

Completion means completion of currently known source revisions. It does not require waiting for an active
agent process to exit forever. Live writes still proceed and update/invalidate the applicable observations.
Initial inventory failure cannot be silently treated as proof that no other children exist.

### Read-only tree probe

On a real root containing 414 native sessions, a prototype recursive query returns the same membership
and readiness counts through three variants:

| Prototype | Median |
| --- | ---: |
| Recursion with `type='subagent'`, default planner | 2,002 ms |
| Same recursion using the parent index | 0.665 ms |
| Recursion through parent links without the redundant type predicate | 0.682 ms |

The slow plan repeatedly searches the type index instead of following parent IDs.
This is a prototype finding, not evidence that the product currently executes that recursive query.
Normal flat trees need only root-plus-direct-member queries. Unflattened graphs need the correct parent traversal.
Loading graph metadata for all 14,645 Session rows takes 20.014 ms in this probe.
Do not rescan all trees after every item. Maintain pending root candidates and inspect their metadata.

## 3. Keeping affected days, including removed contributions

`ComputeAggregateRepair` already retains a source ID, incarnation, generation, activity keys, and old parent IDs.
It deliberately has no source FK, so source deletion does not erase its obligations.
`record_compute_repair` unions old/new item keys, old/new session scopes, prospective metadata, and extra keys.
`preserve_pending_repair` handles live appends and metadata changes when a journal already exists.
Generation checks prevent stale preparation from clearing enlarged obligations.

Reuse this durable inventory. Do not rebuild affected days only from final items: deleted items and old scopes
would disappear. Retain old/new project and provider scopes and creation-count days as well.
Collect the union across the root tree before aggregate publication, retaining journals until their work finishes.
Parent changes retain obligations for both former and current trees.
An orphan journal can repair its former dates even when the source no longer exists.

The current recovery exclusion protects only the single pending Session application.
`repair_compute_observation` can therefore repair a current subagent while another tree member remains stale.
Tree-level deferral must apply to both ordinary finalization and recovery, otherwise recovery bypasses the new rule.

No second durable inventory is inherently required. Existing journal rows can retain the tree's work.
Root grouping after reparenting/deletion and observation settlement require explicit tests.

## 4. Coexistence with live writes

Current-version live appends use contribution deltas. Stale sources or pending repair paths can invoke full repair.
The live source must continue writing immediately with foreground priority.
During an incomplete tree, retain its affected days instead of launching a full tree/activity repair per live append.
For an already published baseline, retain the ordinary live delta path.

A prepared daily sum can become stale after another session writes to the same day.
The target tree's journal generation alone cannot detect that other session's change.
Global SQLite `data_version` detects too much: an unrelated commit also invalidates preparation.

Recommended publication contract:

- Track an in-process contribution revision per affected daily scope/provider/date.
- Advance it after source contribution commits, including metadata compute batches, live writes, visibility changes,
  date/scope moves, deletion, and creation-count changes. Both old and new keys count.
- Prepare daily values in a read-only database snapshot outside the writer lease.
- Observe the relevant revisions before and after preparation. Reject a changed observation.
- Under the writer, publish only if those same relevant revisions still match.
- On mismatch, retain the obligation and prepare again outside the writer. Never fall back to a long SUM under writer.
- Weekly publication sums the current daily rows for its scope/provider/week under a short transaction.
  Keep the live weekly update and this publication serialized through the existing writer.

These revisions are invalidation counters, not a new aggregate source of truth.
They reset after restart; durable journal work is prepared again.
Revision tracking must cover all contribution writers before it can replace global invalidation.
The supported writer model is one TwiCC process per database. Uncoordinated external source-table writes need
separate detection; Django authentication-table commits do not change contributions.

## 5. Interruption and recovery

- Every partial metadata source commit already records its obligations in that same transaction.
- Keep those obligations when source compute succeeds but its tree still has unfinished members.
- At startup, rediscover journals and rebuild pending root groups from persisted Session metadata.
- Resume ordinary source compute first for stale members. Ready trees and orphans can enter aggregate recovery.
- Publish bounded aggregate units. Partial progress remains acceptable to the UI.
- Delete a journal observation only after all associated daily, weekly, session-cost, and parent obligations finish.
- A generation change prevents settlement. Retain and retry the affected work.
- A crash after some bucket writes causes safe recalculation after restart. Exact replacement is idempotent.
- Graceful shutdown need not run all expensive repairs synchronously. Retain durable pending work for restart.
- Keep finite discovery sweeps, foreground priority, and retry backoff from the existing recovery scheduler.

The current `repair_compute_observation` repairs its entire inventory in one writer transaction.
It must use the same bounded pipeline as normal tree publication. It cannot remain an unbounded fallback.

## 6. Remaining aggregate costs under writer

`live_aggregates.apply_contribution_changes(repair=True)` calls `Session.recalculate_costs` for the target and parents.
That method sums the session's raw item costs and all its direct children's raw item costs.
A parent repair therefore revisits all children's history after each completed child.
Root repair can also recalculate every child separately.
This repeated raw-history work is a separate cost from activity bucket SQL.
With many children, repeated sibling-history scans can grow much faster than processing each child's history once.

Read-only timings for a 145,950-item root with 413 native subagents:

| Query | First execution | Later executions |
| --- | ---: | ---: |
| Root own cost SUM | 663 ms | about 26 ms |
| All direct children's item cost SUM | 1,993 ms | about 63–64 ms |
| Root user-message count | 9.3 ms | below 0.1 ms |

For a separate 186,496-item root without children, its own cost SUM takes about 40–42 ms.
These are three read executions per query, without cache reset. They exclude Django and writes.
Earlier activity probes also show multi-second first reads. A fixed small row budget does not bound these sums.

Recommended split:

- Prepare each node's exact own cost/count outside writer from stored item metadata, guarded by its source observation.
- Avoid recomputing its parent's raw history after every child. Publish final tree totals after the tree completes.
- Use the finalized member self costs to derive root subagent totals. Preserve existing flat-tree counting semantics.
  For unflattened graphs, ensure each item contribution appears once; summing overlapping `total_cost` values would duplicate costs.
- Retain the SQL SUM precision/null semantics. Do not substitute unrounded provider token-cost calculations.
- Update project total costs after publishing root costs. Keep project counting/mtime changes separate where needed.
- Read daily sums outside writer; write their small results under guarded bounded transactions.
- Calculate weeks from daily rows. Do not perform raw weekly item-history SUMs in normal publication or recovery.
- Remove whole-history contribution enumeration from final source publication when the durable inventory already covers it.

Per-session source completion, per-tree cost/activity completion, and startup progress are separate events.
They should not share an acknowledgment that blocks dispatch or hides otherwise readable session content.

## Verification and limits

Existing targeted tests pass: 75 tests in 5.63 s across repair journal, mutation, recovery, and content lifecycle modules.
They verify current journal union, stale-generation rejection, rollback, live append, deletion/visibility preservation,
recovery scheduling, and existing lifecycle behavior. They do not prove the proposed caches or tree publication implementation.

All new probes live under `/tmp`. Production data is read through SQLite URI `mode=ro` with `query_only=ON`.
No migrations, server startup, product changes, or database/index resets accompany this research.
Only research documents are changed.

Before implementation, independent review should challenge the contribution-writer coverage, tree completion events,
generation settlement, null/rounding behavior, native-versus-spawn tree distinction, and foreground contention.
