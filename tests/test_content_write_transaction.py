"""Maintenance obtains SQLite write ownership before authoritative reads."""

import sqlite3

import pytest
from django.db import OperationalError, connection, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.transaction import TransactionManagementError
from django.test.utils import CaptureQueriesContext

from tests import test_content_maintenance
from tests.test_content_maintenance import cold
from twicc import content_maintenance_task as maintenance
from twicc.core.models import SessionItem

session_cache = test_content_maintenance.session_cache

pytestmark = pytest.mark.django_db(transaction=True)


def prepared_content(content_row_factory, session_cache):
    owner = cold('owner')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row, original_content=row)
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    entries = page.entries
    assert len(entries) == 1
    return row, entries


def test_publication_begins_immediate_without_primary_key_update_or_fk_scan(content_row_factory, session_cache):
    row, entries = prepared_content(content_row_factory, session_cache)
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA foreign_keys')
        assert cursor.fetchone() == (1,)
    previous_mode = connection.transaction_mode
    with CaptureQueriesContext(connection) as queries:
        assert maintenance._publish(entries, frozenset(), session_cache=session_cache).changed == 1
    sql = [query['sql'] for query in queries]
    assert sql[0] == 'BEGIN IMMEDIATE'
    assert not any('SET "id" = "id"' in statement for statement in sql)
    # BEGIN has no VM table access. The former UPDATE caused FK scans of SessionItem.
    with connection.cursor() as cursor:
        cursor.execute('EXPLAIN QUERY PLAN ' + sql[0])
        assert cursor.fetchall() == []
    assert connection.transaction_mode == previous_mode
    row.refresh_from_db()
    assert row.codec == 'zstd'


@pytest.mark.parametrize('fail', [False, True])
def test_publication_commits_callbacks_only_on_success(content_row_factory, monkeypatch, fail, session_cache):
    row, entries = prepared_content(content_row_factory, session_cache)
    previous_mode = connection.transaction_mode
    committed = []
    publish = maintenance.publish_prepared_batch

    def publish_then_finish(*args, **kwargs):
        count = publish(*args, **kwargs)
        transaction.on_commit(lambda: committed.append('committed'))
        if fail:
            raise ValueError('publication failed')
        return count

    monkeypatch.setattr(maintenance, 'publish_prepared_batch', publish_then_finish)
    if fail:
        with pytest.raises(ValueError, match='publication failed'):
            maintenance._publish(entries, frozenset(), session_cache=session_cache)
    else:
        assert maintenance._publish(entries, frozenset(), session_cache=session_cache).changed == 1
    assert connection.transaction_mode == previous_mode
    assert connection.get_autocommit()
    assert committed == ([] if fail else ['committed'])
    row.refresh_from_db()
    assert row.codec == ('plain' if fail else 'zstd')


def test_empty_publication_does_not_open_transaction(session_cache):
    with CaptureQueriesContext(connection) as queries:
        assert maintenance._publish((), frozenset(), session_cache=session_cache).changed == 0
    assert list(queries) == []


@pytest.mark.parametrize('outer', ['atomic', 'manual'])
def test_publication_rejects_outer_transaction_before_validation(content_row_factory, outer, session_cache):
    _, entries = prepared_content(content_row_factory, session_cache)
    previous_mode = connection.transaction_mode
    if outer == 'atomic':
        with (transaction.atomic(), CaptureQueriesContext(connection) as queries,
              pytest.raises(TransactionManagementError)):
            maintenance._publish(entries, frozenset(), session_cache=session_cache)
    else:
        connection.set_autocommit(False)
        try:
            with CaptureQueriesContext(connection) as queries, pytest.raises(TransactionManagementError):
                maintenance._publish(entries, frozenset(), session_cache=session_cache)
        finally:
            connection.rollback()
            connection.set_autocommit(True)
    assert list(queries) == []
    assert connection.transaction_mode == previous_mode


def test_begin_failure_restores_connection_mode():
    from twicc.core.content_storage.transactions import immediate_content_transaction

    previous_mode = connection.transaction_mode

    def reject_begin(execute, sql, params, many, context):
        if sql == 'BEGIN IMMEDIATE':
            raise OperationalError('begin failed')
        return execute(sql, params, many, context)

    with (connection.execute_wrapper(reject_begin), pytest.raises(OperationalError, match='begin failed'),
          immediate_content_transaction()):
        pytest.fail('Failed BEGIN must not enter the transaction')
    assert connection.transaction_mode == previous_mode
    assert connection.get_autocommit()


def test_immediate_transaction_allows_readers_and_blocks_competing_writer(tmp_path, monkeypatch):
    from twicc.core.content_storage.transactions import immediate_content_transaction

    config = {**connection.settings_dict, 'NAME': str(tmp_path / 'publication.sqlite3')}
    disk = DatabaseWrapper(config, alias='default')
    disk.ensure_connection()
    reader = sqlite3.connect(config['NAME'], timeout=0, isolation_level=None)
    try:
        with disk.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('CREATE TABLE publication (value INTEGER)')
            cursor.execute('INSERT INTO publication VALUES (1)')
        monkeypatch.setattr(connections._connections, 'default', disk)
        previous_mode = disk.transaction_mode
        with immediate_content_transaction():
            # The write reservation exists before the first publication read or update.
            assert reader.execute('SELECT value FROM publication').fetchall() == [(1,)]
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                reader.execute('UPDATE publication SET value = 2')
            with connection.cursor() as cursor:
                cursor.execute('UPDATE publication SET value = 3')
            assert reader.execute('SELECT value FROM publication').fetchall() == [(1,)]
        assert disk.transaction_mode == previous_mode
        assert reader.execute('SELECT value FROM publication').fetchall() == [(3,)]
        reader.execute('UPDATE publication SET value = 4')
    finally:
        reader.close()
        disk.close()


@pytest.mark.parametrize('fail', [False, True])
def test_restoration_reserves_disk_writer_before_authorization_reads(
    content_row_factory, tmp_path, monkeypatch, fail,
):
    from tests.test_content_promotion import START, item, snapshot
    from tests.test_content_root_state import session

    owner = session('hot', storage_incarnation='hot-inc', content_storage_version=1)
    row = content_row_factory('restore ' * 100, True, session_ids=[owner.id])
    item(owner, row)
    batch = maintenance.prepare_promotion_batch(snapshot(owner), START)
    source = connections['default']
    path = tmp_path / 'restore.sqlite3'
    with sqlite3.connect(path) as target:
        source.connection.backup(target)
        target.execute('PRAGMA journal_mode=WAL')
    disk = source.copy()
    disk.settings_dict['NAME'] = str(path)
    connections['default'] = disk
    competitor = sqlite3.connect(path, timeout=0, isolation_level=None)
    original = maintenance._snapshot_matches
    authorization = []
    callbacks = []

    def authorize(observation):
        # Authorization runs only after the IMMEDIATE reservation succeeds.
        assert competitor.execute('SELECT codec FROM core_sessionitemcontent WHERE id = ?', [row.id]).fetchone() == ('zstd',)
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            competitor.execute('UPDATE core_session SET archived = archived WHERE id = ?', [owner.id])
        authorization.append(True)
        transaction.on_commit(lambda: callbacks.append(True))
        return original(observation)

    monkeypatch.setattr(maintenance, '_snapshot_matches', authorize)
    disk.ensure_connection()
    previous_mode = disk.transaction_mode

    def fail_after_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if fail and sql.startswith('UPDATE "core_sessionitemcontent"'):
            raise ValueError('restore rollback')
        return result

    try:
        with disk.execute_wrapper(fail_after_update), CaptureQueriesContext(disk) as queries:
            if fail:
                with pytest.raises(ValueError, match='restore rollback'):
                    maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True)
            else:
                assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True).done
        statements = [query['sql'] for query in queries]
        assert statements[0] == 'BEGIN IMMEDIATE'
        assert all('"payload"' not in sql for sql in statements if sql.startswith('SELECT'))
        assert authorization == [True]
        assert callbacks == ([] if fail else [True])
        assert disk.transaction_mode == previous_mode and disk.get_autocommit()
        expected = 'zstd' if fail else 'plain'
        assert competitor.execute('SELECT codec FROM core_sessionitemcontent WHERE id = ?', [row.id]).fetchone() == (expected,)
        competitor.execute('UPDATE core_session SET archived = archived WHERE id = ?', [owner.id])
    finally:
        competitor.close()
        disk.close()
        connections['default'] = source
