"""Bounded, payload-free diagnostics shared by sync admission boundaries."""

import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

logger = logging.getLogger(__name__)
MAX_TIMING_KEYS = 1024
_context = ContextVar('sync_timing_context', default=None)
_last_logged = OrderedDict()
_log_lock = threading.Lock()


@contextmanager
def sync_timing_context(provider, session_id, **sizes):
    token = _context.set({'provider': provider, 'session': session_id, **sizes})
    try:
        yield
    finally:
        _context.reset(token)


def log_slow(operation, elapsed_ms, *, threshold_ms=100, **sizes):
    """Log at most once per minute for each operation/provider/session tuple."""
    if elapsed_ms <= threshold_ms:
        return
    context = {**(_context.get() or {}), **sizes}
    key = (operation, context.get('provider'), context.get('session'))
    now = monotonic()
    with _log_lock:
        previous = _last_logged.get(key)
        if previous is not None and now - previous < 60:
            return
        _last_logged[key] = now
        _last_logged.move_to_end(key)
        if len(_last_logged) > MAX_TIMING_KEYS:
            _last_logged.popitem(last=False)
    # Only caller-supplied identifiers and numeric sizes reach this module.
    logger.warning('Slow sync operation=%s elapsed_ms=%.1f provider=%s session=%s %s',
                   operation, elapsed_ms, context.pop('provider', None), context.pop('session', None),
                   ' '.join(f'{key}={value}' for key, value in context.items()))
