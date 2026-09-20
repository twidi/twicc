"""Selection shared by the plural session commands that act rather than list.

``sessions stop`` and ``sessions wait-reply`` pick their targets the same way:
the listing's filters through :func:`~twicc.cli.sessions.build_filtered_queryset`,
plus explicit ids **unioned** on top — naming one is an addition to the scope,
never a replacement for it. The two had a copy each, with identical error
messages, and had already drifted apart on which states they refuse.

What stays per-command is what they do with the selection, and what they refuse:
``stop`` is bounded by the live process set and can afford a bare call, while a
bare wait would poll every indexed session until its deadline.
"""

from __future__ import annotations

from twicc.cli._output import emit_error

#: The four filiation scopes, which narrow in incompatible ways.
SCOPE_FLAGS = ("--spawned-by", "--spawn-tree", "--descendants", "--siblings")


def reject_conflicting_scopes(spawned_by, spawn_tree, descendants, siblings,
                              *, project=None, workspace=None) -> None:
    """Refuse the flag combinations the listing refuses, before anything is read."""
    given = [
        name for name, value in zip(
            SCOPE_FLAGS, (spawned_by, spawn_tree, descendants, siblings), strict=True,
        ) if value
    ]
    if len(given) > 1:
        emit_error(f"Error: {' and '.join(given)} are mutually exclusive.", code=1)
    if project and workspace:
        emit_error("Error: --project and --workspace are mutually exclusive.", code=1)


def resolve_explicit_ids(session_ids) -> list[str]:
    """The explicit ids, de-duplicated, with ``self`` / ``parent`` resolved.

    Order is the caller's: a batch result is read against the input, so the
    first id named stays the first reported.
    """
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
