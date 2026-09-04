from __future__ import annotations

import asyncio
from pathlib import Path

from watchfiles import Change

from twicc.core.models import SessionType
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.migration_gate import (
    migrating,
    request_rebuild,
    take_rebuild_requests,
    gate_for,
    wait_for_migration_wake,
    wake_migration_scheduler,
)
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.sessions_watcher import ParsedSessionFile


def test_first_session_operation_finishes_before_migration_enters():
    async def scenario():
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        migration_entered = asyncio.Event()

        async def first_operation():
            async with gate_for("session-1"):
                first_entered.set()
                await release_first.wait()

        async def migration():
            await first_entered.wait()
            async with gate_for("session-1"):
                migration_entered.set()

        first_task = asyncio.create_task(first_operation())
        migration_task = asyncio.create_task(migration())
        await first_entered.wait()
        await asyncio.sleep(0)
        assert not migration_entered.is_set()
        release_first.set()
        await asyncio.gather(first_task, migration_task)
        assert migration_entered.is_set()

    asyncio.run(scenario())


def test_second_session_operation_waits_for_migration():
    async def scenario():
        migration_entered = asyncio.Event()
        release_migration = asyncio.Event()
        operation_entered = asyncio.Event()

        async def migration():
            async with gate_for("session-2"):
                migration_entered.set()
                await release_migration.wait()

        async def second_operation():
            await migration_entered.wait()
            async with gate_for("session-2"):
                operation_entered.set()

        migration_task = asyncio.create_task(migration())
        operation_task = asyncio.create_task(second_operation())
        await migration_entered.wait()
        await asyncio.sleep(0)
        assert not operation_entered.is_set()
        release_migration.set()
        await asyncio.gather(migration_task, operation_task)
        assert operation_entered.is_set()

    asyncio.run(scenario())


def test_unrelated_sessions_enter_concurrently():
    async def scenario():
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()

        async def hold(session_id, entered):
            async with gate_for(session_id):
                entered.set()
                await release.wait()

        tasks = [
            asyncio.create_task(hold("session-a", first_entered)),
            asyncio.create_task(hold("session-b", second_entered)),
        ]
        await asyncio.wait_for(
            asyncio.gather(first_entered.wait(), second_entered.wait()),
            timeout=1,
        )
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_keep_gate_busy():
    async def scenario():
        release = asyncio.Event()
        owner_entered = asyncio.Event()

        async def owner():
            async with gate_for("session-cancel"):
                owner_entered.set()
                await release.wait()

        async def waiter():
            async with gate_for("session-cancel"):
                raise AssertionError("cancelled waiter entered")

        owner_task = asyncio.create_task(owner())
        await owner_entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        waiter_task.cancel()
        try:
            await waiter_task
        except asyncio.CancelledError:
            pass
        release.set()
        await owner_task

        async with gate_for("session-cancel"):
            pass

    asyncio.run(scenario())


def test_scheduler_wait_wakes_immediately():
    async def scenario():
        stop_event = asyncio.Event()
        waiter = asyncio.create_task(wait_for_migration_wake(stop_event, timeout=60))
        await asyncio.sleep(0)
        wake_migration_scheduler()
        await asyncio.wait_for(waiter, timeout=1)

    asyncio.run(scenario())


def test_codex_watcher_defers_events_of_a_migrating_session():
    async def scenario():
        watcher = CodexSessionsWatcher()
        parsed = ParsedSessionFile(
            "project", "watcher-session", SessionType.SESSION, "rollout.jsonl",
        )
        assert await watcher.defer_session_change(parsed) is False
        with migrating(parsed.session_id):
            assert await watcher.defer_session_change(parsed) is True
        assert await watcher.defer_session_change(parsed) is False

    asyncio.run(scenario())


def test_watcher_skips_a_deferred_change_and_replays_it_on_demand(monkeypatch):
    """A deferred event never blocks the loop; ``process_path`` replays the file."""

    async def scenario():
        watcher = CodexSessionsWatcher()
        parsed = ParsedSessionFile("project", "replay-session", SessionType.SESSION, "rollout.jsonl")
        processed: list[tuple[str, Change]] = []
        deferring = True

        async def parse(_path):
            return parsed

        async def defer(_parsed):
            return deferring

        async def process(path, _parsed, change_type, _channel_layer):
            processed.append((str(path), change_type))

        async def special(*_args):
            return False

        monkeypatch.setattr(watcher, "parse_session_file", parse)
        monkeypatch.setattr(watcher, "defer_session_change", defer)
        monkeypatch.setattr(watcher, "_process_parsed_session_change", process)
        monkeypatch.setattr(watcher, "maybe_handle_special_change", special)
        monkeypatch.setattr("twicc.providers.sessions_watcher.run_under_db_write_lock", lambda fn: fn())
        monkeypatch.setattr("twicc.providers.sessions_watcher.get_channel_layer", lambda: object())

        await watcher._process_change(Change.modified, "/tmp/rollout.jsonl", object())
        assert processed == []

        deferring = False
        await watcher.process_path(Path("/tmp/rollout.jsonl"))
        assert processed == [("/tmp/rollout.jsonl", Change.modified)]

    asyncio.run(scenario())


def test_rebuild_request_is_drained_once_and_wakes_the_scheduler(monkeypatch):
    from twicc.providers.codex import migration_gate

    monkeypatch.setattr(migration_gate, "_migration_wake_generation", 0)
    monkeypatch.setattr(migration_gate, "_rebuild_requests", set())
    request_rebuild("rewritten")
    assert migration_gate._migration_wake_generation == 1
    assert take_rebuild_requests() == {"rewritten"}
    assert take_rebuild_requests() == set()


def test_codex_send_uses_gate_before_manager_lock(monkeypatch):
    async def scenario():
        manager = CodexAgentManager()
        send_entered = asyncio.Event()

        async def fake_send(*_args, **_kwargs):
            send_entered.set()
            return True

        monkeypatch.setattr(manager, "_send_to_session_under_gate", fake_send)

        async with gate_for("send-session"):
            task = asyncio.create_task(manager.send_to_session(
                "send-session", "project", "/repo", "hello", None,
            ))
            await asyncio.sleep(0)
            assert not send_entered.is_set()
        assert await task is True
        assert send_entered.is_set()

    asyncio.run(scenario())
