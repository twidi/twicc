"""Slow-operation diagnostics stay bounded and omit payloads."""

import logging


def test_slow_log_rate_limit_is_per_session_and_operation(monkeypatch, caplog):
    from twicc import sync_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(diagnostics.logger, 'disabled', False)
    monkeypatch.setattr(diagnostics.logger, 'propagate', False)
    monkeypatch.setattr(diagnostics.logger, 'level', logging.WARNING)
    diagnostics._last_logged.clear()
    now = [10.0]
    monkeypatch.setattr(diagnostics, 'monotonic', lambda: now[0])
    with caplog.at_level(logging.WARNING):
        with diagnostics.sync_timing_context('codex', 'one', lines=500):
            diagnostics.log_slow('slice', 99)
            diagnostics.log_slow('slice', 101, bytes=1024, backlog=True)
            diagnostics.log_slow('slice', 200)
            diagnostics.log_slow('executor_run', 200)
        with diagnostics.sync_timing_context('codex', 'two'):
            diagnostics.log_slow('slice', 200)
        now[0] += 60
        with diagnostics.sync_timing_context('codex', 'one'):
            diagnostics.log_slow('slice', 200)
    assert len(caplog.records) == 4
    assert 'provider=codex session=one' in caplog.records[0].message
    assert 'lines=500' in caplog.records[0].message
    assert 'bytes=1024' in caplog.records[0].message
    assert diagnostics._context.get() is None


def test_rate_limit_memory_is_bounded(caplog):
    from twicc import sync_diagnostics as diagnostics
    diagnostics._last_logged.clear()
    with caplog.at_level(logging.ERROR):
        for number in range(diagnostics.MAX_TIMING_KEYS + 10):
            with diagnostics.sync_timing_context('codex', str(number)):
                diagnostics.log_slow('slice', 101)
    assert len(diagnostics._last_logged) == diagnostics.MAX_TIMING_KEYS


def test_executor_reports_queue_and_run_separately(monkeypatch):
    import asyncio
    from twicc.providers import compute_executor
    from twicc.sync_diagnostics import _context, sync_timing_context
    times = iter([1, 1.2, 1.5])
    monkeypatch.setattr(compute_executor, 'perf_counter', lambda: next(times))
    seen = []
    monkeypatch.setattr(compute_executor, 'log_slow', lambda operation, elapsed:
                        seen.append((operation, round(elapsed), _context.get())))
    async def run():
        compute_executor.start_compute_executor()
        try:
            with sync_timing_context('codex', 'executor-session'):
                assert await compute_executor.run_compute_sync(lambda: 7) == 7
        finally:
            await compute_executor.stop_compute_executor()
    asyncio.run(run())
    assert [(operation, elapsed) for operation, elapsed, _ in seen] == [
        ('executor_queue', 200), ('executor_run', 300)]
    assert all(context['session'] == 'executor-session' for _, _, context in seen)


def test_transport_logs_termination_once_without_exception_payload(monkeypatch):
    import asyncio
    from twicc.websocket_transport import HeartbeatTransport, logger
    messages = []
    monkeypatch.setattr(logger, 'warning', lambda message, *args: messages.append(message % args))
    async def run():
        transport = HeartbeatTransport(None, None, None)
        transport._log_termination('disconnect-1006')
        transport._fail(OSError('secret message payload'))
        transport._fail(OSError('second secret'))
    asyncio.run(run())
    assert len(messages) == 1
    assert 'disconnect-1006' in messages[0]
    assert 'secret' not in messages[0]
