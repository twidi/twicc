"""Read native membership separately from launcher ancestry and source eligibility."""

from typing import NamedTuple

from django.db.models import Q

from twicc.core.models import AgentLink, Session, SessionType


class NativeTreeError(ValueError):
    """Malformed tree evidence prevents this attempt; later discovery can repair it."""


class NativeTree(NamedTuple):
    root_id: str
    member_ids: tuple[str, ...]
    ordered_source_ids: tuple[str, ...]
    source_levels: tuple[tuple[str, ...], ...] = ()


def resolve_native_root(session_id: str) -> str:
    """Follow native membership only; separately spawned sessions remain roots."""
    seen = set()
    while True:
        if session_id in seen:
            raise NativeTreeError(f'Native membership cycle at {session_id}')
        seen.add(session_id)
        parent = Session.objects.values_list('parent_session_id', flat=True).get(pk=session_id)
        if parent is None:
            return session_id
        session_id = parent


def resolve_native_roots(session_ids) -> dict[str, str]:
    """Resolve an inventory with bulk ancestor reads; omit missing/cyclic paths."""
    candidates = set(session_ids)
    parents = {}
    frontier = candidates
    while frontier:
        rows = dict(Session.objects.filter(pk__in=frontier).values_list('id', 'parent_session_id'))
        parents.update(rows)
        frontier = {parent for parent in rows.values() if parent is not None and parent not in parents}

    resolved = {}
    for session_id in sorted(candidates):
        path = set()
        cursor = session_id
        while cursor not in resolved:
            if cursor in path or cursor not in parents:
                root = None
                break
            path.add(cursor)
            parent = parents[cursor]
            if parent is None:
                root = cursor
                break
            cursor = parent
        else:
            root = resolved[cursor]
        for member in path:
            resolved[member] = root
    return {sid: resolved[sid] for sid in candidates if resolved.get(sid) is not None}


def load_native_tree(root_id: str) -> NativeTree:
    """Reload database membership and sort deepest known launcher children first."""
    if resolve_native_root(root_id) != root_id:
        raise NativeTreeError(f'{root_id} is not a native root')
    members = {root_id}
    frontier = {root_id}
    while frontier:
        children = set(Session.objects.filter(parent_session_id__in=frontier).values_list('id', flat=True))
        if children & members:
            raise NativeTreeError(f'Native membership cycle in {root_id}')
        members.update(children)
        frontier = children

    parents = {}
    for child, parent in AgentLink.objects.filter(agent_id__in=members).values_list('agent_id', 'session_id'):
        if child == root_id and parent not in members:
            # Independently spawned roots keep their own native boundary.
            continue
        if parent not in members or child == root_id:
            raise NativeTreeError(f'Conflicting launcher membership for {child}')
        if child in parents and parents[child] != parent:
            raise NativeTreeError(f'Conflicting launcher parents for {child}')
        parents[child] = parent

    depths = {root_id: 0}
    for member in sorted(members):
        path = []
        seen = set()
        cursor = member
        while cursor not in depths:
            if cursor in seen:
                raise NativeTreeError(f'Launcher cycle in {root_id} at {cursor}')
            seen.add(cursor)
            path.append(cursor)
            cursor = parents.get(cursor, root_id)
        depth = depths[cursor]
        for child in reversed(path):
            depth += 1
            depths[child] = depth

    mtimes = dict(Session.objects.filter(pk__in=members).values_list('id', 'mtime'))
    ordered = tuple(sorted(members - {root_id}, key=lambda sid: (-depths[sid], -mtimes[sid], sid)))
    levels = tuple(
        tuple(sid for sid in ordered if depths[sid] == depth)
        for depth in sorted({depths[sid] for sid in ordered}, reverse=True)
    )
    return NativeTree(root_id, (root_id, *sorted(members - {root_id})), (*ordered, root_id), (*levels, (root_id,)))


def select_outdated_roots(provider: str, compute_version: int) -> tuple[str, ...]:
    """Rank outdated native roots by their own source time, without age gating."""
    return tuple(Session.objects.filter(
        Q(compute_version__isnull=True) | Q(compute_version__lt=compute_version),
        provider=provider, type=SessionType.SESSION, parent_session__isnull=True,
    ).order_by('-mtime', 'id').values_list('id', flat=True))


def _outdated_native_roots(provider: str, compute_version: int, excluded_root_ids):
    """Keep the positive native-root predicate aligned with its partial index."""
    return Session.objects.filter(
        Q(compute_version__isnull=True) | Q(compute_version__lt=compute_version),
        provider=provider, type=SessionType.SESSION, parent_session__isnull=True,
    ).exclude(pk__in=excluded_root_ids)


def select_outdated_root(
    provider: str, compute_version: int, *, cutoff: float, excluded_root_ids=(),
) -> str | None:
    """Admit one stable native root through the ordered partial index."""
    return _outdated_native_roots(provider, compute_version, excluded_root_ids).filter(
        mtime__lte=cutoff,
    ).order_by('-mtime').values_list('id', flat=True).first()


def select_outdated_member_roots(provider: str, compute_version: int, *, batch_size: int = 200) -> set[str]:
    """Return the native roots of outdated members, whatever the roots' own version.

    A version bump makes every member outdated: resolve them in bounded batches.
    """
    member_ids = list(Session.objects.filter(
        Q(compute_version__isnull=True) | Q(compute_version__lt=compute_version),
        provider=provider, parent_session__isnull=False,
    ).values_list('id', flat=True))
    roots = set()
    for offset in range(0, len(member_ids), batch_size):
        roots.update(resolve_native_roots(member_ids[offset:offset + batch_size]).values())
    return roots


def next_root_eligibility(
    provider: str, compute_version: int, *, cutoff: float, excluded_root_ids=(),
) -> float | None:
    """Probe the earliest recent-root deadline after eligible roots run out."""
    from twicc.providers.compute_selection import COMPUTE_MIN_SOURCE_AGE_SECONDS

    mtime = _outdated_native_roots(provider, compute_version, excluded_root_ids).filter(
        mtime__gt=cutoff,
    ).order_by('mtime').values_list('mtime', flat=True).first()
    return None if mtime is None else mtime + COMPUTE_MIN_SOURCE_AGE_SECONDS
