"""Publish question snapshots only after their owning transaction commits."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def publish_question_snapshot_on_commit(session_id: str, snapshot: dict) -> None:
    """Use the existing generic broadcast envelope for every durable writer."""

    def publish():
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "async_questions_updated",
                        "session_id": session_id,
                        "async_questions": snapshot,
                    },
                },
            )

    transaction.on_commit(publish, robust=True)
