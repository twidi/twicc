"""Provider error fields and legacy Codex transcript markers.

Current runtimes persist errors in task_complete.error. Older TwiCC histories
carry an injected private marker; compute still reads those legacy markers.
"""

from __future__ import annotations

from typing import NamedTuple

import orjson


PROVIDER_ERROR_MARKER = "<twicc-provider-error>"


class CodexProviderError(NamedTuple):
    """The stable fields persisted for one terminal provider error."""

    turn_id: str
    message: str
    error_type: object | None = None


def build_provider_error_marker(error: CodexProviderError) -> str:
    """Serialize ``error`` as the private text injected into the rollout."""
    payload = {
        "version": 1,
        "turn_id": error.turn_id,
        "message": error.message,
        "error_type": error.error_type,
    }
    return PROVIDER_ERROR_MARKER + orjson.dumps(payload).decode("utf-8")


def parse_provider_error_marker(text: str) -> CodexProviderError | None:
    """Decode a private provider-error marker, returning ``None`` if invalid."""
    stripped = text.strip()
    if not stripped.startswith(PROVIDER_ERROR_MARKER):
        return None
    try:
        payload = orjson.loads(stripped[len(PROVIDER_ERROR_MARKER) :])
    except orjson.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    turn_id = payload.get("turn_id")
    message = payload.get("message")
    if (
        not isinstance(turn_id, str)
        or not turn_id
        or not isinstance(message, str)
        or not message
    ):
        return None
    return CodexProviderError(
        turn_id=turn_id,
        message=message,
        error_type=payload.get("error_type"),
    )
