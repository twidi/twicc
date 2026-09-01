"""WebSocket message handling specific to the Claude Code provider.

Provider handlers are plain classes (not Channels consumers) instantiated
once per WebSocket connection by ``twicc.asgi.WSConsumer``. The main
consumer routes provider-specific messages (whose ``type`` field is
prefixed with ``"<provider_key>:"``) to the matching handler.

Both inbound actions (via ``dispatch``) and outbound on-connect messages
(via ``get_connect_messages``) use the ``claude_code:`` prefix, e.g.
``claude_code:pending_request_response`` (inbound),
``claude_code:auth_updated`` (outbound).
"""

import logging
from collections.abc import AsyncIterator

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionRuleValue,
    PermissionUpdate,
)

from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.elicitation import (
    ELICITATION_TOOL_NAMES,
    default_elicitation_response,
)
from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager
from twicc.providers.claude_code.auth import (
    check_and_broadcast as check_auth_and_broadcast,
    get_auth_message_for_connection,
)
from twicc.providers.claude_code.usage import fetch_and_save_usage
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.state import ProviderDisabledError, ensure_provider_running
from twicc.usage_task import broadcast_usage_updated, get_usage_message_for_connection

logger = logging.getLogger(__name__)


def _permission_update_from_dict(data: dict) -> PermissionUpdate:
    """Reconstruct a PermissionUpdate from its serialized dict form.

    The SDK's ``PermissionUpdate.to_dict()`` uses camelCase keys (e.g., ``toolName``,
    ``ruleContent``). This function reverses that conversion back to the dataclass
    with snake_case field names.

    Args:
        data: Dictionary as produced by ``PermissionUpdate.to_dict()``

    Returns:
        A ``PermissionUpdate`` instance ready to pass back to the SDK.
    """
    rules = None
    raw_rules = data.get("rules")
    if raw_rules is not None:
        rules = [
            PermissionRuleValue(
                tool_name=r["toolName"],
                # SDK bug workaround: PermissionUpdate.to_dict() serializes None as
                # "ruleContent": null, but Claude Code CLI's Zod schema rejects null
                # (expects string | undefined). Using "" instead of None avoids the error.
                rule_content=r.get("ruleContent") or "",
            )
            for r in raw_rules
        ]

    return PermissionUpdate(
        type=data["type"],
        rules=rules,
        behavior=data.get("behavior"),
        mode=data.get("mode"),
        directories=data.get("directories"),
        destination=data.get("destination"),
    )


async def update_session_permission_mode(session_id: str, permission_mode: str) -> None:
    """Update the permission_mode for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await run_under_db_write_lock(
        lambda: Session.objects.filter(id=session_id)
            .exclude(permission_mode=permission_mode)
            .aupdate(permission_mode=permission_mode)
    )
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    if session is None or session.hidden:
        return
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with permission_mode {permission_mode}")


async def _clamp_setmode_permissions_for_trust(session_id: str, raw_permissions: list[dict]) -> None:
    """Clamp a setMode approval permission to the untrusted-allowed set, in place.

    Security floor for the approval path (trust design §13.4): the
    ``pending_request_response`` handler both hands ``updated_permissions`` to the
    SDK and persists the chosen mode to the DB. Mutating the setMode entry here —
    once, before either consumer reads it — keeps the SDK and the stored
    ``Session.permission_mode`` in agreement, so an untrusted project can never end
    up displaying ``bypassPermissions`` while the agent stays clamped (the bug this
    closes), nor have a forged/stale payload escalate past the floor.

    Trust is re-resolved live, so a project trusted mid-run is honored.
    """
    from twicc.core.models import Session
    from twicc.core.services.trust import clamp_permission_mode_for_untrusted, project_is_untrusted

    setmode = next(
        (p for p in raw_permissions if p.get("type") == "setMode" and p.get("mode")),
        None,
    )
    if setmode is None:
        return

    project_id = await sync_to_async(
        lambda: Session.objects.filter(id=session_id).values_list("project_id", flat=True).first()
    )()
    if project_id is None or not await sync_to_async(project_is_untrusted)(project_id):
        return

    clamped = await sync_to_async(clamp_permission_mode_for_untrusted)(
        Provider.CLAUDE_CODE, setmode["mode"]
    )
    if clamped != setmode["mode"]:
        logger.warning(
            "Untrusted project: setMode %r clamped to %r for session %s (approval path)",
            setmode["mode"], clamped, session_id,
        )
        setmode["mode"] = clamped


# Deny message sent when the user cancels an AskUserQuestion. Deliberately different
# from the native CLI cancel (which hard-interrupts the turn — that surfaces as a
# "terminated due to error" toast in the GUI): we keep the turn alive, exactly like
# "partial", and have the agent acknowledge the decline and hand control back. The SDK
# forwards this verbatim as the tool_result content; a non-empty message also avoids
# the empty ``is_error`` API rejection.
_QUESTION_CANCEL_MESSAGE = (
    "The user chose not to answer these questions. Acknowledge this briefly and ask "
    "them how they would like to proceed."
)


def _build_clarify_message(questions: list[dict], answers: dict) -> str:
    """Reproduce Claude Code's native "clarify" tool result for a partially
    answered ``AskUserQuestion``.

    When the user answers some but not all questions, the Claude Code CLI rejects
    the tool with this exact text: a fixed preamble telling the agent the user
    wants to clarify, followed by every question in order with its answer (or
    ``(No answer provided)``). Matching it verbatim means the agent receives the
    same signal it would from the native TUI.

    ``questions`` is the original ``input.questions`` list; ``answers`` maps a
    question's text to the user's answer (absent or empty for unanswered ones).
    """
    lines = [
        "The user wants to clarify these questions.",
        "    This means they may have additional information, context or questions for you.",
        "    Take their response into account and then reformulate the questions if appropriate.",
        "    Start by asking them what they would like to clarify.",
        "",
        "    Questions asked:",
    ]
    for question in questions:
        text = question.get("question", "")
        lines.append(f'- "{text}"')
        answer = answers.get(text)
        if answer:
            lines.append(f"  Answer: {answer}")
        else:
            lines.append("  (No answer provided)")
    return "\n".join(lines)


class ClaudeCodeWSHandler:
    """Routes Claude Code-specific WebSocket messages to dedicated handlers.

    Instantiated once per WebSocket connection by the main ``WSConsumer``,
    which passes itself in so handlers can call ``self.consumer.send_json()``,
    access ``self.consumer.channel_layer``, etc.
    """

    def __init__(self, consumer):
        self.consumer = consumer

    async def get_connect_messages(self) -> AsyncIterator[dict]:
        """Yield messages to send to a newly connected client.

        Each yielded dict is a fully-formed message ready to be sent. CC-only
        messages have their ``type`` already set to ``"claude_code:<action>"``;
        cross-provider messages (e.g. ``usage_updated``) keep the generic
        type and carry the provider info inside their payload. The consumer
        applies the client's ``subscribe`` filter before sending.
        """
        # Claude Code CLI authentication state
        yield await get_auth_message_for_connection()

        # Latest Claude Code usage snapshot (wire type: ``usage_updated``)
        yield await get_usage_message_for_connection(Provider.CLAUDE_CODE)

    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Claude Code-prefixed message.

        Args:
            action: The message subtype, i.e. the part after the ``claude_code:`` prefix.
            content: The full message dict.

        Returns:
            True if the action was recognized and handled, False otherwise.
        """
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True

        if action == "check_auth":
            # Forced re-check of Claude Code CLI auth state. ``probe=True`` because
            # this is the user-initiated "Check again": we don't trust the local
            # ``claude auth status`` "logged-in" verdict and confirm it with a real
            # throwaway API call (a few seconds). The result is broadcast to the
            # entire "updates" group so every connected client refreshes.
            await check_auth_and_broadcast(force=True, probe=True)
            return True

        if action == "check_usage":
            # User-initiated usage refresh (sidebar "Refresh now" button). Unlike
            # the macOS background loop — which skips the OAuth token refresh to
            # avoid an unprompted Keychain dialog — this path allows the refresh,
            # so any Keychain prompt is now tied to an explicit click. The result
            # is broadcast with reason="manual" so the requesting client can tell
            # its on-demand round-trip apart from a periodic background tick.
            snapshot = await fetch_and_save_usage(allow_refresh=True)
            await broadcast_usage_updated(Provider.CLAUDE_CODE, snapshot is not None, reason="manual")
            return True

        return False

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Handle a pending request response from the user.

        Routes the user's decision (tool approval or clarifying question answer)
        to the correct agent via the ClaudeCodeAgentManager.

        Expected content for tool approval:
        {
            "type": "claude_code:pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "tool_approval",
            "decision": "allow" | "deny",
            "message": "optional reason for deny",
            "updated_input": { ... }  // optional, for approve with modifications
            "updated_permissions": [ ... ]  // optional, checked permission suggestions
        }

        Expected content for ask_user_question:
        {
            "type": "claude_code:pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "ask_user_question",
            "action": "submit" | "partial" | "cancel",  // default "submit"
            "answers": {
                "question text": "selected label or free text",
                ...
            }
        }
        - "submit"  : every question answered → allow the tool with the answers.
        - "partial" : some answered → deny with the native "clarify" message
                      (answered ones listed, the rest "(No answer provided)").
        - "cancel"  : declined → deny (no interrupt) with a fixed message asking the
                      agent to acknowledge and ask how to proceed.

        Expected content for an MCP elicitation (shared Elicitation*Body
        components — they carry ``tool_name``, never ``request_type``):
        {
            "type": "claude_code:pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "tool_name": "elicitationForm" | "elicitationUrl",
            "action": "accept" | "decline" | "cancel",
            "content": {...},  // accept only (filled form values)
        }
        The validated ``{action, content?}`` dict is handed verbatim to the
        elicitation bridge, which writes it back as the CLI control response.
        """
        try:
            ensure_provider_running(Provider.CLAUDE_CODE)
        except ProviderDisabledError as e:
            await self.consumer.send_json({
                "type": "error",
                "code": "provider_disabled",
                "provider": e.provider.value,
                "message": str(e),
            })
            return

        session_id = content.get("session_id")
        request_type = content.get("request_type")
        request_id = content.get("request_id")

        if not session_id or not request_id:
            logger.warning(
                "pending_request_response missing required fields: "
                "session_id=%s, request_id=%s",
                session_id, request_id,
            )
            return

        manager = get_claude_code_agent_manager()

        # MCP elicitations first: their payload is keyed by ``tool_name`` (the
        # shared bodies emit no ``request_type``), and their response is a raw
        # wire dict, not a PermissionResult.
        if content.get("tool_name") in ELICITATION_TOOL_NAMES:
            response = self._build_elicitation_response(content)
            if response is None:
                # Validation failed (already logged). Resolve with the safe
                # default so the elicitation — and the MCP tool call awaiting
                # it — isn't left hanging.
                response = default_elicitation_response()
            resolved = await manager.resolve_pending_request(session_id, request_id, response)
            if not resolved:
                logger.warning(
                    "pending_request_response: failed to resolve elicitation %s for "
                    "session %s (no matching pending request or already resolved)",
                    request_id, session_id,
                )
            return

        if not request_type:
            logger.warning(
                "pending_request_response missing request_type for session %s (request %s)",
                session_id, request_id,
            )
            return

        if request_type == "tool_approval":
            decision = content.get("decision")
            if decision == "allow":
                updated_input = content.get("updated_input")

                # Reconstruct accepted permission suggestions (if any) from the frontend
                updated_permissions = None
                raw_permissions = content.get("updated_permissions")
                if raw_permissions:
                    # Clamp a setMode escalation to the trust floor *before* it
                    # reaches the SDK or the DB write below — both read this list.
                    await _clamp_setmode_permissions_for_trust(session_id, raw_permissions)
                    updated_permissions = [_permission_update_from_dict(p) for p in raw_permissions]

                response = PermissionResultAllow(
                    updated_input=updated_input,
                    updated_permissions=updated_permissions,
                )
                logger.debug("Tool approval allowed for session %s with responses=%s", session_id, response)
            else:
                message = content.get("message", "User denied this action")
                response = PermissionResultDeny(message=message)

        elif request_type == "ask_user_question":
            action = content.get("action", "submit")
            answers = content.get("answers", {})
            # Retrieve the original questions from the matching pending request
            process_info = manager.get_agent_info(session_id)
            matching = None
            if process_info is not None:
                matching = next(
                    (pr for pr in process_info.pending_requests if pr.request_id == request_id),
                    None,
                )
            if matching is None:
                logger.warning(
                    "pending_request_response: no pending request %s for session %s",
                    request_id, session_id,
                )
                return
            original_questions = matching.tool_input.get("questions", [])

            if action == "cancel":
                # User declined to answer. Same mechanism as "partial" — a plain deny
                # (no interrupt) so the agent stays alive and processes the message —
                # but with a fixed text telling it to acknowledge the decline and ask
                # how to proceed.
                response = PermissionResultDeny(message=_QUESTION_CANCEL_MESSAGE)
            elif action == "partial":
                # User answered some but not all questions. Deny with the native
                # "clarify" message so the agent sees the partial answers.
                response = PermissionResultDeny(
                    message=_build_clarify_message(original_questions, answers)
                )
            else:
                response = PermissionResultAllow(
                    updated_input={
                        "questions": original_questions,
                        "answers": answers,
                    }
                )

        else:
            logger.warning(
                "pending_request_response: unknown request_type %r",
                request_type,
            )
            return

        # Persist setMode suggestions in DB so future resumes use the correct mode
        if request_type == "tool_approval" and content.get("decision") == "allow":
            raw_permissions = content.get("updated_permissions")
            if raw_permissions:
                for perm in raw_permissions:
                    if perm.get("type") == "setMode" and perm.get("mode"):
                        await update_session_permission_mode(session_id, perm["mode"])
                        logger.info(
                            "Permission mode updated to %r for session %s (from setMode suggestion)",
                            perm["mode"],
                            session_id,
                        )
                        break  # Only one setMode should be applied

        resolved = await manager.resolve_pending_request(session_id, request_id, response)
        if not resolved:
            logger.warning(
                "pending_request_response: failed to resolve request %s for session %s "
                "(no matching pending request or already resolved)",
                request_id, session_id,
            )

    # Actions an elicitation response may carry (MCP ElicitationResult).
    _ELICITATION_ACTIONS: frozenset[str] = frozenset({"accept", "decline", "cancel"})

    def _build_elicitation_response(self, content: dict) -> dict | None:
        """Validate an elicitation answer into the CLI wire response.

        Mirrors the Codex builder (``CodexWSHandler._build_elicitation_response``)
        minus the ``persist`` variant (Claude has no MCP-tool-call elicitation
        sub-kind). Returns ``None`` on any validation failure — the caller
        substitutes the safe ``cancel`` default. ``content`` (the filled form
        values) is only allowed with ``accept`` and is omitted from the
        response when absent (the CLI schema marks it optional).
        """
        tool_name = content.get("tool_name")
        action = content.get("action")
        if not isinstance(action, str) or action not in self._ELICITATION_ACTIONS:
            logger.error(
                "claude_code %s: invalid action=%r (payload=%r)", tool_name, action, content,
            )
            return None
        form_content = content.get("content")
        if action != "accept" and form_content is not None:
            logger.error(
                "claude_code %s: content only allowed with accept (action=%r)",
                tool_name, action,
            )
            return None
        response: dict = {"action": action}
        if form_content is not None:
            if not isinstance(form_content, dict):
                logger.error(
                    "claude_code %s: invalid content type=%r (expected dict)",
                    tool_name, type(form_content).__name__,
                )
                return None
            response["content"] = form_content
        return response
