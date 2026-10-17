"""Controlled-clock coverage for the whole periodic compute gate."""

import asyncio

import pytest

from twicc import content_maintenance_task as maintenance
from twicc.providers.compute_work import ComputeWorkState
from tests import test_content_storage_admission

writer_state = test_content_storage_admission.writer_state

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def no_scheduler_errors(caplog):
    yield
    assert not [record for record in caplog.get_records('call') if record.levelname in {'ERROR', 'CRITICAL'}]


@pytest.mark.parametrize('finished, first', [(600, 1800), (2400, 2400)])
def test_pending_compute_retains_first_deadline_and_completion_interval(
    writer_state, compute_executor_started, monkeypatch, settings, finished, first,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock, preparations, waits = [0], [], []
    work = ComputeWorkState()
    work.set_pending('claude', True)
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original = maintenance.prepare_compression_batch

    def compression(*args, **kwargs):
        preparations.append(clock[0])
        result = original(*args, **kwargs)
        clock[0] += 20
        return result

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        assert len(waits) < 8, 'Overdue pending work must not spin'
        if work.has_pending():
            clock[0] = finished
            work.set_pending('claude', False)
        elif not preparations:
            clock[0] += timeout
        else:
            assert timeout == 86400
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work))
    assert preparations == [first]


def test_overdue_waits_for_all_providers_and_recovery_without_worker(
    writer_state, compute_executor_started, monkeypatch, settings,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = True
    clock, preparations, cleanup, inspections, waits = [0], [], [], [], []
    work = ComputeWorkState()
    work.set_pending('claude', True)
    work.set_pending('codex', True)
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original = maintenance.pending_session_page

    def page(*args):
        inspections.append(clock[0])
        return original(*args)

    def compression(*args, **kwargs):
        preparations.append(clock[0])
        return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

    async def clean(**kwargs):
        cleanup.append(clock[0])

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            clock[0] = 1800
            work.set_pending('claude', False)
        elif len(waits) == 2:
            assert preparations == cleanup == []
            assert inspections == [0]  # Only metadata-only startup discovery.
            assert timeout is None
            clock[0] = 2400
            work.set_pending('codex', False)  # Recovery drains without a live worker.
        else:
            stop.set()

    from twicc import session_dirs_cleanup_task
    monkeypatch.setattr(maintenance, 'pending_session_page', page)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(session_dirs_cleanup_task, '_run_cleanup_pass', clean)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work))
    assert preparations == cleanup == [2400]


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.parametrize('restart', ['preparation', 'publication'])
def test_compute_restart_pauses_prepared_publication_and_resumes_cursor(
    writer_state, compute_executor_started, monkeypatch, settings, provider, restart,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock, cursors, published = [0], [], []
    work = ComputeWorkState()
    loop = None
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    def compression(cursor, *args, **kwargs):
        cursors.append(cursor)
        if cursor == 0:
            if restart == 'preparation':
                loop.call_soon_threadsafe(work.set_pending, provider, True)
            return maintenance.CompressionPage(9, ('prepared',), False, maintenance.CompressionPageCounts(prepared=1))
        return maintenance.CompressionPage(10, (), True, maintenance.CompressionPageCounts())

    async def apply(function, *args, **kwargs):
        if getattr(function, 'func', function) is maintenance._publish:
            assert not work.has_pending()
            published.extend(args[0])
            if restart == 'publication':
                work.set_pending(provider, True)
            kwargs['lease'].release()
        else:
            pytest.fail('Unexpected writer operation')

    async def wait(stop, wake, timeout):
        if clock[0] == 0:
            clock[0] = 1800
        elif work.has_pending():
            assert cursors == [0]
            assert published == ([] if restart == 'preparation' else ['prepared'])
            assert timeout is None
            clock[0] = 2400
            work.set_pending(provider, False)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_apply', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        nonlocal loop
        loop = asyncio.get_running_loop()
        await maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work)

    asyncio.run(scenario())
    assert cursors == [0, 9]
    assert published == ['prepared']


@pytest.mark.parametrize('provider_pending', [False, True])
def test_foreground_plaintext_demand_runs_while_periodic_is_overdue(
    writer_state, compute_executor_started, content_row_factory, monkeypatch, settings, provider_pending,
):
    from twicc.providers import db_writer
    from tests.test_content_root_state import session
    from twicc.core.models import SessionItem
    from twicc.core.content_storage.codec import decode_content
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    owner = session('demand', content_storage_version=1, storage_incarnation='demand-inc', content_state_epoch=3)
    row = content_row_factory('foreground text' * 1000, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    # The demand arrives after the startup inventory completes.
    monkeypatch.setattr(maintenance, 'pending_session_page', lambda *args: [])
    work = ComputeWorkState()
    work.set_pending('codex', provider_pending)
    if not provider_pending:
        ingress = db_writer.ComputeIngressQueue()
        ingress.put_nowait({'type': 'session_complete', 'run_id': 52, 'session_id': 'demand'})
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
    clock, waits, preparations = [0], [], []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    def compression(*args, **kwargs):
        preparations.append(clock[0])
        return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

    def restored():
        row.refresh_from_db()
        assert row.codec == 'plain'
        assert decode_content(row) == 'foreground text' * 1000

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            clock[0] = 1800
        elif len(waits) == 2:
            assert timeout is None
            maintenance.request_root_promotion(owner.id)
            await wake.wait()
        else:
            await asyncio.to_thread(restored)
            assert work.has_pending() or db_writer.has_pending_source_applications()
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work))
    assert preparations == []
    assert len(waits) == 3


@pytest.mark.parametrize('accepted_source', [False, True])
def test_change_between_inspection_and_wait_is_not_lost(
    writer_state, compute_executor_started, monkeypatch, settings, accepted_source,
):
    from twicc.providers import db_writer

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock, preparations, waits = [0], [], []
    work = ComputeWorkState()
    work.set_pending('codex', not accepted_source)
    message = {'type': 'session_complete', 'run_id': 53, 'session_id': 'race'}
    if accepted_source:
        ingress = db_writer.ComputeIngressQueue(on_put=db_writer._writer_signals.sources.set)
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
        ingress.put_nowait(message)
    original_pending = db_writer.has_pending_source_applications if accepted_source else work.has_pending
    original_wait = maintenance._wait
    inspections = 0
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    def pending():
        nonlocal inspections
        result = original_pending()
        if clock[0] == 1800 and result:
            inspections += 1
            if inspections == 2:
                # Simulate a transition after the last authoritative inspection.
                if accepted_source:
                    ingress.get_nowait()
                    db_writer._release_source_application(message)
                else:
                    work.set_pending('codex', False)
        return result

    def compression(*args, **kwargs):
        preparations.append(clock[0])
        return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            clock[0] = 1800
        elif preparations:
            stop.set()
        else:
            assert timeout is None
            await original_wait(stop, wake, timeout)

    monkeypatch.setattr(db_writer if accepted_source else work,
                        'has_pending_source_applications' if accepted_source else 'has_pending', pending)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        await asyncio.wait_for(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work), 2)

    asyncio.run(scenario())
    assert preparations == [1800]
    assert waits == [1800, None, 86400]


@pytest.mark.parametrize('overdue', [False, True])
def test_stop_wakes_timer_and_overdue_compute_wait(
    writer_state, compute_executor_started, monkeypatch, settings, overdue,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock = [0]
    work = ComputeWorkState()
    work.set_pending('codex', True)
    original_wait = maintenance._wait
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    async def scenario():
        entered, stop = asyncio.Event(), asyncio.Event()

        async def wait(stop, wake, timeout):
            if overdue and clock[0] == 0:
                clock[0] = 1800
                return
            assert timeout == (None if overdue else 1800)
            entered.set()
            await original_wait(stop, wake, timeout)

        monkeypatch.setattr(maintenance, '_wait', wait)
        task = asyncio.create_task(maintenance.start_content_maintenance_task(stop, compute_work=work))
        await asyncio.wait_for(entered.wait(), 2)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_cleanup_rechecks_compute_after_waiting_for_manager_locks(monkeypatch):
    from contextlib import asynccontextmanager
    from twicc import session_dirs_cleanup_task as cleanup
    from twicc.agent.registry import get_agent_manager_registry

    work = ComputeWorkState()
    pruned = []

    @asynccontextmanager
    async def guard():
        work.set_pending('codex', True)
        yield

    monkeypatch.setattr(get_agent_manager_registry(), 'work_dirs_cleanup_guard', guard)
    monkeypatch.setattr(cleanup, '_prune_stale_session_dirs', lambda: pruned.append(True))

    async def scenario():
        assert await cleanup._run_cleanup_pass(compute_work=work) is False

    asyncio.run(scenario())
    assert pruned == []


def test_periodic_recovery_pauses_prepared_promotion_when_compute_restarts(
    writer_state, compute_executor_started, content_row_factory, monkeypatch, settings,
):
    from tests.test_content_root_state import session
    from twicc.core.models import SessionItem
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    owner = session('recovery', content_storage_version=1, storage_incarnation='recovery-inc', content_state_epoch=3)
    row = content_row_factory('recover content' * 1000, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    clock, waits, prepared, published = [0], [], [], []
    work = ComputeWorkState()
    loop = None
    original_page = maintenance.pending_session_page
    original_prepare = maintenance.prepare_promotion_batch
    original_apply = maintenance.apply_promotion_batch
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    def page(*args):
        # This target becomes visible only after the startup inventory.
        return [] if clock[0] == 0 else original_page(*args)

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        prepared.append(args[0].root_id)
        loop.call_soon_threadsafe(work.set_pending, 'codex', True)
        return result

    def apply(*args, **kwargs):
        published.append(args[0].snapshot.root_id)
        return original_apply(*args, **kwargs)

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if clock[0] == 0:
            clock[0] = 1800
        elif work.has_pending():
            assert timeout is None
            assert prepared == [owner.id] and published == []
            clock[0] = 2400
            work.set_pending('codex', False)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'pending_session_page', page)
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        nonlocal loop
        loop = asyncio.get_running_loop()
        await maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work)

    asyncio.run(scenario())
    row.refresh_from_db()
    assert row.codec == 'plain'
    assert prepared == published == [owner.id]


@pytest.mark.parametrize('outcome', ['applied', 'superseded', 'failed', 'abandoned'])
def test_last_application_release_wakes_overdue_maintenance_without_provider_change(
    writer_state, compute_executor_started, monkeypatch, settings, outcome,
):
    from types import SimpleNamespace
    from twicc.providers import db_writer
    from twicc.providers.compute_base import ComputeApplyResult
    from twicc.providers.writer_signals import WriterSignals
    from twicc.providers.compute_executor import (
        start_compute_application_executor, stop_compute_application_executor,
    )

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock, preparations = [0], []
    work = ComputeWorkState()
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original_wait = maintenance._wait

    async def scenario():
        start_compute_application_executor()
        signals = WriterSignals(asyncio.get_running_loop())
        monkeypatch.setattr(db_writer, '_writer_signals', signals)
        ingress = db_writer.ComputeIngressQueue(on_put=signals.notify_sources)
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
        message = {'type': 'session_complete', 'run_id': 41, 'session_id': 'accepted'}
        ingress.put_nowait(message)
        state = SimpleNamespace(abandoned=outcome == 'abandoned', failed_count=0, applied_queue=None)
        monkeypatch.setitem(db_writer._compute_states, 41, state)
        pending = db_writer.PendingComputeApplication(message, 41, state,
            result=ComputeApplyResult('failed' if outcome == 'abandoned' else outcome))
        notifications = asyncio.Event()

        async def notify(*args):
            await notifications.wait()

        def compression(*args, **kwargs):
            assert not ingress.pending_sources
            preparations.append(clock[0])
            return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

        async def wait(stop, wake, timeout):
            if clock[0] == 0:
                clock[0] = 1800
            elif not preparations:
                assert timeout is None, 'Accepted queued work must close the overdue gate'
                assert not work.has_pending() and not work.changed.is_set()
                assert ingress.get_nowait() == message
                monkeypatch.setattr(db_writer, '_pending_compute', pending)
                await db_writer._finish_pending_compute(pending, notify=outcome != 'abandoned')
                assert pending.terminal and signals.sources.is_set()
                assert not work.changed.is_set()
                # The real source event must wake an already overdue scheduler.
                await original_wait(stop, wake, timeout)
            else:
                assert preparations == [1800]
                stop.set()

        monkeypatch.setattr(db_writer, '_complete_compute_result', notify)
        monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
        monkeypatch.setattr(maintenance, '_wait', wait)
        try:
            await asyncio.wait_for(maintenance.start_content_maintenance_task(
                asyncio.Event(), compute_work=work), 2)
        finally:
            notifications.set()
            if pending.notifications is not None:
                await pending.notifications
                assert db_writer._collect_pending_notifications(pending)
            signals.close()
            await stop_compute_application_executor()
        assert not ingress.pending_sources and db_writer._pending_compute is None
        assert preparations == [1800]

    asyncio.run(scenario())


@pytest.mark.parametrize('no_gain', [False, True])
@pytest.mark.parametrize('change_epoch', [False, True])
def test_prepared_periodic_candidate_rechecks_gate_after_lease(
    writer_state, compute_executor_started, content_row_factory, monkeypatch, settings, no_gain, change_epoch,
):
    from tests.test_content_maintenance import cold
    from twicc.core.models import SessionItemContent
    from twicc.providers import db_writer
    from twicc.providers.writer_signals import WriterSignals

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    owner = cold('lease-race')
    row = content_row_factory('x' if no_gain else 'compress this text ' * 1000,
                              False, session_ids=[owner.id])
    clock, published, observations = [0], [], []
    work = ComputeWorkState()
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original_publish = maintenance._publish
    coordinator = db_writer.get_write_coordinator()
    original_lease = coordinator.try_background_lease
    original_prepare = maintenance.prepare_compression_batch
    message = {'type': 'session_complete', 'run_id': 42, 'session_id': 'arrived'}
    raced = False

    async def scenario():
        signals = WriterSignals(asyncio.get_running_loop())
        monkeypatch.setattr(db_writer, '_writer_signals', signals)
        ingress = db_writer.ComputeIngressQueue(on_put=signals.notify_sources)
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)

        def prepare(*args, **kwargs):
            result = original_prepare(*args, **kwargs)
            observations.extend(result[1])
            return result

        def lease():
            nonlocal raced
            result = original_lease()
            if result is not None and observations and not raced:
                raced = True
                ingress.put_nowait(message)
            return result

        def publish(entries, active, **kwargs):
            assert not ingress.pending_sources, 'Gate closes after preparation and lease acquisition'
            published.extend(entries)
            return original_publish(entries, active, **kwargs)

        async def wait(stop, wake, timeout):
            if clock[0] == 0:
                clock[0] = 1800
            elif ingress.pending_sources:
                assert timeout is None
                assert published == [] and not coordinator.holders
                def untouched():
                    stored = SessionItemContent.objects.get(pk=row.pk)
                    assert stored.codec == 'plain' and stored.compression_attempt_version is None
                await asyncio.to_thread(untouched)
                if change_epoch:
                    from twicc.core.models import Session
                    await asyncio.to_thread(lambda: Session.objects.filter(pk=owner.pk).update(content_state_epoch=1))
                ingress.get_nowait()
                db_writer._release_source_application(message)
            else:
                stop.set()

        monkeypatch.setattr(maintenance, 'prepare_compression_batch', prepare)
        monkeypatch.setattr(maintenance, '_publish', publish)
        monkeypatch.setattr(coordinator, 'try_background_lease', lease)
        monkeypatch.setattr(maintenance, '_wait', wait)
        await maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work)
        signals.close()

    asyncio.run(scenario())
    row.refresh_from_db()
    assert raced and published == observations and len(published) == 1
    # Structural epochs do not change the owner's own lifecycle eligibility.
    assert row.compression_no_gain is no_gain
    assert row.codec == ('plain' if no_gain else 'zstd')
