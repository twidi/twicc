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
    for name in policy.COLD_DATE_FIELDS:
        metadata = fields(now) | {name: now - timedelta(days=14)}
        assert not policy.cold_from_fields(metadata, now)
        assert not policy.scratch_cold_from_fields(metadata, now)
    for missing in policy.COLD_FIELDS:
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
        assert grandchild.is_cold_for_content
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
