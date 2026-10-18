"""Combined-release activation keeps storage and metadata readiness independent."""

import hashlib

import pytest
from django.conf import settings

from tests.test_content_extraction import make_session, message
from tests.content_storage_helpers import _public_dictionary, read_current
from twicc.core.content_storage.codec import decode_content, encode_cold
from twicc.core.content_storage.state import extract_storage_slice
from twicc.core.models import ContentDictionary, SessionItem, SessionItemContent
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def release_targets(settings):
    from twicc import settings as production

    settings.CLAUDE_CODE_COMPUTE_VERSION = production.CLAUDE_CODE_COMPUTE_VERSION
    settings.CODEX_COMPUTE_VERSION = production.CODEX_COMPUTE_VERSION


def test_combined_release_targets():
    assert settings.CLAUDE_CODE_COMPUTE_VERSION == 117
    assert settings.CODEX_COMPUTE_VERSION == 58


@pytest.mark.parametrize('provider,old,target', [('claude_code', 114, 115), ('codex', 55, 56)])
def test_inline_upgrade_goes_directly_to_combined_release(provider, old, target):
    session = make_session(compute_version=old)
    session.provider = provider
    session.save(update_fields=['provider'])
    item = SessionItem.objects.create(session=session, line_num=1, content='exact é')
    result = message(session, session_fields={'compute_version': target})
    assert BaseSessionCompute.apply_session_complete(result).outcome == 'applied'
    session.refresh_from_db()
    item.refresh_from_db()
    assert session.content_storage_version == 1
    assert session.compute_version == target
    assert item.content == ''
    assert read_current(item) == 'exact é'


def test_partial_conversion_restarts_before_publishing_compute_readiness():
    session = make_session(compute_version=55)
    for line in range(1, 4):
        SessionItem.objects.create(session=session, line_num=line, content=f'exact {line}')
    cursor = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, -1, 1, 1024)
    assert not cursor.done
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.compute_version != settings.CODEX_COMPUTE_VERSION
    assert BaseSessionCompute.apply_session_complete(
        message(session, session_fields={'compute_version': settings.CODEX_COMPUTE_VERSION})
    ).outcome == 'applied'
    session.refresh_from_db()
    assert session.content_storage_version == 1
    assert session.compute_version == settings.CODEX_COMPUTE_VERSION
    assert [read_current(item) for item in SessionItem.objects.filter(session=session).order_by('line_num')] == [
        'exact 1', 'exact 2', 'exact 3',
    ]


def test_storage_ready_session_still_requires_metadata_compute():
    from twicc.providers.history_facts import history_facts_are_current

    session = make_session(content_storage_version=1, compute_version=55)
    assert not history_facts_are_current(session)


def test_old_dictionary_remains_decodable_after_new_version_ships():
    text = '{"type":"assistant","message":{"role":"assistant"}}' * 100
    old = _public_dictionary('codex')
    encoded, _ = encode_cold(text, old.id, 'test')
    assert encoded.codec == 'zstd'
    row = SessionItemContent.objects.create(
        sha256=encoded.sha256, payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id,
        raw_size=encoded.raw_size, dictionary_provider='codex', session_ids=[],
    )
    new_bytes = b'new public dictionary'
    ContentDictionary.objects.create(provider='codex', version=2, format='raw',
                                     payload=new_bytes, sha256=hashlib.sha256(new_bytes).digest())
    assert row.dictionary_id == old.id
    assert decode_content(row) == text
