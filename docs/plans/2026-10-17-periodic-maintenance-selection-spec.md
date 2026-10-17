# Periodic content maintenance: root selection and plaintext indexing

Date: 2026-10-17.
Status: implemented on `feature/content-storage-compression`; see [validation](2026-10-17-periodic-maintenance-selection-validation.md).
Planning review: adversarially reviewed in two rounds by two independent reviewers before implementation.
Baseline: `feature/content-storage-compression`.

## 1. Outcome and decisions

Reduce the read cost of both periodic maintenance stages.
Select hot roots directly in SQL before inspecting their content references.
Select plaintext contents directly in SQL through a partial index before evaluating compression eligibility.
Keep the existing pass-scoped session cache and candidate-only payload loading and publication.

This specification updates the periodic selection rules in
[Session-local content eligibility and bounded maintenance](2026-10-17-session-content-cold-policy-and-maintenance-spec.md).
It replaces periodic recovery discovery at every native depth with hot-root selection.
It replaces the unfiltered content metadata scan with an indexed plaintext scan.
The established session-local compression policy remains the eligibility rule for all content owners, including subagents.

## 2. Evidence

The 2026-10-17 compute finishes at 13:22:48 Europe/Paris, after approximately 2 hours 15 minutes.
The following periodic pass finishes at 13:32:17, after 568.3 seconds.

| Measurement | Observed value |
|---|---:|
| Sessions inspected during preliminary discovery | 15,005 |
| Hot sessions receiving reference probes, including subagents | 2,687 |
| Hot roots in a later date-based SQL selection | 164 of 4,546 roots |
| Contents inspected | 3,970,574 |
| Contents already compressed | 2,839,580 |
| Contents rejected by owner eligibility | 1,129,991 |
| Contents newly compressed | 1,002 |
| Payload bytes loaded | 2,717,275 |
| Bytes saved | 1,432,219 |
| Publication batches | 11 |
| Cumulative writer hold | 296.4 ms |
| Maximum writer hold | 29.6 ms |

The pass loads 15,009 session metadata records, including four reloads after invalidation.
The dominant avoidable work is selection and reference inspection, rather than actual compression.
The 164-root count excludes the in-memory activity override; it is not a permanent expected count.

An isolated disk-backed copy compares the same plaintext metadata query, in 100-row pages.
Each complete query traversal returns 1,130,850 rows in 11,310 queries.

| Variant | Traversal | Index creation | Index size |
|---|---:|---:|---:|
| No selection index | 256.2 s | — | — |
| Index on `codec` | 146.0 s | 243.7 s | 50.5 MiB |
| Partial index on `id WHERE codec = 'plain'` | 143.9 s | 241.0 s | 12.9 MiB |

SQLite uses both tested indexes.
These are sequential single traversals without OS cache eviction.
They measure SQL metadata retrieval, not the complete maintenance pipeline.
The small difference between indexed variants is not evidence of a repeatable speed advantage.
The partial index offers comparable selection speed with substantially less storage.

## 3. Stage 1: select and restore hot roots

### 3.1 Root selection

Select only `Session.type = 'session'` rows with established content extraction identity/version, using the existing storage readiness criteria.
Apply hot eligibility in SQL before pagination.
Do not enumerate every extracted session and discard cold sessions in Python.

Use one eligibility time for the selection traversal.
The SQL predicate must match the established session-local policy:

- Use the latest non-null value of `created_at`, `last_started_at`, `last_updated_at`, and `last_stopped_at`.
- A hidden or archived root is cold when this value is at least 15 days old.
- Every other root is cold when this value is at least 30 days old.
- A root with all four dates null is hot.
- Equality at the threshold belongs to the cold side.
- A root known active in memory is hot regardless of its stored dates.

Include active roots explicitly in the candidate set without duplicating roots already selected by dates.
SQL must handle nullable dates correctly; a null date must not mask a later non-null date.
Pages may remain bounded at 128 roots using a stable cursor.
Filtering the candidate population, rather than changing the page size, provides this optimization.

### 3.2 Targeted reference inspection and restoration

For selected roots, inspect only that root's current/original content references through the existing bounded restoration path.
Skip already-plaintext representations before loading payload bytes.
Restore compressed representations that the selected hot root requires.
Publish only necessary representation or existing readiness-state changes through bounded writer operations.

Resuming or restoring a root does not enqueue restoration of its subagents or deeper descendants.
Periodic preliminary discovery must not enumerate subagents merely because they have independent hot metadata.
Existing explicit restoration for a requested individual session and startup recovery retain their entry points.
A failed individual-session restoration retains its target for bounded retries during the running process.
This includes a subagent target; retry must not depend on another lifecycle event or periodic descendant rediscovery.
Use targeted retries outside writer ownership between attempts, with foreground precedence at each publication boundary.
A failed target must not indefinitely prevent unrelated targets or periodic compression from progressing.
Startup recovery reconstructs interrupted restoration through its existing discovery path after a process restart.
This change concerns the periodic discovery population, not decoding a requested subagent message.

Reuse eligible root metadata in the pass cache rather than discarding selection results and loading unchanged fields again.
Preserve committed invalidation and current-activity checks before an actual representation change.
If a candidate becomes cold or otherwise invalid before publication, avoid overwriting newer state.
A root becoming hot after the selection cursor passes continues through the existing lifecycle/targeted restoration path.
The periodic selection is not the sole signal for live restoration.

## 4. Stage 2: indexed plaintext selection

### 4.1 Index and query contract

Add a partial index on `SessionItemContent.id` with predicate `codec = 'plain'`.
The metadata selection query must include this predicate and the existing `id > cursor` condition.
Retain ascending ID order and bounded pages.
The executed query, including its Django-generated predicate, must use the partial index on representative populated data.

This stage selects plaintext contents for possible compression.
Already-compressed contents do not enter its metadata pages.
A dictionary change does not make compressed representations candidates for this plaintext-selection pass.

Apply the existing no-gain rule to plaintext candidates:
A no-gain record excludes a content only when its attempt version matches the current provider compression policy.
A missing or older attempt version allows a new attempt when the owners are eligible.
This exclusion may occur during bounded metadata preparation; index eligibility must not depend on mutable policy identifiers.

Keep the existing limits of 100 content rows and an 8 MiB preparation budget.
Keep the existing single-oversized-content handling.
Advance the cursor across inspected plaintext rows even when all are rejected by no-gain or owner eligibility.
An empty indexed result completes the selection pass.

### 4.2 Metadata, owners, and payloads

For each indexed page:

1. Read content identity, representation, size, provider, and attempt metadata without fetching payload bytes.
2. Apply the current no-gain policy rule.
3. Read owner lists only for remaining candidates.
4. Collect owner IDs and fill missing or invalidated entries in the pass cache with grouped queries.
5. Require every owner to be resolved, cold, and inactive, using its own session-local state.
6. Load payloads only for eligible candidates and prepare compressed representations outside writer ownership.
7. Publish actual changes or new no-gain decisions using the existing candidate-only validation and transactions.

A hot root does not prevent compression of an independently cold subagent's exclusive contents.
A hot, active, missing, or unresolved owner prevents compression of a shared content.

Reuse valid cached session metadata across pages and publication.
Evaluate thresholds from cached dates and the relevant current time.
Watcher, lifecycle, UI/CLI, compute, and activity changes retain targeted invalidation.
A stale cache fill cannot reinstall an entry invalidated during its load.

Publication checks the candidate's current identity, representation and ownership before applying prepared bytes.
If a candidate becomes compressed, gains a hot owner, or otherwise changes, skip or reevaluate that candidate under existing rules.
Other valid candidates continue; a stale candidate does not restart the whole page.
Do not reread payloads or decode freshly compressed bytes merely to validate publication.
Pages without publication candidates request no writer lease or write transaction.

### 4.3 Cursor and concurrency behavior

The index changes when ingestion inserts contents and when a representation changes between plaintext and compressed.
Use immutable IDs for traversal, not offsets in the changing result set.
A concurrent removal from the index must not skip later eligible IDs or prevent termination.
A newly plaintext row below the cursor can wait for the next periodic pass when its owners remain cold.
A hot-session restoration remains the responsibility of the existing targeted path.
Concurrent ingestion, restoration, and activity must not allow stale prepared compression to overwrite a newer required plaintext representation.

## 5. Migration and index lifecycle

The release consolidates this branch's migrations.
Create the partial index while the new content table is empty, before initial ingestion or content extraction.
The multi-minute populated-table index build measured above is not the intended user startup path.
Verify this ordering in the release migration graph.

SQLite maintains the index as compute and ingestion insert contents.
It also maintains membership when compression or restoration changes `codec`.
Measure the additional write cost during representative compute and representation transitions.

An already populated disposable worktree can use a separate benchmark setup for validation.
Benchmark preparation time must be reported separately from production startup behavior.

## 6. Scheduling and observability

Keep the periodic deadline at 30 minutes after startup, gated by unfinished compute and accepted source applications.
Schedule the next pass 24 hours after completion.
Keep foreground precedence, bounded background publication, and existing pause/resume behavior at compute boundaries.

Report stage identity in progress and completion metrics.
Separate root selection/reference inspection from plaintext content selection and publication.

Stage 1 reports roots selected, roots inspected, restoration candidates, representations changed, payload bytes loaded, and elapsed time.
Stage 2 reports plaintext rows inspected, no-gain skips, ineligible owners, prepared candidates, changes, and elapsed time.
Both stages retain cache, resource-wait, writer-hold, cancellation, and failure diagnostics.

The new `scanned` count represents selected plaintext rows, not all rows in the content table.
Do not add a complete content scan merely to count compressed rows excluded by the index.
Labels and benchmark comparisons must distinguish the old and new scanned populations.
Progress must remain visible during long read-only selection or reference checks.

## 7. Acceptance and performance validation

### 7.1 Root selection

- SQL selection agrees with the shared hot/cold policy across hidden/archived states and both inactivity thresholds.
- Cover null dates, mixed null/non-null dates, future dates, and exact threshold equality.
- Include active roots whose stored dates otherwise classify them cold.
- Exclude hot subagents from periodic preliminary discovery, including those under cold roots.
- A selected hot root restores its compressed contents without restoring descendants.
- A shared content restored for a root remains available to its other owners.
- A root becomes active after its selection cursor passes; targeted restoration completes without waiting for the next periodic pass.
- A requested subagent restoration fails transiently, then succeeds through targeted retry without another lifecycle event.
  Unrelated restoration and compression work continue during its retry wait.
- Repeated pages retain valid cached metadata; relevant committed changes invalidate only affected entries.

### 7.2 Indexed compression

- The actual parameterized ORM selection uses the partial index and returns only plaintext rows in ID order.
- Matching-policy no-gain rows do not load payloads; older-policy and null-version rows remain eligible for reevaluation.
- Multiple providers retain their own dictionary and policy selection.
- An all-rejected page advances the cursor; an empty result finishes.
- Already-compressed contents are neither loaded nor rewritten by this pass.
- A page with three eligible candidates loads only their payloads, subject to existing oversized-row handling.
- Shared owners, owner-list mutations, active transitions, invalidations during cache fill, and concurrent representation changes retain publication safety.
- Index removals and insertions during traversal preserve the cursor contract.

### 7.3 Benchmarks and release validation

Compare the old and new periodic stages on the same isolated, disk-backed dataset.
Measure stage durations, query counts, payload bytes, cache loads, useful changes, writer holds, and foreground write latency.
Cover a mostly conformant post-compute dataset and genuinely eligible new plaintext contents.
Include sparse plaintext ranges, repeated owners, shared contents, and large payloads.

Benchmark the production metadata projection and ORM path, not only an ID-only query.
Separate query selection gains from restoration, compression, cache, and publication costs.
Report cache conditions and active background load; avoid universal latency claims from a single traversal.
Verify that actual hot-root selection replaces the previous all-depth discovery work.
Verify empty-table index creation before compute and quantify index write overhead during compute.

Acceptance requires the intended query plan, correct candidate populations, preserved publication safety, and measured selection improvement.
No fixed improvement percentage for the complete maintenance pass is asserted by the existing query-only benchmark.

## 8. Review and delivery

This document specifies behavior and validation requirements.
Detailed task ordering, helper signatures, and SQL construction belong in the implementation plan.
Two independent reviewers completed the adversarial specification review.
Round 1 identified a lost recovery fallback when periodic discovery stops visiting subagents.
The correction retains failed individual restoration targets for targeted retries without another lifecycle event.
It also requires unrelated maintenance progress during retry waits.
Acceptance scenarios cover this failure and roots becoming active after the selection cursor passes.
Round 2 confirms that both reviewers find no remaining specification blocker or important defect.
No unresolved specification decision is recorded.

Implementation and migration execution have not started.
