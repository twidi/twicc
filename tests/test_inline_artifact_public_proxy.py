"""Inline public proxies use current source-bookmark grants server-side."""
import orjson
import pytest

from twicc.artifacts import proxy
from twicc.artifacts.proxy import ProxyResult, ResolvedTarget
from twicc.core.models import ArtifactBookmark
from tests import test_inline_artifact_public_routes
from tests.test_inline_artifact_public_routes import request

public_case = test_inline_artifact_public_routes.public_case


@pytest.mark.parametrize('bookmarked', [False, True])
def test_public_proxy_uses_current_bookmark_grants(public_case, monkeypatch, bookmarked):
    _, root, _, _ = public_case
    sent = []
    async def resolve(host, port):
        return ResolvedTarget(ip='8.8.8.8', kind='public')
    async def fetch(**kwargs):
        sent.append(kwargs['pinned_ip'])
        return ProxyResult(status=200, reason='OK', headers={}, body=b'reply')
    monkeypatch.setattr(proxy, 'resolve_target', resolve)
    monkeypatch.setattr(proxy, 'proxy_fetch', fetch)
    bookmark = None
    if bookmarked:
        bookmark = ArtifactBookmark.objects.create(session=root, project=root.project,
            relative_path='inline-artifacts/widget/index.html', allowed_hosts={'https://example.com:443': {}})
    payload = orjson.dumps({'mode': 'fetch', 'pinned_ip': '127.0.0.1', 'bookmark_id': 'caller-invented',
                           'request': {'url': 'https://example.com/', 'method': 'GET'}})
    def call():
        return request(public_case, 'api/inline-artifacts/public-root/widget/proxy/', 'post',
                       data=payload, content_type='application/json')
    response = call()
    assert response.status_code == 200
    if bookmarked:
        assert orjson.loads(response.content)['body_base64'] == 'cmVwbHk='
        assert sent == ['8.8.8.8']
        bookmark.allowed_hosts = {}; bookmark.save(update_fields=['allowed_hosts'])
        assert orjson.loads(call().content) == {'error': 'blocked', 'reason': 'not_allowed'}
        assert sent == ['8.8.8.8']
    else:
        assert orjson.loads(response.content) == {'error': 'blocked', 'reason': 'not_allowed'}
        assert sent == []
