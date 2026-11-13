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
DISPOSITIONS = orjson.loads((Path(__file__).parent / 'fixtures/native_tts_dispositions.json').read_bytes())
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
    policy = {'entry': {'id': 'voice', 'kind': 'openrouter', **FIELDS}, 'adapt': 'off', 'summary': False, 'final_only': False}
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


async def test_activation_telemetry_excludes_replay_recovery_resume_and_stale_epoch(harness, monkeypatch, tmp_path):
    from twicc.telemetry import activity, state
    monkeypatch.setattr(state, 'get_data_dir', lambda: tmp_path)
    monkeypatch.setattr(activity, 'is_telemetry_active', lambda: True)
    monkeypatch.setattr(harness.listener, '_initialize', lambda _: asyncio.sleep(0))
    epoch = await sync_to_async(activity.client_epoch)()
    command = {**activation(), 'protocol': 1, 'seq': 1, 'type': 'listen', 'telemetry_epoch': epoch}
    await harness.session.apply(command)
    await harness.session.apply(command)
    for seq, extra in ((2, {'fresh': True}), (3, {'fresh': False}), (4, {}), (5, {'telemetry_epoch': '0' * 64})):
        await harness.session.apply({**activation(), 'protocol': 1, 'seq': seq, 'type': 'listen', **extra})
    stored = await sync_to_async(state.ensure_state)()
    assert stored['days'][state.utc_today().isoformat()]['usage'] == {'timeline_tts_activations': 1}
    await harness.registry.shutdown()


async def test_automatic_started_counts_configured_reading_but_not_recovery_notice(harness, monkeypatch):
    from twicc.telemetry import activity
    counted = []
    monkeypatch.setattr(activity, 'collection_token', lambda: ('test', 'epoch'))
    monkeypatch.setattr(activity, 'record_activity', lambda metric, **kw: counted.append(metric))
    row = await event()
    await harness.listener.enable(activation())
    await select(harness, row)
    await finish(harness)
    assert counted == ['timeline_tts_messages']
    await harness.session._record_started({'automatic': True, 'source': 'recovery', 'config_id': 'voice'}, ('test', 'epoch'))
    assert counted == ['timeline_tts_messages']
    await harness.registry.shutdown()


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


async def test_listener_failure_keeps_its_activation_identity_after_disable(harness):
    h = harness
    current = activation()
    await h.listener.enable(current)
    await h.listener._stop('scope_unavailable', 'Old scope disappeared')
    assert h.session.listening is None
    assert h.session.error['activation_id'] == current['activation_id']
    failure = next(frame for frame in h.session._frames if frame['type'] == 'error')
    assert failure['activation_id'] == current['activation_id']
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


async def test_disconnection_catchup_crosses_incremental_cap_without_stopping(harness):
    h = harness
    await h.listener.enable(activation())
    h.session.disconnect()
    # Include rows that automatic speech excludes. The feed cap applies before filtering.
    for index in range(AFTER_ID_CAP + 1):
        await event(index + 1, kind='turn.started')
    await h.listener.refresh()
    state = h.session.snapshot()
    played_event_ids = [body.get('text') for body, _ in h.opened]
    assert state['listening'] is not None
    assert state['error'] is None
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


async def test_worktree_is_announced_as_its_main_project(harness):
    h = harness
    first = await event(1, project='wt'); second = await event(2, project='p')
    await h.listener.enable(activation()); await select(h, first)
    assert h.opened[-1][0]['announcement'] == 'Project. Session.', 'the worktree name is never spoken'
    await finish(h)
    await select(h, second, 2)
    assert h.opened[-1][0]['announcement'] == '', 'a worktree and its main project are one spoken project'
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


@pytest.mark.parametrize('silence_reset', [False, True])
async def test_selected_summary_freezes_config_adapt_and_announcement(harness, fake_summaries, monkeypatch, silence_reset):
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
    async def retain(reference, *, usage_origin="unknown"):
        entered.set()
        await release.wait()
        async def close():
            closed.append(reference['handle_id'])
        return SimpleNamespace(id='reader', aclose=close)
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retain)
    h.listener.context = ('p', 's')
    await h.listener.select(first.id, str(uuid4()), 1, silence_reset=silence_reset)
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
    assert body['announcement'] == ('Project. Session.' if silence_reset else '')
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
    assert h.session.listening is not None
    assert h.session.error is None
    assert h.session._candidate['event_id'] == h.listener.pending[0]['id']
    assert h.opened == []
    await h.registry.shutdown()


async def test_selection_uses_pending_summary_setting_changed_before_invalidation(harness, fake_summaries, monkeypatch):
    h = harness
    row = await event(1)
    await h.listener.enable(activation())
    h.policy['summary'] = True
    async def retain(reference, *, usage_origin="unknown"):
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


@pytest.mark.parametrize('change', ['hidden', 'muted', 'muted_choice', 'scope_deleted', 'scope_member_removed'])
async def test_selection_rechecks_current_visibility_and_scope_before_invalidation(
    harness, fake_summaries, monkeypatch, change,
):
    h = harness
    h.policy['summary'] = True
    row = await event(1)
    active = activation('workspace', 'w', include_muted=change == 'muted_choice')
    await h.listener.enable(active)
    retained_handle = h.listener.preparations[row.id]['handle'].id
    async def retain(reference, *, usage_origin="unknown"):
        async def close():
            pass
        return SimpleNamespace(id='reader', aclose=close)
    monkeypatch.setattr(native_sessions.summaries, 'retain_summary_for_speech', retain)
    assert h.session._candidate['event_id'] == row.id
    if change == 'hidden':
        await Session.objects.filter(pk='s').aupdate(hidden=True)
    elif change in ('muted', 'muted_choice'):
        await Session.objects.filter(pk='s').aupdate(mute_on_user_turn=True)
        if change == 'muted_choice':
            h.listener.active['include_muted'] = False
    elif change == 'scope_deleted':
        h.workspaces['workspaces'].clear()
    else:
        h.workspaces['workspaces'][0]['projectIds'] = ['q']
    # No invalidation is delivered between the storage change and selection.
    try:
        await select(h, row)
        if h.listener._refresh_task:
            await h.listener._refresh_task
        assert h.opened == []
        assert h.session.playback is None
        assert h.session.generation == 0
        assert h.listener.pending == []
        assert retained_handle in fake_summaries.released
        assert (h.session.listening is None) == (change == 'scope_deleted')
    finally:
        await h.registry.shutdown()


async def test_resync_replaces_broken_channel_and_receives_future_arrivals(
    harness, fake_summaries, monkeypatch,
):
    from twicc.channel_layer import TwiccChannelLayer
    h = harness
    layer = TwiccChannelLayer(capacity=1)
    h.policy['summary'] = True
    monkeypatch.setattr(native_timeline, 'get_channel_layer', lambda: layer)
    # Bootstrap excludes this history. Resync must retain the live cursor.
    await event(-1)
    accepted = await event(1)
    await h.listener.enable(activation(include_muted=True))
    old_channel = h.listener._channel
    old_cursor = h.listener.cursor
    old_handle = h.listener.preparations[accepted.id]['handle'].id
    fake_summaries.saved_ids.add(accepted.id)
    active = h.session.listening.copy()
    # Saturate the actual channel without letting its receiver drain it.
    envelope = {'type': 'broadcast', 'data': {'type': 'timeline_changed'}}
    await layer.send(old_channel, envelope)
    from channels.exceptions import ChannelFull
    with pytest.raises(ChannelFull):
        await layer.send(old_channel, envelope)
    assert old_channel in layer._broken
    try:
        async with asyncio.timeout(1):
            while h.listener._channel == old_channel:
                await asyncio.sleep(0.005)
        await h.listener._refresh_task
        assert old_channel not in layer.groups.get('updates', {})
        assert old_channel not in layer._broken
        assert h.listener.cursor == old_cursor
        assert old_handle in fake_summaries.released
        assert h.listener.preparations[accepted.id]['descriptor']['saved']['id'] == accepted.id
        assert h.session.listening == active
        assert [e['id'] for e in h.listener.pending] == [accepted.id]
        future = await event(2)
        await layer.group_send('updates', envelope)
        async with asyncio.timeout(1):
            while future.id not in h.listener.seen:
                await asyncio.sleep(0.005)
        await h.listener._refresh_task
        assert [e['id'] for e in h.listener.pending] == [accepted.id, future.id]
        await h.listener.disable()
        next_activation = activation()
        next_activation['boundary'] = format_occurred_at(CLICK + timedelta(seconds=50))
        await h.listener.enable(next_activation)
        after_reactivation = await event(51)
        await layer.group_send('updates', envelope)
        async with asyncio.timeout(1):
            while after_reactivation.id not in h.listener.seen:
                await asyncio.sleep(0.005)
        await h.listener._refresh_task
        assert [e['id'] for e in h.listener.pending] == [after_reactivation.id]
        assert h.opened == []
    finally:
        await h.registry.shutdown()


async def test_disabled_overflow_consumed_before_reactivation_keeps_future_arrivals(harness, monkeypatch):
    from channels.exceptions import ChannelFull
    from twicc.channel_layer import TwiccChannelLayer
    h = harness
    layer = TwiccChannelLayer(capacity=1)
    monkeypatch.setattr(native_timeline, 'get_channel_layer', lambda: layer)
    await h.listener.enable(activation())
    await h.listener.disable()
    old_channel = h.listener._channel
    consumed = asyncio.Event()
    original_invalidate = h.listener.invalidate
    async def observe(payload):
        await original_invalidate(payload)
        if payload.get('type') == 'resync_required':
            consumed.set()
    monkeypatch.setattr(h.listener, 'invalidate', observe)
    envelope = {'type': 'broadcast', 'data': {'type': 'timeline_changed'}}
    await layer.send(old_channel, envelope)
    with pytest.raises(ChannelFull):
        await layer.send(old_channel, envelope)
    assert old_channel in layer._broken
    try:
        await asyncio.wait_for(consumed.wait(), timeout=1)
        assert h.listener.active is None
        assert h.session.listening is None
        await event(10)
        next_activation = activation()
        next_activation['boundary'] = format_occurred_at(CLICK + timedelta(seconds=50))
        await h.listener.enable(next_activation)
        future = await event(51)
        await layer.group_send('updates', envelope)
        async with asyncio.timeout(1):
            while future.id not in h.listener.seen:
                await asyncio.sleep(0.005)
        await h.listener._refresh_task
        assert h.listener._channel != old_channel
        assert old_channel not in layer._broken
        assert old_channel not in layer.groups.get('updates', {})
        assert [e['id'] for e in h.listener.pending] == [future.id]
        assert h.opened == []
    finally:
        await h.registry.shutdown()


@pytest.mark.parametrize('refresh_first', [False, True])
@pytest.mark.parametrize('change', ['hidden', 'muted', 'scope'])
async def test_invalidated_selection_terminates_reservation_and_offers_next(harness, refresh_first, change):
    h = harness
    first = await event(1)
    await Session.objects.acreate(id='next', project_id='q', provider='codex', title='Next', file_path='/tmp/next.jsonl')
    second = await event(2, project='q', session='next')
    active = activation('workspace', 'w')
    h.workspaces['workspaces'][0]['projectIds'] = ['p', 'q']
    await h.listener.enable(active)
    if change == 'hidden':
        await Session.objects.filter(pk='s').aupdate(hidden=True)
    elif change == 'muted':
        await Session.objects.filter(pk='s').aupdate(mute_on_user_turn=True)
    else:
        h.workspaces['workspaces'][0]['projectIds'] = ['q']
    if refresh_first:
        await h.listener.refresh()
    h.session._frames.clear()
    reading = DISPOSITIONS['rejected']['reading_id']
    await h.listener.select(first.id, reading, 1)
    if h.listener._refresh_task:
        await h.listener._refresh_task
    dispositions = [f for f in h.session._frames if f['type'] == 'selection_rejected']
    assert dispositions == [DISPOSITIONS['rejected']]
    assert h.session.error is None
    assert h.session.playback is None
    assert h.session._candidate['event_id'] == second.id
    frames = list(h.session._frames)
    offers = [i for i, f in enumerate(frames) if f['type'] == 'candidate' and f['event_id'] == second.id]
    assert offers and offers[-1] > frames.index(dispositions[0])
    await select(h, second, 2)
    assert h.session.playback['text'] == 'Message 2'
    await h.registry.shutdown()


@pytest.mark.parametrize(('silence_reset', 'expected'), [(False, ''), (True, 'Project. Session.')])
async def test_announcement_uses_native_silence_fact(harness, silence_reset, expected):
    h = harness
    first = await event(1)
    await h.listener.enable(activation()); await select(h, first)
    await finish(h)
    second = await event(51)
    await h.listener.refresh()
    await h.listener.select(second.id, str(uuid4()), 2, silence_reset=silence_reset)
    await h.session.preparation
    assert h.opened[-1][0]['announcement'] == expected
    await h.registry.shutdown()


async def test_delayed_summary_descriptor_failure_keeps_selected_reading_scope(harness, monkeypatch):
    h = harness
    first = await event(1)
    await h.listener.enable(activation())
    h.policy['summary'] = True
    h.listener.preparations[first.id] = {'descriptor': {'error': 'Summary is unavailable'},
        'source': None, 'handle': None, 'watcher': None}
    h.listener._summary_policy = (True, None)
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.listener._release_preparation
    async def delayed_release(event_id):
        entered.set()
        await release.wait()
        await original(event_id)
    monkeypatch.setattr(h.listener, '_release_preparation', delayed_release)
    automatic = DISPOSITIONS['summary_error']['reading_id']
    selection = asyncio.create_task(h.listener.select(first.id, automatic, 1))
    await entered.wait()
    manual = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'manual', 'seq': 1, 'reading_id': manual, 'generation': 2,
        'request': {'kind': 'text', 'source': 'message', 'text': 'Manual', 'speech': {'text': 'Manual'}}})
    await h.session.preparation
    release.set(); await selection
    failure = next(f for f in h.session._frames if f['type'] == 'error')
    assert failure == DISPOSITIONS['summary_error']
    assert h.session.playback['reading_id'] == manual
    await h.registry.shutdown()


async def test_configuration_loss_does_not_release_manual_resource(harness):
    h = harness
    current = activation()
    await h.listener.enable(current)
    manual = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'manual', 'seq': 1, 'reading_id': manual, 'generation': 1,
        'request': {'kind': 'text', 'source': 'message', 'text': 'Manual', 'speech': {'text': 'Manual'}}})
    await h.session.preparation
    before = h.session.playback.copy()
    h.policy['entry'] = None
    await h.listener.refresh()
    assert h.session.listening is None
    assert h.session.playback == before
    assert before['audio_id'] in h.session.owned_audio
    assert h.session.error['code'] == 'tts_unavailable'
    assert next(f for f in h.session._frames if f['type'] == 'error') == {
        **DISPOSITIONS['listener_error'], 'activation_id': current['activation_id'],
        'ui_message': {'key': 'backend.tts.configurationUnavailable', 'params': {}},
    }
    await h.registry.shutdown()


async def test_resume_does_not_reject_an_inflight_selection(harness):
    h = harness
    first = await event(1)
    await h.listener.enable(activation())
    reading = str(uuid4())
    async with h.listener._lock:
        await h.session.apply({'protocol': 1, 'type': 'select', 'seq': 1, 'event_id': first.id,
                               'reading_id': reading, 'generation': 1})
        await h.session.reconcile([], {'reading_id': reading, 'generation': 1, 'audio_id': None,
                                      'phase': 'loading', 'started': False})
        assert not any(f['type'] == 'selection_rejected' for f in h.session._frames)
    await asyncio.gather(*list(h.listener._tasks))
    await h.registry.shutdown()


async def test_superseded_automatic_preparation_exception_preserves_manual(harness, monkeypatch):
    from twicc.tts.service import SpeechError
    h = harness
    first = await event(1)
    await h.listener.enable(activation())
    entered, release = asyncio.Event(), asyncio.Event()
    original = native_sessions.open_speech
    async def delayed_failure(body, **options):
        if body['text'] == 'Message 1':
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise SpeechError('Late automatic failure', 424)
        return await original(body, **options)
    monkeypatch.setattr(native_sessions, 'open_speech', delayed_failure)
    await h.listener.select(first.id, str(uuid4()), 1)
    await entered.wait()
    automatic_task = h.session.preparation
    manual = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'manual', 'seq': 1, 'reading_id': manual, 'generation': 2,
        'request': {'kind': 'text', 'source': 'message', 'text': 'Manual', 'speech': {'text': 'Manual'}}})
    await h.session.preparation
    release.set(); await automatic_task
    assert h.session.playback['reading_id'] == manual
    assert h.session.playback['audio_id'] in h.session.owned_audio
    assert not any(f['type'] == 'error' for f in h.session._frames)
    await h.registry.shutdown()


async def test_selection_configuration_loss_finishes_its_own_stop_cleanup(harness, fake_summaries, monkeypatch):
    h = harness
    h.policy['summary'] = True
    first = await event(1)
    await h.listener.enable(activation())
    original = native_timeline.summaries.release_summary
    async def release_reader(handle_id):
        # Reader cleanup can yield while another service reader releases.
        await asyncio.sleep(0)
        await original(handle_id)
    monkeypatch.setattr(native_timeline.summaries, 'release_summary', release_reader)
    h.policy['entry'] = None
    reading = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'select', 'seq': 1, 'event_id': first.id,
                           'reading_id': reading, 'generation': 1})
    selection = h.listener._selections[(reading, 1)][1]
    await asyncio.gather(selection, return_exceptions=True)
    assert h.session.error and h.session.error['code'] == 'tts_unavailable'
    assert h.session.playback is None and h.session.listening is None
    assert not h.listener.pending and not h.listener.preparations and not fake_summaries.handles
    assert any(f['type'] == 'selection_rejected' and f['reading_id'] == reading for f in h.session._frames)
    assert h.opened == []
    await h.registry.shutdown()


async def test_old_selection_stop_cannot_fail_new_activation_during_reader_cleanup(harness, fake_summaries, monkeypatch):
    h = harness
    h.policy['summary'] = True
    first = await event(1)
    await h.listener.enable(activation())
    entered, release = asyncio.Event(), asyncio.Event()
    original = native_timeline.summaries.release_summary
    async def release_reader(handle_id):
        entered.set()
        await release.wait()
        await original(handle_id)
    monkeypatch.setattr(native_timeline.summaries, 'release_summary', release_reader)
    entry = h.policy['entry']
    h.policy['entry'] = None
    reading = str(uuid4())
    await h.session.apply({'protocol': 1, 'type': 'select', 'seq': 1, 'event_id': first.id,
                           'reading_id': reading, 'generation': 1})
    selection = h.listener._selections[(reading, 1)][1]
    await entered.wait()
    h.policy['entry'] = entry
    new_activation = activation()
    await h.session.apply({'protocol': 1, 'type': 'listen', 'seq': 2, **new_activation})
    assert h.session.listening['activation_id'] == new_activation['activation_id']
    assert not h.listener._caught_up  # Initialization waits for the old selection lock.
    release.set()
    await selection
    await asyncio.gather(*list(h.listener._tasks))
    assert h.listener._caught_up
    assert h.session.listening['activation_id'] == new_activation['activation_id']
    assert h.session.error is None
    assert not any(f['type'] == 'error' for f in h.session._frames)
    assert h.session.playback is None and h.opened == []
    await h.registry.shutdown()


async def test_short_replacement_checkpoint_keeps_unread_equal_timestamp(harness):
    h = harness
    started = await event(1)
    unread = await event(1, text='Unread at the same time')
    later = await event(2)
    activation_command = activation()
    activation_command.update(boundary=format_occurred_at(CLICK + timedelta(seconds=1, microseconds=-1)),
                              excluded_event_ids=[started.id])
    try:
        await h.listener.enable(activation_command)
        assert [item['id'] for item in h.listener.pending] == [unread.id, later.id]
    finally:
        await h.registry.shutdown()


async def test_long_recovery_uses_server_boundary_and_announces_before_fresh_message(harness):
    h = harness
    old = await event(10)
    fresh = await event(51)
    h.policy['adapt'] = 'haiku'
    notice = {'reading_id': str(uuid4()), 'generation': 1, 'text': 'Localized connection notice'}
    listen = {**activation(), 'protocol': 1, 'seq': 1, 'type': 'listen', 'fresh': True, 'notice': notice}
    try:
        await h.session.apply(listen)
        await h.session.preparation
        assert h.session.playback['source'] == 'recovery'
        assert h.opened[0][0]['text'] == notice['text']
        assert h.opened[0][1]['_policy']['adapt'] == 'off'
        # Initialization can finish while the notice plays, but cannot offer a candidate.
        await h.listener._initialize(h.listener.active)
        assert h.listener.active['boundary'] == format_occurred_at(CLICK + timedelta(seconds=50))
        assert [item['id'] for item in h.listener.pending] == [fresh.id]
        assert old.id not in [item['id'] for item in h.listener.pending]
        assert h.session._candidate is None
        await h.session.apply({'protocol': 1, 'seq': 2, 'type': 'finished',
            'reading_id': notice['reading_id'], 'generation': 1, 'outcome': 'complete'})
        await h.listener.refresh()
        assert h.session._candidate['event_id'] == fresh.id
    finally:
        await h.registry.shutdown()


async def test_recovery_notice_failure_releases_fresh_timeline(harness, monkeypatch):
    h = harness
    fresh = await event(51)
    failed = asyncio.Event()

    async def fail_notice(*args, **kwargs):
        failed.set()
        raise native_sessions.SpeechError('Configured voice failed', 424)

    monkeypatch.setattr(native_sessions, 'open_speech', fail_notice)
    notice = {'reading_id': str(uuid4()), 'generation': 1, 'text': 'Localized notice'}
    try:
        await h.session.apply({**activation(), 'protocol': 1, 'seq': 1, 'type': 'listen',
                               'fresh': True, 'notice': notice})
        await failed.wait()
        await asyncio.gather(*tuple(h.session._tasks))
        await h.listener._initialize(h.listener.active)
        await h.listener.refresh()
        assert h.session.playback is None
        assert h.listener.active is not None
        assert h.session._candidate['event_id'] == fresh.id
    finally:
        await h.registry.shutdown()


async def test_notice_policy_exception_does_not_strand_reserved_native_reading(harness, monkeypatch):
    h = harness
    fresh = await event(51)
    calls = 0

    def read_policy():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('Temporary configuration read failure')
        return h.policy.copy()

    monkeypatch.setattr(native_timeline, '_read_policy', read_policy)
    notice = {'reading_id': str(uuid4()), 'generation': 1, 'text': 'Localized notice'}
    try:
        await h.session.apply({**activation(), 'protocol': 1, 'seq': 1, 'type': 'listen',
                               'fresh': True, 'notice': notice})
        errors = [frame for frame in h.session._frames if frame['type'] == 'error']
        assert errors[-1]['reading_id'] == notice['reading_id']
        assert errors[-1]['code'] == 'speech_failed'
        await h.listener._initialize(h.listener.active)
        await h.listener.refresh()
        assert h.session.playback is None
        assert h.session._candidate['event_id'] == fresh.id
    finally:
        await h.registry.shutdown()


async def test_busy_short_recovery_keeps_all_eligible_messages_in_order_without_replay(harness):
    h = harness
    first = await event(1)
    await h.listener.enable(activation())
    reading = await select(h, first)
    await h.session.apply({'protocol': 1, 'seq': 1, 'type': 'started', 'reading_id': reading, 'generation': 1})
    await h.session.apply({'protocol': 1, 'seq': 2, 'type': 'finished', 'reading_id': reading,
                           'generation': 1, 'outcome': 'complete'})
    h.session.disconnect()
    for index in range(AFTER_ID_CAP + 1):
        await event(2, kind='turn.started')
    next_row = await event(3)
    last = await event(4)
    try:
        await h.session.reconcile([], None)
        if h.listener._refresh_task:
            await h.listener._refresh_task
        assert h.session.error is None
        assert h.session.listening is not None
        assert [item['id'] for item in h.listener.pending] == [next_row.id, last.id]
        assert first.id in h.listener.seen
        assert h.session._candidate['event_id'] == next_row.id
    finally:
        await h.registry.shutdown()


async def test_notice_retry_after_short_flap_retains_accepted_boundary(harness):
    h = harness
    fresh = await event(51)
    first = {'reading_id': str(uuid4()), 'generation': 1, 'text': 'Localized notice'}
    try:
        await h.session.apply({**activation(), 'protocol': 1, 'seq': 1, 'type': 'listen',
                               'fresh': True, 'notice': first})
        await h.session.preparation
        accepted_boundary = h.listener.active['boundary']
        await h.listener._initialize(h.listener.active)
        h.listener.clock = lambda: CLICK + timedelta(seconds=52)
        retry = {'reading_id': str(uuid4()), 'generation': 2, 'text': first['text']}
        await h.session.apply({**activation(), 'boundary': accepted_boundary,
                               'protocol': 1, 'seq': 2, 'type': 'listen', 'fresh': False, 'notice': retry})
        await h.session.preparation
        await h.listener._initialize(h.listener.active)
        assert h.listener.active['boundary'] == accepted_boundary
        assert h.session.playback['source'] == 'recovery'
        assert [item['id'] for item in h.listener.pending] == [fresh.id]
    finally:
        await h.registry.shutdown()


async def final_ending(message=None, second=10, session='s'):
    current = await Session.objects.aget(pk=session)
    if not current.storage_incarnation:
        current.storage_incarnation = str(uuid4())
        await current.asave(update_fields=['storage_incarnation'])
    scope = {'storage_incarnation': current.storage_incarnation, 'history_epoch': current.history_epoch}
    if message:
        await TimelineEvent.objects.filter(pk=message.id).aupdate(actor='agent', data={**message.data, **scope})
    return await sync_to_async(record_event_sync)('turn.ended', project_id=current.project_id,
        session_id=session, provider=current.provider, actor='agent', occurred_at=CLICK + timedelta(seconds=second),
        data={'final_message': {'state': 'ready' if message else 'pending', **scope,
                               **({'event_id': message.id} if message else {})}})


async def resolve_ending(ending, message):
    current = await Session.objects.aget(pk=ending.session_id)
    scope = {'storage_incarnation': current.storage_incarnation, 'history_epoch': current.history_epoch}
    await TimelineEvent.objects.filter(pk=message.id).aupdate(actor='agent', data={**message.data, **scope})
    await TimelineEvent.objects.filter(pk=ending.id).aupdate(data={'final_message': {
        'state': 'ready', 'event_id': message.id, **scope}})


async def test_final_only_gates_summaries_and_uses_exact_earlier_message(harness, fake_summaries):
    h = harness
    h.policy.update(final_only=True, summary=True)
    exact = await event(-2, text='Exact final')
    await event(1, text='Progress')
    await h.listener.enable(activation())
    assert h.listener.pending == []
    assert fake_summaries.acquired == []
    ending = await final_ending(exact)
    await h.listener.refresh()
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    assert fake_summaries.acquired == [exact.id]
    assert h.session._candidate['occurred_at'] == format_occurred_at(exact.occurred_at)
    assert h.session._candidate['final_ending_id'] == ending.id
    await h.registry.shutdown()


async def test_final_wait_survives_off_newer_start_and_same_id_signal(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    ending = await final_ending()
    await h.listener.enable(activation())
    assert h.session.listening['pending_final_ending_ids'] == [ending.id]
    h.policy['final_only'] = False
    await h.listener.invalidate({'type': 'synced_settings_updated'})
    await h.listener._refresh_task
    newer = await event(600)
    await h.listener.refresh(); await select(h, newer); await finish(h)
    await resolve_ending(ending, exact)
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [ending.id]})
    await h.listener._refresh_task
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    await select(h, exact, 2)
    assert h.opened[-1][0]['text'] == 'Message -2'
    await h.registry.shutdown()


async def test_replacement_restores_old_final_and_prevents_off_progress_replay(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    ending = await final_ending()
    progress = await event(20)
    await h.listener.enable(activation())
    recovery = h.session.listening.copy()
    assert recovery['ordinary_after_id'] >= progress.id
    recovery['boundary'] = format_occurred_at(CLICK + timedelta(seconds=595))
    h.policy['final_only'] = False
    await resolve_ending(ending, exact)
    unread = await event(600)
    await h.listener.enable(recovery)
    assert [e['id'] for e in h.listener.pending] == [exact.id, unread.id]
    await select(h, exact); await finish(h)
    recovery = h.session.listening.copy()
    await h.listener.enable(recovery)
    assert [e['id'] for e in h.listener.pending] == [unread.id]
    await h.registry.shutdown()


async def test_final_hidden_show_cutoff_uses_ending_id(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    await event(1, kind='hidden.ended')
    await final_ending(exact)
    await h.listener.enable(activation())
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    await Session.objects.filter(pk='s').aupdate(hidden=True)
    await h.listener.refresh()
    assert h.listener.pending == []
    await h.registry.shutdown()


async def replace_native(h, recovery):
    await h.listener.close()
    h.session = h.registry.open(str(uuid4()), ('cookie', 'fp'))
    h.listener = h.session.coordinator
    h.listener.clock = lambda: CLICK + timedelta(seconds=650)
    await h.listener.enable({**recovery, 'activation_id': str(uuid4())})


async def test_expired_native_session_keeps_late_final_after_newer_ordinary_checkpoint(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    waiting = await final_ending()
    started = await event(11)
    await final_ending(started, second=12)
    await h.listener.enable(activation())
    await select(h, started); await finish(h)
    h.policy['final_only'] = False
    newer = await event(600)
    await h.listener.refresh(); await select(h, newer, 2); await finish(h)
    recovery = h.session.listening.copy()
    recovery['boundary'] = format_occurred_at(CLICK + timedelta(seconds=595))
    await replace_native(h, recovery)
    assert h.listener.pending == []
    await resolve_ending(waiting, exact)
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [waiting.id]})
    await h.listener._refresh_task
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    await select(h, exact); await finish(h)
    await h.listener.refresh()
    assert h.listener.pending == []
    assert [body['text'] for body, _ in h.opened] == ['Message 11', 'Message 600', 'Message -2']
    await h.registry.shutdown()


async def test_consumed_ordinary_message_stays_consumed_when_final_association_arrives_after_replacement(harness):
    h = harness
    first = await event(1)
    await h.listener.enable(activation()); await select(h, first); await finish(h)
    newer = await event(600)
    await h.listener.refresh(); await select(h, newer, 2); await finish(h)
    h.policy['final_only'] = True
    waiting = await final_ending(second=601)
    await h.listener.refresh()
    recovery = h.session.listening.copy()
    recovery['boundary'] = format_occurred_at(CLICK + timedelta(seconds=595))
    await replace_native(h, recovery)
    await resolve_ending(waiting, first)
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [waiting.id]})
    await h.listener._refresh_task
    assert h.listener.pending == []
    assert h.session.listening['pending_final_ending_ids'] == []
    await h.registry.shutdown()


async def test_final_resolution_inflight_signal_is_not_lost(harness, monkeypatch):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    waiting = await final_ending()
    await h.listener.enable(activation())
    original = native_timeline.resolve_final_messages
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def delayed(ids, **kwargs):
        nonlocal calls
        calls += 1
        result = await sync_to_async(original)(ids, **kwargs)
        if calls == 1:
            entered.set()
            await release.wait()
        return result
    # Replace the thread adapter only for this asynchronous, controlled service call.
    original_adapter = native_timeline.sync_to_async
    monkeypatch.setattr(native_timeline, 'sync_to_async', lambda fn: delayed if fn is original else original_adapter(fn))
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [waiting.id]})
    await entered.wait()
    await resolve_ending(waiting, exact)
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [waiting.id]})
    release.set()
    await h.listener._refresh_task
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    assert calls == 2
    await h.registry.shutdown()


async def test_reconnect_during_final_lookup_fences_summary_until_next_catchup(harness, fake_summaries, monkeypatch):
    h = harness
    h.policy.update(final_only=True, summary=True)
    exact = await event(-2)
    waiting = await final_ending()
    await h.listener.enable(activation())
    await resolve_ending(waiting, exact)
    original = native_timeline.resolve_final_messages
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(ids, **kwargs):
        result = await sync_to_async(original)(ids, **kwargs)
        entered.set(); await release.wait()
        return result
    original_adapter = native_timeline.sync_to_async
    monkeypatch.setattr(native_timeline, 'sync_to_async', lambda fn: delayed if fn is original else original_adapter(fn))
    h.listener._final_dirty.add(waiting.id)
    task = asyncio.create_task(h.listener.refresh())
    await entered.wait()
    # Reserve reconnect while preventing its catch-up from masking the stale response.
    monkeypatch.setattr(h.listener, 'invalidate', lambda payload: asyncio.sleep(0))
    await h.listener.reconnect()
    release.set(); await task
    assert fake_summaries.acquired == []
    assert h.session._candidate is None
    monkeypatch.setattr(native_timeline, 'sync_to_async', original_adapter)
    await h.listener.refresh()
    assert fake_summaries.acquired == [exact.id]
    await h.registry.shutdown()


async def test_final_replacement_scans_only_new_metadata_and_batches_exact_lookups(harness, monkeypatch):
    h = harness
    h.policy['final_only'] = True
    await h.listener.enable(activation())
    recovery = h.session.listening.copy()
    for index in range(AFTER_ID_CAP + 1):
        await event(1, text='Ignored progress')
    exact = await event(-2)
    waiting = [await final_ending(second=10) for _ in range(101)]
    for ending in waiting:
        await resolve_ending(ending, exact)
    original = native_timeline.resolve_final_messages
    batches = []
    def resolve(ids, **kwargs):
        batches.append(len(ids))
        return original(ids, **kwargs)
    monkeypatch.setattr(native_timeline, 'resolve_final_messages', resolve)
    serialized = []
    original_serializer = native_timeline.serialize_event
    def serialize(row, **kwargs):
        serialized.append(row.kind)
        return original_serializer(row, **kwargs)
    monkeypatch.setattr(native_timeline, 'serialize_event', serialize)
    await replace_native(h, recovery)
    assert [e['id'] for e in h.listener.pending] == [exact.id]
    assert batches == [100, 1]
    assert 'assistant_message.sent' not in serialized
    assert h.session.listening['ending_cursor'] == waiting[-1].id
    await h.registry.shutdown()


async def test_ordinary_notifications_never_resolve_finals_without_obligations(harness, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No outstanding final obligations')
    monkeypatch.setattr(native_timeline, 'resolve_final_messages', forbidden)
    await harness.listener.enable(activation())
    await event(1)
    await harness.listener.refresh()
    await harness.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [9999]})
    await harness.listener._refresh_task
    assert len(harness.listener.pending) == 1
    await harness.registry.shutdown()


async def test_final_selection_checks_source_scope_before_new_summary_work(harness, fake_summaries):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    await final_ending(exact)
    await h.listener.enable(activation())
    await Session.objects.filter(pk='s').aupdate(history_epoch=1)
    h.policy['summary'] = True
    await select(h, exact)
    assert fake_summaries.acquired == []
    assert h.opened == []
    assert h.listener.pending == []
    await h.registry.shutdown()


async def test_final_selection_checks_hidden_state_before_new_summary_work(harness, fake_summaries):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    await final_ending(exact)
    await h.listener.enable(activation())
    await Session.objects.filter(pk='s').aupdate(hidden=True)
    h.policy['summary'] = True
    await select(h, exact)
    assert fake_summaries.acquired == []
    assert h.opened == []
    await h.registry.shutdown()


async def test_replacement_does_not_revive_final_before_hidden_show_cutoff(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    waiting = await final_ending()
    await h.listener.enable(activation())
    await event(11, kind='hidden.ended')
    await h.listener.refresh()
    recovery = h.session.listening.copy()
    await resolve_ending(waiting, exact)
    await replace_native(h, recovery)
    assert h.listener.pending == []
    await h.registry.shutdown()


async def test_fresh_native_recovery_discards_old_endings_and_consumption(harness):
    h = harness
    h.policy['final_only'] = True
    exact = await event(-2)
    waiting = await final_ending()
    ready = await event(12)
    await final_ending(ready, second=13)
    await h.listener.enable(activation())
    recovery = h.session.listening.copy()
    await h.listener.command({**recovery, 'type': 'listen', 'fresh': True})
    await asyncio.gather(*tuple(h.listener._tasks))
    await resolve_ending(waiting, exact)
    await h.listener.invalidate({'type': 'timeline_changed', 'finalized_ending_ids': [waiting.id]})
    await h.listener._refresh_task
    assert h.listener.pending == []
    assert h.session.listening['final_boundary'] == format_occurred_at(CLICK + timedelta(seconds=50))
    assert h.session.listening['pending_final_ending_ids'] == []
    await h.registry.shutdown()


async def test_selection_mode_change_persists_ignored_progress_cutoff_before_replacement(harness):
    h = harness
    progress = await event(1)
    await h.listener.enable(activation())
    h.policy['final_only'] = True
    await select(h, progress)
    assert h.opened == []
    recovery = h.session.listening.copy()
    h.policy['final_only'] = False
    unread = await event(2)
    await replace_native(h, recovery)
    assert [e['id'] for e in h.listener.pending] == [unread.id]
    await h.registry.shutdown()


@pytest.mark.parametrize('projects', [(), ('p',), ('p', 'q'), ('wt',)])
async def test_native_incremental_range_traverses_primary_key_with_real_scope_indexes(harness, monkeypatch, projects):
    from django.db import connection
    from twicc.core.services.timeline_feed import FeedRequest
    from twicc.core.services.timeline_filters import TimelineFilter

    monkeypatch.setattr(native_timeline, 'latest_event_id', lambda: 900010)
    request = FeedRequest(projects, 100, None, 900000,
                          TimelineFilter(families=('turns', 'organization')), True)
    def read_and_explain():
        plans = []
        def capture(execute, sql, params, many, context):
            if sql.startswith('SELECT') and '"core_timelineevent"' in sql:
                with connection.cursor() as cursor:
                    cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                    plans.append([row[-1] for row in cursor.fetchall()])
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            body = native_timeline._read_range(request, '2026-11-04T10:30:00.000000Z', 900000)
        return body, plans
    body, plans = await sync_to_async(read_and_explain)()
    assert body['latest_id'] == 900010
    assert plans
    for plan in plans:
        assert any('SEARCH core_timelineevent USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)' in step
                   for step in plan), plan
        assert not any('SEARCH core_timelineevent USING INDEX idx_timeline_project' in step
                       or 'SEARCH core_timelineevent USING INDEX idx_timeline_session' in step for step in plan), plan
    await harness.registry.shutdown()


async def test_summary_time_mode_change_persists_cutoff_before_off_replacement(harness, fake_summaries, monkeypatch):
    h = harness
    await h.listener.enable(activation())
    progress = await event(1)
    h.policy['summary'] = True
    reads = 0
    def changing_policy():
        nonlocal reads
        reads += 1
        return {**h.policy, 'final_only': reads >= 2}
    monkeypatch.setattr(native_timeline, '_read_policy', changing_policy)
    await h.listener.refresh()
    assert reads == 2
    assert h.listener.pending == []
    assert fake_summaries.acquired == []
    recovery = h.session.listening.copy()
    assert recovery['ordinary_after_id'] == progress.id
    monkeypatch.setattr(native_timeline, '_read_policy', lambda: h.policy.copy())
    h.policy['summary'] = False
    unread = await event(2)
    await replace_native(h, recovery)
    assert [e['id'] for e in h.listener.pending] == [unread.id]
    await h.registry.shutdown()
