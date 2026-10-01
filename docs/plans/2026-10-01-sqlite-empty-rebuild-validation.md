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
