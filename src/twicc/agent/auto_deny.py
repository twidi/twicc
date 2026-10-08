"""Auto-deny of unanswered approval requests.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md``. In Claude
``bypassPermissions`` the CLI still prompts for some dangerous actions, and
nobody is expected to watch. TwiCC denies such a prompt after
``AUTO_DENY_DELAY_SECONDS`` without an answer, so the agent continues, like the
Claude Code CLI does. The timer lives in ``BaseAgent._await_pending_request``;
the provider decides what is armed and with which deny value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .states import PendingRequest

AUTO_DENY_DELAY_SECONDS = 120

# ``loop.call_later`` runs on the monotonic clock, which does not advance
# during a machine suspend. The timer therefore re-checks the wall-clock
# deadline at most this far apart, bounding the delay after a resume.
AUTO_DENY_CHECK_INTERVAL_SECONDS = 5.0

AUTO_DENY_MESSAGE = (
    f"The user did not answer this permission request within {AUTO_DENY_DELAY_SECONDS // 60} minutes, "
    "so TwiCC denied it automatically. The action looked potentially dangerous. "
    "Do not retry it as is: find another way to reach your goal, or explain to the user what you need."
)

# Claude tools whose prompt in ``bypassPermissions`` is a safety check on a
# potentially dangerous action: the CLI's shell tools (``Monitor`` runs a shell
# command too) and file-write tools. Anything else — questions, plan reviews,
# interactive or MCP tools, a future unknown tool — keeps waiting for the human.
# ``MultiEdit`` is absent from current CLIs; keeping it covers older ones.
AUTO_DENY_TOOLS = frozenset({"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"})


def auto_deny_remaining(pending: PendingRequest, now: float) -> float | None:
    """Seconds left before ``pending`` is auto-denied, floored at 0.

    ``None`` when the request is not armed. ``now`` is a wall-clock instant
    (``time.time()``), the clock ``auto_deny_at`` uses.
    """
    if pending.auto_deny_at is None:
        return None
    return max(0.0, round(pending.auto_deny_at - now, 1))
