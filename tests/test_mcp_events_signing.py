"""Standardwebhooks interoperability and per-attempt authority snapshots."""

import base64
from datetime import UTC, datetime, timedelta

import orjson
import pytest
from standardwebhooks import Webhook
from standardwebhooks.webhooks import WebhookVerificationError

from tests.mcp_events_helpers import FakeClock
from twicc.mcp.events.delivery import signing_headers

SECRET = "whsec_" + base64.b64encode(b"a" * 32).decode()
OLD_SECRET = "whsec_" + base64.b64encode(b"b" * 32).decode()
BODY = orjson.dumps({"text": "Unicode 😀 and escaped \n exact bytes"})


def test_five_headers_sign_exact_body_and_same_integer_timestamp():
    clock = FakeClock(epoch=datetime.now(UTC).timestamp() + 0.75)
    headers = signing_headers("sub_1", "evt_1", BODY, SECRET, clock=clock.clock)
    ts = int(clock.epoch())
    assert set(headers) == {"Content-Type", "webhook-id", "webhook-timestamp", "webhook-signature",
                            "X-MCP-Subscription-Id"}
    assert headers["Content-Type"] == "application/json"
    assert headers["X-MCP-Subscription-Id"] == "sub_1"
    assert headers["webhook-id"] == "evt_1"
    assert headers["webhook-timestamp"] == str(ts)
    assert headers["webhook-signature"] == Webhook(SECRET).sign(
        "evt_1", datetime.fromtimestamp(ts, UTC), BODY.decode())
    assert Webhook(SECRET).verify(BODY.decode(), headers) == orjson.loads(BODY)
    with pytest.raises(WebhookVerificationError):
        Webhook(SECRET).verify(BODY.decode() + " ", headers)


@pytest.mark.parametrize("remaining,old_signs", [(300, True), (0.1, True), (0, False), (-1, False)])
def test_rotation_signs_both_keys_only_before_exact_deadline(remaining, old_signs):
    clock = FakeClock(epoch=datetime.now(UTC).timestamp())
    headers = signing_headers("sub_1", "evt_1", BODY, SECRET, previous_secret=OLD_SECRET,
                             previous_secret_until=clock.utcnow() + timedelta(seconds=remaining), clock=clock.clock)
    assert len(headers["webhook-signature"].split(" ")) == (2 if old_signs else 1)
    assert Webhook(SECRET).verify(BODY.decode(), headers) == orjson.loads(BODY)
    if old_signs:
        assert Webhook(OLD_SECRET).verify(BODY.decode(), headers) == orjson.loads(BODY)
    else:
        with pytest.raises(WebhookVerificationError):
            Webhook(OLD_SECRET).verify(BODY.decode(), headers)


def test_retry_uses_new_authority_secret_and_new_timestamp_with_same_body():
    clock = FakeClock(epoch=datetime.now(UTC).timestamp())
    first = signing_headers("sub_1", "evt_1", BODY, OLD_SECRET, clock=clock.clock)
    clock.advance(1)
    second = signing_headers("sub_1", "evt_1", BODY, SECRET, clock=clock.clock)
    assert second["webhook-id"] == first["webhook-id"]
    assert int(second["webhook-timestamp"]) == int(first["webhook-timestamp"]) + 1
    assert second["webhook-signature"] != first["webhook-signature"]
    assert Webhook(SECRET).verify(BODY.decode(), second) == orjson.loads(BODY)
