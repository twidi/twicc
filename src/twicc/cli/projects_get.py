"""``twicc projects get <PROJECT_ID>...`` sub-command.

Batch fetch projects by id. Each input ``project_id`` produces exactly
one output entry in input order (duplicates collapsed, first occurrence
wins). Projects are returned **regardless of archived status** — when
the caller names an explicit id, the listing's archived-by-default
filter doesn't apply (mirrors the singular ``twicc project <ID>``).

Unknown project_ids get a placeholder entry with ``known: false`` and
every other field set to ``null`` so the output shape is uniform with
``known: true`` entries.

The leading-dash normalization (``home/foo`` → ``-home/foo``) is done
by the Typer wrapper, not here — incoming ids are already normalized.
"""

from __future__ import annotations

from twicc.cli._output import emit_json, pagination_notice


# Cached null-filled template for the placeholder shape. Derived lazily
# from one real ``serialize_project`` output (rather than hardcoding the
# field list) so the placeholder shape never drifts from the canonical
# serializer.
_PLACEHOLDER_TEMPLATE: dict | None = None


def _build_placeholder_template() -> dict:
    """Derive a {field: None} dict matching the listing's per-project shape.

    The listing adds ``workspaces`` and ``worktrees`` fields on top of
    ``serialize_project`` — we mirror that here so the placeholder has the
    same keys.
    """
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    sample = Project.objects.first()
    if sample is None:
        return {"id": None, "workspaces": None, "worktrees": None}
    template = {k: None for k in serialize_project(sample)}
    template["workspaces"] = None
    template["worktrees"] = None
    return template


def main(project_ids: list[str], *, paginated: bool = False) -> None:
    """Emit one JSON entry per project_id (placeholder when missing).

    ``project_ids`` must already be normalized (leading dash). The
    Typer wrapper handles the normalization before calling.
    """
    import django

    django.setup()
    paginated = pagination_notice("projects get", paginated, default_limit=None, shape="lookup")

    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project
    from twicc.project_icons import load_repo_icon_cache
    from twicc.projects import worktree_children_by_main
    from twicc.workspaces import read_workspaces

    # Dedupe while preserving caller order: the output mirrors the input
    # 1-to-1 so scripts can zip(ids, output) (output["items"] from 2026-10-01,
    # or with --paginated) without re-mapping.
    unique_ids: list[str] = []
    seen: set[str] = set()
    for pid in project_ids:
        if pid in seen:
            continue
        seen.add(pid)
        unique_ids.append(pid)

    # Batch fetch by id only (no archived filter — explicit ids bypass
    # listing filters by design).
    projects_by_id = {
        p.id: p
        for p in Project.objects.filter(id__in=unique_ids)
    }

    # Build project_id → [workspace_id] index once (same source as the
    # listing) so each known entry carries its workspace memberships.
    all_workspaces = read_workspaces().get("workspaces", [])
    workspaces_by_project: dict[str, list[str]] = {}
    for ws in all_workspaces:
        for pid in ws.get("projectIds", []):
            workspaces_by_project.setdefault(pid, []).append(ws["id"])

    # Reverse of ``worktree_of``: main-repo id -> [worktree child ids] for the
    # requested ids (one query), so each known entry carries its git worktrees.
    worktrees_by_main = worktree_children_by_main(unique_ids)

    # Warm the repo-icon cache once (a short-lived CLI process never ran startup
    # discovery) so each ``repo_icon_url`` brick is populated rather than ``None``.
    load_repo_icon_cache()

    global _PLACEHOLDER_TEMPLATE
    if _PLACEHOLDER_TEMPLATE is None:
        _PLACEHOLDER_TEMPLATE = _build_placeholder_template()

    results = []
    for pid in unique_ids:
        project = projects_by_id.get(pid)
        if project is None:
            entry = dict(_PLACEHOLDER_TEMPLATE)
            entry["id"] = pid
            entry["known"] = False
        else:
            entry = serialize_project(project)
            entry["workspaces"] = workspaces_by_project.get(pid, [])
            entry["worktrees"] = worktrees_by_main.get(pid, [])
            entry["known"] = True
        results.append(entry)

    emit_json({"items": results} if paginated else results)
