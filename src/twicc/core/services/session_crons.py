"""Read helpers over the persisted session crons."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from django.db.models import Count

from twicc.core.models import SessionCron


def active_cron_counts(session_ids: Iterable[str]) -> dict[str, int]:
    """Return the number of non-expired crons of each session, in one query.

    Every requested id is a key: a session without active cron maps to ``0``.
    The expiry rule is :meth:`SessionCron.active_q`. The count covers all
    providers, so the caller needs no provider per session.
    """
    now = datetime.now(tz=UTC)
    counts = dict.fromkeys(session_ids, 0)
    counts.update(
        SessionCron.objects
        .filter(SessionCron.active_q(now), session_id__in=counts)
        .values_list("session_id")
        .annotate(Count("id"))
    )
    return counts
