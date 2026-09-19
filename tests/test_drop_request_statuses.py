"""The terminal status vocabulary of the drop-request transport.

Four files declare it: ``polling`` owns the tuple, ``transport`` imports it,
``output.build_final`` branches on it, and the watcher stamps a timestamp per
status. A status missing from one of them fails silently — no timestamp, or a
poll loop that never returns. These tests pin the agreement.
"""

from __future__ import annotations

from twicc.cli._drop_request import output, polling, transport
from twicc.cli._drop_request.polling import PollOutcome
from twicc.drop_requests_watcher import _STATUS_TIME_FIELDS


def test_every_final_status_has_a_timestamp_field():
    missing = [s for s in polling.FINAL_STATUSES if s not in _STATUS_TIME_FIELDS]
    assert missing == []


def test_fetched_is_a_final_status():
    assert "fetched" in polling.FINAL_STATUSES


def test_the_transport_shares_the_polling_tuple():
    # One object, not two copies that can drift apart.
    assert transport.FINAL_STATUSES is polling.FINAL_STATUSES


def test_fetched_emits_only_the_published_keys():
    # ``execute_drop_payload`` flattens the id fields, ``status_extra`` and the
    # per-status timestamp into one dict before ``build_final`` sees it. The
    # branch whitelists what the design publishes; everything else must drop.
    outcome = PollOutcome(
        status="fetched",
        data={
            "status": "fetched",
            "session_id": "s-1",
            "provider": "claude_code",
            "project_id": "-home-twidi",
            "fetched_at": "2026-09-19T10:00:00+00:00",
            "agent_state": "awaiting_user_input",
            "pending_requests": [{"request_id": "r-1"}],
        },
        received_seen=True,
    )

    payload = output.build_final(outcome, request_uuid="u-1", timeout=30)

    assert payload == {
        "status": "fetched",
        "request_uuid": "u-1",
        "session_id": "s-1",
        "provider": "claude_code",
        "agent_state": "awaiting_user_input",
        "pending_requests": [{"request_id": "r-1"}],
    }
