from unittest.mock import patch

import pytest
from django.db import transaction

from twicc.core.content_storage import codec, references
from twicc.core.content_storage.codec import ContentIntegrityError, decode_content
from twicc.core.models import SessionItemContent

pytestmark = pytest.mark.django_db


def test_existing_hash_skips_compression_and_decode(content_row_factory):
    text = 'existing é\n' * 100
    row = content_row_factory(text, True)
    with patch.object(codec, 'decode_content', wraps=codec.decode_content) as decoder, transaction.atomic():
        ids = references.intern_contents([text], 'codex')
    assert ids[text] == row.id
    assert decoder.call_count == 0


def test_prepare_and_publish_hash_each_unique_string_once(content_row_factory):
    old = 'existing é\n' * 100
    new = 'missing é\n' * 100
    row = content_row_factory(old, True)
    with patch.object(references, 'encode_plain', wraps=references.encode_plain) as digest:
        prepared = references.prepare_intern_contents([old, new, new], 'codex', cold=True)
        with transaction.atomic(), references.prepared_content_guard(lambda: True):
            ids = references.intern_contents([old, new, new], 'codex', prepared)
    assert digest.call_count == 2
    assert ids[old] == row.id
    assert len(prepared) == 2
    assert decode_content(SessionItemContent.objects.get(pk=ids[new])) == new


def test_row_inserted_between_lookup_and_publication_is_reused():
    text = 'race'
    prepared = references.prepare_intern_contents([text], 'codex', cold=False)
    with transaction.atomic():
        first = references.intern_contents([text], 'codex')
        second = references.intern_contents([text], 'claude', prepared)
    assert first == second
    assert SessionItemContent.objects.count() == 1


def test_prepared_metadata_mismatch_fails():
    prepared = references.prepare_intern_contents(['exact'], 'codex', cold=False)
    digest, record = next(iter(prepared.items()))
    prepared[digest] = record._replace(raw_size=100)
    with transaction.atomic(), pytest.raises(ContentIntegrityError):
        references.intern_contents(['exact'], 'codex', prepared)


def test_known_batch_digest_conflict_fails(monkeypatch):
    real = references.encode_plain
    digest = real('one').sha256
    monkeypatch.setattr(references, 'encode_plain', lambda text: real(text)._replace(sha256=digest))
    with transaction.atomic(), pytest.raises(ContentIntegrityError):
        references.intern_contents(['one', 'two'], 'codex')


def test_new_cold_content_compresses_once_without_validation_decode():
    text = 'compress exact é\n' * 1000
    with (patch.object(references, 'encode_cold', wraps=references.encode_cold) as compressor,
          patch.object(codec, 'encode_plain', wraps=codec.encode_plain) as extra_digest,
          patch.object(codec, 'decode_content', wraps=codec.decode_content) as decoder):
        prepared = references.prepare_intern_contents([text, text], 'codex', cold=True)
        with transaction.atomic(), references.prepared_content_guard(lambda: True):
            ids = references.intern_contents([text], 'codex', prepared)
    assert compressor.call_count == 1
    assert extra_digest.call_count == 0
    assert decoder.call_count == 0
    row = SessionItemContent.objects.get(pk=ids[text])
    assert row.codec == 'zstd'
    assert decode_content(row) == text


def test_no_gain_metadata_survives_initial_insert():
    text = 'x'
    prepared = references.prepare_intern_contents([text], 'codex', cold=True)
    with transaction.atomic(), references.prepared_content_guard(lambda: True):
        ids = references.intern_contents([text], 'codex', prepared, initial_owners=['owner'])
    row = SessionItemContent.objects.get(pk=ids[text])
    assert row.codec == 'plain'
    assert row.compression_no_gain is True
    assert row.compression_attempt_version == next(iter(prepared.values())).policy_version
    assert row.compression_attempt_version is not None
    assert row.session_ids == ['owner']
    assert decode_content(row) == 'x'


def test_existing_prepared_record_retains_identity_without_payload(content_row_factory):
    text = 'already stored' * 100
    row = content_row_factory(text, True)
    with patch.object(references, 'encode_cold', wraps=references.encode_cold) as compressor:
        prepared = references.prepare_intern_contents([text], 'codex', cold=True)
    record = next(iter(prepared.values()))
    assert record.existing_id == row.id
    assert record.encoded is None
    assert compressor.call_count == 0
