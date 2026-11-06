"""Authenticated native transport shares instance authorization."""
from importlib import import_module
from uuid import uuid4

import pytest
from channels.sessions import SessionMiddlewareStack
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import AsyncClient

from twicc.auth.session_auth import bind_session
from twicc.tts import native_consumer, native_sessions

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


async def authenticated_cookie(password):
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    bind_session(session, password)
    await session.asave()
    return session, [(b'cookie', f'{settings.SESSION_COOKIE_NAME}={session.session_key}'.encode())]


async def socket(headers=(), client=('127.0.0.1', 123)):
    comm = WebsocketCommunicator(SessionMiddlewareStack(native_consumer.NativeTtsConsumer.as_asgi()),
                                '/ws/tts/', headers=list(headers))
    comm.scope['client'] = client
    return comm


async def test_authentication_reloads_storage_on_ping(settings):
    settings.TWICC_PASSWORD_HASH = 'password'
    session, headers = await authenticated_cookie('password')
    comm = await socket(headers)
    assert (await comm.connect())[0]
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    opened = await comm.receive_json_from()
    assert opened['type'] == 'opened_session'
    await comm.receive_json_from()
    await session.aflush()
    await comm.send_json_to({'protocol': 1, 'type': 'ping', 'seq': 2})
    assert (await comm.receive_json_from())['code'] == 'unauthorized'
    assert (await comm.receive_output())['type'] == 'websocket.close'
    assert native_sessions.registry.get(opened['session_id']) is None
    await comm.disconnect()


@pytest.mark.parametrize('password,cookie_password,client', [
    ('password', None, ('127.0.0.1', 1)), ('changed', 'old', ('127.0.0.1', 1)),
    ('', None, ('192.0.2.1', 1)),
])
async def test_rejects_unauthorized_socket(settings, password, cookie_password, client):
    settings.TWICC_PASSWORD_HASH = password
    headers = []
    if cookie_password:
        _, headers = await authenticated_cookie(cookie_password)
    comm = await socket(headers, client)
    assert (await comm.connect())[0]
    assert (await comm.receive_json_from())['code'] == 'unauthorized'
    assert (await comm.receive_output())['type'] == 'websocket.close'
    await comm.disconnect()


async def test_http_capabilities_and_ownership(settings):
    settings.TWICC_PASSWORD_HASH = 'password'
    client = AsyncClient()
    assert (await client.get('/api/tts/native/capabilities/')).status_code == 401
    cookie, _ = await authenticated_cookie('password')
    client.cookies[settings.SESSION_COOKIE_NAME] = cookie.session_key
    response = await client.get('/api/tts/native/capabilities/')
    assert response.json() == {'protocol': 1, 'available': True}
    session = native_sessions.registry.open(str(uuid4()), (cookie.session_key, cookie.get('auth_fingerprint')))
    url = f'/api/tts/native/{session.id}/audio/foreign/'
    assert (await client.get(url, headers={'X-TwiCC-TTS-Session': session.secret})).status_code == 404
    assert (await client.delete(url, headers={'X-TwiCC-TTS-Session': 'wrong'})).status_code == 404
    await native_sessions.registry.shutdown()


async def test_password_change_after_handshake_closes_native_session(settings):
    settings.TWICC_PASSWORD_HASH = 'password'
    _, headers = await authenticated_cookie('password')
    comm = await socket(headers)
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    opened = await comm.receive_json_from()
    await comm.receive_json_from()
    settings.TWICC_PASSWORD_HASH = 'replacement'
    await comm.send_json_to({'protocol': 1, 'type': 'stop_all', 'seq': 2})
    assert (await comm.receive_json_from())['code'] == 'unauthorized'
    assert native_sessions.registry.get(opened['session_id']) is None
    await comm.disconnect()


async def test_resume_unknown_or_wrong_secret_never_creates_session(settings):
    settings.TWICC_PASSWORD_HASH = ''
    existing = native_sessions.registry.open(str(uuid4()), ('', ''))
    for session_id, secret in [(str(uuid4()), 'unknown'), (existing.id, 'wrong')]:
        comm = await socket()
        await comm.connect()
        await comm.send_json_to({'protocol': 1, 'type': 'resume', 'seq': 1, 'session_id': session_id,
                                 'resume_secret': secret, 'ack_seq': 0, 'commands': [], 'current': None})
        assert (await comm.receive_json_from())['code'] == 'session_lost'
        await comm.disconnect()
    assert list(native_sessions.registry.sessions) == [existing.id]
    await native_sessions.registry.shutdown()


async def test_invalid_command_offers_recovery_and_logs_protocol_details(settings, caplog, monkeypatch):
    # Test settings disable existing loggers when the ASGI application initializes.
    monkeypatch.setattr(native_consumer.logger, 'disabled', False)
    settings.TWICC_PASSWORD_HASH = ''
    comm = await socket()
    await comm.connect()
    try:
        await comm.send_json_to({'protocol': 1, 'type': 'ping', 'seq': 1})
        frame = await comm.receive_json_from()
        assert frame['code'] == 'invalid_command'
        assert frame['message'] == 'Speech could not start. Reload the page and try again.'
        assert 'Open or resume a native session first' in caplog.text
        await comm.send_to(text_data='{broken JSON')
        assert (await comm.receive_json_from())['message'] == frame['message']
    finally:
        await comm.disconnect()


async def test_native_http_stream_and_ending_enforce_session_ownership(settings, monkeypatch):
    from unittest.mock import AsyncMock
    from twicc.tts.service import OpenedSpeech
    from twicc.tts.streams import get_stream, stream_from_bytes
    from tests.test_native_tts_sessions import manual
    settings.TWICC_PASSWORD_HASH = 'password'
    cookie, _ = await authenticated_cookie('password')
    client = AsyncClient()
    client.cookies[settings.SESSION_COOKIE_NAME] = cookie.session_key
    owner = native_sessions.registry.open(str(uuid4()), (cookie.session_key, cookie.get('auth_fingerprint')))
    other = native_sessions.registry.open(str(uuid4()), owner.auth_binding)
    stream = stream_from_bytes(b'owned audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    await owner.apply(manual())
    await owner.preparation
    path = f'/api/tts/native/{owner.id}/audio/{stream.id}/'
    other_path = f'/api/tts/native/{other.id}/audio/{stream.id}/'
    assert (await client.get(other_path, headers={'X-TwiCC-TTS-Session': other.secret})).status_code == 404
    assert (await client.delete(other_path, headers={'X-TwiCC-TTS-Session': other.secret})).status_code == 404
    assert get_stream(stream.id) is stream
    response = await client.get(path, headers={'X-TwiCC-TTS-Session': owner.secret})
    assert response.status_code == 200
    assert b''.join([chunk async for chunk in response.streaming_content]) == b'owned audio'
    ending = await client.get(path + 'ending/', headers={'X-TwiCC-TTS-Session': owner.secret})
    assert ending.json()['state'] == 'complete'
    for _ in range(2):
        assert (await client.delete(path, headers={'X-TwiCC-TTS-Session': owner.secret})).status_code == 204
    assert get_stream(stream.id) is None
    await native_sessions.registry.shutdown()


async def test_public_origin_gate_rejects_foreign_websocket_origin(settings):
    from twicc.asgi import application
    settings.TWICC_PASSWORD_HASH = ''
    comm = WebsocketCommunicator(application, '/ws/tts/',
                                 headers=[(b'host', b'testserver'), (b'origin', b'https://foreign.test')])
    assert await comm.connect() == (False, 4403)
    await comm.disconnect()


async def test_invalid_resume_ack_does_not_replace_live_connection(settings):
    settings.TWICC_PASSWORD_HASH = ''
    owner = await socket()
    await owner.connect()
    await owner.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    opened = await owner.receive_json_from()
    await owner.receive_json_from()
    original = native_sessions.registry.get(opened['session_id'])
    connection_id = original.connection_id
    resume = await socket()
    await resume.connect()
    await resume.send_json_to({'protocol': 1, 'type': 'resume', 'seq': 100, 'session_id': opened['session_id'],
                               'resume_secret': opened['resume_secret'], 'ack_seq': 99, 'commands': [], 'current': None})
    assert (await resume.receive_json_from())['code'] == 'invalid_command'
    assert original.connection_id == connection_id
    assert original.ack_seq == 1
    await resume.disconnect()
    await owner.disconnect()
    await native_sessions.registry.shutdown()


async def test_non_ascii_resume_secret_is_rejected_without_crashing(settings):
    settings.TWICC_PASSWORD_HASH = ''
    original = native_sessions.registry.open(str(uuid4()), ('', ''))
    comm = await socket()
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'resume', 'seq': 1, 'session_id': original.id,
                             'resume_secret': 'é', 'ack_seq': 0, 'commands': [], 'current': None})
    frame = await comm.receive_json_from()
    assert frame['code'] in ('invalid_command', 'session_lost')
    assert not original.closed
    await comm.disconnect()
    await native_sessions.registry.shutdown()


@pytest.mark.parametrize('hint', ['live', 'released'])
async def test_stop_auth_does_not_wait_for_cpu_heavy_summary_validation(settings, monkeypatch, hint):
    import asyncio
    import threading
    from tests.test_summary_service import complete_temporary_reference
    from twicc.providers.text_calls import ENGINES
    service = native_sessions.summaries
    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'auto'})
    monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
    monkeypatch.setattr(service, 'fallback_engine', lambda *_: None)
    page, reference = await complete_temporary_reference(service, monkeypatch, hint)
    settings.TWICC_PASSWORD_HASH = 'password'
    _, headers = await authenticated_cookie('password')
    comm = await socket(headers)
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    await comm.receive_json_from()
    await comm.receive_json_from()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_validate = service.validate_source
    calls = 0
    def validate(source):
        nonlocal calls
        calls += 1
        if calls == 1:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), 'Test must release controlled CPU validation'
        return original_validate(source)
    monkeypatch.setattr(service, 'validate_source', validate)
    monkeypatch.setattr('twicc.summaries.sources.validate_source', validate)
    try:
        from tests.test_native_tts_sessions import manual
        reading = manual(2)
        reading['request'].update(kind='summary', summary=reference)
        await comm.send_json_to(reading)
        assert (await comm.receive_json_from())['ack_seq'] == 2
        await asyncio.wait_for(entered.wait(), timeout=2)
        await comm.send_json_to({'protocol': 1, 'type': 'stop_all', 'seq': 3})
        assert (await comm.receive_json_from(timeout=0.5))['ack_seq'] == 3
    finally:
        release.set()
        await comm.disconnect()
        await service.release_summary(page.id)
        await native_sessions.registry.shutdown()


async def test_ordinary_ui_socket_keeps_its_unversioned_heartbeat(settings):
    from twicc.asgi import application
    settings.TWICC_PASSWORD_HASH = ''
    comm = WebsocketCommunicator(application, '/ws/?subscribe=active_processes',
                                 headers=[(b'host', b'testserver')])
    comm.scope['client'] = ('127.0.0.1', 123)
    comm.scope['root_path'] = ''
    assert (await comm.connect())[0]
    first = await comm.receive_json_from()
    assert first['type'] == 'active_processes'
    assert 'protocol' not in first
    await comm.send_json_to({'type': 'ping'})
    assert await comm.receive_json_from() == {'type': 'pong'}
    await comm.disconnect()


async def test_native_socket_reconnect_replays_stop_before_current_audio(settings, monkeypatch):
    from unittest.mock import AsyncMock
    from twicc.tts.service import OpenedSpeech
    from twicc.tts.streams import get_stream, stream_from_bytes
    from tests.test_native_tts_sessions import manual
    settings.TWICC_PASSWORD_HASH = ''
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    comm = await socket()
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    opened = await comm.receive_json_from()
    await comm.receive_json_from()
    await comm.send_json_to(manual(2))
    while (await comm.receive_json_from())['type'] != 'audio':
        pass
    await comm.disconnect()
    resumed = await socket()
    await resumed.connect()
    await resumed.send_json_to({'protocol': 1, 'type': 'resume', 'seq': 4, 'session_id': opened['session_id'],
                                 'resume_secret': opened['resume_secret'], 'ack_seq': 2,
                                 'commands': [{'protocol': 1, 'type': 'stop_all', 'seq': 3}], 'current': None})
    while True:
        frame = await resumed.receive_output()
        if frame['type'] == 'websocket.close':
            break
        import orjson
        assert orjson.loads(frame['text'])['type'] != 'audio'
    assert get_stream(stream.id) is None
    assert native_sessions.registry.get(opened['session_id']) is None
    await resumed.disconnect()
    await native_sessions.registry.shutdown()


async def test_stale_resume_sequence_preserves_owner_connection_state_and_writer(settings):
    settings.TWICC_PASSWORD_HASH = ''
    owner, rejected = await socket(), await socket()
    await owner.connect()
    await owner.send_json_to({'protocol': 1, 'type': 'open', 'seq': 10, 'client_id': str(uuid4())})
    opened = await owner.receive_json_from()
    await owner.receive_json_from()
    session = native_sessions.registry.get(opened['session_id'])
    connection_id, snapshot = session.connection_id, session.snapshot()
    try:
        await rejected.connect()
        await rejected.send_json_to({'protocol': 1, 'type': 'resume', 'seq': 2, 'session_id': session.id,
                                     'resume_secret': session.secret, 'ack_seq': 1, 'commands': [], 'current': None})
        assert (await rejected.receive_json_from())['code'] == 'invalid_command'
        assert session.connection_id == connection_id
        assert session.snapshot() == snapshot
        await owner.send_json_to({'protocol': 1, 'type': 'ping', 'seq': 11})
        assert (await owner.receive_json_from())['type'] == 'pong'
        assert (await owner.receive_json_from())['ack_seq'] == 11
        assert session.connection_id == connection_id
    finally:
        await rejected.disconnect()
        await owner.disconnect()
        await native_sessions.registry.shutdown()


async def test_socket_accepts_existing_worktree_listen_scope(settings):
    settings.TWICC_PASSWORD_HASH = ''
    comm = await socket()
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    await comm.receive_json_from()
    await comm.receive_json_from()
    try:
        await comm.send_json_to({'protocol': 1, 'type': 'listen', 'seq': 2, 'activation_id': str(uuid4()),
                                 'scope': {'kind': 'worktree', 'id': 'worktree-project'}, 'page_id': 'worktree-project',
                                 'include_muted': False, 'boundary': '2026-11-05T12:00:00.000000Z'})
        frame = await comm.receive_json_from()
        assert frame['type'] == 'state'
        assert frame['ack_seq'] == 2
    finally:
        await comm.disconnect()
        await native_sessions.registry.shutdown()


async def test_socket_reserves_temporary_summary_identity_with_null_key(settings, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from tests.test_native_tts_sessions import manual
    from twicc.tts.service import OpenedSpeech
    from twicc.tts.streams import stream_from_bytes
    settings.TWICC_PASSWORD_HASH = ''
    reader = Mock(id='independent-summary', aclose=AsyncMock())
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', AsyncMock(return_value=reader))
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    comm = await socket()
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    await comm.receive_json_from()
    await comm.receive_json_from()
    identity = {'sourceKey': None, 'sourceHash': 'a' * 64, 'variant': 'summary'}
    reading = manual(2)
    reading['request'].update(kind='summary', identity=identity,
                              summary={'source': {'text': 'Source', 'purpose': 'selection'},
                                       'source_key': None, 'source_hash': 'a' * 64, 'policy_key': 'policy:frozen'})
    try:
        await comm.send_json_to(reading)
        while True:
            frame = await comm.receive_json_from()
            assert frame['type'] != 'error'
            if frame['type'] == 'audio':
                assert frame['identity'] == identity
                break
    finally:
        await comm.disconnect()
        await native_sessions.registry.shutdown()


@pytest.mark.parametrize('older_seq,newer_seq', [(11, 12), (12, 11)])
async def test_overlapping_resumes_keep_newer_ack_state_and_writer(settings, monkeypatch, older_seq, newer_seq):
    import asyncio
    from unittest.mock import AsyncMock
    from tests.test_native_tts_sessions import manual
    from twicc.tts.service import OpenedSpeech
    from twicc.tts.streams import release_stream, stream_from_bytes
    settings.TWICC_PASSWORD_HASH = ''
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    session = native_sessions.registry.open(str(uuid4()), ('', ''))
    await session.apply(manual(10))
    await session.preparation
    release_stream(stream.id)
    entered, release = asyncio.Event(), asyncio.Event()
    class Coordinator:
        async def finished(self, reading, outcome):
            entered.set()
            await release.wait()
        async def close(self):
            pass
    session.coordinator = Coordinator()
    consumers = []
    original_connect = native_consumer.NativeTtsConsumer.connect
    async def capture_connect(consumer):
        consumers.append(consumer)
        await original_connect(consumer)
    monkeypatch.setattr(native_consumer.NativeTtsConsumer, 'connect', capture_connect)
    older, newer = await socket(), await socket()
    try:
        await older.connect()
        await older.send_json_to({'protocol': 1, 'type': 'resume', 'seq': older_seq, 'session_id': session.id,
                                  'resume_secret': session.secret, 'ack_seq': 10, 'commands': [], 'current': None})
        await asyncio.wait_for(entered.wait(), timeout=1)
        stale_consumer = consumers[-1]
        await newer.connect()
        await newer.send_json_to({'protocol': 1, 'type': 'resume', 'seq': newer_seq, 'session_id': session.id,
                                  'resume_secret': session.secret, 'ack_seq': 10, 'commands': [], 'current': None})
        frame = await newer.receive_json_from()
        assert frame['ack_seq'] == newer_seq
        assert consumers[-1]._sender is not None
        snapshot, connection_id = session.snapshot(), session.connection_id
        release.set()
        # An output proves the older callback reaches its handshake continuation.
        await older.receive_output()
        assert session.ack_seq == newer_seq
        assert session.snapshot() == snapshot
        assert session.connection_id == connection_id
        assert stale_consumer._sender is None
        await newer.send_json_to({'protocol': 1, 'type': 'ping', 'seq': 13})
        assert (await newer.receive_json_from())['type'] == 'pong'
        assert (await newer.receive_json_from())['ack_seq'] == 13
    finally:
        release.set()
        await older.disconnect()
        await newer.disconnect()
        await native_sessions.registry.shutdown()
