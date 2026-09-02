"""
Codex CLI OAuth credentials access.

Reads the ChatGPT OAuth token + account id from the Codex CLI's
credential storage so the usage fetcher can call ChatGPT's
``/backend-api/wham/usage`` endpoint with them.

The Codex CLI stores credentials in one of two places, controlled by
its ``cli_auth_credentials_store`` config (default ``file``):

- **File** (``<codex home>/auth.json``): plain JSON with the shape
  ``{"auth_mode": "chatgpt", "tokens": {"access_token": ..., "account_id": ..., "refresh_token": ...}, "last_refresh": "..."}``.
- **Keyring** (any OS): a single ``"Codex Auth"`` service entry whose
  account name is ``"cli|" + sha256(canonical(<codex home>))[:16]`` and
  whose value is the **same JSON blob** that would otherwise live in
  ``auth.json``. In keyring mode the file is removed by the CLI after
  every successful save, so a third-party reader must check the
  keyring when the file is absent.

``<codex home>`` is ``~/.codex`` or the configured ``CODEX_HOME``
(``twicc.provider_homes.codex_home``), resolved at call time.

Mirrors the surface of :mod:`twicc.providers.claude_code.auth` for the
credential half — auth-state tracking (``codex login status`` polling)
lives separately in :mod:`twicc.providers.codex.auth`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import NamedTuple

import orjson
from openai_codex import TextInput
from openai_codex.generated.v2_all import AskForApproval, ReasoningEffort, SandboxMode

from twicc.provider_homes import codex_home

from .bin import make_codex_config
from .sdk_wrappers import TwiccAsyncCodex

logger = logging.getLogger(__name__)


def credentials_path() -> Path:
    """``<codex home>/auth.json`` — the file backend (``cli_auth_credentials_store = "file"``,
    the default) or the fallback in ``"auto"`` mode. Resolved per call."""
    return codex_home().path / "auth.json"

# Keyring service name shared across OSes (constant ``KEYRING_SERVICE`` in
# ``codex-rs/login/src/auth/storage.rs``). The account name is computed
# per Codex home (see :func:`_compute_keyring_account`).
KEYRING_SERVICE = "Codex Auth"

# Cached parsed credentials. Populated on first ``get_credentials()`` call
# and reused on subsequent calls. Dropped by :func:`invalidate_credentials_cache`
# — called both at the start of a refresh attempt and on a usage-API 401 — so a
# token refreshed underneath us (by a real session) is picked up on the re-read.
_cached_credentials: Credentials | None = None

# Track ``last_refresh`` values for which a refresh has already been attempted
# (and failed), to avoid re-spawning the costly SDK throwaway call for the same
# dead token on every cycle. The key is always the value read from *storage* at
# refresh time (never a cached one), so a token that moves forward bypasses the
# guard naturally; :func:`note_credentials_accepted` clears it once any token is
# accepted by the API. Mirrors the equivalent guard in the Claude Code refresh path.
_failed_refresh_keys: set[str] = set()

# Timeout (seconds) for the throwaway SDK turn used to nudge the Codex
# binary into refreshing its tokens — covers the full spawn + initialize
# + thread_start + turn streaming round-trip.
_TOKEN_REFRESH_TIMEOUT = 30

# Fixed model + prompt for the throwaway turn. We don't care about the
# answer; the act of opening a thread and running one turn is what makes
# the codex-app-server binary check / refresh its OAuth tokens. Luna is the
# cheapest model of the catalogue, and the turns below pin ``low`` — the
# lowest effort any Codex model accepts — to keep the probe minimal.
_REFRESH_MODEL = "gpt-5.6-luna"
_REFRESH_PROMPT = "What model are you?"


class Credentials(NamedTuple):
    """Codex OAuth credentials extracted from the CLI's storage.

    ``last_refresh`` is the raw string from the source (or empty when
    absent) — used purely as a cache invalidation key, never parsed.
    """
    access_token: str
    account_id: str
    last_refresh: str


def _compute_keyring_account(codex_home: Path) -> str:
    """Return the keyring account name for ``codex_home``.

    Mirrors ``compute_store_key`` in ``codex-rs/login/src/auth/storage.rs``:
    ``"cli|" + sha256(canonical_path)[:16]``. ``resolve()`` is the
    Python equivalent of Rust's ``canonicalize`` and resolves symlinks
    when possible — the Rust code falls back to the unresolved path on
    canonicalisation failure, so we mirror that with a try/except.
    """
    try:
        canonical = str(codex_home.resolve(strict=False))
    except OSError:
        canonical = str(codex_home)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"cli|{digest[:16]}"


def _read_credentials_from_keyring() -> dict | None:
    """Read the Codex auth blob from the OS keyring, or ``None`` when absent.

    Cross-platform via the ``keyring`` library (macOS Keychain, Linux
    Secret Service, Windows Credential Manager). Returns the parsed
    JSON dict — the value Codex stores is the full ``auth.json``
    payload serialised as a single string.
    """
    try:
        import keyring
    except ImportError:
        logger.debug("keyring library not available, skipping Codex keyring lookup")
        return None

    account = _compute_keyring_account(codex_home().path)

    try:
        raw = keyring.get_password(KEYRING_SERVICE, account)
    except Exception as e:
        logger.debug("Codex keyring read failed: %s", e)
        return None

    if not raw:
        return None

    try:
        data = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse Codex keyring credentials JSON: %s", e)
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_from_file() -> dict | None:
    """Read ``<codex home>/auth.json`` and return the parsed dict, or ``None``."""
    path = credentials_path()
    if not path.is_file():
        return None

    try:
        data = orjson.loads(path.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_data() -> dict | None:
    """Read the full Codex credentials dict from whichever storage is in use.

    Tries the file first (the default backend); on miss, falls back to
    the keyring (which is what's used when the user has set
    ``cli_auth_credentials_store = keyring`` and the CLI has wiped the
    file). Trying file first keeps the common path cheap (no keychain
    auth prompt on macOS).
    """
    data = _read_credentials_from_file()
    if data is not None:
        return data

    return _read_credentials_from_keyring()


def get_credentials() -> Credentials | None:
    """Return the cached :class:`Credentials`, reading storage on first call.

    The cache is dropped by :func:`invalidate_credentials_cache` (at the
    start of a refresh attempt and on a usage-API 401) so a refreshed
    token is picked up on the next call without paying the keyring/file
    read cost on every usage sync.

    Returns ``None`` when:
    - no Codex credentials exist (CLI never logged in),
    - the file/keyring blob is missing the ``tokens`` block,
    - or ``access_token`` / ``account_id`` is unset (e.g. ``auth_mode``
      is ``apikey``, where the user authenticates with an API key
      instead of ChatGPT — the usage endpoint requires the OAuth tokens).
    """
    global _cached_credentials

    if _cached_credentials is not None:
        return _cached_credentials

    data = _read_credentials_data()
    if data is None:
        logger.warning("No Codex credentials found (checked file + keyring)")
        return None

    tokens = data.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")

    if not access_token or not account_id:
        logger.warning(
            "Codex credentials missing access_token or account_id (auth_mode=%s)",
            data.get("auth_mode"),
        )
        return None

    _cached_credentials = Credentials(
        access_token=access_token,
        account_id=account_id,
        last_refresh=data.get("last_refresh") or "",
    )
    return _cached_credentials


def invalidate_credentials_cache() -> None:
    """Drop the cached credentials so the next read goes back to storage.

    Call this after any external action that may have rewritten
    ``auth.json`` / the keyring blob (notably the Codex CLI refreshing
    its own tokens).
    """
    global _cached_credentials
    _cached_credentials = None


def note_credentials_accepted() -> None:
    """Forget past refresh failures after the API accepts the current token.

    A successful usage fetch proves the live token works, so any
    ``last_refresh`` recorded in :data:`_failed_refresh_keys` is moot.
    Clearing keeps the guard from pinning a later expiry to a stale
    "already attempted" verdict and stops the set growing unbounded over
    the process lifetime.
    """
    _failed_refresh_keys.clear()


def refresh_token_via_codex_sdk(last_refresh: str) -> bool:
    """Attempt to refresh the Codex OAuth token by running a throwaway SDK turn.

    Mirrors the Claude Code refresh path: spin up a one-shot AsyncCodex
    session against the wheel-bundled binary, run an ephemeral
    ``thread.turn`` with a trivial prompt, drain the stream, and let the
    codex-app-server binary refresh-and-rewrite ``auth.json`` /
    keyring blob during its session bootstrap. We discard the answer.

    ``last_refresh`` is the value seen on the failed call; we use it as
    a cache key so a permanently-stale token doesn't trigger an endless
    refresh loop. The credential cache is invalidated before the SDK
    call, then re-read after to detect whether ``last_refresh`` actually
    moved forward — that's the success signal, since the binary doesn't
    surface a refresh outcome on its own channel.

    Returns ``True`` when ``last_refresh`` changed (refresh succeeded),
    ``False`` otherwise.
    """
    if last_refresh and last_refresh in _failed_refresh_keys:
        logger.info("Codex token refresh already attempted for last_refresh=%s, skipping", last_refresh)
        return False

    if last_refresh:
        _failed_refresh_keys.add(last_refresh)

    logger.info("Attempting Codex token refresh via throwaway SDK turn (current last_refresh=%s)", last_refresh)

    invalidate_credentials_cache()

    try:
        asyncio.run(
            asyncio.wait_for(_codex_sdk_throwaway_call(), timeout=_TOKEN_REFRESH_TIMEOUT),
        )
    except TimeoutError:
        logger.warning("Codex SDK refresh call timed out after %ds", _TOKEN_REFRESH_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("Codex SDK refresh call failed: %s", e)
        return False

    new_creds = get_credentials()
    new_last_refresh = new_creds.last_refresh if new_creds else ""
    if new_last_refresh == last_refresh:
        logger.warning(
            "Codex token was not refreshed by SDK turn (last_refresh unchanged: %s)",
            last_refresh,
        )
        return False

    logger.info("Codex token refreshed via SDK turn: last_refresh %s → %s", last_refresh, new_last_refresh)
    return True


async def _codex_sdk_throwaway_call() -> None:
    """Run one ephemeral AsyncCodex turn against the bundled binary.

    The point is the side effect: launching the codex-app-server
    subprocess, walking its initialize handshake, and running a real
    turn forces the binary to validate its OAuth tokens against the
    upstream API. A stale ``access_token`` triggers the binary's
    built-in refresh-and-retry path, which rewrites the credentials
    store. We drain the stream so the turn completes cleanly and the
    transport closes without leaking the subprocess.
    """
    config = await make_codex_config()
    async with TwiccAsyncCodex(config=config) as codex:
        thread = await codex.thread_start_with_policy(
            model=_REFRESH_MODEL,
            ephemeral=True,
            sandbox=SandboxMode.danger_full_access,
            approval_policy=AskForApproval.model_validate("never"),
        )
        turn_handle = await thread.turn_with_policy(
            TextInput(_REFRESH_PROMPT),
            effort=ReasoningEffort.low,
        )
        async for _event in turn_handle.stream():
            pass  # drain — we don't care about the reply, just the side effect


async def probe_auth_via_codex_sdk() -> bool | None:
    """Validate the Codex OAuth credentials against the *real* API via a throwaway turn.

    ``codex login status`` (the gate) is a local check; like Claude's, its
    "logged in" verdict can outlive a server-side rejection. When we need
    certainty (the user-initiated "Check again") we run one ephemeral turn and
    watch the stream for the same terminal auth error the live agent treats as a
    logout (:meth:`CodexAgent._is_unauthorized_error`), instead of blindly
    draining like :func:`_codex_sdk_throwaway_call`.

    Returns:
        ``True``  — the turn ran to completion with no terminal auth error.
        ``False`` — an unauthorized terminal error surfaced.
        ``None``  — inconclusive (timeout / transport / other terminal error);
                    the caller should keep the current state rather than guess.
    """
    # Lazy imports: ``agent.agent`` pulls this module at import time, so reusing
    # its classifier here can only be done at call time to avoid an import cycle.
    from openai_codex.generated.v2_all import ErrorNotification

    from .agent.agent import CodexAgent

    result: bool | None = None

    async def _execute() -> None:
        nonlocal result
        config = await make_codex_config()
        async with TwiccAsyncCodex(config=config) as codex:
            thread = await codex.thread_start_with_policy(
                model=_REFRESH_MODEL,
                ephemeral=True,
                sandbox=SandboxMode.danger_full_access,
                approval_policy=AskForApproval.model_validate("never"),
            )
            turn_handle = await thread.turn_with_policy(
                TextInput(_REFRESH_PROMPT),
                effort=ReasoningEffort.low,
            )
            async for event in turn_handle.stream():
                payload = getattr(event, "payload", None)
                # A non-retryable ``error`` notification is terminal. Auth ones
                # mean "not logged in"; any other terminal error is ambiguous
                # for an auth probe, so leave the state untouched (None).
                if (
                    getattr(event, "method", None) == "error"
                    and isinstance(payload, ErrorNotification)
                    and not payload.will_retry
                ):
                    result = False if CodexAgent._is_unauthorized_error(payload) else None
                    return
            # Stream drained with no terminal error → credentials accepted.
            result = True

    try:
        await asyncio.wait_for(_execute(), timeout=_TOKEN_REFRESH_TIMEOUT)
    except Exception as e:
        logger.warning("Codex auth probe via SDK turn was inconclusive: %s", e)

    return result
