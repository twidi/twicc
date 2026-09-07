"""Preparation preserves caller checks and never invokes commands."""
from types import SimpleNamespace
import asyncio

import pytest
from mcp import types as mcp_types

from twicc.mcp.dispatch import prepare_tool
from twicc.mcp.tools import tools_by_name
from twicc.mcp import server


def test_prepare_copies_arguments():
    args = {"limit": 5}
    prepared = prepare_tool("sessions", args, registry=tools_by_name(), external=False)
    args["limit"] = 10
    assert prepared.arguments == {"limit": 5}


def test_invalid_call_never_submits(monkeypatch):
    called = []
    monkeypatch.setattr(server, "_run_invoke", lambda argv: called.append(argv))
    params = mcp_types.CallToolRequestParams(name="session", arguments={})
    result = asyncio.run(server._call_tool(SimpleNamespace(request=None), params))
    assert result.is_error
    assert called == []


def test_render_failure_does_not_mark_started(monkeypatch):
    prepared = prepare_tool("workspaces", {}, registry=tools_by_name(), external=False)
    def fail(*args):
        raise RuntimeError("render failed")
    monkeypatch.setattr(server, "render_argv", fail)
    started = []
    with pytest.raises(RuntimeError, match="render failed"):
        asyncio.run(server.execute_prepared(prepared, session_id=None, on_start=lambda: started.append(True)))
    assert started == []
