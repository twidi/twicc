"""Fixed-column content writes within the caller's guarded transaction."""

from collections.abc import Sequence

from django.db import connection, transaction

from twicc.core.content_storage.codec import ContentIntegrityError
from twicc.core.models import SessionItem, SessionItemContent


def _write_rows(model, rows, names):
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content writes require a caller-owned transaction')
    fields = [model._meta.get_field(name) for name in names]
    primary = model._meta.pk
    assignments = {}
    for row in rows:
        if row.pk is None:
            raise ContentIntegrityError('Content writes require persisted rows')
        values = tuple(field.get_db_prep_save(getattr(row, field.attname), connection) for field in fields)
        if row.pk in assignments and assignments[row.pk] != values:
            raise ContentIntegrityError('Conflicting content assignments for the same row')
        assignments[row.pk] = values
    if not assignments:
        return
    quote = connection.ops.quote_name
    columns = ', '.join(f'{quote(field.column)} = %s' for field in fields)
    sql = f'UPDATE {quote(model._meta.db_table)} SET {columns} WHERE {quote(primary.column)} = %s'
    parameters = [(*values, primary.get_db_prep_save(pk, connection)) for pk, values in assignments.items()]
    with connection.cursor() as cursor:
        cursor.executemany(sql, parameters)


def write_item_content_assignments(items: Sequence[SessionItem]) -> None:
    """Write both FK slots and legacy content once per unique changed item."""
    _write_rows(SessionItem, items, ('current_content', 'original_content', 'content'))


def write_content_owners(rows: Sequence[SessionItemContent]) -> None:
    """Write the caller's changed owner lists using Django JSON adaptation."""
    _write_rows(SessionItemContent, rows, ('session_ids',))
