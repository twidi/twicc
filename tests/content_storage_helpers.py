"""Content rows for codec tests, independent of reference services."""

import hashlib
from collections.abc import Sequence
from importlib.resources import files

import orjson
import pytest
import zstandard

from twicc.core.models import ContentDictionary, SessionItemContent


def _public_dictionary(provider: str) -> ContentDictionary:
    """Restore exact public assets after transaction-test database flushes."""
    try:
        return ContentDictionary.objects.get(provider=provider, version=1)
    except ContentDictionary.DoesNotExist:
        directory = files('twicc.core.content_storage').joinpath('dictionaries')
        entries = orjson.loads(directory.joinpath('manifest.json').read_bytes())['dictionaries']
        entry = next(entry for entry in entries if entry['provider'] == provider and entry['version'] == 1)
        payload = directory.joinpath(entry['file']).read_bytes()
        digest = hashlib.sha256(payload).digest()
        if digest.hex() != entry['sha256'] or len(payload) != entry['byte_length'] or not 0 < len(payload) <= 16384:
            raise ValueError('Bundled content dictionary integrity failure')
        return ContentDictionary.objects.create(
            provider=provider, version=1, format=entry['format'], sha256=digest, payload=payload,
        )


@pytest.fixture
def content_row_factory(db):
    def create(
        text: str, compressed: bool, provider: str = "codex", *, session_ids: Sequence[str] = (),
    ) -> SessionItemContent:
        raw = text.encode("utf-8")
        dictionary = _public_dictionary(provider) if compressed else None
        payload = raw
        if dictionary is not None:
            data = zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)
            payload = zstandard.ZstdCompressor(level=3, dict_data=data).compress(raw)
        return SessionItemContent.objects.create(
            sha256=hashlib.sha256(raw).digest(), payload=payload, codec="zstd" if compressed else "plain",
            dictionary=dictionary, raw_size=len(raw), dictionary_provider=provider, session_ids=sorted(set(session_ids)),
        )
    return create


def read_current(item) -> str:
    """Explicitly load a persisted item before checking its logical string."""
    from twicc.core.content_storage.readers import effective_current, load_current

    load_current([item])
    return effective_current(item)
