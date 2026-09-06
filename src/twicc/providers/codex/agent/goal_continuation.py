"""Consume runtime-owned goal turns and steer their physical IDs.

The SDK reader routes goal notifications before resolving RPC responses. Register
before goal/set: subscribing afterwards loses a fast first turn. Keep this SDK
private-API adapter here, outside the vendored package, for re-vendoring audits.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from openai_codex import AsyncCodex, AsyncTurnHandle
from openai_codex._inputs import RunInput
from openai_codex._goal import _GoalOperationState
from openai_codex.errors import JsonRpcError
from openai_codex.generated.v2_all import ThreadGoalStatus, TurnSteerResponse
from openai_codex.models import Notification

# Only explicit server rejections prove that retrying cannot duplicate input.
_TURN_MISMATCH = re.compile(r"expected active turn id `[^`]+` but found `[^`]+`")
GOAL_STEER_WAIT_SECONDS = 5.0


class _GoalRoute(_GoalOperationState):
    def is_finished(self) -> bool:
        # The SDK normally detaches at the first terminal goal event. TwiCC
        # can replace that goal while its stream consumer is still draining.
        # Keep this route until our consumer closes it, including across set/clear.
        return False


class GoalContinuation:
    def __init__(self, codex: AsyncCodex, thread_id: str) -> None:
        self.codex = codex
        self.thread_id = thread_id
        self.state = _GoalRoute(thread_id=thread_id)
        self.state.activate_turn_routing()
        codex._client._sync._router._register_goal(self.state)
        self.closed = False
        self.command_done = asyncio.Event()
        self.command_done.set()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.codex._client.unregister_goal_operation(self.state)
        self.state.finish()
        # Cancelling to_thread does not stop a blocking queue.get(). Wake it.
        self.state.wake_notification_reader()

    async def steer(self, turn_input: RunInput) -> TurnSteerResponse:
        deadline = asyncio.get_running_loop().time() + GOAL_STEER_WAIT_SECONDS
        rejected_turn = None
        while True:
            if self.closed:
                raise RuntimeError("Cannot steer: goal continuation has ended")
            turn_id = self.state.current_turn()
            if turn_id is not None and turn_id != rejected_turn:
                try:
                    # Do not timeout the RPC and replay it: delivery would be ambiguous.
                    return await AsyncTurnHandle(self.codex, self.thread_id, turn_id).steer(turn_input)
                except JsonRpcError as exc:
                    if exc.code != -32600 or not (
                        exc.message == "no active turn to steer" or _TURN_MISMATCH.fullmatch(exc.message)
                    ):
                        raise
                    rejected_turn = turn_id
            elif self.state.cleared or (self.state.status is not None and self.state.status != ThreadGoalStatus.active):
                raise RuntimeError("Cannot steer: goal continuation has ended")
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("Cannot steer: no active goal turn became available in time")
            # Read the router's state, not the slower UI stream consumer's handle.
            # This bounded wait creates no orphan blocking worker on cancellation.
            await asyncio.sleep(0.02)

    async def stream(self) -> AsyncIterator[Notification]:
        active_turn = None
        terminal = False
        try:
            while True:
                event = await asyncio.to_thread(self.state.next_notification)
                if event.method == "turn/started":
                    active_turn = event.payload.turn.id
                elif event.method == "turn/completed" and active_turn == event.payload.turn.id:
                    active_turn = None
                elif event.method == "thread/goal/updated":
                    terminal = event.payload.goal.status != ThreadGoalStatus.active
                elif event.method == "thread/goal/cleared":
                    terminal = True
                yield event
                if terminal and active_turn is None:
                    # A goal command may clear then replace the goal, or await
                    # transcript injection after clear. Do not settle midway.
                    await self.command_done.wait()
                    # The reader may already have queued a complete replacement
                    # goal. Drain those events before deciding to detach.
                    if not self.state._notifications.empty():
                        continue
                    if self.state.current_turn() is None and (
                        self.state.cleared
                        or (self.state.status is not None and self.state.status != ThreadGoalStatus.active)
                    ):
                        return
                    terminal = False
        finally:
            self.close()
