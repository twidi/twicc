"""Accept titles atomically with Claude observation protection.

File boundaries are captured before entering the write lane. A changed storage
identity restarts capture instead of accepting an incomparable boundary.
"""
import os

from contextvars import ContextVar
from typing import Literal, NamedTuple

from asgiref.sync import sync_to_async
from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import Session
from twicc.core.services.session_timeline import record_title_change
from twicc.providers.claude_code import title_publication
from twicc.providers.claude_code.title_sync import SourceCursor, TitleDecision
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.request_origin import RequestOrigin


class AcceptedTitle(NamedTuple):
    title: str
    changed: bool
    decision: TitleDecision | None


class BoundaryChanged(Exception):
    """Retry preparation outside the admitted write lane."""


_unprepared = object()
_boundary = ContextVar('claude_title_acceptance_boundary', default=_unprepared)


async def capture_title_boundary(session_id):
    """Read a finite complete-record boundary outside the write lane."""
    import asyncio
    from twicc.provider_homes import claude_projects_dir

    row = await Session.objects.filter(pk=session_id).values(
        'file_path', 'storage_incarnation', 'history_epoch', 'last_line', 'last_offset', 'stale',
    ).afirst()
    if row is None or row['stale']:
        return None
    if row['storage_incarnation'] is None:
        def initialize():
            from twicc.core.content_storage.extraction import initialize_compute_session
            initialize_compute_session(session_id)
        await run_under_db_write_lock(lambda: sync_to_async(initialize)())
        return await capture_title_boundary(session_id)
    path = claude_projects_dir() / row['file_path']

    def scan():
        # Count nonempty complete records exactly as ingestion numbers them.
        try:
            with path.open('rb') as stream:
                initial = os.fstat(stream.fileno())
                size = initial.st_size
                stream.seek(0)
                lines = 0
                while stream.tell() < size:
                    record = stream.readline(size - stream.tell())
                    if not record.endswith(b'\n'):
                        break
                    if record.strip():
                        lines += 1
                final = path.stat()
                if (initial.st_dev, initial.st_ino) != (final.st_dev, final.st_ino) or final.st_size < size:
                    raise BoundaryChanged
                return lines
        except FileNotFoundError:
            return None
    lines = await asyncio.to_thread(scan)
    if lines is None:
        # New SDK sessions must dispatch before their first transcript exists.
        return None
    return SourceCursor(session_id, row['storage_incarnation'], row['history_epoch'], lines)


async def accept_title_locked(
    session_id: str, title: str, *, source: Literal['auto', 'user'],
    origin: RequestOrigin | None = None, extra_updates: dict | None = None,
) -> AcceptedTitle:
    """Accept within the caller's lane, using its externally captured boundary."""
    updates = dict(extra_updates or {})
    if updates.keys() - {'title_check_count', 'title_checked_at'}:
        raise ValueError('Unsupported title updates')
    if updates and source != 'auto':
        raise ValueError('Only automatic checks can update title counters')

    def persist():
        session = Session.objects.get(pk=session_id)
        claude = session.provider == Provider.CLAUDE_CODE
        canonical = title
        if claude:
            from twicc.providers.claude_code.title_control import canonicalize_claude_title
            validation = canonicalize_claude_title(title)
            if validation.error:
                raise ValueError(validation.error)
            canonical = validation.title
        with title_publication.title_state_transaction():
            decision = None
            if claude and not session.stale:
                boundary = _boundary.get()
                if boundary is _unprepared:
                    raise RuntimeError('Capture a Claude title boundary before entering the write lane')
                if boundary is not None and not title_publication.protect_claude_acceptance_boundary_locked(boundary):
                    raise BoundaryChanged
                decision = title_publication.get_title_coordinator().accept_decision(session_id, canonical)
            changed = session.title != canonical
            Session.objects.filter(pk=session_id).update(title=canonical, title_origin=source, **updates)
            if changed:
                record_title_change(session_id, canonical, source=source, old=session.title,
                                    old_origin=session.title_origin, origin=origin)
            if decision is not None:
                transaction.on_commit(lambda: _publish_committed(decision))
            return AcceptedTitle(canonical, changed, decision)
    return await sync_to_async(persist)()


def _publish_committed(decision):
    from twicc.pending_titles import pop_pending_title
    pop_pending_title(decision.session_id)
    # Compute and offline tools also accept titles before the manager starts.
    # The next managed attachment reconciles that process-local pending choice.
    try:
        title_publication.request_publication(decision)
    except RuntimeError:
        pass


async def with_title_boundary(session_id, operation):
    """Prepare then execute an operation which acquires the write lane itself."""
    while True:
        worker = title_publication._worker
        if worker is not None and not worker._closed:
            await worker.hooks.ingest_available(session_id)
        try:
            boundary = await capture_title_boundary(session_id)
        except BoundaryChanged:
            continue
        token = _boundary.set(boundary)
        try:
            return await operation()
        except BoundaryChanged:
            continue
        finally:
            _boundary.reset(token)


async def accept_explicit_title(session_id: str, title: str, *, origin: RequestOrigin | None = None) -> AcceptedTitle:
    async def apply():
        return await run_under_db_write_lock(lambda: accept_title_locked(session_id, title, source='user', origin=origin))
    return await with_title_boundary(session_id, apply)
