"""The request-origin carrier (design 2026-10-14-timeline-actor-origin sections 2, 5, 6)."""

import asyncio
import threading

import pytest

from tests.timeline_helpers import ORIGIN_MAPPING
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, ORIGIN_PAYLOAD_KEY, SYSTEM_ORIGIN, UI_ORIGIN,
    VIA_MAX_LENGTH, VIA_VALUES, RequestOrigin, agent_cli_origin, capture_origin, current_origin,
    mcp_session_origin, origin_from_payload, origin_scope, origin_to_payload, resolve_event_origin, rpc_origin,
    system_marker,
)

AGENT = mcp_session_origin("sess-A")


@pytest.mark.parametrize(("origin", "expected"), ORIGIN_MAPPING)
def test_each_entry_path_builds_its_row_of_the_mapping_table(origin, expected):
    assert tuple(origin) == expected


def test_constants_are_pinned_to_the_spec():
    assert VIA_VALUES == {"ui", "cli", "rpc", "mcp_internal", "mcp_external"}
    assert (VIA_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, ACTOR_NAME_MAX_LENGTH) == (20, 255, 80)
    assert DEFAULT_ORIGIN == UI_ORIGIN == RequestOrigin("user", "ui")
    assert SYSTEM_ORIGIN == RequestOrigin("system")
    assert ORIGIN_PAYLOAD_KEY == "_origin"


def test_an_unset_carrier_reads_none_and_captures_the_default():
    assert current_origin() is None
    assert capture_origin() == DEFAULT_ORIGIN


def test_a_scope_sets_then_restores_and_nests():
    with origin_scope(AGENT):
        assert current_origin() == AGENT
        with origin_scope(UI_ORIGIN):
            assert current_origin() == UI_ORIGIN
        assert current_origin() == AGENT
    assert current_origin() is None


def test_a_none_scope_is_an_explicit_unset_inside_an_outer_origin():
    with origin_scope(AGENT):
        with origin_scope(None):
            assert current_origin() is None
            assert capture_origin() == DEFAULT_ORIGIN
        assert current_origin() == AGENT


def test_a_scope_restores_on_exception():
    with pytest.raises(RuntimeError), origin_scope(AGENT):
        raise RuntimeError("boom")
    assert current_origin() is None


def test_the_visibility_facts():
    """Design section 6.3: what sees the carrier and what does not."""

    async def scenario():
        loop = asyncio.get_running_loop()
        gate = asyncio.Event()

        async def long_lived():
            await gate.wait()
            return current_origin()

        async def read():
            return current_origin()

        def worker():
            return asyncio.run_coroutine_threadsafe(read(), loop).result()

        early = asyncio.create_task(long_lived())  # created before the set(): keeps the old context
        with origin_scope(AGENT):
            late = asyncio.create_task(long_lived())
            gate.set()
            in_thread = await asyncio.to_thread(current_origin)
            in_threadsafe = await asyncio.to_thread(worker)
            plain = []
            thread = threading.Thread(target=lambda: plain.append(current_origin()))
            thread.start()
            thread.join()
            return await early, await late, in_thread, in_threadsafe, plain[0]

    early, late, in_thread, in_threadsafe, plain = asyncio.run(scenario())
    assert (early, plain) == (None, None)
    assert (late, in_thread, in_threadsafe) == (AGENT, AGENT, AGENT)


def test_the_system_marker_is_scoped_and_restores_the_request_origin():
    async def scenario():
        with origin_scope(UI_ORIGIN):
            with system_marker():
                inside = current_origin()
                await asyncio.sleep(0)
            return inside, current_origin()

    assert asyncio.run(scenario()) == (SYSTEM_ORIGIN, UI_ORIGIN)


def test_concurrent_requests_never_mix():
    async def request(origin, gate, seen):
        with origin_scope(origin):
            await gate.wait()
            seen.append((origin, current_origin()))

    async def scenario():
        gate, seen = asyncio.Event(), []
        origins = (AGENT, UI_ORIGIN, rpc_origin("tok"), agent_cli_origin("sess-B"))
        tasks = [asyncio.create_task(request(origin, gate, seen)) for origin in origins]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*tasks)
        return seen

    seen = asyncio.run(scenario())
    assert len(seen) == 4 and all(expected == got for expected, got in seen)


@pytest.mark.parametrize("origin", [pytest.param(o, id=i) for o, i in (
    (UI_ORIGIN, "ui"), (agent_cli_origin("S"), "agent"), (RequestOrigin("external", "mcp_external", "c", "N"), "ext"),
)])
def test_the_payload_codec_round_trips(origin):
    assert origin_from_payload(origin_to_payload(origin)) == origin


@pytest.mark.parametrize("raw", [
    None, "ui", [], {}, {"actor": "system", "via": "ui"}, {"actor": ["user"]}, {"actor": "root"},
    {"actor": "user", "via": 3}, {"actor": "user", "actor_ref": ["x"]}, {"actor": "user", "actor_name": 1.5},
])
def test_the_payload_codec_rejects_garbage_and_forged_system_origins(raw):
    assert origin_from_payload(raw) is None


def test_resolve_prefers_a_captured_origin_then_an_explicit_actor_then_the_carrier():
    captured = RequestOrigin("external", "mcp_external", "c1", "Name")
    with origin_scope(AGENT):
        assert resolve_event_origin("system", captured) == captured  # captured full origin wins
        assert resolve_event_origin("agent", None) == RequestOrigin("agent")  # explicit actor: NULL fields
        assert resolve_event_origin(None, None) == AGENT  # carrier
    assert resolve_event_origin(None, None) == DEFAULT_ORIGIN  # nothing known: the default
    with system_marker():
        assert resolve_event_origin(None, None) == SYSTEM_ORIGIN  # the marker is not "unset"


def test_resolve_falls_back_to_the_default_when_the_carrier_cannot_be_read(monkeypatch):
    def boom():
        raise RuntimeError("carrier unreadable")

    monkeypatch.setattr("twicc.request_origin.capture_origin", boom)
    assert resolve_event_origin(None, None) == DEFAULT_ORIGIN
    assert resolve_event_origin("system", None) == RequestOrigin("system")  # a system writer never falls to the default
