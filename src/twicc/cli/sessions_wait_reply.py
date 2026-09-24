"""``twicc sessions wait-reply`` — wait on several sessions nobody just messaged —
or ones you messaged with ``send-messages`` without ``--wait-reply`` (then pass
``--since`` an instant taken before the send, or wait on each with ``session
<id> wait-reply --from <last_line>``).

The plural of ``session <ID> wait-reply``, and the same loop: one poll drives
every session, one wall-clock budget covers the batch, and each concludes on
an answer or on a pending request. ``send-messages --wait-reply`` already runs
that loop; the difference here is where the cursors come from.

**The cursors.** A send hands back the line it was taken at, so
``send-messages`` knows where each recipient starts. Nothing is sent here, so
each session starts after its own last user message, so an answer already
given is returned (above its current ``last_line`` while its compute is not
current, :func:`~twicc.cli._wait_reply.default_wait_cursors`), or above the
instant ``--since`` names. There is deliberately
no ``--from``: a line number belongs to one transcript and means something
else in every other, which is the whole reason ``--since`` exists.

**Selection goes through the listing's** ``build_filtered_queryset``, and
diverges from it in four places. Hidden sessions are always included and
``--include-hidden`` is not offered — orchestration workers are hidden by
convention, and this command is for them. The indexing predicate is off: a
session that started two seconds ago has neither a date nor a first user
message, and is the one most likely to speak. Archived ones are always out and
``--include-archived`` is not offered, since archiving kills the agent. And
``--state dead`` is refused outright.

The fifth difference is the empty case: a bare call would wait on every session
TwiCC has ever indexed, so at least one id or one filter is required.
``sessions stop`` refuses a bare call too.
"""

from __future__ import annotations

from twicc.cli._output import emit_error, emit_json


def main(
    session_ids: list[str],
    *,
    since: str | None = None,
    timeout: float,
    first: bool = False,
    active: bool = False,
    only_hidden: bool = False,
    want_text: bool = True,
    project: str | None = None,
    workspace: str | None = None,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
    provider: str | None = None,
    state: list[str] | None = None,
) -> None:
    """Block until every selected session concludes, or the first one does."""
    import django

    django.setup()

    import time

    from twicc.cli._process_state import DEAD_VIRTUAL_STATE
    from twicc.cli._session_selection import (
        reject_conflicting_scopes,
        resolve_explicit_ids,
    )
    from twicc.cli._wait_reply import default_wait_cursors, degraded_reply, wait_for_replies
    from twicc.cli.session import _cursor_at, _parse_instant
    from twicc.cli.sessions import build_filtered_queryset
    from twicc.core.models import Session

    # Every argument check before anything is read, so a bad flag is named as a
    # bad flag rather than pre-empted by a lookup or an empty selection.
    if timeout <= 0:
        emit_error(f"Error: --wait-timeout must be > 0 (got {timeout:g}).", code=1)
    instant = None if since is None else _parse_instant(since)

    # Refused rather than honoured, as `sessions stop` refuses it: `dead` is
    # "no TwiCC process", so those sessions have nothing to say. It is also the
    # one filter that would lift the refusal below while selecting every
    # unarchived session there is: each would be polled, and one with no answer
    # past its cursor waits out the ~5 s flush window before concluding `ended`,
    # to produce a payload that says nothing.
    if state and DEAD_VIRTUAL_STATE in state:
        emit_error(
            "Error: --state dead selects sessions with no process, which will "
            "never speak again — there is nothing to wait for.",
            code=1,
        )
    if active and state:
        emit_error("Error: --active and --state are mutually exclusive.", code=1)
    reject_conflicting_scopes(
        spawned_by, spawn_tree, descendants, siblings,
        project=project, workspace=workspace,
    )

    explicit = resolve_explicit_ids(session_ids)
    has_filter = any((
        project, workspace, provider, state, active, only_hidden,
        spawned_by, spawn_tree, descendants, siblings, annotation,
    ))
    # The one place this command refuses what the listing allows. A listing with
    # no filter shows a page; a wait with no filter would poll every session
    # ever indexed — the live ones to the deadline, the rest for the flush
    # window — or answered at once when an answer already sits past their
    # cursor — to hand back thousands of blocks nobody asked about.
    if not explicit and not has_filter:
        emit_error(
            "Error: sessions wait-reply needs at least one session id or one "
            "filter — a bare call would wait on every session TwiCC knows.",
            code=1,
        )

    selected: list[str] = []
    if has_filter:
        qs, _ = build_filtered_queryset(
            project=project, workspace=workspace,
            # Same reasoning as `sessions stop`: a session worth waiting on may
            # be hidden (orchestration workers are, by convention) and may have
            # started too recently to be indexed. Archived ones are excluded —
            # archiving kills the agent, so none of them will ever speak again.
            include_hidden=True, only_hidden=only_hidden, require_indexed=False,
            spawned_by=spawned_by, spawn_tree=spawn_tree,
            descendants=descendants, siblings=siblings,
            annotation=annotation, provider=provider, state=state, active=active,
        )
        selected = [sid for sid in qs.values_list("id", flat=True) if sid not in explicit]

    # Explicit ids are UNIONED with the filters, as everywhere else in the
    # plural family — an id named alongside a scope is an addition to it.
    targets = list(explicit) + selected
    if not targets:
        emit_json({"summary": _summary({}), "results": {}})
        return

    rows = {s.id: s for s in Session.objects.filter(id__in=targets)}
    # A session exists as a ProcessRun before the watcher writes its row: one
    # just spawned is live yet unknown here. It is waited on from line 0, as
    # `create-session --wait-reply` does — a new transcript holds nothing
    # older than this wait. The wait loop already copes with the missing row.
    missing = [sid for sid in targets if sid not in rows]
    live_unindexed: set = set()
    if missing:
        from twicc.cli._process_state import (
            live_session_ids,
            load_process_rows,
            resolve_listing_twicc_pid,
        )

        live_unindexed = live_session_ids(
            load_process_rows(missing, resolve_listing_twicc_pid())
        )
    defaults = default_wait_cursors(rows.values()) if instant is None else {}
    cursors = {}
    for sid in targets:
        session = rows.get(sid)
        if session is None:
            if sid in live_unindexed:
                cursors[sid] = 0
                continue
            # Named and unknown, or selected by a filter and deleted between
            # the two queries. Reported rather than dropped: the caller must be
            # able to align the result with what they asked for.
            continue
        cursors[sid] = _cursor_at(session, instant) if instant is not None else defaults[sid]

    started = time.monotonic()
    try:
        replies = wait_for_replies(
            cursors, timeout=timeout, want_text=want_text, first=first,
        ) if cursors else {}
    except BaseException as exc:  # noqa: BLE001 - deliberate, as in `send-messages`
        # N sessions means N times the queries, so this command is the one most
        # exposed to a locked database over a long poll — and losing the whole
        # batch would take the answers already collected with it, along with
        # every cursor to resume from. The singular is covered by
        # ``wait_for_reply_or_degrade``; this is its plural.
        waited = round(time.monotonic() - started, 1)
        replies = {sid: degraded_reply(cursor, waited, exc) for sid, cursor in cursors.items()}
    for sid in targets:
        if sid not in replies:
            replies[sid] = {"outcome": "unknown_session", "session_id": sid}

    emit_json({"summary": _summary(replies), "results": replies})


def _summary(replies: dict) -> dict:
    """Counts over every id in ``results``, including the unknown ones.

    ``total`` is what was asked for, not what was reachable: an id that names
    no session is a mistake the caller has to see, and hiding it from the count
    would let ``all_replied`` read true on a batch that never ran.

    ``replied`` counts `outcome: replied` and nothing else, like
    ``send-messages``: a session that concluded on a pending request concluded,
    but it did not answer. ``concluded`` is the broader count, so a caller does
    not have to choose between the two meanings of the word.
    """
    from twicc.cli._wait_reply import AWAITING, REPLIED

    replied = sum(1 for b in replies.values() if b.get("outcome") == REPLIED)
    blocked = sum(1 for b in replies.values() if b.get("outcome") == AWAITING)
    return {
        "total": len(replies),
        "replied": replied,
        "awaiting_user_input": blocked,
        "concluded": replied + blocked,
        "all_replied": bool(replies) and replied == len(replies),
    }


