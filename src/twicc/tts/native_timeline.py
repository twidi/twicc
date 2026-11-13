"""Selection policy for one native session's automatic timeline readings.

The native session owns playback and resources. This listener owns only its
activation, feed cursor, pending snapshots, and independent summary references.
"""

import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db.models import Max
from django.utils import timezone

from twicc.core.models import Project, TimelineEvent
from twicc.core.services.timeline_feed import (
    FeedRequest, _page_querysets, build_sessions_table, cited_session_ids, fetch_feed, format_occurred_at,
    latest_event_id, serialize_event,
)
from twicc.core.services.timeline_final_messages import resolve_final_messages
from twicc.core.services.timeline_filters import TimelineFilter
from twicc.projects import expand_project_ids_with_worktrees, project_scope_ids
from twicc.summaries import service as summaries
from twicc.synced_settings import read_synced_settings
from twicc.tts.config import SPEECH_FIELDS, openrouter_api_key, read_tts_config, script_config_validated
from twicc.workspaces import read_workspaces
from twicc.core.ui_messages import ui_message_text

logger = logging.getLogger(__name__)
INVALIDATIONS = frozenset({
    'timeline_changed', 'project_added', 'project_updated', 'project_deleted',
    'workspaces_updated', 'session_updated', 'session_removed', 'synced_settings_updated',
    'tts_config_updated', 'summary_saved', 'resync_required',
})


def _spoken_project(project, projects):
    """The project the listener hears: a worktree is a work folder of its main project, so it gives
    way to that project (a missing parent leaves the worktree itself)."""
    return projects.get(project.worktree_of_id) or project


def _project_name(project):
    if project.name:
        return project.name
    if project.directory:
        return project.directory.split('/')[-1] or project.directory
    return project.id.split('-')[-1] or project.id


def _scope(scope):
    """Resolve named scopes at read time. Empty named scopes never mean global."""
    projects = {p.id: p for p in Project.objects.all()}
    kind, scope_id = scope['kind'], scope['id']
    if kind == 'global':
        return tuple(projects), 'All projects', projects
    if kind == 'workspace':
        workspace = next((w for w in read_workspaces().get('workspaces', []) if w.get('id') == scope_id), None)
        if workspace is None:
            return (), '', projects
        members = expand_project_ids_with_worktrees(workspace.get('projectIds', []))
        return tuple(p for p in members if p in projects), workspace.get('name', ''), projects
    project = projects.get(scope_id)
    if project is None:
        return (), '', projects
    return tuple(p for p in project_scope_ids(scope_id) if p in projects), _project_name(project), projects


def _read_policy():
    """Mirror the existing Web availability rule and keep server-only fields private."""
    config = read_tts_config()
    entry = next((c for c in config['configs'] if c['id'] == config['active_id']), None)
    if entry is not None:
        validated = (script_config_validated(entry) if entry['kind'] == 'script' else
                     entry.get('validated') == {field: entry.get(field) for field in SPEECH_FIELDS})
        if not validated or (entry['kind'] != 'script' and openrouter_api_key() is None):
            entry = None
    settings = read_synced_settings()
    return {'entry': entry, 'adapt': config['adapt'], 'summary': settings.get('timelineSummaryEnabled') is not False,
            'summary_model': settings.get('summaryModel'), 'final_only': settings.get('timelineSpeechFinalOnly') is not False}


def _read_range(request, boundary, after_id=0):
    """Capture one complete, scoped range using the feed's existing filtered querysets."""
    high_water = latest_event_id() or 0
    events = {}
    for rows in _page_querysets(request._replace(before=None, after_id=None)):
        rows = rows.filter(id__gt=after_id, id__lte=high_water,
                           occurred_at__gt=timezone.datetime.fromisoformat(boundary))
        for event in rows.iterator(chunk_size=100):
            events[event.id] = event
    ordered = sorted(events.values(), key=lambda event: (event.occurred_at, event.id))
    return {'events': [serialize_event(event, include_ui_messages=True) for event in ordered],
            'latest_id': high_water, 'sessions': build_sessions_table(cited_session_ids(ordered)),
            'truncated': False}


def _shown_boundaries(session_ids):
    return {row['session_id']: row['last_id'] for row in TimelineEvent.objects.filter(
        session_id__in=session_ids, kind='hidden.ended').values('session_id').annotate(last_id=Max('id'))}


class TimelineListener:
    """One activation and one offer. It opens no pending audio."""

    def __init__(self, session, *, clock=timezone.now):
        self.session, self.clock = session, clock
        self.active = None
        self.pending, self.seen = [], set()
        self.boundaries, self.final_boundaries, self.shown = {}, {}, {}
        self.cursor, self.watermark, self.context = 0, None, None
        self.selected = None
        self.finals, self.final_entries, self.consumed = {}, {}, set()
        self._final_dirty = set()
        self._final_only = True
        self.preparations = {}
        self._summary_policy = None
        self._selections = {}
        self._invalidated_summaries = set()
        self._subscription_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._tasks = set()
        self._channel = self._watcher = None
        self._refresh_task = None
        self._dirty = False
        self._caught_up, self._epoch = False, 0

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine, name='native-timeline')
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            logger.error('Native timeline task failed', exc_info=(type(error), error, error.__traceback__))

    async def command(self, command):
        """Reserve intent immediately; storage/preparation never blocks command auth."""
        kind = command['type']
        if kind == 'listen':
            await self.disable()
            if command.get('fresh'):
                command = {**command, 'boundary': format_occurred_at(self.clock())}
                for key in ('final_boundary', 'ending_cursor', 'ordinary_after_id', 'pending_final_ending_ids', 'excluded_event_ids'):
                    command.pop(key, None)
            self._activate(command)
            # Only explicit user activations carry an epoch. Android recovery
            # and notification resume issue listen too, without this field.
            from twicc.telemetry.activity import record_report
            await sync_to_async(record_report)({'metric': 'timeline_tts_activations',
                                                'epoch': command.get('telemetry_epoch')})
            active = self.active
            # Reserve the notice before initialization can offer fresh messages.
            notice = command.get('notice')
            if notice:
                accepted = False
                try:
                    policy = await sync_to_async(_read_policy)()
                    if self.active is active and policy['entry'] is not None:
                        entry = policy['entry']
                        speech = {'text': notice['text'], 'cache': True}
                        if entry['kind'] == 'script':
                            speech.update(kind='script', script_id=entry['id'])
                        else:
                            speech.update({field: entry.get(field) for field in SPEECH_FIELDS})
                        accepted = await self.session.select_reading(notice, {'kind': 'text', 'speech': speech},
                            {'identity': None, 'text': notice['text'], 'source': 'recovery', 'config_id': entry['id']},
                            policy={**policy, 'adapt': 'off'})
                except Exception:
                    logger.exception('Native recovery notice could not start')
                if not accepted and self.active is active:
                    await self.session.fail('speech_failed', ui_message_text('Text-to-speech failed', 'backend.tts.failed'),
                        reading_id=notice['reading_id'], generation=notice['generation'])
            if self.active is active:
                self._spawn(self._initialize(active))
        elif kind == 'set_muted':
            if self.active and self.active['activation_id'] == command['activation_id']:
                self.active['include_muted'] = command['include_muted']
                self._publish()
                await self.invalidate({'type': 'session_updated'})
        elif kind == 'disable':
            if self.active and self.active['activation_id'] == command['activation_id']:
                await self.disable()
        elif kind == 'stop_all':
            await self.disable()
        elif kind == 'select':
            key = (command['reading_id'], command['generation'])
            task = self._spawn(self.select(command['event_id'], *key, silence_reset=command.get('silence_reset', False)))
            self._selections[key] = (command['event_id'], task)
            task.add_done_callback(lambda completed: self._selections.pop(key, None))
        elif kind == 'ping':
            if self.active and self._channel:
                # Channel groups have their own expiry. Existing native
                # heartbeats renew membership during silent listening.
                await get_channel_layer().group_add('updates', self._channel)
        elif kind == 'skip':
            selection = self._selections.pop((command['reading_id'], command['generation']), None)
            if selection:
                event_id, task = selection
                task.cancel()
                self.pending = [event for event in self.pending if event['id'] != event_id]
                self.consumed.add(event_id)
                if event_id in self.final_entries:
                    self._drop_final(self.final_entries[event_id]['ending_id'])
                await self._release_preparation(event_id)
                await self._offer()

    async def reconnect(self):
        """Fence stale offers before the native session publishes resumed state."""
        if self.active:
            self._caught_up = False
            self._epoch += 1
            self.session._candidate = None
            self._final_dirty.update(self.finals)
            await self.invalidate({'type': 'timeline_changed'})

    def _activate(self, activation):
        self.active = {key: activation[key] for key in ('activation_id', 'page_id', 'scope', 'boundary', 'include_muted')}
        self.active.update(final_boundary=activation.get('final_boundary', activation['boundary']),
                           ending_cursor=activation.get('ending_cursor', 0),
                           ordinary_after_id=activation.get('ordinary_after_id', 0))
        self.finals = dict.fromkeys(activation.get('pending_final_ending_ids', []))
        self.final_entries = {}
        self._final_dirty = set(self.finals)
        self.consumed = set(activation.get('excluded_event_ids', []))
        self.active.update(pending_final_ending_ids=sorted(self.finals), excluded_event_ids=sorted(self.consumed))
        self.session.listening = self.active.copy()
        self.session.error = None
        self.pending, self.seen, self.boundaries, self.shown = [], set(activation.get('excluded_event_ids', [])), {}, {}
        self.final_boundaries = {}
        self.cursor, self.watermark, self.context = 0, None, None
        self._caught_up = False
        self._epoch += 1
        self.session.publish_state()

    async def enable(self, activation):
        await self.disable()
        self._activate(activation)
        await self._initialize(self.active)

    async def _initialize(self, active):
        await self._subscribe()
        async with self._lock:
            if self.active is not active:
                return
            members, label, _ = await sync_to_async(_scope)(active['scope'])
            if self.active is not active:
                return
            if not members:
                await self._stop('scope_unavailable', ui_message_text('Timeline reading stopped. Choose another timeline.', 'backend.tts.scopeUnavailable'))
                return
            self.boundaries = {member: active['boundary'] for member in members}
            self.final_boundaries = {member: active['final_boundary'] for member in members}
            active['label'] = label
            # Refresh owns the first captured range. No preparation precedes catch-up.
            self.cursor = 0
        # Include arrivals committed during bootstrap, before any offer.
        if self.active is active:
            await self.refresh()

    def _request(self):
        projects = () if self.active['scope']['kind'] == 'global' else tuple(self.boundaries)
        # Always observe mute transitions; admission retains the activation choice.
        return FeedRequest(projects, 100, None, self.cursor, include_muted=True)

    async def _subscribe(self, *, replace=False):
        async with self._subscription_lock:
            if self._watcher is not None and not replace:
                return
            layer = get_channel_layer()
            previous_channel, previous_watcher = self._channel, self._watcher
            channel = await layer.new_channel('native.tts.')
            await layer.group_add('updates', channel)
            self._channel = channel
            self._watcher = asyncio.create_task(self._watch(layer, channel), name='native-timeline-updates')
            if previous_watcher and previous_watcher is not asyncio.current_task():
                previous_watcher.cancel()
                await asyncio.gather(previous_watcher, return_exceptions=True)
            if previous_channel:
                await layer.group_discard('updates', previous_channel)

    async def _watch(self, layer, channel):
        while self._channel == channel:
            message = await layer.receive(channel)
            await self.invalidate(message.get('data', {}))

    async def invalidate(self, payload):
        """The updates group is an invalidation source, never trusted event content."""
        if not isinstance(payload, dict) or payload.get('type') not in INVALIDATIONS:
            return
        if payload['type'] == 'resync_required':
            # A production updates channel stays broken permanently. Replace
            # it before catch-up, retaining the live cursor and accepted work.
            if self.active:
                self._caught_up = False
                self._epoch += 1
                self.session._candidate = None
                self._invalidated_summaries.update(self.preparations)
            await self._subscribe(replace=True)
        if not self.active:
            return
        if payload['type'] == 'summary_saved':
            for event_id, preparation in self.preparations.items():
                descriptor = preparation['descriptor']
                if (descriptor.get('source_key') == payload.get('source_key')
                        and descriptor.get('source_hash') == payload.get('source_hash')):
                    self._invalidated_summaries.add(event_id)
        if payload['type'] in ('resync_required', 'session_updated', 'project_updated', 'workspaces_updated'):
            self._final_dirty.update(self.finals)
        self._final_dirty.update(set(payload.get('finalized_ending_ids', [])) & self.finals.keys())
        self._dirty = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._spawn(self._refresh_loop())

    async def _refresh_loop(self):
        while self._dirty and self.active:
            self._dirty = False
            await self.refresh()

    def _speech_key(self, event):
        final = self.final_entries.get(event['id'])
        return (final['speech_time'], final['ending_id']) if final else (event['occurred_at'], event['id'])

    def _drop_final(self, ending_id):
        event = self.finals.pop(ending_id, None)
        self._final_dirty.discard(ending_id)
        if event is not None:
            self.final_entries.pop(event['id'], None)
            self.pending = [item for item in self.pending if item['id'] != event['id']]

    def _consume(self, body):
        for event in body['events']:
            if event['kind'] == 'hidden.ended':
                self.shown[event['session_id']] = max(event['id'], self.shown.get(event['session_id'], 0))
        for event in body['events']:
            if event['id'] in self.seen:
                continue
            self.seen.add(event['id'])
            session = body['sessions'].get(event['session_id'], {})
            boundary = self.boundaries.get(event['project_id'])
            visible = (boundary is not None and not session.get('hidden')
                       and (self.active['include_muted'] or not session.get('mute_on_user_turn'))
                       and event['id'] > self.shown.get(event['session_id'], 0))
            if (self._final_only and event['kind'] == 'turn.ended' and event.get('actor') == 'agent'
                    and isinstance(event['data'].get('final_message'), dict) and visible
                    and event['occurred_at'] > self.active['final_boundary']
                    and event['occurred_at'] > self.final_boundaries.get(event['project_id'], self.active['final_boundary'])):
                self.finals.setdefault(event['id'], None)
                self._final_dirty.add(event['id'])
            if (self._final_only or event['id'] in self.consumed
                    or event['id'] <= self.active['ordinary_after_id'] or not visible
                    or event['kind'] != 'assistant_message.sent' or not isinstance(event['data'].get('text'), str)
                    or not event['data']['text'].strip() or event['occurred_at'] <= boundary):
                continue
            occurred = timezone.datetime.fromisoformat(event['occurred_at'])
            if self.watermark is not None and occurred < self.watermark - timedelta(seconds=5):
                self.consumed.add(event['id'])
                continue
            if event['id'] not in {item['id'] for item in self.pending}:
                self.pending.append(event)
        self.pending.sort(key=self._speech_key)

    async def _resolve_finals(self, active, epoch):
        # Remove the captured dirty IDs before awaiting. A concurrent signal stays dirty.
        ids = sorted(self._final_dirty & self.finals.keys())
        self._final_dirty.difference_update(ids)
        try:
            for offset in range(0, len(ids), 100):
                batch = ids[offset:offset + 100]
                result = await sync_to_async(resolve_final_messages)(batch,
                    project_ids=tuple(self.boundaries), include_muted=active['include_muted'])
                shown = await sync_to_async(_shown_boundaries)({item['message']['session_id']
                    for item in result['items'] if item['state'] == 'ready'})
                if self.active is not active or epoch != self._epoch:
                    self._final_dirty.update(ids)
                    return
                for session_id, last_id in shown.items():
                    self.shown[session_id] = max(last_id, self.shown.get(session_id, 0))
                for item in result['items']:
                    ending_id = item['ending_id']
                    if ending_id not in self.finals:
                        continue
                    if item['state'] in ('none', 'unavailable'):
                        self._drop_final(ending_id)
                    elif item['state'] == 'ready':
                        event = item['message']
                        if (event['id'] in self.consumed or ending_id <= self.shown.get(event['session_id'], 0)):
                            self._drop_final(ending_id)
                            continue
                        self.finals[ending_id] = event
                        self.final_entries[event['id']] = {'ending_id': ending_id, 'speech_time': item['occurred_at']}
                        if event['id'] not in {entry['id'] for entry in self.pending}:
                            self.pending.append(event)
        except Exception:
            self._final_dirty.update(ids)
            raise
        self.pending.sort(key=self._speech_key)

    async def refresh(self):
        async with self._lock:
            active = self.active
            epoch = self._epoch
            if active is None or not self.boundaries:
                return
            members, label, _ = await sync_to_async(_scope)(active['scope'])
            if self.active is not active:
                return
            if not members:
                await self._stop('scope_unavailable', ui_message_text('Timeline reading stopped. Choose another timeline.', 'backend.tts.scopeUnavailable'))
                return
            now = format_occurred_at(self.clock())
            self.boundaries = {member: self.boundaries.get(member, now) for member in members}
            self.final_boundaries = {member: self.final_boundaries.get(member, now) for member in members}
            active['label'] = label
            policy = await sync_to_async(_read_policy)()
            if self.active is not active or epoch != self._epoch:
                return
            self._final_only = policy.get('final_only', True)
            if self._final_only:
                for event in list(self.pending):
                    if event['id'] not in self.final_entries:
                        self.pending.remove(event)
                request = self._request()._replace(filter=TimelineFilter(families=('turns', 'organization')))
                body = await sync_to_async(_read_range)(request, active['final_boundary'], active['ending_cursor'])
            else:
                request = self._request()._replace(after_id=max(self.cursor, active['ordinary_after_id']))
                body = await sync_to_async(fetch_feed)(request, with_turns_below=False)
                if self.cursor == 0 or body['truncated']:
                    body = await sync_to_async(_read_range)(request, min(self.boundaries.values()),
                                                           max(self.cursor, active['ordinary_after_id']))
            if self.active is not active or epoch != self._epoch:
                return
            self._consume(body)
            self.cursor = max(self.cursor, body['latest_id'] or 0)
            # Cursor and obligations publish together, after the whole captured range.
            active['ending_cursor'] = max(active['ending_cursor'], self.cursor)
            if self._final_only:
                active['ordinary_after_id'] = max(active['ordinary_after_id'], self.cursor)
            await self._resolve_finals(active, epoch)
            if self.active is not active or epoch != self._epoch:
                return
            table = await sync_to_async(build_sessions_table)({e['session_id'] for e in self.pending})
            if self.active is not active or epoch != self._epoch:
                return
            for event in list(self.pending):
                final = self.final_entries.get(event['id'])
                cutoff_id = final['ending_id'] if final else event['id']
                if (event['project_id'] not in self.boundaries or table.get(event['session_id'], {}).get('hidden')
                        or cutoff_id <= self.shown.get(event['session_id'], 0)
                        or (not active['include_muted'] and table.get(event['session_id'], {}).get('mute_on_user_turn'))):
                    self.consumed.add(event['id'])
                    if final:
                        self._drop_final(final['ending_id'])
                    else:
                        self.pending.remove(event)
            self._caught_up = True
            await self._reconcile_policy(policy)
            if self.active is active and epoch == self._epoch:
                await self._offer()

    async def _reconcile_policy(self, policy=None):
        active = self.active
        if policy is None:
            policy = await sync_to_async(_read_policy)()
        if self.active is not active or not self._caught_up:
            return
        self.session.configuration_available = policy['entry'] is not None
        if policy['entry'] is None:
            await self._stop('tts_unavailable', ui_message_text('Text-to-speech is unavailable. Check the active configuration.', 'backend.tts.configurationUnavailable'))
            return
        if policy.get('final_only', True):
            active['ordinary_after_id'] = max(active['ordinary_after_id'], self.cursor)
            self.pending = [event for event in self.pending if event['id'] in self.final_entries]
        summary_policy = (policy['summary'], policy.get('summary_model'))
        pending_ids = {e['id'] for e in self.pending}
        for event_id in list(self.preparations):
            if self.selected and self.selected['event']['id'] == event_id:
                continue
            if (event_id not in pending_ids or summary_policy != self._summary_policy
                    or event_id in self._invalidated_summaries):
                await self._release_preparation(event_id)
        self._invalidated_summaries.clear()
        self._summary_policy = summary_policy
        if policy['summary']:
            await self._prepare_summaries(active)
        self._publish()

    async def _prepare_summaries(self, active):
        epoch = self._epoch
        if self.active is not active or not self._caught_up:
            return
        current = await sync_to_async(_read_policy)()
        if self.active is not active or epoch != self._epoch or not self._caught_up or not current['summary']:
            return
        if current.get('final_only', True):
            self.pending = [event for event in self.pending if event['id'] in self.final_entries]
        missing = [e for e in self.pending if e['id'] not in self.preparations]
        for event in missing:
            source = None
            try:
                source, descriptor = await summaries.resolve_timeline_summary(event['id'])
            except ValueError as error:
                descriptor = {'error': str(error), **({'ui_message': error.ui_message} if getattr(error, 'ui_message', None) else {})}
            if self.active is not active or epoch != self._epoch or not self._caught_up:
                return
            self.preparations[event['id']] = {
                'descriptor': descriptor, 'source': source, 'handle': None, 'watcher': None,
            }
        running = sum(1 for p in self.preparations.values() if p['handle'] is not None
                      and p['handle'].state == 'running' and not p['descriptor'].get('saved'))
        for event in self.pending:
            preparation = self.preparations.get(event['id'])
            if preparation is None:
                continue
            descriptor = preparation['descriptor']
            if preparation['handle'] is not None or not descriptor.get('eligible') or descriptor.get('error'):
                continue
            if running >= 2 and not descriptor.get('saved'):
                continue
            try:
                handle = await summaries.acquire_summary(
                    {'event_id': event['id']}, _temporary_source=preparation['source'],
                    usage_origin='timeline',
                )
                handle.claim()
                if self.active is not active or epoch != self._epoch or not self._caught_up:
                    await summaries.release_summary(handle.id)
                    return
                preparation['handle'] = handle
                preparation['watcher'] = self._spawn(self._observe_summary(handle.id))
                if handle.state == 'running' and not descriptor.get('saved'):
                    running += 1
            except (ValueError, summaries.SummaryUnavailable) as error:
                descriptor['error'] = str(error)
                if getattr(error, 'ui_message', None):
                    descriptor['ui_message'] = error.ui_message

    async def _observe_summary(self, handle_id):
        async for snapshot in summaries.summary_snapshots(handle_id):
            if snapshot['state'] != 'running':
                await self.invalidate({'type': 'timeline_changed'})
                return

    async def _release_preparation(self, event_id):
        preparation = self.preparations.pop(event_id, None)
        if preparation:
            task = preparation['watcher']
            if task and task is not asyncio.current_task():
                task.cancel()
            if preparation['handle']:
                await summaries.release_summary(preparation['handle'].id)

    def _publish(self):
        self.session.pending_count = len(self.pending)
        if self.active:
            self.active.update(pending_final_ending_ids=sorted(self.finals), excluded_event_ids=sorted(self.consumed))
        self.session.listening = self.active.copy() if self.active else None
        self.session.publish_state()

    async def _offer(self):
        if self.active is None:
            return
        self._publish()
        self.session._candidate = None
        if not self._caught_up or self.session.playback is not None or not self.pending:
            return
        event = self.pending[0]
        preparation = self.preparations.get(event['id'])
        if self._summary_policy and self._summary_policy[0] and preparation is None:
            return
        descriptor = preparation['descriptor'] if preparation else {}
        identity = ({'sourceKey': descriptor['source_key'], 'sourceHash': descriptor['source_hash'], 'variant': 'summary'}
                    if descriptor.get('eligible') and not descriptor.get('error') else None)
        await self.session.offer_candidate({'activation_id': self.active['activation_id'], 'event_id': event['id'],
                                            'identity': identity, 'text': event['data']['text'],
                                            'occurred_at': event['occurred_at'],
                                            **({'final_ending_id': self.final_entries[event['id']]['ending_id'],
                                                'final_occurred_at': self.final_entries[event['id']]['speech_time']}
                                               if event['id'] in self.final_entries else {})})

    async def select(self, event_id: int, reading_id: str, generation: int, *, silence_reset=False):
        completed = False
        try:
            await self._select(event_id, reading_id, generation, silence_reset=silence_reset)
            completed = True
        finally:
            # A candidate reserves native playback before storage revalidation.
            # Every unowned selection must release that exact reservation.
            if not self.session._matches({'reading_id': reading_id, 'generation': generation}):
                self.session.reject_selection(reading_id, generation)
                if completed and self._caught_up:
                    await self._offer()

    async def _select(self, event_id: int, reading_id: str, generation: int, *, silence_reset=False):
        async with self._lock:
            if (not self.active or not self._caught_up or not self.pending or self.pending[0]['id'] != event_id
                    or self.session.playback is not None or generation <= self.session.generation):
                return
            active = self.active
            epoch = self._epoch
            policy = await sync_to_async(_read_policy)()
            if self.active is not active:
                return
            entry = policy['entry']
            if entry is None:
                await self._stop('tts_unavailable', ui_message_text('Text-to-speech is unavailable. Check the active configuration.', 'backend.tts.configurationUnavailable'))
                return
            final = self.final_entries.get(event_id)
            if final:
                self._final_dirty.add(final['ending_id'])
                await self._resolve_finals(active, epoch)
            if self.active is not active or epoch != self._epoch:
                return
            if not self.pending or self.pending[0]['id'] != event_id:
                await self._offer()
                return
            event = self.pending[0]
            table = await sync_to_async(build_sessions_table)({event['session_id']})
            members, _, projects = await sync_to_async(_scope)(active['scope'])
            if self.active is not active or not self._caught_up or epoch != self._epoch:
                return
            if not members:
                await self._stop('scope_unavailable', ui_message_text('Timeline reading stopped. Choose another timeline.', 'backend.tts.scopeUnavailable'))
                return
            visibility = table.get(event['session_id'], {})
            if (event['project_id'] not in members or visibility.get('hidden')
                    or (not active['include_muted'] and visibility.get('mute_on_user_turn'))):
                self.pending.pop(0)
                self.consumed.add(event_id)
                if event_id in self.final_entries:
                    self._drop_final(self.final_entries[event_id]['ending_id'])
                await self._release_preparation(event_id)
                # Reconcile the remaining queue before offering its next event.
                await self.reconnect()
                return
            await self._reconcile_policy(policy)
            if (self.active is not active or epoch != self._epoch or not self._caught_up
                    or not self.pending or self.pending[0]['id'] != event_id):
                return
            names = []
            project = projects.get(event['project_id'])
            spoken = _spoken_project(project, projects) if project else None
            spoken_id = spoken.id if spoken else event['project_id']
            if silence_reset or self.context is None or self.context[0] != spoken_id:
                names.append(_project_name(spoken) if spoken else event['project_id'])
            if silence_reset or self.context is None or self.context[1] != event['session_id']:
                names.append(table.get(event['session_id'], {}).get('title') or str(event['session_id'])[:8])
            speech = {'announcement': ' '.join(f'{name}.' for name in names), 'cache': True}
            if entry['kind'] == 'script':
                speech.update(kind='script', script_id=entry['id'])
            else:
                speech.update({field: entry.get(field) for field in SPEECH_FIELDS})
            request = {'kind': 'text', 'speech': {**speech, 'text': event['data']['text']}}
            metadata = {'identity': None, 'text': event['data']['text'], 'source': 'message', 'config_id': entry['id']}
            preparation = self.preparations.get(event_id) if policy['summary'] else None
            descriptor = preparation['descriptor'] if preparation else {}
            if descriptor.get('error'):
                self.pending.pop(0)
                self.consumed.add(event_id)
                if event_id in self.final_entries:
                    self._drop_final(self.final_entries[event_id]['ending_id'])
                await self._release_preparation(event_id)
                self.context = None
                await self.session.fail('summary_unavailable', descriptor['error'], reading_id=reading_id, generation=generation,
                                        **({'ui_message': descriptor['ui_message']} if descriptor.get('ui_message') else {}))
                await self._offer()
                return
            if descriptor.get('eligible'):
                saved = descriptor.get('saved')
                handle = preparation['handle']
                if saved:
                    reference = {'saved_id': saved['id'], 'result_hash': saved['result_hash']}
                elif handle:
                    reference = {key: descriptor[key] for key in ('source_key', 'source_hash', 'policy_key')}
                    reference.update(source={'event_id': event_id}, handle_id=handle.id)
                else:
                    return
                request = {'kind': 'summary', 'speech': speech, 'summary': reference}
                metadata.update(source='summary', identity={'sourceKey': descriptor['source_key'],
                                'sourceHash': descriptor['source_hash'], 'variant': 'summary'})
            command = {'reading_id': reading_id, 'generation': generation}
            if await self.session.select_reading(command, request, metadata, policy=policy):
                self.pending.pop(0)
                self.consumed.add(event_id)
                final = self.final_entries.get(event_id)
                if final:
                    self._drop_final(final['ending_id'])
                self.selected = {'event': event, 'reading_id': reading_id, 'generation': generation,
                                 'spoken_project_id': spoken_id, 'final': bool(final)}
                self._publish()

    async def started(self, reading: dict | str):
        if isinstance(reading, str):
            reading = {'reading_id': reading, 'automatic': bool(
                self.selected and reading == self.selected['reading_id'],
            )}
        if not reading.get('automatic'):
            self.context = None
            return
        if self.selected and reading['reading_id'] == self.selected['reading_id']:
            event = self.selected['event']
            occurred = timezone.datetime.fromisoformat(event['occurred_at'])
            if not self.selected.get('final'):
                self.watermark = max(self.watermark, occurred) if self.watermark else occurred
            self.context = (self.selected.get('spoken_project_id', event['project_id']), event['session_id'])

    async def finished(self, reading: dict | str, outcome: str):
        if isinstance(reading, str):
            reading = {'reading_id': reading}
        if self.selected and reading['reading_id'] == self.selected['reading_id']:
            event_id = self.selected['event']['id']
            self.selected = None
            await self._release_preparation(event_id)
            if outcome != 'complete':
                self.context = None
        if self.active:
            await self.invalidate({'type': 'timeline_changed'})

    async def set_muted(self, value: bool):
        if self.active:
            self.active['include_muted'] = value
            self._publish()
            await self.invalidate({'type': 'session_updated'})
            # Direct API is awaitable for callers requiring reconciled pending state.
            await self.refresh()

    async def _stop(self, code, message):
        epoch = self._epoch
        activation_id = self.active['activation_id'] if self.active else None
        await self.disable()
        # Reader cleanup can yield to a newly reserved listening activation.
        if self._epoch == epoch and self.active is None:
            await self.session.fail(code, message, activation_id=activation_id)

    async def disable(self):
        self.active = None
        for _, task in self._selections.values():
            if task is not asyncio.current_task():
                task.cancel()
        self._selections.clear()
        self.pending = []
        self.session._candidate = None
        if self.session.playback and self.session.playback.get('automatic'):
            async with self.session._lock:
                self.session._cancel_reading()
        self.selected = None
        for event_id in list(self.preparations):
            await self._release_preparation(event_id)
        self._publish()

    async def close(self):
        await self.disable()
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None
        if self._channel:
            await get_channel_layer().group_discard('updates', self._channel)
            self._channel = None
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
