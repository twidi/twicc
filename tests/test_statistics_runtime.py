"""Deterministic consumer scheduling and owned reader lifecycle."""
import asyncio
import importlib
import threading

import pytest

from twicc.providers.statistics_queue import QueueBatch


def runtime():
    try:
        return importlib.import_module('twicc.providers.statistics_runtime')
    except ModuleNotFoundError:
        pytest.fail('StatisticsConsumer runtime is missing')


async def turns(count=30):
    for _ in range(count):
        await asyncio.sleep(0)


def test_startup_fixed_window_retry_and_idle(monkeypatch):
    module = runtime()

    async def scenario():
        rows = ['claude', 'codex']
        reads = []
        sleeps = []
        gates = asyncio.Queue()
        batches = []
        consumer = module.StatisticsConsumer()

        async def read(function, *args):
            reads.append(function)
            return QueueBatch(len(rows), tuple(rows)) if rows else None

        async def sleep(delay):
            sleeps.append(delay)
            gate = asyncio.Event()
            await gates.put(gate)
            await gate.wait()

        async def batch(value):
            batches.append(value)
            if len(batches) == 1:
                raise RuntimeError('transient')
            rows.clear()

        monkeypatch.setattr(consumer, '_read', read)
        monkeypatch.setattr(consumer, 'run_batch', batch)
        monkeypatch.setattr(module, 'sleep', sleep)
        consumer.start()
        gate = await gates.get()
        assert sleeps == [2.0]
        rows.append('claude')
        consumer.wake()
        await turns()
        assert sleeps == [2.0]
        gate.set()
        retry = await gates.get()
        assert sleeps == [2.0, 1.0]
        assert rows == ['claude', 'codex', 'claude']
        retry.set()
        collection = await gates.get()
        collection.set()
        await turns()
        assert batches == [QueueBatch(3, ('claude', 'codex', 'claude'))] * 2
        count = len(reads)
        await turns()
        assert len(reads) == count
        await consumer.close()

    asyncio.run(scenario())


def test_enqueue_during_empty_inspection_is_not_lost(monkeypatch):
    module = runtime()

    async def scenario():
        consumer = module.StatisticsConsumer()
        reads = 0
        done = asyncio.Event()

        async def read(function, *args):
            nonlocal reads
            reads += 1
            if reads == 1:
                consumer.wake()
                return None
            return QueueBatch(1, ('root',))

        async def batch(value):
            done.set()
            await asyncio.Event().wait()

        async def sleep(delay):
            pass

        monkeypatch.setattr(consumer, '_read', read)
        monkeypatch.setattr(consumer, 'run_batch', batch)
        monkeypatch.setattr(module, 'sleep', sleep)
        consumer.start()
        await asyncio.wait_for(done.wait(), 1)
        await consumer.close()
        assert reads == 4  # Three queue reads, then reader connection closure.

    asyncio.run(scenario())


@pytest.mark.parametrize("reader_fails", [False, True])
def test_close_drains_reader_before_connection_close(monkeypatch, reader_fails):
    module = runtime()

    async def scenario():
        consumer = module.StatisticsConsumer()
        entered = threading.Event()
        release = threading.Event()
        events = []

        def read():
            entered.set()
            release.wait(3)
            events.append(('read', threading.get_ident()))
            if reader_fails:
                raise RuntimeError('reader fails during shutdown')

        def close():
            events.append(('close', threading.get_ident()))

        monkeypatch.setattr(module, 'capture_queue', read)
        monkeypatch.setattr(module, '_close_connections', close)
        consumer.start()
        await asyncio.to_thread(entered.wait, 1)
        closing = asyncio.create_task(consumer.close())
        await turns()
        assert not closing.done()
        closing.cancel()
        await turns()
        closing.cancel()
        await turns()
        assert not closing.done()
        release.set()
        completed, _ = await asyncio.wait({closing}, timeout=1)
        if not completed:
            # Clean up the broken retry loop so this regression cannot hang pytest.
            consumer._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert completed, 'Shutdown retries a failed reader after cancellation'
        assert [event[0] for event in events] == ['read', 'close']
        assert events[0][1] == events[1][1] != threading.get_ident()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_real_batch_partial_trees_and_same_root_arrival(monkeypatch):
    from django.db import transaction
    from twicc.core.models import Project, Session, SessionItem, StatisticsQueueEntry, DailyActivity, WeeklyActivity
    from twicc.providers.statistics_queue import append_roots, capture_queue
    from twicc.providers import statistics_publication, db_writer
    from datetime import UTC, datetime
    from decimal import Decimal

    module = runtime()
    project = Project.objects.create(id='statistics')
    now = datetime(2026, 10, 14, tzinfo=UTC)
    for provider in ('claude', 'codex'):
        session = Session.objects.create(id=provider, project=project, provider=provider, file_path=provider,
            storage_incarnation=provider, last_updated_at=now, created_at=now, user_message_count=1,
            compute_version=0)
        SessionItem.objects.create(session=session, line_num=1, cost=Decimal('2.25'), timestamp=now,
            kind='user_message')
    with transaction.atomic():
        append_roots(('claude', 'codex'))
    batch = capture_queue()
    original = statistics_publication.publish_tree_costs

    async def publish(tree):
        await original(tree)
        def arrival():
            with transaction.atomic():
                append_roots(('claude',))
        await asyncio.to_thread(arrival)

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(statistics_publication, 'publish_tree_costs', publish)
    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)

    async def scenario():
        consumer = module.StatisticsConsumer()
        try:
            await consumer.run_batch(batch._replace(root_ids=('claude', 'codex', 'claude')))
        finally:
            await consumer.close()
    asyncio.run(scenario())
    assert list(StatisticsQueueEntry.objects.values_list('root_session_id', flat=True)) == ['claude']
    assert StatisticsQueueEntry.objects.get().id > batch.max_id
    project.refresh_from_db()
    assert project.total_cost == Decimal('4.5')
    assert list(Session.objects.values_list('total_cost', flat=True)) == [Decimal('2.25')] * 2
    assert DailyActivity.objects.count() == WeeklyActivity.objects.count() == 4
    assert all(row.cost == Decimal('2.25') for row in WeeklyActivity.objects.all())


@pytest.mark.django_db(transaction=True)
def test_batch_failure_keeps_every_captured_entry(monkeypatch):
    from twicc.core.models import StatisticsQueueEntry, Project, Session
    from twicc.providers import statistics_publication
    module = runtime()
    project = Project.objects.create(id='failure')
    for root in ('missing-one', 'missing-two'):
        Session.objects.create(id=root, file_path=root, project=project)
    entries = StatisticsQueueEntry.objects.bulk_create([
        StatisticsQueueEntry(root_session_id='missing-one'), StatisticsQueueEntry(root_session_id='missing-two')])

    async def fail(tree):
        raise RuntimeError('publication fails')
    monkeypatch.setattr(statistics_publication, 'publish_tree_costs', fail)

    async def scenario():
        consumer = module.StatisticsConsumer()
        try:
            with pytest.raises(RuntimeError, match='publication fails'):
                await consumer.run_batch(QueueBatch(entries[-1].id, ('missing-one', 'missing-two')))
        finally:
            await consumer.close()
    asyncio.run(scenario())
    assert StatisticsQueueEntry.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_writer_owns_consumer_lifecycle(monkeypatch):
    from twicc.providers import db_writer
    module = runtime()
    events = []

    class Consumer:
        def start(self):
            assert db_writer._db_writer_ready.done()
            events.append('start')

        async def close(self):
            assert db_writer._write_coordinator is not None
            assert not db_writer._db_writer_stop_event.is_set()
            events.append('close')

    monkeypatch.setattr(module, 'StatisticsConsumer', Consumer)

    async def scenario():
        db_writer.start_db_writer()
        await db_writer.wait_db_writer_ready()
        await db_writer.stop_db_writer()
    asyncio.run(scenario())
    assert events == ['start', 'close']


def test_retry_delay_doubles_and_stops_at_sixty_seconds(monkeypatch):
    module = runtime()

    async def scenario():
        consumer = module.StatisticsConsumer()
        delays = []
        done = asyncio.Event()

        async def read(function, *args):
            if function is module._close_connections:
                return
            raise RuntimeError('database unavailable')

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 8:
                done.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(consumer, '_read', read)
        monkeypatch.setattr(module, 'sleep', sleep)
        consumer.start()
        await done.wait()
        await consumer.close()
        assert delays == [1, 2, 4, 8, 16, 32, 60, 60]

    asyncio.run(scenario())
