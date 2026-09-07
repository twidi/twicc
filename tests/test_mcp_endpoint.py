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

    async def dispatch(prepared, *, session_id):
        name, arguments = prepared.name, prepared.arguments
        assert name == "workspaces" and arguments == {} and session_id == "caller"
        return envelope

    monkeypatch.setattr(server, "execute_prepared", dispatch)

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

    async def dispatch(prepared, *, session_id):
        name = prepared.name
        if unknown:
            raise server.UnknownToolError(name)
        raise RuntimeError("command failed")

    monkeypatch.setattr(server, "execute_prepared", dispatch)

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

        async def dispatch(prepared, *, session_id):
            callers.append(session_id)
            if len(callers) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=5)
            return {"exit_code": 0, "result": session_id, "error": None}

        monkeypatch.setattr(server, "execute_prepared", dispatch)
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

    monkeypatch.setattr(server, "execute_prepared", dispatch)

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

    async def dispatch(prepared, *, session_id):
        arguments = prepared.arguments
        assert arguments["attach"] == [attachment]
        return {"exit_code": 0, "result": None, "error": None}

    monkeypatch.setattr(server, "execute_prepared", dispatch)

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


def test_batch_catalog_schema_and_read_roundtrip(monkeypatch):
    from jsonschema import validate
    from twicc.mcp import server
    from twicc.mcp.batch_contract import BATCH_OUTPUT_SCHEMA
    from twicc.rpc.invoker import InvocationResult

    monkeypatch.setattr(server, "_run_invoke", lambda argv: InvocationResult(0, {"argv": argv}, None))
    async def scenario():
        async with _client() as client:
            catalog = await client.post("/mcp", json=_rpc("tools/list", {}), headers=_session_headers("caller"))
            assert "result" in catalog.json(), catalog.text
            tools = {tool["name"]: tool for tool in catalog.json()["result"]["tools"]}
            assert tools["batch_read"]["annotations"]["readOnlyHint"] is True
            assert tools["batch"]["annotations"]["readOnlyHint"] is False
            response = await client.post("/mcp", json=_rpc("tools/call", {
                "name": "batch_read", "arguments": {"calls": [
                    {"id": "first", "name": "sessions", "arguments": {"limit": 1}},
                    {"id": "second", "name": "workspaces", "arguments": {}},
                ]},
            }), headers=_session_headers("caller"))
            result = response.json()["result"]
            payload = result["structuredContent"]
            validate(payload, BATCH_OUTPUT_SCHEMA)
            assert orjson.loads(result["content"][0]["text"]) == payload
            assert payload["ok"]
            assert [r["id"] for r in payload["results"]] == ["first", "second"]
    asyncio.run(scenario())


def test_invalid_batch_has_no_side_effects_or_secret_logs(monkeypatch, caplog):
    from twicc.mcp import server
    secret = "SECRET-CREDENTIAL-DO-NOT-LOG"
    seen = []
    monkeypatch.setattr(server, "_run_invoke", lambda argv: seen.append(argv))
    async def scenario():
        async with _client() as client:
            response = await client.post("/mcp", json=_rpc("tools/call", {
                "name": "batch", "arguments": {"calls": [
                    {"id": "first", "name": "create_workspace", "arguments": {"name": "no-side-effect"}},
                    {"id": "second", "name": "session", "arguments": {secret: secret}},
                ]},
            }), headers=_session_headers("caller"))
            result = response.json()["result"]
            assert result["isError"]
            assert result["structuredContent"]["executed"] == 0
            assert seen == []
            assert secret not in response.text
            assert secret not in caplog.text
    asyncio.run(scenario())


def test_batch_body_limit_applies_to_complete_request(monkeypatch):
    from twicc.mcp import server
    monkeypatch.setattr(server, "MAX_REQUEST_BODY_BYTES", 1024)
    async def scenario():
        async with _client() as client:
            response = await client.post("/mcp", json=_rpc("tools/call", {
                "name": "batch", "arguments": {"calls": [
                    {"id": str(i), "name": "send_message", "arguments": {
                        "session_id": "self", "prompt": "x" * 600,
                    }} for i in range(2)
                ]},
            }), headers=_session_headers("caller"))
            assert response.status_code == 413
    asyncio.run(scenario())


def test_batch_lifespan_resets_runtime_between_loops():
    from twicc.mcp import server
    previous = None
    async def scenario():
        nonlocal previous
        async with _client():
            runtime = server._batch_runtime
            assert runtime is not None and runtime is not previous
            previous = runtime
        assert server._batch_runtime is None
        assert not runtime.accepting
    asyncio.run(scenario())
    server._session_manager = None
    server._external_manager = None
    asyncio.run(scenario())


def test_json_transport_disconnect_does_not_promise_command_cancellation(monkeypatch):
    """The SDK JSON POST path can finish a command after http.disconnect."""
    from twicc.mcp import server
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        inbound = asyncio.Queue()
        messages = []
        seen = []
        async def execute(prepared, *, session_id, on_start=None):
            on_start()
            entered.set()
            await release.wait()
            seen.append(prepared.name)
            return {"exit_code": 0, "result": None, "error": None}
        monkeypatch.setattr(server, "execute_prepared", execute)
        body = orjson.dumps(_rpc("tools/call", {"name": "batch_read", "arguments": {"calls": [
            {"id": "one", "name": "workspaces", "arguments": {}},
        ]}}))
        await inbound.put({"type": "http.request", "body": body, "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/mcp", "raw_path": b"/mcp", "query_string": b"",
                 "root_path": "", "server": ("test", 80), "client": ("127.0.0.1", 9999),
                 "headers": [(k.encode(), v.encode()) for k, v in _session_headers("caller").items()]}
        async def send(message):
            messages.append(message)
        async with mcp_lifespan():
            request = asyncio.create_task(handle_mcp(scope, inbound.get, send))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await inbound.put({"type": "http.disconnect"})
                release.set()
                await asyncio.wait_for(request, 2)
                assert seen == ["workspaces"]
            finally:
                release.set()
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_batch_matches_separate_commands_with_local_timings():
    """Check real command parity; timings are observations, never speed assertions."""
    from time import monotonic
    from statistics import median
    calls = [
        {"id": "projects", "name": "projects", "arguments": {}},
        {"id": "sessions", "name": "sessions", "arguments": {"limit": 5}},
        {"id": "workspaces", "name": "workspaces", "arguments": {}},
    ]
    async def scenario():
        timings = {"separate": [], "sequential": [], "parallel": []}
        async with _client() as client:
            for _ in range(4):
                start = monotonic()
                separate = []
                for call in calls:
                    response = await client.post("/mcp", json=_rpc("tools/call", {
                        "name": call["name"], "arguments": call["arguments"],
                    }), headers=_session_headers("caller"))
                    separate.append(response.json()["result"]["structuredContent"])
                timings["separate"].append((monotonic() - start) * 1000)
                for mode in ("sequential", "parallel"):
                    start = monotonic()
                    response = await client.post("/mcp", json=_rpc("tools/call", {
                        "name": "batch_read", "arguments": {"calls": calls, "mode": mode},
                    }), headers=_session_headers("caller"))
                    result = response.json()["result"]["structuredContent"]
                    timings[mode].append((monotonic() - start) * 1000)
                    assert result["ok"], result
                    assert [item["response"] for item in result["results"]] == separate
        print("\nLocal ASGI parity timing (ms; cold then median of 3 warm runs):", {
            name: {"cold": round(values[0], 2), "warm": round(median(values[1:]), 2)}
            for name, values in timings.items()
        })
    asyncio.run(scenario())
