"""Shared batch runner for fan-out CLI commands (``update-sessions``, ``send-messages``).

Generalises a single-session drop-request flow to a set of sessions: resolve the
target ids (explicit ids merged with the optional ``--spawned-by`` /
``--descendants`` / ``--siblings`` / ``--annotation`` scope, same union semantics
as ``twicc sessions`` / ``processes stop`` — explicit ids first, scope-selected
ids appended, deduplicated), drop one request per id reusing the *exact same*
``kind`` + payload the singular command would emit, poll every status file under
a single ``--timeout`` wall-clock budget (the watcher processes the drops in
parallel), then emit one aggregated result.

The result is an object keyed by session_id::

    {
      "summary": {"total", "succeeded", "failed", "all_succeeded"},
      "results": {"<session_id>": <per-id outcome>, ...}
    }

Each per-id outcome is exactly what the singular command would have emitted for
that id alone: the success status (``updated`` for ``update-session*``, ``sent``
for ``send-message*``) / ``rejected`` / ``failed`` / ``timeout`` via
:func:`twicc.cli._drop_request.output.build_final`, or ``validation_error`` when
the local ``lookup_session`` pre-check (or the per-id ``prepare``) fails before
any drop. A per-id failure never fails the batch — only command-level concerns
do.

Exit codes:

- 0  — the batch ran and at least one session succeeded, OR the resolved id set
       was empty (nothing to do is not a failure)
- 1  — local argument error (bad ``--timeout``, mutually-exclusive scopes,
       ``--annotation`` without a filiation scope, ``parent`` scope (on
       ``--spawned-by`` / ``--descendants``, or any value on ``--siblings``),
       neither ids nor scope, or an unresolvable ``self``)
- 2  — TwiCC server is not running
- 6  — the resolved id set was non-empty but NOT ONE session succeeded
- 64 — bad CLI usage (handled by Typer)
"""

from __future__ import annotations

import time
from collections.abc import Callable

import typer

from twicc.cli._output import emit_error, emit_json


POLL_INTERVAL_SECONDS = 0.1


def run_batch(
    session_ids: list[str],
    *,
    kind: str,
    prepare: Callable[..., dict | list],
    timeout: int,
    success_status: str = "updated",
    spawned_by: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
    after_send: Callable[[dict, dict], None] | None = None,
) -> None:
    """Drop one ``kind`` request per resolved session and emit the batch result.

    ``prepare(resolved)`` returns either the per-id drop payload (a dict that
    must include ``session_id``), a flat ``list`` of ``ValidationError`` when
    this session can't accept the request (e.g. a ``settings`` value invalid for
    its provider, or an attachment its provider rejects) — in that case the id
    gets a per-id ``validation_error`` and no drop — or ``None`` when there is
    nothing to apply for this session (every touched setting is a no-op for its
    provider, e.g. ``--thinking`` on Codex) — that id gets a per-id ``noop``
    status and no drop. A ``noop`` counts as a success (nothing-to-do is not a
    failure). The provider-agnostic ops pass a ``prepare`` that always returns a
    payload.

    ``success_status`` is the watcher's success token for this ``kind``
    (``"updated"`` for the update services, ``"sent"`` for send-message): it is
    what the poll loop treats as a final success and what ``summary.succeeded``
    counts.
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import build_final
    from twicc.cli._drop_request.polling import PollOutcome
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )

    # --- Argument validation (global, fatal — the "classic" errors) ------

    if timeout <= 0:
        emit_error(f"Error: --timeout must be > 0 (got {timeout}).", code=1)

    # ``--siblings`` rejects ``parent`` at the resolver level too, but catch it
    # here so every parent rejection lands up-front (exit 1) before the
    # server-up check — same ordering as --spawned-by / --descendants, instead
    # of leaking through to a misleading "server down" (exit 2) when offline.
    if spawned_by == "parent" or descendants == "parent" or siblings == "parent":
        emit_error(
            "Error: parent-scoped filters are not supported here. "
            "Use 'self' or an explicit session_id.",
            code=1,
        )

    if sum(x is not None for x in (spawned_by, descendants, siblings)) > 1:
        emit_error(
            "Error: --spawned-by, --descendants and --siblings are mutually exclusive.",
            code=1,
        )

    filiation_scope = any((spawned_by, descendants, siblings))
    if annotation and not filiation_scope:
        emit_error(
            "Error: --annotation requires --spawned-by, --descendants or --siblings.",
            code=1,
        )

    has_scope = filiation_scope or bool(annotation)
    if not session_ids and not has_scope:
        emit_error(
            "Error: no session_ids or filters given. Pass at least one "
            "session_id, or select sessions with --spawned-by, --descendants "
            "or --siblings. Use --annotation only to narrow that scope.",
            code=1,
        )

    # --- Server-up check (exit 2 mirrors the singular command) -----------

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    # --- Resolve 'self' in the explicit id list --------------------------
    # ``merge_session_scope_ids`` only resolves self/parent for the filiation
    # filters, not for explicit ids. Mirror the singular ``<cmd> self``
    # ergonomics by resolving it here (deduplication happens in merge).
    if session_ids and "self" in session_ids:
        from twicc.cli._drop_request.whoami import resolve_current_session

        current = resolve_current_session()
        if current is None:
            emit_error(
                "Error: 'self' could not be resolved: no TwiCC session found "
                "in PID ancestry.",
                code=1,
            )
        session_ids = [current.id if s == "self" else s for s in session_ids]

    # --- Resolve explicit ids + optional scope filters -------------------

    try:
        from twicc.cli._session_scope import merge_session_scope_ids

        unique_ids = merge_session_scope_ids(
            session_ids,
            spawned_by=spawned_by,
            descendants=descendants,
            siblings=siblings,
            annotation=annotation,
        )
    except (RuntimeError, ValueError) as e:
        emit_error(f"Error: {e}", code=1)

    if not unique_ids:
        emit_json({
            "summary": {
                "total": 0, "succeeded": 0, "failed": 0, "all_succeeded": True,
            },
            "results": {},
        })
        raise typer.Exit(0)

    # --- Per-id pre-check + drop -----------------------------------------

    # ``results`` is keyed by sid so the final object keeps ``unique_ids``
    # order. Lookup / prepare failures land here immediately (validation_error,
    # no drop); survivors get a placeholder filled in after polling.
    results: dict[str, dict | None] = {}
    pending: list[tuple[str, transport.Submission]] = []  # (sid, submission)

    for sid in unique_ids:
        try:
            resolved = lookup_session(sid)
        except SessionLookupError as e:
            results[sid] = {
                "status": "validation_error",
                "errors": [{
                    "field": "SESSION_ID",
                    "code": e.code,
                    "message": e.message,
                }],
            }
            continue

        outcome = prepare(resolved)
        if outcome is None:
            # Nothing to apply for this session (every touched field is a no-op
            # for its provider, e.g. --thinking on Codex). Not an error, not a
            # drop — a silent no-op, counted as a success below.
            results[sid] = {"status": "noop", "session_id": sid}
            continue
        if isinstance(outcome, list):
            # Per-id validation errors (e.g. settings invalid for this provider,
            # or an attachment its provider rejects).
            results[sid] = {
                "status": "validation_error",
                "errors": [e._asdict() for e in outcome],
            }
            continue

        results[sid] = None
        pending.append((sid, transport.submit(outcome, kind=kind)))

    # --- Cumulative poll until all pending are resolved or timeout -------

    received_seen: dict[str, bool] = {sid: False for sid, _ in pending}
    still = list(pending)
    deadline = time.time() + timeout
    try:
        while still and time.time() < deadline:
            next_round: list[tuple[str, transport.Submission]] = []
            for sid, sub in still:
                if not received_seen[sid] and sub.was_received():
                    received_seen[sid] = True
                outcome = sub.poll()
                if outcome is None:
                    # Still pending (missing, mid-rename, or "received").
                    next_round.append((sid, sub))
                    continue
                results[sid] = build_final(
                    outcome, request_uuid=sub.request_uuid, timeout=timeout,
                )
            still = next_round
            if still:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Anything still pending after the deadline = timeout.
        for sid, sub in still:
            outcome = PollOutcome(
                status=None, data=None, received_seen=received_seen[sid],
            )
            results[sid] = build_final(
                outcome, request_uuid=sub.request_uuid, timeout=timeout,
            )
    finally:
        # Always clean up the drop + status files we created, even on a raise.
        for _, sub in pending:
            sub.cleanup()

    # --- Aggregate + emit in input order ---------------------------------

    ordered = {sid: results[sid] for sid in unique_ids}
    total = len(ordered)
    # A ``noop`` (nothing to apply for the session's provider) is a success, not
    # a failure: the request was honored, it just had no effect for that session.
    succeeded = sum(
        1 for v in ordered.values()
        if v and v.get("status") in (success_status, "noop")
    )
    failed = total - succeeded

    summary = {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "all_succeeded": total > 0 and failed == 0,
    }
    if after_send is not None:
        # Runs before the emission, and may add to both: `send-messages`
        # uses it to wait for the answers and attach a `reply` per entry.
        # Keeping the emission and the exit code in one place is the point.
        after_send(ordered, summary)

    emit_json({"summary": summary, "results": ordered})

    # ``total`` is > 0 here (the empty set returned earlier). Zero successes
    # while every argument was valid → distinct exit 6 so a script can detect
    # a total failure without parsing the JSON.
    if succeeded == 0:
        raise typer.Exit(6)
    raise typer.Exit(0)
