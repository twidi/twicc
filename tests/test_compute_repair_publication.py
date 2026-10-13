"""Deferred source publication and durable final settlement regressions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import transaction

from twicc.core.content_storage.extraction import (
    begin_compute_application, close_compute_application, prepare_compute_batch, publish_compute_batch,
)
from twicc.core.models import ComputeAggregateRepair, DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.compute_repair_journal import load_compute_repair, merge_compute_repair
from twicc.providers.live_aggregates import _activity_keys
from twicc.providers.helpers import get_provider_helpers
from tests.tree_aggregate_helpers import drain_aggregates
CURRENT = get_provider_helpers("codex").current_compute_version

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 9, 29, tzinfo=UTC)


def source():
    project = Project.objects.create(id='publication-project')
    session = Session.objects.create(id='publication-session', project=project, provider='codex',
                                     compute_version=0, self_cost=Decimal(99), last_line=1, file_path='publication-session')
    item = SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP, cost=1)
    return session, item


def metadata(operation):
    while operation.phase != 'metadata':
        publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
    return publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())


def test_runtime_metadata_chunks_do_not_repair_aggregates(monkeypatch):
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp', 'cost'], 'item_updates': [{'id': item.id, 'timestamp': None, 'cost': 2}]})
    from twicc.providers import compute_base
    reads = []
    contributions = compute_base.item_contributions
    def bounded_read(queryset):
        reads.append(str(queryset.query))
        return contributions(queryset)
    monkeypatch.setattr(compute_base, 'item_contributions', bounded_read)
    calls = []
    original = Session.recalculate_costs
    monkeypatch.setattr(Session, 'recalculate_costs', lambda self: (calls.append(self.id), original(self))[1])
    try:
        metadata(operation)
        assert calls == []
        assert len(reads) == 2
        assert all('"core_sessionitem"."id" IN' in query for query in reads)
        session.refresh_from_db()
        assert session.self_cost == 99 and session.compute_version == 0
        snapshot = load_compute_repair(session.id, session.storage_incarnation)
        assert snapshot.activity_keys >= set(_activity_keys(session.project_id, session.provider, STAMP))
        assert operation.repair_snapshot == snapshot
        assert not DailyActivity.objects.exists()
    finally:
        close_compute_application(operation)


def test_restart_adopts_removed_timestamp_without_private_inventory():
    session, item = source()
    for model, scope, provider, day in _activity_keys(session.project_id, session.provider, STAMP):
        model.objects.create(project_id=scope, provider=provider, date=day, cost=99)
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp', 'cost'], 'item_updates': [{'id': item.id, 'timestamp': None, 'cost': None}]})
    try:
        metadata(operation)
    finally:
        close_compute_application(operation)
    assert ComputeAggregateRepair.objects.filter(session_id=session.id).exists()
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        'session_fields': {'compute_version': CURRENT}}).outcome == 'applied'
    drain_aggregates()
    assert not DailyActivity.objects.exists() and not WeeklyActivity.objects.exists()
    assert not ComputeAggregateRepair.objects.exists()
    session.refresh_from_db()
    assert session.self_cost is None and session.compute_version == CURRENT


def test_final_failure_preserves_committed_source_and_journal(monkeypatch):
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    try:
        metadata(operation)
    finally:
        close_compute_application(operation)
    session.refresh_from_db()
    snapshot = load_compute_repair(session.id, session.storage_incarnation)
    assert snapshot is not None
    def fail(*args, **kwargs):
        raise RuntimeError('final rollback')
    monkeypatch.setattr('twicc.providers.compute_repair.record_compute_repair', fail)
    with pytest.raises(RuntimeError, match='final rollback'):
        BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
            'session_fields': {'compute_version': CURRENT}})
    session.refresh_from_db()
    item.refresh_from_db()
    assert session.compute_version == 0 and session.self_cost == 99
    assert item.cost == 2
    assert load_compute_repair(session.id, session.storage_incarnation) == snapshot


def test_direct_final_initializes_valid_incarnation():
    session, item = source()
    item.delete()
    assert not session.storage_incarnation
    assert BaseSessionCompute._apply_session_complete_final({'session_id': session.id,
        'history_facts': [], 'session_fields': {'compute_version': CURRENT}}).outcome == 'applied'
    session.refresh_from_db()
    assert session.storage_incarnation not in (None, '', 'None')
    assert session.self_cost == 99
    assert ComputeAggregateRepair.objects.exists()
    drain_aggregates()
    session.refresh_from_db()
    assert session.self_cost is None
    assert not ComputeAggregateRepair.objects.exists()


def test_source_final_retains_more_than_former_preparation_capacity():
    session, _ = source()
    from twicc.core.content_storage.extraction import initialize_compute_session
    with transaction.atomic():
        initialize_compute_session(session.id)
        session.refresh_from_db()
        keys = {(DailyActivity, None, 'codex', (STAMP + timedelta(days=n)).date()) for n in range(4100)}
        DailyActivity.objects.create(project=None, provider='codex', date=(STAMP + timedelta(days=4099)).date(), cost=99)
        merge_compute_repair(session.id, session.storage_incarnation, activity_keys=keys, source_changed=True)
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id,
        'history_facts': []}).outcome == 'applied'
    snapshot = load_compute_repair(session.id, session.storage_incarnation)
    assert len(snapshot.activity_keys) >= 4100
    assert DailyActivity.objects.filter(date=(STAMP + timedelta(days=4099)).date()).exists()
    # Source readiness never drains a journal, irrespective of its size.
    session.refresh_from_db()
    assert session.compute_version == 0


def test_journal_failure_rolls_back_source_and_private_mirrors(monkeypatch):
    from twicc.providers import compute_repair
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp'], 'item_updates': [{'id': item.id, 'timestamp': None}]})
    original = compute_repair.record_compute_repair
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('journal rollback')
    monkeypatch.setattr(compute_repair, 'record_compute_repair', fail)
    try:
        with pytest.raises(RuntimeError, match='journal rollback'):
            metadata(operation)
        item.refresh_from_db()
        assert item.timestamp == STAMP
        assert not ComputeAggregateRepair.objects.exists()
        assert operation.repair_snapshot is None
        assert operation.activity_repair_keys == set()
    finally:
        close_compute_application(operation)


def test_source_final_does_not_settle_journal(monkeypatch):
    from twicc.providers import compute_repair_journal
    session, item = source()
    def forbidden(*args, **kwargs):
        raise AssertionError('source final must not settle aggregates')
    monkeypatch.setattr(compute_repair_journal, 'settle_compute_repair', forbidden)
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        'session_fields': {'compute_version': CURRENT}}).outcome == 'applied'
    session.refresh_from_db()
    assert session.compute_version == CURRENT and session.self_cost == 99
    assert load_compute_repair(session.id, session.storage_incarnation) is not None
    assert not DailyActivity.objects.exists()


def test_final_restores_target_children_and_retained_parent_raw_costs():
    session, item = source()
    old_parent = Session.objects.create(id='old-parent', file_path='old-parent', project=session.project, provider='codex',
                                       self_cost=99, subagents_cost=99)
    new_parent = Session.objects.create(id='new-parent', file_path='new-parent', project=session.project, provider='codex',
                                       self_cost=99, subagents_cost=99)
    child = Session.objects.create(id='child', file_path='child', project=session.project, provider='codex',
                                   parent_session=session, type='subagent', self_cost=99)
    SessionItem.objects.create(session=old_parent, line_num=1, content='{}', cost=3)
    SessionItem.objects.create(session=new_parent, line_num=1, content='{}', cost=5)
    SessionItem.objects.create(session=child, line_num=1, content='{}', cost=7)
    session.parent_session = old_parent
    session.type = 'subagent'
    session.save(update_fields=['parent_session', 'type'])
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    try:
        metadata(operation)
    finally:
        close_compute_application(operation)
    # A supported later mutation can change the current parent. SQL retains the old parent.
    Session.objects.filter(pk=session.id).update(parent_session=new_parent)
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': []}).outcome == 'applied'
    for row in (session, child, old_parent, new_parent):
        Session.objects.filter(pk=row.pk).update(compute_version=CURRENT, storage_incarnation=row.id + '-inc')
    # Preserve the source incarnation recorded by the real final application.
    source_snapshot = ComputeAggregateRepair.objects.get(session_id=session.id)
    Session.objects.filter(pk=session.pk).update(storage_incarnation=source_snapshot.incarnation)
    drain_aggregates()
    for row in (session, child, old_parent, new_parent):
        row.refresh_from_db()
    assert session.self_cost == 2 and session.subagents_cost == 7 and session.total_cost == 9
    assert child.self_cost == 7
    assert old_parent.self_cost == 3 and old_parent.subagents_cost is None and old_parent.total_cost == 3
    assert new_parent.self_cost == 5 and new_parent.subagents_cost == 9 and new_parent.total_cost == 14
    assert not ComputeAggregateRepair.objects.exists()


def test_zero_item_final_retains_worker_and_creation_scopes():
    session, item = source()
    item.delete()
    session.created_at = STAMP
    session.save(update_fields=['created_at'])
    future = STAMP + timedelta(days=14)
    for stamp in (STAMP, future):
        for model, scope, provider, day in _activity_keys(session.project_id, session.provider, stamp):
            model.objects.get_or_create(project_id=scope, provider=provider, date=day, defaults={'cost': 99})
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        'affected_days': [future.date().isoformat()], 'session_fields': {'created_at': None, 'compute_version': CURRENT}}).outcome == 'applied'
    drain_aggregates()
    assert not DailyActivity.objects.exists() and not WeeklyActivity.objects.exists()
    assert not ComputeAggregateRepair.objects.exists()


def test_append_supersedes_final_and_keeps_committed_journal():
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'observed_last_offset': 0,
        'history_facts': [], 'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    try:
        metadata(operation)
    finally:
        close_compute_application(operation)
    session.refresh_from_db()
    snapshot = load_compute_repair(session.id, session.storage_incarnation)
    Session.objects.filter(pk=session.id).update(last_offset=1)
    result = BaseSessionCompute.apply_session_complete({'session_id': session.id, 'observed_last_offset': 0,
        'history_facts': [], 'session_fields': {'compute_version': CURRENT}})
    assert result.outcome == 'superseded'
    session.refresh_from_db()
    assert session.compute_version == 0
    assert load_compute_repair(session.id, session.storage_incarnation) == snapshot


def test_compatibility_wrapper_retains_source_and_journal_without_repair():
    from twicc.core.content_storage.extraction import prepare_compute_result
    session, item = source()
    final = prepare_compute_result({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    session.refresh_from_db()
    assert session.self_cost == 99
    assert not DailyActivity.objects.exists()
    assert ComputeAggregateRepair.objects.exists()
    assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'


def test_compatibility_chunk_extends_existing_journal_generation_and_keys():
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    try:
        metadata(operation)
    finally:
        close_compute_application(operation)
    session.refresh_from_db()
    before = load_compute_repair(session.id, session.storage_incarnation)
    future = STAMP + timedelta(days=14)
    assert BaseSessionCompute.apply_session_items_chunk(session.id, 0, ['cost', 'timestamp'],
        [{'id': item.id, 'cost': 3, 'timestamp': future.isoformat()}], []) == 'ok'
    after = load_compute_repair(session.id, session.storage_incarnation)
    assert after.observation.generation > before.observation.generation
    assert after.activity_keys >= before.activity_keys | set(_activity_keys(session.project_id, session.provider, future))
    session.refresh_from_db()
    assert session.self_cost == 99


def test_runtime_final_repairs_raw_precision_over_unfinished_peer():
    from django.db import connection
    from twicc.core.content_storage.extraction import final_compute_message
    from twicc.core.enums import ItemKind
    session, item = source()
    peer = Session.objects.create(id='unfinished-peer', file_path='unfinished-peer', project=session.project, provider='codex',
                                  created_at=STAMP, user_message_count=0, compute_version=0, self_cost=99)
    SessionItem.objects.create(session=peer, line_num=1, content='{}', timestamp=STAMP,
                               kind=ItemKind.USER_MESSAGE)
    with connection.cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost = 0.0000006 WHERE session_id = %s', [peer.id])
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost', 'kind'], 'item_updates': [{'id': item.id, 'cost': '0.000001',
                                                        'kind': ItemKind.USER_MESSAGE}],
        'session_fields': {'compute_version': CURRENT, 'created_at': STAMP.isoformat()}})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        final = final_compute_message(operation, frozenset())
        final['_repaired_activity_buckets'] = set(_activity_keys(session.project_id, session.provider, STAMP))
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    finally:
        close_compute_application(operation)
    drain_aggregates()
    for model in (DailyActivity, WeeklyActivity):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT cost, user_message_count, session_count FROM {model._meta.db_table}')
            for cost, messages, sessions in cursor.fetchall():
                assert cost == pytest.approx(0.0000016, abs=1e-15)
                assert messages == 2 and sessions == 1
    peer.refresh_from_db()
    assert peer.self_cost == 99 and peer.compute_version == 0
    assert not ComputeAggregateRepair.objects.exists()


def test_same_day_source_mutation_advances_durable_generation():
    session, item = source()
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    operation.item_row_budget = 1
    try:
        metadata(operation)
        first = operation.repair_snapshot
        session.refresh_from_db()
        assert BaseSessionCompute.apply_session_items_chunk(session.id, 0, ['cost'],
            [{'id': item.id, 'cost': 3}], [], defer_aggregate_repair=True) == 'ok'
        second = load_compute_repair(session.id, session.storage_incarnation)
        assert second.activity_keys == first.activity_keys
        assert second.observation.generation == first.observation.generation + 1
        assert session.self_cost == 99
    finally:
        close_compute_application(operation)


def test_first_runtime_chunk_seeds_old_prospective_scopes_and_worker_days():
    session, item = source()
    prospective_project = Project.objects.create(id='prospective-project')
    session.created_at = STAMP - timedelta(days=14)
    session.save(update_fields=['created_at'])
    future = STAMP + timedelta(days=14)
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'affected_days': [future.date().isoformat()], 'session_fields': {
            'created_at': future.isoformat(), 'project_id': prospective_project.id, 'provider': 'claude_code'},
        'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': 2}]})
    try:
        metadata(operation)
        journal = operation.repair_snapshot
        assert (DailyActivity, session.project_id, 'codex', session.created_at.date()) in journal.activity_keys
        assert (DailyActivity, 'prospective-project', 'claude_code', future.date()) in journal.activity_keys
        assert (DailyActivity, None, 'claude_code', future.date()) in journal.activity_keys
        assert (DailyActivity, session.project_id, 'codex', future.date()) in journal.activity_keys
        assert (DailyActivity, 'prospective-project', 'claude_code', STAMP.date()) in journal.activity_keys
    finally:
        close_compute_application(operation)
