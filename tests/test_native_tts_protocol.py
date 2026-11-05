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
