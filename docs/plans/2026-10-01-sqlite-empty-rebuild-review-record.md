# Empty rebuild implementation and review record

Branch: `bugfix/sqlite-empty-rebuilds`. Base: `8fc7e186`. Reviewed product head: `f5fc0178`.

The worktree remains stopped. The user runs the real snapshot and rollback, then starts it explicitly.

The forward145-to146 regression has no foreign_key_check. Reverse146 retains global validation. Rollback script logs overall phase durations; subsequent startup logs per-migration identity and durations.

## progress.md

# SDD ledger — plan: docs/plans/2026-10-01-sqlite-empty-rebuilds.md

Base: 8fc7e186. Worktree: bugfix-sqlite-empty-rebuilds.
Spec is embedded in plan. User authorizes implementation and preparation only; real snapshot, rollback and restart reserved for user.

## Preflight

| Tasks | Interface or internal consistency | Result |
| --- | --- | --- |
| 1 / 2 | Backend decision and actual145->146 replay | Backend retains standard Django rebuild; script replays check optimization, not rebuild elimination. |
| 1 | Empty proof versus independent side effects | Proof must retain unrelated effects; new empty table alone is insufficient. |
| 2 | Snapshot source versus rollback destination | Source mode=ro, backup pages=-1, instance lock and path confinement precede destination mutations. |

Task 1: pending. Task 2: pending.

Baseline: 332 migration/launcher tests passed in71.49s. Main real data untouched.
Task1: implementer /root/empty_rebuild_implement (Astra), base8fc7e186; analysis approved to proceed after GREEN baseline.

Task1 RED: 3 expected failures (empty repeated rebuild, scoped orphan validation, actual145->146),6 safety cases pass.
Task1 implementation note:146 conditional UniqueConstraint also produces unproved index creation; narrow standard constraint proof for newly-created empty table required by actual146 acceptance. Existing-table and custom-constraint fallback remains unchanged.

Task1: complete (commits8fc7e186..cb17cc5c, spec compliant and quality approved).347 focused passed,146 dedicated trace noFKqueries scope=none.
Task1: minor (deferred): uv inherited VIRTUAL_ENV mismatch produces expected setup warning; imports and targeted environment verified.
Task2: dispatch pending, basecb17cc5c.

Task2 review: Important env overwrites settings module before destructive snapshot; fixround1 original implementer resumed. Fixbase43f5281f. RequireCLI regression and preflight before destination changes.

Task2: fix round1/5 (1 addressed,0open; ignoredscript settingsbootstrap corrected, doccommit43f5281f..f5fc0178).9disposable groupsPASS, freshprocess CLIoverride and refusal preservingdest.
Task2: complete (commitscb17cc5c..f5fc0178, spec compliant and quality approved after scopedrecheck).
Finalreview: pending, range8fc7e186..f5fc0178; task1Minor inherited VIRTUAL_ENVwarning notproductfailure.

Final review: clean (noCritical/Important/newMinor), reviewer /root/empty_rebuild_final_review.
Controller disposition of declined-to-judge items: real snapshot speed awaits user test; reverse146 existing-table scans and phase-only rollback timings are explicit limits, not new requirements; normal startup has per-migration timings. Historical unrelated suite failures remain out of scope; existing local lock/confinement contract retained. No additional product design ruling.
Ready for user snapshot/restart, not merged.


## task-1-report.md

# Task 1 report

## Status

DONE. Implementation and focused validation complete. Independent review belongs to the controller.

## Changes

- Wrap Django's `_remake_table`; retain its original execution, including all three rebuilds in migration 0146.
- Prove newly-created provenance, absence from initial schema, absent generated temporary name, bounded source emptiness, unchanged native `total_changes`, final empty destination, and removed temporary table.
- Stage only exact zero-row transient INSERT authorizations. Do not discard independent writes or all effects from a rebuild context.
- Prove the exact generated rename SQL with an authorizer whitelist and before/after metadata comparison for every other table.
- Preserve source DELETE effects, unknown reasons, unrelated schema changes, callback writes, and trigger writes.
- Follow only intact standard Django deferred index statement references through the rename.
- Add narrow standard UniqueConstraint provenance for new empty tables. Actual 0146 also required this because its final conditional unique index was separately unproved. Custom constraints and existing-table constraints keep fallback.
- Preserve incoming-reference checks and rollback on invalid references.
- Add an Unreleased changelog entry and durable proof/validation notes.

## Files

- `src/twicc/db/backends/sqlite3/schema.py`
- `src/twicc/db/backends/sqlite3/effects.py` (observer contract documentation)
- `tests/test_sqlite_migration_schema.py`
- `tests/test_sqlite_migration_integration.py`
- `CHANGELOG.md`
- `docs/plans/2026-10-01-sqlite-empty-rebuild-validation.md`

The controller's existing untracked plan is not part of this commit.

## TDD evidence

Every command runs in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds`.

RED, before product edits:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_sqlite_migration_schema.py tests/test_sqlite_migration_integration.py -k 'repeated_rebuilds or empty_rebuild or populated_145' -q
```

Result: **3 failed, 6 passed, 196 deselected in 10.15s**. Failing cases:

- `test_new_table_repeated_rebuilds[False]`: unwanted global FK check instead of none.
- `test_empty_rebuild_checks_incoming_orphans_and_rolls_back`: unwanted global check instead of only orphan.
- `test_populated_145_upgrade_146_skips_unrelated_checks`: unwanted global check instead of none.

Output: `red.log` beside this report.

The first implementation run passed repeated rebuilds but found two follow-ups. The orphan fixture needed a real PK because Django's scoped violation reporter assumes one. The actual146 trace isolated the final conditional UniqueConstraint as the remaining unproved schema operation. The fixture and narrow constraint provenance were corrected. No historical migration changed.

GREEN, same command: **9 passed, 196 deselected in 9.05s**. Output: `green.log`.

Additional safety coverage, same command after adding conservative-fallback tests: **15 passed, 196 deselected in 11.49s**. Output: `covering.log`.

## Complete focused suite

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py tests/test_sqlite_migration_effects.py tests/test_sqlite_migration_metadata.py tests/test_sqlite_migration_schema.py tests/test_sqlite_migration_integration.py -q
```

Result: **347 passed in 84.76s**. Exit code 0. Output: `full.log`.

The controller's unchanged baseline was 332 passed in 71.49s. The final suite adds 15 cases. No broad suite is repeated, per the task constraints and documented historical failures.

All uv commands emit the expected inherited `VIRTUAL_ENV` mismatch warning. uv ignores that environment and uses this worktree. Test output has no failures or pytest warnings.

`git diff --check` passes.

## Migration 0146 evidence

The real MigrationExecutor test migrates a disposable database to 0145, seeds ten SessionItem rows through historical models, and migrates to146. It verifies:

- FK query trace: `[]`.
- FK scope records: `["none"]`.
- Three actual `ALTER TABLE "new__..."` statements execute.
- Ten existing SessionItem rows remain.
- Migration 0146 is recorded as applied.

A dedicated passing run captures the full SQL trace in `146-trace.sql` beside this report. `trace-run.log` records that run. The capture wraps only the test's trace context and adds no production instrumentation.

## Safety coverage and limits

Real SQLite cases cover repeated empty rebuilds, populated source fallback, existing-table fallback, raw creation fallback, custom constraint fallback, field callback writes, trigger writes, callback schema mutations, unknown PRAGMAs, existing references to a generated temporary name, incoming orphan checks, invalid populated child rollback, and generated-name collisions with rollback.

Non-atomic rebuilds retain global fallback. Any row change during the entire rebuild prevents the proof, including unrelated valid writes and insert-then-delete callbacks. Existing incoming references remain validated; rewritten temporary-name references force global fallback. Unknown schema authorization shapes retain global validation. No existing large table is probed for emptiness.

Metadata equality is not a generic schema proof. The exact rename statement and authorization whitelist remain mandatory. Only effects from its own execution are candidates for removal. Effects outside that execution remain untouched.

No server starts or restarts. No real database migration or snapshot. No dependency changes. No frozen migration edits. The migration-only backend remains isolated from runtime.

## Self-review

Reviewed the complete product/test diff, proof boundaries, generated-name handling, mutation retention, and constraints. Added an atomic-only guard and an initial-schema temporary-name guard before the complete focused run. Updated the observer contract to explicitly describe proved zero-row INSERT removal. No unresolved correctness concern identified. `schema.py` grows by roughly 145 lines; the implementation stays within that existing proof module.

## Commit

`cb17cc5c` — `fix(sqlite): prove newly created empty table rebuilds`

Trailer: `Co-Authored-By: Codex GPT-6 Astra <codex@openai.com>` (current agent model confirmed by controller).

Final dedicated trace run: **1 passed in 8.10s**. The actual rename trace is:

```sql
ALTER TABLE "new__core_agentinteraction" RENAME TO "core_agentinteraction"
ALTER TABLE "new__core_agentrunend" RENAME TO "core_agentrunend"
ALTER TABLE "new__core_agentinteraction" RENAME TO "core_agentinteraction"
```

There are no `PRAGMA foreign_key_check` statements. Bounded emptiness queries target only these two newly created tables and their two generated temporary names.


## task-1-review.md

### Spec Compliance

- ✅ Spec compliant. The implementation preserves Django rebuild execution and optimizes only validation.
- `src/twicc/db/backends/sqlite3/schema.py:219` requires atomic execution, standard creation provenance, initial-schema absence, and bounded emptiness.
- `src/twicc/db/backends/sqlite3/schema.py:239` requires unchanged `total_changes`, final emptiness, removed temporary table, and one proved rename.
- `src/twicc/db/backends/sqlite3/schema.py:356` stages individual zero-row INSERT effects. It retains independent writes and unknown reasons.
- `src/twicc/db/backends/sqlite3/schema.py:405` requires exact rename provenance, recognized authorizations, and unchanged metadata for other tables.
- `tests/test_sqlite_migration_integration.py:381` verifies actual populated 0145→0146 execution, three rebuilds, no FK queries, and preserved rows.
- The diff changes no frozen migrations, runtime backend settings, dependencies, or released changelog sections.
- Task 2 replay preparation remains outside this review.

### Strengths

- `src/twicc/db/backends/sqlite3/schema.py:260` follows only intact standard deferred index statements through Django's rename.
- `src/twicc/db/backends/sqlite3/schema.py:283` limits added constraint provenance to standard UniqueConstraint on newly created empty tables.
- `tests/test_sqlite_migration_schema.py:1264` verifies repeated rebuilds and populated-table fallback with actual SQLite execution.
- `tests/test_sqlite_migration_schema.py:1286` checks independent writes, trigger writes, schema mutations, unknown PRAGMAs, and transient references.
- `tests/test_sqlite_migration_schema.py:1336` verifies incoming orphan checks and rollback before commit.
- `tests/test_sqlite_migration_schema.py:1378` verifies existing-table and raw-creation fallback without scanning existing tables for emptiness.
- `tests/test_sqlite_migration_schema.py:1398` verifies invalid populated-child rollback.
- `tests/test_sqlite_migration_schema.py:1420` verifies generated-name collision rollback and preservation of existing temporary-name data.

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- None.

#### Minor (Nice to Have)

- `.superpowers/sdd/2026-10-01-sqlite-empty-rebuilds/full.log:1` contains the inherited `VIRTUAL_ENV` mismatch warning.
- The report explains this expected warning. uv correctly selects the worktree environment. This does not invalidate the results.
- Future validation commands can unset inherited `VIRTUAL_ENV` to produce clean output. Do not use `--active`.

### Assessment

**Task quality:** Approved.

**Reasoning:** The proof removes only identified empty-copy and rename events. Unsupported operations preserve conservative validation.

### Focused External Checks

- Risk: removing transient effects could suppress required incoming-reference validation.
- Checked `src/twicc/db/backends/sqlite3/effects.py:134` onward. Its decision uses before/after dependencies and retains source DELETE effects.
- Checked `src/twicc/db/backends/sqlite3/schema.py:94` onward because the diff truncates this unchanged transaction boundary.
- `schema.py:112` adds existing incoming children for newly created parents. `schema.py:126` validates before `schema.py:167` finishes the atomic transaction.
- Risk: standard constraint provenance could accept unrelated effects. Checked the unchanged `_matches` tail at `schema.py:440` onward.
- That matcher permits only the exact index operation and mechanical catalog events. Independent data effects remain in the observer.
- Read `full.log` and `covering.log`: 347 passed and 15 passed respectively. No suites or probes rerun.
- The initial combined read truncates part of the diff. A subsequent read retrieves only that missing portion.

### Scope Limit

- Existing-table rebuilds retain global fallback, including rebuilds used while reversing migration 0146.
- This task does not optimize arbitrary reverse-migration operations. The acceptance target is actual forward 0145→0146 execution.


## task-2-report.md

# Task 2 report

## Implementation

- Ignored script: `.superpowers/replay-empty-rebuild-upgrade.py`.
- Ignored harness: `.superpowers/test-replay-empty-rebuild.py`.
- Committed usage document: `docs/plans/2026-10-01-sqlite-empty-rebuild-replay.md`.
- Historical script read from `/tmp/twicc-prior-replay-script.sh`; corrected snapshot algorithm read from `devctl.copy_data_from_main`.
- Source mode=ro, online backup pages=-1, explicit closes, destination sidecar replacement and failure cleanup.
- Linked-worktree requirement, package import confinement, DB/log/lock/.env symlink confinement, complete-operation InstanceLock.
- Settings bootstrap writes suppressed; migration-only settings/backend verified.
- Backwards-only core rollback plan validated before execution, 0146 prerequisite enforced.
- Historical 0145 table inventory and applied state verified; nullable compute_version checked and reset to NULL.
- Flushed snapshot/rollback phase logs and timings; exact explicit startup command and log path printed.

## Validation

Command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run python .superpowers/test-replay-empty-rebuild.py
```

Final output:

```text
PASS: committed WAL snapshot; source byte invariance; 0.035s
PASS: direct and symlink path confinement
PASS: running instance lock refusal
PASS: incomplete snapshot and sidecars removed
Validated rollback plan: [('core', '0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index', True), ('core', '0147_session_history_fact', True), ('core', '0146_agent_runs', True)]
Reverse 0146 rebuilds existing tables and retains a global foreign_key_check. This phase can be slow.
Verified core 0145 schema and applied state; all Session.compute_version values are NULL.
PASS: actual latest historical migrations -> 0145; NULL reset; repeat refusal; 3.775s
PASS: cross-app, forward, and <=0145 plans refused before migration
7 validation groups PASS; all databases disposable.
```

All real SQLite connections point to a TemporaryDirectory. Source WAL remains open during snapshot to prove committed WAL inclusion.
The initial byte comparison includes SHM and fails because SQLite updates read marks. The final invariant compares database/WAL bytes.
Fixture setup initially lacks a logs directory and Project FK. Both fixtures are corrected before the final pass.
No TDD requirement applies to this temporary task. No real main/worktree database opens or server startup occurs.

## Self-review and limitations

- Reverse146 remains globally validated because it rebuilds existing tables; script warning and docs state this explicitly.
- Main remains the source. Snapshot replacement discards the prior disposable destination; rollback failure can leave partial migration state.
- Use --copy-main again before retrying after rollback failure.
- No stepped backup, raw copy, extra archive, source checkpoint, artifacts/scratch traversal, or server startup.
- CLI orchestration guards are inspected; helper operations and real historical rollback receive disposable harness coverage.
- uv prints the expected inherited-environment mismatch warning; it does not target the main environment.
- The root untracked plan remains preserved.

## User command

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run python .superpowers/replay-empty-rebuild-upgrade.py --copy-main
```

## Fix round 1: settings selection and preflight order

Review finding: package import loads `.env`, which can overwrite an earlier `DJANGO_SETTINGS_MODULE` selection.
The previous orchestration also checks the configured backend after snapshot replacement.

Changes:

- Select `twicc.settings_migration` after package import and `.env` loading.
- Load Django settings under bootstrap-write suppression while holding InstanceLock.
- Verify backend, resolved Django database, and configured logging paths before snapshot replacement.
- Keep ordinary application `.env` precedence unchanged.
- Add fresh-process main() coverage with `.env` containing `DJANGO_SETTINGS_MODULE=twicc.settings`.
- Copy actual package files into disposable test roots; supply synthetic linked-worktree git metadata.
- Execute a real small SQLite snapshot after settings validation.
- Inject an incorrect backend during preflight and assert snapshot is never called and destination bytes are unchanged.
- The CLI success fixture substitutes rollback with a content assertion. The existing historical test still executes actual latest → 0145 rollback.

Exact command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run python .superpowers/test-replay-empty-rebuild.py
```

Output (exit 0; inherited VIRTUAL_ENV mismatch warning remains expected):

```text
PASS: committed WAL snapshot; source byte invariance; 0.014s
PASS: direct and symlink path confinement
PASS: running instance lock refusal
PASS: incomplete snapshot and sidecars removed
Validated rollback plan: [('core', '0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index', True), ('core', '0147_session_history_fact', True), ('core', '0146_agent_runs', True)]
Reverse 0146 rebuilds existing tables and retains a global foreign_key_check. This phase can be slow.
Verified core 0145 schema and applied state; all Session.compute_version values are NULL.
PASS: actual latest historical migrations -> 0145; NULL reset; repeat refusal; 3.456s
PASS: cross-app, forward, and <=0145 plans refused before migration
7 validation groups PASS; all databases disposable.
Locked disposable worktree: /tmp/twicc-replay-cli-mnu4rly5
Snapshot starts: /tmp/twicc-replay-cli-mnu4rly5/source.sqlite -> /tmp/twicc-replay-cli-mnu4rly5/db/data.sqlite; pages=-1
Snapshot ends: 0.014s
Rollback starts: core 0145
Rollback ends: 0.000s
Prepared. Start explicitly: cd /tmp/twicc-replay-cli-mnu4rly5 && uv run ./devctl.py start all
Migration log: /tmp/twicc-replay-cli-mnu4rly5/logs/backend.log
PASS: CLI .env runtime setting overridden after load; real snapshot uses migration settings
Locked disposable worktree: /tmp/twicc-replay-cli-7aveotly
PASS: CLI preflight backend refusal preserves destination bytes
9 validation groups PASS; CLI bootstrap and refusal covered with disposable databases.
```

Self-review confirms all destination replacement follows backend and resolved-path validation.
The script and harness remain ignored. No real databases or servers are used.


## task-2-review.md

### Spec Compliance

- ❌ Issues found: `.superpowers/replay-empty-rebuild-upgrade.py:94–126` does not reliably select `twicc.settings_migration` after `.env` loading.
- All other reviewed requirements have implementation evidence. Snapshot uses read-only online backup and `pages=-1` at lines 31–41. Path checks precede InstanceLock and snapshot at lines 95–115. Rollback validates its plan and verifies state at lines 49–80. Startup remains explicit at lines 141–142.

### Strengths

- `.superpowers/replay-empty-rebuild-upgrade.py:31–45`: explicit connection closure and failed-snapshot cleanup protect the disposable destination.
- `.superpowers/replay-empty-rebuild-upgrade.py:49–80`: actual migration state, backwards-only plan, table inventory, and nullable compute_version receive checks.
- `.superpowers/test-replay-empty-rebuild.py`: disposable validation covers committed WAL data, source database/WAL byte invariance, lock refusal, cleanup, and historical rollback.
- `docs/plans/2026-10-01-sqlite-empty-rebuild-replay.md:38–54`: reverse146 cost and partial rollback state receive clear documentation.

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- `.superpowers/replay-empty-rebuild-upgrade.py:94–126`: `DJANGO_SETTINGS_MODULE` is set before `import twicc`. That import loads the worktree `.env` and can overwrite it. For example, `DJANGO_SETTINGS_MODULE=twicc.settings` selects the runtime backend. The script then replaces the copied database before rejecting that backend. This violates the required settings selection and makes a refused command destructive to the previous destination. Set the migration settings after package import and `.env` loading. Validate Django settings and resolved paths before snapshot replacement. Add a disposable CLI orchestration test with this `.env` value; current helper tests never execute `main()`.

#### Minor (Nice to Have)

- None.

### Assessment

**Task quality:** Needs fixes.

**Reasoning:** Snapshot and rollback helpers satisfy their safety contracts. CLI bootstrap allows `.env` to change settings before destination replacement.

### Focused checks

- Named risk: settings import and logging can mutate paths before validation. Checked `src/twicc/settings.py:155–161,354–380` and `src/twicc/settings_migration.py`. Bootstrap writes are patched; the FileHandler delays file opening.
- Named risk: package import can override settings selection. Checked `src/twicc/__init__.py:1`, `src/twicc/cli/__init__.py:10–39`, and `src/twicc/paths.py:76–110`. These establish the `.env` overwrite path above.
- Named risk: InstanceLock can mutate unvalidated paths. Checked `src/twicc/instance_lock.py:111–151`. Both lock and info paths receive script confinement checks before acquisition.
- Read the supplied diff once. No passing harness rerun. No real database connection, snapshot, rollback, or server startup.


## task-2-rereview.md

### Finding Verdicts

- **Important: .env overwrites settings selection; destination replacement precedes backend validation** — **ADDRESSED**. `.superpowers/replay-empty-rebuild-upgrade.py:102` selects migration settings after package import. Lines 115–124 validate Django backend, database path, and logging paths before snapshot replacement at line 129.
- **Missing main() regression coverage** — **ADDRESSED**. `.superpowers/test-replay-empty-rebuild.py:110–186` adds fresh-process main() execution. The success fixture loads a real `.env` override and performs a real disposable snapshot. The refusal fixture asserts no snapshot call and unchanged destination bytes.

### New Breakage in the Fix Diff

- None.

### Out-of-Scope Observations

- None.

### Verdict

**Fix round:** All findings addressed, no new Critical/Important breakage.

### Checks

- Read the supplied fix diff once. Checked settings selection order, lock scope, preflight order, and both CLI fixture paths.
- Appended report names the harness command and shows nine passing groups, including both new CLI cases.
- No harness rerun, real database operation, or server operation.


## final-review.md

# Final independent review

**Ready to merge: Yes.** No open Critical or Important findings.

## Scope and evidence

- Reviewed committed range `8fc7e186..f5fc0178` and the supplied review package.
- Reviewed both ignored replay files, the requirements, task reports, previous reviews, and the ledger.
- Read the complete modified schema editor and its observer, metadata reader, and guarded connection.
- Read installed Django's rebuild implementation, InstanceLock, and migration logging integration.
- Checked the actual integration assertions and safety tests against their reported results.
- `full.log` records **347 passing tests**. The replay report records **nine passing disposable validation groups**.
- `git diff --check 8fc7e186..f5fc0178` passes. The checkout has no tracked modifications.
- No passing suite was repeated. No concrete uncovered risk required an additional disposable probe.
- No database or server was opened or operated during this review.
- This report is the only review artifact written. Product files, index, HEAD, and branch state remain unchanged.

## Strengths

- `schema.py:219` restricts the proof to atomic rebuilds of tables created within the current editor.
- Initial-schema and generated-name checks reject existing tables and temporary-name collisions before emptiness probes.
- `schema.py:241` combines unchanged `total_changes`, final emptiness, temporary-table removal, and exactly one proved rename.
- `schema.py:390` stages individual zero-row INSERT effects. It does not clear the whole rebuild's effects.
- `schema.py:407` checks exact rename provenance, mechanical authorizations, and unchanged metadata for every other table.
- Existing references to the temporary name change dependency metadata and retain global fallback.
- `schema.py:264` follows only intact standard deferred index statements through Django's rename.
- Standard conditional UniqueConstraint provenance addresses actual migration 0146 without accepting custom constraint subclasses.
- `test_sqlite_migration_integration.py:381` uses historical models and an actual populated 0145-to-0146 upgrade.
- Its assertions require three real rebuild renames, no FK queries, preserved SessionItem rows, and recorded migration completion.
- Frozen migrations, runtime backend selection, dependencies, and released changelog sections remain unchanged.

## Adversarial boundary checks

### SQL effects and transactions

- Independent data writes remain in the observer. Changed row counts also reject the entire empty rebuild proof.
- Field callbacks, trigger writes, unknown PRAGMAs, and unrelated schema mutations receive real SQLite regression coverage.
- Retained source DELETE effects preserve incoming-reference selection.
- The editor also selects existing children that reference newly created parents.
- Invalid incoming and populated-child references fail before atomic completion and roll back the schema changes.
- The guarded connection checks transaction ownership around execution and result fetching.
- Deferred SQL executes while observation and rollback remain available.
- Validation runs after effect collection and before `_finish()` commits the atomic transaction.
- Metadata equality is not used alone: exact rename SQL and the authorization whitelist remain mandatory.

### Replay preparation

- The source opens read-only. Online backup uses `pages=-1` and explicit connection closure.
- Destination replacement follows linked-worktree, imported-package, path, backend, and resolved-database checks.
- Existing symlink targets for database files, sidecars, log paths, and lock paths receive confinement checks.
- InstanceLock covers settings preflight, snapshot replacement, rollback, and verification.
- The settings fix selects migration settings after `.env` loading and validates them before destination replacement.
- Fresh-process CLI coverage checks that order and verifies destination preservation on backend refusal.
- The rollback plan permits only backwards core migrations above 0145 and requires applied migration 0146.
- Post-rollback checks verify the table inventory, migration state, nullable compute_version, and its NULL reset.
- Snapshot failure removes incomplete destination files. Rollback failure and retry requirements are documented.
- The script does not start servers or traverse artifact and scratch targets.

## Issues

### Critical

None.

### Important

None. The earlier settings-selection finding is addressed in the final script and CLI regression coverage.

### Minor

- Existing deferred observation: `full.log:1` contains the expected inherited `VIRTUAL_ENV` mismatch warning.
- uv ignores that environment and selects the worktree environment. This is not a product defect or validation failure.
- No new Minor finding.

## Declined to judge

- Full-database snapshot duration: real-data operations are reserved for the user; disposable timings cannot establish production duration.
- Reverse146 scan optimization: the requirements explicitly retain global validation for existing-table rebuilds; documentation states this limit.
- Per-migration identity and timing during script rollback: direct MigrationExecutor omits the management-command logging context; required phase timing remains present.
- The rollback diagnostic limit does not affect subsequent startup: devctl uses the management command with migration identity and timing.
- Historical broad-suite failures: the requirements exclude them; the focused migration suite covers the changed subsystem.
- Generic hostile filesystem replacement races: this temporary local script uses the existing instance-lock and symlink-confinement contract, not adversarial filesystem isolation.

## Recommendations

No required correction. Keep the stated reverse-migration and rollback-diagnostic limits visible when handing the script to the user.

## Assessment

**Ready to merge: Yes.** The proof preserves required validation and meets the actual forward migration acceptance test.
The replay preparation satisfies the requested confinement and snapshot contract, with explicit operational limits.


## Complete focused test output

```text
warning: `VIRTUAL_ENV=/home/twidi/dev/twicc-poc/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
============================= test session starts ==============================
platform linux -- Python 3.13.14, pytest-9.0.3, pluggy-1.6.0
django: version: 6.0.4, settings: twicc.settings_test (from option)
rootdir: /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds
configfile: pyproject.toml
plugins: anyio-4.13.0, django-4.12.0
collected 347 items

tests/test_migration_process.py ..............                           [  4%]
tests/test_sqlite_migration_effects.py ................................. [ 13%]
........................................................................ [ 34%]
.......                                                                  [ 36%]
tests/test_sqlite_migration_metadata.py ..........                       [ 39%]
tests/test_sqlite_migration_schema.py .................................. [ 48%]
........................................................................ [ 69%]
........................................................................ [ 90%]
...............                                                          [ 94%]
tests/test_sqlite_migration_integration.py ..................            [100%]

======================== 347 passed in 84.76s (0:01:24) ========================

```
