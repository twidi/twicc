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


def prepare_plaintext_search(session) -> None:
    """Extract explicit fixture content and publish readiness through the real verifier."""
    from django.db import transaction
    from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION
    from twicc.core.content_storage.references import bind_persisted_items, intern_contents
    from twicc.core.content_storage.types import ContentBinding
    from twicc.core.content_storage.state import ensure_storage_incarnation, snapshot_root_storage, verify_plaintext_root
    from twicc.core.models import Session, SessionItem

    with transaction.atomic():
        ensure_storage_incarnation(session.id)
        for item in SessionItem.objects.filter(session=session):
            if item.current_content_id is None:
                text = item.content
                ids = intern_contents([text], session.provider)
                SessionItem.objects.filter(pk=item.pk).update(content='', current_content_id=ids[text])
                bind_persisted_items([ContentBinding(item.pk, session.id, ids[text], item.original_content_id)])
        Session.objects.filter(pk=session.pk).update(content_storage_version=CONTENT_STORAGE_VERSION)
        snapshot = snapshot_root_storage(session.id)
        assert verify_plaintext_root(snapshot)


@pytest.fixture(autouse=True)
def isolated_dictionary_cache():
    from twicc.core.content_storage.dictionaries import reset_dictionary_cache
    from twicc.core.content_storage.cold_cache import reset_cold_cache

    # Database flushes replace lifecycle rows without changing the file identity.
    reset_dictionary_cache()
    reset_cold_cache()
    yield
    reset_dictionary_cache()
    reset_cold_cache()
