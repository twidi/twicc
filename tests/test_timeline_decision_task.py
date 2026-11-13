"""Offline worker scheduling, original deadlines, and retry obligations."""

import asyncio

import pytest
from django.utils import timezone

from tests.test_timeline_decisions import decision_fixture, admit
from tests.test_timeline_messages import live_session, content_row_factory
from tests.test_completion_publication import bridge, open_candidate
from twicc.agent.semantic_types import Classification
from twicc.core.models import TimelineDecision

# Export imported fixtures for pytest discovery.
__all__ = ['bridge', 'content_row_factory', 'decision_fixture', 'live_session']


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('already_published', [False, True])
def test_final_read_requires_accepted_ending_after_shell_rejection(
    bridge, live_session, monkeypatch, already_published,
):
    """A rejected progress ending cannot authorize its message for final speech."""
    from datetime import timedelta

    from tests.test_completion_integration import drain
    from twicc import timeline_decision_task as runtime
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.agent.states import AgentState
    from twicc.core.enums import Provider
    from twicc.core.models import LogicalTurn, TimelineEvent
    from twicc.core.services.timeline_final_messages import resolve_final_messages

    manager, agent = bridge
    f = live_session
    f.append(f.message('user', 'Implement the fix and run the tests.', user=True),
             f.message('progress', 'The tests are still running.'))
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch
    shells = [({'key': 'tests', 'owner': 'root', 'command': 'uv run pytest',
        'description': 'Run the tests.', 'started_at': None, 'start_line': None,
        'owner_ref': 'root', 'generation': 1, 'output_path': None, 'tool_id': 'tests'},)]
    agent.completion_shell_snapshot = lambda: shells[0]
    current = [timezone.now() + timedelta(seconds=6)]
    monkeypatch.setattr(runtime.timezone, 'now', lambda: current[0])
    monkeypatch.setattr(runtime, '_live_ending', lambda row: (manager, agent)
        if agent._completion_pending_ending is not None
        and agent._completion_pending_ending.id == str(row.pk) else None)

    async def classify(request, **kwargs):
        if request.kind == 'ending':
            assert request.payload['previous_messages'][-1]['text'] == 'The tests are still running.'
            return Classification('NO', 'The test result is still required.'), 'provider'
        return Classification('YES', 'The message addresses the request.'), 'provider'

    monkeypatch.setattr(runtime, 'classify', classify)

    def native(identity):
        return NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item',
                                identity, None if f.provider == 'claude_code' else 'native-turn')

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), 'time', lambda: clock[0])
        worker = runtime.DecisionRuntime()
        try:
            # Exercise real message classification and publication for Claude.
            row, _ = await runtime._claim_ready(current[0], set(), ())
            if row is not None:
                await worker.worker(row)
            progress = await asyncio.to_thread(TimelineEvent.objects.get,
                kind='assistant_message.sent', session_id=f.session.pk)
            open_candidate(manager, agent)
            agent.completion_controller.provider_ended(0, final_ref=native('progress'))
            ticket = agent.completion_controller.candidate()
            clock[0] = ticket.deadline
            await manager._confirm_completion(agent, ticket)
            context = agent._completion_pending_ending
            pending = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
            assert pending.status == 'pending'
            assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind='turn.ended').exists())
            assert (await asyncio.to_thread(resolve_final_messages, [progress.pk]))['items'][0]['message'] is None

            row, _ = await runtime._claim_ready(current[0], set(), ())
            assert row is not None and row.kind == 'ending'
            await worker.worker(row)
            await asyncio.to_thread(pending.refresh_from_db)
            assert pending.status == 'rejected'
            assert agent.state == AgentState.ASSISTANT_TURN
            turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
            assert turn.outcome is None
            assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind='turn.ended').exists())

            # The same logical turn continues after NO. The exact final can
            # already exist in the feed, or publication can wait for acceptance.
            agent.completion_controller.activity(clock[0])
            await drain(agent)
            await asyncio.to_thread(f.append, f.message('final', '**All tests pass.**'), capture=already_published)
            current[0] += timedelta(seconds=6)
            row, _ = await runtime._claim_ready(current[0], set(), ())
            if row is not None:
                assert row.kind == 'message'
                await worker.worker(row)
            published = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
                session_id=f.session.pk, kind='assistant_message.sent').order_by('pk')))
            assert len(published) == (2 if already_published else 1)
            final_id = published[-1].pk if already_published else None
            shells[0] = ()
            agent.completion_controller.provider_ended(clock[0], final_ref=native('final'))
            ticket = agent.completion_controller.candidate()
            clock[0] = ticket.deadline
            await manager._confirm_completion(agent, ticket)
            await drain(agent)
            assert agent.state == AgentState.USER_TURN
            await asyncio.to_thread(turn.refresh_from_db)
            assert turn.outcome == 'normal'
            assert await asyncio.to_thread(lambda: LogicalTurn.objects.filter(session_id=f.session.pk).count()) == 1
            ending = await asyncio.to_thread(TimelineEvent.objects.get, kind='turn.ended')
            result = (await asyncio.to_thread(resolve_final_messages, [progress.pk, ending.pk]))['items']
            assert result[0]['state'] == 'unavailable' and result[0]['message'] is None
            assert result[1]['state'] == 'ready'
            assert result[1]['message']['data']['text'] == '**All tests pass.**'
            assert result[1]['message']['id'] != progress.pk
            if final_id is not None:
                assert result[1]['message']['id'] == final_id
            assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(
                session_id=f.session.pk, kind='assistant_message.sent').count()) == 2
        finally:
            await worker.stop()
            agent._cancel_completion_timer()

    asyncio.run(scenario())


@pytest.mark.parametrize('admission_delay', [0, 60])
@pytest.mark.parametrize('model_timeout', [False, True])
def test_shell_decision_waits_for_indexing_before_starting_inference_deadline(
    bridge, live_session, monkeypatch, admission_delay, model_timeout,
):
    from twicc import timeline_decision_task as runtime
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.agent.states import AgentState
    from twicc.core.enums import Provider
    from twicc.core.models import LogicalTurn, TimelineEvent
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    from twicc.providers import db_writer
    from twicc.timeline_classification import build_classifier_payload
    from datetime import timedelta

    manager, agent = bridge
    f = live_session
    f.append(f.message('user', 'Implement the fix and run all tests.', user=True),
             f.message('older', 'Starting the work.'), capture=False)
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch
    agent.completion_shell_snapshot = lambda: ({
        'key': 'tests', 'owner': 'subagent', 'command': 'uv run pytest',
        'description': 'Run the whole test suite.', 'started_at': None, 'start_line': None,
        'owner_ref': 'implementer', 'generation': 1, 'output_path': None, 'tool_id': 'test-tool',
    },)
    current = [timezone.now()]
    monkeypatch.setattr(runtime.timezone, 'now', lambda: current[0])

    def live(row):
        context = agent._completion_pending_ending
        return (manager, agent) if context is not None and context.id == str(row.pk) else None

    monkeypatch.setattr(runtime, '_live_ending', live)
    model_started = asyncio.Event()

    async def classify(request, **kwargs):
        payload = build_classifier_payload(request)
        assert payload['latest_user_request']['text'] == 'Implement the fix and run all tests.'
        assert payload['latest_assistant_response'] == 'The fix is ready. Waiting for the full test suite.'
        assert payload['candidate']['shells'][0]['description'] == 'Run the whole test suite.'
        assert request.expires_at > current[0]
        model_started.set()
        if model_timeout:
            await asyncio.Event().wait()
        return Classification('NO', 'The test result is still required.'), 'provider'

    monkeypatch.setattr(runtime, 'classify', classify)

    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item',
                                 'last', None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()

        async def block():
            entered.set()
            await release.wait()

        blocker = asyncio.create_task(db_writer.run_under_db_write_lock(block))
        await entered.wait()
        confirming = asyncio.create_task(manager._confirm_completion(agent, ticket))
        await asyncio.sleep(0)
        original_time = agent._completion_pending_ending.occurred_at
        current[0] += timedelta(seconds=admission_delay)
        release.set()
        await blocker
        await confirming
        context = agent._completion_pending_ending

        # Indexing and scheduling can both lag longer than an inference budget.
        current[0] += timedelta(seconds=60)
        row, _ = await runtime._claim_ready(current[0], set(), ())
        assert row is None  # An earlier indexed answer cannot replace this final.
        assert await runtime._admitted(runtime._due, current[0]) == []
        await asyncio.to_thread(f.append,
            f.message('last', 'The fix is ready. Waiting for the full test suite.'),
            f.message('future', 'Unrelated later message.'), capture=False)
        current[0] += timedelta(seconds=60)
        row, _ = await runtime._claim_ready(current[0], set(), ())
        assert row is not None
        worker = runtime.DecisionRuntime()
        try:
            running = asyncio.create_task(worker.worker(row))
            await asyncio.wait_for(model_started.wait(), 1)
            if model_timeout:
                current[0] = row.expires_at
                due = await runtime._admitted(runtime._due, current[0])
                assert [item.pk for item in due] == [row.pk]
                assert await worker.apply(row, fallback='timeout')
            await running
        finally:
            await worker.stop()
            agent._cancel_completion_timer()
        stored = await runtime._admitted(lambda: TimelineDecision.objects.get(pk=context.id))
        turn = await runtime._admitted(lambda: LogicalTurn.objects.get(pk=ticket.turn_id))
        assert stored.occurred_at == original_time
        answer = await runtime._admitted(resolve_logical_answer, turn, cursor=0)
        events = await runtime._admitted(lambda: list(TimelineEvent.objects.filter(
            session_id=f.session.pk, kind='turn.ended')))
        if model_timeout:
            assert (stored.status, stored.reason) == ('accepted', 'timeout')
            assert turn.outcome == 'normal' and turn.closed_at == original_time
            assert answer.status == 'replied'
            assert answer.message.text == 'The fix is ready. Waiting for the full test suite.'
            assert agent.state == AgentState.USER_TURN
            assert len(events) == 1 and events[0].occurred_at == original_time
        else:
            assert (stored.status, stored.reason) == ('rejected', 'The test result is still required.')
            assert turn.outcome is None
            assert answer.status == 'pending'
            assert agent.state == AgentState.ASSISTANT_TURN
            assert events == []

    asyncio.run(scenario())


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
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)
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
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)
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


def test_claimed_ending_arms_an_idle_deadline_scheduler(decision_fixture, monkeypatch):
    from tests.test_timeline_decisions import ending_request
    from twicc import timeline_decision_task as runtime

    _, now, _ = decision_fixture
    request, _ = ending_request(decision_fixture)
    request.payload['awaiting_inference'] = True
    key = admit(request, now)
    TimelineDecision.objects.filter(pk=key).update(not_before=now)
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)

    async def scenario():
        worker = runtime.DecisionRuntime()
        idle, armed = asyncio.Event(), asyncio.Event()
        original_wait = runtime._wait
        delays = []

        async def wait(event, seconds):
            if event is worker.deadline_wake:
                delays.append(seconds)
                (idle if seconds is None else armed).set()
            await original_wait(event, seconds)

        async def infer(row):
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, '_wait', wait)
        monkeypatch.setattr(worker, 'worker', infer)
        deadline_task = asyncio.create_task(worker.deadlines())
        schedule_task = None
        try:
            await asyncio.wait_for(idle.wait(), 1)
            schedule_task = asyncio.create_task(worker.schedule_cycle())
            await asyncio.wait_for(armed.wait(), 1)
            assert delays[0] is None
            assert 0 < delays[-1] <= 45
        finally:
            tasks = [task for task in (deadline_task, schedule_task) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await worker.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize('awaiting_inference', [None, False, True])
def test_expiry_skips_only_explicitly_unstarted_endings(decision_fixture, awaiting_inference):
    from tests.test_timeline_decisions import ending_request
    from twicc import timeline_decision_task as runtime

    _, now, _ = decision_fixture
    request, _ = ending_request(decision_fixture)
    if awaiting_inference is not None:
        request.payload['awaiting_inference'] = awaiting_inference
    key = admit(request, now)
    due = runtime._due(request.expires_at)
    if awaiting_inference is True:
        assert due == []
        assert runtime._next_deadline() is None
    else:
        assert [str(row.pk) for row in due] == [key]
        assert runtime._next_deadline() == request.expires_at


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


def test_timely_no_survives_delayed_writer_admission(decision_fixture, monkeypatch):
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
    assert (row.status, row.reason) == ('rejected', 'late')
    assert row.occurred_at == now


def test_fallback_and_ignored_late_result_are_distinguishable(decision_fixture, monkeypatch, caplog):
    import logging
    import orjson
    from twicc import timeline_decision_task as runtime
    _, now, request = decision_fixture
    key = admit(request, now)
    row = TimelineDecision.objects.get(pk=key)
    monkeypatch.setattr(logging.getLogger('twicc.core.services.timeline_decisions'), 'disabled', False)
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


def test_result_diagnostics_keep_a_bounded_duplicate_window(decision_fixture, monkeypatch, caplog):
    import logging
    from uuid import uuid4
    from twicc import timeline_decision_task as runtime

    _, now, request = decision_fixture
    rows = [TimelineDecision.objects.get(pk=admit(request._replace(id=str(uuid4())), now, identity=str(index)))
            for index in range(5)]
    monkeypatch.setattr(runtime, 'RESULT_CACHE_LIMIT', 3, raising=False)
    monkeypatch.setattr(logging.getLogger('twicc.core.services.timeline_decisions'), 'disabled', False)
    caplog.set_level(logging.DEBUG, logger='twicc')

    async def scenario():
        worker = runtime.DecisionRuntime()
        for row in rows:
            assert await worker.apply(row, fallback='timeout')
            await worker.apply(row, classification=Classification('NO', 'late'), route='provider')
        assert len(worker.seen_results) <= 3
        count = caplog.text.count('late_result')
        assert count == 5
        await worker.apply(rows[-1], classification=Classification('NO', 'late'), route='provider')
        assert caplog.text.count('late_result') == count
        await worker.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('finish', ['result', 'cancel', 'deadline'])
def test_runtime_releases_agent_callbacks_after_owned_work(bridge, monkeypatch, finish):
    import gc
    import weakref
    from uuid import uuid4
    from tests.test_completion_publication import Agent
    from tests.test_semantic_completion_integration import pending
    from twicc import timeline_decision_task as runtime
    from twicc.providers.helpers import AgentSettings

    manager, original = bridge
    agent = Agent(original.session_id, original.project_id, '/tmp', AgentSettings())
    agent.process_run = original.process_run
    manager._agents[agent.session_id] = agent
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)
    monkeypatch.setattr(runtime, '_live_ending', lambda row: (manager, manager._agents[row.session_id])
        if row.session_id in manager._agents and manager._agents[row.session_id]._completion_pending_ending else None)

    async def scenario(agent):
        _, context = await pending(manager, agent)
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        worker = runtime.DecisionRuntime()
        previous = agent._completion_activity  # A real bound callback retains its agent.
        agent._completion_cancel_inference = previous
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def infer(row):
            entered.set()
            try:
                if finish != 'result':
                    await asyncio.Event().wait()
                return Classification('NO', 'blocked'), 'provider'
            finally:
                if finish != 'result':
                    cleanup.set()
                    await release.wait()
        monkeypatch.setattr(worker, 'infer', infer)
        task = asyncio.create_task(worker.worker(row))
        await entered.wait()
        if finish != 'result':
            if finish == 'deadline':
                # A concurrent admitted fallback owns the same callback while
                # the worker still owns inference cleanup.
                await worker.apply(row, fallback='timeout')
            else:
                agent._completion_cancel_inference(str(row.pk))
            await cleanup.wait()
            assert agent in worker.callbacks
            assert agent._completion_cancel_inference == worker.cancel_call
            # Message work for the same session does not release ending work.
            message = await runtime._admitted(TimelineDecision.objects.create,
                id=uuid4(), session_id=agent.session_id, provider=agent.provider.value, kind='message', identity_key='concurrent',
                occurred_at=row.occurred_at, not_before=row.not_before, expires_at=row.expires_at, payload={})
            await worker.apply(message, classification=Classification('NO', 'routine'), route='provider')
            assert agent in worker.callbacks
            release.set()
        await task
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert agent._completion_cancel_inference == previous
        assert not worker.callbacks
        assert not worker.calls
        assert not worker.workers
        manager._agents.pop(agent.session_id)
        return worker

    reference = weakref.ref(agent)
    worker = asyncio.run(scenario(agent))
    del agent
    gc.collect()
    assert reference() is None
    assert not worker.callbacks


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
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)
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
    monkeypatch.setattr(runtime, '_source_ready', lambda row, prepared=None: True)
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
