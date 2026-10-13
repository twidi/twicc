"""Operation-owned exact activity repair and conservative WAL proof failures."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.db import connection, transaction
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext

from twicc.core.enums import ItemKind
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.core.content_storage.extraction import (
    begin_compute_application, close_compute_application, final_compute_message,
    prepare_compute_batch, publish_compute_batch,
)
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 9, 29, tzinfo=UTC)


def api():
    # A failed assertion identifies the absent interface before product edits.
    import importlib.util
    assert importlib.util.find_spec('twicc.providers.activity_preparation') is not None
    return import_module('twicc.providers.activity_preparation')


@pytest.fixture
def operation():
    project = Project.objects.create(id='prepared-project')
    session = Session.objects.create(id='prepared-session', project=project, provider='codex')
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP,
                               kind=ItemKind.USER_MESSAGE)
    with connection.cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost = 0.0000012 WHERE session_id = %s', [session.id])
    operation = begin_compute_application({'session_id': session.id, 'history_facts': []})
    while operation.phase != 'final':
        publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
    try:
        yield operation
    finally:
        close_compute_application(operation)


@pytest.fixture
def wal_operation(operation, tmp_path):
    # Pin this operation to a copied, isolated WAL database without changing settings.
    path = tmp_path / 'activity.sqlite3'
    physical = connection.connection
    replacement = sqlite3.connect(path, isolation_level=None)
    physical.backup(replacement)
    replacement.execute('PRAGMA journal_mode=WAL')
    replacement.execute('PRAGMA foreign_keys=ON')
    connection.connection = replacement
    operation.references._connection = replacement
    try:
        yield operation, path
    finally:
        close_compute_application(operation)
        connection.connection = physical
        replacement.close()


@pytest.fixture
def creation_operation(operation, request):
    close_compute_application(operation)
    stored = getattr(request, 'param', True)
    Session.objects.filter(pk='prepared-session').update(created_at=STAMP if stored else None)
    operation = begin_compute_application({'session_id': 'prepared-session', 'history_facts': [],
                                           'session_fields': {'created_at': STAMP.isoformat()}})
    while operation.phase != 'final':
        publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
    try:
        yield operation
    finally:
        close_compute_application(operation)


def enumerate_all(module, state, row_budget=1):
    while not state.enumeration_complete and state.fallback_reason is None:
        module.prepare_activity_key_page(state, row_budget=row_budget)
    return state


def repair_all(module, state):
    for key in state.ordered_keys:
        if state.fallback_reason is None:
            assert module.publish_activity_key(state, key).published


def journal(operation, *, keys=(), source_changed=True):
    from twicc.providers.compute_repair_journal import merge_compute_repair
    with transaction.atomic():
        return merge_compute_repair(operation.message['session_id'], operation.message['observed_incarnation'],
                                    activity_keys=keys, source_changed=source_changed)


@pytest.mark.parametrize('creation_operation', [False, True], indirect=True)
def test_creation_only_inventory_needs_no_preliminary_repairs(creation_operation, monkeypatch):
    # Reusing creation buckets wastes writer grants and can preserve stale counts.
    operation = creation_operation
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert len(state.required_keys) == 4
    assert len(state.skipped_keys) == 4
    assert state.ordered_keys == ()
    assert not state.repaired_keys
    assert operation.activity_repair_keys <= state.required_keys

    repairs = []
    for model in (DailyActivity, WeeklyActivity):
        original = model.recalculate
        def recalculate(*args, model=model, original=original):
            repairs.append((model, *args))
            return original(*args)
        monkeypatch.setattr(model, 'recalculate', recalculate)
    final = final_compute_message(operation, frozenset())
    assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    assert len(repairs) == 4
    for model in (DailyActivity, WeeklyActivity):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT SUM(cost), SUM(session_count) FROM {model._meta.db_table}')
            cost, count = cursor.fetchone()
        assert cost == pytest.approx(0.0000024, abs=1e-15)
        assert count == 2


@pytest.mark.parametrize('durable', [False, True])
def test_skipped_first_page_does_not_end_mixed_inventory(operation, monkeypatch, durable):
    # Empty preliminary work on one page must not hide later historical buckets.
    Session.objects.filter(pk='prepared-session').update(created_at=STAMP)
    later = STAMP + timedelta(days=14)
    SessionItem.objects.create(session_id='prepared-session', line_num=2, content='{}', timestamp=later,
                               cost=Decimal('0.000003'))
    close_compute_application(operation)
    operation = begin_compute_application({'session_id': 'prepared-session', 'history_facts': []})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        old = (DailyActivity, None, 'codex', STAMP.date() - timedelta(days=50))
        if durable:
            journal(operation, keys=operation.activity_repair_keys | {old})
        module = api()
        state = module.begin_activity_preparation(operation)
        page = module.prepare_activity_key_page(state, row_budget=1)
        assert page.rows == 1 and not page.complete
        assert len(state.required_keys) == 4 + int(durable) and not state.ordered_keys
        enumerate_all(module, state)
        assert state.enumeration_cursor == 2
        assert len(state.required_keys) == 8 + int(durable)
        assert len(state.ordered_keys) == 4 + int(durable)
        if durable:
            assert old in state.ordered_keys and old in operation.activity_repair_keys
        repair_all(module, state)
        assert state.repaired_keys == state.required_keys - state.skipped_keys
        from tests.test_activity_final_admission import policy
        assert policy(module, state) == set(state.ordered_keys)

        final_repairs = []
        for model in (DailyActivity, WeeklyActivity):
            original = model.recalculate
            def recalculate(*args, model=model, original=original):
                final_repairs.append((model, *args))
                return original(*args)
            monkeypatch.setattr(model, 'recalculate', recalculate)
        assert BaseSessionCompute._apply_session_complete_final(
            final_compute_message(operation, frozenset())).outcome == 'applied'
        assert len(final_repairs) == 4
        for model in (DailyActivity, WeeklyActivity):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT SUM(cost), SUM(session_count) FROM {model._meta.db_table}')
                cost, count = cursor.fetchone()
            assert cost == pytest.approx(0.0000084, abs=1e-15)
            assert count == 2
    finally:
        close_compute_application(operation)


@pytest.mark.parametrize('changed', [None, STAMP + timedelta(days=14)])
def test_skipped_creation_keys_stay_excluded_after_metadata_changes(creation_operation, changed):
    # Final metadata must not turn skipped keys into reusable evidence.
    operation = creation_operation
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    repair_all(module, state)
    Session.objects.filter(pk='prepared-session').update(created_at=changed)
    from tests.test_activity_final_admission import policy
    assert policy(module, state) == set()
    assert state.fallback_reason is None
    assert len(state.required_keys) == 4
    assert not state.repaired_keys


@pytest.mark.parametrize('serialized', [False, True])
def test_stored_and_effective_creation_union_skips_both_dates(operation, serialized):
    # The worker's new creation date needs exact repair as well as the stored date.
    close_compute_application(operation)
    later = STAMP + timedelta(days=14)
    Session.objects.filter(pk='prepared-session').update(created_at=STAMP)
    SessionItem.objects.create(session_id='prepared-session', line_num=2, content='{}', timestamp=later)
    operation = begin_compute_application({'session_id': 'prepared-session', 'history_facts': [],
        'session_fields': {'created_at': later.isoformat() if serialized else later}})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        module = api()
        state = enumerate_all(module, module.begin_activity_preparation(operation))
        assert len(state.required_keys) == 8
        assert state.ordered_keys == ()
        assert state.skipped_keys == state.required_keys
        assert operation.activity_repair_keys == state.required_keys
        assert BaseSessionCompute._apply_session_complete_final(
            final_compute_message(operation, frozenset())).outcome == 'applied'
        assert Session.objects.get(pk='prepared-session').created_at == later
        for model in (DailyActivity, WeeklyActivity):
            assert model.objects.aggregate(count=Sum('session_count'))['count'] == 2
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT SUM(cost) FROM {model._meta.db_table}')
                assert cursor.fetchone()[0] == pytest.approx(0.0000024, abs=1e-15)
    finally:
        close_compute_application(operation)


@pytest.mark.parametrize('mutation', ['foreign_commit', 'journal_changed'])
def test_all_skipped_final_still_checks_version_and_journal(creation_operation, tmp_path, mutation):
    # Zero preliminary grants must not bypass fixed-version or journal fences.
    operation = creation_operation
    path = tmp_path / 'all-skipped.sqlite3'
    physical = connection.connection
    replacement = sqlite3.connect(path, isolation_level=None)
    physical.backup(replacement)
    replacement.execute('PRAGMA journal_mode=WAL')
    connection.connection = replacement
    operation.references._connection = replacement
    try:
        check_all_skipped_final(module=api(), operation=operation, path=path, mutation=mutation)
    finally:
        close_compute_application(operation)
        connection.connection = physical
        replacement.close()


def check_all_skipped_final(*, module, operation, path, mutation):
    snapshot = journal(operation)
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert state.ordered_keys == ()
    baseline = state.data_version
    if mutation == 'foreign_commit':
        with sqlite3.connect(path) as foreign:
            foreign.execute('UPDATE core_sessionitem SET cost = 2')
    else:
        journal(operation)
    from tests.test_activity_final_admission import policy
    assert policy(module, state, repair_snapshot=snapshot) is None
    assert state.fallback_reason == mutation
    assert state.data_version == baseline
    assert module.begin_activity_preparation(operation) is state
    assert len(state.required_keys) == 4 and not state.repaired_keys
    assert BaseSessionCompute._apply_session_complete_final(
        final_compute_message(operation, frozenset())).outcome == 'applied'
    if mutation == 'foreign_commit':
        assert DailyActivity.objects.aggregate(cost=Sum('cost'))['cost'] == Decimal(4)


def test_all_skipped_superseded_cleanup_preserves_repair_inventory(creation_operation):
    operation = creation_operation
    journal(operation)
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert state.ordered_keys == ()
    obligations = set(operation.activity_repair_keys)
    assert len(obligations) == 4
    Session.objects.filter(pk='prepared-session').update(history_epoch=operation.message['observed_epoch'] + 1)
    assert BaseSessionCompute._apply_session_complete_final(
        final_compute_message(operation, frozenset())).outcome == 'superseded'
    close_compute_application(operation)
    assert state.fallback_reason == 'closed'
    assert not state.required_keys and not state.repaired_keys and not state.skipped_keys
    assert operation.activity_repair_keys == obligations
    assert operation.closed


def test_preparation_adopts_complete_durable_union_without_clearing_keys(operation):
    module = api()
    old = (DailyActivity, None, 'codex', STAMP.date() - timedelta(days=50))
    snapshot = journal(operation, keys={old})
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert state.repair_observation == snapshot.observation
    assert operation.repair_snapshot == snapshot
    assert old in state.required_keys
    assert module.publish_activity_key(state, state.ordered_keys[0]).published
    from twicc.providers.compute_repair_journal import load_compute_repair
    assert load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation) == snapshot


def test_same_connection_generation_change_rejects_bucket_publication(operation):
    module = api()
    journal(operation)
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    journal(operation)  # Same keys and same physical connection; actual source generation changes.
    assert not module.publish_activity_key(state, state.ordered_keys[0]).published
    assert state.fallback_reason == 'journal_changed'


def test_durable_capacity_keeps_unbounded_inventory(operation):
    module = api()
    keys = {(DailyActivity, None, 'codex', STAMP.date() - timedelta(days=day)) for day in range(4097)}
    snapshot = journal(operation, keys=keys)
    state = module.begin_activity_preparation(operation)
    assert state.fallback_reason == 'capacity'
    assert operation.activity_repair_keys >= keys
    from twicc.providers.compute_repair_journal import load_compute_repair
    assert load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation) == snapshot


def test_one_bucket_per_guarded_publication_preserves_raw_sum(operation):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert len(state.required_keys) == 4
    assert state.repaired_keys == set()
    for key in state.ordered_keys:
        with CaptureQueriesContext(connection) as queries:
            result = module.publish_activity_key(state, key)
        statements = [entry['sql'] for entry in queries if entry['sql'] not in ('BEGIN', 'COMMIT')]
        assert statements[0].startswith('UPDATE "core_session"')
        assert result.published
    assert state.repaired_keys == state.required_keys
    for model in (DailyActivity, WeeklyActivity):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT SUM(cost) FROM {model._meta.db_table}')
            assert cursor.fetchone()[0] == pytest.approx(0.0000024, abs=1e-15)
        assert model.objects.aggregate(cost=Sum('cost'))['cost'] == Decimal('0.0000024')
    # Task 6B still sends no prepared cache to final application.
    final = final_compute_message(operation, frozenset())
    assert '_activity_preparation' not in final
    assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'


@pytest.mark.parametrize('position', ['enumeration', 'between_repairs'])
@pytest.mark.parametrize('mutation', ['unrelated', 'current_replay'])
@pytest.mark.parametrize('durable', [False, True])
def test_foreign_commits_discard_all_keys_and_keep_exact_obligations(wal_operation, position, mutation, durable):
    module = api()
    operation, path = wal_operation
    snapshot = journal(operation) if durable else None
    old_key = (DailyActivity, 'prepared-project', 'codex', STAMP.date() - timedelta(days=1))
    operation.activity_repair_keys.add(old_key)
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    if position == 'between_repairs':
        assert module.publish_activity_key(state, state.ordered_keys[0]).published
    with sqlite3.connect(path) as foreign:
        if mutation == 'unrelated':
            foreign.execute("UPDATE core_project SET name = 'foreign'")
        else:
            # Replay updates an existing current row, with no aggregate adapter/delta.
            foreign.execute('UPDATE core_sessionitem SET cost = 2')
    result = module.publish_activity_key(state, state.ordered_keys[state.repair_cursor])
    assert not result.published
    assert state.fallback_reason == 'foreign_commit'
    assert state.repaired_keys == set()
    assert old_key in operation.activity_repair_keys
    assert module.begin_activity_preparation(operation) is state  # No restart loop.
    if durable:
        from twicc.providers.compute_repair_journal import load_compute_repair
        assert load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation) == snapshot
    final = final_compute_message(operation, frozenset())
    assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    assert old_key in final['_activity_repair_keys']
    if mutation == 'current_replay':
        assert DailyActivity.objects.aggregate(cost=Sum('cost'))['cost'] == Decimal(4)


def test_foreign_commit_between_enumeration_pages(wal_operation):
    module = api()
    operation, path = wal_operation
    state = module.begin_activity_preparation(operation)
    module.prepare_activity_key_page(state, row_budget=1)
    with sqlite3.connect(path) as foreign:
        foreign.execute("UPDATE core_project SET name = 'page-gap'")
    module.prepare_activity_key_page(state, row_budget=1)
    assert state.fallback_reason == 'foreign_commit'
    assert not state.enumeration_complete
    assert state.repaired_keys == set()


def test_foreign_commit_before_completion_callback_keeps_fixed_version(wal_operation, monkeypatch):
    module = api()
    operation, path = wal_operation
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    version = state.data_version
    original = connection.run_and_clear_commit_hooks
    def foreign_before_callback():
        with sqlite3.connect(path) as foreign:
            foreign.execute('UPDATE core_sessionitem SET cost = 7')
        original()
    monkeypatch.setattr(connection, 'run_and_clear_commit_hooks', foreign_before_callback)
    module.publish_activity_key(state, state.ordered_keys[0])
    monkeypatch.setattr(connection, 'run_and_clear_commit_hooks', original)
    assert state.data_version == version
    result = module.publish_activity_key(state, state.ordered_keys[state.repair_cursor])
    assert not result.published
    assert state.fallback_reason == 'foreign_commit'
    assert state.repaired_keys == set()


@pytest.mark.parametrize('durable', [False, True])
def test_same_version_replacement_connection_does_not_prove_identity(wal_operation, durable):
    module = api()
    operation, path = wal_operation
    if durable:
        journal(operation)
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    physical = connection.connection
    replacement = sqlite3.connect(path, isolation_level=None)
    assert replacement.execute('PRAGMA main.data_version').fetchone()[0] == state.data_version
    connection.connection = replacement
    try:
        result = module.publish_activity_key(state, state.ordered_keys[0])
        assert not result.published
        assert state.fallback_reason == 'connection_changed'
        assert state.repaired_keys == set()
    finally:
        connection.connection = physical
        replacement.close()


@pytest.mark.parametrize('failure', ['rollback', 'callback', 'cancellation'])
def test_failed_publication_never_publishes_new_evidence(operation, monkeypatch, failure):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    key = state.ordered_keys[0]
    model = key[0]
    original = model.recalculate
    def fail(*args):
        original(*args)
        if failure == 'callback':
            transaction.on_commit(lambda: (_ for _ in ()).throw(RuntimeError('callback failure')))
        elif failure == 'cancellation':
            raise asyncio.CancelledError()
        else:
            raise RuntimeError('repair rollback')
    monkeypatch.setattr(model, 'recalculate', fail)
    if failure == 'cancellation':
        with pytest.raises(asyncio.CancelledError):
            module.publish_activity_key(state, key)
    else:
        assert not module.publish_activity_key(state, key).published
    assert state.repaired_keys == set()
    assert state.repair_cursor == 0
    assert state.fallback_reason is not None
    assert model.objects.exists() == (failure == 'callback')


@pytest.mark.parametrize('method', ['begin', 'page', 'publish'])
def test_outer_transaction_is_never_accepted(operation, method):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation)) if method != 'begin' else None
    with transaction.atomic():
        Session.objects.get(pk=operation.message['session_id'])  # Retained main read snapshot.
        if method == 'begin':
            state = module.begin_activity_preparation(operation)
        elif method == 'page':
            module.prepare_activity_key_page(state)
        else:
            module.publish_activity_key(state, state.ordered_keys[0])
    assert state.fallback_reason == 'transaction_open'
    assert state.repaired_keys == set()


def test_raw_stale_snapshot_cannot_enter_publication(wal_operation):
    module = api()
    operation, path = wal_operation
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    physical = connection.connection
    physical.execute('BEGIN')
    physical.execute('SELECT COUNT(*) FROM core_sessionitem').fetchone()
    with sqlite3.connect(path) as foreign:
        foreign.execute('UPDATE core_sessionitem SET cost = 9')
    try:
        assert not module.publish_activity_key(state, state.ordered_keys[0]).published
        assert state.fallback_reason == 'transaction_open'
    finally:
        physical.rollback()


@pytest.mark.parametrize('count', [4095, 4096, 4097])
def test_capacity_selects_fallback_without_losing_retained_obligations(operation, count):
    module = api()
    # Old-only retained keys are independent of preparation's bounded inventory.
    keys = {(model, scope, 'codex', day)
            for model, day in ((DailyActivity, STAMP.date()), (WeeklyActivity, STAMP.date() - timedelta(days=1)))
            for scope in ('prepared-project', None)}
    keys.update((DailyActivity, 'prepared-project', 'codex', STAMP.date() + timedelta(days=index + 1))
                for index in range(count - 4))
    operation.activity_repair_keys.update(keys)
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert state.fallback_reason == ('capacity' if count > 4096 else None)
    assert state.enumeration_complete == (count <= 4096)
    assert len(state.required_keys) <= 4096
    assert keys <= operation.activity_repair_keys
    assert not state.repaired_keys


@pytest.mark.parametrize('durable', [False, True])
def test_missing_or_unknown_key_disables_preparation(operation, durable):
    if durable:
        journal(operation)
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    missing = state.ordered_keys[0]
    state.required_keys.remove(missing)
    assert not module.publish_activity_key(state, missing).published
    assert state.fallback_reason == 'missing_key'
    assert not state.repaired_keys


def test_source_phase_change_disables_preparation(operation):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    operation.phase = 'metadata'
    assert not module.publish_activity_key(state, state.ordered_keys[0]).published
    assert state.fallback_reason == 'source_incomplete'


@pytest.mark.parametrize('durable', [False, True])
def test_close_and_cancel_release_operation_evidence(operation, durable):
    snapshot = journal(operation) if durable else None
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert module.publish_activity_key(state, state.ordered_keys[0]).published
    module.close_activity_preparation(state, reason='cancelled')
    assert not state.repaired_keys
    assert state.fallback_reason == 'cancelled'
    assert module.begin_activity_preparation(operation) is state
    close_compute_application(operation)
    assert operation.closed
    if durable:
        from twicc.providers.compute_repair_journal import load_compute_repair
        assert load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation) == snapshot


def test_enumeration_reads_bounded_metadata_and_unions_removed_keys(operation):
    module = api()
    old = (DailyActivity, 'prepared-project', 'codex', STAMP.date() - timedelta(days=2))
    operation.activity_repair_keys.add(old)
    state = module.begin_activity_preparation(operation)
    with CaptureQueriesContext(connection) as queries:
        page = module.prepare_activity_key_page(state, row_budget=1)
    assert page.rows == 1
    assert not page.complete
    item_queries = [query['sql'] for query in queries if 'FROM "core_sessionitem"' in query['sql']]
    assert len(item_queries) == 1
    assert 'LIMIT 1' in item_queries[0]
    assert '"content"' not in item_queries[0] and '"cost"' not in item_queries[0]
    assert '"timestamp"' in item_queries[0]
    enumerate_all(module, state)
    assert len(state.required_keys) == 5
    assert old in state.required_keys
    assert state.physical_connection is operation.connection_identity
    assert not connection.in_atomic_block
    assert not connection.connection.in_transaction


def test_foreign_commit_during_page_read_discards_inventory(wal_operation):
    module = api()
    operation, path = wal_operation
    state = module.begin_activity_preparation(operation)
    def interleave(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "core_sessionitem"' in sql:
            with sqlite3.connect(path) as foreign:
                foreign.execute("UPDATE core_project SET name = 'during-page'")
        return result
    with connection.execute_wrapper(interleave):
        page = module.prepare_activity_key_page(state, row_budget=1)
    assert state.fallback_reason == 'foreign_commit'
    assert page.cursor == -1
    assert not state.repaired_keys


def test_later_rollback_retains_prior_committed_evidence_but_disables_permission(operation, monkeypatch):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    first, second = state.ordered_keys[:2]
    assert module.publish_activity_key(state, first).published
    original = second[0].recalculate
    def rollback(*args):
        original(*args)
        raise RuntimeError('later rollback')
    monkeypatch.setattr(second[0], 'recalculate', rollback)
    assert not module.publish_activity_key(state, second).published
    assert state.repaired_keys == {first}
    assert state.repair_cursor == 1
    assert state.fallback_reason == 'publication_failed'
    assert not module.publish_activity_key(state, second).published


def test_unknown_key_disables_every_prepared_permission(operation):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert module.publish_activity_key(state, state.ordered_keys[0]).published
    unknown = (DailyActivity, 'prepared-project', 'codex', STAMP.date() - timedelta(days=50))
    assert not module.publish_activity_key(state, unknown).published
    assert not state.repaired_keys
    assert state.fallback_reason == 'missing_key'


def test_source_incomplete_at_begin_cannot_restart(operation):
    module = api()
    operation.phase = 'reconciliation'
    state = module.begin_activity_preparation(operation)
    assert state.fallback_reason == 'source_incomplete'
    assert state.data_version is None
    operation.phase = 'final'
    assert module.begin_activity_preparation(operation) is state
    assert not module.prepare_activity_key_page(state).complete


def test_operation_cleanup_discards_committed_keys(operation):
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    assert module.publish_activity_key(state, state.ordered_keys[0]).published
    obligations = set(operation.activity_repair_keys)
    close_compute_application(operation)
    assert state.fallback_reason == 'closed'
    assert not state.required_keys and not state.repaired_keys
    assert operation.activity_repair_keys == obligations


def test_closed_physical_connection_selects_explicit_fallback(wal_operation):
    module = api()
    operation, _ = wal_operation
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    connection.connection.close()
    assert not module.publish_activity_key(state, state.ordered_keys[0]).published
    assert state.fallback_reason == 'connection_unusable'


@pytest.mark.parametrize('durable', [False, True])
def test_callback_failure_after_settlement_cannot_leave_new_evidence(operation, monkeypatch, durable):
    snapshot = journal(operation) if durable else None
    module = api()
    state = enumerate_all(module, module.begin_activity_preparation(operation))
    original = connection.run_and_clear_commit_hooks
    def fail_after_settle():
        connection.run_on_commit.append((set(), lambda: (_ for _ in ()).throw(RuntimeError('late callback')), False))
        original()
    monkeypatch.setattr(connection, 'run_and_clear_commit_hooks', fail_after_settle)
    assert not module.publish_activity_key(state, state.ordered_keys[0]).published
    assert state.repaired_keys == set()
    assert state.repair_cursor == 0
    assert state.fallback_reason == 'publication_failed'
    if durable:
        from twicc.providers.compute_repair_journal import load_compute_repair
        assert load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation) == snapshot


def test_preparation_repairs_normalized_costs_after_metadata_without_legacy_cache():
    module = api()
    project = Project.objects.create(id='normalized-project')
    session = Session.objects.create(id='normalized-session', project=project, provider='codex')
    items = [SessionItem.objects.create(session=session, line_num=line, content='{}', timestamp=STAMP)
             for line in (1, 2)]
    with connection.cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost = 0.0000006 WHERE session_id = %s', [session.id])
    legacy = {(model, scope, 'codex', day)
              for model, day in ((DailyActivity, STAMP.date()), (WeeklyActivity, STAMP.date() - timedelta(days=1)))
              for scope in (project.id, None)}
    for model, scope, provider, day in legacy:
        from twicc.core.enums import Provider
        model.recalculate(scope, day, Provider(provider))
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        '_repaired_activity_buckets': legacy, 'item_fields': ['cost'],
        'item_updates': [{'id': item.id, 'cost': '0.000001'} for item in items]})
    try:
        while operation.phase != 'final':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        state = enumerate_all(module, module.begin_activity_preparation(operation))
        assert not state.repaired_keys  # Legacy membership supplies no private proof.
        repair_all(module, state)
        for model in (DailyActivity, WeeklyActivity):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT SUM(cost) FROM {model._meta.db_table}')
                assert cursor.fetchone()[0] == pytest.approx(0.000004, abs=1e-15)
            assert model.objects.aggregate(total=Sum('cost'))['total'] == Decimal('0.000004')
    finally:
        close_compute_application(operation)
