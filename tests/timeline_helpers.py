# tests/timeline_helpers.py
"""Shared helpers of the timeline tests."""

import asyncio

from twicc.core.models import TimelineEvent
from twicc.providers import db_writer


def run_with_writer(factory):
    """Run ``await factory()`` with the DB writer (and its write lock) started."""

    async def runner():
        db_writer.start_db_writer()
        try:
            return await factory()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(runner())


def kinds(session_id):
    """Kinds recorded for ``session_id``, in order."""
    return list(
        TimelineEvent.objects.filter(session_id=session_id).order_by("id").values_list("kind", flat=True)
    )
