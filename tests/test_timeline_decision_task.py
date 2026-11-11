"""Offline worker scheduling, original deadlines, and retry obligations."""

import asyncio
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.test_timeline_decisions import decision_fixture, admit
from tests.test_timeline_messages import live_session, content_row_factory
from tests.test_completion_publication import bridge, open_candidate
from twicc.agent.semantic_types import Classification
from twicc.core.models import TimelineDecision

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def writer(monkeypatch):
    from twicc.providers import db_writer
    monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())


def test_runtime_applies_without_new_ingress(decision_fixture, monkeypatch):
    from twicc import timeline_decision_task as runtime
    session, now, request = decision_fixture
    key = admit(request, now)
    TimelineDecision.objects.filter(pk=key).update(not_before=now)
    async def classify(request, **kwargs):
        return Classification('NO', 'routine'), 'provider'
    monkeypatch.setattr(runtime, 'classify', classify)
    monkeypatch.setattr(runtime, 'build_decision_context', lambda request: asyncio.sleep(0, result=request))
    monkeypatch.setattr(runtime, '_source_ready', lambda row: True)
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        for _ in range(100):
            status = await runtime._admitted(lambda: TimelineDecision.objects.get(pk=key).status)
            if status == 'rejected':
                break
            await asyncio.sleep(.01)
        stop.set()
        await task
        assert status == 'rejected'
    asyncio.run(scenario())


def test_expiry_runs_with_saturated_inference(decision_fixture, monkeypatch):
    from uuid import uuid4
    from twicc import timeline_decision_task as runtime
    from twicc.core.models import Session
    session, now, request = decision_fixture
    current = [now]
    monkeypatch.setattr(runtime.timezone, 'now', lambda: current[0])
    for index in range(5):
        owner = Session.objects.create(id=f'expiry-{index}', project=session.project, provider=session.provider,
            file_path=f'expiry-{index}.jsonl', storage_incarnation=session.storage_incarnation,
            history_epoch=session.history_epoch)
        candidate = request._replace(id=str(uuid4()), session_id=owner.pk)
        admit(candidate, now, identity=f'message:{index}')
    TimelineDecision.objects.update(not_before=now)
    monkeypatch.setattr(runtime, 'build_decision_context', lambda request: asyncio.sleep(0, result=request))
    monkeypatch.setattr(runtime, '_source_ready', lambda row: True)
    async def scenario():
        entered = []
        saturated = asyncio.Event()
        async def classify(request, **kwargs):
            entered.append(request.id)
            if len(entered) == 4:
                saturated.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(runtime, 'classify', classify)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        await asyncio.wait_for(saturated.wait(), 1)
        current[0] = request.expires_at
        runtime.decisions._wakeup()
        for _ in range(100):
            rows = await runtime._admitted(lambda: list(TimelineDecision.objects.all()))
            if all(row.status == 'accepted' for row in rows):
                break
            await asyncio.sleep(.005)
        stop.set()
        await task
        assert all(row.status == 'accepted' and row.reason == 'timeout' for row in rows)
        assert len(entered) == 4  # The queued fifth row expires without inference.
    asyncio.run(scenario())


def test_force_before_inference_skips_model(decision_fixture, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from twicc.core.services.timeline_decisions import force_message
    _, now, request = decision_fixture
    key = admit(request, now)
    force_message(decision_id=key, reason='final_message')
    monkeypatch.setattr(runtime, 'classify', lambda *args, **kw: pytest.fail('Forced message invokes inference'))
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        await asyncio.sleep(.03)
        stop.set()
        await task
    asyncio.run(scenario())
    assert TimelineDecision.objects.get(pk=key).forced


def test_delayed_writer_result_uses_original_expiry(decision_fixture, monkeypatch):
    from twicc import timeline_decision_task as runtime
    _, now, request = decision_fixture
    key = admit(request, now)
    row = TimelineDecision.objects.get(pk=key)
    current = [now]
    monkeypatch.setattr(runtime.timezone, 'now', lambda: current[0])
    async def scenario():
        worker = runtime.DecisionRuntime()
        entered, release = asyncio.Event(), asyncio.Event()
        from twicc.providers import db_writer
        async def block():
            entered.set()
            await release.wait()
        blocker = asyncio.create_task(db_writer.run_under_db_write_lock(block))
        await entered.wait()
        applying = asyncio.create_task(worker.apply(row, classification=Classification('NO', 'late'), route='provider'))
        await asyncio.sleep(0)
        current[0] = request.expires_at
        release.set()
        await blocker
        assert await applying
        await worker.stop()
    asyncio.run(scenario())
    row.refresh_from_db()
    assert (row.status, row.reason) == ('accepted', 'timeout')
    assert row.occurred_at == now


def test_fallback_and_ignored_late_result_are_distinguishable(decision_fixture, monkeypatch, caplog):
    import logging
    import orjson
    from twicc import timeline_decision_task as runtime
    _, now, request = decision_fixture
    key = admit(request, now)
    row = TimelineDecision.objects.get(pk=key)
    caplog.set_level(logging.DEBUG, logger='twicc')
    async def scenario():
        worker = runtime.DecisionRuntime()
        assert await worker.apply(row, fallback='timeout')
        assert not await worker.apply(row, classification=Classification('NO', 'late'), route='provider')
        assert not await worker.apply(row, classification=Classification('NO', 'late'), route='provider')
        await worker.stop()
    asyncio.run(scenario())
    logs = [orjson.loads(record.getMessage().split('semantic_decision ', 1)[1])
            for record in caplog.records if 'semantic_decision ' in record.getMessage()]
    assert [(item['event'], item['reason']) for item in logs] == [('fallback', 'timeout'), ('ignored', 'late_result')]
    assert logs[1]['verdict'] == 'NO'
    assert 'PRIVATE' not in caplog.text


def test_four_workers_and_per_session_fairness(decision_fixture, monkeypatch):
    from uuid import uuid4
    from twicc import timeline_decision_task as runtime
    from twicc.core.models import Session
    session, now, request = decision_fixture
    keys = []
    for index in range(7):
        owner = session if index < 3 else Session.objects.create(id=f'fair-{index}', project=session.project,
            provider=session.provider, file_path=f'fair-{index}.jsonl', storage_incarnation=session.storage_incarnation, history_epoch=session.history_epoch)
        candidate = request._replace(id=str(uuid4()), session_id=owner.pk)
        keys.append(admit(candidate, now, identity=f'candidate:{index}'))
    TimelineDecision.objects.update(not_before=now)
    entered, running = [], []
    async def classify(request, **kwargs):
        entered.append(request.session_id)
        running.append(request.session_id)
        try:
            await asyncio.Event().wait()
        finally:
            running.remove(request.session_id)
    monkeypatch.setattr(runtime, 'classify', classify)
    monkeypatch.setattr(runtime, 'build_decision_context', lambda request: asyncio.sleep(0, result=request))
    monkeypatch.setattr(runtime, '_source_ready', lambda row: True)
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        for _ in range(100):
            if len(entered) == 4:
                break
            await asyncio.sleep(.005)
        assert len(running) == len(set(running)) == 4
        assert entered.count(session.pk) == 1
        stop.set()
        await task
        assert running == []
    asyncio.run(scenario())


def test_saturated_scheduler_waits_for_events_without_scanning(decision_fixture, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from unittest.mock import Mock
    async def scenario():
        worker = runtime.DecisionRuntime()
        worker.workers = {str(i): object() for i in range(4)}
        effects = Mock(return_value=[])
        next_ready = Mock(side_effect=AssertionError('Saturated scheduler reads due candidates'))
        monkeypatch.setattr(runtime, '_effect_rows', effects)
        monkeypatch.setattr(runtime, '_next_ready', next_ready)
        task = asyncio.create_task(worker.schedule())
        await asyncio.sleep(.04)
        assert effects.call_count == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        worker.workers = {}
        await worker.stop()
    asyncio.run(scenario())


def test_shutdown_joins_workers_then_drains_owned_calls(monkeypatch):
    from twicc import timeline_decision_task as runtime
    async def scenario():
        worker = runtime.DecisionRuntime()
        order = []
        started = asyncio.Event()
        async def inference():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append('worker')
        async def drain():
            order.append('drain')
        monkeypatch.setattr(runtime, 'drain_classifier_calls', drain)
        task = asyncio.create_task(inference())
        await started.wait()
        worker.workers['a'] = task
        await worker.stop()
        assert order == ['worker', 'drain']
    asyncio.run(scenario())


def test_live_ending_applies_without_canceling_own_publication(bridge, live_session, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.core.enums import Provider
    from twicc.agent.states import AgentState
    from tests.test_semantic_completion_integration import evidence
    from twicc.core.models import TimelineEvent

    manager, agent = bridge
    f = live_session
    f.append(f.message('last', 'Exact final'))
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch
    agent._completion_capture_ending_evidence = evidence
    agent._completion_shells_are_current = lambda context: True
    async def classify(request, **kwargs):
        return Classification('YES' if request.kind == 'ending' else 'NO', 'independent shell'), 'provider'
    monkeypatch.setattr(runtime, 'classify', classify)
    def live(row):
        context = agent._completion_pending_ending
        return (manager, agent) if context is not None and context.id == str(row.pk) else None
    monkeypatch.setattr(runtime, '_live_ending', live)
    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        await manager._confirm_completion(agent, agent.completion_controller.candidate())
        context = agent._completion_pending_ending
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        for _ in range(100):
            if agent.state == AgentState.USER_TURN:
                break
            await asyncio.sleep(.005)
        stop.set()
        await task
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        agent._cancel_completion_timer()
        assert agent.state == AgentState.USER_TURN
        row = await runtime._admitted(lambda: TimelineDecision.objects.get(pk=context.id))
        assert row.status == 'accepted' and row.applied_at and not row.force_pending
        assert await runtime._admitted(f.texts) == ['Exact final']
        assert await runtime._admitted(lambda: TimelineEvent.objects.filter(kind='turn.ended', session_id=f.session.pk).count()) == 1
        manager._broadcast_info.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize('live_session', ['claude_code'], indirect=True)
def test_accepted_message_retries_event_failure_without_new_ingress(live_session, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from twicc.core.services import timeline_messages
    f = live_session
    f.append(f.message('a', 'Useful answer'))
    row = TimelineDecision.objects.get()
    original = timeline_messages.record_event_sync
    attempts = []
    def insert(*args, **kwargs):
        attempts.append(1)
        return None if len(attempts) == 1 else original(*args, **kwargs)
    monkeypatch.setattr(timeline_messages, 'record_event_sync', insert)
    monkeypatch.setattr(runtime, 'RETRY_SECONDS', .02)
    async def classify(*args, **kwargs):
        return Classification('YES', 'useful'), 'provider'
    monkeypatch.setattr(runtime, 'classify', classify)
    TimelineDecision.objects.filter(pk=row.pk).update(not_before=timezone.now())
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        for _ in range(100):
            applied = await runtime._admitted(lambda: TimelineDecision.objects.get(pk=row.pk).applied_at)
            if applied:
                break
            await asyncio.sleep(.005)
        stop.set()
        await task
        assert applied is not None
        assert len(attempts) == 2
        assert await runtime._admitted(f.texts) == ['Useful answer']
    asyncio.run(scenario())


def test_forcing_running_message_releases_its_inference_slot(decision_fixture, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from twicc.core.services.timeline_decisions import force_message
    _, now, request = decision_fixture
    key = admit(request, now)
    TimelineDecision.objects.filter(pk=key).update(not_before=now)
    monkeypatch.setattr(runtime, '_source_ready', lambda row: True)
    monkeypatch.setattr(runtime, 'build_decision_context', lambda request: asyncio.sleep(0, result=request))
    async def scenario():
        entered, canceled = asyncio.Event(), asyncio.Event()
        async def classify(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
        monkeypatch.setattr(runtime, 'classify', classify)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.start_timeline_decision_task(stop))
        await asyncio.wait_for(entered.wait(), 1)
        await runtime._admitted(force_message, decision_id=key, reason='final_message')
        await asyncio.wait_for(canceled.wait(), 1)
        stop.set()
        await task
    asyncio.run(scenario())


def test_deadline_batches_continue_immediately_and_failed_batch_backs_off(decision_fixture, monkeypatch):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from twicc import timeline_decision_task as runtime
    session, now, request = decision_fixture
    current = request.expires_at
    monkeypatch.setattr(runtime.timezone, 'now', lambda: current)
    TimelineDecision.objects.bulk_create([TimelineDecision(id=uuid4(), session=session, provider=session.provider,
        kind='message', identity_key=str(index), status='pending', occurred_at=now, not_before=now,
        expires_at=current, payload=request.payload, storage_incarnation=session.storage_incarnation,
        history_epoch=session.history_epoch) for index in range(101)])
    delays = []
    async def wait(event, seconds):
        delays.append(seconds)
    monkeypatch.setattr(runtime, '_wait', wait)
    async def scenario():
        worker = runtime.DecisionRuntime()
        await worker.deadline_cycle()
        assert delays == [0]
        assert await runtime._admitted(lambda: TimelineDecision.objects.filter(status='accepted').count()) == 100
        await worker.deadline_cycle()
        assert delays[-1] is None
        await runtime._admitted(lambda: TimelineDecision.objects.update(status='pending', applied_at=None))
        monkeypatch.setattr(worker, 'apply', AsyncMock(return_value=False))
        await worker.deadline_cycle()
        assert delays[-1] == 0  # The other row still receives its original deadline.
        await worker.deadline_cycle()
        assert 0 < delays[-1] <= runtime.RETRY_SECONDS
        await worker.stop()
    asyncio.run(scenario())


def test_unapplied_full_effect_batch_keeps_retry_timer(decision_fixture, monkeypatch):
    from uuid import uuid4
    from twicc import timeline_decision_task as runtime
    session, now, request = decision_fixture
    TimelineDecision.objects.bulk_create([TimelineDecision(id=uuid4(), session=session, provider=session.provider,
        kind='message', identity_key=str(index), status='accepted', occurred_at=now, not_before=now,
        expires_at=request.expires_at, payload=request.payload, resolved_at=now) for index in range(100)])
    delays = []
    async def wait(event, seconds):
        delays.append(seconds)
    monkeypatch.setattr(runtime, '_wait', wait)
    monkeypatch.setattr(runtime, '_retry_effect', lambda *args: None)
    async def scenario():
        worker = runtime.DecisionRuntime()
        await worker.schedule_cycle()
        assert delays[-1] == 0
        await worker.schedule_cycle()
        assert delays[-1] == runtime.RETRY_SECONDS
        await worker.stop()
    asyncio.run(scenario())


def test_repeated_shutdown_cancellation_still_joins_then_drains(monkeypatch):
    from twicc import timeline_decision_task as runtime
    async def scenario():
        worker = runtime.DecisionRuntime()
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        order = []
        async def inference():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                order.append('joined')
        async def drain():
            order.append('drained')
        monkeypatch.setattr(runtime, 'drain_classifier_calls', drain)
        worker.workers['a'] = asyncio.create_task(inference())
        await started.wait()
        stopping = asyncio.create_task(worker.stop())
        await cleaning.wait()
        stopping.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(stopping, return_exceptions=True)
        assert order == ['joined', 'drained']
    asyncio.run(scenario())
