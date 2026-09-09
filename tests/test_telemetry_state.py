from datetime import datetime, UTC

import orjson
import pytest

from twicc.telemetry import state


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "get_data_dir", lambda: tmp_path)
    return tmp_path


def test_ensure_state_creates_instance_id_and_no_backfill_marker(data_dir):
    st = state.ensure_state()
    assert len(st["instance_id"]) == 36
    assert st["last_sent_date"] == state.utc_today().isoformat()
    assert state.ensure_state()["instance_id"] == st["instance_id"]


def test_reset_instance_id_changes_id_and_persists(data_dir):
    old = state.ensure_state()["instance_id"]
    new = state.reset_instance_id()
    assert new != old
    raw = orjson.loads((data_dir / "telemetry.json").read_bytes())
    assert raw["instance_id"] == new


def test_record_tick_accumulates_presence_and_peak(data_dir):
    state.ensure_state()
    state.record_tick(present=True, live_agents=2)
    state.record_tick(present=False, live_agents=5)
    day = state.utc_today().isoformat()
    st = state.ensure_state()
    assert st["days"][day] == {"presence_minutes": 1, "peak_agents": 5}


def test_mark_sent_advances_marker_and_prunes_sent_days(data_dir):
    state.ensure_state()
    with state.state_txn() as txn:
        txn.data["days"] = {
            "2026-07-10": {"presence_minutes": 5, "peak_agents": 1},
            "2026-07-11": {"presence_minutes": 6, "peak_agents": 2},
            "2026-07-12": {"presence_minutes": 7, "peak_agents": 3},
        }
        txn.write()
    state.mark_sent("2026-07-11", {"schema": 1})
    st = state.ensure_state()
    assert st["last_sent_date"] == "2026-07-11"
    assert st["days"] == {"2026-07-12": {"presence_minutes": 7, "peak_agents": 3}}
    assert st["last_payload"] == {"schema": 1}
    assert st["last_sent_at"] is not None


def test_off_on_transition_advances_marker_and_drops_disabled_days(data_dir):
    state.ensure_state()
    today = state.utc_today().isoformat()
    with state.state_txn() as txn:
        txn.data["last_sent_date"] = "2026-07-10"
        txn.data["days"] = {
            "2026-07-11": {"presence_minutes": 5, "peak_agents": 1},
            today: {"presence_minutes": 2, "peak_agents": 1},
        }
        txn.write()

    # First observation ever: records the state, never advances.
    state.note_active_transition(True)
    assert state.ensure_state()["last_sent_date"] == "2026-07-10"

    # Off then on: the disabled window must never be sent.
    state.note_active_transition(False)
    state.note_active_transition(True)
    st = state.ensure_state()
    assert st["last_sent_date"] == today
    # Pre-transition days dropped; today's partial accumulator kept.
    assert list(st["days"]) == [today]


def test_continuous_on_never_advances_marker(data_dir):
    # Offline catch-up must survive: enabled all along, the old marker stays
    # so the elapsed days are sent normally.
    state.ensure_state()
    with state.state_txn() as txn:
        txn.data["last_sent_date"] = "2026-07-10"
        txn.write()
    state.note_active_transition(True)
    state.note_active_transition(True)
    assert state.ensure_state()["last_sent_date"] == "2026-07-10"


def test_prune_keeps_newest_max_day_entries(data_dir):
    state.ensure_state()
    with state.state_txn() as txn:
        txn.data["days"] = {
            f"2026-06-{day:02d}": {"presence_minutes": day, "peak_agents": day}
            for day in range(1, 32)  # 31 entries: 2026-06-01 .. 2026-06-31
        }
        txn.write()
    # record_tick adds/updates a 32nd entry for today and triggers _prune,
    # which must drop exactly the 2 oldest (32 - MAX_DAY_ENTRIES) to land at 30.
    state.record_tick(present=False, live_agents=0)
    st = state.ensure_state()
    today = state.utc_today().isoformat()
    assert len(st["days"]) == state.MAX_DAY_ENTRIES
    assert "2026-06-01" not in st["days"]
    assert "2026-06-02" not in st["days"]
    assert "2026-06-03" in st["days"]
    assert "2026-06-31" in st["days"]
    assert today in st["days"]


def test_off_to_on_stamps_active_since(data_dir):
    """The sender's grace window starts at the transition, not at install."""
    state.ensure_state()

    state.note_active_transition(False)
    assert state.ensure_state()["active_since"] is None

    state.note_active_transition(True)

    stamped = state.ensure_state()["active_since"]
    assert stamped is not None
    # Parseable and recent: within_activation_grace() compares against it.
    assert (datetime.now(UTC) - datetime.fromisoformat(stamped)).total_seconds() < 60


def test_continuous_on_leaves_active_since_unset(data_dir):
    """Enabled all along means no grace: its behaviour must not change."""
    state.ensure_state()

    state.note_active_transition(True)
    state.note_active_transition(True)

    assert state.ensure_state()["active_since"] is None
