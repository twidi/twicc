"""Semantic Android presentation and bounded transport contracts."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc.agent.states import AgentState
from twicc.core.ui_messages import ui_message


def info(**overrides):
    return SimpleNamespace(**({
        'provider': 'codex', 'session_id': 's', 'project_id': 'p',
        'state': AgentState.USER_TURN, 'pending_requests': [],
        'background_work_in_progress': {},
    } | overrides))


def snapshot(**overrides):
    return SimpleNamespace(**({
        'extra_usage_utilization': None, 'extra_usage_remaining_credits': None,
        'extra_usage_decimal_places': None, 'extra_usage_currency': None,
        'extra_usage_used_credits': None, 'extra_usage_monthly_limit': None,
    } | overrides))


def peer(**overrides):
    return SimpleNamespace(**({'name': 'Alice', 'remote_display_name': '', 'base_url': 'https://peer.example'} | overrides))


def message(**overrides):
    return SimpleNamespace(**({'peer': peer(), 'title': 'Subject', 'payload': {'text': 'Preview'}, 'reply_to_message_id': None} | overrides))


def walk(descriptor):
    yield descriptor
    for value in descriptor['params'].values():
        if isinstance(value, dict):
            yield from walk(value)


def assert_catalog_messages(pair):
    manifest = orjson.loads(Path('android/i18n/messages.json').read_bytes())['messages']
    compiled = orjson.loads(Path('android/app/src/main/assets/i18n/messages.json').read_bytes())['locales']
    for root in pair:
        for descriptor in walk(root):
            key = descriptor['key']
            assert key in manifest
            for locale in compiled.values():
                assert key in locale
                assert set(locale[key]['parameters']) == set(descriptor['params'])


@pytest.mark.parametrize('event,key', [
    ('user_turn', 'session.notifications.finished'),
    ('question', 'session.notifications.question'),
    ('approval', 'session.notifications.approval'),
])
def test_every_android_event_uses_existing_descriptors(event, key):
    from twicc.android_push.messages import agent_messages, peer_message_messages, peer_request_messages, extra_usage_messages

    pair = agent_messages(event, info(), None, None, None)
    assert pair[0] == ui_message(key, {'provider': 'Codex'})
    assert pair[1] == ui_message('session.notifications.body', {
        'project': ui_message('common.labels.unknown'), 'session': ui_message('common.labels.unknown'),
    })
    assert_catalog_messages(pair)
    assert_catalog_messages(peer_message_messages(message(), None))
    assert_catalog_messages(peer_request_messages(peer(name='', base_url='', remote_display_name='')))
    assert_catalog_messages(extra_usage_messages('codex', snapshot()))


@pytest.mark.parametrize('shells', [1, 2, 21])
def test_background_shells_use_nested_plural_descriptors(shells):
    from twicc.android_push.messages import agent_messages

    pair = agent_messages('user_turn', info(background_work_in_progress={'shells': shells}), 'S', 'P', 'Main')
    assert pair[0] == ui_message('android.push.finishedWithShells', {
        'finished': ui_message('session.notifications.turnFinished', {'provider': 'Codex'}),
        'shells': ui_message('session.notifications.shells', {'count': shells}, count=shells),
    })
    assert pair[1]['params']['project'] == ui_message('android.push.projectWithParent', {'parent': 'Main', 'project': 'P'})
    assert_catalog_messages(pair)


@pytest.mark.parametrize('data,key,params,count', [
    ({'extra_usage_utilization': 10, 'extra_usage_used_credits': 4419, 'extra_usage_monthly_limit': 8000, 'extra_usage_currency': 'USD', 'extra_usage_decimal_places': 2},
     'moneyUsedOf', {'used': '44.19', 'limit': '80', 'currency': 'USD'}, None),
    ({'extra_usage_utilization': 10, 'extra_usage_used_credits': 4419, 'extra_usage_currency': 'EUR', 'extra_usage_decimal_places': 2},
     'moneyUsed', {'used': '44.19', 'currency': 'EUR'}, None),
    ({'extra_usage_utilization': 10, 'extra_usage_used_credits': 1, 'extra_usage_monthly_limit': 20},
     'creditsUsedOf', {'used': 1, 'limit': 20}, 20),
    ({'extra_usage_utilization': 10, 'extra_usage_used_credits': 2},
     'creditsUsed', {'used': 2}, 2),
    ({'extra_usage_remaining_credits': 19.6}, 'creditsRemaining', {'count': 20}, 20),
])
def test_extra_usage_preserves_numeric_semantics(data, key, params, count):
    from twicc.android_push.messages import extra_usage_messages

    pair = extra_usage_messages('codex', snapshot(**data))
    assert pair[0] == ui_message('session.notifications.extraUsageTitle', {'provider': 'Codex'})
    assert pair[1]['params']['usage'] == ui_message('session.notifications.' + key, params, count=count)
    assert_catalog_messages(pair)


@pytest.mark.parametrize('reply', [False, True])
@pytest.mark.parametrize('routing_fields', [None, (None, None, 'p', 'Project', 'Main'), ('s', None, None, None, None), ('s', 'Session', 'p', 'Project', 'Main')])
def test_peer_reply_and_partial_routing(reply, routing_fields):
    from twicc.android_push.messages import peer_message_messages, peer_request_messages
    from twicc.external_notifications import PeerRouting

    routing = PeerRouting(*routing_fields) if routing_fields else None
    pair = peer_message_messages(message(reply_to_message_id=42 if reply else None), routing)
    assert pair[0] == ui_message('session.notifications.peerReply' if reply else 'session.notifications.peerMessage', {'peer': 'Alice'})
    assert_catalog_messages(pair)
    unnamed = peer_request_messages(peer(name='', remote_display_name=''))
    assert unnamed[0]['params']['peer'] == ui_message('session.notifications.instance')
    assert_catalog_messages(unnamed)


def envelope(title, body, **overrides):
    return {'type': 'twicc.notification', 'version': 2, 'id': 'event-1', 'sent_at': '2026-11-08T09:00:00+00:00', 'kind': 'peer',
            'title_ui_message': title, 'body_ui_message': body, 'path': '/project/p/session/s', 'action': 'peerInbox', 'detail': '42'} | overrides


def test_multibyte_payload_stays_valid_and_bounded():
    from twicc.android_push.messages import bounded_payload, peer_message_messages

    original = envelope(*peer_message_messages(message(title='😀漢é' * 9000, payload={'text': '\\"\n😀' * 8000}, peer=peer(name='😀' * 4000)), None))
    payload = bounded_payload(original)
    assert payload is not None
    encoded = orjson.dumps(payload)
    assert len(encoded) <= 3900
    assert orjson.loads(encoded) == payload
    assert payload['body_ui_message']['key'] == 'android.push.peerBody'
    assert payload['body_ui_message']['params']['title']
    assert original['body_ui_message']['params']['title'] == '😀漢é' * 9000
    for key in ('path', 'action', 'detail', 'kind', 'id', 'sent_at'):
        assert payload[key] == original[key]
    assert_catalog_messages((payload['title_ui_message'], payload['body_ui_message']))


@pytest.mark.parametrize('bad', [
    ui_message('unknown.key'), ui_message('session.notifications.finished', {'provider': float('nan')}),
    ui_message('session.notifications.finished', {'provider': 'x' * 10000}),
])
def test_unrenderable_or_unshrinkable_messages_use_generic_presentation(bad):
    from twicc.android_push.messages import bounded_payload

    payload = bounded_payload(envelope(bad, bad))
    assert payload['title_ui_message'] == ui_message('android.push.genericTitle')
    assert payload['body_ui_message'] == ui_message('android.push.genericBody')
    assert payload['path'] == '/project/p/session/s'


def test_descriptor_traversal_is_bounded_and_bad_destinations_are_dropped():
    from twicc.android_push.messages import bounded_payload

    cycle = ui_message('android.push.peerBodyWithSession')
    cycle['params'] = {'message': cycle, 'session': 'S'}
    payload = bounded_payload(envelope(cycle, cycle))
    assert payload['body_ui_message'] == ui_message('android.push.genericBody')
    for invalid in ({'path': 'https://evil.example'}, {'action': 'bad'}, {'detail': 'x' * 97}, {'id': 'x' * 4000}):
        assert bounded_payload(envelope(ui_message('android.push.genericTitle'), ui_message('android.push.genericBody'), **invalid)) is None


def test_push_has_no_rendered_prose_or_locale(monkeypatch):
    from twicc.android_push import delivery

    sent = []
    async def capture(device, payload):
        sent.append(payload)
    monkeypatch.setattr(delivery.presence, 'is_browser_present', lambda: False)
    monkeypatch.setattr(delivery.storage, 'devices', lambda: [{'preferences': {k: True for k in ('peer', 'user_turn', 'pending_request', 'extra_usage')}}])
    monkeypatch.setattr(delivery, '_send_one', capture)
    for kind in ('peer', 'user_turn', 'pending_request', 'extra_usage'):
        asyncio.run(delivery.send(kind, ui_message('android.push.genericTitle'), ui_message('android.push.genericBody')))
    assert len(sent) == 4
    for payload in sent:
        assert payload['version'] == 2
        assert 'title' not in payload and 'body' not in payload
        assert not {'locale', 'language', 'lang'} & payload.keys()
        assert_catalog_messages((payload['title_ui_message'], payload['body_ui_message']))


def test_completion_and_pending_growth_emit_distinct_descriptors(monkeypatch):
    from twicc import external_notifications as events

    sent = []
    monkeypatch.setattr(events, '_last_seen', {})
    monkeypatch.setattr(events, 'read_synced_settings', dict)
    monkeypatch.setattr(events.android_push, 'dispatch', lambda *args, **kw: sent.append(args))
    events.notify_agent_event(info(pending_requests=[SimpleNamespace(request_type='ask_user_question')]), 'S', 'P', None)
    assert [event[0] for event in sent] == ['user_turn', 'pending_request']
    assert [event[1]['key'] for event in sent] == ['session.notifications.finished', 'session.notifications.question']


def test_apprise_messages_stay_unchanged(monkeypatch):
    from twicc import external_notifications as events

    sent = []
    monkeypatch.setattr(events, '_last_seen', {})
    monkeypatch.setattr(events, 'read_synced_settings', lambda: {'externalNotificationTargets': [{'enabled': True, 'tested': True, 'url': 'json://target'}]})
    monkeypatch.setattr(events.presence, 'is_user_present', lambda: False)
    monkeypatch.setattr(events, '_send', lambda urls, title, body: (title, body))
    monkeypatch.setattr(events, '_spawn', sent.append)
    events.notify_agent_event(info(pending_requests=[SimpleNamespace(request_type='ask_user_question')]), None, None, None)
    assert sent[:2] == [('Codex finished working', 'Project: Unknown\nSession: Unknown'), ('Codex has a question for you', 'Project: Unknown\nSession: Unknown')]
    events.notify_extra_usage_started('codex', snapshot(extra_usage_remaining_credits=20), events.read_synced_settings())
    assert sent[-1] == ('Codex — Extra usage started', 'Codex is currently drawing from your extra usage credit, billed on top of your plan.\n20 credits remaining.')
    events.notify_peer_message(message())
    assert sent[-1] == ('Message from Alice', '"Subject"\nPreview')
    events.notify_peer_request(peer(name='', remote_display_name=''))
    assert sent[-1] == ('Peer request from An instance', 'An instance wants to pair with your instance.\nAddress: https://peer.example')


@pytest.mark.parametrize('bad', [
    ui_message('session.notifications.finished', {'provider': None}),
    ui_message('session.notifications.moneyUsed', {'used': '1,20', 'currency': 'EUR'}),
    ui_message('session.notifications.moneyUsed', {'used': True, 'currency': 'EUR'}),
    ui_message('session.notifications.creditsUsed', {'used': ui_message('common.labels.unknown')}),
])
def test_required_and_decimal_parameters_must_be_renderable(bad):
    from twicc.android_push.messages import bounded_payload

    payload = bounded_payload(envelope(bad, ui_message('android.push.genericBody')))
    assert payload['title_ui_message'] == ui_message('android.push.genericTitle')


@pytest.mark.parametrize('producer', ['agent', 'money', 'credits', 'peer', 'request'])
def test_every_producer_fits_without_shortening_structural_or_numeric_parameters(producer):
    from twicc.android_push.messages import agent_messages, extra_usage_messages, peer_message_messages, peer_request_messages, bounded_payload
    from twicc.external_notifications import PeerRouting

    text = '😀\\"漢\n' * 2000
    if producer == 'agent':
        pair = agent_messages('user_turn', info(background_work_in_progress={'shells': 21}), text, text, text)
    elif producer == 'money':
        pair = extra_usage_messages('codex', snapshot(extra_usage_utilization=10, extra_usage_used_credits=4419, extra_usage_monthly_limit=8000, extra_usage_currency='USD', extra_usage_decimal_places=2))
    elif producer == 'credits':
        pair = extra_usage_messages('codex', snapshot(extra_usage_remaining_credits=21))
    elif producer == 'peer':
        pair = peer_message_messages(message(title=text, payload={'text': text}, peer=peer(name=text)), PeerRouting('s', text, 'p', text, text))
    else:
        pair = peer_request_messages(peer(name=text, base_url='https://peer.example/' + text))
    payload = bounded_payload(envelope(*pair))
    assert len(orjson.dumps(payload)) <= 3900
    assert payload['title_ui_message']['key'] == pair[0]['key']
    assert payload['body_ui_message']['key'] == pair[1]['key']
    assert payload['path'] == '/project/p/session/s'
    assert_catalog_messages((payload['title_ui_message'], payload['body_ui_message']))
    if producer in ('money', 'credits'):
        assert payload['body_ui_message'] == pair[1]


def test_pending_approval_does_not_reuse_completion_or_question(monkeypatch):
    from twicc import external_notifications as events

    sent = []
    monkeypatch.setattr(events, '_last_seen', {})
    monkeypatch.setattr(events, 'read_synced_settings', dict)
    monkeypatch.setattr(events.android_push, 'dispatch', lambda *args, **kw: sent.append(args))
    events.notify_agent_event(info(pending_requests=[SimpleNamespace(request_type='permission')]), 'S', 'P', None)
    assert [event[1]['key'] for event in sent] == ['session.notifications.finished', 'session.notifications.approval']


def test_invalid_unicode_envelope_cannot_escape_bounded_payload():
    from twicc.android_push.messages import bounded_payload

    payload = envelope(ui_message('android.push.genericTitle'), ui_message('android.push.genericBody'), sent_at='\ud800')
    assert bounded_payload(payload) is None
