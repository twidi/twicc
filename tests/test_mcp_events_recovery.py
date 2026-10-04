"""Real detector failures, consumer recovery, and shutdown admission boundaries."""

import asyncio
from itertools import pairwise
import threading
from types import SimpleNamespace

from asgiref.sync import sync_to_async
from django.db.models.query import QuerySet
import orjson
import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_mcp_events_turns import agent, opened, pending, prompt
from twicc.agent.states import AgentState
from twicc.cli import _wait_reply
from twicc.core.enums import ItemKind
from twicc.core.models import McpEventSubscription, Session, SessionItem
from twicc.mcp.events import runtime as runtime_module
from twicc.mcp.events.catalog import event_id
from twicc.mcp.events.delivery import DeliveryService
from twicc.mcp.events.methods import EventMethods, SubscribeParams, SubscriptionSnapshot
from twicc.mcp.events.runtime import AddCommand, CursorWrite, EventsRuntime, TurnWrite
from twicc.mcp.identity import ExternalCaller, external_caller
from twicc.mcp.oauth import config

pytestmark = pytest.mark.django_db(transaction=True)
env = test_mcp_events_turns.env


@pytest.fixture(autouse=True)
def writer_lock(monkeypatch):
    monkeypatch.setattr(runtime_module.logger, "disabled", False)
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())


def fail(*args, **kwargs):
    raise ValueError("injected failure")


def ready_end(env):
    """Advance the real detector through its flush and confirmation scans."""
    env.runtime._tick()
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
    env.runtime._tick()


@pytest.mark.parametrize("site", ["timestamp", "snapshot", "payload", "fit"])
@pytest.mark.parametrize("outcome", ["replied", "provider_error"])
def test_post_step_failure_replays_same_id_from_unchanged_cursor(env, monkeypatch, site, outcome):
    agent(env, at=999)
    if outcome == "replied":
        append_assistant(env.session, 3)
    else:
        SessionItem.objects.create(session=env.session, line_num=3, kind=ItemKind.API_ERROR,
                                   content='{"isApiErrorMessage":true,"message":{"content":[]}}')
        Session.objects.filter(pk=env.session.pk).update(last_line=3)
    original_wait = env.monitor.wait
    closed = []
    monkeypatch.setattr(runtime_module, "close_old_connections", lambda: closed.append(True))
    with monkeypatch.context() as patch:
        if site == "timestamp":
            first = QuerySet.first
            def broken(query):
                if query.model is SessionItem and query.query.values_select == ("timestamp",):
                    fail()
                return first(query)
            patch.setattr(QuerySet, "first", broken)
        else:
            patch.setattr(runtime_module, {"snapshot": "read_session_snapshot", "payload": "build_occurrence",
                                           "fit": "fit_body"}[site], fail)
        env.runtime._tick()
    assert original_wait.scanned_up_to == 3
    assert env.monitor.wait is not original_wait
    assert env.monitor.wait.scanned_up_to == env.monitor.cursor_line == 0
    assert closed == [True] and not env.emissions
    assert env.monitor.failure_count == env.monitor.position_failures == 1
    env.runtime._tick()  # Backoff has not elapsed.
    assert not env.emissions
    env.clock.advance(.25)
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, outcome, "0:3")
    assert env.monitor.failure_count == env.monitor.position_failures == 0


def test_guard_failure_preserves_open_turn_then_emits_end(env, monkeypatch):
    opened(env, by="transition")
    prompt(env, 2)
    agent(env, state=AgentState.USER_TURN)
    ready_end(env)
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, "first_non_command_prompt", fail)
        env.runtime._tick()
    assert env.monitor.failure_count == 1 and env.monitor.turn_open
    assert not env.writes and not env.emissions
    env.clock.advance(.25)
    ready_end(env)
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", "last_line:0:2")


@pytest.mark.parametrize("pending_rebase", [False, True])
def test_snapshot_and_rebase_failures_stay_inside_monitor_boundary(env, monkeypatch, pending_rebase):
    env.monitor.pending_rebase = pending_rebase
    env.clock.advance(5)
    calls = []
    patch_name = "_apply_rebase" if pending_rebase else "_read_monitor_session"
    monkeypatch.setattr(env.runtime, patch_name, fail)
    monkeypatch.setattr(runtime_module, "close_old_connections", lambda: calls.append(True))
    env.runtime._tick()
    assert env.monitor.failure_count == 1 and calls == [True]
    assert env.monitor.retry_at == env.clock.monotonic() + .25


def test_poison_batch_is_skipped_after_three_failures_and_later_answer_emits(env, monkeypatch):
    agent(env, at=999)
    append_assistant(env.session, 3, "poison")
    original = runtime_module.fit_body
    def fit(occurrence):
        if occurrence["data"]["reply"].get("text") == "poison":
            fail()
        return original(occurrence)
    monkeypatch.setattr(runtime_module, "fit_body", fit)
    for delay in (.25, .5, 1):
        env.runtime._tick()
        assert env.monitor.retry_at == env.clock.monotonic() + delay
        env.clock.advance(delay)
    kept = env.monitor.wait
    assert kept.scanned_up_to == 3 and env.monitor.cursor_line == 0
    assert env.monitor.position_failures == 3
    append_assistant(env.session, 5, "good")
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "replied", "0:5")
    assert env.monitor.failure_count == 0


def test_persistent_pending_request_is_reported_lost_then_next_request_emits(env, monkeypatch):
    agent(env, state=AgentState.USER_TURN, pending_requests=[pending("bad"), pending("good")])
    original = runtime_module.fit_body
    def fit(occurrence):
        if occurrence["eventId"] == event_id(env.row.id, "awaiting_user_input", "bad"):
            fail()
        return original(occurrence)
    monkeypatch.setattr(runtime_module, "fit_body", fit)
    for delay in (.25, .5, 1):
        env.runtime._tick()
        env.clock.advance(delay)
    assert list(env.monitor.reported_request_order) == ["bad"]
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "awaiting_user_input", "good")
    assert env.monitor.reported_request_ids == {"bad", "good"}


def test_persistent_step_failure_bounds_logs_backoff_and_does_not_sleep_other_monitors(env, monkeypatch, caplog):
    second = SubscriptionSnapshot.from_row(env.row)._replace(id="other")
    env.runtime._add_monitor(second)
    original = _wait_reply._SessionWait.step
    healthy = []
    def step(wait):
        if wait is env.runtime.monitors["other"].wait:
            healthy.append(True)
            return original(wait)
        fail()
    monkeypatch.setattr(_wait_reply._SessionWait, "step", step)
    monkeypatch.setattr(_wait_reply.time, "sleep", fail, raising=False)
    for count in range(1, 13):
        env.runtime._tick()
        delay = min(.25 * 2 ** (count - 1), 60)
        assert env.monitor.retry_at == env.clock.monotonic() + delay
        env.runtime._tick()
        env.clock.advance(delay)
    assert len(healthy) == 24
    records = [r for r in caplog.records if "subscription" in r.getMessage()]
    assert len([r for r in records if r.exc_info]) == 1
    assert len(records) == 5
    assert env.monitor.failure_count == 12 and env.monitor.position_failures == 12


def test_wait_constructor_failure_is_contained_until_recovery(env, monkeypatch):
    agent(env, at=999)
    append_assistant(env.session, 3)
    with monkeypatch.context() as patch:
        patch.setattr(env.runtime, "_new_wait", fail)
        env.runtime._tick()
        assert env.monitor.retry_wait and not env.emissions
        env.clock.advance(.25)
        env.runtime._tick()
        assert env.monitor.failure_count == 2
    env.clock.advance(.5)
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "replied", "0:3")


def test_unseeded_ended_failures_preserve_series_through_reconstruction(env, monkeypatch, caplog):
    opened(env)
    agent(env, state=AgentState.USER_TURN)
    ready_end(env)
    attempts, retained = [], None
    original = runtime_module.fit_body
    def fit(occurrence):
        attempts.append(env.clock.monotonic())
        fail()
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, "fit_body", fit)
        for count in range(1, 13):
            env.runtime._tick()
            assert len(attempts) == count
            assert env.monitor.failure_count == env.monitor.position_failures == count
            delay = min(.25 * 2 ** (count - 1), 60)
            assert env.monitor.retry_at == env.clock.monotonic() + delay
            if count >= 3:
                retained = retained or env.monitor.wait
                assert env.monitor.wait is retained and env.monitor.turn_open
            env.runtime._tick()  # No retry before the deadline.
            assert len(attempts) == count
            env.clock.advance(delay)
            if count < 3:
                # The new wait must flush and confirm. These are recovery ticks.
                env.runtime._tick()
                assert env.monitor.failure_count == count
                env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
                env.runtime._tick()
                assert env.monitor.failure_count == count
    records = [record for record in caplog.records if "subscription" in record.getMessage()]
    assert sum(bool(record.exc_info) for record in records) == 1
    assert len(records) == 5
    assert all(later - earlier >= 60 for earlier, later in pairwise(attempts[8:]))
    monkeypatch.setattr(runtime_module, "fit_body", original)
    env.runtime._tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", "last_line:0:0")
    assert not env.monitor.turn_open and env.monitor.failure_count == 0


@pytest.mark.parametrize("progress", ["new-turn", "cursor", "scan", "rebase"])
def test_ended_recovery_resets_on_meaningful_progress(env, monkeypatch, progress):
    opened(env)
    agent(env, state=AgentState.USER_TURN)
    ready_end(env)
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, "fit_body", fail)
        env.runtime._tick()
    assert env.monitor.failure_count == 1
    env.clock.advance(.25)
    if progress == "new-turn":
        agent(env)
    elif progress in ("cursor", "scan"):
        append_assistant(env.session, 3, final=progress == "cursor")
    else:
        Session.objects.filter(pk=env.session.pk).update(history_epoch=1)
        env.monitor.pending_rebase = True
    env.runtime._tick()
    assert env.monitor.failure_count == env.monitor.position_failures == 0


def production_runtime(env):
    return EventsRuntime(clock=env.clock.clock, data_dir=env.runtime.data_dir)


def test_supervisor_barrier_recovers_dead_writer_and_retains_committed_commands(env, monkeypatch):
    runtime = production_runtime(env)
    generation = env.row.id, env.row.created_at
    async def run():
        runtime.loop = asyncio.get_running_loop()
        commands, writes = runtime.commands, runtime.writes
        runtime.writer_task = asyncio.create_task(asyncio.sleep(0))
        await runtime.writer_task
        # These posts have not landed yet when supervision starts its barrier.
        runtime.post_write(TurnWrite(*generation, True, 800, "history", 4))
        runtime.post_write(CursorWrite(*generation, 0, 4, 1001))
        loaded, released = threading.Event(), threading.Event()
        original_load = runtime._load_monitors
        def load():
            original_load()
            loaded.set()
            assert released.wait(2)
        monkeypatch.setattr(runtime, "_load_monitors", load)
        await runtime._supervise_once()
        try:
            assert await asyncio.to_thread(loaded.wait, 2)
            # Commit after the reload snapshot, while the replacement thread waits.
            await sync_to_async(McpEventSubscription.objects.filter(pk=env.row.pk).update)(
                secret="committed-during-rebuild",
            )
            runtime.update(SubscriptionSnapshot.from_row(env.row)._replace(secret="committed-during-rebuild"))
        finally:
            released.set()
            await runtime.close()
        assert runtime.commands is commands and runtime.writes is writes
        monitor = runtime.monitors[env.row.id]
        assert monitor.cursor_line == 4 and monitor.turn_started_at == 800
        assert monitor.secret == "committed-during-rebuild"
    asyncio.run(run())


def test_new_subscription_committed_after_supervisor_load_survives_rebuild(env, monkeypatch):
    runtime = production_runtime(env)
    Session.objects.filter(pk=env.session.pk).update(
        created_at=env.clock.utcnow(), user_message_count=1, last_line=7,
    )
    loaded, release = threading.Event(), threading.Event()
    original_load, original_add, original_tick = runtime._load_monitors, runtime.add, runtime._tick
    loaded_ids, added = [], []

    def load():
        original_load()
        loaded_ids.extend(runtime.monitors)
        loaded.set()
        assert release.wait(2)

    monkeypatch.setattr(runtime, "_load_monitors", load)

    async def run():
        runtime.loop = asyncio.get_running_loop()
        queued, ticked = asyncio.Event(), asyncio.Event()
        commands = runtime.commands

        def add(snapshot):
            original_add(snapshot)
            added.append(snapshot)
            queued.set()

        def tick():
            original_tick()
            runtime.request_stop()
            runtime.loop.call_soon_threadsafe(ticked.set)

        async def verified(*args):
            pass

        monkeypatch.setattr(runtime, "add", add)
        monkeypatch.setattr(runtime, "_tick", tick)
        # Exercise supervisor replacement of a terminated worker.
        runtime.thread = threading.Thread(target=lambda: None)
        runtime.thread.start()
        await asyncio.to_thread(runtime.thread.join)
        methods = EventMethods(runtime, SimpleNamespace(verify=verified),
                               clock=env.clock.clock, data_dir=runtime.data_dir)
        token = external_caller.set(ExternalCaller(env.row.connection_id, "Acceptance owner"))
        try:
            await runtime._supervise_once()
            assert await asyncio.to_thread(loaded.wait, 2)
            assert loaded_ids == [env.row.id]
            assert await McpEventSubscription.objects.acount() == 1
            result = await methods.subscribe(None, SubscribeParams.model_validate({
                "name": "session.concluded",
                "arguments": {"session_id": env.session.id, "since_line_num": 3},
                "delivery": {"mode": "webhook", "url": "https://callback.example/new",
                             "secret": "whsec_" + "c3Nz" * 8},
            }))
            await asyncio.wait_for(queued.wait(), 2)
            saved = await McpEventSubscription.objects.aget(pk=result["id"])
            assert saved.id not in loaded_ids and saved.id not in runtime.monitors
            assert added[0].generation == (saved.id, saved.created_at)
            # The real on-commit callback queues an AddCommand, without consuming it here.
            with commands.mutex:
                pending_commands = list(commands.queue)
            assert pending_commands == [AddCommand(added[0])]
            release.set()
            await asyncio.wait_for(ticked.wait(), 2)
        finally:
            release.set()
            await runtime.close()
            external_caller.reset(token)
        assert runtime.commands is commands and runtime.commands.empty()
        assert set(runtime.monitors) == {env.row.id, saved.id}
        monitor = runtime.monitors[saved.id]
        assert monitor.generation == (saved.id, saved.created_at)
        assert (monitor.cursor_line, monitor.initial_last_line, monitor.numbering) == (3, 7, 0)
        assert monitor.first and not monitor.dormant and not monitor.pending_rebase
        assert monitor.session_snapshot.ready and monitor.wait.since_line_num == 3
        persisted = await McpEventSubscription.objects.aget(pk=saved.id)
        assert SubscriptionSnapshot.from_row(persisted) == added[0]
        assert await McpEventSubscription.objects.acount() == 2
    asyncio.run(run())


def test_writer_dies_during_same_barrier_and_recovers(env, monkeypatch):
    runtime = production_runtime(env)
    real_writer = runtime.run_writer
    calls = 0
    async def writer():
        nonlocal calls
        calls += 1
        if calls == 1:
            # Die after taking a normal write; queued barrier remains intact.
            await runtime.writes.get()
            runtime.writes.task_done()
            raise RuntimeError("writer died")
        await real_writer()
    monkeypatch.setattr(runtime, "run_writer", writer)
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime.writes.put_nowait(TurnWrite(env.row.id, env.row.created_at, False, None, "", 0))
        await asyncio.wait_for(runtime._write_barrier(), 2)
        assert calls == 2
        assert runtime.writes.empty()
        await runtime.close()
    asyncio.run(run())


def test_failed_writer_item_still_resolves_supervisor_barrier(env):
    runtime = production_runtime(env)
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime.writes.put_nowait(object())
        await asyncio.wait_for(runtime._write_barrier(), 2)
        assert not runtime.writer_task.done()
        await runtime.close()
    asyncio.run(run())


def test_real_worker_initial_load_failure_is_supervised_and_closes_connections(env, monkeypatch):
    runtime = production_runtime(env)
    original_load = runtime._load_monitors
    load_count, closed = [], []
    original_close = runtime_module.connections.close_all
    def close_all():
        closed.append(threading.get_ident())
        original_close()
    def load():
        load_count.append(True)
        if len(load_count) == 1:
            fail()
        original_load()
    loaded = threading.Event()
    def tick():
        loaded.set()
        runtime.request_stop()
    monkeypatch.setattr(runtime, "_load_monitors", load)
    monkeypatch.setattr(runtime, "_tick", tick)
    monkeypatch.setattr(runtime_module.connections, "close_all", close_all)
    async def run():
        await runtime.start()
        assert runtime.supervisor_task is not None
        await asyncio.to_thread(runtime.thread.join, 2)
        assert not runtime.thread.is_alive()
        await runtime._supervise_once()
        assert await asyncio.to_thread(loaded.wait, 2)
        await runtime.close()
        assert runtime.supervisor_task.done()
    asyncio.run(run())
    assert len(load_count) == 2 and len(closed) == 2


def test_supervisor_checks_writer_while_worker_is_alive_and_uses_five_seconds(env, monkeypatch):
    runtime = production_runtime(env)
    intervals = []
    real_sleep = asyncio.sleep
    async def sleep(seconds):
        intervals.append(seconds)
        await real_sleep(0)
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime.thread = SimpleNamespace(is_alive=lambda: True)
        def ensure():
            intervals.append("writer")
            runtime.request_stop()
        monkeypatch.setattr(runtime, "_ensure_writer", ensure)
        monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
        await runtime._supervise()
    asyncio.run(run())
    assert intervals == [5, "writer"]


@pytest.mark.parametrize("boundary", ["during-fit", "queued-callback", "running-delivery"])
def test_actual_ended_shutdown_boundary_and_restart(env, monkeypatch, boundary):
    McpEventSubscription.objects.filter(pk=env.row.pk).update(
        turn_open=True, turn_started_at=999, turn_opened_by="initial",
    )
    runtime = production_runtime(env)
    agent(env, state=AgentState.USER_TURN)
    monkeypatch.setattr(config, "base_url", lambda: "https://mcp.example")
    entered = threading.Event()
    released = threading.Event()
    original_fit = runtime_module.fit_body
    def fit(occurrence):
        body = original_fit(occurrence)
        if boundary == "during-fit":
            entered.set()
            assert released.wait(2)
        return body
    monkeypatch.setattr(runtime_module, "fit_body", fit)

    async def run():
        runtime.loop = asyncio.get_running_loop()
        await asyncio.to_thread(runtime._load_monitors)
        monitor = runtime.monitors[env.row.id]
        async def tick():
            await asyncio.to_thread(runtime._tick)
        await tick()
        env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
        await tick()
        delivery_entered = asyncio.Event()
        async def send(*args, **kwargs):
            delivery_entered.set()
            await asyncio.Future()
        # A valid key is required before the real delivery reaches send.
        await sync_to_async(McpEventSubscription.objects.filter(pk=env.row.pk).update)(
            secret="whsec_c2VjcmV0",
        )
        runtime.delivery = DeliveryService(runtime, send=send)
        if boundary == "during-fit":
            task = asyncio.create_task(tick())
            assert await asyncio.to_thread(entered.wait, 2)
            runtime.request_stop()
            released.set()
            await task
            assert monitor.turn_open and monitor.cursor_at == 1000
        elif boundary == "queued-callback":
            posted = threading.Event()
            original_post = runtime.post_emission
            def post(emission):
                result = original_post(emission)
                # Stop synchronously before the event loop admits the callback.
                runtime.request_stop()
                posted.set()
                return result
            monkeypatch.setattr(runtime, "post_emission", post)
            await tick()
            assert posted.is_set()
        else:
            await tick()
            await asyncio.wait_for(delivery_entered.wait(), 2)
            assert runtime.delivery_tasks
        await runtime.close()
        assert not runtime.delivery_tasks
        assert runtime.writes.empty()
    asyncio.run(run())
    env.row.refresh_from_db()
    assert env.row.turn_open is (boundary != "running-delivery")
    assert env.row.cursor_at == 1000  # No cancelled or dropped delivery cursor.

    monkeypatch.setattr(runtime_module, "fit_body", original_fit)
    emissions = []
    restarted = EventsRuntime(clock=env.clock.clock, data_dir=runtime.data_dir, post_emission=emissions.append)
    monkeypatch.setattr(restarted, "post_write", lambda item: None)
    restarted._load_monitors()
    restarted._tick()
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
    restarted._tick()
    restarted._tick()
    assert bool(emissions) is (boundary != "running-delivery")
    if emissions:
        assert orjson.loads(emissions[0].body)["data"]["reply"]["outcome"] == "ended"


def test_shutdown_join_is_nonblocking_and_dead_writer_drains(env):
    runtime = production_runtime(env)
    released = threading.Event()
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime.thread = threading.Thread(target=lambda: released.wait(2))
        runtime.thread.start()
        runtime.writer_task = asyncio.create_task(asyncio.sleep(0))
        await runtime.writer_task
        runtime.post_write(TurnWrite(env.row.id, env.row.created_at, True, 950, "initial", 0))
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        # The loop continues while the worker is still alive.
        assert runtime.thread.is_alive() and not closing.done()
        released.set()
        await asyncio.wait_for(closing, 2)
        assert runtime.writer_task.done() and not runtime.thread.is_alive()
    asyncio.run(run())
    env.row.refresh_from_db()
    assert env.row.turn_open and env.row.turn_started_at == 950


def test_barrier_finishes_without_waiting_for_later_writes(env, monkeypatch):
    runtime = production_runtime(env)
    entered, released = threading.Event(), threading.Event()
    original_apply = runtime._apply_write
    def apply(item):
        if item.turn_start_line == 9:
            entered.set()
            assert released.wait(2)
        return original_apply(item)
    monkeypatch.setattr(runtime, "_apply_write", apply)
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime.post_write(TurnWrite(env.row.id, env.row.created_at, True, 999, "initial", 4))
        barrier = asyncio.create_task(runtime._write_barrier())
        await asyncio.sleep(0)  # Barrier insertion is now scheduled behind the prior post.
        runtime.loop.call_soon(runtime.writes.put_nowait,
                               TurnWrite(env.row.id, env.row.created_at, False, 999, "initial", 9))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(barrier, .5)
            assert runtime.writes._unfinished_tasks == 1
        finally:
            released.set()
            await runtime.close()
    asyncio.run(run())
    env.row.refresh_from_db()
    assert env.row.turn_start_line == 9


def test_synchronous_thread_start_failure_leaves_supervisor_available(env, monkeypatch):
    runtime = production_runtime(env)
    original = runtime._start_worker
    calls = []
    def start():
        calls.append(True)
        if len(calls) == 1:
            fail()
        original()
    ticked = threading.Event()
    def tick():
        ticked.set()
        runtime.request_stop()
    monkeypatch.setattr(runtime, "_start_worker", start)
    monkeypatch.setattr(runtime, "_tick", tick)
    async def run():
        await runtime.start()
        assert not runtime.supervisor_task.done()
        await runtime._supervise_once()
        assert await asyncio.to_thread(ticked.wait, 2)
        await runtime.close()
    asyncio.run(run())
    assert len(calls) == 2


def test_shutdown_uses_five_second_join_and_two_second_drain_deadline(env, monkeypatch, caplog):
    runtime = production_runtime(env)
    joined, deadlines = [], []
    runtime.thread = SimpleNamespace(ident=1, join=lambda timeout: joined.append((threading.get_ident(), timeout)))
    real_wait_for = asyncio.wait_for
    async def deadline(awaitable, timeout):
        deadlines.append(timeout)
        return await real_wait_for(awaitable, 0)
    async def writer():
        await asyncio.Future()
    monkeypatch.setattr(runtime, "run_writer", writer)
    monkeypatch.setattr(runtime_module.asyncio, "wait_for", deadline)
    async def run():
        runtime.loop = asyncio.get_running_loop()
        runtime._ensure_writer()
        await runtime.close()
        assert runtime.writer_task.cancelled()
        assert joined == [(joined[0][0], 5)] and joined[0][0] != threading.get_ident()
    asyncio.run(run())
    assert deadlines == [2]
    assert "Timed out draining event state writes" in caplog.text


def test_posted_emission_has_no_later_database_or_payload_work(env, monkeypatch):
    agent(env, at=999)
    append_assistant(env.session, 3)
    def sink(emission):
        env.emissions.append(emission)
        monkeypatch.setattr(runtime_module, "read_session_snapshot", fail)
        monkeypatch.setattr(runtime_module, "build_occurrence", fail)
        monkeypatch.setattr(runtime_module, "fit_body", fail)
        monkeypatch.setattr(env.runtime, "_new_wait", fail)
        monkeypatch.setattr(QuerySet, "first", fail)
    env.runtime.emission_sink = sink
    env.runtime._tick()
    assert len(env.emissions) == 1 and env.monitor.cursor_line == 3
    assert env.monitor.failure_count == 0


def test_shutdown_waits_for_protected_write_settlement_after_drain_deadline(env, monkeypatch):
    from twicc.providers import db_writer

    runtime = production_runtime(env)
    entered, released = threading.Event(), threading.Event()
    original_apply = runtime._apply_write
    deadlines = []
    original_wait_for = asyncio.wait_for
    async def deadline(awaitable, timeout):
        deadlines.append(timeout)
        return await original_wait_for(awaitable, .01)
    def apply(item):
        entered.set()
        assert released.wait(2)
        return original_apply(item)
    monkeypatch.setattr(runtime, "_apply_write", apply)
    monkeypatch.setattr(runtime_module.asyncio, "wait_for", deadline)

    async def run():
        runtime.loop = asyncio.get_running_loop()
        cancelled = asyncio.Event()
        class ObservedTask(asyncio.Task):
            def cancel(self, msg=None):
                cancelled.set()
                return super().cancel(msg)
        runtime.writer_task = ObservedTask(runtime.run_writer(), loop=runtime.loop)
        runtime.writes.put_nowait(TurnWrite(env.row.id, env.row.created_at, True, 999, "initial", 4))
        runtime.writes.put_nowait(TurnWrite(env.row.id, env.row.created_at, False, 999, "initial", 9))
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(runtime.close())
        try:
            await original_wait_for(cancelled.wait(), 1)
            await asyncio.sleep(0)  # Let storage.write observe and shield cancellation.
            assert runtime.writer_task.cancelling() and not runtime.writer_task.done()
            assert not closing.done() and db_writer._db_write_lock.locked()
        finally:
            released.set()
            await original_wait_for(closing, 2)
        assert runtime.writer_task.cancelled()
        assert not db_writer._db_write_lock.locked()
    asyncio.run(run())
    env.row.refresh_from_db()
    assert env.row.turn_start_line == 4 and env.row.turn_open
    assert deadlines == [2]
