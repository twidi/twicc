import asyncio

import pytest
from django.db import connection, transaction

from twicc.core.models import StatisticsQueueEntry
from twicc.providers.statistics_queue import (
    acknowledge_queue,
    append_roots,
    capture_queue,
    install_queue_wakeup,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_append_rolls_back_with_source():
    async def scenario():
        wakeups = []
        install_queue_wakeup(lambda: wakeups.append(True))
        try:
            with pytest.raises(ValueError), transaction.atomic():
                append_roots(['root'])
                raise ValueError('rollback')
            await asyncio.sleep(0)
            assert StatisticsQueueEntry.objects.count() == 0
            assert wakeups == []
            with transaction.atomic():
                append_roots(['root'])
                assert wakeups == []
            await asyncio.sleep(0)
            assert wakeups == [True]
        finally:
            install_queue_wakeup(None)

    # This test intentionally exercises the synchronous source transaction.
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {'DJANGO_ALLOW_ASYNC_UNSAFE': 'true'}):
        asyncio.run(scenario())


def test_repeated_root_entries_survive():
    with transaction.atomic():
        append_roots(['root', 'root'])
        append_roots(['root'])
    assert StatisticsQueueEntry.objects.count() == 2
    batch = capture_queue()
    assert batch.root_ids == ('root',)


def test_acknowledge_preserves_later_same_root():
    with transaction.atomic():
        append_roots(['root'])
    batch = capture_queue()
    with transaction.atomic():
        append_roots(['root'])
        acknowledge_queue(batch.max_id)
    assert StatisticsQueueEntry.objects.count() == 1
    assert capture_queue().max_id > batch.max_id


def test_ids_above_32_bits_are_not_reused():
    with transaction.atomic():
        append_roots(['root'])
        with connection.cursor() as cursor:
            cursor.execute('UPDATE sqlite_sequence SET seq = %s WHERE name = %s',
                           [2**31, StatisticsQueueEntry._meta.db_table])
        append_roots(['root'])
    previous = capture_queue().max_id
    assert previous > 2**31
    with transaction.atomic():
        acknowledge_queue(previous)
        append_roots(['root'])
    assert capture_queue().max_id > previous


def test_queue_requires_source_transaction():
    with pytest.raises(RuntimeError):
        append_roots(['root'])
    with pytest.raises(RuntimeError):
        acknowledge_queue(1)
    assert capture_queue() is None


def test_large_append_stays_in_source_transaction():
    with transaction.atomic():
        append_roots(str(index) for index in range(2000))
    assert len(capture_queue().root_ids) == 2000


def test_wakeup_runs_on_owning_loop_after_thread_commit():
    import threading

    async def scenario():
        notified = asyncio.Event()
        callback_threads = []

        def callback():
            callback_threads.append(threading.get_ident())
            notified.set()

        def source_write():
            try:
                with transaction.atomic():
                    append_roots(['thread-root'])
            finally:
                connection.close()

        install_queue_wakeup(callback)
        try:
            await asyncio.to_thread(source_write)
            await asyncio.wait_for(notified.wait(), 2)
            assert callback_threads == [threading.get_ident()]
        finally:
            install_queue_wakeup(None)

    asyncio.run(scenario())


def test_cache_marker_schema_is_nullable_add_column():
    from twicc.core.models import Session

    field = Session._meta.get_field('items_cost_computed_through_at')
    with connection.schema_editor(collect_sql=True) as editor:
        editor.add_field(Session, field)
    assert editor.collected_sql == [
        'ALTER TABLE "core_session" ADD COLUMN "items_cost_computed_through_at" datetime NULL;'
    ]
    with connection.cursor() as cursor:
        cursor.execute('SELECT sql FROM sqlite_master WHERE name = %s', [StatisticsQueueEntry._meta.db_table])
        sql = cursor.fetchone()[0]
    assert 'integer NOT NULL PRIMARY KEY AUTOINCREMENT' in sql
    assert 'UNIQUE' not in sql


def test_activation_schema_sql_drops_legacy_journal_without_rebuilding_sessions():
    from io import StringIO
    from django.core.management import call_command
    output = StringIO()
    call_command('sqlmigrate', 'core', '0158', stdout=output)
    cache_sql = output.getvalue()
    assert 'ALTER TABLE "core_session" ADD COLUMN "items_cost_computed_through_at" datetime NULL' in cache_sql
    assert 'new__core_session' not in cache_sql
    output = StringIO()
    call_command('sqlmigrate', 'core', '0159', stdout=output)
    assert 'DROP TABLE "core_computeaggregaterepair"' in output.getvalue()
    assert 'core_session' not in output.getvalue()
