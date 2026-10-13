"""Contribution commits invalidate only their input domains.

Removing commit callbacks must fail mutation tests; publishing derived costs
must leave source/day/graph observations usable.

Concrete mutation coverage (S=source, G=native graph, D=daily, C=derived cost,
P=project input). P follows Project.recalculate_total_cost: type=SESSION,
including hidden and stale rows. D message/session eligibility excludes hidden.

Writer / publication                             Hook and domains
apply_session_items_chunk (deferred or legacy)    record_compute_repair / apply_contribution_changes: S,D,P
_apply_session_complete_final item + scope       record_compute_repair / lifecycle_change / apply: S,G,D,C,P
_sync_session_slice new AND replayed item rows   preserve_pending_repair + retained bounded evidence: S,D,P
raw create/update DB-writer payloads             preserve_pending_repair + apply: S,G,D,C,P
BaseSessionsWatcher.create_session_sync          record_source_changes after initialized identity: S,G,P
_apply_delete_sessions_payload                  apply with retained incarnation/items: S,G,D,C,P
Codex replacement begin/insert/finish            preserve_pending_repair even without journal: S,D,P
save_lifecycle / update_lifecycle                before/after metadata: S,G,D,C,P
visibility _apply_visibility_flag               lifecycle + legacy retained historical buckets: S,D,P
_save_session_aggregates + repair children       save_derived_costs: C,P only when values change
repair_retained_parents / repair recovery        save_derived_costs: C,P only when values change
Project.total_cost writers (projects.py, writer) no hook: output never changes its input revision
title/auth/archive/stale-only lifecycle          no contribution domain changes
content override/compression/reference changes   no contribution domain changes

Scope history keys reuse existing legacy enumeration. Task 9 must prepare
those old/new keys outside writer and validate source observations before
mutation when it removes legacy repair dispatch. Specifically, replace the
SessionItem history loop in apply_contribution_changes and the lazy historical
queries supplied by _apply_visibility_flag / _sync_session_slice to
preserve_pending_repair. No hook adds an item-history scan.

Live replay interface: _sync_session_slice retains before_existing_live,
after_existing_live, and inserted_contributions. preserve_pending_repair gets
before_items=before_existing_live and after_items=inserted + existing rows.
Legacy apply_contribution_changes still receives inserted_contributions only.
Task 9 must retain the replay evidence in its new delta/preparation path.
"""

from tests.tree_aggregate_helpers import begin_history_replacement, publish_session_values, publish_project_values
from datetime import date
from decimal import Decimal

import pytest
from django.db import transaction

from twicc.providers import contribution_revisions as revisions
from twicc.core.models import Project, SessionItem
from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution
from tests.test_compute_repair_exact import make_session, STAMP

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def reset():
    revisions.reset_contribution_revisions()


def test_commit_invalidates_selected_domains_only():
    source = revisions.SourceKey('root', 'one')
    day = revisions.DailyKey('project', 'codex', date(2026, 10, 13))
    observed = revisions.observe_revisions(sources=[source], days=[day])
    unrelated = revisions.observe_revisions(sources=[revisions.SourceKey('other', 'one')])
    with transaction.atomic():
        revisions.record_contribution_commit(sources=[source], days=[day])
        assert revisions.revisions_match(observed)
    assert not revisions.revisions_match(observed)
    assert revisions.revisions_match(unrelated)


def test_rollback_advances_nothing():
    source = revisions.SourceKey('root', 'one')
    observed = revisions.observe_revisions(sources=[source], roots=[source], costs=[source], projects=['p'])
    with transaction.atomic():
        revisions.record_contribution_commit(sources=[source], roots=[source], costs=[source], projects=['p'])
        transaction.set_rollback(True)
    assert revisions.revisions_match(observed)


def test_other_root_cost_publication_invalidates_project_preparation():
    source = revisions.SourceKey('root', 'one')
    project = revisions.observe_revisions(projects=['p'])
    tree = revisions.observe_revisions(sources=[source], roots=[source])
    revisions.record_contribution_commit(costs=[revisions.SourceKey('other', 'one')], projects=['p'])
    assert not revisions.revisions_match(project)
    assert revisions.revisions_match(tree)


def test_reset_invalidates_pre_reset_observations():
    observed = revisions.observe_revisions(projects=['p'])
    revisions.reset_contribution_revisions()
    assert not revisions.revisions_match(observed)


def observe_source(session, **kwargs):
    return revisions.observe_revisions(sources=[revisions.SourceKey(session.id, session.storage_incarnation)], **kwargs)


def test_null_timestamp_cost_change_invalidates_source_and_project():
    session = make_session('null-cost')
    row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
    observed = observe_source(session)
    project = revisions.observe_revisions(projects=['p'])
    with transaction.atomic():
        before = item_contributions(SessionItem.objects.filter(pk=row.pk))
        SessionItem.objects.filter(pk=row.pk).update(cost=2)
        after = item_contributions(SessionItem.objects.filter(pk=row.pk))
        state = session_contribution(session)
        apply_contribution_changes(before, after, before_sessions=[state], after_sessions=[state], repair=False)
    assert not revisions.revisions_match(observed)
    assert not revisions.revisions_match(project)


def test_other_root_same_day_invalidates_preparation():
    session = make_session('other-root')
    observed = revisions.observe_revisions(days=[revisions.DailyKey(None, session.provider, STAMP.date())])
    with transaction.atomic():
        row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1, timestamp=STAMP)
        state = session_contribution(session)
        apply_contribution_changes([], item_contributions(SessionItem.objects.filter(pk=row.pk)),
                                   before_sessions=[state], after_sessions=[state], repair=False)
    assert not revisions.revisions_match(observed)


def test_derived_publication_preserves_tree_source_observation():
    session = make_session('derived')
    SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1, timestamp=STAMP)
    source = observe_source(session, roots=[revisions.SourceKey(session.id, session.storage_incarnation)],
                            days=[revisions.DailyKey(None, session.provider, STAMP.date())])
    cost = revisions.observe_revisions(costs=[revisions.SourceKey(session.id, session.storage_incarnation)])
    project = revisions.observe_revisions(projects=['p'])
    with transaction.atomic():
        state = session_contribution(session)
        apply_contribution_changes([], [], before_sessions=[state], after_sessions=[state], repair=True)
    publish_session_values(session)
    assert revisions.revisions_match(source)
    assert not revisions.revisions_match(cost)
    assert not revisions.revisions_match(project)


def test_project_output_does_not_advance_project_input_revision():
    from twicc.projects import update_project_total_cost
    make_session('project-output', total_cost=Decimal(3))
    observed = revisions.observe_revisions(projects=['p'])
    with transaction.atomic():
        update_project_total_cost('p')
    publish_project_values('p')
    assert Project.objects.get(pk='p').total_cost == 3
    assert revisions.revisions_match(observed)


def test_auth_or_title_only_commit_does_not_invalidate_contributions():
    session = make_session('title')
    observed = observe_source(session, roots=[revisions.SourceKey(session.id, session.storage_incarnation)],
                              projects=['p'], days=[revisions.DailyKey(None, session.provider, STAMP.date())])
    with transaction.atomic():
        session.title = 'New title'
        session.permission_mode = 'default'
        from twicc.core.content_storage.lifecycle import save_lifecycle
        save_lifecycle(session, update_fields=['title', 'permission_mode'])
    assert revisions.revisions_match(observed)


def test_lifecycle_reparent_invalidates_both_native_roots():
    from twicc.core.content_storage.lifecycle import save_lifecycle
    old = make_session('old-root')
    new = make_session('new-root')
    child = make_session('child', type='subagent', parent_session=old)
    observations = [revisions.observe_revisions(roots=[revisions.SourceKey(s.id, s.storage_incarnation)])
                    for s in (old, new)]
    with transaction.atomic():
        child.parent_session = new
        save_lifecycle(child, update_fields=['parent_session'])
    assert all(not revisions.revisions_match(observed) for observed in observations)


def test_deletion_invalidates_retained_source_and_native_root():
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
    root = make_session('delete-root')
    child = make_session('delete-child', type='subagent', parent_session=root)
    source = observe_source(child)
    graph = revisions.observe_revisions(roots=[revisions.SourceKey(root.id, root.storage_incarnation)])
    _apply_delete_sessions_payload(DeleteSessionsPayload(child.provider, [child.id]))
    assert not revisions.revisions_match(source)
    assert not revisions.revisions_match(graph)


def test_old_new_date_and_scope_keys_invalidate():
    from datetime import timedelta
    session = make_session('scope')
    row = SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP, cost=1)
    new_day = STAMP + timedelta(days=1)
    Project.objects.create(id='other-project')
    observations = [revisions.observe_revisions(days=[revisions.DailyKey(project, session.provider, day)])
                    for project in ('p', 'other-project', None) for day in (STAMP.date(), new_day.date())]
    with transaction.atomic():
        before = session_contribution(session)
        items = item_contributions(SessionItem.objects.filter(pk=row.pk))
        session.project_id = 'other-project'
        session.save(update_fields=['project'])
        SessionItem.objects.filter(pk=row.pk).update(timestamp=new_day)
        apply_contribution_changes(items, item_contributions(SessionItem.objects.filter(pk=row.pk)),
                                   before_sessions=[before], after_sessions=[session_contribution(session)], repair=True)
    # Actual source keys plus old-scope/new-date keys from the retained legacy history repair.
    for index in (0, 1, 3, 4, 5):
        assert not revisions.revisions_match(observations[index])


def test_metadata_chunk_invalidates_without_journal():
    from twicc.providers.compute_base import BaseSessionCompute
    session = make_session('chunk')
    row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
    observed = observe_source(session)
    outcome = BaseSessionCompute.apply_session_items_chunk(
        session.id, session.last_offset, ['cost'], [{'id': row.pk, 'cost': Decimal(2)}], [],
        defer_aggregate_repair=True,
    )
    assert outcome == 'ok'
    assert not revisions.revisions_match(observed)


def test_creation_invalidates_existing_root_membership():
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile
    root = make_session('create-root')
    observed = revisions.observe_revisions(roots=[revisions.SourceKey(root.id, root.storage_incarnation)])
    watcher = ClaudeCodeSessionsWatcher()
    parsed = ParsedSessionFile('p', 'created-child', 'subagent', 'child.jsonl', parent_session_id=root.id)
    watcher.create_session_sync(parsed, root.project, root)
    assert not revisions.revisions_match(observed)


@pytest.mark.parametrize('phase', ['begin', 'insert', 'finish'])
def test_history_replacement_without_journal_invalidates_source(phase):
    from twicc.providers.codex.rollout_migration import (
        ReplaceCodexHistoryJob, _insert_replace_codex_history_chunk,
        _finish_replace_codex_history,
    )
    session = make_session('replacement')
    job = ReplaceCodexHistoryJob(session.provider, session.id, [(1, '{}')], 100, 1, 2.0, None)
    observed = observe_source(session)
    if phase == 'begin':
        begin_history_replacement(job)
    elif phase == 'insert':
        _insert_replace_codex_history_chunk(session.id, job.items)
    else:
        _finish_replace_codex_history(job)
    assert not revisions.revisions_match(observed)


def test_visibility_without_journal_invalidates_historical_day():
    from datetime import timedelta
    from twicc.core.services.session_visibility import _apply_visibility_flag
    session = make_session('visibility')
    old_day = STAMP - timedelta(days=50)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=old_day, kind='user_message')
    observed = revisions.observe_revisions(days=[revisions.DailyKey('p', session.provider, old_day.date())])
    _apply_visibility_flag(session.id, True)
    assert not revisions.revisions_match(observed)


def test_adopted_creation_does_not_change_graph():
    from twicc.providers.db_writer import CreateSessionPayload, _apply_create_session_payload
    session = make_session('adopted')
    graph = revisions.observe_revisions(roots=[revisions.SourceKey(session.id, session.storage_incarnation)])
    _apply_create_session_payload(CreateSessionPayload(session.provider, 'p', None, False, session,
                                                       [(1, '{}')], 100, 1, 1.0))
    assert revisions.revisions_match(graph)


def test_source_mutation_rollback_preserves_observations():
    session = make_session('rolled-back-source')
    observed = observe_source(session, projects=['p'])
    with transaction.atomic():
        row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
        state = session_contribution(session)
        apply_contribution_changes([], item_contributions(SessionItem.objects.filter(pk=row.pk)),
                                   before_sessions=[state], after_sessions=[state], repair=False)
        transaction.set_rollback(True)
    assert revisions.revisions_match(observed)
    assert not session.items.exists()


def test_metadata_representation_only_preserves_source_observation():
    from twicc.providers.compute_base import BaseSessionCompute
    session = make_session('representation')
    row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
    observed = observe_source(session, projects=['p'])
    assert BaseSessionCompute.apply_session_items_chunk(
        session.id, session.last_offset, ['display_level'], [{'id': row.pk, 'display_level': 1}], [],
        defer_aggregate_repair=True,
    ) == 'ok'
    assert revisions.revisions_match(observed)


def test_hidden_root_cost_publication_invalidates_project_input():
    session = make_session('hidden-root', hidden=True)
    SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
    project = revisions.observe_revisions(projects=['p'])
    tree = observe_source(session)
    with transaction.atomic():
        state = session_contribution(session)
        apply_contribution_changes([], [], before_sessions=[state], after_sessions=[state], repair=True)
    publish_session_values(session)
    assert not revisions.revisions_match(project)
    assert revisions.revisions_match(tree)


def test_other_root_derived_repair_invalidates_project_only():
    root = make_session('prepared-root')
    other = make_session('published-root')
    SessionItem.objects.create(session=other, line_num=1, content='{}', cost=1)
    project = revisions.observe_revisions(projects=['p'])
    tree = observe_source(root)
    publish_session_values(other)
    assert not revisions.revisions_match(project)
    assert revisions.revisions_match(tree)


def test_live_duplicate_metadata_write_invalidates_removed_day(tmp_path):
    from datetime import timedelta
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.live_sync import LiveSyncLimits
    session = make_session('live-duplicate')
    previous = STAMP - timedelta(days=50)
    SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=previous, cost=1)
    observed = revisions.observe_revisions(days=[revisions.DailyKey('p', session.provider, previous.date())])
    path = tmp_path / 'live.jsonl'
    path.write_text('{"type":"user","timestamp":"2026-10-13T12:00:00Z",'
                    '"message":{"role":"user","content":"hello"}}\n')
    ClaudeCodeSessionCompute().sync_session_slice(session.id, path, limits=LiveSyncLimits())
    assert SessionItem.objects.get(session=session, line_num=1).timestamp != previous
    assert not revisions.revisions_match(observed)


@pytest.mark.parametrize('deferred', [False, True])
def test_null_timestamp_cost_does_not_invalidate_creation_day(deferred):
    from twicc.providers.compute_base import BaseSessionCompute
    session = make_session('null-day')
    row = SessionItem.objects.create(session=session, line_num=1, content='{}', cost=1)
    day = revisions.observe_revisions(days=[revisions.DailyKey(None, session.provider, STAMP.date())])
    source = observe_source(session)
    BaseSessionCompute.apply_session_items_chunk(
        session.id, session.last_offset, ['cost'], [{'id': row.pk, 'cost': Decimal(2)}], [],
        defer_aggregate_repair=deferred,
    )
    assert not revisions.revisions_match(source)
    assert revisions.revisions_match(day)


def test_pending_scope_movement_records_preserved_historical_keys():
    from datetime import timedelta
    from twicc.providers.compute_repair import preserve_pending_repair
    from tests.test_compute_repair_exact import record
    session = make_session('pending-scope')
    record(session.id, session.storage_incarnation)
    historical = STAMP - timedelta(days=50)
    observed = revisions.observe_revisions(days=[revisions.DailyKey('p', session.provider, historical.date())])
    state = session_contribution(session)
    with transaction.atomic():
        session.hidden = True
        session.save(update_fields=['hidden'])
        preserve_pending_repair(session.id, session.storage_incarnation, before_sessions=[state],
                                after_sessions=[session_contribution(session)], historical_timestamps=[historical])
    assert not revisions.revisions_match(observed)
