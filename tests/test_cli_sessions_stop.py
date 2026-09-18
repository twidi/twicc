"""``twicc sessions stop`` — selection, and what it refuses to do.

The command stops agents, so the interesting part is not the stopping (that
mechanism is shared with ``processes stop`` and already covered) but **who
ends up in the batch**. Three rules decide it, and each one is a way the
command could quietly do too much or too little:

- only sessions that actually have a process — a stopped one has nothing to
  stop, which is what makes a bare ``sessions stop`` bounded by what is alive
  rather than by how many sessions exist;
- hidden ones included, because the orchestration workers are hidden by
  convention and "stop everything running" that spares them is a lie;
- explicit ids bypass the filters, as in ``sessions get``.
"""

from __future__ import annotations

import orjson
import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import sessions_stop
from twicc.core.models import ProcessRun, Project, Session


TWICC_PID = 4242


@pytest.fixture
def project(db):
    return Project.objects.create(id="stop-project", directory="/tmp/stop")


@pytest.fixture
def server(monkeypatch):
    """A reachable backend at :data:`TWICC_PID`, and a stopper that records.

    The drop-request round trip is cut: this file is about selection, and
    keeping the transport real would make every test depend on a live server.
    """
    monkeypatch.setattr(
        "twicc.cli._drop_request.transport.ensure_server_available", lambda: None,
    )
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc",
        lambda: type("I", (), {"pid": TWICC_PID})(),
    )
    seen: dict = {}

    def fake_stop(ids, *, timeout, force, twicc_pid):
        seen["ids"] = list(ids)
        seen["force"] = force
        return [{"session_id": sid, "status": "stopped"} for sid in ids]

    monkeypatch.setattr("twicc.cli._stop_batch.stop_session_ids", fake_stop)
    return seen


def make_session(project, sid, **kwargs):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        created_at="2026-09-18T10:00:00Z", user_message_count=1, **kwargs,
    )


def make_run(session_id, state=AgentState.ASSISTANT_TURN, **kwargs):
    return ProcessRun.objects.create(
        session_id=session_id, provider="claude_code", twicc_pid=TWICC_PID,
        state=state.value, started_at=timezone.now(),
        last_state_change_at=timezone.now(), **kwargs,
    )


def run(capsysbinary, *args, **kwargs):
    kwargs.setdefault("timeout", 30)
    sessions_stop.main(list(args), **kwargs)
    return orjson.loads(capsysbinary.readouterr().out)


# ---------------------------------------------------------------------------
# Who ends up in the batch
# ---------------------------------------------------------------------------


def test_a_bare_call_stops_everything_running(project, server, capsysbinary):
    """And only that: the blast radius is the live set, not the listing."""
    make_session(project, "busy")
    make_run("busy")
    make_session(project, "idle")
    make_run("idle", AgentState.USER_TURN)
    make_session(project, "gone")

    run(capsysbinary)

    assert set(server["ids"]) == {"busy", "idle"}


def test_a_stopped_session_is_not_in_the_batch(project, server, capsysbinary):
    """A DEAD row is not a process. Sending it would report a stop that
    stopped nothing."""
    make_session(project, "stopped")
    make_run("stopped", AgentState.DEAD)

    assert run(capsysbinary) == []
    assert "ids" not in server


def test_hidden_sessions_are_stopped_too(project, server, capsysbinary):
    """`sessions` hides them when listing; sparing them here would leave every
    orchestration worker running, since they are hidden by convention."""
    make_session(project, "worker", hidden=True)
    make_run("worker")

    run(capsysbinary)

    assert server["ids"] == ["worker"]


def test_an_archived_session_is_still_reachable_by_id(project, server, capsysbinary):
    """Archiving kills the agent, so this cannot happen in production — the
    test pins that the archived default is not silently re-applied here."""
    make_session(project, "filed", archived=True)
    make_run("filed")

    run(capsysbinary)

    assert server["ids"] == ["filed"]


def test_explicit_ids_bypass_the_filters(project, server, capsysbinary):
    """Naming an id is the caller saying they know which one, as in
    ``sessions get``."""
    make_session(project, "one")
    make_run("one")
    make_session(project, "two")
    make_run("two")

    run(capsysbinary, "two", provider="codex")

    assert server["ids"] == ["two"]


def test_an_explicit_id_with_no_process_is_dropped(project, server, capsysbinary):
    """The live check applies to both paths, not only to the filtered one."""
    make_session(project, "one")

    assert run(capsysbinary, "one") == []
    assert "ids" not in server


def test_a_state_filter_narrows_the_batch(project, server, capsysbinary):
    make_session(project, "busy")
    make_run("busy")
    make_session(project, "blocked")
    make_run("blocked", AgentState.ASSISTANT_TURN, awaiting_user_input=True)

    run(capsysbinary, state=["awaiting_user_input"])

    assert server["ids"] == ["blocked"]


def test_force_travels_to_the_stopper(project, server, capsysbinary):
    make_session(project, "wedged")
    make_run("wedged")

    run(capsysbinary, force=True)

    assert server["force"] is True


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_stopping_the_dead_is_refused_not_emptied(project, server, capsysbinary):
    """``--state dead`` selects sessions with no process. Honouring it as a
    silent no-op would hide a mistake worth naming."""
    import typer

    with pytest.raises(typer.Exit) as exc:
        sessions_stop.main([], timeout=30, state=["dead"])

    assert exc.value.exit_code == 1
    assert "nothing to do with" in capsysbinary.readouterr().err.decode()


def test_a_non_positive_timeout_is_refused(project, server, capsysbinary):
    import typer

    with pytest.raises(typer.Exit) as exc:
        sessions_stop.main([], timeout=0)

    assert exc.value.exit_code == 1


def test_no_backend_means_nothing_to_stop(project, monkeypatch, capsysbinary):
    """Exit 2, like every command that needs a live server — not an empty
    success, which would read as "nothing was running"."""
    import typer

    monkeypatch.setattr(
        "twicc.cli._drop_request.transport.ensure_server_available", lambda: None,
    )
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)

    with pytest.raises(typer.Exit) as exc:
        sessions_stop.main([], timeout=30)

    assert exc.value.exit_code == 2


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_flags_travel_from_the_command_line(project, server):
    """Calling ``main()`` directly leaves the Typer wiring untested, and an
    option that never arrives is a silent no-op."""
    from typer.testing import CliRunner

    from twicc.cli import app

    make_session(project, "busy")
    make_run("busy")
    make_session(project, "blocked")
    make_run("blocked", AgentState.ASSISTANT_TURN, awaiting_user_input=True)

    result = CliRunner().invoke(
        app, ["sessions", "stop", "--state", "awaiting_user_input", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert server["ids"] == ["blocked"]
    assert server["force"] is True


def test_the_live_set_is_narrowed_in_sql_not_in_python(project, server, monkeypatch, capsysbinary):
    """The filter must reach the database, not just the result.

    Dropping ``active=True`` from the queryset leaves the outcome identical —
    the live check downstream removes the same rows — so only the *size* of
    what crosses the boundary catches it. On a real base that is thousands of
    ids in one ``IN`` clause, against SQLite's bound-variable ceiling.
    """
    from twicc.cli import _process_state

    for i in range(6):
        make_session(project, f"gone-{i}")
    make_session(project, "busy")
    make_run("busy")

    seen: dict = {}
    real = _process_state.load_process_rows

    def spy(ids, pid):
        # ``ids=None`` is the state filter loading the whole live set; the
        # call under test is the one that hands over the chosen targets.
        if ids is not None:
            seen["n"] = len(list(ids))
        return real(ids, pid)

    monkeypatch.setattr(_process_state, "load_process_rows", spy)

    run(capsysbinary)

    assert seen["n"] == 1
