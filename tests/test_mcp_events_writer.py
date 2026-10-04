"""State writes use real SQL, generation guards, and one ordered writer."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from asgiref.sync import sync_to_async
import pytest

from tests.mcp_events_helpers import FakeClock
from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient
from twicc.mcp.events.runtime import CursorWrite, EventsRuntime, RebaseWrite, TurnWrite, WriteBarrier

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    clock = FakeClock()
    client = McpOAuthClient.objects.create(id="client")
    connection = McpConnection.objects.create(id="connection", client=client, resource="https://mcp.example/mcp")
    row = McpEventSubscription.objects.create(
        id="subscription", connection=connection, name="session.concluded", arguments={"session_id": "session"},
        session_id="session", callback_url="https://callback.example/events", secret="secret",
        cursor_line=10, cursor_at=100.0, initial_last_line=20, turn_open=True, turn_started_at=90.0,
        turn_opened_by="initial", turn_start_line=10, numbering=1, data_dir=str(tmp_path),
        refresh_before=clock.utcnow() + timedelta(hours=1),
    )
    return clock, row, EventsRuntime(clock=clock.clock, data_dir=str(tmp_path))


async def apply(runtime, *items):
    writer = asyncio.create_task(runtime.run_writer())
    barrier = asyncio.get_running_loop().create_future()
    for item in items:
        runtime.writes.put_nowait(item)
    runtime.writes.put_nowait(WriteBarrier(barrier))
    try:
        await asyncio.wait_for(barrier, 2)
        await runtime.writes.join()
    finally:
        writer.cancel()
        with suppress(asyncio.CancelledError):
            await writer


def test_cursor_fields_advance_independently_when_deliveries_finish_out_of_order(state):
    _, row, runtime = state
    asyncio.run(apply(runtime,
        CursorWrite(row.id, row.created_at, 1, 40, 120.0),
        CursorWrite(row.id, row.created_at, 1, 60, None),  # Silent movement has no conclusion timestamp.
        CursorWrite(row.id, row.created_at, 1, 30, 130.0),
        CursorWrite(row.id, row.created_at, 1, 20, 110.0),
    ))
    row.refresh_from_db()
    assert (row.cursor_line, row.cursor_at) == (60, 130.0)


@pytest.mark.parametrize("old_numbering", [1, None])
def test_rebase_cas_can_lower_cursor_and_old_epoch_only_advances_time(state, old_numbering):
    _, row, runtime = state
    McpEventSubscription.objects.filter(pk=row.pk).update(numbering=old_numbering)
    asyncio.run(apply(runtime,
        RebaseWrite(row.id, row.created_at, old_numbering, 2, 3, 3, False, None, "", 3),
        CursorWrite(row.id, row.created_at, old_numbering, 999, 180.0),
        RebaseWrite(row.id, row.created_at, old_numbering, 3, 50, 50, True, 160.0, "initial", 50),
        CursorWrite(row.id, row.created_at, 2, 4, 170.0),
    ))
    row.refresh_from_db()
    assert (row.cursor_line, row.initial_last_line, row.turn_start_line, row.numbering) == (4, 3, 3, 2)
    assert row.cursor_at == 180.0
    assert not row.turn_open and row.turn_started_at is None and row.turn_opened_by == ""


def test_old_generation_cannot_mutate_replacement_row(state):
    _, row, runtime = state
    old_generation = row.created_at
    replacement_generation = old_generation + timedelta(seconds=1)
    McpEventSubscription.objects.filter(pk=row.pk).update(created_at=replacement_generation)
    asyncio.run(apply(runtime,
        CursorWrite(row.id, old_generation, 1, 100, 1000.0),
        TurnWrite(row.id, old_generation, False, None, "", 100),
        RebaseWrite(row.id, old_generation, 1, 2, 2, 2, False, None, "", 2),
    ))
    row.refresh_from_db()
    assert (row.cursor_line, row.cursor_at, row.numbering, row.initial_last_line) == (10, 100.0, 1, 20)
    assert row.turn_open and row.turn_started_at == 90.0 and row.turn_start_line == 10


def test_writer_survives_sql_error_and_commits_turns_in_producer_order(state, caplog):
    _, row, runtime = state
    asyncio.run(apply(runtime,
        TurnWrite(row.id, row.created_at, False, None, "", 12),
        RebaseWrite(row.id, row.created_at, 1, 2, -1, 3, False, None, "", 3),
        TurnWrite(row.id, row.created_at, True, 150.125, "transition", 15),
    ))
    row.refresh_from_db()
    assert (row.turn_open, row.turn_started_at, row.turn_opened_by, row.turn_start_line) == (
        True, 150.125, "transition", 15,
    )
    assert row.numbering == 1
    assert "subscription" in caplog.text and "IntegrityError" in caplog.text


def test_restarted_writer_consumes_same_queue_and_cancelled_barrier_is_safe(state):
    _, row, runtime = state

    async def run():
        queue = runtime.writes
        cancelled = asyncio.get_running_loop().create_future()
        cancelled.cancel()
        await apply(runtime, WriteBarrier(cancelled), CursorWrite(row.id, row.created_at, 1, 20, 120.0))
        await apply(runtime, CursorWrite(row.id, row.created_at, 1, 30, 130.0))
        assert runtime.writes is queue
        saved = await sync_to_async(McpEventSubscription.objects.get)(pk=row.pk)
        assert (saved.cursor_line, saved.cursor_at) == (30, 130.0)

    asyncio.run(run())
