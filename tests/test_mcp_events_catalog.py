"""Frozen contracts, identities and deterministic external-events test seams."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

import jsonschema
import orjson
import pytest

from tests.mcp_events_helpers import (
    Collector,
    ControlledVerification,
    FakeClock,
    FakeRegistry,
    FakeTransport,
    TransportResponse,
    append_assistant,
    assistant_content,
    write_transcript,
)
from twicc.mcp.events import SYSTEM_CLOCK
from twicc.mcp.events.catalog import (
    CAPABILITY_EXTENSION,
    EVENT_DEFINITION,
    EVENT_NAME,
    INPUT_SCHEMA,
    PAYLOAD_SCHEMA,
    canonical_identity,
    event_id,
    subscription_id,
)


def test_event_contract():
    assert EVENT_NAME == "session.concluded"
    assert CAPABILITY_EXTENSION == "io.modelcontextprotocol/events"
    assert EVENT_DEFINITION["name"] == EVENT_NAME
    assert EVENT_DEFINITION["delivery"] == ["webhook"]
    assert INPUT_SCHEMA["required"] == ["session_id"]
    assert INPUT_SCHEMA["additionalProperties"] is False
    assert set(INPUT_SCHEMA["properties"]) == {"session_id", "wait_background", "since_line_num"}
    assert INPUT_SCHEMA["properties"]["since_line_num"]["maximum"] == 2147483647
    assert INPUT_SCHEMA["properties"]["wait_background"]["description"] == (
        "A final message read while background work runs behind the agent (a subagent, background shell, Monitor, "
        "scheduled wake-up or goal) does not count; the event fires on the first final message read once that work "
        "has ended. If the agent then starts a new turn, the ignored message is skipped and the new turn's "
        "conclusion is delivered. If no new answer comes and the agent stays idle, no event fires; the ignored "
        "message stays readable with the session content tools (after a server restart it can still be delivered "
        "as replied once the background work has ended). A dead agent fires 'ended' carrying it."
    )
    assert PAYLOAD_SCHEMA["required"] == ["session_id", "session_title", "reply"]
    assert "additionalProperties" not in PAYLOAD_SCHEMA
    assert PAYLOAD_SCHEMA["properties"]["reply"]["required"] == [
        "outcome",
        "line_num",
        "is_final",
        "since_line_num",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"session_id": "s"},
        {"session_id": "s", "wait_background": False, "since_line_num": 0},
        {"session_id": "s", "since_line_num": 2147483647},
        {"session_id": "s", "since_line_num": 3.0},
    ],
)
def test_valid_input(arguments):
    jsonschema.validate(arguments, INPUT_SCHEMA)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"session_id": ""},
        {"session_id": 1},
        {"session_id": "s", "outcome": "replied"},
        {"session_id": "s", "include_text": True},
        {"session_id": "s", "since_line_num": -1},
        {"session_id": "s", "since_line_num": 2147483648},
        {"session_id": "s", "since_line_num": True},
        {"session_id": "s", "wait_background": 1},
    ],
)
def test_invalid_input(arguments):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arguments, INPUT_SCHEMA)


@pytest.mark.parametrize("outcome", ["replied", "awaiting_user_input", "provider_error", "ended"])
def test_payload_contract(outcome):
    reply = {"outcome": outcome, "line_num": None, "is_final": None, "since_line_num": 0}
    data = {"session_id": "s", "session_title": None, "reply": reply}
    if outcome == "awaiting_user_input":
        data["request_type"] = "ask_user_question"
    elif outcome == "provider_error":
        reply["text"] = ""
    jsonschema.validate(data, PAYLOAD_SCHEMA)
    data["future_additive_field"] = True
    jsonschema.validate(data, PAYLOAD_SCHEMA)


def test_subscription_identity_exact_digest():
    arguments = {"wait_background": True, "session_id": "session-é", "since_line_num": 99}
    expected = b'{"session_id":"session-\xc3\xa9","wait_background":true}'
    assert canonical_identity(arguments) == expected
    identity = b"connection\x00https://callback.example/x\x00session.concluded\x00" + expected
    assert subscription_id("connection", "https://callback.example/x", EVENT_NAME, arguments) == (
        "sub_" + sha256(identity).hexdigest()[:32]
    )


def test_identity_defaults_key_order_and_cursor_independence():
    identify = lambda arguments: subscription_id("c", "https://example.com/", EVENT_NAME, arguments)
    assert identify({"session_id": "s"}) == identify({"wait_background": False, "session_id": "s"})
    assert identify({"session_id": "s", "since_line_num": 1}) == identify(
        {
            "since_line_num": 999,
            "session_id": "s",
            "wait_background": False,
        }
    )
    assert identify({"wait_background": True, "session_id": "s"}) == identify(
        {
            "session_id": "s",
            "wait_background": True,
        }
    )
    assert identify({"session_id": "s"}) != identify({"session_id": "s", "wait_background": True})
    assert identify({"session_id": "s"}) != identify({"session_id": "other"})


@pytest.mark.parametrize(
    "connection,url,name",
    [
        ("other", "https://example.com/", EVENT_NAME),
        ("c", "https://EXAMPLE.com/", EVENT_NAME),
        ("c", "https://example.com:443/", EVENT_NAME),
        ("c", "https://example.com", EVENT_NAME),
        ("c", "https://example.com/x", EVENT_NAME),
        ("c", "https://example.com/?a=1&b=2", EVENT_NAME),
        ("c", "https://example.com/", "other.event"),
    ],
)
def test_identity_connection_url_and_name_sensitivity(connection, url, name):
    base = subscription_id("c", "https://example.com/", EVENT_NAME, {"session_id": "s"})
    assert base != subscription_id(connection, url, name, {"session_id": "s"})


def test_event_identity_exact_digest_and_keys():
    assert event_id("sub_id", "replied", "0:42") == ("evt_" + sha256(b"sub_id\x00replied\x000:42").hexdigest()[:32])
    base = event_id("sub_id", "replied", "0:42")
    assert base != event_id("sub_other", "replied", "0:42")
    assert base != event_id("sub_id", "ended", "0:42")
    assert base != event_id("sub_id", "replied", "1:42")
    assert event_id("sub_id", "awaiting_user_input", "request-a") != event_id(
        "sub_id",
        "awaiting_user_input",
        "request-b",
    )
    assert event_id("sub_id", "ended", "last_line:0:10") != event_id(
        "sub_id",
        "ended",
        "last_line:0:11",
    )


def test_clock_domains_move_independently():
    fake = FakeClock()
    before = fake.clock.utcnow()
    fake.advance(2, monotonic=False)
    assert fake.clock.utcnow().timestamp() == before.timestamp() + 2
    assert fake.clock.epoch() == before.timestamp() + 2
    assert fake.clock.monotonic() == 100
    fake.advance(3, wall=False)
    assert fake.clock.monotonic() == 103
    assert fake.clock.utcnow().tzinfo is UTC
    assert isinstance(SYSTEM_CLOCK.utcnow(), datetime)
    assert isinstance(SYSTEM_CLOCK.epoch(), float)
    assert isinstance(SYSTEM_CLOCK.monotonic(), float)


def test_controlled_async_boundaries():
    async def run():
        verifier = ControlledVerification()
        pending = asyncio.create_task(verifier("connection", "url"))
        await verifier.entered.wait()
        assert not pending.done()
        verifier.succeed("verified")
        assert await pending == "verified"
        transport = FakeTransport([TransportResponse(200, b"ok"), TimeoutError("injected")])
        transport.release.clear()
        attempt = asyncio.create_task(transport("url", body=b"payload"))
        await transport.entered.wait()
        assert not attempt.done()
        transport.release.set()
        assert await attempt == TransportResponse(200, b"ok")
        with pytest.raises(TimeoutError, match="injected"):
            await transport("url")
        assert transport.calls[0] == (("url",), {"body": b"payload"})

    asyncio.run(run())


def test_collector():
    collector = Collector()
    collector({"event": 1})
    assert collector.arrived.is_set()
    assert collector.snapshot() == [{"event": 1}]


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_provider_transcript_fixture_uses_real_wait(provider, tmp_path, monkeypatch):
    from twicc.cli._drop_request import transport
    from twicc.cli._wait_reply import _SessionWait
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="events-project", directory=str(tmp_path))
    session = Session.objects.create(
        id=f"events-{provider}",
        project=project,
        provider=provider,
        created_at=datetime.now(UTC),
        user_message_count=1,
    )
    registry = FakeRegistry()
    registry.set_agent(session.id)
    registry.install(monkeypatch)
    token = transport.backend_loop.set(object())
    try:
        record = assistant_content(provider, "Events answer")
        path = write_transcript(tmp_path, session, [record])
        assert orjson.loads(path.read_bytes()) == record
        append_assistant(session, 2)
        clock = FakeClock()
        wait = _SessionWait(session.id, 0, started=clock.monotonic(), twicc_pid=None, want_text=True)
        reply = wait.step()
        assert reply["outcome"] == "replied"
        assert reply["line_num"] == 2
        assert reply["text"] == "Events answer"
    finally:
        transport.backend_loop.reset(token)
