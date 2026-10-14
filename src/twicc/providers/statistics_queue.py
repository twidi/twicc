"""Durable root invalidations owned by the source writer transaction."""

import asyncio
from collections.abc import Callable, Iterable
from typing import NamedTuple

from django.db import connection, transaction
from django.db.models import Max

from twicc.core.models import StatisticsQueueEntry


class QueueBatch(NamedTuple):
    max_id: int
    root_ids: tuple[str, ...]
    force_cost_session_ids: tuple[str, ...] = ()


_wakeup: tuple[asyncio.AbstractEventLoop, Callable[[], None]] | None = None


def install_queue_wakeup(callback: Callable[[], None] | None) -> None:
    """Bind the consumer callback on its owning main-process event loop."""
    global _wakeup
    _wakeup = (asyncio.get_running_loop(), callback) if callback is not None else None


def _notify_consumer() -> None:
    binding = _wakeup
    if binding is not None:
        loop, callback = binding
        if not loop.is_closed():
            loop.call_soon_threadsafe(callback)


def append_roots(root_ids: Iterable[str], *, force_cost_session_ids: Iterable[str] = ()) -> None:
    """Append once per root in this call, atomically with the source writes."""
    if not connection.in_atomic_block:
        raise RuntimeError('Statistics queue append requires the source writer transaction')
    roots = tuple(dict.fromkeys(root_ids))
    if not roots:
        return
    forced = sorted(set(force_cost_session_ids))
    StatisticsQueueEntry.objects.bulk_create(
        [StatisticsQueueEntry(root_session_id=root_id, force_cost_session_ids=forced) for root_id in roots], batch_size=500,
    )
    transaction.on_commit(_notify_consumer)


def capture_queue() -> QueueBatch | None:
    """Capture a boundary and its roots in one read snapshot."""
    with transaction.atomic():
        max_id = StatisticsQueueEntry.objects.aggregate(max_id=Max('id'))['max_id']
        if max_id is None:
            return None
        rows = StatisticsQueueEntry.objects.filter(id__lte=max_id).order_by('id').values_list(
            'root_session_id', 'force_cost_session_ids',
        )
        roots, forced = {}, set()
        for root, session_ids in rows.iterator():
            roots[root] = None
            forced.update(session_ids or ())
        return QueueBatch(max_id, tuple(roots), tuple(sorted(forced)))


def acknowledge_queue(max_id: int) -> None:
    """Remove only captured IDs inside the consumer publication transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError('Statistics queue acknowledgement requires the writer transaction')
    StatisticsQueueEntry.objects.filter(id__lte=max_id).delete()
