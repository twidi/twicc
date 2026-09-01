"""
Codex provider WebSocket handler.

Handles auth + usage traffic:
- emits ``codex:auth_updated`` and the latest usage snapshot on each new
  connection (the upstream status travels in the cross-provider
  ``providers_status_updated`` frame, sent by the main consumer);
- routes the inbound ``codex:check_auth`` action (manual "Check again"
  from the UI) to a forced re-check + broadcast.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from twicc.core.enums import Provider
from twicc.providers.state import ProviderDisabledError, ensure_provider_running
from twicc.usage_task import broadcast_usage_updated, get_usage_message_for_connection

from .auth import check_and_broadcast, get_auth_message_for_connection
from .usage import fetch_and_save_usage

from twicc.agent.registry import get_agent_manager_registry

logger = logging.getLogger(__name__)


class CodexWSHandler:
    """Routes Codex-specific WebSocket messages to dedicated handlers.

    Instantiated once per WebSocket connection by the main ``WSConsumer``,
    which passes itself in so handlers can call ``self.consumer.send_json()``,
    access ``self.consumer.channel_layer``, etc.
    """

    def __init__(self, consumer):
        self.consumer = consumer

    async def get_connect_messages(self) -> AsyncIterator[dict]:
        """Yield messages to send to a newly connected client."""
        yield await get_auth_message_for_connection()
        # Latest Codex usage snapshot (wire type: ``usage_updated``)
        yield await get_usage_message_for_connection(Provider.CODEX)

    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Codex-prefixed message."""
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True

        if action == "check_auth":
            # Forced re-check of Codex CLI auth state, broadcast to every client.
            # ``probe=True`` because this is the user-initiated "Check again": we
            # don't trust the local ``codex login status`` "logged-in" verdict and
            # confirm it with a real throwaway turn (a few seconds).
            await check_and_broadcast(force=True, probe=True)
            return True

        if action == "check_usage":
            # User-initiated usage refresh (sidebar "Refresh now" button). Unlike
            # the macOS background loop — which skips the OAuth refresh in keyring
            # mode to avoid an unprompted Keychain dialog — this path allows the
            # refresh, so any Keychain prompt now follows an explicit click. The
            # result is broadcast with reason="manual" so the requesting client
            # can tell its on-demand round-trip apart from a background tick.
            snapshot = await fetch_and_save_usage(allow_refresh=True)
            await broadcast_usage_updated(Provider.CODEX, snapshot is not None, reason="manual")
            return True

        return False

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Route the user's decision to the right agent's right future.

        Wire shape (frontend → backend, spec §9.3, §9.5):

            {
                "type": "codex:pending_request_response",
                "session_id": "...",
                "request_id": "...",
                "tool_name": "commandExecution" | "fileChange" | "permissions"
                             | "mcpToolCall" | "elicitationForm" | "elicitationUrl"
                             | "toolRequestUserInput" | "autoReviewDenial",
                "decision": <string-or-dict-variant>,  # see _build_codex_response
                "permissions": {...},   // permissions only
                "scope": "turn" | "session",  // permissions only
                "action": "accept" | "decline" | "cancel",  // elicitations only
                "persist": "session" | "always",  // mcpToolCall accept only
                "content": {...},  // accept only (form values; sent by elicitationForm)
                "answers": {...},  // toolRequestUserInput only
            }

        Invalid / unroutable messages are logged and dropped; we never raise
        through the WS layer (that would tear down the consumer).
        """
        try:
            ensure_provider_running(Provider.CODEX)
        except ProviderDisabledError as e:
            await self.consumer.send_json({
                "type": "error",
                "code": "provider_disabled",
                "provider": e.provider.value,
                "message": str(e),
            })
            return

        session_id = content.get("session_id")
        request_id = content.get("request_id")
        tool_name = content.get("tool_name")

        if not session_id or not request_id or not tool_name:
            logger.warning(
                "codex:pending_request_response missing required fields "
                "(session_id=%r, request_id=%r, tool_name=%r)",
                session_id, request_id, tool_name,
            )
            return

        response = self._build_codex_response(tool_name, content)
        if response is None:
            # Validation failed; _build_codex_response already logged.
            # Resolve with a safe default so the SDK isn't left hanging.
            response = self._safe_default_for(tool_name)

        manager = get_agent_manager_registry().get(Provider.CODEX)
        resolved = await manager.resolve_pending_request(
            session_id, request_id, response,
        )
        if not resolved:
            logger.warning(
                "codex:pending_request_response: failed to resolve %r for session %r "
                "(no matching pending request, or already resolved)",
                request_id, session_id,
            )

    # ------------------------------------------------------------------
    # Validation + response builders (spec §7-Q11: strict)
    # ------------------------------------------------------------------

    # Decisions a string-decision approval (command + file) may carry.
    _SIMPLE_STRING_DECISIONS: set[str] = {
        "accept", "acceptForSession", "decline", "cancel",
    }
    # Object-variant keys for command (network and execpolicy amendments).
    # The mapped tuple is (expected inner-payload key, expected inner-value
    # type) — see spec §1.1.a for the wire shape of each variant.
    _COMMAND_DICT_VARIANTS: dict[str, tuple[str, type]] = {
        "acceptWithExecpolicyAmendment": ("execpolicy_amendment", list),
        "applyNetworkPolicyAmendment":   ("network_policy_amendment", dict),
    }
    _PERMISSIONS_SCOPES: set[str] = {"turn", "session"}
    # Elicitation sub-kinds share one wire shape (McpServerElicitationRequestResponse).
    _ELICITATION_ACTIONS: set[str] = {"accept", "decline", "cancel"}
    _ELICITATION_PERSIST_VALUES: set[str] = {"session", "always"}
    _ELICITATION_TOOL_NAMES: set[str] = {"mcpToolCall", "elicitationForm", "elicitationUrl"}

    def _build_codex_response(self, tool_name: str, content: dict) -> dict | None:
        """Convert the frontend payload to the SDK-wire response dict.

        Returns ``None`` on any validation failure (caller substitutes a
        safe default). Validation rules:
        - command: ``decision`` is either in :attr:`_SIMPLE_STRING_DECISIONS`
          or a dict with exactly one key from :attr:`_COMMAND_DICT_VARIANTS`.
        - file: ``decision`` is in :attr:`_SIMPLE_STRING_DECISIONS` minus
          dict variants (no amendments for file changes — see spec §1.1.b).
        - permissions: ``scope`` ∈ :attr:`_PERMISSIONS_SCOPES`, ``permissions``
          is a dict (may be empty).
        - elicitation (mcpToolCall / elicitationForm / elicitationUrl):
          ``action`` ∈ :attr:`_ELICITATION_ACTIONS`; ``persist``/``content``
          only allowed with ``accept`` (``persist`` further restricted to
          ``mcpToolCall`` and :attr:`_ELICITATION_PERSIST_VALUES`).
        - toolRequestUserInput: ``answers`` is a dict of
          ``{question_id: {"answers": [str, ...]}}`` (may be empty).
        """
        decision = content.get("decision")

        if tool_name == "commandExecution":
            return self._build_command_response(decision)

        if tool_name == "fileChange":
            return self._build_file_response(decision)

        if tool_name == "permissions":
            return self._build_permissions_response(content)

        if tool_name in self._ELICITATION_TOOL_NAMES:
            return self._build_elicitation_response(tool_name, content)

        if tool_name == "toolRequestUserInput":
            return self._build_request_user_input_response(content)

        if tool_name == "autoReviewDenial":
            if isinstance(decision, str) and decision in {"accept", "decline"}:
                return {"decision": decision}
            logger.error(
                "codex autoReviewDenial: invalid decision=%r "
                "(expected 'accept' or 'decline')",
                decision,
            )
            return None

        if tool_name == "planImplementation":
            # TwiCC-owned post-plan prompt (no SDK wire behind it — the agent
            # interprets the decision itself, see
            # ``CodexAgent._prompt_plan_implementation``). ``newSession`` is
            # agent-side identical to ``stay``: the frontend creates the
            # fresh session seeded with the plan itself.
            if isinstance(decision, str) and decision in {"implement", "newSession", "stay"}:
                return {"decision": decision}
            logger.error(
                "codex planImplementation: invalid decision=%r "
                "(expected 'implement', 'newSession' or 'stay')",
                decision,
            )
            return None

        logger.error(
            "codex:pending_request_response: unknown tool_name=%r in %r",
            tool_name, content,
        )
        return None

    def _build_command_response(self, decision: object) -> dict | None:
        if isinstance(decision, str):
            if decision in self._SIMPLE_STRING_DECISIONS:
                return {"decision": decision}
            logger.error(
                "codex commandExecution: invalid string decision=%r", decision,
            )
            return None
        if isinstance(decision, dict):
            keys = list(decision.keys())
            if len(keys) != 1 or keys[0] not in self._COMMAND_DICT_VARIANTS:
                logger.error(
                    "codex commandExecution: invalid dict decision=%r "
                    "(expected exactly one key from %r)",
                    decision, sorted(self._COMMAND_DICT_VARIANTS),
                )
                return None
            variant = keys[0]
            inner = decision[variant]
            if not isinstance(inner, dict):
                logger.error(
                    "codex commandExecution: invalid inner payload for %r — "
                    "expected dict, got %r",
                    variant, type(inner).__name__,
                )
                return None
            inner_key, inner_type = self._COMMAND_DICT_VARIANTS[variant]
            inner_value = inner.get(inner_key)
            if not isinstance(inner_value, inner_type):
                logger.error(
                    "codex commandExecution: invalid inner payload for %r — "
                    "missing or wrong-type %r (expected %s, got %r)",
                    variant, inner_key, inner_type.__name__, type(inner_value).__name__,
                )
                return None
            # Wrap verbatim — Codex expects {"decision": {<variant>: {...}}}.
            return {"decision": decision}
        logger.error("codex commandExecution: invalid decision type=%r", type(decision))
        return None

    def _build_file_response(self, decision: object) -> dict | None:
        if isinstance(decision, str) and decision in self._SIMPLE_STRING_DECISIONS:
            return {"decision": decision}
        logger.error(
            "codex fileChange: invalid decision=%r (must be one of %r — "
            "no amendments allowed for file changes)",
            decision, self._SIMPLE_STRING_DECISIONS,
        )
        return None

    def _build_permissions_response(self, content: dict) -> dict | None:
        scope = content.get("scope")
        permissions = content.get("permissions")
        if not isinstance(scope, str) or scope not in self._PERMISSIONS_SCOPES:
            logger.error(
                "codex permissions: invalid scope=%r (expected %r)",
                scope, self._PERMISSIONS_SCOPES,
            )
            return None
        if not isinstance(permissions, dict):
            logger.error(
                "codex permissions: invalid permissions type=%r (expected dict)",
                type(permissions),
            )
            return None
        # ``strictAutoReview`` is optional + boolean per spec §1.1.c.
        strict_auto_review = content.get("strictAutoReview")
        response: dict = {"permissions": permissions, "scope": scope}
        if isinstance(strict_auto_review, bool):
            response["strictAutoReview"] = strict_auto_review
        return response

    def _build_elicitation_response(self, tool_name: str, content: dict) -> dict | None:
        """Response for the 3 elicitation sub-kinds (wire: McpServerElicitationRequestResponse).

        ``{"action", "content", "_meta"}`` — ``content`` is the filled form on
        an accepted ``elicitationForm``; ``_meta.persist`` is the
        remember-this-choice variant on an accepted ``mcpToolCall``. Both are
        only meaningful with ``accept`` and are rejected otherwise. ``content``
        is deliberately NOT gated per tool_name (an accepted ``mcpToolCall``
        may carry one): Codex's approval parser ignores unexpected content, and
        the looseness keeps the builder simple.
        """
        action = content.get("action")
        if not isinstance(action, str) or action not in self._ELICITATION_ACTIONS:
            # Dump the whole payload: an absent action with a legacy
            # ``decision`` key is the signature of a stale frontend page
            # (pre-elicitation build) answering through the old wire shape.
            logger.error(
                "codex %s: invalid action=%r (payload=%r)", tool_name, action, content,
            )
            return None
        persist = content.get("persist")
        form_content = content.get("content")
        if action != "accept" and (persist is not None or form_content is not None):
            logger.error(
                "codex %s: persist/content only allowed with accept "
                "(action=%r persist=%r)", tool_name, action, persist,
            )
            return None
        response: dict = {"action": action, "content": None, "_meta": None}
        if form_content is not None:
            if not isinstance(form_content, dict):
                logger.error(
                    "codex %s: invalid content type=%r (expected dict)",
                    tool_name, type(form_content).__name__,
                )
                return None
            response["content"] = form_content
        if persist is not None:
            if (
                tool_name != "mcpToolCall"
                or not isinstance(persist, str)
                or persist not in self._ELICITATION_PERSIST_VALUES
            ):
                logger.error(
                    "codex %s: invalid persist=%r", tool_name, persist,
                )
                return None
            response["_meta"] = {"persist": persist}
        return response

    def _build_request_user_input_response(self, content: dict) -> dict | None:
        """Response for ``item/tool/requestUserInput`` (wire: ToolRequestUserInputResponse).

        ``{"answers": {question_id: {"answers": [str, …]}}}`` — an empty map is
        valid (Codex treats a missing answer as a cancel).
        """
        answers = content.get("answers")
        if not isinstance(answers, dict):
            logger.error(
                "codex toolRequestUserInput: invalid answers type=%r",
                type(answers).__name__,
            )
            return None
        normalized: dict = {}
        for question_id, entry in answers.items():
            values = entry.get("answers") if isinstance(entry, dict) else None
            if (
                not isinstance(question_id, str)
                or not isinstance(values, list)
                or not all(isinstance(v, str) for v in values)
            ):
                logger.error(
                    "codex toolRequestUserInput: invalid entry for %r: %r",
                    question_id, entry,
                )
                return None
            normalized[question_id] = {"answers": values}
        return {"answers": normalized}

    def _safe_default_for(self, tool_name: str) -> dict:
        """Wire-safe fallback when the frontend response failed validation.

        Elicitations default to "cancel" rather than "decline": a validation
        failure is not a user refusal, so dismissal (cancel) beats rejection
        (decline). requestUserInput defaults to an empty answer map, which
        Codex treats as a cancel too.
        """
        if tool_name == "permissions":
            return {"permissions": {}, "scope": "turn"}
        if tool_name in self._ELICITATION_TOOL_NAMES:
            return {"action": "cancel", "content": None, "_meta": None}
        if tool_name == "toolRequestUserInput":
            return {"answers": {}}
        if tool_name == "planImplementation":
            # A validation failure is not a "go implement" — stay in Plan mode.
            return {"decision": "stay"}
        return {"decision": "decline"}
