"""Boot-time cleanup of stale :class:`ProcessRun` rows across providers.

Runs once at TwiCC startup, before any provider's agent-restart logic. The
in-process registry is empty at that point, so every persisted ``ProcessRun``
necessarily belongs to a previous TwiCC instance — its persisted ``state``
column is stale (still ``ASSISTANT_TURN`` / ``USER_TURN`` / ``STARTING`` if
the previous process died mid-flight). The cleanup consolidates the table
in three steps inside one ``transaction.atomic`` block so the DB never
exposes a half-cleaned state to concurrent readers:

1. Force every non-``DEAD`` row into ``DEAD`` with a fresh
   ``last_state_change_at``. Bulk ``UPDATE``, scoped to the lifecycle column
   so other writers' decisions remain correct.
2. Group surviving rows by ``(provider, session_id)``; for each session,
   keep the oldest row by ``started_at`` and DELETE the rest (cascade
   deletes their crons). The dedup matters mostly for Claude Code, where
   a crash mid-cron-restart can leave both the original cron-bearing row
   and a fresh restart attempt; for providers without that pattern the
   group always has size 1, so the loop is a no-op.
3. For the surviving row, defer to the provider's
   :meth:`BaseProviderHelpers.should_keep_dead_process_run`. Default
   helpers return ``False`` (every other provider), Claude Code returns
   ``True`` iff there is still at least one :class:`SessionCron` attached
   — those are the rows the cron-restart loop will pick up downstream.

The cleanup runs through the DB writer (single-writer path) via
:func:`submit_async_job`, mirroring the convention of
:class:`twicc.providers.db_writer._MarkSessionsIndexedJob`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context

logger = logging.getLogger(__name__)


class CleanupStaleProcessRunsJob(NamedTuple):
    """Async-queue job that runs :func:`_apply_cleanup_stale_process_runs_job`.

    Cross-provider (``provider = None``): the apply iterates every
    provider's helper to make the keep/delete decision per surviving row.
    ``future`` resolves to the total number of rows deleted (read+write
    inside one ``transaction.atomic``).
    """

    future: asyncio.Future  # → int (rows deleted)
    provider: Provider | None = None


def _apply_cleanup_stale_process_runs_job(
    job: CleanupStaleProcessRunsJob,
) -> int:
    """Run :func:`_cleanup_stale_process_runs` inside ``transaction.atomic``.

    Sync — runs on a worker thread through
    :func:`db_writer._settle_async_job`. The single ``transaction.atomic``
    guarantees the bulk state UPDATE, the per-session dedup DELETE, and
    the per-row helper-driven DELETE all commit together; observers can
    not see a halfway-cleaned table.
    """
    from twicc.core.services.timeline_boot import reconcile_at_boot

    # The timeline must never block the cleanup: its own atomic block rolls back alone.
    # It runs first, so the stale rows still carry their previous state.
    try:
        with transaction.atomic():
            reconcile_at_boot()
    except Exception:
        logger.exception("Timeline boot reconciliation failed")
    with transaction.atomic():
        return _cleanup_stale_process_runs()


def _cleanup_stale_process_runs() -> int:
    """Mark stale rows DEAD, dedup per session, and apply per-row helper keeps.

    Returns the total number of :class:`ProcessRun` rows deleted (sum of
    the per-session dedup and the helper-driven post-DEAD deletes).
    """
    from django.utils import timezone

    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun
    from twicc.providers.helpers import get_provider_helpers_registry

    now = timezone.now()
    dead_value = AgentState.DEAD.value

    # 1) Force every leftover non-DEAD row into DEAD. Anyone still claiming
    # to be alive is necessarily from a previous TwiCC instance — the
    # in-memory registry is empty at boot. Bulk UPDATE; no need to fetch
    # the rows first.
    marked_dead = ProcessRun.objects.exclude(state=dead_value).update(
        state=dead_value,
        last_state_change_at=now,
    )
    if marked_dead:
        logger.info("Marked %d stale process run(s) as DEAD at boot", marked_dead)

    # 2 & 3) Group rows by (provider, session_id), then per group:
    #   - dedup: keep the oldest by ``started_at``, delete the rest
    #   - on the oldest survivor, ask the provider helper if it should be
    #     kept (Claude Code: yes when crons remain; default: no)
    # Both passes happen inside the same per-(provider, session_id) loop so
    # the provider log context wraps every per-row log line — cross-provider
    # summary logs stay under the default ``"global"`` tag.
    # The ordering on ``started_at`` matters for Claude Code where the
    # oldest row carries the original crons — see
    # :mod:`twicc.providers.claude_code.cron_restart` for the lifecycle.
    runs_by_key: dict[tuple[str, str], list[ProcessRun]] = defaultdict(list)
    for run in ProcessRun.objects.all().order_by("started_at"):
        runs_by_key[(run.provider, run.session_id)].append(run)

    registry = get_provider_helpers_registry()
    dedup_deleted = 0
    helper_deleted = 0

    for (provider_value, session_id), runs in runs_by_key.items():
        try:
            provider = Provider(provider_value)
        except ValueError:
            # Unknown provider — wipe every row defensively, can't decide
            # anything sensible without a helper. No provider tag for the
            # log (the value is corrupt anyway).
            pks = [r.pk for r in runs]
            count, _ = ProcessRun.objects.filter(pk__in=pks).delete()
            helper_deleted += count
            logger.warning(
                "Process runs %s carry unknown provider %r, deleted %d row(s)",
                pks, provider_value, count,
            )
            continue

        with provider_log_context(provider):
            if len(runs) > 1:
                stale_pks = [r.pk for r in runs[1:]]
                count, _ = ProcessRun.objects.filter(pk__in=stale_pks).delete()
                dedup_deleted += count
                logger.info(
                    "Session %s had %d process runs at boot, kept oldest, deleted %d newer",
                    session_id, len(runs), count,
                )

            chosen = runs[0]

            try:
                helper = registry.get(provider)
            except KeyError:
                chosen.delete()
                helper_deleted += 1
                logger.warning(
                    "Process run %s has no registered helper, deleting",
                    chosen.pk,
                )
                continue

            if not helper.should_keep_dead_process_run(chosen):
                chosen.delete()
                helper_deleted += 1

    total_deleted = dedup_deleted + helper_deleted
    if total_deleted:
        logger.info(
            "Boot ProcessRun cleanup: deleted %d row(s) total (%d dedup, %d helper-driven)",
            total_deleted, dedup_deleted, helper_deleted,
        )
    return total_deleted


async def cleanup_stale_process_runs() -> int:
    """Submit a :class:`CleanupStaleProcessRunsJob` and await its result.

    Returns the number of :class:`ProcessRun` rows deleted. The job is
    dispatched on the DB writer's single-writer path
    (:func:`twicc.providers.db_writer.submit_async_job`), so the cleanup
    serialises naturally with every other DB write. Safe to call from
    anywhere on the asyncio loop.
    """
    from twicc.providers.db_writer import submit_async_job

    future = asyncio.get_running_loop().create_future()
    return await submit_async_job(CleanupStaleProcessRunsJob(future=future))
