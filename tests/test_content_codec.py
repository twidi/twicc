"""Exact content bytes, representation validation, and compression policy contracts."""

import hashlib

import pytest
import zstandard

from twicc.core.content_storage.codec import ContentIntegrityError, decode_content, encode_cold, encode_plain
from twicc.core.content_storage.policy import compression_policy_version
from twicc.core.models import ContentDictionary, SessionItemContent


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("text", ['', '  {"text":"é\\n","a":1}  ', '{broken', 'a\x00b', 'é🙂\\n' * 400_000])
@pytest.mark.parametrize("compressed", [False, True])
def test_exact_codec_roundtrip(content_row_factory, text, compressed):
    row = content_row_factory(text=text, compressed=compressed)
    assert decode_content(row) == text
    assert row.sha256 == hashlib.sha256(text.encode('utf-8')).digest()


def test_encoders_preserve_exact_bytes(content_row_factory):
    text = 'é🙂 raw\x00 text ' * 1000
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    plain = encode_plain(text)
    assert plain.payload == text.encode('utf-8')
    assert plain.codec == 'plain'
    assert plain.dictionary_id is None
    assert plain.raw_size == len(text.encode('utf-8'))
    assert plain.sha256 == hashlib.sha256(text.encode('utf-8')).digest()
    encoded, no_gain = encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
    assert no_gain is False
    assert encoded.codec == 'zstd'
    assert encoded.dictionary_id == dictionary.id
    row = SessionItemContent(**encoded._asdict())
    assert decode_content(row) == text


@pytest.mark.parametrize('text', ['', 'x', 'é🙂'])
def test_expansion_retains_plain_bytes(text):
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    encoded, no_gain = encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
    assert no_gain is True
    assert encoded == encode_plain(text)


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('field,value', [('raw_size', 999), ('raw_size', -1), ('sha256', b'x' * 32),
                                        ('sha256', b'x'), ('codec', 'unknown'), ('payload', None)])
def test_invalid_representation_is_integrity_error(content_row_factory, compressed, field, value):
    row = content_row_factory(text='exact bytes', compressed=compressed)
    setattr(row, field, value)
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


def test_plain_dictionary_is_rejected(content_row_factory):
    row = content_row_factory(text='plain', compressed=False)
    row.dictionary_id = ContentDictionary.objects.get(provider='codex', version=1).id
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


@pytest.mark.parametrize('dictionary_id', [None, 999999])
def test_missing_dictionary_is_integrity_error(content_row_factory, dictionary_id):
    row = content_row_factory(text='compressed', compressed=True)
    row.dictionary_id = dictionary_id
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


@pytest.mark.parametrize('payload', [b'broken', b'', None])
def test_corrupt_frame_is_integrity_error(content_row_factory, payload):
    row = content_row_factory(text='compressed', compressed=True)
    row.payload = payload
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


def test_extra_frame_bytes_are_rejected(content_row_factory):
    row = content_row_factory(text='compressed', compressed=True)
    row.payload = bytes(row.payload) + zstandard.ZstdCompressor().compress(b'extra')
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


def test_invalid_utf8_is_integrity_error():
    raw = b'\xff'
    row = SessionItemContent(codec='plain', payload=raw, raw_size=1, sha256=hashlib.sha256(raw).digest())
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


@pytest.mark.parametrize('field,value', [('payload', b'corrupt'), ('format', 'unsupported'), ('sha256', b'x' * 32)])
def test_dictionary_validation_survives_cache(content_row_factory, field, value):
    row = content_row_factory(text='compressed', compressed=True)
    assert decode_content(row) == 'compressed'
    ContentDictionary.objects.filter(pk=row.dictionary_id).update(**{field: value})
    with pytest.raises(ContentIntegrityError):
        decode_content(row)


def test_old_dictionary_remains_readable(content_row_factory):
    row = content_row_factory(text='old dictionary text', compressed=True)
    old_version = compression_policy_version('codex', row.dictionary_id)
    raw = b'public replacement dictionary'
    replacement = ContentDictionary.objects.create(provider='codex', version=2, format='raw',
                                                  payload=raw, sha256=hashlib.sha256(raw).digest())
    new_version = compression_policy_version('codex', replacement.id)
    assert old_version != new_version
    assert compression_policy_version('codex', row.dictionary_id) == old_version
    assert decode_content(row) == 'old dictionary text'


def test_policy_rejects_provider_mismatch():
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    with pytest.raises(ContentIntegrityError):
        compression_policy_version('claude_code', dictionary.id)


def test_encode_rejects_missing_dictionary():
    with pytest.raises(ContentIntegrityError):
        encode_cold('content', 999999, 'policy')


@pytest.mark.parametrize('incorrect_size', [False, True])
def test_unknown_frame_size_has_bounded_output(content_row_factory, incorrect_size):
    text = 'unknown frame size ' * 1000
    row = content_row_factory(text=text, compressed=True)
    dictionary = ContentDictionary.objects.get(pk=row.dictionary_id)
    data = zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    row.payload = zstandard.ZstdCompressor(level=3, dict_data=data, write_content_size=False).compress(text.encode())
    if incorrect_size:
        row.raw_size = 1
        with pytest.raises(ContentIntegrityError):
            decode_content(row)
    else:
        assert decode_content(row) == text


@pytest.mark.parametrize('compressed', [False, True])
def test_transcode_preserves_verified_bytes(content_row_factory, compressed):
    from twicc.core.content_storage.codec import transcode_content

    text = 'é🙂 raw\x00 text ' * 1000
    row = content_row_factory(text=text, compressed=compressed)
    dictionary_id = ContentDictionary.objects.get(provider='codex', version=1).id
    encoded, no_gain = transcode_content(row, dictionary_id if not compressed else None)
    assert encoded.sha256 == bytes(row.sha256)
    assert encoded.raw_size == row.raw_size
    assert decode_content(SessionItemContent(**encoded._asdict())) == text
    assert no_gain is False


@pytest.mark.parametrize('compressed', [False, True])
def test_transcode_rejects_invalid_utf8_with_valid_digest(compressed):
    from twicc.core.content_storage.codec import transcode_content

    raw = b'a' * (1024 * 1024 - 1) + b'\xf0\x9f\x99\x82' + b'\xff'
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    payload = raw
    if compressed:
        data = zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)
        payload = zstandard.ZstdCompressor(dict_data=data).compress(raw)
    row = SessionItemContent(payload=payload, codec='zstd' if compressed else 'plain',
                             dictionary_id=dictionary.id if compressed else None,
                             raw_size=len(raw), sha256=hashlib.sha256(raw).digest())
    with pytest.raises(ContentIntegrityError, match='UTF-8'):
        transcode_content(row, dictionary.id if not compressed else None)


def test_transcode_validates_utf8_across_chunk_boundary():
    from twicc.core.content_storage.codec import transcode_content

    raw = b'a' * (1024 * 1024 - 1) + '🙂'.encode() + b'z'
    row = SessionItemContent(codec='plain', payload=raw, raw_size=len(raw), sha256=hashlib.sha256(raw).digest())
    encoded, _ = transcode_content(row)
    assert encoded.payload == raw


def test_transcode_batch_reuses_dictionary_queries(content_row_factory, django_assert_num_queries):
    from twicc.core.content_storage.codec import transcode_content

    rows = [content_row_factory(text=f'large exact bytes {i} ' * 100, compressed=True) for i in range(3)]
    cache = {}
    with django_assert_num_queries(1):
        for i, row in enumerate(rows):
            encoded, _ = transcode_content(row, dictionary_cache=cache)
            assert encoded.payload == (f'large exact bytes {i} ' * 100).encode()


@pytest.mark.django_db(transaction=True)
def test_transcode_worker_gives_event_loop_opportunity(monkeypatch):
    import asyncio
    import threading
    from twicc.core.content_storage import codec

    async def scenario():
        loop = asyncio.get_running_loop()
        opportunity = asyncio.Event()
        resume = threading.Event()
        raw = b'valid raw bytes'
        row = SessionItemContent(codec='plain', payload=raw, raw_size=len(raw), sha256=hashlib.sha256(raw).digest())

        def handoff(seconds):
            assert seconds == 0
            loop.call_soon_threadsafe(opportunity.set)
            assert resume.wait(5)

        monkeypatch.setattr(codec, 'sleep', handoff)
        task = asyncio.create_task(asyncio.to_thread(codec.transcode_content, row))
        try:
            await asyncio.wait_for(opportunity.wait(), 5)
            assert not task.done()
        finally:
            resume.set()
        encoded, _ = await task
        assert encoded.payload == raw

    asyncio.run(scenario())


def test_transcode_same_dictionary_retains_verified_frame(content_row_factory):
    from twicc.core.content_storage.codec import transcode_content

    text = 'exact existing compressed frame ' * 100
    row = content_row_factory(text=text, compressed=True)
    dictionary = ContentDictionary.objects.get(pk=row.dictionary_id)
    data = zstandard.ZstdCompressionDict(bytes(dictionary.payload), dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    row.payload = zstandard.ZstdCompressor(level=1, dict_data=data, write_checksum=True).compress(text.encode())
    encoded, no_gain = transcode_content(row, row.dictionary_id)
    assert encoded.payload == bytes(row.payload)
    assert encoded.codec == 'zstd'
    assert no_gain is False
    assert decode_content(type(row)(**encoded._asdict())) == text
