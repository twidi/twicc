"""Select task state without discarding plans observed only on the SDK stream."""

from datetime import datetime


SDK_PLAN_SOURCE = "turn/plan/updated"


def _timestamp(snapshot: dict) -> datetime | None:
    value = snapshot.get("updated_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def select_tasks_snapshot(stored: dict, candidate: dict) -> dict:
    """Preserve SDK evidence unless the other snapshot is demonstrably newer.

    Ordinary JSONL state stays authoritative on recompute, including its empty
    reset. SDK state is not reconstructible from JSONL, so compare timestamps
    whenever either snapshot comes from that source. Missing timestamps cannot
    justify discarding SDK evidence. SDK wins ties against static extraction;
    consecutive SDK updates win ties in receipt order.
    """
    stored_sdk = stored.get("source") == SDK_PLAN_SOURCE and stored.get("provider") == "codex"
    candidate_sdk = candidate.get("source") == SDK_PLAN_SOURCE and candidate.get("provider") == "codex"
    if not stored_sdk and not candidate_sdk:
        return candidate
    stored_at, candidate_at = _timestamp(stored), _timestamp(candidate)
    if stored_at is not None and candidate_at is not None:
        if candidate_at > stored_at:
            return candidate
        if candidate_at < stored_at:
            return stored
    elif stored_sdk or not candidate_sdk:
        return stored
    return candidate if candidate_sdk else stored
