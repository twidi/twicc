"""Batched recording of refused broker fetches (design 2026-07-10 §4).

``note_denial`` coalesces viewer refusals in memory (the share proxy's
``not_allowed`` path can be hammered by an artifact's retry loop); a 30 s flush
task upserts ``ArtifactNetworkDenial`` rows, prunes, and pings open dialogs.
``record_owner_denial`` is the direct-write path for the owner's prompt "Deny"
(one event per human click — no batching needed). Same coalescing philosophy as
``share.view_tracking``."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 30
_MAX_DENIAL_ROWS = 500

# (bookmark_id, share_id|None, host_key, ip, user_agent) -> {"kind": str, "count": int}
_pending: dict[tuple[int, str | None, str, str, str], dict] = {}


def note_denial(*, bookmark_id: int, share_id: str | None, host_key: str, kind: str,
                ip: str = "", user_agent: str = "") -> None:
    """Record one refused fetch in memory (no I/O)."""
    key = (bookmark_id, share_id, host_key, ip[:64], (user_agent or "")[:255])
    entry = _pending.setdefault(key, {"kind": kind, "count": 0})
    entry["kind"] = kind  # latest resolved kind wins
    entry["count"] += 1


def _drain():
    snapshot = dict(_pending)
    _pending.clear()
    return snapshot


def _persist(snapshot) -> set[int]:
    """Blocking DB work — run under ``run_under_db_write_lock`` (mirrors
    ``view_tracking._persist``). Upserts rows, drops entries for
    now-allowed hosts (the allow purge already removed their rows — design
    §4.1) and unknown bookmarks, prunes per bookmark. Returns the bookmark ids
    whose rows changed."""
    from twicc.core.models import ArtifactBookmark, ArtifactNetworkDenial

    updated: set[int] = set()
    allowed_cache: dict[int, set[str] | None] = {}  # id -> allowed keys, None = missing
    for (bookmark_id, share_id, host_key, ip, user_agent), entry in snapshot.items():
        if bookmark_id not in allowed_cache:
            bm = ArtifactBookmark.objects.filter(id=bookmark_id).only("allowed_hosts").first()
            allowed_cache[bookmark_id] = set((bm.allowed_hosts or {}).keys()) if bm else None
        allowed = allowed_cache[bookmark_id]
        if allowed is None or host_key in allowed:
            continue
        row = ArtifactNetworkDenial.objects.filter(
            bookmark_id=bookmark_id, share_id=share_id, host_key=host_key,
            ip=ip, user_agent=user_agent,
        ).first()
        if row is None:
            ArtifactNetworkDenial.objects.create(
                bookmark_id=bookmark_id, share_id=share_id, host_key=host_key,
                kind=entry["kind"], ip=ip, user_agent=user_agent, count=entry["count"],
            )
        else:
            row.count += entry["count"]
            row.kind = entry["kind"]
            row.save(update_fields=["count", "kind", "last_at"])
        updated.add(bookmark_id)
    for bookmark_id in updated:
        count = ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id).count()
        if count > _MAX_DENIAL_ROWS:
            keep_ids = list(
                ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id)
                .order_by("-last_at", "-id").values_list("id", flat=True)[:_MAX_DENIAL_ROWS]
            )
            ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id).exclude(id__in=keep_ids).delete()
    return updated


async def _persist_under_write_lock(snapshot) -> set[int]:
    from asgiref.sync import sync_to_async
    from twicc.providers.db_writer import run_under_db_write_lock

    return await run_under_db_write_lock(lambda: sync_to_async(_persist)(snapshot))


async def record_owner_denial(*, bookmark, url: str, kind: str) -> None:
    """Direct write for one owner-preview "Deny" (share=None, no ip/ua). Raises
    ``ValueError`` for a bad kind or a bad scheme (via ``normalize_host_key``)."""
    from twicc.artifacts.proxy import normalize_host_key
    from twicc.core.services.artifact_bookmark_mutation import (
        _DENIABLE_KINDS,
        broadcast_artifact_network_denials_updated,
    )

    if kind not in _DENIABLE_KINDS:
        raise ValueError(f"kind must be one of {_DENIABLE_KINDS}; got {kind!r}")
    host_key = normalize_host_key(url)
    snapshot = {(bookmark.id, None, host_key, "", ""): {"kind": kind, "count": 1}}
    updated = await _persist_under_write_lock(snapshot)
    if updated:
        await broadcast_artifact_network_denials_updated(bookmark.id)


async def start_denial_flush_task(stop_event: asyncio.Event) -> None:
    """Flush pending denials every ``_FLUSH_INTERVAL`` s. Started in run_server."""
    from twicc.core.services.artifact_bookmark_mutation import (
        broadcast_artifact_network_denials_updated,
    )

    logger.info("Artifact denial-tracking flush task started (every %ss)", _FLUSH_INTERVAL)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_FLUSH_INTERVAL)
        except TimeoutError:
            pass
        else:
            break
        snapshot = _drain()
        if not snapshot:
            continue
        try:
            updated = await _persist_under_write_lock(snapshot)
            for bookmark_id in updated:
                await broadcast_artifact_network_denials_updated(bookmark_id)
        except Exception:  # noqa: BLE001 — keep the loop alive
            for key, entry in snapshot.items():
                pending = _pending.setdefault(key, {"kind": entry["kind"], "count": 0})
                pending["count"] += entry["count"]
            logger.warning("Artifact denial flush failed (re-queued)", exc_info=True)
