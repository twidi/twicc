# Content storage and tree aggregate optimization

Date: 2026-10-13.
Status: adversarially reviewed; ready for user review before implementation planning.
Scope: behavior and correctness contracts. This document does not authorize product implementation.

## 1. Outcome

Remove redundant content work and repeated aggregate repairs from background compute.
Keep the watcher, agent lifecycle writes, and other foreground work responsive during progressive compute.
Preserve exact content, item identity, line numbers, external payloads, and recovery after interruption.

This specification changes the content pipeline and aggregate publication on the existing feature branch.
It complements the [content storage specification](2026-10-11-session-item-content-extraction-spec.md).
Where they conflict, this document supersedes routine exact-text round-trip verification and per-node background aggregate publication.
Other storage, migration, serialization, compression-policy, and maintenance contracts remain applicable.

Evidence:

- [Performance analysis](2026-10-13-content-extraction-performance-analysis.md).
- [Readiness, live, and recovery research](2026-10-13-content-optimization-readiness-research.md).

Excluded: new rollout/fork behavior, garbage collection, legacy column removal, vacuum, process executors,
new dictionary training, and interleaved application of multiple compute sessions.

## 2. Fixed decisions

- Keep Zstd level 3 and the existing provider dictionaries.
- Load and validate each immutable dictionary once per process/database identity.
- Keep content cold rules: 15 inactive days for archived/hidden roots; 30 days otherwise.
- Keep scratch cleanup rules unchanged.
- Search digests before optional compression. Reuse existing logical content directly.
- Replace content reference and ownership CASE updates with bounded parameterized `executemany` updates.
- Keep the existing 2,000-item metadata batches and their `executemany` implementation unchanged.
- Recalculate activity after each complete native root tree, not after each subagent or every ten roots.
- Calculate weeks from daily aggregates, not raw item history.
- Preserve the current single application operation and bounded per-provider compute pipeline.

## 3. Dictionary lifetime

Persisted dictionary bytes, their validation, and the native Zstd dictionary form one reusable process-local entry.
The entry belongs to the actual database and immutable dictionary identity, including provider and digest.
Unchanged dictionaries require no repeated SQL read, hash validation, or native dictionary construction per content.
Context changes, including database replacement in tests, must not reuse a dictionary from another database.

A future immutable dictionary version may load once when it becomes relevant.
Normal content operations do not poll repeatedly for new versions.
Missing or invalid referenced dictionaries still produce an explicit integrity failure when they are needed.
Mutable compressor/decompressor state must remain safe for concurrent threads.

## 4. Cold decisions and native root mappings

Cache committed lifecycle metadata and cold decisions by native root.
Subagents inherit their root's decision; their launcher hierarchy does not create independent cold policies.
Cache session-to-native-root mappings separately.

Relevant lifecycle changes update or invalidate the root cache after successful commit.
Creation, deletion, type changes, and reparenting invalidate affected mappings, including former and new roots.
Hot decisions expire when time reaches their applicable inactivity threshold.
Cached decisions preserve authoritative wall-clock semantics. A backward clock change that crosses the threshold
must invalidate a previously cold decision before compression publication.
Lifecycle and mapping caches are isolated by database identity and reset when that identity changes.
Current active-agent protection applies once per bounded publication under the existing admission mechanism.
Pending compute lifecycle values form an operation-local overlay until committed.

Adding content references alone does not invalidate the lifecycle decision.
Existing storage incarnation and history/reference guards remain independent of this cache.
Missing roots, unresolved parents, and invalid ancestry never authorize compression.
All supported lifecycle writers must participate in invalidation; rollback must not publish uncommitted cache values.

For shared content, all actual native owners must permit compression.
Fresh publication checks use cached decisions with valid lifecycle state, not a repeated full root query per content.

## 5. Content preparation, reuse, and publication

### Logical identity

SHA-256 identifies the exact uncompressed UTF-8 bytes. It does not identify a storage representation.
Do not parse, normalize, or reserialize the logical string for hashing.
Current and original references retain their existing meaning, including unknown legacy provenance.

### Preparation order

1. Compute bounded batch digests and deduplicate equal input strings.
2. Look up existing digest identities before optional compression.
3. Reuse existing rows without decoding them just to establish their identity.
4. Prepare missing rows in their eligible representation outside the writer.
5. Retain compression attempt policy and no-gain outcomes with the prepared result.

Preparation must not discard the no-gain outcome and require a second attempt during reconciliation.
Publication resolves a row that appeared after lookup instead of overwriting its existing logical identity.
Reuse preserves the first stored representation unless owner policy requires a representation change.

### Integrity boundaries

Treat successful Zstd compression as the lossless transformation supplied by the library.
Do not immediately decompress a prepared candidate or newly inserted row to compare it with its input.
Do not decode an existing row merely because its digest matches an input.
Do not recalculate a preparation digest repeatedly during publication.

Prepared text, digest, size, representation, and dictionary must remain associated throughout the pipeline.
Malformed preparation metadata and conflicting known batch identities must fail explicitly.
The digest uniqueness contract is the ordinary reuse boundary; it does not require routine decoded collision comparisons.
When stored payloads are actually decoded, retain existing size, digest, codec, dictionary, and UTF-8 integrity checks.
Tests must verify exact logical round trips and association preservation across reuse and insertion races.

### Publication and owners

Keep item reference assignment and legacy `content=''` clearing in the same update and transaction.
Use parameterized `executemany` for the affected fixed-column content reference and ownership updates.
Retain field adaptation, source guards, transactional reference accounting, and root invalidation where required.
No commit per individual item is introduced.

Owners are unique session IDs covering current and original references.
New content rows include their known initial owners within the guarded assignment transaction.
Existing rows receive an ownership update only when the owner set changes.
An unchanged list produces no ownership write.
Owner removal still checks that neither current nor original references retain that ownership.

## 6. Representation reconciliation

Reconciliation is change-driven. It does not round-trip or rewrite a representation already conforming to owner policy.

| State | Required action |
| --- | --- |
| Newly inserted content, correct representation and attempt metadata | No second content treatment |
| Existing conforming content | No payload write or validation decode |
| Attempt metadata only needs completion | Update metadata only |
| Compressed content now referenced by a hot owner | Promote to plaintext through the existing lifecycle rules |
| Plain content eligible for a new compression attempt | Prepare compression outside writer |
| Same no-gain policy already tried | Do not retry |

Avoid processing the same shared content repeatedly just because multiple items reference it.
Representation and owner changes between preparation and publication must be detected.
Initial sync, live ingestion, overrides, original/current references, and interrupted extraction retain the same invariants.
No normal path rewrites an unchanged compressed payload after decoding it.
Future dictionary changes do not require per-content version polling today.

## 7. Individual completion and tree aggregate completion

A native tree contains the root and all native subagents, at every launcher depth.
Use native parent/root relationships. Do not include independent sessions solely because `spawned_by` relates them.
Normal imports flatten native subagents to their root; valid unflattened ancestry remains handled conservatively.

Individual compute completion means that the guarded source and storage result is applied in the database.
It advances that Session's compute version and releases its ordinary application acknowledgment.
It does not wait for the whole tree's aggregate publication.
Keep readable session content and existing startup source progress independent of aggregate scheduling.

A tree becomes eligible for aggregate publication when all its currently known members have current source compute versions
and no pending source application invalidates the observed contributions.
Worker completion alone is insufficient.
Failed, superseded, deferred, or unresolved members keep the affected tree pending without blocking other trees.
Respect the existing initial inventory completion boundaries; failed inventory cannot prove an empty tree.

Re-evaluate eligibility after source application, member insertion/deletion, and relationship changes.
A later source or membership change creates new obligations even after a prior tree publication.
Before publishing prepared tree costs, revalidate the observed native membership, source revisions, and tree eligibility.
If they changed, reject the stale prepared values and retain the work for a new eligible observation.
Normal publication and recovery share this rule. A ready node does not establish that its former tree observation remains valid.
An active process can continue producing live writes; completion concerns known source revisions, not eternal agent inactivity.
Do not block source dispatch while waiting for descendant completion.

## 8. Session costs and activity publication

After a tree becomes eligible, publish its final session costs and affected activity buckets.
Individual background subagent completion does not trigger a full parent/tree activity repair.
Per-tree publication occurs after each completed tree. There is no ten-root interval.

### Session and root costs

Own costs use stored item cost metadata, with existing precision and null semantics.
Prepare expensive own-cost sums and message counts outside writer.
Prepared own, root, and project costs carry observations of their contributing sources and membership.
Publication must not overwrite newer costs or metadata from another committed source or tree.
Do not rescan the parent's and siblings' raw history after every subagent.
Derive the root's subagent cost from finalized native member contributions, counting each contribution once.
Intermediate totals must not cause double counting for nested agents.
Publish project total costs from the resulting root costs, preserving project eligibility rules.

### Daily buckets

Retain the union of affected dates and scopes from the complete tree, including old or removed contributions.
Recalculate each affected day for the required project/provider and global/provider scopes from stored metadata.
Preserve the existing definitions of cost, user-message count, and creation-date session count.
Prepare expensive daily reads outside the writer.

Daily buckets reflect the currently persisted source data during progressive compute.
They do not wait for other trees contributing to the same date to finish.
Temporary partial values remain acceptable; completed work must eventually publish exact values.

### Weekly buckets

Calculate weekly cost and counts from the seven UTC daily buckets for the same scope/provider.
An absent daily row contributes zero, matching existing zero-row removal behavior.
Recalculate affected days before deriving their affected weeks.
Other daily values retain the progressive state already stored; there is no new global day-readiness gate.
Later completed trees update their days and weeks again.
Weekly publication must observe a consistent daily state through the writer transaction.
Raw item-history weekly repairs are removed from normal tree publication and recovery.

## 9. Live coexistence and stale preparation

Foreground source writes remain immediate and retain priority over background publication.
Current-version live contributions retain their ordinary incremental behavior.
Live changes to an incomplete tree retain its affected dates instead of triggering full historical repair per append.
Do not change normal message ingestion to wait for a background aggregate job.

A prepared daily value must not overwrite a newer contribution from any session in that day/scope.
Relevant invalidation covers compute batches, live appends, visibility, creation counts, date/scope movement,
deletion, and changed ownership of source contributions.
Unrelated authentication, title, or other non-contribution commits do not invalidate a prepared daily sum.

Use scoped contribution revisions or an equivalent precise observation contract.
Preparation observes a consistent read snapshot outside writer.
Publication checks its relevant observation under writer before recording the small result.
The same freshness requirement applies to prepared session, root, and project aggregates,
including contributions without a timestamp that affect costs but produce no daily key.
Mismatch retains the obligation and retries preparation outside writer.
No mismatch path performs a full historical SUM while holding the writer.

Live daily/weekly updates and background replacements remain serialized through the existing writer.
A new live delta must either appear in the prepared replacement or occur after that replacement, without loss or duplication.
Retry and coalescing must not busy-spin or halt remaining source compute.

## 10. Durable obligations and recovery

Reuse the existing `ComputeAggregateRepair` journal for source identity, incarnation, generation, affected keys, and former parents.
Record source changes and their old/new obligations atomically with source writes.
Do not reconstruct obligations solely from remaining items.
Do not delete completed-node journals just because their source compute version advances.
Retain them until their tree's associated cost, day, week, and parent work completes.

Recovery uses the same tree eligibility and bounded publication rules as normal background work.
It cannot bypass tree deferral by repairing a completed subagent independently.
Orphan journals repair retained former scopes even when their source is deleted.
Reparenting retains obligations for both former and current trees.

Partial aggregate publication is allowed. A crash leaves enough durable work for exact replay.
Exact replacement is idempotent. Settlement applies only to the observed journal generation.
Newer obligations survive old completion callbacks.
After restart, rebuild process-local caches and pending tree groups from persisted source and journals.
Do not require an expensive synchronous repair drain during startup or shutdown.

The existing finite recovery discovery, retry backoff, and foreground priority remain applicable.
Remove the unbounded whole-journal writer transaction as a fallback.

## 11. Writer responsiveness

Preparation, compression, decompression, repeated validation, and historical aggregation belong outside writer ownership.
Publication holds contain bounded guards and the writes needed for that unit.
Release writer ownership between units so the watcher and agent operations can proceed.
Keep existing cooperative yielding and dedicated worker behavior that protects the main asyncio loop.

A design that routinely holds writer ownership for one second or more is unacceptable.
Do not solve preparation invalidation with a long synchronous writer fallback.
Bounded row/byte counts do not alone establish acceptable latency; measure the entire hold.
Do not change the 2,000-item metadata batch size in this work.
Select content publication budgets during implementation validation rather than assuming smaller batches are sufficient.

Controlled real-database-copy validation must report hold distributions, maximum holds, foreground write latency,
watcher progress, asyncio availability, and whole-session duration.
Investigate oversized content and storage/commit outliers explicitly; do not hide them in average throughput.
This specification does not claim that software can guarantee a universal wall-clock bound on arbitrary disk stalls.

## 12. Acceptance and review boundaries

Correctness validation covers:

- Exact content round trips, malformed preparation, deduplication races, and current/original provenance.
- Dictionary reuse across threads and isolation across database identities.
- Cold thresholds, clock expiry, lifecycle rollback, resume, active descendants, and reparenting.
- Shared hot/cold owners, no-gain state, unchanged owners, and representation changes during preparation.
- Deep native trees, flat imports, missing parents, late children, failure, supersession, and independent spawned sessions.
- Preserved old dates/scopes, visibility changes, deletion, orphan journals, and incarnation replacement.
- Live changes before, during, and after preparation; unrelated commits; no lost or double-counted deltas.
- Daily-to-weekly equivalence for UTC boundaries, zero rows, cost precision, message counts, and creation counts.
- Crash/restart after source slices and partial day/week publication; stale generation settlement.
- Source acknowledgment progress while tree aggregates wait, foreground contention, and recovery fairness.

Performance acceptance includes reduced redundant dictionary work, no immediate validation decompression of new content,
no unchanged owner/payload writes, no per-subagent parent-history repair, and no raw weekly item-history repair.
Isolated benchmark gains are evidence for the design, not promises of whole-run speedup.

Implementation structure, SQL syntax, helper signatures, cache containers, migration operations, test file layout,
and exact scheduling constants belong in the implementation plan.
Adversarial review must challenge missing behaviors and contradictory contracts, not demand those implementation details here.

## 13. Specification review result

Two independent internal reviewers examine content/caches and tree aggregates/live/recovery.
Round one identifies three material gaps: backward clock changes, cold-cache database isolation,
and revalidation of native membership and source freshness before prepared aggregate publication.
The corrected specification covers all three, including own/root/project costs and timestamp-free contributions.
Both reviewers pass the second round with no remaining material findings.
Reviews remain at specification level. Product implementation and its validation remain future work.
