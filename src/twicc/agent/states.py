"""
Provider-agnostic agent state types.

These types define the runtime snapshot that every agent provider exposes to
the rest of TwiCC (WebSocket layer, views, watchers). Provider-specific
extras (Claude permission suggestions, Codex tool approvals, ...) are folded
into the same fields when shape-compatible; the serializer omits empty fields.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

import psutil

from twicc.core.enums import Provider


def format_bytes(size: int) -> str:
    """Format a byte size into a human-readable string (e.g. ``"123.4 MB"``)."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_process_memory(pid: int) -> int | None:
    """Return RSS memory in bytes for ``pid``, or ``None`` if unavailable."""
    try:
        process = psutil.Process(pid)
        return process.memory_info().rss
    except Exception:
        # NoSuchProcess, AccessDenied, ZombieProcess, OSError, ...
        return None


# Kill reasons that mean "a human deliberately stopped this session". Set at
# the three deliberate-stop call sites, each shared by the UI and the CLI/MCP:
#
# - ``manual``   — the Stop gesture (``kill_process`` over the WS,
#                  ``process stop`` / ``processes stop``)
# - ``force``    — the hard-kill gesture (Shift-click on Stop, Shift +
#                  triple-Escape, ``process stop --force``); the default reason
#                  of :meth:`BaseAgentManager.hard_kill_agent`
# - ``archived`` — archiving a session (both surfaces go through
#                  ``core.services.session_update.apply_session_archived_change``)
#
# Providers read this to decide what must NOT survive the death: a deliberate
# stop may not resurrect the session (Claude Code drops the ProcessRun row, so
# neither the runtime nor the boot cron restart can bring it back). Every other
# reason — ``shutdown``, ``apply-settings``, ``switch-hybrid``, ``error``, an
# unsolicited crash — is a death nobody asked for, where restoring is the point.
DELIBERATE_STOP_REASONS = frozenset({"manual", "force", "archived"})


@dataclass(frozen=True)
class PendingRequest:
    """A request from an agent that is waiting for user response.

    Covers tool approvals (the agent wants permission to run a tool) and
    clarifying questions (the agent needs the user to choose between options).
    Provider-specific metadata such as ``permission_suggestions`` stays
    optional so other providers can populate the common fields and ignore the
    rest.
    """

    request_id: str
    request_type: str
    tool_name: str
    tool_input: dict
    created_at: float
    permission_suggestions: list[dict] | None = None


class AgentState(StrEnum):
    """Lifecycle state of an agent.

    States:
        STARTING: The agent is initializing, before the first message is processed.
        ASSISTANT_TURN: The agent is working on a response.
        USER_TURN: The agent is waiting for user input (response complete).
        DEAD: The agent has terminated (error, kill, or shutdown).
    """

    STARTING = "starting"
    ASSISTANT_TURN = "assistant_turn"
    USER_TURN = "user_turn"
    DEAD = "dead"


class AgentInfo(NamedTuple):
    """Immutable snapshot of agent state for external consumption.

    The provider-specific fields (``pending_requests``, ``active_tools``,
    ``last_started_tool_id``) default to empty for providers that do not
    populate them; the serializer omits empty values.

    ``provider`` carries the provider key (e.g. ``Provider.CLAUDE_CODE``) so
    that multi-provider consumers can route or filter without re-querying the
    DB.
    """

    session_id: str
    project_id: str
    provider: Provider
    state: AgentState
    previous_state: AgentState | None
    started_at: float
    state_changed_at: float
    last_activity: float
    error: str | None = None
    memory_rss: int | None = None
    kill_reason: str | None = None
    pending_requests: tuple[PendingRequest, ...] = ()
    active_tools: tuple[dict, ...] = ()
    last_started_tool_id: str | None = None
    # Provider/mode-specific live process state, broadcast verbatim to the
    # front (both live and in the initial active_processes snapshot). Absent
    # for normal sessions; hybrid Claude sessions carry
    # ``{"mode": "hybrid", "terminal_blocked": bool}``.
    extra: dict | None = None
    # True while a stop (``kill_agent``) is in flight but the process hasn't
    # died yet. Carried verbatim to the front (live ``process_state`` + the
    # initial ``active_processes`` snapshot) so the "stopping" spinner survives
    # a WS reconnect or page refresh. In-memory only — a backend restart kills
    # or re-adopts the agent, so there is nothing to persist.
    stopping: bool = False
    # Status-line override the front must show *instead of* a bare "thinking",
    # or None when the agent's own live activity speaks for itself. Set only
    # while a turn ended held open by something the agent is waiting on
    # ("waiting for 2 subagents", "monitoring", "waiting for scheduled
    # wakeup (14:05)", "compacting"). Recomputed on every read from the live
    # hold reasons — never a stored copy, which would keep announcing two
    # subagents once only one is left. Travels on the live ``process_state``
    # AND in the ``active_processes`` snapshot, for the same reason as
    # ``stopping``: the ``process_label`` message is a one-shot, and a client
    # that connects (or reconnects, or reloads) afterwards would otherwise
    # see a session working on nothing, with no explanation.
    label: str | None = None
    # What still runs behind the agent's back, whatever ``state`` says: live
    # subagents, background shells, Monitors, a pending scheduled wake-up, a
    # ``/goal`` continuation. ``None`` when nothing does. Orthogonal to
    # ``state`` on purpose — a background shell keeps running in
    # ``USER_TURN``. Built by :meth:`BaseAgent.current_background_work`
    # (see :func:`build_background_work` for the shape), recomputed on every
    # read like ``label``.
    background_work_in_progress: dict | None = None

    @property
    def memory_rss_human(self) -> str | None:
        """Human-readable memory usage, or ``None`` when ``memory_rss`` is missing."""
        if self.memory_rss is None:
            return None
        return format_bytes(self.memory_rss)


def serialize_agent_info(info: AgentInfo) -> dict:
    """Serialize an ``AgentInfo`` to a JSON-friendly dict."""
    data = {
        "session_id": info.session_id,
        "project_id": info.project_id,
        "provider": info.provider,
        "state": info.state,  # StrEnum serializes directly
        "started_at": info.started_at,
        "state_changed_at": info.state_changed_at,
    }
    if info.error is not None:
        data["error"] = info.error
    if info.memory_rss is not None:
        data["memory"] = info.memory_rss
    if info.kill_reason is not None:
        data["kill_reason"] = info.kill_reason
    if info.pending_requests:
        serialized = []
        for pr in info.pending_requests:
            entry = {
                "request_id": pr.request_id,
                "request_type": pr.request_type,
                "tool_name": pr.tool_name,
                "tool_input": pr.tool_input,
                "created_at": pr.created_at,
            }
            if pr.permission_suggestions:
                entry["permission_suggestions"] = pr.permission_suggestions
            serialized.append(entry)
        data["pending_requests"] = serialized
    if info.active_tools:
        data["active_tools"] = list(info.active_tools)
    if info.last_started_tool_id is not None:
        data["last_started_tool_id"] = info.last_started_tool_id
    if info.extra:
        data["extra"] = info.extra
    if info.stopping:
        data["stopping"] = True
    if info.label:
        data["label"] = info.label
    if info.background_work_in_progress:
        data["background_work_in_progress"] = info.background_work_in_progress
    return data


def build_background_work(
    *,
    subagents: int = 0,
    shells: int = 0,
    monitors: int = 0,
    scheduled_wakeup_at: float | None = None,
    goal: bool = False,
) -> dict | None:
    """Build the provider-agnostic ``background_work_in_progress`` snapshot.

    One fixed shape for every provider, so a consumer never has to know which
    provider it reads: a provider without a given kind of work leaves it at its
    zero value. ``None`` when nothing runs — the same "absent means none"
    convention as ``label``.

    - ``subagents``: live subagents spawned by this session.
    - ``shells``: shell commands still running in the background. Claude Code:
      a ``Bash`` run with ``run_in_background`` (or backgrounded later), by the
      session or one of its subagents. Codex: a unified-exec process of the
      session or one of its subagents that has not exited yet — one the
      agent is still polling mid-turn included (Codex draws no line between
      a long foreground command and a background one).
    - ``monitors``: live Claude Code ``Monitor`` tools.
    - ``scheduled_wakeup_at``: ISO-8601 UTC time of a pending Claude Code
      ``ScheduleWakeup``, ``None`` when there is none (or it is past).
    - ``goal``: a Codex ``/goal`` continuation is running.
    """
    if not (subagents or shells or monitors or scheduled_wakeup_at is not None or goal):
        return None
    wakeup_iso = None
    if scheduled_wakeup_at is not None:
        from datetime import UTC, datetime

        wakeup_iso = datetime.fromtimestamp(scheduled_wakeup_at, tz=UTC).isoformat()
    return {
        "subagents": subagents,
        "shells": shells,
        "monitors": monitors,
        "scheduled_wakeup_at": wakeup_iso,
        "goal": goal,
    }
