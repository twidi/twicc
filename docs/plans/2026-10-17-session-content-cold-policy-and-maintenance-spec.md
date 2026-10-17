# Session-local content eligibility and bounded maintenance

Date: 2026-10-17.
Status: adversarially reviewed in two rounds by two independent reviewers; implemented on the feature branch.
Implementation evidence: [Validation report](2026-10-17-session-content-cold-policy-and-maintenance-validation.md).
Baseline: the current `feature/content-storage-compression` worktree.

## 1. Outcome and relationship to existing specifications

Determine content compression eligibility from each owning session's own lifecycle.
Apply the same rule to roots and subagents at every depth.
Complete eligible compression during that session's background compute.
Make periodic maintenance mostly metadata inspection when compute has already established the required representations.

Load unchanged session metadata once per maintenance pass.
Use explicit invalidation when known application events change that metadata.
Publish only actual candidate changes, in bounded transactions with foreground precedence.
Expose maintenance progress, useful work, resource costs, and writer holds.

This specification supersedes the following content-related rules in earlier specifications:

- A subagent inherits its root's cold state or activity protection.
- A current root compute version is required to compress a subagent's contents.
- Maintenance rereads unchanged owner/session metadata at every publication.
- A primary-key self-update reserves SQLite write ownership before publication reads.

The prior publication specification is [Compute publication and content maintenance](2026-10-16-compute-publication-and-content-maintenance-spec.md).
The existing cold thresholds remain 15 days for archived/hidden sessions and 30 days otherwise.
Scratch cleanup retains its existing independent policy.

## 2. Evidence and diagnosed causes

The compute run starts on 2026-10-16 at 18:27:58 Europe/Paris.
Its last source result is applied at 20:35:10, approximately 2 hours 7 minutes later.
All persisted sessions then have the expected provider compute versions.

At 08:43 on 2026-10-17, maintenance is still executing its publication write guard.
Logs record 110 long storage-writer operations after compute completion.
Their cumulative reported duration is approximately 5 hours 37 minutes.
The last such completed operation in that inspection is logged at 02:14.
There is no pass-completion log or persisted progress cursor available to establish a final duration.

The guard runs `UPDATE core_sessionitemcontent SET id = id WHERE id = ?`.
SQLite's query plan contains four scans of `core_sessionitem` for its referencing foreign keys.
The primary-key lookup itself is indexed; the foreign-key checks cause the scans.

An isolated SQLite benchmark uses 200,000 child rows and two unindexed foreign keys.
The primary-key self-update takes approximately 26.2 ms median and produces four child-table scans.
A non-key self-update takes approximately 0.002 ms and produces no child-table scans.
This in-memory benchmark confirms the mechanism, not a disk-latency guarantee.

The compute cold-placement path rejects subagent compression while its root has an old compute version.
Roots complete after their subagents, so this condition leaves eligible subagent contents in plaintext.
A metadata-only sample of 181 contents finds 33 cold plaintext contents without a no-gain flag.
Inspected examples belong to subagents whose roots are now current.
This sample establishes the defect; it does not estimate the total affected population.

A read-only repeated-owner probe calls `_owner_readiness()` three times.
It records three SQL queries initially, then one query on each repeated call.
The existing lifecycle cache therefore does not remove all repeated owner-metadata reads.

## 3. Session-local cold policy

### 3.1 Eligibility

For content eligibility, use the requested session's own fields:

- `archived` and `hidden`.
- `created_at`, `last_started_at`, `last_updated_at`, and `last_stopped_at`.
- Current activity known to the application for that session.

Use the latest known non-null lifecycle timestamp as the inactivity reference.
An archived or hidden session is cold after 15 days of inactivity.
Every other session is cold after 30 days of inactivity.
A currently active session is hot regardless of those timestamps.
Unknown lifecycle information cannot authorize compression.

A parent's activity does not make an inactive child hot.
A child's activity does not make unrelated sessions in the tree hot.
Content eligibility does not require loading native ancestry or checking the root's compute version.
Parent relationships continue to serve tree processing, independently of this compression decision.

### 3.2 Shared contents

A content can be compressed only when every session in its owner list is eligible.
An owner that is hot, active, missing, or unresolved prevents compression.
One hot owner requires the shared content to remain or become plaintext.
The content's owner list remains the source for this traversal; maintenance does not reverse-scan SessionItem references.

### 3.3 Consistent consumers

Use the session-local rule in:

- `Session.is_cold_for_content` and batch eligibility helpers.
- Initial sync, content ingestion, and background compute placement.
- Compute reconciliation and final representation validation.
- Periodic compression and persistent plaintext restoration.
- Lifecycle transitions and resume/start handling.
- Content/message `contains` eligibility and their CLI descriptions.

A hot root can coexist with compressed old subagent contents.
Starting or resuming one session restores that session's required contents through the existing targeted restoration flow.
It does not restore all descendants merely because they share a root.
Reading a cold session continues to decode only the requested contents through the established read path.

## 4. Background compute and ingestion

Each session's compute prepares eligible compressed representations directly.
It uses the final lifecycle metadata available for that session's result.
A subagent does not wait for its parent or root to complete.

Reconciliation uses every content owner's session-local eligibility.
It retains existing compatible compressed representations and recorded no-gain decisions.
Final validation accepts compressed contents for a cold subagent even when its root remains hot or outdated.
Plaintext readiness checks apply to the session that actually requires plaintext.

The initial-sync and live-ingestion paths apply the same placement policy to the lifecycle metadata they know.
If those metadata are incomplete, ingestion remains conservative and the session's compute establishes the final representation.
Later reference changes preserve complete ownership and the hot-owner plaintext requirement.

After compute completes a cold tree, immediate maintenance should find its representations already conformant.
This expectation assumes unchanged ownership, activity, policy, and lifecycle eligibility between the two operations.
An unchanged tiny content with no compression benefit remains plaintext with its no-gain policy recorded.

## 5. Maintenance metadata cache

### 5.1 Lifetime and loading

Create an empty session-metadata cache at the start of each periodic pass.
Release that pass's entries when it finishes or is cancelled.
Existing shared lifecycle-cache facilities may support it, but the pass exposes its own hit/load/invalidation counters.

For each batch, form the set of candidate owners.
Load only the IDs absent from the cache, using bounded grouped SQL queries.
Store the fields required for session-local eligibility and publication decisions together.
Evaluate time thresholds from cached dates and the current time, or an equivalent expiry deadline.
Do not retain a hot/cold boolean indefinitely across a threshold transition.

Unchanged owners are loaded once per pass, including across preparation and publication.
Do not reread version, identity, or lifecycle metadata merely because another content has the same owner.
Remove obsolete root-readiness requirements from this eligibility path.
Any remaining necessary cached field must have an explicit mutation/invalidation path.

### 5.2 Invalidation sources

Invalidate affected session entries when their relevant metadata change through:

- Watcher ingestion and lifecycle updates.
- Archive/hidden updates from UI, CLI, or other existing service entry points.
- Start/resume/stop and activity changes known to the agent managers.
- Compute publication or initialization that changes a retained cached field.

Reuse the existing lifecycle and writer mutation hooks.
Watcher filesystem polling is not an additional maintenance task.
An event that changes one session invalidates that session, rather than every entry in the tree or cache.

Publish invalidation for persisted changes after commit, before releasing writer ownership.
A rollback must not publish new cached metadata as committed state.
A cache fill racing with invalidation must not reinstall the obsolete value.
The existing cache synchronization mechanisms can implement this contract.

Known runtime activity protects the specific active session immediately under the existing admission mechanism.
It must not rely solely on a later JSONL event or timestamp reload.

### 5.3 A change between preparation and publication

Preparation may use a cached cold decision that later becomes invalid.
Before publication, inspect the affected cache entries and current activity in memory.
Reload only entries that are missing or invalidated, using grouped reads.
Reevaluate contents whose owners changed.

Skip a candidate that is no longer eligible.
Continue publishing other valid candidates in the batch.
Do not discard or repeatedly retry an entire batch because one owner changed.
An owner that remains unknown cannot authorize compression and is reconsidered through a later relevant operation.

## 6. Periodic compression pass

### 6.1 Preparation without writer ownership

Keep the existing limits of at most 100 content rows and an 8 MiB preparation budget.
A single oversized content may require its own batch under the existing oversized-row handling.

For each bounded content page:

1. Read metadata and size information without fetching payload bytes.
2. Exclude contents already compressed with the selected dictionary.
3. Exclude contents with a no-gain result for the current compression policy.
4. Collect the remaining contents' owners and complete the session cache.
5. Keep only contents whose owners are all cold and inactive.
6. Load payloads only for those candidates.
7. Prepare compressed representations on the codec worker.

Preserve no-gain results so later passes do not repeat the same ineffective compression.
Reuse validated dictionaries already held by the process.
Do not decode or recompress already conformant contents to prove conformity.

A page without candidates advances its cursor without requesting the writer or an SQLite write transaction.

### 6.2 Targeted publication

Request background writer/admission resources only when prepared candidates require publication.
Foreground work retains precedence at each new resource grant.

Within one bounded publication:

1. Start an explicit SQLite write transaction before authoritative validation reads.
2. Fetch only candidate content rows' metadata.
3. Revalidate the candidate's logical identity, representation state, and owner list against preparation observations.
4. Reuse valid session-cache entries; reload only invalidated or missing entries.
5. Check current activity for the specific owning sessions.
6. Publish valid representation changes and newly determined no-gain policy state.
7. Commit and release the writer.

For a page of 100 rows with three candidates, only those three enter publication validation.
The other 97 rows are not reread by that transaction.
Do not fetch the candidate payload again merely to compare its stored bytes.
Logical identity and representation metadata provide the relevant concurrency validation.
Do not decode newly compressed bytes as a publication check.

If another operation has already established the requested representation, skip the redundant update.
If ownership changes, reevaluate the current owners before deciding whether publication remains valid.
A newly added hot owner prevents compression.
A replacement or invalid candidate is skipped without overwriting newer state.

### 6.3 SQLite transaction contract

Remove the primary-key self-update from `_acquire_publication_write_guard`.
Use explicit write-transaction acquisition, such as `BEGIN IMMEDIATE`, for this publication boundary.
SQLite write ownership must precede reads whose result authorizes the subsequent update.
No artificial UPDATE is needed to obtain it.

Integrate this transaction with Django's commit, rollback, callbacks, and nesting rules.
Do not issue a second BEGIN inside an existing transaction.
Keep any transaction-mode selection scoped to this publication path and restore connection behavior on exit.
Retain the application writer coordinator; the SQLite transaction is not a replacement scheduler.

The publication query plan must not scan SessionItem to acquire write ownership.
Retain the existing FK constraints and the decision not to index their SessionItem columns.

Cancellation must drain an admitted synchronous publication before releasing its writer resources.
Preparation remains outside writer ownership and yields between bounded units through the existing worker scheduling.

## 7. Persistent plaintext restoration

Use the same session-local eligibility and targeted metadata/cache rules for restoration decisions.
Restoration must skip already-plaintext contents before loading payloads.
Reference inspection remains bounded and targeted to the requested session.
If any owner requires plaintext, a shared content cannot remain compressed.

An interruption after a committed cold-to-hot change must not lose the required restoration.
Startup recovery and periodic recovery must discover incomplete restoration for individual sessions at every subagent depth.
Recovery eligibility uses that session's own state, even when its root remains cold.
The application must retain or reconstruct pending restoration from committed state without relying on an in-memory wakeup surviving restart.
Recovery inspects candidates in bounded pages and restores only the sessions that require plaintext.
This also applies to shared contents with a hot subagent owner and other cold owners.

Compression maintenance must not compete ahead of required foreground restoration.
An affected candidate is rechecked when an owner becomes active or hot during preparation.
Existing source/reference consistency checks remain distinct from compression-policy evaluation.

## 8. Scheduling and observability

The initial periodic deadline remains 30 minutes after startup.
The pass waits until provider background compute and accepted source applications finish.
If the deadline has already elapsed, the pass becomes eligible when those operations finish.
The next periodic deadline is 24 hours after the current pass completes.
If compute resumes, periodic work pauses at a bounded boundary and resumes without resetting its cursor.

Emit a pass-start event, periodic progress events, a pass-completion event, and cancellation/failure events.
Distinguish periodic compression from targeted persistent plaintext restoration.
Assign an operation identifier to correlate preparation and publication diagnostics.

Report the following counters and timings:

| Area | Required measurements |
|---|---|
| Progress | Start/end timestamps, elapsed time, rows scanned, current cursor, batches completed |
| Selection | Already compressed, no-gain skips, ineligible owners, candidates prepared |
| Publication | Representations changed, no-gain records changed, already conformant skips, invalidated/stale skips |
| Cache | Owner IDs requested, hits, IDs loaded, grouped queries, invalidations, reloads |
| Data volume | Payload bytes loaded, candidate raw/stored bytes, bytes saved by published changes |
| Execution | Preparation, resource wait, transaction duration, writer hold, and foreground write wait |

Progress must remain visible during long read-only scans and when there is no useful compression work.
Use periodic summaries and bounded statistics collection rather than a log event for every content.
Report writer-hold percentiles, maxima, and counts above relevant thresholds at pass completion.
Do not obtain progress by repeatedly counting or scanning SessionItem or reading every payload.
When no exact total is available, report counts and cursor without inventing a completion percentage.

## 9. Acceptance scenarios

### 9.1 Session-local policy

- A hot root with an old cold subagent allows compression of that subagent's exclusive contents.
- An active subagent remains plaintext even when its parent is cold.
- Nested descendants use their own dates and archived/hidden state.
- Subagent compute compresses eligible contents before the root becomes current.
- Compute final validation and contains eligibility agree with the model property.
- Resuming a root does not restore unrelated cold descendants.
- A subagent becomes hot, its lifecycle change commits, and the process stops before restoration.
  After restart, recovery restores its exclusive and shared contents even when its root remains cold.
  Cold sibling sessions do not acquire restoration work solely because of their ancestry.
- A shared content with one hot owner remains plaintext.
- Threshold boundaries and missing lifecycle values retain the defined behavior.

### 9.2 Cache and concurrency

- Repeated pages with unchanged owners perform one initial metadata load per owner for the pass.
- Preparation and publication reuse the same valid cache entries.
- Changing one owner invalidates only its affected entry and triggers a grouped reload when required.
- Watcher writes, UI lifecycle changes, compute mutations, and starts/resumes all reach the invalidation contract.
- A rollback cannot publish uncommitted cache state.
- An in-flight cache load cannot restore metadata invalidated during that load.
- A cold owner becoming hot between preparation and publication prevents its candidate compression.
- Other valid candidates still complete without whole-batch retries.
- A changed owner list is detected, including a newly added hot owner.
- A candidate already published by another operation produces no redundant update.

### 9.3 SQL and scheduling

- An empty candidate batch requests no writer lease and performs no write transaction.
- A 100-row page with three candidates rereads only those candidates during publication.
- Publication validation does not reload their payloads.
- SQLite write acquisition performs no referenced-primary-key update and no SessionItem FK scan.
- Transaction rollback, callbacks, nesting, cancellation, and connection restoration remain correct.
- Foreground writes pass between background batches and required restoration takes priority.
- Compute gating retains cursor progress and the established 30-minute/24-hour schedule.

### 9.4 End-to-end runs and benchmarks

Run compute on representative cold roots and subagents, then run maintenance with unchanged eligibility and ownership.
Expect zero new compression and zero candidate publication for their already conformant contents.
Recorded no-gain contents must also produce zero repeated compression attempts.

Benchmark a metadata-only pass with repeated owners and a pass with genuinely eligible plaintext contents.
Include small, medium, large, and oversized payloads, plus contents shared by several sessions.
Measure cache loads, useful work, SQL plans, writer holds, and foreground-write latency separately.
Use an isolated database for mutating benchmarks and a realistic disk-backed copy for durable-write measurements.

Compare acquisition plans against a large SessionItem table with the actual unindexed child FKs.
Adding unrelated SessionItem rows must not add FK-scan work to content publication.
Exercise a foreground write while a large maintenance pass runs; HTTP reads alone are insufficient evidence.

The performance objective is bounded publication cost proportional to the selected candidates, their payload sizes, and their owners.
A metadata-only pass must not acquire the writer merely to inspect conformant contents.
Any writer hold at or above one second requires investigation and an explained trace before validation is accepted.
A reproducible hold at or above one second blocks acceptance, even when its cause is understood.
Record smaller outliers and percentiles as well; one second is an investigation threshold, not a target hold duration.

## 10. Delivery boundary

Implementation planning must map every content-eligibility consumer and every retained cache field to its update/invalidation paths.
The implementation plan must specify the scoped Django transaction mechanism and its nesting tests.
It must also establish realistic maintenance benchmarks and foreground-write checks before declaring the work complete.

This document specifies the agreed behavior and its validation requirements.
It does not start implementation or authorize a running-instance reset.
