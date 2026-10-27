"""Partial fact publication stays hidden until the final guarded commit."""

import pytest
from django.db import connection, transaction

from tests.test_content_extraction import make_session, message
from twicc.core.content_storage import extraction
from twicc.core.models import HistoryFactKind, Project, Session, SessionHistoryFact, SessionItem
from twicc.providers import history_facts as facts
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)


def fact(line, key='call'):
    return facts.HistoryFact(line, HistoryFactKind.TOOL_CALL, key, {'value': 'é' * 8})


def advance_to_history(operation):
    while operation.phase != 'history_stage':
        batch = extraction.prepare_compute_batch(operation)
        extraction.publish_compute_batch(operation, batch, frozenset())


@pytest.mark.parametrize('provider', ['codex', 'claude_code'])
@pytest.mark.parametrize('count', [0, 385])
def test_partial_pages_use_raw_fallback_until_final_commit(provider, count):
    session = make_session()
    Session.objects.filter(pk=session.pk).update(provider=provider)
    session.refresh_from_db()
    SessionItem.objects.bulk_create([SessionItem(session=session, line_num=line, content='{}') for line in (1, 2, 3)])
    facts.append_history_facts(session.id, [fact(line, 'old') for line in range(1, 386)])
    target = get_provider_helpers(provider).current_compute_version
    msg = message(session, history_facts=[fact(line)._asdict() for line in range(1, count + 1)],
                  session_fields={'compute_version': target})
    operation = extraction.begin_compute_application(msg, progressive_facts=True)
    phases = []
    try:
        while operation.phase != 'final':
            batch = extraction.prepare_compute_batch(operation)
            phases.append(batch.phase)
            extraction.publish_compute_batch(operation, batch, frozenset())
            session.refresh_from_db()
            assert not facts.history_facts_are_current(session)
            assert [item.line_num for item in facts.iter_resolver_items(session.id, HistoryFactKind.TOOL_CALL)] == [3, 2, 1]
            # Foreground work completes between every publication.
            Project.objects.filter(pk=session.project_id).update(name='foreground')
        assert phases.count('history_delete') >= 4
        assert phases.count('history_insert') >= (4 if count else 1)
        assert operation.history_facts_published
        def reject_final_fact_mutation(execute, sql, params, many, context):
            if sql.startswith(('DELETE', 'INSERT')) and SessionHistoryFact._meta.db_table in sql:
                raise AssertionError('Final publication must not replace progressive facts')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(reject_final_fact_mutation):
            assert BaseSessionCompute._apply_session_complete_final(
                extraction.final_compute_message(operation, frozenset())).outcome == 'applied'
        session.refresh_from_db()
        assert session.compute_version == target
        assert SessionHistoryFact.objects.filter(session=session).count() == count
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.parametrize('later', [False, True])
def test_progressive_facts_reject_current_before_first_delete_and_later_slices(later):
    session = make_session()
    facts.append_history_facts(session.id, [fact(1, 'old')])
    operation = extraction.begin_compute_application(message(session), progressive_facts=True)
    try:
        advance_to_history(operation)
        if later:
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        Session.objects.filter(pk=session.pk).update(
            compute_version=get_provider_helpers(session.provider).current_compute_version)
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert SessionHistoryFact.objects.filter(session=session, key='old').exists()
    finally:
        extraction.close_compute_application(operation)


def test_page_bytes_duplicates_gaps_and_indexed_interval():
    session = make_session()
    stage = facts.prepare_compute_history_facts(session.id, [fact(1), fact(1), fact(2), fact(3)])
    try:
        page = facts.prepare_fact_insert_page(stage, -1, byte_budget=1)
        assert page.positions == (0,)
        assert page.bytes > 1
        with transaction.atomic():
            facts.publish_fact_insert_page(stage, page)
        page = facts.prepare_fact_insert_page(stage, page.cursor)
        assert page.positions == (2, 3)
        with transaction.atomic():
            facts.publish_fact_insert_page(stage, page)
        assert SessionHistoryFact.objects.filter(session=session).count() == 3
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN QUERY PLAN SELECT * FROM temp.{stage.table} '
                           'WHERE source_order > %s AND source_order <= %s ORDER BY source_order', [-1, 3])
            plan = str(cursor.fetchall())
        assert 'SEARCH' in plan and 'INTEGER PRIMARY KEY' in plan
        assert 'TEMP B-TREE' not in plan
    finally:
        stage.close()


def test_source_append_supersedes_prepared_fact_delete():
    session = make_session()
    facts.append_history_facts(session.id, [fact(1, 'old')])
    operation = extraction.begin_compute_application(message(session), progressive_facts=True)
    try:
        advance_to_history(operation)
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        batch = extraction.prepare_compute_batch(operation)
        original = operation.cursor
        # An authoritative source append invalidates this prepared page.
        Session.objects.filter(pk=session.pk).update(last_offset=session.last_offset + 1)
        facts.append_history_facts(session.id, [fact(11, 'watcher')])
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
        assert operation.cursor == original
        assert operation.pending_batch is batch
        assert SessionHistoryFact.objects.filter(session=session, key='old').exists()
    finally:
        extraction.close_compute_application(operation)


def test_current_compatibility_facts_remain_atomic():
    session = make_session()
    target = get_provider_helpers(session.provider).current_compute_version
    Session.objects.filter(pk=session.pk).update(compute_version=target)
    facts.append_history_facts(session.id, [fact(1, 'old')])
    msg = extraction.prepare_compute_result(message(session, history_facts=[fact(2)._asdict()],
                                                    session_fields={'compute_version': target}))
    assert list(SessionHistoryFact.objects.filter(session=session).values_list('key', flat=True)) == ['old']
    assert BaseSessionCompute._apply_session_complete_final(msg).outcome == 'applied'
    assert list(SessionHistoryFact.objects.filter(session=session).values_list('key', flat=True)) == ['call']


def test_commit_failure_retains_fact_page_and_old_rows(monkeypatch):
    from django.db import DatabaseError

    session = make_session()
    facts.append_history_facts(session.id, [fact(1, 'old')])
    operation = extraction.begin_compute_application(message(session), progressive_facts=True)
    try:
        advance_to_history(operation)
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        batch = extraction.prepare_compute_batch(operation)
        original_cursor = operation.cursor
        def fail():
            raise DatabaseError('commit rejected')
        with monkeypatch.context() as patch:
            patch.setattr(connection, 'commit', fail)
            with pytest.raises(DatabaseError, match='commit rejected'):
                extraction.publish_compute_batch(operation, batch, frozenset())
        assert operation.cursor == original_cursor
        assert operation.pending_batch is batch
        assert SessionHistoryFact.objects.filter(session=session, key='old').exists()
        extraction.publish_compute_batch(operation, batch, frozenset())
        assert not SessionHistoryFact.objects.filter(session=session).exists()
    finally:
        extraction.close_compute_application(operation)


def test_actual_insert_interval_uses_index_with_75000_facts():
    session = make_session()
    stage = facts.prepare_compute_history_facts(session.id, (fact(line) for line in range(1, 75001)))
    statements = []
    def explain(execute, sql, params, many, context):
        if sql.startswith('INSERT OR IGNORE INTO') and 'SELECT' in sql:
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                statements.extend(row[3] for row in cursor.fetchall())
        return execute(sql, params, many, context)
    try:
        page = facts.prepare_fact_insert_page(stage, 74000)
        with connection.execute_wrapper(explain), transaction.atomic():
            facts.publish_fact_insert_page(stage, page)
        assert len(page.positions) == 128
        assert SessionHistoryFact.objects.filter(session=session).count() == 128
        assert any('SEARCH' in row and 'INTEGER PRIMARY KEY' in row for row in statements)
        assert not any('SCAN' in row or 'TEMP B-TREE' in row for row in statements)
    finally:
        stage.close()


@pytest.mark.parametrize('helper', ['prepare_fact_delete_page', 'prepare_fact_insert_page',
                                    'publish_fact_delete_page', 'publish_fact_insert_page'])
def test_page_helpers_reject_foreign_thread(helper):
    from concurrent.futures import ThreadPoolExecutor

    session = make_session()
    stage = facts.prepare_compute_history_facts(session.id, [])
    argument = facts.FactPage(0, (), 0, True) if helper.startswith('publish') else 0
    try:
        with ThreadPoolExecutor(max_workers=1) as executor, pytest.raises(ValueError, match='owning connection'):
            executor.submit(getattr(facts, helper), stage, argument).result()
    finally:
        stage.close()


def test_missing_progressive_facts_never_removes_existing_rows():
    session = make_session()
    facts.append_history_facts(session.id, [fact(1, 'old')])
    msg = message(session)
    del msg['history_facts']
    with pytest.raises(ValueError, match='history_facts'):
        extraction.begin_compute_application(msg, progressive_facts=True)
    assert SessionHistoryFact.objects.filter(session=session, key='old').exists()


def test_interrupted_insert_remains_outdated_and_next_operation_reconstructs():
    session = make_session()
    payload = [fact(line)._asdict() for line in range(1, 386)]
    msg = message(session, history_facts=payload)
    operation = extraction.begin_compute_application(msg, progressive_facts=True)
    try:
        while operation.phase != 'history_insert':
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert SessionHistoryFact.objects.filter(session=session).count() == 128
    finally:
        extraction.close_compute_application(operation)
    session.refresh_from_db()
    assert not facts.history_facts_are_current(session)
    operation = extraction.begin_compute_application(msg, progressive_facts=True)
    try:
        while operation.phase != 'final':
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        assert SessionHistoryFact.objects.filter(session=session).count() == 385
        assert operation.history_facts_published
    finally:
        extraction.close_compute_application(operation)


def test_current_version_between_insert_pages_prevents_mutation():
    session = make_session()
    operation = extraction.begin_compute_application(
        message(session, history_facts=[fact(line)._asdict() for line in range(1, 386)]), progressive_facts=True)
    try:
        while operation.phase != 'history_insert':
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        batch = extraction.prepare_compute_batch(operation)
        Session.objects.filter(pk=session.pk).update(
            compute_version=get_provider_helpers(session.provider).current_compute_version)
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
        assert SessionHistoryFact.objects.filter(session=session).count() == 128
        assert not operation.history_facts_published
    finally:
        extraction.close_compute_application(operation)


def test_delete_byte_budget_and_other_session_are_preserved():
    session = make_session()
    other = make_session(id='other')
    facts.append_history_facts(session.id, [fact(1), fact(2)])
    facts.append_history_facts(other.id, [fact(1)])
    stage = facts.prepare_compute_history_facts(session.id, [])
    try:
        page = facts.prepare_fact_delete_page(stage, 0, byte_budget=1)
        assert len(page.positions) == 1
        assert page.bytes > 1
        assert not page.done
        with transaction.atomic():
            facts.publish_fact_delete_page(stage, page)
        assert SessionHistoryFact.objects.filter(session=session).count() == 1
        assert SessionHistoryFact.objects.filter(session=other).count() == 1
    finally:
        stage.close()


def test_failed_final_preserves_facts_without_publishing_readiness():
    from django.core.exceptions import FieldDoesNotExist

    session = make_session()
    operation = extraction.begin_compute_application(message(session, history_facts=[fact(1)._asdict()],
        session_fields={'compute_version': get_provider_helpers(session.provider).current_compute_version,
                        'missing_column': 'fail final'}), progressive_facts=True)
    try:
        while operation.phase != 'final':
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        with pytest.raises(FieldDoesNotExist):
            BaseSessionCompute._apply_session_complete_final(extraction.final_compute_message(operation, frozenset()))
        session.refresh_from_db()
        assert not facts.history_facts_are_current(session)
        assert SessionHistoryFact.objects.filter(session=session).count() == 1
    finally:
        extraction.close_compute_application(operation)


def test_cancelled_operation_drains_worker_before_temp_cleanup():
    import asyncio
    import threading

    from twicc.providers import compute_executor

    session = make_session()
    msg = message(session, history_facts=[fact(line)._asdict() for line in range(1, 386)])
    started, release = threading.Event(), threading.Event()
    retained = []

    def publish_then_block():
        operation = extraction.begin_compute_application(msg, progressive_facts=True)
        retained.append(operation)
        while operation.phase != 'history_insert':
            extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        started.set()
        assert release.wait(5)

    def close():
        extraction.close_compute_application(retained[0])
        compute_executor.close_compute_application_connection()

    async def scenario():
        compute_executor.start_compute_application_executor()
        job = asyncio.create_task(compute_executor.run_compute_application_sync(publish_then_block))
        cleanup = None
        try:
            assert await asyncio.to_thread(started.wait, 3)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            cleanup = asyncio.create_task(compute_executor.run_compute_application_sync(close))
            await asyncio.sleep(0)
            assert not cleanup.done()
            assert not retained[0].history_facts_stage.closed
            release.set()
            await cleanup
            assert retained[0].history_facts_stage.closed
        finally:
            release.set()
            if cleanup is not None:
                await cleanup
            await compute_executor.stop_compute_application_executor()

    asyncio.run(scenario())
    session.refresh_from_db()
    assert not facts.history_facts_are_current(session)
    assert SessionHistoryFact.objects.filter(session=session).count() == 128


@pytest.mark.parametrize('count', [10000, 75000])
def test_delete_pages_bound_actual_prepare_and_publication_vm_work(count):
    """Neither SQL statement may scan the owner's remaining facts per page."""
    session = make_session()
    others = [make_session(id='aaa-unrelated-delete'), make_session(id='zzz-unrelated-delete')]
    rows = [(
        400000 - 2 * line, session.id, line,
        HistoryFactKind.TOOL_CALL if line % 2 else HistoryFactKind.CODE_CELL,
        f'key-{line % 19:02}', f'{{"value": {line}}}',
    ) for line in range(1, count + 1)]
    # Reverse PK order differs from the business-index order. Unrelated rows
    # surround the owner in the same existing index and use interleaved PKs.
    rows += [(400001 - 2 * line, others[line % 2].id, line,
              HistoryFactKind.TOOL_CALL, 'unrelated', '{}')
             for line in range(1, 1025)]
    # This fixture measures SQLite page work, not Django's model construction.
    # Keep the same rows and JSON bytes, including the default encoder's space.
    table = connection.ops.quote_name(SessionHistoryFact._meta.db_table)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.executemany(
            f'INSERT INTO {table} (id, session_id, line_num, kind, key, data) VALUES (%s, %s, %s, %s, %s, %s)',
            rows,
        )
    expected_ids = [row[0] for row in sorted(rows[:count], key=lambda row: (row[3], row[4], row[2]))]
    stage = facts.prepare_compute_history_facts(session.id, [])
    observations = []
    deleted = 0
    try:
        for remaining in (count, count // 2, 3):
            # Fixture setup jumps to the first, middle, and final source pages.
            remove = expected_ids[deleted:count - remaining]
            for offset in range(0, len(remove), 1000):
                SessionHistoryFact.objects.filter(pk__in=remove[offset:offset + 1000]).delete()
            deleted = count - remaining
            select_steps = 0
            def select_progress():
                nonlocal select_steps
                select_steps += 100
                return 0
            # Capture plans separately, so the SELECT step counter includes fetch.
            def capture(execute, sql, params, many, context):
                if sql.startswith('SELECT id, LENGTH'):
                    with connection.cursor() as cursor:
                        cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                        plan = [row[3] for row in cursor.fetchall()]
                    observations.append(('prepare', plan))
                return execute(sql, params, many, context)
            with connection.execute_wrapper(capture):
                connection.connection.set_progress_handler(select_progress, 100)
                try:
                    page = facts.prepare_fact_delete_page(stage, deleted)
                finally:
                    connection.connection.set_progress_handler(None, 0)
            assert select_steps < 10000, (count, remaining, select_steps)
            assert page.positions == tuple(expected_ids[deleted:deleted + min(128, remaining)])
            assert page.cursor == deleted + min(128, remaining)
            assert observations[-1][1] and all(
                'SEARCH' in plan and '(session_id=?)' in plan for plan in observations[-1][1])
            assert not any('TEMP B-TREE' in plan or 'SCAN' in plan for plan in observations[-1][1])
            delete_steps = 0
            def delete_progress():
                nonlocal delete_steps
                delete_steps += 100
                return 0
            def capture_delete(execute, sql, params, many, context):
                if sql.startswith('DELETE FROM'):
                    with connection.cursor() as cursor:
                        cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                        observations.append(('publish', [row[3] for row in cursor.fetchall()]))
                return execute(sql, params, many, context)
            with connection.execute_wrapper(capture_delete), transaction.atomic():
                connection.connection.set_progress_handler(delete_progress, 100)
                try:
                    facts.publish_fact_delete_page(stage, page)
                finally:
                    connection.connection.set_progress_handler(None, 0)
            assert delete_steps < 20000, (count, remaining, delete_steps)
            assert observations[-1][0] == 'publish'
            assert all('INTEGER PRIMARY KEY' in plan for plan in observations[-1][1]), observations
            print({'facts': count, 'remaining': remaining, 'prepare_vm_steps': select_steps,
                   'delete_vm_steps': delete_steps, 'prepare_plan': observations[-2][1],
                   'delete_plan': observations[-1][1]})
            deleted += len(page.positions)
        assert SessionHistoryFact.objects.filter(session__in=others).count() == 1024
        assert not SessionHistoryFact.objects.filter(session=session).exists()
    finally:
        stage.close()


def test_delete_count_cursor_byte_cut_and_rollback_retry():
    session = make_session()
    SessionHistoryFact.objects.bulk_create([
        SessionHistoryFact(id=pk, session=session, line_num=line, kind=HistoryFactKind.TOOL_CALL, key=key, data={})
        for pk, line, key in [(9500, 1, 'a'), (3, 2, 'b'), (8000, 3, 'c')]
    ])
    stage = facts.prepare_compute_history_facts(session.id, [])
    try:
        page = facts.prepare_fact_delete_page(stage, 0, byte_budget=1)
        assert page.positions == (9500,)
        assert page.cursor == 1
        assert page.bytes == 2 and not page.done
        with pytest.raises(RuntimeError, match='rollback'), transaction.atomic():
            facts.publish_fact_delete_page(stage, page)
            raise RuntimeError('rollback')
        assert facts.prepare_fact_delete_page(stage, 0, byte_budget=1) == page
        with transaction.atomic():
            facts.publish_fact_delete_page(stage, page)
        second = facts.prepare_fact_delete_page(stage, page.cursor, byte_budget=1)
        assert second.positions == (3,)
        assert second.cursor == 2
        with transaction.atomic():
            facts.publish_fact_delete_page(stage, second)
        final = facts.prepare_fact_delete_page(stage, second.cursor, byte_budget=1)
        assert final.positions == (8000,) and final.cursor == 3 and final.done
        with transaction.atomic():
            facts.publish_fact_delete_page(stage, final)
        empty = facts.prepare_fact_delete_page(stage, final.cursor)
        assert empty.positions == () and empty.cursor == 3 and empty.done
    finally:
        stage.close()
