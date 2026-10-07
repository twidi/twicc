"""`whoami`: the `session self` payload, reduced by default."""

from __future__ import annotations

import asyncio

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.cli.whoami import whoami_cmd
from twicc.core.models import ProcessRun, Project, Session, SessionType

TWICC_PID = 6161


@pytest.fixture
def me(db, monkeypatch):
    project = Project.objects.create(id="-tmp-who", directory="/tmp/who")
    session = Session.objects.create(
        id="who-me", project=project, provider="claude_code", file_path="w.jsonl",
        type=SessionType.SESSION,
    )
    now = timezone.now()
    ProcessRun.objects.create(
        session_id=session.id, provider="claude_code", twicc_pid=TWICC_PID,
        state=AgentState.ASSISTANT_TURN.value, started_at=now, last_state_change_at=now,
    )
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: session)
    info = type("I", (), {"pid": TWICC_PID})()
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: info)
    return session


def run(capsysbinary, *, slim=False, full=False):
    whoami_cmd(slim=slim, full=full)
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


def session_self(capsysbinary, **flags):
    cli_session.main("who-me", **flags)
    return orjson.loads(capsysbinary.readouterr().out)


def test_no_flag_is_session_self_reduced(me, capsysbinary):
    data, err = run(capsysbinary)
    assert data == session_self(capsysbinary)
    assert data["id"] == "who-me"
    assert set(data["process"]) == {"state", "background_work_in_progress"}
    assert err == ""


def test_slim_is_an_accepted_no_op(me, capsysbinary):
    reduced, _ = run(capsysbinary)
    slim, err = run(capsysbinary, slim=True)
    assert slim == reduced
    assert err == ""


def test_full_is_session_self_in_full(me, capsysbinary):
    data, err = run(capsysbinary, full=True)
    assert data == session_self(capsysbinary, full=True)
    assert "layout" in data
    assert len(data["process"]) == 6
    assert err == ""


def test_slim_is_a_no_op_with_full(me, capsysbinary):
    data, err = run(capsysbinary, slim=True, full=True)
    assert data == session_self(capsysbinary, full=True)
    assert err == ""


def test_both_flags_exit_1_outside_a_session(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=True, full=True)
    assert exc.value.exit_code == 1


def test_outside_a_session_exits_1(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=False, full=False)
    assert exc.value.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_mcp_returns_the_session_self_payload(monkeypatch):
    from twicc.mcp import server as mcp_server

    project = Project.objects.create(id="-tmp-who-mcp", directory="/tmp/who-mcp")
    Session.objects.create(
        id="who-mcp", project=project, provider="claude_code", file_path="m.jsonl",
        type=SessionType.SESSION,
    )
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: type("I", (), {"pid": 1})())
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id="who-mcp"))
    assert result["exit_code"] == 0, result
    assert result["result"]["id"] == "who-mcp"
    assert "session_id" not in result["result"]
