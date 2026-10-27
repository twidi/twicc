#!/usr/bin/env python3
"""Disk-backed content maintenance measurements on disposable databases only.

Synthetic databases use model schema creation, never migrations. An explicit source
is opened read-only and backed up once. Only the new destination receives writes.
Output contains counters and query plans, never content or session identities.
"""

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import time


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('conformant', 'compress', 'foreground', 'acquisition', 'selection'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--source-db', type=Path)
    return parser.parse_args()


def protected_directories():
    # Do not infer or import application settings to resolve the running database.
    # Refuse known data directories, project DB directories, and their aliases.
    checkout = Path(__file__).resolve().parents[1]
    forbidden = {(checkout / 'db').resolve(), (Path.home() / '.twicc').resolve()}
    inherited = os.environ.get('TWICC_DATA_DIR')
    if inherited:
        forbidden.add(Path(inherited).resolve())
    return forbidden


def validate_output(args):
    output = args.output.resolve()
    target = args.work_dir.resolve() / 'benchmark.sqlite'
    if output == target or (args.source_db and output == args.source_db.resolve()):
        raise ValueError('Output must differ from source and destination databases')
    if any(output == path or output.is_relative_to(path) for path in protected_directories()):
        raise ValueError('Output resolves inside an instance data directory')
    # Reject all existing files, including hard links and dangling symbolic links.
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Output already exists')
    return output


def destination(args):
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError('Work directory must be empty')
    forbidden = protected_directories()
    if any(work == path or work.is_relative_to(path) for path in forbidden):
        raise ValueError('Work directory resolves inside an instance data directory')
    work.mkdir(parents=True, exist_ok=True)
    target = work / 'benchmark.sqlite'
    if args.source_db:
        source = args.source_db.resolve(strict=True)
        if source == target:
            raise ValueError('Source and destination must differ')
        if shutil.disk_usage(work).free < source.stat().st_size * 1.25 + 1024**3:
            raise ValueError('Insufficient free disk space for one backup and bounded writes')
        from contextlib import closing

        with (closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader,
              closing(sqlite3.connect(target)) as writer):
            # One step holds a consistent snapshot. Stepped copies restart on busy sources.
            reader.backup(writer, pages=-1)
    return work, target


def configure(work, target, *, source=False):
    # Set the data directory before importing twicc. No instance settings are imported.
    os.environ['TWICC_DATA_DIR'] = str(work)
    import django
    from django.conf import settings

    settings.configure(INSTALLED_APPS=['twicc.core'], SECRET_KEY='disposable-benchmark', USE_TZ=True, TIME_ZONE='UTC',
                       DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(target),
                                             'OPTIONS': {'timeout': 30, 'init_command':
                                                 'PRAGMA synchronous=NORMAL; PRAGMA mmap_size=134217728; '
                                                 'PRAGMA journal_size_limit=27103364; PRAGMA cache_size=2000;'}}},
                       CLAUDE_CODE_COMPUTE_VERSION=999999, CODEX_COMPUTE_VERSION=999999,
                       SESSION_DIRS_CLEANUP_ENABLED=False)
    django.setup()
    from django.db import connection
    with connection.cursor() as cursor:
        # Candidate writes are bounded; normal checkpoints bound the source clone WAL.
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
    if 'core_session' not in connection.introspection.table_names():
        from django.apps import apps
        with connection.schema_editor() as editor:
            for model in apps.get_app_config('core').get_models():
                editor.create_model(model)


def io_sample():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    io = {}
    path = Path('/proc/self/io')
    if path.exists():
        io = {key: int(value) for line in path.read_text().splitlines() for key, value in [line.split(':')]}
    return dict(cpu_seconds=usage.ru_utime + usage.ru_stime, **io)


@contextmanager
def queries(counts, plans=None):
    from django.db import connection

    def record(execute, sql, params, many, context):
        if sql.startswith(('EXPLAIN ', 'SELECT rootpage FROM sqlite_master')):
            counts['profiling_sql'] += 1
            return execute(sql, params, many, context)
        counts['sql'] += 1
        if sql.lstrip().startswith('SELECT'):
            counts['selects'] += 1
            head = sql.split(' FROM ')[0]
            if 'core_sessionitemcontent' in sql and 'payload' in head and 'LENGTH(' not in head:
                counts['payload_selects'] += 1
            if 'FROM "core_session"' in sql:
                counts['session_selects'] += 1
        if (plans is not None and 'candidate_lookup' not in plans and sql.startswith('SELECT')
                and 'FROM "core_sessionitemcontent"' in sql):
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                plans['candidate_lookup'] = [row[3] for row in cursor.fetchall()]
        if plans is not None and sql.startswith('UPDATE "core_sessionitemcontent"'):
            # EXPLAIN includes SQLite FK work, unlike EXPLAIN QUERY PLAN.
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN ' + sql, params)
                instructions = cursor.fetchall()
            plans['publication_opcodes'] = sorted({row[1] for row in instructions})
            plans['publication_fk_counter'] = any(row[1] == 'FkCounter' for row in instructions)
            with connection.cursor() as cursor:
                cursor.execute("SELECT rootpage FROM sqlite_master WHERE name='core_sessionitem'")
                child_root = cursor.fetchone()[0]
            plans['publication_opens_child_table'] = any(
                row[1] in {'OpenRead', 'OpenWrite'} and row[3] == child_root for row in instructions)
        return execute(sql, params, many, context)
    with connection.execute_wrapper(record):
        yield


def seed(scenario):
    import orjson
    from django.db import transaction
    from django.utils import timezone
    from importlib.resources import files
    from twicc.core.content_storage.codec import encode_cold
    from twicc.core.models import ContentDictionary, Project, Session, SessionItem, SessionItemContent

    from twicc.core.content_storage.policy import compression_policy_version

    directory = files('twicc.core.content_storage').joinpath('dictionaries')
    manifest = orjson.loads(directory.joinpath('manifest.json').read_bytes())
    entry = next(row for row in manifest['dictionaries'] if row['provider'] == 'codex' and row['version'] == 1)
    payload = directory.joinpath(entry['file']).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == entry['sha256']
    assert len(payload) == entry['byte_length'] and 0 < len(payload) <= 16384
    dictionary = ContentDictionary.objects.create(provider='codex', version=1, format=entry['format'],
                                                  sha256=hashlib.sha256(payload).digest(), payload=payload)
    policy = compression_policy_version('codex', dictionary.id)
    project = Project.objects.create(id='benchmark')
    now = timezone.now()
    owners = [Session(id=f'owner-{i}', project=project, provider='codex', type='session',
                      storage_incarnation=f'incarnation-{i}', content_storage_version=1,
                      compute_version=999999, created_at=now - timedelta(days=60)) for i in range(8)]
    hot = Session(id='hot', project=project, provider='codex', storage_incarnation='hot-incarnation',
                  content_storage_version=1, compute_version=999999, created_at=now)
    owners.append(hot)
    owners.extend(Session(id=f'hot-child-{i}', project=project, provider='codex', type='subagent', parent_session=hot,
                          storage_incarnation=f'hot-child-incarnation-{i}', content_storage_version=1,
                          compute_version=999999, created_at=now) for i in range(100))
    for owner in owners:
        owner.file_path = '/public-benchmark/' + owner.id
    Session.objects.bulk_create(owners)
    total = 100000 if scenario == 'conformant' else (4 if scenario == 'acquisition' else 24)
    rows = []
    for index in range(total):
        size = 128 if scenario in {'conformant', 'acquisition'} else (1024, 65536, 1024**2, 16 * 1024**2)[index % 4]
        text = (f'public synthetic content {index} ' * (size // 20 + 1))[:size]
        if scenario == 'conformant' or (scenario == 'selection' and index % 3):
            text = text if index % 2 else f'x{index}'
            encoded, no_gain = encode_cold(text, dictionary.id, policy)
            payload, codec, dictionary_id = encoded.payload, encoded.codec, encoded.dictionary_id
            attempt = policy
        else:
            payload, codec, dictionary_id, attempt, no_gain = text.encode(), 'plain', None, None, False
        raw = text.encode()
        owner_ids = [owners[index % 8].id, owners[(index + 1) % 8].id]
        if scenario != 'conformant' and index % 10 == 0:
            owner_ids.append(hot.id)
        rows.append(SessionItemContent(sha256=hashlib.sha256(raw).digest(), payload=payload, codec=codec,
                                      dictionary_id=dictionary_id, raw_size=len(raw), dictionary_provider='codex',
                                      session_ids=sorted(owner_ids), compression_attempt_version=attempt,
                                      compression_no_gain=no_gain))
        if len(rows) == 100:
            SessionItemContent.objects.bulk_create(rows, batch_size=100)
            rows.clear()
    if rows:
        SessionItemContent.objects.bulk_create(rows, batch_size=100)
    # Recovery has a large conformant hot session and 100 conformant hot descendants.
    plain = SessionItemContent.objects.create(sha256=hashlib.sha256(b'hot reference').digest(), payload=b'hot reference',
                                             raw_size=13, codec='plain', dictionary_provider='codex',
                                             session_ids=sorted(owner.id for owner in owners[8:]))
    hot_items = 100000 if scenario in {'conformant', 'acquisition'} else 100
    with transaction.atomic():
        for offset in range(0, hot_items, 1000):
            SessionItem.objects.bulk_create([SessionItem(session=hot, line_num=i, content='', current_content=plain,
                                                        original_content=plain) for i in range(offset, min(offset + 1000, hot_items))])
        SessionItem.objects.bulk_create([SessionItem(session=owner, line_num=1, content='', current_content=plain,
                                                    original_content=plain) for owner in owners[9:]])
    return {'contents': total + 1, 'hot_session_items': hot_items, 'hot_descendants': 100,
            'public_dictionary_sha256': bytes(dictionary.sha256).hex(), 'orjson_version': orjson.__version__}


def inspect_source_metadata(cache):
    """Inspect the whole copy's eligibility without preparing its payload backlog."""
    from django.db.models import BinaryField
    from django.db.models.functions import Cast, Length
    from django.utils import timezone
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.dictionaries import get_provider_dictionary
    from twicc.core.content_storage.policy import compression_policy_version
    from twicc.core.models import SessionItemContent

    counts, sql, selected_ids = Counter(), Counter(), []
    before, started = io_sample(), time.perf_counter()
    cursor = 0
    with queries(sql):
        while True:
            rows = list(SessionItemContent.objects.filter(pk__gt=cursor).order_by('id').annotate(
                stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
            ).values(*(field for field in maintenance.COMPRESSION_METADATA_FIELDS if field != 'session_ids'),
                     'stored_size', 'owner_size')[:100])
            if not rows:
                break
            pending = []
            for row in rows:
                counts['scanned'] += 1
                counts['raw_bytes'] += row['raw_size']
                counts['stored_bytes'] += row['stored_size']
                dictionary = get_provider_dictionary(row['dictionary_provider'])
                if dictionary is None:
                    counts['missing_dictionary'] += 1
                    continue
                policy = compression_policy_version(row['dictionary_provider'], dictionary.row.id)
                if row['codec'] == 'zstd' and row['dictionary_id'] == dictionary.row.id:
                    counts['already_compressed'] += 1
                elif row['compression_no_gain'] and row['compression_attempt_version'] == policy:
                    counts['no_gain_skips'] += 1
                else:
                    pending.append(row['id'])
            owners = dict(SessionItemContent.objects.filter(pk__in=pending).values_list('id', 'session_ids'))
            decisions = cache.decisions({owner for ids in owners.values() for owner in ids},
                                        timezone.now(), frozenset())
            for content_id, owner_ids in owners.items():
                if maintenance._candidate_owners_cold(owner_ids, decisions):
                    counts['eligible'] += 1
                    if len(selected_ids) < 20:
                        selected_ids.append(content_id)
                else:
                    counts['ineligible_owners'] += 1
            cursor = rows[-1]['id']
    after = io_sample()
    return {'elapsed_ms': (time.perf_counter() - started) * 1000, 'counts': dict(counts), 'sql': dict(sql),
            'cache': dict(cache.statistics),
            'process_interval_delta': {key: after[key] - before[key] for key in before},
            'selected_ids': selected_ids}


async def run_pass(scenario, source=False):
    from functools import partial
    from concurrent.futures import ThreadPoolExecutor
    from channels.db import database_sync_to_async
    from django.db import connections, transaction
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
    from twicc.core.content_storage.maintenance_metrics import MaintenanceMetrics
    from twicc.core.models import Project
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync, start_compute_executor, stop_compute_executor
    from twicc.providers.write_coordinator import WriterCoordinator, WriteResources

    # run_server imports ASGI, which imports the registry before task startup.
    bootstrap_started = time.perf_counter()
    from twicc.agent.registry import get_agent_manager_registry
    get_agent_manager_registry()
    bootstrap_ms = (time.perf_counter() - bootstrap_started) * 1000
    coordinator = WriterCoordinator()
    db_writer._db_writer_stop_event = asyncio.Event()
    db_writer._write_coordinator = coordinator
    db_writer._db_write_lock = coordinator.foreground_lock(WriteResources.WRITER)
    db_writer._storage_admission_lock = coordinator.foreground_lock(WriteResources.ADMISSION)
    cache, metrics = MaintenanceSessionCache(), MaintenanceMetrics('benchmark', 'periodic_compression')
    sql, plans, latencies = Counter(), {}, []
    unsubscribe = coordinator.observe_foreground_wait(metrics.record_foreground_wait)
    start_compute_executor()
    codec_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='benchmark-content-codec')
    before, started = io_sample(), time.perf_counter()
    recovery_sql, recovery_time = Counter(), 0
    finished = asyncio.Event()
    foreground_sequence = 0

    def recover():
        start = time.perf_counter()
        targets, cursor = 0, None
        with queries(recovery_sql):
            while ids := maintenance.pending_session_page(cursor):
                targets += len(maintenance.recovery_promotion_targets(ids, cache))
                cursor = ids[-1]
        return targets, (time.perf_counter() - start) * 1000

    def prepare(cursor):
        with queries(sql):
            return maintenance.prepare_compression_batch(cursor, row_budget=1 if source else 100, session_cache=cache)

    def publish(entries, active):
        with queries(sql, plans):
            return maintenance._publish(entries, active, session_cache=cache)

    def write():
        nonlocal foreground_sequence
        foreground_sequence += 1
        with transaction.atomic():
            Project.objects.filter(pk='benchmark').update(name=f'foreground-{foreground_sequence}')

    async def foreground():
        while not finished.is_set():
            requested = time.perf_counter()
            await db_writer.run_under_db_write_lock(lambda: run_compute_sync(write))
            latencies.append((time.perf_counter() - requested) * 1000)
            await asyncio.sleep(.002)

    foreground_task = asyncio.create_task(foreground()) if scenario == 'foreground' else None
    source_metadata = None
    try:
        if source:
            source_metadata = await database_sync_to_async(
                inspect_source_metadata, thread_sensitive=False, executor=codec_executor,
            )(cache)
            source_ids = iter(source_metadata.pop('selected_ids'))
            targets = None
        else:
            targets, recovery_time = await database_sync_to_async(
                recover, thread_sensitive=False, executor=codec_executor,
            )()
        cursor, done = 0, False
        while not done:
            if source:
                content_id = next(source_ids, None)
                if content_id is None:
                    break
                cursor = content_id - 1
            start = time.perf_counter()
            page = await database_sync_to_async(
                prepare, thread_sensitive=False, executor=codec_executor,
            )(cursor)
            metrics.record_preparation((time.perf_counter() - start) * 1000)
            metrics.record_page(page)
            cursor, done = page.cursor, page.done if not source else False
            entries = page.entries
            if entries:
                requested = time.perf_counter()
                while (lease := coordinator.try_background_lease()) is None:
                    await asyncio.sleep(0)
                metrics.counts['resource_wait_ms'] += (time.perf_counter() - requested) * 1000
                await maintenance._apply(partial(publish), entries, lease=lease, metrics=metrics)
            await asyncio.sleep(0)
    finally:
        finished.set()
        if foreground_task:
            await foreground_task
        await stop_compute_executor()
        await database_sync_to_async(connections.close_all, thread_sensitive=False, executor=codec_executor)()
        codec_executor.shutdown(wait=True)
        unsubscribe()
        cache_statistics = dict(cache.statistics)
        cache.close()
    after = io_sample()
    latencies.sort()
    return {'application_registry_initialization_ms': bootstrap_ms,
                'elapsed_ms': (time.perf_counter() - started) * 1000,
                'process_interval_delta': {key: after[key] - before[key] for key in before},
                'counts': dict(metrics.counts), 'sql': dict(sql), 'cache': cache_statistics,
                'writer_hold_percentile_upper_ms': {'p50': metrics._percentile(.5),
                                                    'p95': metrics._percentile(.95),
                                                    'p99': metrics._percentile(.99)},
                'full_metadata_inspection': source_metadata,
                'recovery': ({'not_run': True, 'reason': 'Source recovery remains exploratory; synthetic trials measure it'}
                             if source else {'elapsed_ms': recovery_time, 'sql': dict(recovery_sql), 'targets': targets,
                                             'writer_grants_structural_assertion':
                                                 'Discovery invokes no writer acquisition API',
                                             'payload_reads': recovery_sql['payload_selects']}),
                'foreground': {'count': len(latencies), 'request_to_commit_p50_ms': percentile(latencies, .5),
                                'request_to_commit_p95_ms': percentile(latencies, .95),
                                'request_to_commit_p99_ms': percentile(latencies, .99),
                                'request_to_commit_max_ms': max(latencies, default=0)}, 'plans': plans,
                'sqlite_journal_mode': 'wal'}


def percentile(values, fraction):
    return values[min(len(values) - 1, int(len(values) * fraction))] if values else 0


def restore_candidates():
    from django.db import transaction
    from twicc.core.content_storage.codec import decode_content
    from twicc.core.models import SessionItemContent

    # Fixture reset is outside the measured interval. Never used on source data.
    with transaction.atomic():
        for row in SessionItemContent.objects.filter(codec='zstd').iterator(chunk_size=1):
            row.payload = decode_content(row).encode()
            row.codec, row.dictionary_id = 'plain', None
            row.compression_attempt_version, row.compression_no_gain = None, False
            row.save(update_fields=['payload', 'codec', 'dictionary_id',
                                    'compression_attempt_version', 'compression_no_gain'])


def acquisition(first):
    from django.db import connection, transaction
    from twicc.core.models import SessionItem, SessionItemContent

    # Both FK columns retain their production db_index=False model declaration.
    assert not SessionItem._meta.get_field('current_content').db_index
    assert not SessionItem._meta.get_field('original_content').db_index
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA index_list(core_sessionitem)')
        indexes = [row[1] for row in cursor.fetchall()]
        columns = []
        for index in indexes:
            cursor.execute('PRAGMA index_info("' + index.replace('"', '""') + '")')
            columns.extend(row[2] for row in cursor.fetchall())
    assert 'current_content_id' not in columns and 'original_content_id' not in columns
    first_samples = [dict(cache_state='first measured pass; fixture pages may be warm', **first)]
    for _ in range(2):
        restore_candidates()
        first_samples.append(dict(cache_state='warm repeat on the same database',
                                  **asyncio.run(run_pass('acquisition'))))
    row = SessionItemContent.objects.get(raw_size=13)
    with transaction.atomic():
        for offset in range(100000, 1000000, 1000):
            SessionItem.objects.bulk_create([SessionItem(session_id='hot', line_num=i, content='',
                                                        current_content=row, original_content=row)
                                            for i in range(offset, offset + 1000)])
    second_samples = []
    for iteration in range(3):
        restore_candidates()
        result = asyncio.run(run_pass('acquisition'))
        result['cache_state'] = ('first measured pass at this child count' if iteration == 0
                                 else 'warm repeat on the same database')
        second_samples.append(result)
    for result in first_samples + second_samples:
        # dictionary_id checks its parent dictionary; FkCounter is valid there.
        assert not result['plans']['publication_opens_child_table']
    return {'children_100000': first_samples, 'children_1000000': second_samples, 'unindexed_content_fks': True}


def selection_index(enabled):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('DROP INDEX IF EXISTS idx_content_plain_id')
        if enabled:
            cursor.execute("CREATE INDEX idx_content_plain_id ON core_sessionitemcontent(id) WHERE codec='plain'")


def measure_selection(function):
    """Fresh Django/SQLite connection and process IO delta; OS cache stays uncontrolled."""
    from django.db import connection
    connection.close()
    counts, plans = Counter(), {}
    before, started = io_sample(), time.perf_counter()
    with queries(counts, plans):
        population = function()
    after = io_sample()
    return dict(population, elapsed_ms=(time.perf_counter() - started) * 1000, sql=dict(counts), plans=plans,
                process_interval_delta={key: after[key] - before[key] for key in before},
                cache_condition='fresh application/SQLite caches; OS page cache uncontrolled')


def run_selection_benchmark() -> dict:
    from django.db import connection
    from django.db.models import BinaryField
    from django.db.models.functions import Cast, Length
    from django.utils import timezone
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
    from twicc.core.models import SessionItemContent

    def roots(periodic):
        cache, after, selected, targets = MaintenanceSessionCache(), None, 0, 0
        now = timezone.now()
        pages, report_at = 0, time.monotonic() + 5
        try:
            while page := (maintenance.periodic_hot_root_page(after, now) if periodic
                           else maintenance.pending_session_page(after)):
                selected += len(page)
                targets += len(maintenance.recovery_promotion_targets(page, cache))
                after = page[-1]
                pages += 1
                if time.monotonic() >= report_at:
                    print(f'selection roots: periodic={periodic} pages={pages} sessions={selected} targets={targets}',
                          flush=True)
                    report_at = time.monotonic() + 5
            return {'pages': pages, 'selected': selected, 'restoration_targets': targets, 'cache': dict(cache.statistics)}
        finally:
            cache.close()

    def content(filtered):
        after, scanned, raw, stored, owners = 0, 0, 0, 0, 0
        pages, report_at = 0, time.monotonic() + 5
        while True:
            if filtered:
                query = maintenance._compression_metadata_query(after, maintenance.ROW_BUDGET)
            else:
                query = SessionItemContent.objects.filter(pk__gt=after).order_by('id').annotate(
                    stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
                ).values(*(field for field in maintenance.COMPRESSION_METADATA_FIELDS if field != 'session_ids'),
                         'stored_size', 'owner_size')[:maintenance.ROW_BUDGET]
            rows = list(query)
            if not rows:
                break
            scanned += len(rows)
            raw += sum(row['raw_size'] for row in rows)
            stored += sum(row['stored_size'] for row in rows)
            owners += sum(row['owner_size'] for row in rows)
            after = rows[-1]['id']
            pages += 1
            if time.monotonic() >= report_at:
                print(f'selection content: filtered={filtered} pages={pages} rows={scanned} stored_bytes={stored}',
                      flush=True)
                report_at = time.monotonic() + 5
        return {'pages': pages, 'scanned': scanned, 'represented_raw_bytes': raw, 'represented_stored_bytes': stored,
                    'owner_metadata_bytes': owners, 'payload_bytes_loaded': 0, 'row_budget': maintenance.ROW_BUDGET}

    print('selection: all-depth discovery', flush=True)
    root = {'all_depth_recovery': measure_selection(lambda: roots(False)),
            'periodic_hot_roots': measure_selection(lambda: roots(True))}
    print('selection: all-content metadata baseline', flush=True)
    selection_index(False)
    content_results = {'all_content': measure_selection(lambda: content(False))}
    print('selection: plaintext metadata without index', flush=True)
    content_results['filtered_without_index'] = measure_selection(lambda: content(True))
    print('selection: populated-copy partial-index build', flush=True)
    started = time.perf_counter()
    selection_index(True)
    build = {'elapsed_ms': (time.perf_counter() - started) * 1000}
    with connection.cursor() as cursor:
        cursor.execute("SELECT sum(pgsize) FROM dbstat WHERE name='idx_content_plain_id'")
        build['bytes'] = cursor.fetchone()[0] or 0
    print('selection: production plaintext metadata with index', flush=True)
    content_results['filtered_with_index'] = measure_selection(lambda: content(True))
    print('selection: isolated synthetic dispatcher and index-write trials', flush=True)
    import subprocess
    import sys
    import orjson
    # A fresh process keeps application globals, dictionaries and executor state independent of the source clone.
    work = Path(connection.settings_dict['NAME']).parent / 'synthetic'
    code = ("import importlib.util,pathlib,orjson; "
            f"s=importlib.util.spec_from_file_location('bench',{str(Path(__file__).resolve())!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            f"w=pathlib.Path({str(work)!r}); w.mkdir(); m.configure(w,w/'benchmark.sqlite'); "
            "print(orjson.dumps(m.synthetic_selection_trials()).decode())")
    completed = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    synthetic = orjson.loads(completed.stdout)
    return dict(root_selection=root, content_selection=content_results, index_build=build, **synthetic)


async def dispatcher_trial(*, failures=False):
    """Run the production dispatcher; patch only clock, stop boundary and fault injection."""
    from unittest.mock import patch
    from channels.db import database_sync_to_async
    from django.db import connections, transaction
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.content_storage.maintenance_metrics import MaintenanceMetrics
    from twicc.core.models import Project, SessionItem, SessionItemContent
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor, run_compute_sync
    from twicc.providers.compute_work import ComputeWorkState
    from twicc.providers.write_coordinator import WriterCoordinator, WriteResources

    get_agent_manager_registry()
    from types import SimpleNamespace
    db_writer._writer_signals = SimpleNamespace(sources=asyncio.Event())
    coordinator = WriterCoordinator()
    db_writer._db_writer_stop_event = asyncio.Event()
    db_writer._write_coordinator = coordinator
    db_writer._db_write_lock = coordinator.foreground_lock(WriteResources.WRITER)
    db_writer._storage_admission_lock = coordinator.foreground_lock(WriteResources.ADMISSION)
    stop, clock, emissions, latencies, attempts = asyncio.Event(), [0.0], [], [], Counter()
    pass_count = 0
    # The clock is synthetic. Keep enough time for several real backoff steps,
    # without replaying a permanent failure ~1,400 times before the next pass.
    periodic_interval = 32
    current_stage = ['startup_recovery']
    foreground_stages, stage_sql = {}, {}
    original_snapshot = maintenance._snapshot

    class ObservedMetrics(MaintenanceMetrics):
        def emit(self, event):
            nonlocal pass_count
            if self.operation_kind == 'periodic_compression':
                current_stage[0] = self.stage
            emissions.append({'kind': self.operation_kind, 'event': event, 'stage': self.stage,
                                  'counts': dict(self.counts), 'cache': dict(self.cache_statistics),
                                  'parent_operation_id': self.parent_operation_id,
                                  'elapsed_ms': (time.monotonic() - self.started) * 1000,
                                  'hold_p95_upper_ms': self._percentile(.95)})
            if event == 'complete' and self.operation_kind == 'periodic_compression':
                pass_count += 1
                if pass_count >= (2 if failures else 1):
                    stop.set()

    def snapshot(session_id, *args, **kwargs):
        if failures and session_id in {'hot', 'fault-permanent'}:
            attempts[session_id] += 1
            if session_id == 'fault-permanent' or attempts[session_id] == 1:
                raise RuntimeError('injected benchmark restoration failure')
        return original_snapshot(session_id, *args, **kwargs)

    async def wait(stop_event, wake, timeout):
        clock[0] += max(timeout or 0, 1)
        await asyncio.sleep(.001)

    def foreground_write():
        with transaction.atomic():
            Project.objects.filter(pk='benchmark').update(name='foreground')

    async def foreground():
        while not stop.is_set():
            started = time.perf_counter()
            requested_stage = current_stage[0]
            await db_writer.run_under_db_write_lock(lambda: run_compute_sync(foreground_write))
            latency = (time.perf_counter() - started) * 1000
            latencies.append(latency)
            foreground_stages.setdefault(requested_stage, []).append(latency)
            await asyncio.sleep(.002)

    start_compute_executor()
    started = time.perf_counter()
    sql = Counter()
    from django.db.backends.signals import connection_created

    def recorder(execute, statement, params, many, context):
        sql['sql'] += 1
        stage_sql.setdefault(current_stage[0], Counter())['sql'] += 1
        if statement.startswith('SELECT'):
            sql['selects'] += 1
            if 'core_sessionitemcontent' in statement and 'payload' in statement.split(' FROM ')[0]:
                if 'LENGTH(' not in statement.split(' FROM ')[0]:
                    sql['payload_selects'] += 1
        return execute(statement, params, many, context)

    observed_connections = []

    def attach(sender, connection, **kwargs):
        if recorder not in connection.execute_wrappers:
            connection.execute_wrappers.append(recorder)
            observed_connections.append(connection)

    connection_created.connect(attach, weak=False)
    foreground_task = asyncio.create_task(foreground())
    try:
        with (patch.object(maintenance, 'INITIAL_DELAY', 0), patch.object(maintenance, 'INTERVAL', periodic_interval),
              patch.object(maintenance, '_maintenance_time', lambda: clock[0]),
              patch.object(maintenance, '_wait', wait), patch.object(maintenance, '_snapshot', snapshot),
              patch.object(maintenance, 'MaintenanceMetrics', ObservedMetrics),
              patch.object(maintenance.logger, 'disabled', True)):
            await asyncio.wait_for(maintenance.start_content_maintenance_task(
                stop, compute_work=ComputeWorkState()), timeout=30)
    finally:
        stop.set()
        await foreground_task
        await stop_compute_executor()
        await database_sync_to_async(connections.close_all, thread_sensitive=False)()
        connection_created.disconnect(attach)
        for observed in observed_connections:
            if recorder in observed.execute_wrappers:
                observed.execute_wrappers.remove(recorder)
    summaries = [event for event in emissions if event['kind'] == 'periodic_compression' and event['event'] == 'complete']
    counts = Counter()
    for event in summaries:
        counts.update(event['counts'])
    latencies.sort()
    result = dict(counts, elapsed_ms=(time.perf_counter() - started) * 1000, sql=dict(sql), events=emissions,
                  synthetic_periodic_interval_seconds=periodic_interval,
                  foreground={'count': len(latencies), 'p50_ms': percentile(latencies, .5),
                                  'p95_ms': percentile(latencies, .95), 'max_ms': max(latencies, default=0)})
    result['sql_by_stage'] = {stage: dict(value) for stage, value in stage_sql.items()}
    result['foreground_by_request_stage'] = {
        stage: {'count': len(values), 'p50_ms': percentile(sorted(values), .5),
                'p95_ms': percentile(sorted(values), .95), 'max_ms': max(values)}
        for stage, values in foreground_stages.items()
    }
    if failures:
        def remaining():
            return {
                'unaffected_compressed': SessionItemContent.objects.filter(
                    sha256=hashlib.sha256(b'unaffected new cold ' * 500).digest(), codec='zstd').exists(),
                'transient_restored': not SessionItem.objects.filter(
                    session_id='hot', current_content__codec='zstd').exists(),
            }
        result.update(transient_attempts=attempts['hot'], permanent_attempts=attempts['fault-permanent'],
                      **await database_sync_to_async(remaining)())
    return result


def synthetic_selection_trials():
    from django.db import connection, transaction
    from django.utils import timezone
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.ingestion import insert_ingested_items, prepare_ingestion_contents
    from twicc.core.content_storage.policy import compression_policy_version
    from twicc.core.models import ContentDictionary, Session, SessionItem, SessionItemContent

    started = time.perf_counter()
    fixture = seed('compress')
    # Mostly post-compute contents have real shared references and current policy metadata.
    from twicc.core.content_storage.codec import encode_cold
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    policy = compression_policy_version('codex', dictionary.id)
    for offset in range(0, 10000, 500):
        rows = []
        for index in range(offset, offset + 500):
            text = (f'public conformant benchmark {index} ' * 50)[:1024]
            encoded, no_gain = encode_cold(text, dictionary.id, policy)
            assert encoded.codec == 'zstd' and not no_gain
            rows.append(SessionItemContent(
                sha256=encoded.sha256, payload=encoded.payload, raw_size=encoded.raw_size, codec=encoded.codec,
                dictionary_id=encoded.dictionary_id, dictionary_provider='codex',
                session_ids=['owner-0', 'owner-1'], compression_attempt_version=policy,
                compression_no_gain=False,
            ))
        with transaction.atomic():
            SessionItemContent.objects.bulk_create(rows, batch_size=500)
            SessionItem.objects.bulk_create([
                SessionItem(session_id=owner, line_num=30000 + offset + index, content='',
                            current_content=row, original_content=row)
                for index, row in enumerate(rows) for owner in ('owner-0', 'owner-1')
            ], batch_size=500)
    fixture['contents'] += 10000
    fixture['preconformant_contents'] = 10000
    fixture['shared_cold_references'] = 20000
    setup_ms = (time.perf_counter() - started) * 1000
    complete = {'compressible': asyncio.run(dispatcher_trial()), 'conformant': asyncio.run(dispatcher_trial())}
    # Make one shared compressed row reference two hot roots. Recovery sees real compressed references.
    hot = Session.objects.get(pk='hot')
    permanent = Session.objects.create(id='fault-permanent', project=hot.project, provider='codex',
                                      created_at=timezone.now(), storage_incarnation='fault-permanent',
                                      content_storage_version=1, compute_version=999999, file_path='/public/fault')
    row = SessionItemContent.objects.filter(codec='zstd').first()
    row.session_ids = sorted(set(row.session_ids + [hot.id, permanent.id]))
    row.save(update_fields=['session_ids'])
    SessionItem.objects.create(session=permanent, line_num=1, current_content=row, original_content=row)
    SessionItem.objects.create(session=hot, line_num=10001, current_content=row, original_content=row)
    raw = b'unaffected new cold ' * 500
    SessionItemContent.objects.create(sha256=hashlib.sha256(raw).digest(), payload=raw, raw_size=len(raw),
                                      codec='plain', dictionary_provider='codex', session_ids=['owner-0', 'owner-1'])
    complete['restoration_failures'] = asyncio.run(dispatcher_trial(failures=True))
    writes = {}
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    policy = compression_policy_version('codex', dictionary.id)
    sizes = [128, 65536, 1024**2, 16 * 1024**2]
    for indexed in (False, True):
        selection_index(indexed)
        samples = []
        for repeat in range(3):
            for size in sizes:
                owner = Session.objects.get(pk='owner-0')
                text = (f'index-{repeat}-{size} ' * (size // 8 + 1))[:size]
                start = time.perf_counter()
                prepared = prepare_ingestion_contents(owner, [text])
                codec_ms = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                with transaction.atomic():
                    insert_ingested_items(owner, [(SessionItem(session=owner, line_num=20000 + repeat * 4 + sizes.index(size),
                                                              content=text), text)], prepared=prepared)
                insert_ms = (time.perf_counter() - start) * 1000
                row = SessionItemContent.objects.get(sha256=hashlib.sha256(text.encode()).digest())
                # A hot owner authorizes zstd -> plain. A cold owner authorizes plain -> zstd.
                transitions = []
                for mode in ('plain', policy):
                    row.session_ids = ['hot'] if mode == 'plain' else ['owner-0']
                    row.save(update_fields=['session_ids'])
                    start = time.perf_counter()
                    candidate = maintenance.prepare_representation(row.id, mode)
                    prepare_ms = (time.perf_counter() - start) * 1000
                    start = time.perf_counter()
                    with transaction.atomic():
                        changed = maintenance.publish_representation(candidate, frozenset())
                    transitions.append({'mode': 'restore' if mode == 'plain' else 'compress', 'changed': changed,
                                            'codec_ms': prepare_ms, 'writer_commit_ms': (time.perf_counter() - start) * 1000})
                samples.append({'raw_bytes': size, 'input_sha256': row.sha256.hex(),
                                    'preparation_ms': codec_ms, 'insert_writer_commit_ms': insert_ms,
                                    'transitions': transitions})
                # Matched variants reuse line numbers and remove only this trial's rows outside timing.
                SessionItem.objects.filter(
                    session=owner, line_num=20000 + repeat * 4 + sizes.index(size),
                ).delete()
                SessionItemContent.objects.filter(pk=row.pk).delete()
        writes['with_index' if indexed else 'without_index'] = samples
    selection_index(True)
    connection.close()
    return {'index_write_overhead': {'samples': writes, 'repeats': 3,
                                         'note': 'writer transaction plus commit; no coordinator queue; preparation separate'},
                'complete_pass': complete, 'synthetic_fixture': dict(fixture, setup_ms=setup_ms)}


def main():
    args = arguments()
    output = validate_output(args)
    work, target = destination(args)
    configure(work, target, source=bool(args.source_db))
    fixture = {'source_backup': True} if args.source_db else seed(args.scenario)
    result = (run_selection_benchmark() if args.scenario == 'selection'
              else asyncio.run(run_pass(args.scenario, bool(args.source_db))))
    if args.scenario == 'acquisition':
        result = acquisition(result)
    elif not args.source_db and args.scenario != 'selection':
        samples = [dict(cache_state='first measured pass; fixture pages may be warm', **result)]
        for _ in range(2):
            if args.scenario in {'compress', 'foreground'}:
                restore_candidates()
            samples.append(dict(cache_state='warm repeat on the same database',
                                **asyncio.run(run_pass(args.scenario))))
        result = samples
    import orjson
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects files installed after initial validation.
    with output.open('xb') as destination_file:
        destination_file.write(orjson.dumps({'scenario': args.scenario, 'fixture': fixture, 'result': result},
                                           option=orjson.OPT_INDENT_2))


if __name__ == '__main__':
    main()
