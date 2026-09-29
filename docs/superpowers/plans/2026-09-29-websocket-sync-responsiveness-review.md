# Adversarial implementation-plan review

Date: 2026-09-29
Reviewed baseline: `5ad56736`.
Plan: [WebSocket responsiveness implementation](2026-09-29-websocket-sync-responsiveness.md).
Spec: [Approved design and review clarifications](../specs/2026-09-29-websocket-sync-responsiveness-design.md).
Result: **PASS after corrections.** All three reviewers report no remaining major defect in their reviewed scope.

## Method and scope

The user explicitly requests an adversarial review before implementation.
Three fresh reviewers receive the plan and source paths, without the author's conversation history.
They examine separate areas and construct concrete failure cases.
The author verifies findings against source, corrects the documents, and requests independent rechecks.

| Reviewer | Scope | Final result |
| --- | --- | --- |
| `/root/plan_transport_adversarial` | Tasks 1–2 and 10: executor, heartbeat, Channels lifecycle, tests, benchmark isolation | PASS after correction |
| `/root/plan_facts_adversarial` | Tasks 3–6: extraction, historical evidence, readiness, publication, replacement | PASS after correction |
| `/root/plan_scheduling_adversarial` | Tasks 7–9: enrichment, transactions, aggregates, watcher queue, migration, completion | PASS after corrections and follow-up refinements |

This review validates a plan, not implemented behavior.
No product code, running database, dependency installation, or server state changes during this work.
The Channels failure probe below uses installed dependencies and synthetic coroutines only.

## P1 findings and dispositions

### R1 — Receiver exceptions bypass Channels cleanup

**Original instruction:** deliver an upstream receive failure as a terminal receiver exception, then join the transport reader.

**Counterexample:** an authenticated connection joins `updates`, then the upstream receiver raises.
`AsyncConsumer.__call__` propagates the exception without dispatching `websocket.disconnect`.
The normal group cleanup does not run.
In the installed `await_many_dispatch`, awaiting the already-failed receiver during cleanup raises again before cancelling `channel_receive`.
Joining only the new transport reader leaves the Channels task and group membership behind.

**Evidence:** `src/twicc/asgi.py:523` and `:772`; `src/twicc/settings.py:324`; installed `channels/consumer.py` and `channels/utils.py`.
The author reproduces the dependency behavior with two synthetic receivers:

```text
channel_receive_pending_after_dispatch_exit: True
probe_cleanup_complete: True
```

The probe explicitly cancels its remaining task afterward. It does not access Django or the database.

**Correction:** convert receive/send failures into one synthetic disconnect after admitted events.
Keep failure provenance separate from the receiver result.
Add idempotent consumer cleanup for all exits, including partial setup and repeated cancellation.
Test the actual Channels consumer for transport-reader, channel-receiver, and group-membership cleanup after admitted writes drain.

**Recheck:** transport reviewer passes the corrected task 2.

### R2 — Single-record extraction cannot validate process ownership

**Original interface:** `extract_history_facts(parsed, line_num)` has no access to preceding evidence.

**Counterexample:** identical process output can follow `exec_command` or `write_stdin`.
Only the former is a valid starter under the existing resolver.
Code-mode ownership can additionally require `wait → cell → exec` lookups.

**Evidence:** `src/twicc/providers/codex/compute.py:3525–3549`.
`ContentAnalysis` does not supply all these relationships, and live ingestion does not call that batch analysis hook.

**Correction:** provide a read-only `HistoryFactContext` with bounded prior-call and cell lookup.
Batch reads earlier accumulated evidence; live reads earlier batch evidence plus the appropriate persisted-history path.
Preserve the existing process-output line bound through the wait/cell/owner chain, including reused owner IDs.
Add identical-output/different-call, reused-ID, and wait-announcement regressions.

**Recheck:** facts reviewer passes tasks 3–6.
No independent irreversible source-loss defect is confirmed: examined normalizers preserve needed source fields or original-content markers.

### R3 — Rollback loses enrichment after a concurrent replacement

**Original contract:** retain the current cache entry on rollback, but never restore an entry replaced by a newer value.

**Counterexample:** borrow A, put B under the same call ID, rollback, retry.
The retry sees B or nothing, instead of the original diff data A.
Restoring A into the current slot would instead destroy B.

**Correction:** reserve A for its exact source record outside the current-entry slot.
The retry can reuse A while B remains available independently.
Explicit clear and committed history replacement invalidate reservations.
Unused retry reservations expire after 300 seconds; active borrows remain pinned.

**Follow-up refinement:** claim each token for exactly one source record at first borrow.
Otherwise two records sharing a call ID in one uncommitted slice can both consume A, unlike the original one-shot pop.
Tests now cover repeated IDs within one slice, with and without a newly captured B.

**Recheck:** scheduling reviewer passes the final ownership and expiry contract.

### R4 — Failed migration can strand path waiters

**Original contract:** park deferred paths until the successful replay hook wakes them.

**Counterexample:** migration holds a path, `process_path` waits, then migration fails with `replay=False`.
There is no replay and no further filesystem event. The waiter never finishes.

**Evidence:** `src/twicc/providers/codex/background_compute.py:600–630`.
Failure releases the lease without calling the replay callback.

**Correction:** notify the watcher on every release, with ready, failed, or cancelled outcome.
Preserve the path before removing migration state.
Settle failed/cancelled waiters independently of successful replay.
Keep release tokens through an in-flight callback so release between the deferral check and queue completion cannot lose its wake-up.
Separate callback disposition from the optional search-index request.

**Recheck:** scheduling reviewer passes failure-without-event and release-race behavior.

### R5 — Smaller slices multiply retained historical scans

**Original instructions:** reuse existing two-pass processing and halve the slice size when it takes too long.

**Counterexample:** every slice reloads all historical message IDs and recomputes whole-session costs.
For 150,000 distinct message-ID records, slices of 500 imply approximately 22.4 million prefix-ID visits.
Slices of one can imply approximately 11.25 billion visits.
These are workload-count calculations, not measured production timings.

**Evidence:** `src/twicc/providers/compute_base.py:3793–3800`, `:4085–4099`; `src/twicc/core/models.py:685–705`.

**Correction:** use indexed per-ID existence checks, not historical ID materialization.
Maintain exact current-version aggregate deltas from changed rows, preserving Decimal precision, nullability, eligibility, and UTC buckets.
Apply the same consistency contract to every contribution-changing transaction, including background pre-apply chunks.
An outdated session uses full affected-aggregate repair within that transaction until normal compute establishes its baseline.
Use normal provider compute-version rebuilding to repair older partial applications.
Do not adaptively shrink the expensive outdated path.

**Follow-up refinements:**

- An outdated child can invalidate a current parent's cached totals and shared activity buckets. Its committed chunks must repair those too.
- A parent can have correct raw-item totals while an outdated child's cached `self_cost` remains wrong. Full parent repair must synchronize direct-child costs before relying on the partial child index for nullability, without advancing child compute versions.
- Session counts belong to `created_at` buckets. The first user message can arrive in a later slice or day.

The benchmark now includes many message IDs and non-null costs, a large replay, and a small append to an established session.
It checks query shape and row visits or VM steps, not only elapsed time.

**Recheck:** scheduling reviewer passes the final aggregate contract, including the direct-child baseline correction.

### R6 — A moving EOF can prevent `process_path` completion

The author raises this additional interleaving during review; the scheduling reviewer confirms it against current behavior.

**Counterexample:** a session keeps appending faster than slices drain.
A waiter tied to `has_more=False` never finishes, even after all records present at its request have committed.
The existing `process_path` performs one finite file read.

**Correction:** capture a source-generation token and the end offset of the last complete record present at request time.
Finish that waiter when its finite target commits, while newer appends remain queued.
Exclude an incomplete trailing record from the target.
Deletion or replacement terminates obsolete targets; a later replay captures a fresh one.
The generation token is process-local file coordination, not a new index version or durable builder state.

**Recheck:** scheduling reviewer passes the finite-target and replacement semantics.

## Final scope and remaining verification

The plan retains the user's central constraint: historical construction uses normal compute versions only.
The aggregate baseline correction also uses normal compute, with no new builder or readiness field.
The spec records these review clarifications so implementation does not receive contradictory instructions.

No remaining P1 issue is identified after rechecks.
Implementation still needs the prescribed regression suites, workload measurements, and real-client checks.
Passing this review does not prove that a future implementation is free of concurrency defects.

Documentation validation: plan task structure, links, unchecked implementation steps, and whitespace are checked before commit.
Product tests are not run for documentation-only changes.
