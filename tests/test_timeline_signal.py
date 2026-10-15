# tests/test_timeline_signal.py
"""The ``timeline_changed`` signal (display design 2026-10-15, sections 9.1 and 9.3)."""

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.models import Project, Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services import timeline_signal
from twicc.core.services.session_timeline import (
    EventSpec, insert_event_rows, record_event_sync, record_events_bulk_sync,
)
from twicc.core.services.timeline_backfill import run_timeline_backfill
from twicc.providers import db_writer
from tests.timeline_helpers import run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def reset_signal():
    timeline_signal.stop_timeline_signal()
    yield
    timeline_signal.stop_timeline_signal()


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-signal", directory="/tmp/tl-signal")


@pytest.fixture
def pokes(monkeypatch):
    """Count the pokes writers request (the module function, replaced where writers look it up)."""
    calls = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: calls.append(1))
    return calls


@pytest.fixture
def sent(monkeypatch):
    """The messages the signal sends to the ``updates`` group."""
    messages = []

    async def group_send(group, message):
        messages.append((group, message))

    monkeypatch.setattr(timeline_signal, "get_channel_layer", lambda: SimpleNamespace(group_send=group_send))
    return messages


def make_event(**fields):
    return TimelineEvent.objects.create(
        kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
        session_id="s1", **fields,
    )


def run_loop(scenario, debug=False):
    """Run ``scenario`` on a fresh loop with the signal started (the throttle comes from ``short_throttle``)."""
    async def runner():
        timeline_signal.start_timeline_signal(asyncio.get_running_loop())
        try:
            return await scenario()
        finally:
            timeline_signal.stop_timeline_signal()

    return asyncio.run(runner(), debug=debug)


@pytest.fixture
def short_throttle(monkeypatch):
    monkeypatch.setattr(timeline_signal, "THROTTLE_SECONDS", 0.1)
    return 0.1


# -- Writers request the signal after the commit ---------------------------------------------------------------


def test_a_single_write_requests_the_signal_only_after_the_commit(pokes):
    with transaction.atomic():
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        assert pokes == []
    assert pokes == [1]


def test_a_write_outside_any_transaction_requests_the_signal_at_once(pokes):
    record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert pokes == [1]


def test_a_rolled_back_write_sends_nothing(pokes):
    with pytest.raises(RuntimeError), transaction.atomic():
        record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        raise RuntimeError("the caller fails afterwards")
    assert pokes == [] and TimelineEvent.objects.count() == 0


def test_a_dedupe_hit_sends_nothing(pokes):
    kwargs = {"session_id": "s1", "actor": TimelineActor.SYSTEM, "dedupe_key": "k", "data": {"origin": "sync"}}
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is not None
    assert pokes == [1]
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is None
    assert pokes == [1]


def test_a_failed_insert_sends_nothing(pokes):
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                             data={"bogus": 1}) is None
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor="system", precision=None) is None
    assert pokes == []


def test_a_non_empty_batch_requests_one_signal_and_an_all_duplicate_batch_none(pokes):
    specs = [EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=f"s{i}") for i in range(3)]
    assert record_events_bulk_sync(specs) == 3
    assert pokes == [1]
    keyed = [EventSpec(TimelineKind.SESSION_CREATED, session_id="k", dedupe_key="x", data={"origin": "sync"})]
    assert record_events_bulk_sync(keyed) == 1
    assert pokes == [1, 1]
    assert record_events_bulk_sync(keyed) == 0
    assert pokes == [1, 1]


def test_a_batch_requests_the_signal_only_after_the_commit(pokes):
    with transaction.atomic():
        assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
        assert pokes == []
    assert pokes == [1]


def test_an_empty_batch_sends_nothing(pokes):
    assert record_events_bulk_sync([]) == 0
    assert pokes == []


def test_the_bulk_archive_requests_the_signal(project, pokes):
    from twicc.core.services.session_update import archive_sessions_bulk

    for sid in ("a", "b"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    archive_sessions_bulk({"a", "b"})
    assert pokes == [1]


def test_the_batch_fallback_requests_the_signal_per_row_written(pokes, monkeypatch):
    from twicc.core.services import session_timeline

    def failing_batch(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", failing_batch)
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
    assert pokes == [1]


def test_the_raw_batch_insert_itself_is_silent(pokes):
    rows = [TimelineEvent(
        session_id="s1", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
        data={},
    )]
    assert insert_event_rows(rows) == 1
    assert pokes == []


def test_the_backfill_is_silent_per_session_and_signals_once_at_the_end_of_a_run_that_wrote(project, pokes):
    for sid in ("a", "b", "c"):
        Session.objects.create(
            id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex",
            created_at=timezone.now() - timedelta(days=1), archived=True,
        )
    run_with_writer(run_timeline_backfill)
    assert TimelineEvent.objects.count() == 6
    assert pokes == [1]


def test_a_backfill_run_that_wrote_nothing_sends_nothing(project, pokes):
    run_with_writer(run_timeline_backfill)
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex")  # no created_at
    run_with_writer(run_timeline_backfill)
    assert pokes == []


def test_a_failing_poke_does_not_fail_the_writer(monkeypatch):
    def boom():
        raise RuntimeError("signal broken")

    monkeypatch.setattr(timeline_signal, "poke", boom)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert event is not None and TimelineEvent.objects.count() == 1


def test_a_failing_on_commit_registration_does_not_fail_the_writer(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("on_commit broken")

    monkeypatch.setattr(timeline_signal.transaction, "on_commit", boom)
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT) is not None
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s2")]) == 1


# -- The emission ---------------------------------------------------------------------------------------------------


def test_the_throttle_is_a_third_of_a_second():
    assert timeline_signal.THROTTLE_SECONDS == 0.3


def test_a_failing_poke_at_commit_time_does_not_break_the_transaction_exit(monkeypatch):
    # ``on_commit(..., robust=True)``: the callback runs at the exit of the outer atomic block, whose caller
    # must not see the failure.
    def boom():
        raise RuntimeError("signal broken")

    monkeypatch.setattr(timeline_signal, "poke", boom)
    with transaction.atomic():
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT) is not None
    assert TimelineEvent.objects.count() == 1


def test_a_poke_without_a_running_loop_is_a_noop():
    timeline_signal.poke()
    timeline_signal.poke_on_commit()


def test_a_poke_on_a_closed_loop_is_caught():
    loop = asyncio.new_event_loop()
    timeline_signal.start_timeline_signal(loop)
    loop.close()
    timeline_signal.poke()


def test_a_burst_sends_one_message_with_the_latest_id(sent, short_throttle):
    made = [make_event() for _ in range(3)]

    async def scenario():
        for _ in range(20):
            await asyncio.to_thread(timeline_signal.poke)
        await asyncio.sleep(0.5)

    run_loop(scenario)
    assert sent == [("updates", {
        "type": "broadcast", "data": {"type": "timeline_changed", "latest_id": made[-1].id},
    })]


def test_pokes_from_worker_threads_reach_the_loop_safely(sent, short_throttle):
    # In debug mode asyncio refuses ``call_later`` from another thread: a poke that touched the timer directly
    # (instead of going through ``call_soon_threadsafe``) would be dropped and nothing would be sent.
    make_event()

    async def scenario():
        await asyncio.gather(*(asyncio.to_thread(timeline_signal.poke) for _ in range(5)))
        await asyncio.sleep(0.5)

    run_loop(scenario, debug=True)
    assert len(sent) == 1


def test_a_sustained_stream_sends_at_the_throttle_rate(sent, short_throttle):
    make_event()

    async def scenario():
        for _ in range(40):
            timeline_signal.poke()
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)

    run_loop(scenario)
    # 0.4 s of pokes with a 0.1 s window: well below one signal per poke (40), well above a single one.
    assert 2 <= len(sent) <= 8


def test_the_signal_waits_for_the_window_then_a_later_poke_opens_a_new_one(sent, short_throttle):
    make_event()

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.01)
        early = len(sent)
        await asyncio.sleep(0.3)
        first = len(sent)
        timeline_signal.poke()
        await asyncio.sleep(0.3)
        return early, first, len(sent)

    assert run_loop(scenario) == (0, 1, 2)


def test_an_empty_table_sends_nothing(sent, short_throttle):
    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert sent == []


def test_a_failed_send_is_logged_and_the_next_poke_still_works(monkeypatch, short_throttle):
    make_event()
    attempts = []
    logged = []

    async def group_send(group, message):
        attempts.append(message)
        if len(attempts) == 1:
            raise RuntimeError("layer down")

    monkeypatch.setattr(timeline_signal, "get_channel_layer", lambda: SimpleNamespace(group_send=group_send))
    # Spy on the logger itself: other tests of the suite reconfigure the ``twicc`` loggers, so ``caplog`` is not reliable.
    monkeypatch.setattr(timeline_signal.logger, "exception", lambda message, *args: logged.append(message % args))

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.3)
        timeline_signal.poke()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert len(attempts) == 2
    assert logged == ["Timeline signal could not be sent"]


def test_the_emission_does_not_need_the_db_write_lock(sent, short_throttle):
    make_event()

    async def scenario():
        db_writer.start_db_writer()
        release = asyncio.Event()

        async def hold_the_lock():
            async def inside():
                await release.wait()

            await db_writer.run_under_db_write_lock(inside)

        holder = asyncio.create_task(hold_the_lock())
        try:
            await asyncio.sleep(0.1)
            timeline_signal.poke()
            await asyncio.sleep(0.4)
            return len(sent)
        finally:
            release.set()
            await holder
            await db_writer.stop_db_writer()

    assert run_loop(scenario) == 1


def test_stopping_the_signal_drops_a_pending_window(sent, short_throttle):
    make_event()

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.01)
        timeline_signal.stop_timeline_signal()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert sent == []


def test_a_message_reaches_a_websocket_client_through_the_updates_group(short_throttle):
    """The real channel layer: a consumer in the ``updates`` group receives the unfiltered frame."""
    from channels.layers import get_channel_layer

    make_event()

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)
        timeline_signal.start_timeline_signal(asyncio.get_running_loop())
        timeline_signal.poke()
        message = await asyncio.wait_for(layer.receive(channel), timeout=2)
        timeline_signal.stop_timeline_signal()
        return message

    message = asyncio.run(scenario())
    assert message["type"] == "broadcast" and message["data"]["type"] == "timeline_changed"
    assert isinstance(message["data"]["latest_id"], int)


# -- Wiring ---------------------------------------------------------------------------------------------------------


def test_the_server_source_starts_the_signal_before_the_backfill_and_stops_it_at_shutdown():
    source = Path(__import__("twicc.cli.run", fromlist=["run"]).__file__).read_text()
    start = source.index("start_timeline_signal(asyncio.get_running_loop())")
    backfill = source.index('timeline_backfill_task = asyncio.create_task(_guarded_timeline_backfill()')
    cancel = source.index('await _cancel_task(timeline_backfill_task, "Timeline backfill")')
    stop = source.index("stop_timeline_signal()", cancel)
    shutdown = source[cancel:source.index("Stop cross-provider tasks first", cancel)]
    assert start < backfill and cancel < stop
    assert "stop_timeline_signal()" in shutdown, "the normal shutdown stops it right after the backfill is cancelled"
    # The outer ``finally`` (partial start) stops the signal FIRST, so a failing cancel cannot skip it.
    outer = source[source.index("# Own partial coordinator startup"):]
    assert 0 <= outer.index("stop_timeline_signal()") < outer.index("_cancel_task(")
    assert "from twicc.core.services.timeline_signal import start_timeline_signal, stop_timeline_signal" in source
