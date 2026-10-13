"""Shared source-age selection rules, independent of Django worker setup."""

COMPUTE_MIN_SOURCE_AGE_SECONDS = 30.0
COMPUTE_SELECTION_POLL_SECONDS = 1.0
COMPUTE_STATUS_POLL_SECONDS = 0.05


def compute_age_delay(mtime: float, now: float) -> float:
    """Return the remaining source-age delay in Unix wall-clock seconds."""
    return max(0.0, mtime + COMPUTE_MIN_SOURCE_AGE_SECONDS - now)
