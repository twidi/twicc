"""
Codex CLI authentication state.

Runs ``codex login status`` against the wheel-bundled Codex binary to
determine whether the user is logged into Codex. The exit code is the
source of truth (0 = logged in, 1 = not logged in).

Mirrors the surface of ``providers.claude_code.auth`` so the WS handler
can stay structurally identical between providers.
"""

from __future__ import annotations

import asyncio
import logging
import os

from channels.layers import get_channel_layer

from .bin import resolve_bundled_binary

logger = logging.getLogger(__name__)

# Cached last known auth state (None = never checked yet).
_last_known_authenticated: bool | None = None

# Timeout for the ``codex login status`` subprocess call.
_AUTH_STATUS_TIMEOUT = 10


def _build_auth_message(authenticated: bool) -> dict:
    """Build the ``codex:auth_updated`` message payload."""
    return {
        "type": "codex:auth_updated",
        "authenticated": authenticated,
    }


def get_last_known_authenticated() -> bool | None:
    """Return the last known auth state (None if never checked yet)."""
    return _last_known_authenticated


async def check_auth_status() -> bool:
    """Run ``codex login status`` on the bundled binary and return whether exit code is 0.

    The Codex CLI uses the exit code as the source of truth (0 = logged in,
    1 = not logged in). We don't parse stdout — only the return code.

    Returns ``False`` on any failure (process spawn error, timeout, non-zero
    exit code, missing bundled binary).
    """
    try:
        binary = str(resolve_bundled_binary())
    except FileNotFoundError as e:
        logger.warning("Cannot check Codex auth status: %s", e)
        return False

    from twicc.provider_homes import ensure_codex_home, provider_env_overlay

    # Codex refuses to start on a missing CODEX_HOME (exit 1, nothing created).
    ensure_codex_home()
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "login", "status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            # Configured provider homes, explicit on top of the inherited env.
            env={**os.environ, **provider_env_overlay()},
        )
    except Exception as e:
        logger.warning("Cannot launch Codex auth status check: %s", e)
        return False

    try:
        await asyncio.wait_for(proc.wait(), timeout=_AUTH_STATUS_TIMEOUT)
    except TimeoutError:
        logger.warning("Codex auth status check timed out after %ds", _AUTH_STATUS_TIMEOUT)
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return False

    return proc.returncode == 0


async def get_auth_message_for_connection() -> dict:
    """Build a ``codex:auth_updated`` message for a single client on WS connect.

    Uses the cached state. If it is still unknown (very first connection in a
    fresh process), runs one check inline so the connecting client doesn't
    briefly see a wrong "not authenticated" state.
    """
    if _last_known_authenticated is None:
        await check_and_broadcast()
    return _build_auth_message(bool(_last_known_authenticated))


async def check_and_broadcast(*, force: bool = False, probe: bool = False) -> bool | None:
    """Determine the Codex auth state and broadcast ``codex:auth_updated`` on change.

    Mirrors :func:`providers.claude_code.auth.check_and_broadcast`. The local
    ``codex login status`` gate is the cheap first signal, but its "logged in"
    verdict is NOT trusted on its own — a local check can outlive a server-side
    rejection, so letting it re-broadcast ``true`` would clobber the login
    prompt raised by a real 401. So:

    - gate says *not* logged in → ``False`` (trustworthy: no usable credential).
    - gate says logged in **and** ``probe=True`` → confirm with a real throwaway
      turn. Used only on the user-initiated "Check again", never on a timer.
    - gate says logged in and ``probe=False`` → promote only from the unknown
      baseline; never resurrect an authoritative ``False`` (set by a real auth
      error or a failed probe) on the strength of the local gate alone.

    When ``force`` is True, the message is always broadcast (used to answer a
    manual "Check again").

    (Whether ``codex login status`` actually lies after a real auth failure the
    way Claude's does is unverified — but this gate-first shape is correct either
    way: an honest gate is caught for free at the gate, a lying one by the probe.)
    """
    global _last_known_authenticated

    gate = await check_auth_status()
    if not gate:
        authenticated: bool | None = False
    elif probe:
        from .credentials import probe_auth_via_codex_sdk

        probed = await probe_auth_via_codex_sdk()
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
    """Force the auth state to ``False`` and broadcast.

    Mirrors :func:`providers.claude_code.auth.mark_unauthenticated_and_broadcast`,
    which is called from the Claude message loop on
    ``AssistantMessage.error == "authentication_failed"``. The Codex
    equivalent is called from :meth:`CodexAgent._handle_stream_event`
    when :meth:`CodexAgent._is_unauthorized_error` matches an incoming
    ``ErrorNotification``. The matching covers three paths because Codex
    upstream surfaces auth failures inconsistently:

    1. ``CodexErrorInfoValue.unauthorized`` (``RefreshTokenFailed``).
    2. ``HttpConnectionFailed`` / ``ResponseStreamConnectionFailed``
       variants with ``http_status_code in {401, 403}``.
    3. ``"status 40[13]"`` substring in the message (covers
       ``CodexErr::UnexpectedStatus(401)`` which falls through to
       ``Other`` — the typical session-resume-on-expired-token case).

    Without this fast path, an expired token would only surface on the next
    "Check again" (there is no background auth poll).
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
