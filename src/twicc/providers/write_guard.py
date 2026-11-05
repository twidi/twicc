"""Report SQL writes issued outside the DB writer lock.

Every application write must run under the write coordinator: SQLite accepts
one writer at a time, and a writer outside the coordinator makes the others
fail with "database is locked". The rule cannot be enforced by reading the
code, so this wrapper observes every statement while the DB writer runs and
logs each offending call site once. It never blocks a write.

Import discipline: this module is imported in every process through
``CoreConfig.ready``, the spawn subprocess included, so it stays stdlib-only.
``db_writer`` hands it the ownership check when it starts.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = f"{Path(__file__).resolve().parents[1]}/"
_WRITE = re.compile(
    r'\s*(?:(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)'
    r'\s+(?:"?(temp)"?\.)?"?(\w+)',
    re.IGNORECASE,
)
# Django owns its session writes. TEMP tables live outside the database file.
_EXEMPT_TABLES = ('django_session',)
_UNQUALIFIED_TEMP_TABLES = ('content_reference_counts_',)

_lock_is_held: Callable[[], bool] | None = None
_reported: set[tuple[str, str]] = set()


def activate(lock_is_held: Callable[[], bool]) -> None:
    """Start reporting for one DB writer lifetime."""
    global _lock_is_held
    _reported.clear()
    _lock_is_held = lock_is_held


def deactivate() -> None:
    global _lock_is_held
    _lock_is_held = None


def written_table(sql: str) -> str | None:
    """Return the table a statement writes to, or None if it needs no lock."""
    match = _WRITE.match(sql)
    if match is None or match[1] is not None:
        return None
    table = match[2]
    if table in _EXEMPT_TABLES or table.startswith(_UNQUALIFIED_TEMP_TABLES):
        return None
    return table


def _call_site() -> str:
    """Return the innermost TwiCC frame outside this module."""
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith(_PACKAGE_ROOT) and filename != __file__:
            return f'{filename.removeprefix(_PACKAGE_ROOT)}:{frame.f_lineno}'
        frame = frame.f_back
    return 'unknown'


def report_unlocked_writes(execute, sql, params, many, context):
    """Django execute wrapper: observe the statement, then run it unchanged."""
    lock_is_held = _lock_is_held
    if lock_is_held is not None and (table := written_table(sql)) is not None and not lock_is_held():
        key = (_call_site(), table)
        if key not in _reported:
            _reported.add(key)
            logger.error('SQL write outside the DB writer lock: table=%s site=%s', table, key[0], stack_info=True)
    return execute(sql, params, many, context)


def install(*, connection, **_) -> None:
    """``connection_created`` receiver.

    The wrapper goes first: ``connection.execute_wrapper()`` pops the last
    entry, and a connection can open inside such a block.
    """
    if report_unlocked_writes not in connection.execute_wrappers:
        connection.execute_wrappers.insert(0, report_unlocked_writes)
