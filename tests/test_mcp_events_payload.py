"""Exact detector payloads, occurrence identity and maximal bounded bodies."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import orjson
import pytest
from jsonschema import validate

from twicc.mcp.events.catalog import MAX_BODY_BYTES, PAYLOAD_SCHEMA, event_id
from twicc.mcp.events.delivery import build_occurrence, fit_body

TICK = datetime(2026, 10, 4, tzinfo=UTC)
REQUEST = SimpleNamespace(request_id="request-1", request_type="tool_approval", created_at=TICK.timestamp() - 60)


def occurrence(outcome="replied", *, text="answer", line=7, numbering=2, last_line=8, **fields):
    reply = {"outcome": outcome, "line_num": line, "is_final": True if line else None,
             "since_line_num": 3, "waited_seconds": 17.5}
    if text is not None:
        reply["text"] = text
    return build_occurrence("sub_a", "session-a", None, reply, numbering=numbering,
                            last_line=last_line, tick_started_at=TICK, pending_request=REQUEST, **fields)


@pytest.mark.parametrize("outcome,text,line", [
    ("replied", "exact\nanswer", 7), ("awaiting_user_input", None, None),
    ("provider_error", "", 7), ("ended", None, None), ("ended", "old answer", 7),
])
def test_exact_data_schema_and_text_presence(outcome, text, line):
    event = occurrence(outcome, text=text, line=line)
    data = event["data"]
    validate(data, PAYLOAD_SCHEMA)
    assert data["session_id"] == "session-a"
    assert data["session_title"] is None
    assert data["reply"] == {"outcome": outcome, "line_num": line,
                             "is_final": True if line else None, "since_line_num": 3,
                             **({"text": text} if text is not None else {})}
    assert ("request_type" in data) == (outcome == "awaiting_user_input")
    if outcome == "awaiting_user_input":
        assert data["request_type"] == REQUEST.request_type
    assert event["cursor"] is None
    assert event["name"] == "session.concluded"
    assert "text_truncated" not in orjson.loads(fit_body(event))["data"]["reply"]


@pytest.mark.parametrize("outcome", ["replied", "provider_error", "ended"])
def test_line_identity_includes_epoch_and_is_stable(outcome):
    first = occurrence(outcome)
    assert first["eventId"] == event_id("sub_a", outcome, "2:7")
    assert first["eventId"] == occurrence(outcome)["eventId"]
    assert first["eventId"] != occurrence(outcome, numbering=3)["eventId"]


def test_request_and_lineless_end_keys():
    assert occurrence("awaiting_user_input", line=None, text=None)["eventId"] == event_id(
        "sub_a", "awaiting_user_input", REQUEST.request_id)
    assert occurrence("ended", line=None)["eventId"] == event_id("sub_a", "ended", "last_line:2:8")
    assert occurrence("ended", line=None, last_line=0)["eventId"] == event_id("sub_a", "ended", "last_line:2:0")
    assert occurrence("ended", line=None, last_line=9)["eventId"] != occurrence("ended", line=None)["eventId"]


@pytest.mark.parametrize("outcome", ["replied", "provider_error", "ended", "awaiting_user_input"])
def test_timestamp_selection(outcome):
    old = TICK - timedelta(days=1)
    event = occurrence(outcome, item_timestamp=old)
    expected = old if outcome in ("replied", "provider_error") else TICK
    if outcome == "awaiting_user_input":
        expected = datetime.fromtimestamp(REQUEST.created_at, UTC)
    assert event["timestamp"] == expected.isoformat()
    if outcome in ("replied", "provider_error"):
        assert occurrence(outcome)["timestamp"] == TICK.isoformat()


@pytest.mark.parametrize("unit", ["a", "😀", '"\\\n\t', "é😀\x00"])
def test_truncation_is_maximal_complete_json_and_does_not_mutate(unit):
    text = unit * MAX_BODY_BYTES
    event = occurrence(text=text)
    body = fit_body(event)
    assert len(body) <= MAX_BODY_BYTES
    fitted = orjson.loads(body)
    reply = fitted["data"]["reply"]
    assert reply["text_truncated"] is True
    assert text.startswith(reply["text"])
    prefix_length = len(reply["text"])
    assert prefix_length < len(text)
    reply["text"] = text[:prefix_length + 1]
    assert len(orjson.dumps(fitted)) > MAX_BODY_BYTES
    assert event["data"]["reply"]["text"] == text
    assert "text_truncated" not in event["data"]["reply"]


@pytest.mark.parametrize("text", [None, "small text"])
def test_non_text_oversize_drops_and_logs(text, caplog, monkeypatch):
    monkeypatch.setattr("twicc.mcp.events.delivery.logger.disabled", False)
    event = occurrence(text=text)
    event["data"]["session_title"] = "x" * MAX_BODY_BYTES
    assert fit_body(event) is None
    assert event["eventId"] in caplog.text
    assert "non-text fields exceed body limit" in caplog.text


def test_exact_limit_does_not_add_truncation_marker():
    event = occurrence(text="")
    event["data"]["reply"]["text"] = "x" * (MAX_BODY_BYTES - len(orjson.dumps(event)))
    body = fit_body(event)
    assert len(body) == MAX_BODY_BYTES
    assert "text_truncated" not in orjson.loads(body)["data"]["reply"]
