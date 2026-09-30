# Selective SQLite Migration Checks: First Milestone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate unnecessary FK scans for the reported table/index upgrade and ordinary data migrations, with selected checks for relation-changing data.

**Architecture:** A project SQLite backend observes compiled SQLite effects during schema editing. Dependency metadata selects SQLite table-scoped checks. Unknown schema effects retain the global check; Django keeps ownership of migration execution and recording.

**Tech Stack:** Python >=3.13, Django 6.0.4, stdlib sqlite3, pytest, pytest-django.

**Spec:** [2026-09-30-sqlite-migration-checks-design.md](2026-09-30-sqlite-migration-checks-design.md)

## Global Constraints

- Work only in `.worktrees/bugfix-sqlite-migration-checks`, branch `bugfix/sqlite-migration-checks`.
- No schema migration or compute-version change.
- Do not rewrite historical migration files or migration records.
- No global monkeypatch, SQL parser, or copy of Django's migration executor.
- Use SQLite for FK comparison semantics; no custom FK comparison query.
- Validate before commit. Restore enforcement and observers after failure.
- Unknown schema effects use a global check.
- Global means main schema. Reject user temp/attached mutations and ATTACH/DETACH before execution; allow proven read-only access.
- Reject nested editor/atomic entry before changing FK or authorizer state.
- `TWICC_SQLITE_STANDARD_MIGRATIONS=1` selects standard Django behavior.
- All writes and full-graph migration tests use disposable databases.
- Do not restart the live instance or integrate into main.
- Set `TWICC_DATA_DIR=$PWD` for every project Python command in this worktree.
- Preserve user-owned changes outside this worktree.

## Milestone Boundary

This plan covers no-op editing, known table creation/deletion, nonunique index changes, and column-aware data effects.
It intentionally retains the global fallback for table rebuilds and other unclassified schema changes.
That fallback is temporary and does not fulfill the spec's ordinary-field rebuild target.
A second plan must follow the key-preservation prototype; do not silently relax that target.

The experimental `ObservedEditor` is evidence, not implementation code to copy wholesale.
Its broad schema trust flag and deferred-statement identity shortcut must be replaced.

## Review Focus

1. A cached SQL statement or native cursor must not bypass write observation.
2. A generated or rowid-alias key may change without an UPDATE authorization naming the referenced column.
3. INSERT or UPDATE conflict replacement may invalidate children without a DELETE authorization, including schema-level and partial/expression/generated unique conflicts.
4. Arbitrary writes during a known schema operation or mutated deferred SQL must not inherit that operation's trust.
5. Validation failure must restore enforcement and roll back schema, data, and atomic migration recording.

## Files and Interfaces

- `src/twicc/db/backends/sqlite3/metadata.py`: schema snapshots and canonical identifiers.
- `src/twicc/db/backends/sqlite3/effects.py`: authorizer observations and scope selection.
- `src/twicc/db/backends/sqlite3/schema.py`: schema editor lifecycle and trusted standard operations.
- `src/twicc/db/backends/sqlite3/base.py`: local wrapper selecting that schema editor.
- Parent `__init__.py` files: package structure only.
- `src/twicc/db/migration_logging.py`: per-command migration logging context.
- `src/twicc/core/management/commands/migrate.py`: Django command subclass for progress logging.
- `src/twicc/settings.py`, `src/twicc/settings_test.py`: backend selection.
- `tests/test_sqlite_migration_metadata.py`, `tests/test_sqlite_migration_effects.py`, `tests/test_sqlite_migration_schema.py`, `tests/test_sqlite_migration_upgrade.py`: disposable-database coverage.

## Task 1: Metadata and Column-Aware Effects

**Interfaces:**

- `canonical_identifier(value: str) -> str`: ASCII-only case normalization.
- `ForeignKey(NamedTuple)`: `child_table`, `parent_table`, `child_columns`, `parent_columns`; columns are ordered tuples.
- `TableSchema(NamedTuple)`: `name`, `primary_key`, `foreign_keys`, `generated_columns`, `indexes`.
- `IndexSchema(NamedTuple)`: `name`, `unique`, `origin`, `columns`, `partial`, `has_expressions`, `uses_generated_columns`. Columns are an ordered tuple of canonical key-column identifiers; expression/rowid entries use the flags rather than invented column names.
- `read_schema(connection) -> dict[str, TableSchema]`: dictionary keys use canonical table identifiers.
- `WriteEffect(NamedTuple)`: `action`, `table`, `column`, `source`; source identifies a trigger when supplied.
- `CheckDecision(NamedTuple)`: `scope` (`none`, `tables`, `global`), `tables`, `reasons`.
- `EffectObserver.observe(action, arg1, arg2, database, source) -> int`: no SQL execution; returns SQLITE_OK for supported effects and SQLITE_DENY for unsupported mutations.
- `EffectObserver.decision(before, after) -> CheckDecision`: conservative decision from actual effects and schema metadata.

- [ ] Write metadata tests for composite FKs, omitted parent columns, generated columns, non-ASCII identifiers, case differences, unique indexes, and missing referenced tables.
- [ ] Verify the tests fail because the metadata API does not exist.
- [ ] Implement grouped FK metadata using table_xinfo, foreign_key_list, index_list, and index_xinfo. Preserve original names for SQL quoting. Include primary keys in the unique-conflict input set.
- [ ] Write scope tests: ordinary-column UPDATE -> none; child FK UPDATE -> child; parent key UPDATE/DELETE -> incoming children; parent INSERT -> incoming children.
- [ ] Add generated-key, rowid-alias, uppercase-reference, and INSERT OR REPLACE tests. Unchanged unrelated tables must stay outside the decision.
- [ ] Add UPDATE OR REPLACE, schema-level ON CONFLICT REPLACE, partial unique predicate, expression unique, and generated unique input tests. Unique-conflict inputs select all incoming child tables; unknown predicate/expression/generated dependencies select them on every parent UPDATE.
- [ ] Add observer tests for native cursor execution, executemany, trigger writes, cached statements, and unknown action codes.
- [ ] Add attached/temp write and DDL rejection tests, ATTACH/DETACH rejection, and read-only access tests. Require rejection before unsupported side effects. Test main ALTER TABLE's internal temp catalog maintenance and reject writable_schema setters or enabled entry.
- [ ] Implement observation and decision rules. Unexpected effects must not escape through an exception in the callback.
- [ ] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_effects.py -q` from the worktree. Require all tests to pass.
- [ ] Commit the task with a descriptive body and the required model trailer.

## Task 2: Transaction-Safe Schema Editor

**Interfaces:**

- `DatabaseWrapper`: subclasses Django's SQLite wrapper; `SchemaEditorClass` points at the local editor.
- `DatabaseSchemaEditor`: subclasses Django's SQLite editor; owns one observer for one editor context.
- Consume task 1's `read_schema`, `EffectObserver`, and `CheckDecision`.
- Preserve the normal `connection.schema_editor()` API.

- [ ] Write tests through Django's real MigrationExecutor for RunPython and RunSQL ordinary-column writes: zero FK scans and successful migration recording.
- [ ] Write invalid-FK tests in both directions: selected-table validation, atomic data rollback, expected migration-record state, enforcement on, and autocommit restored.
- [ ] Write CreateModel tests with FKs and implicit M2M, AddIndex/RemoveIndex tests, and DeleteModel tests with surviving incoming references and with all referencing tables deleted.
- [ ] Add mixed schema/data tests, native cursor writes, trigger writes, and custom schema-operation tests. A callback within a known operation must not suppress unrelated writes.
- [ ] Write deferred-SQL tests: known statements execute once before validation; appended or mutated statements lose trust; deferred failure rolls back and cleans up.
- [ ] Add operation-failure, check-failure, nested-context, atomic=False, and collect_sql tests. Nested entry must fail before changing the outer observer or FK state; catching that failure must leave later outer writes observed. Do not assert complete rollback for atomic=False.
- [ ] Run the new tests and verify failure against the standard backend.
- [ ] Implement the local editor. Capture metadata before observation; drain deferred SQL; uninstall the observer; choose and run checks before exiting the transaction.
- [ ] Trust only exact standard operations and their expected objects. For deferred work, snapshot immutable rendered SQL and expected effects; identity alone is insufficient.
- [ ] Keep main-schema global checks for rebuilds, raw main schema SQL, unique/unknown index removal, and custom operations with unknown main effects. Unsupported temp/attached mutations and nested contexts must be rejected, not passed to the standard editor inside an active context.
- [ ] Restore enforcement and observer ownership on every exit path, preserving the original error if cleanup also fails.
- [ ] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_sqlite_migration_schema.py -q`. Require all tests to pass.
- [ ] Commit the task with a descriptive body and the required model trailer.

## Task 3: Backend Selection, Logs, and Upgrade Validation

**Interfaces:**

- Settings select `twicc.db.backends.sqlite3`, unless `TWICC_SQLITE_STANDARD_MIGRATIONS=1` selects `django.db.backends.sqlite3`.
- Test settings explicitly select the project backend for its regression suite; comparison fixtures instantiate the standard backend.
- `migration_logging` holds a context-local migration identity and direction. No global mutable current-migration value.
- `Command` subclasses Django's migrate command. Its progress callback logs start/end at verbosity 0 and delegates normal CLI output.
- Command cleanup restores logging context on both success and failure.

- [ ] Write backend-selection tests for the opt-out, including selection before database connections open.
- [ ] Write migration logging tests for forward, backward, fake, fake_initial, verbosity 0, and failure. Logs must separate total migration duration from FK-check duration and include scope/reason. Test fake_initial's start false/success true transition.
- [ ] Implement settings selection and the logging command subclass. Delegate command handling and executor behavior to Django. handle() must catch failures and log the active migration because Django emits no failure callback; finally must restore the ContextVar token.
- [ ] Write full-graph disposable-database tests for fresh install, applied 0148 then 0149, squash replacement, rollback to 0146, and replay of 0147 plus squash.
- [ ] Add the mixed replacement state: 0148 applied and 0149 unapplied. Use the real loader/executor to apply original 0149 and reverse the path. Assert index state and original/replacement records before and after graph replacement bookkeeping.
- [ ] Insert SessionItem rows before replay. Capture check SQL and require no SessionItem FK scan for the known replay operations.
- [ ] Assert facts-table creation/deletion, Session index creation/deletion, no redundant item index, and correct replacement migration records.
- [ ] Add sqlmigrate, standard-backend opt-out, and unknown-operation regression tests.
- [ ] Run targeted migration tests, existing peer migration tests, and `makemigrations --check --dry-run` against the disposable worktree environment. Never migrate the live DB.
- [ ] Run `uvx ruff check` on the created/modified Python files.
- [ ] Re-run the synthetic probe after any new behavior changes. Record scope counts and timing samples; do not extrapolate in-memory results to startup seconds.
- [ ] Record first-milestone limitations and the stage-2 preservation proof requirements in the design document.
- [ ] Re-read CHANGELOG.md and add an English entry only under Unreleased.
- [ ] Commit the task with a descriptive body and the required model trailer.

## Validation Commands

Prefix shell commands with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks &&`.
Before project tests, verify the imported TwiCC path resolves inside this worktree.
Verify the database path before any data-dependent command.
Full-graph tests must explicitly use temporary database paths or in-memory test databases.
`uv run` can invoke the editable frontend build hook while setting up the worktree environment; do not launch dev servers for these tests.

Do not replace `uv run` with `uv pip`, `--active`, or installs into the main environment.
The prototype uses the main interpreter only to read installed Django dependencies, not to import TwiCC or synchronize environments.

## Self-Review

First-milestone spec coverage maps to tasks 1-3.
The five review-focus cases are assigned to task 1 or task 2 tests.
The table-rebuild preservation requirement remains outside this milestone and is explicitly pending.
No product implementation starts until the plan is reviewed and the user selects its execution method.
