"""Stable identity for the compression attempt policy."""

from datetime import datetime, timedelta

from twicc.core.content_storage.codec import COMPRESSION_LEVEL, ContentIntegrityError, _load_dictionary


def compression_policy_version(provider: str, dictionary_id: int) -> str:
    dictionary = _load_dictionary(dictionary_id)
    if dictionary.provider != provider:
        raise ContentIntegrityError('Content dictionary provider mismatch')
    return (
        f'zstd:level={COMPRESSION_LEVEL}:dictionary={bytes(dictionary.sha256).hex()}'
        ':format=raw:savings=compressed-size<raw-size'
    )


COLD_DATE_FIELDS = ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at')
COLD_FIELDS = ('archived', 'hidden', *COLD_DATE_FIELDS)
CONTENT_STORAGE_VERSION = 1


def cold_from_fields(fields: dict, now: datetime) -> bool:
    """Use complete loaded lifecycle metadata; unknown metadata cannot authorize cold placement."""
    if any(name not in fields for name in COLD_FIELDS):
        return False
    if not (fields['archived'] or fields['hidden']):
        return False
    dates = [fields[name] for name in COLD_DATE_FIELDS if fields[name] is not None]
    return bool(dates) and max(dates) <= now - timedelta(days=30)


def cold_from_loaded_session(session, now: datetime) -> bool:
    """Inspect only explicit model fields and cached native parents, without database access."""
    visited = set()
    while session is not None:
        fields = session.__dict__
        identity = fields.get('id')
        if identity is None or identity in visited:
            return False
        visited.add(identity)
        if 'type' not in fields or 'parent_session_id' not in fields:
            return False
        if fields['type'] == 'session':
            return fields['parent_session_id'] is None and cold_from_fields(fields, now)
        if fields['type'] != 'subagent' or fields['parent_session_id'] is None:
            return False
        parent = session._state.fields_cache.get('parent_session')
        if parent is None or parent.__dict__.get('id') != fields['parent_session_id']:
            return False
        session = parent
    return False
