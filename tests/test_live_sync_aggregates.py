"""Exact persisted contribution maintenance across every writer transaction."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import transaction
from django.core.exceptions import FieldDoesNotExist

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.providers.live_aggregates import (
    apply_contribution_changes, item_contributions, session_contribution,
)

from tests.tree_aggregate_helpers import begin_history_replacement, drain_aggregates
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(name='s', **kwargs):
    kwargs.setdefault('compute_version', get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version)
    kwargs.setdefault('storage_incarnation', name + '-inc')
    project, _ = Project.objects.get_or_create(id='p')
    return Session.objects.create(id=name, project=project, provider=Provider.CLAUDE_CODE,
                                  file_path=name, **kwargs)


def apply(session, before_items=(), before=None, repair=False, repaired_activity_buckets=None):
    apply_contribution_changes(before_items, item_contributions(session.items.all()),
                               before_sessions=[before or session_contribution(session)],
                               after_sessions=[session_contribution(session)], repair=repair,
                               repaired_activity_buckets=repaired_activity_buckets)
    session.refresh_from_db()


def test_delta_preserves_null_zero_and_six_decimals():
    session = make_session(created_at=datetime(2026, 9, 27, 23, 59, tzinfo=UTC))
    before = session_contribution(session)
    with transaction.atomic():
        item = SessionItem.objects.create(session=session, line_num=1, content='{}',
            kind=ItemKind.USER_MESSAGE, timestamp=datetime(2026, 9, 28, tzinfo=UTC), cost=Decimal('0.000001'))
        apply(session, before=before)
    assert session.self_cost == session.total_cost == Decimal('0.000001')
    assert session.user_message_count == 1
    assert DailyActivity.objects.get(project_id='p', date='2026-09-27').session_count == 1
    assert DailyActivity.objects.get(project_id='p', date='2026-09-28').user_message_count == 1
    assert WeeklyActivity.objects.filter(project_id='p').count() == 2
    old = item_contributions(session.items.all())
    before = session_contribution(session)
    with transaction.atomic():
        item.cost = Decimal(0)
        item.save()
        apply(session, old, before)
    assert session.self_cost == 0
    assert session.total_cost is None
    old = item_contributions(session.items.all())
    with transaction.atomic():
        item.cost = None
        item.save()
        apply(session, old)
    assert session.self_cost is None


@pytest.mark.parametrize('cached,raw', [(None, Decimal(0)), (Decimal(0), None)])
def test_incomplete_child_defers_nullability_repair_until_ready(cached, raw):
    parent = make_session('parent')
    child = make_session('child', parent_session=parent, type='subagent', self_cost=cached, compute_version=1)
    SessionItem.objects.create(session=child, line_num=1, content='{}', cost=raw)
    with transaction.atomic():
        apply(parent, repair=True)
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.subagents_cost is None
    assert child.self_cost == cached
    Session.objects.filter(pk=child.pk).update(compute_version=get_provider_helpers(child.provider).current_compute_version)
    drain_aggregates()
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.subagents_cost == raw
    assert child.self_cost == raw
    assert child.compute_version == get_provider_helpers(child.provider).current_compute_version


def assert_activities_match_reference():
    from twicc.core.models import PeriodicActivity
    before = [(model, list(model.objects.order_by('project_id', 'provider', 'date').values_list(
        'project_id', 'provider', 'date', 'user_message_count', 'session_count', 'cost')))
        for model in (DailyActivity, WeeklyActivity)]
    days = {row.timestamp.date() for row in SessionItem.objects.exclude(timestamp=None)}
    days.update(s.created_at.date() for s in Session.objects.exclude(created_at=None))
    for project_id in [None, *Project.objects.values_list('id', flat=True)]:
        PeriodicActivity.recalculate_for_days(project_id, days, Provider.CLAUDE_CODE, do_global=False)
    for model, expected in before:
        assert list(model.objects.order_by('project_id', 'provider', 'date').values_list(
            'project_id', 'provider', 'date', 'user_message_count', 'session_count', 'cost')) == expected


@pytest.mark.parametrize('hidden,kind', [(False, 'session'), (True, 'session'), (False, 'subagent')])
def test_eligibility_and_late_first_message(hidden, kind):
    session = make_session(hidden=hidden, type=kind, created_at=datetime(2026, 9, 20, tzinfo=UTC))
    before = session_contribution(session)
    with transaction.atomic():
        SessionItem.objects.create(session=session, line_num=1, content='{}', kind=ItemKind.USER_MESSAGE,
            timestamp=datetime(2026, 9, 29, tzinfo=UTC), cost=Decimal('1.234567'))
        apply(session, before=before)
    assert session.user_message_count == 1
    assert DailyActivity.objects.get(project_id='p', date='2026-09-29').cost == Decimal('1.234567')
    assert_activities_match_reference()


def test_outdated_chunk_retains_obligations_until_source_ready():
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.helpers import get_provider_helpers
    current = get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version
    parent = make_session('parent', compute_version=current)
    child = make_session('child', parent_session=parent, type='subagent', compute_version=1)
    peer = make_session('peer', compute_version=current, created_at=datetime(2026, 9, 1, tzinfo=UTC))
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    item = SessionItem.objects.create(session=child, line_num=1, content='{}', timestamp=stamp)
    with transaction.atomic():
        SessionItem.objects.create(session=peer, line_num=1, content='{}', timestamp=stamp,
            kind=ItemKind.USER_MESSAGE, cost=Decimal('2.000001'))
        apply(peer)
    assert BaseSessionCompute.apply_session_items_chunk(child.id, 0, ['cost'],
        [{'id': item.id, 'cost': '0.000003'}], []) == 'ok'
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.subagents_cost is child.self_cost is None
    assert DailyActivity.objects.get(project=None, date=stamp.date()).cost == Decimal('2.000001')
    assert BaseSessionCompute.apply_session_complete({'session_id': child.id, 'observed_last_offset': -1}).outcome == 'superseded'
    assert child.compute_version == 1
    assert BaseSessionCompute.apply_session_complete({'session_id': child.id, 'observed_last_offset': 0,
        'history_facts': [], 'session_fields': {'compute_version': current}}).outcome == 'applied'
    drain_aggregates()
    assert_activities_match_reference()
    # A later chunk moves an old cost and changes an old user-message kind.
    peer_item = peer.items.get()
    assert BaseSessionCompute.apply_session_items_chunk(peer.id, 0, ['cost', 'kind', 'timestamp'], [{
        'id': peer_item.id, 'cost': '0', 'kind': ItemKind.SYSTEM,
        'timestamp': '2026-09-30T00:00:00+00:00',
    }], []) == 'ok'
    drain_aggregates()
    peer.refresh_from_db()
    assert peer.self_cost == 0 and peer.user_message_count == 0
    assert_activities_match_reference()


@pytest.mark.django_db(transaction=True)
def test_current_live_append_uses_indexed_dedup_and_no_history_aggregate(tmp_path):
    import orjson
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.helpers import get_provider_helpers
    from twicc.providers.live_sync import LiveSyncLimits
    session = make_session(compute_version=get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version,
        last_line=2000, self_cost=Decimal('0.002'), total_cost=Decimal('0.002'))
    SessionItem.objects.bulk_create([SessionItem(session=session, line_num=n, content='{}',
        message_id='repeated', cost=Decimal('0.000001')) for n in range(1, 2001)])
    path = tmp_path / 's'
    record = {'type': 'assistant', 'message': {'id': 'repeated', 'role': 'assistant',
        'content': [{'type': 'text', 'text': 'hello'}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}}
    path.write_bytes((orjson.dumps(record) + b'\n') * 2)
    with CaptureQueriesContext(connection) as queries:
        result = ClaudeCodeSessionCompute().sync_session_slice(session.id, path, limits=LiveSyncLimits())
    assert result.lines_processed == 2
    session.refresh_from_db()
    assert session.self_cost == Decimal('0.002')
    sql = [q['sql'].upper() for q in queries]
    assert not [q for q in sql if 'CORE_SESSIONITEM' in q and ('SUM(' in q or 'COUNT(' in q)]
    dedup = [q for q in sql if 'SELECT ' in q and 'MESSAGE_ID' in q and 'REPEATED' in q]
    assert len(dedup) == 1
    assert 'LIMIT 1' in dedup[0] and 'LINE_NUM' in dedup[0] and ' < ' in dedup[0]


def test_visibility_repairs_creation_bucket_and_rolls_back_flag(monkeypatch):
    from twicc.core.services.session_visibility import _apply_visibility_flag
    session = make_session(created_at=datetime(2026, 9, 20, tzinfo=UTC))
    with transaction.atomic():
        SessionItem.objects.create(session=session, line_num=1, content='{}', kind=ItemKind.USER_MESSAGE,
            timestamp=datetime(2026, 9, 29, tzinfo=UTC), cost=Decimal('1.000001'))
        apply(session, repair=True)
    _apply_visibility_flag(session.id, True)
    session.refresh_from_db()
    assert session.hidden
    drain_aggregates()
    assert not DailyActivity.objects.filter(date='2026-09-20').exists()
    assert_activities_match_reference()
    def fail(*args, **kwargs):
        raise RuntimeError('activity failed')
    with monkeypatch.context() as patch:
        patch.setattr('twicc.providers.compute_repair.merge_compute_repair', fail)
        with pytest.raises(RuntimeError, match='activity failed'):
            _apply_visibility_flag(session.id, False)
    session.refresh_from_db()
    assert session.hidden
    assert_activities_match_reference()


@pytest.mark.django_db(transaction=True)
def test_partial_chunk_then_failed_final_then_live_append(tmp_path):
    import orjson
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.live_sync import LiveSyncLimits
    from twicc.providers.helpers import get_provider_helpers
    session = make_session(last_line=1, compute_version=None, created_at=datetime(2026, 9, 20, tzinfo=UTC))
    item = SessionItem.objects.create(session=session, line_num=1, content='{}',
                                     timestamp=datetime(2026, 9, 29, tzinfo=UTC))
    assert BaseSessionCompute.apply_session_items_chunk(session.id, 0, ['cost'],
        [{'id': item.id, 'cost': '0.123456'}], []) == 'ok'
    with pytest.raises(FieldDoesNotExist):
        BaseSessionCompute.apply_session_complete({
            'session_id': session.id, 'observed_last_offset': 0, 'history_facts': [],
            'session_fields': {'compute_version': get_provider_helpers(session.provider).current_compute_version,
                               'not_a_field': 1},
        })
    session.refresh_from_db()
    assert session.compute_version is None
    assert session.self_cost is None
    path = tmp_path / 's'
    path.write_bytes(orjson.dumps({'type': 'user', 'timestamp': '2026-09-30T00:00:00Z',
        'message': {'role': 'user', 'content': 'hello'}}) + b'\n')
    result = ClaudeCodeSessionCompute().sync_session_slice(session.id, path, limits=LiveSyncLimits(1))
    assert result.lines_processed == 1
    session.refresh_from_db()
    assert session.user_message_count == 1
    assert session.self_cost is None
    assert session.compute_version is None
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id,
        'observed_last_offset': session.last_offset, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers(session.provider).current_compute_version}}).outcome == 'applied'
    drain_aggregates()
    session.refresh_from_db()
    assert session.self_cost == Decimal('0.123456')
    assert session.user_message_count == 1
    assert_activities_match_reference()


def test_history_replacement_repairs_deleted_cost_and_creation_buckets():
    import asyncio
    from twicc.providers.codex.rollout_migration import ReplaceCodexHistoryJob
    session = make_session(created_at=datetime(2026, 9, 20, tzinfo=UTC))
    with transaction.atomic():
        SessionItem.objects.create(session=session, line_num=1, content='{}', kind=ItemKind.USER_MESSAGE,
            timestamp=datetime(2026, 9, 29, tzinfo=UTC), cost=Decimal('3.000001'))
        apply(session, repair=True)
    drain_aggregates()
    # The shared mutation adapter repairs stored contributions regardless of provider.
    loop = asyncio.new_event_loop()
    try:
        job = ReplaceCodexHistoryJob(Provider.CLAUDE_CODE, session.id, [], 0, 0, 0, loop.create_future())
        begin_history_replacement(job)
    finally:
        loop.close()
    session.refresh_from_db()
    assert session.compute_version is None
    assert DailyActivity.objects.exists()
    Session.objects.filter(pk=session.pk).update(compute_version=get_provider_helpers(session.provider).current_compute_version)
    drain_aggregates()
    session.refresh_from_db()
    assert session.self_cost is None and session.total_cost is None
    assert session.user_message_count == 0
    assert not DailyActivity.objects.exists()
    assert not WeeklyActivity.objects.exists()


def test_current_child_delta_defers_outdated_parent():
    from twicc.providers.helpers import get_provider_helpers
    current = get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version
    parent = make_session('parent', compute_version=1, subagents_cost=Decimal(99))
    child = make_session('child', type='subagent', parent_session=parent, compute_version=current)
    with transaction.atomic():
        SessionItem.objects.create(session=child, line_num=1, content='{}', cost=Decimal('0.000001'))
        apply(child)
    parent.refresh_from_db()
    assert child.self_cost == Decimal('0.000001')
    assert parent.subagents_cost == Decimal(99)
    assert parent.compute_version == 1
    Session.objects.filter(pk=parent.pk).update(compute_version=current)
    drain_aggregates()
    parent.refresh_from_db()
    assert parent.subagents_cost == Decimal('0.000001')


def test_deleting_child_repairs_parent_and_shared_activity_bucket():
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
    parent = make_session('parent')
    child = make_session('child', type='subagent', parent_session=parent)
    peer = make_session('peer')
    with transaction.atomic():
        for session, cost in [(child, '0.000001'), (peer, '0.000002')]:
            SessionItem.objects.create(session=session, line_num=1, content='{}',
                timestamp=datetime(2026, 9, 29, tzinfo=UTC), cost=Decimal(cost))
            apply(session, repair=True)
    payload = DeleteSessionsPayload(provider=Provider.CLAUDE_CODE, session_ids=[child.id])
    assert _apply_delete_sessions_payload(payload) == [child.id]
    drain_aggregates()
    parent.refresh_from_db()
    assert parent.subagents_cost is None
    assert DailyActivity.objects.get(project_id=None).cost == Decimal('0.000002')
    assert_activities_match_reference()


def test_source_final_does_not_run_historical_aggregates(monkeypatch):
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.helpers import get_provider_helpers
    session = make_session(compute_version=0, created_at=datetime(2026, 9, 20, tzinfo=UTC))
    item = SessionItem.objects.create(session=session, line_num=1, content='{}',
                                     timestamp=datetime(2026, 9, 29, tzinfo=UTC))
    def fail(*args, **kwargs):
        raise RuntimeError('final aggregate failure')
    monkeypatch.setattr(DailyActivity, 'recalculate', fail)
    result = BaseSessionCompute.apply_session_complete({
            'session_id': session.id, 'observed_last_offset': 0, 'history_facts': [],
            'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': '1.000001'}],
            'session_fields': {'compute_version': get_provider_helpers(session.provider).current_compute_version},
        })
    item.refresh_from_db()
    session.refresh_from_db()
    assert result.outcome == 'applied'
    assert item.cost == Decimal('1.000001')
    assert session.compute_version == get_provider_helpers(session.provider).current_compute_version
    assert session.self_cost is None
    from twicc.core.models import ComputeAggregateRepair
    assert ComputeAggregateRepair.objects.filter(session_id=session.id).exists()
    assert not DailyActivity.objects.exists()


def test_repair_does_not_recalculate_unrelated_sibling_costs(monkeypatch):
    parent = make_session('parent', compute_version=1)
    changed = make_session('changed', type='subagent', parent_session=parent, compute_version=1)
    sibling = make_session('sibling', type='subagent', parent_session=parent, compute_version=1,
                           self_cost=Decimal(99))
    SessionItem.objects.create(session=changed, line_num=1, content='{}', cost=Decimal('0.000001'))
    SessionItem.objects.create(session=sibling, line_num=1, content='{}', cost=Decimal('0.000002'))

    original = Session.recalculate_costs
    recalculated = []

    def counted(self):
        recalculated.append(self.id)
        return original(self)

    monkeypatch.setattr(Session, 'recalculate_costs', counted)
    with transaction.atomic():
        apply(changed, repair=True)
    parent.refresh_from_db()
    sibling.refresh_from_db()
    assert sibling.id not in recalculated
    assert sibling.self_cost == Decimal(99)
    assert parent.subagents_cost is None
    assert recalculated == []


@pytest.mark.django_db(transaction=True)
def test_repaired_activity_then_live_delta_uses_no_history_helper(monkeypatch):
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    first = make_session('first', created_at=stamp)
    second = make_session('second', created_at=stamp)
    counts = {'daily': 0, 'weekly': 0}
    for model, name in ((DailyActivity, 'daily'), (WeeklyActivity, 'weekly')):
        original = model.recalculate

        def counted(project_id, day, provider, *, original=original, name=name):
            counts[name] += 1
            return original(project_id, day, provider)

        monkeypatch.setattr(model, 'recalculate', staticmethod(counted))

    repaired = set()
    SessionItem.objects.create(session=first, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('1.000001'))
    with transaction.atomic():
        apply(first, repair=True, repaired_activity_buckets=repaired)
    assert counts == {'daily': 0, 'weekly': 0}
    assert not repaired
    drain_aggregates()

    live = make_session('live', created_at=stamp)
    SessionItem.objects.create(session=live, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('3.000003'))
    with transaction.atomic():
        apply(live)

    SessionItem.objects.create(session=second, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('2.000002'))
    with transaction.atomic():
        apply(second, repair=True, repaired_activity_buckets=repaired)
    assert counts == {'daily': 0, 'weekly': 0}
    drain_aggregates()
    for model in (DailyActivity, WeeklyActivity):
        for row in model.objects.all():
            assert (row.user_message_count, row.session_count, row.cost) == (3, 3, Decimal('6.000006'))


@pytest.mark.django_db(transaction=True)
def test_rolled_back_source_obligations_do_not_publish(monkeypatch):
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    session = make_session('rollback', created_at=stamp)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('1.000001'))
    repaired = set()
    calls = []
    original = DailyActivity.recalculate

    def counted(project_id, day, provider):
        calls.append((project_id, day, provider))
        return original(project_id, day, provider)

    monkeypatch.setattr(DailyActivity, 'recalculate', staticmethod(counted))
    with pytest.raises(RuntimeError, match='rollback'), transaction.atomic():
        apply(session, repair=True, repaired_activity_buckets=repaired)
        raise RuntimeError('rollback')
    assert not repaired
    with transaction.atomic():
        apply(session, repair=True, repaired_activity_buckets=repaired)
    assert calls == []
    assert not repaired
    drain_aggregates()
    assert DailyActivity.objects.get(project_id=None).cost == Decimal('1.000001')


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('change', ['hide', 'move_project'])
def test_metadata_change_recalculates_cached_historical_activity(change):
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    session = make_session('metadata-change', created_at=stamp)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('1.000001'))
    repaired = set()
    with transaction.atomic():
        apply(session, repair=True, repaired_activity_buckets=repaired)

    drain_aggregates()
    before = session_contribution(session)
    if change == 'hide':
        session.hidden = True
        session.save(update_fields=['hidden'])
    else:
        session.project = Project.objects.create(id='moved-to')
        session.save(update_fields=['project'])
    with transaction.atomic():
        apply_contribution_changes([], [], before_sessions=[before],
                                   after_sessions=[session_contribution(session)], repair=False,
                                   repaired_activity_buckets=repaired)
    drain_aggregates()
    assert_activities_match_reference()


@pytest.mark.django_db(transaction=True)
def test_live_cost_delta_uses_persisted_six_decimal_precision(tmp_path, monkeypatch):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.helpers import get_provider_helpers
    from twicc.providers.live_sync import LiveSyncLimits
    session = make_session(compute_version=get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version)
    compute = ClaudeCodeSessionCompute()
    def cost(item, *args):
        item.cost = Decimal('0.0000006')
    monkeypatch.setattr(compute, 'compute_item_cost_and_usage', cost)
    path = tmp_path / 's'
    path.write_bytes(b'{}\n{}\n')
    compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    session.refresh_from_db()
    assert list(session.items.values_list('cost', flat=True)) == [Decimal('0.000001')] * 2
    assert session.self_cost == session.total_cost == Decimal('0.000002')
    with transaction.atomic():
        apply_contribution_changes([], [], before_sessions=[session_contribution(session)],
            after_sessions=[session_contribution(session)], repair=True)
    session.refresh_from_db()
    assert session.self_cost == Decimal('0.000002')


def test_shared_bucket_session_count_keeps_cached_count_reference_semantics():
    from twicc.providers.compute_base import BaseSessionCompute
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    # The reference counts cached user_message_count, including an outdated
    # peer whose raw user rows are not yet computed. It does not use EXISTS.
    peer = make_session('peer', created_at=stamp, user_message_count=1, compute_version=1)
    changed = make_session('changed', compute_version=1)
    item = SessionItem.objects.create(session=changed, line_num=1, content='{}', timestamp=stamp)
    BaseSessionCompute.apply_session_items_chunk(changed.id, 0, ['cost'],
        [{'id': item.id, 'cost': '0.000001'}], [])
    assert not DailyActivity.objects.exists()
    BaseSessionCompute.apply_session_complete({'session_id': changed.id, 'observed_last_offset': 0,
        'history_facts': [], 'session_fields': {'compute_version': get_provider_helpers(changed.provider).current_compute_version}})
    drain_aggregates()
    assert DailyActivity.objects.get(project_id=None, date=stamp.date()).session_count == 1
    peer.refresh_from_db()
    assert peer.user_message_count == 1
    assert_activities_match_reference()


def test_current_child_zero_to_null_updates_parent_partial_index():
    from twicc.providers.helpers import get_provider_helpers
    current = get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version
    parent = make_session('parent', compute_version=current)
    child = make_session('child', parent_session=parent, type='subagent', compute_version=current)
    item = SessionItem.objects.create(session=child, line_num=1, content='{}')
    old = item_contributions(child.items.all())
    with transaction.atomic():
        item.cost = Decimal(0)
        item.save()
        apply(child, old)
    parent.refresh_from_db()
    assert parent.subagents_cost == 0 and parent.total_cost is None
    old = item_contributions(child.items.all())
    with transaction.atomic():
        item.cost = None
        item.save()
        apply(child, old)
    parent.refresh_from_db()
    assert parent.subagents_cost is None and parent.total_cost is None


@pytest.mark.parametrize('pending', [False, True])
def test_live_message_count_advances_while_own_cost_baseline_waits(pending):
    """Pending costs must not suppress watcher message visibility or replay deltas."""
    from twicc.providers.compute_repair_journal import merge_compute_repair
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    session = make_session('live-count', compute_version=get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version if pending else 0,
                           self_cost=Decimal(99), total_cost=Decimal(99))
    if pending:
        with transaction.atomic():
            merge_compute_repair(session.id, session.storage_incarnation, source_changed=True, baseline_pending=True)
    with CaptureQueriesContext(connection) as queries:
        before = session_contribution(session)
        with transaction.atomic():
            item = SessionItem.objects.create(session=session, line_num=1, kind=ItemKind.USER_MESSAGE, cost=Decimal(2))
            contribution = item_contributions(SessionItem.objects.filter(pk=item.pk))
            apply_contribution_changes([], contribution, before_sessions=[before], after_sessions=[before], repair=not pending)
        session.refresh_from_db()
        assert session.user_message_count == 1
        assert session.total_cost == Decimal(99)
        with transaction.atomic():
            state = session_contribution(session)
            apply_contribution_changes(contribution, contribution, before_sessions=[state], after_sessions=[state], repair=not pending)
        session.refresh_from_db()
        assert session.user_message_count == 1
    assert not any('SUM(' in query['sql'].upper() for query in queries)


def test_pending_zero_message_count_accepts_negative_replay_delta():
    """An invalid zero baseline cannot make a reclassified source row unwritable."""
    from twicc.providers.compute_repair_journal import merge_compute_repair
    session = make_session('zero-count')
    item = SessionItem.objects.create(session=session, line_num=1, kind=ItemKind.USER_MESSAGE)
    before_items = item_contributions(SessionItem.objects.filter(pk=item.pk))
    with transaction.atomic():
        merge_compute_repair(session.id, session.storage_incarnation, source_changed=True, baseline_pending=True)
        SessionItem.objects.filter(pk=item.pk).update(kind=ItemKind.ASSISTANT_MESSAGE)
        state = session_contribution(session)
        apply_contribution_changes(before_items, item_contributions(SessionItem.objects.filter(pk=item.pk)),
            before_sessions=[state], after_sessions=[state], repair=False)
    session.refresh_from_db()
    assert session.user_message_count == 0
    item.refresh_from_db()
    assert item.kind == ItemKind.ASSISTANT_MESSAGE
