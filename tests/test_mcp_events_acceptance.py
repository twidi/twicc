"""Cross-boundary acceptance scenarios and explicitly accepted delivery limits."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_mcp_events_turns import agent, opened, pending, prompt, stopped_ticks
from tests.test_mcp_events_writer import apply
from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind
from twicc.core.models import McpEventSubscription, Session
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.runtime import CursorWrite

pytestmark = pytest.mark.django_db(transaction=True)
env = test_mcp_events_turns.env


@pytest.fixture(autouse=True)
def writer_lock(monkeypatch):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())


def persist(env, *, cursors=True):
    writes = list(env.writes)
    if cursors:
        writes.extend(event.cursor for event in env.emissions if event.cursor is not None)
    asyncio.run(apply(env.runtime, *writes))
    env.writes.clear()


def reload(env):
    env.runtime._load_monitors()
    env.monitor = env.runtime.monitors[env.row.id]
    env.tick = lambda: env.runtime._tick_monitor(env.monitor)


def test_restart_replays_only_first_past_conclusion_with_same_id(env):
    for line in (2, 4, 6):
        append_assistant(env.session, line)
    McpEventSubscription.objects.update(initial_last_line=6, turn_open=True, turn_opened_by="history")
    reload(env)
    env.tick()
    first_id = env.emissions[0].event_id
    persist(env, cursors=False)
    reload(env)
    env.tick()
    stopped_ticks(env)
    assert [event.event_id for event in env.emissions] == [first_id, first_id]
    assert env.monitor.cursor_line == 6


def test_restart_pending_request_reuses_id_and_older_reply_keeps_later_turn_open(env):
    agent(env, pending_requests=(pending("question", 1001),), at=999)
    env.tick()
    request_id = env.emissions[0].event_id
    persist(env, cursors=False)
    reload(env)
    env.tick()
    assert env.emissions[-1].event_id == request_id
    env.emissions.clear()
    McpEventSubscription.objects.update(turn_started_at=1010, turn_open=True, turn_opened_by="transition")
    append_assistant(env.session, 2, timestamp=datetime.fromtimestamp(1005, UTC))
    prompt(env, 3)
    env.registry.remove_agent(env.session.id)
    reload(env)
    env.tick()
    assert env.monitor.turn_open
    stopped_ticks(env)
    assert [event["data"]["reply"]["outcome"] for event in env.payloads()] == ["replied", "ended"]


def test_supervisor_reload_after_reply_during_hold_does_not_reopen_float_transition(env):
    started = 1000.123456789
    env.clock.advance(1)
    agent(env, at=started, background_work_in_progress={"shells": 1})
    append_assistant(env.session, 2, timestamp=datetime.fromtimestamp(1001, UTC))
    env.tick()
    persist(env)
    reload(env)
    assert env.monitor.turn_started_at == started
    env.tick()
    agent(env, state=AgentState.USER_TURN, at=started)
    stopped_ticks(env)
    assert len(env.emissions) == 1 and not env.monitor.turn_open


def test_ignored_background_final_replays_after_wait_replacement(env):
    McpEventSubscription.objects.update(arguments={"session_id": env.session.id, "wait_background": True})
    reload(env)
    opened(env)
    agent(env, at=999, background_work_in_progress={"shells": 1})
    append_assistant(env.session, 2, "Ignored interim")
    env.tick()
    assert not env.emissions and env.monitor.wait.last_ignored is not None
    agent(env, state=AgentState.USER_TURN, at=999)
    stopped_ticks(env)
    assert not env.emissions
    reload(env)
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["text"] == "Ignored interim"


def test_raw_boot_final_can_be_skipped_by_later_classified_final(env):
    raw = append_assistant(env.session, 2, "Raw boot answer")
    raw.kind = None
    raw.save(update_fields=["kind"])
    append_assistant(env.session, 4, "Live answer")
    env.tick()
    raw.kind = ItemKind.ASSISTANT_MESSAGE
    raw.save(update_fields=["kind"])
    stopped_ticks(env)
    assert [event["data"]["reply"]["text"] for event in env.payloads()] == ["Live answer"]


def test_dormant_turn_and_answered_request_are_lost_but_late_final_delivers(env):
    env.clock.advance(3600)
    env.runtime._tick()
    assert env.monitor.dormant
    agent(env, pending_requests=(pending("missed"),))
    env.runtime._tick()
    agent(env, state=AgentState.USER_TURN)
    env.runtime._tick()
    assert not env.emissions
    snapshot = SubscriptionSnapshot.from_row(env.row)._replace(refresh_before=env.clock.utcnow() + timedelta(hours=1))
    env.runtime.update(snapshot)
    env.runtime._drain_commands()
    stopped_ticks(env)
    assert not env.emissions  # No observed transition or still-pending request.
    append_assistant(env.session, 2, "Late indexed answer")
    env.tick()
    assert [event["data"]["reply"]["outcome"] for event in env.payloads()] == ["replied"]


def test_two_turns_without_any_lines_share_ended_id(env):
    ids = []
    for _ in range(2):
        env.clock.advance(1)
        agent(env)
        env.tick()
        env.registry.remove_agent(env.session.id)
        stopped_ticks(env)
        ids.append(env.emissions[-1].event_id)
    assert len(env.emissions) == 2 and ids[0] == ids[1]


@pytest.mark.parametrize("case", ["same-state", "between-ticks", "no-transition"])
def test_unobserved_turn_transition_has_no_ended(env, case):
    prompt(env, 1)
    if case == "same-state":
        env.clock.advance(1)
        agent(env, previous_state=AgentState.ASSISTANT_TURN)
        env.tick()
    elif case == "between-ticks":
        agent(env)
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions


def test_pseudo_turn_running_at_creation_or_dead_at_end_can_emit_empty_ended(env):
    opened(env, by="initial")
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"
    env.clock.advance(1)
    agent(env)
    env.tick()
    env.registry.remove_agent(env.session.id)
    stopped_ticks(env)
    assert len(env.emissions) == 2
    assert all(event["data"]["reply"]["line_num"] is None for event in env.payloads())


def test_retry_conclusion_passed_by_silent_cursor_is_not_replayed(env):
    append_assistant(env.session, 2)
    env.tick()
    env.writes.append(CursorWrite(*env.monitor.generation, 0, 3, None))
    persist(env, cursors=False)
    reload(env)
    stopped_ticks(env)
    assert len(env.emissions) == 1


@pytest.mark.parametrize("end", [2, 40])
def test_rebuild_discards_intermediate_conclusions_then_delivers_new_epoch(env, end):
    Session.objects.filter(pk=env.session.pk).update(provider="codex")
    env.session.provider = "codex"
    env.monitor.cursor_line = 20
    env.monitor.wait = env.runtime._new_wait(env.monitor)
    opened(env)
    append_assistant(env.session, end, "During rebuild")
    Session.objects.filter(pk=env.session.pk).update(history_epoch=1, compute_version=None)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    assert env.monitor.pending_rebase and not env.emissions
    from twicc.providers.helpers import get_provider_helpers
    Session.objects.filter(pk=env.session.pk).update(compute_version=get_provider_helpers("codex").current_compute_version)
    env.runtime._tick()
    assert env.monitor.cursor_line == end and not env.monitor.turn_open
    stopped_ticks(env)
    assert not env.emissions
    append_assistant(env.session, end + 2, "After rebuild")
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["text"] == "After rebuild"
    persist(env)
    reload(env)
    stopped_ticks(env)
    assert len(env.emissions) == 1


def test_no_row_creation_then_row_appearance_delivers_first_reply_without_rebase(env):
    env.session.delete()
    McpEventSubscription.objects.update(session_id="late-session")
    env.registry.set_agent("late-session", at=999)
    reload(env)
    assert env.monitor.numbering == 0 and not env.monitor.pending_rebase
    late = Session.objects.create(id="late-session", project=env.session.project,
                                  provider="claude_code", compute_version=env.session.compute_version)
    append_assistant(late, 2)
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "replied"
    assert env.monitor.numbering == 0 and not env.monitor.pending_rebase


@pytest.mark.parametrize("reload_kind", ["supervisor", "restart"])
def test_failed_rebase_cas_then_delivered_conclusion_heals_without_replay(env, monkeypatch, reload_kind):
    from django.db import OperationalError
    from twicc.mcp.events.runtime import EventsRuntime, RebaseWrite

    Session.objects.filter(pk=env.session.pk).update(history_epoch=1)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    assert isinstance(env.writes[-1], RebaseWrite)
    original = env.runtime._apply_write

    def fail_rebase(item):
        if isinstance(item, RebaseWrite):
            raise OperationalError("injected failed CAS")
        original(item)

    monkeypatch.setattr(env.runtime, "_apply_write", fail_rebase)
    append_assistant(env.session, 2)
    env.tick()
    assert len(env.emissions) == 1 and env.monitor.numbering == 1
    persist(env)
    env.row.refresh_from_db()
    assert env.row.numbering == 0 and env.row.cursor_line == 0
    if reload_kind == "restart":
        runtime = EventsRuntime(clock=env.clock.clock, data_dir=env.runtime.data_dir, post_emission=env.emissions.append)
        monkeypatch.setattr(runtime, "post_write", env.writes.append)
        env.runtime = runtime
    else:
        monkeypatch.setattr(env.runtime, "_apply_write", original)
    reload(env)
    assert env.monitor.pending_rebase
    env.runtime._tick()
    assert env.monitor.cursor_line == 2 and env.monitor.numbering == 1
    stopped_ticks(env)
    assert len(env.emissions) == 1


def test_history_middle_of_running_turn_closes_after_first_past_final(env):
    prompt(env, 1)
    append_assistant(env.session, 2, "F1")
    prompt(env, 3)
    append_assistant(env.session, 4, "F2")
    McpEventSubscription.objects.update(cursor_line=1, initial_last_line=4, turn_open=True,
                                        turn_opened_by="history", turn_started_at=1001)
    reload(env)
    env.tick()
    stopped_ticks(env)
    assert [event["data"]["reply"]["text"] for event in env.payloads()] == ["F1"]


def test_codex_prompt_during_hold_then_compact_does_not_emit_ended(env):
    from twicc.providers.helpers import get_provider_helpers

    Session.objects.filter(pk=env.session.pk).update(provider="codex",
        compute_version=get_provider_helpers("codex").current_compute_version)
    env.session.provider = "codex"
    McpEventSubscription.objects.update(arguments={"session_id": env.session.id, "wait_background": True})
    reload(env)
    opened(env)
    agent(env, at=999, background_work_in_progress={"subagents": 1})
    prompt(env, 1)
    append_assistant(env.session, 2)
    env.tick()
    assert env.monitor.wait.last_ignored is not None
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    env.clock.advance(1)
    agent(env)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions


def test_claude_stale_compute_keeps_supplied_history_cursor(env):
    append_assistant(env.session, 4)
    Session.objects.filter(pk=env.session.pk).update(compute_version=None)
    McpEventSubscription.objects.update(cursor_line=2, initial_last_line=4)
    reload(env)
    assert not env.monitor.pending_rebase and env.monitor.cursor_line == 2
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["line_num"] == 4


def test_working_turn_at_rebase_apply_later_crashes_and_emits_ended(env):
    agent(env, at=1001)
    env.tick()
    Session.objects.filter(pk=env.session.pk).update(history_epoch=1, compute_version=None)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    Session.objects.filter(pk=env.session.pk).update(compute_version=env.session.compute_version)
    env.runtime._tick()
    assert env.monitor.turn_open and env.monitor.turn_opened_by == "initial"
    env.registry.remove_agent(env.session.id)
    stopped_ticks(env)
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"
