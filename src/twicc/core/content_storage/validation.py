"""Bounded metadata coverage for one live, guarded compute application."""

from django.db import connection, transaction
from django.utils import timezone

from twicc.core.content_storage.codec import ContentIntegrityError
from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION, cold_from_fields
from twicc.core.content_storage.types import PreparedComputeBatch, StorageValidationState
from twicc.core.models import ContentDictionary, Session, SessionItem, SessionItemContent


def invalid_storage_predicate(*, requires_plaintext):
    """One NULL-safe definition for placement and the stronger plaintext property."""
    def invalid_slot(alias):
        shape = (f"{alias}.codec <> 'plain' OR {alias}.dictionary_id IS NOT NULL" if requires_plaintext else
                 f"{alias}.codec NOT IN ('plain', 'zstd') "
                 f"OR ({alias}.codec = 'plain' AND {alias}.dictionary_id IS NOT NULL) "
                 f"OR ({alias}.codec = 'zstd' AND ({alias}_dict.id IS NULL "
                 f"OR {alias}_dict.provider <> {alias}.dictionary_provider))")
        return f'{alias}.id IS NULL OR {alias}.codec IS NULL OR ({shape})'
    return ('item.content IS NULL OR LENGTH(item.content) <> 0 OR (' + invalid_slot('current') + ') '
            'OR (item.original_content_id IS NOT NULL AND (' + invalid_slot('original') + '))')


def _metadata_rows(session_id, where, params, *, limit=None, requires_plaintext=False):
    quote = connection.ops.quote_name
    items, contents, dictionaries = (quote(model._meta.db_table)
                                     for model in (SessionItem, SessionItemContent, ContentDictionary))
    invalid = invalid_storage_predicate(requires_plaintext=requires_plaintext)
    # The byte estimate covers selected metadata, never legacy text or payload bytes.
    sql = (f'SELECT item.id, item.line_num, '
           f'96 + COALESCE(LENGTH(current.codec), 0) + COALESCE(LENGTH(original.codec), 0), '
           f'CASE WHEN ({invalid}) THEN 1 ELSE 0 END '
           f'FROM {items} item LEFT JOIN {contents} current ON current.id = item.current_content_id '
           f'LEFT JOIN {contents} original ON original.id = item.original_content_id '
           f'LEFT JOIN {dictionaries} current_dict ON current_dict.id = current.dictionary_id '
           f'LEFT JOIN {dictionaries} original_dict ON original_dict.id = original.dictionary_id '
           f'WHERE item.session_id = %s AND ({where}) ORDER BY item.line_num')
    if limit is not None:
        sql += ' LIMIT %s'
        params = [*params, limit]
    with connection.cursor() as cursor:
        cursor.execute(sql, [session_id, *params])
        return cursor.fetchall()


def requires_plaintext(operation, placement):
    """Evaluate proposed native-root lifecycle and admitted activity together."""
    effective = dict(placement['initial_fields'] or {})
    effective.update(placement['final_fields'])
    return (operation.plaintext_required
            or operation.validation is not None and operation.validation.requires_plaintext
            or placement['unresolved_active'] or placement['root_id'] is None
            or placement['root_id'] in placement['active_roots']
            or not cold_from_fields(effective, timezone.now()))


def prepare_validation_page(operation, *, row_budget=256, byte_budget=262144):
    from twicc.core.content_storage.extraction import validate_compute_application

    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Validation budgets must be positive')
    validate_compute_application(operation)
    state = operation.validation
    cursor = state.cursor if state else -1
    upper = operation.message['observed_upper_line']
    rows = _metadata_rows(operation.message['session_id'], 'item.line_num > %s AND item.line_num <= %s',
                          [cursor, upper], limit=row_budget)
    accepted, size = [], 0
    for pk, line, cost, _ in rows:
        if accepted and size + cost > byte_budget:
            break
        accepted.append((pk, line))
        size += cost
    # Exhaustion is its own guarded page. A line gap never obstructs completion.
    return PreparedComputeBatch('validation', accepted[-1][1] if accepted else upper,
                                tuple(accepted), (), None, size, not accepted)


def publish_validation_page(operation, batch):
    from twicc.core.content_storage.extraction import _placement, guard_storage, SupersededStorageResult

    msg = operation.message
    guard_storage(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                  msg['observed_upper_line'], msg.get('observed_last_offset'))
    strong = requires_plaintext(operation, _placement.get())
    if batch.rows:
        ids = [row[0] for row in batch.rows]
        rows = _metadata_rows(msg['session_id'], 'item.id IN (' + ','.join(['%s'] * len(ids)) + ')', ids,
                              requires_plaintext=strong)
        if [(row[0], row[1]) for row in rows] != list(batch.rows):
            raise SupersededStorageResult('superseded')
        if any(row[3] for row in rows):
            raise ContentIntegrityError('Compute storage metadata is invalid')
    else:
        cursor = operation.validation.cursor if operation.validation else -1
        if SessionItem.objects.filter(session_id=msg['session_id'], line_num__gt=cursor,
                                      line_num__lte=msg['observed_upper_line']).exists():
            raise SupersededStorageResult('superseded')
    return StorageValidationState(msg['observed_upper_line'], batch.cursor, strong, batch.done)


def restart_stronger_validation(operation, placement):
    """Discard weak evidence only after the unchanged operation fence succeeds."""
    state = operation.validation
    weak = (not state.requires_plaintext if state is not None else
            operation.phase in {'validation', 'final'} and not operation.reconciled_requires_plaintext)
    if not weak or not requires_plaintext(operation, placement):
        return False

    def adopt():
        operation.validation = None
        operation.plaintext_required = True
        operation.reconciled_contents.clear()
        operation.return_to_validation = True
        operation.pending_batch = None
        operation.phase = 'reconciliation'
        operation.cursor = (-1, 1)
        operation.final_message = None
        operation.final_transaction = None

    transaction.on_commit(adopt)
    return True


def complete_validated_storage(operation, final_msg):
    """Consume exact live evidence inside its source-guarded final transaction."""
    from twicc.core.content_storage.extraction import pending_root_fields, SupersededStorageResult
    from twicc.core.content_storage.state import resolve_root_decision

    if (not connection.in_atomic_block or operation.closed
            or operation.final_transaction is not connection.atomic_blocks[-1]
            or final_msg is not operation.final_message
            or final_msg.get('_storage_operation') is not operation or operation.phase != 'final'
            or operation.pending_batch is not None or not operation.stopped_agents_complete
            or not operation.history_facts_published or operation.validation is None
            or not operation.validation.complete
            or operation.validation.upper_line != operation.message['observed_upper_line']
            or operation.validation.cursor != operation.validation.upper_line):
        raise ValueError('Private storage completion requires its complete live owning operation')
    operation.references.validate_connection()
    pending = final_msg['_storage_final_root']
    if requires_plaintext(operation, pending) and not operation.validation.requires_plaintext:
        raise SupersededStorageResult('superseded')
    session_id = operation.observation.session_id
    root_id, actual = pending_root_fields(session_id, {}, pending['active_roots'])
    if root_id != pending['root_id'] or (actual and (
        actual['storage_incarnation'] != pending['root_incarnation']
        or actual['content_state_epoch'] != pending['root_epoch']
        or any(actual[name] != value for name, value in pending['final_fields'].items())
    )):
        raise SupersededStorageResult('superseded')
    Session.objects.filter(pk=session_id, storage_incarnation=operation.observation.target[0]).update(
        content_storage_version=CONTENT_STORAGE_VERSION)
    decision = resolve_root_decision(session_id, timezone.now(), pending['active_roots'])
    if (operation.validation.requires_plaintext and decision.resolved and decision.root_id == session_id
            and not decision.is_cold):
        Session.objects.filter(pk=session_id, storage_incarnation=pending['root_incarnation'],
                               content_state_epoch=pending['root_epoch']).update(
                                   plaintext_verified_epoch=pending['root_epoch'])
    transaction.on_commit(lambda: operation.placement.update(root_epoch=pending['root_epoch']))
