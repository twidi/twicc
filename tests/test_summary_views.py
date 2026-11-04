"""Authenticated API shape and incremental NDJSON delivery."""
import asyncio

import orjson
import pytest
from django.test import AsyncRequestFactory
from django.urls import resolve


@pytest.mark.parametrize('path,name', [('/api/summaries/resolve/', 'summaries_resolve'),
    ('/api/summaries/', 'summaries_open'), ('/api/summaries/abc/', 'summary_detail'),
    ('/api/summaries/saved/1/', 'summary_saved')])
def test_routes(path, name):
    assert resolve(path).func.__name__ == name


def test_shapes_and_methods(monkeypatch):
    from twicc.summaries import views
    factory = AsyncRequestFactory()
    async def scenario():
        for payload in [None, {}, {'sources': 'wrong'}, {'sources': [{}] * 101}]:
            response = await views.summaries_resolve(factory.post('/', orjson.dumps(payload), content_type='application/json'))
            assert response.status_code == 400
        for payload in [{}, {'source': {}, 'start': 1}, {'source': {}, 'regenerate': 'yes'}]:
            response = await views.summaries_open(factory.post('/', orjson.dumps(payload), content_type='application/json'))
            assert response.status_code == 400
        response = await views.summaries_open(factory.get('/'))
        assert response.status_code == 405 and response['Allow'] == 'POST'
        response = await views.summary_detail(factory.get('/'), 'gone')
        assert response.status_code == 410
        response = await views.summary_detail(factory.delete('/'), 'gone')
        assert response.status_code == 204
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_resolve_errors_are_per_source_and_do_not_start_work():
    from twicc.summaries import views
    async def scenario():
        response = await views.summaries_resolve(AsyncRequestFactory().post('/', orjson.dumps({'sources': [
            {'text': 'Selection', 'purpose': 'selection'}, {'session_id': 'missing', 'line_num': 1}]}), content_type='application/json'))
        assert response.status_code == 200
        result = orjson.loads(response.content)['sources']
        assert result[0]['eligible'] and result[0]['running'] is False
        assert 'error' in result[1]
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_ndjson_streams_before_completion_and_disconnect_releases(monkeypatch):
    from twicc.summaries import service, views
    from twicc.providers.text_calls import ENGINES
    factory = AsyncRequestFactory()
    async def scenario():
        gate, sent, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def stream(*args):
            try:
                yield 'Visible before completion'
                sent.set()
                await gate.wait()
                yield ' end'
            finally:
                closed.set()
        monkeypatch.setattr(service, 'read_synced_settings', dict)
        monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
        monkeypatch.setattr(service, 'stream_text', stream)
        response = await views.summaries_open(factory.post('/', orjson.dumps({'source': {'text': 'Selection', 'purpose': 'selection'}}), content_type='application/json'))
        handle = orjson.loads(response.content)['id']
        await sent.wait()
        response = await views.summary_detail(factory.get('/'), handle)
        assert response['Content-Type'].startswith('application/x-ndjson')
        iterator = response.streaming_content.__aiter__()
        first = orjson.loads(await anext(iterator))
        assert first['text'] == 'Visible before completion' and first['state'] == 'running'
        assert not gate.is_set()
        # Close the service iterator, as Django does on a disconnected request.
        await response._iterator.aclose()
        assert closed.is_set() and handle not in service._handles
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_invalid_json_ineligible_missing_saved_and_no_start_statuses(monkeypatch):
    from twicc.summaries import views
    factory = AsyncRequestFactory()
    async def scenario():
        for view in (views.summaries_resolve, views.summaries_open):
            response = await view(factory.post('/', b'{invalid', content_type='application/json'))
            assert response.status_code == 400
        response = await views.summaries_open(factory.post('/', orjson.dumps({'source': {
            'text': 'Temporary selection', 'purpose': 'selection'}, 'start': False}), content_type='application/json'))
        assert response.status_code == 409
        response = await views.summary_saved(factory.get('/'), 99999)
        assert response.status_code == 404
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_summary_routes_use_existing_authentication(settings):
    from django.test import AsyncClient
    settings.TWICC_PASSWORD_HASH = 'configured-password-hash'
    async def scenario():
        client = AsyncClient()
        for path in ('/api/summaries/', '/api/summaries/resolve/', '/api/summaries/id/', '/api/summaries/saved/1/'):
            response = await client.get(path)
            assert response.status_code == 401
    asyncio.run(scenario())
