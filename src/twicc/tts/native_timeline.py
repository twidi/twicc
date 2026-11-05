"""Selection policy for one native session's automatic timeline readings.

The native session owns playback and resources. This listener owns only its
activation, feed cursor, pending snapshots, and independent summary references.
"""
import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.utils import timezone

from twicc.core.models import Project
from twicc.core.services.timeline_feed import (
    FeedRequest, build_sessions_table, fetch_click_boundary, fetch_feed, format_occurred_at,
)
from twicc.projects import expand_project_ids_with_worktrees, project_scope_ids
from twicc.summaries import service as summaries
from twicc.synced_settings import read_synced_settings
from twicc.tts.config import SPEECH_FIELDS, openrouter_api_key, read_tts_config, script_config_validated
from twicc.workspaces import read_workspaces

logger = logging.getLogger(__name__)
INVALIDATIONS = frozenset({
    'timeline_changed', 'project_added', 'project_updated', 'project_deleted',
    'workspaces_updated', 'session_updated', 'session_removed', 'synced_settings_updated',
    'tts_config_updated', 'summary_saved', 'resync_required',
})


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
            'summary_model': settings.get('summaryModel')}


class TimelineListener:
    """One activation and one offer. It opens no pending audio."""

    def __init__(self, session, *, clock=timezone.now):
        self.session, self.clock = session, clock
        self.active = None
        self.pending, self.seen = [], set()
        self.boundaries, self.shown = {}, {}
        self.cursor, self.watermark, self.context = 0, None, None
        self.selected = None
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
            self._activate(command)
            self._spawn(self._initialize(self.active))
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
            task = self._spawn(self.select(command['event_id'], *key))
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
                await self._release_preparation(event_id)
                await self._offer()

    async def reconnect(self):
        """Fence stale offers before the native session publishes resumed state."""
        if self.active:
            self._caught_up = False
            self._epoch += 1
            self.session._candidate = None
            await self.invalidate({'type': 'timeline_changed'})

    def _activate(self, activation):
        self.active = {key: activation[key] for key in ('activation_id', 'page_id', 'scope', 'boundary', 'include_muted')}
        self.session.listening = self.active.copy()
        self.session.error = None
        self.pending, self.seen, self.boundaries, self.shown = [], set(), {}, {}
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
                await self._stop('scope_unavailable', 'The listening scope is empty or no longer exists.')
                return
            self.boundaries = {member: active['boundary'] for member in members}
            active['label'] = label
            body = await sync_to_async(fetch_click_boundary)(self._request(), active['boundary'])
            if self.active is not active:
                return
            self.cursor = body['latest_id'] or 0
            self._consume(body)
            await self._reconcile_policy()
        # Include arrivals committed during bootstrap, before any offer.
        if self.active is active:
            await self.refresh()

    def _request(self):
        projects = () if self.active['scope']['kind'] == 'global' else tuple(self.boundaries)
        # Always observe mute transitions; admission retains the activation choice.
        return FeedRequest(projects, 100, None, self.cursor, include_muted=True)

    async def _subscribe(self):
        async with self._subscription_lock:
            if self._watcher is not None:
                return
            layer = get_channel_layer()
            self._channel = await layer.new_channel('native.tts.')
            await layer.group_add('updates', self._channel)
            self._watcher = asyncio.create_task(self._watch(layer), name='native-timeline-updates')

    async def _watch(self, layer):
        while True:
            message = await layer.receive(self._channel)
            await self.invalidate(message.get('data', {}))

    async def invalidate(self, payload):
        """The updates group is an invalidation source, never trusted event content."""
        if not self.active or not isinstance(payload, dict) or payload.get('type') not in INVALIDATIONS:
            return
        if payload['type'] == 'summary_saved':
            for event_id, preparation in self.preparations.items():
                descriptor = preparation['descriptor']
                if (descriptor.get('source_key') == payload.get('source_key')
                        and descriptor.get('source_hash') == payload.get('source_hash')):
                    self._invalidated_summaries.add(event_id)
        self._dirty = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._spawn(self._refresh_loop())

    async def _refresh_loop(self):
        while self._dirty and self.active:
            self._dirty = False
            await self.refresh()

    def _consume(self, body):
        events = body['events']
        # Show boundaries apply to the whole batch, including older messages first.
        for event in events:
            if event['kind'] == 'hidden.ended':
                self.shown[event['session_id']] = max(event['id'], self.shown.get(event['session_id'], 0))
        for event in events:
            if event['id'] in self.seen:
                continue
            self.seen.add(event['id'])
            session = body['sessions'].get(event['session_id'], {})
            boundary = self.boundaries.get(event['project_id'])
            if (event['kind'] != 'assistant_message.sent' or not isinstance(event['data'].get('text'), str)
                    or not event['data']['text'].strip() or boundary is None or event['occurred_at'] <= boundary
                    or event['id'] <= self.shown.get(event['session_id'], 0)
                    or session.get('hidden') or (not self.active['include_muted'] and session.get('mute_on_user_turn'))):
                continue
            occurred = timezone.datetime.fromisoformat(event['occurred_at'])
            if self.watermark is not None and occurred < self.watermark - timedelta(seconds=5):
                continue
            self.pending.append(event)
        self.pending.sort(key=lambda e: (e['occurred_at'], e['id']))

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
                await self._stop('scope_unavailable', 'The listening scope is empty or no longer exists.')
                return
            now = format_occurred_at(self.clock())
            self.boundaries = {member: self.boundaries.get(member, now) for member in members}
            active['label'] = label
            body = await sync_to_async(fetch_feed)(self._request())
            if self.active is not active:
                return
            if body['truncated']:
                await self._stop('catchup_incomplete', 'Timeline catch-up is incomplete. Automatic listening stopped.')
                return
            self.cursor = max(self.cursor, body['latest_id'] or 0)
            self._consume(body)
            table = await sync_to_async(build_sessions_table)({e['session_id'] for e in self.pending})
            if self.active is not active:
                return
            self.pending = [e for e in self.pending if e['project_id'] in self.boundaries
                            and not table.get(e['session_id'], {}).get('hidden')
                            and e['id'] > self.shown.get(e['session_id'], 0)
                            and (active['include_muted'] or not table.get(e['session_id'], {}).get('mute_on_user_turn'))]
            await self._reconcile_policy()
            if self.active is active and epoch == self._epoch:
                self._caught_up = True
                await self._offer()

    async def _reconcile_policy(self, policy=None):
        active = self.active
        if policy is None:
            policy = await sync_to_async(_read_policy)()
        if self.active is not active:
            return
        self.session.configuration_available = policy['entry'] is not None
        if policy['entry'] is None:
            await self._stop('tts_unavailable', 'Text-to-speech is unavailable. Check the active configuration.')
            return
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
        missing = [e for e in self.pending if e['id'] not in self.preparations]
        for event in missing:
            source = None
            try:
                source, descriptor = await summaries.resolve_timeline_summary(event['id'])
            except ValueError as error:
                descriptor = {'error': str(error)}
            if self.active is not active:
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
                )
                handle.claim()
                if self.active is not active:
                    await summaries.release_summary(handle.id)
                    return
                preparation['handle'] = handle
                preparation['watcher'] = self._spawn(self._observe_summary(handle.id))
                if handle.state == 'running' and not descriptor.get('saved'):
                    running += 1
            except (ValueError, summaries.SummaryUnavailable) as error:
                descriptor['error'] = str(error)

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
                                            'identity': identity, 'text': event['data']['text']})

    async def select(self, event_id: int, reading_id: str, generation: int):
        async with self._lock:
            if (not self.active or not self._caught_up or not self.pending or self.pending[0]['id'] != event_id
                    or self.session.playback is not None or generation <= self.session.generation):
                return
            active = self.active
            policy = await sync_to_async(_read_policy)()
            if self.active is not active:
                return
            entry = policy['entry']
            if entry is None:
                await self._stop('tts_unavailable', 'Text-to-speech is unavailable. Check the active configuration.')
                return
            await self._reconcile_policy(policy)
            if self.active is not active:
                return
            event = self.pending[0]
            table = await sync_to_async(build_sessions_table)({event['session_id']})
            _, _, projects = await sync_to_async(_scope)(active['scope'])
            if self.active is not active:
                return
            names = []
            if self.context is None or self.context[0] != event['project_id']:
                project = projects.get(event['project_id'])
                names.append(_project_name(project) if project else event['project_id'])
            if self.context is None or self.context[1] != event['session_id']:
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
                await self._release_preparation(event_id)
                self.context = None
                await self.session.fail('summary_unavailable', descriptor['error'])
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
                self.selected = {'event': event, 'reading_id': reading_id, 'generation': generation}
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
            self.watermark = max(self.watermark, occurred) if self.watermark else occurred
            self.context = (event['project_id'], event['session_id'])

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
        await self.disable()
        await self.session.fail(code, message)

    async def disable(self):
        self.active = None
        for _, task in self._selections.values():
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
