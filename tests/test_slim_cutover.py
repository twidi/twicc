"""Tests for the dated cutover to slim session listings.

Design: docs/plans/2026-09-23-session-listing-slim-cutover-design.md. The four
commands that return several sessions (``sessions``, ``sessions get``,
``session agents``, ``topology``) switch to their reduced projection on
``LISTING_CUTOVER``, the date the pagination envelope also becomes the default.
``--full`` stays as the way back. Every pinned value is **naive**, like the
constant.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import orjson
import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import sessions_get as cli_sessions_get
from twicc.cli.topology import TOPOLOGY_SESSION_FIELDS, build_topology
from twicc.cli.topology import main as topology_main
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.core.serializers import SESSION_LISTING_FIELDS

PAST = datetime(2000, 1, 1)     # noqa: DTZ001 — naive, like the constant
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001 — naive, like the constant

TWICC_PID = 4343
FULL_PROCESS_KEYS = {"id", "state", "started_at", "last_state_change_at", "pid"}
SLIM_KEYS = set(SESSION_LISTING_FIELDS) | {"process"}
TOPOLOGY_SLIM_KEYS = set(TOPOLOGY_SESSION_FIELDS) | {"directory"}

# The two notice texts, matched by content rather than by position.
LISTING_NOTICE = "returns the reduced session projection by default"
TOPOLOGY_NOTICE = "reduces each node's `process` block"


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


@pytest.fixture(autouse=True)
def live_backend(monkeypatch):
    """A running backend at :data:`TWICC_PID`, so process blocks are read.

    Unpatched, ``resolve_live_twicc`` reads the developer's own data dir.
    """
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc",
        lambda: type("I", (), {"pid": TWICC_PID})(),
    )


@pytest.fixture(autouse=True)
def fresh_placeholder_template(monkeypatch):
    """``sessions get`` caches its placeholder shape in a module global."""
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)


@pytest.fixture
def tree(db):
    """A root that spawned one worker and ran one subagent, the root live."""
    project = Project.objects.create(id="-tmp-twicc-slim", directory="/tmp/twicc-slim")
    now = timezone.now()

    def make(sid, minutes, **extra):
        values = {
            "id": sid, "project": project, "provider": "claude_code",
            "file_path": f"{sid}.jsonl", "type": SessionType.SESSION,
            "created_at": now + timedelta(minutes=minutes), "last_new_content_at": now,
            "mtime": 1000 + minutes, "last_line": 1, "user_message_count": 1,
        }
        return Session.objects.create(**(values | extra))

    root = make("slim-root", 0)
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make("slim-worker", 1, spawned_by=root, spawn_root=root)
    make("slim-sub", 2, parent_session=root, type=SessionType.SUBAGENT)
    ProcessRun.objects.create(
        session_id=root.id, provider="claude_code", twicc_pid=TWICC_PID,
        state=AgentState.ASSISTANT_TURN.value, started_at=now,
        last_state_change_at=now, agent_pid=777,
    )
    return project


def read(capsysbinary):
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


def rows_of(payload):
    return payload["items"] if isinstance(payload, dict) else payload


# The three listings, each called the way its wrapper calls it.
LISTINGS = {
    "sessions": lambda project, **kw: cli_sessions.main(project=project.id, **kw),
    "sessions get": lambda project, **kw: cli_sessions_get.main(["slim-root"], **kw),
    "session agents": lambda project, **kw: cli_session.agents("slim-root", **kw),
}


def listing(name, project, capsysbinary, **flags):
    LISTINGS[name](project, **flags)
    payload, err = read(capsysbinary)
    return rows_of(payload), err


def assert_full(name, rows):
    for row in rows:
        assert "cwd" in row, "a field only the full payload carries"
        if name == "session agents":
            assert row["process"] is None
        else:
            assert set(row["process"]) == FULL_PROCESS_KEYS


def assert_slim(name, rows):
    extra = {"known"} if name == "sessions get" else set()
    for row in rows:
        assert set(row) == SLIM_KEYS | extra
        if name == "session agents":
            assert row["process"] is None
        else:
            assert set(row["process"]) == {"state"}


# --- the three listings -----------------------------------------------------


@pytest.mark.parametrize("name", LISTINGS)
def test_before_a_flagless_listing_is_full_and_announced(before, tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary)
    assert rows
    assert_full(name, rows)
    assert f"`{name}` {LISTING_NOTICE}" in err
    assert "2200-01-01" in err


@pytest.mark.parametrize("name", LISTINGS)
def test_before_slim_is_the_new_shape_already(before, tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary, slim=True)
    assert_slim(name, rows)
    assert LISTING_NOTICE not in err


@pytest.mark.parametrize("name", LISTINGS)
def test_before_full_is_unannounced(before, tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary, full=True)
    assert_full(name, rows)
    assert LISTING_NOTICE not in err


@pytest.mark.parametrize("name", LISTINGS)
@pytest.mark.parametrize("flags", [{}, {"slim": True}])
def test_after_slim_is_the_default_and_the_flag_a_no_op(after, tree, capsysbinary, name, flags):
    rows, err = listing(name, tree, capsysbinary, **flags)
    assert_slim(name, rows)
    assert err == ""


@pytest.mark.parametrize("name", LISTINGS)
def test_after_full_still_brings_the_full_payload_back(after, tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary, full=True)
    assert_full(name, rows)
    assert err == ""


def test_after_the_three_listings_share_one_key_set(after, tree, capsysbinary):
    keys = {
        name: {k for row in listing(name, tree, capsysbinary)[0] for k in row} - {"known"}
        for name in LISTINGS
    }
    assert keys["sessions"] == keys["sessions get"] == keys["session agents"] == SLIM_KEYS


def test_the_notice_date_is_read_at_call_time(tree, capsysbinary, monkeypatch):
    """`sessions get` has no pagination notice, so the date can only come from
    the slim one — built from the pinned constant, not the import-time string."""
    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2199, 3, 4))  # noqa: DTZ001
    _, err = listing("sessions get", tree, capsysbinary)
    assert "2199-03-04" in err
    assert LISTING_NOTICE in err


def test_both_flags_are_a_programming_error():
    with pytest.raises(ValueError):
        _output.slim_notice("sessions", True, True)


# --- topology ---------------------------------------------------------------


def run_topology(capsysbinary, **flags):
    topology_main("slim-root", **flags)
    payload, err = read(capsysbinary)
    return {node["id"]: node for node in payload["nodes"]}, err


def test_topology_before_keeps_its_shape_and_announces(before, tree, capsysbinary):
    nodes, err = run_topology(capsysbinary)
    assert set(nodes["slim-root"]["session"]) == TOPOLOGY_SLIM_KEYS
    assert set(nodes["slim-root"]["process"]) == FULL_PROCESS_KEYS
    assert TOPOLOGY_NOTICE in err
    assert "--full" in err


def test_topology_after_reduces_the_process_block(after, tree, capsysbinary):
    nodes, err = run_topology(capsysbinary)
    assert set(nodes["slim-root"]["session"]) == TOPOLOGY_SLIM_KEYS
    assert nodes["slim-root"]["process"] == {"state": "assistant_turn"}
    assert nodes["slim-worker"]["process"] == {"state": "dead"}
    assert err == ""


def test_topology_slim_before_is_the_new_shape_already(before, tree, capsysbinary):
    nodes, err = run_topology(capsysbinary, slim=True)
    assert nodes["slim-root"]["process"] == {"state": "assistant_turn"}
    assert TOPOLOGY_NOTICE not in err


@pytest.mark.parametrize("side", ["before", "after"])
def test_topology_full_is_full_on_both_sides(tree, capsysbinary, request, side):
    request.getfixturevalue(side)
    nodes, err = run_topology(capsysbinary, full=True)
    assert "cwd" in nodes["slim-root"]["session"]
    assert "process" not in nodes["slim-root"]["session"], "it lives at node level"
    assert set(nodes["slim-root"]["process"]) == FULL_PROCESS_KEYS
    assert TOPOLOGY_NOTICE not in err


@pytest.mark.parametrize("side", ["before", "after"])
def test_topology_without_processes_has_nothing_to_announce(tree, capsysbinary, request, side):
    request.getfixturevalue(side)
    nodes, err = run_topology(capsysbinary, include_processes=False)
    assert all(node["process"] is None for node in nodes.values())
    assert err == ""


def test_the_rest_view_keeps_five_fields_after_the_date(after, tree):
    """``build_topology``'s defaults are what `views.session_topology` gets."""
    root = Session.objects.get(id="slim-root")
    data = build_topology(root, include_processes=True, full_sessions=True, twicc_pid=TWICC_PID)
    assert set(data["nodes"][0]["process"]) == FULL_PROCESS_KEYS


# --- the command line, RPC and MCP ------------------------------------------


def cli(*args):
    from typer.testing import CliRunner

    from twicc.cli import app

    return CliRunner().invoke(app, list(args))


def mcp_argv(tool, arguments):
    """The argv the MCP server would run for ``arguments`` — schema-checked."""
    from twicc.mcp.dispatch import prepare_tool
    from twicc.mcp.tools import tools_by_name
    from twicc.rpc.generator import render_argv

    prepared = prepare_tool(tool, arguments, registry=tools_by_name(), external=False)
    return render_argv(prepared.spec, prepared.arguments)


@pytest.mark.parametrize("args", [
    ("--full-sessions",),
    ("--full",),
])
def test_the_alias_is_full_from_the_command_line(after, tree, args):
    result = cli("topology", "slim-root", *args)
    assert result.exit_code == 0, result.output
    node = orjson.loads(result.stdout)["nodes"][0]
    assert "cwd" in node["session"]
    assert set(node["process"]) == FULL_PROCESS_KEYS


def test_the_alias_is_full_through_mcp_and_rpc(after, tree):
    from twicc.rpc.invoker import invoke

    result = invoke(mcp_argv("topology", {"session_id": "slim-root", "full_sessions": True}))
    assert result.exit_code == 0, result.error
    node = result.result["nodes"][0]
    assert "cwd" in node["session"]
    assert set(node["process"]) == FULL_PROCESS_KEYS


def test_no_full_sessions_never_cancels_full(after, tree):
    from twicc.rpc.invoker import invoke

    argv = mcp_argv("topology", {"session_id": "slim-root", "full": True, "full_sessions": False})
    result = invoke(argv)
    assert result.exit_code == 0, result.error
    assert "cwd" in result.result["nodes"][0]["session"]


@pytest.mark.parametrize("argv", [
    ["sessions", "--slim", "--full"],
    ["sessions", "get", "slim-root", "--slim", "--full"],
    ["session", "slim-root", "agents", "--slim", "--full"],
    ["topology", "slim-root", "--slim", "--full"],
])
def test_slim_and_full_are_mutually_exclusive(tree, argv):
    from twicc.rpc.invoker import invoke

    assert cli(*argv).exit_code == 2
    result = invoke(argv)
    assert result.exit_code == 2
    assert "--slim and --full" in result.error
    assert result.warnings == (), "refused in the wrapper, before any notice"


def test_the_alias_is_named_when_it_conflicts(tree):
    from twicc.rpc.invoker import invoke

    assert cli("topology", "slim-root", "--slim", "--full-sessions").exit_code == 2
    result = invoke(mcp_argv("topology", {"session_id": "slim-root", "slim": True, "full_sessions": True}))
    assert result.exit_code == 2
    assert "(or --full-sessions)" in result.error


def test_rpc_carries_both_notices_in_order(before, tree):
    from twicc.rpc.invoker import invoke

    warnings = invoke(["sessions", "--project", tree.id]).warnings
    assert len(warnings) == 2
    assert "--paginated" in warnings[0]
    assert LISTING_NOTICE in warnings[1]


@pytest.mark.parametrize("argv", [
    ["sessions", "get", "slim-root"],
    ["topology", "slim-root"],
])
def test_mcp_is_never_notified(before, tree, argv):
    """Neither command has a pagination notice that could mask the slim one."""
    from twicc.mcp.identity import mcp_call
    from twicc.rpc.invoker import invoke

    token = mcp_call.set(True)
    try:
        result = invoke(argv)
    finally:
        mcp_call.reset(token)
    assert result.exit_code == 0, result.error
    assert result.warnings == ()


def test_the_flags_reach_the_mcp_schema():
    from twicc.rpc.generator import build_registry

    registry = build_registry()
    for path in ("sessions", "sessions/get", "session/agents", "topology"):
        params = {p.name: p for p in registry[path].params}
        for flag in ("slim", "full"):
            assert params[flag].is_flag and params[flag].json_type == "boolean", (path, flag)
    assert registry["topology"].json_schema["properties"]["full_sessions"]["type"] == "boolean"


def test_the_help_texts_match_the_side_of_the_cutover_we_are_on():
    """Evaluated at import, so no fixture can flip them: read the real clock.

    Asserts a fixed substring, never ``SLIM_CUTOVER_NOTICE in description`` —
    past the date the constant is ``""``, which every string contains.
    """
    from twicc.mcp.tools import iter_mcp_tools, tools_by_name

    described = {t.name: t.description for t in iter_mcp_tools()}
    ids_help = tools_by_name()["sessions_get"].json_schema["properties"]["session_ids"]["description"]
    announcing = {"sessions", "sessions_get", "session_agents"}

    if _output.listing_cutover_passed():
        assert not any(LISTING_NOTICE in described[n] for n in announcing)
        assert not described["topology"].startswith("DEPRECATION")
        assert "reduced projection" in ids_help
    else:
        for name in announcing:
            assert "reduced session projection by default" in described[name], name
        assert described["sessions_get"].startswith("DEPRECATION")
        assert described["topology"].startswith("DEPRECATION")
        assert "the full session metadata or a placeholder" in ids_help
