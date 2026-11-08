"""Compressed sources retain migration gates and list-based rebuild jobs."""

import asyncio
from types import SimpleNamespace

import orjson
import pytest
import zstandard

from twicc.core.content_storage.readers import aload_current, effective_current
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers import db_writer
from twicc.providers.codex import background_compute, migration_gate, rollout_migration
from twicc.providers.codex.background_compute import (
    CodexComputeCandidate, CodexComputeCoordinator, FailedCandidate, PreparedCandidate,
)
from twicc.providers.codex.rollout_migration import CodexMigrationOutcome, RolloutMigrationError


def _content(session_id, *, paginated=False):
    payload = {'id': session_id, 'cwd': '/repo'}
    if paginated:
        payload['history_mode'] = 'paginated'
    return b'\n'.join([
        orjson.dumps({'type': 'session_meta', 'payload': payload}),
        orjson.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'x' * 2000}}),
    ]) + b'\n'


def _write_compressed(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zstandard.ZstdCompressor().compress(content))


@pytest.fixture
def runtime(provider_home, monkeypatch):
    monkeypatch.setattr(migration_gate, '_rebuild_requests', set())
    monkeypatch.setattr(migration_gate, '_migrating', set())
    monkeypatch.setattr(background_compute.search, 'is_initialized', lambda: False)
    return provider_home.codex / 'sessions'


def _session(root, sid, *, disk_paginated=False, database_paginated=False, offset=None, reason=None):
    plain = root / '2026' / f'rollout-{sid}.jsonl'
    compressed = plain.with_suffix('.jsonl.zst')
    content = _content(sid, paginated=disk_paginated)
    _write_compressed(compressed, content)
    session = Session.objects.create(
        id=sid, project=Project.objects.create(id=sid), provider=Provider.CODEX,
        file_path=str(compressed.relative_to(root)), last_line=2,
        last_offset=len(content) if offset is None else offset,
        unavailable_reason=reason,
    )
    if offset != 0:
        for line, record in enumerate(_content(sid, paginated=database_paginated).splitlines(), 1):
            SessionItem.objects.create(session=session, line_num=line, content=record.decode())
    return session, plain, compressed, content


async def _prepare(session, path, runner=None):
    owner = CodexComputeCoordinator(SimpleNamespace(compute_version=2, stop_event=asyncio.Event()), asyncio.Event())
    owner._is_agent_active = lambda _sid: False
    if runner is not None:
        owner.runner.run = runner
    jobs = []
    submit = owner._submit_job

    async def track(job_type, *args):
        jobs.append((job_type.__name__, args))
        return await submit(job_type, *args)

    owner._submit_job = track
    db_writer.start_db_writer()
    try:
        result = await owner.prepare_candidate(CodexComputeCandidate(
            session.id, path, SessionType.SESSION, session.last_offset, session.unavailable_reason,
        ))
        items = [item async for item in SessionItem.objects.filter(session=session).order_by('line_num')]
        await aload_current(items)
        return result, jobs, [effective_current(item) for item in items]
    finally:
        owner._release_lease(session.id, replay=False)
        await db_writer.stop_db_writer()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('output_compressed', [False, True])
def test_compressed_legacy_rollout_uses_existing_migration_and_rebuild(runtime, output_compressed):
    session, plain, compressed, _ = _session(runtime, 'legacy')
    canonical = _content(session.id, paginated=True)

    async def migrate(sid, path):
        assert sid == session.id and path == compressed
        assert migration_gate.is_migrating(sid)
        assert migration_gate.gate_for(sid).locked()
        if output_compressed:
            _write_compressed(compressed, canonical)
        else:
            plain.write_bytes(canonical)
            compressed.unlink()
        return CodexMigrationOutcome('migrated', len(canonical), None)

    result, jobs, items = asyncio.run(_prepare(session, compressed, migrate))
    assert isinstance(result, PreparedCandidate), result
    assert result.kind == 'migrated'
    assert items == [record.decode() for record in canonical.splitlines()]
    replacement = [args for name, args in jobs if name == 'ReplaceCodexHistoryJob']
    assert len(replacement) == 1 and isinstance(replacement[0][1], list)
    assert replacement[0][2:4] == (len(canonical), 2)
    session.refresh_from_db()
    assert session.file_path == str((compressed if output_compressed else plain).relative_to(runtime))
    assert session.history_epoch == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("restore_during", ["registration", "first_migration"])
def test_registration_resume_restores_before_migration_retry(runtime, monkeypatch, restore_during):
    session, plain, compressed, _ = _session(runtime, 'registration')
    calls = []

    async def migrate(sid, path):
        calls.append(path)
        if len(calls) == 1:
            if restore_during == "first_migration":
                plain.write_bytes(_content(sid))
                compressed.unlink()
            return CodexMigrationOutcome('failed', 0, None, 'missing_sqlite_metadata')
        assert path == plain and path.exists()
        plain.write_bytes(_content(sid, paginated=True))
        return CodexMigrationOutcome('migrated', 0, None)

    async def register(sid, path):
        assert path == (compressed if restore_during == "registration" else plain)
        if restore_during == "registration":
            plain.write_bytes(_content(sid))
            compressed.unlink()

    monkeypatch.setattr(background_compute, 'register_thread_with_codex', register)
    result, _, items = asyncio.run(_prepare(session, compressed, migrate))
    assert isinstance(result, PreparedCandidate), result
    assert result.registered
    assert calls == [compressed, plain]
    assert len(items) == 2
    session.refresh_from_db()
    assert session.file_path == str(plain.relative_to(runtime))


@pytest.mark.parametrize('reported_suffix,expected_suffix', [('.jsonl', '.jsonl.zst'), ('.jsonl.zst', '.jsonl')])
def test_sibling_migration_report_is_accepted(tmp_path, reported_suffix, expected_suffix):
    expected = tmp_path / ('rollout-source' + expected_suffix)
    reported = tmp_path / ('rollout-source' + reported_suffix)
    report = {'outcomes': [{'thread_id': 'source', 'rollout_path': str(reported),
                           'status': 'migrated', 'bytes_processed': 12}]}
    assert rollout_migration.parse_migration_report(orjson.dumps(report), 'source', expected).status == 'migrated'
    report['outcomes'][0]['thread_id'] = 'other'
    with pytest.raises(RolloutMigrationError, match='thread id'):
        rollout_migration.parse_migration_report(orjson.dumps(report), 'source', expected)
    report['outcomes'][0]['thread_id'] = 'source'
    report['outcomes'][0]['rollout_path'] = str(tmp_path / 'other.jsonl')
    with pytest.raises(RolloutMigrationError, match='rollout path'):
        rollout_migration.parse_migration_report(orjson.dumps(report), 'source', expected)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('offset_zero', [False, True])
def test_paginated_compressed_input_uses_logical_size_and_recovers_interrupted_rebuild(runtime, offset_zero, monkeypatch):
    session, _, compressed, content = _session(
        runtime, 'paginated', disk_paginated=True, database_paginated=True,
        offset=0 if offset_zero else None,
    )
    prepared = []
    from twicc.providers.codex.rollout_sources import prepare_rollout

    def prepare(path, **kwargs):
        source = prepare_rollout(path, **kwargs)
        prepared.append(source.read_path)
        return source

    monkeypatch.setattr(background_compute, 'prepare_rollout', prepare, raising=False)

    async def never_migrate(*_args):
        raise AssertionError('paginated input must not invoke Codex')

    result, jobs, items = asyncio.run(_prepare(session, compressed, never_migrate))
    assert isinstance(result, PreparedCandidate), result
    assert result.kind == ('replaced' if offset_zero else 'compute')
    assert any(name == 'ReplaceCodexHistoryJob' for name, _ in jobs) == offset_zero
    assert items == [record.decode() for record in content.splitlines()]
    assert len(prepared) == 1
    assert all(not path.exists() for path in prepared)
    session.refresh_from_db()
    assert session.last_offset == len(content)
    assert session.history_epoch == int(offset_zero)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('reason', ['rollout_missing', 'codex_migration_failed:rollout_read_failed'])
def test_missing_plain_association_recovers_compressed_sibling_and_preserves_other_diagnoses(runtime, reason):
    session, plain, compressed, _ = _session(
        runtime, 'recovery', disk_paginated=True, database_paginated=True, reason=reason,
    )
    session.file_path = str(plain.relative_to(runtime))
    session.stale = True
    session.save(update_fields=['file_path', 'stale'])
    result, jobs, _ = asyncio.run(_prepare(session, plain))
    assert isinstance(result, PreparedCandidate), result
    assert result.kind == 'compute'
    assert all(name != 'ReplaceCodexHistoryJob' for name, _ in jobs)
    session.refresh_from_db()
    assert session.file_path == str(compressed.relative_to(runtime))
    assert not session.stale
    assert session.unavailable_reason == (None if reason == 'rollout_missing' else reason)


@pytest.mark.django_db(transaction=True)
def test_corrupt_compressed_rewrite_keeps_existing_history(runtime):
    session, plain, compressed, content = _session(runtime, 'corrupt')

    async def migrate(sid, path):
        _write_compressed(compressed, _content(sid, paginated=True))
        compressed.write_bytes(compressed.read_bytes()[:-1])
        return CodexMigrationOutcome('migrated', 0, None)

    result, jobs, items = asyncio.run(_prepare(session, compressed, migrate))
    assert isinstance(result, FailedCandidate)
    assert all(name != 'ReplaceCodexHistoryJob' for name, _ in jobs)
    assert items == [record.decode() for record in content.splitlines()]
    session.refresh_from_db()
    assert session.history_epoch == 0


@pytest.mark.django_db(transaction=True)
def test_plain_migration_retains_existing_reader_and_job_protocol(runtime, monkeypatch):
    session, plain, compressed, content = _session(runtime, 'plain')
    plain.write_bytes(content)
    compressed.unlink()
    session.file_path = str(plain.relative_to(runtime))
    session.save(update_fields=['file_path'])

    def never_prepare(*_args, **_kwargs):
        raise AssertionError('ordinary JSONL must retain its direct reader')

    monkeypatch.setattr(background_compute, 'prepare_rollout', never_prepare, raising=False)
    async def migrate(sid, path):
        assert path == plain
        path.write_bytes(_content(sid, paginated=True))
        return CodexMigrationOutcome('migrated', 0, None)

    result, jobs, items = asyncio.run(_prepare(session, plain, migrate))
    assert isinstance(result, PreparedCandidate), result
    assert [name for name, _ in jobs] == ['CaptureSnapshotAnchorsJob', 'ReplaceCodexHistoryJob']
    assert isinstance(jobs[-1][1][1], list)
    assert len(items) == 2


@pytest.mark.django_db(transaction=True)
def test_source_representation_changes_while_waiting_for_gate(runtime, monkeypatch):
    session, plain, compressed, content = _session(runtime, 'gate-transition', disk_paginated=True)
    plain.write_bytes(content)
    compressed.unlink()
    session.file_path = str(plain.relative_to(runtime))
    session.save(update_fields=['file_path'])
    original_mode = CodexComputeCoordinator._source_mode

    async def scenario():
        classified = asyncio.Event()
        lease = migration_gate.gate_for(session.id)
        await lease.acquire()

        async def source_mode(owner, path, sid, read_path=None):
            mode = await original_mode(owner, path, sid, read_path)
            classified.set()
            return mode

        monkeypatch.setattr(CodexComputeCoordinator, '_source_mode', source_mode)
        task = asyncio.create_task(_prepare(session, plain))
        await asyncio.wait_for(classified.wait(), timeout=5)
        _write_compressed(compressed, content)
        plain.unlink()
        lease.release()
        return await task

    result, _, items = asyncio.run(scenario())
    assert isinstance(result, PreparedCandidate), result
    assert result.kind == 'replaced'
    assert items == [record.decode() for record in content.splitlines()]
    session.refresh_from_db()
    assert session.file_path == str(compressed.relative_to(runtime))


@pytest.mark.django_db(transaction=True)
def test_source_representation_changes_before_initial_classification(runtime, monkeypatch):
    session, plain, compressed, content = _session(
        runtime, 'initial-transition', disk_paginated=True, database_paginated=True,
    )
    plain.write_bytes(content)
    compressed.unlink()
    session.file_path = str(plain.relative_to(runtime))
    session.save(update_fields=['file_path'])
    original_mode = CodexComputeCoordinator._source_mode
    selected = []

    async def source_mode(owner, path, sid, read_path=None):
        selected.append((path, read_path))
        if len(selected) == 1:
            _write_compressed(compressed, content)
            plain.unlink()
        return await original_mode(owner, path, sid, read_path)

    monkeypatch.setattr(CodexComputeCoordinator, '_source_mode', source_mode)
    result, jobs, items = asyncio.run(_prepare(session, plain))

    assert isinstance(result, PreparedCandidate), result
    assert result.kind == 'compute'
    assert selected[0] == (plain, None)
    assert selected[1][0] == compressed
    assert selected[1][1] is not None
    assert not selected[1][1].exists()
    assert all(name != 'MarkSessionUnavailableJob' for name, _ in jobs)
    assert all(name != 'ReplaceCodexHistoryJob' for name, _ in jobs)
    assert items == [record.decode() for record in content.splitlines()]
    session.refresh_from_db()
    assert session.file_path == str(compressed.relative_to(runtime))
    assert session.unavailable_reason is None
    assert session.history_epoch == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('change', ['none', 'malformed', 'missing'])
def test_initial_plain_classification_preserves_existing_diagnoses(runtime, monkeypatch, change):
    session, plain, compressed, content = _session(
        runtime, 'plain-classification', disk_paginated=True, database_paginated=True,
    )
    plain.write_bytes(content)
    compressed.unlink()
    session.file_path = str(plain.relative_to(runtime))
    session.save(update_fields=['file_path'])
    original_mode = CodexComputeCoordinator._source_mode
    original_resolve = background_compute.resolve_rollout
    resolutions = []

    async def source_mode(owner, path, sid, read_path=None):
        if change == 'missing':
            plain.unlink()
        elif change == 'malformed':
            plain.write_bytes(b'not-json\n')
        return await original_mode(owner, path, sid, read_path)

    def resolve(path):
        resolutions.append(path)
        return original_resolve(path)

    monkeypatch.setattr(CodexComputeCoordinator, '_source_mode', source_mode)
    monkeypatch.setattr(background_compute, 'resolve_rollout', resolve)
    result, jobs, items = asyncio.run(_prepare(session, plain))

    if change == 'none':
        assert isinstance(result, PreparedCandidate), result
        assert result.kind == 'compute'
    else:
        assert isinstance(result, FailedCandidate), result
        assert result.unavailable_reason == ('rollout_missing' if change == 'missing' else None)
    assert bool(resolutions) == (change == 'missing')
    assert any(name == 'MarkSessionUnavailableJob' for name, _ in jobs) == (change == 'missing')
    assert items == [record.decode() for record in content.splitlines()]
