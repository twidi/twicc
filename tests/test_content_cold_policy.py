from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.content_storage import policy
from twicc.core.models import Project, Session


def fields(now):
    return {'archived': True, 'hidden': False, 'created_at': now - timedelta(days=30),
            'last_started_at': None, 'last_updated_at': None, 'last_stopped_at': None}


def test_exact_thirty_day_boundary_and_latest_date():
    now = timezone.now()
    assert policy.cold_from_fields(fields(now), now)
    for name in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
        assert not policy.cold_from_fields(fields(now) | {name: now - timedelta(days=29)}, now)
    assert not policy.cold_from_fields(fields(now) | {'archived': False}, now)
    assert policy.cold_from_fields(fields(now) | {'archived': False, 'hidden': True}, now)
    assert not policy.cold_from_fields(dict.fromkeys(fields(now)) | {'archived': True}, now)
    assert not policy.cold_from_fields({'archived': True}, now)


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
        assert grandchild.is_cold
        assert not spawned.is_cold
    unloaded = Session.objects.get(id=grandchild.id)
    with django_assert_num_queries(0):
        assert not unloaded.is_cold
    child.parent_session = grandchild
    with django_assert_num_queries(0):
        assert not grandchild.is_cold
