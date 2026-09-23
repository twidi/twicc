"""Tests for the retirement of the ``process`` / ``processes`` commands.

Design: docs/plans/2026-09-23-process-commands-removal-design.md. The seven
commands work with a notice until ``LISTING_CUTOVER`` and refuse to run from
then on (exit 64), on every channel. Pinned values are **naive**, like the
constant. What is evaluated at import (help texts, ``hidden=``,
``MCP_EXCLUDED_ROOTS``) cannot be moved by a fixture: those assertions read the
real clock, and the "after the restart" tests patch the cached registry.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from types import SimpleNamespace

import orjson
import pytest
from django.utils import timezone
from mcp import types as mcp_types

from twicc.agent.states import AgentState
from twicc.cli import _output
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.mcp import server
from twicc.mcp.tools import RETIRED_MCP_TOOLS, tools_by_name
from twicc.rpc.invoker import InvocationResult, invoke
from tests.test_rpc_auth import (  # noqa: F401 — fixtures, used by name
    _login,
    _post,
    _record,
    client,
    protected,
    tokens_store,
)

PAST = datetime(2000, 1, 1)     # noqa: DTZ001 — naive, like the constant
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001 — naive, like the constant
TWICC_PID = 5151

# Each retired command, as `invoke()` argv, with the key naming it.
SEVEN = {
    "processes": ["processes"],
    "processes get": ["processes", "get", "rm-live"],
    "processes stop": ["processes", "stop", "rm-live", "--timeout", "1"],
    "processes wait": ["processes", "wait", "rm-live", "user_turn", "--timeout", "1"],
    "process": ["process", "rm-live"],
    "process stop": ["process", "rm-live", "stop", "--timeout", "1"],
    "process wait": ["process", "rm-live", "wait", "user_turn", "--timeout", "1"],
}

# The prose pattern of the design's acceptance check.
RETIRED_PATTERN = re.compile(
    r"(twicc|\$TWICC) process(es)?\b|`processes([ `])|\bprocesses (get|stop|wait|--)"
    r"|`process <|\bprocess <[A-Z_]+> (stop|wait)|\bprocess(es)?/(get|stop|wait)\b"
    r"|mcp__twicc__process|twicc-process(es)?\b|\bprocess(es)? wait\b"
    r"|\bprocess(es)? stop\b|\bprocesses get\b"
)


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


@pytest.fixture
def no_backend(monkeypatch):
    """No live backend: the bodies that do run exit fast instead of polling.

    Both probes are patched. ``settings_test`` does not isolate the data dir, so
    an unpatched drop-request heartbeat sees the developer's own running TwiCC
    and a stop would be delivered to it — and the outcome would then depend on
    whether one happens to run.
    """
    from twicc.cli._drop_request.discovery import ServerDownError

    def server_down():
        raise ServerDownError("TwiCC is not running.")

    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    monkeypatch.setattr("twicc.cli._drop_request.transport.ensure_server_available", server_down)


@pytest.fixture
def live_backend(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc",
        lambda: type("I", (), {"pid": TWICC_PID})(),
    )


@pytest.fixture
def untouched(monkeypatch):
    """Fail loudly if a retired command's body is reached."""
    def boom(*args, **kwargs):
        raise AssertionError("a retired command's body ran")

    for target in (
        "twicc.cli.processes.main", "twicc.cli.processes_get.main",
        "twicc.cli.processes_stop.stop_cmd", "twicc.cli.processes_wait.wait_cmd",
        "twicc.cli.process.main", "twicc.cli.process_stop.stop_cmd",
        "twicc.cli.process_wait.wait_cmd",
    ):
        monkeypatch.setattr(target, boom)


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-twicc-retire", directory="/tmp/twicc-retire")


def _session(project, sid, **extra):
    values = {
        "id": sid, "project": project, "provider": "claude_code",
        "file_path": f"{sid}.jsonl", "type": SessionType.SESSION,
        "created_at": timezone.now(), "user_message_count": 1, "last_line": 3,
    }
    return Session.objects.create(**(values | extra))


def _live_run(session_id, state=AgentState.ASSISTANT_TURN):
    now = timezone.now()
    return ProcessRun.objects.create(
        session_id=session_id, provider="claude_code", twicc_pid=TWICC_PID,
        state=state.value, started_at=now, last_state_change_at=now, agent_pid=4242,
    )


def _mcp(coro):
    return asyncio.run(coro)


def _call_tool(name, arguments):
    params = mcp_types.CallToolRequestParams(name=name, arguments=arguments)
    return _mcp(server._call_tool(SimpleNamespace(request=None), params))


# --- the seven, before and after --------------------------------------------


# What each command answers before the date with no backend running: its own
# behaviour, untouched by the notice placed in front of it.
BEFORE_EXIT_CODES = {
    "processes": 1, "processes get": 1, "processes stop": 2, "processes wait": 2,
    "process": 1, "process stop": 2, "process wait": 2,
}


@pytest.mark.parametrize("command", SEVEN)
def test_before_each_command_runs_with_one_notice(before, no_backend, project, command):
    result = invoke(SEVEN[command])
    assert result.exit_code == BEFORE_EXIT_CODES[command], result.error
    removal = [w for w in result.warnings if "stops working" in w]
    assert len(removal) == 1, result.warnings
    assert f"`twicc {command}`" in removal[0]
    assert "2200-01-01" in removal[0]
    assert f"`twicc {_output.RETIRED_COMMANDS[command]}`" in removal[0]


@pytest.mark.parametrize("command", SEVEN)
def test_after_each_command_refuses_without_running(after, untouched, project, command):
    result = invoke(SEVEN[command])
    assert result.exit_code == 64, result.error
    assert f"`twicc {command}` was removed on 2000-01-01" in result.error
    assert f"`twicc {_output.RETIRED_COMMANDS[command]}`" in result.error


@pytest.mark.parametrize("argv", [
    ["processes", "get"],
    ["processes", "wait", "user_turn"],
    ["process", "rm-live", "wait"],
])
def test_after_the_refusal_wins_over_a_missing_argument(after, untouched, argv):
    from typer.testing import CliRunner

    from twicc.cli import app

    assert CliRunner().invoke(app, argv).exit_code == 64
    result = invoke(argv)
    assert result.exit_code == 64
    assert "was removed on" in result.error


def test_before_processes_carries_the_removal_notice_alone(before, no_backend, project):
    warnings = invoke(["processes"]).warnings
    assert len(warnings) == 1
    assert "stops working" in warnings[0]


def test_the_notice_date_is_read_at_call_time(monkeypatch, no_backend, project):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2199, 3, 4))  # noqa: DTZ001
    assert "2199-03-04" in invoke(["processes", "get", "x"]).warnings[0]


def test_mcp_is_never_notified(before, no_backend, project):
    from twicc.mcp.identity import mcp_call

    token = mcp_call.set(True)
    try:
        result = invoke(["processes", "get", "x"])
    finally:
        mcp_call.reset(token)
    assert result.warnings == ()


def test_mcp_is_refused_after_the_date_even_before_a_restart(after, untouched, project):
    from twicc.mcp.identity import mcp_call

    token = mcp_call.set(True)
    try:
        result = invoke(["processes", "get", "x"])
    finally:
        mcp_call.reset(token)
    assert result.exit_code == 64


# --- MCP single calls -------------------------------------------------------


def _no_retired_registry():
    return {n: s for n, s in tools_by_name().items() if n not in RETIRED_MCP_TOOLS}


def test_mcp_refusal_wins_over_schema_validation(after, monkeypatch):
    """`processes_wait` without its required `timeout`, tool still listed."""
    monkeypatch.setattr(server, "_run_invoke", lambda argv: pytest.fail("ran"))
    result = _call_tool("processes_wait", {"items": ["x", "user_turn"]})
    assert result.is_error is False
    assert result.structured_content["exit_code"] == 64
    assert "`twicc processes wait` was removed" in result.structured_content["error"]


def test_mcp_after_the_restart_names_the_replacement(after, monkeypatch):
    monkeypatch.setattr(server, "tools_by_name", _no_retired_registry)
    result = _call_tool("processes_wait", {})
    assert result.structured_content["exit_code"] == 64
    assert "`twicc sessions wait-reply`" in result.structured_content["error"]

    unknown = _call_tool("no_such_tool", {})
    assert unknown.is_error is True
    assert unknown.content[0].text == "Unknown tool: no_such_tool"


def test_mcp_before_the_date_the_pre_check_is_inert(before, monkeypatch):
    """With the tool listed, as it is until the first restart past the date.

    The registry is built at import from the real clock, so from 2026-10-01 it
    no longer holds `processes`: the tool is put back explicitly, or this test
    would go red on that day for a reason unrelated to what it checks.
    """
    from twicc.mcp.tools import tool_name_for
    from twicc.rpc.generator import build_registry

    registry = dict(tools_by_name())
    registry[tool_name_for("processes")] = build_registry()["processes"]
    monkeypatch.setattr(server, "tools_by_name", lambda: registry)
    ran = []

    def fake_run(argv):
        ran.append(argv)
        return InvocationResult(exit_code=0, result=[], error=None)

    monkeypatch.setattr(server, "_run_invoke", fake_run)
    result = _call_tool("processes", {})
    assert result.structured_content["exit_code"] == 0
    assert ran == [["processes"]]


def test_mcp_batch_rejects_a_retired_child_as_unknown(after):
    from twicc.mcp import batch_contract as contract
    from twicc.mcp.tools import MCP_READ_ONLY_PATHS

    for arguments in ({}, {"items": ["x", "user_turn"], "timeout": 1}):
        body = {"calls": [
            {"id": "a", "name": "workspaces", "arguments": {}},
            {"id": "b", "name": "processes_wait", "arguments": arguments},
        ]}
        validation = contract.validate_batch(
            "batch", body, registry=tools_by_name(), read_only_paths=MCP_READ_ONLY_PATHS,
            external=False, batch_id="t", retired=frozenset(RETIRED_MCP_TOOLS),
        )
        errors = validation.rejection["errors"]
        assert [(e["code"], e["index"]) for e in errors] == [("unknown_tool", 1)]
        assert validation.rejection["executed"] == 0


def test_mcp_batch_call_is_wired_to_the_cutover(after):
    """Through `_call_tool`, so the `retired=` handed over by `_call_batch` is
    what is tested, not only `validate_batch` given the set by hand."""
    result = _call_tool("batch", {"calls": [
        {"id": "a", "name": "workspaces", "arguments": {}},
        {"id": "b", "name": "processes_wait", "arguments": {"items": ["x", "user_turn"], "timeout": 1}},
    ]})
    payload = result.structured_content
    assert payload["executed"] == 0
    assert [(e["code"], e["index"]) for e in payload["errors"]] == [("unknown_tool", 1)]


# --- RPC --------------------------------------------------------------------


def test_rpc_body_refused_before_validation(after, client, settings, tokens_store):
    settings.TWICC_PASSWORD_HASH = ""
    res = _post(client, "/rpc/processes/get", body=b"{}")
    assert res.status_code == 200
    envelope = orjson.loads(res.content)
    assert envelope["exit_code"] == 64
    assert "`twicc processes get` was removed" in envelope["error"]


def test_rpc_keeps_its_other_answers(after, client, protected, tokens_store, transactional_db):
    _login(client)
    assert _post(client, "/rpc/processes/stop").status_code == 403

    tokens_store["secret"] = _record()
    malformed = _post(client, "/rpc/processes/stop", body=b"{nope",
                      headers={"Authorization": "Bearer secret"})
    assert malformed.status_code == 400


# --- what stays -------------------------------------------------------------


def test_session_stop_answers_the_same_on_both_sides(monkeypatch, no_backend, project):
    """It shares `process_stop.stop_cmd`: the date must not change its answer."""
    _session(project, "rm-live")
    argv = ["session", "rm-live", "stop", "--timeout", "1"]
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)
    after_date = invoke(argv)
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    before_date = invoke(argv)
    assert (after_date.exit_code, after_date.error) == (before_date.exit_code, before_date.error)
    assert "was removed" not in (after_date.error or "")


def test_whoami_still_serves_its_nine_field_process_row(after, live_backend, project, monkeypatch, capsysbinary):
    from twicc.cli.whoami import whoami_cmd

    session = _session(project, "rm-me")
    _live_run(session.id)
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: session,
    )
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc_or_exit",
        lambda: type("I", (), {"pid": TWICC_PID})(),
    )
    whoami_cmd()
    process = orjson.loads(capsysbinary.readouterr().out)["process"]
    assert set(process) == {
        "id", "state", "started_at", "last_state_change_at", "pid",
        "provider", "session_id", "session_title", "project_id",
    }


def test_telemetry_keeps_the_group_of_retired_tools(monkeypatch):
    from twicc.telemetry import snapshot

    monkeypatch.setattr("twicc.mcp.tools.build_mcp_registry", lambda: {})
    snapshot.mcp_group_by_tool.cache_clear()
    try:
        groups = snapshot.mcp_group_by_tool()
    finally:
        snapshot.mcp_group_by_tool.cache_clear()
    assert groups["processes_wait"] == snapshot.MCP_TOOL_GROUPS["processes"]
    assert groups["process_stop"] == snapshot.MCP_TOOL_GROUPS["process"]


# --- help texts, on the real side of the cutover -----------------------------


def test_the_help_texts_match_the_side_of_the_cutover_we_are_on():
    """Evaluated at import: read the real clock. A known false positive under a
    plugin that forces the constant, like the pagination descriptions test."""
    from typer.testing import CliRunner

    from twicc.cli import app
    from twicc.mcp.tools import iter_mcp_tools

    described = {t.name: t.description for t in iter_mcp_tools()}
    top_help = CliRunner().invoke(app, ["--help"]).output

    if _output.listing_cutover_passed():
        assert not set(RETIRED_MCP_TOOLS) & set(tools_by_name())
        assert not re.search(r"^\s*│?\s*process(es)?\s", top_help, re.MULTILINE)
    else:
        for tool, command in RETIRED_MCP_TOOLS.items():
            assert described[tool].startswith("DEPRECATION"), tool
            assert f"`twicc {_output.RETIRED_COMMANDS[command]}`" in described[tool], tool


def test_no_other_mcp_text_teaches_a_retired_command():
    from twicc.mcp.tools import iter_mcp_tools

    offenders = []
    for tool in iter_mcp_tools():
        if tool.name in RETIRED_MCP_TOOLS:
            continue
        texts = [tool.description or ""] + [
            prop.get("description", "")
            for prop in (tool.input_schema or {}).get("properties", {}).values()
        ]
        offenders += [tool.name for text in texts if RETIRED_PATTERN.search(text)]
    if RETIRED_PATTERN.search(server.INSTRUCTIONS):
        offenders.append("INSTRUCTIONS")
    assert offenders == []


# --- prerequisites ----------------------------------------------------------


@pytest.fixture
def seen_cursors(monkeypatch):
    seen: dict = {}

    def fake(cursors, *, timeout, want_text, first):
        seen.update(cursors)
        return {sid: {"outcome": "replied", "line_num": 9, "since_line_num": c}
                for sid, c in cursors.items()}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake)
    return seen


def test_p1_plural_waits_from_zero_on_a_live_unindexed_session(live_backend, project, seen_cursors, capsysbinary):
    from twicc.cli import sessions_wait_reply

    _live_run("rm-new")
    sessions_wait_reply.main(["rm-new", "rm-ghost"], timeout=5.0)
    out = orjson.loads(capsysbinary.readouterr().out)
    assert seen_cursors == {"rm-new": 0}
    assert out["results"]["rm-new"]["outcome"] == "replied"
    assert out["results"]["rm-ghost"]["outcome"] == "unknown_session"


def test_p1_plural_ignores_a_dead_process(live_backend, project, seen_cursors, capsysbinary):
    from twicc.cli import sessions_wait_reply

    _live_run("rm-gone", state=AgentState.DEAD)
    sessions_wait_reply.main(["rm-gone"], timeout=5.0)
    out = orjson.loads(capsysbinary.readouterr().out)
    assert out["results"]["rm-gone"]["outcome"] == "unknown_session"


@pytest.mark.parametrize("with_row", [False, True])
def test_p1_singular_waits_from_zero(live_backend, project, monkeypatch, capsysbinary, with_row):
    from twicc.cli import session as cli_session

    if with_row:
        _session(project, "rm-new", user_message_count=0)
    _live_run("rm-new")
    seen = {}

    def fake(session_id, *, since_line_num, timeout, want_text):
        seen.update(session_id=session_id, cursor=since_line_num)
        return {"outcome": "replied", "line_num": 4, "since_line_num": since_line_num}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_reply_or_degrade", fake)
    with pytest.raises(Exception) as exc:  # typer.Exit(0)
        cli_session.wait_reply("rm-new", timeout=5.0)
    assert getattr(exc.value, "exit_code", None) == 0
    assert seen == {"session_id": "rm-new", "cursor": 0}
    assert orjson.loads(capsysbinary.readouterr().out)["session_id"] == "rm-new"


def test_p1_singular_refuses_an_id_with_no_live_process(live_backend, project):
    from twicc.cli import session as cli_session

    with pytest.raises(Exception) as exc:
        cli_session.wait_reply("rm-nothing", timeout=5.0)
    assert getattr(exc.value, "exit_code", None) == 1


@pytest.mark.parametrize(("argv", "expected"), [
    (["session", "abc", "wait-reply", "--wait-timeout", "600"], 615.0),
    (["session", "abc", "wait-reply"], 315.0),
    (["sessions", "wait-reply", "--spawned-by", "abc"], 315.0),
    (["sessions", "wait-reply", "abc", "--wait-timeout", "120"], 135.0),
])
def test_p2_remote_read_timeout_follows_the_wait(argv, expected):
    from twicc.cli import _remote

    assert _remote._request_timeout(_remote.resolve_command(argv)).read == expected


def test_p3_sessions_stop_help_says_union():
    from twicc.rpc.generator import build_registry

    help_text = {p.name: p.help for p in build_registry()["sessions/stop"].params}["session_ids"]
    assert "bypasses" not in help_text
    assert "unioned" in help_text
