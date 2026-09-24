"""`session <id>`: flags on both sides of the id, keywords, lookup rule, date."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime

import orjson
import pytest
from typer.testing import CliRunner

from twicc.cli import _output, app
from twicc.cli import session as cli_session
from twicc.core.models import Project, Session, SessionType
from twicc.rpc.generator import build_registry, render_argv
from twicc.rpc.invoker import invoke

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001
NOTICE = "returns the reduced session projection by default"


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


@pytest.fixture(autouse=True)
def no_backend(monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)


@pytest.fixture
def rows(db):
    project = Project.objects.create(id="-tmp-sc", directory="/tmp/sc")
    root = Session.objects.create(
        id="sc-root", project=project, provider="claude_code", file_path="r.jsonl",
        type=SessionType.SESSION, created_at="2026-09-20T10:00:00Z", user_message_count=1,
    )
    Session.objects.create(  # no created_at, no user message
        id="sc-child", project=project, provider="claude_code", file_path="c.jsonl",
        type=SessionType.SESSION, spawned_by=root, spawn_root=root,
    )
    Session.objects.create(
        id="sc-sub", project=project, provider="claude_code", file_path="s.jsonl",
        type=SessionType.SUBAGENT, parent_session=root, created_at="2026-09-20T10:00:00Z",
        user_message_count=1,
    )
    return project


def cli(*argv):
    return CliRunner().invoke(app, list(argv))


def first_row(result):
    return result[0] if isinstance(result, list) else result["items"][0]


@pytest.mark.parametrize("argv", [
    ("session", "sc-root", "--full"),
    ("session", "--full", "sc-root"),
    ("session", "--full", "--", "sc-root"),
])
def test_full_on_either_side_of_the_id(after, rows, argv):
    result = cli(*argv)
    assert result.exit_code == 0, result.output
    assert "layout" in orjson.loads(result.stdout)
    # Spec § Tests: the orders go through `invoke` too (the /rpc/ path).
    through_rpc = invoke(list(argv))
    assert through_rpc.exit_code == 0, through_rpc.error
    assert "layout" in through_rpc.result


@pytest.mark.parametrize("argv", [
    ("session", "sc-root", "--full", "agents"),
    ("session", "--full", "sc-root", "agents"),
])
def test_a_group_flag_before_a_subcommand_is_refused(rows, argv):
    result = invoke(list(argv))
    assert result.exit_code == 2
    assert "--slim / --full apply to `session <id>` alone, not to `agents`" in result.error


def test_the_subcommand_keeps_its_own_flag(after, rows):
    result = invoke(["session", "sc-root", "agents", "--full"])
    assert result.exit_code == 0, result.error
    assert "layout" in first_row(result.result)


def test_both_flags_are_refused(rows):
    result = invoke(["session", "sc-root", "--slim", "--full"])
    assert result.exit_code == 2
    assert "--slim and --full are mutually exclusive" in result.error


def test_the_mcp_argv_is_the_bare_call(after, rows):
    registry = build_registry()
    argv = render_argv(registry["session"], {"session_id": "sc-root", "full": True})
    assert argv == ["session", "--full", "--", "sc-root"]
    result = invoke(argv)
    assert result.exit_code == 0, result.error
    assert "layout" in result.result
    assert render_argv(registry["session/agents"], {"session_id": "X", "full": True}) == [
        "session", "X", "agents", "--full",
    ]


def test_before_the_flagless_call_is_full_and_announced(before, rows, capsysbinary):
    cli_session.main("sc-root")
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" in row and "project_directory" in row
    assert f"`session` {NOTICE}" in err.decode()


@pytest.mark.parametrize("flags", [{"full": True}, {"slim": True}])
def test_before_a_flag_is_unannounced(before, rows, capsysbinary, flags):
    cli_session.main("sc-root", **flags)
    _, err = capsysbinary.readouterr()
    assert err == b""


def test_after_the_flagless_call_is_reduced(after, rows, capsysbinary):
    cli_session.main("sc-root")
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" not in row
    assert set(row["process"]) == {"state"}
    assert err == b""


def test_a_row_with_no_user_message_is_served(before, rows, capsysbinary):
    cli_session.main("sc-child", full=True)
    assert orjson.loads(capsysbinary.readouterr().out)["id"] == "sc-child"


def test_no_row_exits_1(rows):
    result = invoke(["session", "nope", "--full"])
    assert result.exit_code == 1
    assert "not found" in result.error


def test_the_transcript_readers_still_refuse_such_a_row(rows):
    assert invoke(["session", "sc-child", "messages"]).exit_code == 1


def test_a_subagent_keeps_stored_settings_and_no_process(before, rows, capsysbinary):
    """Review focus 5."""
    cli_session.main("sc-sub", full=True)
    row = orjson.loads(capsysbinary.readouterr().out)
    assert row["process"] is None
    assert row["selected_model"] is None


def test_self_and_parent_resolve(rows, monkeypatch):
    child = Session.objects.get(id="sc-child")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: child)
    assert invoke(["session", "self", "--full"]).result["id"] == "sc-child"
    assert invoke(["session", "parent", "--full"]).result["id"] == "sc-root"
    assert invoke(["session", "parent", "messages"]).exit_code == 0


def test_self_reaches_a_subcommand(rows, monkeypatch):
    """Spec § Tests: `session self messages` gets the resolved id in ctx.obj."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    seen = []
    monkeypatch.setattr(
        "twicc.cli.session.messages", lambda session_id, **kw: seen.append(session_id),
    )
    assert invoke(["session", "self", "messages"]).exit_code == 0
    assert seen == ["sc-root"]


@pytest.mark.parametrize("pinned", [FUTURE, PAST])
def test_full_is_the_full_payload_on_both_sides(monkeypatch, rows, capsysbinary, pinned):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
    cli_session.main("sc-root", full=True)
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" in row and "slug" in row and "mtime" in row
    assert set(row["process"]) == {"id", "state", "started_at", "last_state_change_at", "pid"}
    assert err == b""


def test_self_is_refused_outside_a_session(rows, monkeypatch):
    """Refused by the keyword resolution, not by a lookup of the literal id
    "self" (which also exits 1 today, for the wrong reason)."""
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    result = invoke(["session", "self"])
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "session_context_not_found"


@pytest.mark.parametrize("argv", [["session", "parent"], ["session", "parent", "messages"]])
def test_parent_is_refused_for_a_root_session(rows, monkeypatch, argv):
    """Spec § Tests: refused like the other keyword call sites — a root
    session has no spawner."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    result = invoke(argv)
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "parent_not_found"


def test_help_after_a_keyword_needs_no_session(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    result = cli("session", "self", "messages", "--help")
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_session_self_stop_submits_the_resolved_id(rows, monkeypatch):
    child = Session.objects.get(id="sc-child")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: child)
    seen = []
    # Not the dotted string "twicc.cli.process_stop.stop_cmd": depending on
    # import order, `twicc.cli.process_stop` is the `process stop` command
    # function (src/twicc/cli/__init__.py:1771), which hides the submodule.
    # Patch the module object itself.
    monkeypatch.setattr(
        importlib.import_module("twicc.cli.process_stop"), "stop_cmd",
        lambda session_id, **kw: seen.append(session_id),
    )
    invoke(["session", "self", "stop", "--timeout", "1"])
    assert seen == ["sc-child"]


@pytest.mark.django_db(transaction=True)
def test_self_through_the_mcp_identity(monkeypatch):
    """Review focus 3: the MCP-forced identity, not the PID ancestry."""
    from twicc.mcp import server as mcp_server

    project = Project.objects.create(id="-tmp-sc-mcp", directory="/tmp/sc-mcp")
    Session.objects.create(
        id="sc-mcp", project=project, provider="claude_code", file_path="m.jsonl",
        type=SessionType.SESSION,
    )
    result = asyncio.run(mcp_server.dispatch_tool(
        "session", {"session_id": "self", "full": True}, session_id="sc-mcp",
    ))
    assert result["exit_code"] == 0, result
    assert result["result"]["id"] == "sc-mcp"


def test_a_help_token_as_an_option_value_still_resolves_the_keyword(rows, monkeypatch):
    """Review finding: `--help` as the value of `--contains` is not a help
    request, so `self` must still resolve (it stayed the literal "self")."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    seen = []
    monkeypatch.setattr(
        "twicc.cli.session.content", lambda session_id, **kw: seen.append(session_id),
    )
    result = cli("session", "self", "content", "--contains", "--help")
    assert result.exit_code == 0, result.output
    assert seen == ["sc-root"]


def test_a_session_cannot_wait_on_its_own_reply(rows, monkeypatch):
    """Review finding: the caller is mid-turn while the tool runs, so its own
    answer cannot come before the deadline."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    result = invoke(["session", "self", "wait-reply", "--wait-timeout", "1"])
    assert result.exit_code == 1
    assert "cannot wait on its own reply" in result.error
