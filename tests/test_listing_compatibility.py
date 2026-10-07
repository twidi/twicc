"""Retired listing flags remain accepted without being advertised."""

import asyncio
from types import SimpleNamespace

import jsonschema
import orjson
import pytest
from django.test import RequestFactory
from django.utils import timezone
from mcp import types as mcp_types

from twicc.core.models import Project, Session
from twicc.mcp import server
from twicc.mcp.batch_contract import validate_batch
from twicc.mcp.dispatch import prepare_tool
from twicc.mcp.tools import MCP_READ_ONLY_PATHS, _click_leaf, iter_mcp_tools, tools_by_name
from twicc.rpc.invoker import invoke
from twicc.rpc.views import dispatch


@pytest.fixture
def session(transactional_db, monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    project = Project.objects.create(id="-tmp-compat", directory="/tmp/compat")
    return Session.objects.create(
        id="compat-session", project=project, provider="claude_code", file_path="compat.jsonl",
        user_message_count=1, created_at=timezone.now(),
    )


@pytest.mark.parametrize("flags", [{"slim": True}, {"paginated": True}, {"slim": True, "paginated": True}])
def test_rpc_accepts_retired_listing_fields(session, flags):
    async def run(body):
        request = RequestFactory().post("/rpc/sessions", data=orjson.dumps(body), content_type="application/json")
        return await dispatch(request, "sessions")

    plain = asyncio.run(run({}))
    result = asyncio.run(run(flags))
    assert result.status_code == 200
    payload = orjson.loads(result.content)
    assert payload["exit_code"] == 0
    assert payload == orjson.loads(plain.content)
    assert payload["result"]["items"][0]["id"] == session.id


@pytest.mark.parametrize("external", [False, True])
def test_all_retired_options_validate_without_being_advertised(external):
    registry = tools_by_name()
    tools = {tool.name: tool for tool in iter_mcp_tools()}
    count = 0
    for name, spec in registry.items():
        retired = {
            param.name: True for param in _click_leaf(spec.path).params
            if getattr(param, "hidden", False) and param.name in {"slim", "paginated"}
        }
        if not retired or (external and name == "whoami"):
            continue
        count += 1
        arguments = {
            key: ["compat-session"] if schema["type"] == "array" else "compat-session"
            for key, schema in spec.json_schema["properties"].items()
            if key in spec.json_schema.get("required", [])
        }
        if external and name == "topology":
            arguments["session_id"] = "compat-session"
        arguments.update(retired)
        assert not retired.keys() & tools[name].input_schema["properties"].keys()
        assert "--slim" not in orjson.dumps(tools[name].input_schema).decode()
        assert "--paginated" not in orjson.dumps(tools[name].input_schema).decode()
        prepared = prepare_tool(name, arguments, registry=registry, external=external)
        assert prepared.arguments == arguments
        result = validate_batch(
            "batch_read", {"calls": [{"id": "legacy", "name": name, "arguments": arguments}]},
            registry=registry, read_only_paths=MCP_READ_ONLY_PATHS, external=external, batch_id="compat",
        )
        assert result.rejection is None, (name, result.rejection)
    assert count >= 15


def test_mcp_call_executes_retired_listing_fields(session):
    params = mcp_types.CallToolRequestParams(name="sessions", arguments={"slim": True, "paginated": True})
    result = asyncio.run(server._call_tool(SimpleNamespace(request=None), params))
    assert not result.is_error, result
    payload = orjson.loads(result.content[0].text)
    assert payload["exit_code"] == 0
    assert payload["result"]["items"][0]["id"] == session.id


@pytest.mark.parametrize("field", ["slim", "paginated"])
def test_mcp_rejects_invalid_compatibility_values(field):
    with pytest.raises(jsonschema.ValidationError):
        prepare_tool("sessions", {field: "true"}, registry=tools_by_name(), external=False)


def test_compatibility_fields_do_not_apply_to_unrelated_commands():
    with pytest.raises(jsonschema.ValidationError):
        prepare_tool("info", {"slim": True}, registry=tools_by_name(), external=False)


@pytest.mark.parametrize("argv", [
    ["sessions"],
    ["sessions", "get", "compat-session"],
    ["session", "compat-session"],
    ["session", "compat-session", "agents"],
    ["topology", "compat-session"],
])
def test_slim_does_not_override_or_reject_full(session, argv):
    plain = invoke([*argv, "--full"])
    result = invoke([*argv, "--full", "--slim"])
    assert result.exit_code == 0, result.error
    assert result.result == plain.result


@pytest.mark.parametrize("flags", [{"slim": "true"}, {"paginated": 1}])
def test_rpc_rejects_invalid_compatibility_values(flags):
    request = RequestFactory().post("/rpc/sessions", data=orjson.dumps(flags), content_type="application/json")
    result = asyncio.run(dispatch(request, "sessions"))
    assert result.status_code == 400


def test_rpc_discovery_does_not_advertise_compatibility_flags():
    from twicc.rpc.generator import build_registry
    from twicc.rpc.openapi import build_openapi

    schema = orjson.dumps(build_openapi(build_registry())).decode()
    assert '"slim"' not in schema and '"paginated"' not in schema
    assert "--slim" not in schema and "--paginated" not in schema
