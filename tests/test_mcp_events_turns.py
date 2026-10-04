"""Continuous conclusions use the real CLI detector and provider transcript rows."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.db import connection
from django.test.utils import CaptureQueriesContext
import orjson
import pytest

from tests.mcp_events_helpers import FakeClock, FakeRegistry, append_assistant
from twicc.agent.states import AgentState, PendingRequest
from twicc.cli import _wait_reply
from twicc.cli._drop_request import transport
from twicc.core.enums import ItemKind
from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient, Project, Session, SessionItem
from twicc.mcp.events import runtime as runtime_module
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.runtime import CursorWrite, EventsRuntime, TurnWrite
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db


@pytest.fixture
def env(monkeypatch, tmp_path, request):
    clock, registry = FakeClock(epoch=1000), FakeRegistry()
    registry.install(monkeypatch)
    monkeypatch.setattr(_wait_reply, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: SimpleNamespace(pid=1234))
    token = transport.backend_loop.set(object())
    project = Project.objects.create(id="events-project", directory=str(tmp_path))
    provider = getattr(request, "param", "claude_code")
    session = Session.objects.create(
        id="session", project=project, provider=provider, title="A title",
        compute_version=get_provider_helpers(provider).current_compute_version,
    )
    client = McpOAuthClient.objects.create(id="client")
    oauth = McpConnection.objects.create(id="connection", client=client, resource="https://mcp.example/mcp")
    row = McpEventSubscription.objects.create(
        id="subscription", connection=oauth, name="session.concluded", arguments={"session_id": session.id},
        session_id=session.id, callback_url="https://callback.example/events", secret="secret",
        cursor_line=0, cursor_at=1000, initial_last_line=0, numbering=0, data_dir=str(tmp_path),
        turn_open=False, turn_started_at=None, turn_opened_by="", turn_start_line=0,
        refresh_before=clock.utcnow() + timedelta(hours=1),
    )
    emissions, writes, order = [], [], []

    def emit(item):
        order.append("emit")
        emissions.append(item)

    runtime = EventsRuntime(clock=clock.clock, data_dir=tmp_path, post_emission=emit)

    def write(item):
        order.append("write")
        writes.append(item)

    monkeypatch.setattr(runtime, "post_write", write)
    runtime._add_monitor(SubscriptionSnapshot.from_row(row))
    monitor = runtime.monitors[row.id]
    fixture = SimpleNamespace(clock=clock, registry=registry, session=session, row=row, runtime=runtime,
                              monitor=monitor, emissions=emissions, writes=writes, order=order)
    fixture.tick = lambda: runtime._tick_monitor(monitor)
    fixture.payloads = lambda: [orjson.loads(item.body) for item in emissions]
    try:
        yield fixture
    finally:
        transport.backend_loop.reset(token)


def agent(env, *, state=AgentState.ASSISTANT_TURN, at=None, **kwargs):
    return env.registry.set_agent(env.session.id, state=state, at=env.clock.epoch() if at is None else at,
                                  provider=env.session.provider, **kwargs)


def prompt(env, line, text="Do the work"):
    content = ({"type": "event_msg", "payload": {"type": "user_message", "message": text}}
               if env.session.provider == "codex" else {"type": "user", "message": {"content": text}})
    SessionItem.objects.create(session=env.session, line_num=line, kind=ItemKind.USER_MESSAGE,
                               content=orjson.dumps(content).decode())
    Session.objects.filter(pk=env.session.pk).update(last_line=line)


def stopped_ticks(env):
    env.tick()
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
    env.tick()
    env.tick()


def opened(env, *, by="initial", started=999):
    env.monitor.turn_open = True
    env.monitor.turn_opened_by = by
    env.monitor.turn_started_at = started


def pending(identity, at=1000):
    return PendingRequest(identity, "tool_approval", "Bash", {}, at)


def test_idle_end_is_dropped_without_extra_snapshot_and_wait_is_kept(env, monkeypatch):
    def unexpected(*args):
        raise AssertionError("idle ended must not read the emission snapshot")
    monkeypatch.setattr(runtime_module, "read_session_snapshot", unexpected)
    wait = env.monitor.wait
    stopped_ticks(env)
    env.tick()
    assert env.emissions == env.writes == []
    assert env.monitor.wait is wait and wait.confirming and wait.stopped_since == 100


@pytest.mark.parametrize("outcome", ["replied", "provider_error"])
def test_conclusions_always_emit_then_close_without_followup_end(env, outcome):
    opened(env)
    agent(env, at=999)
    if outcome == "replied":
        append_assistant(env.session, 3, timestamp=datetime.fromtimestamp(1000, UTC))
    else:
        SessionItem.objects.create(session=env.session, line_num=3, kind=ItemKind.API_ERROR,
                                   content='{"isApiErrorMessage":true,"message":{"content":[]}}',
                                   timestamp=datetime.fromtimestamp(1000, UTC))
        Session.objects.filter(pk=env.session.pk).update(last_line=3)
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == outcome
    assert not env.monitor.turn_open and env.monitor.cursor_line == 3
    assert env.order == ["emit", "write"]
    assert isinstance(env.emissions[0].cursor, CursorWrite)
    assert all(isinstance(write, TurnWrite) for write in env.writes)
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert len(env.emissions) == 1
    append_assistant(env.session, 5, "Another answer")
    env.tick()
    assert len(env.emissions) == 2


@pytest.mark.parametrize("by", ["initial", "history"])
def test_known_open_turn_without_prompt_emits_end(env, by):
    opened(env, by=by)
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"
    assert not env.monitor.turn_open


@pytest.mark.parametrize("evidence", ["prompt", "message", "death", "command", "none"])
def test_transition_guard_emits_real_turns_and_closes_pseudo_turns(env, evidence):
    env.clock.advance(1)
    agent(env)
    env.tick()
    wait = env.monitor.wait
    if evidence == "prompt":
        prompt(env, 2)
    elif evidence == "command":
        prompt(env, 2, "<command-name>/rename</command-name><command-message>/rename</command-message>")
    elif evidence == "message":
        append_assistant(env.session, 3, "Working", final=False)
    if evidence == "death":
        env.registry.remove_agent(env.session.id)
    else:
        agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert bool(env.emissions) == (evidence in ("prompt", "message", "death"))
    assert not env.monitor.turn_open
    env.session.refresh_from_db()
    assert env.monitor.turn_start_line == env.session.last_line
    if not env.emissions:
        assert env.monitor.wait is wait
    else:
        assert env.emissions[0].cursor.cursor_at == env.clock.epoch()


@pytest.mark.parametrize("state,previous,at,started,expected", [
    (AgentState.ASSISTANT_TURN, AgentState.USER_TURN, 1001, None, True),
    (AgentState.STARTING, None, 1001, None, True),
    (AgentState.ASSISTANT_TURN, AgentState.ASSISTANT_TURN, 1001, None, False),
    (AgentState.USER_TURN, AgentState.ASSISTANT_TURN, 1001, None, False),
    (AgentState.ASSISTANT_TURN, AgentState.USER_TURN, 1000, None, False),
    (AgentState.ASSISTANT_TURN, AgentState.USER_TURN, 1001, 1001, False),
])
def test_transition_requires_each_state_and_timestamp_condition(env, state, previous, at, started, expected):
    env.monitor.turn_started_at = started
    agent(env, state=state, previous_state=previous, at=at)
    wait = env.monitor.wait
    env.tick()
    assert env.monitor.turn_open is expected
    assert (env.monitor.wait is not wait) is expected


def test_new_turn_uses_pre_move_cursor_and_persists_silent_move_before_step(env, monkeypatch):
    append_assistant(env.session, 5, "Earlier work", final=False)
    agent(env, state=AgentState.USER_TURN)
    env.tick()
    env.monitor.turn_start_line = 2
    env.clock.advance(1)
    agent(env)
    real_step = _wait_reply._SessionWait.step

    def check(wait):
        assert env.monitor.cursor_line == 5 and env.monitor.turn_start_line == 2
        assert [type(item) for item in env.writes] == [TurnWrite, CursorWrite]
        assert env.writes[-1].cursor_at is None
        return real_step(wait)
    monkeypatch.setattr(_wait_reply._SessionWait, "step", check)
    env.tick()
    assert env.monitor.cursor_at == 1000 and env.monitor.wait.last_message is None
    assert env.monitor.wait.scanned_up_to == 5


def test_new_turn_state_survives_later_tick_failure(env, monkeypatch):
    env.clock.advance(1)
    agent(env)
    append_assistant(env.session, 3)
    monkeypatch.setattr(runtime_module, "fit_body", lambda occurrence: (_ for _ in ()).throw(ValueError("fit")))
    with pytest.raises(ValueError, match="fit"):
        env.tick()
    assert env.monitor.turn_open and env.monitor.turn_started_at == 1001
    assert len(env.writes) == 1 and not env.emissions
    assert env.monitor.cursor_line == 0


def test_second_working_transition_preserves_open_turn_origin_and_guard(env):
    opened(env, by="awaiting")
    env.monitor.turn_start_line = 4
    env.clock.advance(1)
    agent(env, state=AgentState.STARTING)
    env.tick()
    wait = env.monitor.wait
    env.clock.advance(1)
    agent(env, previous_state=AgentState.STARTING)
    env.tick()
    assert env.monitor.wait is not wait
    assert (env.monitor.turn_opened_by, env.monitor.turn_start_line, env.monitor.turn_started_at) == ("awaiting", 4, 1002)


@pytest.mark.parametrize("by,kept,renewed", [("history", False, "history"), ("initial", True, "transition"),
                                          ("awaiting", True, "awaiting"), ("transition", True, "transition")])
def test_late_conclusion_preserves_latest_turn_except_history_before_initial_jump(env, by, kept, renewed):
    opened(env, by=by, started=1010)
    env.monitor.initial_last_line, env.monitor.first = 20, True
    prompt(env, 2)
    append_assistant(env.session, 5, timestamp=datetime.fromtimestamp(1005, UTC))
    prompt(env, 8)
    Session.objects.filter(pk=env.session.pk).update(last_line=20)
    agent(env, at=1010)
    env.tick()
    assert env.monitor.turn_open is kept and env.monitor.turn_opened_by == renewed
    assert env.monitor.cursor_line == 20 and not env.monitor.first
    assert env.monitor.turn_start_line == (5 if kept else 0)
    agent(env, state=AgentState.USER_TURN, at=1011)
    stopped_ticks(env)
    assert len(env.emissions) == (2 if kept else 1)


def test_hybrid_late_transition_answer_does_not_emit_empty_end(env):
    prompt(env, 1)
    append_assistant(env.session, 2, timestamp=datetime.fromtimestamp(1001, UTC))
    env.clock.advance(2)
    agent(env)
    env.tick()
    assert env.monitor.turn_open and env.monitor.turn_start_line == 2
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert len(env.emissions) == 1 and not env.monitor.turn_open


def test_ended_reference_blocks_old_crash_prompt_on_next_pseudo_turn(env):
    env.clock.advance(1)
    agent(env)
    prompt(env, 1)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert len(env.emissions) == 1 and env.monitor.turn_start_line == 1
    env.clock.advance(1)
    agent(env)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert len(env.emissions) == 1 and not env.monitor.turn_open


def test_pending_one_per_tick_in_order_survives_cursor_and_keeps_first(env):
    env.monitor.first, env.monitor.initial_last_line = True, 10
    requests = (pending("first"), pending("second"))
    agent(env, state=AgentState.USER_TURN, pending_requests=requests)
    wait = env.monitor.wait
    env.tick()
    assert env.monitor.wait is wait and env.monitor.first and env.monitor.cursor_line == 0
    assert (env.monitor.turn_opened_by, env.monitor.turn_started_at) == ("awaiting", 1000)
    assert env.emissions[0].cursor is None
    env.tick()
    env.tick()
    assert len(env.emissions) == 2
    assert env.monitor.reported_request_ids == {"first", "second"}
    assert all(isinstance(write, TurnWrite) for write in env.writes)
    append_assistant(env.session, 4)
    env.tick()
    assert env.monitor.cursor_line == 10 and not env.monitor.first
    env.tick()
    assert len(env.emissions) == 3
    assert env.monitor.reported_request_ids == {"first", "second"}


def test_pending_reported_ids_are_bounded_to_last_256(env):
    agent(env, state=AgentState.USER_TURN, pending_requests=tuple(pending(str(i)) for i in range(257)))
    for _ in range(257):
        env.tick()
    assert len(env.emissions) == 257
    assert env.monitor.reported_request_ids == {str(i) for i in range(1, 257)}
    assert list(env.monitor.reported_request_order) == [str(i) for i in range(1, 257)]


def test_transcript_precedes_pending_then_awaiting_origin_survives_turn(env):
    opened(env, by="transition", started=1001)
    agent(env, at=1001, pending_requests=(pending("request", 1002),))
    append_assistant(env.session, 1, timestamp=datetime.fromtimestamp(1000, UTC))
    env.tick()
    env.tick()
    assert [p["data"]["reply"]["outcome"] for p in env.payloads()] == ["replied", "awaiting_user_input"]
    assert env.monitor.turn_opened_by == "awaiting" and env.monitor.turn_started_at == 1001
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert env.payloads()[-1]["data"]["reply"]["outcome"] == "ended"


def test_final_snapshot_is_last_database_read_and_before_all_posting(env, monkeypatch):
    opened(env, by="transition")
    agent(env, state=AgentState.USER_TURN)
    prompt(env, 3)
    read = runtime_module.read_session_snapshot
    fit = runtime_module.fit_body
    phases = []

    def read_snapshot(session_id):
        phases.append("snapshot")
        return read(session_id)

    def fit_snapshot(occurrence):
        phases.append("fit")
        return fit(occurrence)
    monkeypatch.setattr(runtime_module, "read_session_snapshot", read_snapshot)
    monkeypatch.setattr(runtime_module, "fit_body", fit_snapshot)
    env.tick()
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
    env.tick()
    with CaptureQueriesContext(connection) as queries:
        env.tick()
    assert phases == ["snapshot", "fit"]
    assert '"history_epoch"' in queries[-1]["sql"] and '"title"' in queries[-1]["sql"]
    assert env.order == ["emit", "write"]
    assert env.monitor.turn_start_line == 3


def test_missing_timestamp_uses_tick_start_and_cursor_time_never_decreases(env):
    env.monitor.cursor_at = 2000
    append_assistant(env.session, 1)
    env.tick()
    assert env.payloads()[0]["timestamp"] == datetime.fromtimestamp(1000, UTC).isoformat()
    assert env.monitor.cursor_at == 2000


def test_size_drop_consumes_conclusion_and_posts_cursor_without_emission(env, monkeypatch):
    opened(env)
    append_assistant(env.session, 1)
    monkeypatch.setattr(runtime_module, "fit_body", lambda occurrence: None)
    env.tick()
    assert not env.emissions and not env.monitor.turn_open and env.monitor.cursor_line == 1
    assert [type(write) for write in env.writes] == [TurnWrite, CursorWrite]


def test_shutdown_drop_preserves_emission_state(env):
    opened(env)
    append_assistant(env.session, 1)
    env.runtime.request_stop()
    env.tick()
    assert env.monitor.turn_open and env.monitor.cursor_line == 0
    assert not env.emissions and not env.writes


def test_title_and_hidden_mute_archive_do_not_filter(env):
    Session.objects.filter(pk=env.session.pk).update(hidden=True, archived=True, mute_on_user_turn=True, title="Changed")
    append_assistant(env.session, 1)
    env.tick()
    assert env.payloads()[0]["data"]["session_title"] == "Changed"


def test_history_delivers_one_old_conclusion_then_only_new_lines(env):
    opened(env, by="history")
    for line in (2, 4, 6):
        append_assistant(env.session, line, f"Old {line}")
    env.monitor.first, env.monitor.initial_last_line = True, 6
    env.tick()
    stopped_ticks(env)
    assert len(env.emissions) == 1 and env.monitor.cursor_line == 6
    assert env.payloads()[0]["data"]["reply"]["line_num"] == 2
    append_assistant(env.session, 8, "New")
    env.tick()
    assert [p["data"]["reply"]["line_num"] for p in env.payloads()] == [2, 8]


def test_dropped_end_message_is_not_carried_into_new_turn_crash(env):
    append_assistant(env.session, 2, "Old commentary", final=False)
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions and env.monitor.wait.last_message is not None
    env.clock.advance(1)
    agent(env)
    prompt(env, 3)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    reply = env.payloads()[0]["data"]["reply"]
    assert reply["outcome"] == "ended" and reply["line_num"] is None and "text" not in reply


def test_idle_gap_between_ticks_still_opens_next_turn(env):
    opened(env)
    agent(env, at=999)
    append_assistant(env.session, 1, timestamp=env.clock.utcnow())
    env.tick()
    env.clock.advance(0.1)
    # No monitor tick observes USER_TURN.
    agent(env, previous_state=AgentState.USER_TURN)
    prompt(env, 2)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert [p["data"]["reply"]["outcome"] for p in env.payloads()] == ["replied", "ended"]


def test_watcher_lag_answer_arrives_after_second_turn_already_stopped(env, tmp_path):
    opened(env)
    path = tmp_path / "transcript.jsonl"
    path.write_text("unindexed bytes")
    Session.objects.filter(pk=env.session.pk).update(file_path=str(path), last_offset=0)
    env.clock.advance(1)
    agent(env)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions
    prompt(env, 1)
    append_assistant(env.session, 2, timestamp=datetime.fromtimestamp(1000, UTC))
    prompt(env, 3)
    Session.objects.filter(pk=env.session.pk).update(last_offset=path.stat().st_size)
    env.tick()
    assert env.monitor.turn_open and env.monitor.turn_start_line == 2
    stopped_ticks(env)
    assert [p["data"]["reply"]["outcome"] for p in env.payloads()] == ["replied", "ended"]


def test_interrupted_open_turn_keeps_origin_during_pseudo_transition(env):
    opened(env)
    agent(env, state=AgentState.USER_TURN)
    env.tick()
    env.clock.advance(1)
    agent(env)
    env.tick()
    assert env.monitor.turn_opened_by == "initial"
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "ended"


def test_stop_between_transition_and_step_gets_full_new_flush_window(env, monkeypatch):
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    old_wait = env.monitor.wait
    assert old_wait.confirming
    env.clock.advance(1)
    agent(env)
    real_step = _wait_reply._SessionWait.step

    def stop_then_step(wait):
        agent(env, state=AgentState.USER_TURN)
        return real_step(wait)
    monkeypatch.setattr(_wait_reply._SessionWait, "step", stop_then_step)
    prompt(env, 1)
    env.tick()
    assert env.monitor.wait is not old_wait and not env.emissions
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS - 0.1)
    env.tick()
    assert not env.emissions
    env.clock.advance(0.1)
    env.tick()
    env.tick()
    assert len(env.emissions) == 1


def test_two_crashes_without_assistant_lines_have_distinct_ids(env):
    for line in (1, 2):
        env.clock.advance(1)
        agent(env)
        prompt(env, line)
        env.tick()
        agent(env, state=AgentState.USER_TURN)
        stopped_ticks(env)
    assert len(env.emissions) == 2
    assert env.emissions[0].event_id != env.emissions[1].event_id


def test_awaiting_stop_carries_old_message_but_closes_at_tick_time(env):
    append_assistant(env.session, 1, "Question", final=False, timestamp=datetime.fromtimestamp(990, UTC))
    agent(env, state=AgentState.USER_TURN, pending_requests=(pending("question", 1000),))
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    stopped_ticks(env)
    assert [p["data"]["reply"]["outcome"] for p in env.payloads()] == ["awaiting_user_input", "ended"]
    assert env.payloads()[-1]["data"]["reply"]["text"] == "Question"
    assert not env.monitor.turn_open and env.monitor.cursor_at > 1000


def test_final_snapshot_captures_prompt_committed_during_guard_query(env, monkeypatch):
    opened(env, by="transition")
    agent(env, state=AgentState.USER_TURN)
    prompt(env, 1)
    real_guard = runtime_module.first_non_command_prompt

    def guard(*args):
        result = real_guard(*args)
        prompt(env, 5, "Late prompt from this crashed turn")
        return result
    monkeypatch.setattr(runtime_module, "first_non_command_prompt", guard)
    stopped_ticks(env)
    assert env.monitor.turn_start_line == 5
    from twicc.mcp.events.catalog import event_id
    assert env.emissions[0].event_id == event_id(env.row.id, "ended", "last_line:0:5")


def test_pending_selection_uses_tick_start_registry_snapshot(env, monkeypatch):
    agent(env, state=AgentState.USER_TURN, pending_requests=(pending("before"),))
    real_step = _wait_reply._SessionWait.step

    def change_requests(wait):
        agent(env, state=AgentState.USER_TURN, pending_requests=(pending("after"),))
        return real_step(wait)
    monkeypatch.setattr(_wait_reply._SessionWait, "step", change_requests)
    env.tick()
    assert env.monitor.reported_request_ids == {"before"}


def test_posting_failure_applies_no_emission_state_and_prepared_wait_failure_never_posts(env, monkeypatch):
    opened(env)
    append_assistant(env.session, 1)

    def fail(*args, **kwargs):
        raise RuntimeError("boundary")
    monkeypatch.setattr(env.runtime, "_new_wait", fail)
    with pytest.raises(RuntimeError, match="boundary"):
        env.tick()
    assert not env.emissions and not env.writes and env.monitor.cursor_line == 0
    assert env.monitor.turn_open


def test_no_row_ended_uses_epoch_zero_and_retains_guard_reference(env):
    opened(env)
    env.monitor.turn_start_line = 7
    env.session.delete()
    env.tick()
    env.clock.advance(_wait_reply.SESSION_ROW_GRACE_SECONDS + _wait_reply.AGENT_FLUSH_SECONDS)
    env.tick()
    assert len(env.emissions) == 1 and env.monitor.turn_start_line == 7
    assert env.payloads()[0]["data"]["session_title"] is None


@pytest.mark.parametrize("env", ["codex"], indirect=True)
@pytest.mark.parametrize("command", ["/compact", "/goal clear", "/plan"])
def test_codex_injected_command_guard_drops_pseudo_turn(env, command):
    env.clock.advance(1)
    agent(env, state=AgentState.STARTING)
    env.tick()
    content = {
        "type": "event_msg", "payload": {"type": "user_message", "message": command},
        "twiccOriginalContent": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": command},
        ]},
    }
    SessionItem.objects.create(session=env.session, line_num=1, kind=ItemKind.USER_MESSAGE,
                               content=orjson.dumps(content).decode())
    Session.objects.filter(pk=env.session.pk).update(last_line=1)
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions and not env.monitor.turn_open
    assert env.monitor.turn_start_line == 1


def test_earlier_conclusion_never_moves_guard_reference_back(env):
    opened(env, by="initial", started=1010)
    env.monitor.turn_start_line = 10
    append_assistant(env.session, 3, timestamp=datetime.fromtimestamp(1000, UTC))
    env.tick()
    assert env.monitor.turn_start_line == 10 and env.monitor.turn_opened_by == "transition"


def test_pre_post_sink_failure_preserves_conclusion_state(env):
    opened(env)
    append_assistant(env.session, 1)

    def reject(emission):
        assert env.monitor.cursor_line == 0 and env.monitor.turn_open
        raise RuntimeError("cannot enqueue")
    env.runtime.emission_sink = reject
    with pytest.raises(RuntimeError, match="cannot enqueue"):
        env.tick()
    assert not env.emissions and not env.writes
    assert env.monitor.cursor_line == 0 and env.monitor.turn_open
