"""Known launcher IDs use exact facts and cooperative stale-history recovery."""

import asyncio
from unittest.mock import patch

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_nested_agent_compute import meta, seed, spawn, live, entry, compute

pytest_plugins = ("tests.test_nested_agent_compute",)
from twicc.core.models import AgentLink, Session
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.history_facts import HistoryFactContext, append_history_facts


pytestmark = pytest.mark.usefixtures("metadata_rows")


def current(session, records):
    history = HistoryFactContext(session.provider, session_id=session.id)
    for line, parsed in enumerate(records, 1):
        facts = get_compute().extract_history_facts(parsed, line_num=line, history=history)
        history.register(parsed, facts, line_num=line)
    append_history_facts(session.id, history.facts)
    Session.objects.filter(id=session.id).update(compute_version=get_compute().compute_version)
    return history


def test_known_id_reads_only_exact_source_and_keeps_first_spawn(tree):
    root, owner, child, home = tree
    records = [spawn('unrelated'), spawn('wanted'), spawn('wanted', 'later')]
    seed(owner, *records)
    current(owner, records)
    with CaptureQueriesContext(connection) as queries:
        resolved = get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted')
    assert resolved.tool_use_id == 'wanted'
    assert resolved.tool_use_line_num == 2
    reads = [q['sql'] for q in queries if 'FROM "core_sessionitem"' in q['sql']]
    assert len(reads) == 1
    assert all(' LIKE ' not in q['sql'].upper() for q in queries)
    assert any('"key" = ' in q['sql'] for q in queries)


def test_in_memory_lookup_keeps_source_block_order_without_queries(tree):
    root, owner, child, home = tree
    parsed = spawn('wanted')
    parsed['message']['content'].append({**parsed['message']['content'][0],
        'input': {'prompt': 'second', 'run_in_background': True}})
    history = current(owner, [parsed])
    with CaptureQueriesContext(connection) as queries:
        found = history.lookup_tool_call('wanted', before_line=2)
    assert found[0]['input']['prompt'] == 'second'
    assert len(queries) == 0
    assert history.lookup_tool_call('wanted', before_line=1) is None
    result = get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted', history=history)
    assert result.tool_use_line_num == 1
    assert not result.is_background


def test_current_fact_absence_does_not_read_raw_launcher(tree):
    root, owner, child, home = tree
    seed(owner, spawn('wanted'))
    current(owner, [])
    with CaptureQueriesContext(connection) as queries:
        assert get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted') is None
    assert not any('FROM "core_sessionitem"' in q['sql'] for q in queries)


def test_eleven_record_slice_attempts_metadata_once(tree):
    root, owner, child, home = tree
    meta(tree)
    current(owner, [])
    original = get_compute().create_agent_link_from_meta
    with patch.object(get_compute(), 'create_agent_link_from_meta', wraps=original) as lookup:
        live(child, home, *[entry('user', 'work', agentId=child.id) for _ in range(11)])
    assert lookup.call_count == 1


def test_parent_publication_recovers_child_without_new_child_record(tree):
    root, owner, child, home = tree
    meta(tree)
    seed(owner, spawn())
    seed(child, entry('user', 'nested work', agentId=child.id))
    compute(owner)
    link = AgentLink.objects.get(agent_id=child.id)
    assert link.session_id == owner.id
    assert link.tool_use_line_num == 1


@pytest.mark.django_db(transaction=True)
def test_stale_launcher_fallback_releases_writer_between_pages(tree, monkeypatch):
    from twicc.providers import launcher_lookup, db_writer
    from twicc.providers.launcher_lookup import LauncherLookupRequest, LauncherLookupResolver
    root, owner, child, home = tree
    seed(owner, *[{'type': 'queue-operation'} for _ in range(260)], spawn('wanted'))
    from threading import Event
    entered, release = Event(), Event()
    page_cursors = []
    original = launcher_lookup._read_page
    def read_page(request, cursor):
        assert not db_writer._lease_admits_current()
        result = original(request, cursor)
        page_cursors.append(cursor.after_line)
        if cursor.after_line == 0:
            entered.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(launcher_lookup, '_read_page', read_page)
    async def run():
        db_writer.start_db_writer()
        stop = asyncio.Event()
        resolved = asyncio.Event()
        async def publish(updates):
            assert updates[0].tool_use_line_num == 261
            resolved.set()
        resolver = LauncherLookupResolver(on_resolved=publish)
        resolver.enqueue([LauncherLookupRequest(owner.id, child.id, 'wanted')] * 2)
        task = asyncio.create_task(resolver.run(stop))
        try:
            from twicc.providers.compute_executor import run_compute_sync
            assert await asyncio.to_thread(entered.wait, 10)
            # The foreground writer commits while fallback still has unread pages.
            changed = await asyncio.wait_for(db_writer.run_under_db_write_lock(
                lambda: run_compute_sync(lambda: Session.objects.filter(id=root.id).update(title='foreground')),
            ), 10)
            assert changed == 1
            assert page_cursors == [0]
            release.set()
            await asyncio.wait_for(resolved.wait(), 10)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())
    assert page_cursors == [0, 128, 256]
    assert AgentLink.objects.get(agent_id=child.id).tool_use_line_num == 261


@pytest.mark.django_db(transaction=True)
def test_resolver_cancellation_drains_submitted_read(tree, monkeypatch):
    from threading import Event
    from twicc.providers import launcher_lookup
    from twicc.providers.launcher_lookup import LauncherLookupRequest, LauncherLookupResolver
    root, owner, child, home = tree
    entered, release, finished = Event(), Event(), Event()
    original = launcher_lookup._read_page
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(launcher_lookup, '_read_page', blocked)
    async def run():
        async def publish(updates):
            pytest.fail('Cancelled lookup must not publish')
        resolver = LauncherLookupResolver(on_resolved=publish)
        resolver.enqueue([LauncherLookupRequest(owner.id, child.id, 'wanted')])
        task = asyncio.create_task(resolver.run(asyncio.Event()))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert finished.is_set()
        assert task.cancelled()
    asyncio.run(run())
    assert not AgentLink.objects.filter(agent_id=child.id).exists()


def test_parent_publication_retries_metadata_discovered_after_compute(tree):
    root, owner, child, home = tree
    seed(owner, spawn())
    message = compute(owner, apply=False)
    meta(tree)
    seed(child, entry('user', 'nested work', agentId=child.id))
    assert not AgentLink.objects.filter(agent_id=child.id).exists()
    assert get_compute().apply_session_complete(message).outcome == 'applied'
    assert AgentLink.objects.get(agent_id=child.id).tool_use_line_num == 1


def test_same_source_history_retries_after_registered_evidence(tree):
    root, owner, child, home = tree
    history = HistoryFactContext(owner.provider, session_id=owner.id, persisted=False)
    assert get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted', history=history) is None
    parsed = spawn('wanted')
    facts = get_compute().extract_history_facts(parsed, line_num=1, history=history)
    history.register(parsed, facts, line_num=1)
    assert get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted', history=history).tool_use_line_num == 1


@pytest.mark.django_db(transaction=True)
def test_other_source_ingests_before_launcher_fallback_exhausts(tree, monkeypatch):
    from threading import Event
    from unittest.mock import AsyncMock
    from twicc.providers import launcher_lookup, db_writer, sessions_watcher
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    root, owner, child, home = tree
    meta(tree, tool='missing')
    seed(owner, *[{'type': 'queue-operation'} for _ in range(400)])
    child_path = home / child.file_path
    child_path.parent.mkdir(parents=True, exist_ok=True)
    child_path.write_bytes(orjson.dumps(entry('user', 'nested work', agentId=child.id)) + b'\n')
    root_path = home / root.file_path
    root_path.parent.mkdir(parents=True, exist_ok=True)
    root_path.write_bytes(orjson.dumps(entry('user', 'second source')) + b'\n')
    entered, release = Event(), Event()
    original = launcher_lookup._read_page
    pages = []
    watcher = ClaudeCodeSessionsWatcher()
    def blocked(request, cursor):
        assert not db_writer._lease_admits_current()
        assert not watcher._change_lock.locked()
        pages.append(cursor.after_line)
        if not cursor.after_line:
            entered.set()
            assert release.wait(10)
        return original(request, cursor)
    monkeypatch.setattr(launcher_lookup, '_read_page', blocked)
    monkeypatch.setattr(sessions_watcher, 'broadcast_message', AsyncMock())
    async def run():
        from channels.db import database_sync_to_async
        db_writer.start_db_writer()
        try:
            await asyncio.wait_for(watcher.process_path(child_path), 10)
            assert await asyncio.to_thread(entered.wait, 10)
            await asyncio.wait_for(watcher.process_path(root_path), 10)
            assert await database_sync_to_async(lambda: Session.objects.get(id=root.id).last_line)() == 1
            assert pages == [0]
            assert watcher._launcher_resolver_task is not None
            assert not watcher._launcher_resolver_task.done()
        finally:
            watcher.stop_watcher()
            release.set()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
        assert watcher._launcher_resolver_task is None
    asyncio.run(run())
    assert not AgentLink.objects.filter(agent_id=child.id).exists()


def test_repeated_id_cannot_upgrade_existing_first_spawn_from_later_record(tree):
    root, owner, child, home = tree
    first, later = spawn('wanted'), spawn('wanted')
    later['message']['content'][0]['input']['run_in_background'] = True
    seed(owner, first, later)
    current(owner, [first, later])
    initial = get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted')
    assert initial.tool_use_line_num == 1
    assert get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted') is None
    assert not AgentLink.objects.get(agent_id=child.id).is_background


@pytest.mark.django_db(transaction=True)
def test_published_late_sidecar_uses_shared_link_broadcast(tree, monkeypatch):
    from time import perf_counter
    from unittest.mock import AsyncMock
    from channels.db import database_sync_to_async
    from twicc.providers import db_writer, sessions_watcher
    root, owner, child, home = tree
    seed(owner, spawn())
    message = compute(owner, apply=False)
    meta(tree)
    seed(child, entry('user', 'nested work', agentId=child.id))
    result = get_compute().apply_session_complete(message)
    assert len(result.agent_link_updates) == 1
    broadcasts = []
    async def capture(layer, payload):
        broadcasts.append(payload)
        assert await database_sync_to_async(lambda: AgentLink.objects.filter(agent_id=child.id).exists())()
    monkeypatch.setattr(sessions_watcher, 'broadcast_message', capture)
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', AsyncMock())
    from twicc.core.enums import Provider
    state = db_writer._ComputeProviderState(
        Provider.CLAUDE_CODE, message['run_id'], set(), 0, publish_progress=False,
    )
    monkeypatch.setattr(db_writer, '_compute_states', {message['run_id']: state})
    async def run():
        await db_writer._complete_compute_result(message, result, state, perf_counter(), perf_counter())
    asyncio.run(run())
    links = [payload for payload in broadcasts if payload['type'] == 'agent_link_created']
    assert len(links) == 1
    assert links[0]['agent_session_id'] == child.id
    assert links[0]['parent_session_id'] == owner.id
    assert links[0]['root_session_id'] == root.id
    assert state.failed_count == 0


def test_stale_child_slice_returns_request_without_reading_parent_payload(tree):
    root, owner, child, home = tree
    meta(tree)
    seed(owner, *[{'type': 'queue-operation'} for _ in range(260)], spawn())
    with CaptureQueriesContext(connection) as queries:
        updates = live(child, home, entry('user', 'work', agentId=child.id))
    assert not AgentLink.objects.filter(agent_id=child.id).exists()
    assert [request.tool_use_id for request in updates.unresolved_launcher_lookups] == ['tool_nested']
    reads = [q['sql'] for q in queries if 'FROM "core_sessionitem"' in q['sql'] and "'aa123'" in q['sql']]
    assert reads == []


def test_registered_later_spawn_preserves_earlier_persisted_identity(tree):
    root, owner, child, home = tree
    seed(owner, spawn('wanted'))
    current(owner, [spawn('wanted')])
    history = HistoryFactContext(owner.provider, session_id=owner.id)
    later = spawn('wanted')
    facts = get_compute().extract_history_facts(later, line_num=2, history=history)
    history.register(later, facts, line_num=2)
    assert get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted', history=history).tool_use_line_num == 1
    with pytest.raises(ValueError, match='Launcher history'):
        get_compute().create_agent_link_from_meta(owner.id, child.id, 'wanted',
            history=HistoryFactContext(child.provider, session_id=child.id))


@pytest.mark.django_db(transaction=True)
def test_fallback_rotates_requests_and_retries_exhausted_absence(tree, monkeypatch):
    from channels.db import database_sync_to_async
    from twicc.providers import launcher_lookup, db_writer
    from twicc.providers.launcher_lookup import LauncherLookupRequest, LauncherLookupResolver
    root, owner, child, home = tree
    seed(owner, *[{'type': 'queue-operation'} for _ in range(260)])
    seed(root, spawn('present'))
    order = []
    original = launcher_lookup._read_page
    def read_page(request, cursor):
        page = original(request, cursor)
        order.append((request.launcher_session_id, cursor.after_line))
        return page
    monkeypatch.setattr(launcher_lookup, '_read_page', read_page)
    async def run():
        db_writer.start_db_writer()
        done = asyncio.Event()
        updates_seen = []
        async def publish(updates):
            updates_seen.extend(updates)
            done.set()
        resolver = LauncherLookupResolver(on_resolved=publish)
        missing = LauncherLookupRequest(owner.id, child.id, 'later')
        resolver.enqueue([missing, LauncherLookupRequest(root.id, owner.id, 'present')])
        task = asyncio.create_task(resolver.run(asyncio.Event()))
        try:
            await asyncio.wait_for(done.wait(), 10)
            assert order[:2] == [(owner.id, 0), (root.id, 0)]
            # Wait for exhaustion without using a timing assumption.
            async def exhausted():
                while missing in resolver._cursors:
                    await asyncio.sleep(0)
            await asyncio.wait_for(exhausted(), 10)
            await database_sync_to_async(seed)(owner, spawn('later'))
            done.clear()
            resolver.enqueue([missing])
            await asyncio.wait_for(done.wait(), 10)
            assert updates_seen[-1].agent_id == child.id
            assert updates_seen[-1].tool_use_line_num == 261
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.parametrize('facts_current', [False, True])
def test_forward_exact_candidates_obey_strict_source_bound(tree, facts_current):
    from twicc.providers.history_facts import HistoryFactKind, iter_resolver_items
    root, owner, child, home = tree
    records = [spawn('wanted'), spawn('wanted'), spawn('wanted')]
    seed(owner, *records)
    if facts_current:
        current(owner, records)
    items = list(iter_resolver_items(owner.id, HistoryFactKind.TOOL_CALL, 'wanted', before_line=3, oldest_first=True))
    assert [item.line_num for item in items] == [1, 2]


@pytest.mark.parametrize('change', ['source', 'membership', 'identity', 'epoch'])
def test_fallback_publication_rechecks_selected_evidence(tree, change):
    from twicc.providers.launcher_lookup import LauncherLookupRequest, _Cursor, _read_page, _publish
    root, owner, child, home = tree
    seed(owner, spawn('wanted'))
    request = LauncherLookupRequest(owner.id, child.id, 'wanted')
    page = _read_page(request, _Cursor())
    assert page.selected is not None
    if change == 'source':
        owner.items.update(content=orjson.dumps(spawn('different')).decode())
    elif change == 'membership':
        Session.objects.filter(id=child.id).update(parent_session=None)
    elif change == 'epoch':
        Session.objects.filter(id=owner.id).update(history_epoch=1)
    else:
        AgentLink.objects.create(session=root, agent_id=child.id, tool_use_id='other', tool_use_line_num=4)
    assert _publish(request, page) == ()
    assert not AgentLink.objects.filter(session=owner, agent_id=child.id).exists()


@pytest.mark.parametrize('finish', ['resolver', 'parent_compute'])
@pytest.mark.django_db(transaction=True)
def test_stale_parent_live_repeat_defers_until_earliest_spawn_is_established(tree, finish):
    from twicc.providers.launcher_lookup import LauncherLookupResolver
    from twicc.providers import db_writer
    from twicc.providers.live_sync import LiveSyncLimits
    root, owner, child, home = tree
    # The first slice precedes sidecar discovery, so it cannot resolve the child.
    live(owner, home, spawn('wanted'))
    meta(tree, tool='wanted')
    later = spawn('wanted', 'later repeated spawn')
    later['message']['content'][0]['input']['run_in_background'] = True
    path = home / owner.file_path
    with path.open('ab') as source:
        source.write(orjson.dumps(later) + b'\n')
    with CaptureQueriesContext(connection) as queries:
        result = get_compute().sync_session_slice(owner.id, path, limits=LiveSyncLimits())
    assert not AgentLink.objects.filter(agent_id=child.id).exists()
    assert result.updates.agent_link_updates == []
    assert [request.tool_use_id for request in result.updates.unresolved_launcher_lookups] == ['wanted']
    raw_reads = [q['sql'] for q in queries if 'FROM "core_sessionitem"' in q['sql'] and "'aa123'" in q['sql']]
    assert not any("LIMIT 128" in sql for sql in raw_reads)
    if finish == 'parent_compute':
        compute(owner)
    else:
        async def run():
            db_writer.start_db_writer()
            done = asyncio.Event()
            async def resolved(updates):
                assert updates[0].tool_use_line_num == 1
                done.set()
            resolver = LauncherLookupResolver(on_resolved=resolved)
            resolver.enqueue(result.updates.unresolved_launcher_lookups)
            task = asyncio.create_task(resolver.run(asyncio.Event()))
            try:
                await asyncio.wait_for(done.wait(), 10)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await db_writer.stop_db_writer()
        asyncio.run(run())
    link = AgentLink.objects.get(agent_id=child.id)
    assert link.tool_use_line_num == 1
    assert not link.is_background


def test_live_slice_without_launcher_evidence_does_not_scan_tree_sidecars(tree):
    root, owner, child, home = tree
    meta(tree)
    with patch.object(get_compute(), 'get_spawn_metas_for_tree', wraps=get_compute().get_spawn_metas_for_tree) as scans:
        live(owner, home, entry('user', 'work'), {'type': 'queue-operation'})
    assert scans.call_count == 0
