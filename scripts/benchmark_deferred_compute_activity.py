"""Run identical persisted workloads in an isolated checkout and disposable data dir.

The controller has no Django imports. The child uses the target project's uv
resolution, then checks source and database paths before schema or data access.
Only synthetic schema setup runs migrations. No server or provider runs here.
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import random
import subprocess
import threading
from time import perf_counter
from uuid import uuid4

SEED = 20261013
STAMP = datetime(2026, 9, 1, 12, tzinfo=UTC)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    return parser.parse_args()


def launch(args):
    for value in (args.checkout, args.data_dir, args.output):
        if not value.is_absolute():
            raise ValueError('All paths must be absolute')
    checkout, data = args.checkout.resolve(), args.data_dir.resolve()
    if data == checkout or data.is_relative_to(checkout):
        raise ValueError('Use a separate disposable data directory outside the checkout')
    if data.exists():
        raise ValueError('Disposable data directory must not already exist')
    data.mkdir(parents=True)
    (data / '.synthetic-activity-benchmark').write_text('Synthetic fixtures only\n')
    env = dict(os.environ, TWICC_DATA_DIR=str(data), TWICC_DEBUG='0', DJANGO_SETTINGS_MODULE='twicc.settings')
    # Prevent inherited Python resolution from selecting another editable checkout.
    env.pop('PYTHONPATH', None)
    subprocess.run([
        'uv', 'run', '--project', str(checkout), 'python', str(Path(__file__).resolve()),
        '--worker', '--checkout', str(checkout), '--data-dir', str(data), '--output', str(args.output),
    ], cwd=checkout, env=env, check=True)


def worker(args):
    import django
    import orjson
    import twicc
    from django.conf import settings

    checkout, data = args.checkout.resolve(), args.data_dir.resolve()
    assert Path(twicc.__file__).resolve().is_relative_to(checkout), twicc.__file__
    assert os.environ['TWICC_DATA_DIR'] == str(data)
    assert (data / '.synthetic-activity-benchmark').is_file()
    db_path = Path(settings.DATABASES['default']['NAME']).resolve()
    assert db_path.is_relative_to(data), db_path
    assert not db_path.exists(), 'Refuse an existing database'
    assert not settings.DEBUG
    # These assertions precede django.setup(), schema setup, and all DB operations.
    django.setup()
    from django.core.management import call_command
    from django.db import connection

    call_command('migrate', verbosity=0, interactive=False)
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')
        assert cursor.fetchone()[0].lower() == 'wal'
    benchmark = Benchmark(data)
    result = asyncio.run(benchmark.run())
    result['safety'] = {'checkout': str(checkout), 'imported_twicc': twicc.__file__, 'database': str(db_path)}
    args.output.write_bytes(orjson.dumps(result, option=orjson.OPT_INDENT_2))


class Benchmark:
    """One shared workload adapter; optional journal detection supports the baseline."""

    def __init__(self, data):
        from twicc.providers import compute_executor, compute_base, activity_preparation
        from twicc.core.content_storage import extraction
        from twicc.core.models import DailyActivity, WeeklyActivity
        from twicc.providers.helpers import get_provider_helpers
        from twicc.providers.write_coordinator import WriterCoordinator
        import importlib.util

        self.data = data
        self.executor = compute_executor
        self.extraction = extraction
        self.compute = compute_base.BaseSessionCompute
        self.activity = activity_preparation
        self.journal = importlib.util.find_spec('twicc.providers.compute_repair_journal') is not None
        self.version = get_provider_helpers('claude_code').current_compute_version
        self.coordinator = WriterCoordinator()
        self.random = random.Random(SEED)
        self.metrics = defaultdict(lambda: defaultdict(float))
        self.workload = 'setup'
        self.phase = 'setup'
        self.oracle_errors = []
        self.foreground_ok = True
        self.repaired_buckets = defaultdict(set)
        self.foreground_phases = []
        self.live_metrics = {}
        self.keys = set()
        self.local = threading.local()
        self.transaction_holds = defaultdict(list)
        self.foreground_times = defaultdict(list)
        self.grant_holds = defaultdict(list)
        # Count real repair entrypoints, leaving their implementation unchanged.
        for model in (DailyActivity, WeeklyActivity):
            recalculate = model.recalculate

            def counted(cls, *args, _method=recalculate, **kwargs):
                self.metrics[self.workload][f'{self.phase}_bucket_repair_count'] += 1
                return _method(*args, **kwargs)

            model.recalculate = classmethod(counted)

    def sql(self, execute, sql, params, many, context):
        """Measure actual SQL on each worker; no mocked SQLite timing."""
        text = sql.upper()
        workload, phase = self.local.workload, self.local.phase
        metrics = self.metrics[workload]
        if text.startswith('SELECT') and 'CORE_SESSIONITEM' in text:
            if 'SUM(' in text or ('SELECT' in text and '"COST"' in text
                                 and '"ID" IN' not in text and '"ID" >' not in text
                                 and '"LINE_NUM" IN' not in text and '"LINE_NUM" >' not in text
                                 and 'LIMIT' not in text):
                metrics[f'{phase}_history_scan_count'] += 1
        probe = (text.startswith('SELECT') and 'CORE_COMPUTEAGGREGATEREPAIR' in text)
        journal_write = ('CORE_COMPUTEAGGREGATEREPAIR' in text and text.startswith(('INSERT', 'UPDATE')))
        if journal_write:
            metrics['journal_writes'] += 1
            metrics['journal_serialized_bytes'] += sum(
                len(str(value).encode()) for value in (params or ()) if isinstance(value, str) and value.startswith(('[', '{'))
            )
        if text.startswith('BEGIN'):
            self.local.transaction_started = perf_counter()
        started = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = perf_counter() - started
            if probe:
                metrics['presence_probe_count'] += 1
                metrics['presence_probe_ms'] += elapsed * 1000
            if text.startswith(('COMMIT', 'ROLLBACK')) and self.local.transaction_started is not None:
                self.transaction_holds[workload].append((phase, perf_counter() - self.local.transaction_started))
                self.local.transaction_started = None

    async def invoke(self, function, *args, application=True):
        from django.db import connection

        workload, phase = self.workload, self.phase

        def measured():
            self.local.workload, self.local.phase = workload, phase
            self.local.transaction_started = None
            assert Path(connection.settings_dict['NAME']).resolve().is_relative_to(self.data)
            # Django commits through the connection API, outside cursor
            # execute wrappers. Time the real backend commit and rollback.
            commit, rollback = connection._commit, connection._rollback

            def finish(method):
                try:
                    return method()
                finally:
                    if self.local.transaction_started is not None:
                        self.transaction_holds[workload].append((phase, perf_counter() - self.local.transaction_started))
                        self.local.transaction_started = None

            connection._commit = lambda: finish(commit)
            connection._rollback = lambda: finish(rollback)
            try:
                with connection.execute_wrapper(self.sql):
                    return function(*args)
            finally:
                connection._commit, connection._rollback = commit, rollback

        runner = self.executor.run_compute_application_sync if application else self.executor.run_compute_sync
        return await runner(measured)

    async def grant(self, function, *args, foreground=None, application=True):
        lease = self.coordinator.try_background_lease()
        assert lease is not None
        started = perf_counter()
        queued = None
        competitor = None
        order = []
        if foreground is not None:
            from twicc.providers.write_coordinator import WritePriority, WriteResources

            async def background_competitor():
                competing_lease = await self.coordinator.acquire(
                    WriteResources.ADMISSION | WriteResources.WRITER, WritePriority.BACKGROUND)
                order.append('background')
                competing_lease.release()

            competitor = asyncio.create_task(background_competitor())
            await asyncio.sleep(0)  # Register the older competing background request.
            demand = self.coordinator.foreground_demand()
            # Foreground pressure must block the NEXT grant while this grant runs.
            assert self.coordinator.try_background_lease() is None
            async def queued_foreground():
                await self.foreground(foreground, demand)
                order.append('foreground')

            queued = asyncio.create_task(queued_foreground())
            await asyncio.sleep(0)
        try:
            result = await self.invoke(function, *args, application=application)
        finally:
            self.grant_holds[self.workload].append((self.phase, perf_counter() - started))
            lease.release()
        if queued is not None:
            await queued
            await competitor
            assert order == ['foreground', 'background'], order
        return result

    async def foreground(self, session_id, demand=None):
        from twicc.providers.write_coordinator import WritePriority, WriteResources

        demand = demand or self.coordinator.foreground_demand()
        started = perf_counter()
        lease = await self.coordinator.acquire(WriteResources.ADMISSION | WriteResources.WRITER, WritePriority.FOREGROUND)
        previous = self.phase
        self.phase = 'foreground'
        try:
            assert self.coordinator.try_background_lease() is None
            await self.invoke(self.append, session_id, application=False)
        finally:
            self.phase = previous
            lease.release()
            demand.release()
        self.foreground_times[self.workload].append((perf_counter() - started) * 1000)

    def create(self, name, size, *, parent=None, days=30):
        from twicc.core.enums import ItemKind
        from twicc.core.models import Project, Session, SessionItem

        project, _ = Project.objects.get_or_create(id='synthetic-project')
        session = Session.objects.create(
            id=name, project=project, provider='claude_code', file_path=str(self.data / f'{name}.jsonl'),
            parent_session_id=parent, type='subagent' if parent else 'session', compute_version=0,
            storage_incarnation=str(uuid4()), last_line=size, created_at=STAMP,
        )
        SessionItem.objects.bulk_create([
            SessionItem(session=session, line_num=n + 1, content='{}', timestamp=STAMP + timedelta(days=n % days),
                        kind=ItemKind.USER_MESSAGE if n % 7 == 0 else None,
                        cost=Decimal('0.00000049') + Decimal(self.random.randrange(1, 99)) / Decimal(1000))
            for n in range(size)
        ], batch_size=1000)
        return name

    def message(self, session_id, *, remove_last=False, creation_move=False, update_cost=True):
        from twicc.core.models import Session, SessionItem

        session = Session.objects.get(pk=session_id)
        updates = [{'id': pk, 'cost': str(Decimal('0.00000049') + Decimal(n % 97 + 1) / Decimal(1000)),
                    'timestamp': None if remove_last and n == 0 else stamp}
                   for n, (pk, stamp) in enumerate(SessionItem.objects.filter(session=session)
                                                 .order_by('line_num').values_list('pk', 'timestamp'))]
        fields = {'compute_version': self.version}
        if creation_move:
            fields['created_at'] = (STAMP + timedelta(days=45)).isoformat()
        return {'session_id': session_id, 'history_facts': [], 'item_fields': ['cost', 'timestamp'] if update_cost else ['timestamp'],
                'item_updates': updates, 'session_fields': fields,
                'observed_last_offset': session.last_offset, 'observed_incarnation': session.storage_incarnation,
                'observed_epoch': session.history_epoch, 'observed_upper_line': session.last_line,
                '_repaired_activity_buckets': self.repaired_buckets[self.workload]}

    def append(self, session_id):
        from twicc.core.models import Session
        from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
        from twicc.providers.live_sync import LiveSyncLimits
        import orjson

        session = Session.objects.get(pk=session_id)
        path = Path(session.file_path)
        with path.open('ab') as stream:
            stream.write(orjson.dumps({'type': 'user', 'timestamp': '2026-09-01T12:00:00Z',
                                      'message': {'role': 'user', 'content': 'foreground'}}) + b'\n')
        ClaudeCodeSessionCompute().sync_session_slice(session.id, path, limits=LiveSyncLimits())

    async def publish(self, session_id, *, partial=None, remove_last=False, creation_move=False,
                      metadata_append=None, metadata_every=False, preparation_append=None, force_fallback=False,
                      final_append=None, update_cost=True):
        msg = await self.invoke(
            lambda: self.message(session_id, remove_last=remove_last, creation_move=creation_move, update_cost=update_cost))
        self.phase = 'begin'
        operation = await self.grant(self.extraction.begin_compute_application, msg)
        chunks = 0
        try:
            while operation.phase != 'final':
                self.phase = 'preparation'
                batch = await self.invoke(self.extraction.prepare_compute_batch, operation)
                self.phase = batch.phase
                append = metadata_append if batch.phase == 'metadata' and (chunks == 0 or metadata_every) else None
                if append is not None and self.workload == 'D':
                    self.foreground_phases.append('metadata')
                await self.grant(self.extraction.publish_compute_batch, operation, batch, frozenset(), foreground=append)
                if batch.phase == 'metadata' and batch.rows:
                    chunks += 1
                    self.metrics[self.workload]['metadata_chunks'] += 1
                    self.metrics[self.workload]['source_rows'] += len(batch.rows)
                if partial is not None and chunks == partial:
                    return
            self.phase = 'bucket_preparation'
            state = await self.invoke(self.activity.begin_activity_preparation, operation)
            if preparation_append is not None:
                # Hold real application-worker preparation at its boundary. The
                # foreground writer uses its own connection while preparation waits.
                entered, release = threading.Event(), threading.Event()

                def blocked_page():
                    entered.set()
                    assert release.wait(30)
                    return self.activity.prepare_activity_key_page(state)

                task = asyncio.create_task(self.invoke(blocked_page))
                assert await asyncio.to_thread(entered.wait, 10)
                try:
                    self.foreground_phases.append('bucket_preparation')
                    await self.foreground(preparation_append)
                finally:
                    release.set()
                await task
            while state.fallback_reason is None and not state.enumeration_complete:
                await self.invoke(self.activity.prepare_activity_key_page, state)
            self.keys.update(state.required_keys)
            if force_fallback:
                await self.invoke(state.disable, 'benchmark_forced_exact')
            while state.fallback_reason is None and state.repair_cursor < len(state.ordered_keys):
                self.phase = 'bucket'
                await self.grant(self.activity.publish_activity_key, state, state.ordered_keys[state.repair_cursor])
            self.metrics[self.workload]['fallbacks'] += int(state.fallback_reason is not None)
            self.phase = 'final'

            def finalize():
                result = self.compute._apply_session_complete_final(
                    self.extraction.final_compute_message(operation, frozenset()))
                assert result.outcome == 'applied', result

            if final_append is not None:
                self.foreground_phases.append('forced_exact_final')
            await self.grant(finalize, foreground=final_append)
        finally:
            self.phase = 'cleanup'
            await self.invoke(self.extraction.close_compute_application, operation)
            await self.invoke(self.executor.close_compute_application_connection)

    def reference(self):
        """Independent raw SQLite oracle, including fractional legacy values.

        SQL SUM order matches SQLite's exact-repair sums. Cache comparisons use
        DecimalField's persisted six-place representation, never input rounding.
        """
        from django.db import connection
        from twicc.core.enums import ItemKind
        from twicc.core.models import SessionType

        errors = []
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, parent_session_id, project_id, provider, type, hidden, created_at, '
                           'self_cost, subagents_cost, total_cost, user_message_count FROM core_session ORDER BY id')
            sessions = cursor.fetchall()
            cursor.execute('SELECT i.timestamp, s.project_id, s.provider FROM core_sessionitem i '
                           'JOIN core_session s ON s.id=i.session_id WHERE i.timestamp IS NOT NULL')
            keys = set(self.keys)
            for stamp, project, provider in cursor.fetchall():
                day = datetime.fromisoformat(str(stamp)).date()
                for scope in (None, project):
                    keys.add(('day', scope, provider, day))
                    keys.add(('week', scope, provider, day - timedelta(days=day.weekday())))
            for sid, parent, project, provider, kind, hidden, created, own, children, total, messages in sessions:
                cursor.execute('SELECT SUM(cost), SUM(kind=%s) FROM core_sessionitem WHERE session_id=%s',
                               [ItemKind.USER_MESSAGE, sid])
                expected_own, expected_messages = cursor.fetchone()
                cursor.execute('SELECT SUM(i.cost) FROM core_sessionitem i JOIN core_session s ON s.id=i.session_id '
                               'WHERE s.parent_session_id=%s', [sid])
                expected_children = cursor.fetchone()[0]
                expected_total = (expected_own or 0) + (expected_children or 0)
                expected = (expected_own, expected_children, expected_total or None, expected_messages or 0)
                actual = (own, children, total, messages)
                if not self.equal(actual, expected):
                    errors.append({'session': sid, 'actual': str(actual), 'expected': str(expected)})
                if created is not None:
                    day = datetime.fromisoformat(str(created)).date()
                    for scope in (None, project):
                        keys.add(('day', scope, provider, day))
                        keys.add(('week', scope, provider, day - timedelta(days=day.weekday())))
            normalized = set()
            for model, project, provider, day in keys:
                period = model if isinstance(model, str) else ('day' if model.__name__ == 'DailyActivity' else 'week')
                normalized.add((period, project, provider, day))
            actual_keys = set()
            for period, table in (('day', 'core_dailyactivity'), ('week', 'core_weeklyactivity')):
                cursor.execute(f'SELECT project_id, provider, date FROM {table}')
                actual_keys.update((period, project, provider, datetime.fromisoformat(str(day)).date())
                                   for project, provider, day in cursor.fetchall())
            for period, project, provider, day in normalized | actual_keys:
                start = datetime.combine(day, datetime.min.time())
                end = start + timedelta(days=1 if period == 'day' else 7)
                scope_sql = ' AND s.project_id=%s' if project is not None else ''
                args = [provider, start, end] + ([project] if project is not None else [])
                cursor.execute('SELECT SUM(i.cost), SUM(CASE WHEN i.kind=%s AND s.type=%s AND s.hidden=0 '
                               'THEN 1 ELSE 0 END) FROM core_sessionitem i JOIN core_session s ON s.id=i.session_id '
                               'WHERE s.provider=%s AND i.timestamp >= %s AND i.timestamp < %s' + scope_sql,
                               [ItemKind.USER_MESSAGE, SessionType.SESSION, *args])
                cost, count = cursor.fetchone()
                cursor.execute('SELECT COUNT(*) FROM core_session s WHERE s.provider=%s AND s.created_at >= %s '
                               'AND s.created_at < %s AND s.type=%s AND s.hidden=0 AND EXISTS '
                               '(SELECT 1 FROM core_sessionitem i WHERE i.session_id=s.id AND i.kind=%s)' + scope_sql,
                               [provider, start, end, SessionType.SESSION, ItemKind.USER_MESSAGE]
                               + ([project] if project is not None else []))
                sessions_count = cursor.fetchone()[0]
                table = 'core_dailyactivity' if period == 'day' else 'core_weeklyactivity'
                cursor.execute(f'SELECT cost, user_message_count, session_count FROM {table} '
                               'WHERE provider=%s AND date=%s AND project_id IS %s', [provider, day, project])
                rows = cursor.fetchall()
                expected = (cost or 0, count or 0, sessions_count)
                if expected == (0, 0, 0):
                    if rows:
                        errors.append({'empty_bucket': str((period, project, provider, day)), 'actual': str(rows)})
                elif len(rows) != 1 or not self.equal(rows[0], expected):
                    errors.append({'bucket': str((period, project, provider, day)),
                                   'actual': str(rows), 'expected': str(expected)})
        print(f'Oracle {self.workload}: {len(errors)} mismatches', flush=True)
        if errors:
            print(errors[:3], flush=True)
        self.oracle_errors.extend(errors)
        return not errors

    @staticmethod
    def equal(actual, expected):
        return all(a == b if a is None or b is None else
                   Decimal(str(a)).quantize(Decimal('.000001')) == Decimal(str(b)).quantize(Decimal('.000001'))
                   for a, b in zip(actual, expected, strict=True))

    def replacement(self, session_id):
        from twicc.core.enums import Provider
        from twicc.providers.codex.rollout_migration import (
            ReplaceCodexHistoryJob, _begin_replace_codex_history, _insert_replace_codex_history_chunk,
            _finish_replace_codex_history,
        )
        job = ReplaceCodexHistoryJob(Provider.CLAUDE_CODE, session_id, [(1, '{}')], 0, 1, 1.0, None)
        _begin_replace_codex_history(job)
        _insert_replace_codex_history_chunk(session_id, job.items)
        _finish_replace_codex_history(job)

    def deletion(self, session_id):
        from twicc.core.enums import Provider
        from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
        _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CLAUDE_CODE, [session_id]))

    def make_orphan(self, session_id):
        from twicc.core.models import Session, SessionItem
        from twicc.providers.live_aggregates import contribution_activity_keys, item_contributions, session_contribution
        session = Session.objects.get(pk=session_id)
        keys = contribution_activity_keys(item_contributions(SessionItem.objects.filter(session=session)),
                                          [session_contribution(session)])
        self.keys.update(keys)
        observation = None
        if self.journal:
            from twicc.providers.compute_repair_journal import load_compute_repair
            observation = load_compute_repair(session.id, session.storage_incarnation).observation
        session.delete()
        return observation, keys

    def repair_orphan(self, orphan):
        from django.db import transaction
        from twicc.core.enums import Provider
        observation, keys = orphan
        if self.journal:
            from twicc.providers.compute_repair import repair_compute_observation
            assert repair_compute_observation(observation) == 'repaired'
        else:
            with transaction.atomic():
                for model, scope, provider, day in keys:
                    model.recalculate(scope, day, Provider(provider))

    async def run(self):
        from twicc.core.models import Session, SessionItem
        from django.db import connection

        started = perf_counter()
        self.executor.start_compute_executor()
        self.executor.start_compute_application_executor()
        try:
            print('Workload A: 12,000-row root and four 3,000-row children', flush=True)
            self.workload = 'A'
            self.phase = 'fixture'
            root = await self.invoke(self.create, 'A-root', 12000)
            children = [await self.invoke(lambda n=n: self.create(f'A-child-{n}', 3000, parent=root)) for n in range(4)]
            begin = perf_counter()
            for sid in [*children, root]:
                await self.publish(sid, update_cost=sid == root)
            self.metrics['A']['elapsed_ms'] = (perf_counter() - begin) * 1000
            await self.invoke(self.reference)

            print('Workload B', flush=True)
            self.workload = 'B'
            self.phase = 'fixture'
            roots = [await self.invoke(lambda n=n: self.create(f'B-{n}', 2400, days=1)) for n in range(20)]
            begin = perf_counter()
            for sid in roots:
                await self.publish(sid)
            self.metrics['B']['elapsed_ms'] = (perf_counter() - begin) * 1000
            await self.invoke(self.reference)

            print('Workload C', flush=True)
            self.workload = 'C'
            self.phase = 'fixture'
            sid = await self.invoke(self.create, 'C-root', 12000)
            # Only the last row owns its old date. Initial exact publication
            # creates that bucket before removal and interruption.
            await self.invoke(lambda: SessionItem.objects.filter(session_id=sid).update(timestamp=STAMP))
            await self.invoke(lambda: SessionItem.objects.filter(session_id=sid, line_num=1)
                              .update(timestamp=STAMP + timedelta(days=29)))
            await self.publish(sid)
            self.metrics['C'].clear()
            self.grant_holds['C'].clear()
            self.transaction_holds['C'].clear()
            begin = perf_counter()
            await self.invoke(lambda: Session.objects.filter(pk=sid).update(compute_version=0))
            await self.publish(sid, partial=2, remove_last=True, creation_move=True)
            # Recreate the application worker; no private operation survives.
            # Current adoption uses only the persisted journal.
            await self.executor.stop_compute_application_executor()
            self.executor.start_compute_application_executor()
            await self.publish(sid, remove_last=True, creation_move=True)
            await self.invoke(self.reference)
            self.phase = 'replacement'
            await self.grant(self.replacement, sid, application=False)
            await self.publish(sid)
            await self.invoke(self.reference)
            self.phase = 'deletion'
            await self.grant(self.deletion, sid, application=False)
            await self.invoke(self.reference)
            self.metrics['C']['elapsed_ms'] = (perf_counter() - begin) * 1000
            self.metrics['C']['fixture_source_rows'] = 12000

            print('Workload D', flush=True)
            self.workload = 'D'
            self.phase = 'fixture'
            sid = await self.invoke(self.create, 'D-root', 12000)
            foreground_sid = await self.invoke(self.create, 'D-foreground', 1)
            await self.publish(foreground_sid)
            begin = perf_counter()
            await self.publish(sid, metadata_append=foreground_sid, preparation_append=foreground_sid,
                               force_fallback=True, final_append=foreground_sid)
            orphan_sid = await self.invoke(self.create, 'D-orphan', 2400)
            await self.publish(orphan_sid, partial=1)
            orphan = await self.invoke(self.make_orphan, orphan_sid)
            self.phase = 'recovery'
            self.foreground_phases.insert(2, 'orphan_recovery')
            await self.grant(self.repair_orphan, orphan, foreground=foreground_sid, application=False)
            await self.invoke(self.reference)
            self.metrics['D']['elapsed_ms'] = (perf_counter() - begin) * 1000

            print('Workload E', flush=True)
            self.workload = 'E'
            self.phase = 'fixture'
            root = await self.invoke(self.create, 'E-root', 12000)
            child = await self.invoke(lambda: self.create('E-child', 3000, parent=root))
            await self.publish(child)
            begin = perf_counter()
            # Each child append advances the root placement epoch. Respect
            # that existing fence; retries adopt durable source obligations.
            for _ in range(3):
                try:
                    await self.publish(root, metadata_append=child)
                except self.extraction.SupersededStorageResult:
                    self.metrics['E']['retries'] += 1
                else:
                    raise AssertionError('Child append must supersede the root placement observation')
            await self.publish(root)
            await self.invoke(self.reference)
            self.metrics['E']['elapsed_ms'] = (perf_counter() - begin) * 1000

            self.workload = 'live_append'
            self.phase = 'fixture'
            sid = await self.invoke(self.create, 'live-current', 2400)
            await self.publish(sid)
            for present in (False, True):
                if present and self.journal:
                    def journal():
                        from django.db import transaction
                        from twicc.providers.compute_repair_journal import merge_compute_repair
                        session = Session.objects.get(pk=sid)
                        with transaction.atomic():
                            merge_compute_repair(sid, session.storage_incarnation, source_changed=True)
                    await self.invoke(journal)
                self.metrics['live_append'].clear()
                self.phase = 'foreground'
                begin = perf_counter()
                await self.foreground(sid)
                self.live_metrics['with_journal' if present else 'without_journal'] = {
                    **self.metrics['live_append'], 'elapsed_ms': (perf_counter() - begin) * 1000,
                    'journal_present': present and self.journal,
                }
                await self.publish(sid)
            await self.invoke(self.reference)
            if self.journal:
                from twicc.core.models import ComputeAggregateRepair
                assert await self.invoke(lambda: ComputeAggregateRepair.objects.count()) == 0
            # Ordinary worker opens the same asserted disposable database.
            await self.invoke(lambda: connection.close(), application=False)
        finally:
            await self.executor.stop_compute_application_executor()
            await self.executor.stop_compute_executor()
        workloads = {}
        for name in 'ABCDE':
            metrics = dict(self.metrics[name])
            grants = self.grant_holds[name]
            holds = self.transaction_holds[name]
            metrics.update({
                'max_grant_hold_ms': max((duration * 1000 for _, duration in grants), default=0),
                'final_transaction_hold_ms': max((duration * 1000 for phase, duration in holds if phase == 'final'),
                                                 default=0),
                'max_recovery_transaction_hold_ms': max(
                    (duration * 1000 for phase, duration in holds if phase == 'recovery'), default=0),
                'foreground_completion_ms': self.foreground_times[name],
                'retries': int(metrics.get('retries', 0)),
            })
            workloads[name] = metrics
        workloads['D']['foreground_phases'] = self.foreground_phases
        # Fixture row count excludes repeated metadata after restart.
        workloads['A']['fixture_source_rows'] = 24000
        workloads['B']['fixture_source_rows'] = 48000
        return {
            'seed': SEED, 'debug': False, 'journal_available': self.journal,
            'total_ms': (perf_counter() - started) * 1000, 'workloads': workloads,
            'unique_keys': len(self.keys), 'live_append': self.live_metrics,
            'metadata_history_scan_count': sum(m['metadata_history_scan_count'] for m in self.metrics.values()),
            'metadata_bucket_repair_count': sum(m['metadata_bucket_repair_count'] for m in self.metrics.values()),
            'foreground_wins_next_background_grant': self.foreground_ok,
            'final_state_matches_independent_reference': not self.oracle_errors,
            'oracle_errors': self.oracle_errors,
            'limits': ['Foreground cannot preempt an admitted transaction.',
                       'Final fallback and orphan recovery retain atomic holds.',
                       'Shared buckets are scanned again for each completed root.',
                       'Stale-parent live-child repairs remain unchanged.',
                       'Application worker restarts after partial commit; process crash tests belong to focused suites.',
                       'Baseline has no journal; orphan repair uses the same exact bucket reference.'],
        }


if __name__ == '__main__':
    args = arguments()
    if args.worker:
        worker(args)
    else:
        launch(args)
