"""Shared source-age selection rules, independent of Django worker setup."""

from typing import NamedTuple


COMPUTE_MIN_SOURCE_AGE_SECONDS = 30.0
COMPUTE_SELECTION_POLL_SECONDS = 1.0
COMPUTE_STATUS_POLL_SECONDS = 0.05


def compute_age_delay(mtime: float, now: float) -> float:
    """Return the remaining source-age delay in Unix wall-clock seconds."""
    return max(0.0, mtime + COMPUTE_MIN_SOURCE_AGE_SECONDS - now)


def select_required_source_ids(tree, compute_version: int, forced_source_ids=()) -> tuple[str, ...]:
    """Keep outdated or forced members in tree order; source age stays separate."""
    from twicc.core.models import Session

    versions = dict(Session.objects.filter(pk__in=tree.member_ids).values_list('id', 'compute_version'))
    forced = set(forced_source_ids)
    return tuple(
        sid for sid in tree.ordered_source_ids
        if sid in versions and (sid in forced or versions[sid] is None or versions[sid] < compute_version)
    )


class TreeSelection(NamedTuple):
    source_id: str | None
    required_pending: bool
    next_eligible_at: float | None


def select_tree_source(
    root_id: str, compute_version: int, *, cutoff: float,
    outstanding=(), excluded_source_ids=(), forced_source_ids=(),
) -> TreeSelection:
    """Refresh one admitted tree and keep its deepest unfinished level fenced.

    Writer acknowledgements remain pending even after their version commits.
    Source-age deadlines use Unix wall time; retry deadlines belong to callers.
    """
    from twicc.core.models import Session
    from twicc.providers.compute_tree import load_native_tree

    tree = load_native_tree(root_id)
    members = set(tree.member_ids)
    outstanding = set(outstanding) & members
    excluded = set(excluded_source_ids)
    required = set(select_required_source_ids(tree, compute_version, forced_source_ids)) | outstanding
    if not required:
        return TreeSelection(None, False, None)
    mtimes = dict(Session.objects.filter(pk__in=required).values_list('id', 'mtime'))
    # A forced current-root repair must enqueue its final source only after
    # earlier applications acknowledge, so its command can complete the tree.
    if root_id not in required and outstanding and len(required - outstanding) == 1:
        return TreeSelection(None, True, None)
    for level in tree.source_levels:
        unfinished = required.intersection(level)
        if not unfinished:
            continue
        deadlines = []
        for sid in level:
            if sid not in unfinished or sid in outstanding or sid in excluded:
                continue
            mtime = mtimes[sid]
            if mtime <= cutoff:
                return TreeSelection(sid, True, None)
            deadlines.append(mtime + COMPUTE_MIN_SOURCE_AGE_SECONDS)
        return TreeSelection(None, True, min(deadlines) if deadlines else None)
    return TreeSelection(None, True, None)


def select_native_tree_source(
    provider, compute_version: int, *, cutoff: float,
    outstanding=(), excluded=(), forced_source_ids=(), eligible_source_ids=None, invalid_roots=None,
) -> str | None:
    """Admit one source from the deepest unfinished level of one native tree.

    Outstanding writer applications pin the active tree and level, even after
    their source version commits. A blocked tree yields to another root only
    after its outstanding applications drain. Membership is reloaded each call.
    """
    from twicc.core.models import Session
    from twicc.providers.compute_tree import (
        NativeTreeError, load_native_tree, resolve_native_roots, select_outdated_roots,
    )

    outstanding = set(outstanding)
    excluded = set(excluded)
    forced = set(forced_source_ids)
    eligible = None if eligible_source_ids is None else set(eligible_source_ids)
    roots = list(select_outdated_roots(provider, compute_version))
    # Persisted stale inventory keeps a current-root repair eligible after its
    # forced source finishes or the coordinator restarts. Resolve in bulk: an
    # inventory may contain thousands of native members sharing a few roots.
    source_roots = resolve_native_roots(outstanding | forced | (eligible or set()))
    additional_roots = set(source_roots.values()) - set(roots)
    if additional_roots:
        roots.extend(Session.objects.filter(pk__in=additional_roots).order_by('-mtime', 'id').values_list(
            'id', flat=True,
        ))
    active_roots = {source_roots[sid] for sid in outstanding if sid in source_roots}
    if active_roots:
        roots = [root for root in roots if root in active_roots]
    for root in roots:
        try:
            tree = load_native_tree(root)
        except (NativeTreeError, Session.DoesNotExist):
            if invalid_roots is not None:
                invalid_roots.add(root)
            continue
        required = set(select_required_source_ids(tree, compute_version, forced)) | (outstanding & set(tree.member_ids))
        mtimes = dict(Session.objects.filter(pk__in=required).values_list('id', 'mtime'))
        for level in tree.source_levels or tuple((sid,) for sid in tree.ordered_source_ids):
            unfinished = [sid for sid in level if sid in required]
            if not unfinished:
                continue
            # A current-root forced repair finishes with its last source. Keep
            # that command behind earlier acknowledgements so it can enqueue.
            unsubmitted = required - outstanding
            if tree.root_id not in required and outstanding & set(tree.member_ids) and len(unsubmitted) == 1:
                break
            for sid in unfinished:
                if (sid not in outstanding and sid not in excluded
                        and (eligible is None or sid in eligible)
                        and mtimes.get(sid, float('inf')) <= cutoff):
                    return sid
            break
    return None


def tree_completion_context(session_id, compute_version, *, forced_source_ids=(), outstanding=()):
    """Attach final-tree evidence to an existing source command, never a new job."""
    from twicc.core.models import Session
    from twicc.providers.compute_tree import NativeTreeError, load_native_tree, resolve_native_root

    try:
        tree = load_native_tree(resolve_native_root(session_id))
    except (NativeTreeError, Session.DoesNotExist):
        return None
    required = set(select_required_source_ids(tree, compute_version, forced_source_ids))
    if (required | (set(outstanding) & set(tree.member_ids))) - {session_id}:
        return None
    return {'root_id': tree.root_id, 'member_ids': tree.member_ids, 'compute_version': compute_version}
