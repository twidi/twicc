"""providers-status.json — incident bookkeeping, acknowledgment, provider isolation.

Real incidents cannot be triggered on demand, so the vendor feed is simulated:
``fetch_component_status`` is replaced by a scripted sequence, and every layer
between it and the WebSocket (the file module, the poll loop, the consumer
handler) is exercised on that script. The data dir is a ``tmp_path``
(``twicc.paths.get_data_dir`` monkeypatched — every path helper reads it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import orjson
import pytest
from asgiref.sync import async_to_sync

from twicc import paths
from twicc import providers_status as ps
from twicc.core.enums import Provider
from twicc.providers import statuspage_task

CC = Provider.CLAUDE_CODE
CX = Provider.CODEX

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(minutes: int) -> str:
    return at(minutes).isoformat()


def incident(started: int, status: str, changed: int, resolved: int | None = None) -> dict:
    return {
        "started_at": iso(started),
        "status": status,
        "changed_at": iso(changed),
        "resolved_at": iso(resolved) if resolved is not None else None,
    }


def episode(started: int, status: str, changed: int) -> dict:
    return {"started_at": iso(started), "status": status, "changed_at": iso(changed)}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_data_dir", lambda: tmp_path)
    return tmp_path


def _file(data_dir) -> dict:
    return orjson.loads((data_dir / "providers-status.json").read_bytes())


# ── Reads ────────────────────────────────────────────────────────────────────

def test_missing_file_reads_empty(data_dir):
    assert ps.read_providers_status() == {}


def test_invalid_json_reads_empty_and_refuses_writes(data_dir):
    path = data_dir / "providers-status.json"
    path.write_bytes(b"{not json")
    assert ps.read_providers_status() == {}
    # The locked read-modify-write refuses a corrupt file rather than
    # overwriting it: no exception, nothing written, nothing to broadcast.
    assert ps.record_status(CC, "major_outage", now=at(0)) is None
    assert path.read_bytes() == b"{not json"


def test_read_drops_invalid_entries_and_unknown_providers(data_dir):
    (data_dir / "providers-status.json").write_bytes(orjson.dumps({
        "claude_code": {"status": "operational", "incident": {"started_at": "x"}, "acknowledged": None},
        "codex": {"status": "operational", "incident": None, "acknowledged": None},
        "gemini": {"status": "operational", "incident": None, "acknowledged": None},
    }))
    assert ps.read_providers_status() == {
        "codex": {"status": "operational", "incident": None, "acknowledged": None},
    }


def test_read_dates_an_undated_incident_from_its_latest_transition(data_dir):
    # A record without ``changed_at``: the resolution when there is one, else
    # the opening, stands for the latest transition.
    (data_dir / "providers-status.json").write_bytes(orjson.dumps({
        "claude_code": {
            "status": "operational",
            "incident": {"started_at": iso(0), "status": "major_outage", "resolved_at": iso(30)},
            "acknowledged": None,
        },
        "codex": {
            "status": "major_outage",
            "incident": {"started_at": iso(5), "status": "major_outage", "resolved_at": None},
            "acknowledged": None,
        },
    }))
    state = ps.read_providers_status()
    assert state["claude_code"]["incident"]["changed_at"] == iso(30)
    assert state["codex"]["incident"]["changed_at"] == iso(5)


# ── Incident lifecycle ───────────────────────────────────────────────────────

def test_first_operational_is_recorded_without_incident(data_dir):
    state = ps.record_status(CC, "operational", now=at(0))
    assert state == {"claude_code": {"status": "operational", "incident": None, "acknowledged": None}}
    assert _file(data_dir) == state


def test_unchanged_status_writes_nothing(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    before = (data_dir / "providers-status.json").read_bytes()
    assert ps.record_status(CC, "operational", now=at(5)) is None
    assert (data_dir / "providers-status.json").read_bytes() == before


def test_same_level_again_is_the_same_episode(data_dir):
    # "major at 17:00, major at 17:30, nothing in between": the poll compares
    # values, so this is one continuous episode — no new transition.
    ps.record_status(CC, "major_outage", now=at(0))
    before = _file(data_dir)
    assert ps.record_status(CC, "major_outage", now=at(30)) is None
    assert _file(data_dir) == before


def test_outage_opens_an_incident_at_now(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    state = ps.record_status(CC, "degraded_performance", now=at(2))
    assert state["claude_code"] == {
        "status": "degraded_performance",
        "incident": incident(2, "degraded_performance", changed=2),
        "acknowledged": None,
    }


def test_first_ever_status_being_an_outage_opens_an_incident(data_dir):
    # No operational ever observed: the incident starts at the first
    # observation — "on part du début".
    state = ps.record_status(CX, "partial_outage", now=at(0))
    assert state["codex"]["incident"] == incident(0, "partial_outage", changed=0)


def test_each_change_of_level_is_a_dated_transition_of_the_same_incident(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    ps.record_status(CC, "degraded_performance", now=at(2))
    ps.record_status(CC, "partial_outage", now=at(10))
    state = ps.record_status(CC, "major_outage", now=at(20))
    assert state["claude_code"]["status"] == "major_outage"
    # The level and the transition date move; the incident's start does not.
    assert state["claude_code"]["incident"] == incident(2, "major_outage", changed=20)


def test_major_partial_major_is_three_transitions(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ps.record_status(CC, "partial_outage", now=at(10))
    state = ps.record_status(CC, "major_outage", now=at(20))
    # Back to the same level as the first transition, but a new transition:
    # its own date, hence its own acknowledgment.
    assert state["claude_code"]["incident"] == incident(0, "major_outage", changed=20)


def test_resolution_closes_the_incident_and_keeps_it(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    ps.record_status(CC, "degraded_performance", now=at(2))
    ps.record_status(CC, "major_outage", now=at(20))
    state = ps.record_status(CC, "operational", now=at(90))
    assert state["claude_code"] == {
        "status": "operational",
        # The window runs from the first problematic status to the recovery,
        # and the incident survives its resolution: it is what a client that
        # was away learns the outage from.
        "incident": incident(2, "major_outage", changed=90, resolved=90),
        "acknowledged": None,
    }


def test_a_new_outage_after_a_resolution_is_a_new_incident(data_dir):
    ps.record_status(CC, "degraded_performance", now=at(0))
    ps.record_status(CC, "operational", now=at(30))
    state = ps.record_status(CC, "degraded_performance", now=at(60))
    assert state["claude_code"]["incident"] == incident(60, "degraded_performance", changed=60)


def test_invalid_status_is_ignored(data_dir):
    assert ps.record_status(CC, "", now=at(0)) is None
    assert ps.record_status(CC, None, now=at(0)) is None  # type: ignore[arg-type]
    assert not (data_dir / "providers-status.json").exists()


# ── Acknowledgment ───────────────────────────────────────────────────────────

def test_acknowledge_the_current_episode(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ack = episode(0, "major_outage", changed=0)
    state = ps.acknowledge_incident("claude_code", ack)
    assert state["claude_code"]["acknowledged"] == ack
    assert _file(data_dir)["claude_code"]["acknowledged"] == ack


def test_acknowledge_the_resolution(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ps.record_status(CC, "operational", now=at(30))
    ack = episode(0, "operational", changed=30)
    assert ps.acknowledge_incident("claude_code", ack)["claude_code"]["acknowledged"] == ack


def test_acknowledgment_is_kept_across_a_change_of_level(data_dir):
    ps.record_status(CC, "degraded_performance", now=at(0))
    ps.acknowledge_incident("claude_code", episode(0, "degraded_performance", changed=0))
    state = ps.record_status(CC, "major_outage", now=at(5))
    # Kept as-is; it no longer matches the current transition, which is what
    # re-announces the incident at its new level.
    assert state["claude_code"]["acknowledged"] == episode(0, "degraded_performance", changed=0)
    assert state["claude_code"]["incident"] == incident(0, "major_outage", changed=5)


def test_acknowledging_the_first_major_does_not_cover_the_second(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ps.acknowledge_incident("claude_code", episode(0, "major_outage", changed=0))
    ps.record_status(CC, "partial_outage", now=at(10))
    state = ps.record_status(CC, "major_outage", now=at(20))
    # Same level as what was acknowledged, different transition.
    assert state["claude_code"]["acknowledged"] != {
        "started_at": state["claude_code"]["incident"]["started_at"],
        "status": state["claude_code"]["incident"]["status"],
        "changed_at": state["claude_code"]["incident"]["changed_at"],
    }


def test_acknowledge_refuses_a_stale_incident(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ps.record_status(CC, "operational", now=at(30))
    ps.acknowledge_incident("claude_code", episode(0, "operational", changed=30))
    ps.record_status(CC, "degraded_performance", now=at(60))
    # A tab that still shows the previous incident's toast must not overwrite
    # the acknowledgment with a stale one.
    assert ps.acknowledge_incident("claude_code", episode(0, "major_outage", changed=0)) is None
    assert _file(data_dir)["claude_code"]["acknowledged"] == episode(0, "operational", changed=30)


@pytest.mark.parametrize("provider, ack", [
    ("gemini", episode(0, "major_outage", 0)),
    (None, episode(0, "major_outage", 0)),
    ("claude_code", None),
    ("claude_code", {"status": "major_outage", "changed_at": iso(0)}),
    ("claude_code", {"started_at": iso(0), "status": "major_outage"}),
    ("claude_code", {"started_at": "", "status": "major_outage", "changed_at": iso(0)}),
    ("claude_code", {"started_at": iso(0), "status": "", "changed_at": iso(0)}),
    ("claude_code", "major_outage"),
])
def test_acknowledge_refuses_bad_input(data_dir, provider, ack):
    ps.record_status(CC, "major_outage", now=at(0))
    assert ps.acknowledge_incident(provider, ack) is None
    assert _file(data_dir)["claude_code"]["acknowledged"] is None


def test_acknowledge_without_an_incident_is_refused(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    assert ps.acknowledge_incident("claude_code", episode(0, "operational", 0)) is None


def test_acknowledge_twice_writes_once(data_dir):
    ps.record_status(CC, "major_outage", now=at(0))
    ack = episode(0, "major_outage", changed=0)
    assert ps.acknowledge_incident("claude_code", ack) is not None
    assert ps.acknowledge_incident("claude_code", ack) is None


# ── Provider isolation ───────────────────────────────────────────────────────

def test_providers_never_touch_each_other(data_dir):
    ps.record_status(CC, "operational", now=at(0))
    ps.record_status(CX, "operational", now=at(0))
    ps.record_status(CC, "major_outage", now=at(1))
    state = _file(data_dir)
    assert state["codex"] == {"status": "operational", "incident": None, "acknowledged": None}

    ps.record_status(CX, "degraded_performance", now=at(2))
    ps.acknowledge_incident("codex", episode(2, "degraded_performance", changed=2))
    state = _file(data_dir)
    assert state["claude_code"]["acknowledged"] is None
    assert state["claude_code"]["incident"]["started_at"] == iso(1)
    assert state["codex"]["incident"]["started_at"] == iso(2)

    ps.record_status(CX, "operational", now=at(3))
    state = _file(data_dir)
    assert state["claude_code"]["status"] == "major_outage"
    assert state["claude_code"]["incident"]["resolved_at"] is None
    assert state["codex"]["incident"]["resolved_at"] == iso(3)


# ── Poll loop (scripted vendor feed) ─────────────────────────────────────────

@pytest.fixture
def feed(monkeypatch):
    """Script ``fetch_component_status`` and capture every broadcast."""
    script: list[object] = []
    broadcasts: list[dict] = []

    async def fake_fetch(components_url, component_name):
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_broadcast(state):
        broadcasts.append(state)

    monkeypatch.setattr(statuspage_task, "fetch_component_status", fake_fetch)
    monkeypatch.setattr(statuspage_task, "broadcast_providers_status", fake_broadcast)
    return script, broadcasts


def test_check_once_records_and_broadcasts_only_changes(data_dir, feed):
    script, broadcasts = feed
    script[:] = ["operational", "degraded_performance", "degraded_performance", "major_outage", "operational"]
    for _ in range(5):
        async_to_sync(statuspage_task.check_once)(CX)
    assert [b["codex"]["status"] for b in broadcasts] == [
        "operational", "degraded_performance", "major_outage", "operational",
    ]
    final = _file(data_dir)["codex"]
    assert final["status"] == "operational"
    assert final["incident"]["status"] == "major_outage"
    assert final["incident"]["resolved_at"] is not None
    assert final["incident"]["changed_at"] == final["incident"]["resolved_at"]
    assert "claude_code" not in _file(data_dir)


def test_check_once_survives_fetch_errors_and_missing_component(data_dir, feed):
    script, broadcasts = feed
    script[:] = [RuntimeError("boom"), None, "major_outage"]
    assert async_to_sync(statuspage_task.check_once)(CC) is None
    assert async_to_sync(statuspage_task.check_once)(CC) is None
    assert async_to_sync(statuspage_task.check_once)(CC)["claude_code"]["status"] == "major_outage"
    assert len(broadcasts) == 1


def test_check_once_detects_a_change_that_happened_while_down(data_dir, feed):
    # The file says "major outage" (written by a previous run); the first poll
    # of a fresh process sees "operational": a real change, resolved + broadcast.
    ps.record_status(CC, "major_outage", now=at(0))
    script, broadcasts = feed
    script[:] = ["operational"]
    state = async_to_sync(statuspage_task.check_once)(CC)
    assert state["claude_code"]["incident"]["resolved_at"] is not None
    assert broadcasts == [state]


def test_poll_loop_runs_the_script_then_stops(data_dir, feed, monkeypatch):
    script, broadcasts = feed
    monkeypatch.setattr(statuspage_task, "STATUSPAGE_INTERVAL", 0.001)
    script[:] = ["operational", "partial_outage", "operational"]
    stop = statuspage_task.get_statuspage_stop_event(CC)

    original_fetch = statuspage_task.fetch_component_status

    async def fetch_then_stop(components_url, component_name):
        value = await original_fetch(components_url, component_name)
        if not script:
            stop.set()
        return value

    monkeypatch.setattr(statuspage_task, "fetch_component_status", fetch_then_stop)
    async_to_sync(statuspage_task.start_statuspage_task)(CC)
    assert [b["claude_code"]["status"] for b in broadcasts] == ["operational", "partial_outage", "operational"]
    assert script == []


def test_loop_does_nothing_for_a_provider_without_status_page(data_dir, feed, monkeypatch):
    from twicc.providers.helpers import get_provider_helpers

    monkeypatch.setattr(type(get_provider_helpers(CX)), "STATUSPAGE", None)
    script, broadcasts = feed
    script[:] = ["major_outage"]
    async_to_sync(statuspage_task.start_statuspage_task)(CX)
    assert async_to_sync(statuspage_task.check_once)(CX) is None
    assert broadcasts == [] and script == ["major_outage"]


# ── WebSocket consumer ───────────────────────────────────────────────────────

@pytest.fixture
def ws(monkeypatch):
    """A consumer with the channel layer replaced by a group_send recorder."""
    from twicc.asgi import WSConsumer

    sent: list[dict] = []

    class Layer:
        async def group_send(self, group, message):
            sent.append((group, message))

    monkeypatch.setattr(ps, "get_channel_layer", lambda: Layer())
    consumer = WSConsumer()
    return consumer, sent


def test_ws_acknowledge_broadcasts_the_whole_file(data_dir, ws):
    consumer, sent = ws
    ps.record_status(CC, "major_outage", now=at(0))
    ps.record_status(CX, "operational", now=at(0))
    ack = episode(0, "major_outage", changed=0)
    async_to_sync(consumer._handle_acknowledge_provider_status)({
        "type": "acknowledge_provider_status", "provider": "claude_code", "episode": ack,
    })
    assert len(sent) == 1
    group, message = sent[0]
    assert group == "updates"
    assert message["type"] == "broadcast"
    assert message["data"]["type"] == "providers_status_updated"
    assert message["data"]["providers_status"]["claude_code"]["acknowledged"] == ack
    assert message["data"]["providers_status"]["codex"]["acknowledged"] is None


def test_ws_acknowledge_refused_broadcasts_nothing(data_dir, ws):
    consumer, sent = ws
    ps.record_status(CC, "major_outage", now=at(0))
    async_to_sync(consumer._handle_acknowledge_provider_status)({
        "type": "acknowledge_provider_status", "provider": "claude_code",
        "episode": episode(99, "major_outage", changed=99),
    })
    async_to_sync(consumer._handle_acknowledge_provider_status)({"type": "acknowledge_provider_status"})
    assert sent == []


def test_connect_message_carries_the_file(data_dir):
    ps.record_status(CC, "degraded_performance", now=at(0))
    message = ps.build_providers_status_message()
    assert message["type"] == "providers_status_updated"
    assert message["providers_status"]["claude_code"]["incident"]["status"] == "degraded_performance"
    assert ps.build_providers_status_message({})["providers_status"] == {}
