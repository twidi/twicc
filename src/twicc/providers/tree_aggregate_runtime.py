"""One bounded aggregate continuation for source completion and journal recovery."""
import asyncio
from collections import deque
from time import monotonic

from django.db import transaction

from twicc.core.models import Session, WeeklyActivity
from twicc.core.enums import Provider
from twicc.providers.compute_repair_journal import settle_compute_repair, load_compute_repair
from twicc.providers.tree_aggregate_prepare import (
    AggregatePreparationExecutor, ChangedPreparation, prepare_daily_values,
    prepare_project_values, prepare_session_values, publish_aggregate,
)
from twicc.providers.tree_aggregate_state import (
    former_tree_dependency_ids, observe_orphan_work, observe_tree_work, tree_work_matches,
)


def inventory_ready():
    """Require successful authoritative inventory for every enabled provider."""
    from twicc.orchestrator import get_orchestrator_registry
    from twicc.providers.state import get_enabled_providers
    registry = get_orchestrator_registry()
    for provider in get_enabled_providers():
        try:
            orchestrator = registry.get(provider)
        except KeyError:
            return False
        if (orchestrator is None or not orchestrator.initial_sync_done.is_set()
                or not getattr(orchestrator, 'initial_sync_succeeded', False)):
            return False
    return True


def _observe(snapshot, completed, pending, candidates=()):
    """Resolve current and former work independently, outside writer ownership."""
    fresh = load_compute_repair(snapshot.observation.session_id, snapshot.observation.incarnation)
    if fresh != snapshot:
        return None, []
    dependencies = set(former_tree_dependency_ids((snapshot,)))
    source = Session.objects.filter(pk=snapshot.observation.session_id).only('id', 'storage_incarnation').first()
    if source is not None and source.storage_incarnation == snapshot.observation.incarnation:
        dependencies.add(source.id)
    else:
        dependencies.add(None)
    works = []
    required = set()
    for sid in sorted(dependencies, key=lambda value: value or ''):
        work = (observe_orphan_work(snapshot) if sid is None else
                observe_tree_work(sid, pending, retained_repairs=(snapshot,), candidate_repairs=candidates))
        if work is None:
            required.add(('deferred', sid))
            continue
        key = work.observation.root or ('orphan', snapshot.observation.journal_id)
        required.add(key)
        previous = completed.get(key)
        previous_valid = previous is not None and tree_work_matches(previous, pending)
        if (not previous_valid
                or not set(work.observation.repairs) <= set(previous.observation.repairs)
                or not work.days <= previous.days or not work.project_ids <= previous.project_ids):
            works.append((key, work, previous_valid))
    return required, works


def _units(work, *, skip_sessions=False):
    members = {member.source.session_id: member for member in work.observation.members}
    def depth(sid):
        count = 0
        while sid in members:
            count += 1
            sid = members[sid].baseline[2]
        return count
    if not skip_sessions:
        for sid in sorted(members, key=lambda sid: (-depth(sid), sid)):
            yield 'session', sid
    for key in sorted(work.days, key=repr):
        yield 'day', key
    for key in sorted(work.weeks, key=repr):
        yield 'week', key
    for project in sorted(work.project_ids):
        yield 'project', project


class TreeAggregateScheduler:
    def __init__(self, *, inventory_ready=inventory_ready, clock=monotonic):
        self._inventory_ready = inventory_ready
        self._clock = clock
        self._executor = AggregatePreparationExecutor()
        self._snapshots = {}
        self._completed = {}
        self._requirements = {}
        self._retry = {}
        self._failures = {}
        self._active = None
        self._units = deque()
        self._future = None
        self._transition = None
        self._prepared = None
        self._closed = False
        self._observing_jid = None
        self._notifications = set()
        self._attempt_jids = set()

    def note_source_applied(self, session_id):
        # Discovery owns bounded journal reads. A successful source wakes retries.
        self._retry.clear()

    def discover(self, snapshots):
        for snapshot in snapshots:
            jid = snapshot.observation.journal_id
            old = self._snapshots.get(jid)
            if old is not None and old.observation != snapshot.observation:
                self._completed.clear()
                self._requirements.clear()
                self._retry.pop(jid, None)
            self._snapshots[jid] = snapshot

    def _abandon(self, *, release=True):
        if release and self._active is not None:
            _, work = self._active
            self._future = self._executor.release_work(work)
            self._transition = 'release'
        self._active = None
        self._units.clear()
        self._prepared = None

    async def advance(self, pending_sources):
        try:
            return await self._advance(pending_sources)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Aggregate continuation failed')
            ids = ([snapshot.observation.journal_id for snapshot in self._active[1].observation.repairs]
                   if self._active is not None else self._attempt_jids)
            for jid in ids:
                if jid is None:
                    continue
                count = self._failures.get(jid, 0) + 1
                self._failures[jid] = count
                self._retry[jid] = self._clock() + min(60, 2 ** min(count - 1, 6))
            self._abandon()
            return True

    async def _advance(self, pending_sources):
        if self._closed or not self._inventory_ready():
            return False
        if self._future is not None:
            if not self._future.done():
                return False
            future, self._future = self._future, None
            transition, self._transition = self._transition, None
            try:
                result = future.result()
            except ChangedPreparation:
                if self._active is not None:
                    self._future = self._executor.submit(tree_work_matches, self._active[1], pending_sources())
                    self._transition = 'validate'
                else:
                    self._abandon()
                return True
            if transition == 'observe':
                jid, required, works = result
                if required is None:
                    self._snapshots.pop(jid, None)
                    self._requirements.pop(jid, None)
                    return True
                self._requirements[jid] = required
                if works:
                    for key, _, _ in works:
                        self._completed.pop(key, None)
                    key, work, skip_sessions = works[0]
                    self._active = key, work
                    self.discover(self._active[1].observation.repairs)
                    self._units = deque(_units(work, skip_sessions=skip_sessions))
                elif not required <= self._completed.keys():
                    self._retry[jid] = self._clock() + 1
            elif transition == 'prepare':
                self._prepared = result
            elif transition == 'validate':
                if not result:
                    self._abandon()
            return True
        if self._active is not None:
            key, work = self._active
            if not self._units:
                self._completed[key] = work
                # Retain only the executor's last valid tree cache. A later
                # scope extension can reuse it; another tree replaces it.
                self._abandon(release=False)
                return True
            kind, unit = self._units[0]
            if kind != 'week' and self._prepared is None:
                function = {'session': prepare_session_values, 'day': prepare_daily_values,
                            'project': prepare_project_values}[kind]
                self._future = self._executor.submit(function, work, unit)
                self._transition = 'prepare'
                return True
            from twicc.providers.db_writer import _background_turn
            from twicc.providers.compute_executor import run_compute_sync
            published = False
            async def publish():
                nonlocal published
                if not self._inventory_ready():
                    return
                def apply():
                    with transaction.atomic():
                        pending = pending_sources()
                        if not self._inventory_ready():
                            return "deferred"
                        if not tree_work_matches(work, pending):
                            return 'deferred' if pending else 'changed'
                        if kind == 'week':
                            WeeklyActivity.recalculate_from_daily(unit.project_id, unit.day, Provider(unit.provider))
                            return 'published'
                        outcome = publish_aggregate(work, self._prepared, pending)
                        return 'unit_changed' if outcome == 'changed' else outcome
                outcome = await run_compute_sync(apply)
                if outcome == 'published':
                    published = True
                    self._units.popleft()
                    self._prepared = None
                elif outcome == 'unit_changed':
                    self._prepared = None
                elif outcome in {'changed', 'deferred'}:
                    for snapshot in work.observation.repairs:
                        self._retry[snapshot.observation.journal_id] = self._clock() + 1
                    self._abandon()
            granted = await _background_turn(publish, content_activity=False)
            if published and kind in {'session', 'project'}:
                from twicc.providers.db_writer import broadcast_session_updated, _broadcast_project_updated
                function = broadcast_session_updated if kind == 'session' else _broadcast_project_updated
                task = asyncio.create_task(function(unit))
                self._notifications.add(task)
                task.add_done_callback(self._notifications.discard)
            return granted
        eligible = {jid: snapshot for jid, snapshot in self._snapshots.items()
                    if self._requirements.get(jid) and self._requirements[jid] <= self._completed.keys()
                    and self._retry.get(jid, 0) <= self._clock()}
        unobserved = any(jid not in self._requirements for jid in self._snapshots)
        if eligible and not unobserved:
            from twicc.providers.db_writer import _background_turn
            from twicc.providers.compute_executor import run_compute_sync
            required = {key for jid in eligible for key in self._requirements[jid]}
            self._attempt_jids = set(eligible)
            completed = {key: self._completed[key] for key in required}
            async def settle():
                if not self._inventory_ready():
                    return
                def apply():
                    with transaction.atomic():
                        pending = pending_sources()
                        if not self._inventory_ready():
                            return False
                        if not all(tree_work_matches(completed[key], pending) for key in required):
                            return False
                        for snapshot in eligible.values():
                            settle_compute_repair(snapshot.observation)
                        return True
                if await run_compute_sync(apply):
                    for jid in eligible:
                        self._snapshots.pop(jid, None)
                self._requirements.clear()
                self._completed.clear()
            return await _background_turn(settle, content_activity=False)
        for jid, snapshot in tuple(self._snapshots.items()):
            if jid in eligible:
                continue
            if self._retry.get(jid, 0) > self._clock():
                continue
            def observe(snapshot=snapshot, jid=jid, pending=pending_sources(),
                        completed=dict(self._completed), candidates=tuple(self._snapshots.values())):  # noqa: B006 -- capture this submission's snapshot
                required, works = _observe(snapshot, completed, pending, candidates)
                return jid, required, works
            self._observing_jid = jid
            self._attempt_jids = {jid}
            self._future = self._executor.submit(observe)
            self._transition = 'observe'
            return True
        return False

    async def close(self):
        self._closed = True
        await self._executor.aclose()
        if self._notifications:
            await asyncio.gather(*self._notifications, return_exceptions=True)
