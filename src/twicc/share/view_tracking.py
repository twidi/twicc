"""Batched share view tracking (design §13). ``note_view`` records an in-memory
touch on the page path; a 30s flush task persists counters + ``ShareAccess`` rows,
prunes, broadcasts ``share_updated``, and fires the optional external notification.
Same coalescing philosophy as ``auth.tokens`` last-used flushing."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 30
# share_id -> list[(at_iso, ip, user_agent)]
_pending: dict[str, list[tuple[str, str, str]]] = {}
_MAX_ACCESS_ROWS = 500


def _client_ip(request) -> str:
    from twicc.auth.views import _get_client_ip
    return _get_client_ip(request)


def note_view(share, request) -> None:
    """Record a page view in memory (no I/O). Called after the password check."""
    at = datetime.now(tz=UTC).isoformat()
    ua = (request.headers.get("User-Agent") or "")[:255]
    _pending.setdefault(share.id, []).append((at, _client_ip(request), ua))


def _drain() -> dict[str, list[tuple[str, str, str]]]:
    snapshot = {k: v for k, v in _pending.items() if v}
    _pending.clear()
    return snapshot


def _persist(snapshot: dict[str, list[tuple[str, str, str]]]) -> list[str]:
    """Blocking DB work — run under ``run_under_db_write_lock``. Returns share ids updated."""
    from twicc.core.models import Share, ShareAccess

    updated: list[str] = []
    for share_id, views in snapshot.items():
        share = Share.objects.filter(id=share_id).first()
        if share is None:
            continue
        share.view_count = (share.view_count or 0) + len(views)
        last_iso = max(v[0] for v in views)
        share.last_viewed_at = datetime.fromisoformat(last_iso)
        share.save(update_fields=["view_count", "last_viewed_at", "updated_at"])
        ShareAccess.objects.bulk_create([
            ShareAccess(share_id=share_id, ip=ip[:64], user_agent=ua) for (_at, ip, ua) in views
        ])
        # Prune to the newest _MAX_ACCESS_ROWS. Keep the top-N by a TOTAL order
        # ``(-at, -id)`` then delete the rest by id — robust to ``at`` ties (a whole
        # flush's rows share the same auto_now_add timestamp, so a timestamp-cutoff
        # prune would over-delete).
        count = ShareAccess.objects.filter(share_id=share_id).count()
        if count > _MAX_ACCESS_ROWS:
            keep_ids = list(
                ShareAccess.objects.filter(share_id=share_id)
                .order_by("-at", "-id").values_list("id", flat=True)[:_MAX_ACCESS_ROWS]
            )
            ShareAccess.objects.filter(share_id=share_id).exclude(id__in=keep_ids).delete()
        updated.append(share_id)
    return updated


# Notification throttle: share_id -> (last_sent_monotonic, suppressed_count)
_notify_state: dict[str, tuple[float, int]] = {}
_NOTIFY_THROTTLE_SECONDS = 3600


def _share_descriptor(share) -> str:
    """Owner-facing handle for the 'share viewed' copy: ``session share 'Title' (label)``.

    The reader is the *creator*, so the ``show_title`` option — which only hides the
    title from viewers — is ignored, and the real target title wins over the public
    ``display_title`` override (it is what the owner recognises; the override is the
    fallback when the target has no title). The private label is appended when set, to
    tell two links to the same object apart. Neither ⇒ the raw share id.

    Requires ``share`` to carry its target relation already loaded (``select_related``):
    a lazy FK load here would run a sync query inside the async flush task.
    """
    from twicc.core.enums import ShareKind
    from twicc.external_notifications import _truncate

    is_session = share.kind == ShareKind.SESSION.value
    if is_session:
        target = (share.session.title if share.session else "") or ""
    else:
        bookmark = share.artifact_bookmark
        target = ((bookmark.name or bookmark.relative_path) if bookmark else "") or ""
    title = target.strip() or ((share.options or {}).get("display_title") or "").strip()
    # A bookmark falls back to its relative path, which is unbounded — keep the copy short.
    title = _truncate(title, 80, "")
    label = (share.label or "").strip()
    if title and label:
        name = f"'{title}' ({label})"
    else:
        name = f"'{title or label or share.id}'"
    return f"{'session' if is_session else 'artifact'} share {name}"


async def _maybe_notify(share) -> None:
    """Fire a 'share viewed' external notification (first view, then ≤1/hour)."""
    import time

    from asgiref.sync import sync_to_async
    from twicc.external_notifications import _send  # reuse the Apprise send path
    from twicc.synced_settings import read_synced_settings

    if share is None or not share.notify_on_view:
        return
    share_id = share.id
    settings = await sync_to_async(read_synced_settings)()
    targets = [t for t in settings.get("externalNotificationTargets") or []
               if isinstance(t, dict) and t.get("enabled") and t.get("url") and t.get("tested") is True]
    if not targets:
        return
    now = time.monotonic()
    last, suppressed = _notify_state.get(share_id, (0.0, 0))
    if last and now - last < _NOTIFY_THROTTLE_SECONDS:
        _notify_state[share_id] = (last, suppressed + 1)
        return
    extra = f" ({suppressed} more views since the last alert)" if suppressed else ""
    _notify_state[share_id] = (now, 0)
    await _send([t["url"] for t in targets], "Share viewed",
                f"Your {_share_descriptor(share)} was viewed.{extra}")


async def start_share_view_flush_task(stop_event: asyncio.Event) -> None:
    """Flush pending share views every _FLUSH_INTERVAL s. Started in run_server."""
    from twicc.core.services.share_mutation import broadcast_share_updated

    logger.info("Share view-tracking flush task started (every %ss)", _FLUSH_INTERVAL)
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
            from asgiref.sync import sync_to_async
            from twicc.core.models import Share
            from twicc.providers.db_writer import run_under_db_write_lock

            updated = await run_under_db_write_lock(lambda views=snapshot: sync_to_async(_persist)(views))
            for share_id in updated:
                share = await sync_to_async(
                    lambda sid=share_id: Share.objects.select_related(
                        "session", "artifact_bookmark", "created_by_session",
                    ).filter(id=sid).first()
                )()
                if share is not None:
                    await broadcast_share_updated(share)
                    await _maybe_notify(share)
        except Exception:  # noqa: BLE001 — keep the loop alive
            for share_id, views in snapshot.items():
                _pending.setdefault(share_id, []).extend(views)
            logger.warning("Share view flush failed (re-queued)", exc_info=True)
