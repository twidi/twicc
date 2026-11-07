"""Cross-provider usage helpers.

Generic computations on :class:`UsageSnapshot` rows that don't depend on
how the snapshot was produced — only on the shared shape (``five_hour_*``
/ ``seven_day_*`` quotas + ``provider`` field). Provider-specific
fetching/parsing lives in each provider's module.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from pathlib import Path

from twicc.core.ui_messages import ui_message_text
from twicc.core.enums import Provider
from twicc.core.models import SessionItem, UsageSnapshot


# Duration of 30 days in seconds, for monthly cost projection.
THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60


def _sum_costs_since(provider: Provider, start: datetime) -> Decimal:
    """Sum SessionItem costs for ``provider`` with timestamp >= ``start``.

    Returns ``Decimal(0)`` when no items match.
    """
    from django.db.models import Sum

    result = (
        SessionItem.objects.filter(
            session__provider=provider.value,
            timestamp__gte=start,
            cost__isnull=False,
        )
        .aggregate(total=Sum("cost"))
    )
    return result["total"] or Decimal(0)


def compute_period_costs(snapshot: UsageSnapshot) -> dict:
    """Compute cost data for the 5-hour and 7-day quota periods.

    For each period, calculates:
    - spent: actual sum of SessionItem costs since period start (USD)
    - estimated_period: projected cost for the full period, capped at quota cutoff
    - estimated_monthly: projected cost over 30 days, derived from capped period cost
    - capped: whether the period estimate was capped due to burn rate > 1
    - cutoff_at: ISO datetime when quota will be exhausted (null if burn rate <= 1)

    When burn rate > 1.0, usage will hit 100% before the period ends.
    The cost at cutoff is: spent * (100 / utilization).
    After cutoff, no more usage is possible, so cost plateaus.

    The 30-day estimate is derived from the (potentially capped) period cost:
    estimated_monthly = (estimated_period / window_seconds) * 30_days_seconds.
    This correctly models the repeating pattern: if you burn through quota in
    half the window every cycle, you spend the capped amount per window, repeated
    across all windows in 30 days.

    Costs are scoped to ``snapshot.provider`` so multi-provider deployments
    don't mix sessions across providers.

    Args:
        snapshot: The usage snapshot containing resets_at times and utilization.

    Returns:
        Dict with keys "five_hour" and "seven_day", each containing:
        - spent (float): actual cost in USD
        - estimated_period (float|None): projected period cost (capped if burn rate > 1)
        - estimated_monthly (float|None): projected 30-day cost
        - capped (bool): True if estimated_period was capped due to quota exhaustion
        - cutoff_at (str|None): ISO datetime when quota will be exhausted, or None
    """
    now = datetime.now(UTC)
    result: dict = {}

    periods = [
        ("five_hour", snapshot.five_hour_resets_at, timedelta(hours=5), snapshot.five_hour_utilization),
        ("seven_day", snapshot.seven_day_resets_at, timedelta(days=7), snapshot.seven_day_utilization),
    ]

    for key, resets_at, window, utilization in periods:
        if resets_at is None:
            result[key] = {
                "spent": 0.0,
                "estimated_period": None,
                "estimated_monthly": None,
                "capped": False,
                "cutoff_at": None,
            }
            continue

        period_start = resets_at - window
        spent = _sum_costs_since(Provider(snapshot.provider), period_start)
        spent_float = float(spent)

        # Time elapsed since period start
        elapsed_seconds = (now - period_start).total_seconds()
        window_seconds = window.total_seconds()

        if elapsed_seconds <= 0 or window_seconds <= 0:
            result[key] = {
                "spent": round(spent_float, 4),
                "estimated_period": None,
                "estimated_monthly": None,
                "capped": False,
                "cutoff_at": None,
            }
            continue

        # Linear projection: cost for the full window at current pace
        rate_per_second = spent_float / elapsed_seconds
        estimated_period_linear = rate_per_second * window_seconds

        # Check if burn rate > 1 (will hit quota before period ends)
        capped = False
        cutoff_at = None  # ISO datetime when quota will be exhausted

        if utilization is not None and utilization > 0:
            # Burn rate = utilization / time_pct
            time_pct = elapsed_seconds / window_seconds
            burn_rate = (utilization / 100.0) / time_pct if time_pct > 0 else 0

            if utilization >= 100:
                # Already exhausted — cost won't grow further
                capped = True
                cutoff_at = now  # already hit
                estimated_period = spent_float
            elif burn_rate > 1.0:
                # Will exhaust before period ends
                # Cost at cutoff = spent * (100 / utilization)
                capped = True
                estimated_period = spent_float * (100.0 / utilization)

                # Time until cutoff: utilization reaches 100% at this pace
                # cutoff_time_pct = 1.0 / burn_rate (fraction of window)
                cutoff_seconds = window_seconds / burn_rate
                remaining_to_cutoff = max(0.0, cutoff_seconds - elapsed_seconds)
                cutoff_at = now + timedelta(seconds=remaining_to_cutoff)
            else:
                estimated_period = estimated_period_linear
        else:
            estimated_period = estimated_period_linear

        # Monthly estimate derived from (capped) period cost
        # This models the repeating cycle: each window costs estimated_period
        estimated_monthly = (estimated_period / window_seconds) * THIRTY_DAYS_SECONDS

        result[key] = {
            "spent": round(spent_float, 4),
            "estimated_period": round(estimated_period, 4),
            "estimated_monthly": round(estimated_monthly, 2),
            "capped": capped,
            "cutoff_at": cutoff_at.isoformat() if cutoff_at else None,
        }

    return result


def _recent_burn_rate(utilization, fetched_at, ref_utilization, ref_fetched_at, window_seconds):
    """Burn rate between two intra-period snapshots, as a ratio (1.0 = sustainable).

    Returns ``None`` when not computable (missing data, reset between snapshots,
    non-positive delta).
    """
    if utilization is None or ref_utilization is None or window_seconds <= 0:
        return None
    delta_util = utilization - ref_utilization
    if delta_util < 0:
        return None  # a quota reset happened between snapshots
    delta_seconds = (fetched_at - ref_fetched_at).total_seconds()
    if delta_seconds <= 0:
        return None
    delta_time_pct = (delta_seconds / window_seconds) * 100.0
    if delta_time_pct <= 0:
        return None
    return delta_util / delta_time_pct


def _compute_recent_rate(
    utilization,
    fetched_at,
    window_start,
    window_seconds,
    lookback_seconds,
    ref,  # {"fetched_at": dt, "utilization": float} or None
    cross_ref,  # {"prev_ref": {...}, "prev_end": {...}} or None
):
    """Compute a single recent burn rate, with cross-period fallback.

    Mirrors ``_computeRecent`` in ``frontend/src/utils/usage.js`` so the CLI
    exposes the same view of recent burn rate that the UI shows. When the
    current window is younger than the lookback, falls back to a calculation
    spanning the end of the previous period plus the start of the current one.
    """
    if utilization is None or window_seconds <= 0:
        return None

    elapsed = (fetched_at - window_start).total_seconds() if window_start else None

    if elapsed is not None and elapsed < lookback_seconds:
        if not cross_ref:
            return None
        prev_ref = cross_ref.get("prev_ref")
        prev_end = cross_ref.get("prev_end")
        if not prev_ref or not prev_end:
            return None
        prev_ref_util = prev_ref.get("utilization")
        prev_end_util = prev_end.get("utilization")
        if prev_ref_util is None or prev_end_util is None:
            return None
        old_consumption = prev_end_util - prev_ref_util
        if old_consumption < 0:
            return None
        total_consumption = old_consumption + utilization
        delta_seconds = (fetched_at - prev_ref["fetched_at"]).total_seconds()
        if delta_seconds <= 0:
            return None
        delta_time_pct = (delta_seconds / window_seconds) * 100.0
        if delta_time_pct <= 0:
            return None
        return total_consumption / delta_time_pct

    if not ref:
        return None
    return _recent_burn_rate(
        utilization,
        fetched_at,
        ref.get("utilization"),
        ref["fetched_at"],
        window_seconds,
    )


def compute_recent_burn_rates(snapshot: UsageSnapshot, references: dict | None) -> dict:
    """Compute recent burn rates over short / long lookbacks for both windows.

    Returns a dict::

        {
            "five_hour":  {"short": <30min rate>, "long": <1h rate>},
            "seven_day":  {"short": <12h rate>,   "long": <24h rate>},
        }

    Each value is a ratio (1.0 = on track to use exactly the quota at reset,
    >1.0 = on track to exhaust before reset), or ``None`` when not computable.

    ``references`` is the dict produced by
    :func:`twicc.usage_task._build_reference_snapshots` (already de-stringified
    in this function, so callers can pass it as-is from JSON or from the
    builder).
    """
    empty = {
        "five_hour": {"short": None, "long": None},
        "seven_day": {"short": None, "long": None},
    }
    if not snapshot or not snapshot.fetched_at:
        return empty

    references = references or {}
    fetched_at = snapshot.fetched_at
    fh_window_start = (
        snapshot.five_hour_resets_at - timedelta(hours=5)
        if snapshot.five_hour_resets_at
        else None
    )
    sd_window_start = (
        snapshot.seven_day_resets_at - timedelta(days=7)
        if snapshot.seven_day_resets_at
        else None
    )

    def _parse_dt(value):
        if isinstance(value, datetime) or value is None:
            return value
        return datetime.fromisoformat(value)

    def _ref(key, util_field):
        raw = references.get(key)
        if not raw:
            return None
        return {
            "fetched_at": _parse_dt(raw.get("fetched_at")),
            "utilization": raw.get(util_field),
        }

    def _cross(key, util_field):
        raw = references.get(key)
        if not raw:
            return None
        prev_ref = raw.get("prev_ref") or {}
        prev_end = raw.get("prev_end") or {}
        return {
            "prev_ref": {
                "fetched_at": _parse_dt(prev_ref.get("fetched_at")),
                "utilization": prev_ref.get(util_field),
            },
            "prev_end": {
                "fetched_at": _parse_dt(prev_end.get("fetched_at")),
                "utilization": prev_end.get(util_field),
            },
        }

    return {
        "five_hour": {
            "short": _compute_recent_rate(
                snapshot.five_hour_utilization,
                fetched_at,
                fh_window_start,
                5 * 3600,
                30 * 60,
                _ref("thirty_min", "five_hour_utilization"),
                _cross("cross_fh_short", "five_hour_utilization"),
            ),
            "long": _compute_recent_rate(
                snapshot.five_hour_utilization,
                fetched_at,
                fh_window_start,
                5 * 3600,
                60 * 60,
                _ref("one_hour", "five_hour_utilization"),
                _cross("cross_fh_long", "five_hour_utilization"),
            ),
        },
        "seven_day": {
            "short": _compute_recent_rate(
                snapshot.seven_day_utilization,
                fetched_at,
                sd_window_start,
                7 * 86400,
                12 * 3600,
                _ref("twelve_hour", "seven_day_utilization"),
                _cross("cross_sd_short", "seven_day_utilization"),
            ),
            "long": _compute_recent_rate(
                snapshot.seven_day_utilization,
                fetched_at,
                sd_window_start,
                7 * 86400,
                24 * 3600,
                _ref("one_day", "seven_day_utilization"),
                _cross("cross_sd_long", "seven_day_utilization"),
            ),
        },
    }


def format_extra_usage_amount(value: float | None, decimal_places: int | None) -> str | None:
    """Render an extra-usage credit figure as a bare money amount.

    ``value`` is in minor units and ``decimal_places`` is the exponent the
    provider reported alongside the currency, so ``4419`` / ``2`` gives
    ``"44.19"``. Trailing zeros are dropped: ``8000`` / ``2`` gives ``"80"``.
    Returns ``None`` when either side is missing — the snapshot then carries no
    money shape and the caller falls back to bare credit counts.

    JS mirror: ``formatExtraUsageAmount`` in ``frontend/src/utils/usage.js``.
    """
    if value is None or decimal_places is None or decimal_places < 0:
        return None
    amount = Decimal(str(value)) / (Decimal(10) ** decimal_places)
    text = f"{amount:.{decimal_places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_usage_dump_path(file_path: str) -> tuple[bool, str]:
    """Validate that a dump file path is usable (parent directory exists and is writable).

    Pure filesystem check — provider-agnostic.

    Returns:
        A ``(valid, message)`` tuple.
    """
    path = Path(file_path)

    if not path.parent.is_dir():
        return False, ui_message_text(f"Directory does not exist: {path.parent}", 'backend.usage.directory_missing', {"path": str(path.parent)})

    if not os.access(path.parent, os.W_OK):
        return False, ui_message_text(f"Directory is not writable: {path.parent}", 'backend.usage.directory_not_writable', {"path": str(path.parent)})

    return True, ui_message_text("Valid dump path", 'backend.usage.valid_dump_path')


def validate_usage_file(provider: Provider, file_path: str) -> tuple[bool, str]:
    """Validate that ``file_path`` holds a usage payload readable for ``provider``.

    Cross-provider envelope: checks that the file exists, is valid JSON,
    and parses to a top-level object. The provider-specific format check
    (e.g. ``five_hour`` / ``seven_day`` for Claude Code) is delegated to
    :meth:`BaseProviderHelpers.validate_usage_file_payload` so each
    provider only owns the schema bits that vary.

    Returns ``(valid, message)``.
    """
    import orjson
    from twicc.providers.helpers import get_provider_helpers

    path = Path(file_path)

    if not path.is_file():
        return False, ui_message_text("File not found", 'backend.usage.file_missing')

    try:
        content = path.read_bytes()
    except OSError as e:
        return False, ui_message_text(f"Cannot read file: {e}", 'backend.usage.read_failed', {"detail": str(e)})

    try:
        data = orjson.loads(content)
    except orjson.JSONDecodeError as e:
        return False, ui_message_text(f"Invalid JSON: {e}", 'backend.usage.invalid_json', {"detail": str(e)})

    if not isinstance(data, dict):
        return False, ui_message_text("JSON root must be an object", 'backend.usage.object_required')

    return get_provider_helpers(provider).validate_usage_file_payload(data)
