#!/usr/bin/env python3
"""Disposable authenticated WebSocket/live-sync benchmark. Never runs providers.

Example:
    uv run python scripts/benchmark_session_sync.py --data-dir /scratch/empty-run \
        --lines 150000 --payload-bytes 1500000000 --report /scratch/report.json

Cold means a new SQLite connection, not a flushed operating-system page cache.
The report retains every slice's SQLite VM steps to expose prefix-scan growth.
"""

import argparse
import asyncio
import logging
import math
import os
import platform
import secrets
import shutil
import socket
import sqlite3
import sys
from pathlib import Path
from time import perf_counter

import orjson


def prepare_directory(path):
    """Reject normal storage and anything except an explicitly empty directory."""
    path = Path(path).expanduser().resolve()
    normal = (Path.home() / '.twicc').resolve()
    inherited = os.environ.get('TWICC_DATA_DIR')
    if path == normal or (inherited and path == Path(inherited).expanduser().resolve()):
        raise ValueError('Refusing the normal or inherited instance data directory')
    if (path / 'db' / 'data.sqlite').exists():
        raise ValueError('Refusing a preexisting database')
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Disposable data directory must be empty')
    path.mkdir(parents=True, exist_ok=True)
    return path


def initialize(path):
    # No TwiCC import may precede these assignments. Its .env loader runs once.
    os.environ['TWICC_DATA_DIR'] = str(path)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'twicc.settings'
    (path / '.env').write_text(
        f'CLAUDE_CONFIG_DIR={path / "unused-claude"}\n'
        f'CLAUDE_SECURESTORAGE_CONFIG_DIR={path / "unused-claude"}\n'
        f'CODEX_HOME={path / "unused-codex"}\nTWICC_NO_LOG_TRIM=1\n'
    )
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    resolved = Path(settings.DATABASES['default']['NAME']).resolve()
    expected = path / 'db' / 'data.sqlite'
    if resolved != expected or resolved.exists():
        raise RuntimeError(f'Refusing database: resolved={resolved}, expected absent={expected}')
    print(f'Verified disposable database: {resolved}', flush=True)
    settings.DEBUG = False
    settings.TWICC_PASSWORD_HASH = secrets.token_hex(32)
    settings.TWICC_ALLOW_INSECURE_REMOTE = False
    call_command('migrate', interactive=False, verbosity=0)
    return resolved


def record(number, padding, *, prefix='large'):
    content = [{'type': 'text', 'text': padding}]
    # Reuse a real tool identifier across distant source records.
    if number % 1000 == 0:
        content.append({'type': 'tool_use', 'id': 'reused-tool', 'name': 'Bash', 'input': {'command': 'true'}})
    return {'type': 'assistant', 'timestamp': '2026-09-29T10:00:00Z', 'message': {
        'id': f'{prefix}-{number}', 'role': 'assistant', 'model': 'claude-sonnet-4-20250514',
        'usage': {'input_tokens': 100, 'output_tokens': 10}, 'content': content}}


def seed(path, lines, payload_bytes):
    from twicc.core.models import ModelPrice, Project, Session
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    ModelPrice.objects.create(provider='claude_code', model_id='anthropic/claude-sonnet-4',
        effective_date='2026-01-01', input_price=3, output_price=15, cache_read_price=0,
        cache_write_5m_price=0, cache_write_1h_price=0)
    ModelPrice.invalidate_price_cache()
    project = Project.objects.create(id='benchmark', directory=str(path))
    for name in ('large', 'small'):
        Session.objects.create(id=name, project=project, provider='claude_code', file_path=f'{name}.jsonl',
                               compute_version=ClaudeCodeSessionCompute().compute_version)
    size, extra = divmod(payload_bytes, lines)
    padding = 'x' * size
    source = path / 'large.jsonl'
    with source.open('wb') as stream:
        for number in range(lines):
            stream.write(orjson.dumps(record(number, padding + ('x' if number < extra else ''))) + b'\n')
    (path / 'small.jsonl').write_bytes(orjson.dumps(record(0, 'small', prefix='small')) + b'\n')
    return source.stat().st_size


def lookup_report(lines):
    from django.db import connection
    from twicc.core.models import Session, SessionHistoryFact, SessionItem
    from twicc.providers.history_facts import HistoryFactContext
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    plans = {
        'fact': SessionHistoryFact.objects.filter(session_id='large', kind='tool_call', key='reused-tool',
            line_num__lt=lines + 1).order_by('-line_num').values('line_num', 'data')[:128].explain(),
        'message_id': SessionItem.objects.filter(session_id='large', message_id='large-0',
            line_num__lt=lines + 1).values('id')[:1].explain(),
        'fallback': SessionItem.objects.filter(session_id='large', line_num__lt=lines + 1)
            .order_by('-line_num').values('line_num', 'content')[:128].explain(),
    }
    for name, plan in plans.items():
        assert 'SEARCH' in plan and 'INDEX' in plan and 'SCAN ' not in plan, (name, plan)
    results = {}
    expected = (lines - 1) // 1000 * 1000 + 1
    for mode in ('current_cold_connection', 'current_warm', 'outdated_fallback'):
        Session.objects.filter(id='large').update(compute_version=(
            0 if mode == 'outdated_fallback' else ClaudeCodeSessionCompute().compute_version))
        samples = []
        for attempt in range(3 if mode == 'current_warm' else 1):
            for key in ('reused-tool', 'true-miss'):
                if mode != 'current_warm':
                    connection.close()
                started = perf_counter()
                owner = HistoryFactContext('claude_code', session_id='large').lookup_tool_call(
                    key, before_line=lines + 1)
                samples.append({'key': key, 'attempt': attempt, 'elapsed_ms': (perf_counter() - started) * 1000,
                                'owner_line': owner[1] if owner else None})
                assert (owner[1] if owner else None) == (expected if key == 'reused-tool' else None)
        results[mode] = samples
    Session.objects.filter(id='large').update(compute_version=ClaudeCodeSessionCompute().compute_version)
    return {'plans': plans, 'lookups': results,
            'cold_definition': 'New SQLite connection for each cold hit/miss; OS cache retained'}


def scaling_report(slices):
    replay = [sample for sample in slices if sample['session'] == 'large' and sample['phase'] == 'replay']
    results = {}
    # Compare actual slice sizes: the byte limit can reduce a 500-line request.
    # Singleton final slices cannot supply a beginning/end comparison.
    for size in sorted({sample['lines'] for sample in replay}):
        same = [sample for sample in replay if sample['lines'] == size]
        if len(same) < 4:
            continue
        width = max(1, len(same) // 4)
        early = sum(row['vm_steps'] for row in same[:width]) / (width * size)
        late = sum(row['vm_steps'] for row in same[-width:]) / (width * size)
        ratio = late / early
        # A prefix scan grows with the committed prefix, far beyond this bound.
        assert ratio < 3, (size, early, late, ratio)
        results[str(size)] = {'early_vm_steps_per_row': early, 'late_vm_steps_per_row': late, 'ratio': ratio,
                              'sample_count': len(same)}
    return results


async def exercise(path, lines, report):
    import uvicorn
    from websockets.asyncio.client import connect
    from asgiref.sync import sync_to_async
    from django.conf import settings
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import connection
    from twicc.asgi import application
    from twicc.auth.session_auth import bind_session
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.live_sync import LiveSyncLimits
    from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile

    samples, slices, small_completions = [], [], []
    phase = 'replay'

    class MeasuredCompute(ClaudeCodeSessionCompute):
        def sync_session_slice(self, session_id, file_path, *, limits):
            connection.ensure_connection()
            steps = 0
            def progress():
                nonlocal steps
                steps += 100
                return 0
            connection.connection.set_progress_handler(progress, 100)
            started = perf_counter()
            try:
                result = super().sync_session_slice(session_id, file_path, limits=limits)
            finally:
                connection.connection.set_progress_handler(None, 0)
            slices.append({'session': session_id, 'phase': phase, 'lines': result.lines_processed,
                           'bytes': result.bytes_consumed, 'elapsed_ms': (perf_counter() - started) * 1000,
                           'vm_steps': steps, 'has_more': result.has_more})
            return result

    compute = MeasuredCompute()
    watcher = BaseSessionsWatcher()
    watcher.projects_dir = path
    watcher.get_compute = lambda: compute
    async def parse(source):
        return ParsedSessionFile('benchmark', source.stem, 'session', source.name, title='Synthetic benchmark')
    watcher.parse_session_file = parse
    original = watcher._process_change
    async def process(*args):
        source = Path(args[1])
        if source.stem == 'large':
            watcher._line_limits[source] = 125 if len(slices) % 2 else 500
        result = await original(*args)
        if source.stem == 'large' and len(slices) % 100 == 0:
            print(f'Completed {len(slices)} slices in {perf_counter() - began:.1f}s', flush=True)
        if source.stem == 'small':
            small_completions.append({'seconds': perf_counter() - began,
                                      'large_rows_committed': sum(s['lines'] for s in slices if s['session'] == 'large')})
        return result
    watcher._process_change = process

    def make_cookie():
        auth = SessionStore()
        bind_session(auth, settings.TWICC_PASSWORD_HASH)
        auth.save()
        return f'{settings.SESSION_COOKIE_NAME}={auth.session_key}'
    cookie = await sync_to_async(make_cookie)()
    sock = socket.socket()
    server = server_task = None
    tasks = []
    began = perf_counter()

    async def cleanup():
        # Each resource owns an independent finally boundary. In particular,
        # stop_db_writer may re-raise cancellation after its successful drain.
        try:
            try:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                watcher.stop_watcher()
                await watcher._drain_changes()
        finally:
            try:
                await db_writer.stop_db_writer()
            finally:
                try:
                    if server is not None:
                        server.should_exit = True
                    if server_task is not None:
                        await server_task
                finally:
                    sock.close()

    try:
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        report['server'] = {'host': '127.0.0.1', 'port': port, 'authentication': 'Disposable database session cookie'}
        server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port, lifespan='off',
                                               log_level='error', access_log=False))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        db_writer.start_db_writer()
        async with asyncio.timeout(10):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError('ASGI server exited before startup')
                await asyncio.sleep(.01)
        async with connect(f'ws://127.0.0.1:{port}/ws/?subscribe=hidden_sessions',
                           additional_headers={'Cookie': cookie}, origin=f'http://127.0.0.1:{port}',
                           ping_interval=None) as websocket:
            initial = orjson.loads(await websocket.recv())
            assert initial['type'] == 'hidden_sessions', initial
            done = asyncio.Event()
            async def pings():
                while not done.is_set():
                    started = perf_counter()
                    await websocket.send('{"type":"ping"}')
                    response = await asyncio.wait_for(websocket.recv(), 5)
                    assert response == '{"type": "pong"}', response
                    samples.append((perf_counter() - started) * 1000)
                    await asyncio.sleep(.05)
            async def small_workload():
                for number in range(20):
                    if number:
                        with (path / 'small.jsonl').open('ab') as stream:
                            stream.write(orjson.dumps(record(number, 'small', prefix='small')) + b'\n')
                    await watcher.process_path(path / 'small.jsonl')
                    await asyncio.sleep(.05)
            heartbeat = asyncio.create_task(pings())
            large = asyncio.create_task(watcher.process_path(path / 'large.jsonl'))
            small = asyncio.create_task(small_workload())
            tasks = [heartbeat, large, small]
            try:
                await large
                report['backlog_seconds'] = perf_counter() - began
                await small
                assert any(row['large_rows_committed'] < lines for row in small_completions)
                phase = 'append'
                with (path / 'large.jsonl').open('ab') as stream:
                    for number in range(lines, lines + 20):
                        stream.write(orjson.dumps(record(number, 'append')) + b'\n')
                started = perf_counter()
                await watcher.process_path(path / 'large.jsonl')
                report['small_append_seconds'] = perf_counter() - started
                # Measure identical established-prefix work at several slice sizes.
                phase = 'append_scaling'
                for size in (125, 500):
                    with (path / 'large.jsonl').open('ab') as stream:
                        for number in range(size):
                            stream.write(orjson.dumps(record(number, 'scaling', prefix=f'scaling-{size}')) + b'\n')
                    await db_writer.run_under_db_write_lock(lambda size=size: run_compute_sync(
                        compute.sync_session_slice, 'large', path / 'large.jsonl', limits=LiveSyncLimits(size)))
            finally:
                done.set()
                await heartbeat
    finally:
        # Repeated caller cancellation must not interrupt resource drainage.
        cleanup_task = asyncio.create_task(cleanup())
        cancelled = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        cleanup_task.result()
        if cancelled:
            raise asyncio.CancelledError
    report['slices'] = slices
    report['small_completions'] = small_completions
    report['pong'] = {'samples_ms': samples, 'count': len(samples),
                      'p95_ms': sorted(samples)[math.ceil(len(samples) * .95) - 1], 'max_ms': max(samples)}
    report['pong']['target_met'] = report['pong']['p95_ms'] < 1000
    report['scaling'] = scaling_report(slices)
    if lines >= 10000:
        assert len(report['scaling']) >= 2, 'Need two measured slice sizes for scaling evidence'
    assert report['pong']['target_met'], report['pong']
    await sync_to_async(lambda: connection.close())()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--lines', type=int, default=150000)
    parser.add_argument('--payload-bytes', type=int, default=1500000000)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.lines < 10 or args.payload_bytes < args.lines:
        parser.error('Use at least 10 lines and at least one payload byte per line')
    path = prepare_directory(args.data_dir)
    report_path = args.report.expanduser().resolve()
    if report_path.exists() or report_path == path or path in report_path.parents:
        parser.error('Report must be a new file outside the disposable data directory')
    report = {'machine': {'platform': platform.platform(), 'python': sys.version,
                         'cpu_count': os.cpu_count(), 'sqlite': sqlite3.sqlite_version,
                         'free_bytes_before': shutil.disk_usage(path).free},
              'workload': {'provider': 'claude_code', 'lines': args.lines, 'payload_bytes': args.payload_bytes},
              'data_dir': str(path), 'status': 'running'}
    began = perf_counter()
    try:
        database = initialize(path)
        report['database'] = str(database)
        report['workload']['source_bytes'] = seed(path, args.lines, args.payload_bytes)
        report['prepare_seconds'] = perf_counter() - began
        print(f'Replaying {args.lines} rows, {report["workload"]["source_bytes"]} source bytes', flush=True)
        asyncio.run(exercise(path, args.lines, report))
        from django.db.models import Sum
        from twicc.core.models import Session, SessionItem
        expected = args.lines + 20 + 125 + 500
        session = Session.objects.get(id='large')
        count = SessionItem.objects.filter(session=session, message_id__isnull=False, cost__isnull=False).count()
        report['counts'] = {'large_rows': session.last_line, 'cost_and_message_id_rows': count,
                            'facts': session.history_facts.count(), 'small_rows': Session.objects.get(id='small').last_line,
                            'last_offset': session.last_offset, 'self_cost': str(session.self_cost)}
        assert session.last_line == count == expected
        assert session.last_offset == (path / 'large.jsonl').stat().st_size
        from decimal import Decimal
        assert session.self_cost == session.items.aggregate(total=Sum('cost'))['total'].quantize(Decimal('0.000001'))
        report.update(lookup_report(args.lines))
        report['status'] = 'passed'
    except BaseException as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['total_seconds'] = perf_counter() - began
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2))
        print(f'Report: {report_path} ({report["status"]})', flush=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    main()
