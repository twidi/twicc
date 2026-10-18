"""Published statistics reach clients before durable queue acknowledgement."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from channels.layers import get_channel_layer


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('cost', [None, Decimal('2.250000')])
def test_batch_notifies_published_values_and_activity(monkeypatch, cost):
    from twicc.core.models import Project, Session, SessionItem, StatisticsQueueEntry
    from twicc.providers import db_writer
    from twicc.providers.statistics_queue import capture_queue
    from twicc.providers.statistics_runtime import StatisticsConsumer

    now = datetime(2026, 10, 14, tzinfo=UTC)
    project = Project.objects.create(id='notify')
    root = Session.objects.create(id='root', project=project, provider='claude_code', file_path='root',
        created_at=now, last_updated_at=now, user_message_count=1)
    child = Session.objects.create(id='child', project=project, provider='claude_code', file_path='child',
        parent_session=root, type='subagent', created_at=now, last_updated_at=now)
    SessionItem.objects.create(session=root, line_num=1, timestamp=now, kind='user_message')
    SessionItem.objects.create(session=child, line_num=1, cost=cost, timestamp=now)
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    batch = capture_queue()

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)
    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add('updates', channel)
        consumer = StatisticsConsumer()
        try:
            await consumer.run_batch(batch)
            messages = []
            while True:
                try:
                    messages.append((await asyncio.wait_for(layer.receive(channel), .05))['data'])
                except TimeoutError:
                    break
            return messages
        finally:
            await consumer.close()
            await layer.group_discard('updates', channel)
    messages = asyncio.run(scenario())
    by_type = {message['type']: message for message in messages}
    assert 'activity_updated' in by_type
    assert by_type['statistics_updated'] == {'type': 'statistics_updated', 'root_ids': ['root'], 'project_ids': ['notify']}
    assert by_type['session_updated']['session']['id'] == 'root'
    assert by_type['project_updated']['project']['id'] == 'notify'
    assert len([m for m in messages if m['type'] == 'session_updated']) == 1
    root.refresh_from_db()
    child.refresh_from_db()
    project.refresh_from_db()
    expected = float(cost) if cost is not None else None
    assert by_type['session_updated']['session']['total_cost'] == expected
    assert by_type['session_updated']['session']['subagents_cost'] == expected
    assert by_type['project_updated']['project']['total_cost'] == expected
    assert child.total_cost == cost
    assert project.total_cost == cost
    assert root.total_cost == cost
    assert not StatisticsQueueEntry.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_workflow_notification_preserves_envelope_and_failure_retains_queue(monkeypatch):
    from twicc.core.models import Project, Session, Workflow, StatisticsQueueEntry
    from twicc.providers import db_writer, statistics_publication
    from twicc.providers.statistics_queue import capture_queue
    from twicc.providers.statistics_runtime import StatisticsConsumer
    project = Project.objects.create(id='workflow-notify')
    root = Session.objects.create(id='workflow-owner', project=project, provider='claude_code')
    envelope = '{"workflowProgress": [], "custom": "unchanged"}'
    workflow = Workflow.objects.create(session=root, run_id='run', raw_json=envelope)
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    batch = capture_queue()

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)
    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)
    original = statistics_publication.notify_statistics_published

    async def notify(*args):
        assert await asyncio.to_thread(StatisticsQueueEntry.objects.exists)
        await original(*args)
        raise RuntimeError('notification interrupted')
    monkeypatch.setattr(statistics_publication, 'notify_statistics_published', notify)

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add('updates', channel)
        consumer = StatisticsConsumer()
        try:
            with pytest.raises(RuntimeError, match='notification interrupted'):
                await consumer.run_batch(batch)
            messages = []
            while True:
                try:
                    messages.append((await asyncio.wait_for(layer.receive(channel), .05))['data'])
                except TimeoutError:
                    break
            assert {'type': 'workflow_changed', 'session_id': root.id} in messages
        finally:
            await consumer.close()
            await layer.group_discard('updates', channel)
    asyncio.run(scenario())
    workflow.refresh_from_db()
    assert workflow.raw_json == envelope
    assert workflow.cost == Decimal(0)
    assert StatisticsQueueEntry.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_startup_consumes_existing_queue_after_provider_initial_sync_failure(monkeypatch, settings):
    # The consumer the writer starts drains the queue left at boot: the test needs the autostart.
    settings.TWICC_STATISTICS_CONSUMER_AUTOSTART = True
    from types import SimpleNamespace
    from twicc import orchestrator
    from twicc.core.models import Project, Session, StatisticsQueueEntry
    from twicc.providers import db_writer, statistics_runtime
    project = Project.objects.create(id='startup-notify')
    root = Session.objects.create(id='startup-root', project=project, provider='codex')
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    monkeypatch.setattr(orchestrator, 'get_orchestrator_registry', lambda: {
        'claude_code': SimpleNamespace(initial_sync_succeeded=False),
        'codex': SimpleNamespace(initial_sync_succeeded=True),
    })
    monkeypatch.setattr(statistics_runtime, 'COLLECTION_SECONDS', 0)

    async def scenario():
        db_writer.start_db_writer()
        try:
            async with asyncio.timeout(3):
                while await asyncio.to_thread(StatisticsQueueEntry.objects.exists):
                    await asyncio.sleep(.01)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
    assert not StatisticsQueueEntry.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_statistics_notification_serialization_stays_off_event_loop(monkeypatch):
    from threading import current_thread, main_thread
    from twicc.core import serializers
    from twicc.core.models import Project, Session
    from twicc.providers.statistics_publication import notify_statistics_published
    project = Project.objects.create(id='notification-thread')
    root = Session.objects.create(id='notification-thread-root', project=project,
        provider='codex', user_message_count=1)
    threads = []
    for name in ('serialize_session', 'serialize_project'):
        original = getattr(serializers, name)
        def serialize(value, original=original):
            threads.append(current_thread() is main_thread())
            return original(value)
        monkeypatch.setattr(serializers, name, serialize)
    asyncio.run(notify_statistics_published((root.id,), (project.id,), ()))
    assert threads == [False, False]
