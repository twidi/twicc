"""Bounded complete-record selection and committed live-sync results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from twicc.providers.compute_base import (
        AgentLinkUpdate, AgentStoppedUpdate, ToolResultUpdate, WorkflowLinkUpdate,
    )


class LiveSyncLimits(NamedTuple):
    max_lines: int = 500
    max_bytes: int = 4 * 1024 * 1024


class RawLiveSlice(NamedTuple):
    records: list[bytes]
    end_offset: int
    has_more: bool
    bytes_consumed: int


class LiveSyncUpdates(NamedTuple):
    new_line_nums: list[int]
    modified_line_nums: list[int]
    agent_link_updates: list[AgentLinkUpdate]
    workflow_link_updates: list[WorkflowLinkUpdate]
    tool_result_updates: list[ToolResultUpdate]
    agent_stopped_updates: list[AgentStoppedUpdate]
    found_compact_summary: bool
    agent_interaction_updates: list[dict]
    agent_run_state_updates: list[dict]
    agents_resumed: list[tuple[str, str]]

    @classmethod
    def empty(cls) -> LiveSyncUpdates:
        return cls([], [], [], [], [], [], False, [], [], [])


class LiveSyncResult(NamedTuple):
    updates: LiveSyncUpdates
    has_more: bool
    lines_processed: int
    bytes_consumed: int
    elapsed_ms: float


def read_live_slice(path: Path, *, offset: int, limits: LiveSyncLimits) -> RawLiveSlice:
    """Read a bounded prefix. Probe readiness without retaining the next record."""
    if limits.max_lines < 1 or limits.max_bytes < 1:
        raise ValueError('Live slice limits must be positive')
    records = []
    consumed = nonempty = 0
    with path.open('rb') as source:
        source.seek(offset)
        while True:
            start = source.tell()
            # Establish completeness with bounded memory before allocating
            # a record. An arbitrarily large incomplete first tail must not
            # become an arbitrarily large readiness buffer.
            while fragment := source.readline(64 * 1024):
                if fragment.endswith(b'\n'):
                    break
            else:
                return RawLiveSlice(records, offset + consumed, False, consumed)
            size = source.tell() - start
            if records and (nonempty >= limits.max_lines or consumed + size > limits.max_bytes):
                return RawLiveSlice(records, offset + consumed, True, consumed)
            # The first complete record is allowed to exceed the budget.
            source.seek(start)
            record = source.read(size)
            if not record.endswith(b'\n'):
                # The source was truncated between probing and selection.
                return RawLiveSlice(records, offset + consumed, False, consumed)
            records.append(record)
            consumed += len(record)
            nonempty += bool(record.decode('utf-8', errors='replace').strip())


def merge_live_updates(left: LiveSyncUpdates, right: LiveSyncUpdates) -> LiveSyncUpdates:
    """Combine committed slice payloads for the temporary watcher drain bridge."""
    new_lines = sorted(set(left.new_line_nums) | set(right.new_line_nums))
    return LiveSyncUpdates(
        new_lines, sorted((set(left.modified_line_nums) | set(right.modified_line_nums)) - set(new_lines)),
        left.agent_link_updates + right.agent_link_updates,
        left.workflow_link_updates + right.workflow_link_updates,
        left.tool_result_updates + right.tool_result_updates,
        left.agent_stopped_updates + right.agent_stopped_updates,
        left.found_compact_summary or right.found_compact_summary,
        left.agent_interaction_updates + right.agent_interaction_updates,
        left.agent_run_state_updates + right.agent_run_state_updates,
        left.agents_resumed + right.agents_resumed,
    )
