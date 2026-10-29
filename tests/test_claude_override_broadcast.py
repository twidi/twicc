"""The synthetic override frame of the Claude Code manager carries wall-clock timestamps."""

import asyncio
import time

from twicc.agent.states import AgentState
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager


def test_the_override_frame_stamps_epoch_time_not_the_monotonic_loop_clock():
    manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
    frames = []

    async def callback(info):
        frames.append(info)

    manager._broadcast_callback = callback

    before = time.time()
    asyncio.run(manager._broadcast_agent_state("s1", "-p", override_state=AgentState.STARTING))
    after = time.time()

    [info] = frames
    assert info.state == AgentState.STARTING
    for stamp in (info.started_at, info.state_changed_at, info.last_activity):
        assert before <= stamp <= after
