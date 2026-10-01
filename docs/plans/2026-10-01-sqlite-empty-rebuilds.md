# SQLite checks for newly created empty tables

## Spec

Migration 0146 creates AgentInteraction and AgentRunEnd, then Django rebuilds these empty tables three times. The current migration-only backend classifies the rebuild rename effects as unproved and runs a global foreign_key_check. Avoid that unnecessary global scan without editing historical migrations or removing required FK validation. Django still performs its standard rebuilds. Optimize validation only.

Acceptance: an actual forward MigrationExecutor upgrade from 0145 through 0146 on a disposable populated database performs no foreign_key_check against unrelated existing tables, normally scope=none. Continue to observe all effects and validate before atomic commit. Existing-table rebuilds, unproved SQL, and unsupported operations retain their previous global fallback. New-table writes and incoming references must not escape validation. Empty-source proof cannot rely on SQL authorizations alone, because INSERT SELECT is authorized even when it copies no rows. Use bounded emptiness checks, not COUNT(*) or scans of existing large tables. Avoid copying Django's entire rebuild method or adding a SQL parser.

The user also requests an ignored temporary worktree replay script. It snapshots the main database with SQLite online backup pages=-1, rolls back only the copied worktree database to 0145, and prepares normal startup replay. The user runs the script and starts/restarts later. Do not run the large snapshot, rollback a real DB, or start/restart any server during implementation.

## Global Constraints

- Work only in /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds on bugfix/sqlite-empty-rebuilds. Prefix shell commands with cd into that worktree.
- Preserve main user changes and real ~/.twicc data. No migrations against a live DB. Disposable tests only.
- Normal runtime engine remains django.db.backends.sqlite3. Changes stay in the migration-only backend.
- Do not edit frozen migrations, dependencies, compute versions, or released changelog sections.
- Do not suppress unrelated authorizer effects or unknown reasons. Validation remains before commit with rollback on failures.
- Prove the recognized rebuild belongs to a table created in this schema editor, absent from its initial schema, and empty around the rebuild. Handle repeated rebuilds and generated temporary names conservatively.
- Existing incoming FK dependencies must remain validated, or retain global fallback if safe scoping cannot be proved.
- Use existing instance lock and paths confinement for the replay script. Destination DB is disposable; no extra DB backup/archive. Never use raw file copy or stepped online backup for the live main DB.
- Temporary replay script stays git-ignored outside the SDD workspace so workspace cleanup does not delete it. All durable docs and code in English.
- Focused regression suite: test_migration_process.py and all five SQLite migration test modules (effects, metadata, schema, integration; launcher is fifth total alongside four sqlite modules). Historical broad-suite baseline failures are documented in docs/plans/2026-09-30-sqlite-migration-checks-validation.md; do not fix unrelated tests or run repeated broad suites.

## Task 1: Prove newly created empty table rebuilds

Implement a narrow proof around Django rebuild execution using existing guarded authorizer and before/after schema metadata. Read schema.py, effects.py, driver.py, metadata.py and actual Django _remake_table. Determine a safe proof contract before implementing. Escalate if provenance cannot distinguish unrelated SQL effects. Keep implementation focused; refactor shared proof helpers only where needed.

Write failing regression tests before product edits. Cover actual 0145 -> 0146 upgrade (seed existing SessionItem data using historical models), several rebuilds on one newly created empty table, new table populated before rebuild, existing table rebuild, incoming FK references, unclassified/custom operations, unrelated writes/triggers during rebuild, and rollback on invalid FK effects. Test required security/correctness edges with actual SQLite execution rather than only mocks. Do not presume emptiness alone permits discarding any authorizer event. If conservative guards deliberately reject a scenario, record it.

Implement and run focused covering tests, then the complete focused migration suite once. Record exact RED/GREEN commands, output, FK scopes/query traces for 146, and limits. Add an Unreleased changelog entry after re-reading its top. Commit product/tests/docs changes with descriptive Conventional Commit and exact model trailer. Never spawn subagents. Report to the task report file supplied by the controller.

## Task 2: Prepare the disposable worktree upgrade replay

Recover scripts/replay-compute-upgrade.sh from 8fc7e186^ (deletion commit); its historical version is also available at /tmp/twicc-prior-replay-script.sh. Read devctl.copy_data_from_main for the corrected snapshot algorithm. Write .superpowers/replay-empty-rebuild-upgrade.py, a temporary ignored script, outside this plan SDD workspace. Add --copy-main for a fresh snapshot and support replay of an already copied DB. Print flushed phase logs and timings before/after snapshot and rollback.

Require a linked git worktree. Set TWICC_DATA_DIR to its root before importing twicc. Use twicc.settings_migration, reject TWICC_SQLITE_STANDARD_MIGRATIONS=1 for this benchmark. Verify actual imported twicc path and resolved DB/data/log/lock paths are confined to this worktree, including existing symlinks, before opening destination connections or changing files. Acquire InstanceLock for the complete operation; refuse running instance. Default source ~/.twicc/db/data.sqlite opened SQLite mode=ro; destination worktree db/data.sqlite. Copy with source.backup(target, pages=-1), explicit closes, no source writes/checkpoint, no extra archive. When --copy-main is used, replace only destination DB and its -wal/-shm/-journal sidecars, and clean an incomplete snapshot on failure. Never touch artifacts/scratch symlink targets.

Read and validate the MigrationExecutor plan before rollback; only backwards core migrations above 0145 are allowed. Require 0146 applied to make the test meaningful. Roll back copied database to core 0145_processrun_background_work_in_progress with the migration settings. Verify resulting schema/applied state. Reset copied Session.compute_version to NULL so normal startup reconstructs metadata and facts; verify schema supports it. No automatic startup/restart. Final output tells user exact command for devctl start and migration log file path. Retain no separate backup since main remains source.

Test with disposable small source/destination DBs including committed WAL contents, path/symlink confinement, running lock refusal, failure cleanup, actual rollback from latest historical schema to145, and source data invariance. Do not snapshot or rollback real main/worktree DB. Keep script and temporary test harness ignored, not committed. Commit a concise English usage/validation doc in docs/plans/2026-10-01-sqlite-empty-rebuild-replay.md if useful. Report script path, command, timings, validation, and caveats. Never spawn subagents.
