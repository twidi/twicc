"""
Cron restart: re-launch Claude Code sessions that had active cron jobs.

Called at TwiCC startup (restart_all_session_crons) and at runtime when a
process with active crons dies from a non-manual cause (_restart_crons_for_session
in ClaudeCodeAgentManager). Both paths use the same restart_session_crons() function.

Relaunching the *process* is the part only TwiCC can do; re-arming the jobs is
mostly the CLI's job now. Since CLI 2.1.110 a resume replays the transcript and
resurrects every unexpired job with its original id and ``created_at``
(:meth:`SessionCron.is_restored_on_resume`). So the message we send on resume
asks Claude to recreate *only* the recurring jobs that went past the CLI's
7-day window — asking for the others would double them. The rows of the
resurrected ones are re-parented onto the new run by
:func:`reattach_crons_and_purge_old_runs` so the expiry monitor keeps renewing
them.

The cross-provider boot cleanup of stale :class:`ProcessRun` rows lives in
:mod:`twicc.agent.process_run_cleanup` and runs *before* this module is
invoked; by the time :func:`_prepare_restarts` reads the table, the only
surviving Claude Code rows are those whose
:meth:`ClaudeCodeHelpers.should_keep_dead_process_run` returned ``True``
(= rows that still have :class:`SessionCron` rows attached).
"""

import asyncio
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider
from twicc.request_origin import system_marker

logger = logging.getLogger(__name__)

RETRY_ESCALATION = [0, 5, 15, 30, 60, 120]
MAX_RETRY_DELAY = 300  # 5 minutes cap between attempts


class PrepareCronRestartsJob(NamedTuple):
    """Async-queue job to run :func:`_prepare_restarts` on the DB writer.

    Claude Code-specific (this provider's helper handles it in
    :meth:`ClaudeCodeHelpers.try_handle_async_job`). Pushed onto
    :attr:`db_writer._async_queue` via :func:`submit_async_job`.

    Today :func:`_prepare_restarts` is a near-pure read (it only deletes
    rows whose :class:`Session` was removed since the boot cleanup ran),
    but it remains routed through the DB writer to keep its read+delete
    pass serialised with every other writer.

    ``provider`` is fixed (this job is CC-only) but kept so the job
    follows the same convention as every other async-queue job — the DB
    writer's :func:`_settle_async_job` reads it for logging.
    ``future`` resolves to the list of session ids the caller should
    restart.
    """

    future: asyncio.Future  # → list[str] (session ids)
    provider: Provider = Provider.CLAUDE_CODE


def _apply_prepare_cron_restarts_job(job: PrepareCronRestartsJob) -> list[str]:
    """Run :func:`_prepare_restarts` inside ``transaction.atomic``.

    Sync — runs in a worker thread via ``sync_to_async`` inside
    :func:`db_writer._settle_async_job`. The atomic block keeps the
    Session-existence DELETE pass serialised with every other DB writer
    write, even though most of the cross-provider consolidation has
    already happened in :mod:`twicc.agent.process_run_cleanup` at boot.
    """
    with transaction.atomic():
        return _prepare_restarts()


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


class RestorableCrons(NamedTuple):
    """The session's crons, split by who is responsible for bringing them back.

    ``restored`` — the resumed CLI re-arms them by itself, same id, same
    ``created_at``. Claude must be told they are back so it does not create a
    duplicate that fires alongside them.

    ``to_recreate`` — recurring crons past the CLI's 7-day window. The CLI drops
    them on resume, so only a fresh ``CronCreate`` brings them back (which also
    resets the window).

    One-shot crons whose fire time has passed are in neither list: they are dead
    on both sides, and recreating them would schedule the next match a year
    later. Their rows go away with the old process run.
    """

    restored: list
    to_recreate: list

    @property
    def has_any(self) -> bool:
        return bool(self.restored or self.to_recreate)


def _split_restorable_crons(session_id: str) -> RestorableCrons:
    """Split ``session_id``'s persisted crons into the two restore buckets.

    Synchronous (DB access) — call from a thread. Deliberately reads every row
    instead of :meth:`SessionCron.active_for_session`: a recurring cron that
    went past its 7 days while the process was dead must still bring the
    session back, it just needs recreating rather than resurrecting.
    """
    from twicc.core.models import SessionCron

    now = datetime.now(tz=UTC)
    restored: list = []
    to_recreate: list = []
    for cron in SessionCron.objects.filter(
        session_id=session_id,
        provider=Provider.CLAUDE_CODE.value,
    ).order_by("created_at"):
        if cron.is_restored_on_resume(now):
            restored.append(cron)
        elif cron.recurring:
            to_recreate.append(cron)
    return RestorableCrons(restored, to_recreate)


def _cron_payload(cron) -> dict:
    """Message-building payload for one :class:`SessionCron` row."""
    return {
        "cron_id": cron.cron_id,
        "cron_expr": cron.cron_expr,
        "recurring": cron.recurring,
        "prompt": cron.prompt,
    }


def reattach_crons_and_purge_old_runs(session_id: str, current_run_id: int) -> tuple[int, int]:
    """Re-parent the still-live crons onto the current run, then drop the old runs.

    Called at the first USER_TURN of a (re)started agent, from
    :meth:`ClaudeCodeAgentManager._on_state_change`, under the DB write lock.

    Deleting a :class:`ProcessRun` cascades onto its :class:`SessionCron` rows.
    That was correct while a resumed CLI lost its jobs, but it now discards rows
    whose job is still armed (see :meth:`SessionCron.is_restored_on_resume`):
    TwiCC would stop tracking a live cron, and the expiry monitor would never
    renew it. So those rows move to the current run first. Everything else —
    expired one-shots, and recurring jobs Claude has just recreated under a new
    id — goes away with the old rows.

    Synchronous (DB access) — call from a thread. Returns
    ``(reattached, deleted_runs)``.
    """
    from twicc.core.models import ProcessRun, SessionCron

    old_run_pks = list(
        ProcessRun.objects
        .filter(session_id=session_id)
        .exclude(pk=current_run_id)
        .values_list("pk", flat=True)
    )
    if not old_run_pks:
        return 0, 0

    now = datetime.now(tz=UTC)
    reattached = 0
    for cron in SessionCron.objects.filter(process_run_id__in=old_run_pks):
        if not cron.is_restored_on_resume(now):
            continue
        cron.process_run_id = current_run_id
        cron.save(update_fields=["process_run"])
        reattached += 1

    # Counting the pks, not ``delete()``'s first return value: that one sums the
    # cascaded SessionCron rows in with the runs.
    ProcessRun.objects.filter(pk__in=old_run_pks).delete()
    return reattached, len(old_run_pks)


def _collect_restart_data(session_id: str) -> dict | None:
    """Collect restart data for a single session (synchronous, runs in thread).

    Returns a dict with keys matching send_to_session() kwargs (minus text)
    plus the two cron buckets used to build the message (see
    :class:`RestorableCrons`). Returns None if restart is not possible
    (nothing left to restore, session not found, or cwd missing).
    """
    from twicc.core.enums import Provider
    from twicc.core.models import Session
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    crons = _split_restorable_crons(session_id)
    if not crons.has_any:
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

    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    agent_settings = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(
            AgentSettings.from_session(session),
        ),
    )

    return {
        "session_id": session_id,
        "project_id": session.project_id,
        "cwd": cwd,
        "restored_crons": [_cron_payload(c) for c in crons.restored],
        "crons_to_recreate": [_cron_payload(c) for c in crons.to_recreate],
        "settings": agent_settings,
    }


async def restart_all_session_crons(stop_event: asyncio.Event) -> None:
    """Scan ProcessRun table and restart all sessions with persisted crons.

    Steps:
    1. Clean up orphan/stale process runs (routed through the DB writer)
    2. Launch restart_session_crons() in parallel for each session with active crons
    """
    from django.conf import settings

    if not settings.CRON_AUTO_RESTART:
        logger.info("Cron auto-restart disabled (TWICC_NO_CRON_RESTART is set)")
        return

    from twicc.providers.db_writer import submit_async_job

    future = asyncio.get_running_loop().create_future()
    try:
        session_ids = await submit_async_job(PrepareCronRestartsJob(future=future))
    except Exception as e:
        logger.error("Cron restart prepare failed via DB writer: %s", e, exc_info=True)
        return

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
    """Return session IDs for Claude Code ProcessRuns eligible for cron restart.

    Called in asyncio.to_thread from restart_all_session_crons(). By the
    time this runs, the cross-provider boot cleanup in
    :mod:`twicc.agent.process_run_cleanup` has already consolidated the
    :class:`ProcessRun` table — orphan rows are gone, per-session duplicates
    are collapsed to a single oldest row, and only rows the Claude Code
    helper deemed worth keeping (= rows that still have :class:`SessionCron`
    rows attached) survive on the CC slice.

    Three concerns remain, all resolved by deleting the now-pointless
    :class:`ProcessRun` (cascading its crons) instead of restarting:

    - Sessions whose JSONL was deleted on disk between TwiCC instances. The
      boot cleanup keeps them because their cron rows still exist; the
      per-session initial sync removes the matching :class:`Session` row but
      doesn't know about ProcessRun, so we catch that case here.
    - Archived sessions. Archiving is a deliberate stop and drops these rows
      itself (``core.services.session_update.apply_session_archived_change``),
      so a surviving row means the archive predates that behaviour — never
      resurrect a session the user put away.
    - Sessions left with nothing to restore. The boot cleanup only checks that
      cron rows *exist*; a session whose every cron is a one-shot that already
      fired has nothing to bring back, and without this its row would survive
      every boot (the purge that would clear it only runs on a USER_TURN that
      never comes).
    """
    from twicc.agent.states import AgentState
    from twicc.core.enums import Provider
    from twicc.core.models import ProcessRun, Session

    cc_provider = Provider.CLAUDE_CODE.value

    session_ids: list[str] = []
    for process_run in (
        ProcessRun.objects
        .filter(provider=cc_provider, state=AgentState.DEAD.value)
        .order_by("started_at")
    ):
        session_id = process_run.session_id

        # Validate the session exists (clean up if the JSONL was deleted) and
        # is not archived.
        archived = (
            Session.objects
            .filter(id=session_id)
            .values_list("archived", flat=True)
            .first()
        )
        if archived is None:
            process_run.delete()
            logger.warning(
                "Session %s: not found in DB, deleted process run %s",
                session_id, process_run.pk,
            )
            continue

        if archived:
            process_run.delete()
            logger.info(
                "Session %s: archived, deleted process run %s (crons dropped)",
                session_id, process_run.pk,
            )
            continue

        if not _split_restorable_crons(session_id).has_any:
            process_run.delete()
            logger.info(
                "Session %s: no cron left to restore, deleted process run %s",
                session_id, process_run.pk,
            )
            continue

        session_ids.append(session_id)

    return session_ids


async def _restart_session_crons(
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
    (_restart_crons_for_session in ClaudeCodeAgentManager).
    """
    from twicc.agent import AgentState
    from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

    manager = get_claude_code_agent_manager()

    # Crons on hybrid sessions are out of scope (V1): a hybrid session can
    # only carry crons created in its SDK era (before the one-way switch),
    # and restarting them would launch the interactive CLI on a timer.
    # Refuse + log instead.
    if await manager._session_is_hybrid(session_id):
        logger.warning(
            "Cron restart refused for session %s: hybrid sessions do not "
            "support crons (V1)", session_id,
        )
        return

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
            except TimeoutError:
                pass  # Normal: delay elapsed, time to retry

        # Collect fresh data on each attempt (crons may expire, settings may change)
        restart_data = await asyncio.to_thread(_collect_restart_data, session_id)
        if restart_data is None:
            logger.info(
                "Cron restart for session %s: no restart data available, stopping (attempt %d)",
                session_id, attempt,
            )
            return

        message = _build_restart_message(
            restart_data.pop("restored_crons"),
            restart_data.pop("crons_to_recreate"),
        )

        try:
            await manager.send_to_session(**restart_data, text=message, cancel_cron_restart=False)

            process = manager._agents.get(session_id)
            if process is None:
                logger.warning(
                    "Cron restart for session %s: process not found after send_to_session (attempt %d)",
                    session_id, attempt,
                )
                continue

            if process.state == AgentState.DEAD:
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
            except TimeoutError:
                logger.warning(
                    "Cron restart for session %s: timeout waiting for USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                await manager.kill_agent(session_id, reason="cron_restart_timeout")
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


async def restart_session_crons(
    session_id: str,
    *,
    stop_event: asyncio.Event,
    initial_delay: int = 0,
) -> None:
    """Restart cron jobs for a single session with infinite retry (see :func:`_restart_session_crons`).

    The restart is automatic, at boot or at runtime: every start it performs runs under the system
    marker, so it is recorded as ``system`` and never falls to the default (design 2026-10-14, section 5).
    """
    with system_marker():
        await _restart_session_crons(session_id, stop_event=stop_event, initial_delay=initial_delay)


def _format_cron_description(cron: dict, *, cron_id_label: str | None = None) -> str:
    """Format a single cron's details for inclusion in a message.

    Args:
        cron: Dict with "cron_id", "cron_expr", "recurring", "prompt" keys.
        cron_id_label: Label for the cron's CLI id line (e.g. "ID", "ID to
            delete"). Omit to leave the id out — a cron Claude must create from
            scratch has no id yet.
    """
    lines = []
    if cron_id_label and cron.get("cron_id"):
        lines.append(f"**{cron_id_label}**: `{cron['cron_id']}`")
    schedule = f'**Schedule**: `{cron["cron_expr"]}`'
    if cron["recurring"]:
        schedule += " (recurring)"
    lines.append(schedule)
    lines.append("**Prompt**:")
    lines.append("<cron-prompt>")
    lines.append(cron["prompt"])
    lines.append("</cron-prompt>")
    return "\n".join(lines)


def _build_cron_descriptions(crons_data: list[dict], *, cron_id_label: str | None = None) -> str:
    """Build the formatted block of cron descriptions separated by ---."""
    parts = ["---\n"]
    for cron in crons_data:
        parts.append(_format_cron_description(cron, cron_id_label=cron_id_label))
        parts.append("\n---\n")
    return "\n".join(parts)


def _build_restart_message(restored: list[dict], to_recreate: list[dict]) -> str:
    """Build the user message sent to a session relaunched for its cron jobs.

    Two independent sections, either of which may be empty (never both — the
    caller stops when there is nothing to restore):

    - ``restored``: jobs the resumed CLI re-armed by itself. Claude is told they
      are back precisely so it does *not* recreate them — a second CronCreate
      would fire alongside the restored job instead of replacing it.
    - ``to_recreate``: recurring jobs past the CLI's 7-day window, dropped on
      resume. Only Claude can bring them back.
    """
    parts = ["<twicc-cron-restart>", "This session was just resumed.\n"]

    if restored:
        one = len(restored) == 1
        parts.append(
            f"Claude Code already restored the cron job{'' if one else 's'} below — "
            f"{'it is' if one else 'they are'} armed and will fire on schedule. "
            f"Do NOT call CronCreate for {'it' if one else 'them'}: that would add a "
            f"duplicate firing alongside the restored job, not replace it.\n"
        )
        parts.append(_build_cron_descriptions(restored, cron_id_label="ID"))

    if to_recreate:
        one = len(to_recreate) == 1
        parts.append(
            f"The cron job{'' if one else 's'} below reached the 7-day expiry and "
            f"{'was' if one else 'were'} dropped. Recreate "
            f"{'it' if one else 'each of them'} using CronCreate, with the exact "
            f"schedule and prompt shown.\n"
        )
        parts.append(_build_cron_descriptions(to_recreate))
        parts.append(
            "Do not say anything other than a short sentence acknowledging the "
            "number of cron jobs recreated."
        )
    else:
        parts.append(
            "There is nothing to do. Do not say anything other than a short sentence "
            "acknowledging the restored cron job(s)."
        )

    parts.append("</twicc-cron-restart>")
    return "\n".join(parts)


def _build_renewal_message(crons_data: list[dict]) -> str:
    """Build the user message asking Claude to delete and recreate expired cron jobs.

    Used when the process is alive but crons have reached their 7-day expiry.
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

    descriptions = _build_cron_descriptions(crons_data, cron_id_label="ID to delete")

    return (
        f"<twicc-cron-renewal>\n"
        f"{header}\n\n{descriptions}\n\n"
        f"Use the exact schedule and prompt shown above for each CronCreate call.\n\n"
        f"Do not say anything other than a short sentence acknowledging the number of crons recreated.\n"
        f"</twicc-cron-renewal>"
    )
