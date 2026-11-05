"""Authenticated native control WebSocket, independent of the UI socket."""

import asyncio
import logging
from contextlib import suppress
from importlib import import_module

import orjson
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from twicc.auth.access import scope_allowed
from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint
from twicc.tts.native_protocol import MAX_COMMAND_BYTES, parse_native_command
from twicc.tts.native_sessions import SessionLost, registry

logger = logging.getLogger(__name__)
INVALID_COMMAND_MESSAGE = 'Speech could not start. Reload the page and try again.'


async def authorize_scope(scope) -> tuple | None:
    """Reload credentials from storage. Cached handshake fields cannot grant access."""
    if scope_remote_access_blocked(scope):
        return None
    session = scope.get('session')
    session_key = session.session_key if session is not None else None
    fresh = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
    values = await fresh.aload() if session_key else {}
    if settings.TWICC_PASSWORD_HASH and not scope_allowed(
        scope, values.get(SESSION_AUTH_KEY), values.get(SESSION_FINGERPRINT_KEY),
    ):
        return None
    return (fresh.session_key or '', compute_fingerprint(settings.TWICC_PASSWORD_HASH))


class NativeTtsConsumer(AsyncJsonWebsocketConsumer):
    """Validate authorization on every command and heartbeat."""

    async def connect(self):
        self.native_session = None
        self.connection_id = None
        self._sender = None
        self.auth_binding = await authorize_scope(self.scope)
        await self.accept()
        if self.auth_binding is None:
            await self._unauthorized()

    async def _unauthorized(self):
        if self.native_session is not None:
            await self.native_session.close()
        await self.send_json({'protocol': 1, 'type': 'error', 'code': 'unauthorized',
                              'message': 'Authentication expired. Open TwiCC and sign in again.'})
        await self.close(code=4001)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if text_data is None or len(text_data.encode('utf-8')) > MAX_COMMAND_BYTES:
            logger.warning('Native speech command is binary or exceeds the size limit')
            await self.send_json({'protocol': 1, 'type': 'error', 'code': 'invalid_command',
                                  'message': INVALID_COMMAND_MESSAGE})
            await self.close(code=1009 if text_data else 1003)
            return
        try:
            content = orjson.loads(text_data)
        except orjson.JSONDecodeError as error:
            logger.warning('Invalid native speech JSON: %s', error)
            await self.send_json({'protocol': 1, 'type': 'error', 'code': 'invalid_command', 'message': INVALID_COMMAND_MESSAGE})
            return
        await self.receive_json(content)

    async def receive_json(self, content, **kwargs):
        binding = await authorize_scope(self.scope)
        if binding is None or binding != self.auth_binding:
            await self._unauthorized()
            return
        try:
            command = parse_native_command(content)
            kind = command['type']
            if self.native_session is None:
                if kind == 'open':
                    session = registry.open(command['client_id'], binding)
                    self.native_session = session
                    self.connection_id = session.attach()
                    session.ack_seq = command['seq']
                    await self.send_json({'protocol': 1, 'type': 'opened_session', 'session_id': session.id,
                                          'resume_secret': session.secret, 'revision': session.revision,
                                          'ack_seq': session.ack_seq, 'connection_id': self.connection_id})
                elif kind == 'resume':
                    session = registry.resume(command['session_id'], command['resume_secret'], binding)
                    if command['ack_seq'] > session.ack_seq:
                        raise ValueError('Resume acknowledges an unknown command')
                    if command['seq'] <= session.ack_seq:
                        raise ValueError('Resume sequence must follow the server acknowledgement')
                    self.native_session = session
                    self.connection_id = session.attach()
                    await session.reconcile(command['commands'], command['current'], connection_id=self.connection_id)
                else:
                    raise ValueError('Open or resume a native session first')
                # No await separates ownership, acknowledgement, state, and writer installation.
                # Reconciliation callbacks may have yielded to a replacement transport.
                if self.connection_id != session.connection_id:
                    raise SessionLost
                if not session.closed:
                    if kind == 'resume':
                        if command['seq'] <= session.ack_seq:
                            raise SessionLost
                        session.ack_seq = command['seq']
                    session.publish_state()
                self._sender = asyncio.create_task(self._send_frames(), name='native-tts-websocket-writer')
            else:
                if self.connection_id != self.native_session.connection_id:
                    await self.close(code=4000)
                    return
                if kind in ('open', 'resume'):
                    raise ValueError('A native session is already attached')
                await self.native_session.apply(command, connection_id=self.connection_id)
        except SessionLost:
            await self.send_json({'protocol': 1, 'type': 'error', 'code': 'session_lost',
                                  'message': 'Speech stopped. Start playback or turn on timeline reading again.'})
            await self.close(code=4004)
        except ValueError as error:
            logger.warning('Invalid native speech command: %s', error)
            await self.send_json({'protocol': 1, 'type': 'error', 'code': 'invalid_command', 'message': INVALID_COMMAND_MESSAGE})

    async def _send_frames(self):
        while True:
            frame = await self.native_session.next_frame(self.connection_id)
            if frame is None:
                await self.close(code=1000)
                return
            await self.send_json({**frame, 'connection_id': self.connection_id})

    async def disconnect(self, close_code):
        if self.native_session is not None:
            self.native_session.disconnect(self.connection_id)
        if self._sender:
            self._sender.cancel()
            with suppress(asyncio.CancelledError):
                await self._sender
