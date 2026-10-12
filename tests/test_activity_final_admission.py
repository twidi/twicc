"""Final writer-owned admission preserves SQL precision and exact fallback."""

import sqlite3
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext

from tests import test_activity_preparation as preparation_tests
from twicc.core.models import DailyActivity, Session, SessionItem, WeeklyActivity
from twicc.core.content_storage.extraction import final_compute_message, guard_storage
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.live_aggregates import session_contribution

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = preparation_tests.STAMP
api = preparation_tests.api
enumerate_all = preparation_tests.enumerate_all
repair_all = preparation_tests.repair_all
operation = preparation_tests.operation
wal_operation = preparation_tests.wal_operation


def prepared(operation):
    module = api()
    assert hasattr(module, 'activity_final_policy'), 'Final writer-owned policy is missing'
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    repair_all(module, state)
    return module, state


def policy(module, state, fields=None, **kwargs):
    with transaction.atomic():
        msg = state.operation.message
        guard_storage(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                      msg['observed_upper_line'], msg.get('observed_last_offset'))
        before = session_contribution(Session.objects.get(pk=msg['session_id']))
        return module.activity_final_policy(state, before, fields or {}, **kwargs)


def raw_totals():
    result = []
    for model in (DailyActivity, WeeklyActivity):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT SUM(cost), SUM(session_count) FROM {model._meta.db_table}')
            result.append(cursor.fetchone())
        assert model.objects.aggregate(cost=Sum('cost'))['cost'] == Decimal('0.0000024')
    return result


def test_final_reuses_committed_historical_keys_without_aggregate_reload(operation):
    module, state = prepared(operation)
    assert policy(module, state) == state.required_keys
    final = final_compute_message(operation, frozenset())
    with CaptureQueriesContext(connection) as queries:
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    # No creation timestamp exists. Every historic bucket has zero final deltas.
    assert not any('UPDATE "core_dailyactivity"' in q['sql'] or 'UPDATE "core_weeklyactivity"' in q['sql']
                   or 'INSERT INTO "core_dailyactivity"' in q['sql']
                   or 'INSERT INTO "core_weeklyactivity"' in q['sql'] for q in queries)
    assert raw_totals() == [pytest.approx((0.0000024, 0), abs=1e-15)] * 2


@pytest.mark.parametrize('old,new', [(None, STAMP), (STAMP, None), (STAMP, STAMP),
                                    (STAMP, STAMP + timedelta(days=8))])
@pytest.mark.parametrize('hidden,child', [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize('cached_count', [0, 1, 3])
def test_creation_union_recalculates_despite_legacy_cache_and_count_stability(
        operation, old, new, hidden, child, cached_count):
    parent = (Session.objects.create(id='prepared-parent', project_id='prepared-project', provider='codex',
                                     file_path='/fixture/prepared-parent.jsonl')
              if child else None)
    Session.objects.filter(pk='prepared-session').update(created_at=old, hidden=hidden,
        type='subagent' if child else 'session', parent_session=parent, user_message_count=cached_count)
    # Lifecycle type is an immutable storage observation. Begin a matching operation.
    from twicc.core.content_storage.extraction import (
        begin_compute_application, close_compute_application, prepare_compute_batch, publish_compute_batch,
    )
    close_compute_application(operation)
    operation = begin_compute_application({'session_id': 'prepared-session', 'history_facts': [],
        'session_fields': {'created_at': new.isoformat() if new else None}})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        module, state = prepared(operation)
        cache = policy(module, state, operation.message['session_fields'])
        for key in cache:
            assert key[3] not in {stamp.date() - timedelta(days=stamp.weekday()) if key[0] is WeeklyActivity
                                 else stamp.date() for stamp in (old, new) if stamp is not None}
        final = final_compute_message(operation, frozenset())
        final['_repaired_activity_buckets'] = set(state.required_keys)
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
        totals = raw_totals()
        assert totals[0][0] == pytest.approx(0.0000024, abs=1e-15)
        assert totals[1][0] == pytest.approx(0.0000024, abs=1e-15)
        assert totals[0][1] == totals[1][1] == 2 * int(not hidden and not child and new is not None)
    finally:
        close_compute_application(operation)


@pytest.mark.parametrize('failure', ['missing', 'capacity', 'incomplete', 'updates', 'overrides',
                                   'project', 'provider', 'type', 'hidden'])
def test_failed_final_gate_selects_no_cache(operation, failure):
    module, state = prepared(operation)
    fields, kwargs = {}, {}
    if failure == 'missing':
        state.repaired_keys.pop()
    elif failure == 'capacity':
        state.disable('capacity')
    elif failure == 'incomplete':
        state.enumeration_complete = False
    elif failure in ('updates', 'overrides'):
        kwargs['item_updates' if failure == 'updates' else 'content_overrides'] = [{'id': 1}]
    else:
        field = 'project_id' if failure == 'project' else failure
        fields[field] = {'project_id': 'other', 'provider': 'claude', 'type': 'subagent', 'hidden': True}[field]
    assert policy(module, state, fields, **kwargs) is None
    assert state.fallback_reason is not None


@pytest.mark.parametrize('mutation', ['title', 'unrelated', 'current_replay'])
def test_foreign_commit_before_final_uses_exact_no_cache(wal_operation, mutation):
    operation, path = wal_operation
    module, state = prepared(operation)
    with sqlite3.connect(path) as foreign:
        if mutation == 'title':
            foreign.execute("UPDATE core_session SET title='foreground' WHERE id='prepared-session'")
        elif mutation == 'unrelated':
            foreign.execute("UPDATE core_project SET name='foreground' WHERE id='prepared-project'")
        else:
            foreign.execute("UPDATE core_sessionitem SET cost=0.0000036 WHERE session_id='prepared-session'")
    assert policy(module, state) is None
    assert state.fallback_reason == 'foreign_commit'
    final = final_compute_message(operation, frozenset())
    final['_repaired_activity_buckets'] = set(state.required_keys)
    assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    for model in (DailyActivity, WeeklyActivity):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT SUM(cost) FROM {model._meta.db_table}')
            assert cursor.fetchone()[0] == pytest.approx(.0000072 if mutation == 'current_replay' else .0000024,
                                                       abs=1e-15)


def test_policy_refuses_unlocked_authorization(operation):
    module, state = prepared(operation)
    before = session_contribution(Session.objects.get(pk='prepared-session'))
    assert module.activity_final_policy(state, before, {}) is None
    assert state.fallback_reason == 'final_transaction_missing'


def test_unknown_final_obligation_disables_entire_cache(operation):
    """An unprepared final obligation cannot receive partial shortcut permission."""
    import inspect
    module, state = prepared(operation)
    assert 'extra_activity_keys' in inspect.signature(module.activity_final_policy).parameters
    extra = {(DailyActivity, 'prepared-project', 'codex', STAMP.date() - timedelta(days=90))}
    assert policy(module, state, extra_activity_keys=extra) is None
    assert state.fallback_reason == 'missing_key'


def test_removed_last_timestamp_is_repaired_after_foreign_invalidation(wal_operation):
    from twicc.core.content_storage.extraction import (
        begin_compute_application, close_compute_application, prepare_compute_batch, publish_compute_batch,
    )
    operation, path = wal_operation
    close_compute_application(operation)
    item = SessionItem.objects.get(session_id='prepared-session')
    operation = begin_compute_application({'session_id': 'prepared-session', 'history_facts': [],
        'item_fields': ['timestamp'], 'item_updates': [{'id': item.id, 'timestamp': None}]})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        module, state = prepared(operation)
        assert len(state.required_keys) == 4
        with sqlite3.connect(path) as foreign:
            foreign.execute("UPDATE core_session SET title='invalidate removed day' WHERE id='prepared-session'")
            foreign.execute("INSERT INTO core_dailyactivity (project_id, provider, date, cost, session_count, user_message_count) VALUES ('prepared-project', 'codex', '2026-09-29', 99, 0, 0)")
        final = final_compute_message(operation, frozenset())
        final['_repaired_activity_buckets'] = set(state.required_keys)
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
        assert state.fallback_reason == 'foreign_commit'
        assert not DailyActivity.objects.exists() and not WeeklyActivity.objects.exists()
    finally:
        close_compute_application(operation)


def test_final_rollback_preserves_outdated_versions_and_readiness(operation, monkeypatch):
    from twicc.core.content_storage import extraction
    module, state = prepared(operation)
    before = Session.objects.get(pk='prepared-session')
    operation.message['session_fields'] = {'compute_version': 123}
    final = final_compute_message(operation, frozenset())
    original = extraction.finish_storage_result
    def fail(msg):
        original(msg)
        raise RuntimeError('rollback final readiness')
    monkeypatch.setattr(extraction, 'finish_storage_result', fail)
    with pytest.raises(RuntimeError, match='rollback final readiness'):
        BaseSessionCompute._apply_session_complete_final(final)
    after = Session.objects.get(pk='prepared-session')
    for field in ('compute_version', 'content_storage_version', 'plaintext_verified_epoch', 'created_at'):
        assert getattr(after, field) == getattr(before, field)
    assert state.repaired_keys == state.required_keys


def test_replaced_physical_handle_still_aborts_final_storage(wal_operation):
    operation, path = wal_operation
    module, state = prepared(operation)
    final = final_compute_message(operation, frozenset())
    original = connection.connection
    replacement = sqlite3.connect(path, isolation_level=None)
    replacement.execute('PRAGMA foreign_keys=ON')
    connection.connection = replacement
    try:
        assert replacement.execute('PRAGMA main.data_version').fetchone()[0] == state.data_version
        with pytest.raises(ValueError, match='connection'):
            BaseSessionCompute._apply_session_complete_final(final)
    finally:
        connection.connection = original
        replacement.close()


def test_unavailable_final_fence_selects_exact_fallback(operation, monkeypatch):
    module, state = prepared(operation)
    def fail():
        raise ValueError('unavailable optional version proof')
    monkeypatch.setattr(module, '_data_version', fail)
    assert policy(module, state) is None
    assert state.fallback_reason == 'final_validation_failed'


def test_final_policy_diagnostics_report_hits_misses_and_fallback(operation, monkeypatch, caplog):
    import logging
    module, state = prepared(operation)
    monkeypatch.setattr(module.logger, 'disabled', False)
    monkeypatch.setattr(module.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(module.logger, 'propagate', False)
    with caplog.at_level(logging.DEBUG, logger=module.logger.name):
        assert len(policy(module, state)) == 4
        state.repaired_keys.pop()
        assert policy(module, state) is None
    logs = [record.getMessage() for record in caplog.records if 'Compute activity final policy:' in record.getMessage()]
    assert len(logs) == 2
    assert 'fallback=none retry_count=0 cache_hits=4 cache_misses=0' in logs[0]
    assert 'fallback=missing_key retry_count=0 cache_hits=0 cache_misses=4' in logs[1]
    assert all('SELECT ' not in row and 'UPDATE ' not in row and 'prepared-session' not in row for row in logs)
