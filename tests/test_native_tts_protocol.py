"""Reject malformed native frames before mutation."""
from uuid import uuid4

import pytest

from twicc.tts.native_protocol import parse_native_command


def frame(kind, **values):
    return {'protocol': 1, 'type': kind, 'seq': 1, **values}


def test_manual_preserves_full_speech_payload():
    request = {'kind': 'text', 'source': 'selection', 'identity': None, 'text': 'Hello',
               'config_id': None, 'speech': {'text': 'Hello', 'kind': 'script', 'command': 'say'}}
    command = frame('manual', reading_id=str(uuid4()), generation=1, request=request)
    assert parse_native_command(command) == command


@pytest.mark.parametrize('command', [None, [], {}, frame('bogus'), frame('ping', seq=True),
    frame('ping', seq=0), frame('ping', protocol=True), frame('manual', reading_id='x', generation=1, request={}),
    frame('finished', reading_id=str(uuid4()), generation=1, outcome='buffered'),
    frame('open', client_id=''), frame('ping', auth_binding=['forged']),
    frame('listen', activation_id=str(uuid4()), page_id='page', scope={'kind': 'global', 'id': None},
          include_muted=False, boundary='2026-11-05T12:00:00Z'),
    frame('manual', reading_id=str(uuid4()), generation=1,
          request={'kind': 'text', 'source': 'invalid', 'speech': {}}),
])
def test_rejects_invalid_commands(command):
    with pytest.raises(ValueError):
        parse_native_command(command)


def test_rejects_oversize_frame():
    with pytest.raises(ValueError):
        parse_native_command(frame('ping', padding='x' * (12 * 1024 * 1024)))


def test_resume_validates_nested_commands_before_accepting():
    with pytest.raises(ValueError):
        parse_native_command(frame('resume', session_id=str(uuid4()), resume_secret='x' * 43,
                                   ack_seq=0, commands=[frame('stop_all'), frame('skip')], current=None))


def test_resume_sequence_must_follow_offline_commands():
    with pytest.raises(ValueError):
        parse_native_command(frame('resume', seq=2, session_id=str(uuid4()), resume_secret='x' * 43,
                                   ack_seq=1, commands=[frame('stop_all', seq=3)], current=None))


def test_text_cannot_smuggle_a_page_owned_summary_handle():
    with pytest.raises(ValueError):
        parse_native_command(frame('manual', reading_id=str(uuid4()), generation=1,
                                   request={'kind': 'text', 'source': 'message', 'identity': None, 'text': '',
                                            'speech': {'summary_id': 'page-only'}}))


def test_manual_preserves_existing_summary_identity_object():
    identity = {'sourceKey': 'item:s1:2', 'sourceHash': 'a' * 64, 'variant': 'summary'}
    command = frame('manual', reading_id=str(uuid4()), generation=1,
                    request={'kind': 'summary', 'speech': {}, 'identity': identity, 'text': 'Summary',
                             'summary': {'saved_id': 1, 'result_hash': 'a' * 64}})
    assert parse_native_command(command)['request']['identity'] == identity


def test_rejects_noncanonical_string_identity():
    with pytest.raises(ValueError):
        parse_native_command(frame('manual', reading_id=str(uuid4()), generation=1,
                                   request={'kind': 'text', 'source': 'message', 'identity': 'message:1',
                                            'text': 'Text', 'speech': {'text': 'Text'}}))


def test_listen_accepts_existing_worktree_scope():
    command = frame('listen', activation_id=str(uuid4()), page_id='worktree-project',
                    scope={'kind': 'worktree', 'id': 'worktree-project'},
                    include_muted=False, boundary='2026-11-05T12:00:00.000000Z')
    assert parse_native_command(command)['scope'] == {'kind': 'worktree', 'id': 'worktree-project'}


def test_manual_preserves_temporary_summary_identity_with_null_source_key():
    identity = {'sourceKey': None, 'sourceHash': 'a' * 64, 'variant': 'summary'}
    command = frame('manual', reading_id=str(uuid4()), generation=1,
                    request={'kind': 'summary', 'speech': {}, 'identity': identity, 'text': 'Summary',
                             'summary': {'source': {'text': 'Source text', 'purpose': 'selection'},
                                         'source_key': None, 'source_hash': 'a' * 64, 'policy_key': 'policy:frozen'}})
    assert parse_native_command(command)['request']['identity'] == identity


@pytest.mark.parametrize('member', ['sourceHash', 'variant'])
def test_temporary_identity_rejects_null_nonkey_members(member):
    identity = {'sourceKey': None, 'sourceHash': 'a' * 64, 'variant': 'summary', member: None}
    with pytest.raises(ValueError):
        parse_native_command(frame('manual', reading_id=str(uuid4()), generation=1,
                                   request={'kind': 'summary', 'speech': {}, 'identity': identity, 'text': 'Summary',
                                            'summary': {'saved_id': 1, 'result_hash': 'a' * 64}}))

@pytest.mark.parametrize('model', ['auto', 'haiku', 'luna', 'openrouter'])
def test_temporary_summary_before_descriptor_has_frozen_model_and_source(model):
    summary = {'source': {'text': 'Captured selection', 'purpose': 'selection'}, 'requested_model': model}
    command = frame('manual', reading_id=str(uuid4()), generation=1,
                    request={'kind': 'summary', 'speech': {}, 'identity': None, 'text': '', 'summary': summary})
    assert parse_native_command(command)['request']['summary'] == summary

@pytest.mark.parametrize('summary', [
    {'source': {'event_id': 1}, 'requested_model': 'auto'},
    {'source': {'text': 'Captured', 'purpose': 'invalid'}, 'requested_model': 'auto'},
    {'source': {'text': 'Captured', 'purpose': 'selection'}, 'requested_model': 'invalid'},
    {'source': {'text': 'Captured', 'purpose': 'selection'}, 'requested_model': 'auto', 'handle_id': 'page'},
    {'source': {'text': 'Captured', 'purpose': 'selection'}, 'requested_model': 'auto', 'policy_key': 'forged'},
])
def test_descriptorless_summary_is_strictly_temporary(summary):
    with pytest.raises(ValueError):
        parse_native_command(frame('manual', reading_id=str(uuid4()), generation=1,
            request={'kind': 'summary', 'speech': {}, 'identity': None, 'text': '', 'summary': summary}))


@pytest.mark.parametrize('silence_reset', [False, True])
def test_selection_accepts_frozen_native_silence_fact(silence_reset):
    command = frame('select', event_id=1, reading_id=str(uuid4()), generation=1, silence_reset=silence_reset)
    assert parse_native_command(command)['silence_reset'] is silence_reset


@pytest.mark.parametrize('silence_reset', [None, 0, 1, 'true', []])
def test_selection_rejects_non_boolean_silence_fact(silence_reset):
    with pytest.raises(ValueError):
        parse_native_command(frame('select', event_id=1, reading_id=str(uuid4()), generation=1, silence_reset=silence_reset))
