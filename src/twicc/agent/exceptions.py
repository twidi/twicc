"""Agent-related exception types shared across providers."""

from twicc.core.session_availability import SESSION_UNAVAILABLE

AGENT_START_FAILED = "agent_start_failed"


class SendDeliveryError(RuntimeError):
    """A message could not be delivered to the agent.

    Raised anywhere on the send path BEFORE the agent has taken ownership
    of the message (state guards, hybrid composer protection, transport
    failures). The WS ``send_message`` handler turns it into an ``error``
    frame whose ``code`` lets the frontend run its recovery flow: restore
    the composer draft and drop the optimistic chat message.

    Known codes: ``agent_starting``, ``agent_dead``,
    ``hybrid_composer_busy``, ``agent_start_failed``, and the generic default
    ``send_failed``.
    """

    def __init__(self, message: str, *, code: str = "send_failed", ui_message: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.ui_message = ui_message if ui_message is not None else getattr(message, "ui_message", None)

    def __str__(self) -> str:
        # Retain UiMessageText metadata through shared send error boundaries.
        return self.args[0]


class AgentStartError(SendDeliveryError):
    """The agent did not start, so it never took ownership of the message.

    Built by :mod:`twicc.agent.start_failure`, which owns the classification
    and the messages. ``reason`` is one of its ``REASON_*`` constants.
    ``diagnostic`` is the bounded provider text of a ``start_failed`` reason,
    and ``None`` for every other reason and for every ephemeral run.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        diagnostic: str | None = None,
        ui_message: dict | None = None,
    ) -> None:
        super().__init__(message, code=AGENT_START_FAILED, ui_message=ui_message)
        self.reason = reason
        self.diagnostic = diagnostic


class SessionUnavailableError(SendDeliveryError):
    """The session's history is unavailable: TwiCC refuses to read or write it.

    Raised through :mod:`twicc.core.session_availability`, which owns the rule
    and the message. A ``SendDeliveryError``, so every send path reports it
    with its ``code`` (``session_unavailable``).
    """

    def __init__(self, message: str, *, session_id: str, reason: str) -> None:
        super().__init__(message, code=SESSION_UNAVAILABLE)
        self.session_id = session_id
        self.reason = reason
