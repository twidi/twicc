"""Manager side of the background shell notice (spec §4, §7)."""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock

from tests.shell_notice_helpers import run_async
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.shell_notice import (
    SHELL_NOTICE_DELAY_SECONDS,
    ShellInfo,
    ShellLookup,
    ShellNoticeState,
    ShellOwner,
)
from twicc.agent.states import AgentState


def shell(key="b1", owner=ShellOwner.MAIN, started_at=0.0):
    return ShellInfo(key=key, shell_id=key, tool_use_id=None, owner=owner, owner_ref=None, owner_label=None,
                     owner_spawner_ref=None, owner_run_ended_at=None, owner_running=False, description=None,
                     command=None, output_path=None, started_at=started_at)


class FakeAgent:
    def __init__(self, shells, *, idle=True, lookups=()):
        self.session_id = "s1"
        self.state = AgentState.USER_TURN
        self._state = ShellNoticeState(idle=idle, shells=list(shells), any_subagent_running=False,
                                       last_subagent_run_end=0.0)
        self._lookups = list(lookups)
        self._shell_notice_idle_since = None
        self._shell_notice_notified = set()
        self._shell_notice_task = None
        self.stored = []
        self.send = AsyncMock(return_value=True)

    def shell_notice_state(self):
        return self._state

    def shell_notice_lookups(self, keys, now):
        return [lookup for lookup in self._lookups if lookup.key in keys]

    def store_shell_resolutions(self, facts, now):
        self.stored.extend(facts)

    def mark_shells_noticed(self, keys):
        self._shell_notice_notified.update(keys)


def make_manager(agent):
    manager = BaseAgentManager.__new__(BaseAgentManager)
    manager._agents = {agent.session_id: agent}
    manager._lock = asyncio.Lock()
    return manager


async def run_step(manager, agent):
    await manager._shell_notice_step(agent)
    if agent._shell_notice_task is not None:
        await agent._shell_notice_task


@run_async
async def test_first_idle_tick_only_starts_the_delay():
    agent = FakeAgent([shell()])
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_idle_since is not None
    agent.send.assert_not_awaited()


@run_async
async def test_sends_once_after_the_delay():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    await run_step(manager, agent)
    agent.send.assert_awaited_once()
    text = agent.send.await_args.args[0]
    assert text.startswith(":: notice from TwiCC")
    assert agent.send.await_args.kwargs == {"shell_notice": True}
    assert agent._shell_notice_notified == {"b1"}
    agent._shell_notice_task = None
    await run_step(manager, agent)
    agent.send.assert_awaited_once()


@run_async
async def test_not_idle_resets_idle_since_and_sends_nothing():
    agent = FakeAgent([shell()], idle=False)
    agent._shell_notice_idle_since = 1.0
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_idle_since is None
    agent.send.assert_not_awaited()


@run_async
async def test_failed_send_adds_nothing_and_retries():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    agent.send = AsyncMock(return_value=False)
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert agent._shell_notice_notified == set()
    agent._shell_notice_task = None
    agent.send = AsyncMock(side_effect=RuntimeError("boom"))
    await run_step(manager, agent)
    assert agent._shell_notice_notified == set()


@run_async
async def test_replaced_agent_gets_nothing():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    manager._agents = {"s1": object()}
    await run_step(manager, agent)
    agent.send.assert_not_awaited()


@run_async
async def test_stale_task_does_not_send():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    await manager.send_shell_notice(agent, time.time() - 31)
    agent.send.assert_not_awaited()


@run_async
async def test_database_part_runs_only_after_the_cheap_filter(monkeypatch):
    calls = []
    monkeypatch.setattr("twicc.agent.base_manager.resolve_shell_owners",
                        lambda root_id, lookups: calls.append(list(lookups)) or [])
    fresh = shell("b1", owner=ShellOwner.UNRESOLVED, started_at=time.time())
    agent = FakeAgent([fresh], lookups=[ShellLookup("b1", "t", None, False)])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    await run_step(manager, agent)
    assert calls == []
    old = shell("b1", owner=ShellOwner.UNRESOLVED, started_at=0.0)
    agent._state = agent._state._replace(shells=[old])
    await run_step(manager, agent)
    assert calls == [[ShellLookup("b1", "t", None, False)]]


@run_async
async def test_running_task_skips_the_step():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    agent._shell_notice_task = asyncio.get_running_loop().create_future()
    manager = make_manager(agent)
    await manager._shell_notice_step(agent)
    agent.send.assert_not_awaited()
    agent._shell_notice_task.cancel()


@run_async
async def test_one_agent_error_does_not_stop_the_others():
    broken = FakeAgent([shell()])
    broken.session_id = "broken"
    broken.shell_notice_state = MagicMock(side_effect=RuntimeError("boom"))
    healthy = FakeAgent([shell()])
    manager = make_manager(healthy)
    # The broken agent comes first, so the healthy one is checked after the error.
    manager._agents = {"broken": broken, healthy.session_id: healthy}
    manager._check_agent_timeout = AsyncMock(return_value=None)
    await manager.check_and_stop_timed_out_agents()
    assert healthy._shell_notice_idle_since is not None


@run_async
async def test_error_inside_the_send_task_does_not_escape():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    agent.shell_notice_state = MagicMock(side_effect=RuntimeError("boom"))
    agent.mark_shells_noticed = MagicMock()
    manager = make_manager(agent)
    await manager.send_shell_notice(agent, time.time())
    agent.send.assert_not_awaited()
    agent.mark_shells_noticed.assert_not_called()
    assert not manager._lock.locked()


@run_async
async def test_send_rechecks_idle_under_the_lock():
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)
    # A turn opened between the tick and the send.
    agent._state = agent._state._replace(idle=False)
    await manager.send_shell_notice(agent, time.time())
    agent.send.assert_not_awaited()


@run_async
async def test_a_shell_concerned_later_gets_its_own_single_notice():
    agent = FakeAgent([shell("b1")])
    agent._shell_notice_idle_since = time.time() - SHELL_NOTICE_DELAY_SECONDS - 1
    manager = make_manager(agent)
    await run_step(manager, agent)
    agent._shell_notice_task = None
    agent._state = agent._state._replace(shells=[shell("b1"), shell("b2")])
    await run_step(manager, agent)
    agent._shell_notice_task = None
    await run_step(manager, agent)
    assert agent.send.await_count == 2
    assert "`b2`" in agent.send.await_args_list[1].args[0]
    assert "`b1`" not in agent.send.await_args_list[1].args[0]


@run_async
async def test_gate_is_taken_before_the_lock():
    order = []
    agent = FakeAgent([shell()])
    agent._shell_notice_idle_since = 0.0
    manager = make_manager(agent)

    @contextlib.asynccontextmanager
    async def gate(session_id):
        order.append("gate")
        assert not manager._lock.locked()
        yield

    manager._send_gate = gate
    await manager.send_shell_notice(agent, time.time())
    assert order == ["gate"]
    agent.send.assert_awaited_once()
