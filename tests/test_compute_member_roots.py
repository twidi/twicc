"""Outdated members beneath current Claude roots reach the background compute."""

import asyncio
from contextlib import closing
import sqlite3
import time

from django.db import connection
import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import AgentLink, Session, SessionType
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.compute_work import ComputeWorkState
from tests.test_background_compute_recovery import RecoveryCompute, versions

pytest_plugins = ['tests.test_background_compute_recovery']

ROOT_UPDATED_AT = '2026-01-01 00:00:00'
FACTORY = f'{__name__}:MemberCompute'


class MemberCompute(RecoveryCompute):
    """Send the admitted tree evidence, so a final member completes its root."""

    def compute_session_metadata(self, session_id, result_queue, run_id):
        session = Session.objects.get(pk=session_id)
        result_queue.put(orjson.dumps({
            'type': 'session_complete', 'provider': 'claude_code', 'run_id': run_id,
            'session_id': session_id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'tree_completion': getattr(self, '_tree_completion', None),
            'force_cost_session_ids': BaseSessionCompute._completed_cost_session_ids(self, session),
            'session_fields': {'compute_version': 123},
        }))


def execute(path, *statements):
    with closing(sqlite3.connect(path)) as database:
        for sql, *params in statements:
            database.execute(sql, *params)
        database.commit()


def current_tree(path):
    """``healthy`` is a current root; ``faulty`` is its current subagent."""
    execute(
        path,
        ("UPDATE core_session SET compute_version=123, last_updated_at=?", (ROOT_UPDATED_AT,)),
        ("UPDATE core_session SET parent_session_id='healthy', type='subagent' WHERE id='faulty'",),
    )


def in_runtime(function):
    """Run a writer applier against the isolated database copy."""
    async def run():
        return await background.sync_to_async(function)()
    return asyncio.run(run())


def reset_offline_member(mtime):
    """Initial sync appends offline lines to an existing, computed subagent."""
    def apply():
        session = Session.objects.get(pk='faulty')
        db_writer._apply_update_session_payload(db_writer.UpdateSessionPayload(
            Provider.CLAUDE_CODE, session, [], 0, 0, mtime, True, False,
        ))
    in_runtime(apply)


def record_admitted(monkeypatch):
    """Record each source the coordinator admits, once per compute command."""
    admitted = []
    submit = db_writer.submit_async_job

    async def record(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            admitted.append(job.session_id)
        return await submit(job)
    monkeypatch.setattr(db_writer, 'submit_async_job', record)
    return admitted


def root_row(path):
    with closing(sqlite3.connect(path)) as database:
        return database.execute(
            "SELECT compute_version, last_updated_at FROM core_session WHERE id='healthy'",
        ).fetchone()


def statistics_entries(path):
    with closing(sqlite3.connect(path)) as database:
        return [(root, orjson.loads(forced)) for root, forced in database.execute(
            'SELECT root_session_id, force_cost_session_ids FROM core_statisticsqueueentry',
        )]


async def run_coordinator(scenario, *, work_state=None):
    db_writer.start_db_writer()
    ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, FACTORY)
    work_state = work_state or ComputeWorkState()
    task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=work_state))
    try:
        await scenario(task, work_state)
    finally:
        ctx.stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await background.retire_compute_run(ctx)
        await db_writer.stop_db_writer()


@pytest.mark.django_db(transaction=True)
def test_offline_lines_on_member_of_current_root_compute_only_the_member(recovery_runtime, monkeypatch):
    path, progress = recovery_runtime
    current_tree(path)
    reset_offline_member(100)
    assert versions(path) == {'faulty': None, 'healthy': 123}
    admitted = record_admitted(monkeypatch)

    async def scenario(task, _):
        await asyncio.wait_for(task, 10)

    asyncio.run(run_coordinator(scenario))
    assert versions(path) == {'faulty': 123, 'healthy': 123}
    # The current root is never admitted as a source: its row stays untouched.
    assert admitted == ['faulty']
    assert root_row(path) == (123, ROOT_UPDATED_AT)
    assert statistics_entries(path) == [('healthy', ['faulty'])]
    assert progress[-1] == ((1, 1), True)


@pytest.mark.django_db(transaction=True)
def test_recent_member_of_current_root_waits_for_its_source_age(recovery_runtime, monkeypatch):
    path, _ = recovery_runtime
    current_tree(path)
    reset_offline_member(time.time() - 28.5)
    admitted = record_admitted(monkeypatch)

    async def scenario(task, work_state):
        await asyncio.sleep(.5)
        assert not task.done()
        assert work_state.has_pending()
        assert versions(path)['faulty'] is None
        await asyncio.wait_for(task, 6)
        assert not work_state.has_pending()

    asyncio.run(run_coordinator(scenario))
    assert versions(path) == {'faulty': 123, 'healthy': 123}
    assert admitted == ['faulty']
    assert root_row(path) == (123, ROOT_UPDATED_AT)


@pytest.mark.django_db(transaction=True)
def test_member_created_by_initial_sync_under_current_root_is_computed(recovery_runtime, monkeypatch):
    path, _ = recovery_runtime
    current_tree(path)

    def create():
        db_writer._apply_create_session_payload(db_writer.CreateSessionPayload(
            Provider.CLAUDE_CODE, 'recovery', None, False, Session(
                id='new', project_id='recovery', provider=Provider.CLAUDE_CODE, file_path='/test/new',
                type=SessionType.SUBAGENT, parent_session_id='healthy',
            ), [], 0, 0, 100,
        ))
    in_runtime(create)
    assert versions(path) == {'faulty': 123, 'healthy': 123, 'new': None}
    # Creation queues its own root refresh; the coordinator adds the forced one.
    queued = statistics_entries(path)
    admitted = record_admitted(monkeypatch)

    async def scenario(task, _):
        await asyncio.wait_for(task, 10)

    asyncio.run(run_coordinator(scenario))
    assert versions(path) == {'faulty': 123, 'healthy': 123, 'new': 123}
    assert admitted == ['new']
    assert root_row(path) == (123, ROOT_UPDATED_AT)
    assert statistics_entries(path) == [*queued, ('healthy', ['new'])]


@pytest.mark.django_db(transaction=True)
def test_no_outdated_member_costs_one_startup_query(recovery_runtime, monkeypatch):
    from twicc.providers import compute_tree

    path, progress = recovery_runtime
    current_tree(path)
    reads = []
    ordinary = background.sync_to_async

    def reader(function):
        def read():
            def record(execute, sql, params, many, context):
                reads.append(sql)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(record):
                return function()
        return ordinary(read)
    monkeypatch.setattr(background, 'sync_to_async', reader)
    monkeypatch.setattr(background, 'start_compute_process', lambda ctx: pytest.fail('nothing is outdated'))

    async def scenario(task, _):
        await asyncio.wait_for(task, 5)

    asyncio.run(run_coordinator(scenario))
    with_members = list(reads)
    assert progress[-1] == ((1, 1), True)
    reads.clear()
    progress.clear()
    monkeypatch.setattr(compute_tree, 'select_outdated_member_roots', lambda provider, version: set())
    asyncio.run(run_coordinator(scenario))
    assert progress[-1] == ((1, 1), True)
    assert len(with_members) == len(reads) + 1
    assert sum('"parent_session_id" IS NOT NULL' in sql for sql in with_members) == 1


@pytest.mark.django_db(transaction=True)
def test_malformed_member_tree_is_deferred_then_retried_without_polling(recovery_runtime, monkeypatch):
    path, _ = recovery_runtime
    current_tree(path)
    reset_offline_member(100)
    in_runtime(lambda: AgentLink.objects.create(
        session_id='faulty', agent_id='faulty', tool_use_id='cycle', tool_use_line_num=1,
    ))
    reads = []
    ordinary = background.sync_to_async

    def reader(function):
        def read():
            def record(execute, sql, params, many, context):
                if 'core_session' in sql:
                    reads.append(sql)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(record):
                return function()
        return ordinary(read)
    monkeypatch.setattr(background, 'sync_to_async', reader)
    admitted = record_admitted(monkeypatch)

    async def scenario(task, work_state):
        await asyncio.sleep(.5)
        assert not task.done()
        assert work_state.has_pending()
        before = len(reads)
        await asyncio.sleep(.3)
        # The deferred root waits for its deadline or a source change.
        assert len(reads) == before
        assert admitted == []
        execute(path, ("DELETE FROM core_agentlink",))
        work_state.notify_source_changed(Provider.CLAUDE_CODE, 'faulty')
        await asyncio.wait_for(task, 10)

    asyncio.run(run_coordinator(scenario))
    assert versions(path) == {'faulty': 123, 'healthy': 123}
    assert admitted == ['faulty']
    assert root_row(path) == (123, ROOT_UPDATED_AT)


@pytest.mark.django_db
def test_member_roots_resolve_in_bounded_batches():
    from twicc.core.models import Project
    from twicc.providers.compute_tree import select_outdated_member_roots

    project = Project.objects.create(id='member-batches')

    def row(sid, *, parent=None, version=None, session_type=SessionType.SUBAGENT, provider=Provider.CLAUDE_CODE):
        return Session(id=sid, project=project, file_path=f'/test/{sid}', provider=provider,
                       compute_version=version, parent_session_id=parent, type=session_type)
    Session.objects.bulk_create([
        row(f'root-{index}', version=7, session_type=SessionType.SESSION) for index in range(4)
    ] + [row('codex-root', session_type=SessionType.SESSION, provider=Provider.CODEX)])
    # Three outdated trees exceed one batch; root-3 has only current members.
    Session.objects.bulk_create(
        [row(f'child-{index}', parent=f'root-{index % 3}') for index in range(450)]
        + [row('current', parent='root-3', version=7), row('codex-child', parent='codex-root', provider=Provider.CODEX)]
    )
    Session.objects.bulk_create([row(f'deep-{index}', parent=f'child-{index}') for index in range(30)])
    statements = []

    def capture(execute, sql, params, many, context):
        statements.append(params)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        roots = select_outdated_member_roots(Provider.CLAUDE_CODE, 7)
    assert roots == {'root-0', 'root-1', 'root-2'}
    assert max(len(params) for params in statements) <= 200
    # One inventory read, then a bounded walk per batch of 200 members.
    assert len(statements) <= 1 + 3 * 3


def malformed_member_tree(path, monkeypatch):
    """A cycle blocks the member tree; each clock read is a minute later."""
    import itertools
    from types import SimpleNamespace
    from twicc.providers import compute_selection

    current_tree(path)
    reset_offline_member(100)
    in_runtime(lambda: AgentLink.objects.create(
        session_id='faulty', agent_id='faulty', tool_use_id='cycle', tool_use_line_num=1,
    ))
    # Deferral deadlines expire at once; source age keeps wall time.
    skew = itertools.count(step=61)
    monkeypatch.setattr(background, 'time', SimpleNamespace(
        time=time.time, monotonic=lambda: time.monotonic() + next(skew),
    ))
    attempts = []
    select = compute_selection.select_tree_source

    def record(root_id, *args, **kwargs):
        attempts.append(root_id)
        return select(root_id, *args, **kwargs)
    monkeypatch.setattr(compute_selection, 'select_tree_source', record)
    # Earlier tests can reconfigure logging: record the call itself.
    warnings = []
    monkeypatch.setattr(background.logger, 'warning', lambda message, *args: warnings.append(message % args))
    return attempts, warnings


@pytest.mark.django_db(transaction=True)
def test_member_tree_malformed_forever_is_dropped_after_bounded_attempts(recovery_runtime, monkeypatch):
    path, _ = recovery_runtime
    attempts, warnings = malformed_member_tree(path, monkeypatch)
    admitted = record_admitted(monkeypatch)

    async def scenario(task, work_state):
        await asyncio.wait_for(task, 5)
        assert not work_state.has_pending()

    asyncio.run(run_coordinator(scenario))
    assert attempts == ['healthy'] * background.MEMBER_ROOT_ATTEMPTS
    assert admitted == []
    assert versions(path) == {'faulty': None, 'healthy': 123}
    assert any('Dropping malformed native tree healthy' in message for message in warnings)


@pytest.mark.django_db(transaction=True)
def test_dropped_member_tree_returns_when_a_source_change_repairs_it(recovery_runtime, monkeypatch):
    from tests.test_compute_age_selection import clone_session

    path, _ = recovery_runtime
    attempts, warnings = malformed_member_tree(path, monkeypatch)
    # A recent outdated root keeps the coordinator running after the drop.
    clone_session(path, 'other', time.time() + 1000)
    execute(path, ("UPDATE core_session SET compute_version=NULL WHERE id='other'",))
    admitted = record_admitted(monkeypatch)

    async def scenario(task, work_state):
        async with asyncio.timeout(5):
            while not warnings:
                await asyncio.sleep(.01)
        await asyncio.sleep(.2)
        assert attempts == ['healthy'] * background.MEMBER_ROOT_ATTEMPTS
        assert not task.done()
        execute(path, ("DELETE FROM core_agentlink",))
        work_state.notify_source_changed(Provider.CLAUDE_CODE, 'faulty')
        async with asyncio.timeout(5):
            while versions(path)['faulty'] != 123:
                await asyncio.sleep(.01)
        assert not task.done()

    asyncio.run(run_coordinator(scenario))
    assert admitted == ['faulty']
    assert versions(path) == {'faulty': 123, 'healthy': 123, 'other': None}
