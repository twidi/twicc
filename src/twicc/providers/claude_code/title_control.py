"""Compatibility boundary for Claude's private SDK title control channel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from anyio import BrokenResourceError, ClosedResourceError
from claude_agent_sdk import CLIConnectionError, ProcessError

from twicc.providers.helpers import BaseProviderHelpers, TitleValidationResult
from twicc.core.ui_messages import ui_message_text

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient


class TitleControlError(RuntimeError):
    """A control failure, including uncertain delivery after a timeout.

    No failure permits a transcript write while the managed process is active.
    """

    def __init__(self, reason: Literal["unavailable", "timeout", "rejected"], message: str):
        super().__init__(message)
        self.reason = reason


async def rename_running_session(
    client: ClaudeSDKClient,
    session_id: str,
    title: str,
    *,
    timeout: float = 10.0,
) -> None:
    """Request a rename from the connected CLI; never fall back to file writes.

    The SDK returns normally only after a control acknowledgement. Transcript
    confirmation and publication ordering belong to the publication coordinator.
    """
    query = getattr(client, "_query", None)
    send_request = getattr(query, "_send_control_request", None)
    if not callable(send_request):
        raise TitleControlError("unavailable", "Claude title control is unavailable")

    try:
        await send_request(
            {"subtype": "rename_session", "title": title, "source": "host", "session_id": session_id},
            timeout=timeout,
        )
    except (CLIConnectionError, ProcessError, OSError, BrokenResourceError, ClosedResourceError) as exc:
        # TimeoutError inherits OSError, but its delivery remains uncertain.
        reason = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        raise TitleControlError(reason, str(exc)) from exc
    except Exception as exc:
        # Query wraps anyio's timeout in a plain Exception. CLI control
        # rejections also arrive as plain Exception instances in this SDK.
        reason = "timeout" if isinstance(exc.__cause__, TimeoutError) else "rejected"
        raise TitleControlError(reason, str(exc)) from exc


def can_publish_existing_title(title: str) -> bool:
    """Conservatively allow only legacy text the CLI retains exactly.

    Bundled CLI 2.1.314 trims, replaces control/format characters, and caps
    titles at 200 Unicode code points. Ordinary repeated spaces survive.
    Unsupported legacy values remain importable; resume must not rewrite them.
    This check never validates or canonicalizes a new title request.
    """
    return bool(title) and len(title) <= BaseProviderHelpers.MAX_TITLE_LENGTH and title == title.strip() and title.isprintable()


def canonicalize_claude_title(title: str | None) -> TitleValidationResult:
    """Collapse whitespace and reject characters the CLI cannot retain."""
    if title is not None:
        if any(not character.isspace() and not character.isprintable() for character in title):
            return TitleValidationResult(title=None, error=ui_message_text(
                "Title cannot contain non-printable characters", "backend.claude.title_non_printable",
            ))
        title = " ".join(title.split())
    return BaseProviderHelpers().validate_title(title)
