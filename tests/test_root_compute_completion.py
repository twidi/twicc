"""Native trees publish readiness only after their descendants commit."""

import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, StatisticsQueueEntry
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.compute_work import ComputeWorkState
from twicc.providers import compute_selection

pytestmark = pytest.mark.django_db


def tree(provider=Provider.CLAUDE_CODE):
    project = Project.objects.create(id='root-completion-project')
    root = Session.objects.create(id='root', file_path='root.jsonl', project=project, provider=provider, mtime=100)
    child = Session.objects.create(id='child', file_path='child.jsonl', project=project, provider=provider, parent_session=root, mtime=50)
    return root, child


def publish(session, version=99):
    return BaseSessionCompute.apply_session_complete({
        'session_id': session.id, 'history_facts': [],
        'session_fields': {'compute_version': version},
    })


@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_root_waits_for_descendant_and_queues_once(provider):
    root, child = tree(provider)
    assert publish(root).outcome == 'superseded'
    root.refresh_from_db()
    assert root.compute_version is None
    assert not StatisticsQueueEntry.objects.exists()
    assert publish(child).outcome == 'applied'
    assert not StatisticsQueueEntry.objects.exists()
    assert publish(root).outcome == 'applied'
    assert list(StatisticsQueueEntry.objects.values_list('root_session_id', flat=True)) == [root.id]


def test_final_publication_rechecks_new_member():
    root, child = tree()
    child.compute_version = 99
    child.save(update_fields=['compute_version'])
    Session.objects.create(id='late', file_path='late.jsonl', project=root.project, parent_session=root, provider=root.provider)
    assert publish(root).outcome == 'superseded'
    root.refresh_from_db()
    assert root.compute_version is None
    assert not StatisticsQueueEntry.objects.exists()


def test_member_final_publication_invalidates_cost_cache():
    root, child = tree()
    Session.objects.filter(pk=child.id).update(items_cost_computed_through_at=timezone.now())
    assert publish(child).outcome == 'applied'
    child.refresh_from_db()
    assert child.items_cost_computed_through_at is None


def test_selection_waits_for_ack_and_reloads_membership():
    root, child = tree()
    select = compute_selection.select_native_tree_source
    assert select(root.provider, 99, cutoff=200) == child.id
    Session.objects.filter(pk=child.id).update(compute_version=99)
    assert select(root.provider, 99, cutoff=200, outstanding={child.id}) is None
    assert select(root.provider, 99, cutoff=200) == root.id
    Session.objects.create(id='late', file_path='late.jsonl', project=root.project, parent_session=root, provider=root.provider, mtime=50)
    assert select(root.provider, 99, cutoff=200) == 'late'


def test_failed_tree_allows_another_root():
    root, child = tree()
    other = Session.objects.create(id='other', file_path='other.jsonl', project=root.project, provider=root.provider, mtime=40)
    assert compute_selection.select_native_tree_source(root.provider, 99, cutoff=200, excluded={child.id}) == other.id


def test_level_overlap_preserves_deepest_first_and_root_last():
    from twicc.core.models import AgentLink
    root, child = tree()
    sibling = Session.objects.create(id='sibling', file_path='sibling', project=root.project,
                                     parent_session=root, provider=root.provider, mtime=40)
    deep = Session.objects.create(id='deep', file_path='deep', project=root.project,
                                  parent_session=root, provider=root.provider, mtime=30)
    AgentLink.objects.create(session=child, agent_id=deep.id, tool_use_line_num=1, tool_use_id='deep')
    select = compute_selection.select_native_tree_source
    assert select(root.provider, 99, cutoff=200) == deep.id
    assert select(root.provider, 99, cutoff=200, outstanding={deep.id}) is None
    Session.objects.filter(pk=deep.id).update(compute_version=99)
    assert select(root.provider, 99, cutoff=200) == child.id
    assert select(root.provider, 99, cutoff=200, outstanding={child.id}) == sibling.id
    assert select(root.provider, 99, cutoff=200, outstanding={child.id, sibling.id}) is None


def test_current_root_forced_member_completes_without_root_recompute():
    root, child = tree(Provider.CODEX)
    Session.objects.filter(pk__in=[root.id, child.id]).update(compute_version=99)
    forced = {child.id}
    assert compute_selection.select_native_tree_source(root.provider, 99, cutoff=200, forced_source_ids=forced) == child.id
    context = compute_selection.tree_completion_context(child.id, 99, forced_source_ids=forced)
    result = BaseSessionCompute.apply_session_complete({
        'session_id': child.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': context,
    })
    assert result.outcome == 'applied'
    assert list(StatisticsQueueEntry.objects.values_list('root_session_id', flat=True)) == [root.id]
    root.refresh_from_db()
    assert root.compute_version == 99


def test_slice_invalidates_cost_cache_in_its_transaction():
    from django.db import transaction
    root, child = tree()
    Session.objects.filter(pk=child.id).update(items_cost_computed_through_at=timezone.now())
    with transaction.atomic():
        assert BaseSessionCompute.apply_session_items_chunk(child.id, None, [], [], []) == 'ok'
        child.refresh_from_db()
        assert child.items_cost_computed_through_at is None


pytest_plugins = ['tests.test_background_compute_recovery']


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_real_coordinator_waits_for_writer_ack(recovery_runtime, monkeypatch, provider):
    import asyncio
    import sqlite3
    from contextlib import closing
    from twicc.providers import background_compute_task as background, db_writer
    from twicc.providers.codex import background_compute as codex
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    from twicc.core.content_storage import extraction
    initialized = []
    initialize = extraction.initialize_compute_session
    def record_initialize(sid):
        initialized.append(sid)
        return initialize(sid)
    monkeypatch.setattr(extraction, 'initialize_compute_session', record_initialize)
    with closing(sqlite3.connect(path)) as database:
        database.execute('UPDATE core_session SET provider=?', (provider.value,))
        database.execute("UPDATE core_session SET parent_session_id='healthy', type='subagent' WHERE id='faulty'")
        database.commit()
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        complete = db_writer._complete_compute_result
        async def controlled(message, *args):
            if message['session_id'] == 'faulty':
                entered.set()
                await release.wait()
            return await complete(message, *args)
        monkeypatch.setattr(db_writer, '_complete_compute_result', controlled)
        db_writer.start_db_writer()
        factory = ('tests.test_codex_compute_recovery:CodexRecoveryCompute' if provider == Provider.CODEX
                   else 'tests.test_background_compute_recovery:RecoveryCompute')
        ctx = background.ComputeContext(provider, 123, factory)
        if provider == Provider.CODEX:
            owner = codex.CodexComputeCoordinator(ctx, asyncio.Event())
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            work_state = ComputeWorkState()
            task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=work_state))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(.15)
            assert versions(path) == {'faulty': 123, 'healthy': None}
            # Initialization precedes root command admission. It must also wait.
            with closing(sqlite3.connect(path)) as database:
                assert 'healthy' not in initialized
                assert database.execute('SELECT count(*) FROM core_statisticsqueueentry').fetchone()[0] == 0
            release.set()
            async with asyncio.timeout(6):
                while versions(path)['healthy'] != 123:
                    await asyncio.sleep(.01)
            with closing(sqlite3.connect(path)) as database:
                assert database.execute('SELECT root_session_id FROM core_statisticsqueueentry').fetchall() == [('healthy',)]
                import orjson
                forced = database.execute('SELECT force_cost_session_ids FROM core_statisticsqueueentry').fetchone()[0]
                assert orjson.loads(forced) == ['faulty', 'healthy']
            ctx.stop_event.set()
            await asyncio.wait_for(task, 3)
        finally:
            release.set()
            ctx.stop_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_changed_membership_rejects_final_forced_source():
    root, child = tree(Provider.CODEX)
    Session.objects.filter(pk__in=[root.id, child.id]).update(compute_version=99)
    context = compute_selection.tree_completion_context(child.id, 99, forced_source_ids={child.id})
    Session.objects.create(id='late', file_path='late', project=root.project, provider=root.provider,
                           parent_session=root, compute_version=99)
    result = BaseSessionCompute.apply_session_complete({
        'session_id': child.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': context,
    })
    assert result.outcome == 'superseded'
    assert not StatisticsQueueEntry.objects.exists()


def test_malformed_tree_yields_to_another_root():
    from twicc.core.models import AgentLink
    root, child = tree()
    AgentLink.objects.create(session=child, agent_id=child.id, tool_use_line_num=1, tool_use_id='cycle')
    assert compute_selection.tree_completion_context(child.id, 99) is None
    other = Session.objects.create(id='other', file_path='other', project=root.project, provider=root.provider, mtime=40)
    assert compute_selection.select_native_tree_source(root.provider, 99, cutoff=200) == other.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
@pytest.mark.parametrize('failure', ['initialize', 'superseded', 'malformed'])
def test_failed_descendant_releases_initial_barrier_without_ready_root(recovery_runtime, monkeypatch, provider, failure):
    import asyncio
    import sqlite3
    from contextlib import closing
    from twicc.core.content_storage import extraction
    from twicc.providers import background_compute_task as background, db_writer
    from twicc.providers.codex import background_compute as codex
    from tests.test_background_compute_recovery import versions

    path, progress = recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute('UPDATE core_session SET provider=?', (provider.value,))
        database.execute("UPDATE core_session SET parent_session_id='healthy', type='subagent' WHERE id='faulty'")
        database.commit()
    from tests.test_compute_age_selection import clone_session
    from twicc.providers.compute_base import ComputeApplyResult
    clone_session(path, 'other', 1)
    if failure == 'malformed':
        from twicc.core.models import AgentLink
        # The coordinator reads the isolated copy through its normal reader.
        async def create_cycle():
            await background.sync_to_async(lambda: AgentLink.objects.create(
                session_id='faulty', agent_id='faulty', tool_use_id='cycle', tool_use_line_num=1,
            ))()
        asyncio.run(create_cycle())
    initialize = extraction.initialize_compute_session
    original_apply = BaseSessionCompute._apply_session_complete_final
    def fail(sid):
        if sid == 'faulty' and failure == 'initialize':
            raise RuntimeError('persistent descendant failure')
        return initialize(sid)
    def supersede(message):
        if message['session_id'] == 'faulty' and failure == 'superseded':
            return ComputeApplyResult('superseded')
        return original_apply(message)
    monkeypatch.setattr(extraction, 'initialize_compute_session', fail)
    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(supersede))
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def scenario():
        db_writer.start_db_writer()
        barrier = asyncio.Event()
        factory = ('tests.test_codex_compute_recovery:CodexRecoveryCompute' if provider == Provider.CODEX
                   else 'tests.test_background_compute_recovery:RecoveryCompute')
        ctx = background.ComputeContext(provider, 123, factory)
        if provider == Provider.CODEX:
            owner = codex.CodexComputeCoordinator(ctx, barrier)
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            work_state = ComputeWorkState()
            task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=work_state))
        try:
            await asyncio.wait_for(barrier.wait(), 3)
            assert versions(path)['healthy'] is None
            assert versions(path)['other'] == 123
            if provider == Provider.CODEX:
                assert 'healthy' in owner.initial_classified
            assert not any(completed for _, completed in progress)
            assert not task.done()
        finally:
            ctx.stop_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_final_root_validates_launcher_links_written_by_same_publication():
    root, child = tree()
    Session.objects.filter(pk=child.id).update(compute_version=99)
    result = BaseSessionCompute.apply_session_complete({
        'session_id': root.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'agent_links_to_create': [{
            'session_id': child.id, 'agent_id': child.id, 'tool_use_id': 'cycle',
            'tool_use_line_num': 1, 'is_background': False,
        }],
    })
    assert result.outcome == 'superseded'
    root.refresh_from_db()
    assert root.compute_version is None
    assert not StatisticsQueueEntry.objects.exists()


def test_persisted_outdated_member_keeps_current_root_eligible():
    root, child = tree(Provider.CODEX)
    Session.objects.filter(pk=root.id).update(compute_version=99)
    # No in-memory forced intent survives this simulated fresh selection.
    assert compute_selection.select_native_tree_source(
        root.provider, 99, cutoff=200, eligible_source_ids={child.id},
    ) == child.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('restart', [False, True])
def test_codex_discovery_during_forced_rebuild_keeps_tree_eligible(recovery_runtime, monkeypatch, restart):
    import asyncio
    import sqlite3
    from contextlib import closing
    from twicc.providers import background_compute_task as background, db_writer
    from twicc.providers.codex import background_compute as codex
    from tests.test_compute_age_selection import clone_session
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET provider='codex', compute_version=123")
        database.execute("UPDATE core_session SET parent_session_id='healthy', type='subagent' WHERE id='faulty'")
        database.commit()
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)
    contexts = []
    context = compute_selection.tree_completion_context
    def record_context(sid, *args, **kwargs):
        result = context(sid, *args, **kwargs)
        contexts.append((sid, result))
        return result
    monkeypatch.setattr(compute_selection, 'tree_completion_context', record_context)

    async def scenario():
        db_writer.start_db_writer()
        owners, tasks = [], []
        prepared = []
        def start(forced):
            ctx = background.ComputeContext(Provider.CODEX, 123,
                'tests.test_codex_compute_recovery:CodexRecoveryCompute')
            owner = codex.CodexComputeCoordinator(ctx, asyncio.Event())
            if forced:
                owner._forced_rebuild.add('faulty')
            async def prepare(candidate):
                sid = candidate.session_id
                prepared.append(sid)
                if sid == 'faulty':
                    # Discovery occurs after selection but before context capture.
                    clone_session(path, 'late', 1, session_type='subagent')
                    with closing(sqlite3.connect(path)) as database:
                        database.execute("UPDATE core_session SET parent_session_id='healthy', compute_version=NULL WHERE id='late'")
                        database.commit()
                return codex.PreparedCandidate(sid, None, sid == 'faulty')
            owner.prepare_candidate = prepare
            if forced and restart:
                applied = owner._handle_applied
                async def stop_after_forced(signal):
                    await applied(signal)
                    if signal.session_id == 'faulty' and signal.outcome == 'applied':
                        assert not owner._forced_rebuild
                        ctx.stop_event.set()
                owner._handle_applied = stop_after_forced
            owners.append(owner)
            task = asyncio.create_task(owner.run())
            tasks.append(task)
            return owner, task
        try:
            owner, task = start(True)
            if restart:
                await asyncio.wait_for(task, 5)
                assert versions(path)['late'] is None
                with closing(sqlite3.connect(path)) as database:
                    assert database.execute('SELECT count(*) FROM core_statisticsqueueentry').fetchone()[0] == 0
                owner, task = start(False)
            async with asyncio.timeout(5):
                while versions(path).get('late') != 123:
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            assert prepared == ['faulty', 'late']
            assert contexts[0] == ('faulty', None)
            assert contexts[-1][0] == 'late'
            assert contexts[-1][1]['root_id'] == 'healthy'
            assert versions(path)['healthy'] == 123
            with closing(sqlite3.connect(path)) as database:
                assert database.execute('SELECT root_session_id FROM core_statisticsqueueentry').fetchall() == [('healthy',)]
                import orjson
                forced = database.execute('SELECT force_cost_session_ids FROM core_statisticsqueueentry').fetchone()[0]
                assert orjson.loads(forced) == (['late'] if restart else ['faulty', 'late'])
            owner.ctx.stop_event.set()
            await asyncio.wait_for(task, 3)
        finally:
            for owner, task in zip(owners, tasks):
                owner.ctx.stop_event.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await background.retire_compute_run(owner.ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_worker_local_completed_ids_reach_final_root_queue(provider):
    from types import SimpleNamespace

    root, child = tree(provider)
    cached = Session.objects.create(id='cached', file_path='cached', project=root.project,
        parent_session=root, provider=provider, compute_version=99)
    compute = SimpleNamespace(_tree_completion=None)
    assert BaseSessionCompute._completed_cost_session_ids(compute, child) == []
    assert publish(child).outcome == 'applied'
    compute._tree_completion = compute_selection.tree_completion_context(root.id, 99)
    forced = BaseSessionCompute._completed_cost_session_ids(compute, root)
    assert forced == ['child', 'root']
    assert compute._computed_cost_sessions == {root.id: set(forced)}
    result = BaseSessionCompute.apply_session_complete({
        'session_id': root.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': compute._tree_completion, 'force_cost_session_ids': forced,
    })
    assert result.outcome == 'applied'
    entry = StatisticsQueueEntry.objects.get()
    assert entry.force_cost_session_ids == ['child', 'root']
    assert cached.id not in entry.force_cost_session_ids


def test_forced_current_root_completion_transmits_only_recomputed_member():
    from types import SimpleNamespace

    root, child = tree(Provider.CODEX)
    Session.objects.filter(pk__in=[root.id, child.id]).update(compute_version=99)
    context = compute_selection.tree_completion_context(child.id, 99, forced_source_ids={child.id})
    compute = SimpleNamespace(_tree_completion=context)
    forced = BaseSessionCompute._completed_cost_session_ids(compute, child)
    assert forced == ['child']
    assert compute._computed_cost_sessions == {root.id: set(forced)}
    result = BaseSessionCompute.apply_session_complete({
        'session_id': child.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': context, 'force_cost_session_ids': forced,
    })
    assert result.outcome == 'applied'
    assert StatisticsQueueEntry.objects.get().force_cost_session_ids == ['child']


@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_rejected_final_keeps_prior_recomputed_member_for_retry(provider):
    from types import SimpleNamespace

    root, child = tree(provider)
    compute = SimpleNamespace(_tree_completion=None)
    assert BaseSessionCompute._completed_cost_session_ids(compute, child) == []
    assert publish(child).outcome == 'applied'
    compute._tree_completion = compute_selection.tree_completion_context(root.id, 99)
    forced = BaseSessionCompute._completed_cost_session_ids(compute, root)
    # A new member supersedes the final result before its queue entry commits.
    late = Session.objects.create(id='late', file_path='late', project=root.project,
        parent_session=root, provider=provider)
    result = BaseSessionCompute.apply_session_complete({
        'session_id': root.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': compute._tree_completion, 'force_cost_session_ids': forced,
    })
    assert result.outcome == 'superseded'
    assert not StatisticsQueueEntry.objects.exists()
    compute._tree_completion = None
    assert BaseSessionCompute._completed_cost_session_ids(compute, late) == []
    assert publish(late).outcome == 'applied'
    compute._tree_completion = compute_selection.tree_completion_context(root.id, 99)
    retried = BaseSessionCompute._completed_cost_session_ids(compute, root)
    assert retried == ['child', 'late', 'root']
    result = BaseSessionCompute.apply_session_complete({
        'session_id': root.id, 'history_facts': [], 'session_fields': {'compute_version': 99},
        'tree_completion': compute._tree_completion, 'force_cost_session_ids': retried,
    })
    assert result.outcome == 'applied'
    assert StatisticsQueueEntry.objects.get().force_cost_session_ids == retried


def test_worker_acknowledgment_clears_only_completed_tree(monkeypatch):
    import queue
    from types import SimpleNamespace
    import orjson
    from twicc.providers import background_compute_task as background

    root, child = tree()
    other = Session.objects.create(id='other', file_path='other', project=root.project, provider=root.provider)
    emitted = []
    compute = SimpleNamespace(provider=root.provider, enable_batch_queue_cache=lambda: None)

    def metadata(session_id, result_queue, run_id):
        session = Session.objects.get(pk=session_id)
        emitted.append((session_id, BaseSessionCompute._completed_cost_session_ids(compute, session)))

    compute.compute_session_metadata = metadata
    commands = queue.Queue()
    for command in [
        {'session_id': child.id},
        {'session_id': other.id},
        {'completed_root_id': other.id},
        {'session_id': root.id},
        {'completed_root_id': root.id},
        {'session_id': root.id},
        None,
    ]:
        commands.put(command)
    result_queue = SimpleNamespace(put=lambda message: orjson.loads(message), close=lambda: None)
    monkeypatch.setattr(background, '_set_pdeathsig_linux', lambda: None)
    monkeypatch.setattr('signal.signal', lambda *args: None)
    monkeypatch.setattr(background, '_resolve_factory', lambda path: lambda: compute)
    monkeypatch.setattr(background, 'current_provider', SimpleNamespace(set=lambda value: None))
    background.compute_worker_main(commands, result_queue, None, SimpleNamespace(is_set=lambda: False), 'test', 1)
    assert emitted == [('child', []), ('other', ['other']), ('root', ['child', 'root']), ('root', ['root'])]
    assert compute._computed_cost_sessions == {'root': {'root'}}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_provider_metadata_payload_transmits_recomputed_tree_members(provider):
    from queue import Queue
    import orjson
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.codex.compute import CodexSessionCompute

    root, child = tree(provider)
    compute = ClaudeCodeSessionCompute() if provider == Provider.CLAUDE_CODE else CodexSessionCompute()
    messages = Queue()
    compute._tree_completion = None
    compute.compute_session_metadata(child.id, messages, 1)
    child_message = orjson.loads(messages.get_nowait())
    assert child_message['force_cost_session_ids'] == []
    assert BaseSessionCompute.apply_session_complete(child_message).outcome == 'applied'
    compute._tree_completion = compute_selection.tree_completion_context(root.id, child_message['session_fields']['compute_version'])
    compute.compute_session_metadata(root.id, messages, 1)
    root_message = orjson.loads(messages.get_nowait())
    assert root_message['force_cost_session_ids'] == ['child', 'root']
    assert BaseSessionCompute.apply_session_complete(root_message).outcome == 'applied'
    assert StatisticsQueueEntry.objects.get().force_cost_session_ids == ['child', 'root']
