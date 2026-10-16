"""Progressive compute extraction uses real commits and exact logical strings."""

from uuid import uuid4

import pytest
from django.db import transaction

from tests.content_storage_helpers import read_current
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(**kwargs):
    project, _ = Project.objects.get_or_create(id='extraction-project')
    return Session.objects.create(id=kwargs.pop('id', 'extract'), project=project, provider='codex',
                                  storage_incarnation=str(uuid4()), file_path=str(uuid4()), last_line=10, **kwargs)


def message(session, **kwargs):
    return {'session_id': session.id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'session_fields': {'compute_version': 123}, **kwargs}


def test_small_result_extracts_unchanged_empty_and_malformed_strings():
    session = make_session()
    for line, text in enumerate(('', 'not json', '{"unicode":"é"}'), 1):
        SessionItem.objects.create(session=session, line_num=line, content=text)
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    for item, text in zip(SessionItem.objects.filter(session=session).order_by('line_num'),
                          ('', 'not json', '{"unicode":"é"}'), strict=True):
        assert item.current_content_id is not None
        assert item.original_content_id is None
        assert item.content == ''
        assert read_current(item) == text
        assert item.current_content.session_ids == [session.id]
    session.refresh_from_db()
    assert session.content_storage_version == 1


@pytest.mark.parametrize('change', ['epoch', 'incarnation'])
def test_equal_offsets_do_not_accept_stale_identity(change):
    session = make_session()
    old = message(session)
    if change == 'epoch':
        Session.objects.filter(pk=session.id).update(history_epoch=1)
    else:
        session.delete()
        session = make_session()
    assert BaseSessionCompute.apply_session_complete(old).outcome == 'superseded'
    session.refresh_from_db()
    assert session.compute_version != 123


def test_storage_slice_commits_and_resumes_without_duplicate_items():
    from twicc.core.content_storage.state import extract_storage_slice

    session = make_session()
    for line in range(1, 4):
        SessionItem.objects.create(session=session, line_num=line, content=f'legacy {line}')
    first = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, -1, 1, 1024)
    assert first.rows == 1
    assert not first.done
    assert not transaction.get_connection().in_atomic_block
    assert SessionItem.objects.get(session=session, line_num=1).current_content_id is not None
    cursor = first
    while not cursor.done:
        cursor = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, cursor.line_num, 1, 1024)
    assert SessionItem.objects.filter(session=session).count() == 3
    assert [read_current(item) for item in SessionItem.objects.filter(session=session).order_by('line_num')] == [
        'legacy 1', 'legacy 2', 'legacy 3',
    ]
    session.refresh_from_db()
    assert session.content_storage_version is None


def test_changed_override_checks_legacy_input_digest():
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='changed elsewhere')
    msg = message(session, content_overrides=[{'id': item.id, 'content': 'computed',
                                             'observed_current_id': None, 'observed_legacy_digest': '0' * 64}])
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'superseded'
    assert read_current(SessionItem.objects.get(pk=item.id)) == 'changed elsewhere'


def test_override_preserves_known_original_and_removes_displaced_membership():
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage.ingestion import insert_ingested_items
    from twicc.core.content_storage.references import intern_contents
    from twicc.core.models import SessionItemContent

    _public_dictionary('codex')
    session = make_session()
    item = SessionItem(session=session, line_num=1, content='source')
    with transaction.atomic():
        insert_ingested_items(session, [(item, 'source')])
        item = SessionItem.objects.get(session=session, line_num=1)
        old_current = intern_contents(['old enrichment'], 'codex')['old enrichment']
        SessionItem.objects.filter(pk=item.id).update(current_content_id=old_current)
        SessionItemContent.objects.filter(pk=old_current).update(session_ids=[session.id])
    original = item.original_content_id
    msg = message(session, content_overrides=[{'id': item.id, 'content': 'new enrichment',
                                             'observed_current_id': old_current}])
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    item.refresh_from_db()
    assert item.original_content_id == original
    assert read_current(item) == 'new enrichment'
    assert SessionItemContent.objects.get(pk=old_current).session_ids == []
    assert SessionItemContent.objects.get(pk=original).session_ids == [session.id]
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'


def test_real_partial_commit_failure_retries_with_fresh_reference_counts(monkeypatch):
    from django.db import DatabaseError, connection
    from twicc.core.content_storage import extraction, references
    from twicc.core.models import SessionItemContent

    session = make_session(last_offset=50)
    for line in range(1, 5):
        SessionItem.objects.create(session=session, line_num=line, content=f'old {line}')
    msg = message(session)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    original = connection.commit
    moved_before_failure = []

    def commit():
        moved = SessionItem.objects.filter(session=session, current_content_id__isnull=False).count()
        if moved == 2:
            moved_before_failure.append(moved)
            raise DatabaseError('injected second storage commit')
        return original()

    with monkeypatch.context() as patch:
        patch.setattr(connection, 'commit', commit)
        with pytest.raises(DatabaseError, match='second storage commit'):
            BaseSessionCompute.apply_session_complete(msg)
    assert moved_before_failure == [2]
    assert SessionItem.objects.filter(session=session, current_content_id__isnull=False).count() == 1
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.compute_version != 123
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    assert [read_current(item) for item in SessionItem.objects.filter(session=session).order_by('line_num')] == [
        f'old {line}' for line in range(1, 5)
    ]
    assert SessionItemContent.objects.count() == 4
    assert all(row.session_ids == [session.id] for row in SessionItemContent.objects.all())
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'content_reference_counts_%'")
        assert cursor.fetchall() == []


def test_storage_slice_byte_budget_allows_one_oversized_row():
    from twicc.core.content_storage.state import extract_storage_slice

    session = make_session()
    SessionItem.objects.create(session=session, line_num=1, content='é' * 50)
    SessionItem.objects.create(session=session, line_num=2, content='small')
    first = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, -1, 10, 10)
    assert (first.line_num, first.rows, first.bytes, first.done) == (1, 1, 100, False)
    assert SessionItem.objects.get(session=session, line_num=2).current_content_id is None


def test_initialize_legacy_identity_is_stable_before_dispatch():
    from twicc.core.content_storage.extraction import initialize_compute_session

    session = make_session()
    Session.objects.filter(pk=session.id).update(storage_incarnation=None)
    initialize_compute_session(session.id)
    session.refresh_from_db()
    first = session.storage_incarnation
    assert first
    initialize_compute_session(session.id)
    session.refresh_from_db()
    assert session.storage_incarnation == first
    assert session.content_storage_version is None


def test_worker_sends_only_changed_text_and_observed_input_identity():
    import queue

    import orjson

    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute

    session = make_session()
    text = orjson.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'unchanged'}}).decode()
    SessionItem.objects.create(session=session, line_num=1, content=text)
    results = queue.Queue()
    ClaudeCodeSessionCompute().compute_session_metadata(session.id, results, 1)
    result = next(orjson.loads(raw) for raw in list(results.queue) if orjson.loads(raw)['type'] == 'session_complete')
    assert result['observed_incarnation'] == session.storage_incarnation
    assert result['observed_epoch'] == session.history_epoch
    assert result['observed_upper_line'] == session.last_line
    assert result['content_overrides'] == []
    assert 'unchanged_contents' not in result
    assert text not in orjson.dumps(result).decode()


def test_final_apply_failure_preserves_committed_extraction_and_retries(monkeypatch):
    from twicc.providers import compute_base

    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='exact')
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('injected final facts')
        patch.setattr(compute_base, 'replace_history_facts', fail)
        with pytest.raises(RuntimeError, match='final facts'):
            BaseSessionCompute.apply_session_complete(message(session))
    item.refresh_from_db()
    assert read_current(item) == 'exact'
    assert item.content == ''
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.compute_version != 123
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'


def test_reconcile_resumes_between_distinct_oversized_fk_payloads(content_row_factory, monkeypatch):
    from twicc.core.content_storage import extraction

    owner = make_session()
    current = content_row_factory('current' * 1500000, True, session_ids=[owner.id])
    original = content_row_factory('original' * 1500000, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=current, original_content=original)
    msg = message(owner)
    decoded = []
    decode = extraction.transcode_content

    def record(row, *args, **kwargs):
        decoded.append(row.id)
        return decode(row, *args, **kwargs)

    monkeypatch.setattr(extraction, 'transcode_content', record)
    cursor = extraction.reconcile_storage_slice(msg, -1)
    assert decoded == [current.id]
    assert cursor.line_num == -1 and not cursor.done
    assert cursor.bytes > extraction.BYTE_BUDGET
    current.refresh_from_db()
    original.refresh_from_db()
    assert current.codec == 'plain' and original.codec == 'zstd'
    cursor = extraction.reconcile_storage_slice(msg, cursor.line_num)
    assert decoded == [current.id, original.id]
    assert cursor.line_num == 1 and cursor.done
    original.refresh_from_db()
    assert original.codec == 'plain'


def test_reconcile_shared_fk_slots_advance_without_duplicate_payload_decode(content_row_factory, monkeypatch):
    from twicc.core.content_storage import extraction

    owner = make_session()
    shared = content_row_factory('shared' * 1500000, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=shared, original_content=shared)
    from unittest.mock import patch
    with patch.object(extraction, 'transcode_content', wraps=extraction.transcode_content) as decode:
        cursor = extraction.reconcile_storage_slice(message(owner), -1)
    assert cursor.done and cursor.line_num == 1
    assert decode.call_count == 1


def test_hot_plain_reconcile_skips_owner_and_payload_loading(content_row_factory, monkeypatch):
    from twicc.core.content_storage import extraction
    from twicc.core.models import SessionItemContent

    owner = make_session()
    for line in range(1, 11):
        row = content_row_factory(f'plain {line}', False, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    loaded = []
    hydrate = SessionItemContent.from_db

    def record(cls, db, fields, values, **kwargs):
        if 'payload' in fields or 'session_ids' in fields:
            loaded.append(fields)
        return hydrate(db, fields, values, **kwargs)

    monkeypatch.setattr(SessionItemContent, 'from_db', classmethod(record))
    from unittest.mock import patch
    with patch.object(extraction, '_owners_cold', wraps=extraction._owners_cold) as eligibility:
        cursor = extraction.reconcile_storage_slice(message(owner), -1)
    assert cursor.done and eligibility.call_count == 0 and loaded == []


def test_cold_reconcile_reuses_owner_metadata_only_within_bounded_slice(content_row_factory, monkeypatch):
    from datetime import timedelta
    from unittest.mock import patch
    from django.utils import timezone
    from twicc.core.content_storage import extraction

    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    owner.last_line = 50
    owner.save(update_fields=['last_line'])
    for line in range(1, 51):
        row = content_row_factory(f'cold {line} ' + 'schema ' * 50, False, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    with patch.object(extraction, 'cold_placement', wraps=extraction.cold_placement) as eligibility:
        extraction.prepare_compute_result(message(owner))
    assert eligibility.call_count <= 8


def test_reconcile_epoch_change_before_publication_rejects_all_candidates(content_row_factory, monkeypatch):
    from twicc.core.content_storage import extraction
    from twicc.core.content_storage.state import invalidate_roots

    owner = make_session()
    stored = content_row_factory('compressed pending ' * 1000, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=stored)
    decode = extraction.transcode_content

    def invalidate(row, *args, **kwargs):
        result = decode(row, *args, **kwargs)
        with transaction.atomic():
            invalidate_roots([owner.id])
        return result

    monkeypatch.setattr(extraction, 'transcode_content', invalidate)
    msg = message(owner)
    with pytest.raises(extraction.SupersededStorageResult):
        extraction.prepare_compute_result(msg)
    stored.refresh_from_db()
    assert stored.codec == 'zstd'
    assert '_storage_reconcile_position' not in msg


def test_reconcile_admits_owner_bytes_before_json_or_payload_loading(content_row_factory, monkeypatch):
    from twicc.core.content_storage import extraction
    from twicc.core.models import SessionItemContent

    owner = make_session()
    rows = []
    for line in range(1, 4):
        row = content_row_factory(f'owner budget {line} ' * 200, True,
                                  session_ids=[owner.id, 'large owner metadata ' * 1000])
        rows.append(row)
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    loaded = []
    hydrate = SessionItemContent.from_db

    def record(cls, db, fields, values, **kwargs):
        if 'payload' in fields or 'session_ids' in fields:
            loaded.append(values[fields.index('id')])
        return hydrate(db, fields, values, **kwargs)

    monkeypatch.setattr(SessionItemContent, 'from_db', classmethod(record))
    cursor = extraction.reconcile_storage_slice(message(owner), -1, byte_budget=1024)
    assert cursor.line_num == 1 and not cursor.done
    assert set(loaded) == {rows[0].id}


@pytest.mark.parametrize('field', ['content', 'current_content', 'original_content',
                                  'current_content_id', 'original_content_id'])
def test_metadata_cannot_mutate_content_assignments(field):
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='preserved')
    with pytest.raises(ValueError, match='reserved'):
        BaseSessionCompute.apply_item_updates([field], [{'id': item.id, field: None}])
    item.refresh_from_db()
    assert item.content == 'preserved' and item.current_content_id is None


@pytest.mark.parametrize('change', ['append', 'replacement', 'recreation', 'sibling', 'reparent'])
def test_resumable_publication_rejects_changed_operation_observation(change):
    from twicc.core.content_storage import extraction
    from twicc.core.content_storage.state import invalidate_roots

    root = make_session()
    target = make_session(id='target', type='subagent', parent_session=root, last_offset=50)
    SessionItem.objects.create(session=target, line_num=1, content='source')
    operation = extraction.begin_compute_application(message(target))
    try:
        batch = extraction.prepare_compute_batch(operation)
        with transaction.atomic():
            if change == 'append':
                Session.objects.filter(pk=target.id).update(last_offset=51)
            elif change == 'replacement':
                Session.objects.filter(pk=target.id).update(last_offset=0, history_epoch=1)
            elif change == 'recreation':
                target.delete()
                target = make_session(id='target', type='subagent', parent_session=root, last_offset=50)
            elif change == 'sibling':
                invalidate_roots([root.id])
            else:
                Session.objects.filter(pk=target.id).update(parent_session_id=None)
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
        target.refresh_from_db()
        assert target.compute_version is None
    finally:
        extraction.close_compute_application(operation)


def test_final_message_revalidates_operation_inside_final_transaction():
    from twicc.core.content_storage import extraction

    session = make_session(last_offset=50)
    SessionItem.objects.create(session=session, line_num=1, content='source')
    operation = extraction.begin_compute_application(message(session))
    try:
        while operation.phase != 'final':
            batch = extraction.prepare_compute_batch(operation)
            extraction.publish_compute_batch(operation, batch, frozenset())
        final = extraction.final_compute_message(operation, frozenset())
        Session.objects.filter(pk=session.id).update(last_offset=49)
        with pytest.raises(extraction.SupersededStorageResult):
            BaseSessionCompute._apply_session_complete_final(final)
        session.refresh_from_db()
        assert session.compute_version is None
    finally:
        extraction.close_compute_application(operation)


def _advance_storage_operation(operation, phase):
    from twicc.core.content_storage import extraction

    while operation.phase != phase:
        batch = extraction.prepare_compute_batch(operation)
        extraction.publish_compute_batch(operation, batch, frozenset())


def test_append_after_real_temp_initialization_retains_second_reference(monkeypatch):
    from twicc.core.content_storage import extraction, references
    from twicc.core.content_storage.ingestion import insert_ingested_items
    from twicc.core.models import SessionItemContent

    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    session = make_session()
    with transaction.atomic():
        insert_ingested_items(session, [(SessionItem(session=session, line_num=1, content='A'), 'A')])
    item = SessionItem.objects.get(session=session, line_num=1)
    # Use legacy provenance so the new reference is the only remaining A owner.
    SessionItem.objects.filter(pk=item.id).update(original_content_id=None)
    operation = extraction.begin_compute_application(message(session, content_overrides=[
        {'id': item.id, 'content': 'B', 'observed_current_id': item.current_content_id}]))
    try:
        assert operation.references.uses_counts
        _advance_storage_operation(operation, 'overrides')
        batch = extraction.prepare_compute_batch(operation)
        with transaction.atomic():
            insert_ingested_items(session, [(SessionItem(session=session, line_num=2, content='A'), 'A')])
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
        assert SessionItemContent.objects.get(pk=item.current_content_id).session_ids == [session.id]
        assert SessionItem.objects.get(pk=item.id).current_content_id == item.current_content_id
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('cyclic', [False, True])
def test_unresolved_operation_uses_plaintext_and_exact_persisted_membership(cyclic, monkeypatch):
    from twicc.core.content_storage import extraction, references
    from twicc.core.models import SessionItemContent

    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    session = make_session(type='subagent')
    if cyclic:
        Session.objects.filter(pk=session.id).update(parent_session_id=session.id)
    SessionItem.objects.create(session=session, line_num=1, content='A')
    operation = extraction.begin_compute_application(message(session))
    try:
        assert not operation.references.uses_counts
        _advance_storage_operation(operation, 'final')
        final = extraction.final_compute_message(operation, frozenset())
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
        item = SessionItem.objects.get(session=session, line_num=1)
        assert item.current_content.codec == 'plain' and item.original_content_id is None
        assert SessionItemContent.objects.get(pk=item.current_content_id).session_ids == [session.id]
    finally:
        extraction.close_compute_application(operation)


def test_later_native_ancestry_resolution_supersedes_unresolved_operation():
    from twicc.core.content_storage import extraction

    target = make_session(type='subagent')
    root = make_session(id='late-root')
    operation = extraction.begin_compute_application(message(target))
    try:
        batch = extraction.prepare_compute_batch(operation)
        Session.objects.filter(pk=target.id).update(parent_session=root)
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
    finally:
        extraction.close_compute_application(operation)


def test_publication_rollback_does_not_settle_epoch_or_cursor(monkeypatch):
    from django.db import DatabaseError, connection
    from twicc.core.content_storage import extraction

    session = make_session()
    SessionItem.objects.create(session=session, line_num=1, content='A')
    operation = extraction.begin_compute_application(message(session))
    try:
        _advance_storage_operation(operation, 'extraction')
        batch = extraction.prepare_compute_batch(operation)
        before = (operation.cursor, operation.placement['root_epoch'])
        def fail_commit():
            raise DatabaseError('publication rollback')
        with monkeypatch.context() as patch:
            patch.setattr(connection, 'commit', fail_commit)
            with pytest.raises(DatabaseError, match='publication rollback'):
                extraction.publish_compute_batch(operation, batch, frozenset())
        assert (operation.cursor, operation.placement['root_epoch']) == before
        assert SessionItem.objects.get(session=session, line_num=1).current_content_id is None
    finally:
        extraction.close_compute_application(operation)


def test_each_phase_leaves_contextvars_unset():
    from twicc.core.content_storage import extraction, references

    session = make_session()
    SessionItem.objects.create(session=session, line_num=1, content='A')
    operation = extraction.begin_compute_application(message(session))
    try:
        while operation.phase != 'final':
            batch = extraction.prepare_compute_batch(operation)
            assert extraction._placement.get() is None and references._reference_operation.get() is None
            extraction.publish_compute_batch(operation, batch, frozenset())
            assert extraction._placement.get() is None and references._reference_operation.get() is None
    finally:
        extraction.close_compute_application(operation)


def test_empty_metadata_phase_does_not_call_metadata_aggregate_helper(monkeypatch):
    from twicc.core.content_storage import extraction

    session = make_session()
    operation = extraction.begin_compute_application(message(session))
    try:
        _advance_storage_operation(operation, 'metadata')
        def forbidden(*args, **kwargs):
            pytest.fail('Empty metadata must preserve the baseline skip')
        monkeypatch.setattr(BaseSessionCompute, 'apply_session_items_chunk', forbidden)
        batch = extraction.prepare_compute_batch(operation)
        progress = extraction.publish_compute_batch(operation, batch, frozenset())
        assert progress.phase == 'overrides'
    finally:
        extraction.close_compute_application(operation)


def test_final_lifecycle_epoch_settles_only_after_commit(monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from twicc.core.content_storage import extraction

    session = make_session()
    operation = extraction.begin_compute_application(message(
        session, session_fields={'compute_version': 123, 'created_at': (timezone.now() - timedelta(days=1)).isoformat()}))
    try:
        _advance_storage_operation(operation, 'final')
        original_epoch = operation.placement['root_epoch']
        final = extraction.final_compute_message(operation, frozenset())
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):
                raise RuntimeError('late final rollback')
            patch.setattr(extraction, 'finish_storage_result', fail)
            with pytest.raises(RuntimeError, match='late final rollback'):
                BaseSessionCompute._apply_session_complete_final(final)
        assert operation.placement['root_epoch'] == original_epoch
        final = extraction.final_compute_message(operation, frozenset())
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
        session.refresh_from_db()
        assert operation.placement['root_epoch'] == session.content_state_epoch > original_epoch
    finally:
        extraction.close_compute_application(operation)


def test_false_reference_page_guard_maps_to_superseded(monkeypatch):
    from twicc.core.content_storage import extraction, references

    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    session = make_session()
    operation = extraction.begin_compute_application(message(session))
    try:
        monkeypatch.setattr(extraction, 'validate_compute_application', lambda operation: False)
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.prepare_compute_batch(operation)
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('cyclic', [False, True])
def test_unresolved_override_reconciles_both_persisted_items_exactly(cyclic, monkeypatch):
    from twicc.core.content_storage import extraction, references
    from twicc.core.content_storage.types import ContentBinding
    from twicc.core.models import SessionItemContent

    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    target = make_session(type='subagent')
    if cyclic:
        Session.objects.filter(pk=target.id).update(parent_session_id=target.id)
    with transaction.atomic():
        pk = references.intern_contents(['A'], 'codex')['A']
        first = SessionItem.objects.create(session=target, line_num=1, content='', current_content_id=pk)
        second = SessionItem.objects.create(session=target, line_num=2, content='', current_content_id=pk)
        references.bind_persisted_items([ContentBinding(row.id, target.id, pk, None) for row in (first, second)])
    operation = extraction.begin_compute_application(message(target, content_overrides=[
        {'id': first.id, 'content': 'B', 'observed_current_id': pk}]))
    try:
        assert not operation.references.uses_counts
        _advance_storage_operation(operation, 'final')
        assert SessionItemContent.objects.get(pk=pk).session_ids == [target.id]
        first.refresh_from_db()
        assert read_current(first) == 'B' and first.original_content_id is None
        assert first.current_content.codec == 'plain'
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('failure', ['failed', 'superseded'])
def test_failed_preparation_records_elapsed_without_advancing_storage(monkeypatch, failure):
    """Skipping the exception interval loses preparation cost without publishing anything."""
    from twicc.core.content_storage import extraction

    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='preserved')
    operation = extraction.begin_compute_application(message(session))
    try:
        _advance_storage_operation(operation, 'extraction')
        cursor = operation.cursor
        epoch = operation.placement['root_epoch']
        previous_ms = operation.preparation_ms
        error = (RuntimeError('preparation failure') if failure == 'failed'
                 else extraction.SupersededStorageResult('superseded'))

        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(extraction, '_prepare_extraction', fail)
        times = iter((10.0, 10.25))
        monkeypatch.setattr(extraction, 'perf_counter', lambda: next(times))
        with pytest.raises(type(error)) as caught:
            extraction.prepare_compute_batch(operation)
        assert caught.value is error
        assert operation.pending_batch is None
        assert operation.cursor == cursor
        assert operation.placement['root_epoch'] == epoch
        item.refresh_from_db()
        session.refresh_from_db()
        assert item.content == 'preserved'
        assert item.current_content_id is None
        assert session.compute_version is None
        assert operation.preparation_ms == pytest.approx(previous_ms + 250.0)
    finally:
        extraction.close_compute_application(operation)


def test_final_apply_logs_remaining_subphase_timings(caplog, monkeypatch):
    import logging
    from twicc import sync_diagnostics

    monkeypatch.setattr(sync_diagnostics.logger, 'disabled', False)
    caplog.set_level(logging.DEBUG, logger='twicc.sync_diagnostics')
    session = make_session()
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    phases = {record.args[0] for record in caplog.records if record.msg.startswith('Sync phase operation=')}
    assert {
        'compute_final_plan_docs', 'compute_final_titles', 'compute_final_project_directory',
        'compute_final_stopped_agents', 'compute_final_project_metadata',
    } <= phases


def test_final_title_failure_logs_timing_and_rolls_back_publication(caplog, monkeypatch):
    import logging
    from twicc import sync_diagnostics

    monkeypatch.setattr(sync_diagnostics.logger, 'disabled', False)
    caplog.set_level(logging.DEBUG, logger='twicc.sync_diagnostics')
    session = make_session()

    def fail(*args, **kwargs):
        raise RuntimeError('title publication failed')

    monkeypatch.setattr(BaseSessionCompute, '_apply_title_maps', fail)
    with pytest.raises(RuntimeError, match='title publication failed'):
        BaseSessionCompute.apply_session_complete(message(session))
    session.refresh_from_db()
    assert session.compute_version is None
    assert session.content_storage_version is None
    phases = {record.args[0] for record in caplog.records if record.msg.startswith('Sync phase operation=')}
    assert 'compute_final_titles' in phases


@pytest.mark.parametrize('slot', ['current_content', 'original_content'])
@pytest.mark.parametrize('cold', [False, True])
def test_final_storage_rejects_dangling_references_before_publication(slot, cold):
    from datetime import timedelta
    from django.db import connection
    from django.utils import timezone
    from twicc.core.content_storage.codec import ContentIntegrityError
    from twicc.core.content_storage.extraction import finish_storage_result
    from twicc.core.models import SessionItemContent

    session = make_session(archived=cold, created_at=timezone.now() - timedelta(days=40))
    plain = SessionItemContent.objects.create(sha256=b'x' * 32, payload=b'x', raw_size=1)
    item = SessionItem.objects.create(session=session, line_num=1, content='',
                                     current_content=plain, original_content=plain)
    with connection.constraint_checks_disabled():
        try:
            SessionItem.objects.filter(pk=item.pk).update(**{f'{slot}_id': plain.pk + 1000})
            with pytest.raises(ContentIntegrityError), transaction.atomic():
                finish_storage_result(message(session))
            session.refresh_from_db()
            assert session.content_storage_version is None
            assert session.plaintext_verified_epoch is None
        finally:
            SessionItem.objects.filter(pk=item.pk).update(**{f'{slot}_id': plain.pk})


@pytest.mark.parametrize('row_count', [0, 1, 1025])
def test_final_hot_root_probes_plaintext_metadata_once(row_count):
    """Repeated root validation adds another full metadata scan inside the writer grant."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.core.content_storage.extraction import prepare_compute_result

    session = make_session()
    Session.objects.filter(pk=session.id).update(last_line=row_count)
    session.refresh_from_db()
    SessionItem.objects.bulk_create([
        SessionItem(session=session, line_num=line, content=f'line {line}')
        for line in range(1, row_count + 1)
    ])
    prepared = prepare_compute_result(message(session))
    with CaptureQueriesContext(connection) as queries:
        assert BaseSessionCompute._apply_session_complete_final(prepared).outcome == 'applied'
    probes = [query['sql'] for query in queries.captured_queries
              if query['sql'].startswith('SELECT 1 FROM') and ' item LEFT JOIN ' in query['sql']]
    assert len(probes) == 1
    session.refresh_from_db()
    assert session.content_storage_version == 1
    assert session.plaintext_verified_epoch == session.content_state_epoch


@pytest.mark.parametrize('change', ['version', 'incarnation', 'epoch', 'ancestry', 'cold'])
def test_final_root_certification_rechecks_snapshot_fences(monkeypatch, change):
    """A root transition after snapshot creation must prevent final certification."""
    from datetime import timedelta
    from django.db.models import F
    from django.utils import timezone
    from twicc.core.content_storage import state
    from twicc.core.content_storage.extraction import finish_storage_result

    session = make_session(created_at=timezone.now() - timedelta(days=40))
    parent = make_session(id='other-root')
    snapshot = state.snapshot_root_storage

    def transition(root_id):
        observed = snapshot(root_id)
        changes = {
            'version': {'content_storage_version': None},
            'incarnation': {'storage_incarnation': 'new-incarnation'},
            'epoch': {'content_state_epoch': F('content_state_epoch') + 1},
            'ancestry': {'type': 'subagent', 'parent_session': parent},
            'cold': {'archived': True},
        }
        Session.objects.filter(pk=root_id).update(**changes[change])
        return observed

    monkeypatch.setattr(state, 'snapshot_root_storage', transition)
    with transaction.atomic():
        finish_storage_result(message(session))
    session.refresh_from_db()
    assert session.plaintext_verified_epoch is None


def test_final_active_native_child_keeps_cold_root_plaintext_ready():
    """Removing active native-root resolution leaves an active archived root uncertified."""
    from datetime import timedelta
    from django.utils import timezone

    session = make_session(archived=True, created_at=timezone.now() - timedelta(days=40))
    child = make_session(id='active-child', type='subagent', parent_session=session, compute_version=123)
    SessionItem.objects.create(session=session, line_num=1, content='active root')
    result = BaseSessionCompute.apply_session_complete(message(session, _active_session_ids=frozenset({child.id})))
    assert result.outcome == 'applied'
    session.refresh_from_db()
    assert session.content_storage_version == 1
    assert session.plaintext_verified_epoch == session.content_state_epoch


def test_public_verification_rescans_metadata_after_final_root_certification():
    """A final certificate must not let public verification skip changed original content."""
    from twicc.core.content_storage import state
    from twicc.core.models import SessionItemContent

    session = make_session()
    SessionItem.objects.create(session=session, line_num=1, content='original')
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    item = SessionItem.objects.get(session=session)
    original = SessionItemContent.objects.create(sha256=b'o' * 32, payload=b'o', raw_size=1, codec='zstd')
    SessionItem.objects.filter(pk=item.pk).update(original_content=original)
    with transaction.atomic():
        assert not state.session_plaintext_metadata(session.id)
        assert not state.verify_plaintext_root(state.snapshot_root_storage(session.id))


@pytest.mark.parametrize('slot', ['current_content', 'original_content'])
@pytest.mark.parametrize('change', ['zstd', 'dictionary'])
def test_final_hot_root_rejects_nonplaintext_metadata_before_publication(slot, change):
    """Final certification must validate both content slots before publishing the version."""
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage.codec import ContentIntegrityError
    from twicc.core.content_storage.extraction import finish_storage_result
    from twicc.core.models import SessionItemContent

    session = make_session()
    plain = SessionItemContent.objects.create(sha256=b'p' * 32, payload=b'p', raw_size=1)
    invalid = SessionItemContent.objects.create(sha256=b'i' * 32, payload=b'i', raw_size=1)
    if change == 'zstd':
        SessionItemContent.objects.filter(pk=invalid.pk).update(codec='zstd')
    else:
        SessionItemContent.objects.filter(pk=invalid.pk).update(dictionary=_public_dictionary('codex'))
    item = SessionItem.objects.create(session=session, line_num=1, content='',
                                     current_content=plain, original_content=plain)
    SessionItem.objects.filter(pk=item.pk).update(**{f'{slot}_id': invalid.pk})
    with pytest.raises(ContentIntegrityError, match='Hot compute storage requires plaintext'), transaction.atomic():
        finish_storage_result(message(session))
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.plaintext_verified_epoch is None


def test_final_hot_root_requires_caller_transaction_before_version_publication():
    from twicc.core.content_storage.extraction import finish_storage_result

    session = make_session()
    with pytest.raises(transaction.TransactionManagementError):
        finish_storage_result(message(session))
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.plaintext_verified_epoch is None


def test_final_child_validation_does_not_certify_its_root():
    from twicc.core.content_storage.extraction import finish_storage_result

    root = make_session()
    SessionItem.objects.create(session=root, line_num=1, content='unextracted root')
    child = make_session(id='child', type='subagent', parent_session=root)
    with transaction.atomic():
        finish_storage_result(message(child))
    child.refresh_from_db()
    root.refresh_from_db()
    assert child.content_storage_version == 1
    assert child.plaintext_verified_epoch is None
    assert root.content_storage_version is None
    assert root.plaintext_verified_epoch is None


def test_compute_phase_order_includes_stopped_agents_and_compatibility_clears_stops():
    from twicc.core.content_storage import extraction

    root = make_session()
    operation = extraction.begin_compute_application(message(root))
    phases = []
    try:
        while operation.phase != 'final':
            phases.append(operation.phase)
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert phases == ['counts', 'metadata', 'overrides', 'extraction', 'stopped_agents', 'reconciliation']
        assert operation.stopped_agents_complete
    finally:
        extraction.close_compute_application(operation)
    assert extraction.prepare_compute_result(message(root))['agent_stopped'] == []
