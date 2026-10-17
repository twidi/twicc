"""Scoped SQLite write ownership for content publication."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, transaction
from django.db.transaction import TransactionManagementError


@contextmanager
def immediate_content_transaction() -> Iterator[None]:
    """Reserve the SQLite writer before reading authoritative content state."""
    connection.ensure_connection()
    if connection.in_atomic_block or not connection.get_autocommit():
        raise TransactionManagementError('Content publication requires an outermost autocommit transaction')
    previous_mode = connection.transaction_mode
    try:
        connection.transaction_mode = 'IMMEDIATE'
        with transaction.atomic():
            yield
    finally:
        connection.transaction_mode = previous_mode
