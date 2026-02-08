"""
Usage quota fetching and storage for Claude Code.

Fetches usage data from the Anthropic OAuth usage API endpoint
using credentials from ~/.claude/.credentials.json, and stores
snapshots in the database.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from twicc.core.models import UsageSnapshot

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
        snapshot = save_usage_snapshot(raw)
        logger.info("Usage snapshot saved (id=%s)", snapshot.id)
        return snapshot
    except Exception as e:
        logger.error("Failed to save usage snapshot: %s", e, exc_info=True)
        return None
