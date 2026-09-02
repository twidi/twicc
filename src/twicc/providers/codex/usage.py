"""
Usage quota fetching and storage for Codex.

Calls ChatGPT's ``/backend-api/wham/usage`` endpoint — the same one the
Codex CLI polls itself for ``/status``. The response carries quota windows
under ``rate_limit.primary_window`` and ``rate_limit.secondary_window``,
which we map onto the existing :class:`UsageSnapshot` columns so the
cross-provider sidebar / graph dialog can consume Codex snapshots through
exactly the same shape as Anthropic's ``five_hour`` / ``seven_day``.

The payload *slot* is not a reliable indicator of a window's real span:
Codex normally puts the 5-hour quota in ``primary_window`` and the weekly
one in ``secondary_window``, but OpenAI can temporarily disable the 5-hour
limit and publish the weekly quota alone in ``primary_window`` (with
``secondary_window`` null). We therefore route each window by its declared
``limit_window_seconds`` (18000 = 5h, 604800 = 7d) rather than its position,
falling back to the slot only when the duration is missing (see
:func:`_extract_windows`).

Credentials access lives in :mod:`.credentials`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC
from pathlib import Path

import httpx
import orjson

from twicc.core.models import UsageSnapshot

from .credentials import (
    get_credentials,
    invalidate_credentials_cache,
    note_credentials_accepted,
    refresh_token_via_codex_sdk,
)

logger = logging.getLogger(__name__)

# ChatGPT internal endpoint used by the Codex CLI's /status command.
USAGE_API_URL = "https://chatgpt.com/backend-api/wham/usage"

# Headers the endpoint expects. The OAuth bearer is the ChatGPT
# ``access_token`` from ``<codex home>/auth.json``; ``ChatGPT-Account-Id``
# is the matching ``account_id``. Origin/Referer/User-Agent mimic a
# browser context — the endpoint rejects bare requests without them.
USAGE_API_BASE_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com/",
    "User-Agent": "Mozilla/5.0",
}


def fetch_usage(*, refresh_token_if_needed: bool = True) -> dict | None:
    """Fetch raw usage data from ChatGPT's ``/backend-api/wham/usage``.

    On 401/403 (with ``refresh_token_if_needed``), the rejected token is
    handled in two steps:

    1. **Stale cache** — drop the cache, re-read storage, and if a
       *different* token now lives there (a real Codex session refreshed
       it underneath us), retry the fetch with it. No SDK turn, and it
       can't get pinned behind the refresh guard.
    2. **Genuinely expired** — storage still holds the same rejected
       token, so trigger the refresh ourselves via a throwaway SDK turn
       (:func:`refresh_token_via_codex_sdk`, keyed on the storage value)
       and retry once if ``last_refresh`` actually moved forward.

    A successful fetch clears the refresh-failure guard via
    :func:`note_credentials_accepted`.

    Returns the raw JSON payload (the unmodified ChatGPT response) on
    success, ``None`` on failure.
    """
    creds = get_credentials()
    if creds is None:
        return None

    headers = {
        **USAGE_API_BASE_HEADERS,
        "Authorization": f"Bearer {creds.access_token}",
        "ChatGPT-Account-Id": creds.account_id,
    }

    try:
        response = httpx.get(USAGE_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        # The token was accepted: forget any prior refresh failure so a later
        # expiry starts from a clean slate (see note_credentials_accepted).
        note_credentials_accepted()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403) and refresh_token_if_needed:
            # Step 1 — treat the rejection as mere cache staleness first: drop
            # the cache, re-read storage, and if a *different* token now lives
            # there (a real Codex session refreshed it underneath us), retry
            # with it. No costly SDK turn, and it can't get stuck behind the
            # refresh guard.
            invalidate_credentials_cache()
            fresh = get_credentials()
            if fresh is not None and fresh.access_token != creds.access_token:
                return fetch_usage(refresh_token_if_needed=False)

            # Step 2 — storage still holds the same rejected token: it is
            # genuinely expired and nobody else refreshed it, so trigger the
            # refresh ourselves, keyed on the storage value (never the cache).
            logger.warning("Codex usage API returned %d, attempting token refresh", e.response.status_code)
            last_refresh = fresh.last_refresh if fresh is not None else ""
            if refresh_token_via_codex_sdk(last_refresh):
                return fetch_usage(refresh_token_if_needed=False)
            return None
        logger.warning("Codex usage API HTTP error: %s", e)
        return None
    except httpx.TimeoutException:
        logger.warning("Codex usage API request timed out")
        return None
    except Exception as e:
        logger.warning("Codex usage API request failed: %s", e)
        return None


def _extract_window_fields(window: dict | None, prefix: str) -> dict:
    """Translate a Codex ``rate_limit`` window into ``UsageSnapshot`` columns.

    Codex uses ``used_percent`` (number 0-100) and ``reset_at`` (epoch
    seconds) — Anthropic uses ``utilization`` (0-100) and ``resets_at``
    (ISO datetime). The only real conversion is the timestamp: epoch
    seconds → timezone-aware UTC ``datetime``.

    ``prefix`` is the destination column prefix (``"five_hour"`` /
    ``"seven_day"``), chosen by :func:`_classify_window_prefix` from the
    window's real span rather than its payload slot. Returns
    ``{f"{prefix}_utilization": float|None, f"{prefix}_resets_at":
    datetime|None}``.
    """
    if window is None:
        return {
            f"{prefix}_utilization": None,
            f"{prefix}_resets_at": None,
        }

    utilization = window.get("used_percent")
    if not isinstance(utilization, (int, float)):
        utilization = None
    else:
        utilization = float(utilization)

    reset_at_epoch = window.get("reset_at")
    resets_at = None
    if isinstance(reset_at_epoch, (int, float)):
        try:
            resets_at = datetime.fromtimestamp(reset_at_epoch, tz=UTC)
        except (OverflowError, OSError, ValueError):
            logger.warning("Failed to parse reset_at for %s: %r", prefix, reset_at_epoch)

    return {
        f"{prefix}_utilization": utilization,
        f"{prefix}_resets_at": resets_at,
    }


# A quota window whose declared span reaches a full day is the weekly (7-day)
# limit; anything shorter is the rolling 5-hour limit. Real Codex values are
# 18000s (5h) and 604800s (7d), so the 1-day split sits comfortably between
# them and stays correct even if OpenAI tweaks the exact durations.
_WEEKLY_WINDOW_MIN_SECONDS = 24 * 60 * 60


def _classify_window_prefix(window: dict, fallback_prefix: str) -> str:
    """Pick the ``UsageSnapshot`` column prefix for a Codex rate-limit window.

    Prefer the window's own declared span (``limit_window_seconds``) over its
    payload slot: OpenAI can publish the weekly quota alone in
    ``primary_window``, so mapping by position would file a 7-day window under
    the 5-hour columns. Returns ``"seven_day"`` for a window spanning a day or
    more, ``"five_hour"`` otherwise. When the duration is missing or
    non-positive (e.g. a degenerate empty window), fall back to
    ``fallback_prefix`` — the slot the window came from — to preserve the
    historical position-based behaviour.
    """
    seconds = window.get("limit_window_seconds")
    if isinstance(seconds, (int, float)) and seconds > 0:
        return "seven_day" if seconds >= _WEEKLY_WINDOW_MIN_SECONDS else "five_hour"
    return fallback_prefix


def _extract_windows(rate_limit: dict) -> dict:
    """Route the Codex ``rate_limit`` windows onto the five_hour / seven_day columns.

    Both cross-provider windows start empty (utilization/resets_at null); each
    window actually present in the payload is then classified by its real span
    (:func:`_classify_window_prefix`) and written into the matching prefix. In
    the normal two-window case this reproduces the old primary→five_hour /
    secondary→seven_day mapping; when only the weekly window is published it
    correctly lands under ``seven_day`` and leaves ``five_hour`` null.
    """
    fields = {
        **_extract_window_fields(None, "five_hour"),
        **_extract_window_fields(None, "seven_day"),
    }
    for slot, fallback_prefix in (("primary_window", "five_hour"), ("secondary_window", "seven_day")):
        window = rate_limit.get(slot)
        if window is None:
            continue
        prefix = _classify_window_prefix(window, fallback_prefix)
        fields.update(_extract_window_fields(window, prefix))
    return fields


def _extract_credits_fields(credits: dict | None) -> dict:
    """Translate a Codex ``credits`` block into ``UsageSnapshot.extra_usage_*`` columns.

    Codex's extra usage shape is intentionally lossier than Anthropic's: only a
    remaining balance is published, with no monthly limit and therefore no
    derivable utilization percentage. We populate ``extra_usage_is_enabled``
    from ``has_credits`` and ``extra_usage_remaining_credits`` from
    ``balance`` (parsed from string), leaving the three Anthropic-shape
    columns (``monthly_limit``, ``used_credits``, ``utilization``) at null
    so the front-end can detect the remaining-only mode and render an
    absolute counter instead of a percent ring.
    """
    if credits is None:
        return {
            "extra_usage_is_enabled": False,
            "extra_usage_remaining_credits": None,
        }

    remaining = None
    balance = credits.get("balance")
    if balance is not None:
        try:
            remaining = float(balance)
        except (TypeError, ValueError):
            logger.warning("Failed to parse Codex credits.balance as float: %r", balance)

    return {
        "extra_usage_is_enabled": bool(credits.get("has_credits", False)),
        "extra_usage_remaining_credits": remaining,
    }


def _build_usage_snapshot_fields(raw: dict) -> dict:
    """Translate a raw Codex usage response into ``UsageSnapshot`` columns.

    Pure — no DB access. The raw payload is preserved verbatim in
    ``raw_response`` so we can surface anything Codex-specific (``plan_type``,
    ``email``, ``unlimited``, …) from the wire snapshot later without a
    backfill. Quota windows are routed onto the cross-provider columns by their
    real span (:func:`_extract_windows`) so the front-end consumes the same
    shape as Claude Code's snapshots.

    The ``credits`` block is mapped onto the shared ``extra_usage_*`` columns
    via :func:`_extract_credits_fields`. Because Codex publishes only a
    remaining balance (no monthly limit, no utilization), we populate the
    dedicated ``extra_usage_remaining_credits`` column rather than try to
    fit a remaining count into the Anthropic-shape ``used_credits`` /
    ``monthly_limit`` / ``utilization`` triple.

    The actual ``UsageSnapshot.objects.create`` happens on the DB writer's
    single-writer path, via :class:`_CreateUsageSnapshotJob`.
    """
    from twicc.core.enums import Provider

    rate_limit = raw.get("rate_limit") or {}

    fields: dict = {
        "provider": Provider.CODEX.value,
        "fetched_at": datetime.now(UTC),
        "raw_response": raw,
    }
    fields.update(_extract_windows(rate_limit))
    fields.update(_extract_credits_fields(raw.get("credits")))

    return fields


def read_usage_from_file(file_path: str) -> dict | None:
    """Load a previously dumped Codex usage payload from disk.

    Same shape as :func:`fetch_usage` would return — i.e. the raw
    ChatGPT response — so the rest of the pipeline doesn't care which
    source produced it.
    """
    path = Path(file_path)
    if not path.is_file():
        logger.warning("Codex usage JSON file not found: %s", file_path)
        return None

    try:
        return orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as e:
        logger.warning("Codex usage JSON file is not valid JSON: %s — %s", file_path, e)
        return None
    except OSError as e:
        logger.warning("Failed to read Codex usage JSON file: %s — %s", file_path, e)
        return None


# Top-level keys a payload must contain to be accepted as a Codex usage
# response. Used both by ``CodexHelpers.validate_usage_file_payload``
# (for the read-mode validation) and as documentation of the contract.
USAGE_REQUIRED_KEYS = {"rate_limit"}


def dump_usage_to_file(raw: dict, file_path: str) -> None:
    """Persist the raw API response to ``file_path`` for offline replay."""
    try:
        Path(file_path).write_bytes(orjson.dumps(raw, option=orjson.OPT_INDENT_2))
    except OSError as e:
        logger.warning("Failed to dump Codex usage to file: %s — %s", file_path, e)


def _fetch_usage_raw_blocking(allow_refresh: bool = True) -> dict | None:
    """Blocking half of :func:`fetch_and_save_usage` — pure I/O, no DB.

    Reads the synced settings and either loads a previously dumped payload
    from disk (read mode) or hits ChatGPT's ``wham/usage`` endpoint. On a
    successful API fetch, optionally dumps the raw response to disk. Returns
    the raw payload, or ``None`` when nothing usable was fetched.

    ``allow_refresh`` is forwarded to :func:`fetch_usage` as
    ``refresh_token_if_needed``: when ``False`` an expired token yields a
    soft miss (``None``) instead of triggering a token refresh that, in
    keyring storage mode, rewrites the macOS Keychain item and pops an
    authorization prompt. See
    :func:`twicc.providers.codex.usage_task.start_usage_sync_task`.
    """
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    read_enabled = settings.get("codexUsageReadFileEnabled", False)
    read_path = settings.get("codexUsageReadFilePath", "")
    dump_enabled = settings.get("codexUsageDumpFileEnabled", False)
    dump_path = settings.get("codexUsageDumpFilePath", "")

    if read_enabled and read_path:
        return read_usage_from_file(read_path)

    raw = fetch_usage(refresh_token_if_needed=allow_refresh)
    if raw is not None and dump_enabled and dump_path:
        dump_usage_to_file(raw, dump_path)
    return raw


async def fetch_and_save_usage(allow_refresh: bool = True) -> UsageSnapshot | None:
    """Fetch a Codex usage payload (API or replay file) and persist via the DB writer.

    Mirrors the Claude Code orchestration: when the user has enabled read
    mode in synced settings, load from the configured path instead of hitting
    the API; when dump mode is on, persist the fresh API response to the
    configured path before saving the snapshot. The two modes are mutually
    exclusive — read mode wins and dump is skipped.

    The HTTP / file read happens on a worker thread; the parsed-fields dict
    is then routed through the DB writer via
    :class:`_CreateUsageSnapshotJob`, so the ``UsageSnapshot.objects.create``
    never races the DB writer on the SQLite write lock.

    ``allow_refresh`` (default ``True``) gates the OAuth token refresh on an
    expired token: the macOS background sync passes ``False`` only when Codex
    is in keyring storage mode (so it won't rewrite the Keychain unprompted),
    while a user-initiated refresh passes ``True``.
    """
    from twicc.core.enums import Provider
    from twicc.providers.db_writer import _CreateUsageSnapshotJob, submit_async_job

    raw = await asyncio.to_thread(_fetch_usage_raw_blocking, allow_refresh)
    if raw is None:
        return None

    try:
        fields = _build_usage_snapshot_fields(raw)
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to parse Codex usage snapshot fields: %s", e, exc_info=True)
        return None

    future = asyncio.get_running_loop().create_future()
    try:
        return await submit_async_job(_CreateUsageSnapshotJob(
            provider=Provider.CODEX,
            fields=fields,
            future=future,
        ))
    except Exception as e:  # noqa: BLE001 — log and surface as a soft miss
        logger.error("Failed to save Codex usage snapshot: %s", e, exc_info=True)
        return None
