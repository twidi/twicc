from decimal import Decimal
from importlib import import_module, util

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from twicc.core.models import Project, Session, SessionItem
from twicc.providers.contribution_revisions import SourceKey, record_contribution_commit
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.tree_aggregate_state import observe_tree_work

pytestmark = pytest.mark.django_db(transaction=True)


def api():
    assert util.find_spec('twicc.providers.tree_aggregate_prepare'), 'aggregate preparation is missing'
    return import_module('twicc.providers.tree_aggregate_prepare')


def node(sid, parent=None):
    project, _ = Project.objects.get_or_create(id='p')
    return Session.objects.create(id=sid, project=project, file_path=f'/{sid}', provider='codex',
        storage_incarnation=sid, parent_session=parent, type='subagent' if parent else 'session',
        compute_version=get_provider_helpers('codex').current_compute_version)


def item(session, cost):
    SessionItem.objects.create(session=session, line_num=1, cost=cost)


def test_null_timestamp_cost_is_included():
    module = api()
    root = node('root')
    item(root, Decimal('1.234567'))
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_session_values, work, root.id).result()
    assert prepared.values.self_cost == Decimal('1.234567')


def test_daily_cost_query_uses_existing_partial_cost_index():
    """Global daily SUM must avoid scanning timestamped rows with no cost."""
    from datetime import UTC, datetime
    from twicc.providers.contribution_revisions import DailyKey
    root = node('indexed-day')
    stamp = datetime(2026, 10, 13, tzinfo=UTC)
    SessionItem.objects.bulk_create([
        SessionItem(session=root, line_num=line, timestamp=stamp, cost=Decimal(2) if line == 1 else None)
        for line in range(1, 101)
    ])
    work = observe_tree_work(root.id, frozenset())
    plans = []
    def prepare():
        def capture(execute, sql, params, many, context):
            if sql.startswith('SELECT') and 'SUM(' in sql and 'core_sessionitem' in sql:
                with connection.cursor() as cursor:
                    cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                    plans.extend(row[3] for row in cursor.fetchall())
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            return api().prepare_daily_values(work, DailyKey(None, 'codex', stamp.date()))
    with api().AggregatePreparationExecutor() as executor:
        prepared = executor.submit(prepare).result()
    assert prepared.values.cost == Decimal(2)
    assert any('idx_item_cost_' in plan for plan in plans), plans


@pytest.mark.parametrize('costs', [
    [None, None], [None, Decimal(0)], [Decimal(-2), None],
    [Decimal('0.123456'), Decimal('0.654321'), None],
])
def test_indexed_daily_cost_preserves_null_rows_counts_and_exact_scope(costs):
    from datetime import UTC, datetime, timedelta
    from twicc.core.enums import ItemKind
    from twicc.providers.contribution_revisions import DailyKey
    root = node('cost-values')
    start = datetime(2026, 10, 13, tzinfo=UTC)
    end = start + timedelta(days=1)
    Session.objects.filter(pk=root.id).update(created_at=start, user_message_count=len(costs))
    for line, cost in enumerate(costs, 1):
        SessionItem.objects.create(session=root, line_num=line, timestamp=start,
                                   kind=ItemKind.USER_MESSAGE, cost=cost)
    for line, stamp in ((100, start - timedelta(microseconds=1)), (101, end)):
        SessionItem.objects.create(session=root, line_num=line, timestamp=stamp, cost=Decimal(999))
    other_project = Project.objects.create(id='other-project')
    for sid, project, provider in [('other-provider', root.project, 'claude_code'),
                                   ('other-project', other_project, 'codex')]:
        other = Session.objects.create(id=sid, file_path=f'/{sid}', project=project, provider=provider)
        SessionItem.objects.create(session=other, line_num=1, timestamp=start, cost=Decimal(999))
    work = observe_tree_work(root.id, frozenset())
    with api().AggregatePreparationExecutor() as executor:
        prepared = executor.submit(api().prepare_daily_values, work, DailyKey('p', 'codex', start.date())).result()
    with connection.cursor() as cursor:
        cursor.execute('SELECT SUM(i.cost) FROM core_sessionitem i JOIN core_session s ON s.id = i.session_id '
                       'WHERE s.project_id = %s AND s.provider = %s AND i.timestamp >= %s AND i.timestamp < %s',
                       ['p', 'codex', start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')])
        raw = cursor.fetchone()[0]
    assert prepared.values.cost == Decimal(str(raw or 0)).quantize(Decimal('0.000001'))
    assert prepared.values.cost == sum((cost for cost in costs if cost is not None), Decimal(0))
    assert prepared.values.message_count == len(costs)
    assert prepared.values.session_count == 1


def test_root_total_counts_each_member_once():
    module = api()
    root = node('root')
    child = node('child', root)
    deep = node('deep', child)
    for row, cost in ((root, '1'), (child, '2'), (deep, '3')):
        item(row, cost)
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_session_values, work, root.id).result()
    assert prepared.values.cost == Decimal(6)
    assert prepared.values.subagents_cost == Decimal(5)


def test_changed_observation_rejects_publication():
    module = api()
    root = node('root')
    item(root, '1')
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_session_values, work, root.id).result()
    record_contribution_commit(sources=[SourceKey(root.id, root.storage_incarnation)])
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset()) == 'changed'
    root.refresh_from_db()
    assert root.total_cost is None


def test_preparation_has_no_writer_lease_or_source_write():
    module = api()
    root = node('root')
    item(root, '2')
    work = observe_tree_work(root.id, frozenset())
    def read():
        with CaptureQueriesContext(connection) as queries:
            result = module.prepare_session_values(work, root.id)
        assert not connection.in_atomic_block
        assert not any(q['sql'].split()[0] in {'UPDATE', 'INSERT', 'DELETE'} for q in queries)
        return result
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(read).result()
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        assert module.publish_aggregate(work, prepared, frozenset()) == 'published'
    assert not any('core_sessionitem' in q['sql'] for q in queries)
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)


def test_job_release_discards_own_cache_and_uses_one_source_sum_per_tree():
    module = api()
    root = node('root')
    child = node('child', root)
    item(root, '1')
    item(child, '2')
    work = observe_tree_work(root.id, frozenset())
    def prepare_both():
        with CaptureQueriesContext(connection) as queries:
            module.prepare_session_values(work, child.id)
            result = module.prepare_session_values(work, root.id)
        assert sum('SUM(' in q['sql'] and 'core_sessionitem' in q['sql'] for q in queries) == 1
        return result
    with module.AggregatePreparationExecutor() as executor:
        assert executor.submit(prepare_both).result().values.cost == Decimal(3)
        assert hasattr(executor, 'release_work'), 'completed tree cache must have explicit ownership release'
        executor.release_work(work).result()
        assert executor.submit(prepare_both).result().values.cost == Decimal(3)


def test_executor_enforces_one_outstanding_read_and_drains_cancelled_consumer():
    from threading import Event
    module = api()
    entered, release = Event(), Event()
    executor = module.AggregatePreparationExecutor()
    def blocked():
        entered.set()
        assert release.wait(5)
        return 23
    try:
        future = executor.submit(blocked)
        assert entered.wait(5)
        assert not future.cancel()
        with pytest.raises(RuntimeError, match='outstanding'):
            executor.submit(lambda: None)
        release.set()
        assert future.result(5) == 23
    finally:
        release.set()
        executor.close()
    with pytest.raises(RuntimeError, match='closed'):
        executor.submit(lambda: None)


def test_read_snapshot_rejects_revision_change_during_sum():
    from threading import Event
    module = api()
    root = node('root')
    item(root, '1')
    work = observe_tree_work(root.id, frozenset())
    entered, release = Event(), Event()
    def pause(execute, sql, params, many, context):
        if 'SUM(' in sql and 'core_sessionitem' in sql:
            entered.set()
            assert release.wait(5)
        return execute(sql, params, many, context)
    def read():
        with connection.execute_wrapper(pause):
            return module.prepare_session_values(work, root.id)
    with module.AggregatePreparationExecutor() as executor:
        future = executor.submit(read)
        try:
            assert entered.wait(5)
            record_contribution_commit(sources=[SourceKey(root.id, root.storage_incarnation)])
        finally:
            release.set()
        with pytest.raises(module.ChangedPreparation):
            future.result(5)


def test_project_observes_hidden_and_spawned_session_contributions():
    module = api()
    root = node('root')
    hidden = node('hidden')
    spawned = node('spawned')
    Session.objects.filter(pk=root.id).update(total_cost='2')
    Session.objects.filter(pk=hidden.id).update(hidden=True, total_cost='3')
    Session.objects.filter(pk=spawned.id).update(parent_session=root, total_cost='4')
    child = node('child', root)
    Session.objects.filter(pk=child.id).update(total_cost='99')
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_project_values, work, 'p').result()
    assert prepared.values.cost == Decimal(9)
    record_contribution_commit(costs=[SourceKey(hidden.id, hidden.storage_incarnation)])
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset()) == 'changed'


@pytest.mark.parametrize('scope', ['p', None])
def test_daily_values_match_reference_and_reject_other_tree_change(scope):
    from datetime import UTC, datetime
    from twicc.core.enums import ItemKind, Provider
    from twicc.core.models import DailyActivity
    from twicc.providers.contribution_revisions import DailyKey
    module = api()
    root = node('root')
    hidden = node('hidden')
    child = node('child', root)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    Session.objects.filter(pk__in=[root.id, hidden.id, child.id]).update(created_at=now, user_message_count=1)
    Session.objects.filter(pk=hidden.id).update(hidden=True)
    for index, row in enumerate((root, hidden, child), 1):
        SessionItem.objects.create(session=row, line_num=1, timestamp=now, cost=str(index), kind=ItemKind.USER_MESSAGE)
    work = observe_tree_work(root.id, frozenset())
    key = DailyKey(scope, 'codex', now.date())
    DailyActivity.recalculate(scope, now.date(), Provider.CODEX)
    reference = DailyActivity.objects.get(project_id=scope, provider='codex', date=now.date())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_daily_values, work, key).result()
    assert prepared.values.cost == reference.cost == Decimal(6)
    assert prepared.values.message_count == reference.user_message_count == 1
    assert prepared.values.session_count == reference.session_count == 1
    record_contribution_commit(days=[key])
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset()) == 'changed'


@pytest.mark.parametrize('root_cost,child_cost,want_self,want_child,want_total', [
    (None, None, None, None, None),
    ('0', '0', Decimal(0), Decimal(0), None),
    ('0.123456789', '0.234567891', Decimal('0.123456789'), Decimal('0.234567891'), Decimal('0.358024680')),
])
def test_session_null_zero_and_legacy_fractional_costs_match_reference(
        root_cost, child_cost, want_self, want_child, want_total):
    module = api()
    root = node('root')
    child = node('child', root)
    for row, cost in ((root, root_cost), (child, child_cost)):
        item(row, None)
        if cost is not None:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE core_sessionitem SET cost = %s WHERE session_id = %s', [cost, row.id])
    root.recalculate_costs()
    reference = root.self_cost, root.subagents_cost, root.total_cost
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_session_values, work, root.id).result()
    assert (prepared.values.self_cost, prepared.values.subagents_cost, prepared.values.cost) == reference
    assert reference == (want_self, want_child, want_total)
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset()) == 'published'
    assert observe_tree_work(root.id, frozenset()).observation.revisions == work.observation.revisions


def test_publishing_child_invalidates_old_parent_preparation_without_resumming_source():
    module = api()
    root = node('root')
    child = node('child', root)
    item(root, '1')
    item(child, '2')
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        parent_prepared = executor.submit(module.prepare_session_values, work, root.id).result()
        child_prepared = executor.submit(module.prepare_session_values, work, child.id).result()
        with transaction.atomic():
            assert module.publish_aggregate(work, child_prepared, frozenset()) == 'published'
        with transaction.atomic():
            assert module.publish_aggregate(work, parent_prepared, frozenset()) == 'changed'
        def reread():
            with CaptureQueriesContext(connection) as queries:
                result = module.prepare_session_values(work, root.id)
            assert not any('core_sessionitem' in q['sql'] for q in queries)
            return result
        fresh = executor.submit(reread).result()
    with transaction.atomic():
        assert module.publish_aggregate(work, fresh, frozenset()) == 'published'
    root.refresh_from_db()
    assert root.total_cost == Decimal(3)


def test_pending_member_defers_publication():
    module = api()
    root = node('root')
    item(root, '1')
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_session_values, work, root.id).result()
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset({root.id})) == 'deferred'
    root.refresh_from_db()
    assert root.total_cost is None


def test_preparation_connection_is_dedicated_and_read_only_without_inherited_lease():
    from sqlite3 import OperationalError
    from twicc.providers.db_writer import _db_write_lock_lease
    module = api()
    root = node('root')
    item(root, '1')
    work = observe_tree_work(root.id, frozenset())
    physical = connection.connection
    lease_token = _db_write_lock_lease.set(object())
    def read():
        assert connection.connection is not physical
        assert _db_write_lock_lease.get() is None
        attempted = False
        def probe(execute, sql, params, many, context):
            nonlocal attempted
            if 'SUM(' in sql and not attempted:
                attempted = True
                with pytest.raises(OperationalError, match='readonly'):
                    context['connection'].connection.execute('UPDATE core_session SET total_cost = 999')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(probe):
            result = module.prepare_session_values(work, root.id)
        assert attempted
        return result
    try:
        with module.AggregatePreparationExecutor() as executor:
            assert executor.submit(read).result().values.cost == Decimal(1)
    finally:
        _db_write_lock_lease.reset(lease_token)
    root.refresh_from_db()
    assert root.total_cost is None


def test_async_shutdown_cancellation_drains_and_closes_worker_connection(tmp_path):
    import asyncio
    from threading import Event
    module = api()
    entered, release = Event(), Event()
    physical = []
    executor = module.AggregatePreparationExecutor()
    def blocked_read():
        connection.settings_dict = {**connection.settings_dict, 'NAME': str(tmp_path / 'shutdown.sqlite')}
        connection.ensure_connection()
        physical.append(connection.connection)
        entered.set()
        assert release.wait(5)
    async def scenario():
        future = executor.submit(blocked_read)
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(executor.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert future.done()
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        executor.close()
    from sqlite3 import ProgrammingError
    with pytest.raises(ProgrammingError, match='closed'):
        physical[0].execute('SELECT 1')


def test_daily_and_project_publications_write_only_prepared_values():
    from datetime import UTC, datetime
    from twicc.core.enums import ItemKind
    from twicc.core.models import DailyActivity
    from twicc.providers.contribution_revisions import DailyKey
    module = api()
    root = node('root')
    now = datetime(2026, 10, 13, tzinfo=UTC)
    Session.objects.filter(pk=root.id).update(created_at=now, user_message_count=1, total_cost='1.25')
    SessionItem.objects.create(session=root, line_num=1, timestamp=now, cost='1.25', kind=ItemKind.USER_MESSAGE)
    work = observe_tree_work(root.id, frozenset())
    key = DailyKey('p', 'codex', now.date())
    with module.AggregatePreparationExecutor() as executor:
        day = executor.submit(module.prepare_daily_values, work, key).result()
        project = executor.submit(module.prepare_project_values, work, 'p').result()
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        assert module.publish_aggregate(work, day, frozenset()) == 'published'
        assert module.publish_aggregate(work, project, frozenset()) == 'published'
    assert not any('core_sessionitem' in q['sql'] or 'SUM(' in q['sql'] for q in queries)
    assert Project.objects.get(pk='p').total_cost == Decimal('1.25')
    daily = DailyActivity.objects.get(project_id='p', provider='codex', date=now.date())
    assert (daily.cost, daily.user_message_count, daily.session_count) == (Decimal('1.25'), 1, 1)


def test_project_scope_revision_rejects_new_unobserved_root():
    module = api()
    root = node('root')
    Session.objects.filter(pk=root.id).update(total_cost='1')
    work = observe_tree_work(root.id, frozenset())
    with module.AggregatePreparationExecutor() as executor:
        prepared = executor.submit(module.prepare_project_values, work, 'p').result()
    other = node('other')
    Session.objects.filter(pk=other.id).update(total_cost='9')
    record_contribution_commit(projects=['p'])
    with transaction.atomic():
        assert module.publish_aggregate(work, prepared, frozenset()) == 'changed'
    assert Project.objects.get(pk='p').total_cost is None


def test_own_count_publication_invalidates_prepared_creation_day_only():
    from datetime import UTC, datetime, timedelta
    from twicc.core.enums import ItemKind
    from twicc.providers.contribution_revisions import DailyKey, observe_revisions, revisions_match
    module = api()
    root = node('root')
    now = datetime(2026, 10, 13, tzinfo=UTC)
    Session.objects.filter(pk=root.id).update(created_at=now, user_message_count=0)
    SessionItem.objects.create(session=root, line_num=1, timestamp=now + timedelta(days=1), kind=ItemKind.USER_MESSAGE)
    work = observe_tree_work(root.id, frozenset())
    key = DailyKey('p', 'codex', now.date())
    other_day = observe_revisions(days=[DailyKey('p', 'codex', (now + timedelta(days=1)).date())])
    with module.AggregatePreparationExecutor() as executor:
        day = executor.submit(module.prepare_daily_values, work, key).result()
        own = executor.submit(module.prepare_session_values, work, root.id).result()
    assert day.values.session_count == 0
    with transaction.atomic():
        assert module.publish_aggregate(work, own, frozenset()) == 'published'
    assert revisions_match(work.observation.revisions)
    assert revisions_match(other_day)
    with transaction.atomic():
        assert module.publish_aggregate(work, day, frozenset()) == 'changed'


def test_project_replacement_preserves_direct_worktree_project_scope():
    """Project storage totals match the direct-project reference, before UI aggregation."""
    from twicc.core.models import Project
    root = node('main-root')
    worktree = Project.objects.create(id='worktree', worktree_of_id='p')
    other = node('worktree-root')
    Session.objects.filter(pk=root.id).update(total_cost='2')
    Session.objects.filter(pk=other.id).update(project=worktree, total_cost='3')
    work = observe_tree_work(root.id, frozenset())
    with api().AggregatePreparationExecutor() as executor:
        prepared = executor.submit(api().prepare_project_values, work, 'p').result()
    assert prepared.values.cost == Decimal(2)
    main = Project.objects.get(pk='p')
    main.recalculate_total_cost()
    assert main.total_cost == prepared.values.cost
    other_work = observe_tree_work(other.id, frozenset())
    with api().AggregatePreparationExecutor() as executor:
        prepared = executor.submit(api().prepare_project_values, other_work, worktree.id).result()
    assert prepared.values.cost == Decimal(3)


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('remove', [False, True])
def test_live_count_eligibility_rejects_other_tree_creation_day_preparation(pending, remove):
    """Timestamp-null live rows still change the source's creation-day session count."""
    from datetime import UTC, datetime
    from twicc.core.enums import ItemKind
    from twicc.providers.compute_repair_journal import merge_compute_repair
    from twicc.providers.contribution_revisions import DailyKey
    from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution
    ready = node('ready-tree')
    changing = node('changing-tree')
    stamp = datetime(2026, 10, 13, tzinfo=UTC)
    Session.objects.filter(pk__in=[ready.id, changing.id]).update(created_at=stamp)
    if remove:
        Session.objects.filter(pk=changing.id).update(user_message_count=1)
        SessionItem.objects.create(session=changing, line_num=1, kind=ItemKind.USER_MESSAGE)
        from twicc.core.models import DailyActivity, WeeklyActivity
        from twicc.core.enums import Provider
        from datetime import timedelta
        for scope in ('p', None):
            DailyActivity.recalculate(scope, stamp.date(), Provider.CODEX)
            WeeklyActivity.recalculate(scope, stamp.date() - timedelta(days=stamp.weekday()), Provider.CODEX)
    if pending:
        with transaction.atomic():
            merge_compute_repair(changing.id, changing.storage_incarnation, source_changed=True, baseline_pending=True)
    work = observe_tree_work(ready.id, frozenset())
    key = DailyKey('p', 'codex', stamp.date())
    with api().AggregatePreparationExecutor() as executor:
        prepared = executor.submit(api().prepare_daily_values, work, key).result()
    assert prepared.values.session_count == int(remove)
    changing.refresh_from_db()
    before = session_contribution(changing)
    before_items = item_contributions(SessionItem.objects.filter(session=changing))
    with transaction.atomic():
        if remove:
            SessionItem.objects.filter(session=changing).update(kind=ItemKind.ASSISTANT_MESSAGE)
        else:
            SessionItem.objects.create(session=changing, line_num=1, kind=ItemKind.USER_MESSAGE)
        apply_contribution_changes(before_items, item_contributions(SessionItem.objects.filter(session=changing)),
            before_sessions=[before], after_sessions=[before], repair=False)
    with transaction.atomic():
        assert api().publish_aggregate(work, prepared, frozenset()) == 'changed'
