"""``twicc sessions wait-reply`` — wait on several sessions nobody just messaged.

The plural of ``session <ID> wait-reply``, and the same loop: one poll drives
every session, one wall-clock budget covers the batch, and each concludes on
an answer or on a pending request. ``send-messages --wait-reply`` already runs
that loop; the difference here is where the cursors come from.

**The cursors.** A send hands back the line it was taken at, so
``send-messages`` knows where each recipient starts. Nothing is sent here, so
each session starts above its own ``last_line`` — "tell me the next thing each
of them says" — or above the instant ``--since`` names. There is deliberately
no ``--from``: a line number belongs to one transcript and means something
else in every other, which is the whole reason ``--since`` exists.

**Selection is the listing's**, filter for filter, through
``build_filtered_queryset``. What is not shared is the empty case: a bare call
would wait on every session TwiCC has ever indexed, so at least one id or one
filter is required. ``sessions stop`` can afford a bare call because the live
process set bounds it; nothing bounds this one.
"""

from __future__ import annotations

from twicc.cli._output import emit_error, emit_json

#: Scope flags that may not be combined, mirroring the listing's own rule.
_SCOPES = ("--spawned-by", "--spawn-tree", "--descendants", "--siblings")


def main(
    session_ids: list[str],
    *,
    since: str | None = None,
    timeout: float,
    first: bool = False,
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

    from twicc.cli._wait_reply import wait_for_replies
    from twicc.cli.session import _cursor_at, _parse_instant
    from twicc.cli.sessions import build_filtered_queryset
    from twicc.core.models import Session

    # Every argument check before anything is read, so a bad flag is named as a
    # bad flag rather than pre-empted by a lookup or an empty selection.
    if timeout <= 0:
        emit_error(f"Error: --wait-timeout must be > 0 (got {timeout:g}).", code=1)
    instant = None if since is None else _parse_instant(since)

    scopes = [n for n, v in zip(_SCOPES, (spawned_by, spawn_tree, descendants, siblings))
              if v]
    if len(scopes) > 1:
        emit_error(f"Error: {' and '.join(scopes)} are mutually exclusive.", code=1)
    if project and workspace:
        emit_error("Error: --project and --workspace are mutually exclusive.", code=1)

    explicit = _resolve_explicit(session_ids)
    has_filter = any((
        project, workspace, provider, state,
        spawned_by, spawn_tree, descendants, siblings, annotation,
    ))
    # The one place this command refuses what the listing allows. A listing with
    # no filter shows a page; a wait with no filter would poll every session
    # ever indexed until the deadline, and mean nothing by it.
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
            include_hidden=True, require_indexed=False,
            spawned_by=spawned_by, spawn_tree=spawn_tree,
            descendants=descendants, siblings=siblings,
            annotation=annotation, provider=provider, state=state,
        )
        selected = [sid for sid in qs.values_list("id", flat=True) if sid not in explicit]

    # Explicit ids are UNIONED with the filters, as everywhere else in the
    # plural family — an id named alongside a scope is an addition to it.
    targets = list(explicit) + selected
    if not targets:
        emit_json({"summary": _summary({}), "results": {}})
        return

    rows = {s.id: s for s in Session.objects.filter(id__in=targets)}
    cursors = {}
    for sid in targets:
        session = rows.get(sid)
        if session is None:
            # Named and unknown. Reported rather than dropped: the caller must
            # be able to align the result with what they asked for.
            continue
        cursors[sid] = (
            _cursor_at(session, instant) if instant is not None else (session.last_line or 0)
        )

    replies = wait_for_replies(
        cursors, timeout=timeout, want_text=want_text, first=first,
    ) if cursors else {}
    for sid in targets:
        if sid not in replies:
            replies[sid] = {"outcome": "unknown_session", "session_id": sid}

    emit_json({"summary": _summary(replies), "results": replies})


def _summary(replies: dict) -> dict:
    """Counts over the sessions actually waited on.

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


def _resolve_explicit(session_ids: list[str]) -> list[str]:
    """Explicit ids, de-duplicated, with ``self`` / ``parent`` resolved."""
    from twicc.cli._drop_request.whoami import resolve_current_session

    out: list[str] = []
    seen: set = set()
    for raw in session_ids or []:
        sid = raw
        if raw in ("self", "parent"):
            current = resolve_current_session()
            if current is None:
                emit_error(
                    f"Error: '{raw}' needs a TwiCC session in the process "
                    "ancestry. Pass an explicit session_id.",
                    code=1,
                )
            sid = current.id if raw == "self" else current.spawned_by_id
            if sid is None:
                emit_error(
                    "Error: the current session has no spawner, so 'parent' "
                    "resolves to nothing.",
                    code=1,
                )
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out
