"""Periodic discovery selects only hot extracted roots before applying its limit."""

import asyncio
from contextlib import closing
from datetime import timedelta

import pytest
from django.utils import timezone

from tests import test_content_storage_admission
from tests.test_content_root_state import session
from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage.lifecycle import active_session_ids, update_lifecycle
from twicc.core.content_storage.policy import COLD_DATE_FIELDS, cold_from_fields
from twicc.core.models import Session, SessionItem
from twicc.providers.compute_work import ComputeWorkState

writer_state = test_content_storage_admission.writer_state
pytestmark = pytest.mark.django_db(transaction=True)


def extracted(name, **kwargs):
    return session(name, storage_incarnation=f'inc-{name}', content_storage_version=1, **kwargs)


@pytest.mark.parametrize('archived,hidden', [(False, False), (False, True), (True, False), (True, True)])
def test_sql_hot_matches_scalar_policy(archived, hidden):
    from twicc.core.content_storage import policy

    now = timezone.now()
    rows = []
    inputs = [{name: None for name in COLD_DATE_FIELDS}]
    for latest in COLD_DATE_FIELDS:
        for days in (15, 30):
            for delta in (-1, 0, 1):
                dates = dict.fromkeys(COLD_DATE_FIELDS, None)
                dates[latest] = now - timedelta(days=days) + timedelta(microseconds=delta)
                inputs.append(dates)
                mixed = dict.fromkeys(COLD_DATE_FIELDS, now - timedelta(days=90))
                mixed[latest] = dates[latest]
                inputs.append(mixed)
        inputs.append({**dict.fromkeys(COLD_DATE_FIELDS, None), latest: now + timedelta(days=1)})
    for index, dates in enumerate(inputs):
        rows.append(extracted(f'case-{index:03}', archived=archived, hidden=hidden, **dates))
    old = extracted('active-old', archived=archived, hidden=hidden, created_at=now - timedelta(days=90))
    child = extracted('active-child', type='subagent', parent_session=old, created_at=now - timedelta(days=90))
    inactive = extracted('inactive-old', created_at=now - timedelta(days=90))
    active_leaf = extracted('active-leaf', type='subagent', parent_session=inactive,
                            created_at=now - timedelta(days=90))
    active = frozenset({old.id, child.id, active_leaf.id})
    expected = {row.id for row in rows if not cold_from_fields(row.__dict__, now)} | {old.id, child.id, active_leaf.id}
    assert set(Session.objects.filter(policy.content_hot_q(now, active)).values_list('id', flat=True)) == expected
    token = active_session_ids.set(active)
    try:
        assert set(maintenance.periodic_hot_root_page(None, now)) == expected - {child.id, active_leaf.id}
    finally:
        active_session_ids.reset(token)


def test_periodic_page_filters_before_limit():
    now = timezone.now()
    hot = []
    for index in range(140):
        extracted(f'{index:03}-cold', created_at=now - timedelta(days=90))
        if index % 3 == 0:
            root = extracted(f'{index:03}-hot')
            hot.append(root.id)
            extracted(f'{index:03}-child', type='subagent', parent_session=root)
    # More than one complete selected page proves cursor advancement after filtering.
    for index in range(140, 280):
        hot.append(extracted(f'{index:03}-hot').id)
    selected, after = [], None
    while page := maintenance.periodic_hot_root_page(after, now):
        selected.extend(page)
        after = page[-1]
    assert selected == sorted(hot)


def test_periodic_page_requires_extracted_identity():
    root = extracted('eligible')
    session('missing-identity', content_storage_version=1)
    session('wrong-version', storage_incarnation='wrong', content_storage_version=0)
    extracted('invalid-root', parent_session=root)
    assert maintenance.periodic_hot_root_page(None, timezone.now()) == [root.id]


def test_startup_discovery_keeps_hot_nested_subagents():
    root = extracted('root')
    child = extracted('child', type='subagent', parent_session=root)
    extracted('leaf', type='subagent', parent_session=child)
    assert maintenance.pending_session_page() == ['child', 'leaf', 'root']
    assert maintenance.periodic_hot_root_page(None, timezone.now()) == ['root']


@pytest.fixture
def periodic_scheduler(monkeypatch, settings):
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'pending_session_page', lambda *args: [])
    clock = [0]
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    async def wait(stop, wake, timeout):
        if clock[0] == 0:
            clock[0] = 1800
        else:
            stop.set()

    monkeypatch.setattr(maintenance, '_wait', wait)
    return clock


def test_periodic_restores_hot_root_without_descendants(
    writer_state, compute_executor_started, content_row_factory, periodic_scheduler,
):
    root = extracted('root')
    children = [extracted('hot-child', type='subagent', parent_session=root),
                extracted('cold-child', type='subagent', parent_session=root,
                          created_at=timezone.now() - timedelta(days=90))]
    rows = []
    for owner in [root, *children]:
        row = content_row_factory(f'{owner.id} content ' * 1000, True, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
        rows.append(row)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['plain', 'zstd', 'zstd']


def test_periodic_root_selection_pauses_without_resetting_cursor(
    writer_state, compute_executor_started, periodic_scheduler, monkeypatch,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    extracted('first')
    extracted('second')
    monkeypatch.setattr(maintenance, 'TREE_PAGE_SIZE', 1)
    original = maintenance.periodic_hot_root_page
    work, calls, selected = ComputeWorkState(), [], []
    loop = None

    def page(after, now):
        calls.append((after, now))
        with CaptureQueriesContext(connection) as queries:
            result = original(after, now)
        selected.extend(result)
        if len(calls) == 1:
            loop.call_soon_threadsafe(work.set_pending, 'codex', True)
        if after == 'first':
            assert "'first'" in queries[0]['sql']
        return result

    async def wait(stop, wake, timeout):
        if periodic_scheduler[0] == 0:
            periodic_scheduler[0] = 1800
        elif work.has_pending():
            assert timeout is None and len(calls) == 1
            periodic_scheduler[0] = 2400
            work.set_pending('codex', False)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'periodic_hot_root_page', page)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def run():
        nonlocal loop
        loop = asyncio.get_running_loop()
        await maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work)

    asyncio.run(run())
    assert selected == ['first', 'second']
    assert [after for after, _ in calls] == [None, 'first', 'second']
    assert len({now for _, now in calls}) == 1


def test_root_activated_below_cursor_uses_targeted_restoration(
    writer_state, compute_executor_started, content_row_factory, periodic_scheduler, monkeypatch,
):
    old = extracted('a-old', created_at=timezone.now() - timedelta(days=90))
    extracted('z-hot')
    row = content_row_factory('restore below cursor ' * 1000, True, session_ids=[old.id])
    SessionItem.objects.create(session=old, line_num=1, content='', current_content=row)
    original, cursors = maintenance.periodic_hot_root_page, []

    def page(after, now):
        cursors.append(after)
        if after == 'z-hot':
            update_lifecycle(Session.objects.filter(pk=old.id), last_started_at=timezone.now())
        return original(after, now)

    monkeypatch.setattr(maintenance, 'periodic_hot_root_page', page)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    row.refresh_from_db()
    assert row.codec == 'plain'
    assert cursors == [None, 'z-hot']


def test_production_metadata_query_uses_plain_index(content_row_factory):
    from django.db import connection

    for index in range(200):
        content_row_factory(f'mixed {index} ' * 20, index % 10 != 0)
    query = maintenance._compression_metadata_query(50, 10)
    sql, params = query.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
        plan = '\n'.join(row[3] for row in cursor.fetchall())
    assert 'idx_content_plain_id' in plan
    assert 'SEARCH' in plan and 'id>?' in plan
    assert 'TEMP B-TREE' not in plan


def test_compressed_rows_never_enter_metadata_page(content_row_factory):
    plain = []
    for index in range(6):
        content_row_factory(f'compressed {index}', True)
        plain.append(content_row_factory(f'plain {index}', False).id)
    assert [row['id'] for row in maintenance._compression_metadata_query(0, 3)] == plain[:3]
    assert [row['id'] for row in maintenance._compression_metadata_query(plain[2], 3)] == plain[3:]


def test_all_rejected_plain_page_advances_cursor(content_row_factory):
    from tests.test_content_maintenance import cold, policy
    from tests.test_content_maintenance_reads import payload_selects
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache

    owner = cold('cold')
    hot = session('hot')
    no_gain = content_row_factory('no gain', False, session_ids=[owner.id])
    no_gain.compression_no_gain = True
    no_gain.compression_attempt_version = policy()
    no_gain.save()
    rejected = content_row_factory('hot content', False, session_ids=[hot.id])
    eligible = content_row_factory('eligible content ' * 100, False, session_ids=[owner.id])
    with closing(MaintenanceSessionCache()) as cache:
        with CaptureQueriesContext(connection) as queries:
            page = maintenance.prepare_compression_batch(0, row_budget=2, session_cache=cache)
        assert page.cursor == rejected.id and not page.done and page.entries == ()
        assert page.counts.no_gain_skips == page.counts.ineligible_owners == 1
        assert payload_selects(queries) == []
        next_page = maintenance.prepare_compression_batch(page.cursor, row_budget=2, session_cache=cache)
        assert next_page.done and [entry.candidate.content_id for entry in next_page.entries] == [eligible.id]


def test_trailing_compressed_rows_finish_plain_scan(content_row_factory):
    from tests.test_content_maintenance import cold
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache

    owner = cold('cold')
    plain = content_row_factory('plain content ' * 100, False, session_ids=[owner.id])
    content_row_factory('trailing compressed', True)
    with closing(MaintenanceSessionCache()) as cache:
        page = maintenance.prepare_compression_batch(0, row_budget=1, session_cache=cache)
    assert page.cursor == plain.id and page.done and len(page.entries) == 1


def test_all_compressed_table_finishes_in_one_empty_page(content_row_factory):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from tests.test_content_maintenance_reads import payload_selects, owner_selects
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache

    for index in range(105):
        content_row_factory(f'compressed {index}', True, session_ids=['unresolved'])
    with closing(MaintenanceSessionCache()) as cache, CaptureQueriesContext(connection) as queries:
        page = maintenance.prepare_compression_batch(0, session_cache=cache)
        result = maintenance._publish(page.entries, frozenset(), session_cache=cache)
    assert page.cursor == 0 and page.done and page.entries == () and page.counts.scanned == 0
    assert result == maintenance.PublicationResult()
    assert len(queries) == 1
    assert payload_selects(queries) == owner_selects(queries) == []
    assert not any(query['sql'].startswith(('UPDATE', 'INSERT', 'DELETE')) for query in queries)


def test_remaining_plain_rows_compressed_concurrently_finish_scan(content_row_factory):
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
    from twicc.core.models import SessionItemContent

    first = content_row_factory('first', False)
    remaining = content_row_factory('remaining', False)
    with closing(MaintenanceSessionCache()) as cache:
        page = maintenance.prepare_compression_batch(0, row_budget=1, session_cache=cache)
        assert page.cursor == first.id and not page.done
        SessionItemContent.objects.filter(pk=remaining.id).update(codec='zstd')
        page = maintenance.prepare_compression_batch(page.cursor, row_budget=1, session_cache=cache)
    assert page.cursor == first.id and page.done and page.entries == () and page.counts.scanned == 0


def test_index_membership_changes_preserve_cursor(content_row_factory):
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
    from twicc.core.models import SessionItemContent

    below = content_row_factory('below', True)
    first = content_row_factory('first', False)
    removed = content_row_factory('removed', False)
    with closing(MaintenanceSessionCache()) as cache:
        page = maintenance.prepare_compression_batch(0, row_budget=1, session_cache=cache)
        assert page.cursor == first.id and not page.done
        SessionItemContent.objects.filter(pk__in=[first.id, removed.id]).update(codec='zstd')
        SessionItemContent.objects.filter(pk=below.id).update(codec='plain', dictionary=None)
        inserted = content_row_factory('inserted', False)
        page = maintenance.prepare_compression_batch(page.cursor, row_budget=1, session_cache=cache)
        assert page.cursor == inserted.id and page.done and page.counts.scanned == 1
        # Restored rows below the cursor enter the next complete pass.
        page = maintenance.prepare_compression_batch(0, row_budget=1, session_cache=cache)
        assert page.cursor == below.id and not page.done
