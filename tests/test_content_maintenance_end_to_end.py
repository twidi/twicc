"""Compute placement and periodic selection share the same content policy."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.content_storage_helpers import _public_dictionary
from tests.test_content_extraction import make_session, message
from tests.test_content_maintenance_reads import payload_selects
from tests import test_content_storage_admission

from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
from twicc.core.models import Session, SessionItem
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.helpers import get_provider_helpers

writer_state = test_content_storage_admission.writer_state
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
def test_compute_then_conformant_pass_has_zero_payload_or_publication(
    provider, monkeypatch, writer_state, compute_executor_started, settings,
):
    _public_dictionary(provider)
    now = timezone.now()
    root = make_session(id='hot-root', created_at=now)
    child = make_session(id='cold-child', parent_session=root, type='subagent', created_at=now - timedelta(days=60))
    leaf = make_session(id='cold-leaf', parent_session=child, type='subagent', created_at=now - timedelta(days=60))
    Session.objects.filter(pk__in=[root.id, child.id, leaf.id]).update(provider=provider)
    shared = 'shared with hot root ' * 500
    for owner, tiny in ((root, 'z'), (child, 'x'), (leaf, 'y')):
        for line, text in enumerate((shared, tiny, f'{owner.id} repeated ' * 1000), 1):
            SessionItem.objects.create(session=owner, line_num=line, content=text)
    # Computing the hot owner first establishes exact shared ownership before cold placement.
    current = get_provider_helpers(provider).current_compute_version
    for owner, expected in ((root, 'superseded'), (leaf, 'applied'), (child, 'applied'), (root, 'applied')):
        result = message(owner)
        result['session_fields']['compute_version'] = current
        assert BaseSessionCompute.apply_session_complete(result).outcome == expected
    assert list(Session.objects.filter(pk__in=[root.id, child.id, leaf.id])
                .values_list('compute_version', flat=True)) == [current] * 3
    assert SessionItem.objects.get(session=leaf, line_num=3).current_content.codec == 'zstd'
    assert SessionItem.objects.get(session=child, line_num=3).current_content.codec == 'zstd'
    assert SessionItem.objects.get(session=root, line_num=1).current_content.codec == 'plain'
    assert SessionItem.objects.get(session=child, line_num=2).current_content.compression_no_gain
    cache = MaintenanceSessionCache()
    try:
        # Recovery completes before compression measurement. No hot compressed references remain.
        assert maintenance.recovery_promotion_targets([root.id, child.id, leaf.id], cache) == set()
        with CaptureQueriesContext(connection) as queries:
            page = maintenance.prepare_compression_batch(0, session_cache=cache)
        assert page.done
        assert page.counts.prepared == 0
        assert page.entries == ()
        assert payload_selects(queries) == []
        # Empty candidate pages never enter the publication coordinator.
        assert page.counts.payload_bytes_loaded == 0
    finally:
        cache.close()

    import asyncio
    from twicc.core.content_storage.maintenance_metrics import MaintenanceMetrics
    from twicc.providers import db_writer
    from twicc.providers.compute_work import ComputeWorkState

    summaries = []

    class ObservedMetrics(MaintenanceMetrics):
        def emit(self, event):
            if event == 'complete' and self.operation_kind == 'periodic_compression':
                summaries.append(dict(self.counts))

    def reject_grant():
        pytest.fail('Conformant compute output must request no periodic writer grant')

    async def wait(stop, wake, timeout):
        stop.set()

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(maintenance, 'INITIAL_DELAY', 0)
    monkeypatch.setattr(maintenance, 'MaintenanceMetrics', ObservedMetrics)
    monkeypatch.setattr(maintenance, '_wait', wait)
    monkeypatch.setattr(db_writer.get_write_coordinator(), 'try_background_lease', reject_grant)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    assert len(summaries) == 1
    assert summaries[0]['prepared'] == summaries[0]['changed'] == summaries[0]['publication_batches'] == 0


def test_ingestion_prepared_lookup_visits_each_record_once():
    from twicc.core.content_storage.ingestion import insert_ingested_items, prepare_ingestion_contents

    _public_dictionary('codex')
    owner = make_session(created_at=timezone.now() - timedelta(days=60))
    texts = [f'{index} repeat ' * 100 for index in range(151)]
    prepared = prepare_ingestion_contents(owner, texts)

    class CountedRecords(dict):
        visits = 0

        def items(self):
            for entry in super().items():
                self.visits += 1
                yield entry

    observed = CountedRecords(prepared)
    with transaction.atomic():
        inserted = insert_ingested_items(owner, [(SessionItem(session=owner, line_num=index), text)
                                                for index, text in enumerate(texts, 1)], prepared=observed)
    assert len(inserted) == 151
    assert observed.visits == len(prepared)


@pytest.mark.parametrize('cancel_preparation', [False, True])
def test_foreground_arrival_during_ingestion_preparation_commits_first(
    writer_state, compute_executor_started, monkeypatch, cancel_preparation,
):
    import asyncio
    import threading

    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync

    _public_dictionary('codex')
    owner = make_session(created_at=timezone.now() - timedelta(days=60))
    payload = db_writer.UpdateSessionPayload('codex', owner, [(1, 'prepared repeat ' * 1000)],
                                            200, 1, 1.0, False, False)
    entered, release = threading.Event(), threading.Event()
    original = db_writer._prepare_ingestion_payload
    trace = []

    def prepare(actual):
        entered.set()
        assert release.wait(5)
        assert not connection.in_atomic_block
        return original(actual)

    def foreground_write():
        with transaction.atomic():
            Session.objects.filter(pk=owner.id).update(title='foreground')
        trace.append('foreground')

    async def publish(actual, *, prepared=None):
        await run_compute_sync(db_writer._apply_update_session_payload, actual, prepared=prepared)
        trace.append('ingestion')

    monkeypatch.setattr(db_writer, '_pending_ingestion', None)
    monkeypatch.setattr(db_writer, '_prepare_ingestion_payload', prepare)
    monkeypatch.setattr(db_writer, '_process_thread_message', publish)

    async def scenario():
        db_writer._thread_queue.put_nowait(payload)
        drain = asyncio.create_task(db_writer._drain_one())
        assert await asyncio.to_thread(entered.wait, 5)
        assert not db_writer._db_write_lock.locked()
        foreground = asyncio.create_task(db_writer.run_under_db_write_lock(lambda: run_compute_sync(foreground_write)))
        await asyncio.sleep(0)
        if cancel_preparation:
            drain.cancel()
        release.set()
        if cancel_preparation:
            with pytest.raises(asyncio.CancelledError):
                await drain
        else:
            await drain
        await foreground
        if db_writer._pending_ingestion is not None:
            await db_writer._drain_one()
        assert db_writer._pending_ingestion is None
        assert not db_writer.get_write_coordinator().holders

    asyncio.run(scenario())
    assert trace == ['foreground', 'ingestion']
    assert SessionItem.objects.get(session=owner).current_content.codec == 'zstd'




def test_real_periodic_recovery_upgrade_and_cancel_drains_publication(
    writer_state, compute_executor_started, monkeypatch, settings, content_row_factory,
):
    import asyncio
    import threading

    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.content_storage.maintenance_metrics import MaintenanceMetrics
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.compute_work import ComputeWorkState
    from twicc.providers.write_coordinator import WritePriority, write_priority

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    clock, emissions, publications, caches = [0.0], [], [], []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    owner = make_session(created_at=timezone.now(), content_storage_version=1, content_state_epoch=3)
    Session.objects.filter(pk=owner.id).update(compute_version=get_provider_helpers('codex').current_compute_version)
    prepared, release_preparation = threading.Event(), threading.Event()
    admitted, release_publication = threading.Event(), threading.Event()
    state = ComputeWorkState()

    class ObservedMetrics(MaintenanceMetrics):
        def emit(self, event):
            emissions.append((self.operation_kind, event, dict(self.counts)))

    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch
    waits = 0

    def seed_hot_compressed():
        for line in (1, 2):
            row = content_row_factory(f'periodic {line} ' * 1000, True, session_ids=[owner.id])
            SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)

    async def wait(stop, wake, timeout):
        nonlocal waits
        waits += 1
        if waits == 1:
            await run_compute_sync(seed_hot_compressed)
            clock[0] += timeout
        else:
            await wake.wait()

    def prepare(*args, **kwargs):
        caches.append(kwargs['session_cache'])
        result = original_prepare(*args, **kwargs)
        if not prepared.is_set():
            prepared.set()
            assert release_preparation.wait(5)
        return result

    def apply(batch, active, **kwargs):
        publications.append(write_priority.get())
        if len(publications) == 2:
            admitted.set()
            assert release_publication.wait(5)
        return original_apply(batch, active, **kwargs)

    monkeypatch.setattr(maintenance, 'MaintenanceMetrics', ObservedMetrics)
    monkeypatch.setattr(maintenance, '_wait', wait)
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)

    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=state))
        try:
            assert await asyncio.to_thread(prepared.wait, 5)
            state.set_pending('codex', True)
            release_preparation.set()
            # A periodic target retains its prepared page while compute pauses.
            for _ in range(10):
                await asyncio.sleep(0)
            assert publications == []
            maintenance.request_root_promotion(owner.id)
            assert await asyncio.to_thread(admitted.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not any(event == 'cancelled' for _, event, _ in emissions)
            assert db_writer.get_write_coordinator()._foreground_wait_observers
        finally:
            release_preparation.set()
            release_publication.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        coordinator = db_writer.get_write_coordinator()
        assert not coordinator.holders and not coordinator.demands and not coordinator.requests
        assert not coordinator._foreground_wait_observers

    asyncio.run(scenario())
    assert publications == [WritePriority.FOREGROUND, WritePriority.FOREGROUND]
    assert caches[0] is not caches[-1]  # Explicit upgrade takes its own cache.
    assert all(cache._closed for cache in caches)
    assert sorted(kind for kind, event, _ in emissions if event == 'cancelled') == [
        'periodic_compression', 'plaintext_restoration',
    ]
    restoration = next(counts for kind, event, counts in emissions
                   if kind == 'plaintext_restoration' and event == 'cancelled')
    assert restoration['changed'] == 2
    assert list(SessionItem.objects.filter(session=owner).values_list('current_content__codec', flat=True)) == [
        'plain', 'plain',
    ]


@pytest.fixture
def benchmark_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'scripts/benchmark_content_maintenance.py'
    spec = importlib.util.spec_from_file_location('benchmark_content_maintenance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_rejects_nonempty_or_instance_work_directory(benchmark_module, tmp_path, monkeypatch):
    from types import SimpleNamespace

    occupied = tmp_path / 'occupied'
    occupied.mkdir()
    marker = occupied / 'keep'
    marker.write_text('unchanged')
    args = SimpleNamespace(work_dir=occupied, source_db=None)
    with pytest.raises(ValueError, match='empty'):
        benchmark_module.destination(args)
    assert marker.read_text() == 'unchanged'
    instance = tmp_path / 'instance'
    monkeypatch.setenv('TWICC_DATA_DIR', str(instance))
    args.work_dir = instance / 'new'
    with pytest.raises(ValueError, match='instance data directory'):
        benchmark_module.destination(args)
    assert not args.work_dir.exists()


def test_benchmark_backup_leaves_source_bytes_unchanged(benchmark_module, tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE fixture (value INTEGER)')
        db.execute('INSERT INTO fixture VALUES (7)')
    before = source.read_bytes()
    monkeypatch.setenv('TWICC_DATA_DIR', str(tmp_path / 'instance'))
    args = SimpleNamespace(work_dir=tmp_path / 'copy', source_db=source)
    _, target = benchmark_module.destination(args)
    with sqlite3.connect(target) as db:
        db.execute('UPDATE fixture SET value=9')
    assert source.read_bytes() == before
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT value FROM fixture').fetchone() == (7,)


def test_changed_owner_skips_only_its_candidate_and_foreground_commits_between_slices(
    writer_state, compute_executor_started, content_row_factory, monkeypatch,
):
    import asyncio
    from functools import partial

    from twicc.core.content_storage.lifecycle import update_lifecycle
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.write_coordinator import WritePriority

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    changed = make_session(id='changed-owner', created_at=old, content_storage_version=1)
    unchanged = make_session(id='unchanged-owner', created_at=old, content_storage_version=1)
    rows = [content_row_factory(f'slice {line} ' * 1000, False, session_ids=[owner.id])
            for line, owner in enumerate((changed, unchanged, unchanged))]
    cache, trace = MaintenanceSessionCache(), []

    def foreground(*, activate=False):
        with transaction.atomic():
            if activate:
                update_lifecycle(Session.objects.filter(pk=changed.id), last_started_at=timezone.now())
            else:
                Session.objects.filter(pk=unchanged.id).update(title='foreground commit')
        trace.append('foreground')

    async def scenario():
        cursor = 0
        for index in range(3):
            page = await run_compute_sync(partial(maintenance.prepare_compression_batch,
                                                 row_budget=1, session_cache=cache), cursor)
            assert len(page.entries) == 1
            if index == 0:
                await db_writer.run_under_db_write_lock(lambda: run_compute_sync(partial(foreground, activate=True)))
            result = await maintenance._apply(partial(maintenance._publish, session_cache=cache), page.entries,
                                              priority=WritePriority.BACKGROUND)
            trace.append(result.changed)
            cursor = page.cursor
            if index == 1:
                await db_writer.run_under_db_write_lock(lambda: run_compute_sync(foreground))

    try:
        asyncio.run(scenario())
    finally:
        cache.close()
    assert trace == ['foreground', 0, 1, 'foreground', 1]
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['plain', 'zstd', 'zstd']


@pytest.mark.parametrize('alias', ['source', 'destination', 'protected', 'symlink', 'hardlink'])
def test_benchmark_rejects_output_alias_before_execution(benchmark_module, tmp_path, monkeypatch, alias):
    import os
    from types import SimpleNamespace

    source = tmp_path / 'source.sqlite'
    source.write_bytes(b'original source bytes')
    work = tmp_path / 'work'
    instance = tmp_path / 'instance'
    instance.mkdir()
    protected = instance / 'data.sqlite'
    protected.write_bytes(b'original instance bytes')
    output = {'source': source, 'destination': work / 'benchmark.sqlite', 'protected': protected}.get(alias)
    if alias in {'symlink', 'hardlink'}:
        output = tmp_path / 'alias.json'
        if alias == 'symlink':
            output.symlink_to(source)
        else:
            os.link(source, output)
    args = SimpleNamespace(work_dir=work, source_db=source, output=output, scenario='conformant')
    monkeypatch.setenv('TWICC_DATA_DIR', str(instance))
    monkeypatch.setattr(benchmark_module, 'arguments', lambda: args)

    def forbidden(*args, **kwargs):
        pytest.fail('Output rejection must precede backup and configuration')

    monkeypatch.setattr(benchmark_module, 'destination', forbidden)
    monkeypatch.setattr(benchmark_module, 'configure', forbidden)
    with pytest.raises(ValueError, match='Output'):
        benchmark_module.main()
    assert source.read_bytes() == b'original source bytes'
    assert protected.read_bytes() == b'original instance bytes'
    assert not work.exists()


def test_benchmark_output_created_during_execution_stays_unchanged(benchmark_module, tmp_path, monkeypatch):
    from types import SimpleNamespace

    output = tmp_path / 'result.json'
    work = tmp_path / 'work'
    args = SimpleNamespace(work_dir=work, source_db=tmp_path / 'source.sqlite', output=output,
                           scenario='conformant')
    monkeypatch.setenv('TWICC_DATA_DIR', str(tmp_path / 'instance'))
    monkeypatch.setattr(benchmark_module, 'arguments', lambda: args)
    monkeypatch.setattr(benchmark_module, 'destination', lambda args: (work, work / 'benchmark.sqlite'))
    monkeypatch.setattr(benchmark_module, 'configure', lambda *args, **kwargs: None)

    async def measured(*args):
        output.write_bytes(b'created by another writer')
        return {}

    monkeypatch.setattr(benchmark_module, 'run_pass', measured)
    with pytest.raises(FileExistsError):
        benchmark_module.main()
    assert output.read_bytes() == b'created by another writer'


def test_benchmark_creates_new_output_inside_work_directory(benchmark_module, tmp_path, monkeypatch):
    import orjson
    from types import SimpleNamespace

    work = tmp_path / 'work'
    output = work / 'result.json'
    args = SimpleNamespace(work_dir=work, source_db=tmp_path / 'source.sqlite', output=output,
                           scenario='conformant')
    monkeypatch.setenv('TWICC_DATA_DIR', str(tmp_path / 'instance'))
    monkeypatch.setattr(benchmark_module, 'arguments', lambda: args)
    monkeypatch.setattr(benchmark_module, 'destination', lambda args: (work, work / 'benchmark.sqlite'))
    monkeypatch.setattr(benchmark_module, 'configure', lambda *args, **kwargs: None)

    async def measured(*args):
        assert not output.exists()
        return {'scanned': 0}

    monkeypatch.setattr(benchmark_module, 'run_pass', measured)
    benchmark_module.main()
    assert orjson.loads(output.read_bytes())['result'] == {'scanned': 0}
