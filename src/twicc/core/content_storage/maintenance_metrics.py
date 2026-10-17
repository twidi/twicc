"""Bounded operation counters. Logging never queries content or session tables.

Foreground observations overlap when operations overlap. Do not add their totals.
The coordinator sample excludes earlier job-queue delay.
"""

import logging
from collections import Counter
from datetime import UTC, datetime
from math import ceil
from time import monotonic

logger = logging.getLogger(__name__)
_HOLD_BOUNDS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, float('inf'))


class MaintenanceMetrics:
    def __init__(self, operation_id: str, operation_kind: str):
        self.operation_id = operation_id
        self.operation_kind = operation_kind
        self.started_at = datetime.now(UTC).isoformat()
        self.started = monotonic()
        self.last_progress = self.started
        self.counts = Counter(dict.fromkeys((
            'scanned', 'already_compressed', 'no_gain_skips', 'ineligible_owners', 'prepared',
            'payload_bytes_loaded', 'candidate_raw_bytes', 'candidate_stored_bytes', 'changed',
            'no_gain_recorded', 'already_conformant', 'stale', 'bytes_saved', 'batches_completed',
            'publication_batches', 'preparation_ms', 'resource_wait_ms', 'transaction_ms', 'writer_hold_ms',
            'writer_hold_max_ms', 'foreground_coordinator_wait_count', 'foreground_coordinator_wait_ms',
            'foreground_coordinator_wait_max_ms', 'writer_hold_ge_100_ms', 'writer_hold_ge_250_ms',
            'writer_hold_ge_500_ms', 'writer_hold_ge_1000_ms',
            'discovery_pages', 'discovery_sessions', 'recovery_reference_probes', 'discovery_preparation_ms',
        ), 0))
        self.cursor = None
        self.discovery_cursor = None
        self.cache_statistics = {}
        self.hold_bins = [0] * len(_HOLD_BOUNDS)

    def record_page(self, page):
        self.counts.update(page.counts._asdict())
        self.counts['batches_completed'] += 1
        self.cursor = page.cursor

    def record_discovery(self, cursor, sessions, probes, duration_ms):
        self.discovery_cursor = cursor
        self.counts['discovery_pages'] += 1
        self.counts['discovery_sessions'] += sessions
        self.counts['recovery_reference_probes'] += probes
        self.counts['discovery_preparation_ms'] += duration_ms
        self.record_preparation(duration_ms)

    def record_preparation(self, duration_ms):
        self.counts['preparation_ms'] += duration_ms

    def record_foreground_wait(self, wait_ms: float) -> None:
        self.counts['foreground_coordinator_wait_count'] += 1
        self.counts['foreground_coordinator_wait_ms'] += wait_ms
        self.counts['foreground_coordinator_wait_max_ms'] = max(
            self.counts['foreground_coordinator_wait_max_ms'], wait_ms,
        )

    def record_publication(self, result, *, wait_ms: float, transaction_ms: float, hold_ms: float):
        if result is not None:
            self.counts.update(result._asdict())
        self.counts['publication_batches'] += 1
        self.counts['resource_wait_ms'] += wait_ms
        self.counts['transaction_ms'] += transaction_ms
        self.counts['writer_hold_ms'] += hold_ms
        self.counts['writer_hold_max_ms'] = max(self.counts['writer_hold_max_ms'], hold_ms)
        for index, bound in enumerate(_HOLD_BOUNDS):
            if hold_ms <= bound:
                self.hold_bins[index] += 1
                break
        for threshold in (100, 250, 500, 1000):
            self.counts[f'writer_hold_ge_{threshold}_ms'] += int(hold_ms >= threshold)
        if hold_ms >= 100:
            logger.warning('Content maintenance long writer hold', extra={'maintenance': {
                'event': 'long_hold', 'operation_id': self.operation_id, 'operation_kind': self.operation_kind,
                'batch': self.counts['publication_batches'], 'cursor': self.cursor,
                'writer_hold_ms': hold_ms, 'changed': 0 if result is None else result.changed,
            }})

    def _percentile(self, percentile):
        rank = ceil(sum(self.hold_bins) * percentile)
        if not rank:
            return 0
        total = 0
        for count, bound in zip(self.hold_bins, _HOLD_BOUNDS):
            total += count
            if total >= rank:
                return bound

    def emit(self, event: str) -> None:
        now = monotonic()
        if event == 'progress' and now - self.last_progress < 10:
            return
        if event == 'progress':
            self.last_progress = now
        summary = dict(self.counts)
        summary.update(operation_id=self.operation_id, operation_kind=self.operation_kind, event=event,
                       started_at=self.started_at, elapsed_ms=(now - self.started) * 1000,
                       cursor=self.cursor, discovery_cursor=self.discovery_cursor, cache=dict(self.cache_statistics),
                       writer_hold_p50_upper_ms=self._percentile(.5),
                       writer_hold_p95_upper_ms=self._percentile(.95),
                       writer_hold_p99_upper_ms=self._percentile(.99))
        if event in {'complete', 'failure', 'cancelled'}:
            summary['ended_at'] = datetime.now(UTC).isoformat()
        logger.info('Content maintenance %s: %s', event, summary, extra={'maintenance': summary})
