"""``twicc workspaces get <WORKSPACE_ID>...`` sub-command.

Batch fetch workspaces by id from ``<data_dir>/workspaces.json``. Each
input ``workspace_id`` produces exactly one output entry in input order
(duplicates collapsed, first occurrence wins). Archived workspaces are
returned just like active ones — when the caller names explicit ids,
the listing's archived-by-default filter doesn't apply (mirrors the
singular ``twicc workspace <ID>``, whose docstring spells out this
choice: "Returns the workspace even when archived (the caller has the
explicit ID)").

Unknown workspace_ids get a placeholder entry with ``known: false``
and every other field set to ``null`` so the output shape is uniform
with ``known: true`` entries.

No Django setup is needed: workspaces live in a JSON file, not the DB.
"""

from __future__ import annotations

from twicc.cli._output import emit_json, pagination_notice


def main(workspace_ids: list[str], *, paginated: bool = False) -> None:
    """Emit one JSON entry per workspace_id (placeholder when missing)."""
    paginated = pagination_notice("workspaces get", paginated, default_limit=None, shape="lookup")

    from twicc.workspaces import read_workspaces

    # Dedupe while preserving caller order.
    unique_ids: list[str] = []
    seen: set[str] = set()
    for wid in workspace_ids:
        if wid in seen:
            continue
        seen.add(wid)
        unique_ids.append(wid)

    all_workspaces = read_workspaces().get("workspaces", [])
    workspaces_by_id = {w["id"]: w for w in all_workspaces if "id" in w}

    # Build the placeholder shape as the union of every key seen in
    # workspaces.json — this way the placeholder mirrors whatever the
    # file actually contains, even if individual entries omit
    # optional fields like ``color`` or ``autoProjectPatterns``.
    template_keys: set[str] = set()
    for ws in all_workspaces:
        template_keys.update(ws.keys())
    template = {k: None for k in template_keys} if template_keys else {"id": None}

    results = []
    for wid in unique_ids:
        ws = workspaces_by_id.get(wid)
        if ws is None:
            entry = dict(template)
            entry["id"] = wid
            entry["known"] = False
        else:
            entry = dict(ws)
            entry["known"] = True
        results.append(entry)

    emit_json({"items": results} if paginated else results)
