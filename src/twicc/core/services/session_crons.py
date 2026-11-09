"""Read helpers over the persisted session crons."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from django.db.models import Count, Q

from twicc.core.models import SessionCron


def active_cron_counts(session_ids: Iterable[str]) -> dict[str, int]:
    """Return the number of crons that archiving each session would destroy, in one query.

    Every requested id is a key: a session without such a cron maps to ``0``.
    A recurring cron counts at any age: past the CLI's 7-day window TwiCC recreates it,
    so it is alive for the user (same rule as ``_split_restorable_crons`` in
    ``providers/claude_code/cron_restart.py``, unlike :meth:`SessionCron.active_q`, which
    describes what the CLI still holds). A one-shot cron counts until its fire time. The
    count covers all providers, so the caller needs no provider per session.
    """
    now = datetime.now(tz=UTC)
    counts = dict.fromkeys(session_ids, 0)
    counts.update(
        SessionCron.objects
        .filter(Q(recurring=True) | Q(recurring=False, next_fire__gt=now), session_id__in=counts)
        .values_list("session_id")
        .annotate(Count("id"))
    )
    return counts
