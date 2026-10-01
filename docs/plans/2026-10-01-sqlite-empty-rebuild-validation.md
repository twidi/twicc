# Empty SQLite rebuild validation

## Scope

The migration-only backend keeps Django's rebuild procedure. It narrows FK validation when a table is created and rebuilt empty within one atomic schema editor. Normal runtime connections still use Django's standard SQLite backend.

Migration 0146 also creates the conditional unique index `uniq_agent_run_end_transcript`. Its standard `UniqueConstraint` needs a separate proof for a newly created empty table. Custom constraint subclasses and constraints on existing tables retain the global fallback.

## Proof boundary

- The source table must have a proven creation in this editor and must not exist in its initial schema.
- The generated `new__` table must be absent from the initial and current schemas.
- `SELECT 1 ... LIMIT 1` checks emptiness only after creation provenance passes. Existing tables are not scanned for this proof.
- Django executes its own `_remake_table`. No copied rebuild implementation or SQL parser is used.
- A transient INSERT authorization is staged only when one execution produces exactly that write, no schema effects, and no `total_changes` increase. Both source and destination remain empty.
- The rename must match Django's exact generated statement. Its authorizations must contain exactly one main-table ALTER plus recognized mechanical catalog updates.
- Before/after metadata must leave every other table unchanged. This detects FK references that SQLite rewrites from a transient table name.
- Staged INSERT and rename authorizations are removed only after the full rebuild returns, the destination remains empty, the transient table disappears, and `total_changes` remains unchanged.
- Independent writes, callback effects, unknown reasons, and other schema authorizations remain observed. Source DELETE authorizations remain observed.
- Only intact standard deferred index statements follow Django's reference rename. Custom index provenance remains unchanged.
- Existing incoming references remain in the final selected-table checks. Invalid references roll back before commit.

## Validation

The focused regression covers actual 0145 to 0146 execution with ten historical `SessionItem` rows. It asserts no FK check statements, `scope=none`, and three executed rebuild renames. Existing data and the migration record survive.

Other cases cover repeated rebuilds, populated tables, existing empty tables, raw table creation, custom constraints, field callbacks, trigger writes, unknown PRAGMAs, unrelated schema effects, transient-name references, generated-name collisions, and invalid references with rollback.

The complete focused suite includes migration process, effects, metadata, schema, and integration tests. It passes all 347 tests in 84.76 seconds. The initial regression run has three expected failures; the final regression selection passes 15 tests. A separate trace-capture run passes in 8.10 seconds. Exact commands and SQL traces are retained in the task report.

## Limits

The proof applies only to atomic rebuilds. A row change anywhere during a rebuild prevents acceptance, even when a callback later restores the original data. Existing and populated tables keep global validation. Unsupported authorization shapes fail closed. This work does not remove Django rebuilds or change frozen migrations.

No live database snapshot, migration, server startup, or server restart is performed. The historical broad-suite failures remain outside this task.

## Real copied-database replay

On 2026-10-01 the user snapshots the main database and rolls the disposable worktree copy back to0145. The database file before startup is20,712,083,456 bytes. Read-only preflight verifies0145 as latest applied core migration and13,444 sessions with NULL compute_version.

Normal devctl startup reapplies0146 in1.029560s with scope=none and zero FK-check duration.0147 takes0.040524s, and the148-149 replacement takes0.036964s; both also have scope=none. The prior reverse146 global FK check takes92.841278s. The application starts at08:52:40 and returns HTTP200 on port3502 despite the devctl verification timeout.

The worktree servers stop before cleanup. This evidence records the real startup; it does not claim background recompute completes.

```text
[2026-10-01 08:34:50,256 -   INFO -      global - twicc.db.migrations] Migration FK check migration=None direction=None scope=global tables=() reason=unproved main-schema effects fk_check_seconds=92.841278 success=True
[2026-10-01 08:52:37,431 -   INFO -      global - twicc.db.migrations] Migration start migration=core.0146_agent_runs direction=forward fake=False duration_seconds=0.000000
[2026-10-01 08:52:38,259 -   INFO -      global - twicc.db.migrations] Migration FK check migration=core.0146_agent_runs direction=forward scope=none tables=() reason=no observed relation effects fk_check_seconds=0.000000 success=True
[2026-10-01 08:52:38,461 -   INFO -      global - twicc.db.migrations] Migration success migration=core.0146_agent_runs direction=forward fake=False duration_seconds=1.029560
[2026-10-01 08:52:38,461 -   INFO -      global - twicc.db.migrations] Migration start migration=core.0147_session_history_fact direction=forward fake=False duration_seconds=0.000000
[2026-10-01 08:52:38,495 -   INFO -      global - twicc.db.migrations] Migration FK check migration=core.0147_session_history_fact direction=forward scope=none tables=() reason=no observed relation effects fk_check_seconds=0.000000 success=True
[2026-10-01 08:52:38,501 -   INFO -      global - twicc.db.migrations] Migration success migration=core.0147_session_history_fact direction=forward fake=False duration_seconds=0.040524
[2026-10-01 08:52:38,501 -   INFO -      global - twicc.db.migrations] Migration start migration=core.0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index direction=forward fake=False duration_seconds=0.000000
[2026-10-01 08:52:38,538 -   INFO -      global - twicc.db.migrations] Migration FK check migration=core.0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index direction=forward scope=none tables=() reason=no observed relation effects fk_check_seconds=0.000000 success=True
[2026-10-01 08:52:38,538 -   INFO -      global - twicc.db.migrations] Migration success migration=core.0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index direction=forward fake=False duration_seconds=0.036964
```
