"""Pass metadata reuse remains safe across committed lifecycle changes."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.content_storage import cold_cache, lifecycle, state
from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
from twicc.core.content_storage.policy import COLD_FIELDS
from twicc.core.models import Project, Session

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(name, **fields):
    project, _ = Project.objects.get_or_create(id='maintenance-cache', defaults={'directory': '/maintenance-cache'})
    fields.setdefault('created_at', timezone.now() - timedelta(days=31))
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name, **fields)


@pytest.fixture
def cache():
    value = MaintenanceSessionCache()
    yield value
    value.close()


def decide(cache, ids, now=None, active=frozenset()):
    return cache.decisions(ids, now or timezone.now(), active)


def test_grouped_metadata_misses_and_warm_publication(cache):
    ids = [make_session(f's-{i}').id for i in range(260)]
    with CaptureQueriesContext(connection) as queries:
        assert all(item.is_cold for item in decide(cache, ids).values())
    assert len(queries) == 3
    assert set(cache._metadata) == set(ids)
    assert all(set(row) == {'id', *COLD_FIELDS} for row in cache._metadata.values())
    with CaptureQueriesContext(connection) as queries:
        for page in (ids[:100], ids[100:200], ids[200:]):
            decide(cache, page)
            with transaction.atomic():
                decide(cache, page)
    assert not [query for query in queries if query['sql'].startswith('SELECT')]


def test_own_fields_and_runtime_activity_are_evaluated_afresh(cache):
    now = timezone.now()
    parent = make_session('parent', created_at=now)
    child = make_session('child', type='subagent', parent_session=parent, created_at=now - timedelta(days=30))
    assert decide(cache, [child.id], now)[child.id].is_cold
    with CaptureQueriesContext(connection) as queries:
        assert not decide(cache, [child.id], now - timedelta(microseconds=1))[child.id].is_cold
        assert not decide(cache, [child.id], now, frozenset({child.id}))[child.id].is_cold
        assert decide(cache, [child.id], now, frozenset({parent.id}))[child.id].is_cold
    assert len(queries) == 0


def test_commit_invalidates_only_changed_owner_and_rollback_retains(cache):
    parent = make_session('parent')
    child = make_session('child', type='subagent', parent_session=parent)
    decide(cache, [parent.id, child.id])
    retained = cache._metadata[parent.id]
    with transaction.atomic():
        lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_updated_at=timezone.now())
        assert child.id in cache._metadata
        assert not decide(cache, [child.id])[child.id].is_cold
    assert child.id not in cache._metadata
    assert cache._metadata[parent.id] is retained
    with CaptureQueriesContext(connection) as queries:
        result = decide(cache, [parent.id, child.id])
    assert len(queries) == 1
    assert not result[child.id].is_cold
    with pytest.raises(RuntimeError), transaction.atomic():
        lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_updated_at=None)
        assert decide(cache, [child.id])[child.id].is_cold
        raise RuntimeError('rollback')
    with CaptureQueriesContext(connection) as queries:
        assert not decide(cache, [child.id])[child.id].is_cold
    assert len(queries) == 0


def test_mapping_invalidation_retains_unrelated_own_entries(cache):
    old = make_session('old')
    new = make_session('new')
    child = make_session('child', type='subagent', parent_session=old)
    decide(cache, [old.id, new.id, child.id])
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), parent_session_id=new.id)
    with CaptureQueriesContext(connection) as queries:
        decide(cache, [old.id, new.id])
    assert len(queries) == 0


def test_invalidated_inflight_fill_is_reloaded(cache, monkeypatch):
    make_session('owner')
    original = cache._read_metadata
    changed = False

    def read_then_change(ids):
        nonlocal changed
        rows = original(ids)
        if not changed:
            changed = True
            lifecycle.update_lifecycle(Session.objects.filter(pk='owner'), last_updated_at=timezone.now())
        return rows

    monkeypatch.setattr(cache, '_read_metadata', read_then_change)
    assert not decide(cache, ['owner'])['owner'].is_cold
    assert not decide(cache, ['owner'])['owner'].is_cold


def test_invalidated_cached_hit_in_mixed_batch_is_reloaded(cache, monkeypatch):
    make_session('warm')
    make_session('miss')
    decide(cache, ['warm'])
    original = cache._read_metadata
    changed = False

    def read_then_change(ids):
        nonlocal changed
        rows = original(ids)
        if not changed:
            changed = True
            lifecycle.update_lifecycle(Session.objects.filter(pk='warm'), last_updated_at=timezone.now())
        return rows

    monkeypatch.setattr(cache, '_read_metadata', read_then_change)
    assert not decide(cache, ['warm', 'miss'])['warm'].is_cold


def test_invalidated_atomic_fill_is_conservative(cache, monkeypatch):
    make_session('owner')
    original = cache._read_metadata

    def read_then_invalidate(ids):
        rows = original(ids)
        cold_cache.invalidate_cold_metadata(ids)
        return rows

    monkeypatch.setattr(cache, '_read_metadata', read_then_invalidate)
    with transaction.atomic():
        assert not decide(cache, ['owner'])['owner'].resolved
    assert not cache._metadata


def test_absence_and_uncommitted_misses_are_not_retained(cache):
    assert not decide(cache, ['late'])['late'].resolved
    make_session('late')
    assert decide(cache, ['late'])['late'].is_cold
    with pytest.raises(RuntimeError), transaction.atomic():
        make_session('rollback')
        assert decide(cache, ['rollback'])['rollback'].is_cold
        raise RuntimeError('rollback')
    assert not decide(cache, ['rollback'])['rollback'].resolved


def test_epoch_and_representation_changes_retain_entries(cache):
    make_session('owner')
    decide(cache, ['owner'])
    with transaction.atomic():
        state.invalidate_roots(['owner'])
    with CaptureQueriesContext(connection) as queries:
        assert decide(cache, ['owner'])['owner'].is_cold
    assert len(queries) == 0


def test_close_discards_pass_state_and_unregisters(cache):
    make_session('owner')
    decide(cache, ['owner'])
    cache.close()
    cache.close()
    assert not cache._metadata
    assert cache not in cold_cache._maintenance_caches
    with pytest.raises(RuntimeError, match='closed'):
        decide(cache, ['owner'])


def test_database_replacement_discards_pass_entries(cache, monkeypatch):
    make_session('owner')
    identity = ['first']
    monkeypatch.setattr(cold_cache, '_database_identity', lambda: identity[0])
    assert decide(cache, ['owner'])['owner'].is_cold
    Session.objects.filter(pk='owner').update(last_updated_at=timezone.now())
    identity[0] = 'replacement'
    assert not decide(cache, ['owner'])['owner'].is_cold


@pytest.mark.parametrize('flag', ['archived', 'hidden'])
def test_real_flag_service_invalidates_warm_owner(cache, flag, monkeypatch):
    import asyncio

    from twicc.core.services.session_update import apply_session_archived_change
    from twicc.core.services.session_visibility import _apply_visibility_flag
    from twicc.providers import db_writer

    owner = make_session('owner', created_at=timezone.now() - timedelta(days=20))
    assert not decide(cache, [owner.id])[owner.id].is_cold
    if flag == 'hidden':
        _apply_visibility_flag(owner.id, True)
    else:
        monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
        monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
        asyncio.run(apply_session_archived_change(owner, True))
    assert decide(cache, [owner.id])[owner.id].is_cold


def test_watcher_recreation_invalidates_only_own_entry(cache):
    from twicc.core.models import SessionType
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile

    owner = make_session('owner')
    other = make_session('other')
    decide(cache, [owner.id, other.id])
    owner.delete()
    watcher = ClaudeCodeSessionsWatcher()
    watcher.create_session_sync(ParsedSessionFile(owner.project_id, owner.id, SessionType.SESSION, '/owner'),
                                owner.project)
    with CaptureQueriesContext(connection) as queries:
        assert decide(cache, [other.id])[other.id].is_cold
    assert len(queries) == 0
    assert not decide(cache, [owner.id])[owner.id].is_cold


def test_full_compute_invalidates_warm_owner(cache):
    from tests.test_content_extraction import message
    from twicc.core.models import SessionItem
    from twicc.providers.compute_base import BaseSessionCompute

    owner = make_session('owner', last_line=1)
    SessionItem.objects.create(session=owner, line_num=1, content='old contents')
    assert decide(cache, [owner.id])[owner.id].is_cold
    msg = message(owner)
    msg['session_fields']['last_updated_at'] = timezone.now().isoformat()
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    assert not decide(cache, [owner.id])[owner.id].is_cold


@pytest.mark.parametrize('entry_point', ['start', 'resume', 'stop'])
def test_agent_entry_points_invalidate_before_writer_release(cache, entry_point, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.enums import Provider
    from twicc.providers import db_writer

    owner = make_session('owner')
    assert decide(cache, [owner.id])[owner.id].is_cold
    manager = BaseAgentManager()
    monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_storage_admission_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())

    async def noop(*args, **kwargs):
        pass

    async def broadcast(*args):
        # This is the first callback after the writer returns.
        assert owner.id not in cache._metadata

    monkeypatch.setattr(manager, '_broadcast_session_updated', broadcast)
    monkeypatch.setattr(manager, '_on_state_change', noop)
    monkeypatch.setattr(manager, '_ensure_timeout_monitor_running', lambda: None)
    agent = SimpleNamespace(session_id=owner.id, provider=Provider.CODEX, ephemeral=False,
                            get_pid=lambda: None, start=noop, process_run=None)

    async def scenario():
        if entry_point == 'stop':
            await manager._update_session_stopped_at(agent)
        else:
            async with manager._lock:
                await manager._register_and_start(agent, '', resume=entry_point == 'resume')

    asyncio.run(scenario())
    assert not decide(cache, [owner.id])[owner.id].is_cold


def test_statistics_count_loads_reuse_and_targeted_reload(cache):
    make_session('owner')
    decide(cache, ['owner', 'owner'])
    decide(cache, ['owner'])
    lifecycle.update_lifecycle(Session.objects.filter(pk='owner'), last_updated_at=timezone.now())
    decide(cache, ['owner'])
    assert cache.statistics == {
        'owner_ids_requested': 3, 'hits': 1, 'ids_loaded': 2,
        'grouped_queries': 2, 'invalidations': 1, 'reloads': 1,
    }


def test_atomic_miss_is_retained_only_after_successful_commit(cache):
    make_session('owner')
    with transaction.atomic():
        assert decide(cache, ['owner'])['owner'].is_cold
        assert 'owner' not in cache._metadata
    with CaptureQueriesContext(connection) as queries:
        assert decide(cache, ['owner'])['owner'].is_cold
    assert len(queries) == 0


def test_pending_atomic_fill_is_fenced_before_commit(cache):
    make_session('owner')
    with transaction.atomic():
        assert decide(cache, ['owner'])['owner'].is_cold
        cold_cache.invalidate_cold_metadata(['owner'])
    assert 'owner' not in cache._metadata


def test_savepoint_rollback_restores_committed_reuse(cache):
    make_session('owner')
    assert decide(cache, ['owner'])['owner'].is_cold
    with transaction.atomic():
        with pytest.raises(RuntimeError), transaction.atomic():
            lifecycle.update_lifecycle(Session.objects.filter(pk='owner'), last_updated_at=timezone.now())
            assert not decide(cache, ['owner'])['owner'].is_cold
            raise RuntimeError('rollback')
        with CaptureQueriesContext(connection) as queries:
            assert decide(cache, ['owner'])['owner'].is_cold
        assert len(queries) == 0


def test_process_cache_reset_fences_and_discards_pass_metadata(cache):
    make_session('owner')
    decide(cache, ['owner'])
    cold_cache.reset_cold_cache()
    assert not cache._metadata
    with CaptureQueriesContext(connection) as queries:
        assert decide(cache, ['owner'])['owner'].is_cold
    assert len(queries) == 1


def test_controlled_concurrent_fill_retries_after_invalidation(cache, monkeypatch):
    import threading

    make_session('owner')
    loaded = threading.Event()
    release = threading.Event()
    original = cache._read_metadata
    calls = []
    results = []
    failures = []

    def paused_read(ids):
        rows = original(ids)
        calls.append(tuple(ids))
        if len(calls) == 1:
            loaded.set()
            assert release.wait(5)
        return rows

    def read():
        try:
            results.append(decide(cache, ['owner']))
        except BaseException as error:
            failures.append(error)
        finally:
            connection.close()

    monkeypatch.setattr(cache, '_read_metadata', paused_read)
    worker = threading.Thread(target=read)
    worker.start()
    try:
        assert loaded.wait(5)
        cold_cache.invalidate_cold_metadata(['owner'])
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failures == []
    assert results[0]['owner'].is_cold
    assert calls == [('owner',), ('owner',)]
