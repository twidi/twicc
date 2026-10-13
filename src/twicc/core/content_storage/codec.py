"""Lossless UTF-8 representations with explicit integrity checks."""

import hashlib
from codecs import getincrementaldecoder
from time import sleep

import zstandard

from twicc.core.content_storage.types import EncodedContent
from twicc.core.models import ContentDictionary, SessionItemContent


COMPRESSION_LEVEL = 3
MAX_DICTIONARY_BYTES = 16 * 1024
UTF8_CHUNK_BYTES = 1024 * 1024


class ContentIntegrityError(ValueError):
    """Stored content or dictionary cannot reproduce its exact logical bytes."""


def _binary(value, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ContentIntegrityError(f"Invalid {name} bytes")
    return bytes(value)


def _load_dictionary(dictionary_id: int) -> ContentDictionary:
    from twicc.core.content_storage.dictionaries import get_dictionary

    return get_dictionary(dictionary_id).row


def _validate_dictionary(dictionary: ContentDictionary) -> ContentDictionary:
    payload = _binary(dictionary.payload, 'dictionary payload')
    digest = _binary(dictionary.sha256, 'dictionary digest')
    if dictionary.format != 'raw' or not 0 < len(payload) <= MAX_DICTIONARY_BYTES:
        raise ContentIntegrityError('Invalid content dictionary format or size')
    if len(digest) != 32 or hashlib.sha256(payload).digest() != digest:
        raise ContentIntegrityError('Content dictionary digest mismatch')
    return dictionary


def _dictionary_data(dictionary: ContentDictionary) -> zstandard.ZstdCompressionDict:
    return zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)


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
    dictionary = _operation_dictionary(dictionary_id, None)
    compressed = zstandard.ZstdCompressor(level=COMPRESSION_LEVEL, dict_data=dictionary.native).compress(
        plain.payload
    )
    if len(compressed) >= plain.raw_size:
        return plain, True
    return EncodedContent(compressed, 'zstd', dictionary_id, plain.raw_size, plain.sha256), False


def _operation_dictionary(dictionary_id, cache):
    from twicc.core.content_storage.dictionaries import get_dictionary

    if cache is None:
        return get_dictionary(dictionary_id)
    if dictionary_id not in cache:
        dictionary = get_dictionary(dictionary_id)
        if len(cache) >= 32:
            cache.pop(next(iter(cache)))
        cache[dictionary_id] = dictionary
    return cache[dictionary_id]


def _decode_bytes(row: SessionItemContent, dictionary_cache=None) -> bytes:
    """Verify one representation before exposing its logical bytes."""
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
        dictionary = _operation_dictionary(row.dictionary_id, dictionary_cache)
        try:
            frame_size = zstandard.frame_content_size(payload)
            # frame_content_size returns -1 for unknown size, unlike the C sentinel.
            if frame_size not in (row.raw_size, -1):
                raise ContentIntegrityError('Compressed frame size mismatch')
            # A fresh decoder avoids sharing mutable decoder state between threads.
            raw = zstandard.ZstdDecompressor(dict_data=dictionary.native).decompress(
                payload, max_output_size=max(row.raw_size, 1), allow_extra_data=False
            )
        except zstandard.ZstdError as error:
            raise ContentIntegrityError('Invalid compressed content frame') from error
    else:
        raise ContentIntegrityError(f'Unknown content codec {row.codec!r}')
    if len(raw) != row.raw_size or hashlib.sha256(raw).digest() != digest:
        raise ContentIntegrityError('Content size or digest mismatch')
    return raw


def transcode_content(row: SessionItemContent, dictionary_id: int | None = None, *, dictionary_cache=None):
    """Change representation without re-encoding text or re-hashing verified bytes.

    The optional dictionary cache belongs to one bounded preparation operation.
    Strict incremental UTF-8 validation bounds temporary strings and GIL work.
    Worker sleeps release the GIL between chunks and after each content.
    """
    raw = _decode_bytes(row, dictionary_cache)
    validator = getincrementaldecoder('utf-8')()
    try:
        for offset in range(0, len(raw), UTF8_CHUNK_BYTES):
            end = offset + UTF8_CHUNK_BYTES
            validator.decode(memoryview(raw)[offset:end], final=end >= len(raw))
            if end < len(raw):
                sleep(0)
    except UnicodeDecodeError as error:
        raise ContentIntegrityError('Content payload is not valid UTF-8') from error
    plain = EncodedContent(raw, 'plain', None, row.raw_size, bytes(row.sha256))
    if dictionary_id is None:
        result = plain, False
    elif row.codec == 'zstd' and row.dictionary_id == dictionary_id:
        result = EncodedContent(bytes(row.payload), 'zstd', dictionary_id, row.raw_size, plain.sha256), False
    else:
        dictionary = _operation_dictionary(dictionary_id, dictionary_cache)
        compressed = zstandard.ZstdCompressor(
            level=COMPRESSION_LEVEL, dict_data=dictionary.native,
        ).compress(raw)
        if len(compressed) >= row.raw_size:
            result = plain, True
        else:
            result = EncodedContent(compressed, 'zstd', dictionary_id, row.raw_size, plain.sha256), False
    sleep(0)
    return result


def decode_content(row: SessionItemContent, *, dictionary_cache=None) -> str:
    """Decode one requested row and verify its exact size, digest, and UTF-8."""
    raw = _decode_bytes(row, dictionary_cache)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ContentIntegrityError('Content payload is not valid UTF-8') from error
