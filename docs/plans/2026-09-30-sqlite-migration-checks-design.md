# Selective SQLite migration checks

Status: first milestone implemented in the isolated worktree. Final integration review remains pending.
Stage-2 table-rebuild preservation proof remains pending. Main integration and live-instance validation remain outside this work.

## Goal

Keep SQLite migrations fast when their operations cannot invalidate existing foreign keys.
Keep validation inside the migration transaction when an operation can invalidate a relation.
Support forward migrations, backward migrations, initial installs, and squashed migrations.
Keep existing migration files and migration records unchanged.

The user requests an isolated worktree because integration may happen later.
Branch: `bugfix/sqlite-migration-checks`.
Worktree: `.worktrees/bugfix-sqlite-migration-checks`.
Starting commit: `87ffc25f`.

## Evidence

- Installed Django: 6.0.4. Installed SQLite: 3.53.1.
- Django disables foreign key enforcement when entering its SQLite schema editor.
- Django runs `connection.check_constraints()` at every schema editor exit.
- With no table argument, that method executes `PRAGMA foreign_key_check` over the database.
- The migration executor opens a schema editor for each applied or unapplied migration.
- On the reported restart, migration startup takes about 197 seconds across two migration exits.
- A read-only check of the replay backup takes 178.520 seconds and returns no violations.
- A check of `core_session` takes 0.015 seconds. A check of `core_sessionhistoryfact` takes 0.323 seconds.
- Those timings use different cache and execution conditions from startup. They establish the expensive mechanism, not an exact startup benchmark.

A new isolated in-memory probe on this branch reproduces one global check for each case:

| Case | Global checks | Total time on empty database |
| --- | ---: | ---: |
| Create two empty tables, including a foreign key | 1 | 0.002112 seconds |
| Add a nonunique index | 1 | 0.000229 seconds |
| Execute no operation | 1 | 0.000075 seconds |

The probe uses Django's actual SQLite schema editor and a connection execution wrapper.
It does not import TwiCC, open its databases, or run its migrations.
The tiny timings are not performance predictions for a large database.

## Existing work

- [Django ticket #36170](https://code.djangoproject.com/ticket/36170) reports this exact scan cost, including no-op migrations.
- [Django PR #19278](https://github.com/django/django/pull/19278) skips validation when no schema SQL executes. It does not solve table or index creation.
- [SQLite ALTER TABLE documentation](https://sqlite.org/lang_altertable.html) distinguishes metadata changes from changes that read or rewrite existing rows.
- [Bugsink's external event storage](https://www.bugsink.com/blog/moving-event-data-out-of-the-database/) reduces the size of tables copied during migrations. This is a separate structural optimization.

## Options

### A. Skip checks only when no SQL executes

Small change, aligned with the Django PR.
It leaves both migration 0147 and the 0148/0149 squash expensive.
It is insufficient for the reported upgrade.

### B. Select checks from known schema operations and observed execution

Recommended direction.
Use a project SQLite backend and schema editor.
Keep Django's migration executor and migration files.
Track known operations, affected tables, affected columns, and unexpected execution.
Use SQLite's own table-scoped foreign key check for relations that can change.
Use the global check when the implementation cannot establish a narrower safe scope.

This avoids a separate SQL implementation of SQLite foreign key semantics.
The production backend implements the first milestone with disposable-database regression tests.
It retains global checks for table rebuilds until the second milestone proves preservation.

### C. Validate once after an entire migration batch

Reduces repeated scans but changes failure boundaries.
Earlier migrations may commit before a later integrity failure appears.
It also retains one expensive scan for an unrelated table creation.
Do not select this approach.

## Proposed architecture

Add a local backend under `src/twicc/db/backends/sqlite3/`.
Subclass Django's SQLite wrapper and schema editor.
Keep standard SQLite behavior outside schema editing.

The schema editor maintains a per-editor check decision:

1. No foreign key validation required.
2. Validate a set of surviving child tables.
3. Validate the whole database because the scope is unknown.

The decision only becomes more conservative during an editor context.
Unknown execution overrides earlier safe decisions.
No state remains on the connection after the context exits.

Use real database metadata for foreign key dependencies.
Inspect both outgoing relations and incoming relations.
Inspect only schema metadata during dependency discovery; do not scan session items.
Account for implicit M2M tables, self references, referenced unique columns, and tables outside the core app.

Observe execution on the connection, not only calls to `schema_editor.execute()`.
`RunPython` can write through the ORM or a cursor while enforcement is disabled.
`RunSQL` can call the editor directly without a known high-level schema operation.
Use SQLite's authorizer callback to observe write tables and UPDATE columns during statement compilation.
The callback must not execute SQL or query metadata.
Build the dependency snapshot before observation and refresh it after deferred SQL.
Do not use the callback as a per-row observer.
Installing the callback covers previously prepared statements in the tested Python/SQLite combination; pin that behavior in tests.

Do not select a check just because an operation is named `RunPython` or `RunSQL`.
A write to an ordinary column requires no FK scan unless a generated key, a trigger, or unique-conflict deletion introduces a relation effect.
Updates to FK columns check their child table.
Deletes and referenced-key updates check surviving child tables that reference the changed parent.
Parent INSERTs also check incoming relations: INSERT OR REPLACE can delete a conflicting parent without a separate DELETE authorization.
Parent UPDATEs of any unique-conflict input also check all incoming relations.
UPDATE OR REPLACE and schema-level ON CONFLICT REPLACE can delete a different parent without a DELETE authorization.
Capture unique-index key columns with index_xinfo, including primary keys.
Partial, expression, and generated unique keys require all parent UPDATEs to check incoming relations because their input dependencies are not proven.
Unknown schema changes and unclassified effects require the global fallback.

SQLite identifiers compare without ASCII letter case.
Normalize ASCII letters only; Unicode `casefold()` has different semantics.
Inspect generated columns with `PRAGMA table_xinfo`, not `table_info`.
If a generated FK or referenced key exists, conservatively check it on updates to that table.
This avoids parsing generated-column expressions.
Treat rowid-alias updates as possible key changes.

## Scope rules

These rules describe the target behavior. Implement each rule only after its proof and tests exist.

| Actual operation | Required scope |
| --- | --- |
| No database operation | No scan |
| Create a new empty table | No scan of unrelated existing tables; account for incoming references and deferred work |
| Add or remove a nonunique index | No FK scan if it cannot change parent-key validity |
| Add an ordinary nullable column through native ALTER TABLE | No FK scan if no relation changes |
| Rename an ordinary column | No FK scan only when affected FK definitions and referenced keys remain valid |
| Rebuild a table while preserving every FK and referenced key | No FK scan only with a proven value/type/collation preservation rule |
| Add or change an outgoing FK | Validate its surviving child table |
| Change a referenced key, its affinity, or its collation | Validate all affected surviving child tables |
| Delete a table | Inspect incoming references; validate surviving child tables when necessary |
| RunPython or RunSQL that changes ordinary data | No scan when observed effects exclude relation changes and unique-conflict deletion |
| RunPython or RunSQL that changes FK or referenced-key data | Check affected surviving child tables |
| Unknown schema SQL or unclassified effects | Global check |

A table-scoped PRAGMA checks all foreign keys of that child table.
It cannot validate just one relation.
That is acceptable when the operation can invalidate rows of that table.
Do not scan the large child table solely because an unrelated field changes.

Dropping and recreating a table is not sufficient evidence that all its relations change.
Conversely, an unchanged FK declaration is not sufficient evidence that its values stay valid.
Defaults, null replacement, affinity changes, and referenced-key changes must be considered.

## Transaction and exit behavior

Complete deferred SQL before the final integrity decision and validation.
Validate before the atomic migration commits.
On operation or validation failure, exit the transaction with the exception.
Restore foreign key enforcement and remove observers in a `finally` path.
Preserve the original exception if cleanup also fails.

Do not use a background validation task: it would run after the migration commits.
Do not claim complete rollback for an `atomic=False` migration.
Reject nested editor contexts and entry inside an existing atomic block before changing PRAGMA state or authorizer ownership.
Do not use a standard-editor fallback inside an active optimized editor.
Keep `sqlmigrate`/`collect_sql` behavior free of actual database changes.

Only main-schema user mutations are supported by the optimized editor.
Reject attached/temp DDL or DML and ATTACH/DETACH during editing before the unsupported statement executes.
An unqualified global foreign_key_check covers main only; it is not a fallback for attached/temp mutations.
Allow read-only access to previously attached/temp schemas.
Allow SQLite's internal temp catalog maintenance during main-schema ALTER TABLE.
Reject writable_schema setters and entry with writable_schema enabled, so this exception cannot authorize direct catalog editing.
Existing atomic=False effects before an unsupported statement may already be committed; the rejected statement itself must not execute.

## Prototype results and remaining proof obligations

The standalone probe is `scripts/prototypes/sqlite_migration_checks.py`.
It imports Django but never imports TwiCC. Every database is in memory.
It calls the real Django migration executor, including migration recording and backward migration execution.
Its editor subclasses are experimental code, not a production backend.

Results:

- Data-only editing with enforcement active avoids a scan and rejects an invalid FK.
- Keeping enforcement active for all schema work fails a valid parent-table reconstruction. Do not use that as the generic solution.
- SQLite's authorizer observes ORM, Django cursor, native cursor, executemany, cached statements, and trigger writes in the tested cases.
- Ordinary-column changes through RunPython and RunSQL avoid the global scan.
- Invalid FK changes and parent deletion trigger selected-table checks and atomic rollback.
- Backward RunPython follows the same rules and preserves the applied migration record on failure.
- Migration-record writes need no special exemption: their table has no relevant relations in the prototype.
- Deferred indexes execute once, before validation and commit.
- The actual 0147 and squash migration classes apply and unapply without a FK scan on a minimal historical core schema.
- The replay removes the replacement migration records and restores the schema. It does not test the complete historical graph.
- A generated parent key exposed an actual missed violation in the first observer. Adding generated-column metadata fixes that case.
- Uppercase parent/key references require identifier normalization.
- Independent review reproduces unobserved deletion from UPDATE conflict replacement and uncovered attached-schema violations.
- Added unique-conflict metadata fixes plain, schema-level, partial, expression, and generated-key replacement cases.
- Unsupported attached/temp writes now fail before execution instead of using a main-only global check.
- Nested-editor rejection preserves the outer observer and enforcement state.
- Rebuilds remain a deliberate global fallback in the prototype.

Completed first-milestone production obligations:

1. Restrict trusted schema scopes to exact standard operations and expected objects. A broad nesting flag is insufficient.
2. Validate immutable deferred-statement provenance. The prototype's object-identity shortcut is not a production guarantee.
3. Cover all schema authorization codes, unknown codes, unsupported-schema rejection, unique-index removal, and unsupported driver features.
4. Prove exception cleanup, atomic=False behavior, and nested contexts in the production backend.
5. Run the complete migration graph, squash bookkeeping, and sqlmigrate compatibility on disposable project databases.

The production editor now covers obligations 1-5.
The complete graph tests cover fresh install, populated replay, original 0149 in the mixed 0148 state, and replacement bookkeeping.
The original 0149 applies and reverses before replacement bookkeeping; normal migrate then records the replacement.
A fresh loader reverses the replacement through 0146 and removes the original and replacement records.
Every known replay operation executes zero FK-check PRAGMAs, including with existing SessionItem rows.

Pending stage-2 preservation obligations:

- Compare the old and new outgoing FK definitions and incoming referenced keys.
- Prove each copied key maps to the same value with the same SQLite storage and comparison semantics.
- Account for affinity, collation, defaults, null replacement, generated columns, and unique-conflict behavior.
- Prove renamed keys and native column operations preserve every affected relation.
- Test valid and invalid existing rows, forward and backward paths, and atomic failure rollback.
- Retain global validation for every rebuild without that proof.

The supported migration contract does not include disabling or replacing the backend observer through raw driver APIs.
Use the standard-backend opt-out for migrations that need unsupported connection customization.
Do not silently broaden the implementation into a generic SQL parser or copy of Django's migration engine.

## Delivery stages

### Stage 1: prove integration and solve the observed upgrade

Cover no-op editing, empty table creation, nonunique indexes, and their backward paths.
Unknown execution retains a global check.
Known data effects use column-aware dependency checks, regardless of RunPython or RunSQL operation names.
Replay 0147 and the 0148/0149 squash on a disposable database.
Do not rewrite their migration files or add a compute-version change.

### Stage 2: cover ordinary field changes and affected relations

Extend the classification to native column operations and table rebuilds.
Add dependency-scoped checks for changed outgoing and incoming relations.
A conservative fallback remains explicit and logged for unproved cases.

Stage 1 alone does not fulfill every target rule for large-table modifications.
Report that limitation if integration happens before stage 2.

## Validation

Use disposable SQLite databases for all writes.
Never apply worktree migrations to `/home/twidi/.twicc/db/data.sqlite`.
Keep the existing replay backup pristine.
Set `TWICC_DATA_DIR` to this worktree for every project Python invocation.
Verify the imported TwiCC path and resolved database path before data-dependent commands.

Required correctness cases:

- Actual forward and backward migrations through Django's executor.
- The 0146 -> 0147 -> squash upgrade and its reverse path.
- Valid and invalid added foreign keys, including defaults on existing rows.
- Parent deletion with surviving children; deletion of both parent and children.
- Referenced unique keys, self references, implicit M2M, and multiple FKs per table.
- Ordinary-field rebuild with unchanged keys; key type/collation changes.
- `RunPython` ORM writes and `RunSQL` after otherwise safe schema work.
- Deferred SQL, operation failure, validation failure, and enforcement restoration.
- Migration records and schema rollback on atomic failure.
- Non-atomic migrations, unknown custom operations, and collected SQL.
- Fresh database installation and migration squash replacement bookkeeping.
- Existing database triggers and unexpected execution paths.

Performance assertions should inspect executed checks and their scopes.
They must not depend on noisy wall-clock thresholds in unit tests.
For large synthetic data, compare operation time, validation time, and total time.
Vary both row count and payload size.
Use repeated measurements and report cold/warm conditions separately.
An unchanged large SessionItem table must not be scanned for the 0147/squash upgrade.

## Logging and operational controls

Log the migration duration and the FK-check duration separately.
Log check scope: skipped, selected tables, or global fallback with reason.
Include direction and migration identifier when a supported integration point provides them.
Do not infer operation duration from migration-record timestamps.
Keep logs compatible with startup verbosity 0.

Subclass the management command's migration progress callback for migration identity and elapsed time.
Delegate its handle method to Django; do not copy migration execution logic.
Clear logging context after both command success and command failure.
There is no Django failure progress callback. Catch failures in handle(), log the active migration, then restore the context.
fake_initial may start with fake=False and finish with fake=True; use the final callback's flag for outcome logging.

Provide `TWICC_SQLITE_STANDARD_MIGRATIONS=1` as a startup-time opt-out that selects standard Django SQLite behavior.
The settings selection applies before connections open, for command-line migrations and application startup.
Test settings explicitly select the optimized backend. Comparison fixtures instantiate the standard backend separately.

The command subclass delegates execution and CLI output to Django.
ContextVar state identifies each migration and direction; finally restores the caller's previous context.
The callback records the final fake flag, including fake_initial's false-to-true transition.
FK-check logs include scope, selected tables, reason, check duration, and success.
Migration logs separately include total duration, direction, fake outcome, and failure identity at verbosity 0.

First-milestone limits remain explicit:

- Native column changes and table rebuilds can still require global validation.
- Atomic editing owns transaction controls; user BEGIN/COMMIT/ROLLBACK are rejected before execution.
- atomic=False preserves already committed effects and cannot provide full rollback.
- Only main-schema mutations are supported; attached/temp read-only access remains allowed.
- Raw-driver replacement or removal of the observer is outside the supported contract.
- Synthetic in-memory timings measure this probe only; they do not predict live startup duration.

## Non-goals

- No PostgreSQL migration, storage split, or JSON relocation.
- No recompute or compute-version change.
- No new schema migration for this backend change.
- No global disabling of foreign key integrity.
- No live-instance restart, production migration, or automatic integration into main.

## Review checklist

- No reliance on an operation name alone when arbitrary writes can follow it.
- Table rebuilds keep global validation until stage-2 preservation proof exists.
- No custom approximation of SQLite's FK comparison rules.
- No frozen migration-file rewrite or runtime global monkeypatch.
- Unknown cases fall back explicitly and remain visible in logs.
- Proof obligations for stage 2 remain visible rather than implied complete.

Review result: the implemented first milestone retains the production proof obligations above.
Tasks 1 and 2 pass independent review. Task 3 integration review remains pending.
The table-rebuild target remains unproved and must not be reported complete with the first milestone.
