"""Source transactions enqueue native roots without publishing derived values."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import transaction

from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, StatisticsQueueEntry, DailyActivity
from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution

# The lines are dated 2026-10-14: freeze the clock one day later so the content storage sees hot sessions.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("frozen_now"),
    pytest.mark.frozen_now_at(datetime(2026, 10, 15, tzinfo=UTC)),
]


def make_session(name='root', **kwargs):
    project, _ = Project.objects.get_or_create(id='p')
    return Session.objects.create(id=name, project=project, file_path=name, provider='claude_code',
        storage_incarnation=name, created_at=datetime(2026, 10, 14, tzinfo=UTC), **kwargs)


def queue_ids():
    return list(StatisticsQueueEntry.objects.order_by('id').values_list('root_session_id', flat=True))


def apply_batch(sessions, count=15, *, cost=Decimal('0.25'), kind=None):
    before = [session_contribution(s) for s in sessions]
    with transaction.atomic():
        for s in sessions:
            SessionItem.objects.bulk_create([SessionItem(session=s, line_num=n, content='{}', cost=cost, kind=kind)
                                            for n in range(1, count + 1)])
        after = item_contributions(SessionItem.objects.filter(session__in=sessions))
        apply_contribution_changes([], after, before_sessions=before, after_sessions=before)
    return after


@pytest.mark.parametrize('roots', [1, 2])
def test_batch_enqueues_each_native_root_once(roots):
    sessions = [make_session(f'root-{i}', compute_version=0) for i in range(roots)]
    apply_batch(sessions)
    assert queue_ids() == sorted(s.id for s in sessions)
    for s in sessions:
        s.refresh_from_db()
        assert s.self_cost is None
    assert not DailyActivity.objects.exists()


def test_debug_only_batch_has_no_statistics_work():
    apply_batch([make_session()], cost=None)
    assert queue_ids() == []


def test_first_cost_free_message_updates_source_count_and_replay_is_empty():
    s = make_session(compute_version=0)
    rows = apply_batch([s], count=1, cost=None, kind=ItemKind.USER_MESSAGE)
    s.refresh_from_db()
    assert s.user_message_count == 1
    assert queue_ids() == ['root']
    with transaction.atomic():
        state = session_contribution(s)
        apply_contribution_changes(rows, rows, before_sessions=[state], after_sessions=[state])
    s.refresh_from_db()
    assert s.user_message_count == 1
    assert queue_ids() == ['root']


def test_failed_source_batch_leaves_no_queue_or_source_count():
    s = make_session()
    with pytest.raises(ValueError), transaction.atomic():
        apply_batch([s], count=1, cost=None, kind=ItemKind.USER_MESSAGE)
        assert queue_ids() == ['root']
        raise ValueError('rollback')
    assert queue_ids() == []
    s.refresh_from_db()
    assert s.user_message_count == 0


def test_hierarchy_move_enqueues_old_and_new_native_roots():
    old, new = make_session('old'), make_session('new')
    child = make_session('child', type='subagent', parent_session=old)
    before = session_contribution(child)
    with transaction.atomic():
        child.parent_session = new
        child.save(update_fields=['parent_session'])
        apply_contribution_changes([], [], before_sessions=[before],
                                   after_sessions=[session_contribution(child)])
    assert set(queue_ids()) == {'old', 'new'}


from tests.statistics_helpers import drain_statistics


def test_partial_background_tree_publishes_live_message_and_creation_count(monkeypatch):
    s = make_session(compute_version=0)
    rows = apply_batch([s], count=1, cost=None, kind=ItemKind.USER_MESSAGE)
    drain_statistics(monkeypatch)
    s.refresh_from_db()
    assert s.user_message_count == 1
    assert DailyActivity.objects.get(project_id='p', date='2026-10-14').session_count == 1
    with transaction.atomic():
        state = session_contribution(s)
        apply_contribution_changes(rows, rows, before_sessions=[state], after_sessions=[state])
    drain_statistics(monkeypatch)
    assert DailyActivity.objects.get(project_id='p', date='2026-10-14').session_count == 1
    assert not StatisticsQueueEntry.objects.exists()


def test_workflow_only_write_enqueues_native_root_and_preserves_published_cost():
    from twicc.core.models import Workflow
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    root = make_session()
    child = make_session('child', type='subagent', parent_session=root)
    workflow = Workflow.objects.create(session=child, run_id='wf_test', raw_json='{}', cost=Decimal(7))
    ClaudeCodeSessionsWatcher._save_workflow_run(child.id, 'p', workflow.run_id, '{"phases": []}')
    workflow.refresh_from_db()
    assert workflow.cost == Decimal(7)
    assert queue_ids() == ['root']


def test_hiding_published_cost_free_session_recalculates_surviving_days(monkeypatch):
    from twicc.core.services.session_visibility import _apply_visibility_flag
    s = make_session()
    apply_batch([s], count=1, cost=None, kind=ItemKind.USER_MESSAGE)
    drain_statistics(monkeypatch)
    assert DailyActivity.objects.get(project_id='p').session_count == 1
    _apply_visibility_flag(s.id, True)
    drain_statistics(monkeypatch)
    assert all(row.session_count == row.user_message_count == row.cost == 0
               for row in DailyActivity.objects.all())


def test_timestamp_only_debug_batch_does_not_enqueue(tmp_path):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.live_sync import LiveSyncLimits
    s = make_session()
    Session.objects.filter(pk=s.pk).update(created_at=None)
    path = tmp_path / 'debug.jsonl'
    path.write_text('{"type":"system","timestamp":"2026-10-14T00:00:00Z"}\n')
    ClaudeCodeSessionCompute().sync_session_slice(s.id, path, limits=LiveSyncLimits())
    assert queue_ids() == []
