from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.content_storage import policy
from twicc.core.models import Project, Session


def fields(now):
    return {'archived': True, 'hidden': False, 'created_at': now - timedelta(days=30),
            'last_started_at': None, 'last_updated_at': None, 'last_stopped_at': None}


@pytest.mark.parametrize('archived,hidden,days,content,scratch', [
    (False, False, 31, True, False),
    (False, False, 29, False, False),
    (True, False, 20, True, False),
    (True, False, 30, True, True),
    (True, False, 14, False, False),
    (True, False, 15, True, False),
    (False, True, 15, True, False),
    (False, False, 30, True, False),
])
def test_content_and_scratch_have_distinct_boundaries(archived, hidden, days, content, scratch):
    now = timezone.now()
    metadata = fields(now) | {'archived': archived, 'hidden': hidden, 'created_at': now - timedelta(days=days)}
    assert policy.cold_from_fields(metadata, now) is content
    assert policy.scratch_cold_from_fields(metadata, now) is scratch


def test_latest_lifecycle_date_and_incomplete_metadata_stay_hot():
    now = timezone.now()
    for name in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
        metadata = fields(now) | {name: now - timedelta(days=14)}
        assert not policy.cold_from_fields(metadata, now)
        assert not policy.scratch_cold_from_fields(metadata, now)
    for missing in ('archived', 'hidden', 'created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
        metadata = fields(now)
        del metadata[missing]
        assert not policy.cold_from_fields(metadata, now)
        assert not policy.scratch_cold_from_fields(metadata, now)
    assert not policy.cold_from_fields(dict.fromkeys(fields(now)) | {'archived': True}, now)


@pytest.mark.django_db
def test_loaded_native_ancestry_never_queries_and_spawned_sessions_are_independent(django_assert_num_queries):
    project = Project.objects.create(id='p', directory='/p')
    root = Session.objects.create(id='root', project=project, provider='codex', file_path='r', **fields(timezone.now()))
    child = Session.objects.create(id='child', project=project, provider='codex', file_path='c',
                                   type='subagent', parent_session=root)
    grandchild = Session.objects.create(id='grandchild', project=project, provider='codex', file_path='g',
                                        type='subagent', parent_session=child)
    spawned = Session.objects.create(id='spawned', project=project, provider='codex', file_path='s', spawned_by=root)
    with django_assert_num_queries(0):
        assert not grandchild.is_cold_for_content
        assert grandchild.is_cold_for_scratch
        assert not spawned.is_cold_for_content
        assert not spawned.is_cold_for_scratch
    unloaded = Session.objects.get(id=grandchild.id)
    with django_assert_num_queries(0):
        assert not unloaded.is_cold_for_content
        assert not unloaded.is_cold_for_scratch
    deferred = Session.objects.only('id').get(id=root.id)
    with django_assert_num_queries(0):
        assert not deferred.is_cold_for_content
        assert not deferred.is_cold_for_scratch
    child.parent_session = grandchild
    with django_assert_num_queries(0):
        assert not grandchild.is_cold_for_content
        assert not grandchild.is_cold_for_scratch


@pytest.mark.parametrize('archived,hidden,days', [(True, False, 15), (False, True, 15), (False, False, 30)])
@pytest.mark.parametrize('offset,cold', [(-1, True), (0, True), (1, False)])
def test_session_decision_precise_boundaries(archived, hidden, days, offset, cold):
    from twicc.core.content_storage.session_policy import content_decision_from_fields

    now = timezone.now()
    metadata = fields(now) | {
        'archived': archived, 'hidden': hidden,
        'created_at': now - timedelta(days=days) + timedelta(microseconds=offset),
    }
    decision = content_decision_from_fields('session', metadata, now, frozenset())
    assert decision.session_id == 'session'
    assert decision.resolved
    assert not decision.active
    assert decision.is_cold is cold


@pytest.mark.parametrize('latest', policy.COLD_DATE_FIELDS)
def test_session_decision_uses_latest_date(latest):
    from twicc.core.content_storage.session_policy import content_decision_from_fields

    now = timezone.now()
    metadata = fields(now) | {name: now - timedelta(days=31) for name in policy.COLD_DATE_FIELDS}
    metadata[latest] = now - timedelta(days=14)
    assert not content_decision_from_fields('session', metadata, now, frozenset()).is_cold


def test_unknown_session_metadata_stays_hot():
    from twicc.core.content_storage.session_policy import content_decision_from_fields

    now = timezone.now()
    missing = content_decision_from_fields('missing', None, now, frozenset())
    assert not missing.resolved and not missing.is_cold
    for name in policy.COLD_FIELDS:
        metadata = fields(now)
        del metadata[name]
        assert not content_decision_from_fields('partial', metadata, now, frozenset()).is_cold
    metadata = fields(now) | dict.fromkeys(policy.COLD_DATE_FIELDS)
    assert not content_decision_from_fields('unknown', metadata, now, frozenset()).is_cold


@pytest.mark.django_db
def test_session_content_policy_ignores_ancestry_and_parent_readiness(django_assert_num_queries):
    from twicc.core.content_storage.lifecycle import active_session_ids
    from twicc.core.content_storage.session_policy import resolve_content_decisions
    from twicc.core.content_storage.state import resolve_scratch_root_decisions

    now = timezone.now()
    project = Project.objects.create(id='own', directory='/own')

    def create(name, date, **kwargs):
        return Session.objects.create(id=name, project=project, provider='codex', file_path=name,
                                      created_at=date, **kwargs)

    root = create('hot-root', now)
    old_child = create('old-child', now - timedelta(days=31), type='subagent', parent_session=root)
    cold_root = create('cold-root', now - timedelta(days=31), archived=True)
    active_child = create('active-child', now - timedelta(days=31), type='subagent', parent_session=cold_root)
    child = create('child', now, type='subagent', parent_session=cold_root)
    grandchild = create('grandchild', now - timedelta(days=31), type='subagent', parent_session=child)
    leaf = create('leaf', now, type='subagent', parent_session=grandchild)
    ids = [root.id, old_child.id, cold_root.id, active_child.id, child.id, grandchild.id, leaf.id, 'missing']
    active = frozenset({active_child.id})
    scratch_before = resolve_scratch_root_decisions(ids, now, active)
    with django_assert_num_queries(1):
        decisions = resolve_content_decisions(ids, now, active)
    assert decisions[old_child.id].is_cold
    assert decisions[cold_root.id].is_cold
    assert not decisions[active_child.id].is_cold
    assert decisions[active_child.id].active
    assert decisions[grandchild.id].is_cold
    assert not decisions[leaf.id].is_cold
    assert not decisions['missing'].resolved
    token = active_session_ids.set(active)
    try:
        with django_assert_num_queries(0):
            assert old_child.is_cold_for_content == decisions[old_child.id].is_cold
            assert not active_child.is_cold_for_content
            assert grandchild.is_cold_for_content
            assert not leaf.is_cold_for_content
    finally:
        active_session_ids.reset(token)
    assert scratch_before == resolve_scratch_root_decisions(ids, now, active)
    unloaded = Session.objects.get(pk=old_child.id)
    with django_assert_num_queries(0):
        assert unloaded.is_cold_for_content


@pytest.mark.django_db
def test_grouped_session_metadata_queries_are_bounded(django_assert_num_queries):
    from twicc.core.content_storage.session_policy import resolve_content_decisions

    project = Project.objects.create(id='bounded', directory='/bounded')
    now = timezone.now()
    sessions = [Session(id=f's{i:03}', project=project, provider='codex', file_path=str(i),
                        type='subagent', created_at=now - timedelta(days=31)) for i in range(257)]
    Session.objects.bulk_create(sessions)
    with django_assert_num_queries(3) as queries:
        decisions = resolve_content_decisions((session.id for session in sessions), now, frozenset({'unrequested'}))
    assert len(decisions) == 257
    assert all(decision.is_cold for decision in decisions.values())
    assert all('parent_session_id' not in query['sql'] and 'compute_version' not in query['sql'] for query in queries)
