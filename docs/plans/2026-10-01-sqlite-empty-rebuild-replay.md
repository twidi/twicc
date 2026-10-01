# Disposable worktree upgrade replay

The temporary script prepares this linked worktree for a normal startup upgrade from core migration 0145.
The script remains ignored at `.superpowers/replay-empty-rebuild-upgrade.py`.
It is not part of the product or a committed maintenance tool.

## Commands

Run from the linked worktree after its backend stops:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run python .superpowers/replay-empty-rebuild-upgrade.py --copy-main
```

`--copy-main` replaces only the worktree database and its SQLite sidecars.
The default source is `~/.twicc/db/data.sqlite`.
Use `--source /absolute/path/data.sqlite` for a disposable source.
Omit `--copy-main` to prepare an existing copied database.
Migration 0146 must already be applied.

The script acquires the worktree instance lock for the operation.
It refuses a running backend, paths outside the worktree, and external symlink targets.
It verifies the imported package path and selects `twicc.settings_migration` after the worktree `.env` loads.
It validates Django database settings and logging paths before snapshot replacement.
This temporary command overrides `.env` settings selection; ordinary application `.env` precedence stays unchanged.
It rejects `TWICC_SQLITE_STANDARD_MIGRATIONS=1`.

The snapshot uses a read-only source connection and `source.backup(target, pages=-1)`.
It includes committed WAL transactions without copying sidecars or issuing a source checkpoint.
SQLite can update shared-memory read marks during this read.
The script keeps no separate database archive.
An interrupted or failed snapshot removes its incomplete destination files.

## Rollback and startup

The script validates the rollback plan before migration execution.
It accepts only backwards core migrations above 0145.
It verifies the resulting core table inventory and applied migration state.
It verifies nullable `Session.compute_version`, resets it to NULL, and checks the reset.
Settings bootstrap directory migration and secret-key creation are suppressed during this temporary command.
The script does not touch artifact or scratch targets.

**Reverse migration 0146 still runs a global foreign key check.**
Django rebuilds existing tables while removing constraints and fields before dropping the new tables.
This rollback can remain slow on the full snapshot.
The optimization applies to the subsequent forward 0145 → 0146 upgrade.
Django retains its standard rebuilds in both directions.

After successful preparation, start explicitly:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && uv run ./devctl.py start all
```

Migration timings and foreign key check scopes appear in:
`/home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds/logs/backend.log`.
Normal startup reapplies pending migrations and reconstructs metadata and facts.
Rollback failure can leave an intermediate migration state; use a fresh `--copy-main` snapshot before retrying.

## Disposable validation

The ignored harness is `.superpowers/test-replay-empty-rebuild.py`.
Run it from the linked worktree:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-empty-rebuilds && TWICC_DATA_DIR=$PWD uv run python .superpowers/test-replay-empty-rebuild.py
```

Nine validation groups pass:

- Committed WAL snapshot and unchanged source database/WAL bytes.
- Direct and symlink path confinement.
- Instance lock refusal.
- Incomplete snapshot and sidecar cleanup.
- Actual latest historical migrations through rollback to 0145, with a populated Session.
- NULL compute-version reset and repeated rollback refusal.
- Cross-app, forward, and older migration plan refusal before execution.
- Fresh-process CLI execution with `.env` selecting runtime settings, followed by a real disposable snapshot.
- CLI backend preflight refusal with unchanged destination database bytes.

The CLI fixtures provide synthetic git metadata and copy package files into each confined temporary root.
The CLI success fixture replaces rollback with a snapshot-content check; the historical test executes the real rollback.

The small snapshot takes 0.014 seconds.
The complete disposable historical migration and rollback test takes 3.456 seconds.
These timings do not predict the full snapshot cost.
The inherited virtual-environment warning is expected; `uv run` selects this worktree's environment.
No real database snapshot, real rollback, or server startup occurs during validation.
