"""Token-scoped inline export authorization and copied-byte responses."""
import asyncio
from datetime import timedelta

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone

from twicc import paths
from twicc.auth.hashers import hash_password
from twicc.core.models import Project, Session, SessionItem, SessionType, Share
from twicc.core.services import share_mutation
from twicc.core.services.share_tokens import mint_token
from twicc.inline_artifacts import share_exports
from twicc.providers.helpers import get_provider_helpers

KEY = '["public-root","widget"]'


@pytest.fixture
def public_case(transactional_db, tmp_path, monkeypatch, settings):
    settings.TWICC_PASSWORD_HASH = ''
    monkeypatch.setattr(paths, 'get_data_dir', lambda: tmp_path)
    async def unlocked(factory):
        return await factory()
    monkeypatch.setattr(share_mutation, 'run_under_db_write_lock', unlocked)
    monkeypatch.setattr(share_exports, 'run_under_db_write_lock', unlocked)
    root = Session.objects.create(id='public-root', project=Project.objects.create(id='public-project'),
        provider='claude_code', file_path='root.jsonl', last_line=10,
        compute_version=get_provider_helpers('claude_code').current_compute_version,
        inline_artifacts={'schema': 1, 'publications': [dict(artifact_id='widget', line_num=10,
            text_block_index=0, tag_offset=2, src='inline-artifacts/widget/index.html', title='Widget', height=360)]})
    SessionItem.objects.create(session=root, line_num=10, content='{}', display_level=1)
    source = tmp_path / 'artifacts' / root.id / 'inline-artifacts' / 'widget'
    (source / 'data').mkdir(parents=True)
    (source / 'index.html').write_text('<html><body>Frozen widget</body></html>')
    (source / 'style.css').write_text('body { color: red }')
    (source / 'app.js').write_text('window.example = 1')
    (source / 'data' / 'saved.json').write_text('{"value":1}')
    result = asyncio.run(share_mutation.create_share('session', session=root, options={'mode': 'snapshot'}))
    assert result.success
    share = Share.objects.select_related('session').get(id=result.share_id)
    return AsyncClient(), root, share, source


def route(share, suffix):
    return f'/share/{share.token}/{suffix}'


def request(case, suffix, method='get', **kwargs):
    client, _, share, _ = case
    return asyncio.run(getattr(client, method)(route(share, suffix), **kwargs))


def body(response):
    if response.streaming:
        if not response.is_async:
            try:
                return b''.join(response.streaming_content)
            finally:
                response.close()
        async def consume():
            return b''.join([part async for part in response.streaming_content])
        return asyncio.run(consume())
    return response.content


ROUTES = [
    ('api/inline-artifacts/', 'get'),
    ('inline-artifacts/public-root/widget/index.html', 'get'),
    ('inline-artifacts/public-root/widget/style.css', 'get'),
    ('inline-artifacts/public-root/widget/data/', 'get'),
    ('api/inline-artifacts/public-root/widget/retry/', 'post'),
    ('api/inline-artifacts/public-root/widget/proxy/', 'post'),
]


def test_ready_manifest_and_wrapped_html(public_case):
    manifest = request(public_case, 'api/inline-artifacts/')
    assert manifest.status_code == 200
    data = orjson.loads(manifest.content)
    assert data['artifacts'][0]['status'] == 'ready'
    assert data['artifacts'][0]['publication'] == ['public-root', 10, 0, 2]
    assert b'copy_id' not in manifest.content and b'allowed_hosts' not in manifest.content
    response = request(public_case, 'inline-artifacts/public-root/widget/index.html', headers={'Sec-Fetch-Dest': 'iframe'})
    assert response.status_code == 200
    assert b'Frozen widget' in response.content and b'/_twicc/artifact-broker-shim.js' in response.content
    assert "connect-src 'none'" in response['Content-Security-Policy']
    assert response['Cache-Control'] == 'no-store'
    assert response['X-Robots-Tag'] == 'noindex, nofollow'


@pytest.mark.parametrize('suffix,method', ROUTES)
@pytest.mark.parametrize('denial', ['revoked', 'expired', 'deleted', 'password', 'excluded', 'disabled', 'native', 'not_ready'])
def test_public_route_authorization_matrix(public_case, suffix, method, denial):
    _, root, share, _ = public_case
    if denial == 'revoked': share.revoked_at = timezone.now()
    elif denial == 'expired': share.expires_at = timezone.now() - timedelta(seconds=1)
    elif denial == 'password': share.password_hash = hash_password('secret')
    elif denial == 'excluded': share.options = {**share.options, 'frozen_at_line': 9}
    elif denial == 'disabled': share.options = {**share.options, 'include_inline_artifacts': False}
    elif denial == 'native':
        root.type = SessionType.SUBAGENT; root.save(update_fields=['type'])
    elif denial == 'not_ready':
        root.compute_version -= 1; root.save(update_fields=['compute_version'])
    if denial == 'deleted': share.delete()
    else: share.save()
    response = request(public_case, suffix, method)
    if suffix == 'api/inline-artifacts/' and denial in ('excluded', 'disabled', 'native'):
        assert response.status_code == 200
        manifest = orjson.loads(response.content)
        assert not any(entry['status'] == 'ready' for entry in manifest['artifacts'])
        if denial != 'excluded': assert manifest['enabled'] is False
    else:
        assert response.status_code in (401, 403, 404, 409)


@pytest.mark.parametrize('suffix,method', ROUTES[1:])
def test_child_identity_never_grants_inline_access(public_case, suffix, method):
    _, root, share, _ = public_case
    Session.objects.create(id='child', project=root.project, provider=root.provider, file_path='child.jsonl',
        type=SessionType.SUBAGENT, parent_session=root, compute_version=root.compute_version)
    share.options = {**share.options, 'include_subagents': True}; share.save()
    assert request(public_case, suffix.replace('public-root', 'child'), method).status_code == 404


@pytest.mark.parametrize('path', ['../other/index.html', '%2e%2e/other/index.html', 'data/../../index.html', 'missing.html'])
def test_confined_assets(public_case, path):
    assert request(public_case, 'inline-artifacts/public-root/widget/' + path).status_code == 404


@pytest.mark.parametrize('asset,content_type', [('style.css', 'text/css'), ('app.js', 'javascript'), ('data/saved.json', 'application/json')])
def test_assets_keep_types_and_head_has_no_body(public_case, asset, content_type):
    response = request(public_case, 'inline-artifacts/public-root/widget/' + asset)
    assert response.status_code == 200
    assert content_type in response['Content-Type']
    assert response['Cache-Control'] == 'no-store'
    assert body(response)
    response = request(public_case, 'inline-artifacts/public-root/widget/' + asset, 'head')
    assert response.status_code == 200 and body(response) == b''


def test_listing_is_read_only_and_bound_to_document(public_case):
    _, _, share, _ = public_case
    headers = {'X-Twicc-Artifact-Doc': route(share, 'inline-artifacts/public-root/widget/index.html')}
    response = request(public_case, 'inline-artifacts/public-root/widget/data/', headers=headers)
    assert response.status_code == 200
    assert orjson.loads(response.content)['files'][0]['path'] == 'saved.json'
    assert request(public_case, 'inline-artifacts/public-root/widget/data/').status_code == 404
    for method in ('put', 'delete'):
        assert request(public_case, 'inline-artifacts/public-root/widget/data/saved.json', method, headers=headers).status_code == 405


def test_reload_never_replaces_successful_snapshot(public_case):
    _, _, _, source = public_case
    (source / 'index.html').write_text('changed private code')
    response = request(public_case, 'api/inline-artifacts/public-root/widget/retry/', 'post')
    assert response.status_code == 200
    response = request(public_case, 'inline-artifacts/public-root/widget/index.html')
    assert b'Frozen widget' in body(response)


@pytest.mark.parametrize('status', ['pending', 'error'])
def test_nonready_export_never_serves_bytes(public_case, status):
    _, _, share, _ = public_case
    share.inline_artifact_exports['artifacts'][KEY]['status'] = status
    share.save(update_fields=['inline_artifact_exports'])
    assert request(public_case, 'inline-artifacts/public-root/widget/index.html').status_code == 404


def test_password_grant_unlocks_all_inline_routes(public_case):
    from twicc.core.services.share_tokens import password_fingerprint
    from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY
    client, _, share, _ = public_case
    share.password_hash = hash_password('secret'); share.save()
    async def grant():
        session = await client.asession()
        session[SHARE_GRANTS_SESSION_KEY] = {share.id: password_fingerprint(share.password_hash)}
        await session.asave()
    asyncio.run(grant())
    assert request(public_case, 'api/inline-artifacts/').status_code == 200
    assert request(public_case, 'inline-artifacts/public-root/widget/index.html', 'head').status_code == 200
    assert request(public_case, 'api/inline-artifacts/public-root/widget/retry/', 'post').status_code == 200


def test_wrong_kind_and_unknown_token_refuse_every_inline_route(public_case):
    _, root, share, _ = public_case
    from twicc.core.models import ArtifactBookmark
    bookmark = ArtifactBookmark.objects.create(session=root, project=root.project, relative_path='inline-artifacts/widget/index.html')
    share.kind = 'artifact'; share.session = None; share.artifact_bookmark = bookmark; share.save()
    for suffix, method in ROUTES:
        assert request(public_case, suffix, method).status_code == 404
    share.token = 'unknown-token'
    for suffix, method in ROUTES:
        assert request(public_case, suffix, method).status_code == 404


def test_html_and_listing_head_have_no_body(public_case):
    _, _, share, _ = public_case
    for suffix in ['inline-artifacts/public-root/widget/index.html', 'inline-artifacts/public-root/widget/data/']:
        response = request(public_case, suffix, 'head', headers={'Sec-Fetch-Dest': 'iframe',
            'X-Twicc-Artifact-Doc': route(share, 'inline-artifacts/public-root/widget/index.html')})
        assert response.status_code == 200 and body(response) == b''


def test_listing_holds_old_copy_during_replacement(public_case, monkeypatch):
    from twicc.share import inline_artifact_views
    _, _, share, source = public_case
    copied = share_exports.inline_export_root(share.id) / share.inline_artifact_exports['artifacts'][KEY]['copy_id']
    original = inline_artifact_views._data_entries
    def replace_while_listing(directory_fd):
        (source / 'data' / 'new.json').write_text('{}')
        from asgiref.sync import ThreadSensitiveContext
        async def replace():
            async with ThreadSensitiveContext():
                return await share_mutation.propagate_share(share)
        result = asyncio.run(replace())
        assert result.success
        assert copied.exists()
        return original(directory_fd)
    monkeypatch.setattr(inline_artifact_views, '_data_entries', replace_while_listing)
    response = request(public_case, 'inline-artifacts/public-root/widget/data/', headers={
        'X-Twicc-Artifact-Doc': route(share, 'inline-artifacts/public-root/widget/index.html')})
    assert response.status_code == 200
    assert [entry['path'] for entry in orjson.loads(response.content)['files']] == ['saved.json']
    assert not copied.exists()


def test_listing_rejects_nested_symlink(public_case):
    _, _, share, source = public_case
    copied = share_exports.inline_export_root(share.id) / share.inline_artifact_exports['artifacts'][KEY]['copy_id']
    (copied / 'data' / 'escape').symlink_to(source)
    response = request(public_case, 'inline-artifacts/public-root/widget/data/', headers={
        'X-Twicc-Artifact-Doc': route(share, 'inline-artifacts/public-root/widget/index.html')})
    assert response.status_code == 404


@pytest.mark.parametrize('missing', [False, True])
def test_legacy_manifest_returns_durable_pending_and_coalesces_copy(public_case, monkeypatch, missing):
    from asgiref.sync import sync_to_async
    client, _, share, source = public_case
    share.inline_artifact_exports = {}; share.save(update_fields=['inline_artifact_exports'])
    if missing: (source / 'index.html').unlink()
    coordinator = share_exports.InlineExportCoordinator()
    monkeypatch.setattr(share_exports, '_coordinator', coordinator)
    original = share_exports.prepare_inline_exports
    calls = []
    async def scenario():
        release = asyncio.Event()
        async def paused(*args, **kwargs):
            calls.append(1)
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(share_exports, 'prepare_inline_exports', paused)
        for _ in range(2):
            response = await client.get(route(share, 'api/inline-artifacts/'))
            assert response.status_code == 200
            assert orjson.loads(response.content)['artifacts'][0]['status'] == 'pending'
        await asyncio.sleep(0)
        assert len(calls) == 1
        release.set()
        await coordinator.stop()
        response = await client.get(route(share, 'api/inline-artifacts/'))
        assert orjson.loads(response.content)['artifacts'][0]['status'] == ('error' if missing else 'ready')
        await sync_to_async(share.refresh_from_db)()
        assert share.inline_artifact_exports['initialized'] is True
    asyncio.run(scenario())


def test_scheduled_legacy_capture_keeps_acceptance_generation(public_case, monkeypatch):
    from asgiref.sync import sync_to_async
    _, _, share, _ = public_case
    share.inline_artifact_exports = {}; share.save(update_fields=['inline_artifact_exports'])
    coordinator = share_exports.InlineExportCoordinator()
    monkeypatch.setattr(share_exports, '_coordinator', coordinator)
    async def scenario():
        lock = share_exports.share_export_lock(share.id)
        await lock.acquire()
        scheduled = asyncio.create_task(coordinator.schedule_public_reconcile(share.id))
        await asyncio.sleep(0)
        # The operation accepts work before the source event supersedes it.
        share_exports.invalidate_inline_exports(share.id)
        lock.release()
        await scheduled
        await coordinator.stop()
        await sync_to_async(share.refresh_from_db)()
        assert share.inline_artifact_exports == {}
    asyncio.run(scenario())


def test_successful_snapshot_manifest_does_not_supersede_owner_operation(public_case):
    _, _, share, _ = public_case
    generation = share_exports.reserve_inline_exports(share.id)
    assert request(public_case, 'api/inline-artifacts/').status_code == 200
    assert share_exports._generation_current(share.id, generation)


def test_snapshot_error_retry_uses_captured_tag_after_new_owner_tag(public_case):
    _, root, share, source = public_case
    share.inline_artifact_exports['artifacts'][KEY].update(status='error', error='artifact_unavailable', copy_id=None)
    share.save(update_fields=['inline_artifact_exports'])
    root.inline_artifacts['publications'].append(dict(artifact_id='widget', line_num=11, text_block_index=0,
        tag_offset=0, src='inline-artifacts/widget/new.html', title='New tag', height=600))
    root.last_line = 11; root.save(update_fields=['inline_artifacts', 'last_line'])
    SessionItem.objects.create(session=root, line_num=11, content='{}', display_level=1)
    (source / 'new.html').write_text('New private tag')
    response = request(public_case, 'api/inline-artifacts/public-root/widget/retry/', 'post')
    assert response.status_code == 200
    descriptor = orjson.loads(response.content)['artifacts'][0]
    assert descriptor['publication'] == ['public-root', 10, 0, 2]
    assert descriptor['entry_filename'] == 'index.html' and descriptor['status'] == 'ready'
    assert b'Frozen widget' in body(request(public_case, 'inline-artifacts/public-root/widget/index.html'))


def test_auxiliary_html_navigation_has_shim_and_csp(public_case):
    _, _, share, source = public_case
    (source / 'auxiliary.html').write_text('<html><body>Auxiliary document</body></html>')
    assert asyncio.run(share_mutation.propagate_share(share)).success
    response = request(public_case, 'inline-artifacts/public-root/widget/auxiliary.html',
                       headers={'Sec-Fetch-Dest': 'document'})
    assert response.status_code == 200
    assert b'/_twicc/artifact-broker-shim.js' in response.content
    assert "connect-src 'none'" in response['Content-Security-Policy']
