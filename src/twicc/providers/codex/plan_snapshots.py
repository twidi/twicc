"""Persist confirmed Codex plan updates independently of the rollout parser."""

from datetime import UTC, datetime

from asgiref.sync import sync_to_async
from django.db import transaction
from openai_codex.generated.v2_all import TurnPlanUpdatedNotification

from twicc.core.enums import Provider
from twicc.core.models import Session
from twicc.providers.db_writer import broadcast_session_updated, run_under_db_write_lock
from twicc.providers.task_snapshots import SDK_PLAN_SOURCE, select_tasks_snapshot


# The first SDK update can precede watcher discovery. Both accesses run under
# the shared writer lock; creation transfers this short-lived buffer to tasks.
_pending_plans: dict[str, dict] = {}


@transaction.atomic
def _persist_snapshot(session_id: str, snapshot: dict) -> bool:
    session = Session.objects.filter(pk=session_id, provider=Provider.CODEX).only("tasks").first()
    if session is None:
        _pending_plans[session_id] = select_tasks_snapshot(_pending_plans.get(session_id, {}), snapshot)
        return False
    selected = select_tasks_snapshot(session.tasks, snapshot)
    if selected == session.tasks:
        return False
    session.tasks = selected
    session.save(update_fields=["tasks"])
    return True


def apply_pending_plan(session: Session) -> None:
    """Transfer an early snapshot when the watcher creates its session row."""
    snapshot = _pending_plans.get(session.id)
    if snapshot is not None:
        _persist_snapshot(session.id, snapshot)
        _pending_plans.pop(session.id, None)
        session.refresh_from_db(fields=["tasks"])


async def persist_plan_notification(payload: TurnPlanUpdatedNotification) -> None:
    """Store before broadcasting, under the same write lock as both compute paths."""
    snapshot = {
        "provider": "codex",
        "source": SDK_PLAN_SOURCE,
        "line": None,
        "updated_at": datetime.now(UTC).isoformat(),
        "turn_id": payload.turn_id,
        "explanation": payload.explanation,
        "items": [
            {"content": entry.step, "status": "in_progress" if entry.status.value == "inProgress"
             else entry.status.value}
            for entry in payload.plan
        ],
    }
    changed = await run_under_db_write_lock(lambda: sync_to_async(_persist_snapshot)(payload.thread_id, snapshot))
    if changed:
        await broadcast_session_updated(payload.thread_id)
