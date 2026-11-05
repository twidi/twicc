"""Native timeline selection uses saved events and actual playback feedback."""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from asgiref.sync import sync_to_async

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_feed import AFTER_ID_CAP, format_occurred_at
from twicc.tts import native_sessions, native_timeline
from twicc.tts.service import OpenedSpeech
from twicc.tts.streams import stream_from_bytes

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]
CLICK = datetime(2026, 11, 4, 10, 30, tzinfo=UTC)
FIELDS = {'model': 'tts', 'voice': 'voice', 'response_format': 'mp3', 'speed': None, 'instructions': ''}


def activation(kind='project', scope_id='p', include_muted=False):
    return {'activation_id': str(uuid4()), 'page_id': scope_id or 'all', 'scope': {'kind': kind, 'id': scope_id},
            'boundary': format_occurred_at(CLICK), 'include_muted': include_muted}


async def event(second=1, project='p', session='s', kind='assistant_message.sent', text=None):
    row = await sync_to_async(record_event_sync)(kind, project_id=project, session_id=session,
        occurred_at=CLICK + timedelta(seconds=second), provider='codex',
        data=({'text': text or f'Message {second}', 'source_refs': [], 'source_line': 1}
              if kind == 'assistant_message.sent' else {}))
    assert row is not None
    return row


@pytest.fixture
def harness(monkeypatch):
    Project.objects.create(id='p', name='Project', directory='/tmp/p')
    Project.objects.create(id='q', name='Other', directory='/tmp/q')
    Project.objects.create(id='wt', name='Worktree', worktree_of_id='p')
    Session.objects.create(id='s', project_id='p', provider='codex', title='Session')
    policy = {'entry': {'id': 'voice', 'kind': 'openrouter', **FIELDS}, 'adapt': 'off', 'summary': False}
    monkeypatch.setattr(native_timeline, '_read_policy', lambda: policy.copy())
    workspaces = {'workspaces': [{'id': 'w', 'name': 'Workspace', 'projectIds': ['p']}]}
    monkeypatch.setattr(native_timeline, 'read_workspaces', lambda: workspaces)
    registry = native_sessions.NativeSessionRegistry()
    session = registry.open(str(uuid4()), ('cookie', 'fp'))
    listener = session.coordinator
    listener.clock = lambda: CLICK + timedelta(seconds=50)
    opened = []
    async def open_speech(body, **options):
        opened.append((body.copy(), options))
        stream = stream_from_bytes(b'audio', 'audio/mpeg', label={})
        return OpenedSpeech(stream.id, stream.content_type)
    monkeypatch.setattr(native_sessions, 'open_speech', open_speech)
    return SimpleNamespace(registry=registry, session=session, listener=listener, policy=policy,
                           workspaces=workspaces, opened=opened)


async def select(h, row, generation=1):
    reading = str(uuid4())
    await h.listener.select(row.id, reading, generation)
    if h.session.preparation:
        await h.session.preparation
    return reading


async def finish(h, outcome='complete', started=True):
    reading = h.session.playback.copy()
    if started:
        await h.session.apply({'protocol': 1, 'type': 'started', 'seq': h.session.ack_seq + 1,
                              'reading_id': reading['reading_id'], 'generation': reading['generation']})
    await h.session.apply({'protocol': 1, 'type': 'finished', 'seq': h.session.ack_seq + 1,
                          'reading_id': reading['reading_id'], 'generation': reading['generation'], 'outcome': outcome})


@pytest.mark.parametrize(('kind', 'scope_id', 'expected'), [
    ('global', None, ['p', 'q', 'wt']), ('workspace', 'w', ['p', 'wt']),
    ('project', 'p', ['p', 'wt']), ('worktree', 'wt', ['wt']),
])
async def test_named_scopes_use_server_membership(harness, kind, scope_id, expected):
    h = harness
    _rows = [await event(project=p) for p in ('p', 'q', 'wt')]
    await h.listener.enable(activation(kind, scope_id))
    assert [e['project_id'] for e in h.listener.pending] == expected
    assert h.opened == []
    assert h.session.owned_audio == set()
    await h.registry.shutdown()


async def test_click_boundary_order_dedup_and_stable_selected_event(harness):
    h = harness
    await event(-1); await event(0)
    late = await event(4)
    first = await event(2)
    tie = await event(2)
    await h.listener.enable(activation())
    assert [e['id'] for e in h.listener.pending] == [first.id, tie.id, late.id]
    reading = await select(h, first)
    older = await event(1)
    await h.listener.refresh(); await h.listener.refresh()
    assert h.session.playback['reading_id'] == reading
    assert [e['id'] for e in h.listener.pending] == [older.id, tie.id, late.id]
    assert len(h.opened) == 1
    await h.registry.shutdown()


async def test_watermark_updates_only_on_start_and_has_inclusive_five_second_tolerance(harness):
    h = harness
    first = await event(40)
    await h.listener.enable(activation()); await select(h, first)
    assert h.listener.watermark is None
    reading = h.session.playback.copy()
    await h.listener.started(reading)
    assert h.listener.watermark == first.occurred_at
    inside = await event(35); await event(34)
    await h.listener.refresh()
    assert [e['id'] for e in h.listener.pending] == [inside.id]
    await finish(h)
    await select(h, inside, 2)
    await h.listener.started(h.session.playback)
    assert h.listener.watermark == first.occurred_at
    await h.registry.shutdown()


async def test_muted_changes_do_not_replay_and_selected_reading_finishes(harness):
    h = harness
    first = await event(1); queued = await event(2)
    await h.listener.enable(activation()); await select(h, first)
    await Session.objects.filter(pk='s').aupdate(mute_on_user_turn=True)
    await h.listener.refresh()
    assert h.listener.pending == []
    assert h.session.playback is not None
    skipped = await event(3)
    await h.listener.refresh()
    await h.listener.set_muted(True)
    assert h.listener.pending == []
    future = await event(4)
    await h.listener.refresh()
    assert [e['id'] for e in h.listener.pending] == [future.id]
    assert queued.id in h.listener.seen and skipped.id in h.listener.seen
    await h.listener.set_muted(False)
    assert h.listener.pending == []
    await h.registry.shutdown()


async def test_hidden_then_shown_never_replays_old_messages(harness):
    h = harness
    await h.listener.enable(activation())
    await Session.objects.filter(pk='s').aupdate(hidden=True)
    await event(1)
    await h.listener.refresh()
    await Session.objects.filter(pk='s').aupdate(hidden=False)
    await event(3, kind='hidden.ended')
    future = await event(4)
    await h.listener.refresh()
    assert [e['id'] for e in h.listener.pending] == [future.id]
    await h.registry.shutdown()


async def test_membership_changes_use_current_boundary_and_empty_scope_stops(harness):
    h = harness
    await h.listener.enable(activation('workspace', 'w'))
    old = await event(20, project='q')
    h.workspaces['workspaces'][0]['projectIds'].append('q')
    await h.listener.refresh()
    new = await event(51, project='q')
    await h.listener.refresh()
    assert [e['id'] for e in h.listener.pending] == [new.id]
    assert old.id not in [e['id'] for e in h.listener.pending]
    h.workspaces['workspaces'][0]['projectIds'] = []
    await h.listener.refresh()
    assert h.session.listening is None
    await h.registry.shutdown()


async def test_disconnection_catchup_never_replays_truncated_history(harness):
    h = harness
    await h.listener.enable(activation())
    h.session.disconnect()
    # Include rows that automatic speech excludes. The feed cap applies before filtering.
    for index in range(AFTER_ID_CAP + 1):
        await event(index + 1, kind='turn.started')
    await h.listener.refresh()
    state = h.session.snapshot()
    played_event_ids = [body.get('text') for body, _ in h.opened]
    assert state['listening'] is None
    assert state['error']['code'] == 'catchup_incomplete'
    assert played_event_ids == []
    await h.registry.shutdown()


async def test_short_disconnect_preserves_boundary_cursor_seen_muted_and_pending(harness):
    h = harness
    first = await event(1)
    active = activation(include_muted=True)
    await h.listener.enable(active)
    cursor = h.listener.cursor
    h.session.disconnect()
    second = await event(2)
    await h.listener.refresh()
    await h.session.reconcile([], None)
    assert h.session.listening['boundary'] == active['boundary']
    assert h.session.listening['include_muted'] is True
    assert h.listener.cursor > cursor
    assert [e['id'] for e in h.listener.pending] == [first.id, second.id]
    assert {first.id, second.id} <= h.listener.seen
    assert h.opened == []
    await h.registry.shutdown()


async def test_manual_preemption_discards_selected_only_and_disable_preserves_manual(harness):
    h = harness
    first = await event(1); second = await event(2)
    await h.listener.enable(activation()); await select(h, first)
    manual_id = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'manual', 'seq': 1, 'reading_id': manual_id, 'generation': 2,
        'request': {'kind': 'text', 'source': 'selection', 'text': 'Manual', 'identity': None,
                    'speech': {'text': 'Manual'}, 'config_id': None}})
    await h.session.preparation
    assert [e['id'] for e in h.listener.pending] == [second.id]
    await h.listener.disable()
    assert h.session.playback['reading_id'] == manual_id
    assert h.session.listening is None
    await h.registry.shutdown()


async def test_announcements_follow_actual_starts_and_manual_invalidates_context(harness):
    h = harness
    first = await event(1); second = await event(2); third = await event(3)
    await h.listener.enable(activation()); await select(h, first)
    assert h.opened[-1][0]['announcement'] == 'Project. Session.'
    assert h.listener.context is None
    await finish(h)
    await select(h, second, 2)
    assert h.opened[-1][0]['announcement'] == ''
    await h.listener.started({'automatic': False, 'reading_id': 'manual'})
    await finish(h, started=False)
    await select(h, third, 3)
    assert h.opened[-1][0]['announcement'] == 'Project. Session.'
    await h.registry.shutdown()


async def test_unavailable_configuration_stops_without_opening_audio(harness):
    h = harness
    await event(1)
    h.policy['entry'] = None
    await h.listener.enable(activation())
    assert h.session.listening is None
    assert h.session.configuration_available is False
    assert h.opened == []
    await h.registry.shutdown()


async def test_shared_policy_fixtures(harness):
    h = harness
    cases = orjson.loads((Path(__file__).parent / 'fixtures/native_tts_timeline_cases.json').read_bytes())
    for case in cases:
        await TimelineEvent.objects.all().adelete()
        await h.listener.enable(activation())
        for item in case['events']:
            await event(item['second'], text=f"Fixture {item['id']}")
        await h.listener.refresh()
        assert [e['data']['text'] for e in h.listener.pending] == [f'Fixture {i}' for i in case['expected']]
        await h.listener.disable()
    await h.registry.shutdown()


async def test_arrival_during_initialization_is_not_lost(harness, monkeypatch):
    h = harness
    entered, release = asyncio.Event(), asyncio.Event()
    async def subscribe():
        entered.set()
        await release.wait()
    monkeypatch.setattr(h.listener, '_subscribe', subscribe)
    active = activation()
    await h.session.apply({'protocol': 1, 'type': 'listen', 'seq': 1, **active})
    await entered.wait()
    row = await event(1)
    assert h.session.listening['boundary'] == active['boundary']
    release.set()
    await asyncio.gather(*list(h.listener._tasks))
    assert [e['id'] for e in h.listener.pending] == [row.id]
    await h.registry.shutdown()


@pytest.fixture
def fake_summaries(monkeypatch):
    handles, released, acquired = {}, [], []
    saved_ids = set()
    def descriptors(sources):
        return [{'source_key': f"event-{source['event_id']}", 'source_hash': 'a' * 64,
                 'policy_key': 'policy:old', 'eligible': True,
                 'saved': {'id': source['event_id'], 'result_hash': 'b' * 64}
                    if source['event_id'] in saved_ids else None} for source in sources]
    async def acquire(source, **options):
        event_id = source['event_id']
        acquired.append(event_id)
        handle = SimpleNamespace(id=f'handle-{event_id}-{len(acquired)}',
                                 state='complete' if event_id in saved_ids else 'running', claim=lambda: None)
        handles[handle.id] = handle
        return handle
    async def release(handle_id):
        released.append(handle_id)
        handles.pop(handle_id, None)
    async def snapshots(handle_id):
        yield {'state': handles[handle_id].state}
        await asyncio.Event().wait()
    async def resolve(event_id):
        return None, descriptors([{'event_id': event_id}])[0]
    monkeypatch.setattr(native_timeline.summaries, 'resolve_timeline_summary', resolve)
    monkeypatch.setattr(native_timeline.summaries, 'acquire_summary', acquire)
    monkeypatch.setattr(native_timeline.summaries, 'release_summary', release)
    monkeypatch.setattr(native_timeline.summaries, 'summary_snapshots', snapshots)
    return SimpleNamespace(handles=handles, released=released, acquired=acquired, saved_ids=saved_ids)


async def test_two_unsaved_summary_preparations_and_saved_results_bypass_slots(harness, fake_summaries):
    h, fake = harness, fake_summaries
    h.policy['summary'] = True
    rows = [await event(i) for i in range(1, 6)]
    fake.saved_ids.add(rows[-1].id)
    await h.listener.enable(activation())
    assert fake.acquired == [rows[0].id, rows[1].id, rows[-1].id]
    assert h.opened == []
    first_handle = h.listener.preparations[rows[0].id]['handle']
    first_handle.state = 'complete'
    await h.listener.refresh()
    assert fake.acquired[-1] == rows[2].id
    await h.listener.disable()
    assert not fake.handles
    await h.registry.shutdown()


async def test_selected_summary_freezes_config_adapt_and_announcement(harness, fake_summaries, monkeypatch):
    h = harness
    h.policy['summary'] = True
    script = {'id': 'script', 'kind': 'script', 'command': 'old-command', 'output': 'stream',
              'audio_format': 'mp3', 'pcm_rate': None, 'pcm_channels': 1, 'max_chars': None,
              'delete_file': True}
    from twicc.tts.config import script_fields
    script['validated'] = script_fields(script)
    h.policy.update(entry=script, adapt='haiku')
    first = await event(1); await event(2)
    await h.listener.enable(activation())
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []
    async def retain(reference):
        entered.set()
        await release.wait()
        async def close():
            closed.append(reference['handle_id'])
        return SimpleNamespace(id='reader', aclose=close)
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retain)
    await h.listener.select(first.id, str(uuid4()), 1)
    await entered.wait()
    selected_handle = h.listener.preparations[first.id]['handle'].id
    h.policy.update(entry={**script, 'command': 'new-command'}, adapt='off', summary=False)
    await h.listener.refresh()
    assert selected_handle not in fake_summaries.released
    release.set()
    await h.session.preparation
    body, options = h.opened[-1]
    assert options['_policy']['entry']['command'] == 'old-command'
    assert options['_policy']['adapt'] == 'haiku'
    assert body['announcement'] == 'Project. Session.'
    await finish(h)
    assert selected_handle in fake_summaries.released
    assert closed == [selected_handle]
    await h.registry.shutdown()


async def test_message_failure_discards_it_and_offers_next(harness, monkeypatch):
    h = harness
    first = await event(1); second = await event(2)
    await h.listener.enable(activation())
    from twicc.tts.service import SpeechError
    async def fail(body, **kwargs):
        raise SpeechError('Message generation failed', 424)
    monkeypatch.setattr(native_sessions, 'open_speech', fail)
    await select(h, first)
    await h.listener.refresh()
    assert h.session.playback is None
    assert h.session._candidate['event_id'] == second.id
    assert h.listener.context is None
    await h.registry.shutdown()


async def test_scope_deletion_stops_and_devices_do_not_share_ownership(harness):
    h = harness
    row = await event(1)
    await h.listener.enable(activation())
    other = h.registry.open(str(uuid4()), h.session.auth_binding)
    await other.coordinator.enable(activation('workspace', 'w'))
    await h.listener.disable()
    assert other.listening is not None
    assert [e['id'] for e in other.coordinator.pending] == [row.id]
    h.workspaces['workspaces'].clear()
    await other.coordinator.refresh()
    assert other.listening is None
    from twicc.tts.native_sessions import SessionLost
    empty = native_sessions.NativeSessionRegistry()
    with pytest.raises(SessionLost):
        empty.resume(other.id, other.secret, other.auth_binding)
    await h.registry.shutdown()


async def test_event_summary_cpu_parsing_keeps_authenticated_stop_responsive(harness, settings, monkeypatch):
    import threading
    from tests.test_native_tts_transport import authenticated_cookie, socket
    from twicc.summaries import sources
    h = harness
    h.policy['summary'] = True
    await event(1, text='A large message. ' * 100)
    settings.TWICC_PASSWORD_HASH = 'password'
    _, headers = await authenticated_cookie('password')
    comm = await socket(headers)
    await comm.connect()
    await comm.send_json_to({'protocol': 1, 'type': 'open', 'seq': 1, 'client_id': str(uuid4())})
    await comm.receive_json_from(); await comm.receive_json_from()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = sources._input_text
    def parse(text):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), 'Release controlled Markdown work'
        return original(text)
    monkeypatch.setattr(sources, '_input_text', parse)
    try:
        await comm.send_json_to({'protocol': 1, 'type': 'listen', 'seq': 2, **activation()})
        # Ignore the activation snapshot. Stop must reauthenticate from storage while parsing is held.
        await asyncio.wait_for(entered.wait(), timeout=2)
        await comm.send_json_to({'protocol': 1, 'type': 'stop_all', 'seq': 3})
        while True:
            frame = await comm.receive_json_from(timeout=0.5)
            if frame.get('ack_seq') == 3:
                break
    finally:
        release.set()
        from contextlib import suppress
        with suppress(asyncio.CancelledError):
            await comm.disconnect()
        await native_sessions.registry.shutdown()
        await h.registry.shutdown()


async def test_select_waiting_for_background_preparation_does_not_block_skip_or_revive(harness, monkeypatch):
    h = harness
    first = await event(1)
    active = activation()
    await h.listener.enable(active)
    entered, release = asyncio.Event(), asyncio.Event()
    async def background_refresh():
        async with h.listener._lock:
            entered.set()
            await release.wait()
    blocked = asyncio.create_task(background_refresh())
    await entered.wait()
    reading_id = str(uuid4())
    selection = asyncio.create_task(h.session.apply({'protocol': 1, 'type': 'select', 'seq': 1,
        'event_id': first.id, 'reading_id': reading_id, 'generation': 1}))
    try:
        await asyncio.sleep(0)
        # The lane must remain available while selection waits on background work.
        await asyncio.wait_for(h.session.apply({'protocol': 1, 'type': 'skip', 'seq': 2,
            'reading_id': reading_id, 'generation': 1}), timeout=0.2)
    finally:
        release.set()
        await blocked
        await selection
    await asyncio.gather(*list(h.listener._tasks))
    assert h.session.playback is None
    assert h.opened == []
    assert h.listener.pending == []
    await h.registry.shutdown()


async def test_update_subscription_ignores_unrelated_payload_and_admits_saved_events(harness):
    h = harness
    await h.listener.enable(activation())
    await h.listener.invalidate({'type': 'usage_updated'})
    assert h.listener._refresh_task is None
    row = await event(1)
    from channels.layers import get_channel_layer
    await get_channel_layer().group_send('updates', {'type': 'broadcast', 'data': {'type': 'timeline_changed'}})
    async with asyncio.timeout(1):
        while h.session._candidate is None:
            await asyncio.sleep(0.005)
    assert h.session._candidate['event_id'] == row.id
    await h.registry.shutdown()


async def test_pending_saved_summary_refreshes_after_saved_result_changes(harness, fake_summaries):
    h, fake = harness, fake_summaries
    h.policy['summary'] = True
    first = await event(1)
    await h.listener.enable(activation())
    old_handle = h.listener.preparations[first.id]['handle'].id
    fake.saved_ids.add(first.id)
    await h.listener.invalidate({'type': 'summary_saved', 'source_key': f'event-{first.id}',
                                 'source_hash': 'a' * 64, 'policy_key': 'policy:old'})
    await h.listener._refresh_task
    assert old_handle in fake.released
    assert h.listener.preparations[first.id]['descriptor']['saved']['id'] == first.id
    await h.registry.shutdown()


async def test_shared_speech_service_uses_private_frozen_script_and_adapt(harness, monkeypatch):
    from twicc.tts import service
    from twicc.tts.config import script_fields
    from twicc.tts.streams import release_stream
    script = {'id': 'script', 'kind': 'script', 'command': 'frozen-command', 'output': 'stream',
              'audio_format': 'mp3', 'pcm_rate': None, 'pcm_channels': 1, 'max_chars': None, 'delete_file': True}
    script['validated'] = script_fields(script)
    policy = {'entry': script, 'adapt': 'off'}
    monkeypatch.setattr(service, 'stored_script_config', lambda _: {**script, 'command': 'changed-command'})
    monkeypatch.setattr(service, '_stored_adapt_choice', lambda: 'haiku')
    choices, commands = [], []
    def engine(choice, text):
        choices.append(choice)
        return None, 'off'
    async def pump(request, stream, parts, **options):
        commands.append(request.command)
        stream.set_headers('audio/mpeg', None)
        stream.append(b'audio')
        stream.finish()
    monkeypatch.setattr(service, 'resolve_engine', engine)
    monkeypatch.setattr(service, 'pump_script_speech', pump)
    opened = await service.open_speech({'kind': 'script', 'script_id': 'script', 'text': 'Hello'}, _policy=policy)
    assert commands == ['frozen-command']
    assert choices == ['off']
    release_stream(opened.audio_id)
    await harness.registry.shutdown()


async def test_resume_reconciles_missed_invalidations_before_offering_pending_work(harness):
    h = harness
    await event(1)
    await h.listener.enable(activation())
    h.session.disconnect()
    for index in range(AFTER_ID_CAP + 1):
        await event(index + 2, kind='turn.started')
    await h.session.reconcile([], None)
    # No updates notification is delivered. The socket resume itself must catch up.
    if h.listener._refresh_task:
        await h.listener._refresh_task
    assert h.session.listening is None
    assert h.session.error['code'] == 'catchup_incomplete'
    assert not any(frame['type'] == 'candidate' for frame in h.session._frames)
    assert h.opened == []
    await h.registry.shutdown()


async def test_selection_uses_pending_summary_setting_changed_before_invalidation(harness, fake_summaries, monkeypatch):
    h = harness
    row = await event(1)
    await h.listener.enable(activation())
    h.policy['summary'] = True
    async def retain(reference):
        async def close():
            pass
        return SimpleNamespace(id='reader', aclose=close)
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retain)
    await select(h, row)
    assert h.session.playback['source'] == 'summary'
    await h.registry.shutdown()


async def test_heartbeat_renews_updates_membership_after_long_silent_listening(harness):
    import time
    from channels.layers import get_channel_layer
    h = harness
    await h.listener.enable(activation())
    layer = get_channel_layer()
    layer.groups['updates'][h.listener._channel] = time.time() - layer.group_expiry - 1
    await h.session.apply({'protocol': 1, 'type': 'ping', 'seq': 1})
    row = await event(1)
    await layer.group_send('updates', {'type': 'broadcast', 'data': {'type': 'timeline_changed'}})
    try:
        async with asyncio.timeout(0.3):
            while h.session._candidate is None:
                await asyncio.sleep(0.005)
        assert h.session._candidate['event_id'] == row.id
    finally:
        await h.registry.shutdown()
