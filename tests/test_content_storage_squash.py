"""Verify consolidated storage upgrades and Django replacement bookkeeping."""

import hashlib
import importlib
import shutil

import pytest
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from tests.test_sqlite_migration_integration import (
    STORAGE_SQUASH,
    check_sql,
    default_alias_database,
    index_names,
    original_storage_migrations,
    populate_items,
    trace,
)


PREVIOUS = ('core', '0155_session_hidden_partial_index')
SQUASH = importlib.import_module(f'twicc.core.migrations.{STORAGE_SQUASH[1]}').Migration
ORIGINALS = SQUASH.replaces


@pytest.fixture(scope='module')
def storage_predecessor(tmp_path_factory, django_db_blocker):
    path = tmp_path_factory.mktemp('storage_predecessor') / 'template.sqlite3'
    with default_alias_database(path, django_db_blocker) as db:
        executor = MigrationExecutor(db)
        executor.migrate([PREVIOUS])
        populate_items(db, executor.loader.project_state([PREVIOUS]))
    return path


def test_squash_has_final_models_and_index_at_table_creation():
    from django.db import migrations
    from django.db.migrations.loader import MigrationLoader

    replacement = SQUASH('storage', 'core')
    created = {operation.name: operation for operation in replacement.operations
               if isinstance(operation, migrations.CreateModel)}
    assert set(created) == {'ContentDictionary', 'SessionItemContent', 'StatisticsQueueEntry'}
    assert 'force_cost_session_ids' in dict(created['StatisticsQueueEntry'].fields)
    assert [index.name for index in created['SessionItemContent'].options['indexes']] == ['idx_content_plain_id']
    assert not any(isinstance(operation, migrations.DeleteModel) for operation in replacement.operations)
    assert not any(isinstance(operation, migrations.AddField)
                   and operation.model_name == 'statisticsqueueentry' for operation in replacement.operations)
    loader = MigrationLoader(None)
    state = loader.project_state([PREVIOUS])
    expected = state.clone()
    for key in ORIGINALS:
        expected = loader.disk_migrations[key].mutate_state(expected)
    assert replacement.mutate_state(state).models == expected.models


@pytest.mark.parametrize('applied_count', [0, 7], ids=['main-upgrade', 'original-complete'])
def test_storage_replacement_upgrade_preserves_data_and_history(
    tmp_path, django_db_blocker, storage_predecessor, applied_count,
):
    path = tmp_path / 'upgrade.sqlite3'
    shutil.copyfile(storage_predecessor, path)
    with default_alias_database(path, django_db_blocker) as db:
        if applied_count:
            with original_storage_migrations():
                MigrationExecutor(db).migrate([ORIGINALS[applied_count - 1]])
        before = db.connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE name IN ('core_session', 'core_sessionitem') ORDER BY name"
        ).fetchall()
        executor = MigrationExecutor(db)
        # The squash itself, not the leaf: later migrations (e.g. the timeline's) are not under test here.
        targets = [STORAGE_SQUASH]
        plan = executor.migration_plan(targets)
        if applied_count == 0:
            assert [(migration.name, backwards) for migration, backwards in plan] == [(STORAGE_SQUASH[1], False)]
        else:
            assert plan == []
        with trace(db) as statements:
            executor.migrate(targets)
        assert STORAGE_SQUASH in MigrationRecorder(db).applied_migrations()
        assert set(ORIGINALS) <= MigrationRecorder(db).applied_migrations().keys()
        assert db.connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE name IN ('core_session', 'core_sessionitem') ORDER BY name"
        ).fetchall() == before
        assert db.connection.execute('SELECT content FROM core_sessionitem ORDER BY line_num').fetchall() == [
            ('{"text":"historical"}',)
        ] * 10
        assert db.connection.execute('SELECT COUNT(*) FROM core_sessionitemcontent').fetchone() == (0,)
        assert 'core_computeaggregaterepair' not in db.introspection.table_names()
        assert 'idx_content_plain_id' in index_names(db, 'core_sessionitemcontent')
        assert 'idx_session_compute_root' in index_names(db, 'core_session')
        assert 'force_cost_session_ids' in {
            column.name for column in db.introspection.get_table_description(db.cursor(), 'core_statisticsqueueentry')
        }
        dictionaries = db.connection.execute(
            'SELECT provider, version, sha256, payload FROM core_contentdictionary ORDER BY provider'
        ).fetchall()
        assert [row[:2] for row in dictionaries] == [('claude_code', 1), ('codex', 1)]
        assert all(hashlib.sha256(row[3]).digest() == row[2] for row in dictionaries)
        if applied_count == 0:
            assert check_sql(statements) == ['PRAGMA foreign_key_check("core_sessionitemcontent")']
            assert not any(sql.startswith(('CREATE TABLE \"new__core_', 'INSERT INTO \"new__core_')) for sql in statements), [sql for sql in statements if 'new__core_' in sql]
            assert not any('computeaggregaterepair' in sql for sql in statements)
        assert db.connection.execute('PRAGMA foreign_key_check').fetchall() == []
