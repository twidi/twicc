"""Transcript preparation must let the watcher acquire writer admission."""

import asyncio

import pytest

from tests.test_completion_publication import bridge, open_candidate
from tests.test_timeline_messages import content_row_factory, live_session
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.semantic_types import Classification
from twicc.core.enums import Provider

__all__ = ['bridge', 'content_row_factory', 'live_session']
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('block_context', [False, True])
def test_shell_context_reads_release_writer_for_indexing(bridge, live_session, monkeypatch, block_context):
    from threading import Event
    from twicc import timeline_decision_task as runtime
    from twicc.core.content_storage import readers
    from twicc.providers import db_writer

    manager, agent = bridge
    f = live_session
    f.append(f.message('user', 'Run the full suite.', user=True),
             f.message('final', 'The suite is still running.'), capture=False)
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch
    agent.completion_shell_snapshot = lambda: ({
        'key': 'tests', 'owner': 'root', 'command': 'uv run pytest',
        'description': 'Run the suite', 'started_at': None, 'start_line': None,
        'owner_ref': 'root', 'generation': 1, 'output_path': None, 'tool_id': 'tool',
    },)
    monkeypatch.setattr(runtime, '_live_ending', lambda row: (manager, agent)
        if agent._completion_pending_ending is not None
        and agent._completion_pending_ending.id == str(row.pk) else None)
    original_decode = readers.decode_content
    observations = []
    preparing, release_read = Event(), Event()
    if block_context:
        from twicc.core.services import timeline_messages
        capture = timeline_messages.capture_message_context

        def slow_context(*args, **kwargs):
            preparing.set()
            assert release_read.wait(3), 'The test must release preparation.'
            return capture(*args, **kwargs)

        monkeypatch.setattr(timeline_messages, 'capture_message_context', slow_context)

    def decode(content):
        observations.append(db_writer._holds_db_write_lease())
        return original_decode(content)

    async def classify(request, **kwargs):
        assert request.payload['previous_messages'][-1]['text'] == 'The suite is still running.'
        return Classification('NO', 'Tests must finish.'), 'provider'

    async def no_wait(*args):
        return None

    monkeypatch.setattr(runtime, 'classify', classify)
    monkeypatch.setattr(runtime, '_wait', no_wait)

    async def scenario():
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        open_candidate(manager, agent)
        agent.completion_controller.provider_ended(0, final_ref=NativeMessageRef(
            'claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'final',
            None if f.provider == 'claude_code' else 'native-turn'))
        await manager._confirm_completion(agent, agent.completion_controller.candidate())
        monkeypatch.setattr(readers, 'decode_content', decode)
        worker = runtime.DecisionRuntime()
        try:
            if block_context:
                scheduling = asyncio.create_task(worker.schedule_cycle())
                assert await asyncio.to_thread(preparing.wait, 2)
                indexing = asyncio.create_task(db_writer.run_under_db_write_lock(lambda: asyncio.to_thread(
                    f.append, f.message('later', 'Unrelated later source.'), capture=False)))
                try:
                    # A stalled source reader cannot prevent real ingestion.
                    await asyncio.wait_for(asyncio.shield(indexing), 1)
                finally:
                    release_read.set()
                await indexing
                await scheduling
            else:
                await worker.schedule_cycle()
            await asyncio.gather(*tuple(worker.workers.values()))
        finally:
            await worker.stop()
            agent._cancel_completion_timer()

    asyncio.run(scenario())
    assert observations, 'Exercise real transcript decompression.'
    if not block_context:
        assert not any(observations), 'Source preparation must not own writer admission.'


def test_new_candidate_does_not_read_previous_context_under_ingestion_lock(live_session, monkeypatch):
    from twicc.core.content_storage import readers
    from twicc.core.models import TimelineDecision
    from twicc.providers import db_writer

    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Only Claude messages need classification.')
    f.append(f.message('user', 'Implement the fix.', user=True), capture=False)
    original_decode = readers.decode_content
    old_ids = set(f.session.items.values_list('current_content_id', flat=True))
    old_reads_under_lock = []

    def decode(content):
        if content.pk in old_ids and db_writer._holds_db_write_lease():
            import traceback
            if any(frame.name == 'capture_message_context' for frame in traceback.extract_stack()):
                old_reads_under_lock.append(content.pk)
        return original_decode(content)

    monkeypatch.setattr(readers, 'decode_content', decode)

    async def scenario():
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        await db_writer.run_under_db_write_lock(lambda: asyncio.to_thread(
            f.append, f.message('answer', 'The fix is ready.')))

    asyncio.run(scenario())
    assert TimelineDecision.objects.filter(kind='message').count() == 1
    assert old_reads_under_lock == []


def test_recovery_retries_only_missing_references_after_new_indexing(live_session, monkeypatch):
    from twicc import timeline_decision_task as runtime
    from twicc.core.content_storage import readers
    from twicc.core.models import TimelineDecision
    from twicc.core.services.session_timeline import record_event_sync
    from twicc.providers import db_writer

    f = live_session
    f.append(f.message('old', 'Old source text.'), capture=False)
    old_ids = set(f.session.items.values_list('current_content_id', flat=True))
    native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item',
                             'late', None if f.provider == 'claude_code' else 'native-turn')
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent',
                      data={'message_refs': [native._asdict()]})
    decoded = []
    original_decode = readers.decode_content

    def decode(content):
        decoded.append(content.pk)
        assert not db_writer._holds_db_write_lease()
        return original_decode(content)

    async def scenario():
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        worker = runtime.DecisionRuntime()
        try:
            await worker.recover_sources()
            assert f.session.id in worker.pending_sources
            monkeypatch.setattr(readers, 'decode_content', decode)
            await worker.recover_sources()
            assert decoded == []  # No new indexed data: no repeated search.
            await asyncio.to_thread(f.append, f.message('late', 'The delayed answer.', final=None), capture=False)
            decoded.clear()
            await worker.recover_sources()
            assert worker.pending_sources == {}
            assert not old_ids.intersection(decoded)
            if f.provider == 'claude_code':
                row = await asyncio.to_thread(TimelineDecision.objects.get, kind='message')
                assert row.payload['target'] == {'text': 'The delayed answer.'}
            else:
                assert await asyncio.to_thread(f.texts) == ['The delayed answer.']
        finally:
            await worker.stop()

    asyncio.run(scenario())


def test_recovery_does_not_resurrect_expired_decisions_on_later_pages(live_session, monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from twicc import timeline_decision_task as runtime
    from twicc.core.models import TimelineDecision
    from twicc.core.services.session_timeline import record_event_sync
    from twicc.providers import db_writer

    f = live_session
    f.append(f.message('old', 'A previously hidden message.'), capture=False)
    native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item',
                             'old', None if f.provider == 'claude_code' else 'native-turn')
    for _ in range(2):
        record_event_sync('turn.started', session_id=f.session.id, actor='agent', data={'message_refs': []})
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent',
        occurred_at=timezone.now() - timedelta(days=3), data={'message_refs': [native._asdict()]})
    monkeypatch.setattr(runtime, 'BATCH_SIZE', 2)

    async def scenario():
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        worker = runtime.DecisionRuntime()
        try:
            await worker.recover_sources()
            assert worker.source_events_pending
            await worker.recover_sources()
            assert worker.pending_sources == {}
        finally:
            await worker.stop()

    asyncio.run(scenario())
    assert f.texts() == []
    assert not TimelineDecision.objects.exists()


def test_unbound_source_recovery_rotates_past_missing_sessions(live_session, monkeypatch):
    from uuid import uuid4
    from django.utils import timezone
    from twicc import timeline_decision_task as runtime
    from twicc.core.models import LogicalTurn, Session
    from twicc.providers import db_writer

    f = live_session
    f.append(f.message('answer', 'An indexed answer.'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    native_turn = None if f.provider == 'claude_code' else 'native-turn'
    for index in range(2):
        session = Session.objects.create(id=f'missing-{index}', file_path=f'missing-{index}.jsonl',
                                         project=f.session.project, provider=f.provider)
        LogicalTurn.objects.create(id=uuid4(), session_id=session.id, provider=f.provider, run_id=uuid4(),
            ordinal=1, position=1, source_floor=0, opened_at=timezone.now(),
            first_ref=NativeMessageRef(kind, 'missing', native_turn)._asdict())
    turn = LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=0, opened_at=timezone.now(),
        first_ref=NativeMessageRef(kind, 'answer', native_turn)._asdict())
    monkeypatch.setattr(runtime, 'BATCH_SIZE', 2)

    async def scenario():
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        worker = runtime.DecisionRuntime()
        try:
            await worker.recover_sources()
            await asyncio.to_thread(turn.refresh_from_db)
            assert turn.storage_incarnation is None
            await worker.recover_sources()
            await asyncio.to_thread(turn.refresh_from_db)
            await asyncio.to_thread(f.session.refresh_from_db)
            assert turn.storage_incarnation == f.session.storage_incarnation
            assert turn.history_epoch == f.session.history_epoch
        finally:
            await worker.stop()

    asyncio.run(scenario())
