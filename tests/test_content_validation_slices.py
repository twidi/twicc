"""Paged metadata evidence belongs only to its retained compute operation."""
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.test_content_extraction import make_session, message
from twicc.core.content_storage import extraction
from twicc.core.content_storage.codec import ContentIntegrityError
from twicc.core.models import Session, SessionItem
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


def advance(operation, phase='final', active=frozenset()):
    while operation.phase != phase:
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), active)


def begin(owner, **kwargs):
    return extraction.begin_compute_application(message(owner, **kwargs), progressive_facts=True)


def test_progressive_finalization_does_not_scan_session_items(content_row_factory):
    owner = make_session(content_storage_version=1, content_state_epoch=0, plaintext_verified_epoch=0,
                         archived=True, created_at=timezone.now() - timedelta(days=60))
    owner.last_line = 800
    owner.save(update_fields=['last_line'])
    row = content_row_factory('plaintext', False, session_ids=[owner.id])
    SessionItem.objects.bulk_create([
        SessionItem(session=owner, line_num=line, content='', current_content=row, original_content=row)
        for line in range(1, 770)
    ])
    child = make_session(id='child', type='subagent', parent_session=owner,
                         compute_version=123, content_storage_version=1)
    from twicc.core.content_storage.lifecycle import update_lifecycle
    update_lifecycle(Session.objects.filter(pk=child.id), defer_plaintext_inspection=True,
                     last_updated_at=timezone.now())
    owner.refresh_from_db()
    assert owner.plaintext_verified_epoch != owner.content_state_epoch
    update_lifecycle(Session.objects.filter(pk=owner.id), defer_plaintext_inspection=True,
                     last_updated_at=timezone.now())
    owner.refresh_from_db()
    operation = begin(owner, session_fields={'compute_version': 123, 'last_updated_at': timezone.now().isoformat()})
    try:
        advance(operation)
        assert Session.objects.values('last_line', 'last_offset', 'history_epoch', 'storage_incarnation').get(pk=owner.pk) == {
            'last_line': operation.message['observed_upper_line'], 'last_offset': operation.message['observed_last_offset'],
            'history_epoch': operation.message['observed_epoch'], 'storage_incarnation': operation.message['observed_incarnation']}
        with CaptureQueriesContext(connection) as queries:
            msg = extraction.final_compute_message(operation, frozenset())
            assert BaseSessionCompute._apply_session_complete_final(msg).outcome == 'applied'
        probes = [q['sql'] for q in queries if q['sql'].startswith('SELECT')
                  and 'core_sessionitem' in q['sql']]
        assert probes == []
        assert operation.validation.complete
        assert operation.validation.cursor == 800
        assert set(Session.objects.filter(plaintext_verified_epoch=F('content_state_epoch')).values_list('id', flat=True)) == {owner.id}
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('lines', [[], [2, 8]])
def test_exhausted_page_covers_empty_sessions_and_line_gaps(content_row_factory, lines):
    owner = make_session()
    row = content_row_factory('text', False, session_ids=[owner.id])
    for line in lines:
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        assert operation.validation is None
        batch = extraction.prepare_compute_batch(operation)
        extraction.publish_compute_batch(operation, batch, frozenset())
        if lines:
            assert not operation.validation.complete
            assert operation.validation.cursor == 8
            batch = extraction.prepare_compute_batch(operation)
            assert batch.rows == ()
            extraction.publish_compute_batch(operation, batch, frozenset())
        assert operation.validation.complete
        assert operation.validation.cursor == owner.last_line
        assert operation.phase == 'final'
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('slot', ['current', 'original'])
@pytest.mark.parametrize('invalid', ['missing', 'null_codec', 'dictionary', 'unknown_codec', 'legacy', 'compressed'])
def test_validation_rechecks_both_reference_slots(content_row_factory, slot, invalid, monkeypatch):
    owner = make_session()
    row = content_row_factory('value ' * 100, invalid == 'compressed', session_ids=[owner.id])
    other = content_row_factory('other', False, session_ids=[owner.id])
    item = SessionItem.objects.create(session=owner, line_num=1, content='', current_content=other,
                                      original_content=other)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        # Corrupt only after reconciliation, so the validation predicate must reject it.
        if invalid == 'null_codec':
            with connection.cursor() as cursor:
                cursor.execute('CREATE TEMP TABLE nullable_validation_content AS SELECT * FROM core_sessionitemcontent')
                cursor.execute('UPDATE nullable_validation_content SET codec = NULL WHERE id = %s', [row.id])
            from twicc.core.models import SessionItemContent
            monkeypatch.setattr(SessionItemContent._meta, 'db_table', 'nullable_validation_content')
        elif invalid == 'dictionary':
            from tests.content_storage_helpers import _public_dictionary
            row.dictionary_id = _public_dictionary('codex').id
            row.save(update_fields=['dictionary_id'])
        elif invalid == 'unknown_codec':
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA ignore_check_constraints = ON')
                cursor.execute("UPDATE core_sessionitemcontent SET codec = 'broken' WHERE id = %s", [row.id])
        if invalid == 'legacy':
            SessionItem.objects.filter(pk=item.id).update(content='legacy')
        else:
            SessionItem.objects.filter(pk=item.id).update(**{f'{slot}_content_id': row.id})
            if invalid == 'missing':
                with connection.constraint_checks_disabled(), connection.cursor() as cursor:
                    cursor.execute('DELETE FROM core_sessionitemcontent WHERE id = %s', [row.id])
        with pytest.raises(ContentIntegrityError):
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert operation.validation is None
    finally:
        monkeypatch.undo()
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS nullable_validation_content')
        # Restore referential integrity for Django's teardown check.
        SessionItem.objects.filter(pk=item.id).update(current_content=other, original_content=other)
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA ignore_check_constraints = OFF')
        extraction.close_compute_application(operation)


def test_validation_adopts_coverage_only_after_commit(content_row_factory, monkeypatch):
    owner = make_session()
    row = content_row_factory('plain', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        batch = extraction.prepare_compute_batch(operation)
        original = connection.commit
        monkeypatch.setattr(connection, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('commit failure')))
        with pytest.raises(RuntimeError, match='commit failure'):
            extraction.publish_compute_batch(operation, batch, frozenset())
        assert operation.validation is None
        assert operation.pending_batch is batch
        monkeypatch.setattr(connection, 'commit', original)
        extraction.publish_compute_batch(operation, batch, frozenset())
        assert operation.validation.cursor == 1
    finally:
        extraction.close_compute_application(operation)


def test_activity_tightens_after_validation_restarts_bounded_repair(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row, original_content=row)
    operation = begin(owner)
    try:
        advance(operation)
        assert not operation.validation.requires_plaintext
        assert extraction.final_compute_message(operation, frozenset({owner.id})) is None
        assert operation.phase == 'reconciliation'
        assert operation.validation is None
        owner.refresh_from_db()
        assert owner.compute_version != 123
        assert owner.plaintext_verified_epoch != owner.content_state_epoch
        advance(operation, active=frozenset({owner.id}))
        assert operation.validation.complete and operation.validation.requires_plaintext
        row.refresh_from_db()
        assert row.codec == 'plain'
        msg = extraction.final_compute_message(operation, frozenset({owner.id}))
        assert BaseSessionCompute._apply_session_complete_final(msg).outcome == 'applied'
    finally:
        extraction.close_compute_application(operation)


def test_private_completion_rejects_arbitrary_message_and_other_operation():
    from twicc.core.content_storage.validation import complete_validated_storage
    owner = make_session()
    operation = begin(owner)
    other = begin(owner)
    try:
        advance(operation)
        final = extraction.final_compute_message(operation, frozenset())
        for candidate, msg in [(operation, dict(final)), (other, final)]:
            with transaction.atomic():
                extraction.validate_final_compute_operation(final)
                with pytest.raises(ValueError):
                    complete_validated_storage(candidate, msg)
    finally:
        extraction.close_compute_application(operation)
        extraction.close_compute_application(other)


def first_validation_page(operation):
    from twicc.core.content_storage.validation import prepare_validation_page
    advance(operation, 'validation')
    batch = prepare_validation_page(operation, row_budget=1)
    operation.pending_batch = batch
    extraction.publish_compute_batch(operation, batch, frozenset())
    assert operation.validation.cursor == 1
    assert not operation.validation.complete


@pytest.mark.parametrize('mutation', ['append', 'reference', 'owner', 'resume', 'ancestry', 'epoch'])
def test_external_mutations_reject_validated_prefix(content_row_factory, mutation):
    from twicc.core.content_storage.lifecycle import update_lifecycle
    from twicc.core.content_storage.references import bind_persisted_items
    from twicc.core.content_storage.state import invalidate_roots
    from twicc.core.content_storage.types import ContentBinding
    owner = make_session()
    row = content_row_factory('plain', False, session_ids=[owner.id])
    items = [SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
             for line in (1, 2)]
    operation = begin(owner)
    try:
        first_validation_page(operation)
        accepted = operation.validation
        expected_epoch = operation.placement['root_epoch']
        with transaction.atomic():
            if mutation == 'append':
                Session.objects.filter(pk=owner.id).update(last_line=11, last_offset=1)
            elif mutation in {'reference', 'owner'}:
                target = items[0]
                if mutation == 'reference':
                    fresh = content_row_factory('replacement', False, session_ids=[])
                    target.current_content_id = fresh.id
                    target.save(update_fields=['current_content'])
                else:
                    row.session_ids = []
                    row.save(update_fields=['session_ids'])
                bind_persisted_items([ContentBinding(target.id, owner.id, target.current_content_id, None)])
            elif mutation == 'resume':
                update_lifecycle(Session.objects.filter(pk=owner.id), last_started_at=timezone.now())
            elif mutation == 'ancestry':
                parent = make_session(id='new-parent')
                update_lifecycle(Session.objects.filter(pk=owner.id), type='subagent', parent_session_id=parent.id)
            else:
                invalidate_roots([owner.id])
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert operation.validation is accepted
        assert operation.placement['root_epoch'] == expected_epoch
        owner.refresh_from_db()
        assert owner.compute_version != 123
        assert owner.plaintext_verified_epoch != owner.content_state_epoch
    finally:
        extraction.close_compute_application(operation)


def test_external_epoch_before_own_lifecycle_cannot_be_adopted(content_row_factory):
    from twicc.core.content_storage.state import invalidate_roots
    owner = make_session()
    row = content_row_factory('value', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    changed = timezone.now()
    operation = begin(owner, session_fields={'compute_version': 123, 'last_updated_at': changed.isoformat()})
    try:
        advance(operation)
        final = extraction.final_compute_message(operation, frozenset())
        expected = operation.placement['root_epoch']
        with transaction.atomic():
            invalidate_roots([owner.id])
        with pytest.raises(extraction.SupersededStorageResult):
            BaseSessionCompute._apply_session_complete_final(final)
        owner.refresh_from_db()
        assert owner.last_updated_at != changed
        assert operation.placement['root_epoch'] == expected
        assert owner.compute_version != 123
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('child_target', [True, False])
def test_certificate_covers_only_validated_native_root(content_row_factory, child_target):
    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root,
                         compute_version=123, content_storage_version=1)
    target = child if child_target else root
    row = content_row_factory('text', False, session_ids=[root.id, child.id])
    for owner in (root, child):
        SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(target)
    try:
        advance(operation)
        msg = extraction.final_compute_message(operation, frozenset())
        assert BaseSessionCompute._apply_session_complete_final(msg).outcome == 'applied'
        root.refresh_from_db()
        child.refresh_from_db()
        assert child.plaintext_verified_epoch is None
        if child_target:
            assert root.plaintext_verified_epoch != root.content_state_epoch
        else:
            assert root.plaintext_verified_epoch == root.content_state_epoch
    finally:
        extraction.close_compute_application(operation)


def test_final_rollback_publishes_neither_readiness_nor_own_epoch(content_row_factory, monkeypatch):
    from twicc.core.content_storage import validation
    owner = make_session()
    row = content_row_factory('text', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(owner, session_fields={'compute_version': 123, 'last_updated_at': timezone.now().isoformat()})
    try:
        advance(operation)
        msg = extraction.final_compute_message(operation, frozenset())
        expected = operation.placement['root_epoch']
        complete = validation.complete_validated_storage
        def fail(*args):
            complete(*args)
            raise RuntimeError('final rollback')
        with monkeypatch.context() as patch:
            patch.setattr(validation, 'complete_validated_storage', fail)
            with pytest.raises(RuntimeError, match='final rollback'):
                BaseSessionCompute._apply_session_complete_final(msg)
        owner.refresh_from_db()
        assert owner.content_storage_version is None
        assert owner.compute_version != 123
        assert owner.plaintext_verified_epoch is None
        assert owner.content_state_epoch == expected
        assert operation.placement['root_epoch'] == expected
        # The rolled-back message contains transaction-local own-epoch state.
        # Build a fresh handoff from the still-live committed operation.
        msg = extraction.final_compute_message(operation, frozenset())
        assert BaseSessionCompute._apply_session_complete_final(msg).outcome == 'applied'
    finally:
        extraction.close_compute_application(operation)


def test_stricter_policy_between_pages_discards_prefix_even_if_activity_ends(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    for line in (1, 2):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = begin(owner)
    try:
        first_validation_page(operation)
        assert not operation.validation.requires_plaintext
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset({owner.id}))
        assert operation.validation is None and operation.phase == 'reconciliation'
        assert operation.return_to_validation
        advance(operation)
        assert operation.validation.requires_plaintext
        assert operation.validation.complete
        row.refresh_from_db()
        assert row.codec == 'plain'
    finally:
        extraction.close_compute_application(operation)


def test_cold_threshold_crossing_preserves_stronger_coverage(content_row_factory, monkeypatch):
    from twicc.core.content_storage import validation
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=14))
    row = content_row_factory('text', False, session_ids=[owner.id])
    for line in (1, 2):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = begin(owner)
    try:
        first_validation_page(operation)
        assert operation.validation.requires_plaintext
        now = timezone.now()
        monkeypatch.setattr(validation.timezone, 'now', lambda: now + timedelta(days=2))
        advance(operation)
        assert operation.validation.requires_plaintext
        assert BaseSessionCompute._apply_session_complete_final(
            extraction.final_compute_message(operation, frozenset())).outcome == 'applied'
        owner.refresh_from_db()
        assert owner.plaintext_verified_epoch is None  # Cold roots receive no new certificate.
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('invalidate', [False, True])
def test_foreground_plaintext_promotion_keeps_prefix_only_with_matching_guards(content_row_factory, invalidate):
    from twicc.core.content_storage.codec import encode_plain
    from twicc.core.content_storage.state import invalidate_roots
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    for line in (1, 2):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row)
    operation = begin(owner)
    try:
        first_validation_page(operation)
        with transaction.atomic():
            encoded = encode_plain('schema ' * 100)
            row.codec, row.dictionary_id, row.payload = 'plain', None, encoded.payload
            row.save(update_fields=['codec', 'dictionary_id', 'payload'])
            if invalidate:
                invalidate_roots([owner.id])
        if invalidate:
            with pytest.raises(extraction.SupersededStorageResult):
                advance(operation)
        else:
            advance(operation)
            assert operation.validation.complete
            assert not operation.validation.requires_plaintext
    finally:
        extraction.close_compute_application(operation)


def test_activity_before_first_validation_page_reconciles_plaintext(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('schema ' * 100, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset({owner.id}))
        assert operation.phase == 'reconciliation'
        assert operation.validation is None
        advance(operation, active=frozenset({owner.id}))
        assert operation.validation.requires_plaintext and operation.validation.complete
    finally:
        extraction.close_compute_application(operation)


def test_validation_outer_rollback_does_not_adopt_coverage(content_row_factory):
    owner = make_session()
    row = content_row_factory('plain', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        batch = extraction.prepare_compute_batch(operation)
        with transaction.atomic():
            extraction.publish_compute_batch(operation, batch, frozenset())
            assert operation.validation is None
            transaction.set_rollback(True)
        assert operation.validation is None
        assert operation.pending_batch is batch
    finally:
        extraction.close_compute_application(operation)


def test_validation_metadata_query_is_bounded_and_never_selects_payloads(content_row_factory):
    from twicc.core.content_storage.validation import prepare_validation_page
    owner = make_session()
    row = content_row_factory('text', False, session_ids=[owner.id])
    for line in range(1, 6):
        SessionItem.objects.create(session=owner, line_num=line, content='', current_content=row, original_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        with CaptureQueriesContext(connection) as preparation:
            batch = prepare_validation_page(operation, row_budget=3, byte_budget=1)
        assert len(batch.rows) == 1 and batch.bytes > 1
        assert any('LIMIT 3' in q['sql'] for q in preparation)
        operation.pending_batch = batch
        with CaptureQueriesContext(connection) as publication:
            extraction.publish_compute_batch(operation, batch, frozenset())
        item_reads = [q['sql'] for q in publication if q['sql'].startswith('SELECT')
                      and 'core_sessionitem' in q['sql']]
        assert len(item_reads) == 1 and 'item.id IN (' in item_reads[0]
        for query in [*preparation, *publication]:
            assert 'payload' not in query['sql'].lower()
            assert 'sha256' not in query['sql'].lower()
            assert 'item.content,' not in query['sql'].lower()
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('change', ['closed', 'pending', 'incomplete', 'connection'])
def test_private_completion_rejects_incomplete_or_detached_evidence(change):
    from twicc.core.content_storage.validation import complete_validated_storage
    owner = make_session()
    operation = begin(owner)
    try:
        advance(operation)
        final = extraction.final_compute_message(operation, frozenset())
        with transaction.atomic():
            extraction.validate_final_compute_operation(final)
            if change == 'closed':
                extraction.close_compute_application(operation)
            elif change == 'pending':
                operation.pending_batch = object()
            elif change == 'incomplete':
                operation.validation = operation.validation._replace(complete=False)
            else:
                operation.references._connection = object()
            with pytest.raises((ValueError, RuntimeError)):
                complete_validated_storage(operation, final)
        owner.refresh_from_db()
        assert owner.content_storage_version is None
    finally:
        if change == 'connection':
            operation.references._connection = connection.connection
        extraction.close_compute_application(operation)


def test_exact_handoff_cannot_authorize_completion_outside_guarded_final_transaction():
    from twicc.core.content_storage.validation import complete_validated_storage
    owner = make_session()
    operation = begin(owner)
    try:
        advance(operation)
        final = extraction.final_compute_message(operation, frozenset())
        with transaction.atomic(), pytest.raises(ValueError):
            complete_validated_storage(operation, final)
        owner.refresh_from_db()
        assert owner.content_storage_version is None
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('slot', ['current', 'original'])
@pytest.mark.parametrize('shape', ['missing_dictionary', 'provider_mismatch', 'plain_dictionary', 'unknown_codec'])
def test_cold_validation_rejects_invalid_compressed_metadata(content_row_factory, slot, shape):
    from tests.content_storage_helpers import _public_dictionary
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('value', False, session_ids=[owner.id])
    item = SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row, original_content=row)
    operation = begin(owner)
    original_dictionary = row.dictionary_id
    try:
        advance(operation, 'validation')
        row.codec = 'plain' if shape == 'plain_dictionary' else 'broken' if shape == 'unknown_codec' else 'zstd'
        row.dictionary_id = (_public_dictionary('claude_code').id if shape == 'provider_mismatch' else
                             _public_dictionary('codex').id if shape == 'plain_dictionary' else None)
        row.save(update_fields=['codec', 'dictionary_id'])
        # One slot carries the invalid metadata, while its peer remains valid.
        valid = content_row_factory('valid', False, session_ids=[owner.id])
        SessionItem.objects.filter(pk=item.id).update(**{
            'current_content_id' if slot == 'original' else 'original_content_id': valid.id,
        })
        with pytest.raises(ContentIntegrityError):
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert operation.validation is None
    finally:
        row.codec, row.dictionary_id = 'plain', original_dictionary
        row.save(update_fields=['codec', 'dictionary_id'])
        extraction.close_compute_application(operation)


def test_proposed_hot_lifecycle_repairs_both_slots_before_validation(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    current = content_row_factory('current schema ' * 100, True, session_ids=[owner.id])
    original = content_row_factory('original schema ' * 100, True, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=current, original_content=original)
    operation = begin(owner, session_fields={'compute_version': 123, 'last_updated_at': timezone.now().isoformat()})
    try:
        advance(operation)
        assert operation.validation.requires_plaintext
        for row in (current, original):
            row.refresh_from_db()
            assert row.codec == 'plain' and row.dictionary_id is None
        assert BaseSessionCompute._apply_session_complete_final(
            extraction.final_compute_message(operation, frozenset())).outcome == 'applied'
        owner.refresh_from_db()
        assert owner.plaintext_verified_epoch == owner.content_state_epoch
    finally:
        extraction.close_compute_application(operation)


def test_null_current_reference_is_not_complete_placement(content_row_factory):
    owner = make_session()
    row = content_row_factory('text', False, session_ids=[owner.id])
    item = SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    operation = begin(owner)
    try:
        advance(operation, 'validation')
        SessionItem.objects.filter(pk=item.id).update(current_content_id=None)
        with pytest.raises(ContentIntegrityError):
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert operation.validation is None
    finally:
        extraction.close_compute_application(operation)


def test_detached_compatibility_completion_owns_independent_placement_validation(content_row_factory):
    owner = make_session(archived=True, created_at=timezone.now() - timedelta(days=60))
    row = content_row_factory('text', False, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    final = extraction.prepare_compute_result(message(owner))
    row.codec = 'broken'
    row.save(update_fields=['codec'])
    with pytest.raises(ContentIntegrityError):
        BaseSessionCompute._apply_session_complete_final(final)
    owner.refresh_from_db()
    assert owner.content_storage_version is None


def test_close_discards_operation_validation_and_final_authorization():
    owner = make_session()
    operation = begin(owner)
    advance(operation)
    extraction.final_compute_message(operation, frozenset())
    extraction.close_compute_application(operation)
    assert operation.validation is None
    assert operation.final_message is None
    assert operation.final_transaction is None
