"""Synchronous durable async-question mutations.

Runtime callers must use ``run_under_db_write_lock``. Compute apply callers
already own the DB writer. These short transactions never call the provider.
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import AsyncQuestionState, Session, SessionType
from twicc.providers.codex.async_questions import (
    QuestionFact,
    build_question_boundary,
    format_question_answers,
    reduce_question_state,
    validate_question_answers,
)


class PreparedQuestionSend(NamedTuple):
    text: str
    submission: dict | None


def _session(session_id: str, *, mutation: bool = False) -> Session | None:
    session = (
        Session.objects.filter(id=session_id)
        .only(
            "provider",
            "type",
            "parent_session_id",
            "question_widget",
        )
        .first()
    )
    if (
        session is None
        or session.provider != Provider.CODEX
        or session.type != SessionType.SESSION
        or session.parent_session_id
    ):
        if mutation:
            raise ValueError("async_questions_invalid")
        return None
    return session


def _state(session_id: str) -> dict:
    stored = AsyncQuestionState.objects.filter(session_id=session_id).values_list("state", flat=True).first()
    return stored or {"schema": 1, "revision": 0, "facts": {}, "batches": {}}


def _snapshot(session: Session | None, state: dict) -> dict:
    batches, resolutions = [], {}
    for item_id, batch in state.get("batches", {}).items():
        if batch["status"] in {"sent", "dismissed"}:
            resolutions[item_id] = {"status": batch["status"], "request_id": batch["resolved_request_id"]}
        else:
            batches.append(deepcopy(batch))
    return {
        "revision": state.get("revision", 0),
        "batches": batches,
        "resolutions": resolutions,
        "widget_enabled": session is not None and session.question_widget is not False,
    }


def _merge(session: Session, state: dict, facts: list[QuestionFact]) -> dict:
    merged = reduce_question_state(state, facts)
    if merged != state:
        AsyncQuestionState.objects.update_or_create(session=session, defaults={"state": merged})
        from twicc.providers.codex.question_snapshots import publish_question_snapshot_on_commit

        publish_question_snapshot_on_commit(session.id, _snapshot(session, merged))
    return _snapshot(session, merged)


def read_question_snapshot(session_id: str) -> dict:
    """Read settings and lifecycle in one coherent, read-only DB snapshot."""
    session = (
        Session.objects.filter(
            id=session_id, provider=Provider.CODEX, type=SessionType.SESSION, parent_session_id__isnull=True,
        )
        .select_related("async_question_state")
        .only("question_widget", "async_question_state__state")
        .first()
    )
    state = {}
    if session is not None:
        try:
            state = session.async_question_state.state
        except AsyncQuestionState.DoesNotExist:
            pass
    return _snapshot(session, state)


def read_question_send_statuses(session_id: str, request_ids: list[str]) -> dict:
    """Read native delivery bookkeeping; absence alone is not safe to retry."""
    facts = _state(session_id).get("facts", {})
    result = {}
    for request_id in request_ids:
        data = facts.get(f"send:{request_id}", {}).get("data")
        result[request_id] = (
            "missing" if data is None else "not_admitted" if data.get("not_admitted") else
            data["status"] if data.get("status") in {"accepted", "rejected"} else "uncertain"
        )
    return result


@transaction.atomic
def retire_unadmitted_question_sends(session_id: str, request_ids: list[str]) -> dict:
    """Fence absent identities under the manager gate before permitting recovery.

    A delayed original frame must not deliver after the browser edits or retries
    its recovered draft. These rejected facts survive recompute with other sends.
    """
    session = _session(session_id, mutation=True)
    state = _state(session_id)
    at = datetime.now(UTC).isoformat()
    facts = [QuestionFact(
        f"send:{request_id}", "user_submission", at, None, None, None,
        {"request_id": request_id, "client_message_id": request_id, "origin": "human",
         "origin_source": "live", "status": "rejected", "not_admitted": True,
         "boundary": {}, "text": ""},
    ) for request_id in request_ids if f"send:{request_id}" not in state.get("facts", {})]
    _merge(session, state, facts)
    return {"requests": read_question_send_statuses(session_id, request_ids),
            "snapshot": read_question_snapshot(session_id)}


@transaction.atomic
def refresh_question_widget_snapshot(session_id: str, *, previous_enabled: bool) -> None:
    """Order an effective widget change after every older hydration snapshot.

    The settings writer calls this inside its transaction and writer lease.
    Question facts remain unchanged. Reads never increment the revision.
    """
    session = _session(session_id)
    if session is None or (session.question_widget is not False) == previous_enabled:
        return
    state = _state(session_id)
    state["revision"] += 1
    AsyncQuestionState.objects.update_or_create(session=session, defaults={"state": state})
    from twicc.providers.codex.question_snapshots import publish_question_snapshot_on_commit

    publish_question_snapshot_on_commit(session_id, _snapshot(session, state))


@transaction.atomic
def merge_question_facts(session_id: str, facts: list[QuestionFact]) -> dict:
    """Merge SDK/source/recompute evidence without dropping durable decisions."""
    session = _session(session_id, mutation=True)
    return _merge(session, _state(session_id), facts)


@transaction.atomic
def prepare_question_send(
    session_id: str,
    text: str,
    response: dict | None,
    *,
    request_id: str,
    origin: str,
    at: str,
) -> PreparedQuestionSend:
    """Persist immutable admission before delivery; keep questions unresolved."""
    session = _session(session_id, mutation=True)
    if not isinstance(request_id, str) or not request_id.strip() or origin not in ("human", "agent", "internal"):
        raise ValueError("async_questions_invalid")
    current = _state(session_id)
    stored = current["facts"].get(f"send:{request_id}")
    if stored is not None:
        # Retry returns stored final text. Never format it a second time.
        data = deepcopy(stored["data"])
        if data.get("not_admitted"):
            raise ValueError("async_questions_not_admitted")
        return PreparedQuestionSend(data["text"], data)
    snapshot = _snapshot(session, current)
    answers = validate_question_answers(snapshot, response)
    if response is not None and origin != "human":
        raise ValueError("async_questions_invalid")
    final_text = format_question_answers(snapshot["batches"], answers, text)
    boundary = build_question_boundary(
        current,
        at=at,
        # The ingestion cursor does not identify the source submission item.
        line=None,
        batch_ids=response["batch_ids"] if response is not None else None,
    )
    submission = {
        "request_id": request_id,
        "origin": origin,
        "origin_source": "live",
        "boundary": boundary,
        "status": "prepared",
        "client_message_id": request_id,
        "source_item_id": None,
        "target_turn_id": None,
        "delivery_route": None,
        "text": final_text,
    }
    _merge(
        session,
        current,
        [
            QuestionFact(
                f"send:{request_id}",
                "user_submission",
                at,
                None,
                None,
                None,
                submission,
            )
        ],
    )
    return PreparedQuestionSend(final_text, deepcopy(submission))


@transaction.atomic
def accept_question_send(session_id: str, submission: dict) -> dict:
    """Record accepted, rejected, or uncertain delivery against prepared intent.

    A prepared submission passed unchanged means accepted. Explicit delivery
    statuses override that default. Original origin and boundary always win.
    """
    session = _session(session_id, mutation=True)
    current = _state(session_id)
    if not isinstance(submission, dict) or not isinstance(submission.get("request_id"), str):
        raise ValueError("async_questions_invalid")  # noqa: TRY004 - Public API requires ValueError.
    stored = current["facts"].get(f"send:{submission['request_id']}")
    status = submission.get("status", "accepted")
    if status == "prepared":
        status = "accepted"
    if stored is None or status not in ("accepted", "rejected", "uncertain"):
        raise ValueError("async_questions_invalid")
    if stored["data"].get("status") == "accepted" and status != "accepted":
        # Late failure evidence cannot undo delivered identity or its route.
        return _snapshot(session, current)
    data = deepcopy(stored["data"])
    data["status"] = status
    for field in ("source_item_id", "target_turn_id", "delivery_route"):
        if submission.get(field) is not None:
            data[field] = submission[field]
    fact = QuestionFact(**{**stored, "data": data})
    return _merge(session, current, [fact])


@transaction.atomic
def dismiss_question_batch(session_id: str, item_id: str, *, request_id: str) -> dict:
    """Resolve a ready batch locally, retaining the request ID for recovery."""
    session = _session(session_id, mutation=True)
    current = _state(session_id)
    if not isinstance(request_id, str) or not request_id.strip() or not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("async_questions_invalid")
    prior = current["facts"].get(f"dismiss:{request_id}")
    if prior is not None:
        if prior["data"]["batch_ids"] != [item_id]:
            raise ValueError("async_questions_invalid")
        return _snapshot(session, current)
    batch = current["batches"].get(item_id)
    if batch is not None and batch["status"] in {"sent", "dismissed"}:
        return _snapshot(session, current)
    if batch is None or batch["status"] != "ready":
        raise ValueError("async_questions_stale")
    fact = QuestionFact(
        f"dismiss:{request_id}",
        "dismiss",
        datetime.now(UTC).isoformat(),
        None,
        None,
        None,
        {"request_id": request_id, "batch_ids": [item_id]},
    )
    return _merge(session, current, [fact])
