"""Maintenance obtains SQLite write ownership before authoritative reads."""

import sqlite3

import pytest
from django.db import OperationalError, connection, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.transaction import TransactionManagementError
from django.test.utils import CaptureQueriesContext

from tests.test_content_maintenance import cold
from twicc import content_maintenance_task as maintenance
from twicc.core.models import SessionItem

pytestmark = pytest.mark.django_db(transaction=True)


def prepared_content(content_row_factory):
    owner = cold('owner')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row, original_content=row)
    _, entries, _ = maintenance.prepare_compression_batch(0)
    assert len(entries) == 1
    return row, entries


def test_publication_begins_immediate_without_primary_key_update_or_fk_scan(content_row_factory):
    row, entries = prepared_content(content_row_factory)
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA foreign_keys')
        assert cursor.fetchone() == (1,)
    previous_mode = connection.transaction_mode
    with CaptureQueriesContext(connection) as queries:
        assert maintenance._publish(entries, frozenset()) == 1
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
def test_publication_commits_callbacks_only_on_success(content_row_factory, monkeypatch, fail):
    row, entries = prepared_content(content_row_factory)
    previous_mode = connection.transaction_mode
    committed = []
    publish = maintenance.publish_prepared_batch

    def publish_then_finish(*args):
        count = publish(*args)
        transaction.on_commit(lambda: committed.append('committed'))
        if fail:
            raise ValueError('publication failed')
        return count

    monkeypatch.setattr(maintenance, 'publish_prepared_batch', publish_then_finish)
    if fail:
        with pytest.raises(ValueError, match='publication failed'):
            maintenance._publish(entries, frozenset())
    else:
        assert maintenance._publish(entries, frozenset()) == 1
    assert connection.transaction_mode == previous_mode
    assert connection.get_autocommit()
    assert committed == ([] if fail else ['committed'])
    row.refresh_from_db()
    assert row.codec == ('plain' if fail else 'zstd')


def test_empty_publication_does_not_open_transaction():
    with CaptureQueriesContext(connection) as queries:
        assert maintenance._publish((), frozenset()) == 0
    assert list(queries) == []


@pytest.mark.parametrize('outer', ['atomic', 'manual'])
def test_publication_rejects_outer_transaction_before_validation(content_row_factory, outer):
    _, entries = prepared_content(content_row_factory)
    previous_mode = connection.transaction_mode
    if outer == 'atomic':
        with (transaction.atomic(), CaptureQueriesContext(connection) as queries,
              pytest.raises(TransactionManagementError)):
            maintenance._publish(entries, frozenset())
    else:
        connection.set_autocommit(False)
        try:
            with CaptureQueriesContext(connection) as queries, pytest.raises(TransactionManagementError):
                maintenance._publish(entries, frozenset())
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
