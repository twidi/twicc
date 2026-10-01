"""The Codex root's live set of running subagents, kept in step with the run model (design §6.3, §6.4, §9).

A Codex root agent tracks its running first-level children in
``_live_subagents``. The stream adds them (``started``) and removes most of
them (``interrupted`` / ``completed``); the watcher relays the rest: a
``followup_task`` that opens a run puts the child back
(``notify_subagents_resumed``), a batch that stops it drops it
(``notify_subagents_stopped``), and the prune re-checks the set on each
``wait`` and hold decision. The three read ``agent_run_states`` under one
lock, so relays running out of order settle on the latest committed state.
An ephemeral agent has no watcher rows: it reads ``thread_read`` instead.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from watchfiles import Change

from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentInteractionKind,
    AgentLink,
    AgentRunEnd,
    Project,
    Session,
    SessionType,
    ToolResultLink,
)
from twicc.providers import sessions_watcher
from twicc.providers.codex.agent import agent as agent_module
from twicc.providers.codex.agent import manager as manager_module
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.sessions_watcher import ParsedSessionFile

from tests.codex_agent_run_fixtures import (
    AGENT_A,
    AGENT_B,
    PATH_A,
    PATH_B,
    ROOT,
    Rollout,
    _fixture,
    fixture_child_turn_ends,
)
from tests.test_codex_agent_runs_live import LiveReplay

ROOT_ID = "live-process-root"
CHILD = "live-process-child"
OTHER = "live-process-other"
NESTED = "live-process-nested"


pytestmark = pytest.mark.usefixtures("compute_executor_started")


@pytest.mark.parametrize("delay, running", [(0, False), (30, False), (30.001, True)])
def test_process_end_keeps_thirty_second_late_announcement_rule(delay, running):
    """Historical facts must not replace the process owner/end state machine."""
    import orjson

    from twicc.providers.codex.compute import CodexSessionCompute
    from tests.test_codex_code_mode import _command_execution_line
    from tests.test_history_fact_extraction import output

    compute = CodexSessionCompute()
    end = orjson.loads(_command_execution_line("42"))
    end["timestamp"] = t(0).isoformat()
    assert compute._take_process_owner(ROOT_ID, end) is None
    announcement = output("owner", "Process running with session ID 42")
    announcement["timestamp"] = t(delay).isoformat()
    compute._note_process_announcement(ROOT_ID, announcement, "owner")
    assert compute.has_process_owner(ROOT_ID, 42) is running
    if running:
        assert compute._take_process_owner(ROOT_ID, end) == "owner"
        assert not compute.has_process_owner(ROOT_ID, 42)


def t(seconds: float) -> datetime:
    return datetime(2026, 9, 27, 10, tzinfo=UTC) + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Hand-built run rows
# ---------------------------------------------------------------------------


def make_tree() -> Session:
    project = Project.objects.create(id="-tmp-live-process", directory="/tmp/live-process")
    root = Session.objects.create(id=ROOT_ID, project=project, provider=Provider.CODEX, file_path="root.jsonl")
    for agent_id in (CHILD, OTHER, NESTED):
        Session.objects.create(
            id=agent_id, project=project, provider=Provider.CODEX, type=SessionType.SUBAGENT,
            parent_session=root, file_path=f"{agent_id}.jsonl",
        )
    return root


def spawn(owner_id: str, agent_id: str, call_id: str, at: float) -> None:
    """A background spawn link (a Codex spawn closes on ack + ``FINAL_ANSWER``)."""
    AgentLink.objects.create(
        session_id=owner_id, tool_use_line_num=1, tool_use_id=call_id, agent_id=agent_id,
        is_background=True, started_at=t(at),
    )


def result(owner_id: str, call_id: str, line: int, at: float) -> None:
    ToolResultLink.objects.create(
        session_id=owner_id, tool_use_line_num=1, tool_result_line_num=line, tool_use_id=call_id,
        tool_result_at=t(at),
    )


def followup(owner_id: str, agent_id: str, call_id: str, at: float, line: int = 50) -> None:
    """A run-opening ``followup_task`` interaction."""
    AgentInteraction.objects.create(
        session_id=owner_id, tool_use_line_num=line, event_line_num=line + 1, tool_use_id=call_id,
        agent_id=agent_id, kind=AgentInteractionKind.RESUME, opens_run=True, started_at=t(at),
    )


def closed_spawn(owner_id: str, agent_id: str, call_id: str, at: float) -> None:
    """A spawn whose run already ended: its ack, then its ``FINAL_ANSWER``."""
    spawn(owner_id, agent_id, call_id, at)
    result(owner_id, call_id, 10, at + 1)
    result(owner_id, call_id, 20, at + 2)


def close_followup(owner_id: str, call_id: str, at: float) -> None:
    result(owner_id, call_id, 60, at)
    result(owner_id, call_id, 70, at + 1)


# ---------------------------------------------------------------------------
# A live agent built without the SDK
# ---------------------------------------------------------------------------


def make_agent(session_id: str = ROOT_ID, *, ephemeral: bool = False,
               state: AgentState = AgentState.USER_TURN) -> CodexAgent:
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = session_id
    agent.ephemeral = ephemeral
    agent.state = state
    agent.previous_state = None
    agent.state_changed_at = 0.0
    agent.last_activity = 0.0
    agent._live_subagents = {}
    agent._live_shells = {}
    agent._subagent_set_lock = asyncio.Lock()
    agent._subagent_stop_retry_tasks = set()
    agent._subagent_wait_label_active = False
    agent._subagent_hold_active = False
    agent._manual_compaction = False
    agent._goal_continuation_active = False
    agent._ephemeral_subagent_task = None
    agent._current_turn = None
    agent._schedule_background_work_refresh = MagicMock()
    agent._broadcast_process_label = AsyncMock()
    agent._notify_state_change = AsyncMock()
    agent._set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))
    agent._init_shell_notice_state()
    agent._init_codex_shell_notice_state()
    return agent


def labels(agent: CodexAgent) -> list[str]:
    return [call.args[0] for call in agent._broadcast_process_label.await_args_list]


def activity(agent_id: str, kind: str, agent_path: str = "/root/task") -> SimpleNamespace:
    """A ``SubAgentActivityThreadItem`` as the SDK hands it over."""
    return SimpleNamespace(
        type="subAgentActivity", id=f"call_{agent_id}", agent_thread_id=agent_id, agent_path=agent_path,
        kind=SimpleNamespace(value=kind),
    )


def thread_reader(statuses: dict[str, str]):
    async def read(child_id):
        return SimpleNamespace(
            thread=SimpleNamespace(status=SimpleNamespace(root=SimpleNamespace(type=statuses[child_id])))
        )
    return SimpleNamespace(_client=SimpleNamespace(thread_read=read))


def forbid_db(monkeypatch) -> None:
    """Fail on any run-model read: the ephemeral paths must never touch the DB."""
    def boom(*_args):
        raise AssertionError("ephemeral agent read the run model")
    monkeypatch.setattr(agent_module, "_stopped_subagent_ids", boom)
    monkeypatch.setattr(agent_module, "_running_first_level_subagent_ids", boom)


# ---------------------------------------------------------------------------
# Resume relay
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestResumeRelay:
    def test_re_adds_a_running_first_level_child_only(self) -> None:
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)  # open run: running again
        closed_spawn(ROOT_ID, OTHER, "c_other", 0)
        followup(ROOT_ID, OTHER, "c_fu_other", 10)
        close_followup(ROOT_ID, "c_fu_other", 11)  # its resumed run already ended
        agent = make_agent()

        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child"), (OTHER, "/root/other")]))

        assert agent._live_subagents == {CHILD: "/root/child"}
        agent._schedule_background_work_refresh.assert_called_once_with()
        assert labels(agent) == []  # no label shown, none to refresh

    def test_a_nested_agent_adds_nothing(self) -> None:
        """Its spawn link is owned by a subagent: the root's stream never tracked it."""
        make_tree()
        closed_spawn(CHILD, NESTED, "c_nested", 0)
        followup(CHILD, NESTED, "c_fu_nested", 10)
        agent = make_agent()

        asyncio.run(agent.notify_subagents_resumed([(NESTED, "/root/child/nested")]))

        assert agent._live_subagents == {}
        agent._schedule_background_work_refresh.assert_not_called()

    def test_missing_root_row_adds_nothing(self) -> None:
        agent = make_agent("no-such-root")

        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))

        assert agent._live_subagents == {}

    def test_refreshes_the_wait_label_in_a_turn(self) -> None:
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)
        agent = make_agent(state=AgentState.ASSISTANT_TURN)
        agent._current_turn = object()
        agent._subagent_wait_label_active = True
        agent._live_subagents = {OTHER: "/root/other"}

        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))

        assert set(agent._live_subagents) == {CHILD, OTHER}
        agent._schedule_background_work_refresh.assert_called_once_with()
        assert labels(agent) == ["waiting for 2 subagents"]

    def test_refreshes_the_hold_label_and_never_arms_the_hold(self) -> None:
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)
        held = make_agent(state=AgentState.ASSISTANT_TURN)
        held._subagent_hold_active = True
        held._live_subagents = {OTHER: "/root/other"}

        asyncio.run(held.notify_subagents_resumed([(CHILD, "/root/child")]))

        assert labels(held) == ["waiting for 2 subagents"]
        held._schedule_background_work_refresh.assert_called_once_with()

        idle = make_agent(state=AgentState.USER_TURN)
        asyncio.run(idle.notify_subagents_resumed([(CHILD, "/root/child")]))

        assert idle._live_subagents == {CHILD: "/root/child"}
        assert idle._subagent_hold_active is False
        assert idle.state == AgentState.USER_TURN
        idle._notify_state_change.assert_not_awaited()
        assert labels(idle) == []
        idle._schedule_background_work_refresh.assert_called_once_with()

    def test_ephemeral_agent_ignores_the_relay(self, monkeypatch) -> None:
        forbid_db(monkeypatch)
        agent = make_agent(ephemeral=True)

        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))

        assert agent._live_subagents == {}


# ---------------------------------------------------------------------------
# Prune and stop relay (watcher-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPruneAndStopAgainstTheRunModel:
    def test_prune_keeps_an_open_run_and_a_child_with_no_link(self) -> None:
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)  # resumed: its run is open
        closed_spawn(ROOT_ID, OTHER, "c_other", 0)  # finished
        agent = make_agent()
        # NESTED stands for a child whose spawn link the watcher has not written yet.
        agent._live_subagents = {CHILD: "/root/child", OTHER: "/root/other", NESTED: "/root/new"}

        asyncio.run(agent._prune_finished_subagents())

        assert agent._live_subagents == {CHILD: "/root/child", NESTED: "/root/new"}
        agent._schedule_background_work_refresh.assert_called_once_with()

    def test_prune_never_reads_last_stopped_at(self) -> None:
        """A subagent's ``last_stopped_at`` is a display value, not a run input (§5.4)."""
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)
        Session.objects.filter(id=CHILD).update(last_stopped_at=t(3))
        agent = make_agent()
        agent._live_subagents = {CHILD: "/root/child"}

        asyncio.run(agent._prune_finished_subagents())

        assert agent._live_subagents == {CHILD: "/root/child"}

    def test_stop_relay_keeps_a_running_child(self) -> None:
        """The relay's payload alone never drops a child: the run model decides."""
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        followup(ROOT_ID, CHILD, "c_fu", 10)
        agent = make_agent()
        agent._live_subagents = {CHILD: "/root/child"}

        asyncio.run(agent.notify_subagents_stopped([CHILD]))

        assert agent._live_subagents == {CHILD: "/root/child"}
        agent._schedule_background_work_refresh.assert_not_called()


    def test_a_failed_read_retries_the_stop_relay_once(self, monkeypatch) -> None:
        """No pop on an error (the child may still run); one delayed retry re-reads under the lock."""
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)
        monkeypatch.setattr(agent_module, "_STOPPED_RELAY_RETRY_DELAY", 0.01)
        real = agent_module._stopped_subagent_ids
        reads = []

        def flaky(root_id, ids):
            reads.append(list(ids))
            if len(reads) == 1:
                raise RuntimeError("database is locked")
            return real(root_id, ids)

        monkeypatch.setattr(agent_module, "_stopped_subagent_ids", flaky)
        agent = make_agent(state=AgentState.ASSISTANT_TURN)
        agent._live_subagents = {CHILD: "/root/child"}
        agent._subagent_hold_active = True

        async def run() -> None:
            await agent.notify_subagents_stopped([CHILD])
            assert agent._live_subagents == {CHILD: "/root/child"}  # kept on the error
            assert len(agent._subagent_stop_retry_tasks) == 1
            await asyncio.gather(*agent._subagent_stop_retry_tasks)

        asyncio.run(run())

        assert reads == [[CHILD], [CHILD]]
        assert agent._live_subagents == {}
        assert agent._subagent_hold_active is False
        assert agent.state == AgentState.USER_TURN
        assert agent._subagent_stop_retry_tasks == set()

    def test_the_retry_does_not_retry_again(self, monkeypatch) -> None:
        make_tree()
        monkeypatch.setattr(agent_module, "_STOPPED_RELAY_RETRY_DELAY", 0.01)
        reads = []

        def failing(root_id, ids):
            reads.append(list(ids))
            raise RuntimeError("database is locked")

        monkeypatch.setattr(agent_module, "_stopped_subagent_ids", failing)
        agent = make_agent()
        agent._live_subagents = {CHILD: "/root/child"}

        async def run() -> None:
            await agent.notify_subagents_stopped([CHILD])
            await asyncio.gather(*agent._subagent_stop_retry_tasks)
            assert agent._subagent_stop_retry_tasks == set()

        asyncio.run(run())

        assert len(reads) == 2
        assert agent._live_subagents == {CHILD: "/root/child"}


# ---------------------------------------------------------------------------
# Relay order
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRelayOrder:
    """Fire-and-forget relays may run in any order; each reads the committed state in the lock."""

    def _resumed_tree(self) -> None:
        make_tree()
        closed_spawn(ROOT_ID, CHILD, "c_child", 0)  # run 1: closed
        followup(ROOT_ID, CHILD, "c_fu", 10)  # run 2: open

    def test_resume_relay_then_close_then_stop_relay(self) -> None:
        self._resumed_tree()
        agent = make_agent()
        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))
        assert agent._live_subagents == {CHILD: "/root/child"}

        close_followup(ROOT_ID, "c_fu", 20)  # the next batch closes run 2
        asyncio.run(agent.notify_subagents_stopped([CHILD]))

        assert agent._live_subagents == {}

    def test_close_then_stop_relay_then_late_resume_relay(self) -> None:
        self._resumed_tree()
        close_followup(ROOT_ID, "c_fu", 20)
        agent = make_agent()

        asyncio.run(agent.notify_subagents_stopped([CHILD]))  # not tracked yet: no-op
        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))  # reads run 2 closed

        assert agent._live_subagents == {}

    def test_older_stop_relay_after_a_newer_resume_keeps_the_child(self) -> None:
        self._resumed_tree()
        agent = make_agent()

        asyncio.run(agent.notify_subagents_resumed([(CHILD, "/root/child")]))
        asyncio.run(agent.notify_subagents_stopped([CHILD]))  # the relay of run 1's close
        assert agent._live_subagents == {CHILD: "/root/child"}

        asyncio.run(agent._prune_finished_subagents())
        assert agent._live_subagents == {CHILD: "/root/child"}

    def test_the_relays_are_serialized_by_one_lock(self) -> None:
        """A relay waits for the one holding the lock; it then reads the state committed meanwhile."""
        self._resumed_tree()
        agent = make_agent()

        async def run() -> None:
            await agent._subagent_set_lock.acquire()
            resume = asyncio.create_task(agent.notify_subagents_resumed([(CHILD, "/root/child")]))
            await asyncio.sleep(0.05)
            assert not resume.done()
            assert agent._live_subagents == {}
            await asyncio.to_thread(close_followup, ROOT_ID, "c_fu", 20)
            agent._subagent_set_lock.release()
            await resume

        asyncio.run(run())

        assert agent._live_subagents == {}


# ---------------------------------------------------------------------------
# Ephemeral agent (no watcher rows)
# ---------------------------------------------------------------------------


class TestEphemeralAgent:
    def test_interacted_re_adds_only_when_ephemeral(self) -> None:
        ephemeral = make_agent(ephemeral=True)
        ephemeral._note_sub_agent_activity(activity(CHILD, "interacted", "/root/child"))
        assert ephemeral._live_subagents == {CHILD: "/root/child"}

        backed = make_agent()
        backed._note_sub_agent_activity(activity(CHILD, "interacted", "/root/child"))
        assert backed._live_subagents == {}
        backed._schedule_background_work_refresh.assert_not_called()

    def test_stop_relay_releases_the_hold_without_a_db_read(self, monkeypatch) -> None:
        forbid_db(monkeypatch)
        agent = make_agent(ephemeral=True, state=AgentState.ASSISTANT_TURN)
        agent._live_subagents = {CHILD: "/root/child"}
        agent._subagent_hold_active = True
        agent._codex = thread_reader({CHILD: "idle"})

        async def run() -> None:
            stopped = await agent._ephemeral_finished_subagents()
            await agent.notify_subagents_stopped(stopped)

        asyncio.run(run())

        assert agent._live_subagents == {}
        assert agent._subagent_hold_active is False
        assert agent.state == AgentState.USER_TURN
        agent._notify_state_change.assert_awaited_once()

    def test_stop_relay_keeps_a_child_re_added_and_active_again(self, monkeypatch) -> None:
        """The poll saw it idle; an ``interacted`` item re-adds it before the relay takes the lock."""
        forbid_db(monkeypatch)
        agent = make_agent(ephemeral=True, state=AgentState.ASSISTANT_TURN)
        agent._live_subagents = {CHILD: "/root/child"}
        agent._subagent_hold_active = True
        statuses = {CHILD: "idle"}
        agent._codex = thread_reader(statuses)

        async def run() -> None:
            stopped = await agent._ephemeral_finished_subagents()
            assert stopped == [CHILD]
            await agent._subagent_set_lock.acquire()
            relay = asyncio.create_task(agent.notify_subagents_stopped(stopped))
            await asyncio.sleep(0)
            agent._note_sub_agent_activity(activity(CHILD, "interacted", "/root/child"))
            statuses[CHILD] = "active"
            agent._subagent_set_lock.release()
            await relay

        asyncio.run(run())

        assert agent._live_subagents == {CHILD: "/root/child"}
        assert agent._subagent_hold_active is True
        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_not_awaited()

    def test_prune_drops_idle_children_without_a_db_read(self, monkeypatch) -> None:
        forbid_db(monkeypatch)
        agent = make_agent(ephemeral=True, state=AgentState.ASSISTANT_TURN)
        agent._live_subagents = {CHILD: "/root/child", OTHER: "/root/other"}
        agent._subagent_hold_active = True
        agent._codex = thread_reader({CHILD: "idle", OTHER: "active"})

        asyncio.run(agent._prune_finished_subagents())
        assert agent._live_subagents == {OTHER: "/root/other"}

        agent._codex = thread_reader({OTHER: "idle"})
        armed = asyncio.run(agent._try_arm_subagent_hold())

        assert armed is False
        assert agent._live_subagents == {}
        assert agent._subagent_hold_active is False


# ---------------------------------------------------------------------------
# End to end: the watcher's hooks reach the root's live agent
# ---------------------------------------------------------------------------


class LiveRoot:
    """A replayed fixture synced through the real Codex watcher, with a live root agent registered."""

    def __init__(self, fixture, tmp_path: Path, monkeypatch) -> None:
        self.fixture = fixture
        self.replay = LiveReplay(fixture, tmp_path)
        self.watcher = CodexSessionsWatcher()
        self.agent = make_agent(ROOT, state=AgentState.ASSISTANT_TURN)
        manager = CodexAgentManager.__new__(CodexAgentManager)
        manager._agents = {ROOT: self.agent}
        monkeypatch.setattr(manager_module, "get_codex_agent_manager", lambda: manager)
        monkeypatch.setattr(sessions_watcher, "broadcast_message", AsyncMock())

    def sync_quietly(self, session_id: str, upto: int) -> None:
        """Sync through the compute only (no hooks), up to and including line ``upto``."""
        self.replay.sync(session_id, upto)

    def watch(self, session_id: str, upto: int | None = None) -> None:
        """Append the next lines of ``session_id`` and sync them through the watcher; settle the relays."""
        lines = self.fixture.session(session_id).lines
        end = len(lines) if upto is None else upto
        path = self.replay.paths[session_id]
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines[self.replay.synced[session_id]:end])
        self.replay.synced[session_id] = end
        session = Session.objects.get(id=session_id)
        parsed = ParsedSessionFile(session.project_id, session.id, session.type, file_path=session.file_path,
                                   parent_session_id=session.parent_session_id)

        async def run() -> None:
            await self.watcher.sync_and_broadcast(path, parsed, Change.modified, None)
            relays = [task for task in asyncio.all_tasks() if task.get_name().startswith("subagents-")]
            await asyncio.gather(*relays)

        asyncio.run(run())


def _root_with_prompt() -> Rollout:
    """A root rollout that starts with a user message: the watcher's broadcast block (and so its
    hooks) only runs for a root that has one."""
    root = Rollout(ROOT)
    root.meta(0)
    root.add(Rollout._entry(0.5, "event_msg", {
        "type": "item_completed", "thread_id": ROOT, "turn_id": "t1",
        "item": {"type": "UserMessage", "id": "prompt", "content": [{"type": "text", "text": "go", "text_elements": []}]},
    }))
    root.task_started(1, "t1")
    return root


def _owner_abort_all_cut():
    """Turn t1 spawns A and B, then aborts: both runs are cut, nothing else runs."""
    root = _root_with_prompt()
    root.spawn(2, "c_a", AGENT_A, PATH_A, mark="spawn_a")
    root.spawn(5, "c_b", AGENT_B, PATH_B, mark="spawn_b")
    root.turn_aborted(30, "t1", mark="abort")
    return _fixture("owner_abort_all_cut", root)


def _idle_followup():
    """Spawn → its pair → ``followup_task`` on the idle agent → the follow-up's own pair."""
    root = _root_with_prompt()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.completed(11, AGENT_A, PATH_A, "a1", mark="completed_1")
    root.final_answer(12, PATH_A, mark="final_1")
    root.followup(13, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.completed(20, AGENT_A, PATH_A, "a2", mark="completed_2")
    root.final_answer(21, PATH_A, mark="final_2")
    return _fixture("idle_followup", root)


@pytest.mark.django_db(transaction=True)
class TestHooksReachTheRootAgent:
    def test_owner_turn_abort_drops_the_cut_children_and_releases_the_hold(self, tmp_path, monkeypatch):
        fixture = _owner_abort_all_cut()
        live = LiveRoot(fixture, tmp_path, monkeypatch)
        live.sync_quietly(ROOT, fixture.line(ROOT, "abort") - 1)
        live.agent._live_subagents = {AGENT_A: PATH_A, AGENT_B: PATH_B}
        # The root's turn end came first: the hold arms (both children still run in the DB).
        assert asyncio.run(live.agent._try_arm_subagent_hold()) is True
        assert live.agent._subagent_hold_active is True
        assert live.agent._live_subagents == {AGENT_A: PATH_A, AGENT_B: PATH_B}

        live.watch(ROOT)  # the ``turn_aborted`` line

        assert AgentRunEnd.objects.filter(session_id=ROOT, status="owner_turn_aborted").count() == 2
        assert live.agent._live_subagents == {}
        assert live.agent._subagent_hold_active is False
        assert live.agent.state == AgentState.USER_TURN

    def test_rule_five_close_from_the_child_file_releases_the_hold(self, tmp_path, monkeypatch):
        fixture = fixture_child_turn_ends()
        live = LiveRoot(fixture, tmp_path, monkeypatch)
        live.sync_quietly(ROOT, fixture.line(ROOT, "completed") - 1)  # spawn + its ack: still running
        live.agent._live_subagents = {AGENT_A: PATH_A}
        assert asyncio.run(live.agent._try_arm_subagent_hold()) is True
        assert live.agent._live_subagents == {AGENT_A: PATH_A}

        live.watch(AGENT_A, fixture.line(AGENT_A, "end_1"))  # the child's own turn end (rule 5)

        assert live.agent._live_subagents == {}
        assert live.agent._subagent_hold_active is False
        assert live.agent.state == AgentState.USER_TURN

    def test_followup_batch_fires_the_resume_hook_before_the_stop_hook(self, tmp_path, monkeypatch):
        fixture = _idle_followup()
        live = LiveRoot(fixture, tmp_path, monkeypatch)
        live.sync_quietly(ROOT, fixture.line(ROOT, "final_1"))
        calls = []

        async def resumed(session_id, agents):
            calls.append(("resumed", session_id, agents))

        async def stopped(session_id, agent_ids):
            calls.append(("stopped", session_id, agent_ids))

        monkeypatch.setattr(live.watcher, "_after_agents_resumed", resumed)
        monkeypatch.setattr(live.watcher, "_after_agents_stopped", stopped)

        live.watch(ROOT)  # follow-up, its completed + FINAL_ANSWER, in one batch

        assert calls == [("resumed", ROOT, [(AGENT_A, PATH_A)]), ("stopped", ROOT, [AGENT_A])]

    def test_resume_relay_reaches_the_root_agent(self, tmp_path, monkeypatch):
        fixture = _idle_followup()
        live = LiveRoot(fixture, tmp_path, monkeypatch)
        live.sync_quietly(ROOT, fixture.line(ROOT, "final_1"))
        live.agent.state = AgentState.USER_TURN

        live.watch(ROOT, fixture.line(ROOT, "fu_event"))

        assert live.agent._live_subagents == {AGENT_A: PATH_A}
        live.agent._schedule_background_work_refresh.assert_called()

        live.watch(ROOT)  # its completed + FINAL_ANSWER: the stop relay drops it

        assert live.agent._live_subagents == {}
