"""Tests for the reduced session projection.

The four commands that return several sessions (``sessions``, ``sessions get``,
``session agents``, ``topology``) return the reduced projection by default;
``--full`` brings the full payload back and ``--slim`` is an accepted no-op.
"""

from __future__ import annotations

from datetime import timedelta

import orjson
import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import sessions_get as cli_sessions_get
from twicc.cli.topology import TOPOLOGY_SESSION_FIELDS, build_topology
from twicc.cli.topology import main as topology_main
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.core.serializers import SESSION_LISTING_FIELDS

TWICC_PID = 4343
FULL_PROCESS_KEYS = {"id", "state", "background_work_in_progress", "started_at", "last_state_change_at", "pid"}
SLIM_KEYS = set(SESSION_LISTING_FIELDS) | {"process"}
TOPOLOGY_SLIM_KEYS = set(TOPOLOGY_SESSION_FIELDS) | {"directory"}


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
        assert "layout" in row, "a field only the full payload carries"
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
            assert set(row["process"]) == {"state", "background_work_in_progress"}


# --- the three listings -----------------------------------------------------


@pytest.mark.parametrize("name", LISTINGS)
def test_a_flagless_listing_is_reduced(tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary)
    assert rows
    assert_slim(name, rows)
    assert err == ""


@pytest.mark.parametrize("name", LISTINGS)
def test_full_brings_the_full_payload_back(tree, capsysbinary, name):
    rows, err = listing(name, tree, capsysbinary, full=True)
    assert_full(name, rows)
    assert err == ""


def test_the_three_listings_share_one_key_set(tree, capsysbinary):
    keys = {
        name: {k for row in listing(name, tree, capsysbinary)[0] for k in row} - {"known"}
        for name in LISTINGS
    }
    assert keys["sessions"] == keys["sessions get"] == keys["session agents"] == SLIM_KEYS


# --- topology ---------------------------------------------------------------


def run_topology(capsysbinary, **flags):
    topology_main("slim-root", **flags)
    payload, err = read(capsysbinary)
    return {node["id"]: node for node in payload["nodes"]}, err


def test_topology_reduces_the_process_block(tree, capsysbinary):
    nodes, err = run_topology(capsysbinary)
    assert set(nodes["slim-root"]["session"]) == TOPOLOGY_SLIM_KEYS
    assert nodes["slim-root"]["process"] == {"state": "assistant_turn", "background_work_in_progress": None}
    assert nodes["slim-worker"]["process"] == {"state": "dead", "background_work_in_progress": None}
    assert err == ""


def test_topology_full_is_full(tree, capsysbinary):
    nodes, err = run_topology(capsysbinary, full=True)
    assert "cwd" in nodes["slim-root"]["session"]
    assert "process" not in nodes["slim-root"]["session"], "it lives at node level"
    assert set(nodes["slim-root"]["process"]) == FULL_PROCESS_KEYS
    assert err == ""


def test_topology_without_processes_has_nothing_to_announce(tree, capsysbinary):
    nodes, err = run_topology(capsysbinary, include_processes=False)
    assert all(node["process"] is None for node in nodes.values())
    assert err == ""


def test_the_rest_view_keeps_the_full_process_block(tree):
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


def test_full_reaches_topology_from_the_command_line(tree):
    result = cli("topology", "slim-root", "--full")
    assert result.exit_code == 0, result.output
    node = orjson.loads(result.stdout)["nodes"][0]
    assert "cwd" in node["session"]
    assert set(node["process"]) == FULL_PROCESS_KEYS


def test_full_reaches_topology_through_mcp_and_rpc(tree):
    from twicc.rpc.invoker import invoke

    result = invoke(mcp_argv("topology", {"session_id": "slim-root", "full": True}))
    assert result.exit_code == 0, result.error
    node = result.result["nodes"][0]
    assert "cwd" in node["session"]
    assert set(node["process"]) == FULL_PROCESS_KEYS


def test_the_full_sessions_alias_is_gone(tree):
    assert cli("topology", "slim-root", "--full-sessions").exit_code == 2


def test_slim_alone_is_accepted_and_ignored(tree):
    result = cli("topology", "slim-root", "--slim")
    assert result.exit_code == 0, result.output
    node = orjson.loads(result.stdout)["nodes"][0]
    assert set(node["process"]) == {"state", "background_work_in_progress"}


@pytest.mark.parametrize("argv", [
    ["sessions", "--slim", "--full"],
    ["sessions", "get", "slim-root", "--slim", "--full"],
    ["session", "slim-root", "agents", "--slim", "--full"],
    ["topology", "slim-root", "--slim", "--full"],
    ["session", "slim-root", "--slim", "--full"],
    ["whoami", "--slim", "--full"],
])
def test_slim_is_a_no_op_with_full(tree, argv, monkeypatch):
    from twicc.rpc.invoker import invoke

    root = Session.objects.get(id="slim-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    full_argv = [arg for arg in argv if arg != "--slim"]

    cli_result = cli(*argv)
    full_cli_result = cli(*full_argv)
    assert cli_result.exit_code == 0, cli_result.output
    assert full_cli_result.exit_code == 0, full_cli_result.output
    assert orjson.loads(cli_result.stdout) == orjson.loads(full_cli_result.stdout)

    result = invoke(argv)
    full_result = invoke(full_argv)
    assert result.exit_code == 0, result.error
    assert full_result.exit_code == 0, full_result.error
    assert result.result == full_result.result


def test_only_full_reaches_the_schemas():
    from twicc.mcp.tools import tools_by_name
    from twicc.rpc.generator import build_registry

    registry = build_registry()
    for path in ("sessions", "sessions/get", "session/agents", "topology", "session"):
        props = registry[path].json_schema["properties"]
        assert props["full"]["type"] == "boolean", path
        assert "slim" not in props, path
    assert "full" not in registry["session/messages"].json_schema["properties"]
    assert "full_sessions" not in registry["topology"].json_schema["properties"]

    props = tools_by_name()["whoami"].json_schema["properties"]
    assert props["full"]["type"] == "boolean"
    assert "slim" not in props


def test_the_help_texts_describe_the_reduced_default():
    from twicc.mcp.tools import iter_mcp_tools, tools_by_name

    described = {t.name: t.description for t in iter_mcp_tools()}
    ids_help = tools_by_name()["sessions_get"].json_schema["properties"]["session_ids"]["description"]
    assert not any(d.startswith("DEPRECATION") for d in described.values())
    assert "reduced projection" in ids_help


NEW_SLIM_FIELDS = {
    "last_line", "cwd", "git_directory", "project_directory", "artifacts_dir",
    "scratch_dir", "orchestration_scratch_dir", "compacted", "hybrid",
    "permission_mode", "selected_model", "effort", "thinking_enabled",
    "claude_in_chrome", "fast_mode", "question_widget",
}
DROPPED_FIELDS = {
    "inline_artifacts", "type",
    "tasks", "plan_paths", "goals", "layout", "last_started_at", "last_updated_at",
    "last_stopped_at", "last_viewed_at", "mtime", "self_cost", "subagents_cost",
    "slug", "browser_url", "compute_version_up_to_date", "has_pending_title",
}


def test_the_reduced_projection_keeps_everything_but_the_dropped_fields(tree, capsysbinary):
    assert NEW_SLIM_FIELDS <= set(SESSION_LISTING_FIELDS)
    assert "context_max" in SESSION_LISTING_FIELDS
    slim_rows, _ = listing("sessions", tree, capsysbinary)
    full_rows, _ = listing("sessions", tree, capsysbinary, full=True)
    for slim_row, full_row in zip(slim_rows, full_rows, strict=True):
        assert set(full_row) - set(slim_row) == DROPPED_FIELDS
        assert set(slim_row["process"]) == {"state", "background_work_in_progress"}
