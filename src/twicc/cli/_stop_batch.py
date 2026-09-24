"""The shared machinery behind ``processes stop`` and ``sessions stop``.

Both commands answer "stop these agents"; they differ in how they choose the
ids and in the envelope they emit. The drop-per-id submission, the per-id
pre-check, the single-deadline poll loop and the per-id **entry** live here so
the two cannot drift into reporting the same operation differently; each
command shapes its envelope (``processes stop`` a bare array until its removal,
``sessions stop`` ``{summary, results}``), and only ``sessions stop`` passes
``caller_id``.

The selection stays at the call site: ``processes stop`` takes explicit ids
plus filiation scopes, ``sessions stop`` takes the whole ``sessions`` filter
family narrowed to what is actually running.
"""

from __future__ import annotations

import time

POLL_INTERVAL_SECONDS = 0.1

# Map :class:`SessionLookupError.code` → CLI ``status`` value. The mapping is
# exhaustive against the codes ``lookup_session`` raises: any unmapped code
# falls back to ``"skipped_unknown"`` defensively, but a new code added there
# should be reflected here.
_LOOKUP_CODE_TO_SKIP_STATUS = {
    "session_not_found": "skipped_unknown",
    "is_subagent": "skipped_subagent",
    "session_stale": "skipped_stale",
    "project_no_directory": "skipped_no_directory",
    "unknown_provider": "skipped_unknown_provider",
}


def stop_session_ids(
    unique_ids, *, timeout: int, force: bool, twicc_pid: int, caller_id: str | None = None,
) -> list[dict]:
    """Stop every id, and return one outcome per id in the given order.

    Idempotent by construction: asking to stop a session whose process is
    already gone still reports ``status="stopped"``.

    ``caller_id`` is the calling session, never stopped: its entry gets
    ``status="skipped_self"`` and an ``error`` pointing to ``session self
    stop``, and no drop is submitted for it. ``None`` skips nothing.
    """
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.core.models import ProcessRun, Session

    info = type("I", (), {"pid": twicc_pid})()

    # --- Batch metadata fetch (one query each) ---------------------------

    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=unique_ids).only(
            "id", "title", "project_id", "provider"
        )
    }
    # session_known also fires when only a ProcessRun exists (rare:
    # brand-new session not yet seen by the JSONL watcher). It does NOT
    # bypass the lookup_session pre-check — the entry will still be
    # ``skipped_unknown`` if Session is missing — but it documents in
    # the output that TwiCC has *some* trace of the id.
    process_sids = set(
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id__in=unique_ids)
        .values_list("session_id", flat=True)
    )

    # --- Per-id pre-check + drop -----------------------------------------

    # ``outcomes`` is keyed by sid so the final array can be rebuilt in
    # ``unique_ids`` order without searching. ``initial_drops`` holds the
    # (drop, status_path) for every entry that survived the pre-check; we
    # use it as the cleanup source-of-truth so the ``finally`` doesn't
    # have to reason about which entries are still pending.
    outcomes: dict[str, dict] = {}
    initial_drops: list[tuple[str, object, object]] = []  # (sid, drop, status_path)

    for sid in unique_ids:
        session = sessions_by_id.get(sid)
        session_known = session is not None or sid in process_sids
        entry = {
            "session_id": sid,
            "session_known": session_known,
            "status": None,
            "request_uuid": None,
            "provider": session.provider if session is not None else None,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "error": None,
        }
        outcomes[sid] = entry

        if sid == caller_id:
            entry["status"] = "skipped_self"
            entry["error"] = (
                "The calling session is never stopped by `sessions stop`; "
                "use `session self stop`."
            )
            continue

        try:
            resolved = lookup_session(sid)
        except SessionLookupError as e:
            entry["status"] = _LOOKUP_CODE_TO_SKIP_STATUS.get(
                e.code, "skipped_unknown"
            )
            entry["error"] = e.message
            continue

        payload = {"session_id": resolved.session_id}
        if force:
            payload["force"] = True
        sub = transport.submit(payload, kind="process:stop")
        entry["request_uuid"] = sub.request_uuid
        initial_drops.append((sid, sub))

    # --- Cumulative poll until all pending are resolved or timeout -------

    pending = list(initial_drops)
    deadline = time.time() + timeout
    try:
        while pending and time.time() < deadline:
            still_pending = []
            for sid, sub in pending:
                outcome = sub.poll()
                if outcome is None:
                    # Missing, mid-rename, or "received" — keep polling.
                    still_pending.append((sid, sub))
                    continue
                status = outcome.status
                data = outcome.data
                entry = outcomes[sid]
                entry["status"] = status
                if status == "rejected":
                    errors = data.get("errors", [])
                    entry["error"] = (
                        "; ".join(
                            f"{e.get('code')}: {e.get('message')}"
                            for e in errors
                        )
                        if errors else None
                    )
                elif status == "failed":
                    entry["error"] = data.get("error")
            pending = still_pending
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Anything still pending after the deadline = timeout.
        for sid, _ in pending:
            outcomes[sid]["status"] = "timeout"
            outcomes[sid]["error"] = (
                f"No final status within {timeout}s "
                "(server received the request but did not finish in time)"
            )
    finally:
        # Always clean up the drop + status files we created, even if the
        # poll loop raised. ``missing_ok=True`` covers the server having
        # already deleted them.
        for _, sub in initial_drops:
            sub.cleanup()

    # --- Return the entries in input order (each command shapes its envelope) ---

    return [outcomes[sid] for sid in unique_ids]
