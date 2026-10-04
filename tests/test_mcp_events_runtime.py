"""Monitor loading and command ownership with real rows and worker threads."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import queue
import threading
from types import MappingProxyType, SimpleNamespace

from django.db import close_old_connections
import pytest

from tests.mcp_events_helpers import FakeClock, FakeRegistry
from twicc.cli._drop_request import transport
from twicc.cli._wait_reply import _agent_activity
from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient, Project, Session
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.runtime import CursorWrite, EventsRuntime, TurnWrite

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: SimpleNamespace(pid=1234))
    clock = FakeClock()
    registry = FakeRegistry()
    registry.install(monkeypatch)
    client = McpOAuthClient.objects.create(id="client")
    connection = McpConnection.objects.create(id="connection", client=client, resource="https://mcp.example/mcp")
    project = Project.objects.create(id="project", directory=str(tmp_path))
    session = Session.objects.create(id="session", project=project, provider="claude_code", last_line=30,
                                     last_offset=100, history_epoch=2)
    row = McpEventSubscription.objects.create(
        id="subscription", connection=connection, name="session.concluded", arguments={"session_id": "session"},
        session_id="session", callback_url="https://callback.example/events", secret="secret",
        cursor_line=10, cursor_at=100.0, initial_last_line=20, turn_open=True, turn_started_at=90.123456789,
        turn_opened_by="initial", turn_start_line=10, numbering=2, data_dir=str(tmp_path),
        refresh_before=clock.utcnow() + timedelta(hours=1),
    )
    runtime = EventsRuntime(clock=clock.clock, data_dir=str(tmp_path))
    return SimpleNamespace(clock=clock, row=row, runtime=runtime, registry=registry, session=session)


def in_worker(fn):
    def run():
        try:
            return fn()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(run).result(timeout=3)


def clone(row, identity, **changes):
    values = {field.attname: getattr(row, field.attname) for field in row._meta.fields
              if field.name not in ("id", "created_at", "updated_at")}
    return McpEventSubscription.objects.create(id=identity, **(values | changes))


def test_load_only_current_live_unrevoked_rows_and_restore_persisted_state(env):
    clone(env.row, "foreign", data_dir="/other")
    clone(env.row, "expired", refresh_before=env.clock.utcnow())
    revoked = McpConnection.objects.create(id="revoked", client=env.row.connection.client,
                                          resource="https://mcp.example/mcp", revoked_at=env.clock.utcnow())
    clone(env.row, "revoked", connection_id=revoked.id)
    env.registry.set_agent("session", at=999.0)

    def check():
        env.runtime._load_monitors()
        assert set(env.runtime.monitors) == {"subscription"}
        monitor = env.runtime.monitors["subscription"]
        assert monitor.generation == (env.row.id, env.row.created_at)
        assert (monitor.cursor_line, monitor.cursor_at, monitor.initial_last_line, monitor.numbering) == (10, 100, 20, 2)
        assert (monitor.turn_open, monitor.turn_started_at, monitor.turn_opened_by, monitor.turn_start_line) == (
            True, 90.123456789, "initial", 10,
        )
        assert monitor.first and not monitor.dormant and not monitor.pending_rebase
        assert monitor.session_snapshot.history_epoch == 2
        assert monitor.wait.want_text and monitor.wait.twicc_pid == 1234
        assert monitor.wait.started == 100 and monitor.wait.since_line_num == 10
    in_worker(check)


def test_refresh_preserves_live_state_and_dormant_wake_creates_fresh_wait(env):
    snapshot = SubscriptionSnapshot.from_row(env.row)

    def check():
        runtime = env.runtime
        runtime.add(snapshot)
        runtime._drain_commands()
        monitor = runtime.monitors[snapshot.id]
        wait = monitor.wait
        monitor.cursor_line, monitor.cursor_at = 35, 150.0
        monitor.turn_started_at, monitor.turn_opened_by = 140.0, "transition"
        monitor.turn_start_line, monitor.first = 31, False
        monitor.reported_request_ids.add("request")
        refreshed = snapshot._replace(secret="new", previous_secret="secret", previous_secret_until=env.clock.utcnow(),
                                      arguments=MappingProxyType({"session_id": "session", "since_line_num": 999}))
        runtime.add(refreshed)
        runtime._drain_commands()
        assert runtime.monitors[snapshot.id] is monitor and monitor.wait is wait
        assert monitor.secret == "new" and monitor.previous_secret == "secret"
        assert monitor.arguments["since_line_num"] == 999
        assert (monitor.cursor_line, monitor.cursor_at, monitor.turn_start_line) == (35, 150, 31)
        assert (monitor.turn_started_at, monitor.turn_opened_by, monitor.first) == (140, "transition", False)
        assert monitor.reported_request_ids == {"request"}
        env.clock.advance(3600)
        runtime._tick()
        assert monitor.dormant
        wait.confirming, wait.stopped_since = True, 1
        runtime.update(refreshed._replace(refresh_before=env.clock.utcnow() + timedelta(hours=1)))
        runtime._drain_commands()
        assert not monitor.dormant and monitor.wait is not wait
        assert monitor.wait.since_line_num == 35 and monitor.wait.stopped_since is None
        assert not monitor.wait.confirming and monitor.reported_request_ids == {"request"}
    in_worker(check)


def test_generation_commands_replace_only_add_and_ignore_stale_update_remove(env):
    old = SubscriptionSnapshot.from_row(env.row)
    new = old._replace(created_at=old.created_at + timedelta(seconds=1), cursor_line=30)

    def check():
        runtime = env.runtime
        runtime.update(old)
        runtime._drain_commands()
        assert runtime.monitors == {}
        runtime.add(old)
        runtime._drain_commands()
        old_monitor = runtime.monitors[old.id]
        runtime.add(new)
        runtime.update(old._replace(secret="stale"))
        runtime.remove(*old.generation)
        runtime._drain_commands()
        monitor = runtime.monitors[new.id]
        assert monitor is not old_monitor and monitor.generation == new.generation
        assert monitor.cursor_line == 30 and not monitor.first and monitor.secret == "secret"
        runtime.remove(*new.generation)
        runtime._drain_commands()
        assert runtime.monitors == {}
    in_worker(check)


@pytest.mark.parametrize("numbering,exists,pending", [(1, True, True), (None, True, True), (0, False, False)])
def test_creation_records_epoch_mismatch_and_missing_row_epoch_zero(env, numbering, exists, pending):
    if not exists:
        env.session.delete()
    snapshot = SubscriptionSnapshot.from_row(env.row)._replace(numbering=numbering)

    def check():
        env.runtime.add(snapshot)
        env.runtime._drain_commands()
        monitor = env.runtime.monitors[snapshot.id]
        assert monitor.pending_rebase is pending
        if not exists:
            assert monitor.session_snapshot is None
    in_worker(check)


def test_wake_keeps_pending_rebase_when_session_disappears(env):
    snapshot = SubscriptionSnapshot.from_row(env.row)._replace(numbering=1)

    def check():
        env.runtime.add(snapshot)
        env.runtime._drain_commands()
        monitor = env.runtime.monitors[snapshot.id]
        env.clock.advance(3600)
        env.runtime._tick()
        Session.objects.filter(pk=env.session.pk).delete()
        env.runtime.add(snapshot._replace(numbering=0, refresh_before=env.clock.utcnow() + timedelta(hours=1)))
        env.runtime._drain_commands()
        assert monitor.pending_rebase and not monitor.dormant and monitor.numbering == 1
    in_worker(check)


def test_real_worker_sets_backend_context_drains_commands_and_posts_fifo_writes(env):
    observations = queue.Queue()
    env.registry.set_agent("session", at=200.0)

    class ObservedRuntime(EventsRuntime):
        def _tick_monitor(self, monitor):
            observations.put((threading.get_ident(), transport.backend_loop.get(),
                              _agent_activity(monitor.session_id, None).working, monitor.secret))
            self.post_write(TurnWrite(*monitor.generation, False, None, "", 40))
            self.post_write(TurnWrite(*monitor.generation, True, 170.125, "transition", 45))
            self.post_write(CursorWrite(*monitor.generation, monitor.numbering, 45, 180.0))
            self.request_stop()

    async def run():
        runtime = ObservedRuntime(clock=env.clock.clock, data_dir=env.runtime.data_dir)
        commands, writes = runtime.commands, runtime.writes
        runtime.add(SubscriptionSnapshot.from_row(env.row)._replace(secret="fresh"))
        await runtime.start()
        try:
            thread_id, loop, working, secret = await asyncio.to_thread(observations.get, True, 2)
            assert thread_id != threading.get_ident() and loop is asyncio.get_running_loop()
            assert working and secret == "fresh"
        finally:
            await runtime.close()
        assert runtime.commands is commands and runtime.writes is writes
        assert not runtime.thread.is_alive() and runtime.writer_task.done()
    asyncio.run(run())
    env.row.refresh_from_db()
    assert (env.row.cursor_line, env.row.cursor_at) == (45, 180.0)
    assert (env.row.turn_open, env.row.turn_started_at, env.row.turn_start_line) == (True, 170.125, 45)
