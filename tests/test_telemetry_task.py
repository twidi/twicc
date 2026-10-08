"""Tests for the telemetry background task (design docs/plans/2026-07-18-telemetry-design.md).

No network: httpx is always monkeypatched. ``is_telemetry_active`` gating uses
the ``temp_settings`` fixture pattern from ``tests/test_settings_mutation.py``
(a tmp settings.json, module cache cleared) so tests never touch the real
synced settings file.
"""

import asyncio
from datetime import datetime, timedelta, UTC

import httpx
import pytest

import twicc.synced_settings as ss
from twicc.telemetry import task
from twicc.telemetry.state import utc_today


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.fixture
def notice_acknowledged(temp_settings):
    """Mark the telemetry notice as seen — the precondition for any activity.

    Tests about the OTHER conditions need it explicitly, so the gate stays
    visible instead of being buried in ``temp_settings``.
    """
    ss.write_synced_settings({**ss.read_synced_settings(), "telemetryNoticeSeen": True})


@pytest.fixture
def temp_state(tmp_path, monkeypatch):
    """Point the telemetry state file at a tmp path, isolated from the real data dir."""
    import twicc.telemetry.state as state_mod

    state_path = tmp_path / "telemetry.json"
    monkeypatch.setattr(state_mod, "get_state_path", lambda: state_path)
    return state_path


class TestIsTelemetryActive:
    def test_setting_can_enable_telemetry_with_environment_opt_out(self, temp_settings, notice_acknowledged, monkeypatch):
        monkeypatch.setenv("TWICC_NO_TELEMETRY", "1")
        assert task.is_telemetry_active() is True

    def test_false_when_synced_setting_disabled(self, temp_settings):
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": False})
        assert task.is_telemetry_active() is False

    def test_true_otherwise(self, temp_settings, notice_acknowledged):
        # Isolated synced settings with no override -> the default
        # telemetryEnabled (True) applies, so telemetry is active.
        assert task.is_telemetry_active() is True

    def test_true_when_synced_setting_is_null(self, temp_settings, notice_acknowledged):
        # The frontend syncs a `null` placeholder for unset synced keys; a
        # present null must read as enabled (default-on), matching the frontend
        # getter `telemetryEnabled !== false` -- never silently disabled.
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": None})
        assert task.is_telemetry_active() is True


class TestSendCycle:
    def test_no_pending_payload_no_post_attempted(self, monkeypatch):
        monkeypatch.setattr(task, "build_pending_payload", lambda: None)

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("httpx.AsyncClient should not be instantiated")

        monkeypatch.setattr(task.httpx, "AsyncClient", _fail_if_called)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []

    def test_unsent_day_posted_and_marker_advanced_on_success(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10", "messages_sent": 3}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        posts = []

        class _FakeResponse:
            status_code = 204

            def raise_for_status(self):
                pass

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                posts.append((url, json))
                return _FakeResponse()

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert len(posts) == 1
        url, body = posts[0]
        assert url == task.settings.TELEMETRY_ENDPOINT
        assert body == payload
        assert [d["date"] for d in body["days"]] == ["2026-07-10"]
        assert mark_sent_calls == [(("2026-07-10", payload), {})]

    def test_network_error_leaves_marker_untouched(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10"}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                raise ConnectionError("network unreachable")

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []

    def test_http_status_error_leaves_marker_untouched(self, monkeypatch):
        payload = {
            "schema": 1,
            "instance_id": "fake-instance",
            "instance": {},
            "days": [{"date": "2026-07-10"}],
        }
        monkeypatch.setattr(task, "build_pending_payload", lambda: payload)

        class _FakeResponse:
            status_code = 500

            def raise_for_status(self):
                request = httpx.Request("POST", task.settings.TELEMETRY_ENDPOINT)
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError("Server error", request=request, response=response)

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, url, json):
                return _FakeResponse()

        monkeypatch.setattr(task.httpx, "AsyncClient", _FakeClient)

        import twicc.telemetry.state as state_mod

        mark_sent_calls = []
        monkeypatch.setattr(state_mod, "mark_sent", lambda *a, **kw: mark_sent_calls.append((a, kw)))

        asyncio.run(task.send_cycle())

        assert mark_sent_calls == []


@pytest.mark.django_db
class TestTickOnceGating:
    def test_tick_once_noop_when_inactive(self, temp_state, temp_settings, notice_acknowledged):
        # First tick while active: records a real day entry. temp_settings
        # isolates the synced settings so is_telemetry_active() sees the default
        # (telemetryEnabled unset -> True) instead of the real machine's file.
        task.tick_once()

        import twicc.telemetry.state as state_mod

        day = utc_today().isoformat()
        state_after_first_tick = state_mod.ensure_state()
        assert day in state_after_first_tick["days"]
        entry_after_first_tick = dict(state_after_first_tick["days"][day])

        # Disable via the synced setting, tick again: no change.
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": False})
        task.tick_once()

        state_after_second_tick = state_mod.ensure_state()
        assert state_after_second_tick["days"][day] == entry_after_first_tick


class TestStartTelemetryTaskLoop:
    """Drives the actual loop in ``start_telemetry_task`` -- ``tick_once``/``send_cycle``
    are replaced with counting spies so no DB/network is touched; ``TICK_INTERVAL``/
    ``TELEMETRY_SEND_INTERVAL`` are shrunk so the whole run takes a few milliseconds.
    The tick spy sets the stop event once it has been called enough times, so the
    assertions are driven by call counts, never by wall-clock timing.
    """

    def test_sends_on_first_iteration_then_waits_a_full_interval(self, monkeypatch):
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 0.003)  # 3 ticks between sends

        stop_event = asyncio.Event()
        tick_calls: list[int] = []
        send_calls: list[int] = []  # tick count at the time each send fired

        def fake_tick_once():
            tick_calls.append(len(tick_calls) + 1)
            if len(tick_calls) >= 4:
                stop_event.set()

        async def fake_send_cycle():
            send_calls.append(len(tick_calls))

        monkeypatch.setattr(task, "tick_once", fake_tick_once)
        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        asyncio.run(asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5))

        # Sent once right on the first iteration (ticks_since_send starts already
        # "due"), then not again until a full TELEMETRY_SEND_INTERVAL worth of
        # ticks (3) had elapsed -- i.e. on the 4th tick, not before.
        assert tick_calls == [1, 2, 3, 4]
        assert send_calls == [1, 4]

    def test_exits_promptly_once_stop_event_is_set(self, monkeypatch):
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 10)  # never due, keep it out of the way

        stop_event = asyncio.Event()
        tick_calls: list[int] = []

        def fake_tick_once():
            tick_calls.append(1)
            if len(tick_calls) >= 2:
                stop_event.set()

        async def fake_send_cycle():
            raise AssertionError("send_cycle should not fire in this test")

        monkeypatch.setattr(task, "tick_once", fake_tick_once)
        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        # A generous bound: proves the loop actually exits once the event is
        # set, rather than spinning until the timeout.
        asyncio.run(asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5))

        assert len(tick_calls) == 2

    def test_cancellation_propagates_and_does_not_hang(self, monkeypatch):
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "TELEMETRY_SEND_INTERVAL", 10)

        monkeypatch.setattr(task, "tick_once", lambda: None)

        async def fake_send_cycle():
            return None

        monkeypatch.setattr(task, "send_cycle", fake_send_cycle)

        async def _run():
            stop_event = asyncio.Event()
            task_obj = asyncio.create_task(task.start_telemetry_task(stop_event))
            await asyncio.sleep(0.01)  # let it enter the loop for a few iterations
            task_obj.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task_obj, timeout=5)

        asyncio.run(_run())

    @pytest.mark.django_db
    def test_disabled_loop_can_resume_after_setting_enabled(
        self, temp_state, temp_settings, notice_acknowledged, monkeypatch
    ):
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": False})
        monkeypatch.setenv("TWICC_NO_TELEMETRY", "1")
        monkeypatch.setattr(task, "TICK_INTERVAL", 0.001)
        monkeypatch.setattr(task, "build_pending_payload", lambda: None)
        real_tick = task.tick_once
        ticks = []
        stop_event = asyncio.Event()

        def tick_and_enable():
            real_tick()
            ticks.append(1)
            if len(ticks) == 1:
                ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": True})
            else:
                stop_event.set()

        monkeypatch.setattr(task, "tick_once", tick_and_enable)
        asyncio.run(asyncio.wait_for(task.start_telemetry_task(stop_event), timeout=5))

        from twicc.telemetry.state import ensure_state

        assert len(ticks) == 2
        assert utc_today().isoformat() in ensure_state()["days"]


class TestNoticeGate:
    """Nothing at all before the user has acknowledged the notice dialog."""

    def test_false_when_notice_never_seen(self, temp_settings):
        # Isolated settings, notice untouched -> the default (False) applies.
        assert task.is_telemetry_active() is False

    def test_false_when_notice_seen_is_null(self, temp_settings):
        """A synced null placeholder is NOT an acknowledgement.

        Asymmetric with ``telemetryEnabled`` on purpose: that one is opt-out
        (null reads as enabled), this one is an explicit user act.
        """
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryNoticeSeen": None})

        assert task.is_telemetry_active() is False

    def test_notice_alone_is_not_enough(self, temp_settings, notice_acknowledged):
        """Acknowledging the notice does not re-enable a setting turned off."""
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": False})

        assert task.is_telemetry_active() is False


class TestActivationGrace:
    """No send within GRACE_AFTER_ACTIVATION of telemetry becoming active."""

    def test_no_grace_when_never_observed_becoming_active(self):
        """Enabled all along, including every install predating the field."""
        assert task.within_activation_grace({}) is False
        assert task.within_activation_grace({"active_since": None}) is False

    def test_grace_holds_just_after_activation(self):
        now = datetime.now(UTC).isoformat()

        assert task.within_activation_grace({"active_since": now}) is True

    def test_grace_holds_one_minute_before_expiry(self):
        almost = datetime.now(UTC) - timedelta(seconds=task.GRACE_AFTER_ACTIVATION - 60)

        assert task.within_activation_grace({"active_since": almost.isoformat()}) is True

    def test_grace_over_once_the_window_has_passed(self):
        past = datetime.now(UTC) - timedelta(seconds=task.GRACE_AFTER_ACTIVATION + 1)

        assert task.within_activation_grace({"active_since": past.isoformat()}) is False

    def test_unparseable_value_never_blocks_forever(self):
        assert task.within_activation_grace({"active_since": "not-a-timestamp"}) is False

    def test_build_pending_payload_returns_nothing_during_grace(
        self, temp_state, temp_settings, notice_acknowledged, monkeypatch
    ):
        """The end-to-end gate: an acknowledgement is never followed by a send."""
        import twicc.telemetry.state as state_mod

        # A complete unsent day exists, so only the grace can hold the payload back.
        with state_mod.state_txn() as txn:
            txn.data["last_sent_date"] = (utc_today() - timedelta(days=3)).isoformat()
            txn.data["active_since"] = datetime.now(UTC).isoformat()
            txn.write()

        assert task.build_pending_payload() is None

    def test_build_pending_payload_resumes_after_the_grace(
        self, temp_state, temp_settings, notice_acknowledged, monkeypatch, db
    ):
        import twicc.telemetry.state as state_mod

        with state_mod.state_txn() as txn:
            txn.data["last_sent_date"] = (utc_today() - timedelta(days=3)).isoformat()
            txn.data["active_since"] = (
                datetime.now(UTC) - timedelta(seconds=task.GRACE_AFTER_ACTIVATION + 1)
            ).isoformat()
            txn.write()

        assert task.build_pending_payload() is not None


class TestTelemetrySettingsBootstrap:
    @pytest.mark.parametrize("initial", [True, None, False])
    def test_environment_disables_copied_setting(self, temp_state, temp_settings, monkeypatch, initial):
        from twicc.telemetry import state

        ss.write_synced_settings({
            "telemetryEnabled": initial,
            "telemetryNoticeSeen": False,
            "publicBaseUrl": "https://example.com",
        })
        monkeypatch.setenv("TWICC_NO_TELEMETRY", "1")
        state.apply_telemetry_settings_bootstrap()
        ss._cache.clear()
        settings = ss.read_synced_settings()
        assert settings["telemetryEnabled"] is False
        assert settings["telemetryNoticeSeen"] is False
        assert settings["publicBaseUrl"] == "https://example.com"

    def test_manual_enable_survives_restart(self, temp_state, temp_settings, monkeypatch):
        from twicc.telemetry import state

        monkeypatch.setenv("TWICC_NO_TELEMETRY", "1")
        state.apply_telemetry_settings_bootstrap()
        ss.write_synced_settings({**ss.read_synced_settings(), "telemetryEnabled": True})
        ss._cache.clear()
        state.apply_telemetry_settings_bootstrap()
        assert ss.read_synced_settings()["telemetryEnabled"] is True

    @pytest.mark.parametrize("value", [None, "0", "false"])
    def test_without_opt_out_keeps_setting(self, temp_state, temp_settings, monkeypatch, value):
        from twicc.telemetry import state

        if value is None:
            monkeypatch.delenv("TWICC_NO_TELEMETRY", raising=False)
        else:
            monkeypatch.setenv("TWICC_NO_TELEMETRY", value)
        state.apply_telemetry_settings_bootstrap()
        assert ss.read_synced_settings()["telemetryEnabled"] is True
        assert not temp_state.exists()
