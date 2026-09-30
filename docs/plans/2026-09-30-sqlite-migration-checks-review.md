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
