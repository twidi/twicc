"""Compactions in the timeline: ``compaction.requested`` and ``compaction.completed``.

A compaction request is not a message: ``/compact`` never becomes a ``user_message.sent`` row.

* ``compaction.requested`` is written when TwiCC delivers the command (:func:`record_compaction_request_sent`), at
  once: Claude Code writes the command's line in its session file only once the compaction is over. A command typed
  outside TwiCC (in the CLI's own terminal) is only seen through that line: :func:`capture_source_compaction_request`
  writes it then, at the time of the line, unless a sent request near that time already stands for it.
* ``compaction.completed`` is written for each compaction summary line of the live ingestion
  (:func:`record_compactions_completed`), asked for or automatic, with the summary text when the provider gives it in
  clear (Codex encrypts it).

Live only, like the message snapshots: nothing is written for the history read at startup.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

import orjson

from twicc.core.content_storage.readers import iter_current_items
from twicc.core.enums import ItemKind
from twicc.core.models import SessionItem, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services.session_timeline import record_event, record_event_sync
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.message_text import parse_compaction_request
from twicc.request_origin import RequestOrigin, capture_origin

logger = logging.getLogger(__name__)

#: How far apart a sent request and the session-file line of the same command can be. Claude Code dates that line
#: when it receives the command, a moment after TwiCC sent it.
SENT_REQUEST_WINDOW = timedelta(minutes=5)
#: How many lines before a compaction summary its boundary line (``compact_boundary``, Claude Code) can be.
BOUNDARY_LOOKBACK = 8


def _dedupe_key(prefix: str, *parts) -> str:
    return f"{prefix}:" + hashlib.sha256(orjson.dumps([str(part) for part in parts])).hexdigest()


async def record_compaction_request_sent(*, session_id: str, project_id: str | None, provider: str | None,
                                         text: str | None) -> None:
    """Write ``compaction.requested`` when ``text``, just delivered to the session, asks for a compaction."""
    request = parse_compaction_request(text or "", provider)
    if request is None:
        return
    await record_event(
        TimelineKind.COMPACTION_REQUESTED, session_id=session_id, project_id=project_id, provider=provider,
        origin=capture_origin(), data={"text": request.prompt, "source": "send"},
    )


def capture_source_compaction_request(session, *, text: str, occurred_at: datetime | None, identity,
                                      origin: RequestOrigin | None) -> bool:
    """Handle a user message line of the session file that asks for a compaction.

    Returns False when ``text`` is not a compaction request (the caller records the message), True otherwise: the
    line is then never a message. It is written as ``compaction.requested`` only when no request sent by TwiCC near
    its time stands for it.
    """
    request = parse_compaction_request(text, session.provider)
    if request is None:
        return False
    if occurred_at is not None and TimelineEvent.objects.filter(
        session_id=session.id, kind=TimelineKind.COMPACTION_REQUESTED, data__source="send",
        occurred_at__gte=occurred_at - SENT_REQUEST_WINDOW, occurred_at__lte=occurred_at + SENT_REQUEST_WINDOW,
    ).exists():
        return True
    record_event_sync(
        TimelineKind.COMPACTION_REQUESTED, session_id=session.id, project_id=session.project_id,
        provider=session.provider, occurred_at=occurred_at, origin=origin or RequestOrigin("external"),
        dedupe_key=_dedupe_key("compaction-requested", session.provider, identity.kind, identity.native_id),
        data={"text": request.prompt, "source": "source"},
    )
    return True


def _trigger(session, helpers, item) -> str | None:
    """``manual`` or ``auto``: what the provider says (Claude Code's boundary line), else whether a request is open."""
    lines = SessionItem.objects.filter(
        session_id=session.id, kind=ItemKind.SYSTEM,
        line_num__gte=item.line_num - BOUNDARY_LOOKBACK, line_num__lt=item.line_num,
    ).order_by("-line_num")
    for line in iter_current_items(lines, 16, 1024 * 1024):
        parsed = helpers.parse_item_content(line)
        if parsed is not None and (trigger := helpers.compaction_trigger_from_parsed(parsed)) is not None:
            return trigger
    boundaries = TimelineEvent.objects.filter(
        session_id=session.id, kind__in=(TimelineKind.COMPACTION_REQUESTED, TimelineKind.COMPACTION_COMPLETED),
    ).order_by("-id").values_list("kind", flat=True).first()
    return "manual" if boundaries == TimelineKind.COMPACTION_REQUESTED else "auto"


def record_compactions_completed(session, *, new_line_nums: Sequence[int], timeline_since: datetime | None) -> None:
    """Write ``compaction.completed`` for each new compaction summary line of the live ingestion. Never raises."""
    if timeline_since is None or not new_line_nums:
        return
    try:
        rows = SessionItem.objects.filter(
            session_id=session.id, line_num__in=new_line_nums, kind=ItemKind.COMPACT_SUMMARY,
        ).order_by("line_num")
        helpers = get_provider_helpers(session.provider)
        for item in iter_current_items(rows, 16, 8 * 1024 * 1024):
            if item.timestamp is None or item.timestamp < timeline_since:
                continue
            parsed = helpers.parse_item_content(item)
            summary = helpers.compaction_summary_from_parsed(parsed) if parsed is not None else None
            record_event_sync(
                TimelineKind.COMPACTION_COMPLETED, session_id=session.id, project_id=session.project_id,
                provider=session.provider, occurred_at=item.timestamp, actor=TimelineActor.AGENT,
                dedupe_key=_dedupe_key("compaction-completed", session.provider, session.storage_incarnation,
                                       session.history_epoch, item.line_num),
                data={"trigger": _trigger(session, helpers, item), "text": summary},
            )
    except Exception:
        logger.exception("Timeline compaction capture failed for %s", session.id)
