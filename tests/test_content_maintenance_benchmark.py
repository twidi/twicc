"""Subprocess isolation contracts for the disposable selection benchmark."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import orjson
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/benchmark_content_maintenance.py'


def invoke(*args):
    environment = dict(os.environ)
    environment.pop('DJANGO_SETTINGS_MODULE', None)
    return subprocess.run([sys.executable, str(SCRIPT), '--scenario', 'selection', *map(str, args)],
                          cwd=ROOT, env=environment, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize('alias', ['source', 'hardlink', 'destination', 'occupied', 'protected'])
def test_selection_rejects_unsafe_paths(tmp_path, alias, monkeypatch):
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE marker(value)')
    before = source.read_bytes()
    work, output = tmp_path / 'work', tmp_path / 'result.json'
    if alias == 'source':
        output = source
    elif alias == 'hardlink':
        os.link(source, output)
    elif alias == 'destination':
        work.mkdir()
        os.link(source, work / 'benchmark.sqlite')
    elif alias == 'occupied':
        output.write_bytes(b'keep')
    else:
        monkeypatch.setenv('TWICC_DATA_DIR', str(tmp_path / 'protected'))
        work = tmp_path / 'protected' / 'new'
    result = invoke('--source-db', source, '--work-dir', work, '--output', output)
    assert result.returncode != 0
    assert 'invalid choice' not in result.stderr
    assert source.read_bytes() == before
    if alias == 'occupied':
        assert output.read_bytes() == b'keep'


def test_selection_source_schema_and_bytes_unchanged(tmp_path):
    source = tmp_path / 'source.sqlite'
    code = '''import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('bench', {!r})
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.configure(pathlib.Path({!r}), pathlib.Path({!r}))
m.seed('selection')
from django.db import connections
connections.close_all()
'''.format(str(SCRIPT), str(tmp_path / 'source-home'), str(source))
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    before = source.read_bytes()
    with sqlite3.connect(source) as db:
        schema = db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    output = tmp_path / 'result.json'
    result = invoke('--source-db', source, '--work-dir', tmp_path / 'copy', '--output', output)
    assert result.returncode == 0, result.stderr
    assert source.read_bytes() == before
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == schema
    measured = orjson.loads(output.read_bytes())['result']
    assert set(measured) >= {'root_selection', 'content_selection', 'index_build',
                             'index_write_overhead', 'complete_pass'}
    assert measured['content_selection']['filtered_with_index']['scanned'] > 0
    assert measured['complete_pass']['compressible']['changed'] > 0
    assert measured['synthetic_fixture']['preconformant_contents'] == 10000
    assert measured['synthetic_fixture']['shared_cold_references'] == 20000
    assert measured['complete_pass']['conformant']['payload_bytes_loaded'] == 0
    failure = measured['complete_pass']['restoration_failures']
    assert failure['transient_attempts'] >= 2
    assert failure['permanent_attempts'] >= 2
    assert failure['unaffected_compressed']
    assert failure['transient_restored']
    assert failure['changed'] > 0
    variants = measured['content_selection']
    assert variants['all_content']['scanned'] > variants['filtered_with_index']['scanned']
    assert variants['filtered_without_index']['scanned'] == variants['filtered_with_index']['scanned']
    assert any('idx_content_plain_id' in line for line in
               variants['filtered_with_index']['plans']['candidate_lookup'])


def test_backup_keeps_one_snapshot_when_source_commits_between_steps(tmp_path, monkeypatch):
    """A concurrent commit cannot restart a stepped copy onto a later source snapshot."""
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location('bench', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'busy.sqlite'
    connect = sqlite3.connect
    with connect(source) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE fixture(value, payload)')
        writer.execute('INSERT INTO fixture VALUES (7, zeroblob(12000000))')
    writer = connect(source)

    class BusySource(sqlite3.Connection):
        def backup(self, target, **kwargs):
            def commit_during_copy(status, remaining, total):
                if writer.execute('SELECT value FROM fixture').fetchone()[0] == 7:
                    writer.execute('UPDATE fixture SET value=42')
                    writer.commit()
            return super().backup(target, progress=commit_during_copy, **kwargs)

    def connect_with_commit(database, **kwargs):
        if kwargs.get('uri'):
            kwargs['factory'] = BusySource
        return connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_with_commit)
    try:
        _, target = module.destination(SimpleNamespace(work_dir=tmp_path / 'copy', source_db=source))
        with connect(target) as copied:
            assert copied.execute('SELECT value FROM fixture').fetchone() == (7,)
        assert writer.execute('SELECT value FROM fixture').fetchone() == (42,)
    finally:
        writer.close()
