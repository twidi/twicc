"""
Usage quota fetching and storage for Claude Code.

Fetches usage data from the Anthropic OAuth usage API endpoint
using credentials from ~/.claude/.credentials.json, and stores
snapshots in the database.

Also provides cost estimation for quota periods by summing
SessionItem costs within the relevant time windows.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from twicc.core.models import SessionItem, UsageSnapshot

logger = logging.getLogger(__name__)

# Anthropic usage API endpoint
USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"

# Required headers for the usage API
USAGE_API_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/2.1.34",
}

# Credentials file path (cross-platform)
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def has_oauth_credentials() -> bool:
    """
    Check whether OAuth credentials are configured.

    Returns True if the credentials file exists and contains a
    claudeAiOauth entry (regardless of whether the token is valid).
    """
    if not CREDENTIALS_PATH.is_file():
        return False

    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    return bool(data.get("claudeAiOauth"))


def _get_access_token() -> str | None:
    """
    Read the OAuth access token from ~/.claude/.credentials.json.

    Returns:
        The access token string, or None if not found.
    """
    if not CREDENTIALS_PATH.is_file():
        logger.warning("Credentials file not found: %s", CREDENTIALS_PATH)
        return None

    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read credentials file: %s", e)
        return None

    token = data.get("claudeAiOauth", {}).get("accessToken")
    if not token:
        logger.warning("No OAuth access token found in credentials file")
        return None

    return token


def fetch_usage() -> dict | None:
    """
    Fetch usage data from the Anthropic OAuth usage API.

    Returns:
        The raw JSON response as a dict, or None on failure.
    """
    token = _get_access_token()
    if token is None:
        return None

    headers = {
        **USAGE_API_HEADERS,
        "Authorization": f"Bearer {token}",
    }

    try:
        response = httpx.get(USAGE_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Usage API HTTP error: %s", e)
        return None
    except httpx.TimeoutException:
        logger.warning("Usage API request timed out")
        return None
    except Exception as e:
        logger.warning("Usage API request failed: %s", e)
        return None


def _extract_quota_fields(data: dict | None, prefix: str) -> dict:
    """
    Extract utilization and resets_at from a quota block.

    Args:
        data: The quota block (e.g., {"utilization": 12.0, "resets_at": "..."}) or None.
        prefix: Field name prefix (e.g., "five_hour").

    Returns:
        Dict with "{prefix}_utilization" and "{prefix}_resets_at" keys.
    """
    if data is None:
        return {
            f"{prefix}_utilization": None,
            f"{prefix}_resets_at": None,
        }

    resets_at_str = data.get("resets_at")
    resets_at = None
    if resets_at_str:
        try:
            resets_at = datetime.fromisoformat(resets_at_str)
        except ValueError:
            logger.warning("Failed to parse resets_at for %s: %s", prefix, resets_at_str)

    return {
        f"{prefix}_utilization": data.get("utilization"),
        f"{prefix}_resets_at": resets_at,
    }


def save_usage_snapshot(raw: dict) -> UsageSnapshot:
    """
    Parse a raw usage API response and save it as a UsageSnapshot.

    Args:
        raw: The raw JSON response from the usage API.

    Returns:
        The created UsageSnapshot instance.
    """
    now = datetime.now(timezone.utc)

    fields = {
        "fetched_at": now,
        "raw_response": raw,
    }

    # Quota blocks
    for key, prefix in [
        ("five_hour", "five_hour"),
        ("seven_day", "seven_day"),
        ("seven_day_opus", "seven_day_opus"),
        ("seven_day_sonnet", "seven_day_sonnet"),
        ("seven_day_oauth_apps", "seven_day_oauth_apps"),
        ("seven_day_cowork", "seven_day_cowork"),
    ]:
        fields.update(_extract_quota_fields(raw.get(key), prefix))

    # Extra usage block
    extra = raw.get("extra_usage")
    if extra is not None:
        fields["extra_usage_is_enabled"] = extra.get("is_enabled", False)
        fields["extra_usage_monthly_limit"] = extra.get("monthly_limit")
        fields["extra_usage_used_credits"] = extra.get("used_credits")
        fields["extra_usage_utilization"] = extra.get("utilization")
    else:
        fields["extra_usage_is_enabled"] = False
        fields["extra_usage_monthly_limit"] = None
        fields["extra_usage_used_credits"] = None
        fields["extra_usage_utilization"] = None

    return UsageSnapshot.objects.create(**fields)


def fetch_and_save_usage() -> UsageSnapshot | None:
    """
    Fetch usage data from the API and save a snapshot.

    Returns:
        The created UsageSnapshot, or None if fetch failed.
    """
    raw = fetch_usage()
    if raw is None:
        return None

    try:
        return save_usage_snapshot(raw)
    except Exception as e:
        logger.error("Failed to save usage snapshot: %s", e, exc_info=True)
        return None


# Duration of 30 days in seconds, for monthly cost projection
THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60


def _sum_costs_since(start: datetime) -> Decimal:
    """
    Sum all SessionItem costs with timestamp >= start.

    Returns Decimal(0) if no items found.
    """
    from django.db.models import Sum

    result = (
        SessionItem.objects.filter(
            timestamp__gte=start,
            cost__isnull=False,
        )
        .aggregate(total=Sum("cost"))
    )
    return result["total"] or Decimal(0)


def compute_period_costs(snapshot: UsageSnapshot) -> dict:
    """
    Compute cost data for the 5-hour and 7-day quota periods.

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
    now = datetime.now(timezone.utc)
    result = {}

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
        spent = _sum_costs_since(period_start)
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
