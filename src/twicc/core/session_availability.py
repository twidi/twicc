"""The rule for a session whose history TwiCC cannot show: no read, no write.

``Session.unavailable_reason`` set to any value means:

- **No read.** Nothing serves the session's conversation content: items,
  messages, plans, subagents, workflows, search hits, shares, title
  generation, live item pushes.
- **No write.** Nothing makes its agent append to it: sends from every
  surface, process (re)starts, question answers, hybrid switches.

Metadata stays available: listings, the session payload (which keeps
``unavailable_reason``), archive, pin, hide, mute, rename, annotations, tree
links, and the stop of a process that already runs.

The rule depends only on the field being non-null, never on a reason value or
a provider, so a new reason needs no change here or at the call sites. A
subagent follows its parent: its content is part of the parent's history.

Every entry point calls this module. None tests the field itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import JsonResponse

    from twicc.core.models import Session


#: The error code every surface reports: REST and WebSocket ``code``, the
#: rejected status of a drop request, the CLI and MCP error text.
SESSION_UNAVAILABLE = "session_unavailable"

#: HTTP status of a refused REST request.
HTTP_STATUS = 409

#: CLI exit code of a refused command: "rejected", as for a server rejection.
CLI_EXIT_CODE = 3

def unavailable_message(session_id: str, reason: str) -> str:
    """The text every surface shows. It names the session and the reason."""
    return (
        f"Session {session_id!r} is unavailable ({reason}): TwiCC cannot read "
        "its history or continue it."
    )


def session_unavailable_reason(session: Session, *, parent: Session | None = None) -> str | None:
    """The reason that blocks ``session``, or ``None``.

    The session's own reason first. For a subagent without one, its parent's:
    from ``parent`` when the caller holds that row, else read with one query.
    A top-level session never reads a row.
    """
    if session.unavailable_reason is not None:
        return session.unavailable_reason
    if session.parent_session_id is None:
        return None
    if parent is not None and parent.pk == session.parent_session_id:
        return parent.unavailable_reason
    from twicc.core.models import Session

    return (
        Session.objects.filter(pk=session.parent_session_id)
        .values_list("unavailable_reason", flat=True).first()
    )


async def asession_unavailable_reason(session: Session, *, parent: Session | None = None) -> str | None:
    """Async :func:`session_unavailable_reason`.

    Answered inline when the rows at hand settle it (the hot path of every
    available top-level session). Only a subagent whose parent row is not at
    hand goes to the thread executor for its one query.
    """
    if (
        session.unavailable_reason is not None
        or session.parent_session_id is None
        or (parent is not None and parent.pk == session.parent_session_id)
    ):
        return session_unavailable_reason(session, parent=parent)
    from asgiref.sync import sync_to_async

    return await sync_to_async(session_unavailable_reason)(session)


def row_unavailable_reason(session: Session) -> str | None:
    """The row's own reason only, never the parent's: for query-free callers (the serializer)."""
    return session.unavailable_reason


def unavailable_reason_by_id(session_id: str) -> str | None:
    """Same as :func:`session_unavailable_reason`, from an id. ``None`` for an unknown id."""
    from twicc.core.models import Session

    row = (
        Session.objects.filter(pk=session_id)
        .values_list("unavailable_reason", "parent_session__unavailable_reason").first()
    )
    if row is None:
        return None
    return row[0] if row[0] is not None else row[1]


async def aunavailable_reason_by_id(session_id: str) -> str | None:
    """Async :func:`unavailable_reason_by_id`."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(unavailable_reason_by_id)(session_id)


def all_unavailable_session_ids() -> set[str]:
    """Every session the rule covers. Few by nature: for query-time exclusions.

    Two indexed lookups, not one OR over a join (which scans every session):
    the sessions with a reason, then, only when there are some, their subagents.
    """
    from twicc.core.models import Session

    # ``order_by()``: the model's default ordering would sort the rows for nothing.
    ids = set(Session.objects.filter(unavailable_reason__isnull=False).order_by().values_list("pk", flat=True))
    if ids:
        ids |= set(Session.objects.filter(parent_session_id__in=ids).order_by().values_list("pk", flat=True))
    return ids


def ensure_session_id_available(session_id: str) -> None:
    """Raise :class:`~twicc.agent.exceptions.SessionUnavailableError` when the rule covers the session.

    An unknown id passes: "not found" is the caller's check.
    """
    reason = unavailable_reason_by_id(session_id)
    if reason is not None:
        # Lazy: ``twicc.agent`` loads the whole agent runtime, which imports this module.
        from twicc.agent.exceptions import SessionUnavailableError

        raise SessionUnavailableError(
            unavailable_message(session_id, reason), session_id=session_id, reason=reason,
        )


async def aensure_session_id_available(session_id: str) -> None:
    """Async :func:`ensure_session_id_available`, for the write paths."""
    from asgiref.sync import sync_to_async

    await sync_to_async(ensure_session_id_available)(session_id)


async def arefuse_unavailable(session: Session) -> JsonResponse | None:
    """For a REST view that serves content: the refusal when the rule covers ``session``, else ``None``.

    Body: ``{"error": <message>, "code": "session_unavailable", "unavailable_reason": <reason>,
    "unavailable_session_id": <id>}``. The id names the session that carries the
    reason: ``session`` itself, or the parent a subagent follows. The frontend
    stores the reason on that row.
    """
    from django.http import JsonResponse

    reason = await asession_unavailable_reason(session)
    if reason is None:
        return None
    carrier = session.id if session.unavailable_reason is not None else session.parent_session_id
    return JsonResponse({
        "error": unavailable_message(session.id, reason),
        "code": SESSION_UNAVAILABLE,
        "unavailable_reason": reason,
        "unavailable_session_id": carrier,
    }, status=HTTP_STATUS)
