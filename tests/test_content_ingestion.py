"""Initial and live ingestion commit exact source and transformed content together."""

from uuid import UUID, uuid4

import orjson
import pytest
from django.db import DatabaseError, connection

from tests import content_storage_helpers
from tests.live_sync_helpers import drain_live_sync
from twicc.core.content_storage.codec import decode_content
from twicc.core.content_storage.readers import effective_current, load_current
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionItemContent
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.db_writer import (
    CreateSessionPayload, UpdateSessionPayload, _apply_create_session_payload, _apply_update_session_payload,
)

content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db(transaction=True)


def _create(items, *, session_id='ingested', parent=None):
    session = Session(id=session_id, project_id='ingestion-project', provider=Provider.CLAUDE_CODE,
                      file_path=f'/initial/{session_id}', parent_session=parent, type='subagent' if parent else 'session')
    payload = CreateSessionPayload(Provider.CLAUDE_CODE, session.project_id, None, False,
                                   session, items, 100 if items else 0, len(items), 1.0)
    _apply_create_session_payload(payload)
    session.refresh_from_db()
    return session


def _append(session, items):
    _apply_update_session_payload(UpdateSessionPayload(Provider.CLAUDE_CODE, session, items,
                                                       200, 2, 2.0, False, False))
    session.refresh_from_db()


def _assert_item(item, source, current):
    assert item.content == ''
    assert decode_content(item.original_content) == source
    assert decode_content(item.current_content) == current
    assert item.session_id in item.original_content.session_ids
    assert item.session_id in item.current_content.session_ids
    load_current([item])
    assert effective_current(item) == current


def test_initial_create_and_append_externalize_exact_source():
    source = '{"type":"user","unicode":"é"}'
    session = _create([(1, source)])
    UUID(session.storage_incarnation)
    assert session.content_storage_version == 1
    item = SessionItem.objects.get(session=session, line_num=1)
    _assert_item(item, source, source)
    assert item.original_content_id == item.current_content_id
    epoch = session.content_state_epoch
    _append(session, [(2, source)])
    _assert_item(SessionItem.objects.get(session=session, line_num=2), source, source)
    assert SessionItemContent.objects.count() == 1
    assert session.content_state_epoch > epoch


def test_duplicate_append_preserves_enriched_reference(content_row_factory):
    session = _create([(1, '{}')])
    current = content_row_factory('{"enriched":true}', compressed=False, session_ids=[session.id])
    SessionItem.objects.filter(session=session).update(current_content=current)
    _append(session, [(1, '{}'), (2, '{"new":true}')])
    item = SessionItem.objects.get(session=session, line_num=1)
    _assert_item(item, '{}', '{"enriched":true}')
    assert item.current_content_id == current.id
    assert SessionItemContent.objects.get(pk=current.id).session_ids == [session.id]


def test_append_legacy_mixed_session_initializes_identity_without_claiming_completion():
    project = Project.objects.create(id='ingestion-project')
    session = Session.objects.create(id='legacy', project=project, provider=Provider.CLAUDE_CODE)
    SessionItem.objects.create(session=session, line_num=1, content='{"legacy":true}')
    _append(session, [(2, '{}')])
    UUID(session.storage_incarnation)
    assert session.content_storage_version is None
    assert SessionItem.objects.get(session=session, line_num=1).content == '{"legacy":true}'
    _assert_item(SessionItem.objects.get(session=session, line_num=2), '{}', '{}')


def test_initial_commit_failure_rolls_back_references_membership_and_tracking(monkeypatch):
    session = _create([(1, '{}')])
    before = list(SessionItemContent.objects.values())
    tracking = (session.last_offset, session.last_line, session.mtime, session.content_state_epoch)
    def fail():
        raise DatabaseError('injected commit')
    with monkeypatch.context() as patch:
        patch.setattr(connection, 'commit', fail)
        with pytest.raises(DatabaseError, match='injected commit'):
            _append(session, [(2, '{"new":true}')])
    session.refresh_from_db()
    assert (session.last_offset, session.last_line, session.mtime, session.content_state_epoch) == tracking
    assert list(SessionItemContent.objects.values()) == before
    assert session.items.count() == 1
    _append(session, [(2, '{"new":true}')])
    _assert_item(SessionItem.objects.get(session=session, line_num=2), '{"new":true}', '{"new":true}')


def test_live_preserves_pre_transform_source_and_same_batch_current(tmp_path, monkeypatch):
    session = _create([])
    records = [{'type': 'user', 'message': {'role': 'user', 'content': 'before'}},
               {'type': 'user', 'message': {'role': 'user', 'content': 'second'}}]
    texts = [orjson.dumps(record).decode() for record in records]
    path = tmp_path / 'live.jsonl'
    path.write_text('\n'.join(texts) + '\n')
    compute = ClaudeCodeSessionCompute()
    original = compute.transform_inline
    def transform(parsed, **kwargs):
        if kwargs['line_num'] == 2:
            assert kwargs['in_memory_items'][0][2]['message']['content'] == 'transformed'
        result = original(parsed, **kwargs)
        if kwargs['line_num'] == 1:
            parsed['message']['content'] = 'transformed'
            return orjson.dumps(parsed).decode()
        return result
    monkeypatch.setattr(compute, 'transform_inline', transform)
    drain_live_sync(compute, session, path)
    item = SessionItem.objects.get(session=session, line_num=1)
    transformed = orjson.dumps({**records[0], 'message': {'role': 'user', 'content': 'transformed'}}).decode()
    _assert_item(item, texts[0], transformed)
    assert item.original_content_id != item.current_content_id
    _assert_item(SessionItem.objects.get(session=session, line_num=2), texts[1], texts[1])


def test_new_subagent_invalidates_parent_even_before_first_item():
    parent = _create([])
    epoch = parent.content_state_epoch or 0
    child = _create([], session_id='child', parent=parent)
    UUID(child.storage_incarnation)
    parent.refresh_from_db()
    assert parent.content_state_epoch > epoch


def test_hot_ingestion_promotes_shared_compressed_both_refs(content_row_factory):
    source = '{"source":"shared"}'
    compressed = content_row_factory(source, compressed=True, session_ids=[])
    session = _create([(1, source)])
    item = session.items.get()
    _assert_item(item, source, source)
    assert item.current_content_id == compressed.id == item.original_content_id
    compressed.refresh_from_db()
    assert compressed.codec == 'plain'
    assert compressed.dictionary_id is None


def test_hot_reuse_promotion_rolls_back_on_commit_failure(content_row_factory, monkeypatch):
    source = '{"source":"compressed"}'
    compressed = content_row_factory(source, compressed=True, session_ids=[])
    before = bytes(compressed.payload)
    session = _create([])
    def fail():
        raise DatabaseError('injected commit')
    with monkeypatch.context() as patch:
        patch.setattr(connection, 'commit', fail)
        with pytest.raises(DatabaseError, match='injected commit'):
            _append(session, [(1, source)])
    compressed.refresh_from_db()
    assert compressed.codec == 'zstd'
    assert bytes(compressed.payload) == before
    assert compressed.session_ids == []
    assert not session.items.exists()
    _append(session, [(1, source)])
    compressed.refresh_from_db()
    assert compressed.codec == 'plain'
    _assert_item(session.items.get(), source, source)


def test_live_latest_lifecycle_promotes_previous_cold_owner(content_row_factory, tmp_path):
    from datetime import timedelta
    from django.utils import timezone

    session = _create([])
    session.archived = True
    session.created_at = timezone.now() - timedelta(days=60)
    session.last_updated_at = session.created_at
    session.save(update_fields=['archived', 'created_at', 'last_updated_at'])
    text = orjson.dumps({'type': 'user', 'timestamp': timezone.now().isoformat(),
                         'message': {'role': 'user', 'content': 'new activity'}}).decode()
    compressed = content_row_factory(text, compressed=True, session_ids=[])
    path = tmp_path / 'cold-live.jsonl'
    path.write_text(text + '\n')
    drain_live_sync(ClaudeCodeSessionCompute(), session, path)
    assert not session.is_cold_for_content
    compressed.refresh_from_db()
    assert compressed.codec == 'plain'
    _assert_item(session.items.get(), text, text)


def test_cold_initial_reuse_keeps_compressed_representation(content_row_factory):
    from datetime import timedelta
    from django.utils import timezone

    text = '{"old":true}'
    compressed = content_row_factory(text, compressed=True, session_ids=[])
    session = Session(id='cold', project_id='ingestion-project', provider=Provider.CLAUDE_CODE,
                      file_path='/initial/cold', archived=True, created_at=timezone.now() - timedelta(days=60))
    _apply_create_session_payload(CreateSessionPayload(Provider.CLAUDE_CODE, session.project_id, None, False,
                                                       session, [(1, text)], 10, 1, 1.0))
    compressed.refresh_from_db()
    assert compressed.codec == 'zstd'
    _assert_item(session.items.get(), text, text)


def test_initial_creation_race_preserves_existing_identity_and_mixed_storage(content_row_factory):
    project = Project.objects.create(id='ingestion-project')
    existing_identity = str(uuid4())
    session = Session.objects.create(id='ingested', project=project, provider=Provider.CLAUDE_CODE,
                                     file_path='/initial/ingested', storage_incarnation=existing_identity,
                                     title='User title', title_origin='user', last_offset=300, last_line=2, mtime=4.0)
    original = content_row_factory('{"old":true}', compressed=False, session_ids=[session.id])
    enriched = content_row_factory('{"enriched":true}', compressed=False, session_ids=[session.id])
    SessionItem.objects.create(session=session, line_num=1, content='', original_content=original,
                               current_content=enriched)
    SessionItem.objects.create(session=session, line_num=2, content='{"legacy":true}')
    raced = _create([(1, '{"old":true}'), (2, '{"legacy":true}'), (3, '{"new":true}')])
    assert raced.storage_incarnation == existing_identity
    assert raced.content_storage_version is None
    assert raced.title == 'User title'
    assert (raced.last_offset, raced.last_line, raced.mtime) == (300, 3, 4.0)
    _assert_item(session.items.get(line_num=1), '{"old":true}', '{"enriched":true}')
    assert session.items.get(line_num=2).content == '{"legacy":true}'
    _assert_item(session.items.get(line_num=3), '{"new":true}', '{"new":true}')


def test_live_promotes_distinct_source_and_current_without_resetting_attempts(content_row_factory, tmp_path):
    session = _create([])
    source = orjson.dumps({'type': 'user', 'message': {'role': 'user',
                           'content': '<twicc:context>private context</twicc:context>\nHello'}}).decode()
    compute = ClaudeCodeSessionCompute()
    current = compute.transform_inline(orjson.loads(source), session_id=session.id, line_num=1, in_memory_items=[])
    assert current is not None and current != source
    original_row = content_row_factory(source, compressed=True)
    current_row = content_row_factory(current, compressed=True)
    for row in (original_row, current_row):
        row.compression_attempt_version = 'previous-policy'
        row.compression_no_gain = True
        row.save(update_fields=['compression_attempt_version', 'compression_no_gain'])
    path = tmp_path / 'distinct.jsonl'
    path.write_text(source + '\n')
    drain_live_sync(compute, session, path)
    _assert_item(session.items.get(), source, current)
    for row in (original_row, current_row):
        row.refresh_from_db()
        assert row.codec == 'plain'
        assert row.dictionary_id is None
        assert row.compression_attempt_version == 'previous-policy'
        assert row.compression_no_gain is True


def test_ignored_append_does_not_promote_or_bind_unused_candidate(content_row_factory):
    session = _create([(1, '{}')])
    ignored = content_row_factory('{"ignored":true}', compressed=True)
    epoch = Session.objects.values_list('content_state_epoch', flat=True).get(pk=session.id)
    _append(session, [(1, '{"ignored":true}')])
    ignored.refresh_from_db()
    assert ignored.codec == 'zstd'
    assert ignored.session_ids == []
    _assert_item(session.items.get(), '{}', '{}')
    assert session.content_state_epoch == epoch


def test_watcher_creation_initializes_identity_and_invalidates_native_root():
    from twicc.core.models import SessionType
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile

    watcher = ClaudeCodeSessionsWatcher()
    project = Project.objects.create(id='ingestion-project')
    parsed = ParsedSessionFile(project.id, 'watch-root', SessionType.SESSION, '/watch-root')
    root = watcher.create_session_sync(parsed, project)
    UUID(root.storage_incarnation)
    root.refresh_from_db()
    epoch = root.content_state_epoch
    root.plaintext_verified_epoch = epoch
    root.save(update_fields=['plaintext_verified_epoch'])
    child = watcher.create_session_sync(
        ParsedSessionFile(project.id, 'watch-child', SessionType.SUBAGENT, '/watch-child'), project, root,
    )
    UUID(child.storage_incarnation)
    assert root.storage_incarnation != child.storage_incarnation
    assert child.content_storage_version == 1
    root.refresh_from_db()
    assert root.content_state_epoch > epoch
    assert root.content_state_epoch == root.plaintext_verified_epoch


@pytest.mark.parametrize('operation', ['insert_ingested_items', 'promote_ingested_items'])
def test_ingestion_item_helpers_require_caller_transaction(operation):
    from django.db import transaction
    from twicc.core.content_storage import ingestion

    session = Session(id='unsaved', provider=Provider.CLAUDE_CODE)
    with pytest.raises(transaction.TransactionManagementError, match='transaction'):
        getattr(ingestion, operation)(session, [])


@pytest.mark.parametrize('stale_certificate', [False, True])
def test_hot_append_carries_certificate_without_plaintext_request(monkeypatch, content_row_factory, stale_certificate):
    from django.db import transaction
    from twicc.core.content_storage import lifecycle, state

    owner = _create([(1, '{}')])
    with transaction.atomic():
        assert state.verify_plaintext_root(state.snapshot_root_storage(owner.id))
    if stale_certificate:
        with transaction.atomic():
            state.invalidate_roots([owner.id])
    owner.refresh_from_db()
    certificate = owner.plaintext_verified_epoch
    epoch = owner.content_state_epoch
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    source = '{"shared":"compressed"}'
    row = content_row_factory(source, True, session_ids=[])
    _append(owner, [(2, source)])
    assert requests == []
    assert owner.content_state_epoch > epoch
    if stale_certificate:
        assert owner.plaintext_verified_epoch == certificate
        assert owner.plaintext_verified_epoch != owner.content_state_epoch
    else:
        assert owner.plaintext_verified_epoch == owner.content_state_epoch
    row.refresh_from_db()
    assert row.codec == 'plain'
    with transaction.atomic():
        assert state.require_plaintext_search(owner.id).epoch == owner.content_state_epoch


def test_watcher_carries_certificate_after_deferred_promotion(tmp_path, monkeypatch, content_row_factory):
    from django.db import transaction
    from twicc.core.content_storage import lifecycle, state

    owner = _create([])
    with transaction.atomic():
        assert state.verify_plaintext_root(state.snapshot_root_storage(owner.id))
    source = orjson.dumps({'type': 'user', 'timestamp': '2026-10-15T10:00:00Z',
                          'message': {'role': 'user', 'content': 'shared'}}).decode()
    row = content_row_factory(source, True, session_ids=[])
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    path = tmp_path / 'watcher.jsonl'
    path.write_text(source + '\n')
    drain_live_sync(ClaudeCodeSessionCompute(), owner, path)
    owner.refresh_from_db()
    row.refresh_from_db()
    assert row.codec == 'plain'
    assert owner.plaintext_verified_epoch == owner.content_state_epoch
    assert requests == []


def test_new_child_preserves_root_only_certificate_without_demand(monkeypatch):
    from django.db import transaction
    from twicc.core.content_storage import lifecycle, state

    owner = _create([])
    with transaction.atomic():
        assert state.verify_plaintext_root(state.snapshot_root_storage(owner.id))
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    _create([], session_id='new-child', parent=owner)
    owner.refresh_from_db()
    assert owner.plaintext_verified_epoch == owner.content_state_epoch
    assert requests == []


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.parametrize('complete', [False, True])
def test_initial_ingestion_uses_complete_own_lifecycle(provider, complete, monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage import references

    _public_dictionary(provider)
    session = Session(id='cold-ingestion', project_id='ingestion-project', provider=provider,
                      file_path='/initial/cold-ingestion', created_at=timezone.now() - timedelta(days=60) if complete else None)
    source = 'initial repeated text ' * 1000
    blocks = []
    encode = references.encode_cold
    def observe(*args, **kwargs):
        blocks.append(connection.in_atomic_block)
        return encode(*args, **kwargs)
    monkeypatch.setattr(references, 'encode_cold', observe)
    payload = CreateSessionPayload(provider, session.project_id, None, False, session, [(1, source)], 100, 1, 1.0)
    _apply_create_session_payload(payload)
    item = SessionItem.objects.get(session=session)
    assert item.current_content.codec == ('zstd' if complete else 'plain')
    assert decode_content(item.current_content) == source
    assert not any(blocks)


def test_ingestion_rechecks_hot_policy_after_cold_preparation(monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage import references

    _public_dictionary('codex')
    session = Session(id='changed-ingestion', project_id='ingestion-project', provider='codex',
                      file_path='/initial/changed-ingestion', created_at=timezone.now() - timedelta(days=60))
    encode = references.encode_cold
    def activate(*args, **kwargs):
        session.created_at = timezone.now()
        return encode(*args, **kwargs)
    monkeypatch.setattr(references, 'encode_cold', activate)
    payload = CreateSessionPayload('codex', session.project_id, None, False, session,
                                   [(1, 'changed repeat ' * 1000)], 100, 1, 1.0)
    _apply_create_session_payload(payload)
    assert SessionItem.objects.get(session=session).current_content.codec == 'plain'


def test_ingestion_dispatch_prepares_before_writer_lease(monkeypatch):
    import asyncio
    from datetime import timedelta
    from django.utils import timezone
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage import references
    from twicc.providers import db_writer

    _public_dictionary('codex')
    session = Session(id='dispatch-ingestion', project_id='ingestion-project', provider='codex',
                      file_path='/initial/dispatch-ingestion', created_at=timezone.now() - timedelta(days=60))
    payload = CreateSessionPayload('codex', session.project_id, None, False, session,
                                   [(1, 'dispatch repeat ' * 1000)], 100, 1, 1.0)
    observed = []
    encode = references.encode_cold
    def observe(*args, **kwargs):
        lease = db_writer._db_write_lock_lease.get()
        observed.append((connection.in_atomic_block, lease))
        return encode(*args, **kwargs)
    monkeypatch.setattr(references, 'encode_cold', observe)
    monkeypatch.setattr(db_writer, '_db_writer_loop', db_writer._db_writer_loop_body)
    async def run():
        completed = asyncio.Event()
        async def publish(actual, *, prepared=None):
            assert actual is payload
            assert prepared and any(record.encoded.codec == 'zstd' for record in prepared.values())
            assert db_writer._db_write_lock_lease.get() is not None
            completed.set()
        monkeypatch.setattr(db_writer, '_process_thread_message', publish)
        db_writer.start_db_writer()
        try:
            db_writer.get_thread_queue().put_nowait(payload)
            await asyncio.wait_for(completed.wait(), 5)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(run())
    assert observed == [(False, None)]
