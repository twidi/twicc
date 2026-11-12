"""Event-driven durable decisions with independent expiry and owned inference.

All database mutations use existing writer admission. Classifier calls own no
publication lock. The deadline path never queues behind inference capacity.
"""

import asyncio
import logging
from collections import OrderedDict
from contextlib import suppress
from datetime import timedelta

from django.db import transaction
from django.db.models import Min, Q
from django.utils import timezone

from twicc.agent.semantic_types import DecisionRequest, SourceScope
from twicc.core.enums import Provider
from twicc.core.models import Session, TimelineDecision
from twicc.core.services import timeline_decisions as decisions
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.timeline_classification import (
    ClassificationError, build_decision_context, classify, drain_classifier_calls,
)

logger = logging.getLogger(__name__)
WORKERS = 4
BATCH_SIZE = 100
RETRY_SECONDS = 5
RESULT_CACHE_LIMIT = 256
_AWAITING_INFERENCE = Q(kind='ending', payload__has_key='awaiting_inference', payload__awaiting_inference=True)


async def _admitted(function, *args, **kwargs):
    return await run_under_db_write_lock(lambda: asyncio.to_thread(function, *args, **kwargs))


def _live_ending(row):
    from twicc.agent.registry import get_agent_manager_registry

    manager = get_agent_manager_registry().get(Provider(row.provider))
    agent = manager._agents.get(row.session_id)
    context = agent._completion_pending_ending if agent is not None else None
    return (manager, agent) if context is not None and context.id == str(row.pk) else None


def _source_ready(row) -> bool:
    """Check exact frozen references; never build context from a current tail."""
    from twicc.core.services.timeline_messages import _message_application_candidate, _ensure_decision_source, _resolve_groups
    from twicc.agent.completion_types import NativeMessageRef

    decisions.invalidate_semantic_decisions(session_id=row.session_id)
    row.refresh_from_db()
    if row.status not in ('pending', 'running', 'accepted') or row.forced:
        return False
    if row.kind == 'message':
        return _message_application_candidate(row) is not None
    if not _ensure_decision_source(row):
        return False
    if row.payload.get('awaiting_inference'):
        from twicc.core.content_storage.codec import ContentIntegrityError
        from twicc.core.services.timeline_messages import capture_ending_context

        payload = row.payload
        if not payload.get('evidence_captured'):
            session = Session.objects.get(pk=row.session_id)
            try:
                evidence = {} if payload.get('tool_context_incomplete') else capture_ending_context(
                    session, refs=[NativeMessageRef(**ref) for ref in payload.get('message_refs', [])],
                    floor=payload.get('source_floor', 0), occurred_at=row.occurred_at,
                    input_origins=payload.get('input_context', []),
                    tool_refs=[NativeMessageRef(**ref) for ref in payload.get('captured_tool_refs', [])])
            except ContentIntegrityError:
                logger.warning('Completion context unavailable for session %s', row.session_id, exc_info=True)
                evidence = {}
            if evidence is None:
                return False
            payload.update(evidence)
            payload['evidence_captured'] = True
        return True
    session = Session.objects.filter(pk=row.session_id).first()
    refs = [NativeMessageRef(**ref) for ref in row.payload.get('message_refs', [])]
    groups = _resolve_groups(session, refs, floor=row.payload.get('source_floor', 0),
        ceiling=row.payload.get('source_ceiling')) if session is not None and refs else []
    return any(ref in refs for group in groups for _, _, aliases in group for ref in aliases)


def _claim(now, occupied, active, deferred=()):
    query = TimelineDecision.objects.filter(status='pending', forced=False, not_before__lte=now).filter(
        Q(expires_at__gt=now) | _AWAITING_INFERENCE
    ).exclude(pk__in=(*active, *deferred)).order_by('not_before', 'created_at', 'pk')
    for session_id, kind in occupied:
        query = query.exclude(session_id=session_id, kind=kind)
    # Check at most 100 rows before yielding writer admission.
    skipped = []
    for row in query[:BATCH_SIZE]:
        with transaction.atomic():
            if not _source_ready(row):
                skipped.append(str(row.pk))
                continue
            if row.kind == 'ending' and row.payload.get('awaiting_inference'):
                # The inference budget starts with a ready source and a free
                # worker. DB admission and watcher lag consume no model time.
                row.payload['awaiting_inference'] = False
                row.expires_at = timezone.now() + timedelta(seconds=45)
                row.save(update_fields=['payload', 'expires_at'])
            elif row.expires_at <= timezone.now():
                continue
            if TimelineDecision.objects.filter(pk=row.pk, status='pending', forced=False).update(status='running'):
                return row, skipped
    return None, skipped


def _due(now, exclude=()):
    return list(TimelineDecision.objects.filter(status__in=('pending', 'running'), expires_at__lte=now)
        .exclude(_AWAITING_INFERENCE).exclude(pk__in=exclude)
        .order_by('expires_at', 'pk')[:BATCH_SIZE])


def _next_deadline(exclude=()):
    return TimelineDecision.objects.filter(status__in=('pending', 'running')).exclude(
        _AWAITING_INFERENCE).exclude(pk__in=exclude).aggregate(value=Min('expires_at'))['value']


def _next_ready(occupied, deferred=()):
    query = TimelineDecision.objects.filter(status='pending').exclude(pk__in=deferred)
    for session_id, kind in occupied:
        query = query.exclude(session_id=session_id, kind=kind)
    return query.aggregate(value=Min('not_before'))['value']


def _unresolved_calls(ids):
    return {str(pk) for pk in TimelineDecision.objects.filter(pk__in=ids,
        status__in=('pending', 'running'), forced=False).values_list('pk', flat=True)}


def _effect_rows(after=None):
    query = TimelineDecision.objects.filter(status='accepted').filter(
        Q(applied_at__isnull=True) | Q(force_pending=True))
    if after is not None:
        query = query.filter(pk__gt=after)
    return list(query.order_by('pk')[:BATCH_SIZE])


def _retry_effect(row, now):
    with transaction.atomic():
        decisions.invalidate_semantic_decisions(session_id=row.session_id)
        if row.kind == 'message':
            return decisions.publish_message_if_ready(decision_id=str(row.pk))
        return decisions.apply_accepted_ending(decision_id=str(row.pk), now=now)


def _message_result(row, classification, route, fallback):
    with transaction.atomic():
        decisions.invalidate_semantic_decisions(session_id=row.session_id)
        if fallback is not None:
            now = timezone.now()
            expires = TimelineDecision.objects.values_list('expires_at', flat=True).filter(pk=row.pk).first()
            changed = expires is not None and decisions.accept_fallback(decision_id=str(row.pk),
                reason='timeout' if now >= expires else fallback, now=now)
        else:
            changed = decisions.resolve_decision(decision_id=str(row.pk), classification=classification,
                effective_route=route, now=timezone.now())
        decisions.publish_message_if_ready(decision_id=str(row.pk))
        return changed


def _ignored(row, verdict, route):
    # Reuse captured safe diagnostics; no source reads for logging.
    row.reason = 'late_result'
    row.status = None  # A guarded result has no newly applied durable outcome.
    row.effective_route = route
    decisions._record_after_commit(row, event='ignored', verdict=verdict)


class DecisionRuntime:
    """One scheduler and one deadline task share four per-session-limited workers."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.deadline_wake = asyncio.Event()
        self.workers = {}
        self.calls = {}
        self.occupied = set()
        self.callbacks = {}
        self.seen_results = OrderedDict()
        self.effect_cursor = None
        self.effects_pending = False
        self.deadline_retries = {}
        self.source_retries = {}

    def signal(self):
        self.loop.call_soon_threadsafe(self.wake.set)
        self.loop.call_soon_threadsafe(self.deadline_wake.set)

    def cancel_call(self, decision_id):
        call = self.calls.get(decision_id)
        if call is not None and not call.done() and not call.cancelling():
            call.cancel()
            self.signal()

    def _bind(self, agent):
        # The callback targets inference children only. An accepted ending also
        # invokes it while its worker is applying the admitted publication.
        if agent not in self.callbacks:
            self.callbacks[agent] = (agent._completion_cancel_inference, 1)
            agent._completion_cancel_inference = self.cancel_call
        else:
            previous, owners = self.callbacks[agent]
            self.callbacks[agent] = (previous, owners + 1)

    def _release(self, agent):
        previous, owners = self.callbacks[agent]
        if owners > 1:
            self.callbacks[agent] = (previous, owners - 1)
            return
        self.callbacks.pop(agent)
        if agent._completion_cancel_inference == self.cancel_call:
            agent._completion_cancel_inference = previous

    async def apply(self, row, *, classification=None, route=None, fallback=None):
        ignored = False
        if row.kind == 'ending':
            live = _live_ending(row)
            if live is not None:
                manager, agent = live
                self._bind(agent)
                context = agent._completion_pending_ending
                try:
                    changed = await manager.apply_ending_decision(agent, str(row.pk), classification=classification,
                        effective_route=route, fallback_reason=fallback)
                finally:
                    self._release(agent)
                # A normal NO defers the same open controller without a local
                # cancellation/claim. All guard winners retain their local
                # resolution or change that snapshot. No lookup is for logging.
                ignored = not changed and (str(row.pk) in agent._completion_ending_results
                    or agent.completion_controller.snapshot() != context.snapshot)
            else:
                # A detached unresolved ending cannot claim an obsolete live
                # candidate. Boot recovery alone reconstructs exact old turns.
                await _admitted(decisions.cancel_decision, decision_id=str(row.pk), reason='stale_candidate')
                changed = False
                ignored = True
        else:
            changed = await _admitted(_message_result, row, classification, route, fallback)
            ignored = not changed
        if classification is not None and str(row.pk) not in self.seen_results:
            self.seen_results[str(row.pk)] = None
            while len(self.seen_results) > RESULT_CACHE_LIMIT:
                self.seen_results.popitem(last=False)
            if ignored:
                await asyncio.to_thread(_ignored, row, classification.verdict, route)
        return changed

    async def infer(self, row):
        request = DecisionRequest(str(row.pk), row.kind, row.session_id,
            SourceScope(row.provider, row.storage_incarnation, row.history_epoch), row.payload,
            row.expires_at, row.requested_route)
        deadline = self.loop.time() + max(0, (row.expires_at - timezone.now()).total_seconds())
        request = await build_decision_context(request)
        return await classify(request, session_provider=Provider(row.provider), deadline=deadline)

    async def worker(self, row):
        key = str(row.pk)
        agent = None
        try:
            if row.kind == 'ending':
                live = _live_ending(row)
                if live is None:
                    await self.apply(row, fallback='stale_candidate')
                    return
                agent = live[1]
                self._bind(agent)
                # Local activity can win before callback registration or insert.
                if _live_ending(row) is None:
                    return
            # Ending claims already freeze their indexed context. Do not queue
            # a second writer admission between the budget start and inference.
            if row.kind == 'message' and not await _admitted(_source_ready, row):
                return
            if row.kind == 'ending' and _live_ending(row) is None:
                return
            if timezone.now() >= row.expires_at:
                await self.apply(row, fallback='timeout')
                return
            call = asyncio.create_task(self.infer(row))
            self.calls[key] = call
            try:
                result, route = await call
            except ClassificationError as exc:
                await self.apply(row, fallback=exc.reason)
            else:
                await self.apply(row, classification=result, route=route)
        except asyncio.CancelledError:
            # A local winner or independent expiry cancels only inference.
            # Durable status/effects remain owned by that winning path.
            pass
        except Exception:
            logger.exception('Semantic decision worker failed for %s', key)
            with suppress(Exception):
                await self.apply(row, fallback='generation_failed')
        finally:
            if agent is not None:
                self._release(agent)
            self.calls.pop(key, None)
            self.workers.pop(key, None)
            self.occupied.discard((row.session_id, row.kind))
            self.signal()

    async def deadlines(self):
        while True:
            try:
                await self.deadline_cycle()
            except Exception:
                logger.exception('Semantic decision deadline cycle failed')
                await _wait(self.deadline_wake, RETRY_SECONDS)

    async def deadline_cycle(self):
        self.deadline_wake.clear()
        self.deadline_retries = {key: due for key, due in self.deadline_retries.items() if due > self.loop.time()}
        rows = await _admitted(_due, timezone.now(), tuple(self.deadline_retries))
        for row in rows:
            self.cancel_call(str(row.pk))
            try:
                await self.apply(row, fallback='timeout')
            except Exception:
                logger.exception('Semantic decision deadline application failed for %s', row.pk)
        if rows:
            unresolved = await _admitted(_unresolved_calls, tuple(row.pk for row in rows))
            self.deadline_retries.update((key, self.loop.time() + RETRY_SECONDS) for key in unresolved)
        next_due = await _admitted(_next_deadline, tuple(self.deadline_retries))
        delay = max(0, (next_due - timezone.now()).total_seconds()) if next_due else None
        if self.deadline_retries:
            retry = max(0, min(self.deadline_retries.values()) - self.loop.time())
            delay = retry if delay is None else min(delay, retry)
        await _wait(self.deadline_wake, delay)

    async def schedule(self):
        while True:
            try:
                await self.schedule_cycle()
            except Exception:
                logger.exception('Semantic decision scheduling failed')
                await _wait(self.wake, RETRY_SECONDS)

    async def schedule_cycle(self):
        self.wake.clear()
        if self.calls:
            unresolved = await _admitted(_unresolved_calls, tuple(self.calls))
            for key in tuple(self.calls):
                if key not in unresolved:
                    self.cancel_call(key)
        continuing_sweep = self.effect_cursor is not None
        effects = await _admitted(_effect_rows, self.effect_cursor)
        self.effects_pending = bool(effects) or continuing_sweep
        self.effect_cursor = effects[-1].pk if len(effects) == BATCH_SIZE else None
        for row in effects:
            try:
                await _admitted(_retry_effect, row, timezone.now())
            except Exception:
                logger.exception('Semantic decision effect retry failed for %s', row.pk)
        self.source_retries = {key: due for key, due in self.source_retries.items() if due > self.loop.time()}
        while len(self.workers) < WORKERS:
            row, skipped = await _admitted(_claim, timezone.now(), self.occupied,
                tuple(self.workers), tuple(self.source_retries))
            self.source_retries.update((key, self.loop.time() + RETRY_SECONDS) for key in skipped)
            if row is None:
                if len(skipped) == BATCH_SIZE:
                    continue
                break
            self.deadline_wake.set()
            key = str(row.pk)
            self.occupied.add((row.session_id, row.kind))
            self.workers[key] = asyncio.create_task(self.worker(row))
        next_ready = await _admitted(_next_ready, self.occupied, tuple(self.source_retries)) if len(self.workers) < WORKERS else None
        delay = max(0, (next_ready - timezone.now()).total_seconds()) if next_ready else None
        if self.effects_pending or delay == 0:
            delay = RETRY_SECONDS if delay is None else min(RETRY_SECONDS, delay or RETRY_SECONDS)
        if self.source_retries and len(self.workers) < WORKERS:
            retry = max(0, min(self.source_retries.values()) - self.loop.time())
            delay = retry if delay is None else min(delay, retry)
        if self.effect_cursor is not None:
            delay = 0  # Continue to the next bounded page, once per sweep.
        await _wait(self.wake, delay)

    async def stop(self):
        decisions.register_decision_wakeup(None)
        for worker in tuple(self.workers.values()):
            worker.cancel()
        await _join_cleanup(asyncio.gather(*tuple(self.workers.values()), return_exceptions=True))
        # Every active callback owner releases in its own finally block.
        # No completed agent or saved bound callback remains in this runtime.
        # Owned SDK cleanup can exceed response deadlines. A canceled drain
        # retains ownership and must finish before the inference loop closes.
        await _join_cleanup(asyncio.create_task(drain_classifier_calls()))


async def _join_cleanup(task):
    """Finish owned shutdown work even if the caller receives another cancel."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return await task


async def _wait(event, seconds):
    if seconds is None:
        await event.wait()
    else:
        with suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=seconds)


async def start_timeline_decision_task(stop_event: asyncio.Event) -> None:
    runtime = DecisionRuntime()
    decisions.register_decision_wakeup(runtime.signal)
    scheduler = asyncio.create_task(runtime.schedule())
    expiry = asyncio.create_task(runtime.deadlines())
    stopped = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait((scheduler, expiry, stopped), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stopped:
                task.result()
    finally:
        for task in (scheduler, expiry, stopped):
            task.cancel()
        await _join_cleanup(asyncio.gather(scheduler, expiry, stopped, return_exceptions=True))
        await runtime.stop()
