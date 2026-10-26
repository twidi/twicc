"""Selective own-cost reads and launcher hierarchy snapshots."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from twicc.core.models import AgentLink, Project, Session, SessionItem

# The lines are dated 2026-10-14: freeze the clock one day later so the content storage sees hot sessions.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("frozen_now"),
    pytest.mark.frozen_now_at(datetime(2026, 10, 15, tzinfo=UTC)),
]
NOW = datetime(2026, 10, 14, tzinfo=UTC)


@pytest.fixture
def source():
    project = Project.objects.create(id='cost-project')

    def create(sid, own='1', parent=None, marker=None, updated=NOW, cached=None):
        session = Session.objects.create(
            id=sid, project=project, provider='claude_code', file_path=f'/test/{sid}',
            parent_session_id=parent, type='subagent' if parent else 'session',
            last_updated_at=updated, items_cost_computed_through_at=marker, self_cost=cached,
            storage_incarnation=f'inc-{sid}',
        )
        if own is not None:
            SessionItem.objects.create(session=session, line_num=1, content='{}', cost=Decimal(own))
        return session
    return create


def prepare(*roots, force_cost_session_ids=()):
    from twicc.providers.statistics_prepare import prepare_tree_costs

    def run():
        try:
            with CaptureQueriesContext(connection) as queries:
                result = prepare_tree_costs(roots, force_cost_session_ids)
            return result, [q['sql'] for q in queries.captured_queries]
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()


def link(parent, child):
    AgentLink.objects.create(session_id=parent, agent_id=child, tool_use_line_num=1, tool_use_id=child)


def test_own_cost_sum_uses_partial_cost_index(source):
    source('root', '1.25')
    SessionItem.objects.create(session_id='root', line_num=2, content='{}', cost=None)
    result, queries = prepare('root')
    assert result.own_updates[0].self_cost == Decimal('1.25')
    cost_sql, = [sql for sql in queries if 'SUM(' in sql and 'core_sessionitem' in sql]
    with connection.cursor() as cursor:
        cursor.execute('EXPLAIN QUERY PLAN ' + cost_sql)
        plan = ' '.join(row[3] for row in cursor.fetchall())
    assert 'idx_item_cost_session' in plan


def test_only_changed_member_sums_items(source):
    source('root', marker=NOW, cached='1')
    for index in range(398):
        source(f'cached-{index}', parent='root', marker=NOW, cached='1')
    source('changed', '2', parent='root', marker=NOW, updated=NOW + timedelta(seconds=1), cached='1')
    result, queries = prepare('root')
    sums = [sql for sql in queries if 'SUM(' in sql and 'core_sessionitem' in sql]
    assert len(sums) == 1
    assert "'changed'" in sums[0]
    assert "'cached-" not in sums[0]
    assert [row.session_id for row in result.own_updates] == ['changed']
    assert {row.session_id: row.total_cost for row in result.session_costs}['root'] == Decimal(401)


def test_subtree_costs_include_nested_agents_once(source):
    source('root', '1')
    source('child', '2', parent='root')
    source('grandchild', '3', parent='root')
    link('root', 'child')
    link('child', 'grandchild')
    result, _ = prepare('root')
    costs = {row.session_id: row for row in result.session_costs}
    assert costs['child'].total_cost == Decimal(5)
    assert costs['root'].total_cost == Decimal(6)
    assert costs['root'].subagents_cost == Decimal(5)


def test_unlinked_member_counts_once(source):
    source('root', '1')
    source('child', '2', parent='root')
    source('grandchild', '3', parent='root')
    link('child', 'grandchild')
    result, _ = prepare('root', 'root')
    assert len(result.session_costs) == 3
    assert {row.session_id: row.total_cost for row in result.session_costs} == {
        'root': Decimal(6), 'child': Decimal(5), 'grandchild': Decimal(3),
    }


@pytest.mark.parametrize('updated', [NOW, NOW - timedelta(seconds=1)])
def test_accepted_equal_timestamp_cache_hit(source, updated):
    source('root', '9', marker=NOW, updated=updated, cached='2')
    result, queries = prepare('root')
    assert result.own_updates == ()
    assert result.session_costs[0].self_cost == Decimal(2)
    assert result.session_costs[0].storage_incarnation == 'inc-root'
    assert not any('core_sessionitem' in sql for sql in queries)


@pytest.mark.parametrize('marker,updated', [(None, NOW), (None, None), (NOW, None)])
def test_null_marker_or_source_timestamp_forces_sum(source, marker, updated):
    source('root', '3', marker=marker, updated=updated, cached='9')
    result, _ = prepare('root')
    assert result.own_updates[0].self_cost == Decimal(3)
    assert result.own_updates[0].observed_at == updated




def test_prepare_rejects_main_thread(source):
    from twicc.providers.statistics_prepare import prepare_tree_costs
    source('root')
    with pytest.raises(RuntimeError, match='thread'):
        prepare_tree_costs(('root',))


@pytest.mark.parametrize('publication', ['chunk', 'final'])
def test_actual_background_publication_resets_marker(source, publication):
    from twicc.providers.compute_base import BaseSessionCompute

    session = source('root')
    item = SessionItem.objects.get(session=session)
    Session.objects.filter(pk=session.id).update(items_cost_computed_through_at=NOW)
    if publication == 'chunk':
        assert BaseSessionCompute.apply_session_items_chunk(
            session.id, None, ['cost'], [{'id': item.id, 'cost': 2}], [],
        ) == 'ok'
    else:
        assert BaseSessionCompute.apply_session_complete({
            'session_id': session.id, 'history_facts': [],
            'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}],
        }).outcome == 'applied'
    session.refresh_from_db()
    assert session.items_cost_computed_through_at is None



@pytest.mark.parametrize('own,expected', [(None, None), ('0', Decimal(0)), ('0.123456', Decimal('0.123456'))])
def test_own_cost_null_zero_and_precision(source, own, expected):
    source('root', own=own)
    result, _ = prepare('root')
    assert result.own_updates[0].self_cost == expected
    assert result.session_costs[0].total_cost == (expected if expected else None)


@pytest.mark.parametrize('own,expected', [(None, None), ('0', Decimal(0))])
def test_subagents_preserve_null_and_zero(source, own, expected):
    source('root')
    source('child', own=own, parent='root')
    result, _ = prepare('root')
    assert {row.session_id: row.subagents_cost for row in result.session_costs}['root'] == expected




@pytest.mark.parametrize('updated', [NOW, NOW - timedelta(seconds=1)])
def test_forced_member_sums_even_with_current_marker(source, updated):
    source('root', '9', marker=NOW, updated=updated, cached='2')
    source('child', '3', parent='root', marker=NOW, cached='1')
    result, queries = prepare('root', force_cost_session_ids=('root', 'missing'))
    assert [row.session_id for row in result.own_updates] == ['root']
    assert result.own_updates[0].self_cost == Decimal(9)
    sums = [sql for sql in queries if 'SUM(' in sql and 'core_sessionitem' in sql]
    assert len(sums) == 1
    assert "'child'" not in sums[0]
    assert {row.session_id: row.total_cost for row in result.session_costs}['root'] == Decimal(10)
