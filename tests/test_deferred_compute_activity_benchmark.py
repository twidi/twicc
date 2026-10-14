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


def test_metadata_reads_stay_outside_writer(report):
    assert report['historical_queries_inside_writer'] == []
    assert report['writer_hold_ms']['max'] < 1000


def test_exact_reference_cache_and_recovery(report):
    assert report['debug'] is False
    assert set(report['workloads']) == {'small', 'medium', 'large', 'very_large', 'cache400'}
    assert report['final_state_matches_independent_reference'], report['oracle_errors']
    assert report['queue_rows_remaining'] == 0
    assert report['workloads']['cache400']['one_changed_own_reads'] == 1
    for label, size in [('small', 499), ('medium', 9753), ('large', 59004), ('very_large', 458192)]:
        assert report['workloads'][label]['source_rows'] == size
        assert report['workloads'][label]['first_ms'] > 0
        assert report['workloads'][label]['warm_ms'] > 0
