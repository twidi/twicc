"""One automatic title check, including its conditional apply and provider push."""

import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from django.utils import timezone

from twicc.agent.registry import get_agent_manager_registry
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType
from twicc.core.services.title_suggestion import suggest_title
from twicc.pending_titles import get_pending_title
from twicc.providers.db_writer import broadcast_session_updated, run_under_db_write_lock
from twicc.providers.helpers import get_provider_helpers
from twicc.search_indexing_task import request_session_reindex
from twicc.synced_settings import read_synced_settings
from twicc.title_cadence import (
    TitleCheckState,
    after_check,
    closing_check_due,
    is_relevant_message,
    rebased,
    title_check_due,
)
from twicc.title_echo import record_automatic_title_push
from twicc.title_transcript import build_title_source, title_rejection_reasons

logger = logging.getLogger(__name__)


def _is_eligible(session: Session, *, closing: bool) -> bool:
    """Check mutable eligibility, including pending choices and hybrid liveness."""
    if (
        session.type == SessionType.SUBAGENT
        or (session.archived and not closing)
        or session.hidden
        or session.stale
        or (session.title_origin != "auto" and session.title is not None)
        or get_pending_title(session.id)
    ):
        return False
    if session.hybrid and session.title_check_count is not None:
        manager = get_agent_manager_registry().get(Provider(session.provider))
        agent = manager._agents.get(session.id)
        if agent is not None and getattr(agent, "is_hybrid", False) and agent.state != AgentState.DEAD:
            return False
    return True


def _passes_pre_gate(session: Session, now: datetime, *, closing: bool) -> bool:
    """Use the total count as an upper bound before reading the transcript."""
    state = TitleCheckState(session.title_check_count, session.title_checked_at)
    count = session.user_message_count
    if state.count is not None and count < state.count:
        return True  # A shrink must reach the exact count, even before the interval.
    return closing_check_due(state, count) if closing else title_check_due(state, count, now)


async def _matching_session(snapshot: Session, *, closing: bool) -> Session | None:
    """Recheck under the caller's shared write lock, for every kind of write."""
    current = await Session.objects.filter(pk=snapshot.pk).afirst()
    if (
        current is None
        or current.title != snapshot.title
        or current.title_origin != snapshot.title_origin
        or not _is_eligible(current, closing=closing)
    ):
        return None
    return current


def _snapshot_rows(snapshot: Session):
    # Keep the text AND origin in the UPDATE itself. A user can confirm the same
    # text, or rename A -> B -> A, while generation runs outside the lock.
    return Session.objects.filter(pk=snapshot.pk, title=snapshot.title, title_origin=snapshot.title_origin)


async def _apply_rebase(snapshot: Session, state: TitleCheckState, *, closing: bool) -> bool:
    """Persist only a lower count, without consuming an attempt or a user choice."""
    async def apply():
        if await _matching_session(snapshot, closing=closing) is None:
            return False
        return bool(await _snapshot_rows(snapshot).aupdate(title_check_count=state.count))

    return await run_under_db_write_lock(apply)


async def _apply_check(
    snapshot: Session,
    state: TitleCheckState,
    *,
    title: str | None,
    now: datetime,
    closing: bool,
    succeeded: bool,
) -> str:
    """Conditionally commit a success or failure; stale results consume nothing."""
    async def apply():
        if await _matching_session(snapshot, closing=closing) is None:
            return "discarded"
        updates = {"title_checked_at": now}
        result = "failed"
        if succeeded:
            updates["title_check_count"] = state.count
            if title == (snapshot.title.strip() if snapshot.title is not None else None):
                result = "kept"
            else:
                updates.update(title=title, title_origin="auto")
                result = "changed"
        if not await _snapshot_rows(snapshot).aupdate(**updates):
            return "discarded"
        return result

    return await run_under_db_write_lock(apply)


async def _publish_change(session: Session, title: str) -> None:
    """Complete all post-commit work before releasing the runner's session slot."""
    # The provider may write successfully before raising. Keep this record even
    # on failure, and never replace it with the corrective (user) title below.
    record_automatic_title_push(session.id, title)
    try:
        await broadcast_session_updated(session.id)
    except Exception:
        logger.exception("Automatic title broadcast failed for %s", session.id)
    try:
        task = request_session_reindex(session.id)
        if task is not None:
            await task
    except Exception:
        logger.exception("Automatic title reindex failed for %s", session.id)
    try:
        helpers = get_provider_helpers(Provider(session.provider))
        await helpers.rename_session(session.id, title)
    except Exception:
        logger.exception("Automatic title rename failed for %s", session.id)
        return
    try:
        current = await Session.objects.filter(pk=session.pk).afirst()
        if current is not None and current.title is not None and current.title != title:
            # One corrective push only. Further user writeback races remain the
            # existing provider contract; this is not a new writeback queue.
            await helpers.rename_session(session.id, current.title)
    except Exception:
        logger.exception("Automatic title corrective push failed for %s", session.id)


async def check_session_title(session_id: str, *, closing: bool = False) -> None:
    """Evaluate one request at execution time, without scheduling a retry timer."""
    settings = read_synced_settings()
    if not settings["titleGenerationEnabled"] or not settings["titleAutoApply"]:
        return
    snapshot = await Session.objects.filter(pk=session_id).afirst()
    if snapshot is None or not _is_eligible(snapshot, closing=closing):
        return
    now = timezone.now()
    if not _passes_pre_gate(snapshot, now, closing=closing):
        return
    state = TitleCheckState(snapshot.title_check_count, snapshot.title_checked_at)
    system_prompt = settings["titleSystemPrompt"]
    if "{text}" not in system_prompt:
        logger.warning("Automatic title prompt lacks {text} for %s", session_id)
        await _apply_check(snapshot, state, title=None, now=now, closing=closing, succeeded=False)
        return

    provider = Provider(snapshot.provider)
    helpers = get_provider_helpers(provider)
    messages = await sync_to_async(helpers.get_title_messages)(session_id)
    relevant_messages = [text for text in messages if is_relevant_message(text)]
    count = len(relevant_messages)
    next_state = rebased(state, count)
    due = closing_check_due(next_state, count) if closing else title_check_due(next_state, count, now)
    if not due:
        if next_state != state:
            await _apply_rebase(snapshot, next_state, closing=closing)
        return

    title = None
    try:
        suggestion = await suggest_title(
            build_title_source(relevant_messages), system_prompt, provider,
            title_model=settings["titleSuggestionModel"],
            current_title=snapshot.title if snapshot.title_check_count is not None else None,
        )
        if suggestion.suggestion is not None:
            reasons = title_rejection_reasons(suggestion.suggestion)
            validation = helpers.validate_title(suggestion.suggestion)
            if not reasons and not validation.error:
                title = validation.title
            else:
                logger.warning("Automatic title rejected for %s: %s", session_id, reasons or validation.error)
    except Exception:
        logger.exception("Automatic title generation failed for %s", session_id)

    succeeded = title is not None
    result = await _apply_check(
        snapshot, after_check(state, count, now, succeeded=succeeded),
        title=title, now=now, closing=closing, succeeded=succeeded,
    )
    if result == "changed":
        await _publish_change(snapshot, title)
