"""Refcounted per-session send lanes and draft-to-canonical aliases.

Every test drives the interleaving with ``asyncio.Event`` objects. The only
"waiting" is ``_settle()``, which yields to the event loop a bounded number of
times so already-runnable tasks reach their next await; it never sleeps.
"""

import asyncio

import pytest

from twicc.agent import send_lanes
from twicc.agent.send_lanes import bind_send_lane, send_lane, wait_for_send_barrier


async def _settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def _run(coro):
    send_lanes._reset_for_tests()
    try:
        asyncio.run(coro)
    finally:
        send_lanes._reset_for_tests()


def test_sends_run_one_at_a_time_in_arrival_order_and_registry_clears():
    async def scenario():
        order: list[str] = []
        gates = {name: asyncio.Event() for name in ("first", "second", "third")}
        entered = {name: asyncio.Event() for name in gates}

        async def send(name: str) -> None:
            async with send_lane("sess"):
                entered[name].set()
                await gates[name].wait()
                order.append(name)

        tasks = [asyncio.create_task(send(name)) for name in ("first", "second", "third")]
        await entered["first"].wait()
        await _settle()
        assert not entered["second"].is_set()
        assert send_lanes._reference_count("sess") == 3

        # Releasing a later gate first does not let it overtake the lane.
        gates["third"].set()
        gates["second"].set()
        await _settle()
        assert order == []
        gates["first"].set()
        await asyncio.gather(*tasks)
        assert order == ["first", "second", "third"]
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_lanes_for_different_sessions_are_independent():
    async def scenario():
        hold = asyncio.Event()
        other_done = asyncio.Event()

        async def holder() -> None:
            async with send_lane("a"):
                await hold.wait()

        async def other() -> None:
            async with send_lane("b"):
                other_done.set()

        held = asyncio.create_task(holder())
        await _settle()
        await asyncio.wait_for(other(), timeout=5)
        assert other_done.is_set()
        hold.set()
        await held
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_registry_clears_after_failure():
    async def scenario():
        with pytest.raises(RuntimeError):
            async with send_lane("sess"):
                raise RuntimeError("delivery failed")
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_registry_clears_after_cancelling_holder_and_waiter():
    async def scenario():
        holding = asyncio.Event()
        never = asyncio.Event()

        async def hold() -> None:
            async with send_lane("sess"):
                holding.set()
                await never.wait()

        holder = asyncio.create_task(hold())
        await holding.wait()
        waiter = asyncio.create_task(wait_for_send_barrier("sess"))
        await _settle()
        assert send_lanes._reference_count("sess") == 2

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert send_lanes._reference_count("sess") == 1

        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        assert send_lanes._live_keys() == set()

        # A fresh lane works after a full cancellation cleanup.
        async with send_lane("sess"):
            pass
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_barrier_returns_at_once_when_idle_and_waits_behind_earlier_sends():
    async def scenario():
        await asyncio.wait_for(wait_for_send_barrier("idle"), timeout=5)
        assert send_lanes._live_keys() == set()

        gate = asyncio.Event()
        entered = asyncio.Event()
        barrier_passed = asyncio.Event()

        async def send() -> None:
            async with send_lane("sess"):
                entered.set()
                await gate.wait()

        async def barrier() -> None:
            await wait_for_send_barrier("sess")
            barrier_passed.set()

        sending = asyncio.create_task(send())
        await entered.wait()
        barrier_task = asyncio.create_task(barrier())
        await _settle()
        assert not barrier_passed.is_set()
        gate.set()
        await asyncio.gather(sending, barrier_task)
        assert barrier_passed.is_set()
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_barrier_does_not_hold_the_lane_after_it_passes():
    async def scenario():
        passed = asyncio.Event()
        resume = asyncio.Event()
        later_send_ran = asyncio.Event()

        async def control() -> None:
            await wait_for_send_barrier("sess")
            passed.set()
            await resume.wait()  # The control action runs outside the lane.

        async def later_send() -> None:
            async with send_lane("sess"):
                later_send_ran.set()

        control_task = asyncio.create_task(control())
        await passed.wait()
        await asyncio.wait_for(later_send(), timeout=5)
        assert later_send_ran.is_set()
        resume.set()
        await control_task
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_bind_aliases_the_live_lane_until_every_waiter_finishes():
    async def scenario():
        order: list[str] = []
        creation_gate = asyncio.Event()
        creation_bound = asyncio.Event()
        y_send_gate = asyncio.Event()
        y_send_entered = asyncio.Event()

        async def creation() -> None:
            async with send_lane("draft-x"):
                bind_send_lane("draft-x", "canonical-y")
                creation_bound.set()
                await creation_gate.wait()
                order.append("creation")

        async def y_send() -> None:
            async with send_lane("canonical-y"):
                y_send_entered.set()
                await y_send_gate.wait()
                order.append("y-send")

        async def y_barrier() -> None:
            await wait_for_send_barrier("canonical-y")
            order.append("y-barrier")

        async def x_send() -> None:
            async with send_lane("draft-x"):
                order.append("x-send")

        creating = asyncio.create_task(creation())
        await creation_bound.wait()
        assert send_lanes._live_keys() == {"draft-x", "canonical-y"}

        y_sending = asyncio.create_task(y_send())
        y_barrier_task = asyncio.create_task(y_barrier())
        await _settle()
        assert not y_send_entered.is_set()
        assert send_lanes._reference_count("canonical-y") == 3

        creation_gate.set()
        await y_send_entered.wait()
        # The draft's own send ended, but the alias stays while Y's send runs:
        # a new send through the draft id still joins the same queue.
        assert send_lanes._live_keys() == {"draft-x", "canonical-y"}
        x_sending = asyncio.create_task(x_send())
        await _settle()
        assert order == ["creation"]

        y_send_gate.set()
        await asyncio.gather(creating, y_sending, y_barrier_task, x_sending)
        assert order == ["creation", "y-send", "y-barrier", "x-send"]
        assert send_lanes._live_keys() == set()

        # After the shared entry dies, the canonical id gets its own fresh lane.
        async with send_lane("canonical-y"):
            assert send_lanes._live_keys() == {"canonical-y"}
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_bind_without_live_draft_lane_or_with_equal_ids_is_a_no_op():
    async def scenario():
        bind_send_lane("nobody", "canonical")
        assert send_lanes._live_keys() == set()
        async with send_lane("same"):
            bind_send_lane("same", "same")
            assert send_lanes._live_keys() == {"same"}
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_bind_keeps_an_existing_distinct_canonical_lane():
    async def scenario():
        x_entered = asyncio.Event()
        y_entered = asyncio.Event()
        release = asyncio.Event()

        async def hold(session_id: str, entered: asyncio.Event) -> None:
            async with send_lane(session_id):
                entered.set()
                await release.wait()

        x_task = asyncio.create_task(hold("draft-x", x_entered))
        y_task = asyncio.create_task(hold("canonical-y", y_entered))
        await x_entered.wait()
        await y_entered.wait()
        bind_send_lane("draft-x", "canonical-y")
        assert send_lanes._reference_count("canonical-y") == 1
        release.set()
        await asyncio.gather(x_task, y_task)
        assert send_lanes._live_keys() == set()

    _run(scenario())
