"""Native session metadata is loaded once and reused by the root and content decisions."""
from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.content_storage.session_policy import resolve_content_decisions
from twicc.core.content_storage.state import (
    NATIVE_METADATA_FIELDS,
    load_native_metadata,
    native_metadata_row,
    native_root_ids,
    resolve_root_decisions,
)
from twicc.core.models import Project, Session

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_now")]


def make_session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='metadata', defaults={'directory': '/metadata'})
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name,
                                  content_state_epoch=4, plaintext_verified_epoch=4, **kwargs)


@pytest.fixture
def tree():
    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    child = make_session('child', type='subagent', parent_session=root, created_at=timezone.now())
    return root, child


def test_load_native_metadata_reads_requested_rows_and_ancestors(tree, django_assert_num_queries):
    root, child = tree
    with django_assert_num_queries(2):
        metadata = load_native_metadata([child.id])
    assert set(metadata) == {child.id, root.id}
    for row in metadata.values():
        assert set(row) == set(NATIVE_METADATA_FIELDS)
    assert metadata[child.id]['parent_session_id'] == root.id
    assert metadata[root.id]['content_state_epoch'] == 4


def test_load_native_metadata_skips_known_rows(tree, django_assert_num_queries):
    root, child = tree
    known = {child.id: native_metadata_row(child)}
    snapshot = dict(known)
    with django_assert_num_queries(1):
        metadata = load_native_metadata([child.id], known=known)
    assert set(metadata) == {child.id, root.id}
    assert metadata[child.id] is known[child.id]
    assert known == snapshot

    both = {child.id: native_metadata_row(child), root.id: native_metadata_row(root)}
    snapshot = dict(both)
    with django_assert_num_queries(0):
        metadata = load_native_metadata([child.id], known=both)
    assert metadata == both
    assert metadata is not both
    assert both == snapshot


def test_native_metadata_row_matches_loaded_values(tree):
    root, child = tree
    assert load_native_metadata([child.id]) == {
        child.id: native_metadata_row(Session.objects.get(pk=child.id)),
        root.id: native_metadata_row(Session.objects.get(pk=root.id)),
    }


def test_load_native_metadata_terminates_on_cycles_and_marks_missing_rows():
    make_session('loop', type='subagent', parent_session_id='loop')
    first = make_session('first', type='subagent')
    make_session('second', type='subagent', parent_session=first)
    Session.objects.filter(pk='first').update(parent_session_id='second')
    ids = ['loop', 'first', 'unknown']
    metadata = load_native_metadata(ids)
    assert set(metadata) == {'loop', 'first', 'second', 'unknown'}
    assert metadata['unknown'] is None
    assert native_root_ids(ids) == dict.fromkeys(ids)
    assert native_root_ids(ids, metadata=metadata) == dict.fromkeys(ids)
    decisions = resolve_root_decisions(ids, timezone.now(), frozenset())
    assert all(decisions[identity].root_id is None and not decisions[identity].resolved for identity in ids)


def test_native_root_ids_resolve_through_subagents(tree):
    root, child = tree
    assert native_root_ids([child.id, root.id]) == {child.id: root.id, root.id: root.id}


def test_root_decisions_from_metadata_match_and_skip_reads(tree, django_assert_num_queries):
    _root, child = tree
    expected = resolve_root_decisions([child.id], timezone.now(), frozenset())
    metadata = load_native_metadata([child.id])
    with django_assert_num_queries(0):
        decisions = resolve_root_decisions([child.id], timezone.now(), frozenset(), metadata=metadata)
    assert decisions == expected
    assert decisions[child.id].is_cold


def test_content_decisions_from_metadata_match_and_skip_reads(tree, django_assert_num_queries):
    root, child = tree
    ids = [root.id, child.id]
    expected = resolve_content_decisions(ids, timezone.now(), frozenset({child.id}))
    metadata = load_native_metadata(ids)
    with django_assert_num_queries(0):
        decisions = resolve_content_decisions(ids, timezone.now(), frozenset({child.id}), metadata=metadata)
    assert decisions == expected
    assert decisions[root.id].is_cold and decisions[child.id].active


def test_content_decisions_load_only_ids_absent_from_metadata(tree, django_assert_num_queries):
    root, child = tree
    metadata = {child.id: native_metadata_row(child), 'missing': None}
    expected = resolve_content_decisions([root.id, child.id, 'missing'], timezone.now(), frozenset())
    with django_assert_num_queries(1):
        decisions = resolve_content_decisions([root.id, child.id, 'missing'], timezone.now(), frozenset(),
                                              metadata=metadata)
    assert decisions == expected
    assert not decisions['missing'].resolved
