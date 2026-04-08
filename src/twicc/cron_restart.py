"""
Cron restart: re-launch Claude sessions that had active cron jobs.

Called at TwiCC startup (restart_all_session_crons) and at runtime when a
process with active crons dies from a non-manual cause (_restart_crons_for_session
in ProcessManager). Both paths use the same restart_session_crons() function.
"""

import asyncio
import logging
import os
from collections import defaultdict
from collections.abc import Iterator

logger = logging.getLogger(__name__)

RETRY_ESCALATION = [0, 5, 15, 30, 60, 120]
MAX_RETRY_DELAY = 300  # 5 minutes cap between attempts


def _retry_delays(initial_delay: int = 0) -> Iterator[int]:
    """Yield retry delays infinitely: initial_delay, then escalation (skipping ≤), then MAX_RETRY_DELAY forever."""
    yield initial_delay
    for delay in RETRY_ESCALATION:
        # Skip delays ≤ initial_delay to keep the sequence monotonically increasing
        # (e.g., with initial_delay=10: skip 0, 5 → yield 15, 30, 60, 120)
        if delay <= initial_delay:
            continue
        yield delay
    while True:
        yield MAX_RETRY_DELAY


def _collect_restart_data(session_id: str) -> dict | None:
    """Collect restart data for a single session (synchronous, runs in thread).

    Returns a dict with keys matching send_to_session() kwargs (minus text)
    plus crons_data for message building. Returns None if restart not possible
    (no active crons, session not found, or cwd missing).
    """
    from twicc.core.models import Session, SessionCron

    active_crons = list(SessionCron.active_for_session(session_id))
    if not active_crons:
        return None

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("Cron restart for session %s: session not found in DB", session_id)
        return None

    cwd = session.cwd
    if not cwd or not os.path.isdir(cwd):
        logger.warning("Cron restart for session %s: cwd '%s' does not exist on disk", session_id, cwd)
        return None

    return {
        "session_id": session_id,
        "project_id": session.project_id,
        "cwd": cwd,
        "crons_data": [
            {"cron_expr": c.cron_expr, "recurring": c.recurring, "prompt": c.prompt}
            for c in active_crons
        ],
        "permission_mode": session.permission_mode or "default",
        "selected_model": session.selected_model,
        "effort": session.effort,
        "thinking_enabled": session.thinking_enabled,
        "claude_in_chrome": session.claude_in_chrome,
        "context_max": session.context_max,
        "keep_settings": session.keep_settings,
    }


async def restart_all_session_crons(stop_event: asyncio.Event) -> None:
    """Scan ProcessRun table and restart all sessions with persisted crons.

    Steps:
    1. Clean up orphan/stale process runs
    2. Launch restart_session_crons() in parallel for each session with active crons
    """
    from django.conf import settings

    if not settings.CRON_AUTO_RESTART:
        logger.info("Cron auto-restart disabled (TWICC_NO_CRON_RESTART is set)")
        return

    session_ids = await asyncio.to_thread(_prepare_restarts)

    if not session_ids:
        logger.info("No cron jobs to restart")
        return

    logger.info("Restarting cron jobs for %d session(s)", len(session_ids))

    tasks = [
        restart_session_crons(sid, stop_event=stop_event)
        for sid in session_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = 0
    cancelled = 0
    for result in results:
        if result is None:
            succeeded += 1
        elif isinstance(result, asyncio.CancelledError):
            cancelled += 1
        elif isinstance(result, BaseException):
            logger.error("Unexpected error in cron restart: %s", result)

    logger.info(
        "Cron restart complete: %d succeeded, %d cancelled (shutdown)",
        succeeded, cancelled,
    )


def _prepare_restarts() -> list[str]:
    """Synchronous DB work: cleanup orphan/stale process runs, return session IDs to restart.

    Called in asyncio.to_thread from restart_all_session_crons().
    """
    from django.db.models import Count

    from twicc.core.models import ProcessRun, Session, SessionCron

    # 1. Delete orphan process runs (no crons attached)
    orphan_count, _ = (
        ProcessRun.objects
        .annotate(cron_count=Count("crons"))
        .filter(cron_count=0)
        .delete()
    )
    if orphan_count:
        logger.info("Cleaned up %d orphan process run(s)", orphan_count)

    # 2. For sessions with multiple process runs, keep only the oldest
    runs_by_session: dict[str, list[ProcessRun]] = defaultdict(list)
    for process_run in ProcessRun.objects.order_by("started_at"):
        runs_by_session[process_run.session_id].append(process_run)

    for session_id, runs in runs_by_session.items():
        if len(runs) > 1:
            # Keep the oldest (index 0), delete all others (cascade deletes their crons)
            stale_pks = [r.pk for r in runs[1:]]
            deleted_count, _ = ProcessRun.objects.filter(pk__in=stale_pks).delete()
            logger.info(
                "Session %s had %d process runs, kept oldest, deleted %d newer one(s)",
                session_id, len(runs), deleted_count,
            )
            runs_by_session[session_id] = [runs[0]]

    # 3. Filter to sessions with active crons, clean up the rest
    session_ids = []
    for session_id, runs in runs_by_session.items():
        process_run = runs[0]

        if not SessionCron.active_for_session(session_id).filter(process_run=process_run).exists():
            process_run.delete()
            logger.info("Session %s: all crons expired, deleted process run %s", session_id, process_run.pk)
            continue

        # Validate session exists (clean up if JSONL was deleted)
        if not Session.objects.filter(id=session_id).exists():
            process_run.delete()
            logger.warning("Session %s: not found in DB, deleted process run %s", session_id, process_run.pk)
            continue

        session_ids.append(session_id)

    return session_ids


async def restart_session_crons(
    session_id: str,
    *,
    stop_event: asyncio.Event,
    initial_delay: int = 0,
) -> None:
    """Restart cron jobs for a single session with infinite retry.

    On each attempt: collects fresh data from DB, sends restart message to Claude,
    waits for the first USER_TURN to confirm success. Retries indefinitely with
    capped exponential backoff until success, cancellation (stop_event), or all
    crons have expired (nothing left to restart).

    Used identically by startup (restart_all_session_crons) and runtime
    (_restart_crons_for_session in ProcessManager).
    """
    from twicc.agent.manager import get_process_manager
    from twicc.agent.states import ProcessState

    manager = get_process_manager()
    delays = _retry_delays(initial_delay)
    attempt = 0

    while True:
        delay = next(delays)
        attempt += 1

        if delay > 0:
            logger.info(
                "Cron restart for session %s: attempt %d in %ds",
                session_id, attempt, delay,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                logger.info(
                    "Cron restart for session %s: cancelled during delay (attempt %d)",
                    session_id, attempt,
                )
                return
            except asyncio.TimeoutError:
                pass  # Normal: delay elapsed, time to retry

        # Collect fresh data on each attempt (crons may expire, settings may change)
        restart_data = await asyncio.to_thread(_collect_restart_data, session_id)
        if restart_data is None:
            logger.info(
                "Cron restart for session %s: no restart data available, stopping (attempt %d)",
                session_id, attempt,
            )
            return

        crons_data = restart_data.pop("crons_data")
        message = _build_restart_message(crons_data)

        try:
            await manager.send_to_session(**restart_data, text=message, cancel_cron_restart=False)

            process = manager._processes.get(session_id)
            if process is None:
                logger.warning(
                    "Cron restart for session %s: process not found after send_to_session (attempt %d)",
                    session_id, attempt,
                )
                continue

            if process.state == ProcessState.DEAD:
                logger.warning(
                    "Cron restart for session %s: process died immediately (attempt %d)",
                    session_id, attempt,
                )
                continue

            # Wait for first USER_TURN (success) or DEAD (failure)
            try:
                await asyncio.wait_for(
                    process._first_turn_done_event.wait(),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Cron restart for session %s: timeout waiting for USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                await manager.kill_process(session_id, reason="cron_restart_timeout")
                continue

            if process._first_user_turn_reached:
                logger.info("Successfully restarted crons for session %s (attempt %d)", session_id, attempt)
                return
            else:
                logger.warning(
                    "Cron restart for session %s: process died before USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                continue

        except Exception as e:
            logger.error(
                "Cron restart for session %s: unexpected error (attempt %d): %s",
                session_id, attempt, e,
            )
            continue


def _format_cron_description(cron: dict, *, cron_id_to_delete: str | None = None) -> str:
    """Format a single cron's details for inclusion in a message.

    Args:
        cron: Dict with "cron_expr", "recurring", "prompt" keys.
        cron_id_to_delete: If provided, adds an "ID to delete" line (for renewal messages).
    """
    lines = []
    if cron_id_to_delete:
        lines.append(f"**ID to delete**: `{cron_id_to_delete}`")
    schedule = f'**Schedule**: `{cron["cron_expr"]}`'
    if cron["recurring"]:
        schedule += " (recurring)"
    lines.append(schedule)
    lines.append("**Prompt**:")
    lines.append("<cron-prompt>")
    lines.append(cron["prompt"])
    lines.append("</cron-prompt>")
    return "\n".join(lines)


def _build_cron_descriptions(crons_data: list[dict], *, with_cron_ids: bool = False) -> str:
    """Build the formatted block of cron descriptions separated by ---."""
    parts = ["---\n"]
    for cron in crons_data:
        cron_id_to_delete = cron.get("cron_id") if with_cron_ids else None
        parts.append(_format_cron_description(cron, cron_id_to_delete=cron_id_to_delete))
        parts.append("\n---\n")
    return "\n".join(parts)


def _build_restart_message(crons_data: list[dict]) -> str:
    """Build the user message asking Claude to recreate cron jobs.

    Used when the process is dead and crons need to be recreated from scratch.
    No deletion needed since the CLI crons are already gone.
    """
    if len(crons_data) == 1:
        header = (
            "This session was just resumed, so we lost our previous cron job, "
            "please recreate it using CronCreate:"
        )
    else:
        header = (
            "This session was just resumed, so we lost our previous cron jobs, "
            "please recreate each of them using CronCreate:"
        )

    descriptions = _build_cron_descriptions(crons_data)

    return (
        f"<twicc-cron-restart>\n"
        f"{header}\n\n{descriptions}\n\n"
        f"Use the exact schedule and prompt shown above for each CronCreate call.\n\n"
        f"Do not say anything other than a short sentence acknowledging the number of crons recreated.\n"
        f"</twicc-cron-restart>"
    )


def _build_renewal_message(crons_data: list[dict]) -> str:
    """Build the user message asking Claude to delete and recreate expired cron jobs.

    Used when the process is alive but crons have reached their 3-day expiry.
    The CLI may or may not have auto-deleted them yet, so we ask Claude to
    delete them first (if they still exist) before recreating.

    Each dict in crons_data must include a "cron_id" key with the CLI cron ID.
    """
    if len(crons_data) == 1:
        header = (
            "A cron job may have automatically expired. "
            "Please delete it using CronDelete if it still exists, "
            "then recreate it using CronCreate:"
        )
    else:
        header = (
            "The following cron jobs may have automatically expired. "
            "For each one, delete it using CronDelete if it still exists, "
            "then recreate it using CronCreate:"
        )

    descriptions = _build_cron_descriptions(crons_data, with_cron_ids=True)

    return (
        f"<twicc-cron-renewal>\n"
        f"{header}\n\n{descriptions}\n\n"
        f"Use the exact schedule and prompt shown above for each CronCreate call.\n\n"
        f"Do not say anything other than a short sentence acknowledging the number of crons recreated.\n"
        f"</twicc-cron-renewal>"
    )
