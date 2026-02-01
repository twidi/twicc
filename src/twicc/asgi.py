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
from twicc.agent.states import ProcessInfo
from twicc.core.models import Project

logger = logging.getLogger(__name__)


@sync_to_async
def get_project_directory(project_id: str) -> str | None:
    """Get the directory for a project from the database.

    Returns None if project not found or has no directory set.
    """
    try:
        project = Project.objects.get(id=project_id)
        return project.directory
    except Project.DoesNotExist:
        return None


async def broadcast_process_state(info: ProcessInfo) -> None:
    """Broadcast a process state change to all connected clients.

    This is the callback registered with ProcessManager to handle
    state change notifications.
    """
    channel_layer = get_channel_layer()
    message = {
        "type": "process_state",
        "session_id": info.session_id,
        "project_id": info.project_id,
        "state": info.state,  # ProcessState is StrEnum, serializes directly
    }
    if info.error is not None:
        message["error"] = info.error

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
        await self.send_json({
            "type": "active_processes",
            "processes": [
                {
                    "session_id": p.session_id,
                    "project_id": p.project_id,
                    "state": p.state,  # ProcessState is StrEnum
                }
                for p in processes
            ],
        })

    async def disconnect(self, close_code):
        """Remove from the updates group on disconnect."""
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.

        Supported message types:
        - ping: heartbeat, responds with pong
        - send_message: send a message to a Claude session
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})

        elif msg_type == "send_message":
            await self._handle_send_message(content)

    async def _handle_send_message(self, content: dict) -> None:
        """Handle send_message request from client.

        Expected content format:
        {
            "type": "send_message",
            "session_id": "claude-conv-xxx",
            "project_id": "proj-xyz",
            "text": "The message text"
        }
        """
        session_id = content.get("session_id")
        project_id = content.get("project_id")
        text = content.get("text")

        # Validate required fields
        if not session_id or not project_id or not text:
            logger.warning(
                "send_message missing required fields: session_id=%s, project_id=%s, text=%s",
                session_id, project_id, bool(text),
            )
            await self.send_json({
                "type": "error",
                "message": "send_message requires session_id, project_id, and text",
            })
            return

        # Get project directory from database
        cwd = await get_project_directory(project_id)
        if not cwd:
            logger.warning("send_message: project %s not found or has no directory", project_id)
            await self.send_json({
                "type": "error",
                "message": f"Project {project_id} not found or has no directory configured",
            })
            return

        # Send message via ProcessManager
        manager = get_process_manager()
        try:
            await manager.send_message(session_id, project_id, cwd, text)
        except RuntimeError as e:
            # Process busy or other expected errors
            logger.warning("send_message failed: %s", e)
            await self.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception as e:
            # Unexpected errors - log full traceback
            logger.exception("Unexpected error in send_message")
            await self.send_json({
                "type": "error",
                "message": f"Failed to send message: {e}",
            })

    async def broadcast(self, event):
        """Handle broadcast events by sending data to the client."""
        await self.send_json(event["data"])


websocket_urlpatterns = [
    path("ws/", UpdatesConsumer.as_asgi()),
]

# Django ASGI application for HTTP requests
django_asgi_app = get_asgi_application()

# Protocol router for HTTP and WebSocket
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": URLRouter(websocket_urlpatterns),
})
