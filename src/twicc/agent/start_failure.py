"""Classify the reasons why an agent did not start, and word them.

Provider-agnostic: every launch path turns its failure into an
:class:`~twicc.agent.exceptions.AgentStartError` here, so that each provider
reports the same reasons with the same messages.
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os

from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message

from .exceptions import AgentStartError, SendDeliveryError

logger = logging.getLogger(__name__)

DIAGNOSTIC_MAX_CHARS = 500

REASON_WORKING_DIRECTORY_MISSING = "working_directory_missing"
REASON_CLI_NOT_FOUND = "cli_not_found"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_START_FAILED = "start_failed"


def _ephemeral_failure(reason: str) -> AgentStartError:
    """Build the generic failure of an ephemeral run, which must reveal nothing."""
    return AgentStartError(
        "The ephemeral run could not start.",
        reason=reason,
        ui_message=ui_message("backend.agent.ephemeralStartFailed"),
    )


def _provider_label(provider: Provider) -> str:
    # Imported here: twicc.agent must not import the provider packages at module load.
    from twicc.providers.helpers import get_provider_helpers

    return get_provider_helpers(provider).LABEL


def _cause_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` and each explicit ``__cause__``, stopping on a cycle."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def _bounded_diagnostic(exc: BaseException) -> str:
    """Return ``str(exc)`` or its type name if empty, cut to :data:`DIAGNOSTIC_MAX_CHARS` characters."""
    text = str(exc) or type(exc).__name__
    if len(text) <= DIAGNOSTIC_MAX_CHARS:
        return text
    return f"{text[:DIAGNOSTIC_MAX_CHARS - 1]}…"


def _own_ui_message(exc: BaseException) -> dict | None:
    """Return the UI message that ``exc`` carries, or ``None``."""
    descriptor = getattr(exc, "ui_message", None)
    if descriptor is None and exc.args:
        descriptor = getattr(exc.args[0], "ui_message", None)
    return descriptor


def launch_directory_failure(
    directory: str, *, provider: Provider, ephemeral: bool = False,
) -> AgentStartError | None:
    """Return the failure for an unusable launch directory, or ``None`` if it exists."""
    if os.path.isdir(directory):
        return None
    if ephemeral:
        return _ephemeral_failure(REASON_WORKING_DIRECTORY_MISSING)
    label = _provider_label(provider)
    return AgentStartError(
        f"{label} did not start: the working directory {directory} does not exist.",
        reason=REASON_WORKING_DIRECTORY_MISSING,
        ui_message=ui_message(
            f"backend.agent.startFailed.{REASON_WORKING_DIRECTORY_MISSING}",
            {"provider": label, "directory": directory},
        ),
    )


def classify_start_failure(
    exc: BaseException, *, directory: str, provider: Provider, ephemeral: bool = False,
) -> SendDeliveryError:
    """Turn a launch exception into the error reported to the user.

    A :class:`SendDeliveryError` already carries its own report, so it is
    returned unchanged. A missing directory then takes priority, because this
    test does not depend on provider error text.
    """
    if isinstance(exc, SendDeliveryError):
        return exc

    directory_failure = launch_directory_failure(directory, provider=provider, ephemeral=ephemeral)
    if directory_failure is not None:
        return directory_failure

    # Imported here: twicc.agent must not import the provider packages at module load.
    from claude_agent_sdk import CLINotFoundError

    from twicc.providers.claude_code.bin import ClaudeBinaryNotFound
    from twicc.providers.codex.bin import CodexRuntimeNotReady
    from twicc.providers.codex.runtime import CodexRuntimeError

    chain = list(_cause_chain(exc))
    if any(isinstance(error, (CLINotFoundError, ClaudeBinaryNotFound, CodexRuntimeNotReady, CodexRuntimeError))
           for error in chain):
        reason = REASON_CLI_NOT_FOUND
    elif any(isinstance(error, PermissionError) for error in chain):
        reason = REASON_PERMISSION_DENIED
    else:
        reason = REASON_START_FAILED

    if ephemeral:
        return _ephemeral_failure(reason)

    label = _provider_label(provider)
    key = f"backend.agent.startFailed.{reason}"
    if reason == REASON_CLI_NOT_FOUND:
        return AgentStartError(
            f"{label} did not start: its program was not found.",
            reason=reason,
            ui_message=ui_message(key, {"provider": label}),
        )
    if reason == REASON_PERMISSION_DENIED:
        return AgentStartError(
            f"{label} did not start: the system refused access.",
            reason=reason,
            ui_message=ui_message(key, {"provider": label}),
        )

    diagnostic = _bounded_diagnostic(exc)
    return AgentStartError(
        f"{label} did not start: {diagnostic}",
        reason=reason,
        diagnostic=diagnostic,
        ui_message=ui_message(key, {"provider": label, "diagnostic": _own_ui_message(exc) or diagnostic}),
    )


def log_start_failure(exc: BaseException, *, session_id: str, ephemeral: bool) -> None:
    """Log a start failure. An ephemeral failure logs only the exception type."""
    if ephemeral:
        logger.error("Ephemeral agent failed to start (%s)", type(exc).__name__)
    else:
        logger.error("Agent failed to start for session %s", session_id, exc_info=exc)
