"""Complete-record limits and live transaction boundaries."""

import os

import pytest

from twicc.providers.live_sync import LiveSyncLimits, read_live_slice


def test_reader_bounds_lines_and_keeps_ready_backlog(tmp_path):
    path = tmp_path / 'live.jsonl'
    path.write_bytes(b'{}\n' * 501)
    first = read_live_slice(path, offset=0, limits=LiveSyncLimits())
    assert len(first.records) == 500
    assert first.end_offset == first.bytes_consumed == 1500
    assert first.has_more
    second = read_live_slice(path, offset=first.end_offset, limits=LiveSyncLimits())
    assert second.records == [b'{}\n']
    assert not second.has_more


def test_reader_bytes_oversized_and_incomplete_tail(tmp_path):
    path = tmp_path / 'live.jsonl'
    path.write_bytes(b'123456789\n{}\n' + b'{"text":"\xc3')
    first = read_live_slice(path, offset=0, limits=LiveSyncLimits(500, 4))
    assert first.records == [b'123456789\n']
    assert first.bytes_consumed == 10
    assert first.has_more
    second = read_live_slice(path, offset=10, limits=LiveSyncLimits(500, 4))
    assert second.records == [b'{}\n']
    assert second.end_offset == 13
    assert not second.has_more
    with path.open('ab') as f:
        f.write(b'\xa9"}\n')
    third = read_live_slice(path, offset=13, limits=LiveSyncLimits())
    assert third.records == ['{"text":"é"}\n'.encode()]


def test_blank_records_count_bytes_only(tmp_path):
    path = tmp_path / 'live.jsonl'
    path.write_bytes(b'\n \nwrong\n{}\n')
    result = read_live_slice(path, offset=0, limits=LiveSyncLimits(1, 100))
    assert result.records == [b'\n', b' \n', b'wrong\n']
    assert result.bytes_consumed == 9
    assert result.has_more


@pytest.mark.django_db(transaction=True)
def test_live_records_numbering_checkpoint_same_mtime_and_slow_second_pass(tmp_path, monkeypatch):
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers import compute_base

    session = Session.objects.create(id='s', project=Project.objects.create(id='p'),
                                    provider=Provider.CLAUDE_CODE, file_path='s')
    path = tmp_path / 's'
    path.write_bytes(b'\n \nmalformed\n{}\n' + b'{"text":"\xc3')
    compute = ClaudeCodeSessionCompute()
    now = [0]
    monkeypatch.setattr(compute_base, 'perf_counter', lambda: now[0])
    original = compute.apply_agent_run_signals
    def slow(*args, **kwargs):
        now[0] += .2
        return original(*args, **kwargs)
    monkeypatch.setattr(compute, 'apply_agent_run_signals', slow)
    result = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    session.refresh_from_db()
    assert result.lines_processed == 2
    assert result.updates.new_line_nums == [1, 2]
    assert result.elapsed_ms >= 400
    assert not result.has_more
    assert session.last_line == 2
    assert session.last_offset == len(b'\n \nmalformed\n{}\n')
    stat = path.stat()
    with path.open('ab') as f:
        f.write(b'\xa9"}\n')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    assert result.updates.new_line_nums == [3]
    assert result.lines_processed == 1
    session.refresh_from_db()
    assert session.last_offset == path.stat().st_size


@pytest.mark.django_db(transaction=True)
def test_live_501_records_commit_500_then_one(tmp_path):
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session
    from twicc.providers.codex.compute import CodexSessionCompute
    session = Session.objects.create(id='s', project=Project.objects.create(id='p'), provider=Provider.CODEX)
    path = tmp_path / 's'
    path.write_bytes(b'{}\n' * 501)
    compute = CodexSessionCompute()
    first = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    assert first.lines_processed == 500 and first.has_more
    assert session.items.count() == 500
    second = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    assert second.lines_processed == 1 and not second.has_more


def test_readiness_never_uses_an_unbounded_line_read(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'tail'
    path.write_bytes(b'x' * (2 * 1024 * 1024))
    original_open = Path.open
    class BoundedProbe:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def readline(self, size=-1):
            assert size > 0, 'readiness must not allocate an incomplete tail'
            return self.stream.readline(size)
    monkeypatch.setattr(Path, 'open', lambda self, *a, **kw: BoundedProbe(original_open(self, *a, **kw)))
    result = read_live_slice(path, offset=0, limits=LiveSyncLimits(500, 8))
    assert result.records == [] and not result.has_more and result.end_offset == 0
