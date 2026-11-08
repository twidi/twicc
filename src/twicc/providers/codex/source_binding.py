"""Writer-owned publication of validated Codex source associations.

Producers validate metadata before constructing an envelope. Its fingerprint
binds that identity evidence to the selected physical source. Publication only
checks bounded source identity and DB checkpoints; it never decodes history.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider
from twicc.provider_homes import codex_sessions_dir
from twicc.providers.db_writer import (
    CreateSessionPayload, MarkSessionsStalePayload, UpdateSessionPayload,
    _UNPREPARED_INGESTION,
)
from .rollout_sources import SourceFingerprint, logical_rollout_path, resolve_rollout, source_is_current


class SourceCheckpoint(NamedTuple):
    last_offset: int
    last_line: int
    history_epoch: int
    storage_incarnation: str | None


class CodexSourcePayload(NamedTuple):
    provider: Provider
    session_id: str
    physical_path: str
    fingerprint: SourceFingerprint
    expected: SourceCheckpoint | None
    ingestion: CreateSessionPayload | UpdateSessionPayload | None


class ReconcileCodexSourceJob(NamedTuple):
    provider: Provider
    payload: CodexSourcePayload
    future: asyncio.Future


class MarkCodexSourcesMissingPayload(NamedTuple):
    provider: Provider
    sources: list[tuple[str, str]]


class _SourceApplication(NamedTuple):
    applied: bool
    project: object = None
    created: bool = False
    adopted: bool = False
    availability_changed: bool = False
    retry: bool = False


def source_checkpoint(session) -> SourceCheckpoint:
    return SourceCheckpoint(session.last_offset, session.last_line, session.history_epoch, session.storage_incarnation)


def _apply_codex_source_payload(payload: CodexSourcePayload, *, prepared=_UNPREPARED_INGESTION) -> _SourceApplication:
    from twicc.core.models import Project, Session
    from twicc.providers.db_writer import (
        _apply_create_session_payload, _apply_update_session_payload, _compute_project_mtime,
    )
    from twicc.providers.compute_work import notify_committed_source_changed
    from twicc.providers.live_aggregates import apply_contribution_changes, session_contribution

    rejected = _SourceApplication(False)
    obsolete = _SourceApplication(False, retry=True)
    if payload.provider != Provider.CODEX:
        return rejected
    physical = Path(payload.physical_path)
    if physical.is_absolute() or ".." in physical.parts:
        return rejected
    if payload.fingerprint.physical_path != codex_sessions_dir() / physical:
        return rejected
    if not source_is_current(payload.fingerprint):
        return obsolete
    ingestion = payload.ingestion
    if ingestion is not None and (ingestion.provider != Provider.CODEX or ingestion.session.id != payload.session_id):
        return rejected
    with transaction.atomic():
        session = Session.objects.filter(pk=payload.session_id).first()
        if payload.expected is None:
            if session is not None:
                if (session.provider == Provider.CODEX
                        and logical_rollout_path(Path(session.file_path)) == logical_rollout_path(physical)):
                    return obsolete
                return rejected
            if not isinstance(ingestion, CreateSessionPayload):
                return rejected
            if ingestion.session.file_path != payload.physical_path:
                return rejected
        else:
            if session is None:
                return obsolete
            if (session.provider != Provider.CODEX
                    or logical_rollout_path(Path(session.file_path)) != logical_rollout_path(physical)):
                return rejected
            if source_checkpoint(session) != payload.expected:
                return obsolete
        if ingestion is not None and payload.expected is not None:
            if ingestion.last_offset < payload.expected.last_offset or ingestion.last_line < payload.expected.last_line:
                return rejected
        availability_changed = session is not None and (session.stale or session.unavailable_reason == "rollout_missing")
        project, created, adopted = None, False, False
        if isinstance(ingestion, CreateSessionPayload):
            project, created, adopted = _apply_create_session_payload(ingestion, prepared=prepared)
            if project is None:
                return rejected
            session = ingestion.session
        elif isinstance(ingestion, UpdateSessionPayload):
            _apply_update_session_payload(ingestion, prepared=prepared)
            session = ingestion.session
        before = session_contribution(session)
        session.file_path = payload.physical_path
        fields = ["file_path"]
        source_mtime = payload.fingerprint.mtime_ns / 1_000_000_000
        mtime_changed = ingestion is None and session.mtime != source_mtime
        if mtime_changed:
            session.mtime = source_mtime
            fields.append("mtime")
        if session.stale:
            session.stale = False
            fields.append("stale")
        if session.unavailable_reason == "rollout_missing":
            session.unavailable_reason = None
            fields.append("unavailable_reason")
        session.save(update_fields=fields)
        if availability_changed:
            apply_contribution_changes([], [], before_sessions=[before], after_sessions=[session_contribution(session)])
            transaction.on_commit(lambda: notify_committed_source_changed(Provider.CODEX, session.id))
        if availability_changed or mtime_changed:
            Project.objects.filter(pk=session.project_id).update(mtime=_compute_project_mtime(session.project_id))
    return _SourceApplication(True, project, created, adopted, availability_changed)


def apply_codex_source_payload(payload: CodexSourcePayload) -> bool:
    """Apply an internal, producer-validated envelope under writer ownership."""
    return _apply_codex_source_payload(payload).applied


def apply_mark_codex_sources_missing_payload(payload: MarkCodexSourcesMissingPayload) -> list[str]:
    """Mark only unchanged associations with both representations absent."""
    from twicc.core.models import Session
    from twicc.providers.db_writer import _apply_mark_sessions_stale_payload

    if payload.provider != Provider.CODEX:
        return []
    absent = []
    with transaction.atomic():
        for session_id, expected_path in payload.sources:
            session = Session.objects.filter(pk=session_id, provider=Provider.CODEX, file_path=expected_path).first()
            if session is None:
                continue
            try:
                resolve_rollout(codex_sessions_dir() / expected_path)
            except FileNotFoundError:
                absent.append(session_id)
            except OSError:
                # Unreadability is not evidence of deletion.
                continue
        _apply_mark_sessions_stale_payload(MarkSessionsStalePayload(Provider.CODEX, absent))
    return absent


async def request_selected_source_catch_up(payload: CodexSourcePayload) -> None:
    """Return obsolete evidence to the watcher's ordinary selected-source queue."""
    from watchfiles import Change
    from .sessions_watcher import get_watcher

    watcher = get_watcher()
    # Startup rejection can precede watcher creation and search readiness.
    # Reopen a drained lifecycle without starting its consumer prematurely.
    watcher.prepare_start()
    logical = logical_rollout_path(codex_sessions_dir() / payload.physical_path)
    watcher._queue.enqueue(logical, Change.modified)
    if watcher._channel_layer is not None or watcher._consumer_task is not None:
        watcher._ensure_consumer()


async def process_codex_source_payload(payload: CodexSourcePayload, *, prepared=_UNPREPARED_INGESTION) -> bool:
    """Apply an envelope and retain ordinary post-commit publication effects."""
    from twicc.providers import db_writer

    result = await db_writer.run_compute_sync(_apply_codex_source_payload, payload, prepared=prepared)
    if not result.applied:
        if result.retry:
            await request_selected_source_catch_up(payload)
        return False
    if result.project is not None:
        await db_writer._publish_created_project(result.project, result.created, result.adopted)
    if result.availability_changed:
        await db_writer.broadcast_session_updated(payload.session_id)
    return True
