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
import subprocess
from time import perf_counter

SEED = 20261013
STAMP = datetime(2026, 9, 1, 12, tzinfo=UTC)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--reuse-report', type=Path, help='Reuse this benchmark-owned disposable metadata')
    parser.add_argument('--source-db', type=Path, help='Read-only source metadata; never migrated')
    return parser.parse_args()


def launch(args):
    for value in (args.checkout, args.data_dir, args.output):
        if not value.is_absolute():
            raise ValueError('All paths must be absolute')
    checkout, data = args.checkout.resolve(), args.data_dir.resolve()
    if data == checkout or data.is_relative_to(checkout):
        raise ValueError('Use a separate disposable data directory outside the checkout')
    if args.reuse_report:
        if not (data / '.synthetic-activity-benchmark').is_file():
            raise ValueError('Reuse requires a benchmark-owned disposable directory')
    else:
        if data.exists():
            raise ValueError('Disposable data directory must not already exist')
        data.mkdir(parents=True)
        (data / '.synthetic-activity-benchmark').write_text('Synthetic fixtures only\n')
    env = dict(os.environ, TWICC_DATA_DIR=str(data), TWICC_DEBUG='0', DJANGO_SETTINGS_MODULE='twicc.settings')
    # Prevent inherited Python resolution from selecting another editable checkout.
    env.pop('PYTHONPATH', None)
    source_args = ['--source-db', str(args.source_db.resolve())] if args.source_db else []
    if args.reuse_report:
        source_args.extend(['--reuse-report', str(args.reuse_report.resolve())])
    subprocess.run([
        'uv', 'run', '--project', str(checkout), 'python', str(Path(__file__).resolve()),
        '--worker', '--checkout', str(checkout), '--data-dir', str(data), '--output', str(args.output), *source_args,
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
    assert args.reuse_report or not db_path.exists(), 'Refuse an existing database'
    assert not settings.DEBUG
    # These assertions precede django.setup(), schema setup, and all DB operations.
    django.setup()
    from django.core.management import call_command
    from django.db import connection

    if not args.reuse_report:
        call_command('migrate', verbosity=0, interactive=False)
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')
        assert cursor.fetchone()[0].lower() == 'wal'
    benchmark = Benchmark(data, args.source_db, args.reuse_report)
    result = asyncio.run(benchmark.run())
    result['safety'] = {'checkout': str(checkout), 'imported_twicc': twicc.__file__, 'database': str(db_path)}
    args.output.write_bytes(orjson.dumps(result, option=orjson.OPT_INDENT_2))


class Benchmark:
    """Measure the production calculator against disposable persisted metadata."""

    def __init__(self, data, source, reuse_report=None):
        self.data = data
        self.source = source
        self.reuse_report = reuse_report
        self.phases = defaultdict(list)
        self.queries = defaultdict(int)
        self.holds = []
        self.worker_holds = []
        self.commits = []
        self.waits = []
        self.gaps = []
        self.historical_in_writer = []
        self.workloads = {}
        self.references = {}
        self.ids = {}

    def seed(self):
        import sqlite3
        from twicc.core.models import AgentLink, Project, Session, SessionItem
        from django.db import connection
        def aware(value):
            if not value:
                return None
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        project = Project.objects.create(id='benchmark')
        if self.source:
            # URI read-only plus query_only is independent of Django's writable
            # disposable connection. No content column is selected or copied.
            with sqlite3.connect(self.source.resolve().as_uri() + '?mode=ro', uri=True) as source:
                source.execute('PRAGMA query_only=ON')
                source.row_factory = sqlite3.Row
                sessions = {row['id']: dict(row) for row in source.execute(
                    'SELECT id, provider, parent_session_id, type, last_line, created_at, last_updated_at, '
                    'user_message_count, hidden FROM core_session')}
                groups = defaultdict(list)
                for sid, row in sessions.items():
                    root = sid
                    visited = set()
                    while sessions[root]['parent_session_id'] in sessions and root not in visited:
                        visited.add(root)
                        root = sessions[root]['parent_session_id']
                    groups[root].append(sid)
                sizes = {root: sum(sessions[s]['last_line'] for s in ids) for root, ids in groups.items()}
                selected = []
                for label, target in [('small', 499), ('medium', 9753), ('large', 59004), ('very_large', 458192)]:
                    root = min((sid for sid in groups if sid not in selected), key=lambda sid: abs(sizes[sid] - target))
                    selected.append(root)
                    ids = groups[root]
                    self.ids[label] = (root,)
                    self.workloads[label] = {'target_rows': target, 'source_root': root, 'members': len(ids)}
                    for sid in ids:
                        row = sessions[sid]
                        Session.objects.create(id=sid, project=project, provider=row['provider'], type=row['type'],
                            parent_session_id=row['parent_session_id'] if row['parent_session_id'] in ids else None,
                            created_at=aware(row['created_at']), last_updated_at=aware(row['last_updated_at']),
                            user_message_count=row['user_message_count'], hidden=row['hidden'], file_path=sid)
                    total, count = Decimal(0), 0
                    for sid in ids:
                        cursor = source.execute('SELECT line_num, timestamp, kind, cost FROM core_sessionitem WHERE session_id=?', (sid,))
                        while rows := cursor.fetchmany(2000):
                            SessionItem.objects.bulk_create([SessionItem(session_id=sid, line_num=row['line_num'],
                                timestamp=aware(row['timestamp']), kind=row['kind'], cost=row['cost']) for row in rows])
                            total += sum((Decimal(str(row['cost'])) for row in rows if row['cost'] is not None), Decimal(0))
                            count += len(rows)
                    placeholders = ','.join('?' for _ in ids)
                    links = source.execute(f'SELECT session_id, agent_id, tool_use_line_num, tool_use_id FROM core_agentlink WHERE agent_id IN ({placeholders})', ids)
                    for row in links:
                        if row['session_id'] in ids:
                            AgentLink.objects.create(**dict(row))
                    self.workloads[label]['source_rows'] = count
                    self.references[label] = total.quantize(Decimal('.000001'))
        else:
            for label, size in [('small', 499), ('medium', 9753), ('large', 59004), ('very_large', 458192)]:
                root = Session.objects.create(id=label, project=project, provider='claude_code', file_path=label,
                    created_at=STAMP, last_updated_at=STAMP, user_message_count=1)
                self.ids[label] = (root.id,)
                for start in range(0, size, 2000):
                    SessionItem.objects.bulk_create([SessionItem(session=root, line_num=n + 1, timestamp=STAMP,
                        cost=Decimal('.000001'), kind='assistant_message') for n in range(start, min(start + 2000, size))])
                self.workloads[label] = {'source_rows': size, 'members': 1}
                self.references[label] = Decimal(size) / 1_000_000
        root = Session.objects.create(id='cache-root', project=project, provider='claude_code', file_path='cache-root',
            created_at=STAMP, last_updated_at=STAMP, user_message_count=1)
        for n in range(400):
            sid = root.id if n == 0 else f'cache-child-{n}'
            if n:
                Session.objects.create(id=sid, project=project, provider=root.provider, type='subagent',
                    parent_session=root, file_path=sid, last_updated_at=STAMP)
                AgentLink.objects.create(session=root, agent_id=sid, tool_use_id=sid, tool_use_line_num=n)
            SessionItem.objects.create(session_id=sid, line_num=1, timestamp=STAMP, cost=Decimal('.000001'))
        self.ids['cache400'] = (root.id,)
        self.references['cache400'] = Decimal('.000400')
        self.workloads['cache400'] = {'source_rows': 400, 'members': 400}
        connection.close()

    def reuse(self):
        import orjson
        from twicc.core.models import Session, SessionItem
        from django.db import connection
        previous = orjson.loads(self.reuse_report.read_bytes())
        assert previous['safety']['database'] == str(self.data / 'db' / 'data.sqlite')
        self.source = previous['source']
        self.workloads = previous['workloads']
        for label, workload in self.workloads.items():
            root = workload.get('source_root', 'cache-root' if label == 'cache400' else label)
            self.ids[label] = (root,)
            members = {root}
            frontier = {root}
            while frontier:
                frontier = set(Session.objects.filter(parent_session_id__in=frontier).values_list('id', flat=True)) - members
                members.update(frontier)
            self.references[label] = sum((cost for cost in SessionItem.objects.filter(
                session_id__in=members, cost__isnull=False).values_list('cost', flat=True)), Decimal(0))
        Session.objects.update(items_cost_computed_through_at=None)
        connection.close()

    def check_activity(self):
        from twicc.core.models import DailyActivity, Session, SessionItem, WeeklyActivity
        sessions = {row['id']: row for row in Session.objects.values(
            'id', 'provider', 'type', 'hidden', 'created_at', 'user_message_count')}
        daily = defaultdict(lambda: [0, 0, Decimal(0)])
        for sid, stamp, kind, cost in SessionItem.objects.values_list('session_id', 'timestamp', 'kind', 'cost').iterator():
            if stamp is None:
                continue
            owner = sessions[sid]
            day = stamp.astimezone(UTC).date()
            eligible = kind == 'user_message' and owner['type'] == 'session' and not owner['hidden']
            if cost is not None or eligible:
                for project in (None, 'benchmark'):
                    values = daily[(project, owner['provider'], day)]
                    values[0] += int(eligible)
                    values[2] += cost or Decimal(0)
        for owner in sessions.values():
            if (owner['created_at'] and owner['type'] == 'session' and not owner['hidden']
                    and owner['user_message_count'] > 0):
                for project in (None, 'benchmark'):
                    daily[(project, owner['provider'], owner['created_at'].astimezone(UTC).date())][1] += 1
        weekly = defaultdict(lambda: [0, 0, Decimal(0)])
        for (project, provider, day), values in daily.items():
            target = weekly[(project, provider, day - timedelta(days=day.weekday()))]
            for index, value in enumerate(values):
                target[index] += value
        errors = []
        for model, expected in ((DailyActivity, daily), (WeeklyActivity, weekly)):
            actual = {(row.project_id, row.provider, row.date): [row.user_message_count, row.session_count, row.cost]
                for row in model.objects.all() if row.user_message_count or row.session_count or row.cost}
            normalized = {key: [values[0], values[1], values[2].quantize(Decimal('.000001'))]
                for key, values in expected.items() if any(values)}
            if actual != normalized:
                errors.append(model.__name__)
        return errors

    async def run(self):
        from django.db import connection
        from twicc.core.models import Session, StatisticsQueueEntry
        from twicc.providers import db_writer, statistics_runtime, statistics_publication
        from twicc.providers.statistics_queue import capture_queue
        from twicc.providers.statistics_prepare import prepare_tree_costs
        await asyncio.to_thread(self.reuse if self.reuse_report else self.seed)
        # The manual consumer owns batches. Disable the automatic consumer for
        # this benchmark only, retaining the production writer/coordinator.
        consumer_start = statistics_runtime.StatisticsConsumer.start
        statistics_runtime.StatisticsConsumer.start = lambda self: None
        ordinary = db_writer.run_compute_sync
        phase = ['idle']
        def measured(function, args, kwargs):
            start = perf_counter()
            def record(execute, sql, params, many, context):
                self.queries[phase[0]] += 1
                if 'core_sessionitem' in sql.lower():
                    self.historical_in_writer.append(sql)
                return execute(sql, params, many, context)
            commit = connection.commit
            def timed_commit():
                before = perf_counter()
                try:
                    return commit()
                finally:
                    self.commits.append((perf_counter() - before) * 1000)
            connection.commit = timed_commit
            try:
                with connection.execute_wrapper(record):
                    return function(*args, **kwargs)
            finally:
                connection.commit = commit
                self.worker_holds.append((perf_counter() - start) * 1000)
        async def writer(function, /, *args, **kwargs):
            return await ordinary(measured, function, args, kwargs)
        db_writer.run_compute_sync = writer
        drive = db_writer._drive_publication
        async def measured_drive(factory, priority, resources):
            start = perf_counter()
            try:
                return await drive(factory, priority, resources)
            finally:
                if priority == db_writer.WritePriority.BACKGROUND:
                    self.holds.append((perf_counter() - start) * 1000)
        db_writer._drive_publication = measured_drive
        publish = db_writer.publish_statistics_sync
        async def measured_publish(function, *args):
            start = perf_counter()
            try:
                return await publish(function, *args)
            finally:
                self.phases[phase[0] + ':publish:' + function.__name__].append((perf_counter() - start) * 1000)
        db_writer.publish_statistics_sync = measured_publish
        consumer = statistics_runtime.StatisticsConsumer()
        read = consumer._read
        async def measured_read(function, *args):
            start = perf_counter()
            def counted():
                def record(execute, sql, params, many, context):
                    self.queries[phase[0] + ':reader'] += 1
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(record):
                    return function(*args)
            try:
                result = await read(counted)
                if function.__name__ == 'prepare_tree_costs':
                    self.queries[phase[0] + ':own_updates'] = len(result.own_updates)
                return result
            finally:
                self.phases[phase[0] + ':' + function.__name__].append((perf_counter() - start) * 1000)
        consumer._read = measured_read
        notify = statistics_publication.notify_statistics_published
        async def measured_notify(*args):
            start = perf_counter()
            try:
                return await notify(*args)
            finally:
                self.phases[phase[0] + ':notification'].append((perf_counter() - start) * 1000)
        statistics_publication.notify_statistics_published = measured_notify
        done = False
        async def heartbeat():
            last = perf_counter()
            while not done:
                await asyncio.sleep(.001)
                now = perf_counter()
                self.gaps.append((now - last) * 1000)
                last = now
        async def foreground():
            while not done:
                await asyncio.sleep(.005)
                start = perf_counter()
                async def noop():
                    pass
                await db_writer.run_under_storage_write_lock(noop, priority=db_writer.WritePriority.FOREGROUND)
                self.waits.append((perf_counter() - start) * 1000)
        db_writer.start_db_writer()
        probes = [asyncio.create_task(heartbeat()), asyncio.create_task(foreground())]
        errors = []
        try:
            for label, roots in self.ids.items():
                for temperature in ('first', 'warm'):
                    phase[0] = f'{label}:{temperature}'
                    def enqueue(roots=roots):
                        StatisticsQueueEntry.objects.bulk_create([StatisticsQueueEntry(root_session_id=root) for root in roots])
                    await asyncio.to_thread(enqueue)
                    batch = await consumer._read(capture_queue)
                    start = perf_counter()
                    await consumer.run_batch(batch)
                    self.workloads[label][temperature + '_ms'] = (perf_counter() - start) * 1000
                    actual = await asyncio.to_thread(lambda roots=roots: Session.objects.get(pk=roots[0]).total_cost)
                    if actual != self.references[label]:
                        errors.append([label, str(actual), str(self.references[label])])
            phase[0] = 'cache400:one_changed'
            def advance_one():
                member = Session.objects.get(pk='cache-child-399')
                Session.objects.filter(pk=member.pk).update(last_updated_at=member.last_updated_at + timedelta(seconds=1))
            await asyncio.to_thread(advance_one)
            prepared = await consumer._read(prepare_tree_costs, self.ids['cache400'])
            self.workloads['cache400']['one_changed_own_reads'] = len(prepared.own_updates)
            await statistics_publication.publish_tree_costs(prepared)
            phase[0] = 'shared_roots'
            roots = tuple(root for ids in self.ids.values() for root in ids)
            await asyncio.to_thread(StatisticsQueueEntry.objects.bulk_create,
                [StatisticsQueueEntry(root_session_id=root) for root in roots])
            await consumer.run_batch(await consumer._read(capture_queue))
            remaining = await asyncio.to_thread(StatisticsQueueEntry.objects.count)
        finally:
            done = True
            await asyncio.gather(*probes)
            await consumer.close()
            await db_writer.stop_db_writer()
            db_writer.run_compute_sync = ordinary
            db_writer._drive_publication = drive
            db_writer.publish_statistics_sync = publish
            statistics_runtime.StatisticsConsumer.start = consumer_start
            statistics_publication.notify_statistics_published = notify
        errors.extend(await asyncio.to_thread(self.check_activity))
        def percentiles(values):
            ordered = sorted(values)
            return {name: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))] if ordered else 0
                for name, fraction in [('p50', .5), ('p90', .9), ('p95', .95), ('p99', .99), ('max', 1)]}
        return {'workloads': self.workloads, 'phases_ms': dict(self.phases), 'query_counts': dict(self.queries),
            'writer_hold_ms': percentiles(self.holds), 'writer_worker_ms': percentiles(self.worker_holds), 'commit_ms': percentiles(self.commits),
            'foreground_wait_ms': percentiles(self.waits), 'asyncio_gap_ms': percentiles(self.gaps),
            'historical_queries_inside_writer': self.historical_in_writer,
            'queue_rows_produced': len(self.ids) * 3, 'queue_rows_remaining': remaining,
            'final_state_matches_independent_reference': not errors, 'oracle_errors': errors,
            'source': str(self.source) if self.source else 'synthetic', 'debug': False}


if __name__ == '__main__':
    args = arguments()
    worker(args) if args.worker else launch(args)
