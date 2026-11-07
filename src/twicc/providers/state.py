"""Single source of truth for every provider's state.

Two layered concerns live here:

1. **Intent** (persisted) — does the user *want* this provider on?
   Derived from the `disabledProviders` key in the synced settings file:

   - Key absent → no choice made yet (initial install / upgrade from
     pre-feature version). ``get_enabled_providers()`` returns an empty
     set, the orchestrator starts nothing, and the frontend opens the
     activation dialog.
   - Key present → a provider is *enabled* if it is registered (compiled
     in) AND not in the list.

2. **Runtime** (in-memory) — where is the provider in its lifecycle right now?
   Four states cover the full cycle:

   - ``stopped``   — orchestrator never started, or its ``shutdown()`` is done.
   - ``starting``  — ``orchestrator.start()`` is in progress.
   - ``running``   — ``start()`` returned successfully; operational.
   - ``stopping``  — ``shutdown()`` is in progress.

The two layers are connected by hot toggles: enabling a provider transitions
its runtime state ``stopped → starting → running``; disabling it transitions
``running → stopping → stopped``.

Why both live in the same module:

- They answer the same broad question: "what is the state of this provider?"
- Most callers need both — endpoints that pilot a provider's SDK / agent
  must check that it is *enabled* AND *running* before acting. That's what
  :func:`ensure_provider_running` does in one call.
- The error type is shared (:class:`ProviderDisabledError`), and so is the
  exception code propagated to the frontend (``provider_disabled``).

Read-only paths (DB queries, parsing of historical session content) do NOT
need to gate on this module — see spec §6.3.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type (shared by intent and runtime gates)
# ---------------------------------------------------------------------------


class ProviderDisabledError(Exception):
    """Raised when an operation targets a provider that is not usable.

    "Not usable" covers both intent layers:

    - Provider is in ``disabledProviders`` (user disabled it).
    - Provider is in ``starting`` / ``stopping`` (lifecycle in transition).

    Both surface the same way to the frontend (toast: "Cannot send to X:
    it is disabled. Enable it from Settings → Providers.") which is
    correct in both cases — the user simply retries once the situation
    stabilises (the dialog closes, the spinner finishes).
    """

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.ui_message = ui_message("backend.providers.disabled", {"provider": provider.value})
        super().__init__(f"Provider {provider.value} is disabled")


# ---------------------------------------------------------------------------
# Intent layer (persisted in synced_settings.json)
# ---------------------------------------------------------------------------


def _has_disabled_providers_key() -> bool:
    """Return True if the ``disabledProviders`` key is physically present
    in the synced settings file. The mere absence of the key is the
    sentinel that triggers the initial activation dialog (cf. spec §2.1).
    """
    return "disabledProviders" in read_synced_settings()


def get_disabled_providers() -> set[Provider]:
    """Return the set of providers the user has explicitly disabled.

    Returns an empty set if the key is absent or malformed. Unknown
    provider names are silently dropped (forward-compat with futures
    that ship a renamed provider — never raises on stale strings).
    """
    raw = read_synced_settings().get("disabledProviders") or []
    if not isinstance(raw, list):
        return set()
    valid = {p.value for p in Provider}
    return {Provider(v) for v in raw if v in valid}


def get_enabled_providers() -> set[Provider]:
    """Return the set of registered providers not currently disabled.

    Returns an empty set when the ``disabledProviders`` key is absent
    (= no initial choice yet — the back stays idle until the user
    validates the dialog).
    """
    if not _has_disabled_providers_key():
        return set()
    registered = {p for p, _ in get_provider_helpers_registry().items()}
    disabled = get_disabled_providers()
    return registered - disabled


def is_provider_enabled(provider: Provider) -> bool:
    return provider in get_enabled_providers()


def get_orchestration_disabled_providers() -> set[Provider]:
    """Return the providers the user opted OUT of orchestration.

    Read from the ``orchestrationDisabledProviders`` synced-settings key
    (same shape and same defensive parsing as ``disabledProviders``).
    This is a *soft* preference: nothing in the backend or CLI blocks a
    provider based on it — agents are simply told (via ``twicc info``)
    not to pick it on their own.
    """
    raw = read_synced_settings().get("orchestrationDisabledProviders") or []
    if not isinstance(raw, list):
        return set()
    valid = {p.value for p in Provider}
    return {Provider(v) for v in raw if v in valid}


def get_orchestration_providers() -> set[Provider]:
    """Return the enabled providers preferred for orchestration.

    Effective set = enabled providers minus the orchestration opt-outs.
    If that intersection is empty (reachable despite the UI guard, e.g.
    by globally disabling the last orchestration-enabled provider), fall
    back to every enabled provider — an empty preference is no usable
    preference, and the setting is advisory anyway.
    """
    enabled = get_enabled_providers()
    preferred = enabled - get_orchestration_disabled_providers()
    return preferred or enabled


def apply_auto_enable_providers_bootstrap() -> None:
    """Simulate the activation dialog being validated with every provider checked.

    Runs once at backend startup (cf. :func:`twicc.cli.run.run_server`). When
    ``TWICC_AUTO_ENABLE_PROVIDERS=1`` is set and the ``disabledProviders`` key
    is absent from ``settings.json``, writes ``disabledProviders = []`` so the
    initial activation dialog never opens and every orchestrator starts. The
    user is still free to toggle providers from Settings afterwards — the
    one-shot write only seeds the missing initial choice.

    Idempotent: a no-op once the key is present (any prior user choice wins),
    or when the flag is unset.
    """
    from django.conf import settings

    if not settings.AUTO_ENABLE_PROVIDERS:
        return

    from twicc.synced_settings import _settings_lock, write_synced_settings

    with _settings_lock:
        snapshot = read_synced_settings()
        if "disabledProviders" in snapshot:
            return
        snapshot["disabledProviders"] = []
        write_synced_settings(snapshot)
    logger.info("Auto-enabled all providers (TWICC_AUTO_ENABLE_PROVIDERS is set)")


# ---------------------------------------------------------------------------
# Runtime layer (in-memory)
# ---------------------------------------------------------------------------


class ProviderState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


# Sparse dict — every Provider enum value is implicitly STOPPED until the
# orchestrator transitions it. ``get_all_provider_states`` materialises the
# default for callers that want the full picture.
_states: dict[Provider, ProviderState] = {}


def get_provider_state(provider: Provider) -> ProviderState:
    return _states.get(provider, ProviderState.STOPPED)


def get_all_provider_states() -> dict[str, str]:
    """Snapshot of every registered provider's state.

    Always returns every ``Provider`` enum value (with ``stopped`` as the
    default for entries not yet in ``_states``), so the frontend can rely
    on every provider being present in the dict.
    """
    return {p.value: get_provider_state(p).value for p in Provider}


def is_provider_running(provider: Provider) -> bool:
    return get_provider_state(provider) == ProviderState.RUNNING


def ensure_provider_running(provider: Provider) -> None:
    """Raise ``ProviderDisabledError`` if ``provider`` is not currently running.

    This is the runtime gate used by every endpoint that pilots a
    provider's SDK or agent. It is strictly stronger than checking the
    intent layer alone: a provider that is enabled but still in
    ``starting`` / ``stopping`` is refused too, which closes
    race-condition windows during hot toggles. The frontend already greys
    out the relevant UI during transitions; this gate is the defence in
    depth for races between a toggle and an in-flight runtime call.
    """
    if not is_provider_running(provider):
        raise ProviderDisabledError(provider)


async def set_provider_state(provider: Provider, state: ProviderState) -> None:
    """Update the in-memory state and broadcast the change to every client.

    No-op if the new state matches the current one (avoids spamming the
    WS with duplicate transitions). Logs every real change at INFO level
    so the lifecycle is auditable from ``backend.log``.
    """
    previous = _states.get(provider, ProviderState.STOPPED)
    if previous == state:
        return
    _states[provider] = state
    logger.info("Provider state: %s -> %s", previous.value, state.value)
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "provider_state_changed",
                "provider": provider.value,
                "state": state.value,
            },
        },
    )


async def force_disable_after_failed_start(provider: Provider) -> None:
    """Persist ``provider`` into ``disabledProviders`` after a failed start.

    Called by the orchestrator when ``orch.start()`` raises during a hot
    activation. The state has already been rolled back to ``stopped`` by
    the caller; this helper adds the provider to ``disabledProviders``
    on disk so a server restart doesn't blindly retry the failed start,
    and broadcasts the new synced-settings snapshot so every client's
    Settings switch flips back to off.

    Idempotent — if the provider is already in ``disabledProviders`` no
    work is done.
    """
    from twicc.synced_settings import (
        _settings_lock,
        write_synced_settings,
    )

    def _persist() -> tuple[list[str], int] | None:
        with _settings_lock:
            settings = read_synced_settings()
            disabled = set(settings.get("disabledProviders") or [])
            if provider.value in disabled:
                return None
            disabled.add(provider.value)
            new_disabled_list = sorted(disabled)
            settings["disabledProviders"] = new_disabled_list
            new_version = settings.get("_version", 0) + 1
            settings["_version"] = new_version
            write_synced_settings(settings)
            return new_disabled_list, new_version

    result = await sync_to_async(_persist)()
    if result is None:
        return
    new_disabled_list, new_version = result

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "synced_settings_updated",
                "settings": {"disabledProviders": new_disabled_list},
                "version": new_version,
            },
        },
    )
