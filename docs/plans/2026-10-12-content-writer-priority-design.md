# Foreground priority during content compute

## Purpose

Normal application use takes precedence over background compute and periodic content compression.
Reduce whole-session writer contention without changing stored content, ownership, metadata, or recovery semantics.
This amendment builds on `2026-10-11-session-item-content-extraction-spec.md` and the contention investigation in its audit.
It does not authorize different cold rules, scratch cleanup, API payloads, or storage schema.

## Scope

Correct compute application scheduling and background representation publication.
Keep Django, SQLite, the provider compute subprocesses, and one-result-at-a-time application.
Do not rewrite every writer caller or introduce persistent operation state.
Do not add dependencies, migrations, indexes, or a process executor.

Implementation occurs in the isolated `fix/content-writer-priority` worktree.
The existing running feature worktree keeps its current code and database.
Independent reviewers approve this design and its implementation plan before product code changes.
Independent reviewers approve code and verification before any integration or restart.

## Priority contract

Foreground includes API/CLI writes, live ingestion, agent registration/lifecycle, and explicitly requested hot promotion.
Existing unclassified writer callers retain foreground priority to preserve their behavior.
Background includes compute publication, initial bulk ingestion, startup promotion recovery, and periodic compression.
Background work receives service only while no competing foreground request needs admission or writer ownership.
FIFO order applies among eligible requests within each priority class.
Foreground queue/demand pressure registers before resource acquisition and spans dispatch without gaps.
A mixed job queue must not let background initialization block foreground jobs behind it.
Continuous foreground activity may delay background work indefinitely. This is an accepted consequence of user priority.
When foreground traffic stops, pending background work resumes without lost results or stranded futures.

An already-admitted transaction cannot be preempted safely.
Foreground waits for that transaction to commit or roll back, then takes precedence over the next background publication.
Rows and byte limits bound work size, not absolute latency.
One oversized content, one SQL query, or final atomic publication can exceed the normal slice duration.
Telemetry must expose these exceptions instead of claiming zero blocking.

Admission and writer ownership retain their existing order and cancellation protections.
Background work must not reserve admission while waiting behind a foreground writer.
Its acquisition of admission plus writer must be coordinated as one resource request.
Foreground registration can still acquire admission before requesting writer ownership.
Normal writer-only callers remain supported.
The coordinator must permit the writer request of an admission holder; otherwise an admission waiter can cause deadlock.

## Maintenance demand scheduling

Recovery and demanded promotion advance at bounded batch boundaries, rather than finishing an entire target first.
A foreground demand arriving during recovery or compression receives the next eligible maintenance turn.
A waiting background resource request cannot block the dispatcher that services foreground demand.
Admitted publication drains; running codec work drains before worker reuse.
Foreground target requests use FIFO with deduplication and background-to-foreground upgrade.

Scheduling priority and recovery eligibility are separate values.
Background compute bindings also produce promotion wakeups; these must not become foreground demand automatically.
Capture scoped write-priority origin when registering the commit callback, then propagate it through the thread-safe enqueue callback.
A later background invalidation cannot downgrade existing foreground demand.
This prevents repeated high-priority full-session promotion walks after each background extraction slice.
Preserve explicit-versus-recovery eligibility and target-only promotion semantics.

## Resumable compute application

Keep one pending result with its run identity, observations, phase, committed cursors, and reference-operation handle.
Keep provider result FIFO, including the terminal `done` marker.
Do not consume a successor result while its predecessor remains pending.
Service queued writer jobs between publication steps and while preparation runs.
A background wait must not suspend the writer coordinator before those jobs can dispatch.

Each preparation produces bounded inputs without admission or writer ownership.
Read and codec preparation use a separate application worker, leaving the existing writer executor available.
Each publication acquires coordinated resources, validates observations, writes one slice, and releases resources.
Capture fresh active identities under admission for every publication.
Use fresh eligibility time; discard unsafe compressed candidates after an intervening activation or owner change.

Preserve current metadata, override, legacy extraction, and representation reconciliation order.
Advance cursors only after commit.
Keep final session fields, facts, links, versions, aggregate baseline, and readiness publication atomic.
Move only safe post-commit notifications outside ownership; post-commit DB writes acquire their own writer lease.
Acknowledge each result exactly once after terminal application handling.
Partial successful chunks remain valid on failure or supersession, with compute versions still outdated.

## References and observation validity

TEMP counts remain an optimization for both current and original FK assignments.
A resumable operation must not inherit the old whole-session exclusion assumption.
Before count use, verify target incarnation, history, offset/upper line, root identity/incarnation, and expected root epoch.
Audit every production FK mutation before relying on those fences.
Only this operation's committed mutations advance its expected root epoch.
Foreign changes supersede the operation; never silently refresh observations while retaining old counts.
Shared-owner changes require separate publication-time checks.
Unresolved ancestry requires exact persisted reconciliation or another proven target fence, never a guessed root certificate.

TEMP state uses an explicitly pinned physical SQLite connection on the application worker.
Do not rely on Channels connection cleanup preserving a connection between executor calls.
Initialize counts through bounded metadata preparation, without long writer ownership.
Validate the initialization observation before publication uses those counts.
Activate reference and placement ContextVars only inside this operation's calls.
Unrelated writer jobs cannot inherit them.
Detect connection loss and abort safely before cached state is used.
Close operation resources on success, supersession, failure, cancellation, abandonment, and shutdown.

## Final-publication costs

Instrument preparation, publication, lock waits/holds, final subphases, and commit without logging payloads.
Measure activity bucket cache hits/misses and metadata-change causes.
Preserve exact aggregate repair unless an independently reviewed optimization proves an equivalent baseline and all mutation deltas.
Zero item updates alone do not permit skipping repair.
A progressive bucket-repair optimization may use the existing committed per-run cache after its mutation contract is audited.
It must preserve metadata-change and cache-eviction fallbacks.
Do not move required final aggregate consistency after compute-version publication.
Do not add startup index creation to hide this cost.

Django DEBUG BLOB rendering is a separate measurement confounder.
Report DEBUG modes separately. Do not toggle global DEBUG during threaded work.
A scoped logging optimization requires its own evidence and review; it is not required to change scheduling safely.

## Review and acceptance

Use barrier-based tests for priority, preparation waits, publication interleaving, and cancellation.
Verify same-session append/replacement, sibling/root changes, owner changes, and intervening starts.
Verify connection loss, no ContextVar leakage, partial retry, terminal ordering, and shutdown.
Retain exact facts, ownership, costs, activity counts, readiness, and versions in equivalence tests.
Run the backend suite before completion and report every failure or warning.
Use synthetic isolated databases for scheduler/load measurements; no live DB mutation is part of this correction.

Acceptance requires foreground precedence at publication boundaries and unchanged data correctness.
Total background throughput may decrease because foreground priority takes precedence.
Document remaining longest atomic holds and any unoptimized final-publication cases.
No review or test provides an absolute guarantee against regressions; evidence and isolated rollout reduce the risk.
