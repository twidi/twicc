"""The stop reason of an agent and the origin of the request that asked for it, paired.

Design 2026-10-14-timeline-actor-origin, section 7 (*Stale context*). The DEAD transition is often
written from the agent's own long-lived task, whose inherited context is the one of the request that
STARTED the session, not the one that stopped it. So the origin is captured when the reason is
assigned (in the stopping request's own context) and stored next to it; the DEAD hook reads only
``stop_origin``, never the carrier.

The pairing is a property setter, so it holds at EVERY assignment site (provider agents, hybrid
agent, the manager's hard kill) and for any site added later. Never re-assign a deliberate reason you
merely read back from an agent task: the setter would capture that task's stale context.
"""

from __future__ import annotations

from twicc.agent.states import DELIBERATE_STOP_REASONS
from twicc.request_origin import RequestOrigin, capture_origin


class StopOriginMixin:
    @property
    def kill_reason(self) -> str | None:
        # ``getattr``: some tests build agents with ``object.__new__`` and never run ``__init__``.
        return getattr(self, "_kill_reason", None)

    @kill_reason.setter
    def kill_reason(self, reason: str | None) -> None:
        """Assign the reason and, with it, the origin: captured for a deliberate stop, cleared otherwise.

        A deliberate stop (``manual``, ``force``, ``archived``) resolves its origin NOW, default
        included, and the last assignment wins (reason and origin always come from the same request).
        A later non-request reason (``error``, ``shutdown``, ``timeout``, ``apply-settings``...) clears
        the origin so an automatic cause never keeps a stale human origin.
        """
        self._kill_reason = reason
        self._stop_origin = capture_origin() if reason in DELIBERATE_STOP_REASONS else None

    @property
    def stop_origin(self) -> RequestOrigin | None:
        """The origin captured with a deliberate ``kill_reason``; ``None`` for any other reason."""
        return getattr(self, "_stop_origin", None)
