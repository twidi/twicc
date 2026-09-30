# Migration Process Isolation Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Follow the task and independent review loop.

**Goal:** Use the custom SQLite backend only in a temporary migration process.

**Architecture:** The application uses Django's standard SQLite backend. Startup waits for a child Python process running Django migrate with separate settings. The child exits before normal startup continues.

**Tech Stack:** Python 3.13, Django 6, SQLite, stdlib subprocess.

**Spec:** The user-approved design in this session: separate migration settings import normal settings, copy database dictionaries, change only the engine, inherit environment, preserve blocking startup and backend.log diagnostics.

## Global Constraints

- Work only in the existing dedicated worktree. Do not restart servers or migrate live data.
- Normal runtime uses django.db.backends.sqlite3 and must not load the custom driver or its adapter registration.
- Migration settings use twicc.db.backends.sqlite3 unless TWICC_SQLITE_STANDARD_MIGRATIONS=1 requests the standard fallback.
- Both processes use the same database path and SQLite options. Inherit the parent environment without a replacement environment map.
- Keep the instance lock in the parent. Wait for migrations before backfill, server, Watcher, or compute startup.
- Log migration diagnostics and subprocess failures to backend.log. Preserve useful stderr tracebacks without duplicating normal migration log records.
- No new dependencies, schema migrations, compute versions, main integration, or unrelated test fixes.
- Preserve pre-existing uncommitted review and validation documents.

## Review Focus

- Failed migration or child launch prevents normal startup and preserves error diagnostics.
- Interrupted parent does not leave a migration child running after lock release.
- Explicit settings selection wins over inherited DJANGO_SETTINGS_MODULE.
- Settings dictionaries remain independent; normal interpreter never imports the custom driver.
- Test and manual migration workflows still exercise the optimized backend explicitly.

### Task 1: Isolate migration execution

**Files:**
- Modify: src/twicc/settings.py
- Create: src/twicc/settings_migration.py
- Create: src/twicc/db/migration_process.py
- Modify: src/twicc/cli/run.py
- Create: tests/test_migration_process.py
- Modify: existing SQLite backend settings/integration tests and documentation that depend on default engine selection.

**Interfaces:**
- Produce run_migrations() in twicc.db.migration_process. It blocks until the child finishes and raises on failure or interruption.
- Startup replaces call_command("migrate", verbosity=0) with run_migrations() at the same location.

- [x] Write failing tests for standard runtime engine, isolated migration settings, actual child migration/logging on a disposable database, failure propagation, and child cleanup on interruption.
- [x] Run focused tests and capture expected failures before product edits.
- [x] Implement the settings split and child launcher using sys.executable, -m django, migrate, --settings=twicc.settings_migration, --verbosity=0. Avoid shell=True. Ensure child termination and reaping precede parent error propagation.
- [x] Update optimized migration test entry points and documentation to select migration settings. Keep ordinary runtime tests on standard settings.
- [x] Run focused launcher/settings/integration/backend regression tests. Use TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest. Record outputs. Do not repeat the known-failing full suite without a specific new reason.
- [x] Self-review and commit only task-owned changes with a descriptive conventional commit and exact model trailer.

## Task 1 Verification

- 330 focused launcher, settings, SQLite backend, integration, and peer migration checks pass.
- One additional child launch failure check passes.
- Runtime and ordinary test settings use the standard backend.
- SIGINT and SIGTERM stop and reap the child before lock release. Cleanup escalates to kill after five seconds.
- SIGKILL cannot run parent cleanup. The child does not inherit the instance lock descriptor.
- Full-suite checks remain out of scope because standard-backend failures are already documented.
