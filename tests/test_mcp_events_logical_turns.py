"""Managed MCP conclusions keep logical identity and recover expired progress."""

import asyncio
from datetime import timedelta
from uuid import uuid4

from django.utils import timezone
import pytest

from tests.test_mcp_events_turns import agent, env, opened, pending, stopped_ticks  # noqa: F401
from tests.test_mcp_events_payload import occurrence
from tests.test_mcp_events_writer import apply
from tests.test_wait_reply import api_error, managed_turn, native_assistant
from twicc.agent.states import AgentState
from twicc.core.models import LogicalTurn, McpEventSubscription, Session
from twicc.logical_turn_cleanup_task import purge_logical_turns
from twicc.mcp.events.catalog import event_id
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.runtime import EventsRuntime, LogicalProgressReset

pytestmark = pytest.mark.django_db(transaction=True)


def reload_monitor(env):
    env.row.refresh_from_db()
    env.runtime._add_monitor(SubscriptionSnapshot.from_row(env.row))
    env.monitor = env.runtime.monitors[env.row.pk]
    return env.monitor


def test_open_logical_turn_does_not_emit_a_native_final(env):
    turn = managed_turn(env.session)
    native_assistant(env.session, 2, "intermediate", "a1")
    opened(env)
    agent(env, state=AgentState.USER_TURN, at=999)
    env.tick()
    assert not env.emissions
    LogicalTurn.objects.filter(pk=turn.pk).update(outcome="normal", closed_at=timezone.now())
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "replied"


def test_two_distinct_empty_logical_ends_have_distinct_event_ids(env):
    managed_turn(env.session, outcome="ended", refs=(), first=None)
    managed_turn(env.session, position=2, outcome="ended", refs=(), first=None)
    opened(env)
    agent(env, state=AgentState.USER_TURN, at=999)
    env.tick()
    env.tick()
    assert len(env.emissions) == 2
    assert len({item.event_id for item in env.emissions}) == 2


def test_monitor_reload_after_progress_purge_keeps_working(env, monkeypatch):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    previous = managed_turn(env.session, outcome="normal")
    native_assistant(env.session, 2, "consumed answer", "a1")
    LogicalTurn.objects.filter(pk=previous.pk).update(closed_at=timezone.now() - timedelta(hours=49))
    McpEventSubscription.objects.filter(pk=env.row.pk).update(
        cursor_line=2, cursor_at=1001, last_logical_turn_id=previous.pk,
    )
    assert purge_logical_turns(now=timezone.now()) == 1
    next_turn = managed_turn(env.session, position=2, outcome="ended", refs=(), first=None)
    monitor = reload_monitor(env)
    agent(env, state=AgentState.USER_TURN, at=999)
    assert not env.runtime._tick_monitor(monitor)
    assert not env.emissions
    assert monitor.last_logical_turn_id is None
    assert (monitor.cursor_line, monitor.cursor_at) == (2, 1001)
    assert monitor.wait.after_turn_id is None
    assert monitor.wait.since_line_num == 2
    asyncio.run(apply(env.runtime, *env.writes))
    monitor = reload_monitor(env)
    assert monitor.last_logical_turn_id is None
    assert (monitor.cursor_line, monitor.cursor_at) == (2, 1001)
    env.runtime._tick_monitor(monitor)
    assert len(env.emissions) == 1
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"
    assert env.emissions[0].cursor.last_logical_turn_id == str(next_turn.pk)


@pytest.mark.parametrize("outcome", ["normal", "ended", "provider_error"])
def test_logical_identity_is_stable_when_unacknowledged_monitor_reloads(env, outcome):
    turn = managed_turn(env.session, outcome=outcome)
    native_assistant(env.session, 2, "answer", "a1")
    agent(env, state=AgentState.USER_TURN, at=999)
    # An end needs two ready snapshots when a transcript row changes last_line.
    for _ in range(2):
        env.runtime._tick_monitor(env.monitor)
    first = env.emissions[0]
    monitor = reload_monitor(env)
    env.clock.advance(1)
    for _ in range(2):
        env.runtime._tick_monitor(monitor)
    assert len(env.emissions) == 2
    assert first.event_id == env.emissions[1].event_id
    expected_outcome = "replied" if outcome == "normal" else outcome
    assert first.event_id == event_id(env.row.id, expected_outcome, f"logical_turn:{turn.pk}")


def test_selected_open_turn_does_not_change_pending_request_identity(env):
    managed_turn(env.session)
    native_assistant(env.session, 2, "intermediate", "a1")
    agent(env, state=AgentState.USER_TURN, pending_requests=[pending("request-1")])
    env.tick()
    assert env.emissions[0].event_id == event_id(env.row.id, "awaiting_user_input", "request-1")
    assert env.monitor.wait.logical_turn_id is not None
    assert env.monitor.wait.progress().last_logical_turn_id is None


def test_legacy_answer_before_selected_open_turn_keeps_transcript_identity(env):
    managed_turn(env.session, refs=("a2",), first="a2")
    native_assistant(env.session, 2, "legacy answer", "legacy")
    native_assistant(env.session, 5, "managed intermediate", "a2")
    agent(env, state=AgentState.USER_TURN, at=999)
    env.tick()
    assert len(env.emissions) == 1
    assert env.emissions[0].event_id == event_id(env.row.id, "replied", "0:2")
    assert env.payloads()[0]["data"]["reply"]["text"] == "legacy answer"
    assert env.emissions[0].cursor.last_logical_turn_id is None


def consume_background_answer(env):
    turn = managed_turn(env.session, outcome="normal")
    native_assistant(env.session, 2, "background answer", "a1")
    env.session.last_line = 2
    env.session.save(update_fields=["last_line"])
    env.monitor.arguments = {**env.monitor.arguments, "wait_background": True}
    env.monitor.wait = env.runtime._new_wait(env.monitor)
    agent(env, state=AgentState.USER_TURN, at=999, background_work_in_progress={"shells": 1})
    env.tick()
    assert not env.emissions
    assert env.monitor.wait.progress().last_logical_turn_id == str(turn.pk)
    assert env.monitor.wait.logical_turn_id is None
    assert env.monitor.last_logical_turn_id is None
    return turn


@pytest.mark.parametrize("outcome", ["replied", "provider_error"])
def test_background_consumption_does_not_change_later_unmanaged_identity(env, outcome):
    consumed = consume_background_answer(env)
    env.registry.remove_agent(env.session.id)
    if outcome == "replied":
        native_assistant(env.session, 5, "unmanaged answer", "legacy")
        expected_text = "unmanaged answer"
    else:
        api_error(env.session, 5, terminal=True, text="unmanaged provider unavailable")
        expected_text = "unmanaged provider unavailable"
    env.tick()
    reply = env.payloads()[0]["data"]["reply"]
    assert (reply["outcome"], reply["text"]) == (outcome, expected_text)
    assert env.emissions[0].event_id == event_id(env.row.id, outcome, "0:5")
    assert env.emissions[0].cursor.cursor_line == 5
    assert env.emissions[0].cursor.last_logical_turn_id == str(consumed.pk)


def test_background_consumption_keeps_legacy_identity_before_selected_pending_turn(env):
    consumed = consume_background_answer(env)
    selected = managed_turn(env.session, position=2, refs=("b1",), first="b1")
    native_assistant(env.session, 5, "historical answer", "legacy")
    native_assistant(env.session, 8, "managed intermediate", "b1")
    env.registry.remove_agent(env.session.id)
    wait = env.monitor.wait
    env.tick()
    assert wait.logical_turn_id == str(selected.pk)
    assert env.payloads()[0]["data"]["reply"]["text"] == "historical answer"
    assert env.emissions[0].event_id == event_id(env.row.id, "replied", "0:5")
    assert env.emissions[0].cursor.last_logical_turn_id == str(consumed.pk)


@pytest.mark.parametrize("outcome", ["normal", "ended", "provider_error"])
def test_background_consumption_uses_returned_managed_terminal_identity(env, outcome):
    consume_background_answer(env)
    returned = managed_turn(env.session, position=2, outcome=outcome, refs=("b1",), first="b1")
    native_assistant(env.session, 5, "managed answer", "b1")
    env.session.last_line = 5
    env.session.save(update_fields=["last_line"])
    agent(env, state=AgentState.USER_TURN, at=999)
    env.tick()
    if outcome == "ended":
        assert not env.emissions  # The changed transcript end needs another ready snapshot.
        env.tick()
    expected_outcome = "replied" if outcome == "normal" else outcome
    assert env.payloads()[0]["data"]["reply"]["outcome"] == expected_outcome
    assert env.emissions[0].event_id == event_id(env.row.id, expected_outcome, f"logical_turn:{returned.pk}")
    assert env.emissions[0].cursor.last_logical_turn_id == str(returned.pk)


@pytest.mark.parametrize("ready_before_end", [False, True])
def test_background_consumption_uses_new_empty_managed_end_identity(env, ready_before_end):
    consume_background_answer(env)
    returned = managed_turn(env.session, position=2, outcome="normal", refs=(), first=None)
    LogicalTurn.objects.filter(pk=returned.pk).update(source_floor=2)
    env.registry.remove_agent(env.session.id)
    if ready_before_end:
        env.runtime._read_monitor_session(env.monitor)
    wait = env.monitor.wait
    env.tick()
    assert wait.logical_turn_id is None
    assert wait.progress().last_logical_turn_id == str(returned.pk)
    if not ready_before_end:
        assert not env.emissions
        stopped_ticks(env)
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", f"logical_turn:{returned.pk}")
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"
    assert env.payloads()[0]["data"]["reply"]["line_num"] is None
    assert "text" not in env.payloads()[0]["data"]["reply"]


@pytest.mark.parametrize("change", ["outcome", "source", "consumed_interval"])
def test_deferred_empty_managed_end_does_not_override_changed_result(env, change):
    earlier = consume_background_answer(env)
    deferred = managed_turn(env.session, position=2, outcome="normal", refs=(), first=None)
    LogicalTurn.objects.filter(pk=deferred.pk).update(source_floor=2)
    env.registry.remove_agent(env.session.id)
    env.tick()
    assert not env.emissions
    assert env.monitor.wait.progress().last_logical_turn_id == str(deferred.pk)
    if change == "outcome":
        agent(env, state=AgentState.USER_TURN, at=999, pending_requests=[pending("new-request")])
        env.tick()
        assert env.payloads()[0]["data"]["reply"]["outcome"] == "awaiting_user_input"
        env.registry.remove_agent(env.session.id)
        expected_key = f"logical_turn:{earlier.pk}"
    elif change == "source":
        env.session.storage_incarnation = str(uuid4())
        env.session.save(update_fields=["storage_incarnation"])
        expected_key = "0:2"
    else:
        returned = managed_turn(env.session, position=3, outcome="ended", refs=(), first=None)
        expected_key = f"logical_turn:{returned.pk}"
    stopped_ticks(env)
    assert env.emissions[-1].event_id == event_id(env.row.id, "ended", expected_key)
    assert len(env.emissions) == (2 if change == "outcome" else 1)


@pytest.mark.parametrize("ignored_owner", ["managed", "legacy", "earlier_managed"])
def test_dead_agent_fallback_uses_the_ignored_answer_owner(env, ignored_owner):
    returned = consumed = consume_background_answer(env)
    if ignored_owner == "legacy":
        # Late indexing can expose a historical final before managed coverage.
        native_assistant(env.session, 1, "late historical answer", "legacy")
        other_session = Session.objects.create(
            id="other-session", project=env.session.project, file_path="other-session.jsonl",
        )
        foreign = managed_turn(other_session, outcome="normal", refs=("legacy",), first="legacy")
        LogicalTurn.objects.filter(pk=foreign.pk).update(storage_incarnation=env.session.storage_incarnation)
        env.tick()
        assert env.monitor.wait.last_ignored.line_num == 1
        expected_text, expected_key = "late historical answer", "0:1"
    else:
        expected_text, expected_key = "background answer", f"logical_turn:{returned.pk}"
        if ignored_owner == "earlier_managed":
            consumed = managed_turn(env.session, position=2, outcome="normal", refs=(), first=None)
            LogicalTurn.objects.filter(pk=consumed.pk).update(source_floor=2)
            env.tick()
            assert env.monitor.wait.progress().last_logical_turn_id == str(consumed.pk)
    assert not env.emissions
    env.registry.remove_agent(env.session.id)
    stopped_ticks(env)
    reply = env.payloads()[0]["data"]["reply"]
    assert (reply["outcome"], reply["text"]) == ("ended", expected_text)
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", expected_key)
    assert env.emissions[0].cursor.last_logical_turn_id == str(consumed.pk)
    assert env.emissions[0].cursor.cursor_line == 2


@pytest.mark.parametrize("source_change", [
    {"provider": "codex"}, {"storage_incarnation": "another-source"}, {"history_epoch": 9},
])
def test_dead_agent_fallback_requires_matching_managed_source(env, source_change):
    consumed = consume_background_answer(env)
    LogicalTurn.objects.filter(pk=consumed.pk).update(**source_change)
    env.registry.remove_agent(env.session.id)
    stopped_ticks(env)
    assert env.payloads()[0]["data"]["reply"]["text"] == "background answer"
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", "0:2")
    assert env.emissions[0].cursor.last_logical_turn_id == str(consumed.pk)


def test_live_monitor_recovers_through_existing_writer_queue_after_consumed_answer_is_purged(env, monkeypatch):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    previous = managed_turn(env.session, outcome="normal")
    native_assistant(env.session, 2, "old answer", "a1")
    agent(env, state=AgentState.USER_TURN, at=999)
    env.tick()
    assert env.monitor.last_logical_turn_id == str(previous.pk)
    original_cursor = env.emissions[0].cursor
    async def run():
        await apply(env.runtime, *env.writes, original_cursor)
        await LogicalTurn.objects.filter(pk=previous.pk).aupdate(
            closed_at=timezone.now() - timedelta(hours=49),
        )
        assert await asyncio.to_thread(purge_logical_turns, now=timezone.now()) == 1
        env.runtime.loop = asyncio.get_running_loop()
        monkeypatch.setattr(env.runtime, "post_write", EventsRuntime.post_write.__get__(env.runtime))
        try:
            await asyncio.to_thread(env.runtime._tick_monitor, env.monitor)
            assert len(env.emissions) == 1
            assert env.monitor.last_logical_turn_id is None
            await env.runtime._write_barrier()
            saved = await McpEventSubscription.objects.aget(pk=env.row.pk)
            assert saved.last_logical_turn_id is None
            assert (saved.cursor_line, saved.cursor_at) == (original_cursor.cursor_line, original_cursor.cursor_at)
            await asyncio.to_thread(env.runtime._tick_monitor, env.monitor)
            assert len(env.emissions) == 1  # The retained native answer cannot replay.
            next_turn = await asyncio.to_thread(
                managed_turn, env.session, position=2, outcome="normal", refs=("a2",), first="a2",
            )
            await asyncio.to_thread(native_assistant, env.session, 5, "new answer", "a2")
            await asyncio.to_thread(env.runtime._tick_monitor, env.monitor)
            assert len(env.emissions) == 2
            assert env.payloads()[-1]["data"]["reply"]["text"] == "new answer"
            assert env.emissions[-1].cursor.last_logical_turn_id == str(next_turn.pk)
            await env.runtime._write_barrier()
        finally:
            if env.runtime.writer_task is not None:
                env.runtime.writer_task.cancel()
                await asyncio.gather(env.runtime.writer_task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["replied", "provider_error", "ended"])
def test_builder_logical_identity_does_not_depend_on_transcript_cursor(outcome):
    first = occurrence(outcome, logical_turn_id="logical-1")
    second = occurrence(outcome, line=None, numbering=9, last_line=100, logical_turn_id="logical-1")
    assert first["eventId"] == second["eventId"]
    assert first["eventId"] == event_id("sub_a", outcome, "logical_turn:logical-1")
    assert "logical_turn_id" not in first["data"]


def test_builder_keeps_pending_request_identity_when_logical_identity_is_supplied():
    assert occurrence("awaiting_user_input", logical_turn_id="logical-1")["eventId"] == event_id(
        "sub_a", "awaiting_user_input", "request-1",
    )


@pytest.mark.parametrize("failure", ["prepare", "post"])
def test_progress_reset_keeps_memory_and_cursors_when_preparation_or_post_fails(env, monkeypatch, failure):
    missing_id = uuid4()
    McpEventSubscription.objects.filter(pk=env.row.pk).update(last_logical_turn_id=missing_id)
    monitor = reload_monitor(env)
    old_wait = monitor.wait
    def fail(*args, **kwargs):
        raise RuntimeError("reset failed")
    monkeypatch.setattr(env.runtime, "_new_wait" if failure == "prepare" else "post_write", fail)
    with pytest.raises(RuntimeError, match="reset failed"):
        env.runtime._tick_monitor(monitor)
    assert monitor.wait is old_wait
    assert monitor.last_logical_turn_id == missing_id
    assert (monitor.cursor_line, monitor.cursor_at) == (0, 1000)
    assert not env.writes and not env.emissions


def test_progress_reset_prepares_wait_before_post_and_commits_memory_after_post(env, monkeypatch):
    missing_id = uuid4()
    McpEventSubscription.objects.filter(pk=env.row.pk).update(last_logical_turn_id=missing_id)
    monitor = reload_monitor(env)
    old_wait = monitor.wait
    prepare = env.runtime._new_wait
    prepared = []
    def new_wait(*args, **kwargs):
        wait = prepare(*args, **kwargs)
        prepared.append(wait)
        return wait
    def post(write):
        assert len(prepared) == 1
        assert monitor.wait is old_wait
        assert monitor.last_logical_turn_id == missing_id
        env.writes.append(write)
    monkeypatch.setattr(env.runtime, "_new_wait", new_wait)
    monkeypatch.setattr(env.runtime, "post_write", post)
    env.runtime._tick_monitor(monitor)
    assert monitor.wait is prepared[0]
    assert monitor.last_logical_turn_id is None
    env.row.refresh_from_db()
    assert env.row.last_logical_turn_id == missing_id  # The worker never mutates SQL.
    assert not env.emissions


def test_progress_reference_in_another_session_is_missing_for_this_monitor(env):
    previous = managed_turn(env.session, outcome="ended", refs=(), first=None)
    LogicalTurn.objects.filter(pk=previous.pk).update(session_id="other-session")
    McpEventSubscription.objects.filter(pk=env.row.pk).update(last_logical_turn_id=previous.pk)
    monitor = reload_monitor(env)
    env.runtime._tick_monitor(monitor)
    assert monitor.last_logical_turn_id is None
    assert not env.emissions


@pytest.mark.parametrize("failure", ["source_changed", "selected_missing"])
def test_unrelated_detector_failures_do_not_reset_consumed_progress(env, failure):
    previous = managed_turn(env.session, outcome="ended", refs=(), first=None)
    selected = managed_turn(env.session, position=2)
    McpEventSubscription.objects.filter(pk=env.row.pk).update(last_logical_turn_id=previous.pk)
    monitor = reload_monitor(env)
    agent(env, state=AgentState.USER_TURN, at=999)
    env.runtime._tick_monitor(monitor)
    assert monitor.wait.logical_turn_id == str(selected.pk)
    LogicalTurn.objects.filter(pk=previous.pk).delete()
    if failure == "source_changed":
        LogicalTurn.objects.filter(pk=selected.pk).update(history_epoch=9)
    else:
        LogicalTurn.objects.filter(pk=selected.pk).delete()
    env.runtime._tick_monitor(monitor)
    assert monitor.tick_outcome == "wait_failed"
    assert str(monitor.last_logical_turn_id) == str(previous.pk)
    assert not any(isinstance(write, LogicalProgressReset) for write in env.writes)
    reply = env.payloads()[0]["data"]["reply"]
    assert reply["outcome"] == "wait_failed"
    if failure == "source_changed":
        assert reply["error"] == "Logical turn source changed"


@pytest.mark.parametrize("failure", ["source_changed", "selected_missing"])
def test_background_consumption_keeps_safe_cursor_when_a_later_detector_failure_occurs(env, failure):
    consumed = managed_turn(env.session, outcome="normal")
    native_assistant(env.session, 2, "background answer", "a1")
    env.session.last_line = 2
    env.session.save(update_fields=["last_line"])
    selected = managed_turn(env.session, position=2, refs=(), first=None)
    env.monitor.arguments = {**env.monitor.arguments, "wait_background": True}
    env.monitor.wait = env.runtime._new_wait(env.monitor)
    agent(env, state=AgentState.USER_TURN, at=999, background_work_in_progress={"shells": 1})
    env.tick()
    assert not env.emissions
    assert env.monitor.wait.progress().consumed_up_to == 2
    assert env.monitor.wait.progress().last_logical_turn_id == str(consumed.pk)
    assert env.monitor.cursor_line == 0
    assert env.monitor.last_logical_turn_id is None
    env.tick()
    assert env.monitor.wait.logical_turn_id == str(selected.pk)
    if failure == "source_changed":
        LogicalTurn.objects.filter(pk=selected.pk).update(history_epoch=9)
    else:
        LogicalTurn.objects.filter(pk=selected.pk).delete()
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "wait_failed"
    assert env.emissions[0].cursor.cursor_line == 2
    assert env.monitor.cursor_line == 2
    assert env.monitor.wait.since_line_num == 2
    assert env.emissions[0].event_id == event_id(env.row.id, "wait_failed", "last_line:0:2")
    assert not any(isinstance(write, LogicalProgressReset) for write in env.writes)
