"""Unified statistics keeps historical reads outside real writer ownership."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection

from twicc.core.models import Project, Session, SessionItem, StatisticsQueueEntry
from twicc.providers import db_writer, statistics_runtime

pytestmark = pytest.mark.django_db(transaction=True)


def test_400_member_publication_has_no_historical_reads_under_writer(monkeypatch):
    project = Project.objects.create(id='contention')
    stamp = datetime(2026, 10, 14, tzinfo=UTC)
    root = Session.objects.create(id='root', project=project, provider='codex', last_updated_at=stamp)
    Session.objects.bulk_create([Session(id=f'child-{n}', project=project, provider='codex',
        parent_session=root, type='subagent', file_path=f'child-{n}', last_updated_at=stamp) for n in range(399)])
    ids = list(Session.objects.values_list('id', flat=True))
    SessionItem.objects.bulk_create([SessionItem(session_id=sid, line_num=1, timestamp=stamp,
        cost=Decimal('.000001')) for sid in ids])
    StatisticsQueueEntry.objects.create(root_session_id=root.id)
    monkeypatch.setattr(statistics_runtime, 'COLLECTION_SECONDS', .001)
    ordinary = db_writer.run_compute_sync
    sql_inside_writer = []
    def recorded(function, args, kwargs):
        def record(execute, sql, params, many, context):
            sql_inside_writer.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(record):
            return function(*args, **kwargs)
    async def runner(function, /, *args, **kwargs):
        return await ordinary(recorded, function, args, kwargs)
    monkeypatch.setattr(db_writer, 'run_compute_sync', runner)

    async def scenario():
        db_writer.start_db_writer()
        try:
            async with asyncio.timeout(10):
                while await asyncio.to_thread(StatisticsQueueEntry.objects.exists):
                    await asyncio.sleep(.01)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
    root.refresh_from_db()
    assert root.total_cost == Decimal('.000400')
    assert sql_inside_writer
    assert not any('core_sessionitem' in sql.lower() for sql in sql_inside_writer)
