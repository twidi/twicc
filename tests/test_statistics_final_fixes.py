"""Surviving queue work and workflow responses converge after publication."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import orjson
import pytest
from channels.layers import get_channel_layer

from tests.statistics_helpers import drain_statistics
from twicc.core.models import DailyActivity, Project, Session, SessionItem, StatisticsQueueEntry, Workflow
from twicc.providers import db_writer, statistics_publication, statistics_runtime
from twicc.providers.statistics_queue import capture_queue

pytestmark = pytest.mark.django_db(transaction=True)


def queued_roots():
    project = Project.objects.create(id='final-fixes')
    now = datetime(2026, 10, 14, tzinfo=UTC)
    healthy = Session.objects.create(id='healthy', file_path='healthy', project=project, provider='codex',
        created_at=now, last_updated_at=now, user_message_count=1)
    SessionItem.objects.create(session=healthy, line_num=1, cost=Decimal(7),
        timestamp=now, kind='user_message')
    removed = Session.objects.create(id='removed', file_path='removed', project=project, provider='codex')
    StatisticsQueueEntry.objects.create(root_session_id=removed.id)
    StatisticsQueueEntry.objects.create(root_session_id=healthy.id)
    db_writer._apply_delete_sessions_payload(db_writer.DeleteSessionsPayload(
        provider='codex', session_ids=[removed.id]))
    return healthy


def test_guardian_deletion_does_not_block_startup_drain(monkeypatch):
    healthy = queued_roots()
    monkeypatch.setattr(statistics_runtime, 'COLLECTION_SECONDS', 0)

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)

    async def scenario():
        consumer = statistics_runtime.StatisticsConsumer()
        consumer.start()
        try:
            async with asyncio.timeout(3):
                while await asyncio.to_thread(StatisticsQueueEntry.objects.exists):
                    await asyncio.sleep(.01)
        finally:
            await consumer.close()

    asyncio.run(scenario())
    healthy.refresh_from_db()
    assert healthy.total_cost == Decimal(7)
    assert DailyActivity.objects.get(project_id=healthy.project_id).cost == Decimal(7)
    assert not StatisticsQueueEntry.objects.exists()


def test_absent_root_keeps_boundary_and_surviving_failure(monkeypatch):
    queued_roots()
    batch = capture_queue()
    later = StatisticsQueueEntry.objects.create(root_session_id='healthy')
    original = statistics_publication.publish_daily

    async def fail(*args):
        raise RuntimeError('surviving daily failure')

    monkeypatch.setattr(statistics_publication, 'publish_daily', fail)
    with pytest.raises(RuntimeError, match='surviving daily failure'):
        drain_statistics(monkeypatch)
    assert StatisticsQueueEntry.objects.count() == 3
    monkeypatch.setattr(statistics_publication, 'publish_daily', original)

    async def scenario():
        consumer = statistics_runtime.StatisticsConsumer()
        try:
            await consumer.run_batch(batch)
        finally:
            await consumer.close()

    asyncio.run(scenario())
    assert list(StatisticsQueueEntry.objects.values_list('id', flat=True)) == [later.id]


@pytest.mark.parametrize('synthetic', [False, True])
def test_workflow_changed_refresh_overlays_published_agent_costs(monkeypatch, capsys, synthetic):
    from twicc.cli.session import workflow as show_workflow
    from twicc.views import session_workflows

    project = Project.objects.create(id='workflow-final')
    root = Session.objects.create(id='root', file_path='root', project=project, provider='claude_code',
        created_at=datetime.now(UTC), user_message_count=1)
    for agent_id, cost in [('assigned', Decimal(7)), ('unassigned', Decimal(3)), ('null', None)]:
        agent = Session.objects.create(id=f'run:{agent_id}', file_path=agent_id, project=project, provider='claude_code',
            parent_session=root, type='subagent', last_updated_at=datetime.now(UTC))
        SessionItem.objects.create(session=agent, line_num=1, cost=cost)
    envelope = orjson.dumps({'runId': 'run', 'synthetic': synthetic, 'status': 'completed',
        'phases': [{'title': 'Phase'}], 'workflowProgress': [
            {'type': 'workflow_agent', 'agentId': 'assigned', 'phaseIndex': 1, 'cost': 1},
            {'type': 'workflow_agent', 'agentId': 'unassigned'},
            {'type': 'workflow_agent', 'agentId': 'null', 'cost': 9},
            {'type': 'workflow_agent', 'agentId': 'missing', 'cost': 8},
        ]}).decode()
    workflow = Workflow.objects.create(session=root, run_id='run', raw_json=envelope)
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    batch = capture_queue()

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add('updates', channel)
        consumer = statistics_runtime.StatisticsConsumer()
        try:
            await consumer.run_batch(batch)
            async with asyncio.timeout(3):
                while (await layer.receive(channel))['data']['type'] != 'workflow_changed':
                    pass
            # Real async REST call also rejects synchronous ORM access on the loop.
            response = await session_workflows(None, project.id, root.id)
            return orjson.loads(response.content)[0]
        finally:
            await consumer.close()
            await layer.group_discard('updates', channel)

    response = asyncio.run(scenario())
    workflow.refresh_from_db()
    assert response['cost'] == 10
    assert response['phases_cost'] == {'1': 7.0}
    expected = [7.0, 3.0, None, None]
    assert [entry.get('cost') for entry in response['raw']['workflowProgress']] == expected
    show_workflow(root.id, 'run')
    cli = orjson.loads(capsys.readouterr().out)
    assert [entry.get('cost') for entry in cli['workflowProgress']] == expected
    # The Unassigned display sums these exact unphased numeric response values.
    assert sum(entry['cost'] for entry in response['raw']['workflowProgress']
               if entry.get('phaseIndex') != 1 and entry.get('cost') is not None) == 3
    workflow.refresh_from_db()
    assert workflow.raw_json == envelope
