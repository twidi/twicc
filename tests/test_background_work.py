"""Background work: what still runs behind an agent, whatever its state says.

Covers the provider-agnostic snapshot, its bookkeeping in both agents (Claude
Code tasks, Codex unified-exec processes), its publication (front message +
``ProcessRun`` column), the idle auto-stop it blocks, and the Codex watcher
relay for processes that exit after their turn.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import psutil
import pytest
from claude_agent_sdk import SystemMessage, UserMessage
from django.utils import timezone

from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.states import AgentInfo, AgentState, build_background_work, serialize_agent_info
from twicc.core.enums import Provider
from twicc.core.models import Project, ProcessRun, Session, SessionItem
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.codex.agent.agent import CodexAgent


def _with_publication_state(agent):
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._background_work_broadcast_failures = 0
    return agent


def _claude_agent() -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "claude-1"
    agent.state = AgentState.ASSISTANT_TURN
    agent._live_background_tasks = {}
    agent._live_monitor_tasks = set()
    agent._live_shell_tasks = {}
    agent._listed_background_tasks = set()
    agent._wakeup_refresh_handle = None
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._broadcast_process_label = AsyncMock()
    return _with_publication_state(agent)


def _codex_agent() -> CodexAgent:
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "codex-1"
    agent.state = AgentState.USER_TURN
    agent._live_subagents = {}
    agent._live_shells = {}
    agent._first_shell_started_at = None
    agent._recently_ended_shells = {}
    agent._goal_continuation_active = False
    return _with_publication_state(agent)


def _child(pid: int, created: float, cmdline: list[str], status: str = "sleeping"):
    return SimpleNamespace(pid=pid, create_time=lambda: created, cmdline=lambda: cmdline, status=lambda: status)


def _task(subtype: str, task_id: str, **data) -> SystemMessage:
    return SystemMessage(subtype=subtype, data={"subtype": subtype, "task_id": task_id, **data})


def _task_list(*task_ids: str) -> SystemMessage:
    tasks = [{"task_id": task_id, "task_type": "local_bash", "description": ""} for task_id in task_ids]
    return SystemMessage(subtype="background_tasks_changed", data={"subtype": "background_tasks_changed", "tasks": tasks})


def _command_event(method: str, process_id: str | None, thread_id: str = "codex-1"):
    item = SimpleNamespace(type="commandExecution", process_id=process_id, command="sleep 60")
    return method, SimpleNamespace(thread_id=thread_id, item=item)


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


class TestSnapshotShape:
    def test_nothing_running_is_none(self):
        assert build_background_work() is None

    def test_one_fixed_shape_whatever_runs(self):
        assert build_background_work(shells=2) == {
            "subagents": 0, "shells": 2, "monitors": 0,
            "scheduled_wakeup_at": None, "goal": False,
        }

    def test_wakeup_is_iso_utc(self):
        snapshot = build_background_work(scheduled_wakeup_at=0.0)
        assert snapshot["scheduled_wakeup_at"] == "1970-01-01T00:00:00+00:00"

    def test_serialized_only_when_something_runs(self):
        base = AgentInfo(
            session_id="s", project_id="p", provider=Provider.CLAUDE_CODE,
            state=AgentState.USER_TURN, previous_state=None,
            started_at=0.0, state_changed_at=0.0, last_activity=0.0,
        )
        assert "background_work_in_progress" not in serialize_agent_info(base)
        busy = base._replace(background_work_in_progress=build_background_work(shells=1))
        assert serialize_agent_info(busy)["background_work_in_progress"]["shells"] == 1

    def test_a_dead_or_ephemeral_agent_reports_nothing(self):
        agent = _claude_agent()
        agent._live_shell_tasks["b1"] = True
        assert agent.background_work_snapshot()["shells"] == 1
        agent.state = AgentState.DEAD
        assert agent.background_work_snapshot() is None
        agent.state = AgentState.USER_TURN
        agent.ephemeral = True
        assert agent.background_work_snapshot() is None


# ---------------------------------------------------------------------------
# Claude Code bookkeeping
# ---------------------------------------------------------------------------


class TestClaudeTracking:
    def test_only_backgrounded_shells_count(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "fg", task_type="local_bash", is_backgrounded=False)))
        asyncio.run(agent._update_live_tasks(_task("task_started", "bg", task_type="local_bash", is_backgrounded=True)))
        assert agent.background_shell_count() == 1

    def test_a_shell_moved_to_the_background_starts_counting(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "b1", task_type="local_bash", is_backgrounded=False)))
        asyncio.run(agent._update_live_tasks(_task("task_updated", "b1", patch={"is_backgrounded": True})))
        assert agent.background_shell_count() == 1

    def test_the_terminal_event_releases_the_shell(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "b1", task_type="local_bash", is_backgrounded=True)))
        asyncio.run(agent._update_live_tasks(_task("task_notification", "b1", status="completed")))
        assert agent.background_shell_count() == 0
        assert agent.current_background_work() is None

    def test_a_monitor_is_not_a_shell(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "m1", task_type="local_bash", is_backgrounded=True)))
        asyncio.run(agent._update_live_monitor_tasks(UserMessage(
            content="Monitor started (task m1, timeout 600000ms).",
            tool_use_result={"taskId": "m1", "timeoutMs": 600000},
        )))
        work = agent.current_background_work()
        assert (work["shells"], work["monitors"]) == (0, 1)
        # And a late task_started for an already-known Monitor stays out too.
        asyncio.run(agent._update_live_tasks(_task("task_started", "m1", task_type="local_bash", is_backgrounded=True)))
        assert agent.background_shell_count() == 0

    def test_subagents_and_wakeup_are_reported(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "a1", task_type="local_agent")))
        agent._pending_wakeup_at = time.time() + 600
        work = agent.current_background_work()
        assert work["subagents"] == 1
        assert work["scheduled_wakeup_at"] is not None
        agent._pending_wakeup_at = time.time() - 1
        assert agent.current_background_work()["scheduled_wakeup_at"] is None

    def test_the_cli_task_list_drops_a_shell_whose_end_went_missing(self):
        agent = _claude_agent()
        asyncio.run(agent._update_live_tasks(_task("task_started", "b1", task_type="local_bash", is_backgrounded=True)))
        asyncio.run(agent._update_live_tasks(_task("task_started", "b2", task_type="local_bash", is_backgrounded=True)))
        # b2 is not listed yet: a shell may start before any list names it.
        asyncio.run(agent._update_live_tasks(_task_list("b1")))
        assert agent.background_shell_count() == 2
        # b1 was listed and is now gone: its terminal event went missing.
        asyncio.run(agent._update_live_tasks(_task_list("b2")))
        assert list(agent._live_shell_tasks) == ["b2"]

    def test_a_pending_wakeup_publishes_again_when_it_comes_due(self):
        agent = _claude_agent()
        published = []
        agent._schedule_background_work_refresh = lambda: published.append(agent.current_background_work())

        async def run():
            # Due in 0.1 s: the timer fires 1 s past the deadline.
            agent._pending_wakeup_at = time.time() + 0.1
            agent._arm_wakeup_refresh()
            assert agent._wakeup_refresh_handle is not None
            await asyncio.sleep(1.3)

        asyncio.run(run())
        assert published and published[-1] is None


# ---------------------------------------------------------------------------
# Codex bookkeeping
# ---------------------------------------------------------------------------


class TestCodexTracking:
    def test_started_then_completed_process(self):
        agent = _codex_agent()
        agent._note_command_execution(*_command_event("item/started", "30127"))
        assert agent.background_shell_count() == 1
        agent._note_command_execution(*_command_event("item/completed", "30127"))
        assert agent.background_shell_count() == 0

    def test_items_without_a_process_are_ignored(self):
        agent = _codex_agent()
        agent._note_command_execution(*_command_event("item/started", None))
        assert agent._live_shells == {}

    def test_a_subagent_process_counts_and_is_released_per_thread(self):
        """Its items never reach our stream: the watcher relays its rollout."""
        agent = _codex_agent()
        asyncio.run(agent.notify_shells_started("child", {"7": time.time()}))
        agent._note_command_execution(*_command_event("item/started", "7"))
        # Same process id, other thread: only the named thread's entry goes.
        asyncio.run(agent.notify_shells_exited("child", ["7"]))
        assert list(agent._live_shells) == [("codex-1", "7")]
        asyncio.run(agent.notify_shells_exited("codex-1", ["7"]))
        assert agent.has_live_shells() is False

    def test_another_thread_item_on_our_stream_is_ignored(self):
        agent = _codex_agent()
        agent._note_command_execution(*_command_event("item/started", "7", thread_id="child"))
        assert agent._live_shells == {}

    def test_an_announcement_right_after_the_end_does_not_revive(self):
        agent = _codex_agent()
        asyncio.run(agent.notify_shells_exited("child", ["7"]))
        asyncio.run(agent.notify_shells_started("child", {"7": time.time()}))
        assert agent._live_shells == {}

    def test_the_manager_remembers_ends_even_for_an_agent_tracking_nothing(self):
        """The end lands in an earlier watcher batch than the announcement."""
        from twicc.providers.codex.agent.manager import CodexAgentManager

        agent = _codex_agent()
        manager = CodexAgentManager.__new__(CodexAgentManager)
        manager._agents = {"codex-1": agent}
        asyncio.run(manager.notify_shells_exited("child", ["7"]))
        asyncio.run(manager.notify_shells_started("codex-1", "child", {"7": time.time()}))
        assert agent._live_shells == {}

    def test_goal_and_subagents_are_reported(self):
        agent = _codex_agent()
        agent._live_subagents = {"child": "/root/a"}
        agent._goal_continuation_active = True
        work = agent.current_background_work()
        assert (work["subagents"], work["goal"], work["monitors"]) == (1, True, 0)


class TestCodexShellProbe:
    """The safety net for a process killed without an end event."""

    def _children(self, monkeypatch, children):
        monkeypatch.setattr(
            "twicc.providers.codex.agent.agent.psutil.Process",
            lambda pid: SimpleNamespace(children=lambda recursive: children),
        )

    def test_only_a_fresh_non_helper_child_may_run_a_command(self, monkeypatch):
        from twicc.providers.codex.agent.agent import command_processes_may_run

        since = 1000.0
        mcp = _child(1, since - 60, ["hey", "mcp"])  # started before any command
        host = _child(2, since + 5, ["/x/codex-code-mode-host"])  # helper
        zombie = _child(3, since + 5, ["sleep", "1"], status="zombie")
        self._children(monkeypatch, [mcp, host, zombie])
        assert command_processes_may_run(4242, since) is False
        self._children(monkeypatch, [mcp, host, _child(4, since + 9, ["sleep", "240"])])
        assert command_processes_may_run(4242, since) is True

    def test_a_child_that_cannot_be_inspected_is_a_doubt(self, monkeypatch):
        from twicc.providers.codex.agent.agent import command_processes_may_run

        def denied():
            raise psutil.AccessDenied(9)

        child = SimpleNamespace(pid=9, create_time=denied, cmdline=list, status=lambda: "sleeping")
        self._children(monkeypatch, [child])
        assert command_processes_may_run(4242, 0.0) is True

    def test_the_probe_reaches_back_before_the_first_start(self):
        """psutil creation times are rounded to the boot second."""
        agent = _codex_agent()
        agent.get_pid = lambda: 4242
        agent._track_shell(("codex-1", "1"), 1000.0)
        assert agent.shell_probe() == (4242, 995.0)

    def test_a_relayed_start_is_placed_well_before_its_announcement(self):
        """The subagent's process spawned a yield (and a relay lag) earlier."""
        agent = _codex_agent()
        asyncio.run(agent.notify_shells_started("child", {"7": 1000.0}))
        assert agent._live_shells[("child", "7")] == 940.0
        assert agent._first_shell_started_at == 940.0

    def test_an_unreadable_process_table_proves_nothing(self, monkeypatch):
        from twicc.providers.codex.agent.agent import command_processes_may_run

        def boom(pid):
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr("twicc.providers.codex.agent.agent.psutil.Process", boom)
        assert command_processes_may_run(4242, 0.0) is None

    def test_no_command_process_drops_only_the_shells_old_enough(self):
        agent = _codex_agent()
        now = time.time()
        agent._track_shell(("codex-1", "old"), now - 60)
        agent._track_shell(("codex-1", "young"), now - 1)  # its process may not be spawned yet
        assert agent.drop_gone_shells(now) is True
        assert list(agent._live_shells) == [("codex-1", "young")]

    def _idle_agent(self, monkeypatch):
        agent = _codex_agent()
        agent._track_shell(("codex-1", "1"), time.time() - 60)
        agent.get_pid = lambda: 4242
        agent.last_activity = 0.0
        agent._schedule_background_work_refresh = MagicMock()
        monkeypatch.setattr(type(agent), "pending_requests", property(lambda self: ()))
        return agent

    def test_the_idle_check_drops_gone_shells_before_deciding(self, monkeypatch):
        from twicc.providers.codex.agent.manager import CodexAgentManager

        agent = self._idle_agent(monkeypatch)
        monkeypatch.setattr("twicc.providers.codex.agent.manager.command_processes_may_run", lambda pid, since: False)
        manager = CodexAgentManager.__new__(CodexAgentManager)

        result = asyncio.run(manager._check_agent_timeout(agent, time.time()))

        assert agent._live_shells == {}
        agent._schedule_background_work_refresh.assert_called_once()
        # With the stale shell gone, the idle session times out as usual.
        assert result is not None and result[0] == "timeout_user_turn"

    @pytest.mark.parametrize("may_run", [True, None])
    def test_any_doubt_keeps_the_session_alive(self, monkeypatch, may_run):
        from twicc.providers.codex.agent.manager import CodexAgentManager

        agent = self._idle_agent(monkeypatch)
        monkeypatch.setattr(
            "twicc.providers.codex.agent.manager.command_processes_may_run", lambda pid, since: may_run,
        )
        manager = CodexAgentManager.__new__(CodexAgentManager)

        assert asyncio.run(manager._check_agent_timeout(agent, time.time())) is None
        assert agent.has_live_shells()


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


class TestPublication:
    def test_changes_are_coalesced_and_published_once(self):
        agent = _codex_agent()
        agent.BACKGROUND_WORK_DEBOUNCE_SECONDS = 0.01
        agent._broadcast_stream_event = AsyncMock()
        agent._background_work_callback = AsyncMock()

        async def run():
            # A one-second command: start and end land before the debounce.
            agent._note_command_execution(*_command_event("item/started", "1"))
            agent._note_command_execution(*_command_event("item/completed", "1"))
            # A long one.
            agent._note_command_execution(*_command_event("item/started", "2"))
            await agent._background_work_refresh_task

        asyncio.run(run())
        agent._broadcast_stream_event.assert_awaited_once()
        message = agent._broadcast_stream_event.await_args.args[0]
        assert message["type"] == "process_background_work"
        assert message["background_work_in_progress"]["shells"] == 1
        agent._background_work_callback.assert_awaited_once()

    def test_an_unchanged_snapshot_publishes_nothing(self):
        agent = _codex_agent()
        agent._broadcast_stream_event = AsyncMock()
        agent._background_work_callback = AsyncMock()
        asyncio.run(agent._publish_background_work())
        agent._broadcast_stream_event.assert_not_awaited()
        agent._background_work_callback.assert_not_awaited()

    def test_a_snapshot_published_by_a_transition_is_not_republished(self):
        agent = _codex_agent()
        agent._live_shells[("codex-1", "1")] = 0.0
        agent._broadcast_stream_event = AsyncMock()
        agent.note_background_work_published(agent.background_work_snapshot())
        asyncio.run(agent._publish_background_work())
        agent._broadcast_stream_event.assert_not_awaited()

    def test_a_failed_broadcast_is_retried(self):
        agent = _codex_agent()
        agent.BACKGROUND_WORK_DEBOUNCE_SECONDS = 0.01
        agent._live_shells[("codex-1", "1")] = 0.0
        agent._broadcast_stream_event = AsyncMock(side_effect=[RuntimeError("down"), None])
        agent._background_work_callback = AsyncMock()

        async def run():
            await agent._publish_background_work()
            # The row is still written, and a retry is on its way.
            agent._background_work_callback.assert_awaited_once()
            await agent._background_work_refresh_task

        asyncio.run(run())
        assert agent._broadcast_stream_event.await_count == 2
        assert agent._background_work_broadcast_failures == 0

    def test_a_transition_racing_a_refresh_publishes_the_current_value_again(self):
        """The work changed while the transition awaited its DB write."""
        agent = _codex_agent()
        stale = AgentInfo(
            session_id="codex-1", project_id="p", provider=Provider.CODEX,
            state=AgentState.USER_TURN, previous_state=None,
            started_at=0.0, state_changed_at=0.0, last_activity=0.0,
            background_work_in_progress=build_background_work(shells=1),
        )
        # The shell ended meanwhile: the agent runs nothing any more.
        agent.get_info = lambda: stale
        manager = BaseAgentManager.__new__(BaseAgentManager)
        manager._persist_process_run_transition = AsyncMock()
        manager._broadcast_info = AsyncMock()
        agent._schedule_background_work_refresh = MagicMock()
        agent.ephemeral = False

        asyncio.run(BaseAgentManager._on_state_change(manager, agent))

        agent._schedule_background_work_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Idle auto-stop
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    def _idle_agent(self, shells: int):
        return SimpleNamespace(
            state=AgentState.USER_TURN,
            pending_requests=(),
            last_activity=0.0,
            background_shell_count=lambda: shells,
        )

    def test_an_idle_session_times_out(self):
        manager = BaseAgentManager.__new__(BaseAgentManager)
        result = manager._state_based_timeout(self._idle_agent(0), time.time())
        assert result is not None and result[0] == "timeout_user_turn"

    def test_a_background_shell_keeps_it_alive(self):
        manager = BaseAgentManager.__new__(BaseAgentManager)
        assert manager._state_based_timeout(self._idle_agent(1), time.time()) is None


# ---------------------------------------------------------------------------
# ProcessRun persistence
# ---------------------------------------------------------------------------


async def _direct(factory):
    return await factory()


@pytest.fixture
def unlocked_writes(monkeypatch):
    monkeypatch.setattr("twicc.agent.base_manager.run_under_db_write_lock", _direct)


@pytest.mark.django_db(transaction=True)
class TestProcessRunColumn:
    def _run(self) -> ProcessRun:
        now = timezone.now()
        return ProcessRun.objects.create(
            provider="codex", session_id="codex-1", started_at=now,
            last_state_change_at=now, state=AgentState.USER_TURN.value,
        )

    def test_the_refresh_path_writes_the_current_value(self, unlocked_writes):
        """What the agent runs once the lock is held, not the queued value."""
        run = self._run()
        agent = _codex_agent()
        agent._live_shells[("codex-1", "1")] = 0.0
        agent.process_run = run
        agent.provider = Provider.CODEX
        manager = BaseAgentManager.__new__(BaseAgentManager)

        asyncio.run(manager._persist_background_work(agent, None))

        run.refresh_from_db()
        assert run.background_work_in_progress == build_background_work(shells=1)
        assert run.state == AgentState.USER_TURN.value

    def test_a_transition_writes_it_and_dead_clears_it(self, unlocked_writes, monkeypatch):
        run = self._run()
        agent = _codex_agent()
        agent.process_run = run
        agent.provider = Provider.CODEX
        agent._pending_requests = {}
        agent.get_pid = lambda: None
        agent._live_shells[("codex-1", "1")] = 0.0
        manager = BaseAgentManager.__new__(BaseAgentManager)

        asyncio.run(manager._persist_process_run_transition(agent, AgentState.USER_TURN))
        run.refresh_from_db()
        assert run.background_work_in_progress == build_background_work(shells=1)

        helpers = SimpleNamespace(should_keep_dead_process_run=lambda *a, **k: True)
        monkeypatch.setattr("twicc.providers.helpers.get_provider_helpers", lambda provider: helpers)
        asyncio.run(manager._persist_process_run_transition(agent, AgentState.DEAD))
        run.refresh_from_db()
        assert run.background_work_in_progress is None


# ---------------------------------------------------------------------------
# Codex watcher relay
# ---------------------------------------------------------------------------


def _rollout_line(payload: dict, type_: str = "event_msg") -> str:
    return orjson.dumps({"timestamp": "2026-09-25T12:00:00+00:00", "type": type_, "payload": payload}).decode()


def _ended_line(process_id: str) -> str:
    return _rollout_line({"type": "item_completed", "item": {
        "type": "CommandExecution", "id": "exec-1", "process_id": process_id,
        "status": "completed", "exit_code": 0,
    }})


@pytest.mark.django_db(transaction=True)
def test_the_watcher_relays_processes_ended_after_their_turn():
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

    project = Project.objects.create(id="bg-work-project")
    session = Session.objects.create(id="codex-1", project=project, provider=Provider.CODEX)
    other = _rollout_line({"type": "item_completed", "item": {"type": "Reasoning"}})
    SessionItem.objects.bulk_create([
        SessionItem(session=session, line_num=1, content=other),
        SessionItem(session=session, line_num=2, content=_ended_line("30127")),
    ])
    manager = SimpleNamespace(notify_shells_exited=AsyncMock(), notify_shells_started=AsyncMock())

    asyncio.run(CodexSessionsWatcher()._relay_shell_events(manager, session.id, None, [1, 2]))

    manager.notify_shells_exited.assert_awaited_once_with("codex-1", ["30127"])
    manager.notify_shells_started.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_the_watcher_relays_a_subagent_s_process_starts_to_its_parent():
    """A subagent's shell items never reach its parent's stream."""
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

    project = Project.objects.create(id="bg-work-project")
    Session.objects.create(id="codex-1", project=project, provider=Provider.CODEX, file_path="a.jsonl")
    child = Session.objects.create(
        id="codex-child", project=project, provider=Provider.CODEX, parent_session_id="codex-1",
        file_path="b.jsonl",
    )
    call = _rollout_line(
        {"type": "function_call", "call_id": "call_ec", "name": "exec_command",
         "arguments": orjson.dumps({"cmd": "npm run dev"}).decode()},
        type_="response_item",
    )
    running = _rollout_line(
        {"type": "function_call_output", "call_id": "call_ec",
         "output": "Process running with session ID 555\nOutput:\n"},
        type_="response_item",
    )
    short_lived = _rollout_line(
        {"type": "function_call_output", "call_id": "call_ec",
         "output": "Process running with session ID 777\nOutput:\n"},
        type_="response_item",
    )
    SessionItem.objects.bulk_create([
        SessionItem(session=child, line_num=1, content=call),
        SessionItem(session=child, line_num=2, content=running),
        SessionItem(session=child, line_num=3, content=_ended_line("777")),
        SessionItem(session=child, line_num=4, content=short_lived),
    ])
    manager = SimpleNamespace(notify_shells_exited=AsyncMock(), notify_shells_started=AsyncMock())

    asyncio.run(CodexSessionsWatcher()._relay_shell_events(manager, child.id, "codex-1", [1, 2, 3, 4]))

    # Starts go out before ends, so the process ended before its
    # announcement nets out on the agent side.
    started_at = datetime.fromisoformat("2026-09-25T12:00:00+00:00").timestamp()
    manager.notify_shells_started.assert_awaited_once_with(
        "codex-1", "codex-child", {"555": started_at, "777": started_at},
    )
    manager.notify_shells_exited.assert_awaited_once_with("codex-child", ["777"])
