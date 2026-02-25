"""
Raw SDK message logger.

Intercepts all messages sent to and received from the Claude CLI subprocess
and writes them as raw JSON lines to a log file in <data_dir>/logs/sdk/.

Each session gets its own log file: <data_dir>/logs/sdk/{session_id}.jsonl
Each line is a JSON object with:
  - "direction": "sent" or "received"
  - "timestamp": ISO 8601 timestamp
  - "data": the raw message dict (exactly as the SDK sends/receives it)
"""

import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import Message

from twicc.paths import get_sdk_logs_dir

logger = logging.getLogger(__name__)

# Resolve once at import time — SDK logs go in <data_dir>/logs/sdk/
LOGS_DIR = get_sdk_logs_dir()


def _get_log_path(session_id: str) -> Path:
    """Return the log file path for a given session."""
    return LOGS_DIR / f"{session_id}.jsonl"


def _write_log_line(log_path: Path, direction: str, data: Any) -> None:
    """Append a single JSON line to the log file.

    This is intentionally synchronous and simple: the SDK message loop is
    already async, and we want to minimize overhead. File I/O for a single
    short line is fast enough to not warrant async file writes.
    """
    line = {
        "direction": direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.exception("Failed to write SDK log line to %s", log_path)


def patch_client(client: ClaudeSDKClient, session_id: str) -> None:
    """Monkeypatch a ClaudeSDKClient instance to log all sent/received messages.

    This replaces `receive_messages()` and `query()` on the *instance* (not the class)
    so that each message is logged as raw JSON before being parsed or after being
    serialized.

    Args:
        client: The SDK client instance to patch.
        session_id: The session ID (used for the log filename).
    """
    log_path = _get_log_path(session_id)

    # Ensure the directory exists (should already, but be safe)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug("SDK logger attached for session %s -> %s", session_id, log_path)

    # --- Patch receive_messages ---
    # Original: iterates over self._query.receive_messages() and calls parse_message().
    # We intercept the raw dict before parse_message.

    original_receive = client.receive_messages

    async def patched_receive_messages() -> AsyncIterator[Message]:
        # Access the internal query stream for raw dicts
        async for data in client._query.receive_messages():
            _write_log_line(log_path, "received", data)
            yield parse_message(data)

    client.receive_messages = patched_receive_messages  # type: ignore[assignment]

    # --- Patch query ---
    # Original: serializes to JSON and writes to transport.
    # We intercept the dict/string before it's written.

    original_query = client.query

    async def patched_query(
        prompt: str | AsyncIterable[dict[str, Any]], session_id: str = "default"
    ) -> None:
        if isinstance(prompt, str):
            # SDK wraps strings into a message dict
            message = {
                "type": "user",
                "message": {"role": "user", "content": prompt},
                "parent_tool_use_id": None,
                "session_id": session_id,
            }
            _write_log_line(log_path, "sent", message)
            await original_query(prompt, session_id)
        else:
            # AsyncIterable[dict] — wrap it to log each message

            async def _logging_stream() -> AsyncIterator[dict[str, Any]]:
                async for msg in prompt:
                    _write_log_line(log_path, "sent", msg)
                    yield msg

            await original_query(_logging_stream(), session_id)

    client.query = patched_query  # type: ignore[assignment]
