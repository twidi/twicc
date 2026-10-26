"""Opt-in benchmark of the bulk archive write. Run with:
TWICC_TIMELINE_BENCH=1 uv run pytest tests/test_timeline_bulk_archive_bench.py -s -v
It prints the time the DB write lock is held and the write statement counts, before (the old
single-transaction implementation, kept here as a reference) and after; it asserts only
structural facts, not a performance gate."""

import asyncio
import os
import re
import time

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.db_write_helpers import run_with_writer
from twicc.core.models import Project, Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.session_update import archive_sessions_bulk
from twicc.providers.db_writer import run_under_db_write_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(os.environ.get("TWICC_TIMELINE_BENCH") != "1", reason="set TWICC_TIMELINE_BENCH=1"),
]

SESSIONS = 2000


def archive_sessions_bulk_before(ids):
    """Reference: the implementation of commit d4124ea0 (reads, update and one savepoint insert per row)."""
    with transaction.atomic():
        to_flip = list(Session.objects.filter(id__in=ids, archived=False).values_list("id", "project_id"))
        Session.objects.filter(id__in=ids).update(archived=True, search_version=0)
        for session_id, project_id in to_flip:
            record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id=session_id, project_id=project_id,
                              actor=TimelineActor.USER)
    return [session_id for session_id, _ in to_flip]


def _seed():
    project = Project.objects.create(id="-tl-bench-arch", directory="/tmp/tl-bench-arch")
    now = timezone.now()
    Session.objects.bulk_create([
        Session(id=f"s{i:05d}", file_path=f"s{i:05d}.jsonl", project=project, provider="codex",
                created_at=now, last_updated_at=now, mtime=float(i)) for i in range(SESSIONS)
    ])
    return {f"s{i:05d}" for i in range(SESSIONS)}


def _writes(captured):
    sqls = [re.sub(r"^\d+ times: ", "", q["sql"]) for q in captured.captured_queries]
    return [s for s in sqls if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


IMPLEMENTATIONS = [("before", archive_sessions_bulk_before), ("after", archive_sessions_bulk)]


@pytest.mark.parametrize("name,func", IMPLEMENTATIONS)
def test_lock_hold_time(name, func):
    ids = _seed()
    held = []

    async def call():
        def timed():
            started = time.perf_counter()
            try:
                return func(ids)
            finally:
                held.append(time.perf_counter() - started)

        return await run_under_db_write_lock(lambda: asyncio.to_thread(timed))

    # Real writer: the benchmark measures how long the real DB write lock is held.
    flipped = run_with_writer(call)
    print(f"\n[{name}] {SESSIONS} sessions: lock held {held[0] * 1000:.1f} ms")
    assert len(flipped) == SESSIONS
    assert TimelineEvent.objects.filter(kind="archived.started").count() == SESSIONS


@pytest.mark.parametrize("name,func", IMPLEMENTATIONS)
def test_statement_counts(name, func):
    ids = _seed()
    with CaptureQueriesContext(connection) as captured:
        func(ids)
    print(f"\n[{name}] {SESSIONS} sessions: queries {len(captured)}; write statements {len(_writes(captured))}")
