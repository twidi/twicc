"""UI metadata remains separate from English machine-facing prose."""

import orjson
import pickle
from copy import copy, deepcopy

from twicc.core.ui_messages import ui_error, ui_message, ui_message_text


def test_wire_descriptor_preserves_named_parameters_and_zero_count():
    assert ui_message('backend.test.items', {'name': 'example'}, count=0) == {
        'key': 'backend.test.items', 'params': {'name': 'example'}, 'count': 0,
    }


def test_english_error_and_machine_fields_remain_available():
    assert ui_error('Directory missing', 'backend.test.directoryMissing', {'directory': '/tmp'}, code='missing') == {
        'error': 'Directory missing',
        'ui_message': {'key': 'backend.test.directoryMissing', 'params': {'directory': '/tmp'}},
        'code': 'missing',
    }


def test_internal_message_metadata_does_not_change_cli_serialization():
    message = ui_message_text('Directory missing', 'backend.test.directoryMissing', {'directory': '/tmp'})
    assert isinstance(message, str)
    assert message == 'Directory missing'
    assert message.ui_message == {'key': 'backend.test.directoryMissing', 'params': {'directory': '/tmp'}}
    assert orjson.loads(orjson.dumps({'message': message})) == {'message': 'Directory missing'}
    assert str(message) == 'Directory missing'


def test_message_metadata_survives_worker_ipc():
    message = ui_message_text('Waiting for 2 tasks', 'backend.test.waiting', {'count': 2}, count=2)
    restored = pickle.loads(pickle.dumps(message))
    assert restored == message
    assert restored.ui_message == message.ui_message


def test_message_metadata_survives_copy_and_deepcopy():
    message = ui_message_text('Waiting', 'backend.test.waiting', {'count': 2}, count=2)
    for clone in [copy(message), deepcopy(message)]:
        assert clone == message
        assert clone.ui_message == message.ui_message
    clone.ui_message['params']['count'] = 3
    assert message.ui_message['params']['count'] == 2
