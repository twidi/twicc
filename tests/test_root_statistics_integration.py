"""Source completion, partial publication, recovery, and exact persisted outputs."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import orjson
import pytest

from twicc.core.models import AgentLink, DailyActivity, Project, Session, SessionItem, StatisticsQueueEntry, WeeklyActivity, Workflow
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.live_aggregates import enqueue_sessions
from tests.statistics_helpers import drain_statistics

# The lines are dated 2026-10-14: freeze the clock one day later so the content storage sees hot sessions.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("frozen_now"),
    pytest.mark.frozen_now_at(datetime(2026, 10, 15, 12, tzinfo=UTC)),
]
STAMP = datetime(2026, 10, 14, 12, tzinfo=UTC)


def source(sid, project, provider, parent=None, cost='1.125001'):
    session = Session.objects.create(id=sid, project=project, provider=provider,
        parent_session=parent, type='subagent' if parent else 'session', file_path=sid,
        created_at=STAMP, last_updated_at=STAMP, user_message_count=1 if parent is None else 0)
    SessionItem.objects.create(session=session, line_num=1, timestamp=STAMP,
        kind='assistant_message', cost=Decimal(cost))
    if parent is None:
        SessionItem.objects.create(session=session, line_num=2, timestamp=STAMP, kind='user_message')
    return session


def complete(session):
    return BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        'session_fields': {'compute_version': 999}}).outcome


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
def test_partial_live_tree_then_restart_with_current_descendants(provider):
    project = Project.objects.create(id='integration')
    root = source('root', project, provider)
    child = source('child', project, provider, root, '2.000002')
    AgentLink.objects.create(session=root, agent_id=child.id, tool_use_line_num=1, tool_use_id='child')
    from django.db import transaction
    with transaction.atomic():
        enqueue_sessions([root.id, child.id])
    assert StatisticsQueueEntry.objects.count() == 1
    drain_statistics()
    root.refresh_from_db()
    assert root.compute_version is None
    assert root.total_cost == Decimal('3.125003')
    assert complete(root) == 'superseded'
    assert complete(child) == 'applied'
    # Restart selection sees a current child and an outdated root.
    from twicc.providers.compute_selection import select_native_tree_source
    assert select_native_tree_source(provider, 999, cutoff=float('inf')) == root.id
    assert complete(root) == 'applied'
    assert StatisticsQueueEntry.objects.count() == 1
    drain_statistics()
    root.refresh_from_db(); child.refresh_from_db(); project.refresh_from_db()
    assert (root.self_cost, root.subagents_cost, root.total_cost) == (
        Decimal('1.125001'), Decimal('2.000002'), Decimal('3.125003'))
    assert child.total_cost == Decimal('2.000002')
    assert project.total_cost == root.total_cost
    for model in (DailyActivity, WeeklyActivity):
        rows = list(model.objects.values_list('project_id', 'cost', 'user_message_count', 'session_count'))
        assert sorted(rows, key=lambda r: r[0] or '') == [
            (None, Decimal('3.125003'), 1, 1), ('integration', Decimal('3.125003'), 1, 1)]
    assert not StatisticsQueueEntry.objects.exists()


def test_workflow_regions_cross_linked_nonworkflow_intermediate():
    project = Project.objects.create(id='workflow-integration')
    root = source('root', project, 'claude_code', cost='1')
    a = source('run:a', project, root.provider, root, '2')
    bridge = source('bridge', project, root.provider, root, '3')
    b = source('run:b', project, root.provider, root, '4')
    for owner, child in ((root, a), (a, bridge), (bridge, b)):
        AgentLink.objects.create(session=owner, agent_id=child.id, tool_use_line_num=1, tool_use_id=child.id)
    workflow = Workflow.objects.create(session=root, run_id='run', raw_json=orjson.dumps({'workflowProgress': [
        {'type': 'workflow_agent', 'agentId': 'a', 'phaseIndex': 1},
        {'type': 'workflow_agent', 'agentId': 'b', 'phaseIndex': 2},
    ]}).decode())
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    drain_statistics()
    workflow.refresh_from_db(); root.refresh_from_db()
    assert root.total_cost == Decimal(10)
    assert workflow.cost == Decimal(9)
    assert workflow.phases_cost == {'1': 5.0, '2': 4.0}


def test_interrupted_partial_publication_retains_new_arrival_and_self_recovers(monkeypatch, settings):
    # The writer-started consumer retries the retained queue: the test needs the autostart.
    settings.TWICC_STATISTICS_CONSUMER_AUTOSTART = True
    from twicc.providers import db_writer, statistics_publication, statistics_runtime
    project = Project.objects.create(id='retry-integration')
    root = source('root', project, 'codex')
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    monkeypatch.setattr(statistics_runtime, 'COLLECTION_SECONDS', .001)
    monkeypatch.setattr(statistics_runtime, 'RETRY_SECONDS', .001)
    original = statistics_publication.publish_project_cost
    attempts = []

    async def fail_once(*args):
        attempts.append(1)
        if len(attempts) == 1:
            # A source transaction arrives after tree publication. No wakeup is
            # sent here; the retained queue itself must drive the retry.
            await asyncio.to_thread(StatisticsQueueEntry.objects.create, root_session_id=root.id)
            raise RuntimeError('interrupt after session publication')
        return await original(*args)
    monkeypatch.setattr(statistics_publication, 'publish_project_cost', fail_once)

    async def scenario():
        db_writer.start_db_writer()
        try:
            async with asyncio.timeout(5):
                while await asyncio.to_thread(StatisticsQueueEntry.objects.exists):
                    await asyncio.sleep(.01)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
    project.refresh_from_db()
    assert project.total_cost == Decimal('1.125001')
    assert len(attempts) == 2


def test_equal_timestamp_accepts_cached_own_cost_until_source_completion():
    project = Project.objects.create(id='equal-timestamp')
    root = source('root', project, 'codex', cost='1')
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    drain_statistics()
    SessionItem.objects.filter(session=root, line_num=1).update(cost=Decimal(2))
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    drain_statistics()
    root.refresh_from_db()
    # The reviewed cache contract intentionally cannot detect equal timestamps.
    assert root.total_cost == Decimal(1)
    assert root.items_cost_computed_through_at == STAMP
    # Actual background source publication invalidates the marker explicitly.
    assert complete(root) == 'applied'
    drain_statistics()
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)
