"""Per-project agent-settings defaults: backend chain resolution (creation-only).

Backend mirror of ``frontend/src/utils/projectAgentDefaults.js``. Given the
defaults-relevant slice of every project, walk a project's ancestor chain
(worktree main repo first, else nearest registered path ancestor, recursively)
and resolve — field by field — the inherited agent-settings defaults and the
inherited default provider.

This resolver runs at **session creation only** (the CLI ``create-session``
materializes the resolved values into the payload, exactly like the frontend
freezes them onto a draft — see
``docs/archives/plans/2026-06-09-project-agent-defaults-design.md`` §4, "snapshot at
creation"). It must NEVER be wired into the per-turn resolution sites
(``resolve_agent_settings`` and its callers): re-resolving a running session
against the project chain is precisely the option-B bug the design removed.

Unlike trust (``twicc/trust.py``) there is NO propagation gate: every ancestor
is visited and, for each field, the first non-null value wins. The chain is
also UNFILTERED — an ancestor with no own defaults is still a step up, because
a different field may be set higher.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class ProjectDefaultsRow(NamedTuple):
    """The defaults-relevant slice of a ``Project``, loaded once for resolution."""

    id: str
    directory: str | None
    worktree_of_id: str | None
    default_provider: str | None
    # ``{ "<provider>": { "<AgentSettings field>": value } }`` or ``None``.
    default_agent_settings: dict | None
    # Default layout id (named-layout id / "single-pane" / None=inherit). Used by the
    # sibling layout resolver (twicc/project_layout_default.py), which reuses this row + the
    # chain walk here; the agent-defaults resolution ignores it.
    default_layout_id: str | None


def _segments(path: str) -> list[str]:
    """Normalized, non-empty path segments (pure — same rules as twicc/trust.py)."""
    return [s for s in os.path.normpath(path).split(os.sep) if s]


def _is_path_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff *ancestor* is a strict directory prefix of *descendant*.

    Compared segment-by-segment (not ``str.startswith``) so ``/a/b`` is not an
    ancestor of ``/a/bc``. Both paths are assumed already realpath-normalized
    (``Project.directory`` is stored realpath'd); ``normpath`` only tidies them.
    """
    a, d = _segments(ancestor), _segments(descendant)
    return len(a) < len(d) and d[: len(a)] == a


def _nearest_path_ancestor(
    target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]
) -> ProjectDefaultsRow | None:
    """The nearest (longest-path) registered ancestor of *target* — UNFILTERED.

    Any registered project that is a strict path-prefix qualifies, regardless
    of whether it carries its own defaults (a different field may be set on it,
    or further up through it).
    """
    if not target.directory:
        return None
    best: ProjectDefaultsRow | None = None
    best_len = -1
    for row in rows:
        if row.id == target.id or not row.directory:
            continue
        if _is_path_ancestor(row.directory, target.directory):
            depth = len(_segments(row.directory))
            if depth > best_len:
                best, best_len = row, depth
    return best


def ancestor_chain(
    target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]
) -> list[ProjectDefaultsRow]:
    """Ordered ancestor chain ``[self, parent, grandparent, ...]``.

    At each node the parent is its ``worktree_of`` main repo if set, else its
    nearest registered path ancestor. Cycle-guarded against pathological
    ``worktree_of`` loops.
    """
    by_id = {r.id: r for r in rows}
    chain: list[ProjectDefaultsRow] = []
    seen: set[str] = set()
    node: ProjectDefaultsRow | None = target
    while node is not None and node.id not in seen:
        chain.append(node)
        seen.add(node.id)
        if node.worktree_of_id:
            node = by_id.get(node.worktree_of_id)
        elif node.directory:
            node = _nearest_path_ancestor(node, rows)
        else:
            node = None
    return chain


def resolve_agent_defaults(
    target: ProjectDefaultsRow, provider: str, rows: list[ProjectDefaultsRow]
) -> dict:
    """Resolve the inherited per-project agent-settings defaults for *provider*.

    Returns a ``{field: value}`` map holding only the fields the chain actually
    sets; a missing field means "fall back to the global default" (the caller
    applies that global fallback). Field keys are the canonical wire names,
    including the default-shaping ``permission_mode_if_untrusted``.
    """
    resolved: dict = {}
    for node in ancestor_chain(target, rows):
        bundle = (node.default_agent_settings or {}).get(provider) or {}
        for field, value in bundle.items():
            # First non-null wins (nearest ancestor); an explicit null means
            # "inherit", so it does not block a value set further up.
            if value is not None and field not in resolved:
                resolved[field] = value
    return resolved


def resolve_default_provider(
    target: ProjectDefaultsRow, rows: list[ProjectDefaultsRow]
) -> str | None:
    """Resolve the inherited default provider (worktree/path chain).

    Returns a provider value, or ``None`` when nothing in the chain sets one
    (the caller falls back to the global default provider).
    """
    for node in ancestor_chain(target, rows):
        if node.default_provider:
            return node.default_provider
    return None


# --- DB-facing helpers ----------------------------------------------------


def load_defaults_rows() -> list[ProjectDefaultsRow]:
    """Load the defaults-relevant slice of every project (sync DB access)."""
    from twicc.core.models import Project

    return [
        ProjectDefaultsRow(
            id=row["id"],
            directory=row["directory"],
            worktree_of_id=row["worktree_of_id"],
            default_provider=row["default_provider"],
            default_agent_settings=row["default_agent_settings"],
            default_layout_id=row["default_layout_id"],
        )
        for row in Project.objects.values(
            "id", "directory", "worktree_of_id",
            "default_provider", "default_agent_settings", "default_layout_id",
        )
    ]


def _target_row(
    project_id: str, directory: str | None, rows: list[ProjectDefaultsRow]
) -> ProjectDefaultsRow:
    """The target's row, synthesized from the directory hint when unregistered.

    A CLI ``create-session`` may point at a directory with no ``Project`` row
    yet (the server registers it at creation). A synthetic row (no own
    defaults, no worktree link) still lets path-ancestor inheritance apply —
    e.g. a brand-new sub-directory of a project that sets defaults.
    """
    target = next((r for r in rows if r.id == project_id), None)
    if target is not None:
        return target
    return ProjectDefaultsRow(
        id=project_id,
        directory=directory,
        worktree_of_id=None,
        default_provider=None,
        default_agent_settings=None,
        default_layout_id=None,
    )


def resolve_project_agent_defaults(
    project_id: str, provider: str, *, directory: str | None = None
) -> dict:
    """Resolve one project's inherited agent defaults against the live DB (sync)."""
    rows = load_defaults_rows()
    return resolve_agent_defaults(_target_row(project_id, directory, rows), provider, rows)


def resolve_project_default_provider(
    project_id: str, *, directory: str | None = None
) -> str | None:
    """Resolve one project's inherited default provider against the live DB (sync)."""
    rows = load_defaults_rows()
    return resolve_default_provider(_target_row(project_id, directory, rows), rows)


def materialize_inherited_defaults(
    settings,
    *,
    project_id: str,
    directory: str | None,
    provider: str,
    pb,
    untrusted: bool,
):
    """Fill every still-``None`` supported field with its inherited default.

    Shared by the CLI ``create-session`` (moved from there) and the server-side
    peer-message delivery (``peer_messages.deliver_to_new_session``) — one
    "fill None settings from project chain + global defaults" entry point.
    Field by field, the project chain's value (worktree main repo / path
    ancestors) wins, else the provider's global synced default — so the created
    session stores a concrete snapshot, exactly like a UI-launched one (see
    ``docs/archives/plans/2026-06-09-project-agent-defaults-design.md`` §4, "snapshot
    at creation"). ``permission_mode`` resolves through the trust-matching
    variant: the ``permission_mode_if_untrusted`` chain (then the global
    untrusted default) when the project is untrusted. The hidden
    ``question_widget`` is not a project default and keeps its flag-driven
    value (``None`` = the server-side global default at run time).

    ``settings`` is the CLI's ``AgentSettings`` NamedTuple; ``pb`` a provider
    bundle from the local bootstrap (``bootstrap_local.load_local_bootstrap``).
    """
    from twicc.cli._drop_request.aliases import supported_fields
    from twicc.providers.helpers import (
        AGENT_SETTINGS_HIDDEN_FROM_FRONTEND,
        AgentSettings,
        get_provider_helpers,
    )

    chain = resolve_project_agent_defaults(project_id, provider, directory=directory)
    global_resolved = get_provider_helpers(provider).resolve_agent_settings(AgentSettings())

    fields = (
        supported_fields(pb) & set(AgentSettings._fields)
    ) - set(AGENT_SETTINGS_HIDDEN_FROM_FRONTEND)
    updates: dict = {}
    for field in fields:
        if getattr(settings, field) is not None:
            continue
        if field == "permission_mode" and untrusted:
            value = chain.get("permission_mode_if_untrusted")
            if value is None:
                value = pb.untrusted_permission_mode_default
        else:
            value = chain.get(field)
            if value is None:
                value = getattr(global_resolved, field)
        if value is not None:
            updates[field] = value
    return settings._replace(**updates) if updates else settings
