"""Generated transports preserve timeline selection and caller identity."""

import asyncio

import orjson
import pytest
from django.test import AsyncClient

from tests.db_write_helpers import run_with_direct_writes
from tests.test_mcp_batch_contract import validate
from tests.test_timeline_api import make_event
from twicc.cli._remote import RemoteUsageError, reject_host_bound, resolve_command
from twicc.core.models import McpConnection, McpOAuthClient, Project, Session
from twicc.mcp import server
from twicc.mcp.batch import BatchRuntime
from twicc.mcp.dispatch import prepare_tool
from twicc.mcp.identity import ExternalCaller, external_caller
from twicc.mcp.tools import tools_by_name
from twicc.rpc.generator import build_registry, render_argv
from twicc.rpc.invoker import invoke

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def history():
    project = Project.objects.create(id="-timeline-transport", directory="/tmp/timeline-transport")
    root = Session.objects.create(id="root", project=project, provider="codex", file_path="root.jsonl")
    Session.objects.create(
        id="child", project=project, provider="codex", file_path="child.jsonl", spawned_by=root,
        annotations={"team": "backend", "ready": True},
    )
    events = [make_event(session_id="child", project=project, at=i) for i in range(4)]
    return project, events


def rpc(arguments):
    response = asyncio.run(AsyncClient().post(
        "/rpc/timeline", data=orjson.dumps(arguments), content_type="application/json",
    ))
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.parametrize("filiation_visibility", [None, {}, {"include_hidden": True}, {"only_hidden": True}])
def test_explicit_arguments_match_across_transports(history, filiation_visibility):
    project, events = history
    arguments = {"project": project.id, "annotation": ["team=backend", "ready=true"], "limit": 2}
    if filiation_visibility is not None:
        Session.objects.filter(pk="child").update(hidden=True)
        arguments.update(spawned_by="root", **filiation_visibility)
    local = invoke(render_argv(build_registry()["timeline"], arguments))
    http = rpc(arguments)
    internal = asyncio.run(server.dispatch_tool("timeline", arguments, session_id="root"))
    client = McpOAuthClient.objects.create(id="timeline-client")
    connection = McpConnection.objects.create(id="timeline-connection", client=client, resource="https://mcp.test/mcp")

    async def external_call():
        token = external_caller.set(ExternalCaller(connection.id, "Test"))
        try:
            prepared = prepare_tool("timeline", arguments, registry=tools_by_name(), external=True)
            return await server.execute_prepared(prepared, session_id=None)
        finally:
            external_caller.reset(token)

    external = run_with_direct_writes(external_call)
    assert local.exit_code == http["exit_code"] == internal["exit_code"] == external["exit_code"] == 0
    assert local.result == http["result"] == internal["result"] == external["result"]
    assert [e["id"] for e in local.result["items"]] == [events[3].id, events[2].id]


def test_cursor_round_trip_across_transports(history):
    _, events = history
    first = rpc({"session": "child", "limit": 2})["result"]
    second = asyncio.run(server.dispatch_tool(
        "timeline", {"session": "child", "limit": 2, "before": first["pagination"]["next_cursor"]}, session_id=None,
    ))["result"]
    assert [e["id"] for e in first["items"] + second["items"]] == [e.id for e in reversed(events)]
    assert second["pagination"]["next_cursor"] is None


def test_batch_read_accepts_timeline(history):
    prepared = validate({"calls": [
        {"id": "timeline", "name": "timeline", "arguments": {"session": "child", "limit": 1}},
    ]}, "batch_read")
    assert prepared.rejection is None

    async def valid():
        return True

    async def execute(tool, session_id, *, on_start):
        return await server.execute_prepared(tool, session_id=session_id, on_start=on_start)

    async def run():
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        try:
            return await runtime.run(prepared.prepared, session_id="root")
        finally:
            await runtime.close()

    body = asyncio.run(run())
    record = body["results"][0]
    assert record["status"] == "success"
    assert record["response"]["result"]["pagination"]["has_more"] is True


def test_internal_mcp_resolves_caller_self_and_parent(history):
    self_page = asyncio.run(server.dispatch_tool("timeline", {"session": "self"}, session_id="child"))
    parent_page = asyncio.run(server.dispatch_tool("timeline", {"session": "parent"}, session_id="child"))
    assert {e["session_id"] for e in self_page["result"]["items"]} == {"child"}
    assert parent_page["exit_code"] == 0
    assert parent_page["result"]["items"] == []


@pytest.mark.parametrize("option", ["session", "spawned_by", "spawn_tree", "descendants", "siblings"])
@pytest.mark.parametrize("keyword", ["self", "parent"])
def test_external_and_remote_require_explicit_ids(option, keyword):
    with pytest.raises(ValueError, match="explicit IDs"):
        prepare_tool("timeline", {option: keyword}, registry=tools_by_name(), external=True)
    with pytest.raises(RemoteUsageError):
        reject_host_bound(resolve_command(["timeline", f"--{option.replace('_', '-')}={keyword}"]))
    assert prepare_tool("timeline", {option: "explicit"}, registry=tools_by_name(), external=True)


def test_rpc_has_no_session_identity(history, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami._walk_ppids", lambda: iter(()))
    response = rpc({"session": "self"})
    assert response["exit_code"] != 0
    assert response["result"] is None


def test_registry_renders_repeated_and_dash_prefixed_values(history):
    project, _ = history
    arguments = {
        "project": project.id, "annotation": ["team=backend", "ready=true"],
        "state": ["starting", "assistant_turn", "awaiting_user_input", "user_turn", "dead"], "limit": 1,
    }
    first = rpc(arguments)["result"]
    arguments["before"] = first["pagination"]["next_cursor"]
    second = rpc(arguments)["result"]
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] > second["items"][0]["id"]
