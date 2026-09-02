"""Cross-provider project trust: resolution (provider-agnostic).

The DB (``Project.trust`` + ``Project.trust_propagation``) is the single source
of truth. The provider configs (``~/.claude.json``, ``~/.codex/config.toml`` —
or their equivalents under the configured homes, ``twicc.provider_homes``)
are a write-mostly projection handled in the provider-specific ``trust`` modules
and orchestrated by :mod:`twicc.core.services.trust`.

This module holds only the pure resolver: given the trust-relevant slice of
every project, compute one project's *effective* trust. The effective trust of
a NULL (inherited) project is **never stored** — always recomputed.

Resolution order (see docs/plans/2026-06-09-project-trust-design.md §4):

1. ``trust`` non-NULL → that value (the self is sovereign).
2. The project is a worktree (``worktree_of`` set) → inherit the **main repo's**
   effective trust *if the main repo propagates* (worktree-ness takes precedence
   over path parentage).
3. Otherwise → inherit from the **nearest explicit path ancestor** if it
   propagates.
4. Nothing resolves → unknown (ask at the gate).

There is **no git boundary**: propagation flows by pure path prefix, so trusting
a directory above several git repos and propagating reaches them.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class ProjectTrustRow(NamedTuple):
    """The trust-relevant slice of a ``Project``, loaded once for resolution."""

    id: str
    directory: str | None
    worktree_of_id: str | None
    trust: bool | None
    trust_propagation: bool


class TrustResolution(NamedTuple):
    """Outcome of resolving a project's effective trust.

    ``state`` is the resolved value: ``True`` trusted, ``False`` untrusted,
    ``None`` unresolved (→ ask). ``source_id`` is the project whose explicit
    decision produced the value (the project itself when ``via == "self"``).
    """

    state: bool | None
    source_id: str | None
    via: str  # "self" | "path" | "worktree" | "unresolved"


def _segments(path: str) -> list[str]:
    """Normalized, non-empty path segments (pure — no filesystem access)."""
    return [s for s in os.path.normpath(path).split(os.sep) if s]


def _is_path_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff *ancestor* is a strict directory prefix of *descendant*.

    Compared segment-by-segment (not ``str.startswith``) so ``/a/b`` is not an
    ancestor of ``/a/bc``. Both paths are assumed already realpath-normalized
    (``Project.directory`` is stored realpath'd); ``normpath`` only tidies them.
    """
    a, d = _segments(ancestor), _segments(descendant)
    return len(a) < len(d) and d[: len(a)] == a


def _nearest_explicit_path_ancestor(
    target: ProjectTrustRow, rows: list[ProjectTrustRow]
) -> ProjectTrustRow | None:
    """The nearest (longest-path) ancestor of *target* with an explicit trust."""
    if not target.directory:
        return None
    best: ProjectTrustRow | None = None
    best_len = -1
    for row in rows:
        if row.id == target.id or row.trust is None or not row.directory:
            continue
        if _is_path_ancestor(row.directory, target.directory):
            depth = len(_segments(row.directory))
            if depth > best_len:
                best, best_len = row, depth
    return best


def _resolve(
    target: ProjectTrustRow,
    by_id: dict[str, ProjectTrustRow],
    rows: list[ProjectTrustRow],
    seen: frozenset[str],
) -> tuple[bool | None, bool, str | None, str]:
    """Recursive core. Returns ``(state, propagates_onward, source_id, via)``.

    ``propagates_onward`` is internal: whether *target*'s resolution cascades to
    its own NULL descendants. It equals ``trust_propagation`` for an explicit
    node, and continues down an inherited chain only while each link propagates.
    """
    # 1. Self wins.
    if target.trust is not None:
        return target.trust, bool(target.trust_propagation), target.id, "self"

    if target.id in seen:  # cycle guard (pathological worktree_of loops)
        return None, False, None, "unresolved"
    seen = seen | {target.id}

    # 2./3. Pick the inheritance source: worktree main repo, else path ancestor.
    if target.worktree_of_id:
        source = by_id.get(target.worktree_of_id)
        via = "worktree"
    else:
        source = _nearest_explicit_path_ancestor(target, rows)
        via = "path"

    if source is None:
        return None, False, None, "unresolved"

    state, propagates, src_id, _ = _resolve(source, by_id, rows, seen)
    if propagates and state is not None:
        return state, True, src_id, via
    return None, False, None, "unresolved"


def effective_trust(
    target: ProjectTrustRow, rows: list[ProjectTrustRow]
) -> TrustResolution:
    """Resolve *target*'s effective trust from *rows* (the full project set)."""
    by_id = {r.id: r for r in rows}
    state, _propagates, source_id, via = _resolve(
        target, by_id, rows, frozenset()
    )
    return TrustResolution(state=state, source_id=source_id, via=via)


# --- DB-facing helpers ----------------------------------------------------


def load_trust_rows() -> list[ProjectTrustRow]:
    """Load the trust-relevant slice of every project (sync DB access)."""
    from twicc.core.models import Project

    return [
        ProjectTrustRow(
            id=row["id"],
            directory=row["directory"],
            worktree_of_id=row["worktree_of_id"],
            trust=row["trust"],
            trust_propagation=row["trust_propagation"],
        )
        for row in Project.objects.values(
            "id", "directory", "worktree_of_id", "trust", "trust_propagation"
        )
    ]


def resolve_project(project_id: str) -> TrustResolution:
    """Resolve one project's effective trust against the live DB (sync)."""
    rows = load_trust_rows()
    target = next((r for r in rows if r.id == project_id), None)
    if target is None:
        return TrustResolution(state=None, source_id=None, via="unresolved")
    return effective_trust(target, rows)
