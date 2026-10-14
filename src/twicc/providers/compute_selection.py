"""Shared source-age selection rules, independent of Django worker setup."""

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
