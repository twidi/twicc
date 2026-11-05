"""Native ownership, idempotent ordering, cancellation and resume."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from twicc.tts import native_sessions
from twicc.tts.service import OpenedSpeech
from twicc.tts.streams import get_stream, stream_from_bytes


def command(kind, seq, **values):
    return {'protocol': 1, 'type': kind, 'seq': seq, **values}


def manual(seq=1, generation=1):
    return command('manual', seq, reading_id=str(uuid4()), generation=generation,
                   request={'kind': 'text', 'source': 'message', 'speech': {'text': 'Hello'},
                            'identity': None, 'text': 'Hello', 'config_id': None})


def drain_frames(session):
    frames = list(session._frames)
    session._frames.clear()
    return frames


async def settle(session):
    if session.preparation:
        await session.preparation


@pytest.mark.asyncio
async def test_resume_resends_unclaimed_resource_without_regeneration(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    prepare = AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type))
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fingerprint'))
    await session.apply(manual())
    await settle(session)
    first_audio = next(f for f in drain_frames(session) if f['type'] == 'audio')
    session.disconnect()
    resumed = registry.resume(session.id, session.secret, session.auth_binding)
    await resumed.reconcile([], None)
    resumed_audio = next(f for f in drain_frames(session) if f['type'] == 'audio')
    assert resumed_audio['audio_id'] == first_audio['audio_id']
    assert prepare.await_count == 1
    other = registry.open(str(uuid4()), session.auth_binding)
    assert stream.id not in other.owned_audio
    await registry.shutdown()
    assert get_stream(stream.id) is None


@pytest.mark.asyncio
async def test_duplicate_manual_and_stale_callbacks_do_not_change_current(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    prepare = AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type))
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    first = manual()
    await session.apply(first)
    await session.apply(first)
    await settle(session)
    await session.apply(command('finished', 2, reading_id=str(uuid4()), generation=1, outcome='complete'))
    assert session.snapshot()['playback']['reading_id'] == first['reading_id']
    assert prepare.await_count == 1
    await registry.shutdown()


@pytest.mark.asyncio
async def test_superseded_generation_releases_late_audio(monkeypatch):
    gate = asyncio.Event()
    first_stream = stream_from_bytes(b'old', 'audio/mpeg', label={})
    second_stream = stream_from_bytes(b'new', 'audio/mpeg', label={})
    calls = 0
    async def prepare(data):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                await gate.wait()
            return OpenedSpeech(first_stream.id, first_stream.content_type)
        return OpenedSpeech(second_stream.id, second_stream.content_type)
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    await session.apply(manual())
    await asyncio.sleep(0)
    old_task = session.preparation
    await session.apply(manual(2, 2))
    gate.set()
    await old_task
    await settle(session)
    assert get_stream(first_stream.id) is None
    assert session.snapshot()['playback']['audio_id'] == second_stream.id
    await registry.shutdown()


@pytest.mark.asyncio
async def test_offline_stop_replays_before_any_audio(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    await session.apply(manual())
    await settle(session)
    drain_frames(session)
    session.disconnect()
    registry.resume(session.id, session.secret, session.auth_binding)
    await session.reconcile([command('stop_all', 2)], None)
    assert all(f['type'] != 'audio' for f in drain_frames(session))
    assert session.closed
    assert get_stream(stream.id) is None
    await registry.shutdown()


@pytest.mark.asyncio
async def test_disconnect_expiry_and_wrong_binding():
    now = [0.0]
    registry = native_sessions.NativeSessionRegistry(clock=lambda: now[0])
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    with pytest.raises(native_sessions.SessionLost):
        registry.resume(session.id, session.secret, ('other', 'fp'))
    with pytest.raises(native_sessions.SessionLost):
        registry.resume(session.id, 'wrong', session.auth_binding)
    session.disconnect()
    now[0] = 119
    await registry.expire()
    assert not session.closed
    now[0] = 120
    await registry.expire()
    assert session.closed
    with pytest.raises(native_sessions.SessionLost):
        registry.resume(session.id, session.secret, session.auth_binding)
    await registry.shutdown()


@pytest.mark.asyncio
async def test_resume_started_feedback_is_idempotent(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    reading = manual()
    await session.apply(reading)
    await settle(session)
    coordinator = AsyncMock()
    session.coordinator = coordinator
    current = {'reading_id': reading['reading_id'], 'generation': 1, 'audio_id': stream.id,
               'phase': 'playing', 'started': True}
    await session.reconcile([], current)
    await session.reconcile([], current)
    assert coordinator.started.await_count == 1
    assert session.snapshot()['playback']['started'] is True
    await registry.shutdown()


@pytest.mark.asyncio
async def test_missing_audio_fails_reading_without_regeneration(monkeypatch):
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech('expired', 'audio/mpeg')))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    await session.apply(manual())
    await settle(session)
    await session.reconcile([], None)
    assert session.snapshot()['error']['code'] == 'audio_expired'
    assert not session.owned_audio
    await registry.shutdown()


@pytest.mark.asyncio
async def test_native_lease_preserves_disconnected_audio_but_browser_audio_expires(monkeypatch):
    from twicc.tts import streams
    monkeypatch.setattr(streams, 'UNCLAIMED_TTL', 0.01)
    native = stream_from_bytes(b'native', 'audio/mpeg', label={})
    browser = stream_from_bytes(b'browser', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(native.id, native.content_type)))
    now = [0.0]
    registry = native_sessions.NativeSessionRegistry(clock=lambda: now[0])
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    await session.apply(manual())
    await settle(session)
    session.disconnect()
    await asyncio.sleep(0.02)
    assert get_stream(browser.id) is None
    assert get_stream(native.id) is native
    assert native.claimed is False
    now[0] = 119
    await registry.expire()
    assert get_stream(native.id) is native
    now[0] = 120
    await registry.expire()
    assert get_stream(native.id) is None
    await registry.shutdown()


@pytest.mark.asyncio
async def test_foreign_finish_and_release_cannot_touch_another_session(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    owner = registry.open(str(uuid4()), ('cookie', 'fp'))
    other = registry.open(str(uuid4()), owner.auth_binding)
    reading = manual()
    await owner.apply(reading)
    await settle(owner)
    assert await other.release_audio(stream.id) is False
    await other.apply(command('finished', 1, reading_id=reading['reading_id'], generation=1, outcome='complete'))
    assert get_stream(stream.id) is stream
    await owner.apply(command('finished', 2, reading_id=reading['reading_id'], generation=1, outcome='complete'))
    assert get_stream(stream.id) is None
    assert await owner.release_audio(stream.id) is True
    await registry.shutdown()


@pytest.mark.asyncio
async def test_invalid_frame_cannot_advance_ack_or_cancel_reading(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    await session.apply(manual())
    await settle(session)
    with pytest.raises(ValueError):
        await session.apply(command('stop_all', 2, auth_binding=['replacement']))
    assert session.ack_seq == 1
    assert get_stream(stream.id) is stream
    await registry.shutdown()


@pytest.mark.asyncio
async def test_manual_reserve_blocks_automatic_selection(monkeypatch):
    gate = asyncio.Event()
    async def prepare(data):
        await gate.wait()
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    reading = manual()
    await session.apply(reading)
    accepted = await session.select_reading(command('select', 2, reading_id=str(uuid4()), generation=2, event_id=1),
                                            reading['request'], {'identity': None})
    assert accepted is False
    assert session.snapshot()['playback']['reading_id'] == reading['reading_id']
    await registry.shutdown()


@pytest.mark.asyncio
async def test_offline_select_then_skip_never_resends_selected_audio(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    selection = command('select', 1, reading_id=str(uuid4()), generation=1, event_id=1)
    class Coordinator:
        async def command(self, cmd):
            if cmd['type'] == 'select':
                await session.select_reading(cmd, manual()['request'], {'identity': None})
                await settle(session)
        async def started(self, reading):
            pass
        async def finished(self, reading, outcome):
            pass
        async def close(self):
            pass
    session.coordinator = Coordinator()
    session.disconnect()
    registry.resume(session.id, session.secret, session.auth_binding)
    await session.reconcile([selection, command('skip', 2, reading_id=selection['reading_id'], generation=1)], None)
    assert session.snapshot()['playback'] is None
    assert all(frame['type'] != 'audio' for frame in drain_frames(session))
    assert get_stream(stream.id) is None
    await registry.shutdown()


@pytest.mark.asyncio
async def test_heartbeat_expiry_detaches_then_releases_resources(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    now = [0.0]
    registry = native_sessions.NativeSessionRegistry(clock=lambda: now[0])
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    session.attach()
    await session.apply(manual())
    await settle(session)
    now[0] = 75
    await registry.expire()
    assert session.connection_id is None
    assert get_stream(stream.id) is stream
    now[0] = 195
    await registry.expire()
    assert get_stream(stream.id) is None
    await registry.shutdown()


@pytest.mark.asyncio
async def test_summary_retains_reader_until_shared_opener_owns_it(monkeypatch):
    from unittest.mock import Mock
    reader = Mock(id='native-summary', aclose=AsyncMock())
    retained = AsyncMock(return_value=reader)
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retained)
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    async def prepare(data):
        assert data == {'summary_id': 'native-summary'}
        assert reader.aclose.await_count == 0
        return OpenedSpeech(stream.id, stream.content_type)
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    request = manual()
    request['request'].update(kind='summary', summary={'saved_id': 1, 'result_hash': 'a' * 64})
    await session.apply(request)
    await settle(session)
    assert session.snapshot()['playback']['audio_id'] == stream.id
    assert reader.aclose.await_count == 1
    await registry.shutdown()


@pytest.mark.asyncio
async def test_summary_changed_keeps_http_conflict_metadata(monkeypatch):
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech',
                        AsyncMock(side_effect=native_sessions.summaries.SummaryChanged(19)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    request = manual()
    request['request'].update(kind='summary', summary={'saved_id': 19, 'result_hash': 'a' * 64})
    await session.apply(request)
    await settle(session)
    error = session.snapshot()['error']
    assert error['status'] == 409
    assert error['saved_id'] == 19
    assert error['summary_changed'] is True
    await registry.shutdown()


@pytest.mark.asyncio
async def test_resume_offers_only_last_candidate_after_all_replay_commands():
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    activation_id = str(uuid4())
    observed = []
    class Coordinator:
        async def command(self, cmd):
            observed.append(cmd['seq'])
            await session.offer_candidate({'event_id': cmd['seq'], 'activation_id': activation_id,
                                           'identity': None, 'text': 'Candidate'})
        async def close(self):
            pass
    session.coordinator = Coordinator()
    session.listening = {'activation_id': activation_id}
    await session.reconcile([command('set_muted', 1, activation_id=activation_id, include_muted=True),
                             command('set_muted', 2, activation_id=activation_id, include_muted=False)], None)
    candidates = [frame for frame in drain_frames(session) if frame['type'] == 'candidate']
    assert observed == [1, 2]
    assert [frame['event_id'] for frame in candidates] == [2]
    await registry.shutdown()


@pytest.mark.asyncio
async def test_stop_all_during_summary_validation_is_immediate(monkeypatch):
    started, gate = asyncio.Event(), asyncio.Event()
    cancelled = asyncio.Event()
    async def retain(reference):
        started.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retain)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    request = manual()
    request['request'].update(kind='summary', summary={'saved_id': 1, 'result_hash': 'a' * 64})
    await session.apply(request)
    assert session.ack_seq == 1
    await started.wait()
    await asyncio.wait_for(session.apply(command('stop_all', 2)), timeout=0.5)
    assert session.closed
    assert cancelled.is_set()
    await registry.shutdown()


@pytest.mark.asyncio
async def test_preparation_failure_notifies_coordinator_to_advance(monkeypatch):
    from twicc.tts.service import SpeechError
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(side_effect=SpeechError('Unavailable', 424)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    coordinator = AsyncMock()
    session.coordinator = coordinator
    reading = manual()
    await session.apply(reading)
    await settle(session)
    assert session.snapshot()['playback'] is None
    assert coordinator.finished.await_count == 1
    assert coordinator.finished.call_args.args[0]['reading_id'] == reading['reading_id']
    assert coordinator.finished.call_args.args[1] == 'failed'
    await registry.shutdown()


@pytest.mark.asyncio
async def test_manual_replacement_notifies_coordinator_of_discarded_selection(monkeypatch):
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    monkeypatch.setattr(native_sessions, 'open_speech', AsyncMock(return_value=OpenedSpeech(stream.id, stream.content_type)))
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    coordinator = AsyncMock()
    session.coordinator = coordinator
    first = manual()
    await session.apply(first)
    await settle(session)
    await session.apply(manual(2, 2))
    await settle(session)
    assert coordinator.finished.await_count == 1
    assert coordinator.finished.call_args.args[0]['reading_id'] == first['reading_id']
    assert coordinator.finished.call_args.args[1] == 'aborted'
    await registry.shutdown()


@pytest.mark.asyncio
async def test_manual_summary_reconstructs_after_page_releases_before_acquisition(monkeypatch):
    from tests.test_summary_service import complete_temporary_reference
    from twicc.providers.text_calls import ENGINES
    service = native_sessions.summaries
    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'auto'})
    monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
    monkeypatch.setattr(service, 'fallback_engine', lambda *_: None)
    page, reference = await complete_temporary_reference(service, monkeypatch, 'live')
    entered, gate = asyncio.Event(), asyncio.Event()
    original_retain = service.retain_summary_for_speech
    async def retain(source):
        entered.set()
        await gate.wait()
        return await original_retain(source)
    monkeypatch.setattr(service, 'retain_summary_for_speech', retain)
    stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
    async def prepare(data):
        reader = await service.acquire_summary_reader(data['summary_id'])
        snapshots = [snapshot async for snapshot in reader.snapshots()]
        assert snapshots[-1]['text'] == 'Summary of source A.'
        assert reader.id != page.id
        await reader.aclose()
        return OpenedSpeech(stream.id, stream.content_type)
    monkeypatch.setattr(native_sessions, 'open_speech', prepare)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    request = manual()
    request['request'].update(kind='summary', summary=reference)
    await session.apply(request)
    await entered.wait()
    await service.release_summary(page.id)
    gate.set()
    await settle(session)
    assert session.snapshot()['playback']['audio_id'] == stream.id
    assert page.id not in service._handles
    await registry.shutdown()


@pytest.mark.asyncio
async def test_resume_resends_last_unselected_candidate_without_a_timeline_event():
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    activation_id = str(uuid4())
    session.listening = {'activation_id': activation_id}
    await session.offer_candidate({'activation_id': activation_id, 'event_id': 17, 'identity': None, 'text': 'Ready'})
    drain_frames(session)
    session.disconnect()
    registry.resume(session.id, session.secret, session.auth_binding)
    await session.reconcile([], None)
    assert [frame['event_id'] for frame in drain_frames(session) if frame['type'] == 'candidate'] == [17]
    await registry.shutdown()


@pytest.mark.asyncio
async def test_queued_old_connection_command_cannot_mutate_after_replacement():
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    old_connection = session.attach()
    await session._commands.acquire()
    try:
        pending = asyncio.create_task(session.apply(command('stop_all', 1), connection_id=old_connection))
        await asyncio.sleep(0)
        new_connection = session.attach()
    finally:
        session._commands.release()
    with pytest.raises(native_sessions.SessionLost):
        await pending
    assert not session.closed
    assert session.ack_seq == 0
    assert session.connection_id == new_connection
    await registry.shutdown()
