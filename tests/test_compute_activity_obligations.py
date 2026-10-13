"""Exact final repair inventory and raw SQLite precision regressions."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models import Sum

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.core.content_storage.extraction import (
    begin_compute_application, close_compute_application, final_compute_message,
    prepare_compute_batch, publish_compute_batch,
)
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.live_aggregates import _activity_keys
from twicc.providers.helpers import get_provider_helpers
from tests.tree_aggregate_helpers import drain_aggregates

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 9, 29, tzinfo=UTC)


def make_session(**fields):
    fields.setdefault('compute_version', get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version)
    project = Project.objects.create(id='obligation-project')
    return Session.objects.create(id='obligation-session', project=project, provider=Provider.CLAUDE_CODE,
                                  file_path='unused', **fields)


def complete(operation):
    while operation.phase != 'final':
        batch = prepare_compute_batch(operation)
        publish_compute_batch(operation, batch, frozenset())
    return final_compute_message(operation, frozenset())


def raw_cost(model, scope):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT cost FROM {model._meta.db_table} WHERE project_id IS %s', [scope])
        return cursor.fetchone()[0]


def test_removed_last_timestamp_retained_for_exact_final_repair():
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP, cost=Decimal(1))
    for model, scope, provider, day in _activity_keys(session.project_id, session.provider, STAMP):
        model.objects.create(project_id=scope, provider=provider, date=day, cost=Decimal(99))
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp', 'cost'], 'item_updates': [{'id': item.id, 'timestamp': None, 'cost': None}]})
    try:
        final = complete(operation)
        for model, scope, provider, day in _activity_keys(session.project_id, session.provider, STAMP):
            model.objects.update_or_create(project_id=scope, provider=provider, date=day,
                                           defaults={'cost': Decimal(99)})
        # Simulate discarded optimization state: the retained inventory must survive.
        final['_repaired_activity_buckets'] = set(_activity_keys(session.project_id, session.provider, STAMP))
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
        drain_aggregates()
        assert not DailyActivity.objects.exists()
        assert not WeeklyActivity.objects.exists()
    finally:
        close_compute_application(operation)


def test_legacy_cache_cannot_preserve_fractional_normalization_error():
    session = make_session()
    items = [SessionItem.objects.create(session=session, line_num=line, content='{}', timestamp=STAMP)
             for line in (1, 2)]
    with connection.cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost = 0.0000006 WHERE session_id = %s', [session.id])
    legacy = set(_activity_keys(session.project_id, session.provider, STAMP))
    for model, scope, provider, day in legacy:
        model.recalculate(scope, day, Provider(provider))
    result = BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        '_repaired_activity_buckets': legacy, 'item_fields': ['cost'],
        'item_updates': [{'id': item.id, 'cost': '0.000001'} for item in items]})
    assert result.outcome == 'applied'
    drain_aggregates()
    for model in (DailyActivity, WeeklyActivity):
        for scope in (session.project_id, None):
            assert raw_cost(model, scope) == pytest.approx(0.000002, abs=1e-15)
            assert model.objects.filter(project_id=scope).aggregate(total=Sum('cost'))['total'] == Decimal('0.000002')


@pytest.mark.parametrize('created', [None, datetime(2026, 9, 20, tzinfo=UTC), STAMP])
@pytest.mark.parametrize('effective', [None, STAMP, datetime(2026, 10, 5, tzinfo=UTC)])
@pytest.mark.parametrize('hidden,child', [(False, False), (True, False), (False, True)])
def test_creation_count_repair_preserves_raw_fractional_cost(created, effective, hidden, child):
    session = make_session(created_at=created, hidden=hidden, type='subagent' if child else 'session')
    if child:
        parent = Session.objects.create(id='obligation-parent', project=session.project, provider=session.provider,
            storage_incarnation='parent-inc', compute_version=session.compute_version)
        session.parent_session = parent
        session.save(update_fields=['parent_session'])
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP,
                               kind=ItemKind.USER_MESSAGE)
    with connection.cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost = 0.0000012 WHERE session_id = %s', [session.id])
    legacy = set(_activity_keys(session.project_id, session.provider, STAMP))
    for model, scope, provider, day in legacy:
        model.recalculate(scope, day, Provider(provider))
    assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
        '_repaired_activity_buckets': legacy, 'session_fields': {'created_at': effective.isoformat() if effective else None}}).outcome == 'applied'
    drain_aggregates()
    for model in (DailyActivity, WeeklyActivity):
        for scope in (session.project_id, None):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT SUM(cost) FROM {model._meta.db_table} WHERE project_id IS %s', [scope])
                assert cursor.fetchone()[0] == pytest.approx(0.0000012, abs=1e-15)
            assert model.objects.filter(project_id=scope).aggregate(total=Sum('cost'))['total'] == Decimal('0.0000012')
            assert model.objects.filter(project_id=scope).aggregate(total=Sum('session_count'))['total'] == int(
                not hidden and not child and effective is not None)


def test_failed_metadata_transaction_does_not_publish_repair_obligations(monkeypatch):
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP)
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp'], 'item_updates': [{'id': item.id, 'timestamp': None}]})
    try:
        while operation.phase != 'metadata':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        original = set(operation.activity_repair_keys)
        batch = prepare_compute_batch(operation)
        def fail(*args):
            raise RuntimeError('rollback inventory')
        monkeypatch.setattr(BaseSessionCompute, 'apply_item_updates', fail)
        with pytest.raises(RuntimeError, match='rollback inventory'):
            publish_compute_batch(operation, batch, frozenset())
        assert operation.activity_repair_keys == original
        item.refresh_from_db()
        assert item.timestamp == STAMP
    finally:
        close_compute_application(operation)


def test_metadata_and_final_subphase_diagnostics_are_payload_free(caplog, monkeypatch):
    import logging
    from twicc import sync_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics.logger, 'disabled', False)
    monkeypatch.setattr(diagnostics.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(diagnostics.logger, 'propagate', False)
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='SECRET-CONTENT', timestamp=STAMP)
    with caplog.at_level(logging.DEBUG, logger='twicc.sync_diagnostics'):
        assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
            'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': '1.000001'}]}).outcome == 'applied'
    logs = '\n'.join(record.getMessage() for record in caplog.records if record.name == 'twicc.sync_diagnostics')
    for phase in ('compute_metadata_source', 'compute_metadata_aggregates', 'compute_final_links',
                  'compute_final_facts', 'compute_final_source', 'compute_final_aggregates', 'compute_final_readiness'):
        assert f'operation={phase} ' in logs
    assert 'SECRET-CONTENT' not in logs
    assert 'UPDATE ' not in logs and 'SELECT ' not in logs



def test_inventory_contains_worker_days_and_old_prospective_creation_scopes():
    old = datetime(2026, 9, 20, tzinfo=UTC)
    new = datetime(2026, 10, 5, tzinfo=UTC)
    session = make_session(created_at=old)
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'affected_days': ['2026-09-29'], 'session_fields': {'created_at': new.isoformat()}})
    try:
        expected = {key for stamp in (old, new, STAMP)
                    for key in _activity_keys(session.project_id, session.provider, stamp)}
        assert operation.activity_repair_keys == expected
        assert complete(operation)['_activity_repair_keys'] == frozenset(expected)
    finally:
        close_compute_application(operation)


def test_empty_metadata_keeps_source_and_aggregate_diagnostics_absent(monkeypatch, caplog):
    import logging
    from twicc import sync_diagnostics
    monkeypatch.setattr(sync_diagnostics.logger, 'disabled', False)
    monkeypatch.setattr(sync_diagnostics.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(sync_diagnostics.logger, 'propagate', False)
    session = make_session()
    with caplog.at_level(logging.DEBUG, logger='twicc.sync_diagnostics'):
        assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': []}).outcome == 'applied'
    assert not any('operation=compute_metadata_' in record.getMessage() for record in caplog.records)
