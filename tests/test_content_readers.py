"""Explicit readers retain strings and SQLite substring behavior."""
import asyncio
from unittest.mock import patch

import pytest
from django.db.models import Q

from twicc.core.content_storage import readers
from twicc.core.content_storage.codec import ContentIntegrityError
from twicc.core.models import ContentDictionary, Project, Session, SessionItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    project = Project.objects.create(id='readers', directory='/readers')
    return Session.objects.create(id='reader', project=project, file_path='reader', provider='codex')


def make_item(owner, line, text='stale', current=None):
    return SessionItem.objects.create(session=owner, line_num=line, content=text, current_content=current)


def test_mixed_loaded_empty_unloaded_and_missing(owner, content_row_factory, django_assert_num_queries):
    legacy = make_item(owner, 1, 'legacy é')
    plain = make_item(owner, 2, current=content_row_factory('plain', False))
    cold = make_item(owner, 3, current=content_row_factory('zstd' * 100, True))
    empty = make_item(owner, 4, current=content_row_factory('', False))
    with django_assert_num_queries(0):
        assert readers.effective_current(legacy) == 'legacy é'
        with pytest.raises(readers.ContentNotLoadedError):
            readers.effective_current(plain)
    readers.load_current([legacy, plain, cold, empty])
    with django_assert_num_queries(0):
        assert [readers.effective_current(i) for i in [legacy, plain, cold, empty]] == [
            'legacy é', 'plain', 'zstd' * 100, '',
        ]
    broken = SessionItem(content='stale', current_content_id=99999999)
    with pytest.raises(ContentIntegrityError):
        readers.load_current([broken])
    with django_assert_num_queries(0), pytest.raises(readers.ContentNotLoadedError):
        readers.effective_current(broken)


def test_distinct_pk_decoded_once(owner, content_row_factory):
    text = 'large é' * 20000
    stored = content_row_factory(text, True)
    page = [make_item(owner, n, current=stored) for n in range(1, 8)]
    with patch.object(readers, 'decode_content', wraps=readers.decode_content) as spy:
        readers.load_current(page)
    assert spy.call_count == 1
    assert all(readers.effective_current(i) == text for i in page)


def test_mutations_invalidate_loaded_and_parsed(owner, content_row_factory, django_assert_num_queries):
    first = content_row_factory('first', False)
    second = content_row_factory('second', False)
    row = make_item(owner, 1, current=first)
    readers.load_current([row])
    row._parsed_content = {'old': True}
    readers.set_current_override(row, '')
    with django_assert_num_queries(0):
        assert readers.effective_current(row) == ''
    assert not hasattr(row, '_parsed_content')
    readers.clear_current_override(row)
    with pytest.raises(readers.ContentNotLoadedError):
        readers.effective_current(row)
    readers.load_current([row])
    readers.set_current_reference(row, second.id)
    with pytest.raises(readers.ContentNotLoadedError):
        readers.effective_current(row)
    readers.load_current([row])
    assert readers.effective_current(row) == 'second'
    row.current_content_id = first.id
    with django_assert_num_queries(0), pytest.raises(readers.ContentNotLoadedError):
        readers.effective_current(row)


@pytest.mark.django_db(transaction=True)
def test_async_boundary_loads_before_serialization(owner, content_row_factory):
    # Transaction test teardown flushes migration data before later tests.
    ContentDictionary.objects.all().delete()
    row = make_item(owner, 1, current=content_row_factory('async é', True))

    async def run():
        await readers.aload_current([row])
        assert readers.effective_current(row) == 'async é'
    asyncio.run(run())


def test_iteration_bounds_payload_pages_and_keeps_order(owner, content_row_factory):
    for n, text in enumerate(['aa', 'bb', 'oversized' * 100, 'cc', 'dd'], 1):
        make_item(owner, n, current=content_row_factory(text, n == 3))
    real = readers.load_current
    pages = []

    def record(page):
        pages.append([i.line_num for i in page])
        return real(page)

    with patch.object(readers, 'load_current', side_effect=record):
        rows = list(readers.iter_current_items(SessionItem.objects.order_by('line_num'), 2, 4))
    assert pages == [[1, 2], [3], [4, 5]]
    assert [readers.effective_current(i) for i in rows] == ['aa', 'bb', 'oversized' * 100, 'cc', 'dd']


@pytest.mark.parametrize('terms', [[], [''], ['alpha'], ['É'], ['é'], ['_'], ['%'], ['\\'],
                                   ['before'], ['after'], ['before\x00after'], ['\x00'], ['alpha', '_']])
def test_decoded_prefilter_matches_sqlite_text_semantics(owner, content_row_factory, terms):
    texts = ['ALPHA_', 'alpha%', 'é', 'É', 'slash\\end', 'before\x00after', 'after', '', 'before']
    for n, text in enumerate(texts, 1):
        make_item(owner, n, text)
        make_item(owner, n + len(texts), '', content_row_factory(text, n % 2 == 0))
    legacy = SessionItem.objects.filter(current_content__isnull=True).order_by('line_num')
    for term in terms:
        legacy = legacy.filter(content__icontains=term)
    expected = list(legacy.values_list('line_num', flat=True))
    got = [i.line_num for i in readers.iter_matching_items(SessionItem.objects.order_by('line_num'), terms)]
    assert got == expected + [n + len(texts) for n in expected]
    assert [i.line_num for i in readers.iter_matching_items(SessionItem.objects.order_by('line_num'), terms, 1)] == got[:1]


def test_plaintext_projection_casts_blob_and_ignores_stale_legacy(owner, content_row_factory):
    make_item(owner, 1, 'legacy é')
    make_item(owner, 2, 'stale', content_row_factory('plain É', False))
    make_item(owner, 3, 'stale', content_row_factory('', False))
    qs = SessionItem.objects.annotate(effective=readers.effective_plaintext_expression()).order_by('line_num')
    assert list(qs.values_list('effective', flat=True)) == ['legacy é', 'plain É', '']
    assert list(qs.filter(Q(effective__icontains='é')).values_list('line_num', flat=True)) == [1]
    assert not qs.filter(effective__icontains='stale').exists()


def test_deferred_legacy_explicit_load_and_metadata_no_payload(owner, django_assert_num_queries):
    make_item(owner, 1, 'deferred')
    with django_assert_num_queries(1) as captured:
        row = SessionItem.objects.only('id', 'line_num', 'current_content_id').get()
    assert 'payload' not in captured.captured_queries[0]['sql']
    with django_assert_num_queries(0), pytest.raises(readers.ContentNotLoadedError):
        readers.effective_current(row)
    readers.load_current([row])
    with django_assert_num_queries(0):
        assert readers.effective_current(row) == 'deferred'


def test_deferred_fk_never_reads_stale_legacy(owner, content_row_factory, django_assert_num_queries):
    stored = content_row_factory('current', False)
    make_item(owner, 1, 'stale' * 10000, stored)
    row = SessionItem.objects.only('id', 'line_num').get()
    with django_assert_num_queries(3) as captured:
        readers.load_current([row])
    assert all('"core_sessionitem"."content"' not in q['sql'] for q in captured.captured_queries)
    assert readers.effective_current(row) == 'current'


@pytest.mark.parametrize('rows, size', [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_iteration_rejects_invalid_budgets(rows, size):
    with pytest.raises(ValueError):
        list(readers.iter_current_items(SessionItem.objects.all(), rows, size))


def test_iteration_legacy_utf8_budget_and_empty_rows(owner):
    for n, text in enumerate(['é', 'é', '', 'tail'], 1):
        make_item(owner, n, text)
    real = readers.load_current
    pages = []

    def record(page):
        pages.append([i.line_num for i in page])
        return real(page)

    with patch.object(readers, 'load_current', side_effect=record):
        rows = list(readers.iter_current_items(SessionItem.objects.order_by('line_num'), 3, 3))
    assert pages == [[1], [2, 3], [4]]
    assert [readers.effective_current(i) for i in rows] == ['é', 'é', '', 'tail']


def test_override_skips_missing_reference_and_cleared_legacy(owner):
    row = SessionItem(content='', current_content_id=99999999)
    readers.set_current_override(row, 'unsaved')
    readers.load_current([row])
    assert readers.effective_current(row) == 'unsaved'
    readers.set_current_reference(row, None)
    assert readers.effective_current(row) == ''


def test_matching_limits_zero_and_negative(owner):
    make_item(owner, 1, 'content')
    assert list(readers.iter_matching_items(SessionItem.objects.all(), [], 0)) == []
    with pytest.raises(ValueError):
        list(readers.iter_matching_items(SessionItem.objects.all(), [], -1))


@pytest.mark.parametrize('row_budget,byte_budget,expected', [(2, 1000, [2, 2, 1]), (10, 8, [2, 1, 2])])
def test_direct_loader_bounds_payload_batches(owner, content_row_factory, monkeypatch, row_budget, byte_budget, expected):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    monkeypatch.setattr(readers, 'DEFAULT_ROW_BUDGET', row_budget)
    monkeypatch.setattr(readers, 'DEFAULT_BYTE_BUDGET', byte_budget)
    texts = ['aa', 'bb', 'oversized' * 100, 'cc', 'dd'] if byte_budget == 8 else ['aa', 'bb', 'ee', 'cc', 'dd']
    page = [make_item(owner, n, current=content_row_factory(text, False)) for n, text in enumerate(texts, 1)]
    with CaptureQueriesContext(connection) as queries:
        readers.load_current(page)
    payload_queries = [q['sql'] for q in queries if '"core_sessionitemcontent"."payload"' in q['sql']
                       and q['sql'].startswith('SELECT') and 'LENGTH(' not in q['sql']]
    assert [query.split(' IN (')[1].split(')')[0].count(',') + 1 for query in payload_queries] == expected
    assert [readers.effective_current(item) for item in page] == texts
