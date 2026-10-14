"""Bounded replacement publications retain writer admission through commit."""
import asyncio
from datetime import UTC, datetime, date
from decimal import Decimal

import pytest

from twicc.core.models import Project, Session, DailyActivity
from twicc.providers import statistics_publication as publication
from twicc.providers.contribution_revisions import (
    DailyKey, SourceKey, observe_own_cost_invalidations, restrict_own_cost_observation,
    record_own_cost_invalidation,
)
from twicc.providers.statistics_prepare import PreparedOwnCost, PreparedTreeCosts, SessionCosts, ActivityValues

pytest_plugins = ['tests.test_compute_continuation']

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def writer(monkeypatch):
    calls = []
    async def admitted(function, /, *args):
        calls.append(function)
        return await asyncio.to_thread(function, *args)
    monkeypatch.setattr(publication, '_publish', admitted)
    return calls


def prepared(session):
    observation = restrict_own_cost_observation(observe_own_cost_invalidations(),
        SourceKey(session.id, session.storage_incarnation))
    own = PreparedOwnCost(session.id, datetime(2026, 1, 1, tzinfo=UTC), Decimal('1.234567'), observation)
    return PreparedTreeCosts((own,), (SessionCosts(session.id, own.self_cost, None, own.self_cost, session.storage_incarnation),), (), ())


def test_exact_replacement_replay(writer):
    project = Project.objects.create(id='project')
    session = Session.objects.create(id='session', project=project, storage_incarnation='original')
    values = prepared(session)
    async def run():
        await publication.publish_tree_costs(values)
        await publication.publish_tree_costs(values)
        key = DailyKey(project.id, 'codex', date(2026, 1, 1))
        await publication.publish_daily(key, ActivityValues(2, 1, Decimal('3.5')))
        await publication.publish_daily(key, ActivityValues(2, 1, Decimal('3.5')))
    asyncio.run(run())
    session.refresh_from_db()
    assert session.self_cost == Decimal('1.234567')
    assert session.items_cost_computed_through_at == values.own_updates[0].observed_at
    assert DailyActivity.objects.get().user_message_count == 2


def test_invalidated_before_publication_never_sets_marker(writer):
    from django.db import transaction
    project = Project.objects.create(id='project')
    session = Session.objects.create(id='session', project=project, storage_incarnation='original')
    values = prepared(session)
    with transaction.atomic():
        record_own_cost_invalidation(session.id, session.storage_incarnation)
    with pytest.raises(publication.BackgroundInvalidated):
        asyncio.run(publication.publish_tree_costs(values))
    session.refresh_from_db()
    assert session.items_cost_computed_through_at is None


def test_bounded_slices_yield_and_revalidate(writer, monkeypatch):
    project = Project.objects.create(id='project')
    sessions = [Session(id=str(index), file_path=str(index), project=project, storage_incarnation=str(index)) for index in range(257)]
    Session.objects.bulk_create(sessions)
    values = [prepared(session) for session in sessions]
    tree = PreparedTreeCosts(tuple(value.own_updates[0] for value in values),
        tuple(value.session_costs[0] for value in values), (), ())
    original = publication._publish
    events = []
    async def admitted(function, /, *args):
        result = await original(function, *args)
        events.append('publication')
        if len(events) == 1:
            asyncio.get_running_loop().call_soon(events.append, 'foreground')
        return result
    monkeypatch.setattr(publication, '_publish', admitted)
    asyncio.run(publication.publish_tree_costs(tree))
    assert events == ['publication', 'foreground', 'publication']
    assert len(writer) == 2


def test_cached_source_identity_change_rejects_replacement(writer):
    project = Project.objects.create(id='project')
    session = Session.objects.create(id='session', project=project, storage_incarnation='original')
    from concurrent.futures import ThreadPoolExecutor
    from twicc.providers.statistics_prepare import prepare_tree_costs
    now = datetime(2026, 1, 1, tzinfo=UTC)
    Session.objects.filter(pk=session.id).update(
        last_updated_at=now, items_cost_computed_through_at=now, self_cost=Decimal(2))
    with ThreadPoolExecutor(max_workers=1) as executor:
        values = executor.submit(prepare_tree_costs, (session.id,)).result()
    assert values.own_updates == ()
    Session.objects.filter(pk=session.id).update(storage_incarnation='replacement')
    with pytest.raises(publication.BackgroundInvalidated):
        asyncio.run(publication.publish_tree_costs(values))
    session.refresh_from_db()
    assert session.total_cost is None


def test_invalidation_between_slices_rejects_remaining_rows(writer, monkeypatch):
    from django.db import transaction
    project = Project.objects.create(id='project')
    sessions = [Session(id=str(index), file_path=str(index), project=project, storage_incarnation=str(index)) for index in range(257)]
    Session.objects.bulk_create(sessions)
    values = [prepared(session) for session in sessions]
    tree = PreparedTreeCosts(tuple(value.own_updates[0] for value in values),
        tuple(value.session_costs[0] for value in values), (), ())
    original = publication._publish
    calls = 0
    async def admitted(function, /, *args):
        nonlocal calls
        result = await original(function, *args)
        calls += 1
        if calls == 1:
            def invalidate():
                with transaction.atomic():
                    Session.objects.filter(pk=sessions[0].id).update(items_cost_computed_through_at=None)
                    record_own_cost_invalidation(sessions[0].id, sessions[0].storage_incarnation)
            await asyncio.to_thread(invalidate)
        return result
    monkeypatch.setattr(publication, '_publish', admitted)
    with pytest.raises(publication.BackgroundInvalidated):
        asyncio.run(publication.publish_tree_costs(tree))
    sessions[0].refresh_from_db()
    sessions[-1].refresh_from_db()
    assert sessions[0].items_cost_computed_through_at is None
    assert sessions[-1].items_cost_computed_through_at is None


def test_real_writer_prefers_waiting_foreground_and_uses_worker(monkeypatch, wal_runtime):
    from twicc.providers import db_writer
    session, _ = wal_runtime
    events = []
    original = publication._session_slice
    def apply(*args):
        from threading import current_thread, main_thread
        assert current_thread() is not main_thread()
        events.append('background')
        return original(*args)
    monkeypatch.setattr(publication, '_session_slice', apply)
    row = SessionCosts(session.id, Decimal(1), None, Decimal(1), session.storage_incarnation)
    tree = PreparedTreeCosts((), (row,), (), ())
    async def run():
        db_writer.start_db_writer()
        try:
            started = asyncio.Event()
            release = asyncio.Event()
            async def first():
                started.set()
                await release.wait()
            held = asyncio.create_task(db_writer.run_under_storage_write_lock(first,
                priority=db_writer.WritePriority.BACKGROUND))
            await started.wait()
            background = asyncio.create_task(publication.publish_tree_costs(tree))
            await asyncio.sleep(0)
            async def foreground():
                events.append('foreground')
            front = asyncio.create_task(db_writer.run_under_storage_write_lock(foreground,
                priority=db_writer.WritePriority.FOREGROUND))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(held, background, front)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(run())
    assert events == ['foreground', 'background']


def test_remaining_replacements_are_exact(writer):
    from twicc.core.models import WeeklyActivity, Workflow
    project = Project.objects.create(id='project')
    session = Session.objects.create(id='session', project=project)
    workflow = Workflow.objects.create(run_id='run', session=session)
    key = DailyKey(project.id, 'codex', date(2026, 1, 5))
    async def run():
        for _ in range(2):
            await publication.publish_weekly(key, ActivityValues(3, 2, Decimal('4.25')))
            await publication.publish_project_cost(project.id, Decimal('4.25'))
            await publication.publish_workflow_cost(workflow.id, Decimal('4.25'), {'phase': 4.25})
    asyncio.run(run())
    project.refresh_from_db()
    workflow.refresh_from_db()
    assert WeeklyActivity.objects.get().cost == Decimal('4.25')
    assert project.total_cost == Decimal('4.25')
    assert workflow.cost == Decimal('4.25')
    assert workflow.phases_cost == {'phase': 4.25}


def test_slice_checks_observations_with_one_filesystem_identity_lookup(writer, monkeypatch):
    from twicc.providers import contribution_revisions
    project = Project.objects.create(id='project')
    sessions = [Session.objects.create(id=str(i), file_path=str(i), project=project,
        storage_incarnation=str(i)) for i in range(3)]
    values = [prepared(session) for session in sessions]
    tree = PreparedTreeCosts(tuple(value.own_updates[0] for value in values),
        tuple(value.session_costs[0] for value in values), (), ())
    calls = []
    original = contribution_revisions._identity
    def identity():
        calls.append(True)
        return original()
    monkeypatch.setattr(contribution_revisions, '_identity', identity)
    asyncio.run(publication.publish_tree_costs(tree))
    assert len(calls) == 1


def test_real_two_slice_publication_admits_foreground_between_slices(monkeypatch, wal_runtime):
    import threading
    from twicc.providers import db_writer
    session, _ = wal_runtime
    entered, release = threading.Event(), threading.Event()
    events = []
    original = publication._session_slice
    monkeypatch.setattr(publication, 'SESSION_PUBLICATION_LIMIT', 1)

    def apply(*args):
        result = original(*args)
        events.append('background')
        if events == ['background']:
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(publication, '_session_slice', apply)

    async def scenario():
        db_writer.start_db_writer()
        try:
            def create():
                Session.objects.create(id='second', file_path='second', project_id=session.project_id,
                    storage_incarnation='second')
            await db_writer.publish_statistics_sync(create)
            tree = PreparedTreeCosts((), (
                SessionCosts(session.id, Decimal(1), None, Decimal(1), session.storage_incarnation),
                SessionCosts('second', Decimal(2), None, Decimal(2), 'second')), (), ())
            background = asyncio.create_task(publication.publish_tree_costs(tree))
            assert await asyncio.to_thread(entered.wait, 3)
            async def foreground():
                events.append('foreground')
            front = asyncio.create_task(db_writer.run_under_storage_write_lock(
                foreground, priority=db_writer.WritePriority.FOREGROUND))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(background, front)
        finally:
            release.set()
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
    assert events == ['background', 'foreground', 'background']


def test_cancelled_publication_keeps_writer_through_commit(wal_runtime):
    import threading
    import sqlite3
    from django.db import connection, transaction
    from twicc.providers import db_writer
    session, path = wal_runtime
    entered, release = threading.Event(), threading.Event()
    events = []

    def apply():
        commit = connection.commit
        def blocked_commit():
            entered.set()
            assert release.wait(5)
            commit()
            events.append('commit')
        connection.commit = blocked_commit
        try:
            with transaction.atomic():
                Session.objects.filter(pk=session.id).update(title='committed statistics')
        finally:
            connection.commit = commit

    async def scenario():
        db_writer.start_db_writer()
        try:
            background = asyncio.create_task(db_writer.publish_statistics_sync(apply))
            assert await asyncio.to_thread(entered.wait, 3)
            background.cancel()
            async def foreground():
                events.append('foreground')
            front = asyncio.create_task(db_writer.run_under_storage_write_lock(
                foreground, priority=db_writer.WritePriority.FOREGROUND))
            await asyncio.sleep(0)
            background.cancel()
            await asyncio.sleep(0)
            assert not background.done() and not front.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await background
            await front
        finally:
            release.set()
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
    assert events == ['commit', 'foreground']
    with sqlite3.connect(path) as database:
        assert database.execute('SELECT title FROM core_session WHERE id=?', (session.id,)).fetchone() == (
            'committed statistics',)
