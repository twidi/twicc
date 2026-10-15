# Compute selection and content work

Date: 2026-10-15.
Status: adversarially reviewed; ready for user review before implementation planning.

Two independent reviewers approved the revised specification at specification level.
The second review round confirmed resolution of fallback-completeness and launcher-retry ambiguity.

## 1. Outcome and precedence

Keep the application responsive while background compute processes existing session data.
Remove repeated history searches, redundant plaintext scans, and repeated selection over complete pending inventories.
Foreground application work retains priority over background compute and periodic compression.

This specification updates the selection contract in the
[root compute specification](2026-10-14-root-compute-and-unified-statistics-spec.md).
It also updates cold-cache, plaintext-demand, and periodic scheduling behavior in the
[content optimization specification](2026-10-13-content-and-tree-aggregate-optimization-spec.md).
These documents continue to define their other contracts.

## 2. Tool-call lookup

### 2.1 Lookup sources

Use the exact tool-call ID whenever the caller knows it.
This is an equality lookup, not a substring search through serialized JSON.

For earlier records in the current source computation, use its in-memory history context.
The watcher also uses this context for earlier records in its current ingestion batch.
Preserve the rule that a lookup only sees records before the requested source line.
Preserve existing provider handling of repeated IDs and multiple blocks on one line.

For persisted history, use indexed `SessionHistoryFact` rows when the source session's compute version is current.
Facts select candidate source lines. Load only the source records needed to validate the provider-specific match.
Facts do not need to duplicate complete tool inputs.

A child context contains the child's records, not its parent's records.
Resolving a child's launcher uses the identified launcher's history.
When the launcher's persisted facts are incomplete, retain a bounded raw-history fallback.
Avoid a complete launcher scan when a current indexed lookup establishes an exact match or authoritative absence.
An incomplete fallback search remains unresolved. Exhausting a read budget does not establish absence.
Relevant history beyond the first fallback page must remain searchable.

### 2.2 Repeated failed searches

A watcher batch means the bounded set of new JSONL records ingested in one slice.
Coalesce identical failed launcher lookups within this batch.
Eleven child records carrying the same child marker must not trigger eleven identical scans of the same launcher history.

An unsuccessful lookup does not prevent ingestion of the child records.
A later batch or applicable launcher update retries after the parent's evidence becomes available.
Launcher ingestion or compute completion must permit resolution even after the child's final batch.
Resolving such a link must not require another child record.
Keep temporary lookup results local to the relevant source history and batch.
New in-batch evidence must remain usable after an earlier unsuccessful lookup.

## 3. Content metadata and cold-state reuse

### 3.1 Shared cold cache

Periodic compression must reuse the existing committed lifecycle cache and native-root mapping cache.
Subagents inherit the native root's content cold policy.
Contents sharing owners or roots must reuse previously loaded lifecycle metadata.

Committed lifecycle changes invalidate or refresh affected cache entries.
Time-dependent eligibility and active-session protection remain authoritative during a long pass.
Use the existing invalidation mechanism rather than an independent pass-long Boolean snapshot.

Load other required owner metadata in bounded groups.
Reuse it within the batch instead of querying the same owners separately for every content.
This metadata includes extraction readiness and compute readiness used by the existing compression contract.
Final publication must respect lifecycle changes committed after preparation.

### 3.2 Load payloads only for transformations

Compression and persistent decompression first load content metadata without `payload`.
Metadata determines whether a representation change is necessary.

For compression, skip already conforming compressed rows and unchanged no-gain attempts before loading payloads.
For decompression, skip already conforming plaintext rows before loading payloads.
Load payloads only for selected transformations, with bounded row and byte budgets.
Existing oversized-row handling remains available.

Deduplicate content IDs within each prepared batch.
Current and original references can point to the same content.
Metadata validation must still identify incomplete references and invalid representation metadata.
Representation publication preserves concurrent ownership and reference changes.
Actual content reads retain the existing integrity checks.

## 4. Source mutations and plaintext demand

Separate recording a storage mutation from requesting a plaintext traversal.
`content_state_epoch` identifies changes that can invalidate an in-progress storage observation.
Incrementing this field does not inherently require a full session scan.

The ingestion transaction already establishes plaintext for the new references required by a hot session.
Appending these references to an already hot session must not schedule a complete plaintext traversal.
The same applies to activity timestamp changes that leave the session hot.
Keep mutation fencing necessary for concurrent compression and decompression publication.

Create persistent plaintext demand when an actual transition or unresolved storage state requires existing references to become plaintext.
Examples include resuming a cold session and reusing compressed content in a hot session.
Where ingestion already satisfies the exact reused references, it does not create another full-session request for those references.
Unresolved legacy extraction and an interrupted real plaintext request remain recoverable.

Ordinary cold-session reads continue to load and decompress only requested contents.
Resuming a session persistently restores the session's required plaintext, without eagerly restoring every subagent.
The existing substring-search gate can validate scoped representation metadata without loading plaintext payloads.
Mutation handling must preserve that availability when the session remains fully plaintext.

## 5. Root selection on demand

### 5.1 Select one root

Select native roots only: `type='session'` and `parent_session_id IS NULL`.
Filter by provider and outdated compute version: NULL or lower than the provider's current version.
Rank by descending `Session.mtime`, the source file modification time stored in the database.
Equal mtimes need no ID ordering.

Select one candidate when the bounded worker pipeline can accept work.
Avoid loading all candidate roots before choosing one.
Filter source age with the existing 30-second stability rule.
Exclude the small set of roots already admitted or temporarily deferred for an existing retry reason.

An admitted root's own members remain eligible within its current tree operation.
Do not interpret root exclusion as excluding the rest of that admitted tree.
Preserve deepest-first subagent processing, descending mtime within each level, and root-last completion.
Required descendant applications finish before successful tree completion.
Current members are skipped. Failed and superseded required work remains eligible for automatic retry.

The stability check also applies to each member source before admission.
A blocked tree can yield to another eligible root after its outstanding applications drain.
Source observations and newly discovered membership are refreshed at admission boundaries.

### 5.2 Index contract

Add a partial root index supporting provider filtering and descending mtime selection.
The candidate key is `(provider, mtime DESC, compute_version)`.
The predicate is `type='session' AND parent_session_id IS NULL`.
Session ID is returned and used for small exclusions; it is not a sort key or an explicit index key.

Validate the final Django-generated query with SQLite query plans and representative pending fractions.
The query should use the root index without a temporary sort for descending mtime.
Keep index creation a schema operation without historical content movement.

### 5.3 Wakeups and progress

Remove the once-per-second reclassification of every initially outdated session and subagent.
Do not repeatedly construct large `id IN (...)` lists to select the next root or report progress.

Use application outcomes and relevant source updates to reconsider work.
Retain only the small deferred-work state needed for retry deadlines and stability waits.
When only recent sources remain, wait until an eligibility deadline or a relevant wakeup.
Re-read authoritative database state before admitting work.
Outstanding compute application results continue to drive bounded pipeline admission.

Progress still counts roots, not subagents, and reports successfully current roots.
Use bounded confirmations or database counts instead of periodic whole-inventory reclassification.
The coordinator must still detect newly eligible database work and finish after admitted applications drain.
Restart recovery derives pending compute from the persisted compute versions.

## 6. Periodic compression waits for compute

The first periodic compression deadline remains 30 minutes after backend startup.
Periodic content maintenance waits while an enabled provider has an unfinished background compute run.
This includes source-age waits, pending applications, and automatic recovery between worker attempts.
A temporarily absent worker does not mean its run has finished.
Use compute completion state for database work, independently of `initial_sync_succeeded`.

If compute finishes before the deadline, wait for the remaining startup delay.
If compute finishes after the deadline, start the overdue periodic pass when background admission permits it.
Subsequent completed passes retain the existing 24-hour cadence.
Apply the same compute gate before subsequent passes.
If a new compute run starts during a pass, pause at a bounded batch boundary and resume afterwards.

Demand-driven plaintext restoration remains available while compute runs.
It retains foreground priority when needed by application activity.
The existing directory-cleanup eligibility rules remain attached to their existing cleanup operation.
The shared periodic pass must not bypass the compute gate through another periodic entry point.

## 7. Validation requirements

Validate behavior and performance with representative small and large native trees.
Keep source data and the main instance isolated from destructive validation.

Required cases:

- Exact persisted lookup returns the correct source block without scanning unrelated tool calls.
- Earlier current-batch calls resolve from memory before their records are committed.
- Missing launcher evidence causes one equivalent lookup per unchanged batch context, with successful later retry.
- A match beyond the first fallback page remains reachable; incomplete searches never establish absence.
- Parent evidence arriving after child ingestion stops still resolves the launcher link.
- Shared owner/root decisions reuse cached metadata and observe committed lifecycle changes.
- Already conforming representations load no payload for the transformation decision.
- Hot appends avoid full plaintext traversals; genuine restoration still completes after interruption.
- Root selection uses the partial index with most, few, and no outdated roots.
- Recent, admitted, failed, and superseded sources make progress without inventory-wide polling.
- Root completion and progress remain correct across descendant failures and restart.
- Periodic compression waits through both providers' unfinished runs, then respects its startup and recurring deadlines.
- Foreground restoration remains usable during compute and periodic-pass deferral.

Report SQL counts, payload reads, event-loop responsiveness, and writer hold distributions separately.
Elapsed multi-slice application time must not be described as one continuous writer hold.

## 8. Evidence used for this specification

Read-only probes on the worktree database found these local costs:

| Operation | Observed time | SQL calls |
|---|---:|---:|
| Launcher-wide scan for one known ID | 3.24 s | 5,981 |
| Exact persisted ID lookup | 11.7 ms | 5 |
| Existing in-memory tool-call lookup | 0.016 ms | 0 |
| Current eligibility checks for 100 contents with two owners | 82.7 ms | 200 |
| Batched cold-cache first access for those owners | 0.73 ms | 1 |
| Repeated cached access | 0.08 ms | 0 |

The cold-cache probe measures lifecycle decisions, not all publication-readiness checks.
The observed 37.4-second live slice is consistent with repeated launcher scans.
Its historical logs do not establish the exact number of failed attempts.

Index probes used copied metadata for 14,824 sessions and 4,538 roots.
An mtime-leading partial index removed the temporary sort across representative pending fractions.
The initial benchmark included an ID key.
A separate three-column probe, including small ID exclusions, also used the root index without a temporary sort.
The final Django-generated query still requires query-plan validation.
A temporary full Session-table copy measured approximately 15 ms for that experimental index creation.
These measurements are evidence, not portable latency guarantees.
