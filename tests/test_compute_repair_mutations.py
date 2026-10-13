"""Supported source mutations retain durable aggregate obligations."""

from tests.tree_aggregate_helpers import begin_history_replacement, drain_aggregates
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, SessionItem, WeeklyActivity
from twicc.providers import compute_repair, compute_repair_journal as journal
from twicc.providers.db_writer import (
    CreateSessionPayload, DeleteSessionsPayload, MarkSessionsStalePayload, UpdateSessionPayload,
    _apply_create_session_payload, _apply_delete_sessions_payload, _apply_mark_sessions_stale_payload,
    _apply_update_session_payload,
)
from twicc.providers.live_aggregates import contribution_activity_keys, session_contribution
from twicc.providers.helpers import get_provider_helpers
from tests.test_compute_repair_exact import make_session, record, raw_state, reference, STAMP

pytestmark = pytest.mark.django_db(transaction=True)


def test_absent_journal_probe_does_not_enumerate_history():
    session = make_session('absent')
    def forbidden():
        raise AssertionError('history scanned without pending journal')
        yield
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        assert compute_repair.preserve_pending_repair(
            session.id, session.storage_incarnation, before_sessions=[session_contribution(session)],
            after_sessions=[session_contribution(session)], historical_timestamps=forbidden(),
        ).project_ids == frozenset({'p'})
    assert all('CORE_SESSIONITEM' not in query['sql'].upper() for query in queries)


@pytest.mark.parametrize('old_created, new_created', [(STAMP, None), (None, STAMP)])
def test_scope_parent_and_null_creation_preservation(old_created, new_created):
    session = make_session('scopes')
    before = session_contribution(session)._replace(created_at=old_created)
    old = record(session.id, session.storage_incarnation)
    after = before._replace(project_id='new-project', provider=Provider.CODEX, type='subagent',
                            hidden=True, parent_session_id='new-parent', created_at=new_created)
    before = before._replace(parent_session_id='old-parent')
    stamp = STAMP - timedelta(days=30)
    with transaction.atomic():
        pending = compute_repair.preserve_pending_repair(
            session.id, session.storage_incarnation, before_sessions=[before], after_sessions=[after],
            historical_timestamps=[stamp, None],
        )
    assert pending.activity_keys >= old.activity_keys
    assert pending.project_ids == {'p', 'new-project'}
    assert pending.parent_ids == {'old-parent', 'new-parent'}
    assert pending.observation.generation > old.observation.generation


@pytest.mark.parametrize('adopted', [False, True])
def test_raw_initial_sync_mutation_invalidates_pending_generation(adopted):
    session = make_session('raw')
    old = record(session.id, session.storage_incarnation)
    if adopted:
        payload = CreateSessionPayload(Provider.CLAUDE_CODE, session.project_id, None, False,
                                       session, [(1, '{}')], 100, 1, 1.0)
        _apply_create_session_payload(payload)
    else:
        _apply_update_session_payload(UpdateSessionPayload(Provider.CLAUDE_CODE, session, [(1, '{}')],
                                                           100, 1, 1.0, False, False))
    pending = journal.load_compute_repair(session.id, session.storage_incarnation)
    assert pending.observation.generation > old.observation.generation
    row = SessionItem.objects.get(session=session)
    assert row.timestamp is row.kind is row.cost is None


def test_live_append_invalidates_pending_generation(tmp_path):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.live_sync import LiveSyncLimits
    session = make_session('live')
    old = record(session.id, session.storage_incarnation)
    path = tmp_path / 'live.jsonl'
    path.write_text('{"type":"user","timestamp":"2026-10-13T12:00:00Z",'
                    '"message":{"role":"user","content":"hello"}}\n')
    ClaudeCodeSessionCompute().sync_session_slice(session.id, path, limits=LiveSyncLimits())
    pending = journal.load_compute_repair(session.id, session.storage_incarnation)
    assert pending.observation.generation > old.observation.generation
    with pytest.raises(journal.RepairObservationChanged), transaction.atomic():
        journal.require_compute_repair(old.observation)
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.helpers import get_provider_helpers

    assert BaseSessionCompute.apply_session_complete({
        'session_id': session.id, 'observed_last_offset': 0,
    }).outcome == 'superseded'
    session.refresh_from_db()
    assert BaseSessionCompute.apply_session_complete({
        'session_id': session.id, 'observed_last_offset': session.last_offset, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers(session.provider).current_compute_version},
    }).outcome == 'applied'
    drain_aggregates()
    assert journal.load_compute_repair(session.id, session.storage_incarnation) is None
    assert raw_state() == reference(pending.activity_keys, [session])


def test_visibility_preserves_history_and_keeps_journal():
    from twicc.core.services.session_visibility import _apply_visibility_flag
    session = make_session('visible')
    stamp = STAMP - timedelta(days=30)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=stamp,
                               kind=ItemKind.USER_MESSAGE, cost=Decimal('0.2'))
    old = record(session.id, session.storage_incarnation)
    _apply_visibility_flag(session.id, True)
    pending = journal.load_compute_repair(session.id, session.storage_incarnation)
    assert pending.observation.generation > old.observation.generation
    assert session.project_id in pending.project_ids
    drain_aggregates()
    assert raw_state() == reference(pending.activity_keys, [session])


@pytest.mark.parametrize('shape', ['root', 'child', 'tree', 'missing'])
def test_deletion_settles_all_deleted_incarnations_and_retained_parent(shape):
    parent = make_session('parent', subagents_cost=Decimal(99))
    root = make_session('root', parent_session=parent if shape == 'child' else None)
    child = make_session('child', parent_session=root, type='subagent') if shape == 'tree' else None
    sessions = [root] + ([child] if child else [])
    keys = set()
    for session in sessions:
        for incarnation in (session.storage_incarnation, 'older'):
            snapshot = record(session.id, incarnation, parents=[parent.id])
            keys.update(snapshot.activity_keys)
    requested_id = root.id
    deleted_session_ids = [s.id for s in sessions]
    if shape == 'missing':
        root.delete()
    deleted = _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CLAUDE_CODE, [requested_id]))
    assert deleted == ([] if shape == 'missing' else sorted(deleted_session_ids))
    drain_aggregates()
    assert journal.load_session_repairs(deleted_session_ids) == ()
    assert raw_state() == reference(keys, [parent])


def test_wrong_provider_delete_preserves_source_and_all_journals():
    session = make_session('wrong')
    old = record(session.id, 'older')
    assert _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CODEX, [session.id])) == []
    assert journal.load_compute_repair(session.id, 'older') == old
    assert type(session).objects.filter(pk=session.id).exists()


def test_delete_rolls_back_source_and_journal_on_journal_failure(monkeypatch):
    session = make_session('rollback')
    old = record(session.id, session.storage_incarnation)
    def fail(*args, **kwargs):
        raise RuntimeError('bucket failure')
    monkeypatch.setattr(journal, 'merge_compute_repair', fail)
    with pytest.raises(RuntimeError, match='bucket failure'):
        _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CLAUDE_CODE, [session.id]))
    assert type(session).objects.filter(pk=session.id).exists()
    assert journal.load_compute_repair(session.id, session.storage_incarnation) == old


def test_stale_flag_alone_does_not_change_journal():
    session = make_session('stale')
    old = record(session.id, session.storage_incarnation)
    _apply_mark_sessions_stale_payload(MarkSessionsStalePayload(Provider.CLAUDE_CODE, [session.id]))
    assert journal.load_compute_repair(session.id, session.storage_incarnation) == old


def test_replacement_retains_removed_keys_and_invalidates_each_raw_phase():
    from twicc.providers.codex.rollout_migration import (
        ReplaceCodexHistoryJob, _insert_replace_codex_history_chunk,
        _finish_replace_codex_history,
    )
    session = make_session('replace')
    old = record(session.id, session.storage_incarnation)
    removed_stamp = STAMP - timedelta(days=60)
    removed_day = removed_stamp.date()
    removed_week = removed_day - timedelta(days=removed_day.weekday())
    with transaction.atomic():
        old = journal.merge_compute_repair(
            session.id, session.storage_incarnation, source_changed=True,
            activity_keys=[(DailyActivity, 'p', Provider.CLAUDE_CODE, removed_day),
                           (WeeklyActivity, None, Provider.CLAUDE_CODE, removed_week)],
        )
    DailyActivity.objects.create(project_id='p', provider=Provider.CLAUDE_CODE, date=removed_day, cost=99)
    WeeklyActivity.objects.create(project_id=None, provider=Provider.CLAUDE_CODE, date=removed_week, cost=99)
    stamp = STAMP - timedelta(days=30)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=stamp, cost=1)
    job = ReplaceCodexHistoryJob(Provider.CLAUDE_CODE, session.id, [(1, '{}')], 100, 1, 2.0, None)
    generation = old.observation.generation
    for mutate in (lambda: begin_history_replacement(job),
                   lambda: _insert_replace_codex_history_chunk(session.id, job.items),
                   lambda: _finish_replace_codex_history(job)):
        mutate()
        pending = journal.load_compute_repair(session.id, session.storage_incarnation)
        assert pending.activity_keys >= old.activity_keys
        assert pending.observation.generation > generation
        generation = pending.observation.generation
        assert DailyActivity.objects.filter(date=removed_day).exists()
        assert WeeklyActivity.objects.filter(date=removed_week).exists()
    assert pending.activity_keys >= contribution_activity_keys(
        sessions=[session_contribution(session)._replace(created_at=stamp)],
    )
    from twicc.providers.compute_base import BaseSessionCompute

    assert BaseSessionCompute.apply_session_complete({
        'session_id': session.id, 'observed_last_offset': job.last_offset, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers(session.provider).current_compute_version},
    }).outcome == 'applied'
    drain_aggregates()
    assert raw_state() == reference(pending.activity_keys, [session])
    assert journal.load_session_repairs([session.id]) == ()


def test_missing_source_deletion_repairs_cross_provider_orphans_only():
    replacement = make_session('replacement', self_cost=Decimal(99), user_message_count=17)
    record('missing', 'old', parents=['missing-parent'])
    with transaction.atomic():
        journal.merge_compute_repair('missing', 'old', source_changed=True,
            activity_keys=[(DailyActivity, None, Provider.CODEX, STAMP.date())])
    DailyActivity.objects.create(project_id=None, provider=Provider.CODEX, date=STAMP.date(), cost=99)
    before = raw_state()[0]
    assert _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CLAUDE_CODE, ['missing'])) == []
    drain_aggregates()
    assert raw_state()[0] == before
    assert journal.load_session_repairs(['missing']) == ()
    assert not DailyActivity.objects.filter(provider=Provider.CODEX).exists()
    assert replacement.self_cost == Decimal(99)
