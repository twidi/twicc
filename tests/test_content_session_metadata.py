"""Native session metadata is loaded once and reused by the root and content decisions."""
from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.content_storage.session_policy import resolve_content_decisions
from twicc.core.content_storage.state import (
    NATIVE_METADATA_FIELDS,
    capture_plaintext_certificate,
    carry_plaintext_certificate,
    ensure_known_storage_incarnation,
    invalidate_roots,
    load_native_metadata,
    native_metadata_row,
    native_root_ids,
    resolve_root_decisions,
    session_plaintext_metadata,
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


def test_load_native_metadata_pages_each_level(monkeypatch):
    import re

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from twicc.core.content_storage import state

    monkeypatch.setattr(state, 'TREE_PAGE_SIZE', 2)
    root = make_session('page-root')
    parents = [make_session(f'page-parent-{index}', type='subagent', parent_session=root) for index in range(3)]
    children = [make_session(f'page-child-{index}', type='subagent', parent_session=parent)
                for index, parent in enumerate(parents)]
    requested = [child.id for child in children] + ['page-missing']
    with CaptureQueriesContext(connection) as queries:
        metadata = load_native_metadata(requested)
    # One query per page of at most 2 sorted ids, level by level: children (and the missing id), parents, root.
    assert [re.findall(r"'(page-[^']+)'", query['sql']) for query in queries] == [
        ['page-child-0', 'page-child-1'], ['page-child-2', 'page-missing'],
        ['page-parent-0', 'page-parent-1'], ['page-parent-2'],
        ['page-root'],
    ]
    assert set(metadata) == {*requested, *(parent.id for parent in parents), root.id}
    assert metadata['page-missing'] is None
    for session in (root, *parents, *children):
        assert metadata[session.id] == native_metadata_row(Session.objects.get(pk=session.id))


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


@pytest.fixture
def certified():
    """A hot native root holding a plaintext certificate, with a hot certified child."""
    root = make_session('certified', storage_incarnation='root-incarnation', content_storage_version=1,
                        created_at=timezone.now() - timedelta(days=1))
    child = make_session('certified-child', type='subagent', parent_session=root,
                         storage_incarnation='child-incarnation', content_storage_version=1,
                         created_at=timezone.now())
    return root, child


def test_ensure_known_incarnation_keeps_one_write(certified, django_assert_num_queries):
    root, _child = certified
    with django_assert_num_queries(1) as captured:
        assert ensure_known_storage_incarnation(root.id, 'root-incarnation') == 'root-incarnation'
    assert captured.captured_queries[0]['sql'].startswith('UPDATE')

    bare = make_session('bare', created_at=timezone.now())
    with django_assert_num_queries(1) as captured:
        incarnation = ensure_known_storage_incarnation(bare.id, None)
    assert captured.captured_queries[0]['sql'].startswith('UPDATE')
    assert incarnation
    assert Session.objects.values_list('storage_incarnation', flat=True).get(pk=bare.id) == incarnation


def test_capture_from_metadata_and_storage_fields_reads_nothing(certified, django_assert_num_queries):
    root, _child = certified
    with django_assert_num_queries(2):
        expected = capture_plaintext_certificate(root.id)
    assert expected is not None
    metadata = load_native_metadata([root.id])
    with django_assert_num_queries(0):
        assert capture_plaintext_certificate(root.id, metadata=metadata, storage_fields=metadata[root.id]) == expected
    with django_assert_num_queries(1):
        assert capture_plaintext_certificate(root.id, metadata=metadata) == expected


@pytest.mark.parametrize('from_metadata', [False, True])
def test_carry_from_metadata_keeps_epoch_read(certified, from_metadata, django_assert_num_queries):
    root, _child = certified
    snapshot = capture_plaintext_certificate(root.id)
    assert snapshot is not None
    invalidate_roots([root.id])
    kwargs = {'metadata': load_native_metadata([root.id])} if from_metadata else {}
    with django_assert_num_queries(2 if from_metadata else 3):
        assert carry_plaintext_certificate(snapshot, **kwargs)
    fields = Session.objects.values('content_state_epoch', 'plaintext_verified_epoch').get(pk=root.id)
    assert fields['content_state_epoch'] == fields['plaintext_verified_epoch'] == snapshot.epoch + 1


def test_plaintext_metadata_from_fields_keeps_the_probe(certified, django_assert_num_queries):
    _root, child = certified
    with django_assert_num_queries(2):
        expected = session_plaintext_metadata(child.id)
    assert expected
    metadata = load_native_metadata([child.id])
    with django_assert_num_queries(1):
        assert session_plaintext_metadata(child.id, fields=metadata[child.id]) == expected


@pytest.mark.parametrize('from_metadata', [False, True])
def test_invalidate_roots_targets_from_metadata(certified, from_metadata, effects, django_assert_num_queries,
                                                django_capture_on_commit_callbacks):
    root, child = certified
    kwargs = {'metadata': load_native_metadata([child.id])} if from_metadata else {}
    with (django_capture_on_commit_callbacks(execute=True) as callbacks,
          django_assert_num_queries(1 if from_metadata else 2)):
        invalidate_roots([root.id], promotion_targets=[child.id], **kwargs)
    assert len(callbacks) == 1
    assert effects.wakeups == [child.id]
    assert Session.objects.values_list('content_state_epoch', flat=True).get(pk=root.id) == 5


def make_certified_tree(prefix, *, root_kwargs=None, child_kwargs=None):
    """A certified, versioned native root with a versioned child, both with an incarnation."""
    root = make_session(f'{prefix}-root', storage_incarnation=f'{prefix}-root-incarnation',
                        content_storage_version=1, **(root_kwargs or {}))
    child = make_session(f'{prefix}-child', type='subagent', parent_session=root,
                         storage_incarnation=f'{prefix}-child-incarnation', content_storage_version=1,
                         **(child_kwargs or {}))
    return root, child


def storage_epochs(session_id):
    fields = Session.objects.values('content_state_epoch', 'plaintext_verified_epoch').get(pk=session_id)
    return fields['content_state_epoch'], fields['plaintext_verified_epoch']


@pytest.mark.parametrize(('target', 'changed', 'budget'), [
    pytest.param('root', False, 7, id='root-unchanged'),
    pytest.param('child', False, 8, id='child-unchanged'),
    pytest.param('root', True, 10, id='root-changed'),
    pytest.param('child', True, 13, id='child-changed'),
])
def test_lifecycle_change_budget(target, changed, budget, django_assert_num_queries):
    from django.db import transaction

    from twicc.core.content_storage.lifecycle import save_lifecycle

    recent = {'created_at': timezone.now() - timedelta(days=1), 'last_updated_at': timezone.now() - timedelta(hours=1)}
    root, child = make_certified_tree('budget', root_kwargs=recent, child_kwargs=recent)
    session = root if target == 'root' else child
    if changed:
        session.last_updated_at += timedelta(seconds=5)
    # Each budget holds 2 SAVEPOINT and 2 RELEASE: this atomic block and ``lifecycle_change``'s own.
    with django_assert_num_queries(budget), transaction.atomic():
        save_lifecycle(session, update_fields=['last_updated_at'])
    if changed:
        assert storage_epochs(root.id) == (5, 5)
    else:
        assert storage_epochs(root.id) == (4, 4)


def test_reparent_carries_old_root_certificate_without_after_ancestry():
    from twicc.core.content_storage.lifecycle import update_lifecycle

    recent = {'created_at': timezone.now() - timedelta(days=1)}
    r1, child = make_certified_tree('r1', root_kwargs=recent, child_kwargs=recent)
    r2, _other_child = make_certified_tree('r2', root_kwargs=recent, child_kwargs=recent)
    update_lifecycle(Session.objects.filter(pk=child.id), parent_session=r2)
    # Certificates are captured for the old roots only: the old root is carried, the new one is not.
    assert storage_epochs(r1.id) == (5, 5)
    assert storage_epochs(r2.id) == (5, 4)
    assert native_root_ids([child.id]) == {child.id: r2.id}


def test_carry_uses_after_write_policy():
    from twicc.core.content_storage.lifecycle import update_lifecycle

    old = {'created_at': timezone.now() - timedelta(days=20), 'last_updated_at': timezone.now() - timedelta(days=20)}
    root, _child = make_certified_tree('archive', root_kwargs=old, child_kwargs=old)
    assert storage_epochs(root.id) == (4, 4)
    update_lifecycle(Session.objects.filter(pk=root.id), archived=True)
    # Archived for 20 days, the root is cold after the write: the carry refuses.
    assert storage_epochs(root.id) == (5, 4)

    recent = {'created_at': timezone.now() - timedelta(days=1)}
    demoted, _demoted_child = make_certified_tree('demoted', root_kwargs=recent, child_kwargs=recent)
    other, _other_child = make_certified_tree('other', root_kwargs=recent, child_kwargs=recent)
    update_lifecycle(Session.objects.filter(pk=demoted.id), type='subagent', parent_session=other)
    # A former root is no longer a native root after the write: the carry refuses.
    assert storage_epochs(demoted.id) == (5, 4)
    assert storage_epochs(other.id) == (5, 4)


def test_bulk_lifecycle_change_keeps_wakeups_and_epochs(effects, django_capture_on_commit_callbacks):
    from django.db import transaction

    from twicc.core.content_storage.lifecycle import update_lifecycle

    # Cold while archived (15 days), hot once unarchived (30 days).
    old = {'archived': True, 'created_at': timezone.now() - timedelta(days=20),
           'last_updated_at': timezone.now() - timedelta(days=20)}
    r1, child = make_certified_tree('bulk1', root_kwargs=old, child_kwargs=old)
    r2 = make_session('bulk2-root', storage_incarnation='bulk2-root-incarnation', content_storage_version=1, **old)
    ids = [r1.id, child.id, r2.id]
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        assert update_lifecycle(Session.objects.filter(pk__in=ids), archived=False) == 3
    assert sorted(effects.wakeups) == ['bulk1-child', 'bulk1-root', 'bulk2-root']
    assert effects.cold == [(['bulk1-child', 'bulk1-root', 'bulk2-root'], False,
                             ['bulk1-child', 'bulk1-root', 'bulk2-root'])]
    assert storage_epochs('bulk1-root') == (5, 4)
    assert storage_epochs('bulk2-root') == (5, 4)
    assert storage_epochs('bulk1-child') == (4, 4)


def test_lifecycle_change_with_a_deleted_row(effects, django_capture_on_commit_callbacks):
    from twicc.core.content_storage.lifecycle import lifecycle_change

    recent = {'created_at': timezone.now() - timedelta(days=1)}
    root, child = make_certified_tree('deleted', root_kwargs=recent, child_kwargs=recent)
    with django_capture_on_commit_callbacks(execute=True), lifecycle_change([child.id]):
        Session.objects.filter(pk=child.id).delete()
    assert not Session.objects.filter(pk=child.id).exists()
    # The deleted row has no decision, so no promotion; the mappings of both identities are invalidated.
    assert effects.wakeups == []
    assert effects.cold == [(['deleted-child', 'deleted-root'], True, ['deleted-child'])]
    # The old root is fenced and its certificate carried to the new epoch.
    assert storage_epochs(root.id) == (5, 5)


@pytest.mark.parametrize(('version', 'wakeups'), [
    pytest.param(None, ['orphan'], id='legacy'),
    pytest.param(1, [], id='versioned'),
])
def test_lifecycle_change_with_an_unresolved_parent(version, wakeups, effects, django_capture_on_commit_callbacks):
    from twicc.core.content_storage.lifecycle import update_lifecycle

    recent = {'created_at': timezone.now() - timedelta(days=1)}
    # A subagent whose parent is a subagent without a parent: the chain reaches no native root.
    parent = make_session('orphan-parent', type='subagent', **recent)
    orphan = make_session('orphan', type='subagent', parent_session=parent, storage_incarnation='orphan-incarnation',
                          content_storage_version=version, **recent)
    assert native_root_ids([orphan.id]) == {orphan.id: None}
    with django_capture_on_commit_callbacks(execute=True):
        update_lifecycle(Session.objects.filter(pk=orphan.id), last_updated_at=timezone.now())
    # The unresolved session stands for its own root: it is fenced, with no certificate to carry.
    assert effects.cold == [(['orphan'], False, ['orphan'])]
    # Its own policy is hot: a legacy session needs promotion, a versioned one already holds plaintext.
    assert effects.wakeups == wakeups
    assert storage_epochs(orphan.id) == (5, 4)
    assert storage_epochs(parent.id) == (4, 4)


def stored_incarnation(session_id):
    return Session.objects.values_list('storage_incarnation', flat=True).get(pk=session_id)


@pytest.mark.parametrize(('target', 'budget'), [
    pytest.param('root', 5, id='root'),
    pytest.param('child', 8, id='child'),
])
def test_initialize_without_metadata_skips_root_is_session(target, budget, django_assert_num_queries):
    from django.db import transaction

    from twicc.core.content_storage.ingestion import initialize_ingestion_session

    root, child = make_certified_tree('plain')
    session = Session.objects.get(pk=(root if target == 'root' else child).id)
    expected = stored_incarnation(session.id)
    # Each budget holds this atomic block's SAVEPOINT and RELEASE. A root is not ensured twice.
    with django_assert_num_queries(budget), transaction.atomic():
        initialize_ingestion_session(session)
    assert session.storage_incarnation == stored_incarnation(session.id) == expected
    assert stored_incarnation(root.id) == 'plain-root-incarnation'


@pytest.mark.parametrize(('target', 'root_incarnation', 'session_incarnation', 'budget'), [
    pytest.param('root', True, True, 3, id='root'),
    pytest.param('child', True, True, 3, id='child-root-has-incarnation'),
    pytest.param('child', False, True, 4, id='child-root-without-incarnation'),
    pytest.param('root', False, False, 3, id='session-without-incarnation'),
])
def test_initialize_with_metadata(target, root_incarnation, session_incarnation, budget,
                                  django_assert_num_queries):
    from django.db import transaction

    from twicc.core.content_storage.ingestion import initialize_ingestion_session

    root, child = make_certified_tree('known')
    if not root_incarnation:
        Session.objects.filter(pk=root.id).update(storage_incarnation=None)
    target_id = (root if target == 'root' else child).id
    if not session_incarnation:
        Session.objects.filter(pk=target_id).update(storage_incarnation=None)
    session = Session.objects.get(pk=target_id)
    metadata = load_native_metadata([session.id], known={session.id: native_metadata_row(session)})
    # Each budget holds this atomic block's SAVEPOINT and RELEASE; the session's UPDATE is the first write.
    with django_assert_num_queries(budget) as captured, transaction.atomic():
        initialize_ingestion_session(session, metadata=metadata)
    assert captured.captured_queries[1]['sql'].startswith('UPDATE')
    assert session.storage_incarnation
    assert session.storage_incarnation == stored_incarnation(session.id) == metadata[session.id]['storage_incarnation']
    assert stored_incarnation(root.id)
    assert stored_incarnation(root.id) == metadata[root.id]['storage_incarnation']
    if session_incarnation:
        assert session.storage_incarnation == f'known-{target}-incarnation'


def bound_setup(prefix, target):
    """A certified hot tree with one stored item of ``target`` and its unbound binding."""
    from django.db import transaction

    from twicc.core.content_storage.references import intern_contents
    from twicc.core.content_storage.types import ContentBinding
    from twicc.core.models import SessionItem

    recent = {'created_at': timezone.now() - timedelta(days=1)}
    root, child = make_certified_tree(prefix, root_kwargs=recent, child_kwargs=recent)
    owner = child if target == 'child' else root
    text = f'{prefix} content'
    with transaction.atomic():
        content_id = intern_contents([text], owner.provider)[text]
    item = SessionItem.objects.create(session=owner, line_num=1, content='', current_content_id=content_id)
    return root, owner, [ContentBinding(item.id, owner.id, content_id, None)]


@pytest.mark.parametrize(('target', 'saved'), [
    pytest.param('child', 4, id='child-certified-root'),
    pytest.param('root', 1, id='root'),
])
def test_bind_with_metadata_skips_ancestry_reads(target, saved):
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    from twicc.core.content_storage.references import bind_persisted_items

    results = []
    for prefix, from_metadata in (('fresh', False), ('known', True)):
        root, owner, bindings = bound_setup(prefix, target)
        kwargs = {'metadata': load_native_metadata([owner.id])} if from_metadata else {}
        with CaptureQueriesContext(connection) as queries, transaction.atomic():
            bind_persisted_items(bindings, **kwargs)
        results.append((len(queries), storage_epochs(root.id), storage_epochs(owner.id)))
    (fresh_count, *fresh_state), (known_count, *known_state) = results
    # Child: its ancestry (2), the capture's and the carry's root resolution (1 each). Root: its resolution.
    assert fresh_count - known_count == saved
    assert fresh_state == known_state
    # A child binding carries its root's certificate; a root binding leaves its own epoch uncertified.
    epoch, verified = fresh_state[0]
    assert (epoch == verified) is (target == 'child')


def test_insert_with_metadata_skips_policy_read():
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    from twicc.core.content_storage.ingestion import INSERT_BATCH_SIZE, insert_ingested_items
    from twicc.core.models import SessionItem

    root, _child = make_certified_tree('insert', root_kwargs={'created_at': timezone.now() - timedelta(days=1)})
    lines = range(1, INSERT_BATCH_SIZE + 11)
    metadata = load_native_metadata([root.id])
    results = []
    for kwargs in ({}, {'metadata': metadata}):
        with transaction.atomic():
            session = Session.objects.get(pk=root.id)
            items = [(SessionItem(session=session, line_num=line, content=f'current {line}'), f'source {line}')
                     for line in lines]
            with CaptureQueriesContext(connection) as queries:
                inserted = insert_ingested_items(session, items, **kwargs)
            stored = list(SessionItem.objects.filter(session_id=root.id).order_by('line_num').values_list(
                'line_num', 'current_content__sha256', 'current_content__codec',
                'original_content__sha256', 'original_content__codec',
            ))
            results.append((len(queries), inserted, stored, storage_epochs(root.id)))
            transaction.set_rollback(True)
    (fresh_count, *fresh_state), (known_count, *known_state) = results
    assert fresh_state[0] == set(lines)
    assert len(fresh_state[1]) == len(lines)
    # Per batch: the cold-policy read and the root resolution of ``bind_persisted_items``.
    assert fresh_count - known_count == 4
    assert fresh_state == known_state


def test_multibatch_child_slice_keeps_root_certified(tmp_path, monkeypatch, effects,
                                                    django_capture_on_commit_callbacks):
    import orjson

    from tests.test_live_sync_session_reads import (
        FIXTURE, ROOT, SliceReplay, certified_root_multibatch_child,
    )

    replay = SliceReplay(tmp_path, monkeypatch, django_capture_on_commit_callbacks, effects)
    # The scenario certifies the root, syncs a 121-line child slice (3 insert batches), then checks the root.
    certified_root_multibatch_child(replay)
    epoch, verified = storage_epochs(ROOT)
    assert epoch is not None and verified == epoch
    stored = orjson.loads(FIXTURE.read_bytes())['certified_root_multibatch_child']
    assert orjson.loads(orjson.dumps(replay.records)) == stored
