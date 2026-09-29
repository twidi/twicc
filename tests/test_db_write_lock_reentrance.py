"""Tests for the DB write lock's reentrance behaviour (LOCK#2).

These tests exercise the runners in isolation from the rest of the DB
writer machinery (no queues, no consumer task, no Django ORM). They
manually install / uninstall the module-level lock and stop event so
each test starts from a clean slate.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from twicc.providers import db_writer
from twicc.providers.db_writer import (
    _db_write_lock_lease,
    _run_under_db_write_lock,
    run_under_db_write_lock,
    spawn_isolated_db_write_task,
)


@pytest.fixture(autouse=True)
def _install_lock_state():
    """Install a fresh asyncio.Lock + dummy stop event for each test.

    The public runner checks ``_db_writer_stop_event`` at entry, so we
    install a real ``asyncio.Event`` that we never set — the check then
    passes as ``not is_set()``.
    """
    saved_lock = db_writer._db_write_lock
    saved_stop = db_writer._db_writer_stop_event
    db_writer._db_write_lock = asyncio.Lock()
    db_writer._db_writer_stop_event = asyncio.Event()
    try:
        yield
    finally:
        db_writer._db_write_lock = saved_lock
        db_writer._db_writer_stop_event = saved_stop


# ---------------------------------------------------------------------------
# Reentrance — nested calls reuse the held lock instead of self-deadlocking
# ---------------------------------------------------------------------------


def test_nested_run_under_db_write_lock_does_not_deadlock():
    """An outer ``run_under_db_write_lock`` whose factory itself calls
    ``run_under_db_write_lock`` must complete without deadlocking.
    """

    async def inner():
        return "inner"

    async def outer_factory():
        # Nested call inside the held lock.
        nested = await run_under_db_write_lock(lambda: inner())
        return f"outer:{nested}"

    async def scenario():
        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: outer_factory()),
            timeout=2.0,
        )

    result = asyncio.run(scenario())
    assert result == "outer:inner"


def test_nested_via_helper_does_not_deadlock():
    """The nested call may be hidden behind a helper that the outer
    factory just ``await``s — reentrance still kicks in.
    """

    async def helper():
        return await run_under_db_write_lock(lambda: _identity("via-helper"))

    async def scenario():
        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: helper()),
            timeout=2.0,
        )

    assert asyncio.run(scenario()) == "via-helper"


def test_reentrance_works_under_eager_task_factory():
    """Reentrance must work even when the event loop uses
    :func:`asyncio.eager_task_factory` (Python 3.12+).

    Eager mode runs ``asyncio.create_task(coro)``'s coroutine
    synchronously until its first suspension point. The drive's
    inner Task is created with that factory, so its body now executes
    immediately at the call site. If the inner's first action is a
    nested ``run_under_db_write_lock`` call, the membership check
    must still admit the inner Task — otherwise the nested call
    would fall through to a real acquire, block on the lock the
    outer caller already holds, and deadlock the drive (drive waits
    for inner, inner waits for the lock, nobody releases).

    The implementation guards against this by wrapping the factory
    in an inner coroutine that admits ``current_task()`` to
    ``drive_tasks`` BEFORE any user code runs.
    """

    async def factory():
        # Immediate nested call. Under eager mode this body runs at
        # ``create_task`` time — so the drive's wrapper must have
        # admitted us (i.e. ``current_task()``) to ``drive_tasks``
        # as its FIRST statement, before our own body started.
        # Otherwise the nested call below would not short-circuit.
        #
        # We pin the invariant explicitly with an assertion BEFORE
        # the nested call. A regression where the wrapper failed to
        # self-admit would surface as ``AssertionError`` here (loud,
        # observable) instead of hanging the suite forever on the
        # deadlock (the shield-loop in the drive prevents wait_for
        # from breaking the deadlock, so timeout=2.0 below would
        # not actually fire if we let the nested call deadlock).
        lease = _db_write_lock_lease.get()
        assert lease is not None, "inner task lost the inherited lease"
        assert asyncio.current_task() in lease.drive_tasks, (
            "inner task not in drive_tasks at start — the wrapper's "
            "self-admit failed; a nested run_under_db_write_lock here "
            "would deadlock"
        )
        return await run_under_db_write_lock(lambda: _identity("eager-nested"))

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            return await asyncio.wait_for(
                run_under_db_write_lock(lambda: factory()),
                timeout=2.0,
            )
        finally:
            loop.set_task_factory(None)

    assert asyncio.run(scenario()) == "eager-nested"


def test_internal_runner_is_reentrant_too():
    """``_run_under_db_write_lock`` shares the same lease, so a nested
    call from inside an outer internal runner is safe too.
    """

    async def scenario():
        async def factory():
            return await _run_under_db_write_lock(lambda: _identity("nested-internal"))

        return await asyncio.wait_for(
            _run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    assert asyncio.run(scenario()) == "nested-internal"


def test_public_inside_internal_and_vice_versa():
    """Cross-runner reentrance: an internal-runner outer with a public
    inner (and the symmetric case) must both work.
    """

    async def public_in_internal():
        async def factory():
            return await run_under_db_write_lock(lambda: _identity("pub-in-int"))

        return await asyncio.wait_for(
            _run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    async def internal_in_public():
        async def factory():
            return await _run_under_db_write_lock(lambda: _identity("int-in-pub"))

        return await asyncio.wait_for(
            run_under_db_write_lock(lambda: factory()),
            timeout=2.0,
        )

    assert asyncio.run(public_in_internal()) == "pub-in-int"
    assert asyncio.run(internal_in_public()) == "int-in-pub"


# ---------------------------------------------------------------------------
# Lease lifecycle — installed on acquire, invalidated on release
# ---------------------------------------------------------------------------


def test_lease_resets_after_success():
    """After a clean run, the outer's lease must be cleared from the
    caller's context.
    """

    async def scenario():
        assert _db_write_lock_lease.get() is None
        await run_under_db_write_lock(lambda: _identity("ok"))
        assert _db_write_lock_lease.get() is None

    asyncio.run(scenario())


def test_lease_resets_after_exception():
    """A factory that raises must still leave the caller's lease cleared."""

    class Boom(Exception):
        pass

    async def raiser():
        raise Boom()

    async def scenario():
        assert _db_write_lock_lease.get() is None
        with pytest.raises(Boom):
            await run_under_db_write_lock(lambda: raiser())
        assert _db_write_lock_lease.get() is None

    asyncio.run(scenario())


def test_lease_active_inside_factory():
    """The lease must read ``active == True`` while the factory is running."""

    seen: list[bool | None] = []

    async def factory():
        lease = _db_write_lock_lease.get()
        seen.append(None if lease is None else lease.active)

    async def scenario():
        await run_under_db_write_lock(lambda: factory())

    asyncio.run(scenario())
    assert seen == [True]


def test_lease_invalidated_after_release():
    """``finally`` flips ``lease.active`` to ``False`` after the outer
    releases. Under v5+ the membership test in ``_lease_admits_current``
    already excludes inherited sub-tasks from the short-circuit (their
    Task is not in ``drive_tasks``), so the invalidation is
    defence-in-depth: a future code path that ever inspected the lease
    without going through the membership test would still see it
    invalidated after release. See also
    ``test_lease_invalidation_is_defense_in_depth`` for the same
    invariant observed from inside an inherited sub-task.
    """

    captured_lease: list[db_writer._LockLease] = []

    async def factory():
        lease = _db_write_lock_lease.get()
        assert lease is not None
        captured_lease.append(lease)
        assert lease.active is True

    async def scenario():
        await run_under_db_write_lock(lambda: factory())
        # After the outer returns, the captured lease must be invalidated
        # so any sub-task that inherited it falls through to acquire.
        assert captured_lease[0].active is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Independent serialisation — concurrent Tasks queue FIFO on the lock
# ---------------------------------------------------------------------------


def test_independent_tasks_serialise_fifo():
    """Two independent Tasks (each acquires fresh — neither inherits an
    active lease) must run serially under the lock, in submission order.
    """

    order: list[str] = []

    async def scenario():
        started_a = asyncio.Event()
        released_a = asyncio.Event()
        b_reached_acquire = asyncio.Event()

        async def slow_a():
            order.append("a:enter")
            started_a.set()
            await released_a.wait()
            order.append("a:exit")

        async def fast_b():
            order.append("b:enter")
            order.append("b:exit")

        async def call_b():
            # Signal that we are about to enter the runner; set does
            # not yield, so B continues synchronously into
            # ``run_under_db_write_lock`` and parks on its
            # ``async with lock:`` ``await``.
            b_reached_acquire.set()
            await run_under_db_write_lock(lambda: fast_b())

        # Spawn A as a top-level task — it does NOT inherit any lease
        # (none is installed), so it acquires the lock.
        ta = asyncio.create_task(run_under_db_write_lock(lambda: slow_a()))
        await started_a.wait()  # A holds the lock now.

        # Spawn B; it will queue FIFO behind A.
        tb = asyncio.create_task(call_b())

        # Deterministic handshake: by the time we wake, B has set the
        # Event AND yielded on the acquire (blocking on A).
        await b_reached_acquire.wait()
        assert order == ["a:enter"], f"B started before A released: {order}"

        # Release A; B should then run.
        released_a.set()
        await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)

    asyncio.run(scenario())
    assert order == ["a:enter", "a:exit", "b:enter", "b:exit"]


# ---------------------------------------------------------------------------
# Sub-task isolation — spawn_isolated_db_write_task opts out
# ---------------------------------------------------------------------------


def test_spawn_isolated_lets_subtask_acquire_independently():
    """A sub-task spawned with ``spawn_isolated_db_write_task()`` gets
    a fresh isolated context with the lease forced back to ``None``.
    The pin of this test:

    - The sub observes ``_db_write_lock_lease.get() is None`` (the
      helper's defence-in-depth: even if a future change broke the
      membership restriction, the helper would still hide the
      inherited lease).
    - The sub serialises FIFO behind the outer (acquires only after
      the outer releases).

    Under v5+ a NAIVE ``asyncio.create_task`` sub-task ALSO queues
    FIFO (its ``current_task()`` is not in ``drive_tasks``), so the
    serialisation behaviour shared with
    ``test_naive_subtask_queues_fifo_behind_outer`` is no longer the
    helper's distinctive guarantee — the no-inherited-lease snapshot
    is.

    Synchronised via the Event-handshake trick. The lease-isolation
    assertion is emitted by the sub AFTER the handshake set, so a
    regression that left the lease inherited would surface as a test
    failure rather than an indefinite outer hang.
    """

    order: list[str] = []
    isolation_results: list[bool] = []  # True iff sub saw no lease.

    async def scenario():
        reached_acquire = asyncio.Event()

        async def sub_writer():
            # Capture isolation-result and signal BEFORE asserting,
            # so a regression where the helper failed to clear the
            # lease still releases the outer instead of hanging the
            # test indefinitely (an AssertionError in the sub would
            # otherwise leave reached_acquire un-set and the outer
            # forever in ``await reached_acquire.wait()``).
            isolation_results.append(_db_write_lock_lease.get() is None)
            # Set the handshake. Set does not yield; we continue
            # synchronously into ``run_under_db_write_lock`` and park
            # on its ``async with lock:`` ``await``.
            reached_acquire.set()
            await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))

        async def outer_factory():
            order.append("outer:enter")
            lease = _db_write_lock_lease.get()
            assert lease is not None and lease.active is True
            sub = spawn_isolated_db_write_task(sub_writer())
            await reached_acquire.wait()
            # The sub has reached the runner's acquire and is blocked.
            # Under v5+ the FIFO-behind-outer behaviour is what the
            # membership check gives us; the helper's distinctive role
            # is the isolation snapshot captured above.
            assert "sub:acquired" not in order, (
                f"sub ran before outer released: {order}"
            )
            order.append("outer:exit")
            # Returning the sub handle so the scenario can await it.
            return sub

        sub = await run_under_db_write_lock(lambda: outer_factory())
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    # Helper's distinctive pin: isolated sub sees no inherited lease.
    assert isolation_results == [True], (
        "spawn_isolated_db_write_task did not clear the inherited "
        f"lease in the sub-task's context: {isolation_results}"
    )
    assert order == ["outer:enter", "outer:exit", "sub:acquired"]


def test_naive_subtask_queues_fifo_behind_outer():
    """A sub-task spawned WITHOUT isolation inherits the lease but its
    Task is NOT in ``lease.drive_tasks`` — its reentrance check fails
    and it falls through to a real acquire. The acquire blocks until
    the outer releases, so the sub runs AFTER the outer factory has
    returned, FIFO behind us. This is the strict serialisation the
    drive_tasks restriction gives us; the previous "parallel-during-
    hold" footgun is now gone.

    Concretely: the sub records ``sub:acquired`` AFTER the outer's
    ``outer:exit``, demonstrating it did not run inside the outer's
    hold despite inheriting the lease.

    Synchronisation: the sub signals an Event right before entering
    :func:`run_under_db_write_lock`, and the outer waits on it before
    asserting. Setting the Event does not yield — sub continues
    synchronously into the runner, through the membership-test
    short-circuit (False), into ``async with lock:`` which is sub's
    first ``await``. That ``await`` is where sub blocks (outer holds).
    Asyncio then resumes the outer (already scheduled by the Event
    set). When the outer's check runs, sub is provably blocked on
    the acquire, so the "did not run" assertion is deterministic
    rather than dependent on a sleep timeout.
    """

    order: list[str] = []

    async def scenario():
        reached_acquire = asyncio.Event()

        async def sub_writer():
            # Signal that we are about to enter the runner. Set does
            # not yield; we continue synchronously into
            # ``run_under_db_write_lock`` and hit ``async with lock:``
            # there, which is our first real ``await``. So by the time
            # outer wakes up from ``await reached_acquire.wait()``
            # below, sub is guaranteed to be blocked on the acquire.
            reached_acquire.set()
            await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))

        async def outer_factory():
            order.append("outer:enter")
            # Spawn sub WITHOUT isolation. It inherits the lease,
            # attempts acquire, blocks because we still hold the lock.
            sub = asyncio.create_task(sub_writer())
            # Deterministic handshake: when reached_acquire fires, sub
            # is past its ``set()`` call and parked on the acquire's
            # ``await``.
            await reached_acquire.wait()
            # Sub must NOT have run its body — it is blocked on the
            # acquire. If reentrance leaked, "sub:acquired" would
            # already be in order.
            assert "sub:acquired" not in order, (
                f"naive sub ran before outer released: {order}"
            )
            order.append("outer:exit")
            return sub

        sub = await run_under_db_write_lock(lambda: outer_factory())
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    assert order == ["outer:enter", "outer:exit", "sub:acquired"]


# NOTE: there is no test for the "factory awaits a naive sub-task
# from inside a locked block" pattern. With the drive_tasks restriction
# the sub blocks on its own acquire (we hold the lock), and the factory
# blocks on awaiting the sub. The result is a TRUE deadlock that
# ``asyncio.wait_for`` cannot break — the shield-loop inside
# :func:`_drive_inner_under_held_lock` deliberately waits for the
# inner Task to finish before yielding to a cancellation, so the
# top-level cancel never reaches the sub. A test for that pattern
# would hang the test suite indefinitely. The docstring on
# :func:`run_under_db_write_lock` warns about this; the safer
# alternative is to await the sub coroutine directly (without
# ``create_task``), which becomes a transparent reentrant call.


def test_lease_invalidation_is_defense_in_depth():
    """Under the v4 membership design, an inherited sub-task never
    short-circuits the acquire even if the outer's lease still read
    ``active = True`` — its ``current_task()`` is not in
    ``drive_tasks``, and that membership test is what actually gates
    the short-circuit. The ``active = False`` flip and the
    ``drive_tasks.clear()`` the runner performs in ``finally`` are
    therefore **defence-in-depth**: a future code path that ever
    inspected the lease without going through the membership test
    would still see it invalidated after release.

    This test pins both defence-in-depth invariants. A sub-task that
    inherits the lease and is gated to resume only AFTER the outer
    released observes ``active is False`` AND empty ``drive_tasks``.
    If either invariant regressed, this test would surface the change.
    """

    order: list[str] = []
    snapshots: list[tuple[bool, int]] = []

    async def scenario():
        release_event = asyncio.Event()

        async def sub_writer():
            # Gate on the scenario: it sets the event only after
            # ``run_under_db_write_lock`` has returned, which means
            # the runner's ``finally`` has run (lease invalidated,
            # drive_tasks cleared, lock released).
            await release_event.wait()
            lease = _db_write_lock_lease.get()
            assert lease is not None, "lease somehow not inherited"
            snapshots.append((lease.active, len(lease.drive_tasks)))
            order.append("sub:before-acquire")
            # Under v4 the membership check would already make this
            # acquire normally (current_task() not in drive_tasks).
            # The defence-in-depth invariants captured in
            # ``snapshots`` are what this test asserts beyond the
            # membership behaviour itself.
            await run_under_db_write_lock(lambda: _append_then("sub:acquired", order))
            order.append("sub:after-release")

        async def outer_factory():
            order.append("outer:enter")
            sub = asyncio.create_task(sub_writer())
            order.append("outer:exit")
            return sub

        sub = await run_under_db_write_lock(lambda: outer_factory())
        # The outer has returned: ``finally`` ran (lease invalidated,
        # drive_tasks cleared, lock released). Let the sub proceed.
        release_event.set()
        await asyncio.wait_for(sub, timeout=2.0)

    asyncio.run(scenario())
    # Defence-in-depth invariants pinned by this test:
    assert snapshots == [(False, 0)], (
        f"after outer release, inherited lease should be active=False "
        f"with empty drive_tasks; got {snapshots}"
    )
    assert order == [
        "outer:enter",
        "outer:exit",
        "sub:before-acquire",
        "sub:acquired",
        "sub:after-release",
    ]


def test_cancelled_write_keeps_lease_until_thread_finishes():
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def slow_write():
        order.append("first:enter")
        started.set()
        assert release.wait(5)
        order.append("first:exit")

    async def scenario():
        db_writer.start_compute_executor()
        first = asyncio.create_task(run_under_db_write_lock(
            lambda: db_writer.run_compute_sync(slow_write)
        ))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            first.cancel()
            await asyncio.sleep(0)
            second = asyncio.create_task(run_under_db_write_lock(
                lambda: _append_then("second:enter", order)
            ))
            await asyncio.sleep(0)
            assert order == ["first:enter"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, 2)
        await db_writer.stop_compute_executor()

    asyncio.run(scenario())
    assert order == ["first:enter", "first:exit", "second:enter"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _identity(value):
    return value


async def _append_then(label, sink):
    sink.append(label)
