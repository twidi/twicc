"""Opt-in benchmark of the incremental backfill. Run with:
TWICC_TIMELINE_BENCH=1 TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_bench.py -s -v
It prints timings and asserts only structural facts: it is not a pass/fail performance gate."""

import os
import time

import orjson
import pytest
from django.db import connection, transaction
from django.utils import timezone

from tests.timeline_helpers import run_with_writer
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services import timeline_backfill

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(os.environ.get("TWICC_TIMELINE_BENCH") != "1", reason="set TWICC_TIMELINE_BENCH=1"),
]

SESSIONS = 2000


def _project():
    return Project.objects.create(id="-tl-bench", directory="/tmp/tl-bench")


def test_per_session_time_and_total_duration(monkeypatch):
    project = _project()
    now = timezone.now()
    Session.objects.bulk_create([
        Session(id=f"s{i:05d}", file_path=f"s{i:05d}.jsonl", project=project, provider="codex",
                created_at=now, last_updated_at=now, mtime=float(i), archived=(i % 5 == 0))
        for i in range(SESSIONS)
    ])
    spans = []
    real = timeline_backfill.backfill_session_sync

    def timed(session_id):
        started = time.perf_counter()
        try:
            return real(session_id)
        finally:
            spans.append(time.perf_counter() - started)

    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", timed)
    begin = time.perf_counter()
    run_with_writer(timeline_backfill.run_timeline_backfill)
    first = time.perf_counter() - begin
    millis = sorted(span * 1000 for span in spans)
    print(f"\nfirst run: {len(millis)} sessions in {first:.2f}s; per session ms: "
          f"median {millis[len(millis) // 2]:.2f}, p95 {millis[int(len(millis) * 0.95)]:.2f}, max {millis[-1]:.2f}")
    assert len(millis) == SESSIONS
    spans.clear()
    begin = time.perf_counter()
    run_with_writer(timeline_backfill.run_timeline_backfill)
    print(f"following run: {time.perf_counter() - begin:.3f}s, sessions processed: {len(spans)}")
    assert not spans


def _rows(project_id, session_id):
    now = timezone.now()
    return [
        TimelineEvent(session_id=session_id, project_id=project_id, kind=kind, occurred_at=now,
                      precision="derived", actor="system", data={}, dedupe_key=f"backfill:{kind}")
        for kind in ("session.created", "archived.started", "hidden.started")
    ]


def _per_row(rows):
    for row in rows:
        row.save(force_insert=True)


def _bulk(rows):
    TimelineEvent.objects.bulk_create(rows)


def _executemany(rows):
    ops = connection.ops
    params = [
        (r.session_id, r.project_id, r.kind, ops.adapt_datetimefield_value(r.occurred_at),
         ops.adapt_datetimefield_value(timezone.now()), r.precision, r.actor, "", None,
         orjson.dumps(r.data).decode(), r.dedupe_key)
        for r in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            'INSERT INTO "core_timelineevent" ("session_id","project_id","kind","occurred_at","recorded_at",'
            '"precision","actor","reason","caused_by_id","data","dedupe_key") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            params,
        )


def test_insert_variants_one_transaction_per_session():
    project = _project()
    timings = {}
    for name, insert in (("per_row", _per_row), ("bulk_create", _bulk), ("executemany", _executemany)):
        TimelineEvent.objects.all().delete()
        begin = time.perf_counter()
        for i in range(SESSIONS):
            with transaction.atomic():
                insert(_rows(project.id, f"{name}-{i}"))
        timings[name] = time.perf_counter() - begin
        assert TimelineEvent.objects.count() == SESSIONS * 3
    print("\ninsert variants, total seconds for", SESSIONS, "sessions x 3 rows:", {k: round(v, 3) for k, v in timings.items()})
