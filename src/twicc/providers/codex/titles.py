"""
Codex title management.

Async helpers for reading and setting Codex thread names via the Codex SDK,
plus the DB writer async-queue job that imports a freshly-read catalogue
into the local ``Session`` rows.

Unlike Claude Code, Codex stores thread metadata in its own state DB
(single source of truth), so there is no anti-stale-write protection
(no ``protect_title`` machinery) — the matching JSONL rollout file never
carries the title in the first place.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from openai_codex import AsyncCodex
from django.db import transaction

from twicc.core.enums import Provider

from .bin import make_codex_config
from .sdk_wrappers import TwiccAsyncCodex

logger = logging.getLogger(__name__)


class SyncSessionTitlesJob(NamedTuple):
    """Async-queue job: bulk title import for Codex sessions.

    Codex-specific (routed through :meth:`CodexHelpers.try_handle_async_job`).
    Pushed onto :attr:`db_writer._async_queue` via :func:`submit_async_job`
    by the orchestrator's boot-time title sync, after the initial-sync
    drain has completed (the orchestrator awaits the initial-sync done
    future before submitting this).

    ``provider`` is fixed (this job is Codex-only) but kept so the job
    follows the same convention as every other async-queue job — the DB
    writer reads it for log tagging.

    ``future`` resolves to the list of serialized sessions whose title
    actually changed (``list[dict]`` from ``serialize_session``). The
    helper that handles the job runs the sync apply via
    ``_settle_async_job``, then broadcasts ``session_updated`` for each
    changed session as a post-apply async side effect.
    """

    titles: dict[str, str]
    future: asyncio.Future  # → list[dict]
    provider: Provider = Provider.CODEX


def _apply_sync_session_titles_job(job: SyncSessionTitlesJob) -> list[dict]:
    """Apply the title map; return the serialized sessions whose title changed.

    Sync — runs inside ``transaction.atomic`` on a worker thread via
    ``run_compute_sync`` inside :func:`db_writer._settle_async_job`. Reads
    the Codex sessions named in the map, updates the rows whose title
    actually differs, and returns them serialised for ``session_updated``.
    Only conditional NULL transitions stamp the automatic origin.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    with transaction.atomic():
        sessions = list(Session.objects.filter(
            provider=job.provider, id__in=list(job.titles.keys()),
        ))
        changed: list = []
        for session in sessions:
            new_title = job.titles.get(session.id)
            if new_title and session.title != new_title:
                stamped = Session.objects.filter(id=session.id, title__isnull=True).update(
                    title=new_title, title_origin="auto",
                )
                if not stamped:
                    Session.objects.filter(id=session.id).update(title=new_title)
                session.refresh_from_db(fields=["title", "title_origin"])
                changed.append(session)
    return [serialize_session(s) for s in changed if not s.hidden]


async def _broadcast_changed_titles(changed: list[dict]) -> None:
    """Broadcast ``session_updated`` for each changed session.

    Async post-apply side effect: runs after :func:`_apply_sync_session_titles_job`
    has committed and the DB writer's future has been settled with the
    list of changed sessions. One WS broadcast per session — could be
    batched later if the volume ever becomes a problem.
    """
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    for session_data in changed:
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "session_updated",
                "session": session_data,
            }},
        )


async def rename_thread_via_sdk(thread_id: str, title: str) -> None:
    """Set the Codex thread's display name via ``thread/name/set``.

    Spawns a short-lived ``TwiccAsyncCodex`` transport (one initialize + one
    ``thread/name/set`` RPC). The app-server applies that request directly to
    the metadata store, so loading the rollout with ``thread/resume`` first is
    unnecessary. Cannot piggy-back on an existing ``AsyncCodex`` because the
    SDK only allows one streamed turn consumer at a time across the whole
    client (see the ``_active_turn_consumer`` guard in
    ``openai_codex.client``): sharing it with an active agent would either
    starve the agent or fail-fast on a name update.

    Raises on failure so the caller can decide how to react. The HTTP
    rename endpoint currently swallows the error (the DB title is
    already updated; the watcher / next session reload will reconcile).
    """
    config = await make_codex_config()
    try:
        async with TwiccAsyncCodex(config=config) as codex:
            await codex.thread_set_name(thread_id, title)
    except Exception as e:
        logger.warning("Codex thread/name/set failed for %s: %s", thread_id, e)
        raise


async def read_title_from_codex(thread_id: str) -> str | None:
    """Read the Codex thread's persisted display name from the state DB.

    Walks the same ``thread/list`` paginated endpoint as
    :func:`bulk_sync_titles_from_codex` but stops as soon as the target
    ``thread_id`` is found. ``use_state_db_only=True`` makes the binary
    answer straight from the state DB without scanning JSONL rollouts
    or returning the live in-memory thread name — important when we
    want the *durable* value rather than what the active session has in
    memory.

    Why not ``thread_resume + thread.read``? That path returns the
    in-memory thread state (which may still hold the value we set via
    ``thread/name/set``), even when the Codex binary has already
    re-flushed the row in the state DB from an in-memory candidate
    derived from ``first_user_message``. The verify task uses this
    function precisely to detect that divergence — it must see what
    will survive a TwiCC restart (i.e. the state-DB value).

    Returns ``None`` if the thread is not found, has no name, or on
    any error (logged at WARNING).
    """
    config = await make_codex_config()
    try:
        async with AsyncCodex(config=config) as codex:
            cursor: str | None = None
            while True:
                page = await codex.thread_list(
                    use_state_db_only=True,
                    cursor=cursor,
                    limit=100,
                )
                for thread in page.data:
                    if thread.id == thread_id:
                        return thread.name if thread.name else None
                if page.next_cursor is None:
                    return None
                cursor = page.next_cursor
    except Exception as e:
        logger.warning("Codex thread/list failed while reading title for %s: %s", thread_id, e)
        return None


async def bulk_sync_titles_from_codex() -> dict[str, str]:
    """Read every Codex thread's display name via state-DB-only list.

    Returns a ``{thread_id: name}`` mapping (only entries with a non-empty
    name are included). Returns an empty dict on error (logged at WARNING).
    Pagination is exhaustive — there is no upper bound on the number of
    threads, but the call is cheap because ``use_state_db_only=True``
    skips JSONL rollout scanning.
    """
    config = await make_codex_config()
    titles: dict[str, str] = {}
    try:
        async with AsyncCodex(config=config) as codex:
            cursor: str | None = None
            while True:
                page = await codex.thread_list(
                    use_state_db_only=True,
                    cursor=cursor,
                    limit=100,
                )
                for thread in page.data:
                    if thread.name:
                        titles[thread.id] = thread.name
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
    except Exception as e:
        logger.warning("Codex bulk thread/list failed: %s", e)
    return titles
