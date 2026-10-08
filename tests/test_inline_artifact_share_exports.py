"""Atomic export rollback, frozen bytes, stale work, and response leases."""

import asyncio
from copy import deepcopy
from threading import Event

import pytest
from asgiref.sync import sync_to_async

from twicc import paths
from twicc.core.models import Project, Session, SessionItem, Share
from twicc.core.services import share_mutation
from twicc.inline_artifacts import share_exports as exports
from twicc.inline_artifacts.share_selection import prepare_share_selection, public_inline_manifest
from twicc.providers.helpers import get_provider_helpers

KEY = '["main","widget"]'


def publication(line=10, artifact_id='widget', filename='index.html'):
    return dict(artifact_id=artifact_id, line_num=line, text_block_index=0, tag_offset=0,
                src=f'inline-artifacts/{artifact_id}/{filename}', title=artifact_id, height=360)


@pytest.fixture
def case(transactional_db, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'get_data_dir', lambda: tmp_path)
    async def unlocked(factory):
        return await factory()
    monkeypatch.setattr(share_mutation, 'run_under_db_write_lock', unlocked)
    monkeypatch.setattr(exports, 'run_under_db_write_lock', unlocked)
    session = Session.objects.create(id='main', project=Project.objects.create(id='p'), provider='claude_code',
                                     file_path='main.jsonl', last_line=20,
                                     compute_version=get_provider_helpers('claude_code').current_compute_version)
    set_publications(session, publication())
    write_source(session, b'first')
    return session


def set_publications(session, *records):
    session.inline_artifacts = {'schema': 1, 'publications': list(records)}
    session.save(update_fields=['inline_artifacts', 'last_line'])
    for record in records:
        SessionItem.objects.update_or_create(session=session, line_num=record['line_num'],
                                             defaults={'content': '{}', 'display_level': 1})


def write_source(session, content, artifact_id='widget', filename='index.html'):
    path = paths.get_data_dir() / 'artifacts' / session.id / 'inline-artifacts' / artifact_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def create(session, **options):
    result = asyncio.run(share_mutation.create_share('session', session=session,
                         options={'mode': 'snapshot', **options}))
    assert result.success, result.errors
    return Share.objects.select_related('session').get(id=result.share_id)


def leased_bytes(share, key=KEY):
    lease = exports.lease_inline_asset(share.id, key, 'index.html')
    try:
        return lease.file.read()
    finally:
        lease.file.close()
        lease.release()


def test_creation_copy_failure_leaves_no_row(case):
    write_source(case, b'first').unlink()
    result = asyncio.run(share_mutation.create_share('session', session=case, options={'mode': 'snapshot'}))
    assert not result.success
    assert Share.objects.count() == 0
    assert list(paths.get_shares_dir().glob('*/inline-artifacts/*')) == []


def test_push_failure_preserves_options_manifest_and_files(case):
    share = create(case)
    before = deepcopy((share.options, share.inline_artifact_exports))
    case.last_line = 40
    set_publications(case, publication(30, filename='missing.html'))
    result = asyncio.run(share_mutation.propagate_share(share))
    assert not result.success
    assert (share.options, share.inline_artifact_exports) == before
    share.refresh_from_db()
    assert (share.options, share.inline_artifact_exports) == before
    assert leased_bytes(share) == b'first'


def test_push_same_tag_replaces_bytes_and_code_revision(case):
    share = create(case)
    before = deepcopy(share.inline_artifact_exports)
    write_source(case, b'corrected')
    assert asyncio.run(share_mutation.propagate_share(share)).success
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['code_revision'] > before['artifacts'][KEY]['code_revision']
    assert leased_bytes(share) == b'corrected'


def test_transcript_options_leave_inline_state_unchanged(case):
    share = create(case)
    before = deepcopy(share.inline_artifact_exports)
    write_source(case, b'changed')
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'include_subagents': False,
                                                                  'show_timestamps': False}})).success
    assert share.inline_artifact_exports == before
    assert leased_bytes(share) == b'first'


def test_visibility_tombstone_restores_exact_bytes(case):
    share = create(case, max_display_mode='debug')
    SessionItem.objects.filter(session=case, line_num=10).update(display_level=3)
    write_source(case, b'changed')
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'max_display_mode': 'normal'}})).success
    assert not share.inline_artifact_exports['selected']
    assert share.inline_artifact_exports['artifacts'][KEY]['copy_id']
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'max_display_mode': 'debug'}})).success
    assert leased_bytes(share) == b'first'


def test_visibility_relaxation_copy_failure_preserves_all_state(case):
    set_publications(case, publication(), publication(15, 'other'))
    SessionItem.objects.filter(session=case, line_num=15).update(display_level=3)
    share = create(case)
    before = deepcopy((share.options, share.inline_artifact_exports))
    result = asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'max_display_mode': 'debug'}}))
    assert not result.success
    share.refresh_from_db()
    assert (share.options, share.inline_artifact_exports) == before
    assert leased_bytes(share) == b'first'


def test_retained_hidden_copies_count_toward_aggregate_limit(case, monkeypatch):
    share = create(case, max_display_mode='debug')
    set_publications(case, publication(), publication(15, 'other'))
    SessionItem.objects.filter(session=case, line_num=10).update(display_level=3)
    write_source(case, b'other', 'other')
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', 9)
    result = asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'max_display_mode': 'normal'}}))
    assert not result.success
    share.refresh_from_db()
    assert leased_bytes(share) == b'first'


def test_disable_bypasses_readiness_and_enable_replaces_within_frozen_capture(case):
    share = create(case)
    case.compute_version = 0
    case.save(update_fields=['compute_version'])
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'include_inline_artifacts': False}})).success
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)
    case.compute_version = get_provider_helpers('claude_code').current_compute_version
    set_publications(case, publication(), publication(15, filename='new.html'))
    case.save(update_fields=['compute_version'])
    write_source(case, b'current')
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'include_inline_artifacts': True}})).success
    assert share.inline_artifact_exports['selected'][KEY]['line_num'] == 10
    assert share.options['frozen_at_line'] == 20
    assert leased_bytes(share) == b'current'


def test_retirement_waits_for_response_lease(case):
    share = create(case)
    lease = exports.lease_inline_asset(share.id, KEY, 'index.html')
    old_root = exports.inline_export_root(share.id) / lease.copy_id
    write_source(case, b'new')
    assert asyncio.run(share_mutation.propagate_share(share)).success
    assert old_root.exists()
    assert lease.file.read() == b'first'
    lease.file.close()
    lease.release()
    lease.release()
    assert not old_root.exists()
    assert leased_bytes(share) == b'new'


def test_delete_denies_new_readers_and_removes_root_after_response(case):
    share = create(case)
    lease = exports.lease_inline_asset(share.id, KEY, 'index.html')
    share_id = share.id
    root = exports.inline_export_root(share_id)
    assert asyncio.run(share_mutation.delete_share(share)).success
    with pytest.raises(exports.InlineArtifactUnavailable):
        exports.lease_inline_asset(share_id, KEY, 'index.html')
    assert root.exists()
    assert lease.file.read() == b'first'
    lease.file.close()
    lease.release()
    assert not root.exists()


@pytest.mark.parametrize('action', ['disable', 'revoke', 'delete', 'new_publication'])
def test_slow_prepared_work_cannot_publish_after_invalidating_change(case, monkeypatch, action):
    share = create(case)
    entered, finish = Event(), Event()
    original_copy = exports.copy_source_artifact
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', blocked)
    async def race():
        loaded = await sync_to_async(lambda: Share.objects.select_related('session').get(id=share.id))()
        selection = await sync_to_async(prepare_share_selection)(loaded, recapture=True)
        task = asyncio.create_task(exports.prepare_inline_exports(share.id, selection, retain=loaded.inline_artifact_exports))
        assert await asyncio.to_thread(entered.wait, 5)
        if action == 'disable':
            await share_mutation.patch_share(loaded, {'options': {**loaded.options, 'include_inline_artifacts': False}})
        elif action == 'revoke':
            await share_mutation.revoke_share(loaded)
        elif action == 'delete':
            await share_mutation.delete_share(loaded)
        else:
            await sync_to_async(set_publications)(case, publication(), publication(15))
        finish.set()
        prepared = await task
        assert not await exports.commit_inline_exports(share.id, loaded.inline_artifact_exports['revision'],
                                                       prepared, loaded.options)
        return prepared
    prepared = asyncio.run(race())
    assert all(not (exports.inline_export_root(share.id) / cid).exists() for cid in prepared.new_copy_ids)


def test_legacy_initialization_records_error_and_retry_preserves_capture(case):
    share = Share.objects.create(id='shr_legacy', token='legacy', kind='session', session=case,
                                  options={'mode': 'snapshot', 'frozen_at_line': 20})
    write_source(case, b'first').unlink()
    asyncio.run(exports.ensure_inline_exports(share.id))
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['status'] == 'error'
    assert share.inline_artifact_exports['captured'][KEY]['line_num'] == 10
    set_publications(case, publication(), publication(15, filename='new.html'))
    write_source(case, b'retry-original')
    asyncio.run(exports.retry_inline_export(share.id, KEY))
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['status'] == 'ready'
    assert leased_bytes(share) == b'retry-original'
    write_source(case, b'changed-after-ready')
    asyncio.run(exports.ensure_inline_exports(share.id))
    asyncio.run(exports.retry_inline_export(share.id, KEY))
    assert leased_bytes(share) == b'retry-original'


def test_recovery_changes_only_pending_and_preserves_successful_bytes(case):
    share = create(case)
    state = deepcopy(share.inline_artifact_exports)
    other_key = '["main","other"]'
    state['captured'][other_key] = state['selected'][other_key] = publication(15, 'other')
    state['artifacts'][other_key] = {'status': 'pending', 'copy_id': None, 'code_revision': None}
    share.inline_artifact_exports = state
    share.save(update_fields=['inline_artifact_exports'])
    set_publications(case, publication(), publication(15, 'other'), publication(18, 'other', 'new.html'))
    write_source(case, b'changed')
    write_source(case, b'captured-other', 'other')
    asyncio.run(exports.recover_snapshot_initialization(share.id))
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY] == state['artifacts'][KEY]
    assert share.inline_artifact_exports['artifacts'][other_key]['error'] == 'export_interrupted'
    assert public_inline_manifest(share)['artifacts'][0]['error'] == 'export_interrupted'
    asyncio.run(exports.retry_inline_export(share.id, other_key))
    assert leased_bytes(share) == b'first'
    assert leased_bytes(share, other_key) == b'captured-other'


def test_startup_reconciliation_removes_orphans_and_unreferenced_staging(case):
    share = create(case)
    unreferenced = exports.inline_export_root(share.id) / ('0' * 32)
    unreferenced.mkdir()
    orphan = exports.inline_export_root('shr_orphan') / ('1' * 32)
    orphan.mkdir(parents=True)
    asyncio.run(exports.reconcile_inline_exports())
    assert not unreferenced.exists()
    assert not orphan.parent.exists()
    assert leased_bytes(share) == b'first'


def test_file_response_close_releases_export_after_replacement(case):
    from twicc.inline_artifacts.views import inline_asset_response
    share = create(case)
    lease = exports.lease_inline_asset(share.id, KEY, 'index.html')
    root = exports.inline_export_root(share.id) / lease.copy_id
    response = inline_asset_response(lease.file, 'application/octet-stream', as_document=False,
                                     head=False, release=lease.release)
    write_source(case, b'changed')
    assert asyncio.run(share_mutation.propagate_share(share)).success
    assert b''.join(response.streaming_content) == b'first'
    assert root.exists()
    response.close()
    response.close()
    assert not root.exists()


def test_legacy_initialization_exposes_pending_before_copy(case, monkeypatch):
    share = Share.objects.create(id='shr_pending', token='pending', kind='session', session=case,
                                  options={'mode': 'snapshot', 'frozen_at_line': 20})
    entered, finish = Event(), Event()
    original_copy = exports.copy_source_artifact
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', blocked)
    async def initialize():
        task = asyncio.create_task(exports.ensure_inline_exports(share.id))
        assert await asyncio.to_thread(entered.wait, 5)
        pending = await sync_to_async(lambda: Share.objects.get(id=share.id).inline_artifact_exports)()
        assert pending['initialized'] is True
        assert pending['artifacts'][KEY]['status'] == 'pending'
        assert pending['captured'][KEY]['line_num'] == 10
        finish.set()
        await task
    asyncio.run(initialize())
    assert leased_bytes(share) == b'first'


def test_cancelled_preparation_cleans_root_after_copy_thread_finishes(case, monkeypatch):
    share = create(case)
    entered, finish = Event(), Event()
    original_copy = exports.copy_source_artifact
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', blocked)
    before = {entry.name for entry in exports.inline_export_root(share.id).iterdir()}
    async def cancel():
        selection = await sync_to_async(prepare_share_selection)(share, recapture=True)
        task = asyncio.create_task(exports.prepare_inline_exports(share.id, selection, retain=share.inline_artifact_exports))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    assert {entry.name for entry in exports.inline_export_root(share.id).iterdir()} == before


def test_delete_during_initial_slow_copy_removes_root_after_thread_finishes(case, monkeypatch):
    share = Share.objects.create(id='shr_slow_delete', token='slowdelete', kind='session', session=case,
                                  options={'mode': 'snapshot', 'frozen_at_line': 20})
    share_id = share.id
    entered, finish = Event(), Event()
    original_copy = exports.copy_source_artifact
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', blocked)
    async def delete_during_copy():
        task = asyncio.create_task(exports.ensure_inline_exports(share_id))
        assert await asyncio.to_thread(entered.wait, 5)
        await share_mutation.delete_share(share)
        finish.set()
        await task
    asyncio.run(delete_during_copy())
    assert not exports.inline_export_root(share_id).exists()


def test_push_can_remove_last_captured_identity(case):
    share = create(case)
    SessionItem.objects.filter(session=case).delete()
    set_publications(case)
    assert asyncio.run(share_mutation.propagate_share(share)).success
    assert share.inline_artifact_exports['selected'] == {}
    assert share.inline_artifact_exports['captured'] == {}
    assert share.inline_artifact_exports['artifacts'] == {}


@pytest.mark.parametrize('target_mode', ['live', 'snapshot'])
def test_mode_change_copies_current_bytes_even_without_new_tag(case, target_mode):
    share = create(case, mode='snapshot' if target_mode == 'live' else 'live')
    before = deepcopy(share.inline_artifact_exports)
    write_source(case, b'current')
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'mode': target_mode}})).success
    assert leased_bytes(share) == b'current'
    assert share.inline_artifact_exports['artifacts'][KEY]['code_revision'] > before['artifacts'][KEY]['code_revision']


def test_failed_mode_change_keeps_old_options_and_copy(case):
    share = create(case, mode='live')
    before = deepcopy((share.options, share.inline_artifact_exports))
    write_source(case, b'first').unlink()
    result = asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'mode': 'snapshot'}}))
    assert not result.success
    share.refresh_from_db()
    assert (share.options, share.inline_artifact_exports) == before
    assert leased_bytes(share) == b'first'


def test_exact_aggregate_budget_includes_saved_data(case, monkeypatch):
    write_source(case, b'data', filename='data/state.json')
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', 9)
    share = create(case)
    assert share.inline_artifact_exports['artifacts'][KEY]['byte_size'] == 9
    lease = exports.lease_inline_asset(share.id, KEY, 'data/state.json')
    try:
        assert lease.file.read() == b'data'
    finally:
        lease.file.close()
        lease.release()


def test_nested_source_symlink_aborts_replacement_and_preserves_old_bytes(case, tmp_path):
    share = create(case)
    before = deepcopy((share.options, share.inline_artifact_exports))
    outside = tmp_path / 'outside'
    outside.write_bytes(b'secret')
    source = write_source(case, b'changed')
    (source.parent / 'linked.js').symlink_to(outside)
    result = asyncio.run(share_mutation.propagate_share(share))
    assert not result.success
    share.refresh_from_db()
    assert (share.options, share.inline_artifact_exports) == before
    assert leased_bytes(share) == b'first'


def test_configured_global_artifacts_symlink_allows_export(case, tmp_path):
    artifacts = paths.get_data_dir() / 'artifacts'
    moved = tmp_path / 'configured-artifacts'
    artifacts.rename(moved)
    artifacts.symlink_to(moved, target_is_directory=True)
    assert leased_bytes(create(case)) == b'first'


def test_reenable_failure_preserves_disabled_capture(case):
    share = create(case)
    assert asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'include_inline_artifacts': False}})).success
    before = deepcopy((share.options, share.inline_artifact_exports))
    write_source(case, b'first').unlink()
    result = asyncio.run(share_mutation.patch_share(share, {'options': {**share.options, 'include_inline_artifacts': True}}))
    assert not result.success
    share.refresh_from_db()
    assert (share.options, share.inline_artifact_exports) == before
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)


def test_live_retry_uses_current_publication_instead_of_stale_failed_tag(case):
    share = create(case, mode='live')
    state = deepcopy(share.inline_artifact_exports)
    state['artifacts'][KEY] = dict(status='error', copy_id=None, code_revision=None, error='export_failed')
    share.inline_artifact_exports = state
    share.save(update_fields=['inline_artifact_exports'])
    set_publications(case, publication(), publication(15, filename='current.html'))
    write_source(case, b'current-live', filename='current.html')
    asyncio.run(exports.retry_inline_export(share.id, KEY))
    share.refresh_from_db()
    assert share.inline_artifact_exports['selected'][KEY]['line_num'] == 15
    assert share.inline_artifact_exports['artifacts'][KEY]['status'] == 'ready'
    lease = exports.lease_inline_asset(share.id, KEY, 'current.html')
    try:
        assert lease.file.read() == b'current-live'
    finally:
        lease.file.close()
        lease.release()


def test_failed_retry_cannot_publish_after_revoke_and_restore(case, monkeypatch):
    share = Share.objects.create(id='shr_retry_race', token='retryrace', kind='session', session=case,
                                  options={'mode': 'snapshot', 'frozen_at_line': 20})
    write_source(case, b'first').unlink()
    asyncio.run(exports.ensure_inline_exports(share.id))
    share.refresh_from_db()
    before = deepcopy(share.inline_artifact_exports)
    entered, finish = Event(), Event()
    original_copy = exports.copy_source_artifact
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', blocked)
    async def race():
        task = asyncio.create_task(exports.retry_inline_export(share.id, KEY))
        assert await asyncio.to_thread(entered.wait, 5)
        await share_mutation.revoke_share(share)
        await share_mutation.revoke_share(share, revoked=False)
        finish.set()
        await task
    asyncio.run(race())
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
