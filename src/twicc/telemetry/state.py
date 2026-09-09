"""Telemetry state file (<data_dir>/telemetry.json).

Holds the anonymous instance id, the last-sent marker (the no-backfill
rule: initialized to *today* on first run so pre-telemetry DB history is
never sent — design doc §5.1), the per-day accumulators for the two
metrics that have no DB trace (presence minutes, peak concurrent agents),
a copy of the last payload sent (for the settings "View last payload"
dialog), and ``last_sent_at`` (UTC timestamp of that last successful send).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, UTC
from pathlib import Path

from twicc.atomic_json import locked_json_file
from twicc.paths import get_data_dir

STATE_FILENAME = "telemetry.json"

# Offline catch-up cap (design §5.1): older unsent days are dropped.
MAX_DAY_ENTRIES = 30

_DEFAULT_STATE = {
    "instance_id": None,
    "last_sent_date": None,
    "days": {},
    "last_payload": None,
    "last_sent_at": None,
    "was_active": None,
    # When telemetry last became active (off->on), so the sender can hold a
    # grace window: the user has just acknowledged the notice (or re-enabled
    # the setting) and may still want to go and turn it off. ``None`` = never
    # observed becoming active, i.e. an instance enabled all along — no grace
    # applies, its behaviour is unchanged. See ``twicc.telemetry.task``.
    "active_since": None,
}


def utc_today() -> date:
    return datetime.now(UTC).date()


def get_state_path() -> Path:
    return get_data_dir() / STATE_FILENAME


@contextmanager
def state_txn():
    """Locked read-modify-write on the state file, defaults ensured."""
    with locked_json_file(get_state_path(), default=dict(_DEFAULT_STATE)) as txn:
        for key, value in _DEFAULT_STATE.items():
            txn.data.setdefault(key, value if not isinstance(value, dict) else dict(value))
        if not txn.data["instance_id"]:
            txn.data["instance_id"] = str(uuid.uuid4())
            txn.write()
        if not txn.data["last_sent_date"]:
            txn.data["last_sent_date"] = utc_today().isoformat()
            txn.write()
        yield txn


def ensure_state() -> dict:
    with state_txn() as txn:
        return dict(txn.data)


def reset_instance_id() -> str:
    with state_txn() as txn:
        txn.data["instance_id"] = str(uuid.uuid4())
        txn.write()
        return txn.data["instance_id"]


def record_tick(*, present: bool, live_agents: int) -> None:
    """One ticker sample: +1 presence minute if present, max() the peak."""
    day = utc_today().isoformat()
    with state_txn() as txn:
        entry = txn.data["days"].setdefault(day, {"presence_minutes": 0, "peak_agents": 0})
        if present:
            entry["presence_minutes"] += 1
        entry["peak_agents"] = max(entry["peak_agents"], live_agents)
        _prune(txn.data)
        txn.write()


def mark_sent(sent_through: str, payload: dict) -> None:
    """Advance the marker after a successful POST and drop covered days."""
    with state_txn() as txn:
        txn.data["last_sent_date"] = sent_through
        txn.data["last_payload"] = payload
        txn.data["last_sent_at"] = datetime.now(UTC).isoformat()
        txn.data["days"] = {d: v for d, v in txn.data["days"].items() if d > sent_through}
        txn.write()


def note_active_transition(active: bool) -> None:
    """Persist the observed enabled state; off->on advances the marker to today.

    "Off" must mean "no data about that period": while telemetry is disabled
    the marker stops advancing, so without this a later re-enable would
    retroactively send DB-derived day blocks for the whole disabled window.
    Advancing the marker on the transition drops those days for good (their
    accumulators too — today's partial entry is kept, it belongs to the
    re-enabled period). A missing stored state (first observation, or installs
    predating this field) records the state without advancing, which preserves
    the offline catch-up: enabled-all-along instances keep their old marker.

    The same transition stamps ``active_since``, which the sender uses as the
    start of its grace window.
    """
    with state_txn() as txn:
        was_active = txn.data.get("was_active")
        if active and was_active is False:
            today = utc_today().isoformat()
            txn.data["last_sent_date"] = max(txn.data["last_sent_date"], today)
            txn.data["days"] = {d: v for d, v in txn.data["days"].items() if d >= today}
            # Start the sender's grace window from this instant.
            txn.data["active_since"] = datetime.now(UTC).isoformat()
            txn.write()
        if was_active != active:
            txn.data["was_active"] = active
            txn.write()


def _prune(data: dict) -> None:
    days = data["days"]
    if len(days) > MAX_DAY_ENTRIES:
        for day in sorted(days)[: len(days) - MAX_DAY_ENTRIES]:
            del days[day]
