"""The benchmark cannot reuse instance storage or hide quadratic work."""

import pytest

from scripts.benchmark_session_sync import prepare_directory, scaling_report


def test_benchmark_refuses_normal_inherited_and_nonempty_directories(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    with pytest.raises(ValueError, match='normal'):
        prepare_directory(tmp_path / '.twicc')
    inherited = tmp_path / 'instance'
    monkeypatch.setenv('TWICC_DATA_DIR', str(inherited))
    with pytest.raises(ValueError, match='inherited'):
        prepare_directory(inherited)
    database = tmp_path / 'reused' / 'db' / 'data.sqlite'
    database.parent.mkdir(parents=True)
    database.touch()
    with pytest.raises(ValueError, match='preexisting'):
        prepare_directory(database.parent.parent)
    nonempty = tmp_path / 'nonempty'
    nonempty.mkdir()
    (nonempty / '.env').touch()
    with pytest.raises(ValueError, match='empty'):
        prepare_directory(nonempty)
    empty = tmp_path / 'new'
    assert prepare_directory(empty) == empty
    assert list(empty.iterdir()) == []


def test_vm_step_scaling_rejects_prefix_scans():
    rows = [{'session': 'large', 'phase': 'replay', 'lines': 125, 'vm_steps': 1000 * n}
            for n in range(1, 21)]
    with pytest.raises(AssertionError):
        scaling_report(rows)
    for row in rows:
        row['vm_steps'] = 1000
    assert scaling_report(rows)['125']['ratio'] == 1
