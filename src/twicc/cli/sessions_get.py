"""``twicc sessions get <SESSION_ID>...`` sub-command.

Batch fetch sessions by id. Each input session_id produces exactly one
output entry in input order (duplicates collapsed, first occurrence
wins). Sessions are returned **regardless of archived / hidden /
subagent status** — when the caller names an explicit id, layering the
listing filters on top would only blur the meaning of placeholder
entries.

Unknown session_ids (no Session row in the DB) get a placeholder entry
with ``known: false`` and every session field set to ``null`` — the
serializer's keys plus ``CLI_ENRICHED_KEYS`` — so the output shape is
uniform with ``known: true`` entries. Callers can then
``zip(ids, output)`` (``output["items"]`` from 2026-10-01, or with
``--paginated``) and read any field directly, only checking
``known`` when they need to disambiguate.

``process`` is the exception: it is joined from ``ProcessRun``, whose row
is created BEFORE the watcher writes the ``Session`` row, so an unknown id
can legitimately carry a live process block.
"""

from __future__ import annotations

from twicc.cli._output import emit_json, pagination_notice, slim_notice
from twicc.cli._session_payload import CLI_ENRICHED_KEYS, cli_session_payloads


# Cached null-filled template for the placeholder shape. We derive it
# lazily from one real ``serialize_session`` output (rather than
# hardcoding the field list) so the placeholder shape never drifts
# from the canonical serializer, plus the keys the CLI enrichment adds
# (``CLI_ENRICHED_KEYS``).
_PLACEHOLDER_TEMPLATE: dict | None = None


def _build_placeholder_template() -> dict:
    """Derive a {field: None} dict matching ``serialize_session`` output plus ``CLI_ENRICHED_KEYS``.

    Uses any existing Session row as a template source. Falls back to
    ``{"id": None}`` plus ``CLI_ENRICHED_KEYS`` if the DB has no session yet (edge
    case on a fresh install — the placeholder is still consistent within
    a single command invocation).
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    sample = Session.objects.first()
    if sample is None:
        return {"id": None} | dict.fromkeys(CLI_ENRICHED_KEYS)
    return {k: None for k in serialize_session(sample)} | dict.fromkeys(CLI_ENRICHED_KEYS)


def main(session_ids: list[str], *, slim: bool = False, full: bool = False, paginated: bool = False) -> None:
    """Emit one JSON entry per session_id (placeholder when missing).

    The slim mode (``--slim``, or the default past the cutover) applies the same
    projection as ``twicc sessions``, on the placeholders too: a batch whose rows
    changed shape depending on whether the id resolved would be worse than no
    projection at all.
    """
    import django

    django.setup()
    paginated = pagination_notice("sessions get", paginated, default_limit=None, shape="lookup")
    slim = slim_notice("sessions get", slim, full)

    from twicc.core.models import Session

    # Dedupe while preserving caller order: the output mirrors the input
    # 1-to-1 so scripts can zip(ids, output) (output["items"] from 2026-10-01,
    # or with --paginated) without re-mapping.
    unique_ids: list[str] = []
    seen: set[str] = set()
    for sid in session_ids:
        if sid in seen:
            continue
        seen.add(sid)
        unique_ids.append(sid)

    # Batch fetch by id only — no archived/hidden/subagent filter.
    # Explicit ids bypass listing filters by design (mirrors
    # ``twicc session <id>``, which has the same scope).
    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=unique_ids)
    }

    global _PLACEHOLDER_TEMPLATE
    if _PLACEHOLDER_TEMPLATE is None:
        _PLACEHOLDER_TEMPLATE = _build_placeholder_template()

    from twicc.core.serializers import slim_session

    project = slim_session if slim else (lambda entry: entry)

    # Enrich the known rows in one call (one query at most for their projects).
    known_ids = [sid for sid in unique_ids if sid in sessions_by_id]
    enriched = dict(zip(known_ids, cli_session_payloads(sessions_by_id[sid] for sid in known_ids), strict=True))

    results = []
    for sid in unique_ids:
        session = sessions_by_id.get(sid)
        if session is None:
            entry = project(dict(_PLACEHOLDER_TEMPLATE))
            entry["id"] = sid
            entry["known"] = False
        else:
            entry = project(enriched[sid])
            entry["known"] = True
        results.append(entry)

    # Per entry, AFTER the projection — never into _PLACEHOLDER_TEMPLATE, which
    # is a module global the MCP server keeps alive across tool calls: a
    # `process` key written there would leak into every later placeholder.
    #
    # An unknown id is looked up like any other. A ProcessRun row is created
    # before the watcher writes the Session row, so `known: false` with a live
    # block is a session that just started, not a bug.
    from twicc.cli._process_state import (
        attach_process_blocks,
        load_process_rows,
        resolve_listing_twicc_pid,
    )

    attach_process_blocks(
        results,
        load_process_rows(unique_ids, resolve_listing_twicc_pid()),
        slim=slim,
    )

    emit_json({"items": results} if paginated else results)
