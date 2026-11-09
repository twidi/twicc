"""Validation for version one of the native TTS control protocol."""

import re
from datetime import datetime
from uuid import UUID

import orjson

from twicc.summaries.sources import validate_temporary_model_request

PROTOCOL = 1
MAX_COMMAND_BYTES = 12 * 1024 * 1024
MAX_REPLAY_COMMANDS = 256
MAX_SEQUENCE = 2**53 - 1

_COMMON = {'protocol', 'type', 'seq'}
_FIELDS = {
    'open': {'client_id'},
    'resume': {'session_id', 'resume_secret', 'ack_seq', 'commands', 'current'},
    'listen': {'activation_id', 'scope', 'page_id', 'include_muted', 'boundary', 'fresh', 'notice', 'excluded_event_ids'},
    'set_muted': {'activation_id', 'include_muted'},
    'disable': {'activation_id'},
    'manual': {'reading_id', 'generation', 'request'},
    'select': {'event_id', 'reading_id', 'generation', 'silence_reset'},
    'started': {'reading_id', 'generation'},
    'finished': {'reading_id', 'generation', 'outcome', 'error'},
    'skip': {'reading_id', 'generation'},
    'stop_all': set(), 'ping': set(),
}


def _integer(value, label, minimum=1):
    if type(value) is not int or not minimum <= value <= MAX_SEQUENCE:
        raise ValueError(f'{label} must be an integer from {minimum} to {MAX_SEQUENCE}')


def _string(value, label, maximum=1024):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{label} must be a non-empty string of at most {maximum} characters')


def _uuid(value, label):
    _string(value, label, 36)
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ValueError(f'{label} must be a canonical UUID') from None


def _reading(value):
    _uuid(value.get('reading_id'), 'reading_id')
    _integer(value.get('generation'), 'generation')


def _request(value):
    if not isinstance(value, dict) or value.get('kind') not in ('text', 'summary'):
        raise ValueError('request must be a text or summary request')
    allowed = {'kind', 'speech', 'source', 'identity', 'text', 'config_id', 'summary'}
    if set(value) - allowed or not isinstance(value.get('speech'), dict):
        raise ValueError('request has invalid fields')
    identity = value.get('identity')
    if isinstance(identity, dict):
        if set(identity) != {'sourceKey', 'sourceHash', 'variant'}:
            raise ValueError('Invalid reading identity')
        for key in identity:
            if key == 'sourceKey' and identity[key] is None:
                continue
            _string(identity[key], f'identity {key}', 4096)
    elif identity is not None:
        raise ValueError('identity must be an object or null')
    if not isinstance(value.get('text'), str):
        raise ValueError('request text must be a string')  # noqa: TRY004 - wire errors use ValueError
    if value.get('config_id') is not None:
        _string(value['config_id'], 'config_id')
    if value['kind'] == 'text':
        if (value.get('source') not in ('message', 'selection', 'test') or 'summary' in value
                or {'summary_id', 'saved_summary_id', 'summary_result_hash'} & value['speech'].keys()):
            raise ValueError('Invalid text request source')
    else:
        summary = value.get('summary')
        if not isinstance(summary, dict):
            raise ValueError('summary must contain a frozen summary reference')
        if 'saved_id' in summary:
            if set(summary) != {'saved_id', 'result_hash'}:
                raise ValueError('Invalid saved summary reference')
            _integer(summary['saved_id'], 'saved_id')
            if not isinstance(summary['result_hash'], str) or not re.fullmatch('[0-9a-f]{64}', summary['result_hash']):
                raise ValueError('Invalid summary result hash')
        elif 'requested_model' in summary:
            if set(summary) != {'source', 'requested_model'}:
                raise ValueError('Invalid temporary summary reference')
            validate_temporary_model_request(summary['source'], summary['requested_model'])
        else:
            if set(summary) - {'source', 'source_key', 'source_hash', 'policy_key', 'handle_id'}:
                raise ValueError('Invalid summary reference fields')
            if not isinstance(summary.get('source'), dict):
                raise ValueError('summary source must be an object')
            if 'source_key' not in summary:
                raise ValueError('A summary source needs its frozen source key')
            if summary['source_key'] is not None:
                _string(summary['source_key'], 'source_key', 4096)
            for key in ('source_hash', 'policy_key'):
                _string(summary.get(key), key, 4096)
            if 'handle_id' in summary:
                _string(summary['handle_id'], 'handle_id')


def parse_native_command(data: object) -> dict:
    """Validate an entire frame, including replay, without changing session state."""
    try:
        size = len(orjson.dumps(data))
    except (TypeError, ValueError):
        raise ValueError('Command must be JSON') from None
    if size > MAX_COMMAND_BYTES:
        raise ValueError('The command is too large')
    if not isinstance(data, dict) or type(data.get('protocol')) is not int or data['protocol'] != PROTOCOL:
        raise ValueError('Unsupported native TTS protocol')
    kind = data.get('type')
    if not isinstance(kind, str) or kind not in _FIELDS or set(data) - _COMMON - _FIELDS[kind]:
        raise ValueError('Invalid command type or fields')
    _integer(data.get('seq'), 'seq')
    for key in ('reading_id', 'generation'):
        if key in _FIELDS[kind]:
            _reading(data)
            break
    if kind == 'open':
        _uuid(data.get('client_id'), 'client_id')
    elif kind == 'resume':
        _uuid(data.get('session_id'), 'session_id')
        _string(data.get('resume_secret'), 'resume_secret', 128)
        _integer(data.get('ack_seq'), 'ack_seq', 0)
        commands = data.get('commands')
        if not isinstance(commands, list) or len(commands) > MAX_REPLAY_COMMANDS:
            raise ValueError('Invalid replay commands')
        last = data['ack_seq']
        for command in commands:
            parse_native_command(command)
            if command['type'] in ('open', 'resume') or command['seq'] <= last:
                raise ValueError('Replay commands must have increasing sequence numbers')
            last = command['seq']
        if data['seq'] <= last:
            raise ValueError('Resume sequence must follow retained commands')
        current = data.get('current')
        if current is not None:
            if not isinstance(current, dict) or set(current) - {'reading_id', 'generation', 'audio_id', 'phase', 'started'}:
                raise ValueError('Invalid current reading')
            _reading(current)
            if current.get('audio_id') is not None:
                _string(current['audio_id'], 'audio_id')
            if current.get('phase') not in ('loading', 'buffering', 'playing', 'settling') or type(current.get('started')) is not bool:
                raise ValueError('Invalid current phase')
    elif kind == 'manual':
        _request(data.get('request'))
    elif kind == 'listen':
        scope = data.get('scope')
        if not isinstance(scope, dict) or set(scope) != {'kind', 'id'} or scope['kind'] not in ('global', 'project', 'workspace', 'worktree'):
            raise ValueError('Invalid listening scope')
        if scope['kind'] == 'global':
            if scope['id'] is not None:
                raise ValueError('Global scope must have a null ID')
        else:
            _string(scope['id'], 'scope id')
        _string(data.get('page_id'), 'page_id')
        if 'fresh' in data and type(data['fresh']) is not bool:
            raise ValueError('fresh must be boolean')
        excluded = data.get('excluded_event_ids', [])
        if not isinstance(excluded, list):
            raise ValueError('excluded_event_ids must be an array')
        for event_id in excluded:
            _integer(event_id, 'excluded event id')
        notice = data.get('notice')
        if notice is not None:
            if not isinstance(notice, dict) or set(notice) != {'reading_id', 'generation', 'text'}:
                raise ValueError('Invalid recovery notice')
            _reading(notice)
            _string(notice['text'], 'notice text', 4096)
        boundary = data.get('boundary')
        if not isinstance(boundary, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', boundary):
            raise ValueError('boundary must be UTC with six fractional digits')
        try:
            datetime.fromisoformat(boundary)
        except ValueError:
            raise ValueError('Invalid boundary timestamp') from None
    elif kind == 'select':
        _integer(data.get('event_id'), 'event_id')
        if 'silence_reset' in data and type(data['silence_reset']) is not bool:
            raise ValueError('silence_reset must be boolean')
    elif kind == 'finished':
        if data.get('outcome') not in ('complete', 'failed', 'aborted'):
            raise ValueError('Invalid reading outcome')
        if 'error' in data and data['error'] is not None:
            _string(data['error'], 'error', 4096)
    for key in ('activation_id',):
        if key in _FIELDS[kind]:
            _uuid(data.get(key), key)
    if kind in ('listen', 'set_muted') and type(data.get('include_muted')) is not bool:
        raise ValueError('include_muted must be boolean')
    return orjson.loads(orjson.dumps(data))
