"""
Claude Code CLI authentication state and credentials access.

Reads OAuth credentials from the system Keychain (macOS) or
``<credentials dir>/.credentials.json`` (Linux) — ``~/.claude`` by default,
relocated with ``CLAUDE_CONFIG_DIR`` / ``CLAUDE_SECURESTORAGE_CONFIG_DIR``
(``twicc.provider_homes``) — exposes the auth state, and broadcasts changes
to connected WebSocket clients.

This module owns all credential reading. Other modules (usage.py,
ASGI consumer) consume from here.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path

import orjson
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def credentials_path() -> Path:
    """``<credentials dir>/.credentials.json`` (cross-platform file storage).

    Resolved at call time from ``twicc.provider_homes`` — never an import-time
    constant, the home is configurable per instance.
    """
    from twicc.provider_homes import claude_secure_storage_dir

    return claude_secure_storage_dir().path / ".credentials.json"

# Track expiresAt values for which a token refresh has already been attempted
# (and failed), to avoid re-spawning the costly SDK throwaway call for the same
# dead token on every cycle. The key is always the value read from *storage* at
# refresh time (never a cached one), so a token that moves forward bypasses the
# guard naturally; ``note_credentials_accepted()`` clears it once any token is
# accepted by the API. Mirrors the equivalent guard in the Codex refresh path.
_failed_refresh_expires: set[int] = set()

# Cached (token, expiresAt) tuple. Populated by get_credentials() on first read
# and reused on subsequent calls (so we don't hit the macOS Keychain — which can
# prompt for authorization — on every usage sync). Dropped by
# ``invalidate_credentials_cache()`` — called both at the start of a refresh
# attempt and on a usage-API 401 — so a token refreshed underneath us (by a real
# session, or a logout/login) is picked up on the re-read.
_cached_credentials: tuple[str, int] | None = None

# Timeout for the SDK token refresh call
_TOKEN_REFRESH_TIMEOUT = 30

# Cached last known auth state (None = never checked yet)
_last_known_authenticated: bool | None = None

# Timeout for the `claude auth status --json` subprocess call.
_AUTH_STATUS_TIMEOUT = 10

# Timeout for the throwaway SDK probe that *validates* the credentials against
# the real API (distinct from, and slower than, the cheap local
# ``claude auth status`` gate). Covers the full connect + query + result
# round-trip.
_AUTH_PROBE_TIMEOUT = 30


def _read_credentials_from_keychain() -> dict | None:
    """Read credentials from the macOS Keychain via the ``keyring`` library."""
    try:
        import keyring
    except ImportError:
        logger.debug("keyring library not available, skipping Keychain lookup")
        return None

    try:
        account = os.environ.get("USER") or getpass.getuser()
    except Exception:
        logger.debug("Cannot determine user account for Keychain lookup")
        return None

    # The service name carries a hash suffix when the credentials dir is
    # relocated (``CLAUDE_CONFIG_DIR`` without an empty
    # ``CLAUDE_SECURESTORAGE_CONFIG_DIR``); resolved per call like the path.
    from twicc.provider_homes import claude_keychain_service

    try:
        raw = keyring.get_password(claude_keychain_service(), account)
    except Exception as e:
        logger.debug("Keychain read failed: %s", e)
        return None

    if not raw:
        return None

    try:
        data = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse Keychain credentials JSON: %s", e)
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_from_file() -> dict | None:
    """Read credentials from ``<credentials dir>/.credentials.json``."""
    path = credentials_path()
    if not path.is_file():
        return None

    try:
        data = orjson.loads(path.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_data() -> dict | None:
    """
    Read the full credentials dict from the appropriate storage.

    On macOS: tries the system Keychain first (via the ``keyring`` library),
    then falls back to the JSON file.
    On other platforms: reads the JSON file directly.

    Returns the parsed dict, or None if credentials cannot be read.
    """
    if sys.platform == "darwin":
        data = _read_credentials_from_keychain()
        if data is not None:
            return data

    return _read_credentials_from_file()


def get_credentials() -> tuple[str, int] | None:
    """
    Read the OAuth access token and expiresAt from credentials storage.

    Returns the cached value if available; otherwise reads from the
    Keychain (macOS) or file, caches it, and returns. The cache is
    dropped by ``invalidate_credentials_cache()`` (at the start of a
    refresh attempt and on a usage-API 401) so a refreshed or
    re-logged-in token is picked up on the next call.

    Returns:
        A (token, expires_at_ms) tuple, or None if not found.
    """
    global _cached_credentials

    if _cached_credentials is not None:
        return _cached_credentials

    data = _read_credentials_data()
    if data is None:
        logger.warning("No credentials found (checked %s)", "Keychain + file" if sys.platform == "darwin" else "file")
        return None

    oauth = data.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    if not token:
        logger.warning("No OAuth access token found in credentials")
        return None

    expires_at = oauth.get("expiresAt", 0)
    _cached_credentials = (token, expires_at)
    return _cached_credentials


def invalidate_credentials_cache() -> None:
    """Drop the cached credentials so the next read goes back to storage.

    Call this after any external action that may have rewritten the
    credentials store (a real session refreshing the OAuth token, or a
    logout/login), and on a usage-API 401 before deciding whether the
    rejection is mere cache staleness or a genuinely expired token.
    """
    global _cached_credentials
    _cached_credentials = None


def note_credentials_accepted() -> None:
    """Forget past refresh failures after the API accepts the current token.

    A successful usage fetch proves the live token works, so any expiresAt
    recorded in :data:`_failed_refresh_expires` is moot. Clearing keeps the
    guard from pinning a later expiry to a stale "already attempted" verdict
    and stops the set growing unbounded over the process lifetime. Mirrors
    the equivalent helper in the Codex credentials path.
    """
    _failed_refresh_expires.clear()


def refresh_token_via_sdk(expires_at: int) -> bool:
    """
    Attempt to refresh the OAuth token by making a throwaway SDK call.

    The SDK automatically refreshes the stored credentials when it connects.
    We send a trivial prompt and discard the response.

    ``expires_at`` is the value seen on the failed call (read fresh from
    storage by the caller, never from the cache); we use it as a cache key
    so a permanently-stale token doesn't trigger an endless refresh loop.
    The credentials cache is invalidated before the SDK call, then re-read
    after to detect whether ``expiresAt`` actually moved forward — that's
    the success signal.

    Returns True if the token was refreshed (expiresAt changed), False otherwise.
    """
    if expires_at and expires_at in _failed_refresh_expires:
        logger.info("Token refresh already attempted for expiresAt=%d, skipping", expires_at)
        return False

    if expires_at:
        _failed_refresh_expires.add(expires_at)

    logger.info("Attempting token refresh via SDK (current expiresAt=%d)", expires_at)

    invalidate_credentials_cache()

    try:
        asyncio.run(_sdk_throwaway_call())
    except Exception as e:
        logger.warning("SDK token refresh call failed: %s", e)
        return False

    new_creds = get_credentials()
    new_expires_at = new_creds[1] if new_creds else 0
    if new_expires_at == expires_at:
        logger.warning("Token was not refreshed by SDK (expiresAt unchanged: %d)", expires_at)
        return False

    logger.info("Token refreshed via SDK: expiresAt %d → %d", expires_at, new_expires_at)
    return True


async def _sdk_throwaway_call() -> None:
    """Make a minimal SDK call to trigger token refresh."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

    from twicc.provider_homes import provider_env_overlay

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
        allowed_tools=[],
        effort='low',
        # Configured provider homes, explicit (see the SDK agent's env_option).
        env=provider_env_overlay(),
    )
    client = ClaudeSDKClient(options=options)

    async def _execute():
        await client.connect()
        await client.query("What model are you?")
        async for msg in client.receive_messages():
            if isinstance(msg, ResultMessage):
                break

    try:
        await asyncio.wait_for(_execute(), timeout=_TOKEN_REFRESH_TIMEOUT)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def probe_auth_via_sdk() -> bool | None:
    """Validate the OAuth credentials against the *real* API via a throwaway call.

    ``claude auth status`` (the gate) only inspects the local token (present +
    unexpired) and will happily report ``loggedIn`` for a token the server
    rejects with 401 — that mismatch is the root of the disappearing-login bug.
    The only authoritative check is an actual API call, so when we need certainty
    (the user-initiated "Check again") we run a minimal throwaway turn and watch
    for the SDK's ``authentication_failed`` signal — the same one a real agent
    turn raises.

    Returns:
        ``True``  — the API accepted the credentials.
        ``False`` — the API rejected them (``authentication_failed``).
        ``None``  — inconclusive (timeout, network, or any other error); the
                    caller should keep the current state rather than guess.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
    )

    from twicc.provider_homes import provider_env_overlay

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
        allowed_tools=[],
        effort="low",
        # Configured provider homes, explicit (see the SDK agent's env_option).
        env=provider_env_overlay(),
    )
    client = ClaudeSDKClient(options=options)

    result: bool | None = None

    async def _execute() -> None:
        nonlocal result
        await client.connect()
        await client.query("ping")
        async for msg in client.receive_messages():
            if isinstance(msg, AssistantMessage) and msg.error == "authentication_failed":
                result = False
                return
            if isinstance(msg, ResultMessage):
                result = True
                return

    try:
        await asyncio.wait_for(_execute(), timeout=_AUTH_PROBE_TIMEOUT)
    except Exception as e:
        logger.warning("Auth probe via SDK was inconclusive: %s", e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


def _build_auth_message(authenticated: bool) -> dict:
    """Build the claude_code:auth_updated message payload."""
    return {
        "type": "claude_code:auth_updated",
        "authenticated": authenticated,
    }


def get_last_known_authenticated() -> bool | None:
    """Return the last known auth state (None if never checked yet)."""
    return _last_known_authenticated


async def check_auth_status() -> bool:
    """
    Run ``claude auth status --json`` (Claude Code CLI command) and return ``loggedIn``.

    Calls the bundled CLI binary directly (the same one the SDK uses). This
    avoids depending on how TwiCC itself was installed (uvx, pip, etc.).

    Returns False on any failure (process error, timeout, missing field,
    invalid JSON). This is only a *local* check (token present + not expired):
    it can still report ``loggedIn`` for a token the server rejects with 401,
    which is why a real ``authentication_failed`` outranks it and why
    ``check_and_broadcast`` never lets its ``true`` resurrect an authoritative
    ``False`` (see there).
    """
    from .bin import resolve_bundled_binary

    try:
        binary = resolve_bundled_binary()
    except FileNotFoundError:
        logger.warning("Cannot check Claude Code auth status: bundled CLI not found")
        return False

    from twicc.provider_homes import provider_env_overlay

    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary), "auth", "status", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Configured provider homes, explicit on top of the inherited env.
            env={**os.environ, **provider_env_overlay()},
        )
    except Exception as e:
        logger.warning("Cannot launch Claude Code auth status check: %s", e)
        return False

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_AUTH_STATUS_TIMEOUT)
    except TimeoutError:
        logger.warning("Claude Code auth status check timed out after %ds", _AUTH_STATUS_TIMEOUT)
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return False

    if proc.returncode != 0:
        logger.warning(
            "Claude Code auth status check failed (exit %d): %s",
            proc.returncode,
            stderr.decode(errors="replace").strip(),
        )
        return False

    try:
        data = orjson.loads(stdout)
    except orjson.JSONDecodeError as e:
        logger.warning("Claude Code auth status returned invalid JSON: %s", e)
        return False

    return bool(data.get("loggedIn"))


async def get_auth_message_for_connection() -> dict:
    """
    Build a claude_code:auth_updated message for a single client on WS connect.

    Uses the cached state. If it is still unknown (very first connection in a
    fresh process), runs one check inline so the connecting client doesn't
    briefly see a wrong "not authenticated" state.
    """
    if _last_known_authenticated is None:
        # Populate the cache now via check_and_broadcast (which also
        # updates state and broadcasts on change).
        await check_and_broadcast()
    return _build_auth_message(bool(_last_known_authenticated))


async def check_and_broadcast(*, force: bool = False, probe: bool = False) -> bool | None:
    """
    Determine the Claude Code auth state and broadcast claude_code:auth_updated on change.

    The local ``claude auth status`` gate is the cheap first signal, but its
    ``loggedIn: true`` is NOT trusted on its own — it only reflects the local
    token and lies when the server rejects it. (That mismatch was the bug: the
    login prompt raised by a real 401 vanished the instant the next gate poll
    re-broadcast ``true``.) So:

    - gate says *not* logged in → ``False`` (trustworthy: no usable credential).
    - gate says logged in **and** ``probe=True`` → confirm with a real throwaway
      API call. This is the user-initiated "Check again" — the only path that
      may promote ``False`` → ``True`` (we never auto-probe on a timer).
    - gate says logged in and ``probe=False`` → promote only from the unknown
      baseline; never resurrect an authoritative ``False`` (set by a real 401 or
      a failed probe) on the strength of the local gate alone.

    When ``force`` is True, the message is always broadcast (used to answer a
    manual "Check again", where echoing the current state to the requester is
    desirable even without a change).

    Returns the current authenticated tristate (``True`` / ``False`` / ``None``
    when a forced probe was inconclusive at the unknown baseline).
    """
    global _last_known_authenticated

    gate = await check_auth_status()
    if not gate:
        authenticated: bool | None = False
    elif probe:
        probed = await probe_auth_via_sdk()
        # Inconclusive → keep the current state rather than flip either way.
        authenticated = _last_known_authenticated if probed is None else probed
    else:
        # Untrusted "true": never promote an authoritative False back to True;
        # only the unknown baseline (or a True we already hold) may read as True.
        authenticated = _last_known_authenticated is not False

    changed = authenticated != _last_known_authenticated
    _last_known_authenticated = authenticated

    if changed or force:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": _build_auth_message(bool(authenticated)),
            },
        )

    return authenticated


async def mark_unauthenticated_and_broadcast() -> None:
    """
    Force the auth state to ``False`` and broadcast.

    Used when an external signal (e.g. the SDK reporting
    ``authentication_failed``) tells us the credentials are no longer
    accepted, even if a fresh ``claude auth status`` would still claim
    ``loggedIn`` (race / stale token).
    """
    global _last_known_authenticated

    changed = _last_known_authenticated is not False
    _last_known_authenticated = False

    if changed:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": _build_auth_message(False),
            },
        )
