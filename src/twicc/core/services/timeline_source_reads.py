"""Prepare native source groups before admission to the timeline writer.

Only reference locators survive an operation. Parsed records and loaded content
belong to one preparation and never enter the bounded process-local locator.
"""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import NamedTuple

from django.db.models import Max, Q

from twicc.core.content_storage.readers import effective_current
from twicc.core.models import Session, SessionItem
from twicc.core.services.message_sources import _canonical, _dedupe_claude_records, _read

_SESSION_LIMIT = 32
_INTERVAL_LIMIT = 16
_ALIAS_LIMIT = 8192
_RECENT_ROW_LIMIT = 128
_RECENT_BYTE_LIMIT = 8 * 1024 * 1024
_METADATA = ('id', 'current_content_id', 'kind', 'timestamp', 'message_id', 'line_num')
_active = ContextVar('timeline_source_reads', default=None)
_locators = OrderedDict()
_locator_lock = RLock()


class _Search(NamedTuple):
    watermark: int
    aliases: dict


class _SessionLocator:
    def __init__(self):
        self.intervals = OrderedDict()
        self.lock = RLock()


class _GroupBoundary(NamedTuple):
    message_id: str
    ceiling: int | None
    members: tuple


def _scope(session):
    return session.provider, session.storage_incarnation, session.history_epoch


def _signature(item):
    return tuple(getattr(item, field) for field in _METADATA)


def _request_key(refs, floor, ceiling):
    return frozenset(refs), floor, ceiling


class _SourceReads:
    def __init__(self, session, locator, watermark):
        self.session = session
        self.session_id = session.id
        self.scope = _scope(session)
        self.locator = locator
        self.watermark = watermark
        self.records = {}
        self.recent = OrderedDict()
        self.recent_bytes = 0
        self.results = {}
        self.available = []
        self.used = {}
        self.legacy = {}
        self.boundaries = []
        self.insertion_bounds = []
        self.consistent = True

    def read(self, query):
        """Reuse records already decoded during this operation."""
        pks = list(query.values_list('pk', flat=True))
        for pk in pks:
            if pk not in self.records and pk in self.recent:
                self.records[pk] = self.recent[pk][0]
                self.recent.move_to_end(pk)
        absent = set(pks).difference(self.records)
        if absent:
            self.records.update({record[0].pk: record for record in _read(
                self.session, SessionItem.objects.filter(session_id=self.session_id, pk__in=absent))})
        return sorted((self.records[pk] for pk in pks if pk in self.records), key=lambda record: record[0].line_num)

    def remember_recent(self, record):
        """Keep one bounded recent scan window for immediate group hydration."""
        item = record[0]
        previous = self.recent.pop(item.pk, None)
        if previous is not None:
            self.recent_bytes -= previous[1]
        size = getattr(item, '_current_read_size', None)
        if size is None:
            size = len(effective_current(item).encode('utf-8'))
        if size > _RECENT_BYTE_LIMIT or _RECENT_ROW_LIMIT <= 0:
            return
        while self.recent and (len(self.recent) >= _RECENT_ROW_LIMIT
                or self.recent_bytes + size > _RECENT_BYTE_LIMIT):
            _, (_, removed_size) = self.recent.popitem(last=False)
            self.recent_bytes -= removed_size
        self.recent[item.pk] = record, size
        self.recent_bytes += size

    def remember(self, metadata, legacy):
        """Reject observations that disagree during one preparation."""
        for target, observed in ((self.used, metadata), (self.legacy, legacy)):
            for pk, value in observed.items():
                if pk in target and target[pk] != value:
                    self.consistent = False
                target.setdefault(pk, value)

    def observe(self, items, *, floor, ceiling):
        """Include already loaded context or tool rows in the admission proof."""
        self.insertion_bounds.append((floor, ceiling))
        for item in items:
            legacy = {item.pk: effective_current(item)} if item.current_content_id is None else {}
            self.remember({item.pk: _signature(item)}, legacy)

    def prepare(self, requests):
        """Search each distinct interval once for all requested uncached refs."""
        found = {}
        wanted = {ref for refs, _, _ in requests for ref in refs}
        for refs, floor, ceiling in requests:
            direct = [ref for ref in refs if ref.kind == 'claude_message']
            if direct:
                query = SessionItem.objects.filter(session_id=self.session_id, line_num__gt=floor,
                    message_id__in=[ref.native_id for ref in direct])
                if ceiling is not None:
                    query = query.filter(line_num__lte=ceiling)
                records = self.read(query)
                for ref in direct:
                    found[ref, floor, ceiling] = tuple(record[0].pk for record in records if ref in record[2])
            indirect = [ref for ref in refs if ref.kind != 'claude_message']
            if not indirect:
                continue
            previous = next((key for key in reversed(self.locator)
                if key[0] <= floor and (key[1] is None or ceiling is not None and key[1] >= ceiling)), None)
            interval = previous
            if previous is None:
                previous = next((key for key in reversed(self.locator)
                    if key[0] <= floor and key[1] is not None and floor < key[1]
                    and (ceiling is None or ceiling >= key[1])), None)
                interval = (previous[0], ceiling) if previous is not None else (floor, ceiling)
            cached = self.locator.get(previous, _Search(0, {}))
            aliases = cached.aliases
            alias_count = sum(len(pks) for pks in aliases.values())
            cacheable = True
            delta = Q(pk__gt=cached.watermark)
            if previous is not None and previous != interval:
                # An advanced source ceiling exposes old PKs outside the old
                # interval too. Read that gap as well as newly inserted PKs.
                delta |= Q(line_num__gt=previous[1])
            query = SessionItem.objects.filter(delta, session_id=self.session_id,
                line_num__gt=interval[0], pk__lte=self.watermark)
            if interval[1] is not None:
                query = query.filter(line_num__lte=interval[1])
            cached_records = self.read(query.filter(pk__in=self.records))

            def observe(record):
                nonlocal alias_count, cacheable, aliases
                item, _, native_refs = record
                self.remember_recent(record)
                if wanted.intersection(native_refs):
                    self.records[item.pk] = record
                for ref in native_refs:
                    if not cacheable and ref not in wanted:
                        continue
                    locations = aliases.setdefault(ref, set())
                    if item.pk not in locations:
                        locations.add(item.pk)
                        alias_count += 1
                if cacheable and alias_count > _ALIAS_LIMIT:
                    # Evict the whole interval, including its negative proof.
                    # Keeping an advanced watermark after alias eviction loses refs.
                    aliases = {ref: pks for ref, pks in aliases.items() if ref in wanted}
                    cacheable = False

            for record in cached_records:
                observe(record)
            for record in _read(self.session, query.exclude(pk__in=self.records)):
                observe(record)
            if previous is not None and previous != interval:
                self.locator.pop(previous, None)
            if cacheable:
                self.locator[interval] = _Search(self.watermark, aliases)
                self.locator.move_to_end(interval)
            else:
                self.locator.pop(interval, None)
            for ref in indirect:
                found[ref, floor, ceiling] = tuple(aliases.get(ref, ()))
            while len(self.locator) > _INTERVAL_LIMIT or sum(
                    len(pks) for state in self.locator.values() for pks in state.aliases.values()) > _ALIAS_LIMIT:
                self.locator.popitem(last=False)
        for refs, floor, ceiling in requests:
            pks = {pk for ref in refs for pk in found.get((ref, floor, ceiling), ())}
            query = SessionItem.objects.filter(session_id=self.session_id, pk__in=pks, line_num__gt=floor)
            if ceiling is not None:
                query = query.filter(line_num__lte=ceiling)
            records = self.read(query)
            wanted = set(refs)
            records = [record for record in records if wanted.intersection(record[2])]
            missing = wanted.difference(ref for _, _, aliases in records for ref in aliases)
            if missing:
                self.insertion_bounds.append((floor, ceiling))
            grouped = {}
            for record in records:
                identity = _canonical(self.session, record)
                if identity is not None:
                    grouped.setdefault(identity, []).append(record)
            groups = []
            for identity, group in grouped.items():
                if identity.kind == 'claude_message':
                    query = SessionItem.objects.filter(session_id=self.session_id, message_id=identity.native_id)
                    # Capture proof metadata before decoding. A concurrent rewrite
                    # must not pair old parsed records with a newer content FK.
                    hydration_metadata = {row[0]: row for row in query.values_list(*_METADATA)}
                    legacy_pks = [pk for pk, row in hydration_metadata.items() if row[1] is None]
                    hydration_legacy = dict(SessionItem.objects.filter(pk__in=legacy_pks).values_list('pk', 'content'))
                    hydrated = self.read(query)
                    group = _dedupe_claude_records(hydrated)
                else:
                    group = group[:1]
                terminals = [item.line_num for item, _, aliases in group
                    if any(ref in wanted and ref.kind == 'claude_uuid' for ref in aliases)]
                upper = min(ceiling, max(terminals)) if ceiling is not None and terminals else (
                    max(terminals) if terminals else ceiling)
                if upper is not None:
                    group = [record for record in group if record[0].line_num <= upper]
                if identity.kind == 'claude_message':
                    metadata = {pk: row for pk, row in hydration_metadata.items()
                        if upper is None or row[5] <= upper}
                    self.remember(metadata, {pk: text for pk, text in hydration_legacy.items() if pk in metadata})
                    self.boundaries.append(_GroupBoundary(identity.native_id, upper, tuple(sorted(metadata))))
                if group and group[0][0].line_num > floor:
                    groups.append(group)
                    self.available.append((floor, ceiling, upper, group))
                    # A late duplicate exact identity can enter another group.
                    # Bound this metadata-only check at the frozen terminal.
                    self.insertion_bounds.append((floor, upper if upper is not None else group[-1][0].line_num))
                    for item, _, _ in group:
                        legacy = {item.pk: effective_current(item)} if item.current_content_id is None else {}
                        self.remember({item.pk: _signature(item)}, legacy)
            self.results[_request_key(refs, floor, ceiling)] = sorted(groups, key=lambda group: group[0][0].line_num)

    def resolve(self, refs, *, floor=0, ceiling=None):
        """Return a prepared request without source reads or fallback scans."""
        key = _request_key(refs, floor, ceiling)
        if key in self.results:
            return self.results[key]
        wanted = set(refs)
        selected = {}
        for source_floor, source_ceiling, frozen_upper, group in self.available:
            if floor < source_floor:
                continue
            if source_ceiling is not None and (ceiling is None or ceiling > source_ceiling):
                continue
            if not any(wanted.intersection(aliases) and item.line_num > floor
                    and (ceiling is None or item.line_num <= ceiling) for item, _, aliases in group):
                continue
            terminals = [item.line_num for item, _, aliases in group
                if any(ref in wanted and ref.kind == 'claude_uuid' for ref in aliases)]
            upper = min(ceiling, max(terminals)) if ceiling is not None and terminals else (
                max(terminals) if terminals else ceiling)
            identity = _canonical(self.session, group[0])
            if identity.kind == 'claude_message' and frozen_upper is not None:
                if upper is None or upper > frozen_upper:
                    continue
            bounded = [record for record in group if upper is None or record[0].line_num <= upper]
            if bounded and bounded[0][0].line_num > floor:
                previous = selected.get(identity)
                if previous is None or len(bounded) > len(previous):
                    selected[identity] = bounded
        return sorted(selected.values(), key=lambda group: group[0][0].line_num)

    def is_current(self):
        """Validate source scope and relevant metadata without loading content."""
        if not self.consistent:
            return False
        current = Session.objects.filter(pk=self.session_id).values_list(
            'provider', 'storage_incarnation', 'history_epoch').first()
        if current != self.scope:
            return False
        actual = {row[0]: row for row in SessionItem.objects.filter(
            session_id=self.session_id, pk__in=self.used).values_list(*_METADATA)}
        if actual != self.used:
            return False
        if self.legacy and dict(SessionItem.objects.filter(session_id=self.session_id,
                pk__in=self.legacy).values_list('pk', 'content')) != self.legacy:
            return False
        for message_id, ceiling, members in self.boundaries:
            query = SessionItem.objects.filter(session_id=self.session_id, message_id=message_id)
            if ceiling is not None:
                query = query.filter(line_num__lte=ceiling)
            if tuple(query.order_by('pk').values_list('pk', flat=True)) != members:
                return False
        for floor, ceiling in set(self.insertion_bounds):
            query = SessionItem.objects.filter(session_id=self.session_id,
                pk__gt=self.watermark, line_num__gt=floor)
            if ceiling is not None:
                query = query.filter(line_num__lte=ceiling)
            if query.exists():
                return False
        return True


def prepare_source_reads(session_id, requests):
    """Read requested native groups outside the writer and freeze their proofs."""
    session = Session.objects.only('id', 'provider', 'storage_incarnation', 'history_epoch').get(pk=session_id)
    requests = [(tuple(refs), floor, ceiling) for refs, floor, ceiling in requests]
    with _locator_lock:
        key = session.id, _scope(session)
        locator = _locators.get(key)
        if locator is None:
            # A changed scope cannot reuse any old negative lookup.
            for old in list(_locators):
                if old[0] == session.id:
                    del _locators[old]
            locator = _locators[key] = _SessionLocator()
        _locators.move_to_end(key)
        while len(_locators) > _SESSION_LIMIT:
            _locators.popitem(last=False)
    with locator.lock:
        watermark = SessionItem.objects.filter(session_id=session.id).aggregate(value=Max('pk'))['value'] or 0
        prepared = _SourceReads(session, locator.intervals, watermark)
        prepared.prepare(requests)
        # Locator objects remain process-local. Snapshots carry only their data.
        prepared.locator = None
        prepared.records.clear()
        prepared.recent.clear()
        prepared.recent_bytes = 0
        return prepared


def resolve_source_groups(session, refs, *, floor=0, ceiling=None):
    """Resolve one request through the shared locator, outside writer admission."""
    return prepare_source_reads(session.id, [(refs, floor, ceiling)]).resolve(refs, floor=floor, ceiling=ceiling)


@contextmanager
def prepared_source_reads(prepared):
    """Install one operation's prepared groups in the current execution context."""
    token = _active.set(prepared)
    try:
        yield prepared
    finally:
        _active.reset(token)


def current_source_reads(session_id):
    """Select a snapshot only for its own session."""
    prepared = _active.get()
    return prepared if prepared is not None and prepared.session_id == session_id else None
