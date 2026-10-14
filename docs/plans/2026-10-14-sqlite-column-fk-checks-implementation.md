# SQLite column FK checks implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development in the existing feature/content-storage-compression worktree. Steps use checkbox syntax.

**Spec:** `docs/plans/2026-10-14-sqlite-column-fk-checks-spec.md`

**Goal:** Remove unnecessary global FK scans from column operations, including actual migrations 0158 and 0160.

**Tech Stack:** Python, Django 6.1.2, SQLite, pytest.

**Architecture:** Extend the existing authorizer-based schema editor. Prove mechanical operation effects separately from independent data writes. Use physical schema metadata, not field-class allowlists.

## Global constraints

- Work only in the existing worktree.
- Use `TWICC_DATA_DIR=$PWD` for tests; use separate disposable databases for migration replay.
- Preserve authorizer visibility of callbacks, rollback, and transaction guards.
- No application-row scan for a preservation proof.
- Do not restart DevCTL or migrate a running instance.
- English artifacts. No changelog changes.

## Review focus

- Callback writes during SQL generation must survive proof suppression.
- Parent key collation and uniqueness must survive populated rebuilds.
- A NULL FK still requires a valid parent schema.
- Temporary-table effects and creation bookkeeping must retain their logical identities.
- Physical schema drift or altered copy mappings must not receive an unsafe no-check decision.

## Task 1: Generic column operations and preserving rebuilds

**Files:**
- Modify: `src/twicc/db/backends/sqlite3/schema.py`
- Modify: `src/twicc/db/backends/sqlite3/metadata.py`
- Modify if necessary: `src/twicc/db/backends/sqlite3/effects.py`
- Test: `tests/test_sqlite_migration_schema.py`
- Create if needed: `tests/test_sqlite_migration_metadata.py`

**Interfaces:** Preserve `read_schema(connection) -> dict[str, TableSchema]`, `observer.decision(before, after)`, and Django schema-editor method signatures. Extend metadata with immutable declaration signatures and unique-index collations where necessary. Keep exact SQL/event matching internal to the editor.

- [x] Add failing tests for nullable DateTimeField, JSONField(default=list), nullable OneToOne, populated related rebuilds, ordinary default/check columns, unrelated-column removal, and outbound-FK removal. Assert no `PRAGMA foreign_key_check` for preserving operations.
- [x] Add failure tests for invalid callback writes, invalid parent keys, modified copy mapping, changed referenced collation/key semantics, schema drift, and temporary-name collisions. Assert relevant checks or conservative fallback and rollback.
- [x] Run the new tests and confirm failures against the existing backend.
- [x] Replace the field-class allowlist with a generic column-operation context. Capture actual pre/post schema and executed SQL. Prove NULL values from the actual default/copy expression.
- [x] Extend rebuild handling to existing populated tables. Match the standard create/copy/drop/rename sequence. Suppress exact mechanical effects only after successful verification. Preserve callback effects and correct temporary-table bookkeeping.
- [x] Add bounded physical declaration comparison. Tokenize quoted identifiers, strings, nested parentheses, and top-level clauses if needed. Do not build a general SQL expression parser. Tables disconnected from the FK graph need no relation signature comparison.
- [x] Run all SQLite migration schema, metadata, effects, and transaction tests. Keep failing checks scoped when affected dependencies are known; global fallback remains for unclassified schema effects.
- [x] Self-review and commit with a descriptive Conventional Commit body and the current model trailer.

## Task 2: Actual migration replay and validation

**Files:**
- Modify: `tests/test_sqlite_migration_integration.py`
- Create: `docs/plans/2026-10-14-sqlite-column-fk-checks-validation.md`

**Interfaces:** Consume Task 1's schema-editor decisions and existing disposable-database fixtures. Replay actual historical migrations with MigrationExecutor or their actual migration objects.

- [x] Add an integration test applying actual 0158 and 0160 after the historical 0157 state. Trace FK checks. Assert zero checks for each operation and valid final schema.
- [x] Replay the sequence with the standard backend as a comparison. Keep timing assertions out of tests.
- [x] Benchmark a populated synthetic disposable database with unrelated FK rows. Record sizes, row counts, total time, FK-check time, and check scopes. Separate rebuild copy costs from eliminated scans.
- [x] Run the full migration-focused test suite and Ruff on changed Python files. Run `git diff --check`.
- [x] Save a short validation record with exact commands and measured results. Commit tests and validation.

## Review sequence

Review this spec and plan before code changes. Review Task 1 before Task 2. Review the complete change after Task 2. Correct blocking findings and repeat scoped review. Do not broaden the task to runtime compute or statistics.

## Completion

Preimplementation review approves the design. Task and final reviews approve the completed change. The first code review finds an unsafe IGNORE conflict path; commit a95098fc fixes it and adds rollback tests. Migration replay and benchmark results are recorded in `2026-10-14-sqlite-column-fk-checks-validation.md`.
