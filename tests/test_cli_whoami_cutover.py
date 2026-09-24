"""`whoami`: today's object until the date, the `session self` payload after."""

from __future__ import annotations

import asyncio
from datetime import datetime

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.cli.whoami import whoami_cmd
from twicc.core.models import ProcessRun, Project, Session, SessionType

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001
TWICC_PID = 6161
LEGACY_KEYS = {
    "session_id", "title", "project_id", "project_directory", "current_working_directory",
    "artifacts_dir", "scratch_dir", "agent_settings", "session", "process",
}


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


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
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc_or_exit", lambda: info)
    return session


def run(capsysbinary, *, slim=False, full=False):
    whoami_cmd(slim=slim, full=full)
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


def session_self(capsysbinary, **flags):
    cli_session.main("who-me", **flags)
    return orjson.loads(capsysbinary.readouterr().out)


def test_before_no_flag_is_today_s_object_with_a_notice(before, me, capsysbinary):
    data, err = run(capsysbinary)
    assert LEGACY_KEYS <= set(data)
    assert len(data["process"]) == 9
    assert "`whoami` returns the `session self` payload" in err


@pytest.mark.parametrize("flag", ["slim", "full"])
def test_a_flag_is_session_self_on_both_sides(monkeypatch, me, capsysbinary, flag):
    for pinned in (FUTURE, PAST):
        monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
        data, err = run(capsysbinary, **{flag: True})
        assert data == session_self(capsysbinary, **{flag: True})
        assert err == ""


def test_after_no_flag_is_session_self_reduced(after, me, capsysbinary):
    data, err = run(capsysbinary)
    assert data == session_self(capsysbinary)
    assert set(data["process"]) == {"state"}
    assert err == ""


def test_both_flags_exit_2_outside_a_session(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=True, full=True)
    assert exc.value.exit_code == 2


def test_outside_a_session_exits_1(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=False, full=False)
    assert exc.value.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_mcp_before_the_date_keeps_today_s_object_silently(monkeypatch):
    """Review focus 1. The MCP envelope carries no warnings, so the notice
    recorder itself is watched."""
    from twicc.mcp import server as mcp_server

    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    recorded = []
    monkeypatch.setattr(_output, "_record_notice", recorded.append)
    project = Project.objects.create(id="-tmp-who-mcp", directory="/tmp/who-mcp")
    Session.objects.create(
        id="who-mcp", project=project, provider="claude_code", file_path="m.jsonl",
        type=SessionType.SESSION,
    )
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc_or_exit", lambda: type("I", (), {"pid": 1})(),
    )
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id="who-mcp"))
    assert result["exit_code"] == 0, result
    assert result["result"]["session_id"] == "who-mcp"
    assert "agent_settings" in result["result"]
    assert recorded == []
