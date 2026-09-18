"""CLI implementation for the ``twicc session`` subcommand."""

import orjson

from twicc.cli._output import emit_error, emit_json, emit_list, pagination_notice, resolve_limit


def _get_session(session_id: str):
    """Fetch a valid session (created_at set, at least one user message) or exit."""
    from twicc.core.models import Session

    try:
        session = Session.objects.get(
            id=session_id,
            created_at__isnull=False,
            user_message_count__gt=0,
        )
    except Session.DoesNotExist:
        emit_error(f"Error: session '{session_id}' not found.", code=1)

    return session


def _parse_range_filter(range_str: str) -> dict:
    """Parse ``"N"`` or ``"N-M"`` into Django ORM filter kwargs for ``line_num``.

    Exits with status 1 on malformed input.
    """
    if "-" in range_str:
        parts = range_str.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            emit_error(f"Error: invalid range '{range_str}'. Use a number or start-end (e.g. '5' or '10-20').", code=1)
        if start > end:
            emit_error(f"Error: invalid range '{range_str}'. Start must be <= end.", code=1)
        return {"line_num__gte": start, "line_num__lte": end}

    try:
        line_num = int(range_str)
    except ValueError:
        emit_error(f"Error: invalid line number '{range_str}'. Use a number or start-end (e.g. '5' or '10-20').", code=1)
    return {"line_num": line_num}


def _slice_window(seq, total: int, *, limit: int | None, offset: int, tail: int | None):
    """Apply ``tail``/``limit``/``offset`` windowing to a sliceable sequence.

    Works on both a lazy queryset (slicing stays at the DB level) and a plain
    list. ``total`` is only consulted for ``tail`` — callers pass ``qs.count()``
    for a queryset (cheap, avoids materialising it) or ``len(...)`` for a list.
    ``tail`` is assumed already validated as mutually exclusive with
    ``limit``/``offset``.
    """
    if tail is not None:
        return seq[max(0, total - tail):]
    if limit is not None:
        return seq[offset : offset + limit]
    return seq[offset:]


def main(session_id: str) -> None:
    """Fetch a single session by ID and print its JSON representation to stdout."""
    import django

    django.setup()

    from twicc.core.serializers import serialize_session

    session = _get_session(session_id)
    data = serialize_session(session)

    emit_json(data)


def content(
    session_id: str,
    *,
    range_str: str | None = None,
    contains: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    tail: int | None = None,
    paginated: bool = False,
) -> None:
    """Fetch session item(s) by line/range and/or content substring(s), print as JSON to stdout.

    Every selector is optional but at least one must be given — a bare call
    would dump the whole session, and raw items are the heaviest payload the CLI
    can produce. ``paginated`` counts as one: it supplies its own page size, so
    it cannot dump anything either. Combined, they apply in this order:
    ``range_str`` scopes the lines, ``contains`` filters within that scope, then
    ``limit``/``offset`` (or ``tail``) window the matches. ``contains`` is a list
    of case-insensitive substrings AND-combined (an item must contain every term)
    matched against the raw JSONL string stored in ``SessionItem.content`` (so it
    also matches JSON keys and sees escaped sequences like ``\\n``).

    ``range_str`` and ``limit``/``offset`` answer different questions and are
    meant to be combined: the range is an absolute address in the JSONL (a
    ``line_num`` span), the window is a rank in the filtered result. They only
    coincide when nothing else filters, since items map one-to-one onto lines.

    ``tail`` is the last N matches, mutually exclusive with ``limit``/``offset``.
    It exists because the end of a filtered result has no address: without it,
    reaching the last N takes a first call to learn ``last_line`` or ``total``,
    and the session may grow between the two.

    No match is an empty result, not an error: with a window, running past the
    end is ordinary paging rather than a failure.
    """
    import django

    django.setup()

    from twicc.core.models import SessionItem

    # Above the selector guard below, which counts ``paginated`` as one: past the
    # cutover the flag is always set, so the guard stops firing and a bare
    # ``content`` call returns the first page instead of an error. Bounded, which
    # is all the guard ever protected against.
    paginated = pagination_notice("session content", paginated, default_limit=None)

    contains = contains or []
    if (
        range_str is None and not contains and limit is None and not offset
        and tail is None and not paginated
    ):
        emit_error(
            "Error: provide a line/range argument, --contains, --limit/--offset, "
            "--tail, or --paginated.",
            code=1,
        )

    if tail is not None:
        if limit is not None or offset:
            emit_error("Error: --tail is mutually exclusive with --limit and --offset.", code=1)
        if tail <= 0:
            emit_error(f"Error: --tail must be a positive integer (got {tail}).", code=1)
    else:
        limit = resolve_limit(limit, paginated=paginated, default=None)

    _get_session(session_id)

    items = SessionItem.objects.filter(session_id=session_id)
    if range_str is not None:
        items = items.filter(**_parse_range_filter(range_str))
    for term in contains:
        # Chained filters AND together; each term must appear in the raw content.
        items = items.filter(content__icontains=term)
    items = items.order_by("line_num")

    # ``tail`` needs the count to place its window, whether or not the envelope
    # is asked for; ``paginated`` needs it for ``total``.
    total = items.count() if (tail is not None or paginated) else None
    selected = _slice_window(items, total or 0, limit=limit, offset=offset, tail=tail)

    # Wrap each item with its line number; parse the raw content string into a real JSON object.
    data = [{"line_num": item.line_num, "content": orjson.loads(item.content)} for item in selected]

    if tail is not None:
        # Same reading as ``messages``: report the range the window really covers,
        # and let ``has_more`` mean "there are matches before it".
        window_offset = max(0, total - tail)
        emit_list(
            data, paginated=paginated, limit=tail, offset=window_offset,
            total=total, has_more=window_offset > 0,
        )
        return

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)


TRI_STATE_TOKENS = {"true": True, "false": False, "null": None}
"""Wire vocabulary of ``--is-final``: the three JSON literals the field emits."""


def messages(
    session_id: str,
    *,
    range_str: str | None = None,
    role: str | None = None,
    contains: list[str] | None = None,
    is_final: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    tail: int | None = None,
    paginated: bool = False,
) -> None:
    """Fetch user/assistant messages of a session and print as JSON to stdout.

    Output is uniform across providers: a list of
    ``{line_num, text, role, timestamp, is_final}`` produced by the same
    ``get_indexable_messages`` helper used by the full-text search
    indexer (the helper's ``from_role`` field is exposed as ``role``
    on the wire — the wider name was an indexing-side concern). Items
    whose provider-specific text extraction yields an empty string are
    silently dropped (same behaviour as the search indexer), so the
    result may contain fewer entries than ``--limit`` or ``--tail``.

    ``is_final`` tells an assistant message that closes its turn from one
    of the intermediate ones the model emits between tool calls. ``null``
    means unknown (always so on a user message); see
    ``BaseProviderHelpers.is_final_assistant_message``.

    ``contains`` is a list of case-insensitive substrings AND-combined (a
    message must contain every term). Unlike ``content``'s raw-JSONL filter, it
    matches against the extracted ``text`` — what this sub-command emits — so it
    never matches JSON keys or tool noise. Because that text is produced in
    Python, the filter (and, with it, the ``tail``/``limit``/``offset`` window)
    is applied after extraction, on the matching messages.

    The ``is_final`` argument is the matching filter on that field: a list of
    ``"true"`` / ``"false"`` / ``"null"`` tokens, **OR-combined** (a message
    matches when its own value is any of them). OR, where ``contains`` is AND,
    because a message carries a single value — an AND would always be empty.
    Omitting it filters nothing, so ``null`` is only ever dropped when the
    caller asks for a set that excludes it. Asking for all three values is the
    identity: byte-for-byte the no-flag answer, not merely the same entries.

    ``paginated`` adds the shared envelope. ``total`` counts what the window was
    applied to, which differs per branch: when something actually filters after
    extraction — ``contains``, or an ``is_final`` set that leaves a value out —
    the window sits on the extracted messages, so the count is exact. Otherwise
    it sits on the raw items, so the count includes the few that extract to an
    empty string and are dropped; ``has_more`` can then be a (rare) false
    positive — never a false negative, so no message is ever hidden behind a
    ``has_more: false``. A vacuous ``is_final`` (all three values) stays on that
    second branch on purpose, being the identity of the no-flag call. Under
    ``--tail`` the window is reported as the range it actually covers
    (``offset = total - tail``) and ``has_more`` says whether messages remain
    *before* it — the only direction that means anything there.
    """
    import django

    django.setup()

    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.providers.helpers import get_provider_helpers

    paginated = pagination_notice("session messages", paginated, default_limit=None)

    session = _get_session(session_id)

    contains = contains or []

    if role is not None and role not in {"user", "assistant"}:
        emit_error(f"Error: invalid --role '{role}'. Use 'user' or 'assistant'.", code=1)

    # ``None`` means "keep everything", and asking for all three values
    # collapses to it: that is what makes ``--is-final true --is-final false
    # --is-final null`` return exactly what no flag at all returns — same
    # entries, same ``total``, same ``has_more``. Routing it through the
    # filtering branch instead would give a *different* (and, on the ``total``,
    # more exact) answer than the no-flag call, which is the one thing an
    # identity must not do. The price is that a vacuous filter keeps the raw
    # ``total`` of the cheap branch; see this function's docstring.
    wanted_is_final = None
    if is_final:
        for token in is_final:
            if token not in TRI_STATE_TOKENS:
                emit_error(
                    f"Error: invalid --is-final '{token}'. "
                    "Use 'true', 'false' or 'null'.",
                    code=1,
                )
        wanted = {TRI_STATE_TOKENS[token] for token in is_final}
        # Compared against the values, not the token count: an alias token
        # added to the mapping must not silently break the identity case.
        if wanted != set(TRI_STATE_TOKENS.values()):
            wanted_is_final = wanted

    if tail is not None:
        if limit is not None or offset:
            emit_error("Error: --tail is mutually exclusive with --limit and --offset.", code=1)
        if tail <= 0:
            emit_error(f"Error: --tail must be a positive integer (got {tail}).", code=1)
    else:
        # After the exclusivity check, so an explicit --limit alongside --tail is
        # still rejected rather than silently replaced by the paginated default.
        limit = resolve_limit(limit, paginated=paginated, default=None)

    if role == "user":
        kinds = [ItemKind.USER_MESSAGE]
    elif role == "assistant":
        kinds = [ItemKind.ASSISTANT_MESSAGE]
    else:
        kinds = [ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE]

    filter_kwargs: dict = {"session_id": session_id, "kind__in": kinds}
    if range_str is not None:
        filter_kwargs |= _parse_range_filter(range_str)

    qs = SessionItem.objects.filter(**filter_kwargs).order_by("line_num")
    helpers = get_provider_helpers(session.provider)

    if contains or wanted_is_final is not None:
        # Both filters read what extraction produces — the text for ``contains``,
        # the ``is_final`` field for the other — so we materialise and extract
        # everything in range first, filter, then window on the matches.
        terms = [term.lower() for term in contains]
        matched = [
            msg
            for msg in helpers.get_indexable_messages(list(qs))
            if all(term in msg.text.lower() for term in terms)
            and (wanted_is_final is None or msg.is_final in wanted_is_final)
        ]
        # The window sits on the extracted messages, already in memory: the total
        # is exact and free, no query and no over-fetch.
        total = len(matched)
        selected = _slice_window(matched, total, limit=limit, offset=offset, tail=tail)
    else:
        # No post-extraction filter: window at the DB level, then extract (existing
        # behaviour — the window counts raw items, so dropped-empty extractions may
        # shrink the result).
        total = qs.count() if (tail is not None or paginated) else 0
        items = list(_slice_window(qs, total, limit=limit, offset=offset, tail=tail))
        selected = list(helpers.get_indexable_messages(items))

    data = [
        {
            "line_num": msg.line_num,
            "text": msg.text,
            "role": msg.from_role,
            "timestamp": msg.timestamp,
            "is_final": msg.is_final,
        }
        for msg in selected
    ]

    if tail is not None:
        # ``--tail`` is a window pinned to the end: report the range it really
        # covers, and let ``has_more`` mean "there are messages before it".
        window_offset = max(0, total - tail)
        emit_list(
            data, paginated=paginated, limit=tail, offset=window_offset,
            total=total, has_more=window_offset > 0,
        )
        return

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)


def agents(session_id: str, *, limit: int | None = None, offset: int = 0,
          paginated: bool = False, slim: bool = False) -> None:
    """List subagents of a session as JSON to stdout."""
    import django

    django.setup()
    paginated = pagination_notice("session agents", paginated, default_limit=20)

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session, slim_session

    session = _get_session(session_id)

    if session.parent_session_id is not None:
        emit_error(f"Error: session '{session_id}' is a subagent, not a parent session.", code=1)

    qs = Session.objects.filter(parent_session_id=session_id).order_by("-mtime")
    limit = resolve_limit(limit, paginated=paginated, default=20)
    total = qs.count() if paginated else None
    data = [serialize_session(s) for s in qs[offset : offset + limit]]
    if slim:
        data = [slim_session(row) for row in data]

    # Every row here is a subagent, which runs inside its parent's process and
    # never owns a ProcessRun row. The answer is known without asking, so this
    # resolves no pid and runs no query — but it still emits the key, so the
    # three listing commands keep one projection.
    for row in data:
        row["process"] = None

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)


def plan(session_id: str, *, list_docs: bool = False, doc_path: str | None = None) -> None:
    """Read the session's tracked plan documents (``Session.plan_paths``); print JSON to stdout.

    Three modes:

    - default (no argument): the content of the most recently updated tracked
      document — not necessarily the native Claude plan.
    - a positional path: the content of that document, matched against the
      stored ``path`` (project-relative when the doc lives under the project)
      or its resolved absolute path — never an arbitrary filesystem path.
    - ``--list``: every tracked document, newest first, each entry enriched
      with its resolved ``abs_path`` and fresh ``exists`` (the same entries
      the default session view carries in ``plan_paths``, minus ``abs_path``).
    """
    import os

    import django

    django.setup()

    if list_docs and doc_path:
        emit_error("Error: --list and a positional path are mutually exclusive.", code=1)

    from twicc.providers.plan_docs import resolve_stored_path

    session = _get_session(session_id)

    # Same roots as the compute pipeline's relativization: the session's
    # project directory, then the worktree_of parent's.
    project = session.project
    parent = project.worktree_of if project else None
    roots = [project.directory if project else None, parent.directory if parent else None]

    entries = sorted(
        session.plan_paths or [],
        key=lambda e: e.get("updated_at") or "",
        reverse=True,
    )
    enriched = []
    for entry in entries:
        abs_path = resolve_stored_path(entry.get("path", ""), roots)
        enriched.append({
            **entry,
            "abs_path": abs_path,
            "exists": bool(abs_path and os.path.exists(abs_path)),
        })

    if list_docs:
        emit_json({"plan_paths": enriched})
        return

    if not enriched:
        emit_error(f"Error: no plan documents tracked for session '{session_id}'.", code=1)

    if doc_path:
        requested = os.path.normpath(doc_path)
        entry = next(
            (e for e in enriched if requested in (os.path.normpath(e["path"]), e["abs_path"])),
            None,
        )
        if entry is None:
            emit_error(
                f"Error: no tracked plan document '{doc_path}' for session "
                f"'{session_id}' (see --list for the tracked paths).",
                code=1,
            )
    else:
        entry = enriched[0]

    if not entry["abs_path"]:
        emit_error(f"Error: cannot resolve '{entry['path']}' to an absolute path.", code=1)
    try:
        content = open(entry["abs_path"], encoding="utf-8").read()
    except OSError:
        emit_error(f"Error: plan document '{entry['path']}' is missing on disk.", code=1)
    emit_json({"path": entry["path"], "abs_path": entry["abs_path"], "content": content})


#: The envelope keys a listing drops unless asked for. A denylist, not an
#: allowlist: the ``wf_*.json`` schema is fixed by the Claude Code runtime — 20
#: keys present in every run observed, plus ``args`` when the workflow took some
#: and ``error`` when it failed — and TwiCC's own synthesis emits the same shape.
#: A future key would therefore come from the runtime, most likely a scalar that
#: belongs in the general view; naming what is heavy keeps the default honest
#: instead of freezing today's field list and silently going stale.
#:
#: ``result`` is separated from the rest because "what did it produce" is a
#: different question from "how did it run".
WORKFLOW_RESULT_FIELDS = ("result",)
WORKFLOW_DETAIL_FIELDS = ("workflowProgress", "script", "logs", "args")


def _workflow_envelope(run, session_cutoff=None) -> dict:
    """The run's parsed ``raw_json`` with its ``runId`` key renamed ``id`` (first).

    An orphaned synthetic run (its session restarted or stopped before any
    ``wf_*.json`` landed) is surfaced as ``interrupted`` at read time — derived,
    not stored. ``session_cutoff`` is ``Session.cutoff`` (its lifecycle boundary).
    """
    from twicc.providers.claude_code.workflow_synthesis import apply_orphan_status

    try:
        raw = orjson.loads(run.raw_json)
    except orjson.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        return {"id": run.run_id, "raw": raw}
    apply_orphan_status(raw, run.updated_at, session_cutoff)
    return {"id": raw.pop("runId", run.run_id), **raw}


def workflows(session_id: str, *, limit: int | None = None, offset: int = 0,
             paginated: bool = False, result: bool = False, full: bool = False) -> None:
    """List a session's workflows as JSON to stdout (newest first).

    A run's execution trace is the bulk of its envelope — on the heaviest run
    measured, ``workflowProgress`` alone was 94% of 774 KB, because it carries a
    prompt and a result preview for each of its 99 agents. Listing twenty of
    those returned megabytes to answer "which runs are there".

    So the listing answers that question and no more: name, summary, status,
    timing, counts and phase progress. ``result`` adds what each run produced;
    ``full`` adds everything and reproduces the envelope verbatim, which is also
    what ``session workflow <id>`` gives for a single run.
    """
    import django

    django.setup()
    paginated = pagination_notice("session workflows", paginated, default_limit=20)

    from twicc.core.models import Workflow

    session = _get_session(session_id)

    qs = Workflow.objects.filter(session_id=session_id).order_by("-updated_at")
    limit = resolve_limit(limit, paginated=paginated, default=20)
    total = qs.count() if paginated else None
    data = [_workflow_envelope(w, session.cutoff) for w in qs[offset : offset + limit]]

    if not full:
        dropped = set(WORKFLOW_DETAIL_FIELDS)
        if not result:
            dropped |= set(WORKFLOW_RESULT_FIELDS)
        data = [{k: v for k, v in run.items() if k not in dropped} for run in data]

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)


def workflow(session_id: str, workflow_id: str) -> None:
    """Show one of a session's workflows as JSON to stdout."""
    import django

    django.setup()

    from twicc.core.models import Workflow

    session = _get_session(session_id)

    try:
        run = Workflow.objects.get(run_id=workflow_id, session_id=session_id)
    except Workflow.DoesNotExist:
        emit_error(f"Error: workflow '{workflow_id}' not found for session '{session_id}'.", code=1)

    emit_json(_workflow_envelope(run, session.cutoff))
