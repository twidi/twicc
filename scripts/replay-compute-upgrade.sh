#!/usr/bin/env bash
# Recreate the September 29 schema and compute-version upgrade on the main instance.
# Run only after migration 0149 is applied and the TwiCC backend is stopped.
# The script does not restart the backend.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
export TWICC_REPLAY_REPO_ROOT="$REPO_ROOT"
DATA_DIR="/home/twidi/.twicc"
export TWICC_DATA_DIR="$DATA_DIR"
cd "$REPO_ROOT"

status="$(uv run ./devctl.py status)"
if [[ "$status" == *"Backend (Django): running"* ]]; then
    echo "Stop the main TwiCC backend before replaying the upgrade." >&2
    exit 1
fi

database_path="$(uv run python - <<'PY'
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django
import twicc

django.setup()
from django.conf import settings

repo_root = Path(os.environ['TWICC_REPLAY_REPO_ROOT']).resolve()
assert Path(twicc.__file__).resolve().is_relative_to(repo_root / 'src')
print(settings.DATABASES['default']['NAME'])
PY
)"
expected_path="$DATA_DIR/db/data.sqlite"
if [[ "$database_path" != "$expected_path" ]]; then
    echo "Unexpected database path: $database_path" >&2
    exit 1
fi

uv run python - <<'PY'
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django

django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

plan = MigrationExecutor(connection).migration_plan([('core', '0146_agent_runs')])
names = [(migration.app_label, migration.name, backwards) for migration, backwards in plan]
expected = [
    ('core', '0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index', True),
    ('core', '0147_session_history_fact', True),
]
if names != expected:
    raise SystemExit(f'Unexpected rollback plan: {names}')
print(f'Rollback plan verified: {[name for _app, name, _backwards in names]}')
PY

backup_path="$DATA_DIR/db/data.sqlite.pre-compute-replay-$(date +%Y%m%dT%H%M%S)-$$"
export TWICC_REPLAY_BACKUP_PATH="$backup_path"
uv run python - <<'PY'
import os
import shutil
import sqlite3
from pathlib import Path

db_path = Path(os.environ['TWICC_DATA_DIR']) / 'db' / 'data.sqlite'
backup_path = Path(os.environ['TWICC_REPLAY_BACKUP_PATH'])
if not db_path.is_file() or backup_path.exists():
    raise SystemExit('Database is missing or backup path already exists')
free_bytes = shutil.disk_usage(backup_path.parent).free
if free_bytes < db_path.stat().st_size + 5 * 1024**3:
    raise SystemExit('Insufficient free space for a database backup')
with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as source:
    applied = {row[0] for row in source.execute(
        "SELECT name FROM django_migrations WHERE app = 'core' AND name IN "
        "('0147_session_history_fact', '0148_live_contribution_indexes', "
        "'0149_remove_redundant_message_index')"
    )}
    expected = {
        '0147_session_history_fact',
        '0148_live_contribution_indexes',
        '0149_remove_redundant_message_index',
    }
    if applied != expected:
        raise SystemExit(f'Apply migration 0149 before replaying the upgrade; found: {sorted(applied)}')
    item_index = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_item_message_line'"
    ).fetchone()
    if item_index:
        raise SystemExit('Migration 0149 is recorded, but the SessionItem index still exists')
    with sqlite3.connect(backup_path) as backup:
        source.backup(backup, pages=4096, sleep=0.1)
print(f'Backup created: {backup_path}')
PY

uv run python -m django migrate core 0146_agent_runs --settings=twicc.settings --noinput

uv run python - <<'PY'
import os
import sqlite3
from pathlib import Path

db_path = Path(os.environ['TWICC_DATA_DIR']) / 'db' / 'data.sqlite'
with sqlite3.connect(db_path) as database:
    database.execute('BEGIN IMMEDIATE')
    database.execute(
        "UPDATE core_session SET compute_version = CASE provider "
        "WHEN 'claude_code' THEN 110 WHEN 'codex' THEN 50 ELSE compute_version END "
        "WHERE provider IN ('claude_code', 'codex')"
    )
    applied = database.execute(
        "SELECT name FROM django_migrations WHERE app = 'core' AND name IN "
        "('0147_session_history_fact', '0148_live_contribution_indexes', "
        "'0149_remove_redundant_message_index', "
        "'0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index')"
    ).fetchall()
    facts_table = database.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'core_sessionhistoryfact'"
    ).fetchone()
    contribution_index = database.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_session_parent_cost'"
    ).fetchone()
    versions = database.execute(
        "SELECT provider, compute_version, COUNT(*) FROM core_session "
        "WHERE provider IN ('claude_code', 'codex') GROUP BY provider, compute_version"
    ).fetchall()
    if applied or facts_table or contribution_index or any(
        version != {'claude_code': 110, 'codex': 50}[provider]
        for provider, version, _count in versions
    ):
        raise SystemExit('Post-rollback schema or compute-version check failed')
print(f'Compute versions reset: {versions}')
print('The next backend start will reapply migration 0147 and the 0148-0149 squash, then recompute sessions.')
PY
