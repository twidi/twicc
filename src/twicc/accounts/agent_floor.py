"""Security floor for agents driven by member accounts.

A member's agent runs with the instance account's OS privileges, so TwiCC
never lets one run without guardrails: a session a member has sent to is
treated exactly like a session in an UNTRUSTED project — permission mode
clamped to the provider's untrusted-allowed set (no ``bypassPermissions`` /
``yolo``), repo-controlled settings sources ignored. The provider agents ask
:func:`requires_untrusted_floor` at the same three places they already
resolve project trust (build time and live re-clamp).

This is a guardrail, not a sandbox: an agent allowed to run shell commands can
still read anything the instance account can. The design doc spells that out.

The registry is in-memory: an agent is (re)built on a send, and a member's
send always re-marks the session first. Once marked, a session stays clamped
for the life of the process — even when an admin continues it — which errs on
the safe side.
"""

from __future__ import annotations

import threading

_member_driven: set[str] = set()
_lock = threading.Lock()


def mark_member_driven(session_id: str) -> None:
    if session_id:
        with _lock:
            _member_driven.add(session_id)


def is_member_driven(session_id: str | None) -> bool:
    if not session_id:
        return False
    with _lock:
        return session_id in _member_driven


def requires_untrusted_floor(project_id: str | None, session_id: str | None) -> bool:
    """The trust floor's single question: project untrusted, or session member-driven."""
    from twicc.core.services.trust import project_is_untrusted

    return is_member_driven(session_id) or project_is_untrusted(project_id)
