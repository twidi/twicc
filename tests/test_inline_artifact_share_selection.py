"""Inline exports follow root visibility and preserve captured identities."""

from copy import deepcopy

import orjson
import pytest

from twicc.core.models import Project, Session, SessionItem, SessionType, Share
from twicc.core.serializers import serialize_share, serialize_share_public_meta
from twicc.core.services.share_mutation import _validate_session_options
from twicc.inline_artifacts.share_selection import (
    SelectionNotReady, include_inline_artifacts, select_share_publications,
    prepare_share_selection, public_inline_manifest,
)
from twicc.providers.helpers import get_provider_helpers

KEY = '["main","preferences"]'


def publication(line=10, artifact_id='preferences', filename='index.html', title='Preferences'):
    return {'artifact_id': artifact_id, 'line_num': line, 'text_block_index': 1, 'tag_offset': 7,
            'src': f'inline-artifacts/{artifact_id}/{filename}', 'title': title, 'height': 360}


@pytest.fixture
def share(transactional_db):
    session = Session.objects.create(id='main', project=Project.objects.create(id='p'), file_path='main.jsonl', provider='claude_code',
                                     last_line=30)
    session.compute_version = get_provider_helpers(session.provider).current_compute_version
    session.save()
    return Share.objects.create(id='shr_test', token='token', kind='session', session=session,
                                options={'mode': 'snapshot', 'frozen_at_line': 20, 'max_display_mode': 'normal'})


def publish(share, *records, level=1):
    share.session.inline_artifacts = {'schema': 1, 'publications': list(records), 'source_messages': {'private': 10}}
    share.session.save()
    for record in records:
        SessionItem.objects.update_or_create(session=share.session, line_num=record['line_num'],
                                             defaults={'content': '{}', 'display_level': level})


def test_missing_and_explicit_option_defaults():
    assert include_inline_artifacts({}) is True
    assert include_inline_artifacts({'include_inline_artifacts': False}) is False
    assert _validate_session_options({})[0]['include_inline_artifacts'] is True
    assert _validate_session_options({'include_inline_artifacts': False})[0]['include_inline_artifacts'] is False


@pytest.mark.parametrize('key', ['inline_artifact_exports', 'captured', 'publications', 'src', 'copy_id', 'initialized', 'ready'])
def test_caller_options_cannot_write_export_metadata(key):
    assert _validate_session_options({key: {}})[1][0].code == 'unknown_keys'


def test_latest_eligible_root_publication_uses_display_and_frozen_boundary(share):
    publish(share, publication(5), publication(10), publication(25))
    SessionItem.objects.filter(session=share.session, line_num=10).update(display_level=3)
    assert select_share_publications(share) == {KEY: publication(5)}
    share.options['max_display_mode'] = 'debug'
    assert select_share_publications(share) == {KEY: publication(10)}


def test_child_catalog_never_participates(share):
    publish(share, publication())
    Session.objects.create(id='child', project=share.session.project, provider='claude_code', file_path='child.jsonl', type=SessionType.SUBAGENT,
                           parent_session=share.session, inline_artifacts={'publications': [publication(20)]})
    share.options['include_subagents'] = True
    assert select_share_publications(share) == {KEY: publication()}


def test_snapshot_options_do_not_replace_capture_or_change_revisions(share):
    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY] = {'status': 'ready', 'code_revision': 4, 'copy_id': 'private-copy'}
    before = deepcopy(share.inline_artifact_exports)
    publish(share, publication(), publication(15, filename='widget.html'))
    share.options.update(include_subagents=False, show_timestamps=False, display_title='Title')
    assert prepare_share_selection(share) == before
    assert select_share_publications(share)[KEY]['line_num'] == 10
    assert select_share_publications(share, captured=before['captured'])[KEY]['line_num'] == 10


def test_excluded_capture_remains_tombstone_until_push_update(share):
    publish(share, publication(5), publication(10))
    share.options['max_display_mode'] = 'debug'
    share.inline_artifact_exports = prepare_share_selection(share)
    publish(share, publication(5), publication(10), publication(15, filename='changed.html'))
    SessionItem.objects.filter(session=share.session, line_num=10).update(display_level=3)
    share.options['max_display_mode'] = 'normal'
    tightened = prepare_share_selection(share)
    assert tightened['selected'] == {}
    assert tightened['captured'][KEY] == publication(10)
    assert tightened['revision'] > share.inline_artifact_exports['revision']
    share.inline_artifact_exports = tightened
    share.options['max_display_mode'] = 'debug'
    assert prepare_share_selection(share)['selected'][KEY] == publication(10)
    assert prepare_share_selection(share, recapture=True)['selected'][KEY] == publication(15, filename='changed.html')


def test_new_main_identity_captures_current_eligible_publication(share):
    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    publish(share, publication(), publication(15, artifact_id='calculator'))
    prepared = prepare_share_selection(share)
    assert prepared['selected']['["main","calculator"]'] == publication(15, artifact_id='calculator')
    assert prepared['captured'][KEY] == publication()


def test_unready_capture_is_retriable_and_does_not_initialize(share):
    publish(share, publication())
    share.session.compute_version = 0
    share.session.save()
    for operation in (select_share_publications, prepare_share_selection, public_inline_manifest):
        with pytest.raises(SelectionNotReady) as error:
            operation(share)
        assert error.value.session_ids == ('main',)
    assert share.inline_artifact_exports == {}
    share.session.compute_version = get_provider_helpers(share.session.provider).current_compute_version
    assert prepare_share_selection(share)['selected'] == {KEY: publication()}


def test_native_child_share_has_no_inline_metadata_or_readiness_gate(share):
    share.session.type = SessionType.SUBAGENT
    share.session.compute_version = 0
    publish(share, publication())
    assert select_share_publications(share) == {}
    assert prepare_share_selection(share) == {}
    assert public_inline_manifest(share) == {'enabled': False, 'revision': 0, 'artifacts': []}
    assert serialize_share_public_meta(share)['inline_artifacts_supported'] is False


def test_disabled_manifest_keeps_placements_without_exports_or_paths(share):
    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY] = {'status': 'ready', 'code_revision': 3,
                                                     'copy_id': '/private/path/copy'}
    share.options['include_inline_artifacts'] = False
    manifest = public_inline_manifest(share)
    assert manifest['enabled'] is False
    descriptor = manifest['artifacts'][0]
    assert descriptor['status'] == 'not_included'
    assert descriptor['code_revision'] is None
    assert descriptor['publication'] == ['main', 10, 1, 7]
    assert descriptor['entry_filename'] == 'index.html'
    wire = orjson.dumps([manifest, serialize_share(share), serialize_share_public_meta(share)]).decode()
    assert '/private/' not in wire
    assert 'source_messages' not in wire
    assert 'copy_id' not in wire
    assert 'inline-artifacts/preferences/' not in wire


@pytest.fixture
def mutation_lock(monkeypatch):
    async def passthrough(factory):
        return await factory()
    monkeypatch.setattr('twicc.core.services.share_mutation.run_under_db_write_lock', passthrough)


@pytest.mark.parametrize('mode', ['live', 'snapshot'])
def test_unready_owner_mutations_preserve_published_state(share, mutation_lock, mode):
    import asyncio
    from twicc.core.services import share_mutation
    from twicc.share.owner_views import _err_response

    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    share.save()
    before = deepcopy(share.inline_artifact_exports)
    share.session.compute_version = 0
    share.session.save()
    created = asyncio.run(share_mutation.create_share('session', session=share.session, options={'mode': mode}))
    assert not created.success
    assert created.errors[0].code == 'session_not_ready'
    assert _err_response(created).status_code == 409
    changed = asyncio.run(share_mutation.patch_share(share, {'label': 'new', 'options': {'max_display_mode': 'debug',
                                                       'mode': 'snapshot'}}))
    assert not changed.success
    pushed = asyncio.run(share_mutation.propagate_share(share))
    assert not pushed.success
    share.refresh_from_db()
    assert share.inline_artifact_exports == before
    assert share.label == ''
    assert share.options['max_display_mode'] == 'normal'
    assert Share.objects.count() == 1


def test_commit_rechecks_root_readiness(share, monkeypatch):
    import asyncio
    from twicc.core.services import share_mutation

    async def invalidate_then_commit(factory):
        from asgiref.sync import sync_to_async
        await sync_to_async(Session.objects.filter(id='main').update)(compute_version=0)
        return await factory()
    monkeypatch.setattr('twicc.core.services.share_mutation.run_under_db_write_lock', invalidate_then_commit)
    result = asyncio.run(share_mutation.create_share('session', session=share.session, options={'mode': 'snapshot'}))
    assert not result.success
    assert result.errors[0].code == 'session_not_ready'
    assert Share.objects.count() == 1


def test_transcript_only_changes_ignore_unready_root(share, mutation_lock):
    import asyncio
    from twicc.core.services import share_mutation

    share.session.compute_version = 0
    share.session.save()
    options = {**share.options, 'include_subagents': False, 'show_timestamps': False}
    result = asyncio.run(share_mutation.patch_share(share, {'label': 'title', 'options': options}))
    assert result.success
    assert share.inline_artifact_exports == {}


def test_disabling_inline_exports_does_not_wait_for_root_compute(share, mutation_lock):
    import asyncio
    from twicc.core.services import share_mutation

    share.session.compute_version = 0
    share.session.save()
    result = asyncio.run(share_mutation.patch_share(share, {'options': {
        **share.options, 'include_inline_artifacts': False}}))
    assert result.success
    assert share.options['include_inline_artifacts'] is False
    assert share.inline_artifact_exports == {}


def test_unready_native_root_still_creates_transcript_share(share, mutation_lock):
    import asyncio
    from twicc.core.services import share_mutation

    share.session.type = SessionType.SUBAGENT
    share.session.compute_version = 0
    share.session.save()
    result = asyncio.run(share_mutation.create_share('session', session=share.session, options={'mode': 'snapshot'}))
    assert result.success
    created = Share.objects.select_related('session').get(id=result.share_id)
    assert created.inline_artifact_exports == {}
    assert serialize_share_public_meta(created)['inline_artifacts_supported'] is False


def test_legacy_unready_snapshot_never_stores_empty_capture(share):
    share.session.compute_version = 0
    share.session.save()
    share.options.pop('include_inline_artifacts', None)
    with pytest.raises(SelectionNotReady):
        prepare_share_selection(share)
    assert share.inline_artifact_exports == {}
    publish(share, publication())
    share.session.compute_version = get_provider_helpers(share.session.provider).current_compute_version
    prepared = prepare_share_selection(share)
    assert prepared['initialized'] is True
    assert prepared['captured'] == {KEY: publication()}
    assert share.inline_artifact_exports == {}


def test_live_selection_follows_latest_eligible_main_occurrence(share):
    share.options['mode'] = 'live'
    publish(share, publication(), publication(25, filename='updated.htm'))
    assert select_share_publications(share)[KEY] == publication(25, filename='updated.htm')
    assert prepare_share_selection(share)['captured'] == {}


def test_public_errors_and_records_strip_private_fields(share):
    record = {**publication(), 'absolute_path': '/private/source'}
    publish(share, record, {**publication(15, artifact_id='calculator'), 'src': '/private/index.html'})
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY] = {'status': 'error', 'error': '/private/missing.html'}
    manifest = public_inline_manifest(share)
    assert len(manifest['artifacts']) == 1
    assert manifest['artifacts'][0]['error'] == 'export_failed'
    assert '/private' not in orjson.dumps(manifest).decode()


@pytest.mark.parametrize('bad', [None, 0, 1, 'false', {}])
def test_owner_option_requires_literal_boolean(bad):
    options, errors = _validate_session_options({'include_inline_artifacts': bad})
    assert any(error.field == 'include_inline_artifacts' and error.code == 'invalid' for error in errors)


def test_snapshot_visibility_restores_the_same_copy_after_source_changes(share):
    publish(share, publication())
    share.options['max_display_mode'] = 'debug'
    share.inline_artifact_exports = prepare_share_selection(share)
    entry = {'status': 'ready', 'copy_id': 'frozen-copy', 'code_revision': 4}
    share.inline_artifact_exports['artifacts'][KEY] = deepcopy(entry)
    share.options['max_display_mode'] = 'normal'
    SessionItem.objects.filter(session=share.session, line_num=10).update(display_level=3)
    tightened = prepare_share_selection(share)
    assert tightened['selected'] == {}
    assert tightened['artifacts'][KEY] == entry
    share.inline_artifact_exports = tightened
    publish(share, publication(), publication(15, filename='changed.html'))
    SessionItem.objects.filter(session=share.session, line_num=10).update(display_level=3)
    share.options['max_display_mode'] = 'debug'
    relaxed = prepare_share_selection(share)
    assert relaxed['selected'][KEY] == publication()
    assert relaxed['artifacts'][KEY] == entry
    assert public_inline_manifest(share)['artifacts'][0]['code_revision'] == 4


def test_live_visibility_drops_excluded_copy_metadata(share):
    share.options['mode'] = 'live'
    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY] = {'status': 'ready', 'copy_id': 'live-copy', 'code_revision': 1}
    SessionItem.objects.filter(session=share.session, line_num=10).update(display_level=3)
    assert prepare_share_selection(share)['artifacts'] == {}


def test_inline_relevant_option_changes_increment_manifest_revision(share):
    publish(share, publication())
    share.inline_artifact_exports = prepare_share_selection(share)
    revision = share.inline_artifact_exports['revision']
    share.options['max_display_mode'] = 'debug'
    prepared = prepare_share_selection(share)
    assert prepared['selected'] == share.inline_artifact_exports['selected']
    assert prepared['revision'] == revision + 1
    share.inline_artifact_exports = prepared
    share.options['mode'] = 'live'
    assert prepare_share_selection(share)['revision'] == revision + 2


def test_public_websocket_metadata_never_forwards_private_catalog(share):
    import asyncio
    from channels.layers import get_channel_layer
    from channels.testing import WebsocketCommunicator
    from twicc.share.consumer import ShareConsumer

    publish(share, publication())
    share.options['mode'] = 'live'
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY]['copy_id'] = '/private/export'
    share.save()
    private_session = {'id': 'main', 'inline_artifacts': {'preferences': publication()},
                       'source_messages': {'private': 10}}

    async def scenario():
        communicator = WebsocketCommunicator(ShareConsumer.as_asgi(), '/ws/share/token/')
        communicator.scope['url_route'] = {'kwargs': {'token': 'token'}}
        connected, _ = await communicator.connect()
        assert connected
        try:
            await get_channel_layer().group_send('updates', {'type': 'broadcast', 'data': {
                'type': 'session_updated', 'session': private_session}})
            event = await communicator.receive_json_from(timeout=2)
            assert event['type'] == 'share_meta'
            assert event['meta']['include_inline_artifacts'] is True
            wire = orjson.dumps(event).decode()
            assert 'inline-artifacts/preferences' not in wire
            assert 'source_messages' not in wire
            assert '/private/export' not in wire
            assert 'copy_id' not in wire
        finally:
            await communicator.disconnect()

    asyncio.run(scenario())


def test_terminal_inline_delivery_recovers_after_compute_readiness_without_source_event(share):
    import asyncio
    from asgiref.sync import sync_to_async
    from channels.layers import get_channel_layer
    from channels.testing import WebsocketCommunicator
    from twicc.core.services.share_mutation import broadcast_share_updated
    from twicc.share.consumer import ShareConsumer

    publish(share, publication())
    share.options['mode'] = 'live'
    share.inline_artifact_exports = prepare_share_selection(share)
    share.inline_artifact_exports['artifacts'][KEY].update(status='ready', code_revision=4, copy_id='/private/export')
    share.save()

    async def scenario():
        communicator = WebsocketCommunicator(ShareConsumer.as_asgi(), '/ws/share/token/')
        communicator.scope['url_route'] = {'kwargs': {'token': 'token'}}
        connected, _ = await communicator.connect()
        assert connected
        try:
            # Both producer and relay read an obsolete root during the terminal update.
            share.session.compute_version -= 1
            await sync_to_async(share.session.save)(update_fields=['compute_version'])
            await broadcast_share_updated(share)
            assert (await communicator.receive_json_from(timeout=2))['type'] == 'share_meta'
            assert await communicator.receive_nothing(timeout=0.1)
            share.session.compute_version += 1
            await sync_to_async(share.session.save)(update_fields=['compute_version'])
            await get_channel_layer().group_send('updates', {'type': 'broadcast', 'data': {
                'type': 'session_updated', 'session': {'id': 'main'}}})
            assert (await communicator.receive_json_from(timeout=2))['type'] == 'share_meta'
            event = await communicator.receive_json_from(timeout=1)
            assert event['type'] == 'share_inline_artifacts'
            assert event['manifest']['artifacts'][0]['status'] == 'ready'
            assert event['manifest']['artifacts'][0]['code_revision'] == 4
            assert '/private/export' not in orjson.dumps(event).decode()
            # A delivered obligation clears: unrelated later metadata does not replay it.
            await get_channel_layer().group_send('updates', {'type': 'broadcast', 'data': {
                'type': 'session_updated', 'session': {'id': 'main'}}})
            assert (await communicator.receive_json_from(timeout=2))['type'] == 'share_meta'
            assert await communicator.receive_nothing(timeout=0.1)
        finally:
            await communicator.disconnect()

    asyncio.run(scenario())
