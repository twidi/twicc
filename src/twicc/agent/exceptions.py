"""Agent-related exception types shared across providers."""

from twicc.core.session_availability import SESSION_UNAVAILABLE


class SendDeliveryError(RuntimeError):
    """A message could not be delivered to the agent.

    Raised anywhere on the send path BEFORE the agent has taken ownership
    of the message (state guards, hybrid composer protection, transport
    failures). The WS ``send_message`` handler turns it into an ``error``
    frame whose ``code`` lets the frontend run its recovery flow: restore
    the composer draft and drop the optimistic chat message.

    Known codes: ``agent_starting``, ``agent_dead``,
    ``hybrid_composer_busy``, and the generic default ``send_failed``.
    """

    def __init__(self, message: str, *, code: str = "send_failed") -> None:
        super().__init__(message)
        self.code = code


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
