"""Drop requests carry the origin from one common place (design section 6.2; verification 3, 3a)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.timeline_helpers import RECORDED
from twicc.cli._drop_request import transport
from twicc.cli._drop_request.origin import cli_origin, reset_cli_origin_cache
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload
from twicc.request_origin import (
    ORIGIN_PAYLOAD_KEY, agent_cli_origin, current_origin, human_cli_origin, mcp_session_origin, origin_scope,
    origin_to_payload,
)

KINDS = sorted(_KIND_HANDLERS)


@pytest.fixture(autouse=True)
def clean_state():
    RECORDED.clear()
    reset_cli_origin_cache()
    yield
    RECORDED.clear()
    reset_cli_origin_cache()


def _use_recording_service(monkeypatch, kind):
    monkeypatch.setitem(_KIND_HANDLERS, kind, ("tests.timeline_helpers", "recording_service", "updated"))


# --- CLI side -----------------------------------------------------------------------------------------------


def test_local_mode_writes_the_cli_origin_into_the_payload_of_every_kind(monkeypatch):
    written = []

    def fake_write(payload, kind):
        written.append((payload, kind))
        return SimpleNamespace(request_uuid="u1", path=Path("/tmp/u1.json"))

    monkeypatch.setattr(transport, "write_drop_file", fake_write)
    monkeypatch.setattr(transport, "cli_origin", lambda: agent_cli_origin("sess-A"))
    for kind in KINDS:
        transport.submit({"session_id": "s1"}, kind=kind)
    assert [kind for _, kind in written] == KINDS
    expected = {"actor": "agent", "via": "cli", "actor_ref": "sess-A", "actor_name": None}
    assert all(payload[ORIGIN_PAYLOAD_KEY] == expected and payload["session_id"] == "s1" for payload, _ in written)


def test_backend_mode_submit_carries_the_origin_through_the_loop(monkeypatch):
    seen = []

    async def fake_execute(payload, kind):
        seen.append((payload, current_origin()))
        return {"status": "updated"}

    def forbidden():
        raise AssertionError("the process ancestry must not be walked inside the backend")

    monkeypatch.setattr("twicc.drop_requests_watcher.execute_drop_payload", fake_execute)
    monkeypatch.setattr(transport, "cli_origin", forbidden)

    async def scenario():
        loop = asyncio.get_running_loop()

        def worker():
            token = transport.backend_loop.set(loop)
            try:
                with origin_scope(mcp_session_origin("sess-A")):
                    return transport.submit({"session_id": "s1"}, kind="session:update_title")
            finally:
                transport.backend_loop.reset(token)

        submission = await asyncio.to_thread(worker)
        return await asyncio.wrap_future(submission._future)

    assert asyncio.run(scenario()) == {"status": "updated"}
    [(payload, visible)] = seen
    assert payload[ORIGIN_PAYLOAD_KEY]["actor_ref"] == "sess-A" and payload[ORIGIN_PAYLOAD_KEY]["via"] == "mcp_internal"
    assert visible == mcp_session_origin("sess-A")  # the carrier also crossed run_coroutine_threadsafe


def test_backend_mode_with_no_origin_adds_no_key(monkeypatch):
    seen = []

    async def fake_execute(payload, kind):
        seen.append(payload)
        return {"status": "updated"}

    monkeypatch.setattr("twicc.drop_requests_watcher.execute_drop_payload", fake_execute)

    async def scenario():
        loop = asyncio.get_running_loop()

        def worker():
            token = transport.backend_loop.set(loop)
            try:
                return transport.submit({"session_id": "s1"}, kind="session:update_title")
            finally:
                transport.backend_loop.reset(token)

        await asyncio.wrap_future((await asyncio.to_thread(worker))._future)

    asyncio.run(scenario())
    assert ORIGIN_PAYLOAD_KEY not in seen[0]


def test_cli_origin_is_an_agent_when_the_ancestry_resolves(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: SimpleNamespace(id="sess-A"),
    )
    assert cli_origin() == agent_cli_origin("sess-A")


def test_cli_origin_is_a_human_without_a_session_in_the_ancestry(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    assert cli_origin() == human_cli_origin()


def test_cli_origin_falls_back_to_a_human_when_the_lookup_fails(monkeypatch):
    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", boom)
    assert cli_origin() == human_cli_origin()


def test_cli_origin_is_resolved_once_per_process(monkeypatch):
    calls = []

    def lookup():
        calls.append(1)
        return SimpleNamespace(id="sess-A")

    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lookup)
    assert cli_origin() == cli_origin() == agent_cli_origin("sess-A")
    assert len(calls) == 1


# --- Watcher side ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_gets_its_origin_re_established_and_never_sees_the_key(kind, monkeypatch):
    _use_recording_service(monkeypatch, kind)
    origin = agent_cli_origin("sess-A")
    payload = {"kind": kind, "session_id": "s1", ORIGIN_PAYLOAD_KEY: origin_to_payload(origin)}
    assert asyncio.run(execute_drop_payload(payload, kind))["status"] == "updated"
    [(seen_payload, seen_origin)] = RECORDED
    assert seen_origin == origin
    assert ORIGIN_PAYLOAD_KEY not in seen_payload and seen_payload["session_id"] == "s1"
    assert ORIGIN_PAYLOAD_KEY in payload  # the caller's dict is left alone


@pytest.mark.parametrize("kind", KINDS)
def test_a_payload_without_origin_runs_unset_even_inside_a_stale_carrier(kind, monkeypatch):
    _use_recording_service(monkeypatch, kind)

    async def scenario():
        with origin_scope(mcp_session_origin("stale")):
            return await execute_drop_payload({"kind": kind, "session_id": "s1"}, kind)

    asyncio.run(scenario())
    assert RECORDED[0][1] is None  # unset: the default user/ui applies downstream


@pytest.mark.parametrize("raw", [
    {"actor": "system", "via": "ui"}, {"actor": "root", "via": "cli"}, "ui", ["user"], {"actor": "user", "via": 3},
])
def test_a_forged_or_malformed_origin_degrades_to_unset(raw, monkeypatch):
    _use_recording_service(monkeypatch, "session:update_title")
    payload = {"kind": "session:update_title", "session_id": "s1", ORIGIN_PAYLOAD_KEY: raw}
    asyncio.run(execute_drop_payload(payload, "session:update_title"))
    [(seen_payload, seen_origin)] = RECORDED
    assert seen_origin is None and ORIGIN_PAYLOAD_KEY not in seen_payload
