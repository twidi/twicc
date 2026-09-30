# SQLite migration checks: implementation validation

This record preserves implementation reports and independent review evidence.
Workflow-local paths below identify historical test artifacts.
All database writes use disposable databases. Main integration is not performed.


---

## Archived progress.md

# SDD ledger — plan: docs/plans/2026-09-30-sqlite-migration-checks-implementation.md

User approval: 2026-09-30, "let's go". Execution: SubAgentDriven Development.
Worktree: /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks
Branch base: 87ffc25f. Two independent plan reviewers approve after three rounds.

## Preflight interface and task consistency scan

| Tasks | Produced / consumed or shared file | Result |
| --- | --- | --- |
| 1 / 2 | read_schema, EffectObserver, CheckDecision -> schema editor | Compatible; task 2 uses task 1's actual interfaces. |
| 1 / 3 | Metadata/effect decisions -> integration assertions | Compatible; task 3 verifies real graph behavior. |
| 2 / 3 | schema.py lifecycle -> FK duration logging with migration context | Task 3 also needs a small schema.py integration change; its global file map already includes schema.py. |
| 1 | Metadata/observer tests vs API/files | Consistent; enabled writable_schema entry check belongs to the later editor while observer setter rejection belongs here. |
| 2 | Exact standard-operation and immutable deferred provenance vs experimental shortcut | Consistent; cannot copy prototype trust flags. |
| 3 | Backend opt-out + command logging + full graph vs tests | Consistent; user approval excludes server restart and merge. |

Ruling: Task 3 may modify schema.py to connect migration context and FK timing logs — task 2 owns lifecycle, task 3 owns logging integration — if wrong, task 3 review must correct that integration change.

## Progress

No implementation tasks complete.

Task 1: dispatched to sqlite_task1_implement (model gpt-6.1-sol; BASE87ffc25f79da436f702310b697875dbaf371d73e).

Ruling: Use TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest for worktree tests — the fresh project environment does not automatically select optional test dependencies, and inherited pytest can otherwise execute main sources — if wrong, environment setup changes only this worktree and test evidence must be repeated.
Ruling: Task 1 anchors .gitignore db/ to /db/ — the runtime root DB directory must stay ignored while the new src/twicc/db package must remain visible to git and rg — if wrong, nested runtime data may appear untracked; existing SQLite filename patterns still protect data files.
Task 1: initial RED using inherited pytest is invalid evidence; implementer repeats RED with the worktree test extra and local interpreter.

Task 1: initial review requires fixes: child rowid-alias FK updates and attached argument-free mutating PRAGMAs. Fix round 1/5 starts at b1d9d293; reviewer sqlite_task1_review.
Documentation/prototype evidence preserved in b1d9d293 (no product-code changes).

Task 1: fix round 1/5 (2 addressed, 0 open; commits b1d9d293..b4bafaa2). Re-review sqlite_task1_review approved, no new breakage.
Task 1: complete (commits 87ffc25f..b4bafaa2, review clean). 122 focused tests pass. Deferred verification items explicitly assigned: writable_schema entry/transaction/observer/nested cleanup to Task 2; standard-backend override to Task 3.
Task 2: ready for dispatch; BASEb4bafaa289ee60645c4ff3a3096869d18cae5913.
Task 2: dispatched to sqlite_task2_implement (model gpt-6.1-sol, high); no concurrent implementers.
Task 2: implementation DONE at b56c3e3562092232414a4bba81407d6e7d407223; 52 schema and 174 combined tests pass. Fresh reviewer sqlite_task2_review dispatched (GPT-6.1 Sol high), package b4bafaa2..b56c3e35.

Task 2: initial review requires fixes: explicit transaction completion bypasses pre-commit checks; custom Meta.indexes subclass gets trusted deferred proof. Fix round 1/5 starts b56c3e35.
Ruling: Reject user transaction-control statements during atomic schema observation before they execute, allowing only editor-owned lifecycle controls — validation must precede commit and global fallback cannot undo an early commit — if wrong, migrations with explicit transaction customization require the standard-backend opt-out. Preserve atomic=False semantics without claiming full rollback.
Task 2 transaction ruling clarification: SQLITE_TRANSACTION is rejected during atomic observation; SQLITE_SAVEPOINT remains supported only while outer BEGIN prevents RELEASE from committing the owning transaction. Implementer must cover Django nested savepoints and RELEASE.

Task 2: fix round 1/5 (2 addressed, 0 open; commits b56c3e35..12b00c6c). Scoped re-review approved spec and quality; no new Important/Critical breakage.
Task 2: complete (commits b4bafaa2..12b00c6c, review clean). 71 schema tests pass; existing snapshot/effect suite remains unchanged. Task3 settings/logging/full graph verification remains pending.
Task 3: dispatched to sqlite_task3_implement (GPT-6.1 Sol high), BASE12b00c6cc45a336ff2ed10d3f8c521012a058264. Controller authorizes preserving/appending and committing review.md evidence. Broader suite appropriate after integration; no live operations.
Task 3 broad evidence: 6 failed, 6213 passed, 21 skipped, 61 warnings. Targeted standard-backend comparison reproduces all 6 failures: five wait_reply no_provider_configured cases and date-dependent log-retention case. Affected files unchanged from BASE; unrelated fixes excluded. Warnings: 2 AsyncMock coroutine, 59 Click protected_args deprecations. Full details requested in report.
Task 3: DONEd3207b2b; 212 focused pass, Ruff pass, no model drift; reviewer sqlite_task3_review dispatched with 12b00c6c..d3207b2b package. User confirms log-retention test fixed later on main5767bc2c; no duplicate correction or rebase.


---

## Archived task-1-report.md

# Task 1 report

Status: DONE.

Commit: `248581e882b07f33682d0313364abcf47e1c87fa`.

Subject: `feat(sqlite): observe migration effects and schema dependencies`.

Model trailer: `Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>`.

## Scope and environment

- Worktree: `/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks`.
- Branch: `bugfix/sqlite-migration-checks`.
- Base: `87ffc25f79da436f702310b697875dbaf371d73e`.
- Python: `3.13.14`. Django: `6.0.4`. SQLite: `3.53.1`. pytest: `9.0.3`.
- All SQLite writes use in-memory disposable databases.
- No migration, backend integration, settings change, compute-version change, server operation, or new dependency.
- Existing design, plan, review, and prototype artifacts remain untouched and untracked.
- No subagent or reviewer dispatch.

Path verification:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -c 'import twicc,pytest,sqlite3; print("twicc:",twicc.__file__); print("pytest:",pytest.__file__); print("sqlite:",sqlite3.sqlite_version)'
```

```text
twicc: /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/src/twicc/__init__.py
pytest: /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/.venv/lib/python3.13/site-packages/pytest/__init__.py
sqlite: 3.53.1
```

Inherited `VIRTUAL_ENV` mismatch warnings are expected. No `--active` or `uv pip` command runs.

## Implemented files

| File | Change |
| --- | --- |
| `.gitignore` | Change `db/` to `/db/`. Keep runtime data ignored while exposing the source package. |
| `src/twicc/db/__init__.py` | Package marker. |
| `src/twicc/db/backends/__init__.py` | Package marker. |
| `src/twicc/db/backends/sqlite3/__init__.py` | Package marker. |
| `src/twicc/db/backends/sqlite3/metadata.py` | Main-schema metadata and ASCII identifier normalization. |
| `src/twicc/db/backends/sqlite3/effects.py` | Authorizer observation, rejection, and scope selection. |
| `tests/test_sqlite_migration_metadata.py` | Ten metadata tests. |
| `tests/test_sqlite_migration_effects.py` | Seventy effect tests. |

## Interfaces for Task 2

Metadata module:

- `canonical_identifier(value: str) -> str`: normalize ASCII uppercase letters only.
- `ForeignKey(child_table, parent_table, child_columns, parent_columns)`: immutable NamedTuple.
- Table identifiers and FK column identifiers are canonical.
- Child and parent column tuples preserve FK order.
- An omitted parent-column list resolves to the parent primary key.
- A missing implicit parent key remains `()`.
- `IndexSchema(name, unique, origin, columns, partial, has_expressions, uses_generated_columns)`: immutable NamedTuple.
- Index names preserve the original SQL name. Key-column names are canonical and ordered.
- Expression and rowid key entries set `has_expressions`; they do not invent column names.
- SQLite rowid primary keys have a synthetic unique index with `name=''`, `origin='pk'`.
- Synthetic indexes are conflict inputs. They are not physical indexes and must never enter index-removal matching.
- `TableSchema(name, primary_key, foreign_keys, generated_columns, indexes)`: immutable NamedTuple.
- Table names preserve the original SQL name. Generated columns are a `frozenset[str]`.
- `read_schema(connection) -> dict[str, TableSchema]`: accepts the native SQLite connection.
- Dictionary keys are canonical. Metadata comes from `main.sqlite_schema` and main-schema PRAGMAs.
- Metadata discovery does not read application rows.

Effects module:

- `WriteEffect(action, table, column, source)`: immutable NamedTuple.
- `action` uses SQLite authorization integer constants.
- `table` and UPDATE `column` are canonical. Non-UPDATE `column` is `None`.
- `source` preserves the trigger name supplied by SQLite.
- `AuthorizationEffect(action, arg1, arg2, database, source)`: immutable NamedTuple with original callback arguments.
- `CheckDecision(scope, tables, reasons)`: immutable NamedTuple.
- `scope` is `none`, `tables`, or `global`.
- `tables` is a canonical `frozenset[str]`. `reasons` is a tuple of strings.
- Global decisions have an empty table set. Global covers main only.
- `EffectObserver()` requires no connection and executes no SQL.
- `observe(action, arg1, arg2, database, source) -> int` installs directly with native `set_authorizer()`.
- Public `effects: list[WriteEffect]` contains data writes.
- Public `schema_effects: list[AuthorizationEffect]` contains unproved main-schema effects and catalog writes.
- Public `unknown_reasons: list[str]` contains permanent global-fallback reasons.
- `decision(before, after) -> CheckDecision` uses dependency metadata from both snapshots and selects surviving children.
- Any remaining `schema_effects` entry makes the decision global.
- Task 2 can remove only exactly proved schema entries. It must preserve independent data writes and unknown reasons.
- No separate blanket snapshot-change fallback overrides Task 2's future exact schema proof.
- Main ALTER authorizations permit SQLite's subsequent internal temp catalog maintenance.
- This temp exception requires Task 2 to reject entry when `writable_schema` is already enabled.
- This observer rejects all `writable_schema` setters. It permits the read-only query.
- Unknown action codes select global for main or unspecified schemas. Unknown attached/temp codes return `SQLITE_DENY`.
- Callback processing exceptions return `SQLITE_DENY` and retain a global-fallback reason.

## RED evidence

Initial `uv run pytest` commands use the principal checkout's pytest executable because the fresh worktree lacks the test extra.
Their `ModuleNotFoundError: No module named 'twicc.db'` output is **invalid RED evidence** and does not count.

The environment correction selects the existing declared test extra:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -c 'import pytest,twicc; print(pytest.__file__); print(twicc.__file__)'
```

The worktree installs the four packages already declared by the test extra. Both imports point inside the worktree.

Corrected metadata RED repeats with the new metadata module temporarily absent.
The driver uses `try/finally` to restore that task-owned file after the command.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python - <<'PY'
from pathlib import Path
import subprocess
source = Path('src/twicc/db/backends/sqlite3/metadata.py')
temporary = source.with_suffix('.red')
source.rename(temporary)
try:
    result = subprocess.run(['uv', 'run', 'pytest', 'tests/test_sqlite_migration_metadata.py', '-q'])
    print('RED exit code:', result.returncode)
finally:
    temporary.rename(source)
PY
```

```text
ModuleNotFoundError: No module named 'twicc.db.backends.sqlite3.metadata'
1 error in 0.20s
RED exit code: 2
```

Effects RED runs before the effects module exists:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_effects.py -q
```

```text
ModuleNotFoundError: No module named 'twicc.db.backends.sqlite3.effects'
1 error in 0.18s
```

Self-review regression RED after adding SQLite-prefix tests:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py -q
```

```text
FAILED tests/test_sqlite_migration_metadata.py::test_sqlite_prefix_without_underscore_is_a_user_table
FAILED tests/test_sqlite_migration_effects.py::test_parent_delete_checks_user_table_with_sqlite_prefix
2 failed, 77 passed in 0.37s
```

Cause: SQL LIKE's underscore wildcard excludes valid `sqliteChild` user tables.
Fix: `NOT GLOB 'sqlite_*'` excludes only SQLite's reserved prefix.

Self-review unknown-attached-action RED:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_effects.py -q
```

```text
FAILED tests/test_sqlite_migration_effects.py::test_unknown_attached_action_is_not_proven_readonly
1 failed, 69 passed in 0.33s
```

Cause: unknown attached action returns `SQLITE_OK` without proving read-only behavior.
Fix: unknown attached/temp actions return `SQLITE_DENY` before execution.

## GREEN and static checks

Metadata implementation first GREEN: `9 passed in 0.31s`.
Combined implementation first GREEN: `74 passed in 0.31s`.
SQLite-prefix correction GREEN: `79 passed in 0.31s`.

Final command before commit:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py -q && uvx ruff check src/twicc/db tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py && git diff --check
```

```text
collected 80 items
tests/test_sqlite_migration_metadata.py ..........
tests/test_sqlite_migration_effects.py ......................................................................
80 passed in 0.31s
All checks passed!
```

Exit code: `0`. `git diff --check` emits no output.
The staged diff also passes `git diff --cached --check` before commit.

The controller approves the test-extra command correction and the anchored ignore rule.
The controller defers the broad suite to Task 3, when consumers change. No broad suite runs during Task 1.

## Self-review and concerns

- Composite FK ordering, omitted parent columns, missing parents, generated columns, and Unicode identifiers have real metadata tests.
- INSERT and UPDATE replacement tests show actual child violations without a parent DELETE authorization.
- Primary and unique conflict inputs select all incoming child tables.
- Partial, expression, and generated unique dependencies select children on every parent UPDATE.
- Ordinary updates exclude unchanged unrelated tables.
- Native cursors, executemany, cached statements, and trigger sources have observation tests.
- Unsupported temp/attached DDL and DML tests compare schemas, data, schema versions, and attachments after rejection.
- Read-only temp/attached queries remain supported.
- Unknown main schema effects remain global. Independent data writes survive schema-proof removal.
- Before/after relation metadata and dropped-child filtering have scope tests.
- No unresolved Task 1 concern.
- Editor entry checks, transaction lifecycle, trusted schema matching, deferred SQL, restoration, and integration remain Task 2/Task 3 work.
- Entry with enabled `writable_schema` specifically remains Task 2, as instructed by the controller.

## Fix round 1

Status: DONE.

Review: `.superpowers/sdd/2026-09-30-sqlite-migration-checks-implementation/task-1-review.md`.

Fix base: `b1d9d293`.

Fix commit: `b4bafaa289ee60645c4ff3a3096869d18cae5913`.

Subject: `fix(sqlite): check child rowid writes and classify read-only pragmas`.

Changed files:

- `src/twicc/db/backends/sqlite3/effects.py`.
- `tests/test_sqlite_migration_effects.py`.

No metadata API or metadata implementation change is required.

### Findings and fixes

1. Child INTEGER PRIMARY KEY foreign keys skip validation after updates through rowid aliases.
   SQLite reports `rowid` for the three aliases in the tested runtime.
   Outgoing-child selection now conservatively selects tables with outgoing FKs on alias updates.
   The original incoming-parent alias selection remains intact.
2. An argument-free PRAGMA can mutate a database.
   The observer now recognizes proven read-only operation forms with two explicit lists.
   Metadata/check PRAGMAs accept their read-only table, index, or count argument.
   Configuration query forms require an absent argument.
   Unproved temp/attached PRAGMAs return `SQLITE_DENY` before execution.
   Unclassified main PRAGMAs return `SQLITE_OK` and permanently select global fallback.

SQLite documents argument-free incremental_vacuum as a database-changing operation:
[SQLite PRAGMA reference](https://sqlite.org/pragma.html#pragma_incremental_vacuum).
The same reference documents the supported metadata and configuration query forms.

### Covering tests

- `test_child_primary_key_foreign_key_updates_through_rowid_aliases`: three aliases, table-scoped selection, and actual SQLite FK violation.
- `test_attached_argument_free_incremental_vacuum_is_denied_before_page_changes`: disposable file-backed attachment with free pages.
- That test requires rejection and verifies unchanged `page_count` and `freelist_count`.
- `test_unproved_argument_free_main_pragmas_require_global_fallback`: incremental_vacuum, optimize, and unknown_pragma.
- `test_unknown_argument_free_non_main_pragmas_are_rejected`: attached and temp schemas.
- `test_proven_argument_free_readonly_pragmas_keep_scope_none`: eleven query forms across main, attached, and temp schemas.
- Both metadata and effect test files run after the fix.

### RED evidence

Command before implementation:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_effects.py -q
```

```text
collected 112 items
FAILED tests/test_sqlite_migration_effects.py::test_child_primary_key_foreign_key_updates_through_rowid_aliases[rowid]
FAILED tests/test_sqlite_migration_effects.py::test_child_primary_key_foreign_key_updates_through_rowid_aliases[_rowid_]
FAILED tests/test_sqlite_migration_effects.py::test_child_primary_key_foreign_key_updates_through_rowid_aliases[oid]
FAILED tests/test_sqlite_migration_effects.py::test_attached_argument_free_incremental_vacuum_is_denied_before_page_changes
FAILED tests/test_sqlite_migration_effects.py::test_unproved_argument_free_main_pragmas_require_global_fallback[incremental_vacuum]
FAILED tests/test_sqlite_migration_effects.py::test_unproved_argument_free_main_pragmas_require_global_fallback[unknown_pragma]
FAILED tests/test_sqlite_migration_effects.py::test_unknown_argument_free_non_main_pragmas_are_rejected[other]
FAILED tests/test_sqlite_migration_effects.py::test_unknown_argument_free_non_main_pragmas_are_rejected[temp]
8 failed, 104 passed in 0.59s
```

Exit code: `1`. The rowid tests return `none` instead of `tables`.
The attached vacuum and unknown non-main PRAGMAs fail to raise `DatabaseError`.
Unproved argument-free main PRAGMAs return `none` instead of `global`.

### GREEN evidence

Exact command after implementation:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py -q && uvx ruff check src/twicc/db tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py && git diff --check
```

```text
collected 122 items
tests/test_sqlite_migration_metadata.py ..........                       [  8%]
tests/test_sqlite_migration_effects.py ................................. [ 35%]
........................................................................ [ 94%]
.......                                                                  [100%]
122 passed in 0.47s
All checks passed!
```

Exit code: `0`. The diff check emits no output.
The staged diff check also passes before commit.
Only the expected inherited `VIRTUAL_ENV` mismatch warning appears.

Path check repeats with:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -c 'import twicc,pytest; print(twicc.__file__); print(pytest.__file__)'
```

```text
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/src/twicc/__init__.py
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/.venv/lib/python3.13/site-packages/pytest/__init__.py
```

### Interface changes and self-review

- No public interface changes.
- PRAGMA fallback reasons now describe unclassified operations, including argument-free operations.
- Every no-argument query exemption requires an explicit name with documented read-only query semantics.
- Unknown PRAGMAs do not inherit a read-only exemption from their argument shape.
- Schema-sensitive configuration setters do not inherit their query-form exemption.
- Main schema fallback remains conservative.
- The rowid fix deliberately favors safe child selection when outgoing dependencies exist.
- The new vacuum fixture commits and closes its disposable file connection before attachment.
- No broader suite, live database access, migration, server operation, or subagent dispatch occurs.
- The two review warnings remain later-task obligations: enabled writable_schema entry rejection and editor/integration lifecycle.
- No unresolved fix-round concern.


---

## Archived task-1-review.md

### Spec Compliance

- ❌ **Issues found:** Rowid-alias updates can bypass required child checks. Argument-free PRAGMAs can mutate attached databases.
- **Important:** `src/twicc/db/backends/sqlite3/effects.py:136` omits rowid aliases from outgoing FK update selection.
- **Important:** `src/twicc/db/backends/sqlite3/effects.py:88` treats every argument-free PRAGMA as read-only.
- ⚠️ **Cannot verify from this diff:** Enabled `writable_schema` entry rejection remains Task 2 work, according to the controller-approved report.
- ⚠️ **Cannot verify from this diff:** Transaction validation, observer restoration, nested-entry rejection, and the standard-behavior override require later integration tasks.

### Strengths

- `src/twicc/db/backends/sqlite3/metadata.py:9` uses ASCII-only normalization and preserves non-ASCII identifiers.
- `src/twicc/db/backends/sqlite3/metadata.py:67` groups composite FK rows and preserves their column order.
- `src/twicc/db/backends/sqlite3/metadata.py:73` resolves omitted parent columns through the parent primary key.
- `src/twicc/db/backends/sqlite3/metadata.py:83` records unique-index inputs and uncertain dependencies. Line 91 adds synthetic primary-key inputs.
- `src/twicc/db/backends/sqlite3/effects.py:143` includes replacement-conflict inputs and uncertain unique dependencies in incoming-child selection.
- `src/twicc/db/backends/sqlite3/effects.py:148` checks dependencies from both snapshots and excludes children absent afterward.
- `src/twicc/db/backends/sqlite3/effects.py:69` prevents callback exceptions from escaping and retains a global-check reason.
- `tests/test_sqlite_migration_effects.py:1` adds real SQLite tests for replacement violations, triggers, cached statements, and unsupported mutations.
- `tests/test_sqlite_migration_metadata.py:1` adds real metadata tests without application-row discovery.

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

1. **Child rowid updates skip FK validation.** `src/twicc/db/backends/sqlite3/effects.py:136` selects outgoing checks through declared FK column names only.
   SQLite reports an update through `rowid` as column `rowid`, even when `id INTEGER PRIMARY KEY` aliases it.
   The incoming-parent branch handles these aliases at line 158. The outgoing-child branch does not.

   Disposable counterexample:

   ```sql
   CREATE TABLE parent(id INTEGER PRIMARY KEY);
   CREATE TABLE child(id INTEGER PRIMARY KEY REFERENCES parent(id));
   INSERT INTO parent VALUES(1);
   INSERT INTO child VALUES(1);
   UPDATE child SET rowid = 999;
   ```

   Observed results:

   ```text
   effects: [WriteEffect(action=23, table='child', column='rowid', source=None)]
   decision: CheckDecision(scope='none', tables=frozenset(), reasons=())
   PRAGMA foreign_key_check(child): [('child', 999, 'parent', 0)]
   ```

   This skips a real integrity violation during migrations with FK enforcement disabled.
   Resolve rowid aliases to the declared primary-key column, or conservatively select children with outgoing FKs on alias updates.
   Add real regression tests for `rowid`, `_rowid_`, and `oid` updates of a child primary-key FK.

2. **Argument-free PRAGMAs bypass attached/temp mutation rejection.** `src/twicc/db/backends/sqlite3/effects.py:88` allows `arg2 is None` without proving read-only behavior.
   `PRAGMA other.incremental_vacuum` mutates an attached database without an argument or additional write authorizations.

   Disposable counterexample setup:

   ```sql
   -- Run in a disposable file database, then attach it as other.
   PRAGMA auto_vacuum=INCREMENTAL;
   CREATE TABLE target(data BLOB);
   INSERT INTO target VALUES(zeroblob(100000));
   DELETE FROM target;
   -- Commit before attaching. Install the observer on the attaching connection.
   PRAGMA other.incremental_vacuum;
   ```

   Observed results:

   ```text
   other.page_count: 27 -> 3
   other.freelist_count: 24 -> 0
   decision: CheckDecision(scope='none', tables=frozenset(), reasons=())
   ```

   This violates the requirement to reject unsupported attached mutations before side effects.
   Prove read-only PRAGMAs by their operation semantics, rather than the absence of an argument.
   Reject unproved attached/temp PRAGMAs. Retain a global fallback for unclassified main-schema effects.
   Add a regression test that rejects argument-free attached `incremental_vacuum` and preserves page and freelist counts.

#### Minor (Nice to Have)

- None.

### Assessment

- **Task quality:** Needs fixes.
- **Reasoning:** The metadata and conservative incoming-child rules are sound in the reviewed cases. Two uncovered paths bypass required protections.
- **Checks run:** One focused disposable script tests child rowid-alias selection and attached argument-free `incremental_vacuum`.
- **Environment check:** `twicc.__file__` resolves inside `/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/src/twicc/`.
- **Test evidence:** The report records 80 passing tests and passing Ruff/diff checks. The passed suite is not rerun.
- **Warning assessment:** The focused command emits only the expected inherited `VIRTUAL_ENV` mismatch warning.
- **Review boundary:** No unchanged sources require inspection. The initial tool output truncates implementation hunks; their missing content is read from the diff package.
- **Checkout boundary:** Only this authorized review report is written. Code, index, HEAD, branch state, and live databases remain unchanged.


---

## Archived task-1-rereview-1.md

### Finding Verdicts

- **Child rowid updates skip FK validation — ADDRESSED.** `src/twicc/db/backends/sqlite3/effects.py:150` selects surviving children with outgoing FKs on rowid-alias updates.
  `tests/test_sqlite_migration_effects.py:323` tests all three aliases against a child primary-key FK.
  The test asserts the selected child and the actual SQLite FK violation.
- **Argument-free PRAGMAs bypass attached/temp mutation rejection — ADDRESSED.** `src/twicc/db/backends/sqlite3/effects.py:95` requires an explicit read-only operation classification.
  Line 99 rejects unproved attached/temp operations. Line 102 retains global fallback for unclassified main operations.
  `tests/test_sqlite_migration_effects.py:337` requires rejection of attached argument-free `incremental_vacuum` and unchanged page/freelist counts.

### New Breakage in the Fix Diff

- **None.** `src/twicc/db/backends/sqlite3/effects.py:52` limits configuration query exemptions to explicit argument-free forms.
- **None.** `src/twicc/db/backends/sqlite3/effects.py:150` adds conservative child selection without altering incoming-parent selection.

### Out-of-Scope Observations

- **None.** The earlier lifecycle and enabled-`writable_schema` obligations remain later-task checks, as recorded in the implementation report.

### Verdict

- **Fix round:** All findings addressed, no new Critical/Important breakage.
- **Evidence checked:** The appended fix report names covering tests and records `122 passed in 0.47s` plus passing Ruff/diff checks.
- **Validation scope:** The fix diff and regression assertions address both original counterexamples. No suite or counterexample rerun is necessary.
- **Checkout scope:** Only this authorized review report is written. No product, index, HEAD, branch, or live-database mutation occurs.


---

## Archived task-2-report.md

# Task 2 report

Status: DONE.

Base: `b4bafaa289ee60645c4ff3a3096869d18cae5913`.
Branch: `bugfix/sqlite-migration-checks`.
Worktree: `/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks`.
Model: `GPT-6.1 Sol`, confirmed by the controller from the selected agent model.

## Implementation

| File | Responsibility |
| --- | --- |
| `src/twicc/db/backends/sqlite3/base.py` | Subclass Django's SQLite wrapper and select the local schema editor. |
| `src/twicc/db/backends/sqlite3/schema.py` | Observe one editor context, prove standard statements, validate before commit, restore state. |
| `tests/test_sqlite_migration_schema.py` | 52 disposable-database editor and real MigrationExecutor cases. |

No settings integration or logging is included. Task 3 owns those changes.
No migration, compute-version change, dependency, server operation, or main integration occurs.
Only the three task files are staged. Existing design/review artifacts remain untouched.
No subagent is dispatched. The controller owns independent review.

## Interfaces and proof choices

- `DatabaseWrapper.SchemaEditorClass` points to `DatabaseSchemaEditor`.
- The standard `connection.schema_editor()` interface remains intact.
- The editor exposes its `observer` and final `decision` for Task 3 integration.
- `observer` is an `EffectObserver` from Task 1.
- `decision` is a `CheckDecision` from Task 1.
- `_migration_effect_editor` on the wrapper owns one active editor context.
- Nested contexts fail before changing authorizer ownership or FK enforcement.
- Entry also rejects Django atomic blocks, native transactions, and enabled `writable_schema`.
- `collect_sql` keeps ownership but does not start a transaction, disable enforcement, or install an observer.
- Collected deferred statements pass through Django's collection implementation.
- Dependency metadata is captured before observation. It is refreshed after deferred execution and observer removal.
- Validation calls Django's existing `check_constraints()` with no arguments for global scope, or original table names for selected scope.
- Validation completes before the editor exits its atomic transaction.
- No SQL parser, custom FK comparison query, migration executor copy, or global monkeypatch is added.

`StatementProof` is a NamedTuple. It stores the original statement, immutable rendered SQL, parameter tuple, operation kind, table, and index.
Statement identity alone is insufficient. Execution must match the rendered SQL and parameters.
Retaining the original statement also prevents identity reuse.

Operation contexts permit proof registration for expected objects. They never suppress an interval's effects.
Each execution matches only its own new structured `AuthorizationEffect` entries.
The proof requires exactly one expected object authorization and its recognized mechanical catalog effects.
Unexpected schema objects retain global fallback. Trigger-sourced schema effects cannot match.
Independent `WriteEffect` entries and `unknown_reasons` always remain intact.

Standard table creation proves its expected table and SQLite's automatic unique/PK indexes using real metadata.
The automatic `sqlite_sequence` table remains recognized as SQLite's mechanical creation effect.
New empty child tables need no scan. Existing children that reference a newly created parent receive selected-table validation.

Standard deletion proves the expected DROP TABLE statement. Observed parent DELETE effects select surviving incoming children.
Deleting all referencing tables removes those children from the final scope.

Index creation proves the expected table and index from Django's standard SQL builder.
Explicit AddIndex/RemoveIndex trust requires the exact standard `Index` class.
Index removal requires a real named physical index with `unique=False` in the expected table.
Synthetic unnamed PK indexes never enter removal matching.
Unique index removal and unknown physical index removal retain global fallback.

Deferred proofs snapshot rendered SQL when the standard builder returns the statement.
Appended raw statements and mutated deferred statements lack valid proof.
Deferred execution occurs once, under observation, before validation and commit.
Operation, deferred, and validation failures enter atomic cleanup with the original error.
Cleanup attempts observer removal, transaction exit, enforcement restoration, and ownership release.
The original error survives a second cleanup error. A cleanup error after success is reported.

## Requirements coverage

- Real MigrationExecutor applies and unapplies ordinary-column RunSQL and ORM RunPython writes with zero FK scans.
- Those cases verify successful migration recording and removal.
- Invalid child FK updates, parent deletes, and referenced PK updates select `child` in both migration directions.
- Atomic failure restores data and preserves the expected applied/unapplied migration record.
- CreateModel supports FKs, implicit M2M, db_index fields, Meta.indexes, and deferred unique_together indexes without unrelated checks.
- A real executor CreateModel/M2M migration applies and unapplies without checks.
- AddIndex/RemoveIndex skip checks for a known nonunique index.
- DeleteModel checks surviving children. Deleting all referencing tables skips checks.
- Native cursor writes, trigger writes, custom Index callbacks, and custom field callbacks preserve independent data and schema effects.
- A real custom migration Operation with unknown main effects retains a global check in both directions.
- Raw schema SQL, standard unique index removal, and table rebuilds use global fallback.
- Deferred indexes execute once before selected validation and COMMIT.
- Appended and mutated deferred schema statements lose trust.
- Deferred SQL failure rolls back table creation and restores connection state.
- Nested normal/atomic contexts preserve the outer observer after their rejection is caught.
- Nested contexts inside collect_sql are rejected and ownership is released afterward.
- Operation errors, real FK-check failures, and injected cleanup failures verify error precedence and restoration.
- atomic=False validation failure restores enforcement and ownership. Previously committed data remains; no rollback claim is made.
- Unsupported native attached/temp writes leave those schemas unchanged.
- Temp DDL and ATTACH are rejected before execution.
- Proven read-only access to existing temp/attached tables remains available.
- Enabled writable_schema entry is rejected before FK state changes.
- Native transaction entry is rejected before PRAGMA or observer changes, including enforcement-already-disabled entry.
- Positional arguments to Django's `_create_unique_sql` remain compatible.

## Environment verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -c 'import twicc,pytest; print(twicc.__file__); print(pytest.__file__)'
```

```text
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/src/twicc/__init__.py
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/.venv/lib/python3.13/site-packages/pytest/__init__.py
```

All writes use a fixture-owned `tmp_path/disposable.sqlite3` or disposable in-memory attachments.
The existing tests still use `twicc.settings_test`. The new fixture explicitly constructs the optimized wrapper.
Python 3.13.14, Django 6.0.4, pytest 9.0.3.
The inherited `VIRTUAL_ENV` mismatch warning is expected. No `--active` or `uv pip` runs.

## TDD evidence

Initial fixture setup hit pytest-django's database blocker. That setup error is corrected and excluded from RED evidence.
The corrected first RED run uses the real standard Django backend before either local implementation file exists.
The temporary fixture selects the standard wrapper when the local wrapper is absent.
The final fixture imports the local wrapper directly.

Command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q
```

First valid RED: `26 failed, 7 passed in 4.24s`.
Failures show unwanted global checks, missing failure restoration, unsupported mutations, nested ownership loss, writable_schema entry, and collect_sql enforcement changes.
Representative output:

```text
assert ['PRAGMA foreign_key_check'] == []
assert ['PRAGMA foreign_key_check'] == ['PRAGMA foreign_key_check("child")']
assert (0,) == (1,)
```

First implementation GREEN: `33 passed in 3.68s`.
Boundary tests characterize the implemented behavior: `46 passed in 4.37s`.

Native transaction regression RED:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q -k native_transaction
```

```text
OperationalError: cannot start a transaction within a transaction
1 failed, 46 deselected in 0.33s
```

Fix: reject native `connection.in_transaction` before PRAGMAs or atomic entry.
Following full schema GREEN: `47 passed in 4.93s`.

Positional compatibility regression RED:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q -k positional
```

```text
TypeError: DatabaseSchemaEditor._create_unique_sql() takes 3 positional arguments but 4 were given
1 failed, 49 deselected in 0.28s
```

Fix: preserve positional argument forwarding to Django's standard builder.
Following combined GREEN: `172 passed in 5.22s`.

Collected-context nesting regression RED:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q -k collect_sql_context
```

```text
Failed: DID NOT RAISE <class 'django.db.utils.NotSupportedError'>
2 failed, 50 deselected in 0.37s
```

Fix: own collected editor contexts too, then release ownership in finally.

## Final verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py tests/test_sqlite_migration_schema.py -q
```

```text
174 passed in 5.46s
```

Final focused command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q
```

```text
52 passed in 4.94s
```

Final static command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && uvx ruff check src/twicc/db/backends/sqlite3/base.py src/twicc/db/backends/sqlite3/schema.py tests/test_sqlite_migration_schema.py
```

```text
All checks passed!
```

`git diff --cached --check` passes with no output.
The staged diff contains exactly the three task files: 924 insertions.
The controller reserves the broader project suite for Task 3 integration.

## Self-review and concerns

Self-review reads the implementation and staged scope. It fixes native transaction rejection, positional compatibility, and collected-context ownership.
Every fix has explicit RED evidence before its production change.
No unresolved correctness concern remains.
Rebuild optimization remains a deliberate later-milestone limitation, with global validation retained here.
Task 3 still owns settings selection, opt-out wiring, logging, complete graph/replay validation, and broad integration tests.
The supported contract excludes arbitrary replacement or disabling of the authorizer through raw driver APIs.

## Commit

`b56c3e3562092232414a4bba81407d6e7d407223` — `feat(sqlite): validate migration effects before transaction commit`.

The commit includes a descriptive body and `Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>`.
After commit, only the pre-existing controller-owned design review file is modified.
The report remains an untracked workflow artifact, as with Task 1.

## Fix round 1

Status: DONE.
Base: `b56c3e3562092232414a4bba81407d6e7d407223`.
Review: `.superpowers/sdd/2026-09-30-sqlite-migration-checks-implementation/task-2-review.md`.

### Findings and changes

The independent review exposes two gaps in the first implementation.

1. Explicit COMMIT can persist invalid rows before validation. Native commit and executescript can follow the same path.
2. Custom Meta.indexes objects inherit trusted deferred provenance from CreateModel's outer operation context.

The editor now installs its `_authorize()` callback. It delegates ordinary authorizations to the unchanged EffectObserver.
During atomic observation, it denies `SQLITE_TRANSACTION` before SQLite executes the transaction control.
This covers BEGIN, COMMIT, and ROLLBACK through SQL, native cursors, native commit/rollback, and executescript's implicit COMMIT.
The editor starts its own transaction before observation. It completes or rolls back after removing observation.
No SQL parser is added. A denied statement adds no fictitious successful data effect.
Catching denial leaves the outer transaction and observer active.

`SQLITE_SAVEPOINT` remains supported, as approved by the controller.
An atomic editor owns an outer BEGIN transaction. Releasing a nested savepoint cannot commit that outer transaction.
The new test enters a nested Django atomic savepoint, then creates and releases a native savepoint.
It verifies the outer transaction remains active. Final FK validation rolls back writes from both paths.

atomic=False keeps its prior native transaction behavior. The new test explicitly starts and commits a native transaction there.
No complete rollback guarantee is introduced for atomic=False.

`_model_indexes_sql()` now carries each index's provenance while it calls that index's SQL builder.
Only the exact standard Index class gets the standard-index operation context.
Field index builders get a separate field-index context.
`_create_index_sql()` no longer accepts the outer CreateModel context as index provenance.
A custom Meta.indexes subclass that delegates to the standard builder retains an unproved deferred schema effect and global validation.
The existing tests still verify zero scans for exact standard Meta.indexes and ordinary field indexes.

Changed files:

- `src/twicc/db/backends/sqlite3/schema.py`.
- `tests/test_sqlite_migration_schema.py`.

Task 1 metadata/effects files remain unchanged. No settings, migrations, dependencies, or server operations change.
No broader suite or subagent is used.

### RED evidence

Command before either fix:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q -k 'transaction_controls or custom_meta_index or deferred_commit or caught_transaction_denial or explicit_native_transaction_behavior'
```

Output:

```text
17 failed, 1 passed, 52 deselected in 2.47s
```

The 14 real-executor failures cover seven control paths in both directions:
RunSQL COMMIT/ROLLBACK/BEGIN, native cursor COMMIT, native commit, native rollback, and native executescript.
The additional failures cover custom Meta.indexes, deferred COMMIT, and catching COMMIT denial.
The nonatomic native transaction test already passes. It protects that supported behavior during the fix.

Representative failures:

```text
Expected regex: 'not authorized'
Actual message: The row in table 'child' ... has an invalid foreign key ...
Failed: DID NOT RAISE <class 'sqlite3.DatabaseError'>
```

The custom Meta.indexes regression gets no FK scan instead of its expected global scan.
The failures reproduce the review findings before production code changes.

### GREEN evidence

After both fixes and the controller-requested savepoint boundary case:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py -q
```

```text
71 passed in 5.63s
```

All executor rejection cases verify original child rows, unchanged migration records, enforcement on, autocommit restored, and observer removal.
Their traces contain no user COMMIT.
Deferred COMMIT rejection restores the invalid write before it can persist.
Caught-denial validation still selects child and rolls back later invalid writes.

Static command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && uvx ruff check src/twicc/db/backends/sqlite3/schema.py tests/test_sqlite_migration_schema.py
```

```text
All checks passed!
```

`git diff --check` passes with no output.
Only the expected inherited VIRTUAL_ENV warning appears during pytest.

### Self-review and concerns

The production diff contains only authorizer transaction ownership and per-index provenance.
Authorization denies controls before they can commit or replace the editor transaction.
Django and native nested savepoints retain the outer transaction, with explicit covering evidence.
Existing deferred snapshots, schema matching, independent write observation, and original-error cleanup remain intact.
No unresolved fix-round concern remains.

Fix commit: `12b00c6cc45a336ff2ed10d3f8c521012a058264` — `fix(sqlite): protect migration transactions and index provenance`.


---

## Archived task-2-review.md

# Task 2 Review

## Spec Compliance

- ❌ Issues found. Transaction ownership does not prevent an explicit commit before validation.
- ❌ Exact `Index` provenance is absent for deferred `Meta.indexes` during `CreateModel`.
- ⚠️ Task 3 owns settings selection, logging, and complete migration graph tests. This review does not verify those requirements.
- Scope matches the three planned files. No migration or compute-version change appears in the diff.

## Strengths

- `src/twicc/db/backends/sqlite3/base.py:8` preserves the normal wrapper interface through `SchemaEditorClass`.
- `src/twicc/db/backends/sqlite3/schema.py:34` checks editor ownership and active transactions before changing FK enforcement.
- `src/twicc/db/backends/sqlite3/schema.py:72` drains deferred SQL before normal validation and transaction exit.
- `src/twicc/db/backends/sqlite3/schema.py:152` snapshots rendered statements. Execution also requires matching identity and parameters.
- `src/twicc/db/backends/sqlite3/schema.py:248` matches expected schema objects and mechanical catalog effects.
- `src/twicc/db/backends/sqlite3/schema.py:223` removes only matched schema effects. Independent writes and unknown reasons remain observable.
- `src/twicc/db/backends/sqlite3/schema.py:114` attempts every cleanup stage and preserves the original operation error.
- `tests/test_sqlite_migration_schema.py:106` uses the real executor for ordinary writes and migration-record assertions.
- `tests/test_sqlite_migration_schema.py:132` checks invalid writes in both directions, including rollback and migration records.
- `tests/test_sqlite_migration_schema.py:246` covers deferred mutation, appended statements, and execution failure.
- `tests/test_sqlite_migration_schema.py:378` verifies deferred execution precedes selected validation and COMMIT.
- `tests/test_sqlite_migration_schema.py:454` covers original-error precedence when enforcement cleanup also fails.
- `tests/test_sqlite_migration_schema.py:609` covers collected-context nesting and ownership release.

## Issues

### Critical

None.

### Important

1. **Explicit transaction completion bypasses validation before commit.**

   Location: `src/twicc/db/backends/sqlite3/schema.py:64`, `src/twicc/db/backends/sqlite3/schema.py:223`.

   The editor installs `EffectObserver.observe` directly. That observer permits `SQLITE_TRANSACTION`, including COMMIT.
   An atomic editor can therefore execute `UPDATE child SET parent_id=99`, followed by `editor.execute("COMMIT")`.
   Exit detects the FK violation and raises `IntegrityError`. However, the invalid value remains committed as `[(99,)]`.
   Enforcement and Django autocommit both return to their normal states. The earlier commit prevents atomic rollback.
   The same SQL can enter through RunSQL or a native cursor. Native `connection.commit()` uses the same authorization action.

   This violates validation-before-commit and atomic failure rollback. The supported contract excludes observer replacement, not explicit transaction completion.
   Reject transaction completion while an atomic editor owns observation. Use the authorizer action, without a SQL parser.
   Add disposable executor and native-driver cases. Verify denial before commit, rollback, unchanged records, and restored connection state.

2. **CreateModel trusts custom Meta.indexes through the standard builder.**

   Location: `src/twicc/db/backends/sqlite3/schema.py:182`.

   `_create_index_sql()` registers proofs whenever the outer operation is `create` for the model's table.
   Django's `_model_indexes_sql()` invokes each `Meta.indexes` object's `create_sql()` directly.
   This path bypasses the exact `type(index) is Index` check at `schema.py:171`.
   A custom Index subclass that delegates to the standard builder receives trusted deferred provenance and skips validation.
   The focused case returns `CheckDecision(scope='none', tables=frozenset(), reasons=())` for that subclass.

   This misses the required exact standard-operation provenance. It also gives equivalent custom indexes different trust through CreateModel and AddIndex.
   Carry the actual index provenance into deferred proof registration. Custom callbacks must not inherit trust from the outer create context.
   Add a Meta.indexes subclass regression. Keep ordinary field indexes and exact standard Meta.indexes optimized.

### Minor

None.

## Focused Checks

- The initial combined tool output truncates the schema hunk and early tests. Subsequent diff slices retrieve those missing sections.
- Changed product files are not read separately. No git command or routine test suite runs during this review.
- Named interface risk: removing proof effects must preserve independent data writes and unknown reasons.
  Checked unchanged `src/twicc/db/backends/sqlite3/effects.py`; its lists remain independent and unknown reasons force global validation.
- Named interface risk: bypassing SQLite's standard editor exit must retain Django's deferred execution and atomic cleanup semantics.
  Checked Django 6.0.4's base and SQLite schema-editor entry, exit, create, index-builder, and validation interfaces.
- Named interface risk: CreateModel's custom index callback can bypass the explicit AddIndex provenance gate.
  Checked Django's `_model_indexes_sql()` and confirmed its direct callback invocation.
- Named contract risk: raw transaction completion might be excluded explicitly.
  Checked the design's supported-contract paragraph. It excludes disabling or replacing the observer; it does not exclude COMMIT.
- A single disposable Python counterexample runs with `TWICC_DATA_DIR=$PWD` and `DJANGO_SETTINGS_MODULE=twicc.settings_test`.
  It creates a temporary SQLite database and explicitly imports the local wrapper.
- Custom Meta.indexes output: `CheckDecision(scope='none', tables=frozenset(), reasons=())`.
- Explicit COMMIT output: `IntegrityError`; child data after failure: `[(99,)]`; FK enforcement: `(1,)`; autocommit: `True`.
- The inherited VIRTUAL_ENV mismatch warning appears. Global constraints explicitly identify that warning as expected.
- Reported final evidence contains 52 schema tests, 174 combined tests, and passing ruff. This review does not rerun those commands.

## Assessment

**Spec compliance:** Issues found.

**Task quality:** Needs fixes.

**Reasoning:** Normal operation, deferred execution, and failure cleanup are well covered. Two uncovered paths violate transaction or provenance guarantees.


---

## Archived task-2-rereview-1.md

# Task 2 Re-review: Fix Round 1

## Finding Verdicts

- **Explicit transaction completion bypasses validation before commit** — **ADDRESSED**.
  `src/twicc/db/backends/sqlite3/schema.py:64` installs the editor-owned authorization callback.
  `src/twicc/db/backends/sqlite3/schema.py:109` denies `SQLITE_TRANSACTION` during atomic observation.
  SQLite rejects BEGIN, COMMIT, and ROLLBACK before execution. Native commit, rollback, and executescript share this boundary.
  The editor starts its transaction before installing observation. It removes observation before its own transaction cleanup.
  `tests/test_sqlite_migration_schema.py:626` covers seven executor paths in both directions, rollback, records, and connection restoration.
  `tests/test_sqlite_migration_schema.py:685` covers deferred COMMIT rejection.
  `tests/test_sqlite_migration_schema.py:693` covers caught denial and subsequent observed invalid writes.
  `tests/test_sqlite_migration_schema.py:704` preserves the existing atomic=False transaction behavior.
  `tests/test_sqlite_migration_schema.py:714` verifies Django and native savepoint RELEASE retain the outer BEGIN transaction.
  Final validation rolls back both savepoint paths. Savepoints remain compatible with the controller's explicit clarification.

- **CreateModel trusts custom Meta.indexes through the standard builder** — **ADDRESSED**.
  `src/twicc/db/backends/sqlite3/schema.py:189` carries each Meta.index object's exact-class provenance during SQL construction.
  Field indexes receive a separate `field_index` context. Custom Index subclasses receive `unknown` context.
  `src/twicc/db/backends/sqlite3/schema.py:203` accepts field-index or standard-index provenance, never the outer create context.
  `tests/test_sqlite_migration_schema.py:663` verifies custom Meta.indexes cause global validation and execute exactly once.
  The same case includes an exact standard Meta.index and a field index.
  Previously reported ordinary CreateModel tests retain their zero-scan expectations in the amended schema test run.

## New Breakage in the Fix Diff

None.

- `schema.py:109` delegates all other authorization actions to the existing observer. Independent effect observation remains intact.
- `schema.py:189` retains Django's managed/proxy/swapped gate and expression-index feature gate.
- `schema.py:203` retains immutable rendered-statement registration and object matching.
- The fix contains two product changes and their regression tests. Task 1's metadata and observer remain unchanged.

## Out-of-Scope Observations

None.

## Evidence Checked

- Read the Task 2 brief, updated global constraints, prior findings, appended fix report, and supplied fix diff.
- The appended report names the covering regressions and records RED: `17 failed, 1 passed, 52 deselected`.
- The appended report records GREEN: `71 passed in 5.63s`, including the savepoint boundary test.
- The appended report records ruff: `All checks passed!` and an empty `git diff --check` result.
- The only reported environment warning is the explicitly expected inherited VIRTUAL_ENV mismatch.
- No test suite, counterexample, git command, or outside-code inspection runs during this scoped re-review.
- Product files, index, HEAD, and branch state remain untouched. Only this requested report is written.

## Verdict

**Spec compliance:** Approved for the findings and fix scope.

**Code quality:** Approved for the fix scope.

**Fix round:** All findings addressed, no new Critical/Important breakage.


---

## Archived task-3-report.md

# Task 3 report

Status: DONE.
Base: `12b00c6cc45a336ff2ed10d3f8c521012a058264`.
Branch: `bugfix/sqlite-migration-checks`.
Worktree: `/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks`.
Model: `GPT-6.1 Sol`, selected by the controller.

## Implementation

| File | Responsibility |
| --- | --- |
| `src/twicc/settings.py` | Select optimized backend before connections open; exact `TWICC_SQLITE_STANDARD_MIGRATIONS=1` opt-out. |
| `src/twicc/settings_test.py` | Explicit optimized test backend. |
| `src/twicc/db/migration_logging.py` | ContextVar identity/direction and FK scope/reason/duration/success logs. |
| `src/twicc/core/management/commands/migrate.py` | Delegate Django handle/callback/output; start/success/failure total durations at verbosity 0. |
| `src/twicc/db/backends/sqlite3/schema.py` | Time validation separately; log skipped/selected/global scope and validation failure. Preserve Task 2 lifecycle. |
| `tests/test_sqlite_migration_integration.py` | 17 disposable integration regressions. |
| `docs/plans/2026-09-30-sqlite-migration-checks-design.md` | Actual milestone status, implemented boundaries, pending stage-2 preservation proof. |
| `docs/plans/2026-09-30-sqlite-migration-checks-review.md` | Preserve controller Task 1/2 reviews and append Task 3 evidence. |
| `CHANGELOG.md` | English entry only under freshly checked Unreleased. |

No historical migration, compute version, executor copy, production monkeypatch, dependency, server restart, live migration, or main integration.
All database writes use disposable temporary SQLite files or in-memory connections.
No subagent is dispatched. The controller owns independent review.

## Coverage

- Startup settings choose the backend before a physical connection opens. Empty/0 select optimized; 1 selects standard.
- Command logs real forward and backward execution at verbosity 0 while keeping normal verbosity 1 output.
- fake and backward fake retain accurate final outcomes and recorder behavior.
- fake_initial begins with fake=False and ends with fake=True; the success callback uses the final flag.
- Both command success and operation failure restore an outer ContextVar token.
- Operation failure logs the active migration/direction and preserves the failed migration's unapplied state.
- FK logs distinguish total migration time from actual FK-check time. Skipped check time is exactly zero.
- Selected successful/failed checks log table names, reason, check duration, and outcome. Failed validation rolls back.
- Unknown custom Operation retains global checks and reasons in both directions through the real command/executor.
- Standard comparison keeps Django's global scans, including recorder-table creation.
- sqlmigrate emits real CreateModel SQL without schema writes or FK checks, and retains enforcement.
- The complete historical graph installs on a fresh disposable database.
- Rollback through 0146 removes facts, Session index, and original/replacement records with zero check PRAGMAs.
- Populated replay through 0147 and the squash preserves ten SessionItem rows with zero check PRAGMAs.
- The mixed 0148-applied/0149-unapplied state selects original 0149 in the real loader.
- Original 0149 applies/reverses before replacement bookkeeping and removes/recreates the redundant item index.
- Normal migrate then applies original 0149 and records the replacement. A fresh loader selects the replacement graph.
- Fresh-loader rollback through 0146 removes facts, Session index, redundant item index, and all relevant records.

The mixed-state setup temporarily omits only the new squash from the fixture's disk loader.
This simulates a release before that squash exists while keeping older replacements intact.
It executes the full real migration graph through 0148; it never edits migration files or records directly.
Original apply/unapply uses the real executor methods before batch replacement bookkeeping.
Normal migrate and fresh-loader rollback then verify bookkeeping without modifying Django behavior.

## Environment verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -c 'import twicc; from twicc import settings; print(twicc.__file__); print(settings.DATABASES["default"]["NAME"])'
```

Output:

```text
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/src/twicc/__init__.py
/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks/db/data.sqlite
```

Tests use `twicc.settings_test` and its `:memory:` default.
Full-graph fixtures temporarily replace/restore the default connection with `tmp_path/disposable.sqlite3`.
Historical RunPython migrations use the default ORM alias, so a separate alias cannot run the full graph correctly.
The expected inherited VIRTUAL_ENV mismatch warning does not change the selected project.

## TDD evidence

First command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_integration.py -q
```

Initial RED: `11 failed, 2 passed in 3.04s`.
Valid behavioral failures show the standard engine instead of the optimized engine, absent migration events, and absent FK diagnostics.
Representative failures:

```text
At index 0 diff: 'django.db.backends.sqlite3' != 'twicc.db.backends.sqlite3'
assert [] == [('core.0001_initial', 'forward', False), ...]
IndexError: list index out of range  # no start/success/FK log records
```

The initial full-graph fixture uses a separate alias, but historical migrations write through default ORM.
That setup error and the recorder-check count correction are excluded from RED feature evidence.
After those corrections, the pre-implementation run reports `10 failed, 3 passed in 11.14s`.
It retains the expected selection/logging failures and two graph fixture problems.
The missing ContextVar module also fails explicitly before implementation.

Implementation GREEN after graph fixture stabilization: `16 passed in 13.15s`.
Additional real selected-check, failure, fake-backward, normal-output, and custom-operation coverage characterize implemented behavior.
Adding the custom migration initially creates conflicting fixture leaves; this is a fixture error, not product RED.
The fixture becomes linear and uses a fake failed-node record only in the custom-operation test.
Final integration GREEN: `17 passed in 15.30s`.

## Final targeted verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py tests/test_sqlite_migration_schema.py tests/test_sqlite_migration_integration.py tests/test_peer_threading_migration.py tests/test_peer_revocation_migration.py -q
```

```text
212 passed in 27.29s
```

This includes both existing peer migration tests. Final focused output has no pytest warning summary.
Captured output: `task-3-focused-final.log` beside this report.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m django makemigrations --check --dry-run --settings=twicc.settings_test
```

```text
No changes detected
```

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && uvx ruff check src/twicc/db/migration_logging.py src/twicc/core/management/commands/migrate.py src/twicc/db/backends/sqlite3/schema.py src/twicc/settings.py src/twicc/settings_test.py tests/test_sqlite_migration_integration.py
```

```text
All checks passed!
```

`git diff --check` passes without output.

## Broad suite and standard comparison

Run once after settings integration:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest -q
```

```text
6 failed, 6213 passed, 21 skipped, 61 warnings in 348.10s (0:05:48)
```

The broad run collects the 16-test integration version. The added custom-operation regression passes in the final focused run.
No production behavior changes after the broad run starts. The whole suite is not repeated.
Full output: `task-3-full-suite.log` beside this report.

Failures:

- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[args0-timeout-300.0]`
- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[args1-timeout-12.0]`
- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[args2-want_text-False]`
- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[args3-wait_background-False]`
- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[args4-wait_background-True]`
- `tests/test_log_retention.py::test_default_now_is_the_current_time`

All five wait failures return `no_provider_configured` before testing their wait arguments.
The retention fixture anchors activity to 2026-09-05 while its expectation uses the current date, 2026-09-30.
It expects 2026-08-31 but policy retains a tail starting 2026-08-29.

A workflow-only temporary settings module imports settings_test and forces the standard engine plus `:memory:`.
The command also sets the actual startup opt-out. The temporary module is not a product change.
Before comparison, TwiCC imports from the worktree and the selected DB configuration prints:

```text
{'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
```

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD TWICC_SQLITE_STANDARD_MIGRATIONS=1 PYTHONPATH=$PWD/.superpowers/sdd/2026-09-30-sqlite-migration-checks-implementation uv run --extra test python -m pytest --ds=task3_standard_settings tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over tests/test_log_retention.py::test_default_now_is_the_current_time -q
```

```text
6 failed in 3.81s
```

The six names and failure causes match the optimized broad run.
`git diff BASE -- tests/test_wait_reply.py tests/test_log_retention.py src/twicc/log_retention.py src/twicc/cli/create_session/command.py` is empty.
Those failures are unrelated to this task and remain unchanged.
The user confirms the log-retention test is already corrected on main.
This branch starts from an older base; no duplicate correction, main merge, or main rebase occurs.
Comparison output: `task-3-standard-failures.log` beside this report.

Broad warnings:

- 2 RuntimeWarnings in `test_codex_sdk_wrappers.py` for unawaited AsyncMock coroutines.
- 59 Click protected_args DeprecationWarnings: 8 process-command-removal, 11 prompt-includes, 40 session-keywords cases.

## Probe results

The original synthetic probe reruns unchanged after the final production behavior change:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python scripts/prototypes/sqlite_migration_checks.py
```

Exit code 0. JSON output: `task-3-prototype.json` beside this report.
It uses only in-memory databases and does not import TwiCC.

A workflow-only actual-backend probe uses production wrappers and the actual 0147/squash migration classes.
Representative historical tables contain Session, SessionItem, and migration records.
It applies 0147, applies the squash, reverses the squash, and reverses 0147 with populated SessionItem rows.
Assertions require every item row to survive, four global checks for standard, and zero checks for optimized.
There are no timing thresholds.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python .superpowers/sdd/2026-09-30-sqlite-migration-checks-implementation/task-3-backend-probe.py
```

Exit code 0. Samples: `task-3-backend-probe.json` beside this report.
Three repetitions per case; fresh in-memory connections; seed time excluded; seeded pages already resident.
No cold-disk measurement or live-startup prediction.

| SessionItem rows | Payload | Standard total samples (seconds) | Optimized total samples (seconds) | Standard FK-check samples (seconds) |
| --- | --- | --- | --- | --- |
| 10,000 | 256 bytes | 0.020321, 0.017804, 0.018267 | 0.016160, 0.015177, 0.015620 | 0.004951, 0.004900, 0.005423 |
| 100,000 | 256 bytes | 0.066883, 0.058935, 0.058109 | 0.016354, 0.016571, 0.016867 | 0.052201, 0.046330, 0.046202 |
| 100,000 | 4096 bytes | 0.088218, 0.086339, 0.088707 | 0.022027, 0.022096, 0.026468 | 0.072732, 0.071857, 0.073041 |

Every standard sample has 4 global/0 selected checks.
Every optimized sample has 0 global/0 selected checks and exactly 0 FK-check seconds.
Total includes actual migration operations and recording; check duration measures only check_constraints().

## Self-review and limits

The staged scope includes only Task 3 files and the controller-approved accumulated review document.
Context cleanup, failure identity, final fake flags, normal output, graph records, and validation cleanup have direct tests.
No task correctness concern remains. The broad suite has six independently reproduced unrelated failures.

First milestone does not optimize unproved table rebuilds or every native column operation.
Global fallback remains for those cases. Stage 2 must prove key mapping, values, storage/affinity, collation, defaults, null replacement, generated keys, and relations.
The optimized contract rejects user transaction controls in atomic editors and attached/temp mutations.
Atomic=False cannot roll back already committed effects. Raw-driver authorizer replacement remains unsupported.
The standard opt-out retains Django checks; detailed FK scope logs come from the optimized editor only.
The command's total migration diagnostics remain available with either backend.
Independent Task 3 review and main integration remain pending.
The user must restart via devctl.py after any later integration into their running backend; no restart happens here.

## Commit

`d3207b2b7f0ffa3b6a86397044aff1d0005a5012` — `feat(sqlite): select migration backend and log scoped validation`.

The commit includes a descriptive body and `Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>`.
The worktree is clean after commit. Workflow reports and probe outputs remain ignored local artifacts.


---

## Actual backend benchmark source

Run from the isolated worktree with `TWICC_DATA_DIR=$PWD uv run --extra test python <saved-probe-path>`.
The probe uses only in-memory databases.

```python
"""Disposable representative replay probe. No production startup prediction."""
from importlib import import_module
from statistics import median
from time import perf_counter

import django
import orjson
from django.conf import settings

settings.configure(INSTALLED_APPS=[], DATABASES={"default": {
    "ENGINE": "twicc.db.backends.sqlite3", "NAME": ":memory:"}}, SECRET_KEY="probe")
django.setup()

from django.db import connections, models
from django.db.backends.sqlite3.base import DatabaseWrapper as StandardWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ModelState, ProjectState
from twicc.db.backends.sqlite3.base import DatabaseWrapper

M147 = import_module("twicc.core.migrations.0147_session_history_fact").Migration
Squash = import_module("twicc.core.migrations.0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index").Migration


def sample(wrapper, rows, payload_bytes):
    config = {**connections["default"].settings_dict, "NAME": ":memory:"}
    db = wrapper(config, "default")
    connections._connections.default = db
    state = ProjectState()
    state.add_model(ModelState("core", "Session", [
        ("id", models.CharField(primary_key=True, max_length=200)),
        ("parent_session", models.ForeignKey("core.Session", on_delete=models.CASCADE, null=True)),
        ("self_cost", models.DecimalField(max_digits=20, decimal_places=10, null=True)),
    ]))
    state.add_model(ModelState("core", "SessionItem", [
        ("id", models.BigAutoField(primary_key=True)),
        ("session", models.ForeignKey("core.Session", on_delete=models.CASCADE)),
        ("content", models.TextField()),
    ]))
    db.ensure_connection()
    try:
        with db.schema_editor() as editor:
            editor.create_model(state.apps.get_model("core", "Session"))
            editor.create_model(state.apps.get_model("core", "SessionItem"))
        db.connection.execute("INSERT INTO core_session VALUES ('session', NULL, NULL)")
        db.connection.executemany("INSERT INTO core_sessionitem VALUES (?, 'session', ?)",
                                  ((i + 1, 'x' * payload_bytes) for i in range(rows)))
        executor = MigrationExecutor(db)
        executor.recorder.ensure_schema()
        checks = []
        db.connection.set_trace_callback(lambda sql: checks.append(sql) if sql.startswith("PRAGMA foreign_key_check") else None)
        check_seconds = []
        check_constraints = db.check_constraints

        def timed_check(*args, **kwargs):
            start = perf_counter()
            try:
                return check_constraints(*args, **kwargs)
            finally:
                check_seconds.append(perf_counter() - start)

        db.check_constraints = timed_check
        start = perf_counter()
        after147 = executor.apply_migration(state.clone(), M147("0147_probe", "core"))
        executor.apply_migration(after147.clone(), Squash("0148_probe", "core"))
        executor.unapply_migration(after147.clone(), Squash("0148_probe", "core"))
        executor.unapply_migration(state.clone(), M147("0147_probe", "core"))
        elapsed = perf_counter() - start
        assert db.connection.execute("SELECT count(*) FROM core_sessionitem").fetchone() == (rows,)
        assert checks == (["PRAGMA foreign_key_check"] * 4 if wrapper is StandardWrapper else [])
        return {"total_seconds": elapsed, "fk_check_seconds": sum(check_seconds),
                "global_checks": len(checks), "selected_checks": 0}
    finally:
        db.close()


cases = []
for rows, payload in [(10000, 256), (100000, 256), (100000, 4096)]:
    results = {}
    for name, wrapper in [("standard", StandardWrapper), ("optimized", DatabaseWrapper)]:
        samples = [sample(wrapper, rows, payload) for _ in range(3)]
        results[name] = {"samples": samples, "median_total_seconds": median(s["total_seconds"] for s in samples)}
    cases.append({"SessionItem_rows": rows, "payload_bytes": payload, "backends": results})
print(orjson.dumps({"storage": "in-memory", "repetitions": 3, "seed_time_excluded": True,
                    "operations": "real 0147 and squash apply/unapply on representative historical core tables",
                    "conditions": "fresh in-memory connection per sample; seeded pages already resident; no cold-disk measurement",
                    "cases": cases}, option=orjson.OPT_INDENT_2).decode())

```


## Actual backend benchmark samples

```json
{
  "storage": "in-memory",
  "repetitions": 3,
  "seed_time_excluded": true,
  "operations": "real 0147 and squash apply/unapply on representative historical core tables",
  "conditions": "fresh in-memory connection per sample; seeded pages already resident; no cold-disk measurement",
  "cases": [
    {
      "SessionItem_rows": 10000,
      "payload_bytes": 256,
      "backends": {
        "standard": {
          "samples": [
            {
              "total_seconds": 0.02032067500113044,
              "fk_check_seconds": 0.004950832022586837,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.017803687005653046,
              "fk_check_seconds": 0.004899682986433618,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.018267425999511033,
              "fk_check_seconds": 0.005422509988420643,
              "global_checks": 4,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.018267425999511033
        },
        "optimized": {
          "samples": [
            {
              "total_seconds": 0.016160294006112963,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.015177444991422817,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.015619593003066257,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.015619593003066257
        }
      }
    },
    {
      "SessionItem_rows": 100000,
      "payload_bytes": 256,
      "backends": {
        "standard": {
          "samples": [
            {
              "total_seconds": 0.06688342000416014,
              "fk_check_seconds": 0.05220063403248787,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.05893472798925359,
              "fk_check_seconds": 0.04632953100372106,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.05810880599892698,
              "fk_check_seconds": 0.0462018660036847,
              "global_checks": 4,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.05893472798925359
        },
        "optimized": {
          "samples": [
            {
              "total_seconds": 0.0163536569889402,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.016570742009207606,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.016867099999217317,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.016570742009207606
        }
      }
    },
    {
      "SessionItem_rows": 100000,
      "payload_bytes": 4096,
      "backends": {
        "standard": {
          "samples": [
            {
              "total_seconds": 0.08821824299229775,
              "fk_check_seconds": 0.07273199698829558,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.08633878899854608,
              "fk_check_seconds": 0.07185685698641464,
              "global_checks": 4,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.08870677099912427,
              "fk_check_seconds": 0.07304076601576526,
              "global_checks": 4,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.08821824299229775
        },
        "optimized": {
          "samples": [
            {
              "total_seconds": 0.02202655498695094,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.022096043001511134,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            },
            {
              "total_seconds": 0.02646757299953606,
              "fk_check_seconds": 0,
              "global_checks": 0,
              "selected_checks": 0
            }
          ],
          "median_total_seconds": 0.022096043001511134
        }
      }
    }
  ]
}

```

---

## Archived task-3-review.md

# Task 3 review

## Spec compliance

- **Verdict: ✅ Spec compliant.** Base: `12b00c6cc45a336ff2ed10d3f8c521012a058264`. Head: `d3207b2b7f0ffa3b6a86397044aff1d0005a5012`.
- `src/twicc/settings.py:331` selects the project backend during settings import. Only the exact opt-out value `1` selects Django.
- `tests/test_sqlite_migration_integration.py:68` checks all three selection values before a physical connection opens.
- `src/twicc/settings_test.py:39` explicitly selects the project backend. The standard comparison uses a separate wrapper.
- `src/twicc/core/management/commands/migrate.py:11` delegates execution, logs active failures, and restores the outer ContextVar token.
- `src/twicc/core/management/commands/migrate.py:24` delegates normal output. Success logs use the final fake flag.
- `tests/test_sqlite_migration_integration.py:153` covers forward/backward logs and separate check durations at verbosity 0.
- `tests/test_sqlite_migration_integration.py:170` covers fake and fake_initial, including fake_initial's false-to-true transition.
- `tests/test_sqlite_migration_integration.py:183` checks failure identity, unapplied records, enforcement restoration, and success/failure context cleanup.
- `tests/test_sqlite_migration_integration.py:355` checks normal verbosity 1 output and fake backward execution.
- `src/twicc/db/migration_logging.py:11` records scope, tables, reason, check duration, and success independently from total duration.
- `src/twicc/db/backends/sqlite3/schema.py:102` times validation inside the existing cleanup boundary. It preserves validation before commit.
- `tests/test_sqlite_migration_integration.py:228` covers fresh installation, rollback through 0146, and populated replay through 0147 and squash.
- `tests/test_sqlite_migration_integration.py:249` uses the real loader/executor for the mixed 0148-applied/0149-unapplied state.
- That test checks original 0149 apply/reverse, index effects, replacement bookkeeping, fresh-loader replacement selection, and rollback records.
- Both graph tests require zero FK-check PRAGMAs during known replay paths. The populated replay preserves ten SessionItem rows.
- `tests/test_sqlite_migration_integration.py:300` verifies sqlmigrate collects real SQL without schema writes or FK checks.
- `tests/test_sqlite_migration_integration.py:311` verifies Django's standard wrapper retains global checks.
- `tests/test_sqlite_migration_integration.py:319` and `:330` verify global/selected diagnostics and failed validation rollback.
- `tests/test_sqlite_migration_integration.py:368` verifies unknown Operation fallback through real command/executor execution in both directions.
- `docs/plans/2026-09-30-sqlite-migration-checks-design.md:3` explicitly keeps stage-2 preservation proof pending.
- `CHANGELOG.md:32` adds the requested English entry under Unreleased. The diff changes no historical migration or compute version.
- **⚠️ Diff boundary:** Tasks 1/2 provide the unchanged observer and proof implementation. This review does not repeat their independent reviews.
- **⚠️ Workflow boundary:** The package does not expose the commit body or independently establish every past command's execution.
- `task-3-report.md:244` records the required commit body and model trailer. The controller can verify commit metadata separately.

## Strengths

- `src/twicc/core/management/commands/migrate.py:11` keeps Django responsible for migration execution and executor behavior.
- `src/twicc/db/migration_logging.py:8` uses context-local identity. It introduces no mutable global current-migration value.
- `tests/test_sqlite_migration_integration.py:30` replaces the default connection only for a temporary SQLite database.
- The fixture restores the previous connection/configuration. Historical RunPython operations therefore use the correct disposable default alias.
- `tests/test_sqlite_migration_integration.py:249` tests replacement records through Django bookkeeping rather than fabricated production records.
- `tests/test_sqlite_migration_integration.py:330` verifies actual SQLite validation and rollback rather than mocked validation results.
- `task-3-backend-probe.json:1` records in-memory conditions, check counts, and timing samples without live-startup extrapolation.

## Issues

### Critical

- **None.** No Critical defect appears in this task's diff.

### Important

- **None.** No missing task requirement or blocking quality defect appears in this task's diff.

### Minor

- **Existing warning noise:** `tests/test_codex_sdk_wrappers.py:131` and `:150` emit two unawaited AsyncMock coroutine warnings.
- `src/twicc/cli/_remote.py:158` emits 59 Click protected_args deprecation warnings across existing CLI tests.
- Evidence: `task-3-full-suite.log:703` and `:720`. These files are outside this diff.
- Fix the AsyncMock setup and deprecated Click access in separate work. These warnings do not block Task 3.
- `task-3-focused-final.log:1` contains the expected inherited VIRTUAL_ENV warning. It confirms uv ignores the main environment.
- Keep the project-targeting command. Do not use `--active` to suppress that warning.

## Validation assessment

- **Inspected evidence:** `task-3-focused-final.log:20` records 212 passing migration/peer tests and no pytest warning summary.
- `task-3-report.md:119` records no model changes. `task-3-report.md:128` records successful Ruff validation.
- `task-3-full-suite.log:731` records 6 failures, 6213 passes, 21 skips, and 61 warnings.
- `task-3-standard-failures.log:329` records the same six failed tests with Django's standard backend.
- Five failures stop at `no_provider_configured`; see `task-3-standard-failures.log:63`. They do not reach wait-argument assertions.
- The retention failure matches the fixed-date fixture mismatch; see `task-3-standard-failures.log:313` and `:318`.
- The controller supplies the user's confirmation that main commit `5767bc2c` fixes that retention test.
- **Disposition:** All six failures are outside Task 3. No merge, rebase, or duplicate correction is required for this gate.
- **Focused lifecycle check:** The schema diff cuts off `__exit__`. I inspect its enclosing lifecycle and `_finish` at `schema.py:131`.
- Validation logging remains before atomic completion. Cleanup still restores enforcement and editor ownership after failure.
- **Named callback-contract risk:** I inspect Django 6.0.4's installed callback and executor implementation.
- `.venv/lib/python3.13/site-packages/django/db/migrations/executor.py:241` confirms start/success ordering and final fake_initial outcome.
- `.venv/lib/python3.13/site-packages/django/core/management/commands/migrate.py:390` confirms delegated CLI output and callback arguments.
- **Named logging-delivery risk:** I inspect `src/twicc/settings.py:359`. The existing twicc logger accepts INFO and retains child propagation.
- **Review checks:** I read the supplied diff and captured evidence. I rerun no tests, routine suite, migration, or probe.
- Output truncation hides part of the initial combined read. I retrieve only the hidden package/report sections and warning excerpt.
- I modify only this requested review artifact. Product files, index, HEAD, and branch state remain untouched.

## Assessment

- **Task quality: Approved.** The implementation meets Task 3 requirements and preserves Django's migration lifecycle.
- **Reasoning:** Real graph tests cover original and replacement routes. Logging stays small and context-local, with failure cleanup tests.
- **Remaining boundary:** Stage-2 table-rebuild preservation proof stays deliberately pending. This approval covers the first milestone only.

## Current review status

All three task reviews approve spec compliance and quality.
The broad whole-branch review remains pending.
The six broad-suite failures reproduce with the standard backend.
Main commit `5767bc2c` subsequently fixes the log-retention test; this branch does not duplicate it.
