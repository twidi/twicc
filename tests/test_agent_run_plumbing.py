"""Compute plumbing for agent runs: hooks, batch state, batch diffs, live tuple.

The provider hooks (``collect_agent_run_signals`` in batch,
``apply_agent_run_signals`` live) return nothing by default; these tests plug
spy subclasses in to check where the hooks run, what they see, and how the
batch path turns their rows into ``AgentInteraction`` / ``AgentRunEnd`` diffs.
Design: ``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md``
§6.2 and §7.1.
"""
import copy
from datetime import UTC, datetime
from queue import Queue

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentLink,
    AgentRunEnd,
    AgentRunEndSource,
    Project,
    Session,
    SessionItem,
    SessionType,
    ToolResultLink,
)
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.compute_base import BatchAgentSignals, LiveAgentSignals

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
LATER = datetime(2026, 9, 27, 12, 5, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Claude line builders and harness
# ---------------------------------------------------------------------------


def entry(role, content, **extra):
    return {"type": role, "timestamp": NOW.isoformat(), "message": {"role": role, "content": content}, **extra}


def spawn(tool="tool_run"):
    return entry("assistant", [{"type": "tool_use", "id": tool, "name": "Agent", "input": {"prompt": "run work"}}])


def ack(agent="ad123", tool="tool_run"):
    return entry("user", [{"type": "tool_result", "tool_use_id": tool,
        "content": f"Async agent launched successfully.\nagentId: {agent} (internal)"}])


def queue_entry(agent="ad123", tool="tool_run"):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": NOW.isoformat(), "content":
        f"<task-notification><task-id>{agent}</task-id><tool-use-id>{tool}</tool-use-id>"
        f"<status>completed</status><result>done</result></task-notification>"}


@pytest.fixture
def tree(db, provider_home):
    project = Project.objects.create(id="run-project")
    root = Session.objects.create(id="run-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path="run-project/run-root.jsonl")

    def child(id):
        return Session.objects.create(id=id, project=project, provider=Provider.CLAUDE_CODE,
            type=SessionType.SUBAGENT, parent_session=root,
            file_path=f"run-project/run-root/subagents/agent-{id}.jsonl")
    return root, child("aa123"), child("ad123"), provider_home.claude / "projects"


def seed(session, *entries, compute=None):
    compute = compute or ClaudeCodeSessionCompute()
    start = session.items.count()
    for n, parsed in enumerate(entries, start + 1):
        SessionItem.objects.create(session=session, line_num=n, content=orjson.dumps(parsed).decode(),
            timestamp=NOW, kind=compute.compute_item_kind(parsed))


def write_lines(session, home, *entries):
    path = home / session.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for parsed in entries:
            f.write(orjson.dumps(parsed) + b"\n")
    return path


def live(session, home, *entries, compute=None):
    path = write_lines(session, home, *entries)
    return (compute or ClaudeCodeSessionCompute()).sync_session_items_from_file(session, path)


def recompute(session, compute):
    queue = Queue()
    compute.compute_session_metadata(session.id, queue, "plumbing-test")
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    msg = next(m for m in messages if m["type"] == "session_complete")
    assert compute.apply_session_complete(msg).outcome == "applied"
    return msg


# ---------------------------------------------------------------------------
# Spies
# ---------------------------------------------------------------------------


class ClaudeBatchSpy(ClaudeCodeSessionCompute):
    """Records what ``batch_state`` shows at each line; returns scripted signals."""

    def __init__(self, watched_tool_use_id="tool_run", signals=None):
        super().__init__()
        self.watched_tool_use_id = watched_tool_use_id
        self.signals = signals or {}
        self.seen = {}

    def collect_agent_run_signals(self, session_id, item, parsed, batch_state):
        results = batch_state.results_by_tool_use.get(self.watched_tool_use_id)
        self.seen[item.line_num] = {
            "state": batch_state,
            "results": list(results) if results is not None else None,
            "agent_links": set(batch_state.all_agent_links),
            "interactions": {key: dict(row) for key, row in batch_state.all_agent_interactions.items()},
        }
        # Fresh copies: the batch stores and mutates the returned row dicts.
        return copy.deepcopy(self.signals.get(item.line_num, BatchAgentSignals()))


class CodexBatchSpy(CodexSessionCompute):
    def __init__(self):
        super().__init__()
        self.seen = {}

    def collect_agent_run_signals(self, session_id, item, parsed, batch_state):
        self.seen[item.line_num] = set(batch_state.all_agent_links)
        return BatchAgentSignals()


class ClaudeLiveSpy(ClaudeCodeSessionCompute):
    """Records, per line, the links already in the DB when the live hook runs."""

    def __init__(self, session_id, child_id, tool_use_id="tool_run", resumed=()):
        super().__init__()
        self.session_id = session_id
        self.child_id = child_id
        self.tool_use_id = tool_use_id
        self.resumed = resumed
        self.seen = {}

    def apply_agent_run_signals(self, session_id, item, parsed):
        self.seen[item.line_num] = {
            "result_link": ToolResultLink.objects.filter(
                session_id=self.session_id, tool_use_id=self.tool_use_id,
                tool_result_line_num=item.line_num,
            ).exists(),
            "agent_link": AgentLink.objects.filter(agent_id=self.child_id).exists(),
        }
        return LiveAgentSignals(agents_resumed=self.resumed)


# ---------------------------------------------------------------------------
# Live tuple
# ---------------------------------------------------------------------------


def test_live_tuple_appends_three_lists_and_keeps_indexes(tree):
    root, owner, child, home = tree
    live(owner, home, spawn())
    child.delete()
    result = live(root, home, queue_entry())
    assert len(result) == 10
    assert result[2][0].parent_session_id == owner.id
    assert result[5][0].agent_session_id == "ad123"
    assert result[6] is False
    assert result[7] == [] and result[8] == [] and result[9] == []


def test_live_tuple_early_returns_have_ten_elements(tree):
    root, owner, child, home = tree
    compute = ClaudeCodeSessionCompute()
    missing = compute.sync_session_items_from_file(root, home / "missing.jsonl")
    assert missing == ([], [], [], [], [], [], False, [], [], [])
    first = live(root, home, entry("user", "hello"))
    assert len(first) == 10
    root.refresh_from_db()
    unchanged = compute.sync_session_items_from_file(root, home / root.file_path)
    assert unchanged == ([], [], [], [], [], [], False, [], [], [])


def test_live_tuple_carries_agents_resumed(tree):
    root, owner, child, home = tree
    spy = ClaudeLiveSpy(owner.id, child.id, resumed=(("agent-x", "/root/x"),))
    result = live(owner, home, spawn(), compute=spy)
    assert result[9] == [("agent-x", "/root/x")]


# ---------------------------------------------------------------------------
# Hook position
# ---------------------------------------------------------------------------


def test_batch_hook_sees_this_lines_result_and_agent_link(tree):
    root, owner, child, home = tree
    seed(owner, spawn(), ack())
    spy = ClaudeBatchSpy()
    recompute(owner, spy)
    assert spy.seen[1]["results"] is None
    assert spy.seen[1]["agent_links"] == set()
    assert [r["tool_result_line_num"] for r in spy.seen[2]["results"]] == [2]
    assert (child.id, "tool_run") in spy.seen[2]["agent_links"]
    state = spy.seen[2]["state"]
    assert state.session_id == owner.id
    assert state.root_session_id == root.id
    assert state.session_type == SessionType.SUBAGENT
    assert "tool_run" in state.tool_use_map
    assert state.results_by_tool_use["tool_run"][0] is state.all_tool_result_links[("tool_run", 2)]


def test_batch_hook_root_state_uses_own_id(tree):
    root, owner, child, home = tree
    seed(root, entry("user", "hello"))
    spy = ClaudeBatchSpy()
    recompute(root, spy)
    state = spy.seen[1]["state"]
    assert state.root_session_id == root.id
    assert state.session_type == SessionType.SESSION


def _codex_line(type_, payload):
    return orjson.dumps({"timestamp": NOW.isoformat(), "type": type_, "payload": payload}).decode()


def test_batch_hook_sees_link_from_codex_started_line(db):
    project = Project.objects.create(id="run-project-codex")
    session = Session.objects.create(id="run-codex", project=project, provider=Provider.CODEX)
    call_id, agent_id = "call_spawn", "01a003c9-adec-79a2-b236-131c185aeaf9"
    lines = [
        _codex_line("response_item", {
            "type": "function_call", "name": "spawn_agent", "namespace": "collaboration",
            "call_id": call_id,
            "arguments": orjson.dumps({"task_name": "t", "fork_turns": "none", "message": "x"}).decode(),
        }),
        _codex_line("event_msg", {
            "type": "item_completed", "thread_id": "parent-thread", "turn_id": "turn-1",
            "completed_at_ms": 1786769944144,
            "item": {"type": "SubAgentActivity", "id": call_id, "agent_thread_id": agent_id,
                     "agent_path": "/root/t", "kind": "started"},
        }),
    ]
    for n, content in enumerate(lines, start=1):
        SessionItem.objects.create(session=session, line_num=n, content=content)
    spy = CodexBatchSpy()
    recompute(session, spy)
    assert spy.seen[1] == set()
    assert (agent_id, call_id) in spy.seen[2]


def test_live_hook_runs_after_result_link_and_before_tool_use_link(tree):
    root, owner, child, home = tree
    # Sidecar + child's first line before the launcher's tool_use: the
    # tool_use line itself creates the AgentLink (race path).
    meta_path = (home / child.file_path).with_suffix(".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_bytes(orjson.dumps({"toolUseId": "tool_run", "parentAgentId": owner.id}))
    live(child, home, entry("user", "run work", agentId=child.id))
    spy = ClaudeLiveSpy(owner.id, child.id)
    live(owner, home, spawn(), compute=spy)
    assert spy.seen[1]["agent_link"] is False
    assert AgentLink.objects.filter(agent_id=child.id).exists()
    owner.refresh_from_db()
    live(owner, home, ack(), compute=spy)
    assert spy.seen[2]["result_link"] is True


# ---------------------------------------------------------------------------
# Batch diffs
# ---------------------------------------------------------------------------


def interaction(session, line, tool="call_x", *, agent="ag1", kind="message", started=NOW):
    return {"session_id": session.id, "tool_use_line_num": line, "event_line_num": line,
            "tool_use_id": tool, "agent_id": agent, "kind": kind, "opens_run": False,
            "started_at": started.isoformat() if started else None}


def run_end(session, line, tool="call_x", *, agent="ag1", status="completed"):
    return {"session_id": session.id, "line_num": line, "tool_use_id": tool,
            "agent_id": agent, "ended_at": NOW.isoformat(), "status": status}


def test_batch_diff_creates_updates_and_deletes(tree):
    root, owner, child, home = tree
    seed(root, entry("user", "a"), entry("user", "b"), entry("user", "c"))

    first = ClaudeBatchSpy(signals={
        1: BatchAgentSignals(interactions=(interaction(root, 1),)),
        2: BatchAgentSignals(opens_run=(("call_x", LATER.isoformat()),), run_ends=(run_end(root, 2),)),
        3: BatchAgentSignals(opens_run=(("unknown_call", LATER.isoformat()),)),
    })
    msg = recompute(root, first)
    assert len(msg["agent_interactions_to_create"]) == 1
    assert len(msg["agent_run_ends_to_create"]) == 1
    row = AgentInteraction.objects.get()
    assert (row.session_id, row.tool_use_id, row.agent_id, row.kind) == (root.id, "call_x", "ag1", "message")
    assert row.opens_run is True
    assert row.started_at == LATER
    assert first.seen[2]["interactions"]["call_x"]["opens_run"] is False
    assert first.seen[3]["interactions"]["call_x"]["opens_run"] is True
    end = AgentRunEnd.objects.get()
    assert (end.line_num, end.tool_use_id, end.source, end.status) == (2, "call_x", "transcript", "completed")
    ids = (row.id, end.id)

    # Same signals: nothing to write.
    same = recompute(root, ClaudeBatchSpy(signals=first.signals))
    for key in ("agent_interactions_to_create", "agent_interactions_to_update", "agent_interactions_to_delete",
                "agent_run_ends_to_create", "agent_run_ends_to_update", "agent_run_ends_to_delete"):
        assert same[key] == [], key

    changed = ClaudeBatchSpy(signals={
        1: BatchAgentSignals(interactions=(interaction(root, 1, kind="resume"),)),
        2: BatchAgentSignals(run_ends=(run_end(root, 2, status="stopped"),)),
    })
    msg = recompute(root, changed)
    assert len(msg["agent_interactions_to_update"]) == 1
    assert len(msg["agent_run_ends_to_update"]) == 1
    row, end = AgentInteraction.objects.get(), AgentRunEnd.objects.get()
    assert (row.id, end.id) == ids
    assert (row.kind, row.opens_run, row.started_at) == ("resume", False, NOW)
    assert end.status == "stopped"

    msg = recompute(root, ClaudeBatchSpy())
    assert msg["agent_interactions_to_delete"] == [ids[0]]
    assert msg["agent_run_ends_to_delete"] == [ids[1]]
    assert not AgentInteraction.objects.exists()
    assert not AgentRunEnd.objects.exists()


def test_batch_first_line_wins_for_duplicate_interactions(tree):
    root, owner, child, home = tree
    seed(root, entry("user", "a"), entry("user", "b"))
    spy = ClaudeBatchSpy(signals={
        1: BatchAgentSignals(interactions=(interaction(root, 1, tool="call_dup", agent="first"),)),
        2: BatchAgentSignals(interactions=(interaction(root, 2, tool="call_dup", agent="second"),)),
    })
    recompute(root, spy)
    row = AgentInteraction.objects.get()
    assert (row.event_line_num, row.agent_id) == (1, "first")


def test_ui_run_end_survives_root_recompute(tree):
    root, owner, child, home = tree
    seed(root, entry("user", "a"))
    ui_end = AgentRunEnd.objects.create(session=root, source=AgentRunEndSource.UI, line_num=None,
                                        agent_id=child.id, ended_at=NOW, status="stopped")
    msg = recompute(root, ClaudeBatchSpy(signals={
        1: BatchAgentSignals(run_ends=(run_end(root, 1, agent=child.id),)),
    }))
    assert msg["agent_run_ends_to_delete"] == []
    recompute(root, ClaudeBatchSpy())
    assert list(AgentRunEnd.objects.values_list("id", flat=True)) == [ui_end.id]


def test_default_hooks_write_nothing(tree):
    root, owner, child, home = tree
    seed(owner, spawn(), ack())
    msg = recompute(owner, ClaudeCodeSessionCompute())
    assert msg["agent_interactions_to_create"] == []
    assert msg["agent_run_ends_to_create"] == []


# ---------------------------------------------------------------------------
# remap_tool_result_id keeps its old call shape
# ---------------------------------------------------------------------------


def test_codex_remap_without_batch_state_still_works():
    compute = CodexSessionCompute()
    parsed = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "ok"}}
    assert compute.remap_tool_result_id(parsed, "c1", session_id="s", tool_use_map={}) == "c1"
    assert compute.remap_tool_result_id(parsed, "c1", session_id="s", tool_use_map={}, batch_state=None) == "c1"
