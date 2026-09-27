"""The subagent run model: whether an agent runs, computed from its run evidence.

A **run** is a spawn (``AgentLink``) or an ``AgentInteraction`` with
``opens_run = true`` that passes the tree rule. :func:`agent_run_states` is the
only place that decides whether an agent runs; the stop step, the Codex live
process and the ``/subagents/`` snapshot all read its output. Design:
``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`` §5.1
(tree rule), §5.4 (runs, close rules, ``frozen_at_line``, ``exclude``) and
§5.5 (explicit stops).

The reads are bounded by the requested ``agent_ids`` (a tree reaches hundreds
of links and 15 000+ ``ToolResultLink`` rows on the root), never by the whole
tree — except on the frozen path (share snapshots), which needs every link of
the tree to know which owners were visible at the freeze.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from django.db.models import Max, Q

from twicc.core.enums import Provider

if TYPE_CHECKING:
    from twicc.core.models import Session

# Agent-level end statuses read by rule 5 (``tool_use_id == ""``): the Codex
# child's own turn end and the Claude child's user-interrupt marker (§5.2).
AGENT_LEVEL_END_STATUSES = frozenset({"turn_complete", "interrupted"})


class RunInfo(NamedTuple):
    """One run of an agent: a spawn, or an interaction that opened a run."""

    owner_session_id: str
    tool_use_id: str
    started_at: datetime | None
    open: bool
    closed_at: datetime | None
    background: bool  # spawn: link.is_background; interaction run: True


class AgentRunState(NamedTuple):
    """Whether one agent runs, and the runs that decide it (design §5.4)."""

    known: bool  # at least one run (spawn link or run interaction)
    running: bool
    run_started_at: datetime | None  # newest open run; None when not running
    run_background: bool | None  # newest open run; None when not running
    stopped_at: datetime | None  # max non-null closed_at over closed runs
    runs: tuple[RunInfo, ...]  # sorted by (started_at, nulls first), owner, tool_use_id


class RunStateExclude(NamedTuple):
    """Rows the stop step's "before" call treats as absent (design §5.4 ``exclude``).

    Runs and stop records are identified by their natural key ``(owner
    session id, tool_use_id)``; evidence rows by their primary key.
    """

    # (owner session id, tool_use_id) of AgentLink rows CREATED in the batch
    agent_links: frozenset[tuple[str, str]] = frozenset()
    # (session id, tool_use_id): created, or opens_run turned true, in the batch
    run_interactions: frozenset[tuple[str, str]] = frozenset()
    # stop interactions that became stop records in the batch
    stop_records: frozenset[tuple[str, str]] = frozenset()
    tool_result_link_ids: frozenset[int] = frozenset()
    # transcript rows written in the batch, and a ui row
    run_end_ids: frozenset[int] = frozenset()


NO_EXCLUDE = RunStateExclude()

UNKNOWN_STATE = AgentRunState(
    known=False, running=False, run_started_at=None, run_background=None, stopped_at=None, runs=(),
)


class _Run(NamedTuple):
    """A run candidate with the facts the close rules need."""

    owner_session_id: str
    tool_use_id: str
    started_at: datetime | None
    background: bool
    required: int  # rule 1 result count
    is_interaction: bool
    opening_line: int | None  # rule 2 tie-break; None for a spawn


class _Stop(NamedTuple):
    """An explicit stop record (design §5.5)."""

    owner_session_id: str
    at: datetime | None
    first_line: int | None  # first non-error result line; None for a ui row
    is_interaction: bool


class _Result(NamedTuple):
    line: int
    at: datetime | None
    error: str | None


def _owner_filter(root_id: str) -> Q:
    """Owned by the root or by a session whose parent is the root (the tree rule's owner scope)."""
    return Q(session_id=root_id) | Q(session__parent_session_id=root_id)


def _frozen_links(root: Session, frozen_at_line: int) -> tuple[list, set[str]]:
    """The tree's links visible at the freeze, and the visible agent ids.

    Same filter as ``build_subagents_state``: a root-owned link counts when its
    call line is at or before the freeze, a link owned by an agent counts when
    that agent is visible.
    """
    from twicc.core.session_queries import tree_agent_links, visible_tree_agent_ids

    links = tree_agent_links(root)
    allowed = {link.agent_id for link in links
               if link.session_id == root.id and link.tool_use_line_num <= frozen_at_line}
    visible = visible_tree_agent_ids(root.id, links, allowed)
    links = [link for link in links if link.agent_id in visible and (
        (link.session_id in visible) if link.session_id != root.id
        else link.tool_use_line_num <= frozen_at_line
    )]
    return links, visible


def _freeze_time(root: Session, frozen_at_line: int) -> datetime | None:
    """Newest non-null item timestamp of the root at or before the frozen line."""
    from twicc.core.models import SessionItem

    return SessionItem.objects.filter(
        session_id=root.id, line_num__lte=frozen_at_line, timestamp__isnull=False,
    ).aggregate(at=Max("timestamp"))["at"]


def _result_count_close(results: list[_Result], required: int) -> tuple[bool, datetime | None]:
    """Rule 1: closed when the number of distinct ``tool_result_at`` values reaches ``required``.

    Compaction copies a result with its original timestamp, so rows are never
    counted, values are. The close time is the ``required``-th smallest
    distinct non-null timestamp (``None`` when the count is reached only
    through a null value).
    """
    values = {result.at for result in results}
    if len(values) < required:
        return False, None
    timed = sorted(value for value in values if value is not None)
    return True, timed[required - 1] if len(timed) >= required else None


def _stop_closes(stop: _Stop, run: _Run) -> bool:
    """Rule 2, with its same-time line-order tie-break (§5.4 rule 2, §5.5)."""
    if run.started_at is None:
        return True
    if stop.at is None:
        return False
    if stop.at > run.started_at:
        return True
    if stop.at < run.started_at:
        return False
    # Same time: line order decides only between an interaction stop and an
    # interaction run in the same owner file.
    keeps_open = (
        stop.is_interaction and run.is_interaction
        and stop.owner_session_id == run.owner_session_id
        and stop.first_line is not None and run.opening_line is not None
        and stop.first_line < run.opening_line
    )
    return not keeps_open


def _sort_key(run: RunInfo):
    return (run.started_at is not None, run.started_at or datetime.min, run.owner_session_id, run.tool_use_id)


def agent_run_states(
    root: Session,
    agent_ids: Iterable[str],
    frozen_at_line: int | None = None,
    exclude: RunStateExclude | None = None,
) -> dict[str, AgentRunState]:
    """Compute the run state of each requested agent of ``root``'s tree.

    Returns an entry for every requested id; an id with no run (a shell
    ``task_id``, ``"main"``, a Monitor id, the root itself) is ``known=False``.
    ``frozen_at_line`` limits the root's evidence and runs to a share's freeze;
    ``exclude`` (stop step only) treats this batch's rows as absent.
    """
    from twicc.core.models import (
        AgentInteraction,
        AgentInteractionKind,
        AgentLink,
        AgentRunEnd,
        AgentRunEndSource,
        ToolResultLink,
    )

    agent_ids = set(agent_ids)
    states = dict.fromkeys(agent_ids, UNKNOWN_STATE)
    exclude = exclude or NO_EXCLUDE
    frozen = frozen_at_line is not None
    root_id = root.id
    is_codex = root.provider == Provider.CODEX
    agent_ids.discard(root_id)
    if not agent_ids:
        return states

    # 1. Spawn links. The frozen path reads the whole tree once (owner visibility).
    visible: set[str] = set()
    if frozen:
        tree_links, visible = _frozen_links(root, frozen_at_line)
        links = [link for link in tree_links if link.agent_id in agent_ids]
    else:
        links = list(
            AgentLink.objects.filter(_owner_filter(root_id), agent_id__in=agent_ids).exclude(agent_id=root_id)
        )
    # Removing the links created in the batch BEFORE the tree rule is the "late
    # tree rule" of §5.4: a target whose only links are new has no run yet.
    links = [link for link in links if (link.session_id, link.tool_use_id) not in exclude.agent_links]
    linked = {link.agent_id for link in links}
    if not linked:
        return states

    def owner_visible(owner_id: str) -> bool:
        return not frozen or owner_id == root_id or owner_id in visible

    # 2. Interactions targeting a linked agent, owned inside the tree.
    interactions = [
        row for row in AgentInteraction.objects.filter(_owner_filter(root_id), agent_id__in=linked)
        if owner_visible(row.session_id)
    ]

    # 3. Results of the spawn and interaction calls, filtered to exact pairs.
    pairs = {(link.session_id, link.tool_use_id) for link in links}
    pairs |= {(row.session_id, row.tool_use_id) for row in interactions}
    results: dict[tuple[str, str], list[_Result]] = {}
    if pairs:
        rows = ToolResultLink.objects.filter(
            session_id__in={owner for owner, _ in pairs}, tool_use_id__in={tool for _, tool in pairs},
        )
        if frozen:
            rows = rows.filter(~Q(session_id=root_id) | Q(tool_result_line_num__lte=frozen_at_line))
        for row in rows.values_list("id", "session_id", "tool_use_id", "tool_result_line_num",
                                    "tool_result_at", "error"):
            row_id, owner, tool_use_id, line, at, error = row
            if (owner, tool_use_id) not in pairs or row_id in exclude.tool_result_link_ids:
                continue
            results.setdefault((owner, tool_use_id), []).append(_Result(line, at, error))

    # 4. Ends, selected by agent in any session (rule 4 needs the root-file end
    # of a subagent's call); ui rows only on this root.
    ends = AgentRunEnd.objects.filter(agent_id__in=linked)
    if frozen:
        ends = ends.filter(
            ~Q(session_id=root_id) | Q(source=AgentRunEndSource.UI) | Q(line_num__lte=frozen_at_line)
        )
    call_ends: dict[tuple[str, str], list[datetime | None]] = {}
    agent_level_ends: dict[str, list[datetime]] = {}
    ui_rows: list[tuple[str, datetime | None]] = []
    for end in ends.values_list("id", "session_id", "source", "agent_id", "tool_use_id", "ended_at", "status"):
        end_id, session_id, source, agent_id, tool_use_id, ended_at, status = end
        if end_id in exclude.run_end_ids:
            continue
        if source == AgentRunEndSource.UI:
            if session_id == root_id:
                ui_rows.append((agent_id, ended_at))
        elif tool_use_id:
            call_ends.setdefault((agent_id, tool_use_id), []).append(ended_at)
        elif status in AGENT_LEVEL_END_STATUSES and ended_at is not None:
            agent_level_ends.setdefault(agent_id, []).append(ended_at)

    # 5. Frozen path: a root ui row counts when it is at or before the freeze time.
    if frozen and ui_rows:
        freeze_time = _freeze_time(root, frozen_at_line)
        ui_rows = [(agent_id, at) for agent_id, at in ui_rows
                   if freeze_time is not None and at is not None and at <= freeze_time]

    # Runs.
    runs: dict[str, list[_Run]] = {}
    for link in links:
        runs.setdefault(link.agent_id, []).append(_Run(
            owner_session_id=link.session_id, tool_use_id=link.tool_use_id,
            started_at=link.started_at, background=link.is_background,
            required=2 if link.is_background else 1, is_interaction=False, opening_line=None,
        ))
    stops: dict[str, list[_Stop]] = {}
    for row in interactions:
        key = (row.session_id, row.tool_use_id)
        call_results = results.get(key, [])
        if row.opens_run and key not in exclude.run_interactions:
            opening_line = row.event_line_num if is_codex else min(
                (result.line for result in call_results), default=None,
            )
            if frozen and row.session_id == root_id and (
                row.tool_use_line_num > frozen_at_line or opening_line is None or opening_line > frozen_at_line
            ):
                continue
            runs.setdefault(row.agent_id, []).append(_Run(
                owner_session_id=row.session_id, tool_use_id=row.tool_use_id,
                started_at=row.started_at, background=True, required=2, is_interaction=True,
                opening_line=opening_line,
            ))
        elif row.kind == AgentInteractionKind.STOP and key not in exclude.stop_records:
            ok = [result for result in call_results if result.error is None]
            if not ok:
                continue
            first_line = min(result.line for result in ok)
            if frozen and row.session_id == root_id and row.event_line_num > frozen_at_line:
                continue  # its non-error result line is already bounded by the result filter
            times = [result.at for result in ok if result.at is not None]
            stops.setdefault(row.agent_id, []).append(_Stop(
                owner_session_id=row.session_id, at=min(times) if times else None,
                first_line=first_line, is_interaction=True,
            ))
    for agent_id, at in ui_rows:
        stops.setdefault(agent_id, []).append(_Stop(
            owner_session_id=root_id, at=at, first_line=None, is_interaction=False,
        ))

    cutoff = root.cutoff
    for agent_id, agent_runs in runs.items():
        infos = []
        for run in agent_runs:
            # The time of every piece of evidence that closes the run (None when untimed).
            pieces: list[datetime | None] = []
            # Rule 1: distinct result timestamps reach the required count.
            reached, at = _result_count_close(results.get((run.owner_session_id, run.tool_use_id), []),
                                              run.required)
            if reached:
                pieces.append(at)
            # Rule 2: explicit stops.
            pieces.extend(stop.at for stop in stops.get(agent_id, ()) if _stop_closes(stop, run))
            # Rule 3: started before the root's cutoff.
            if cutoff is not None and (run.started_at is None or run.started_at < cutoff):
                pieces.append(cutoff)
            # Rule 4: an end row for this call, whatever the session holding it.
            pieces.extend(call_ends.get((agent_id, run.tool_use_id), ()))
            # Rule 5: an agent-level turn end after the run's start.
            if run.started_at is not None:
                pieces.extend(ended_at for ended_at in agent_level_ends.get(agent_id, ())
                              if ended_at > run.started_at)
            timed = [at for at in pieces if at is not None]

            infos.append(RunInfo(
                owner_session_id=run.owner_session_id, tool_use_id=run.tool_use_id,
                started_at=run.started_at, open=not pieces,
                closed_at=min(timed) if timed else None, background=run.background,
            ))

        infos.sort(key=_sort_key)
        open_runs = [info for info in infos if info.open]
        newest_open = open_runs[-1] if open_runs else None
        closed_times = [info.closed_at for info in infos if not info.open and info.closed_at is not None]
        states[agent_id] = AgentRunState(
            known=True,
            running=newest_open is not None,
            run_started_at=newest_open.started_at if newest_open else None,
            run_background=newest_open.background if newest_open else None,
            stopped_at=max(closed_times) if closed_times else None,
            runs=tuple(infos),
        )
    return states


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_runs(state: AgentRunState) -> list[dict]:
    """The ``runs`` list of the snapshot and the ``agent_run_state`` payload."""
    return [
        {
            "owner_session_id": run.owner_session_id,
            "tool_use_id": run.tool_use_id,
            "started_at": _iso(run.started_at),
            "open": run.open,
            "closed_at": _iso(run.closed_at),
        }
        for run in state.runs
    ]


def serialize_run_state(root_id: str, agent_id: str, state: AgentRunState) -> dict:
    """The design §7.3 ``agent_run_state`` payload, minus ``project_id`` (added by the broadcaster)."""
    return {
        "root_session_id": root_id,
        "agent_session_id": agent_id,
        "running": state.running,
        "run_started_at": _iso(state.run_started_at),
        "run_background": state.run_background,
        "runs": serialize_runs(state),
    }
