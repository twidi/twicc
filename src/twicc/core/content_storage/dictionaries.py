"""Process lifetime registry of validated immutable content dictionaries."""

from threading import RLock
from typing import NamedTuple

import zstandard
from django.db import connections

from twicc.core.models import ContentDictionary


class DictionaryEntry(NamedTuple):
    row: ContentDictionary
    native: zstandard.ZstdCompressionDict
    policy_version: str


_lock = RLock()
_entries: dict[tuple, DictionaryEntry] = {}
_providers: dict[tuple, DictionaryEntry] = {}


def _identity():
    connection = connections['default']
    config = connection.settings_dict
    return tuple(str(config.get(key, '')) for key in ('ENGINE', 'NAME', 'HOST', 'PORT', 'USER'))


def _entry(row):
    from twicc.core.content_storage.codec import COMPRESSION_LEVEL, _dictionary_data, _validate_dictionary

    _validate_dictionary(row)
    return DictionaryEntry(
        row, _dictionary_data(row),
        f'zstd:level={COMPRESSION_LEVEL}:dictionary={bytes(row.sha256).hex()}'
        ':format=raw:savings=compressed-size<raw-size',
    )


def reset_dictionary_cache() -> None:
    """End the registry lifetime after all owned workers stop."""
    with _lock:
        _entries.clear()
        _providers.clear()


def get_dictionary(dictionary_id: int) -> DictionaryEntry:
    """Load and validate an unseen committed identity once."""
    from twicc.core.content_storage.codec import ContentIntegrityError

    with _lock:
        key = _identity(), dictionary_id
        if key in _entries:
            return _entries[key]
        try:
            row = ContentDictionary.objects.get(pk=dictionary_id)
        except ContentDictionary.DoesNotExist as error:
            raise ContentIntegrityError(f'Missing content dictionary {dictionary_id}') from error
        entry = _entry(row)
        # A transaction can expose a row that later rolls back. Keep it local.
        if connections['default'].get_autocommit():
            _entries[key] = entry
        return entry


def get_provider_dictionary(provider: str) -> DictionaryEntry | None:
    """Select the newest immutable dictionary within this database lifetime."""
    with _lock:
        key = _identity(), provider
        if key in _providers:
            return _providers[key]
        row = ContentDictionary.objects.filter(provider=provider).order_by('-version').first()
        if row is None:
            return None
        entry = get_dictionary(row.id)
        if connections['default'].get_autocommit():
            _providers[key] = entry
        return entry


def preload_dictionaries() -> None:
    """Validate committed startup rows before processing source content."""
    with _lock:
        identity = _identity()
        entries = []
        for row in ContentDictionary.objects.order_by('version'):
            entry = _entries.get((identity, row.id)) or _entry(row)
            entries.append(entry)
        if connections['default'].get_autocommit():
            for entry in entries:
                _entries[identity, entry.row.id] = entry
                _providers[identity, entry.row.provider] = entry
