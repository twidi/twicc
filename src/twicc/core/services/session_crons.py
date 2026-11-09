"""Read helpers over the persisted session crons."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from django.db.models import Count, Q

from twicc.core.models import SessionCron


def active_cron_counts(session_ids: Iterable[str]) -> dict[str, int]:
    """Return the number of non-expired crons of each session, in one query.

    Every requested id is a key: a session without active cron maps to ``0``.
    The expiry rule is the one of :meth:`SessionCron.active_for_session`, but
    the count covers all providers, so the caller needs no provider per session.
    """
    now = datetime.now(tz=UTC)
    counts = dict.fromkeys(session_ids, 0)
    counts.update(
        SessionCron.objects
        .filter(session_id__in=counts)
        .filter(
            Q(recurring=True, created_at__gt=now - SessionCron.CLAUDE_RECURRING_MAX_AGE)
            | Q(recurring=False, next_fire__gt=now)
        )
        .values_list("session_id")
        .annotate(Count("id"))
    )
    return counts
