"""Isolated OAuth admission limits, protective pause, and authorized traffic."""

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.test_mcp_external import (
    BASE, CHALLENGE, REDIRECT, RESOURCE, authorize, client, config, forbidden_inner, tokens,
)
from twicc.origin_gate import PublicOriginGate

pytestmark = pytest.mark.django_db(transaction=True)


def from_source(ip):
    return httpx.AsyncClient(transport=httpx.ASGITransport(
        app=PublicOriginGate(forbidden_inner, forbidden_inner), client=(ip, 1234),
    ), base_url=BASE)


async def register(c):
    return await c.post('/mcp/oauth/register', json={
        'redirect_uris': [REDIRECT], 'token_endpoint_auth_method': 'none',
    })


async def pending(c, client_id):
    return await c.get('/mcp/oauth/authorize', params={
        'client_id': client_id, 'redirect_uri': REDIRECT, 'response_type': 'code',
        'code_challenge': CHALLENGE, 'code_challenge_method': 'S256', 'resource': RESOURCE,
    })


def test_one_client_cannot_fill_the_pending_queue(config):
    async def run():
        async with client() as c:
            first = (await register(c)).json()['client_id']
            second = (await register(c)).json()['client_id']
            for _ in range(3):
                assert '/wait#' in (await pending(c, first)).headers['location']
            denied = await pending(c, first)
            assert parse_qs(urlsplit(denied.headers['location']).query)['error'] == ['temporarily_unavailable']
            assert '/wait#' in (await pending(c, second)).headers['location']
    asyncio.run(run())


def test_registration_rate_is_per_source_and_has_retry_after(config):
    async def run():
        async with from_source('198.51.100.1') as noisy, from_source('198.51.100.2') as other:
            for _ in range(10):
                assert (await register(noisy)).status_code == 201
            denied = await register(noisy)
            assert denied.status_code == 429
            assert int(denied.headers['retry-after']) > 0
            assert (await register(other)).status_code == 201
    asyncio.run(run())


def test_discovery_flood_does_not_block_refresh_or_revocation(config):
    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            for _ in range(301):
                await c.get('/.well-known/oauth-protected-resource/mcp')
            assert (await c.get('/mcp/oauth/wait')).status_code == 200
            preflight = await c.options('/mcp/oauth/token', headers={
                'Origin': 'https://client.example.com', 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type',
            })
            assert preflight.status_code == 204
            assert preflight.headers['access-control-allow-origin'] == '*'
            response = await c.post('/mcp/oauth/token', data={
                'grant_type': 'refresh_token', 'client_id': credentials['client_id'],
                'refresh_token': pair['refresh_token'], 'resource': RESOURCE,
            })
            assert response.status_code == 200
            assert (await c.post('/mcp/oauth/revoke', data={
                'client_id': credentials['client_id'], 'token': response.json()['refresh_token'],
            })).status_code == 200
    asyncio.run(run())


def test_source_pending_quota_spans_client_ids(config):
    async def run():
        async with from_source('198.51.100.1') as noisy, from_source('198.51.100.2') as other:
            ids = [(await register(noisy)).json()['client_id'] for _ in range(4)]
            for i in range(10):
                assert '/wait#' in (await pending(noisy, ids[i // 3])).headers['location']
            denied = await pending(noisy, ids[3])
            assert 'temporarily_unavailable' in denied.headers['location']
            other_id = (await register(other)).json()['client_id']
            assert '/wait#' in (await pending(other, other_id)).headers['location']
    asyncio.run(run())


def test_durable_registration_quota_cannot_be_bypassed_by_waiting(config, monkeypatch):
    from twicc.mcp.oauth import protection
    from twicc.core.models import McpOAuthClient

    clock = [1000.0]
    monkeypatch.setattr(protection, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def run():
        async with from_source('198.51.100.1') as c:
            for _ in range(20):
                assert (await register(c)).status_code == 201
                clock[0] += 61  # Stay below the request rate and detection thresholds.
            protection.protection = protection.Protection()  # Simulate a process restart.
            assert (await register(c)).status_code == 429
            assert await McpOAuthClient.objects.acount() == 20
            row = await McpOAuthClient.objects.afirst()
            assert len(row.source_hash) == 64
            assert '198.51.100.1' not in str(row.metadata)
    asyncio.run(run())


def test_distributed_attempts_pause_admission_but_preserve_active_flow(config, monkeypatch):
    from twicc.mcp.oauth import protection
    from twicc.mcp.oauth.provider import provider
    from twicc.mcp.oauth.storage import decide, write

    clock = [1000.0]
    monkeypatch.setattr(protection, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(protection, 'MAX_NEW_ATTEMPTS', 12)

    async def run():
        async with client() as owner:
            credentials, code = await authorize(owner)
            pair = (await tokens(owner, credentials, code)).json()
            # A second flow is already waiting for consent when protection trips.
            waiting = await pending(owner, credentials['client_id'])
            request_id, handle, verification = urlsplit(waiting.headers['location']).fragment.split(':')
            for i in range(20):
                async with from_source(f'198.51.100.{i + 1}') as source:
                    await register(source)
                if protection.protection.snapshot()['paused']:
                    break
            state = protection.protection.snapshot()
            assert state['paused'] is True
            assert state['sources'] > 1
            assert state['incident']['reason']
            assert protection.protection.acknowledge() is False
            denied = await register(owner)
            assert denied.status_code == 429
            assert int(denied.headers['retry-after']) == 600
            assert (await pending(owner, credentials['client_id'])).status_code == 429
            assert (await owner.get('/.well-known/oauth-protected-resource/mcp')).status_code == 200
            assert await provider.load_access_token(pair['access_token']) is not None
            # Already-started consent, code exchange, refresh, and revocation still work.
            assert (await write(lambda: decide(request_id, True, verification, 'existing flow')))[0]
            continued = await owner.post('/mcp/oauth/continue', json={'id': request_id, 'handle': handle})
            new_code = parse_qs(urlsplit(continued.json()['redirect']).query)['code'][0]
            assert (await tokens(owner, credentials, new_code)).status_code == 200
            refreshed = await owner.post('/mcp/oauth/token', data={
                'grant_type': 'refresh_token', 'client_id': credentials['client_id'],
                'refresh_token': pair['refresh_token'], 'resource': RESOURCE,
            })
            assert refreshed.status_code == 200
            assert (await owner.post('/mcp/oauth/revoke', data={
                'client_id': credentials['client_id'], 'token': refreshed.json()['refresh_token'],
            })).status_code == 200
            clock[0] += 601
            assert (await register(owner)).status_code == 201
            assert protection.protection.snapshot()['paused'] is False
            assert protection.protection.acknowledge() is True
    asyncio.run(run())


def test_invalid_token_traffic_cannot_spend_valid_grant_budget(config):
    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            for _ in range(121):
                await c.post('/mcp/oauth/token', data={
                    'grant_type': 'refresh_token', 'client_id': credentials['client_id'],
                    'refresh_token': 'invalid', 'resource': RESOURCE,
                })
            response = await c.post('/mcp/oauth/token', data={
                'grant_type': 'refresh_token', 'client_id': credentials['client_id'],
                'refresh_token': pair['refresh_token'], 'resource': RESOURCE,
            })
            assert response.status_code == 200
    asyncio.run(run())


def test_anonymous_bucket_churn_preserves_live_limits_and_authorized_reserve(config, monkeypatch):
    from twicc.mcp.oauth import protection

    monkeypatch.setattr(protection, 'MAX_BUCKETS', 2)
    guard = protection.Protection()
    for _ in range(120):
        assert guard.check('token', 'source-1') == 0
    assert guard.check('token', 'source-2') == 0
    assert guard.check('token', 'source-3') > 0
    assert guard.check('token', 'source-1') > 0
    assert guard.check('token', 'source-1', 'grant:valid') == 0


def test_source_identity_uses_asgi_peer_and_groups_ipv6_prefixes(config):
    from twicc.mcp.oauth.protection import source_hash

    first = source_hash({'client': ('198.51.100.1', 1)})
    assert first == source_hash({'client': ('198.51.100.1', 2), 'headers': [(b'x-forwarded-for', b'198.51.100.2')]})
    assert first != source_hash({'client': ('198.51.100.2', 1)})
    assert first == source_hash({'client': ('::ffff:198.51.100.1', 1)})
    assert source_hash({'client': ('2001:db8::1', 1)}) == source_hash({'client': ('2001:db8::2', 1)})


def test_owner_can_suspend_everything_without_revoking_and_resume(config, settings, monkeypatch, tmp_path):
    from asgiref.sync import sync_to_async
    from django.test import Client
    from twicc.auth.session_auth import bind_session
    from twicc.core.services import settings_mutation
    from twicc.mcp.oauth.provider import provider
    import twicc.synced_settings as ss

    monkeypatch.setattr(ss, 'get_synced_settings_path', lambda: tmp_path / 'settings.json')
    ss._cache.clear()
    ss.write_synced_settings({**ss.read_synced_settings(), 'mcpBaseUrl': BASE, 'externalMcpEnabled': True})
    monkeypatch.setattr(ss, 'read_routing_settings', lambda: ss.RoutingSettingsSnapshot(ss.read_synced_settings(), True))
    monkeypatch.setattr('twicc.mcp.oauth.config.read_routing_settings', ss.read_routing_settings)
    original = settings_mutation.update_synced_settings

    async def isolated_update(patch, **kwargs):
        return await original(patch, broadcast=False, **kwargs)

    monkeypatch.setattr(settings_mutation, 'update_synced_settings', isolated_update)
    owner = Client(REMOTE_ADDR='127.0.0.1')
    session = owner.session
    bind_session(session, settings.TWICC_PASSWORD_HASH)
    session.save()

    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            response = await sync_to_async(owner.get)('/api/mcp/')
            assert 'protection' in response.json()
            response = await sync_to_async(owner.post)('/api/mcp/', {'action': 'suspend'}, content_type='application/json')
            assert response.status_code == 403
            response = await sync_to_async(owner.post)(
                '/api/mcp/', {'action': 'suspend'}, content_type='application/json', HTTP_X_TWICC_MCP_OWNER='1',
            )
            assert response.status_code == 200
            assert ss.read_synced_settings()['externalMcpEnabled'] is False
            assert (await c.get('/mcp')).status_code == 404
            assert (await c.post('/mcp/oauth/token')).status_code == 404
            assert await provider.load_access_token(pair['access_token']) is None
            response = await sync_to_async(owner.post)(
                '/api/mcp/', {'action': 'configure', 'externalMcpEnabled': True},
                content_type='application/json', HTTP_X_TWICC_MCP_OWNER='1',
            )
            assert response.status_code == 200
            assert await provider.load_access_token(pair['access_token']) is not None
    try:
        asyncio.run(run())
    finally:
        ss._cache.clear()
