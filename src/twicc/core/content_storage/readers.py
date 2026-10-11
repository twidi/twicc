"""Explicit, bounded reads of effective current content.

The accessor never performs SQL. Producers must use the mutation helpers when
changing an override or reference, and invalidate any separately parsed state.
Plaintext expressions require the caller's ready-hot storage predicate.
"""

import re
from collections.abc import Iterator, Sequence

from asgiref.sync import sync_to_async
from django.db.models import BinaryField, Case, Expression, F, IntegerField, QuerySet, TextField, Value, When
from django.db.models.functions import Cast, Coalesce, Length

from twicc.core.content_storage.codec import ContentIntegrityError, decode_content
from twicc.core.content_storage.types import UNLOADED
from twicc.core.models import SessionItem, SessionItemContent


DEFAULT_ROW_BUDGET = 256
DEFAULT_BYTE_BUDGET = 8 * 1024 * 1024
_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


class ContentNotLoadedError(RuntimeError):
    """The caller must explicitly load the effective current value."""


def invalidate_current(item: SessionItem) -> None:
    """Discard decoded and parsed values after a producer changes content."""
    for name in ('_loaded_current', '_loaded_current_id', '_parsed_content'):
        item.__dict__.pop(name, None)


def set_current_reference(item: SessionItem, content_id: int | None) -> None:
    """Set an unsaved reference and discard the previous override and caches."""
    invalidate_current(item)
    item.__dict__.pop('_current_override', None)
    item.current_content_id = content_id


def set_current_override(item: SessionItem, text: str) -> None:
    """Set an explicit unsaved string, including an empty string."""
    if not isinstance(text, str):
        raise TypeError('Current content override must be a string')
    invalidate_current(item)
    item._current_override = text


def clear_current_override(item: SessionItem) -> None:
    """Return to persisted content; require an explicit reload for FK content."""
    invalidate_current(item)
    item.__dict__.pop('_current_override', None)


def effective_current(item: SessionItem) -> str:
    """Return loaded current text or an override without any implicit SQL."""
    state = item.__dict__
    override = state.get('_current_override', UNLOADED)
    if override is not UNLOADED:
        return override
    current_id = state.get('current_content_id', UNLOADED)
    if current_id is None:
        legacy = state.get('content', UNLOADED)
        if legacy is not UNLOADED:
            return legacy
    elif current_id is not UNLOADED and state.get('_loaded_current_id', UNLOADED) == current_id:
        loaded = state.get('_loaded_current', UNLOADED)
        if loaded is not UNLOADED:
            return loaded
    raise ContentNotLoadedError('Effective current content is not explicitly loaded')


def _budgets(row_budget: int, byte_budget: int) -> None:
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Content read budgets must be positive')


def load_current(items: Sequence[SessionItem]) -> None:
    """Decode each distinct requested PK once in bounded payload batches.

    Decoded strings live on the requested items only, with an operation-local
    deduplication map. One oversized row may occupy a batch on its own.
    """
    targets = {}
    deferred = []
    for item in items:
        state = item.__dict__
        if state.get('_current_override', UNLOADED) is not UNLOADED:
            continue
        if 'current_content_id' not in state:
            deferred.append(item)
        elif item.current_content_id is None:
            if 'content' not in state:
                deferred.append(item)
        else:
            targets.setdefault(item.current_content_id, []).append(item)
    if deferred:
        by_pk = {}
        for item in deferred:
            if item.pk is None:
                raise ContentNotLoadedError('Unsaved item has deferred content fields')
            by_pk.setdefault(item.pk, []).append(item)
        rows = SessionItem.objects.filter(pk__in=by_pk).only('id', 'current_content_id')
        found, legacy = set(), {}
        for row in rows.iterator(chunk_size=1):
            found.add(row.pk)
            for item in by_pk[row.pk]:
                # An explicitly assigned reference always wins over persisted state.
                item.__dict__.setdefault('current_content_id', row.current_content_id)
                if item.current_content_id is None:
                    legacy.setdefault(row.pk, []).append(item)
                else:
                    targets.setdefault(item.current_content_id, []).append(item)
        if found != by_pk.keys():
            raise ContentIntegrityError('Missing deferred session item')
        if legacy:
            rows = SessionItem.objects.filter(pk__in=legacy).values_list('id', 'content')
            found = set()
            for pk, text in rows.iterator(chunk_size=1):
                found.add(pk)
                for item in legacy[pk]:
                    item.__dict__.setdefault('content', text)
            if found != legacy.keys():
                raise ContentIntegrityError('Missing legacy session item')
    if not targets:
        return

    def attach(ids):
        rows = SessionItemContent.objects.filter(pk__in=ids).only(
            'id', 'payload', 'codec', 'dictionary_id', 'raw_size', 'sha256',
        )
        found = set()
        for row in rows.iterator(chunk_size=1):
            text = decode_content(row)
            found.add(row.pk)
            for item in targets[row.pk]:
                item._loaded_current = text
                item._loaded_current_id = row.pk
        if found != set(ids):
            raise ContentIntegrityError('Missing current content row')

    metadata = SessionItemContent.objects.filter(pk__in=targets).annotate(
        stored_size=Length('payload'),
    ).values_list('id', 'raw_size', 'stored_size').order_by('id')
    batch, size, found = [], 0, set()
    for pk, raw_size, stored_size in metadata.iterator(chunk_size=DEFAULT_ROW_BUDGET):
        cost = raw_size + stored_size
        if batch and (len(batch) >= DEFAULT_ROW_BUDGET or size + cost > DEFAULT_BYTE_BUDGET):
            attach(batch)
            batch, size = [], 0
        batch.append(pk)
        size += cost
        found.add(pk)
    if batch:
        attach(batch)
    if found != targets.keys():
        raise ContentIntegrityError('Missing current content row')


async def aload_current(items: Sequence[SessionItem]) -> None:
    """Use the existing synchronous reader boundary, outside the writer queue."""
    await sync_to_async(load_current)(items)


def effective_plaintext_expression() -> Expression:
    """Select current FK text or legacy text for explicitly ready-hot queries."""
    return Case(
        When(current_content_id__isnull=False, then=Cast('current_content__payload', TextField())),
        default=F('content'), output_field=TextField(),
    )


def iter_current_items(queryset: QuerySet, row_budget: int, byte_budget: int) -> Iterator[SessionItem]:
    """Read metadata first; then load pages within row and logical-byte budgets."""
    _budgets(row_budget, byte_budget)
    logical_size = Case(
        When(current_content_id__isnull=False, then=F('current_content__raw_size')),
        default=Length(Cast('content', BinaryField())), output_field=IntegerField(),
    )
    candidates = queryset.defer('content').annotate(_current_read_size=Coalesce(logical_size, Value(0)))
    page, size = [], 0
    for item in candidates.iterator(chunk_size=1):
        cost = item._current_read_size
        if page and (len(page) >= row_budget or size + cost > byte_budget):
            load_current(page)
            yield from page
            page, size = [], 0
        page.append(item)
        size += cost
    if page:
        load_current(page)
        yield from page


def _literal_like_pattern(term: str) -> re.Pattern:
    # Django SQLite icontains surrounds an escaped literal with percent signs.
    # SQLite's LIKE implementation terminates BOTH operands at the first NUL.
    pattern = ('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%').split('\x00', 1)[0]
    tokens, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if char == '\\':
            index += 1
            if index == len(pattern):
                return re.compile(r'(?!)')
            tokens.append(re.escape(pattern[index]))
        elif char == '%':
            tokens.append('.*')
        elif char == '_':
            tokens.append('.')
        else:
            tokens.append(re.escape(char))
        index += 1
    return re.compile(''.join(tokens).translate(_ASCII_LOWER), re.DOTALL)


def iter_matching_items(
    queryset: QuerySet, terms: Sequence[str], limit: int | None = None,
) -> Iterator[SessionItem]:
    """Yield ordered decoded candidates matching every SQLite icontains term."""
    if limit is not None and limit < 0:
        raise ValueError('Content match limit must not be negative')
    if limit == 0:
        return
    patterns = [_literal_like_pattern(term) for term in terms]
    matched = 0
    for item in iter_current_items(queryset, DEFAULT_ROW_BUDGET, DEFAULT_BYTE_BUDGET):
        text = effective_current(item).split('\x00', 1)[0].translate(_ASCII_LOWER)
        if all(pattern.fullmatch(text) is not None for pattern in patterns):
            yield item
            matched += 1
            if limit is not None and matched >= limit:
                return
