"""Reconciliation changes representations only when current metadata requires it."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.content_storage_helpers import content_row_factory
from tests.test_content_extraction import make_session, message
from twicc.core.content_storage import extraction
from twicc.core.content_storage.policy import compression_policy_version
from twicc.core.models import SessionItem

pytestmark = pytest.mark.django_db(transaction=True)


def reconcile(owner):
    operation = extraction.begin_compute_application(message(owner))
    try:
        with extraction._compute_scope(operation, frozenset()):
            batch = extraction._prepare_reconciliation(operation.message, -1)
            extraction._publish_reconciliation(operation.message, batch)
        return batch
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('attempt', [True, False])
def test_new_conforming_contents_skip_reconciliation(content_row_factory, attempt, monkeypatch):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    policy = compression_policy_version('codex', row.dictionary_id)
    if attempt:
        row.compression_attempt_version = policy
        row.save(update_fields=['compression_attempt_version'])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    decode_calls = []
    original = extraction.transcode_content

    def transcode(*args, **kwargs):
        decode_calls.append(args[0].id)
        return original(*args, **kwargs)

    monkeypatch.setattr(extraction, 'transcode_content', transcode)
    with CaptureQueriesContext(connection) as queries:
        batch = reconcile(owner)
    sql = [q['sql'].lower() for q in queries if 'core_sessionitemcontent"' in q['sql'].lower()]
    assert not any('select' in q and '"payload"' in q and 'length(' not in q for q in sql)
    assert not any(q.startswith('update') and '"payload"' in q for q in sql)
    row.refresh_from_db()
    assert row.compression_attempt_version == policy
    assert len(batch.prepared) == (0 if attempt else 1)
    assert decode_calls == []


def test_current_no_gain_is_not_retried(content_row_factory):
    from tests.content_storage_helpers import _public_dictionary
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('x', False, session_ids=[owner.id])
    row.compression_attempt_version = compression_policy_version('codex', _public_dictionary('codex').id)
    row.compression_no_gain = True
    row.save(update_fields=['compression_attempt_version', 'compression_no_gain'])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    with CaptureQueriesContext(connection) as queries:
        batch = reconcile(owner)
    assert batch.prepared == ()
    assert not any('core_sessionitemcontent' in q['sql'] and '"payload"' in q['sql'] and 'SELECT' in q['sql'] and 'LENGTH(' not in q['sql'] for q in queries)


def test_hot_owner_added_after_preparation_rejects_compression(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = extraction.begin_compute_application(message(owner))
    try:
        with extraction._compute_scope(operation, frozenset()):
            batch = extraction._prepare_reconciliation(operation.message, -1)
            hot = make_session(id='hot')
            row.session_ids = sorted([owner.id, hot.id])
            row.save(update_fields=['session_ids'])
            with pytest.raises(extraction.SupersededStorageResult):
                extraction._publish_reconciliation(operation.message, batch)
        row.refresh_from_db()
        assert row.codec == 'plain'
    finally:
        extraction.close_compute_application(operation)


def test_no_gain_metadata_action_does_not_write_payload(content_row_factory):
    from tests.content_storage_helpers import _public_dictionary
    _public_dictionary('codex')
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('x', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    with CaptureQueriesContext(connection) as queries:
        reconcile(owner)
    assert not any(q['sql'].startswith('UPDATE "core_sessionitemcontent"') and '"payload"' in q['sql'] for q in queries)
    row.refresh_from_db()
    assert row.compression_no_gain is True


def test_repeated_shared_ids_are_not_transcoded_again(content_row_factory):
    from tests.content_storage_helpers import _public_dictionary
    _public_dictionary('codex')
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, False, session_ids=[owner.id])
    for line in (1, 2):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = extraction.begin_compute_application(message(owner))
    try:
        with extraction._compute_scope(operation, frozenset()):
            first = extraction._prepare_reconciliation(operation.message, -1, 1)
            extraction._publish_reconciliation(operation.message, first)
            operation.message['_storage_reconcile_position'] = first.cursor
            with CaptureQueriesContext(connection) as queries:
                second = extraction._prepare_reconciliation(operation.message, 1, 1)
                extraction._publish_reconciliation(operation.message, second)
        assert second.prepared == ()
        assert not any('core_sessionitemcontent' in q['sql'] and '"payload"' in q['sql']
                       and 'SELECT' in q['sql'] and 'LENGTH(' not in q['sql'] for q in queries)
    finally:
        extraction.close_compute_application(operation)


def test_promotion_between_pages_is_not_hidden_by_visited_ids(content_row_factory):
    from twicc.core.content_storage.codec import encode_plain
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    text = 'schema ' * 100
    row = content_row_factory(text, True, session_ids=[owner.id])
    for line in (1, 2):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = extraction.begin_compute_application(message(owner))
    try:
        with extraction._compute_scope(operation, frozenset()):
            first = extraction._prepare_reconciliation(operation.message, -1, 1)
            extraction._publish_reconciliation(operation.message, first)
            operation.message['_storage_reconcile_position'] = first.cursor
            row.codec = 'plain'
            row.dictionary_id = None
            row.payload = encode_plain(text).payload
            row.save(update_fields=['codec', 'dictionary_id', 'payload'])
            second = extraction._prepare_reconciliation(operation.message, 1, 1)
            assert len(second.prepared) == 1
            extraction._publish_reconciliation(operation.message, second)
        row.refresh_from_db()
        assert row.codec == 'zstd'
    finally:
        extraction.close_compute_application(operation)


def test_maintenance_conforming_content_skips_payload(content_row_factory):
    from twicc.content_maintenance_task import prepare_compression_batch
    from twicc.providers.helpers import get_provider_helpers
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60),
                         compute_version=get_provider_helpers('codex').current_compute_version,
                         content_storage_version=1)
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    with CaptureQueriesContext(connection) as queries:
        _, entries, _ = prepare_compression_batch(0)
    assert entries == ()
    assert not any('core_sessionitemcontent' in q['sql'] and '"payload"' in q['sql']
                   and 'SELECT' in q['sql'] and 'LENGTH(' not in q['sql'] for q in queries)
