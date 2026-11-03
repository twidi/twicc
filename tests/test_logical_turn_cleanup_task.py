"""Bounded live-journal expiry and writer lifecycle contracts."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.models import LogicalTurn, LogicalTurnInput, ProcessRun, TimelineEvent

pytestmark = pytest.mark.django_db(transaction=True)


def turn(*, opened_at, closed_at=None, outcome=None):
    return LogicalTurn.objects.create(
        id=uuid4(), session_id="cleanup-session", provider="codex", run_id=uuid4(),
        source_floor=0, ordinal=1, position=LogicalTurn.objects.count() + 1,
        opened_at=opened_at, closed_at=closed_at, outcome=outcome,
    )


def test_inclusive_boundaries_cascade_and_recent_closure(monkeypatch):
    from twicc.logical_turn_cleanup_task import purge_logical_turns
    from twicc.core.services import timeline_signal

    now = timezone.now()
    old_closed = turn(opened_at=now - timedelta(days=10), closed_at=now - timedelta(hours=48), outcome="normal")
    old_open = turn(opened_at=now - timedelta(days=7))
    LogicalTurnInput.objects.create(id=uuid4(), turn=old_open, admission_cursor=0, status="accepted")
    recent_closed = turn(opened_at=now - timedelta(days=10), closed_at=now, outcome="normal")
    recent_open = turn(opened_at=now - timedelta(days=7) + timedelta(microseconds=1))
    recent_boundary = turn(opened_at=now - timedelta(days=3), closed_at=now - timedelta(hours=48) + timedelta(microseconds=1), outcome="ended")
    monkeypatch.setattr(timeline_signal, "poke", lambda: pytest.fail("Cleanup broadcasts a timeline change"))

    assert purge_logical_turns(now=now) == 2
    assert set(LogicalTurn.objects.values_list("id", flat=True)) == {recent_closed.id, recent_open.id, recent_boundary.id}
    assert not LogicalTurnInput.objects.exists()
    assert not LogicalTurn.objects.filter(id__in=[old_closed.id, old_open.id]).exists()
    assert TimelineEvent.objects.count() == 0
    assert ProcessRun.objects.count() == 0


def test_batch_limit_and_transaction_rollback():
    from twicc.logical_turn_cleanup_task import purge_logical_turns

    now = timezone.now()
    for _ in range(3):
        turn(opened_at=now - timedelta(days=8))
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            assert purge_logical_turns(now=now, limit=2) == 2
            raise RuntimeError("rollback")
    assert LogicalTurn.objects.count() == 3
    assert purge_logical_turns(now=now, limit=2) == 2
    assert LogicalTurn.objects.count() == 1


def test_boot_drains_more_than_one_batch_before_settlement(monkeypatch):
    from twicc.agent.process_run_cleanup import cleanup_stale_process_runs
    from twicc.providers import db_writer

    now = timezone.now()
    LogicalTurn.objects.bulk_create([
        LogicalTurn(id=uuid4(), session_id="cleanup-session", provider="codex", run_id=uuid4(),
                    source_floor=0, ordinal=1, position=i + 1, opened_at=now - timedelta(days=8))
        for i in range(501)
    ])
    survivor = turn(opened_at=now - timedelta(days=1))
    admitted = []

    async def submit(job):
        admitted.append(type(job).__name__)
        await db_writer._dispatch_async_job(job)
        return await job.future

    monkeypatch.setattr(db_writer, "submit_async_job", submit)
    monkeypatch.setattr(db_writer, "run_compute_sync", asyncio.to_thread)
    asyncio.run(cleanup_stale_process_runs())
    assert LogicalTurn.objects.count() == 1
    survivor.refresh_from_db()
    assert survivor.outcome == "ended"
    assert len(admitted) == 3  # Two independent purge jobs, then boot settlement.


def test_cycle_freezes_time_and_stops_between_batches(monkeypatch):
    from twicc import logical_turn_cleanup_task as cleanup
    from twicc.providers import db_writer

    async def scenario():
        stop = asyncio.Event()
        jobs = []

        async def submit(job):
            jobs.append(job)
            if len(jobs) == 2:
                stop.set()
            return 500

        monkeypatch.setattr(db_writer, "submit_async_job", submit)
        assert await cleanup.run_logical_turn_cleanup_cycle(stop) == 1000
        assert len(jobs) == 2
        assert jobs[0].now is jobs[1].now
        assert all(job.limit == 500 for job in jobs)

    asyncio.run(scenario())


def test_hourly_loop_retries_failure_and_stops(monkeypatch):
    from twicc import logical_turn_cleanup_task as cleanup

    async def scenario():
        stop = asyncio.Event()
        attempts = []

        async def cycle(event):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient")
            stop.set()
            return 0

        monkeypatch.setattr(cleanup, "CLEANUP_INTERVAL_SECONDS", 0.001)
        monkeypatch.setattr(cleanup, "run_logical_turn_cleanup_cycle", cycle)
        await asyncio.wait_for(cleanup.start_logical_turn_cleanup_task(stop), timeout=2)
        assert len(attempts) == 2
        await cleanup.start_logical_turn_cleanup_task(stop)
        assert len(attempts) == 2

    asyncio.run(scenario())
