"""Gemini provider WebSocket handler.

Handles the ``gemini:pending_request_response`` action: the user's answer to
a tool approval raised by the Gemini CLI over ACP
(``session/request_permission``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import Provider
from twicc.providers.state import ProviderDisabledError, ensure_provider_running

logger = logging.getLogger(__name__)


class GeminiWSHandler:
    """Routes Gemini-specific WebSocket messages (one instance per connection)."""

    def __init__(self, consumer):
        self.consumer = consumer

    async def get_connect_messages(self) -> AsyncIterator[dict]:
        """Nothing provider-specific to push on connect."""
        return
        yield  # pragma: no cover - makes this an async generator

    async def dispatch(self, action: str, content: dict) -> bool:
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True
        return False

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Resolve an approval with the chosen ACP option.

        Wire shape::

            {"type": "gemini:pending_request_response",
             "session_id": "...", "request_id": "...", "option_id": "proceed_once"}

        The agent validates ``option_id`` against the options it offered and
        rejects on anything else; invalid messages are logged and dropped.
        """
        try:
            ensure_provider_running(Provider.GEMINI)
        except ProviderDisabledError as e:
            await self.consumer.send_json({
                "type": "error",
                "code": "provider_disabled",
                "provider": e.provider.value,
                "message": str(e),
                **({"ui_message": e.ui_message} if getattr(e, "ui_message", None) is not None else {}),
            })
            return

        session_id = content.get("session_id")
        request_id = content.get("request_id")
        option_id = content.get("option_id")
        if not isinstance(session_id, str) or not isinstance(request_id, str) or not isinstance(option_id, str):
            logger.warning(
                "gemini:pending_request_response with invalid fields (session_id=%r, request_id=%r, option_id=%r)",
                session_id, request_id, option_id,
            )
            return
        manager = get_agent_manager_registry().get(Provider.GEMINI)
        resolved = await manager.resolve_pending_request(session_id, request_id, {"option_id": option_id})
        if not resolved:
            logger.warning(
                "gemini:pending_request_response: no pending request %r for session %r", request_id, session_id,
            )
