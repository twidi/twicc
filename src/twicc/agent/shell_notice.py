"""Background shell notice: TwiCC tells an idle agent about lingering shells.

Design: ``docs/plans/2026-09-27-background-shell-notice-design.md``. This
module is provider-agnostic: it never reads provider event shapes. Providers
describe their shells as :class:`ShellInfo` records through
``BaseAgent.shell_notice_state()``; the manager selects the concerned ones with
:func:`select_concerned_shells` and sends :func:`build_shell_notice`'s text.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import NamedTuple

from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER, inline_md

SHELL_NOTICE_DELAY_SECONDS = 300
SHELL_NOTICE_RESOLUTION_GRACE_SECONDS = 60
SHELL_NOTICE_STALE_SECONDS = 30
SHELL_NOTICE_TEXT_MAX_CHARS = 300

_WHITESPACE_RUN_RE = re.compile(r"\s+")
_BACKTICK_RUN_RE = re.compile(r"`+")


class ShellOwner(StrEnum):
    MAIN = "main"
    SUBAGENT = "subagent"
    UNRESOLVED = "unresolved"
    UNATTRIBUTED = "unattributed"


class ShellInfo(NamedTuple):
    key: str  # Claude task_id; Codex "thread_id:process_id"
    shell_id: str  # the id the agent knows: Claude task_id, Codex process_id
    tool_use_id: str | None  # Claude: the Bash tool_use; Codex: None
    owner: ShellOwner
    owner_ref: str | None  # the id the main agent uses to reach the subagent
    owner_label: str | None
    owner_spawner_ref: str | None  # nested owner only
    owner_run_ended_at: float | None
    owner_running: bool
    description: str | None
    command: str | None
    output_path: str | None
    started_at: float  # epoch seconds


class ShellNoticeState(NamedTuple):
    idle: bool
    shells: list[ShellInfo]  # backgrounded shells only, owners merged
    any_subagent_running: bool
    last_subagent_run_end: float  # 0 when no subagent run ended


def earliest_delay_start(shell: ShellInfo, idle_since: float) -> float:
    """The earliest possible start of the 5-minute delay (spec §4 step 2)."""
    return max(idle_since, shell.started_at)


def _delay_start(shell: ShellInfo, state: ShellNoticeState, idle_since: float) -> float | None:
    """When the delay of ``shell`` starts, or ``None`` if it is not concerned (spec §3.3)."""
    start = earliest_delay_start(shell, idle_since)
    if shell.owner is ShellOwner.MAIN:
        return start
    if shell.owner is ShellOwner.SUBAGENT:
        if shell.owner_running:
            return None
        return max(start, shell.owner_run_ended_at or 0.0)
    if shell.owner is ShellOwner.UNATTRIBUTED:
        if state.any_subagent_running:
            return None
        return max(start, state.last_subagent_run_end)
    return None  # UNRESOLVED: never reported


def select_concerned_shells(
    state: ShellNoticeState, *, idle_since: float, notified: Collection[str], now: float,
) -> list[ShellInfo]:
    """The shells to report now, in the order ``state`` lists them."""
    concerned = []
    for shell in state.shells:
        if shell.key in notified:
            continue
        start = _delay_start(shell, state, idle_since)
        if start is not None and start <= now - SHELL_NOTICE_DELAY_SECONDS:
            concerned.append(shell)
    return concerned


def code_span(value: str, *, max_chars: int = SHELL_NOTICE_TEXT_MAX_CHARS) -> str:
    """Wrap a command or a path in a code span, verbatim (spec §6.1).

    No backslash escaping: inside a code span it would show literally and change
    the command the agent reads. The fence is one backtick longer than the
    longest backtick run in the value.
    """
    flattened = _WHITESPACE_RUN_RE.sub(" ", value).strip()
    if len(flattened) > max_chars:
        flattened = flattened[: max_chars - 1] + "…"
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(flattened)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flattened.startswith("`") or flattened.endswith("`") else ""
    return f"{fence}{pad}{flattened}{pad}{fence}"


def _shell_count(count: int) -> str:
    return "a background shell" if count == 1 else f"{count} background shells"


def _shell_lines(shells: Sequence[ShellInfo], now: float) -> str:
    lines = []
    for shell in sorted(shells, key=lambda s: s.started_at):
        minutes = max(0, int((now - shell.started_at) // 60))
        description = inline_md(shell.description)
        head = f"- shell {code_span(shell.shell_id)}"
        head += f': "{description}", running for {minutes} min' if description else f": running for {minutes} min"
        lines.append(head)
        if shell.command:
            lines.append(f"  - command: {code_span(shell.command)}")
        if shell.output_path:
            lines.append(f"  - output: {code_span(shell.output_path)}")
    return "\n".join(lines)


def _subagent_ask(count: int) -> str:
    if count == 1:
        return "Ask your subagent what this shell is for, and tell it to stop the shell if it is not needed."
    return "Ask your subagent what these shells are for, and tell it to stop them if they are not needed."


def _nested_ask(count: int) -> str:
    what = "this shell is" if count == 1 else "these shells are"
    it = "it" if count == 1 else "them"
    return (
        f"Check with whichever of the two you can reach what {what} for, "
        f"and have {it} stopped if not needed."
    )


def _unattributed_ask(count: int) -> str:
    what = "this shell" if count == 1 else "these shells"
    it = "it" if count == 1 else "them"
    return f"Find which subagent started {what}, and have {it} stopped if not needed."


_MAIN_ASK = (
    "If it is a long-running process you started on purpose, nothing to do. Otherwise, check what it "
    "is doing, make sure it will end, and keep the user informed so they do not wait for nothing."
)


def build_shell_notice(shells: Sequence[ShellInfo], *, now: float) -> str:
    """The notice text for ``shells`` (spec §6.1): subagents, unattributed, own."""
    parts = [f"{SHELL_NOTICE_HEADER}: background shell(s) still running"]

    by_owner: dict[str, list[ShellInfo]] = {}
    for shell in shells:
        if shell.owner is ShellOwner.SUBAGENT and shell.owner_ref:
            by_owner.setdefault(shell.owner_ref, []).append(shell)
    for owner_ref, group in by_owner.items():
        first = group[0]
        label = inline_md(first.owner_label)
        label_part = f' ("{label}")' if label else ""
        count = _shell_count(len(group))
        if first.owner_spawner_ref:
            who = (
                f"Subagent {code_span(owner_ref)}{label_part}, "
                f"started by subagent {code_span(first.owner_spawner_ref)},"
            )
            ask = _nested_ask(len(group))
        else:
            who = f"Your subagent {code_span(owner_ref)}{label_part}"
            ask = _subagent_ask(len(group))
        parts.append(f"{who} has finished its work, but it still has {count} running:")
        parts.append(_shell_lines(group, now))
        parts.append(ask)

    unattributed = [s for s in shells if s.owner is ShellOwner.UNATTRIBUTED]
    if unattributed:
        count = _shell_count(len(unattributed))
        parts.append(f"One of your subagents, which TwiCC cannot identify, still has {count} running:")
        parts.append(_shell_lines(unattributed, now))
        parts.append(_unattributed_ask(len(unattributed)))

    own = [s for s in shells if s.owner is ShellOwner.MAIN]
    if own:
        also = " also" if len(parts) > 1 else ""
        parts.append(f"You{also} still have {_shell_count(len(own))} of your own:")
        parts.append(_shell_lines(own, now))
        parts.append(_MAIN_ASK)

    return "\n\n".join(parts)


class ShellLookup(NamedTuple):
    key: str
    tool_use_id: str | None  # Claude: the shell's Bash tool_use
    owner_id: str | None  # Codex: the owner's thread id
    codex: bool


class OwnerFacts(NamedTuple):
    """Database facts about one shell's owner (spec §5.5, database part)."""

    key: str
    owner_id: str | None
    tool_name: str | None  # Claude only
    spawner_id: str | None  # None for a first-level owner
    title: str | None
    known: bool | None  # Codex run model only
    running: bool | None
    stopped_at: float | None


class ShellResolution(NamedTuple):
    """Stored placement of one shell (spec §5.5, Storage)."""

    owner_id: str | None
    tool_name: str | None
    spawner_id: str | None
    title: str | None
    known: bool | None
    running: bool | None
    stopped_at: float | None
    first_attempt_at: float


class ClaudeLiveOwners(NamedTuple):
    """Claude live run data, every level (spec §5.5 step 4)."""

    labels: Mapping[str, str]  # agent id -> description; "seen live" test
    running: Collection[str]  # agent ids in _live_background_tasks
    ended: Mapping[str, float]  # agent id -> last run end


def _grace_over(resolution: ShellResolution, now: float) -> bool:
    return now - resolution.first_attempt_at >= SHELL_NOTICE_RESOLUTION_GRACE_SECONDS


def needs_lookup(resolution: ShellResolution | None, *, codex: bool, now: float) -> bool:
    """Whether a raw ``UNRESOLVED`` shell still needs the database part."""
    if resolution is None:
        return True
    if resolution.tool_name not in (None, "Bash"):
        return False  # not a shell: final
    if resolution.owner_id is None:
        return not _grace_over(resolution, now)
    if not codex:
        return False  # Claude reads the run state live
    # Codex re-reads the run state at each tick, except once UNATTRIBUTED
    # (still unknown after the grace): final (spec §5.5 Storage).
    return bool(resolution.known) or not _grace_over(resolution, now)


def merge_resolution(
    shell: ShellInfo,
    resolution: ShellResolution | None,
    *,
    now: float,
    claude_live: ClaudeLiveOwners | None,
) -> ShellInfo | None:
    """Apply a stored placement to a raw shell (spec §5.5 outcome table).

    ``claude_live`` is given for Claude (run state from live data) and ``None``
    for Codex (run state from the stored run model facts). Returns ``None``
    for "not a shell".
    """
    if shell.owner is not ShellOwner.UNRESOLVED or resolution is None:
        return shell
    if resolution.tool_name not in (None, "Bash"):
        return None
    if resolution.owner_id is None:
        return shell._replace(owner=ShellOwner.UNATTRIBUTED) if _grace_over(resolution, now) else shell
    owner_id = resolution.owner_id
    if claude_live is not None:
        if owner_id not in claude_live.labels:
            return shell._replace(owner=ShellOwner.UNATTRIBUTED)
        running = owner_id in claude_live.running
        ended = claude_live.ended.get(owner_id)
        label = claude_live.labels.get(owner_id) or resolution.title
    else:
        if not resolution.known:
            return shell._replace(owner=ShellOwner.UNATTRIBUTED) if _grace_over(resolution, now) else shell
        running = bool(resolution.running)
        ended = resolution.stopped_at
        label = resolution.title
    return shell._replace(
        owner=ShellOwner.SUBAGENT,
        owner_ref=owner_id,
        owner_label=label,
        owner_spawner_ref=resolution.spawner_id,
        owner_running=running,
        owner_run_ended_at=ended,
    )


def resolve_shell_owners(root_id: str, lookups: Sequence[ShellLookup]) -> list[OwnerFacts]:
    """Database part of spec §5.5. Sync; reads the database only, never an agent."""
    from twicc.core import agent_runs
    from twicc.core.models import AgentLink, Session, ToolResultLink

    owner_by_key: dict[str, str] = {}
    tool_by_key: dict[str, str] = {}

    claude = [lookup for lookup in lookups if not lookup.codex and lookup.tool_use_id]
    if claude:
        sessions_by_tool: dict[str, set[str]] = {}
        names_by_tool: dict[str, set[str]] = {}
        rows = ToolResultLink.objects.filter(
            tool_use_id__in=[lookup.tool_use_id for lookup in claude],
            session__parent_session_id=root_id,
        ).values_list("tool_use_id", "session_id", "tool_name").distinct()
        for tool_use_id, session_id, tool_name in rows:
            sessions_by_tool.setdefault(tool_use_id, set()).add(session_id)
            names_by_tool.setdefault(tool_use_id, set()).add(tool_name)
        for lookup in claude:
            sessions = sessions_by_tool.get(lookup.tool_use_id, set())
            if len(sessions) != 1:
                continue  # zero: not synced yet; several: treated as not found
            owner_by_key[lookup.key] = next(iter(sessions))
            names = names_by_tool.get(lookup.tool_use_id, set())
            non_bash = sorted(name for name in names if name != "Bash")
            tool_by_key[lookup.key] = non_bash[0] if non_bash else "Bash"

    codex = [lookup for lookup in lookups if lookup.codex and lookup.owner_id]
    for lookup in codex:
        owner_by_key[lookup.key] = lookup.owner_id

    owner_ids = set(owner_by_key.values())
    spawners: dict[str, str] = {}
    titles: dict[str, str | None] = {}
    if owner_ids:
        # Ordered by pk: when an owner has several links, the first one names its spawner.
        for agent_id, session_id in AgentLink.objects.filter(agent_id__in=owner_ids).order_by("pk").values_list(
            "agent_id", "session_id",
        ):
            # A self-referential link is skipped: it would name the owner as its own spawner.
            if session_id != root_id and session_id != agent_id:
                spawners.setdefault(agent_id, session_id)
        titles = dict(Session.objects.filter(id__in=owner_ids).values_list("id", "title"))

    run_states = {}
    codex_ids = {lookup.owner_id for lookup in codex}
    if codex_ids:
        root = Session.objects.get(id=root_id)
        run_states = agent_runs.agent_run_states(root, codex_ids)

    facts = []
    for lookup in lookups:
        owner_id = owner_by_key.get(lookup.key)
        known = running = stopped_at = None
        if lookup.codex and owner_id is not None:
            state = run_states.get(owner_id)
            known = bool(state and state.known)
            running = bool(state and state.running)
            stopped_at = state.stopped_at.timestamp() if state and state.stopped_at else None
        facts.append(OwnerFacts(
            key=lookup.key,
            owner_id=owner_id,
            tool_name=tool_by_key.get(lookup.key),
            spawner_id=spawners.get(owner_id) if owner_id else None,
            title=titles.get(owner_id) if owner_id else None,
            known=known,
            running=running,
            stopped_at=stopped_at,
        ))
    return facts
