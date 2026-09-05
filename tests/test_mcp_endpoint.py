"""End-to-end JSON-RPC over the raw-ASGI /mcp endpoint (sync tests, asyncio.run)."""

import asyncio
import base64
import contextlib

import httpx
import orjson
import pytest

from twicc.mcp import identity
from twicc.mcp.endpoint import handle_mcp, mcp_lifespan


@pytest.fixture(autouse=True)
def _fresh_session_manager(monkeypatch):
    """The streamable-HTTP session manager's ``.run()`` is single-shot per
    instance; drop the process-wide singleton so each test starts a fresh one."""
    monkeypatch.setattr("twicc.mcp.server._session_manager", None)
    monkeypatch.setattr("twicc.mcp.server._external_manager", None)
    yield
    monkeypatch.setattr("twicc.mcp.server._session_manager", None)
    monkeypatch.setattr("twicc.mcp.server._external_manager", None)


HEADERS_BASE = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _rpc(method: str, params: dict | None = None, id_: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id_ is not None:
        msg["id"] = id_
    return msg


INIT = _rpc("initialize", {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
})


@contextlib.asynccontextmanager
async def _client():
    async with mcp_lifespan():
        transport = httpx.ASGITransport(app=handle_mcp, client=("127.0.0.1", 9999))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def test_unauthenticated_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post("/mcp", json=INIT, headers=HEADERS_BASE)

    assert asyncio.run(scenario()).status_code == 401


def test_bad_token_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post(
                "/mcp", json=INIT,
                headers={**HEADERS_BASE, "authorization": "Bearer twicc_mcp_x.deadbeef"},
            )

    assert asyncio.run(scenario()).status_code == 401


@pytest.mark.django_db(transaction=True)
def test_initialize_list_call_roundtrip():
    headers = {
        **HEADERS_BASE,
        "authorization": f"Bearer {identity.mint_session_token('some-session')}",
    }

    async def scenario():
        async with _client() as client:
            r = await client.post("/mcp", json=INIT, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["result"]["serverInfo"]["name"] == "twicc"

            r = await client.post(
                "/mcp", json=_rpc("notifications/initialized", id_=None), headers=headers,
            )
            assert r.status_code in (200, 202)

            r = await client.post("/mcp", json=_rpc("tools/list", {}, 2), headers=headers)
            assert r.status_code == 200, r.text
            names = {t["name"] for t in r.json()["result"]["tools"]}
            assert "whoami" in names and "create_session" in names

            r = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "workspaces", "arguments": {}}, 3),
                headers=headers,
            )
            assert r.status_code == 200, r.text
            payload = r.json()["result"]
            assert payload["structuredContent"]["exit_code"] == 0

    asyncio.run(scenario())


def _session_headers(session_id):
    return {
        **HEADERS_BASE,
        "authorization": f"Bearer {identity.mint_session_token(session_id)}",
    }


@pytest.mark.parametrize("exit_code", [0, 3])
def test_tool_wire_result_preserves_envelope(monkeypatch, exit_code):
    from twicc.mcp import server

    envelope = {"exit_code": exit_code, "result": {"items": [1]}, "error": "rejected" if exit_code else None}

    async def dispatch(name, arguments, *, session_id):
        assert name == "workspaces" and arguments == {} and session_id == "caller"
        return envelope

    monkeypatch.setattr(server, "dispatch_tool", dispatch)

    async def scenario():
        async with _client() as client:
            response = await client.post(
                "/mcp", json=_rpc("tools/call", {"name": "workspaces"}),
                headers=_session_headers("caller"),
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["structuredContent"] == envelope
            assert orjson.loads(result["content"][0]["text"]) == envelope
            assert result.get("isError", False) is False

    asyncio.run(scenario())


@pytest.mark.parametrize("unknown", [False, True])
def test_tool_failures_remain_mcp_tool_errors(monkeypatch, unknown):
    from twicc.mcp import server

    async def dispatch(name, arguments, *, session_id):
        if unknown:
            raise server.UnknownToolError(name)
        raise RuntimeError("command failed")

    monkeypatch.setattr(server, "dispatch_tool", dispatch)

    async def scenario():
        async with _client() as client:
            response = await client.post(
                "/mcp", json=_rpc("tools/call", {"name": "workspaces", "arguments": {}}),
                headers=_session_headers("caller"),
            )
            result = response.json()["result"]
            assert result["isError"] is True
            assert result["content"][0]["text"] == (
                "Unknown tool: workspaces" if unknown else "command failed"
            )

    asyncio.run(scenario())


def test_concurrent_http_calls_keep_session_identity(monkeypatch):
    from twicc.mcp import server

    async def scenario():
        both_entered = asyncio.Event()
        callers = []

        async def dispatch(name, arguments, *, session_id):
            callers.append(session_id)
            if len(callers) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=5)
            return {"exit_code": 0, "result": session_id, "error": None}

        monkeypatch.setattr(server, "dispatch_tool", dispatch)
        async with _client() as client:
            responses = await asyncio.gather(*(
                client.post(
                    "/mcp", json=_rpc("tools/call", {"name": "workspaces", "arguments": {}}),
                    headers=_session_headers(sid),
                ) for sid in ("first", "second")
            ))
            assert [r.json()["result"]["structuredContent"]["result"] for r in responses] == ["first", "second"]

    asyncio.run(scenario())


def test_invalid_tool_arguments_are_rejected_before_dispatch(monkeypatch):
    from twicc.mcp import server

    async def dispatch(*args, **kwargs):
        pytest.fail("invalid arguments must not reach the command")

    monkeypatch.setattr(server, "dispatch_tool", dispatch)

    async def scenario():
        async with _client() as client:
            response = await client.post(
                "/mcp", json=_rpc("tools/call", {"name": "create_workspace", "arguments": {"name": 123}}),
                headers=_session_headers("caller"),
            )
            result = response.json()["result"]
            assert result["isError"] is True
            assert "Input validation error" in result["content"][0]["text"]

    asyncio.run(scenario())


def test_large_attachment_request_reaches_dispatch(monkeypatch):
    from twicc.mcp import server

    # A valid 4 MiB attachment exceeds v2's default HTTP cap once base64 encoded.
    attachment = "data:image/png;base64," + base64.b64encode(b"x" * (4 * 1024 * 1024)).decode()

    async def dispatch(name, arguments, *, session_id):
        assert arguments["attach"] == [attachment]
        return {"exit_code": 0, "result": None, "error": None}

    monkeypatch.setattr(server, "dispatch_tool", dispatch)

    async def scenario():
        async with _client() as client:
            response = await client.post(
                "/mcp", json=_rpc("tools/call", {
                    "name": "send_message",
                    "arguments": {"session_id": "self", "prompt": "Inspect this", "attach": [attachment]},
                }), headers=_session_headers("caller"),
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["structuredContent"]["exit_code"] == 0

    asyncio.run(scenario())
