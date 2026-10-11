"""Lossless UTF-8 representations with explicit integrity checks."""

import hashlib
from functools import lru_cache

import zstandard

from twicc.core.content_storage.types import EncodedContent
from twicc.core.models import ContentDictionary, SessionItemContent


COMPRESSION_LEVEL = 3
MAX_DICTIONARY_BYTES = 16 * 1024


class ContentIntegrityError(ValueError):
    """Stored content or dictionary cannot reproduce its exact logical bytes."""


def _binary(value, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ContentIntegrityError(f"Invalid {name} bytes")
    return bytes(value)


def _load_dictionary(dictionary_id: int) -> ContentDictionary:
    try:
        dictionary = ContentDictionary.objects.get(pk=dictionary_id)
    except ContentDictionary.DoesNotExist as error:
        raise ContentIntegrityError(f"Missing content dictionary {dictionary_id}") from error
    payload = _binary(dictionary.payload, 'dictionary payload')
    digest = _binary(dictionary.sha256, 'dictionary digest')
    if dictionary.format != 'raw' or not 0 < len(payload) <= MAX_DICTIONARY_BYTES:
        raise ContentIntegrityError('Invalid content dictionary format or size')
    if len(digest) != 32 or hashlib.sha256(payload).digest() != digest:
        raise ContentIntegrityError('Content dictionary digest mismatch')
    return dictionary


@lru_cache(maxsize=32)
def _compression_dictionary(digest: bytes, payload: bytes) -> zstandard.ZstdCompressionDict:
    # Immutable bytes and digest identify the dictionary across database identities.
    return zstandard.ZstdCompressionDict(payload, dict_type=zstandard.DICT_TYPE_RAWCONTENT)


def _dictionary_data(dictionary: ContentDictionary) -> zstandard.ZstdCompressionDict:
    return _compression_dictionary(bytes(dictionary.sha256), bytes(dictionary.payload))


def encode_plain(text: str) -> EncodedContent:
    """Retain the exact string without parsing or normalizing it."""
    try:
        raw = text.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ContentIntegrityError('Content is not valid UTF-8 text') from error
    return EncodedContent(raw, 'plain', None, len(raw), hashlib.sha256(raw).digest())


def encode_cold(text: str, dictionary_id: int, policy_version: str) -> tuple[EncodedContent, bool]:
    """Compress only when smaller; the caller records the supplied policy identity."""
    plain = encode_plain(text)
    dictionary = _load_dictionary(dictionary_id)
    compressed = zstandard.ZstdCompressor(level=COMPRESSION_LEVEL, dict_data=_dictionary_data(dictionary)).compress(
        plain.payload
    )
    if len(compressed) >= plain.raw_size:
        return plain, True
    return EncodedContent(compressed, 'zstd', dictionary_id, plain.raw_size, plain.sha256), False


def decode_content(row: SessionItemContent) -> str:
    """Decode one requested row and verify its exact size and digest."""
    payload = _binary(row.payload, 'content payload')
    digest = _binary(row.sha256, 'content digest')
    if type(row.raw_size) is not int or row.raw_size < 0 or len(digest) != 32:
        raise ContentIntegrityError('Invalid content size or digest')
    if row.codec == 'plain':
        if row.dictionary_id is not None:
            raise ContentIntegrityError('Plain content has a dictionary')
        raw = payload
    elif row.codec == 'zstd':
        if row.dictionary_id is None:
            raise ContentIntegrityError('Compressed content has no dictionary')
        dictionary = _load_dictionary(row.dictionary_id)
        try:
            frame_size = zstandard.frame_content_size(payload)
            # frame_content_size returns -1 for unknown size, unlike the C sentinel.
            if frame_size not in (row.raw_size, -1):
                raise ContentIntegrityError('Compressed frame size mismatch')
            # A fresh decoder avoids sharing mutable decoder state between threads.
            raw = zstandard.ZstdDecompressor(dict_data=_dictionary_data(dictionary)).decompress(
                payload, max_output_size=max(row.raw_size, 1), allow_extra_data=False
            )
        except zstandard.ZstdError as error:
            raise ContentIntegrityError('Invalid compressed content frame') from error
    else:
        raise ContentIntegrityError(f'Unknown content codec {row.codec!r}')
    if len(raw) != row.raw_size or hashlib.sha256(raw).digest() != digest:
        raise ContentIntegrityError('Content size or digest mismatch')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ContentIntegrityError('Content payload is not valid UTF-8') from error
