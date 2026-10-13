"""Opt-in persisted WAL characterization through one isolated revision adapter."""

import os
from pathlib import Path
import subprocess
import sys

import orjson
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('TWICC_ACTIVITY_BENCHMARK') != '1', reason='isolated activity benchmark is opt-in',
)


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    checkout = Path(os.environ.get('TWICC_ACTIVITY_BENCHMARK_CHECKOUT', root)).resolve()
    disposable = tmp_path_factory.mktemp('activity-benchmark')
    output = disposable / 'report.json'
    subprocess.run([
        sys.executable, str(root / 'scripts/benchmark_deferred_compute_activity.py'),
        '--checkout', str(checkout), '--data-dir', str(disposable / 'data'), '--output', str(output),
    ], check=True, cwd=root)
    result = orjson.loads(output.read_bytes())
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    return result


def test_metadata_deferral_characterization(report):
    assert report['metadata_history_scan_count'] == 0
    assert report['metadata_bucket_repair_count'] == 0


def test_exact_reference_and_foreground_admission(report):
    assert report['debug'] is False
    assert set(report['workloads']) == {'A', 'B', 'C', 'D', 'E'}
    assert report['final_state_matches_independent_reference'], report['oracle_errors']
    assert report['foreground_wins_next_background_grant']
    assert report['workloads']['A']['source_rows'] == 24_000
    assert report['workloads']['B']['source_rows'] == 48_000
    assert report['workloads']['C']['fixture_source_rows'] == 12_000
    assert report['workloads']['D']['foreground_phases'] == [
        'metadata', 'bucket_preparation', 'orphan_recovery', 'forced_exact_final',
    ]
    assert report['workloads']['E']['foreground_history_scan_count'] > 0
    assert report['workloads']['E']['retries'] == 3
    assert all(report['workloads'][name]['final_transaction_hold_ms'] > 0 for name in 'ABCDE')
    if report['journal_available']:
        assert report['live_append']['with_journal']['presence_probe_count'] > 0
        assert report['live_append']['without_journal']['presence_probe_count'] > 0
