"""Bounded cleanup of the live logical-turn journal."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import NamedTuple

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from twicc.core.enums import Provider

logger = logging.getLogger(__name__)

CLOSED_RETENTION = timedelta(hours=48)
OPEN_RETENTION = timedelta(days=7)
CLEANUP_INTERVAL_SECONDS = 3600
CLEANUP_BATCH_SIZE = 500


class PurgeLogicalTurnsJob(NamedTuple):
    future: asyncio.Future
    now: datetime
    limit: int = CLEANUP_BATCH_SIZE
    provider: Provider | None = None


def purge_logical_turns(*, now: datetime, limit: int = CLEANUP_BATCH_SIZE) -> int:
    """Delete one batch under writer admission; count intervals, excluding inputs."""
    from twicc.core.models import LogicalTurn
    from twicc.core.services.timeline_decisions import purge_semantic_decisions, protected_semantic_turns

    if not 1 <= limit <= CLEANUP_BATCH_SIZE:
        raise ValueError("Cleanup batch size must be between 1 and 500")
    with transaction.atomic():
        purge_semantic_decisions(now=now, limit=limit)
        protected = protected_semantic_turns()
        ids = list(LogicalTurn.objects.exclude(pk__in=protected).filter(
            Q(closed_at__lte=now - CLOSED_RETENTION)
            | Q(closed_at__isnull=True, opened_at__lte=now - OPEN_RETENTION)
        ).order_by("opened_at", "pk").values_list("pk", flat=True)[:limit])
        LogicalTurn.objects.filter(pk__in=ids).delete()
        return len(ids)


def apply_purge_logical_turns_job(job: PurgeLogicalTurnsJob) -> int:
    return purge_logical_turns(now=job.now, limit=job.limit)


async def run_logical_turn_cleanup_cycle(stop_event: asyncio.Event) -> int:
    """Drain a fixed-time backlog, yielding writer admission between batches."""
    from twicc.providers.db_writer import submit_async_job

    now = timezone.now()
    total = 0
    while not stop_event.is_set():
        count = await submit_async_job(PurgeLogicalTurnsJob(
            future=asyncio.get_running_loop().create_future(), now=now,
        ))
        total += count
        if count < CLEANUP_BATCH_SIZE:
            break
    return total


async def start_logical_turn_cleanup_task(stop_event: asyncio.Event) -> None:
    """Repeat hourly after the startup drain, retaining retries after failures."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
        except TimeoutError:
            pass
        else:
            break
        try:
            await run_logical_turn_cleanup_cycle(stop_event)
        except Exception:
            logger.exception("Logical turn cleanup cycle failed")
