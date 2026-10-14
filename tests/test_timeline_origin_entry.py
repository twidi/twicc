"""RPC and MCP entry points set the origin (design sections 4, 5, 6; verification 3a, 3b, 3c, 4, 8b)."""

import asyncio
import contextvars
from types import SimpleNamespace

import httpx
import pytest
from django.test import AsyncClient
from mcp import types as mcp_types

from twicc.auth import tokens as api_tokens
from twicc.auth.tokens import TokenRecord
from twicc.mcp import batch as batch_module
from twicc.mcp import identity, server
from twicc.mcp.batch import BatchRuntime
from twicc.mcp.batch_contract import PreparedBatch, PreparedCall
from twicc.mcp.dispatch import PreparedTool
from twicc.mcp.endpoint import _authenticate, handle_mcp, mcp_lifespan
from twicc.mcp.identity import ExternalCaller, TOKEN_ID_SCOPE_KEY, external_caller, mint_session_token
from twicc.mcp.tools import tools_by_name
from twicc.request_origin import (
    UI_ORIGIN, current_origin, mcp_external_origin, mcp_session_origin, mcp_token_origin, origin_scope, rpc_origin,
)
from twicc.rpc.invoker import InvocationResult

pytestmark = pytest.mark.django_db(transaction=True)


# --- RPC ---------------------------------------------------------------------------------------------------


@pytest.fixture
def tokens_store(monkeypatch):
    records: dict[str, TokenRecord] = {}
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: bool(records))
    monkeypatch.setattr(api_tokens, "verify_token", lambda token: records.get(token))
    return records


@pytest.fixture
def invoked(monkeypatch):
    """The origins seen by the command invoker, one per executed RPC command."""
    seen = []

    def fake_invoke(argv):
        seen.append(current_origin())
        return InvocationResult(exit_code=0, result={"ok": True}, error=None)

    monkeypatch.setattr("twicc.rpc.views.invoke", fake_invoke)
    return seen


def _post(path, **extra):
    return asyncio.run(AsyncClient().post(path, data=b"", content_type="application/json", **extra))


def test_an_rpc_call_with_a_token_carries_the_token_id(tokens_store, invoked):
    tokens_store["secret"] = TokenRecord(id="tok_abc", name="t", digest="x", created_at="", last_used_at=None)
    assert _post("/rpc/sessions", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert invoked == [rpc_origin("tok_abc")]


def test_an_rpc_call_with_no_token_record_has_no_ref(tokens_store, invoked, settings):
    settings.TWICC_PASSWORD_HASH = ""  # neither a password nor a token: the instance is open to loopback
    assert _post("/rpc/sessions").status_code == 200
    assert invoked == [rpc_origin(None)]


def test_a_cookie_scope_rpc_call_sets_no_origin(tokens_store, invoked, settings):
    # The cookie scope is read-only and writes no timeline event (design section 4): nothing is set.
    from importlib import import_module

    from django.conf import settings as dj_settings

    from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint

    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$test$deadbeef"
    client = AsyncClient()
    store = import_module(dj_settings.SESSION_ENGINE).SessionStore()
    store[SESSION_AUTH_KEY] = True
    store[SESSION_FINGERPRINT_KEY] = compute_fingerprint(settings.TWICC_PASSWORD_HASH)
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key
    response = asyncio.run(client.post("/rpc/sessions", data=b"", content_type="application/json"))
    assert response.status_code == 200
    assert invoked == [None]


# --- Internal MCP --------------------------------------------------------------------------------------------


def _ctx(token="", *, token_id=None):
    scope = {TOKEN_ID_SCOPE_KEY: token_id} if token_id else {}
    return SimpleNamespace(request=SimpleNamespace(headers={"authorization": f"Bearer {token}"}, scope=scope))


@pytest.fixture
def executed(monkeypatch):
    """Replace the command execution: record the origin each tool call runs under."""
    seen = []

    async def fake_execute(prepared, *, session_id, on_start=None):
        seen.append(current_origin())
        await asyncio.sleep(0)
        return {"exit_code": 0, "result": None, "error": None}

    monkeypatch.setattr(server, "execute_prepared", fake_execute)
    return seen


def _call(ctx, name="workspaces", arguments=None):
    return server._call_tool(ctx, mcp_types.CallToolRequestParams(name=name, arguments=arguments or {}))


def test_an_internal_session_token_gives_the_calling_session(executed):
    asyncio.run(_call(_ctx(mint_session_token("sess-A"))))
    assert executed == [mcp_session_origin("sess-A")]


def test_a_local_api_token_gives_the_token_id(executed):
    asyncio.run(_call(_ctx("twicc_pat_xxx", token_id="tok_9")))
    assert executed == [mcp_token_origin("tok_9")]


def test_an_external_call_is_never_a_cli_call(executed):
    async def scenario():
        token = external_caller.set(ExternalCaller("conn-1", "ChatGPT"))
        try:
            await _call(_ctx("oauth-access-token"))
        finally:
            external_caller.reset(token)

    asyncio.run(scenario())
    assert executed == [mcp_external_origin("conn-1", "ChatGPT")]
    assert executed[0].via == "mcp_external" and executed[0].actor == "external"


def test_an_internal_call_is_never_an_agent_cli_call(executed):
    # That the backend never walks its own process ancestry is pinned where the walk could happen:
    # ``test_timeline_origin_drop::test_backend_mode_submit_carries_the_origin_through_the_loop`` (T5).
    asyncio.run(_call(_ctx(mint_session_token("sess-A"))))
    assert executed[0].via == "mcp_internal" and executed[0].actor_ref == "sess-A"


def test_two_overlapping_calls_never_mix_their_origins(monkeypatch):
    seen = {}

    async def scenario():
        gate = asyncio.Event()

        async def fake_execute(prepared, *, session_id, on_start=None):
            await gate.wait()
            seen[session_id] = current_origin()
            return {"exit_code": 0, "result": None, "error": None}

        monkeypatch.setattr(server, "execute_prepared", fake_execute)
        calls = [asyncio.create_task(_call(_ctx(mint_session_token(sid)))) for sid in ("sess-A", "sess-B")]
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.gather(*calls)

    asyncio.run(scenario())
    assert seen == {"sess-A": mcp_session_origin("sess-A"), "sess-B": mcp_session_origin("sess-B")}


def test_the_call_runs_under_its_origin_and_restores_the_previous_one(executed):
    # Inside an outer origin different from the call's: the tool must see the CALL's origin (it fails when
    # ``_call_tool`` does not set one) and the outer one must be back afterwards.
    async def scenario():
        with origin_scope(UI_ORIGIN):
            await _call(_ctx(mint_session_token("sess-A")))
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert executed == [mcp_session_origin("sess-A")]


def test_the_external_name_is_read_at_call_time(executed):
    async def scenario(name):
        token = external_caller.set(ExternalCaller("conn-1", name))
        try:
            await _call(_ctx("oauth-access-token"))
        finally:
            external_caller.reset(token)

    asyncio.run(scenario("Old name"))
    asyncio.run(scenario("Renamed"))
    assert [origin.actor_name for origin in executed] == ["Old name", "Renamed"]


# --- MCP batch ------------------------------------------------------------------------------------------------


def _batch(count=3, *, mode="parallel"):
    spec = tools_by_name()["session"]
    return PreparedBatch("batch", mode, "continue", tuple(
        PreparedCall(i, str(i), PreparedTool("session", spec, {"session_id": str(i)})) for i in range(count)
    ))


async def _valid(grant):
    return True


@pytest.mark.parametrize("mode", ["parallel", "sequential"])
def test_batch_children_carry_the_origin_of_the_batch_call(mode, monkeypatch):
    # ``BatchRuntime.run`` creates its children inside the request's scope, so they would inherit the
    # origin by themselves and the test would pass without the explicit ``request_origin.set`` in
    # ``_child``. Launch them in an EMPTY context instead (what an executor or a detached task gives):
    # only the explicit set can then supply the origin.
    def launch_in_an_empty_context(self, state, call):
        child = asyncio.create_task(self._child(state, call), context=contextvars.Context())
        state.children.add(child)
        self.children.add(child)
        child.add_done_callback(state.children.discard)
        child.add_done_callback(self.children.discard)
        child.add_done_callback(batch_module._consume)

    monkeypatch.setattr(BatchRuntime, "_launch", launch_in_an_empty_context)

    async def scenario():
        seen = []

        async def execute(tool, session_id, *, on_start):
            on_start()
            await asyncio.sleep(0)
            seen.append(current_origin())
            return {"exit_code": 0, "result": None, "error": None}

        runtime = BatchRuntime(execute=execute, check_grant=_valid)
        try:
            with origin_scope(mcp_session_origin("sess-A")):
                await runtime.run(_batch(mode=mode), session_id="sess-A")
        finally:
            await runtime.close()
        return seen

    assert asyncio.run(scenario()) == [mcp_session_origin("sess-A")] * 3


# --- The real endpoint ------------------------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def fresh_managers(monkeypatch):
    for name in ("_session_manager", "_external_manager"):
        monkeypatch.setattr(f"twicc.mcp.server.{name}", None)
    yield
    for name in ("_session_manager", "_external_manager"):
        monkeypatch.setattr(f"twicc.mcp.server.{name}", None)


def test_authenticate_returns_the_session_or_the_token_id(monkeypatch):
    monkeypatch.setattr(
        "twicc.mcp.endpoint.verify_token",
        lambda token: TokenRecord(id="tok_7", name="t", digest="x", created_at="", last_used_at=None)
        if token == "good" else None,
    )

    def scope(token):
        return {"headers": [(b"authorization", f"Bearer {token}".encode())]}

    assert _authenticate(scope(mint_session_token("sess-A"))) == ("sess-A", None)
    assert _authenticate(scope("good")) == (None, "tok_7")
    assert _authenticate(scope("bad")) is None
    assert _authenticate(scope(identity.TOKEN_PREFIX + "forged.signature")) is None
    assert _authenticate({"headers": []}) is None


def test_the_endpoint_publishes_the_token_id_and_the_tool_runs_under_its_origin(fresh_managers, monkeypatch):
    monkeypatch.setattr("twicc.mcp.endpoint.verify_token", lambda token: TokenRecord(
        id="tok_7", name="t", digest="x", created_at="", last_used_at=None) if token == "good" else None)
    seen = []

    def fake_run_invoke(argv):
        seen.append(current_origin())
        return InvocationResult(exit_code=0, result=[], error=None)

    monkeypatch.setattr(server, "_run_invoke", fake_run_invoke)
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream",
               "authorization": "Bearer good"}

    async def scenario():
        async with mcp_lifespan():
            transport = httpx.ASGITransport(app=handle_mcp, client=("127.0.0.1", 9999))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "workspaces", "arguments": {}},
                })
                return response.status_code

    assert asyncio.run(scenario()) == 200
    assert seen == [mcp_token_origin("tok_7")]
