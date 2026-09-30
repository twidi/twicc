# Independent Adversarial Review

Scope: design, first-milestone implementation plan, and standalone prototype.
No production backend is implemented or approved by this report.
Reviewers use fresh contexts and read-only access to the artifacts.
All counterexamples use in-memory SQLite databases.

## Round 1

Reviewers: `sqlite_review_integrity` and `sqlite_review_plan`.
Both report that the initial plan is not ready.

### P1: UPDATE conflict replacement

An UPDATE of an ordinary UNIQUE column can delete a different referenced parent.
SQLite reports UPDATE of that column, without a DELETE authorization.
The original prototype commits an orphan without validation.

The coordinating agent independently reproduces:

```text
UPDATE OR REPLACE probe_parent SET label='parent' WHERE id=2
checks=[]; failure=None
foreign_key_check=[('probe_child', 1, 'probe_parent', 0)]
```

Correction: capture unique-index inputs and primary keys.
Check incoming relations on UPDATE of conflict-capable inputs.
Partial, expression, or generated unique dependencies conservatively affect every parent UPDATE.
New probes cover all five variants, including schema-level ON CONFLICT REPLACE.

### P1: Main-only global validation

Unqualified foreign_key_check does not check attached or temp schemas.
The coordinating agent independently reproduces:

```text
UPDATE aux.child SET parent_id=999
checks=['PRAGMA foreign_key_check']; failure=None
aux.foreign_key_check=[('child', 1, 'parent', 0)]
```

Correction: reject unsupported user-schema mutations and attachment changes before execution.
Read-only access remains allowed.
SQLite's internal temp catalog maintenance for main ALTER TABLE remains supported.
Writable schema mode is rejected, including enabled state on editor entry.

### P2: Nested observer ownership

Standard-editor fallback can replace the outer observer and restore enforcement too early.
Correction: reject nested editor/atomic entry before changing FK or observer state.
A new probe catches the rejection inside an outer migration, then verifies that a later invalid outer write is still caught.

### P2: Mixed squash replacement state

The initial full-graph test list omits 0148 applied with 0149 unapplied.
Correction: plan explicit loader/executor forward and reverse coverage for the original remaining migration and replacement bookkeeping.

### P2: Failure and fake_initial logs

Django provides no failure progress callback.
Correction: the command's handle catches/logs the active failed migration and always restores its ContextVar.
Add fake_initial coverage for start fake=False and success fake=True.

## Acknowledged Prototype Limits

- Broad known-schema nesting flags are experimental shortcuts.
- Deferred-statement identity is not production provenance.
- Full-graph upgrade tests and atomic=False tests remain implementation requirements.
- Table rebuild preservation remains a second-milestone proof obligation.

These limits are explicit in the design and plan. Review does not mark them implemented.

## Round 2

Both reviewers approve starting first-milestone implementation, with no blocking findings.
The integrity reviewer runs all 37 cases and the minimal upgrade replay successfully.
An additional atomic=False probe confirms that an unsupported attached write does not execute, enforcement and observer cleanup succeed, and earlier committed main writes remain committed.

The plan reviewer identifies one P3 wording inconsistency: an introductory sentence omits unique-conflict deletion from ordinary-write exceptions.
Correction: add that exception to the sentence, matching the detailed rules.

## Round 3

Both independent reviewers confirm the final wording correction.
Final result: no unresolved findings or blockers for the first-milestone implementation plan.

Plan approval does not establish production correctness or complete stage-2 table-rebuild optimization.

## Implementation review: Task 1

Implementation commits: `248581e8`, `b4bafaa2`.
Independent reviewer: `sqlite_task1_review` (GPT-6.1 Sol).
Initial review finds two Important defects:

- A child INTEGER PRIMARY KEY FK update through rowid can skip its required check.
- Argument-free incremental_vacuum can mutate an attached database without a write authorization.

Fix round 1 adds real regression tests, selects outgoing child relations for rowid aliases,
and replaces the no-argument PRAGMA heuristic with explicit read-only forms.
Unknown main PRAGMAs select global validation; unproved attached/temp PRAGMAs are rejected.
The independent scoped re-review marks both findings addressed, with no new breakage.
Final focused evidence: 122 tests pass; Ruff and diff checks pass.
Task 2 must still prove writable_schema entry rejection and editor lifecycle cleanup.
Task 3 must prove the standard-backend override and complete migration graph.

## Implementation review: Task 2

Implementation commits: `b56c3e35`, `12b00c6c`.
Independent reviewer: `sqlite_task2_review` (GPT-6.1 Sol).
Initial review finds two Important defects:

- Explicit COMMIT can persist invalid data before validation.
- Custom Meta.indexes can inherit standard deferred provenance during CreateModel.

Fix round 1 rejects SQLITE_TRANSACTION during atomic observation before execution.
Editor lifecycle controls run outside observation. Savepoints retain the outer transaction;
a regression verifies that RELEASE cannot commit it and final validation rolls back both writes.
Deferred index proof registration now carries exact Index provenance.
Standard field indexes and exact standard Meta.indexes retain their optimized path.
Scoped re-review approves both fixes and finds no new Important/Critical breakage.
Final focused evidence: 71 schema tests pass; Ruff and diff checks pass.

Transaction ownership clarification: migrations that explicitly complete or replace the
atomic editor transaction require the standard-backend opt-out. The authorizer enforces
this boundary without parsing SQL. Atomic=False keeps its existing partial-commit semantics.

## Implementation evidence: Task 3

Task 3 selects the optimized backend before connections open.
`TWICC_SQLITE_STANDARD_MIGRATIONS=1` selects Django's standard backend.
The migrate subclass delegates execution and output to Django and adds ContextVar-based duration diagnostics.
Both failure and success restore the caller's context. fake_initial records its final fake outcome.

Disposable tests cover the complete fresh graph, rollback through 0146, and populated replay.
The mixed state runs original 0149 apply/reverse before replacement bookkeeping, then normal replacement recording and rollback.
Facts-table, Session index, redundant SessionItem index, original records, and replacement records match each stage.
Known replay operations execute zero FK-check PRAGMAs with existing SessionItem rows.
Logging tests cover skipped, selected, global, failed validation, fake, and backward cases at verbosity 0.
The custom-operation regression keeps global checks in both directions. sqlmigrate collects without writes.

The actual production-backend probe applies/unapplies 0147 and the squash on representative in-memory tables.
Every standard sample executes four global checks; every optimized sample executes zero checks.
Three repetitions vary SessionItem rows and payload size. Median total replay times:

| Rows | Payload | Standard | Optimized |
| --- | --- | --- | --- |
| 10,000 | 256 bytes | 0.018267 s | 0.015620 s |
| 100,000 | 256 bytes | 0.058935 s | 0.016571 s |
| 100,000 | 4,096 bytes | 0.088218 s | 0.022096 s |

Seed time is excluded; seeded pages are resident. These are not cold-disk or live-startup predictions.
Final focused evidence: 212 migration and peer tests pass in 27.29 seconds.
The original standalone synthetic probe also passes. makemigrations reports no changes; Ruff passes.
The complete suite runs once: 6 failed, 6213 passed, 21 skipped, 61 warnings in 348.10 seconds.
The six failures reproduce with the standard backend in a disposable in-memory database.
Five wait-reply cases fail on missing provider configuration. One log-retention case has a fixed-date fixture.
Those tests and production files remain unchanged from the Task 3 base.
Warnings comprise two existing AsyncMock coroutine warnings and 59 Click protected_args deprecations.
The extra custom-operation regression runs separately after the complete-suite collection.
Independent Task 3 review remains pending. Stage-2 rebuild preservation remains pending.
