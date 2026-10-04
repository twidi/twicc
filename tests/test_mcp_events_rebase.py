"""History epochs protect active and durable event state through replacements."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_mcp_events_turns import agent, opened, stopped_ticks
from tests.test_mcp_events_writer import apply
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import McpEventSubscription, Session, SessionItem
from twicc.mcp import events
from twicc.mcp.events import runtime as runtime_module
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.runtime import CursorWrite, RebaseWrite, TurnWrite
from twicc.providers.codex.rollout_migration import (
    ReplaceCodexHistoryJob,
    _begin_replace_codex_history,
    _finish_replace_codex_history,
    _insert_replace_codex_history_chunk,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.parametrize("env", ["codex"], indirect=True)]
env = test_mcp_events_turns.env


@pytest.fixture(autouse=True)
def writer_lock(monkeypatch):
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr(events, "_runtime", None)


def rebuild(env, *, epoch=1, end=0, ready=False):
    Session.objects.filter(pk=env.session.pk).update(
        history_epoch=epoch, last_line=end, last_offset=end * 100,
        compute_version=env.session.compute_version if ready else None,
    )


def job(env, end=3):
    return ReplaceCodexHistoryJob(Provider.CODEX, env.session.id, [], end * 100, end, 42.0, None)


@pytest.mark.parametrize("end", [2, 40])
def test_pending_rebase_reads_once_per_tick_then_moves_all_lines_to_new_end(env, monkeypatch, end):
    monitor = env.monitor
    monitor.cursor_line = 10
    monitor.initial_last_line = 20
    monitor.turn_start_line = 9
    monitor.first = True
    monitor.reported_request_ids.add("question")
    monitor.reported_request_order.append("question")
    opened(env)
    wait = monitor.wait
    monkeypatch.setattr(wait, "step", lambda: pytest.fail("pending monitor must not scan"))
    rebuild(env)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    assert monitor.pending_rebase
    for _ in range(2):
        with CaptureQueriesContext(connection) as queries:
            env.runtime._tick()
        assert len(queries) == 1 and '"history_epoch"' in queries[0]["sql"]
    assert env.emissions == env.writes == [] and monitor.wait is wait
    rebuild(env, end=end, ready=True)
    env.runtime._tick()
    assert len(env.writes) == 1 and isinstance(env.writes[0], RebaseWrite)
    assert (monitor.cursor_line, monitor.initial_last_line, monitor.turn_start_line, monitor.numbering) == (end, end, end, 1)
    assert not monitor.first and not monitor.pending_rebase and not monitor.turn_open
    assert monitor.turn_started_at is None and monitor.cursor_at == 1000
    assert monitor.wait is not wait and monitor.wait.since_line_num == end
    assert monitor.reported_request_ids == {"question"} and list(monitor.reported_request_order) == ["question"]
    asyncio.run(apply(env.runtime, *env.writes))
    env.row.refresh_from_db()
    assert (env.row.cursor_line, env.row.initial_last_line, env.row.turn_start_line, env.row.numbering) == (end, end, end, 1)
    assert env.row.cursor_at == 1000


@pytest.mark.parametrize("state", [AgentState.STARTING, AgentState.ASSISTANT_TURN])
def test_rebase_opens_current_work_even_when_transition_already_observed(env, state):
    agent(env, state=state, at=900)
    opened(env, started=900)
    rebuild(env, end=8, ready=True)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    assert (env.monitor.turn_open, env.monitor.turn_started_at, env.monitor.turn_opened_by) == (True, 900, "initial")
    assert env.monitor.turn_start_line == 8 and env.monitor.cursor_at == 1000


@pytest.mark.parametrize("outcome", ["replied", "awaiting", "ended", "readiness_drop", "guard_drop"])
def test_final_snapshot_epoch_mismatch_suppresses_every_emission_phase_write(env, monkeypatch, outcome):
    from tests.test_mcp_events_turns import pending

    opened(env, by="transition" if outcome == "guard_drop" else "initial")
    agent(env, state=AgentState.USER_TURN, at=999,
          pending_requests=(pending("question"),) if outcome == "awaiting" else ())
    if outcome == "replied":
        append_assistant(env.session, 1)
    read = runtime_module.read_session_snapshot

    def begin_before_final_read(identity):
        rebuild(env, ready=outcome != "readiness_drop")
        return read(identity)

    monkeypatch.setattr(runtime_module, "read_session_snapshot", begin_before_final_read)
    stopped_ticks(env)
    assert env.monitor.pending_rebase and env.monitor.numbering == 0
    assert env.emissions == env.writes == []
    assert env.monitor.turn_open and env.monitor.cursor_line == 0
    assert not env.monitor.reported_request_ids


def test_new_turn_writes_precede_rebase_and_are_superseded(env, monkeypatch):
    env.clock.advance(1)
    agent(env)
    append_assistant(env.session, 1)
    read = runtime_module.read_session_snapshot

    def begin(identity):
        rebuild(env, ready=True, end=2)
        return read(identity)

    monkeypatch.setattr(runtime_module, "read_session_snapshot", begin)
    env.tick()
    assert env.monitor.pending_rebase and not env.emissions
    assert len(env.writes) == 1 and isinstance(env.writes[0], TurnWrite)
    env.runtime._tick()
    assert isinstance(env.writes[-1], RebaseWrite)
    asyncio.run(apply(env.runtime, *env.writes))
    env.row.refresh_from_db()
    assert env.row.numbering == 1 and env.row.turn_start_line == 2 and env.row.turn_opened_by == "initial"


def test_lost_wakeup_backstop_detects_cursor_past_shorter_history(env):
    env.monitor.cursor_line = 50
    env.monitor.wait = env.runtime._new_wait(env.monitor)
    rebuild(env, end=2, ready=True)
    env.clock.advance(4.99)
    env.runtime._tick()
    assert env.monitor.numbering == 0 and not env.writes
    env.clock.advance(.01)
    env.runtime._tick()
    assert env.monitor.numbering == 1 and env.monitor.cursor_line == 2 and not env.emissions


@pytest.mark.parametrize("numbering", [0, None])
def test_load_and_reload_recover_arrival_before_begin_or_mid_replacement(env, numbering):
    McpEventSubscription.objects.filter(pk=env.row.pk).update(numbering=numbering, cursor_line=99)
    rebuild(env)
    env.runtime._load_monitors()
    assert env.runtime.monitors[env.row.id].pending_rebase
    # A supervisor reload or process restart loses only in-memory state.
    env.runtime._load_monitors()
    monitor = env.runtime.monitors[env.row.id]
    rebuild(env, end=4, ready=True)
    env.runtime._tick()
    assert monitor.numbering == 1 and monitor.cursor_line == 4
    assert env.writes[-1].old_numbering == numbering
    asyncio.run(apply(env.runtime, *env.writes))
    env.row.refresh_from_db()
    assert env.row.numbering == 1 and env.row.cursor_line == 4


def test_partial_replacement_repair_keeps_pending_until_current_compute(env):
    _begin_replace_codex_history(job(env))
    _insert_replace_codex_history_chunk(env.session.id, [(1, '{}')])
    env.runtime._load_monitors()
    assert env.runtime.monitors[env.row.id].pending_rebase
    _begin_replace_codex_history(job(env, 2))
    assert not SessionItem.objects.filter(session=env.session).exists()
    _finish_replace_codex_history(job(env, 2))
    env.runtime._tick()
    assert env.runtime.monitors[env.row.id].pending_rebase and not env.writes
    Session.objects.filter(pk=env.session.pk).update(compute_version=env.session.compute_version)
    env.runtime._tick()
    assert env.runtime.monitors[env.row.id].numbering == 2
    assert env.runtime.monitors[env.row.id].cursor_line == 2


def test_two_overlapping_rebases_keep_fifo_cas_and_reject_old_cursor_lines(env):
    rebuild(env, end=3, ready=True)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    first = env.writes[-1]
    rebuild(env, epoch=2, end=1, ready=True)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.runtime._tick()
    second = env.writes[-1]
    assert (first.old_numbering, first.numbering, second.old_numbering, second.numbering) == (0, 1, 1, 2)
    asyncio.run(apply(env.runtime, first, second,
                      CursorWrite(*env.monitor.generation, 1, 99, 1100)))
    env.row.refresh_from_db()
    assert (env.row.numbering, env.row.cursor_line, env.row.cursor_at) == (2, 1, 1100)
    env.runtime._load_monitors()
    env.runtime._tick()
    assert not env.emissions and env.runtime.monitors[env.row.id].numbering == 2


@pytest.mark.parametrize("loss", ["dropped", "cas_rejected"])
def test_lost_rebase_write_keeps_memory_new_and_blocks_lines_until_reload_heals(env, loss):
    with asyncio.Runner() as runner:
        rebuild(env, end=3, ready=True)
        env.runtime.rebase(env.session.id)
        env.runtime._drain_commands()
        env.runtime._tick()
        # Drop the CAS. An ordinary write must not silently adopt its numbering.
        lost = env.writes.pop()
        assert isinstance(lost, RebaseWrite)
        if loss == "cas_rejected":
            runner.run(apply(env.runtime, lost._replace(old_numbering=99)))
        runner.run(apply(env.runtime, CursorWrite(*env.monitor.generation, 1, 8, 1100)))
        env.row.refresh_from_db()
        assert (env.row.numbering, env.row.cursor_line, env.row.cursor_at) == (0, 0, 1100)
        assert (env.monitor.numbering, env.monitor.cursor_line) == (1, 3)
        env.runtime._load_monitors()
        env.runtime._tick()
        healed = env.runtime.monitors[env.row.id]
        assert healed.numbering == 1 and healed.cursor_at == 1100
        runner.run(apply(env.runtime, *env.writes))
        env.row.refresh_from_db()
        assert (env.row.numbering, env.row.cursor_line, env.row.cursor_at) == (1, 3, 1100)


def test_dormant_command_reads_but_wake_applies_before_scanning(env, monkeypatch):
    env.clock.advance(3600)
    env.runtime._tick()
    assert env.monitor.dormant
    rebuild(env, end=4, ready=True)
    env.runtime.rebase(env.session.id)
    with CaptureQueriesContext(connection) as queries:
        env.runtime._drain_commands()
    assert len(queries) == 1 and env.monitor.pending_rebase
    env.runtime._tick()
    assert not env.writes
    snapshot = SubscriptionSnapshot.from_row(env.row)._replace(refresh_before=env.clock.utcnow() + timedelta(hours=1))
    env.runtime.update(snapshot)
    env.runtime._drain_commands()
    monkeypatch.setattr(env.monitor.wait, "step", lambda: pytest.fail("wake must rebase before scanning"))
    env.runtime._tick()
    assert not env.monitor.pending_rebase and env.monitor.cursor_line == 4


def test_missing_row_never_clears_existing_pending_rebase(env):
    rebuild(env)
    env.runtime.rebase(env.session.id)
    env.runtime._drain_commands()
    env.session.delete()
    env.runtime._tick()
    assert env.monitor.pending_rebase and not env.writes


def test_begin_epoch_reset_is_atomic_and_wakeup_runs_only_after_commit(env):
    observed = []

    def wake(identity):
        snapshot = runtime_module.read_session_snapshot(identity)
        observed.append((snapshot.history_epoch, snapshot.last_line, snapshot.ready))

    events.set_runtime(SimpleNamespace(rebase=wake))
    assert events.get_runtime() is not None
    with transaction.atomic():
        _begin_replace_codex_history(job(env))
        assert observed == []
        env.session.refresh_from_db()
        assert env.session.history_epoch == 1 and env.session.last_offset == 0 and env.session.compute_version is None
    assert observed == [(1, 0, False)]
    with pytest.raises(RuntimeError), transaction.atomic():
        _begin_replace_codex_history(job(env))
        raise RuntimeError("rollback")
    env.session.refresh_from_db()
    assert env.session.history_epoch == 1 and observed == [(1, 0, False)]
    _begin_replace_codex_history(job(env))
    assert observed == [(1, 0, False), (2, 0, False)]


def test_offline_or_disabled_runtime_begin_has_no_wakeup_dependency(env, monkeypatch):
    monkeypatch.setenv("TWICC_NO_MCP", "1")
    assert events.get_runtime() is None
    events.rebase(env.session.id)
    _begin_replace_codex_history(job(env))
    env.session.refresh_from_db()
    assert env.session.history_epoch == 1
