"""Initial compute places content using final native-root lifecycle dates."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.content_storage_helpers import _public_dictionary, read_current
from tests.test_content_extraction import make_session, message
from twicc.core.models import SessionItem, SessionItemContent
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('final_hot', [False, True])
def test_initial_compute_uses_final_root_dates_before_compression(final_hot, monkeypatch):
    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old, last_updated_at=old)
    text = '{"message":' + '"repeated text ' * 100 + '}'
    item = SessionItem.objects.create(session=session, line_num=1, content=text)
    inserted = []
    original = SessionItemContent.objects.bulk_create

    def observe(rows, **kwargs):
        inserted.extend(row.codec for row in rows)
        return original(rows, **kwargs)

    monkeypatch.setattr(SessionItemContent.objects, 'bulk_create', observe)
    msg = message(session)
    if final_hot:
        msg['session_fields']['last_updated_at'] = timezone.now().isoformat()
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    item.refresh_from_db()
    assert read_current(item) == text
    assert item.current_content.codec == ('plain' if final_hot else 'zstd')
    assert inserted == ['plain' if final_hot else 'zstd']
    session.refresh_from_db()
    assert session.content_storage_version == 1
    if final_hot:
        assert session.plaintext_verified_epoch == session.content_state_epoch


def test_recent_root_compute_does_not_wait_for_old_descendant_metadata():
    session = make_session()
    child = make_session(id='older-child', type='subagent', parent_session=session)
    SessionItem.objects.create(session=session, line_num=1, content='recent')
    SessionItem.objects.create(session=child, line_num=1, content='old')
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    session.refresh_from_db()
    child.refresh_from_db()
    assert session.compute_version == 123
    assert session.content_storage_version == 1
    assert child.content_storage_version is None
    assert session.plaintext_verified_epoch == session.content_state_epoch
    assert SessionItem.objects.get(session=child, line_num=1).current_content_id is None


@pytest.mark.parametrize('active_child', [False, True])
def test_active_native_tree_prevents_initial_compression(active_child):
    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    active = session
    if active_child:
        active = make_session(id='active-child', type='subagent', parent_session=session)
    item = SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    msg = message(session, _active_session_ids=frozenset({active.id}))
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'


def test_unknown_active_ancestry_keeps_initial_contents_plain():
    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    item = SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    assert BaseSessionCompute.apply_session_complete(message(
        session, _active_session_ids=frozenset({'unresolved-active'}),
    )).outcome == 'applied'
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'


def test_shared_hot_original_requires_plaintext_during_cold_compute():
    from twicc.core.content_storage.references import bind_persisted_items, intern_contents
    from twicc.core.content_storage.types import ContentBinding
    from django.db import transaction

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    cold = make_session(archived=True, created_at=old)
    hot = make_session(id='hot', content_storage_version=1)
    text = 'shared original ' * 1000
    with transaction.atomic():
        ids = intern_contents([text, 'current'], 'codex')
        owner = SessionItem.objects.create(session=hot, line_num=1, content='',
                                           current_content_id=ids['current'], original_content_id=ids[text])
        bind_persisted_items([ContentBinding(owner.id, hot.id, owner.current_content_id, owner.original_content_id)])
    item = SessionItem.objects.create(session=cold, line_num=1, content=text)
    assert BaseSessionCompute.apply_session_complete(message(cold)).outcome == 'applied'
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'
    assert item.current_content.session_ids == sorted([cold.id, hot.id])


def test_old_root_metadata_cannot_authorize_descendant_compression():
    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    root = make_session(archived=True, created_at=old)
    child = make_session(id='old-child', type='subagent', parent_session=root)
    item = SessionItem.objects.create(session=child, line_num=1, content='repeat ' * 1000)
    assert BaseSessionCompute.apply_session_complete(message(child)).outcome == 'applied'
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'
    root.refresh_from_db()
    assert root.compute_version is None


def test_shared_owner_without_storage_completion_keeps_plain():
    from django.db import transaction
    from twicc.core.content_storage.references import bind_persisted_items, intern_contents
    from twicc.core.content_storage.types import ContentBinding
    from twicc.providers.helpers import get_provider_helpers

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    owner = make_session(id='incomplete-owner', archived=True, created_at=old,
                         compute_version=get_provider_helpers('codex').current_compute_version)
    current = make_session(archived=True, created_at=old)
    text = 'shared repeat ' * 1000
    with transaction.atomic():
        pk = intern_contents([text], 'codex')[text]
        item = SessionItem.objects.create(session=owner, line_num=1, content='', current_content_id=pk)
        bind_persisted_items([ContentBinding(item.id, owner.id, pk, None)])
    item = SessionItem.objects.create(session=current, line_num=1, content=text)
    assert BaseSessionCompute.apply_session_complete(message(current)).outcome == 'applied'
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'


def test_optional_encoding_failure_does_not_block_extraction(monkeypatch):
    from twicc.core.content_storage import extraction

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    item = SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    def fail(*args, **kwargs):
        raise RuntimeError('optional encoding failure')
    monkeypatch.setattr(extraction, 'encode_cold', fail)
    monkeypatch.setattr(extraction, 'transcode_content', fail)
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    item.refresh_from_db()
    assert read_current(item) == 'repeat ' * 1000
    assert item.current_content.codec == 'plain'


def test_encoding_runs_outside_write_transactions(monkeypatch):
    from django.db import connection
    from twicc.core.content_storage import extraction

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    original = extraction.encode_cold
    blocks = []
    def observe(*args, **kwargs):
        blocks.append(connection.in_atomic_block)
        return original(*args, **kwargs)
    monkeypatch.setattr(extraction, 'encode_cold', observe)
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    assert blocks and not any(blocks)


def test_final_lifecycle_retry_does_not_mutate_worker_message():
    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    msg = message(session)
    timestamp = old.isoformat()
    msg['session_fields']['created_at'] = timestamp
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    assert msg['session_fields']['created_at'] == timestamp
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'


def test_root_epoch_change_during_preparation_rejects_result(monkeypatch):
    from django.db.models import F
    from twicc.core.content_storage import extraction
    from twicc.core.models import Session

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    original = extraction.encode_cold
    def change_epoch(*args, **kwargs):
        Session.objects.filter(pk=session.id).update(content_state_epoch=F('content_state_epoch') + 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(extraction, 'encode_cold', change_epoch)
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'superseded'
    session.refresh_from_db()
    assert session.compute_version != 123
    assert session.content_storage_version is None


def test_candidate_owner_validation_does_not_repeat_reverse_item_scans():
    from django.db import connection

    _public_dictionary('codex')
    old = timezone.now() - timedelta(days=60)
    session = make_session(archived=True, created_at=old)
    for line in range(1, 5):
        SessionItem.objects.create(session=session, line_num=line, content=f'repeat {line} ' * 1000)
    queries = []
    def observe(execute, sql, params, many, context):
        queries.append(sql)
        return execute(sql, params, many, context)
    with connection.execute_wrapper(observe):
        assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    assert not [sql for sql in queries if sql.startswith(f'SELECT "{SessionItem._meta.db_table}"."session_id" AS "session_id" FROM')]
