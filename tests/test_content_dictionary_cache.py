"""Dictionary reuse must remove SQL without changing exact logical content."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from tests.content_storage_helpers import _public_dictionary
from twicc.core.content_storage import codec
from twicc.core.content_storage.policy import compression_policy_version
from twicc.core.models import ContentDictionary, SessionItemContent


def registry():
    try:
        return import_module('twicc.core.content_storage.dictionaries')
    except ModuleNotFoundError:
        return None


def preload():
    if hasattr(registry(), 'preload_dictionaries'):
        registry().preload_dictionaries()


@pytest.mark.django_db(transaction=True)
def test_dictionary_loads_and_validates_once(monkeypatch):
    dictionary = _public_dictionary('codex')
    validations = []
    original = codec._validate_dictionary

    def validate(row):
        validations.append(row.id)
        return original(row)

    monkeypatch.setattr(codec, '_validate_dictionary', validate)
    preload()
    assert validations.count(dictionary.id) == 1
    validations.clear()
    preload()
    texts = ['exact unicode é content ' * 100, 'another content ' * 100]
    decoded = []
    with CaptureQueriesContext(connection) as queries:
        for text in texts:
            encoded, _ = codec.encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
            row = SessionItemContent(**encoded._asdict())
            decoded.append(codec.decode_content(row))
    selects = [q for q in queries if 'SELECT' in q['sql'] and 'core_contentdictionary' in q['sql']]
    assert len(selects) == 0
    assert len(validations) == 0
    assert decoded == texts


@pytest.mark.django_db(transaction=True)
def test_parallel_decoders_share_only_immutable_dictionary(content_row_factory):
    rows = [content_row_factory(text=f'parallel exact content {i} ' * 100, compressed=True) for i in range(12)]
    preload()
    with ThreadPoolExecutor(max_workers=4) as executor:
        decoded = list(executor.map(codec.decode_content, rows))
    assert decoded == [f'parallel exact content {i} ' * 100 for i in range(12)]


@pytest.mark.django_db(transaction=True)
def test_missing_or_invalid_dictionary_fails():
    with pytest.raises(codec.ContentIntegrityError):
        codec.encode_cold('text', 999999, 'policy')
    row = ContentDictionary.objects.create(provider='invalid', version=1, format='raw', payload=b'bad', sha256=b'x' * 32)
    with pytest.raises(codec.ContentIntegrityError):
        codec.encode_cold('text', row.id, 'policy')


@pytest.mark.django_db(transaction=True)
def test_rolled_back_dictionary_is_not_published():
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            row = ContentDictionary.objects.create(provider='rollback', version=1, format='raw', payload=b'abc',
                                                   sha256=hashlib.sha256(b'abc').digest())
            codec.encode_cold('abc' * 100, row.id, 'policy')
            raise RuntimeError('rollback')
    with pytest.raises(codec.ContentIntegrityError):
        codec.encode_cold('abc' * 100, row.id, 'policy')


@pytest.mark.django_db(transaction=True)
def test_same_pk_in_another_database_is_isolated(tmp_path):
    import hashlib
    from django.db import connections

    dictionary = _public_dictionary('codex')
    first_policy = compression_policy_version('codex', dictionary.id)
    preload()
    original = connections['default']
    alternate = original.copy(alias='default')
    alternate.settings_dict['NAME'] = str(tmp_path / 'other.sqlite3')
    connections['default'] = alternate
    try:
        with alternate.schema_editor() as editor:
            editor.create_model(ContentDictionary)
        payload = b'other database dictionary'
        ContentDictionary.objects.create(pk=dictionary.id, provider='codex', version=1, format='raw',
                                         payload=payload, sha256=hashlib.sha256(payload).digest())
        second_policy = compression_policy_version('codex', dictionary.id)
        assert second_policy != first_policy
    finally:
        alternate.close()
        connections['default'] = original
    assert compression_policy_version('codex', dictionary.id) == first_policy


@pytest.mark.django_db(transaction=True)
def test_maintenance_preparation_and_publication_reuse_preloaded_dictionary(content_row_factory):
    from datetime import timedelta
    from django.utils import timezone
    from tests.test_content_root_state import session
    from twicc.content_maintenance_task import prepare_representation, publish_representation

    from twicc.providers.helpers import get_provider_helpers

    root = session('dictionary-maintenance', archived=True, created_at=timezone.now() - timedelta(days=31),
                   content_storage_version=1, storage_incarnation='maintenance', content_state_epoch=0,
                   compute_version=get_provider_helpers('codex').current_compute_version)
    rows = [content_row_factory(f'cold exact content {i} ' * 100, False, session_ids=[root.id]) for i in range(3)]
    dictionary = _public_dictionary('codex')
    preload()
    policy = compression_policy_version('codex', dictionary.id)
    with CaptureQueriesContext(connection) as queries:
        candidates = [prepare_representation(row.id, policy) for row in rows]
        with transaction.atomic():
            published = [publish_representation(candidate, frozenset()) for candidate in candidates]
    selects = [q for q in queries if 'SELECT' in q['sql'] and 'core_contentdictionary' in q['sql']]
    assert len(selects) == 0
    assert published == [True, True, True]
    for i, row in enumerate(rows):
        row.refresh_from_db()
        assert codec.decode_content(row) == f'cold exact content {i} ' * 100


@pytest.mark.django_db(transaction=True)
def test_writer_startup_reports_invalid_dictionary_and_shutdown_completes():
    import asyncio
    from twicc.providers import db_writer

    ContentDictionary.objects.create(provider='invalid-startup', version=1, format='raw',
                                     payload=b'bad', sha256=b'x' * 32)

    async def scenario():
        db_writer.start_db_writer()
        marker = asyncio.get_running_loop().create_future()
        db_writer.get_thread_queue().put(db_writer.InitialSyncDoneMarker('codex', marker))
        _, completion = db_writer.arm_compute_completion('codex', set(), 0)
        try:
            with pytest.raises(codec.ContentIntegrityError):
                await asyncio.wait_for(db_writer.wait_db_writer_ready(), 2)
            for future in (marker, completion):
                with pytest.raises(codec.ContentIntegrityError):
                    await asyncio.wait_for(asyncio.shield(future), 2)
        finally:
            await asyncio.wait_for(db_writer.stop_db_writer(), 2)
        assert db_writer._db_writer_task is None
        assert db_writer._write_coordinator is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_writer_readiness_preloads_before_producer_decode(content_row_factory):
    import asyncio
    from twicc.providers import db_writer

    row = content_row_factory('startup exact unicode é ' * 100, True)

    def produce():
        with CaptureQueriesContext(connection) as queries:
            decoded = codec.decode_content(row)
        assert len(queries) == 0
        return decoded

    async def scenario():
        db_writer.start_db_writer()
        try:
            await asyncio.wait_for(db_writer.wait_db_writer_ready(), 2)
            assert await db_writer.run_compute_sync(produce) == 'startup exact unicode é ' * 100
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(scenario())
