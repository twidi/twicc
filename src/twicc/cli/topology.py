"""``twicc topology`` — inspect the spawned-session tree around a session."""

from __future__ import annotations

from decimal import Decimal

from twicc.cli._output import emit_error, emit_json, slim_notice
from twicc.cli._process_state import load_process_rows, serialize_compact_process


# Fields kept in each ``nodes[].session`` block by default. The caller can opt
# into the full ``serialize_session()`` shape with ``--full``; the slim
# shape is enough to render the tree and identify nodes, and any other field can
# be recovered for a specific node via ``twicc session <id> --full``. The synthetic
# ``directory`` field (``git_directory`` falling back to ``cwd``) is added on
# top of these by ``_slim_session``.
TOPOLOGY_SESSION_FIELDS = (
    "id",
    "project_id",
    "provider",
    "title",
    "annotations",
    "spawned_by",
    "spawn_root",
    "created_at",
    "last_new_content_at",
    "context_usage",
    "context_max",
    "total_cost",
)


def main(
    session_id: str,
    *,
    include_processes: bool = True,
    slim: bool = False,
    full: bool = False,
    annotation: list[str] | None = None,
    siblings: bool = False,
) -> None:
    """Emit the spawned-session tree containing ``session_id`` as JSON."""
    import django

    django.setup()
    # The cutover only changes the `process` block: without one there is
    # nothing to announce. ``full`` alone decides the session projection.
    slim_processes = (
        slim_notice("topology", slim, full, kind="topology") if include_processes else False
    )

    from twicc.core.models import SessionType

    seed = _resolve_seed(session_id)
    if seed.type != SessionType.SESSION:
        emit_error(
            f"Error: session '{seed.id}' is a subagent; topology follows "
            "spawned_by, not parent_session_id.",
            code=1,
        )

    annotation_filters = None
    if annotation:
        from twicc.cli._annotation_filters import parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            emit_error(f"Error: {exc}", code=2)

    data = build_topology(
        seed,
        include_processes=include_processes,
        full_sessions=full,
        slim_processes=slim_processes,
        annotation_filters=annotation_filters,
        mark_siblings=siblings,
    )
    emit_json(data)


def build_topology(
    seed,
    *,
    include_processes: bool = True,
    full_sessions: bool = False,
    slim_processes: bool = False,
    twicc_pid: int | None = None,
    annotation_filters: list | None = None,
    mark_siblings: bool = False,
) -> dict:
    """Build the spawned-session topology containing ``seed``.

    ``twicc_pid`` is injectable for tests. When omitted and process data is
    requested, the live TwiCC sidecar is resolved; if unavailable, topology is
    still returned with process data marked unavailable.

    ``slim_processes`` reduces each read ``process`` block to ``{state}``. Its
    default keeps the five fields, which the REST view relies on: only the CLI
    resolves it from ``--slim`` / ``--full`` and the cutover.

    ``annotation_filters`` preserves the full tree but enriches every node with
    a ``matches_annotations`` flag when provided.

    ``mark_siblings`` similarly preserves the full tree but enriches every node
    with a ``matches_siblings`` flag — True for the ``seed``'s siblings (the
    other sessions spawned by the seed's parent, seed excluded). A seed with no
    spawner (a root) has no siblings, so every flag is False.
    """
    from twicc.core.models import ProcessRun

    (
        root,
        path_to_seed,
        sessions_by_id,
        children_by_parent,
        cycle_detected,
    ) = _load_topology_sessions(seed)

    # Second query: same spawn_root filter + annotation filter.
    # The full tree above is preserved; this set drives the
    # matches_annotations flag per node — see spec §5.4.
    matching_ids: set[str] | None = None
    if annotation_filters:
        from twicc.cli._annotation_filters import apply_annotation_filters
        from django.db.models import Q
        from twicc.core.models import Session

        # `Q(spawn_root_id=root.id) | Q(pk=root.id)` includes root in the match
        # universe even when its own spawn_root_id is NULL (standalone session
        # or root before its first child is ever spawned).
        # Note: spec §5.4 pseudocode uses `filter(spawn_root_id=root.id)` alone,
        # which misses standalone roots. This diverges intentionally from the spec.
        matching_ids = set(
            apply_annotation_filters(
                Session.objects.filter(Q(spawn_root_id=root.id) | Q(pk=root.id)),
                annotation_filters,
            ).values_list("id", flat=True)
        )

    # The seed's siblings are the other sessions sharing the seed's parent.
    # The whole tree is already loaded in memory (sessions_by_id), so the set is
    # derived without an extra query. A rootless seed (no spawned_by) has none.
    sibling_ids: set[str] | None = None
    if mark_siblings:
        seed_parent_id = seed.spawned_by_id
        sibling_ids = (
            {
                sid
                for sid, session in sessions_by_id.items()
                if session.spawned_by_id == seed_parent_id and sid != seed.id
            }
            if seed_parent_id is not None
            else set()
        )

    tree = _build_tree(root.id, children_by_parent)
    ordered_ids = list(_walk_tree_ids(tree))
    metrics_by_id = _compute_node_metrics(
        root.id,
        children_by_parent,
        sessions_by_id,
    )

    process_rows_by_id: dict[str, ProcessRun] = {}
    processes_available = False
    if include_processes:
        if twicc_pid is None:
            from twicc.cli._twicc_info import resolve_live_twicc

            info = resolve_live_twicc()
            twicc_pid = info.pid if info is not None else None
        if twicc_pid is not None:
            processes_available = True
            process_rows_by_id = load_process_rows(ordered_ids, twicc_pid)

    nodes = [
        _serialize_topology_node(
            sessions_by_id[session_id],
            process_rows_by_id.get(session_id),
            processes_available=processes_available,
            metrics=metrics_by_id[session_id],
            full_sessions=full_sessions,
            slim_processes=slim_processes,
            matching_ids=matching_ids,
            sibling_ids=sibling_ids,
        )
        for session_id in ordered_ids
    ]

    return {
        "seed_session_id": seed.id,
        "root_session_id": root.id,
        "path_to_seed": path_to_seed,
        "cycle_detected": cycle_detected,
        "total_cost": metrics_by_id[root.id]["subtree_total_cost"],
        "node_count": len(ordered_ids),
        "processes": {
            "requested": include_processes,
            "available": processes_available,
            "reason": _processes_reason(include_processes, processes_available),
        },
        "tree": tree,
        "nodes": nodes,
    }


def _resolve_seed(session_id: str):
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.core.models import Session

    if session_id == "self":
        session = resolve_current_session()
        if session is None:
            emit_error(
                "Error: self could not be resolved: no TwiCC session found in PID ancestry.",
                code=1,
            )
        return session

    try:
        return Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        emit_error(f"Error: session '{session_id}' not found.", code=1)


def _load_topology_sessions(seed) -> tuple:
    """Load the topology containing ``seed``.

    Spawned sessions carry ``spawn_root`` so the whole tree can be loaded in
    one query. Ordinary sessions with no ``spawn_root`` are single-node trees
    until they spawn a child.
    """
    root = _get_session(seed.spawn_root_id) if seed.spawn_root_id is not None else None
    if root is None:
        root = seed

    return _build_loaded_topology(seed, root, _get_spawn_root_sessions(root.id))


def _get_spawn_root_sessions(root_id: str) -> list:
    from twicc.core.models import Session

    return list(
        Session.objects
        .filter(spawn_root_id=root_id)
        .order_by("created_at", "id")
    )


def _build_loaded_topology(seed, root, descendants: list) -> tuple:
    sessions_by_id = {root.id: root}
    for session in descendants:
        sessions_by_id.setdefault(session.id, session)

    children_by_parent: dict[str, list[str]] = {}
    cycle_detected = False
    for session in sessions_by_id.values():
        if session.id == root.id:
            continue
        if session.spawned_by_id == session.id:
            cycle_detected = True
            continue
        if session.spawned_by_id in sessions_by_id:
            children_by_parent.setdefault(session.spawned_by_id, []).append(session.id)

    path_to_seed, path_cycle_detected = _build_path_to_seed(
        seed.id,
        root.id,
        sessions_by_id,
    )
    return (
        root,
        path_to_seed,
        sessions_by_id,
        children_by_parent,
        cycle_detected or path_cycle_detected,
    )


def _build_path_to_seed(
    seed_id: str,
    root_id: str,
    sessions_by_id: dict,
) -> tuple[list[str], bool]:
    current_id = seed_id
    path = []
    seen = set()
    cycle_detected = False

    while True:
        if current_id in seen:
            cycle_detected = True
            break
        seen.add(current_id)

        session = sessions_by_id.get(current_id)
        if session is None:
            break
        path.append(current_id)
        if current_id == root_id:
            break
        if session.spawned_by_id is None:
            break
        current_id = session.spawned_by_id

    path.reverse()
    return path, cycle_detected


def _get_session(session_id: str):
    from twicc.core.models import Session, SessionType

    return (
        Session.objects
        .filter(id=session_id, type=SessionType.SESSION)
        .first()
    )


def _build_tree(session_id: str, children_by_parent: dict[str, list[str]]) -> dict:
    return {
        "id": session_id,
        "children": [
            _build_tree(child_id, children_by_parent)
            for child_id in children_by_parent.get(session_id, [])
        ],
    }


def _walk_tree_ids(node: dict):
    yield node["id"]
    for child in node["children"]:
        yield from _walk_tree_ids(child)


def _compute_node_metrics(
    root_id: str,
    children_by_parent: dict[str, list[str]],
    sessions_by_id: dict,
) -> dict[str, dict]:
    metrics_by_id = {}

    def visit(session_id: str) -> tuple[int, Decimal, bool]:
        direct_children = children_by_parent.get(session_id, [])
        descendant_count = 0
        total_cost = Decimal(0)
        has_cost = False

        session_cost = sessions_by_id[session_id].total_cost
        if session_cost is not None:
            total_cost += session_cost
            has_cost = True

        for child_id in direct_children:
            child_descendant_count, child_total_cost, child_has_cost = visit(child_id)
            descendant_count += 1 + child_descendant_count
            if child_has_cost:
                total_cost += child_total_cost
                has_cost = True

        metrics_by_id[session_id] = {
            "direct_child_count": len(direct_children),
            "descendant_count": descendant_count,
            "subtree_total_cost": float(total_cost) if has_cost else None,
        }
        return descendant_count, total_cost, has_cost

    visit(root_id)
    return metrics_by_id


def _serialize_topology_node(
    session,
    process_row,
    *,
    processes_available: bool,
    metrics: dict,
    full_sessions: bool,
    slim_processes: bool = False,
    matching_ids: set[str] | None = None,
    sibling_ids: set[str] | None = None,
) -> dict:
    from twicc.core.serializers import serialize_session

    serialized = serialize_session(session)
    session_payload = serialized if full_sessions else _slim_session(serialized)

    node = {
        "id": session.id,
        "session": session_payload,
        # ``None`` when processes were not read at all — topology says so in its
        # own ``processes`` envelope, so the node does not repeat the reason.
        "process": (
            serialize_compact_process(process_row, slim=slim_processes)
            if processes_available
            else None
        ),
        **metrics,
    }
    if matching_ids is not None:
        node["matches_annotations"] = session.id in matching_ids
    if sibling_ids is not None:
        node["matches_siblings"] = session.id in sibling_ids
    return node


def _slim_session(serialized: dict) -> dict:
    """Project the full session payload down to the topology default shape."""
    slim = {field: serialized[field] for field in TOPOLOGY_SESSION_FIELDS}
    slim["directory"] = serialized["git_directory"] or serialized["cwd"]
    return slim


def _processes_reason(requested: bool, available: bool) -> str | None:
    if not requested:
        return "not_requested"
    if not available:
        return "twicc_not_running"
    return None
