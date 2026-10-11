"""Content rows for codec tests, independent of reference services."""

import hashlib

import pytest
import zstandard

from twicc.core.models import ContentDictionary, SessionItemContent


@pytest.fixture
def content_row_factory(db):
    def create(text: str, compressed: bool, provider: str = "codex") -> SessionItemContent:
        raw = text.encode("utf-8")
        dictionary = ContentDictionary.objects.get(provider=provider, version=1) if compressed else None
        payload = raw
        if dictionary is not None:
            data = zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)
            payload = zstandard.ZstdCompressor(level=3, dict_data=data).compress(raw)
        return SessionItemContent.objects.create(
            sha256=hashlib.sha256(raw).digest(), payload=payload, codec="zstd" if compressed else "plain",
            dictionary=dictionary, raw_size=len(raw), dictionary_provider=provider,
        )
    return create
