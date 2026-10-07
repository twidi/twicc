"""Send a message to an existing agent session from a generic payload.

Called for ``kind="session:send_message"`` (drop-request watcher, and the in-backend transport
of the RPC and the MCP) through :func:`send_message_from_drop_payload`. The session is identified
by ``session_id`` (already existing in DB); provider and project are looked up from it.

The function does NOT raise for business-rule errors (missing session, stale
session, agent awaiting user input, etc.); it returns a
:class:`SendMessageResult` with ``success=False`` and a list of structured
error tuples. Unexpected exceptions propagate normally and are the caller's
responsibility to translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

import os
from typing import NamedTuple
from uuid import uuid4

from asgiref.sync import sync_to_async

from twicc.agent.send_lanes import send_lane
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.services.attachments import drop as attachment_drop
from twicc.core.services.attachments import lifecycle as attachment_lifecycle
from twicc.core.services.attachments import planner as attachment_planner
from twicc.core.services.attachments import target as plan_target
from twicc.core.services.attachments.staging import AttachmentError
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


def _rejected(field: str, code: str, message: str) -> SendMessageResult:
    return SendMessageResult(False, None, None, None, [SendMessageError(field, code, message)])


async def send_message_from_drop_payload(payload: dict) -> SendMessageResult:
    """Drop-request handler for ``kind="session:send_message"`` (phase 2 design §4.5.1).

    Takes the session's send lane before any read of the session row, so two sends to one
    session keep their order whatever their entry point (D15). The backend owns the refs of
    the payload from here (D14).
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return await send_message_to_session_from_payload(payload, release_refs_on_outcome=True)
    async with send_lane(session_id):
        return await send_message_to_session_from_payload(payload, release_refs_on_outcome=True)


async def send_message_to_session_from_payload(
    payload: dict, *, release_refs_on_outcome: bool = False,
) -> SendMessageResult:
    """Send ``text`` (and optional attachments) to an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the existing target session (required).
    - ``text``: message body. Required unless the payload carries at least one
      attachment: both providers accept a user message made only of attachments
      (creating a session still demands text — that is where the title comes
      from; see :mod:`twicc.core.services.session_creation`).
    - ``attachments``: staged refs ``[{bucket, id}, ...]``, planned for the
      session's provider once its settings are resolved, then committed by the
      manager (phase 1 pipeline).
    - ``images``, ``documents``: legacy SDK blocks of an older CLI, passed as is.

    With ``release_refs_on_outcome`` (drop-request callers), the refs are released
    on every outcome, except a send the manager did not deliver now: a parked send
    holds them, and the retention reaper removes them otherwise (§4.5.3).

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
    - attachment codes (field ``attachments``): ``invalid_attachments``,
      ``attachment_missing``, ``attachment_not_ready``, ``attachments_with_command``,
      ``attachment_commit_failed``, …
    """
    refs = attachment_drop.refs_to_release(payload) if release_refs_on_outcome else ()
    keep_refs = False
    try:
        result, keep_refs = await _send(payload)
        return result
    finally:
        if not keep_refs:
            attachment_lifecycle.delivery_release(refs)()


async def _send(payload: dict) -> tuple[SendMessageResult, bool]:
    """The send itself. The flag is True when the refs must stay (a send not delivered now)."""
    session_id = payload.get("session_id")
    raw_text = payload.get("text") or ""
    text = raw_text.strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []
    try:
        refs = attachment_planner.validate_attachment_frame(payload)
    except AttachmentError as exc:
        return _rejected("attachments", exc.code, str(exc)), False

    errors: list[SendMessageError] = []
    if not session_id:
        errors.append(SendMessageError("session_id", "missing", "session_id is required"))
    if not text and not images and not documents and not refs and not (
        isinstance(payload.get("async_questions"), dict) and payload["async_questions"].get("answers")
    ):
        errors.append(SendMessageError(
            "text", "empty_text", "text is required (unless the message carries attachments)",
        ))
    if errors:
        return SendMessageResult(False, None, None, None, errors), False

    # --- session lookup -------------------------------------------------
    from twicc.core.models import ProcessRun, Session, SessionType
    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return _rejected("session_id", "session_not_found", f"Session {session_id!r} not found"), False
    if session.type == SessionType.SUBAGENT:
        return _rejected(
            "session_id", "is_subagent",
            f"Session {session_id!r} is a subagent; subagents cannot be messaged directly. "
            "Target the parent session instead.",
        ), False
    if session.stale:
        return _rejected(
            "session_id", "session_stale",
            f"Session {session_id!r} is stale (its JSONL file no longer exists on disk)",
        ), False

    project = session.project
    if project is None or not project.directory:
        return _rejected(
            "session_id", "project_no_directory", f"Session {session_id!r} has no project directory",
        ), False

    # --- provider resolution --------------------------------------------
    try:
        provider = Provider(session.provider)
    except ValueError:
        return _rejected(
            "session_id", "unknown_provider", f"Session {session_id!r} has unknown provider {session.provider!r}",
        ), False

    if provider == Provider.CODEX:
        text = raw_text

    if "async_questions" in payload and provider != Provider.CODEX:
        return _rejected(
            "async_questions", "async_questions_invalid", "Question answers require a Codex session",
        ), False

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return _rejected("provider", "provider_disabled", str(e)), False

    # --- awaiting_user_input guard --------------------------------------
    # A live ProcessRun blocked on a user click won't consume new CLI
    # messages. A question can be answered from the CLI too
    # (``session <ID> answer-questions``); anything else needs the UI. We
    # refuse rather than enqueue silently.
    twicc_pid = os.getpid()
    row = await sync_to_async(
        lambda: ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id=session_id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )()
    if row is not None and row.awaiting_user_input:
        return _rejected(
            "session_id", "awaiting_user_input",
            f"Session {session_id!r} is awaiting user input (tool approval or pending question). See "
            "'twicc session <ID> pending-requests'; a question is answerable with "
            "'twicc session <ID> answer-questions', anything else needs the UI.",
        ), False

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

    # Staged refs: planned for this session (phase 1 planner, §4.5.2), passed only when
    # there are refs, so a send without files keeps its exact manager call.
    plan_kwargs: dict = {}
    if refs:
        try:
            target = await plan_target.resolve_existing_session_plan_target(
                session_id=session_id,
                provider=provider.value,
                effective_settings=effective,
                directory=project.directory,
                ephemeral=False,
                live_agent=manager.get_live_agent(session_id),
            )
            plan_kwargs["attachment_plan"] = await attachment_planner.plan_attachments_off_loop(
                refs, target, text=text,
            )
        except AttachmentError as exc:
            code, message, _names = attachment_planner.describe_attachment_error(exc)
            return _rejected("attachments", code, message), False

    try:
        delivered = await manager.send_to_session(
            session_id, session.project_id, project.directory, text,
            settings=effective, images=images, documents=documents,
            **plan_kwargs,
            **({"async_questions": payload.get("async_questions"),
                "request_id": payload.get("_send_request_id") or str(uuid4()),
                "send_origin": payload.get("_send_origin", "internal")} if provider == Provider.CODEX else {}),
        )
    except RuntimeError as e:
        code = getattr(e, "code", None) or "manager_busy"
        if attachment_drop.is_attachment_code(code):
            return _rejected("attachments", code, attachment_drop.message_with_names(e)), False
        return _rejected("session", code, str(e)), False

    if delivered is False:
        # Maybe parked by the manager: the parked entry holds the refs and their release.
        return _rejected("session", "send_failed", "The message was not accepted for delivery"), True

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
    ), False
