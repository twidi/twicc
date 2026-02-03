"""
ASGI application configuration with WebSocket routing.

Provides HTTP and WebSocket protocol routing, with the UpdatesConsumer
handling real-time updates on the /ws/ endpoint. Also handles agent-related
messages for sending messages to Claude sessions.
"""

import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

from twicc.agent.manager import get_process_manager
from twicc.agent.states import ProcessInfo, serialize_process_info

logger = logging.getLogger(__name__)


@sync_to_async
def get_project_directory(project_id: str) -> str | None:
    """Get the directory for a project from the database.

    Returns None if project not found or has no directory set.
    """
    from twicc.core.models import Project

    try:
        project = Project.objects.get(id=project_id)
        return project.directory
    except Project.DoesNotExist:
        return None


@sync_to_async
def session_exists(session_id: str) -> bool:
    """Check if a session exists in the database.

    Returns True if the session exists, False otherwise.
    """
    from twicc.core.models import Session

    return Session.objects.filter(id=session_id).exists()


async def broadcast_process_state(info: ProcessInfo) -> None:
    """Broadcast a process state change to all connected clients.

    This is the callback registered with ProcessManager to handle
    state change notifications.
    """
    channel_layer = get_channel_layer()
    message = serialize_process_info(info)
    message["type"] = "process_state"

    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


class UpdatesConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for broadcasting real-time updates.

    All connected clients join the "updates" group and receive broadcasts
    about project, session, session item changes, and process state changes.

    Handles incoming messages:
    - ping: heartbeat, responds with pong
    - send_message: send a message to a Claude session
    """

    async def connect(self):
        """Accept connection, add to updates group, and send active processes."""
        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()

        # Set up broadcast callback on ProcessManager (idempotent, safe to call multiple times)
        manager = get_process_manager()
        manager.set_broadcast_callback(broadcast_process_state)

        # Send current active processes to the connecting client
        processes = manager.get_active_processes()
        await self.send_json(
            {
                "type": "active_processes",
                "processes": [serialize_process_info(p) for p in processes],
            }
        )

    async def disconnect(self, close_code):
        """Remove from the updates group on disconnect."""
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.

        Supported message types:
        - ping: heartbeat, responds with pong
        - send_message: send a message to a Claude session (creates new or resumes existing)
        - kill_process: kill a running Claude process
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})

        elif msg_type == "send_message":
            await self._handle_send_message(content)

        elif msg_type == "kill_process":
            await self._handle_kill_process(content)

    async def _handle_send_message(self, content: dict) -> None:
        """Handle send_message request from client.

        Expected content format:
        {
            "type": "send_message",
            "session_id": "claude-conv-xxx",
            "project_id": "proj-xyz",
            "text": "The message text",
            "title": "Optional session title"  // Only for new sessions
        }

        This handles both new sessions and existing sessions:
        - If session exists in database: resume the session
        - If session doesn't exist: create a new session with the provided session_id

        The optional title field is only used for new sessions (drafts becoming real).
        If provided, it will be stored as a pending title and written to JSONL
        when the process becomes safe.
        """
        session_id = content.get("session_id")
        project_id = content.get("project_id")
        text = content.get("text")
        title = content.get("title")  # Optional, only for new sessions

        # Validate required fields
        if not session_id or not project_id or not text:
            logger.warning(
                "send_message missing required fields: session_id=%s, project_id=%s, text=%s",
                session_id,
                project_id,
                bool(text),
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": "send_message requires session_id, project_id, and text",
                }
            )
            return

        # Validate title if provided
        if title is not None:
            from twicc.titles import validate_title

            validated_title, title_error = validate_title(title)
            if title_error:
                logger.warning(
                    "send_message: invalid title for session %s: %s",
                    session_id,
                    title_error,
                )
                await self.send_json(
                    {
                        "type": "invalid_title",
                        "session_id": session_id,
                        "title": title,
                        "error": title_error,
                    }
                )
                return
            title = validated_title

        # Get project directory from database
        cwd = await get_project_directory(project_id)
        if not cwd:
            logger.warning(
                "send_message: project %s not found or has no directory", project_id
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Project {project_id} not found or has no directory configured",
                }
            )
            return

        # Check if session exists to determine whether to create new or resume
        exists = await session_exists(session_id)

        manager = get_process_manager()
        try:
            if exists:
                # Session exists: send message to it
                await manager.send_to_session(session_id, project_id, cwd, text)
            else:
                # Session doesn't exist: create new with client-provided ID
                # Store title as pending if provided (will be written when process is safe)
                if title:
                    from twicc.titles import set_pending_title

                    set_pending_title(session_id, title)

                await manager.create_session(session_id, project_id, cwd, text)
        except RuntimeError as e:
            # Process busy or other expected errors
            logger.warning("send_message failed: %s", e)
            await self.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception as e:
            # Unexpected errors - log full traceback
            logger.exception("Unexpected error in send_message")
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Failed to send message: {e}",
                }
            )

    async def _handle_kill_process(self, content: dict) -> None:
        """Handle kill_process request from client.

        Expected content format:
        {
            "type": "kill_process",
            "session_id": "claude-conv-xxx"
        }

        Only processes in STARTING or ASSISTANT_TURN state can be killed.
        The state change to DEAD will be broadcast via process_state message.
        """
        session_id = content.get("session_id")

        if not session_id:
            logger.warning("kill_process missing session_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "kill_process requires session_id",
                }
            )
            return

        manager = get_process_manager()
        killed = await manager.kill_process(session_id, reason="manual")

        if not killed:
            # Process not found or not in killable state - not an error, just log
            logger.debug(
                "kill_process: session %s not killed (not found or not active)",
                session_id,
            )

    async def broadcast(self, event):
        """Handle broadcast events by sending data to the client."""
        await self.send_json(event["data"])


websocket_urlpatterns = [
    path("ws/", UpdatesConsumer.as_asgi()),
]

# Django ASGI application for HTTP requests
django_asgi_app = get_asgi_application()

# Protocol router for HTTP and WebSocket
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
