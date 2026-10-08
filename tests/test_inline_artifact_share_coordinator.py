"""Committed live publications and independent saved-data replacement."""

import asyncio
from copy import deepcopy
from threading import Event

import pytest
from asgiref.sync import sync_to_async

from tests.test_inline_artifact_share_exports import (KEY, case, create, leased_bytes, publication, set_publications,
                                                     write_source)
from twicc.core.models import Share
from twicc.inline_artifacts import share_exports as exports
from twicc.inline_artifacts.share_selection import public_inline_manifest


def read_asset(share, name):
    lease = exports.lease_inline_asset(share.id, KEY, name)
    try:
        return lease.file.read()
    finally:
        lease.file.close()
        lease.release()


def test_live_publication_copies_once_without_viewer(case, monkeypatch):
    share = create(case, mode='live')
    write_source(case, b'published second')
    set_publications(case, publication(15))
    copies = []
    original = exports.copy_source_artifact
    def counted(*args):
        copies.append(args[1])
        return original(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', counted)
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator.reconcile(share.id)
        await coordinator.reconcile(share.id)
    asyncio.run(scenario())
    share.refresh_from_db()
    assert leased_bytes(share) == b'published second'
    assert share.inline_artifact_exports['selected'][KEY]['line_num'] == 15
    assert len(copies) == 1


def test_data_refresh_preserves_published_code_and_revision(case):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'settings.json'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    before = deepcopy(share.inline_artifact_exports['artifacts'][KEY])
    source.write_bytes(b'unpublished')
    data.write_bytes(b'new')
    asyncio.run(exports.refresh_export_data(share.id, KEY, ['data/settings.json']))
    share.refresh_from_db()
    assert leased_bytes(share) == b'published'
    assert read_asset(share, 'data/settings.json') == b'new'
    assert share.inline_artifact_exports['artifacts'][KEY]['code_revision'] == before['code_revision']
    data.unlink()
    asyncio.run(exports.refresh_export_data(share.id, KEY, ['data/settings.json']))
    with pytest.raises(exports.InlineArtifactUnavailable):
        read_asset(share, 'data/settings.json')


def test_data_failure_keeps_last_complete_copy(case, monkeypatch):
    source = write_source(case, b'code')
    data = source.parent / 'data' / 'settings.json'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    before = deepcopy(share.inline_artifact_exports['artifacts'][KEY])
    data.write_bytes(b'exceeds limit')
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', before['byte_size'])
    asyncio.run(exports.refresh_export_data(share.id, KEY, ['data/settings.json']))
    share.refresh_from_db()
    entry = share.inline_artifact_exports['artifacts'][KEY]
    assert entry['status'] == 'ready'
    assert entry['copy_id'] == before['copy_id']
    assert entry['code_revision'] == before['code_revision']
    assert entry['data_error'] == 'export_failed'
    assert read_asset(share, 'data/settings.json') == b'old'
    assert 'data_error' not in public_inline_manifest(share)['artifacts'][0]
    from twicc.core.serializers import serialize_share
    assert serialize_share(share)['inline_artifact_data_errors'] == [{'artifact_id': 'widget', 'error': 'export_failed'}]


def test_dirty_data_during_copy_gets_later_pass(case, monkeypatch):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    entered, resume = Event(), Event()
    original = exports.copy_export_with_source_data
    calls = []
    def paused(*args):
        result = original(*args)
        calls.append(result)
        if len(calls) == 1:
            entered.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        assert await asyncio.to_thread(entered.wait, 5)
        data.write_bytes(b'latest')
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        resume.set()
        await coordinator.stop()
    asyncio.run(scenario())
    assert read_asset(share, 'data/value') == b'latest'
    assert leased_bytes(share) == b'published'
    assert len(calls) >= 2


def test_disabled_during_queued_copy_cannot_publish(case, monkeypatch):
    share = create(case, mode='live')
    set_publications(case, publication(15))
    entered, resume = Event(), Event()
    original = exports.copy_source_artifact
    def paused(*args):
        entered.set()
        assert resume.wait(5)
        return original(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', paused)
    async def scenario():
        task = asyncio.create_task(exports.InlineExportCoordinator().reconcile(share.id))
        assert await asyncio.to_thread(entered.wait, 5)
        exports.invalidate_inline_exports(share.id)
        await sync_to_async(Share.objects.filter(id=share.id).update)(options={'mode': 'live',
                                                                                 'include_inline_artifacts': False})
        resume.set()
        await task
    asyncio.run(scenario())
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['status'] == 'pending'
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)


def test_publication_enqueue_does_not_recopy_ready_data(case, monkeypatch):
    share = create(case, mode='live')
    calls = []
    async def unexpected(*args):
        calls.append(args)
    monkeypatch.setattr(exports, '_refresh_data_locked', unexpected)
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator._load_live_shares()
        coordinator.publication_changed(case.id)
        paths = coordinator._pending[case.id]
        await coordinator._reconcile(share.id, paths, expected_generation=coordinator._reservations[share.id])
    asyncio.run(scenario())
    assert calls == []


def test_queued_work_reservation_cannot_resume_after_invalidation(case):
    share = create(case, mode='live')
    write_source(case, b'new')
    set_publications(case, publication(15))
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator._load_live_shares()
        coordinator.publication_changed(case.id)
        generation = coordinator._reservations[share.id]
        exports.invalidate_inline_exports(share.id)
        await coordinator._reconcile(share.id, coordinator._pending[case.id], expected_generation=generation)
    asyncio.run(scenario())
    share.refresh_from_db()
    assert share.inline_artifact_exports['selected'][KEY]['line_num'] == 10
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)


def test_registration_recovers_events_before_new_share_exists(case):
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        # No share exists while the source event is processed.
        await asyncio.sleep(0)
        from twicc.core.services.share_mutation import create_share
        result = await create_share('session', session=case, options={'mode': 'live'})
        assert result.success
        share = await sync_to_async(Share.objects.select_related('session').get)(id=result.share_id)
        coordinator.register_share(share.id, case.id, share.options, supported=True, active=True)
        assert coordinator._pending[case.id] is None
        queued_generation = coordinator._reservations[share.id]
        coordinator.register_share(share.id, case.id, {**share.options, 'include_subagents': False},
                                   supported=True, active=True)
        assert coordinator._reservations[share.id] == queued_generation
        await coordinator.stop()
    asyncio.run(scenario())


def test_restart_keeps_frozen_snapshot_and_published_live_code(case):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    live = create(case, mode='live')
    frozen = create(case, mode='snapshot')
    frozen_state = deepcopy(frozen.inline_artifact_exports)
    source.write_bytes(b'unpublished')
    data.write_bytes(b'latest')
    async def scenario():
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        await coordinator.stop()
    asyncio.run(scenario())
    frozen.refresh_from_db()
    assert frozen.inline_artifact_exports == frozen_state
    assert read_asset(frozen, 'data/value') == b'old'
    assert leased_bytes(live) == b'published'
    assert read_asset(live, 'data/value') == b'latest'


def test_retired_copy_waits_for_old_data_lease(case):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    lease = exports.lease_inline_asset(share.id, KEY, 'data/value')
    old_root = exports.inline_export_root(share.id) / lease.copy_id
    data.write_bytes(b'latest')
    asyncio.run(exports.refresh_export_data(share.id, KEY, []))
    assert read_asset(share, 'data/value') == b'latest'
    assert lease.file.read() == b'old'
    assert old_root.exists()
    lease.file.close()
    lease.release()
    assert not old_root.exists()


def test_data_failure_clears_after_success(case, monkeypatch):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    original_limit = exports.MAX_INLINE_EXPORT_BYTES
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', 0)
    asyncio.run(exports.refresh_export_data(share.id, KEY, []))
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['data_error'] == 'export_failed'
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', original_limit)
    data.write_bytes(b'latest')
    asyncio.run(exports.refresh_export_data(share.id, KEY, []))
    share.refresh_from_db()
    assert 'data_error' not in share.inline_artifact_exports['artifacts'][KEY]
    assert read_asset(share, 'data/value') == b'latest'


def test_data_symlink_failure_does_not_copy_unpublished_code(case):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    source.write_bytes(b'unpublished')
    data.unlink()
    data.symlink_to(source)
    asyncio.run(exports.refresh_export_data(share.id, KEY, []))
    assert read_asset(share, 'data/value') == b'old'
    assert leased_bytes(share) == b'published'


def test_data_refresh_counts_unselected_retained_copies(case, monkeypatch):
    share = create(case, mode='live')
    cid = '1' * 32
    retained = exports.inline_export_root(share.id) / cid
    retained.mkdir()
    (retained / 'hidden').write_bytes(b'x' * 50)
    state = deepcopy(share.inline_artifact_exports)
    state['artifacts']['hidden'] = {'status': 'ready', 'copy_id': cid, 'byte_size': 1, 'code_revision': 1}
    share.inline_artifact_exports = state
    share.save(update_fields=['inline_artifact_exports'])
    monkeypatch.setattr(exports, 'MAX_INLINE_EXPORT_BYTES', 52)
    asyncio.run(exports.refresh_export_data(share.id, KEY, []))
    share.refresh_from_db()
    assert share.inline_artifact_exports['artifacts'][KEY]['data_error'] == 'export_failed'
    assert retained.exists()
    assert leased_bytes(share) == b'first'


def test_repeated_cancel_waits_for_data_worker_before_cleanup(case, monkeypatch):
    share = create(case, mode='live')
    entered, resume = Event(), Event()
    original = exports.copy_export_with_source_data
    def paused(*args):
        entered.set()
        assert resume.wait(5)
        return original(*args)
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    before = deepcopy(share.inline_artifact_exports)
    async def scenario():
        task = asyncio.create_task(exports.refresh_export_data(share.id, KEY, []))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
    assert {item.name for item in exports.inline_export_root(share.id).iterdir()} == exports._copy_ids(before)


def test_coalesced_queue_is_bounded_and_preserves_latest_work():
    coordinator = exports.InlineExportCoordinator()
    for number in range(coordinator.MAX_PENDING_SESSIONS + 50):
        coordinator.files_changed(f'session-{number}', ['inline-artifacts/widget/data/value'])
    assert len(coordinator._pending) == coordinator.MAX_PENDING_SESSIONS
    assert coordinator._sweep
    for number in range(coordinator.MAX_PENDING_PATHS + 5):
        coordinator.files_changed('session-0', [f'inline-artifacts/widget/data/value-{number}'])
    assert coordinator._pending['session-0'] is None


def test_current_ineligibility_denies_old_copy_before_worker(case):
    from twicc.core.models import SessionItem
    share = create(case, mode='live')
    SessionItem.objects.filter(session=case, line_num=10).update(display_level=3)
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)


def test_new_tag_denies_previous_copy_before_pending_transition(case):
    share = create(case, mode='live')
    set_publications(case, publication(15))
    with pytest.raises(exports.InlineArtifactUnavailable):
        leased_bytes(share)


def test_frozen_captured_tag_keeps_access_after_new_owner_tag(case):
    share = create(case, mode='snapshot')
    write_source(case, b'unpublished')
    set_publications(case, publication(15))
    assert leased_bytes(share) == b'first'


def test_artifact_watcher_enqueues_first_tick_without_orm(tmp_path, monkeypatch):
    from twicc import artifacts_watcher
    from watchfiles import Change
    root = tmp_path / 'artifacts'
    source = root / 'unknown' / 'inline-artifacts' / 'widget' / 'data' / 'value'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'value')
    monkeypatch.setattr(artifacts_watcher, 'get_artifacts_dir', lambda: root)
    seen, owner = [], []
    watcher = artifacts_watcher.ArtifactsWatcher(enqueue=lambda sid, paths: seen.append((sid, paths)))
    async def watch(*args, **kwargs):
        yield {(Change.added, str(source))}
        source.unlink()
        yield {(Change.deleted, str(source))}
    async def available(sid):
        owner.append(('available', sid))
    async def changed(sid, paths):
        owner.append(('changed', sid, paths))
    monkeypatch.setattr(artifacts_watcher, 'awatch', watch)
    monkeypatch.setattr(watcher, '_broadcast_available', available)
    monkeypatch.setattr(watcher, '_broadcast_files_changed', changed)
    asyncio.run(watcher._watch_loop())
    expected = ('unknown', ['inline-artifacts/widget/data/value'])
    assert seen == [expected, expected]
    assert owner == [('available', 'unknown'), ('changed', *expected)]
    assert watcher.has('unknown')


def test_committed_publications_emit_pending_then_ready_manifest(case):
    from channels.layers import get_channel_layer
    share = create(case, mode='live')
    before_code = share.inline_artifact_exports['artifacts'][KEY]['code_revision']
    write_source(case, b'published second')
    set_publications(case, publication(15))
    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel('inline-test')
        await layer.group_add('updates', channel)
        try:
            await exports.InlineExportCoordinator().reconcile(share.id)
            manifests = []
            for _ in range(4):
                event = await asyncio.wait_for(layer.receive(channel), 2)
                if event['data']['type'] == 'share_inline_artifacts':
                    assert set(event['data']) == {'type', 'share_id', 'manifest'}
                    manifests.append(event['data']['manifest'])
            return manifests
        finally:
            await layer.group_discard('updates', channel)
    manifests = asyncio.run(scenario())
    assert [manifest['artifacts'][0]['status'] for manifest in manifests] == ['pending', 'ready']
    assert manifests[1]['revision'] > manifests[0]['revision']
    assert manifests[1]['artifacts'][0]['code_revision'] > before_code


def test_intermediate_code_file_events_keep_copy_and_revision(case):
    source = write_source(case, b'published')
    share = create(case, mode='live')
    before = deepcopy(share.inline_artifact_exports)
    source.write_bytes(b'unpublished')
    asyncio.run(exports.InlineExportCoordinator()._reconcile(share.id, {'inline-artifacts/widget/index.html'}))
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
    assert leased_bytes(share) == b'published'


@pytest.mark.parametrize('action', ['disable', 'ineligible', 'revoke_restore'])
def test_access_change_during_data_copy_rejects_late_result(case, monkeypatch, action):
    from twicc.core.models import SessionItem
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    before = deepcopy(share.inline_artifact_exports)
    entered, resume = Event(), Event()
    original = exports.copy_export_with_source_data
    def paused(*args):
        result = original(*args)
        entered.set()
        assert resume.wait(5)
        return result
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    data.write_bytes(b'new')
    async def scenario():
        task = asyncio.create_task(exports.refresh_export_data(share.id, KEY, []))
        assert await asyncio.to_thread(entered.wait, 5)
        exports.invalidate_inline_exports(share.id)
        if action == 'disable':
            await sync_to_async(Share.objects.filter(id=share.id).update)(
                options={**share.options, 'include_inline_artifacts': False})
        elif action == 'ineligible':
            await sync_to_async(SessionItem.objects.filter(session=case, line_num=10).update)(display_level=3)
        else:
            from django.utils import timezone
            await sync_to_async(Share.objects.filter(id=share.id).update)(revoked_at=timezone.now())
            await sync_to_async(Share.objects.filter(id=share.id).update)(revoked_at=None)
        resume.set()
        await task
    asyncio.run(scenario())
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
    cid = before['artifacts'][KEY]['copy_id']
    assert (exports.inline_export_root(share.id) / cid / 'data' / 'value').read_bytes() == b'old'
    assert {item.name for item in exports.inline_export_root(share.id).iterdir()} == {cid}


@pytest.mark.parametrize('unrelated_event', ['html', 'publication'])
def test_unrelated_event_preserves_inflight_data_requirement(case, monkeypatch, unrelated_event):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    before_revision = share.inline_artifact_exports['artifacts'][KEY]['code_revision']
    entered, resume = Event(), Event()
    copied = []
    original_copy = exports.copy_export_with_source_data
    def paused(*args):
        result = original_copy(*args)
        copied.append(result)
        if len(copied) == 2:
            entered.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    async def scenario():
        startup_done = asyncio.Event()
        original_reconcile = exports.InlineExportCoordinator._reconcile
        async def observed(*args, **kwargs):
            result = await original_reconcile(*args, **kwargs)
            startup_done.set()
            return result
        monkeypatch.setattr(exports.InlineExportCoordinator, '_reconcile', observed)
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        await asyncio.wait_for(startup_done.wait(), 5)
        data.write_bytes(b'new')
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        assert await asyncio.to_thread(entered.wait, 5)
        source.write_bytes(b'unpublished')
        if unrelated_event == 'html':
            coordinator.files_changed(case.id, ['inline-artifacts/widget/index.html'])
        else:
            coordinator.publication_changed(case.id)
        resume.set()
        await coordinator.stop()
    asyncio.run(scenario())
    share.refresh_from_db()
    assert read_asset(share, 'data/value') == b'new'
    assert leased_bytes(share) == b'published'
    assert share.inline_artifact_exports['artifacts'][KEY]['code_revision'] == before_revision
    assert len(copied) >= 3


@pytest.mark.parametrize('publication_first', [True, False])
def test_unrelated_file_coalescing_keeps_new_publication_work(case, monkeypatch, publication_first):
    set_publications(case, publication(), publication(15, 'other'))
    write_source(case, b'published other', artifact_id='other')
    share = create(case, mode='live')
    copies = []
    original_copy = exports.copy_source_artifact
    def counted(*args):
        copies.append(args[1]['artifact_id'])
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', counted)
    async def scenario():
        startup_done = asyncio.Event()
        original_reconcile = exports.InlineExportCoordinator._reconcile
        async def observed(*args, **kwargs):
            result = await original_reconcile(*args, **kwargs)
            startup_done.set()
            return result
        monkeypatch.setattr(exports.InlineExportCoordinator, '_reconcile', observed)
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        await asyncio.wait_for(startup_done.wait(), 5)
        write_source(case, b'published replacement')
        await sync_to_async(set_publications)(case, publication(), publication(15, 'other'), publication(18))
        if publication_first:
            coordinator.publication_changed(case.id)
            coordinator.files_changed(case.id, ['inline-artifacts/other/index.html'])
        else:
            coordinator.files_changed(case.id, ['inline-artifacts/other/index.html'])
            coordinator.publication_changed(case.id)
        await coordinator.stop()
    asyncio.run(scenario())
    share.refresh_from_db()
    assert share.inline_artifact_exports['selected'][KEY]['line_num'] == 18
    assert share.inline_artifact_exports['artifacts'][KEY]['status'] == 'ready'
    assert leased_bytes(share) == b'published replacement'
    assert copies == ['widget']


def test_unrelated_file_event_does_not_retry_unchanged_error(case, monkeypatch):
    set_publications(case, publication(), publication(15, 'other'))
    write_source(case, b'published other', artifact_id='other')
    share = create(case, mode='live')
    set_publications(case, publication(18, filename='replacement.html'), publication(15, 'other'))
    asyncio.run(exports.InlineExportCoordinator().reconcile(share.id))
    share.refresh_from_db()
    before = deepcopy(share.inline_artifact_exports)
    assert before['artifacts'][KEY]['status'] == 'error'
    write_source(case, b'unpublished replacement', filename='replacement.html')
    copies = []
    original_copy = exports.copy_source_artifact
    def counted(*args):
        copies.append(args[1]['artifact_id'])
        return original_copy(*args)
    monkeypatch.setattr(exports, 'copy_source_artifact', counted)
    asyncio.run(exports.InlineExportCoordinator()._reconcile(share.id, {'inline-artifacts/other/index.html'}))
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
    assert copies == []


def test_superseding_event_preserves_consumed_data_queued_behind_other_session(case, monkeypatch):
    from twicc.core.models import Session
    second = Session.objects.create(id='second', project=case.project, provider=case.provider,
                                    file_path='second.jsonl', last_line=20, compute_version=case.compute_version)
    set_publications(second, publication())
    first_source = write_source(case, b'published first')
    second_source = write_source(second, b'published second')
    for source in (first_source, second_source):
        (source.parent / 'data').mkdir()
        (source.parent / 'data' / 'value').write_bytes(b'old')
    first_share, second_share = create(case, mode='live'), create(second, mode='live')
    entered, resume = Event(), Event()
    pause = [False]
    original_copy = exports.copy_export_with_source_data
    def paused(*args):
        result = original_copy(*args)
        if pause[0] and args[1] == case.id:
            pause[0] = False
            entered.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    async def scenario():
        startup_done = asyncio.Event()
        started = []
        original_reconcile = exports.InlineExportCoordinator._reconcile
        async def observed(*args, **kwargs):
            result = await original_reconcile(*args, **kwargs)
            started.append(args[1])
            if len(started) == 2:
                startup_done.set()
            return result
        monkeypatch.setattr(exports.InlineExportCoordinator, '_reconcile', observed)
        coordinator = exports.InlineExportCoordinator()
        await coordinator.start()
        await asyncio.wait_for(startup_done.wait(), 5)
        (first_source.parent / 'data' / 'value').write_bytes(b'new first')
        (second_source.parent / 'data' / 'value').write_bytes(b'new second')
        pause[0] = True
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        coordinator.files_changed(second.id, ['inline-artifacts/widget/data/value'])
        assert await asyncio.to_thread(entered.wait, 5)
        coordinator.files_changed(second.id, ['inline-artifacts/widget/index.html'])
        resume.set()
        await coordinator.stop()
    asyncio.run(scenario())
    assert read_asset(first_share, 'data/value') == b'new first'
    lease = exports.lease_inline_asset(second_share.id, '["second","widget"]', 'data/value')
    try:
        assert lease.file.read() == b'new second'
    finally:
        lease.file.close()
        lease.release()


@pytest.mark.parametrize('operation', ['ensure', 'retry'])
def test_direct_authorized_operation_preserves_active_data(case, monkeypatch, operation):
    set_publications(case, publication(), publication(15, 'other'))
    source = write_source(case, b'published widget')
    write_source(case, b'published other', artifact_id='other')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    frozen = create(case, mode='snapshot')
    frozen_state = deepcopy(frozen.inline_artifact_exports)
    set_publications(case, publication(), publication(18, 'other', 'replacement.html'))
    asyncio.run(exports.InlineExportCoordinator().reconcile(share.id))
    share.refresh_from_db()
    before_revision = share.inline_artifact_exports['artifacts'][KEY]['code_revision']
    assert share.inline_artifact_exports['artifacts']['["main","other"]']['status'] == 'error'
    entered, resume = Event(), Event()
    copied = []
    reservations = []
    original_copy = exports.copy_export_with_source_data
    def paused(*args):
        result = original_copy(*args)
        copied.append(result)
        if len(copied) == 2:
            entered.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(exports, 'copy_export_with_source_data', paused)
    async def scenario():
        startup_done, api_reserved = asyncio.Event(), asyncio.Event()
        original_reconcile = exports.InlineExportCoordinator._reconcile
        async def observed(*args, **kwargs):
            result = await original_reconcile(*args, **kwargs)
            startup_done.set()
            return result
        monkeypatch.setattr(exports.InlineExportCoordinator, '_reconcile', observed)
        coordinator = exports.InlineExportCoordinator()
        monkeypatch.setattr(exports, 'get_inline_export_coordinator', lambda: coordinator)
        await coordinator.start()
        await asyncio.wait_for(startup_done.wait(), 5)
        data.write_bytes(b'new')
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        assert await asyncio.to_thread(entered.wait, 5)
        source.write_bytes(b'unpublished widget')
        write_source(case, b'published retry', artifact_id='other', filename='replacement.html')
        original_reserve = exports.reserve_inline_exports
        def reserved(share_id):
            generation = original_reserve(share_id)
            reservations.append(generation)
            api_reserved.set()
            return generation
        monkeypatch.setattr(exports, 'reserve_inline_exports', reserved)
        api = asyncio.create_task(exports.ensure_inline_exports(share.id) if operation == 'ensure'
                                  else exports.retry_inline_export(share.id, '["main","other"]'))
        await asyncio.wait_for(api_reserved.wait(), 5)
        resume.set()
        await api
        await coordinator.stop()
    asyncio.run(scenario())
    share.refresh_from_db()
    frozen.refresh_from_db()
    assert len(reservations) == 1
    assert read_asset(share, 'data/value') == b'new'
    assert leased_bytes(share) == b'published widget'
    assert share.inline_artifact_exports['artifacts'][KEY]['code_revision'] == before_revision
    assert frozen.inline_artifact_exports == frozen_state
    assert read_asset(frozen, 'data/value') == b'old'
    if operation == 'retry':
        assert share.inline_artifact_exports['artifacts']['["main","other"]']['status'] == 'ready'


def test_direct_ensure_preserves_data_queued_before_worker_consumption(case, monkeypatch):
    source = write_source(case, b'published')
    data = source.parent / 'data' / 'value'
    data.parent.mkdir()
    data.write_bytes(b'old')
    share = create(case, mode='live')
    async def scenario():
        startup_done = asyncio.Event()
        original_reconcile = exports.InlineExportCoordinator._reconcile
        async def observed(*args, **kwargs):
            result = await original_reconcile(*args, **kwargs)
            startup_done.set()
            return result
        monkeypatch.setattr(exports.InlineExportCoordinator, '_reconcile', observed)
        coordinator = exports.InlineExportCoordinator()
        monkeypatch.setattr(exports, 'get_inline_export_coordinator', lambda: coordinator)
        await coordinator.start()
        await asyncio.wait_for(startup_done.wait(), 5)
        data.write_bytes(b'new')
        coordinator.files_changed(case.id, ['inline-artifacts/widget/data/value'])
        # The direct API reserves before the worker can consume its data hint.
        await exports.ensure_inline_exports(share.id)
        await coordinator.stop()
    asyncio.run(scenario())
    assert read_asset(share, 'data/value') == b'new'
    assert leased_bytes(share) == b'published'
