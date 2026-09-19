"""Send a message to an existing agent session from a generic payload.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
with ``kind="session:send_message"``. The session is identified by
``session_id`` (already existing in DB); provider and project are looked
up from it.

The function does NOT raise for business-rule errors (missing session, stale
session, agent awaiting user input, etc.); it returns a
:class:`SendMessageResult` with ``success=False`` and a list of structured
error tuples. Unexpected exceptions propagate normally and are the caller's
responsibility to translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

import os
from typing import NamedTuple

from asgiref.sync import sync_to_async

from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


class SendMessageError(NamedTuple):
    field: str
    code: str
    message: str


class SendMessageResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SendMessageError] | None
    # Generic passthrough to the CLI's status payload (see the watcher's
    # ``_RESULT_ID_FIELDS``). Carries ``last_line`` — the transcript cursor a
    # ``--wait-reply`` measures "strictly past" against. NEVER put a "status"
    # key in here.
    status_extra: dict = {}


async def send_message_to_session_from_payload(payload: dict) -> SendMessageResult:
    """Send ``text`` (and optional attachments) to an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the existing target session (required).
    - ``text``: message body. Required unless the payload carries at least one
      attachment: both providers accept a user message made only of image /
      document blocks (creating a session still demands text — that is where
      the title comes from; see :mod:`twicc.core.services.session_creation`).
    - ``images``, ``documents``: lists of SDK block dicts (already validated
      by the caller — the service does not re-validate attachments).

    Business-rule rejections (returned as ``success=False``):
    - ``session_not_found``: no row in DB for that id.
    - ``is_subagent``: the row exists but is a subagent. Subagents cannot be
      messaged directly; the parent session is the right target.
    - ``session_stale``: ``Session.stale=True`` (file gone from disk).
    - ``project_no_directory``: the owning project has no directory set.
    - ``provider_disabled``: the owning provider was disabled in settings.
    - ``awaiting_user_input``: a live ProcessRun for this session is blocked
      on a user click (tool approval, ``AskUserQuestion``, Codex approval).
      Sending a message from the CLI would not unblock it — the user must
      resolve the pending dialog in the UI first.
    """
    session_id = payload.get("session_id")
    text = (payload.get("text") or "").strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []

    errors: list[SendMessageError] = []
    if not session_id:
        errors.append(SendMessageError("session_id", "missing", "session_id is required"))
    if not text and not images and not documents:
        errors.append(SendMessageError(
            "text", "empty_text", "text is required (unless the message carries attachments)",
        ))
    if errors:
        return SendMessageResult(False, None, None, None, errors)

    # --- session lookup -------------------------------------------------
    from twicc.core.models import ProcessRun, Session, SessionType
    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "session_not_found",
                             f"Session {session_id!r} not found"),
        ])
    if session.type == SessionType.SUBAGENT:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "is_subagent",
                             f"Session {session_id!r} is a subagent; "
                             "subagents cannot be messaged directly. "
                             "Target the parent session instead."),
        ])
    if session.stale:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "session_stale",
                             f"Session {session_id!r} is stale "
                             "(its JSONL file no longer exists on disk)"),
        ])

    project = session.project
    if project is None or not project.directory:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "project_no_directory",
                             f"Session {session_id!r} has no project directory"),
        ])

    # --- provider resolution --------------------------------------------
    try:
        provider = Provider(session.provider)
    except ValueError:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "unknown_provider",
                             f"Session {session_id!r} has unknown provider "
                             f"{session.provider!r}"),
        ])

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("provider", "provider_disabled", str(e)),
        ])

    # --- awaiting_user_input guard --------------------------------------
    # A live ProcessRun blocked on a user click won't consume new CLI
    # messages. A question can be answered from the CLI too
    # (``session <ID> answer``); anything else needs the UI. We refuse rather
    # than enqueue silently.
    twicc_pid = os.getpid()
    row = await sync_to_async(
        lambda: ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id=session_id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )()
    if row is not None and row.awaiting_user_input:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session_id", "awaiting_user_input",
                             f"Session {session_id!r} is awaiting user input "
                             "(tool approval or pending question). See "
                             "'twicc session <ID> pending-request'; a question "
                             "is answerable with 'twicc session <ID> answer', "
                             "anything else needs the UI."),
        ])

    # --- invoke the agent manager ---------------------------------------
    # Rehydrate the AgentSettings bundle from the Session row so the live
    # agent receives the values currently persisted for this session (None
    # columns mean "use the synced default", which ``resolve_agent_settings``
    # then fills in). Passing all-None here would risk a spurious settings
    # change vs the in-memory agent and trigger an unwanted restart.
    helpers = get_provider_helpers(provider)
    agent_settings = AgentSettings(**{
        field: getattr(session, field) for field in AgentSettings._fields
    })
    effective = helpers.resolve_agent_settings(agent_settings)
    effective = helpers.enforce_agent_settings_consistency(effective)

    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    try:
        await manager.send_to_session(
            session_id, session.project_id, project.directory, text,
            settings=effective, images=images, documents=documents,
        )
    except RuntimeError as e:
        return SendMessageResult(False, None, None, None, [
            SendMessageError("session", "manager_busy", str(e)),
        ])

    # Read **after** the agent has taken the message, and server-side: it is
    # the highest line the watcher had indexed at that instant, so every line
    # past it was written afterwards. A caller reading it itself, once the
    # command has returned, would be racing the reply it is about to wait for.
    last_line = await sync_to_async(
        lambda: Session.objects.filter(id=session_id)
        .values_list("last_line", flat=True).first()
    )()

    return SendMessageResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
        status_extra={"last_line": last_line or 0},
    )
