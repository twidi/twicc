"""Process-local native reading ownership, command ordering and reconnect leases."""

import asyncio
import logging
import secrets
import time
from collections import deque
from uuid import uuid4

from twicc.summaries import service as summaries
from twicc.tts.native_protocol import parse_native_command
from twicc.tts.service import SpeechError, open_speech
from twicc.tts.streams import get_stream, release_stream, retain_stream

logger = logging.getLogger(__name__)
DISCONNECT_TTL = 120.0
HEARTBEAT_TTL = 75.0


class SessionLost(Exception):
    """A session is unknown, expired, or belongs to different credentials."""


class NativeSession:
    """One native installation process; no durable playback state."""

    def __init__(self, registry, client_id: str, auth_binding: tuple):
        self.registry = registry
        self.id, self.secret = str(uuid4()), secrets.token_urlsafe(32)
        self.client_id, self.auth_binding = client_id, auth_binding
        self.revision, self.ack_seq, self.generation = 0, 0, 0
        self.listening, self.playback, self.error = None, None, None
        self.pending_count = 0
        self.configuration_available = None
        self.coordinator = None
        self.owned_audio: set[str] = set()
        self.released_audio: set[str] = set()
        self.preparation = None
        self._tasks = set()
        self._lock, self._commands = asyncio.Lock(), asyncio.Lock()
        self._frames = deque(maxlen=64)
        self._wake = asyncio.Event()
        self.connection_id = None
        self.closed = False
        self.disconnected_at = None
        self.last_heartbeat = registry.clock()
        self._expiry_timer = None
        self._heartbeat_timer = None
        self._reconciling = False
        self._candidate = None

    def snapshot(self) -> dict:
        """Return credential-free authoritative state."""
        return {'protocol': 1, 'type': 'state', 'revision': self.revision, 'ack_seq': self.ack_seq,
                'listening': self.listening.copy() if self.listening else None,
                'pending_count': self.pending_count,
                'playback': self.playback.copy() if self.playback else None,
                'configuration_available': self.configuration_available,
                'error': self.error.copy() if self.error else None}

    def _emit(self, frame: dict):
        # Disconnected clients reconcile against authority, not an event history.
        if self.disconnected_at is not None:
            return
        if frame['type'] == 'state':
            self._frames = deque((f for f in self._frames if f['type'] != 'state'), maxlen=64)
        self._frames.append({'protocol': 1, **frame})
        self._wake.set()

    def publish_state(self) -> None:
        self.revision += 1
        self._emit(self.snapshot())

    async def next_frame(self, connection_id: str) -> dict | None:
        while connection_id == self.connection_id:
            if self._frames:
                return self._frames.popleft()
            if self.closed:
                return None
            self._wake.clear()
            await self._wake.wait()
        return None

    def attach(self) -> str:
        """Replace the transport identity; stale disconnects cannot detach it."""
        self.connection_id = str(uuid4())
        self.disconnected_at = None
        self._frames.clear()
        if self._expiry_timer:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        self.heartbeat()
        self._wake.set()
        return self.connection_id

    def heartbeat(self) -> None:
        self.last_heartbeat = self.registry.clock()
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
        self._heartbeat_timer = asyncio.get_running_loop().call_later(HEARTBEAT_TTL, self._heartbeat_expired)

    def _heartbeat_expired(self):
        if not self.closed:
            self.disconnect()

    def disconnect(self, connection_id: str | None = None) -> None:
        if self.closed or (connection_id is not None and connection_id != self.connection_id):
            return
        if self.disconnected_at is not None:
            return
        self.disconnected_at = self.registry.clock()
        self.connection_id = None
        self._frames.clear()
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None
        self._expiry_timer = asyncio.get_running_loop().call_later(
            DISCONNECT_TTL, lambda: asyncio.create_task(self.registry.expire()))
        self._wake.set()

    def _matches(self, command):
        return (self.playback is not None and self.playback['reading_id'] == command['reading_id']
                and self.playback['generation'] == command['generation'])

    def _cancel_reading(self):
        """Invalidate ownership before cancellation can yield a late resource."""
        old = self.playback
        self.playback = None
        task, self.preparation = self.preparation, None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        if old and old.get('audio_id'):
            self._release(old['audio_id'])
        return old

    def _release(self, audio_id):
        if audio_id in self.owned_audio:
            self.owned_audio.remove(audio_id)
            # Keep a bounded idempotency set. Foreign IDs never enter it.
            if len(self.released_audio) >= 64:
                self.released_audio.pop()
            self.released_audio.add(audio_id)
            release_stream(audio_id)

    async def release_audio(self, audio_id: str) -> bool:
        async with self._lock:
            if audio_id not in self.owned_audio and audio_id not in self.released_audio:
                return False
            self._release(audio_id)
            return True

    def _reserve(self, command, request, metadata, automatic=False):
        if command['generation'] <= self.generation:
            return False
        if automatic and self.playback is not None:
            return False
        self._cancel_reading()
        self.generation = command['generation']
        self._candidate = None
        self.error = None
        reading = {'reading_id': command['reading_id'], 'generation': command['generation'],
                   'audio_id': None, 'phase': 'loading', 'started': False,
                   'automatic': automatic, **metadata}
        self.playback = reading
        task = asyncio.create_task(self._prepare(reading, request), name='native-tts-prepare')
        self.preparation = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def select_reading(self, command: dict, request: dict, metadata: dict) -> bool:
        """Task 4 boundary: reserve automatic speech only when no reading exists."""
        async with self._lock:
            accepted = not self.closed and self._reserve(command, request, metadata, automatic=True)
            if accepted:
                self.publish_state()
            return accepted

    async def offer_candidate(self, candidate: dict) -> None:
        async with self._lock:
            if not self.closed and self.playback is None:
                self._candidate = candidate
                if not self._reconciling:
                    self._emit({'type': 'candidate', **candidate})

    async def fail(self, code: str, message: str, **extra) -> None:
        async with self._lock:
            self.error = {'code': code, 'message': message, **extra}
            self._emit({'type': 'error', **self.error})
            self.publish_state()

    async def _prepare(self, reading, request):
        reader = None
        opened = None
        failed = False
        try:
            speech = dict(request['speech'])
            if request['kind'] == 'summary':
                reader = await summaries.retain_summary_for_speech(request['summary'])
                # The shared opener acquires a second reader before this one closes.
                for key in ('text', 'config_id', 'saved_summary_id', 'summary_result_hash'):
                    speech.pop(key, None)
                speech['summary_id'] = reader.id
            opened = await open_speech(speech)
            async with self._lock:
                if self.closed or self.playback is not reading:
                    release_stream(opened.audio_id)
                    return
                self.owned_audio.add(opened.audio_id)
                retain_stream(opened.audio_id)
                reading.update(audio_id=opened.audio_id, content_type=opened.content_type)
                self._emit(self._audio_frame())
                self.publish_state()
        except asyncio.CancelledError:
            if opened is not None:
                release_stream(opened.audio_id)
            raise
        except Exception as error:
            if isinstance(error, SpeechError):
                details = {'code': 'speech_failed', 'message': error.message, 'status': error.status, **error.extra}
            elif isinstance(error, summaries.SummaryChanged):
                details = {'code': 'summary_changed', 'message': str(error), 'status': 409,
                           'summary_changed': True, 'saved_id': error.saved_id}
            elif isinstance(error, summaries.SummaryGone):
                details = {'code': 'summary_unavailable', 'message': str(error), 'status': 410}
            elif isinstance(error, (ValueError, summaries.SummaryUnavailable)):
                details = {'code': 'summary_unavailable', 'message': str(error), 'status': 400}
            else:
                logger.exception('Native speech preparation failed')
                details = {'code': 'speech_failed', 'message': 'Text-to-speech failed', 'status': 500}
            async with self._lock:
                if self.playback is reading and not self.closed:
                    self._cancel_reading()
                    failed = True
                    self.error = {**details, 'reading_id': reading['reading_id'], 'generation': reading['generation']}
                    self._emit({'type': 'error', **self.error})
                    self.publish_state()
            if failed and self.coordinator:
                await self.coordinator.finished(reading.copy(), 'failed')
        finally:
            if reader is not None:
                await reader.aclose()

    def _audio_frame(self):
        return {'type': 'audio', **self.playback}

    async def apply(self, command: dict, *, connection_id: str | None = None) -> None:
        command = parse_native_command(command)
        if command['type'] in ('open', 'resume'):
            raise ValueError('Handshake commands belong to the transport')
        async with self._commands:
            async with self._lock:
                if self.closed or (connection_id is not None and connection_id != self.connection_id):
                    raise SessionLost
                if command['seq'] <= self.ack_seq:
                    self._emit(self.snapshot())
                    return
                kind = command['type']
                self.ack_seq = command['seq']
                old = None
                started = None
                if kind == 'manual':
                    request = command['request']
                    metadata = {key: request.get(key) for key in ('identity', 'text', 'source', 'config_id')}
                    if request['kind'] == 'summary':
                        metadata['source'] = 'summary'
                    if command['generation'] > self.generation:
                        old = self.playback.copy() if self.playback else None
                    self._reserve(command, request, metadata)
                elif kind in ('skip', 'finished') and self._matches(command):
                    old = self._cancel_reading()
                elif kind == 'started' and self._matches(command) and not self.playback['started']:
                    self.playback.update(started=True, phase='playing')
                    started = self.playback.copy()
                elif kind == 'stop_all':
                    self._cancel_reading()
                    self.listening = None
                    self._candidate = None
                    self.pending_count = 0
                elif kind == 'disable' and self.listening and self.listening['activation_id'] == command['activation_id']:
                    self.listening = None
                    self._candidate = None
                    self.pending_count = 0
                    if self.playback and self.playback['automatic']:
                        old = self._cancel_reading()
                elif kind == 'ping':
                    self.heartbeat()
                    self._emit({'type': 'pong', 'ack_seq': self.ack_seq})
                self.publish_state()
            # Coordinator methods may reserve speech; never call them under _lock.
            if self.coordinator:
                if started:
                    await self.coordinator.started(started)
                if old:
                    await self.coordinator.finished(old, command.get('outcome', 'aborted'))
                if kind in ('listen', 'set_muted', 'disable', 'select', 'stop_all'):
                    await self.coordinator.command(command)
            if kind == 'stop_all':
                await self.close()

    async def reconcile(
        self, commands: list[dict], current: dict | None, *, connection_id: str | None = None,
    ) -> None:
        """Apply offline intent before exposing audio or timeline candidates."""
        if self.disconnected_at is not None:
            self.attach()
        self._reconciling = True
        try:
            for command in commands:
                await self.apply(command, connection_id=connection_id)
                if self.closed:
                    return
        finally:
            self._reconciling = False
        started = None
        expired = None
        async with self._lock:
            if connection_id is not None and connection_id != self.connection_id:
                raise SessionLost
            self._frames.clear()
            if self.playback and self.playback.get('audio_id'):
                audio_id = self.playback['audio_id']
                if get_stream(audio_id) is None:
                    old = self._cancel_reading()
                    expired = old
                    self.error = {'code': 'audio_expired', 'message': 'The audio expired before playback',
                                  'reading_id': old['reading_id'], 'generation': old['generation']}
                    self._emit({'type': 'error', **self.error})
                elif current and self._matches(current) and current.get('audio_id') == audio_id:
                    if current.get('started') and not self.playback['started']:
                        self.playback.update(started=True, phase='playing')
                        started = self.playback.copy()
                else:
                    self._emit(self._audio_frame())
            self.publish_state()
            if (self._candidate and self.playback is None and self.listening
                    and self._candidate.get('activation_id') == self.listening['activation_id']):
                self._emit({'type': 'candidate', **self._candidate})
        if expired and self.coordinator:
            await self.coordinator.finished(expired, 'failed')
        if started and self.coordinator:
            await self.coordinator.started(started)

    async def close(self) -> None:
        async with self._lock:
            if self.closed:
                return
            self.closed = True
            self._cancel_reading()
            self.listening = None
            self._candidate = None
            self.pending_count = 0
            for audio_id in list(self.owned_audio):
                self._release(audio_id)
            for timer in (self._expiry_timer, self._heartbeat_timer):
                if timer:
                    timer.cancel()
            self.registry.sessions.pop(self.id, None)
            self._wake.set()
        if self.coordinator:
            await self.coordinator.close()
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class NativeSessionRegistry:
    """One ASGI-process registry; no second lifespan or background service."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sessions: dict[str, NativeSession] = {}

    def get(self, session_id: str) -> NativeSession | None:
        return self.sessions.get(session_id)

    def open(self, client_id: str, auth_binding: tuple) -> NativeSession:
        session = NativeSession(self, client_id, auth_binding)
        self.sessions[session.id] = session
        return session

    def resume(self, session_id: str, secret: str, auth_binding: tuple) -> NativeSession:
        session = self.get(session_id)
        if (session is None or session.closed or session.auth_binding != auth_binding
                or not isinstance(secret, str) or not secrets.compare_digest(session.secret.encode(), secret.encode())
                or (session.disconnected_at is not None and self.clock() - session.disconnected_at >= DISCONNECT_TTL)):
            raise SessionLost
        return session

    async def expire(self) -> None:
        now = self.clock()
        for session in list(self.sessions.values()):
            if session.disconnected_at is not None and now - session.disconnected_at >= DISCONNECT_TTL:
                await session.close()
            elif session.disconnected_at is None and now - session.last_heartbeat >= HEARTBEAT_TTL:
                session.disconnect()

    async def shutdown(self) -> None:
        await asyncio.gather(*(session.close() for session in list(self.sessions.values())))


registry = NativeSessionRegistry()
