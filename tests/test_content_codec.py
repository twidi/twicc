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
