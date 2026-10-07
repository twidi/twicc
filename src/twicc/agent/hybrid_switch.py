"""Session ids whose switch to hybrid CLI mode is in flight (composer attachments design §6.2).

The WS ``set_session_hybrid`` handler adds an id synchronously before it spawns the detached
switch, and removes it once the switch ends (success, failure or cancellation), always AFTER the
switch wrote ``Session.hybrid``. The composer sends ``set_session_hybrid`` and ``send_message``
back to back, so a send planned while the switch runs must already target the hybrid CLI.
Import-free: the attachment plan target reads it from outside ``asgi.py``.
"""

_PENDING_HYBRID_SWITCHES: set[str] = set()


def is_hybrid_switch_pending(session_id: str) -> bool:
    """True while a switch of *session_id* to hybrid CLI mode is in flight."""
    return session_id in _PENDING_HYBRID_SWITCHES
