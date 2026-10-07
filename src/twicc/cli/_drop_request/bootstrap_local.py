"""Load the same data ``/api/bootstrap/`` returns, but in-process.

Used by the CLI to validate user inputs without making any HTTP call.
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent_settings_presets import read_agent_settings_presets
from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings


class ProviderBootstrap(NamedTuple):
    provider: Provider
    is_disabled: bool
    agent_settings_categories: dict
    agent_settings_choices: dict
    agent_settings_aliases: dict
    # Permission modes a session may use in an UNTRUSTED (or unknown-trust)
    # project, and the fallback the CLI clamps an out-of-set value to (mirrors
    # the backend ``clamp_permission_mode_for_untrusted`` so the client message
    # matches what the server would enforce). Empty / ``None`` for providers
    # without an untrusted permission mode. See project-trust design §13.
    untrusted_permission_modes: frozenset
    untrusted_permission_mode_default: str | None
    model_registry: list
    presets: list


class LocalBootstrap(NamedTuple):
    disabled_providers_present: bool
    disabled_providers: list[str]
    default_provider: str | None
    providers: dict[str, ProviderBootstrap]


def load_local_bootstrap() -> LocalBootstrap:
    """Build the bootstrap snapshot from on-disk + in-code sources."""
    synced = read_synced_settings()
    disabled_present = "disabledProviders" in synced
    disabled = synced.get("disabledProviders") or []
    default_provider = synced.get("defaultProvider") or None

    providers: dict[str, ProviderBootstrap] = {}
    for provider, helpers in get_provider_helpers_registry().items():
        provider_data = helpers.get_bootstrap_data() or {}
        presets = read_agent_settings_presets(provider).get("presets", [])
        # Resolve the provider's untrusted permission-mode set + default once,
        # so the alias/clamp pipeline stays Django-free (no synced-settings read
        # per session). The configured default is honored only when it is itself
        # in-set; otherwise the hard default applies — same rule as the backend.
        untrusted_modes = frozenset(helpers.UNTRUSTED_PERMISSION_MODES)
        untrusted_default: str | None = None
        untrusted_key = helpers.UNTRUSTED_PERMISSION_MODE_SYNCED_KEY
        if untrusted_key is not None:
            configured = synced.get(untrusted_key)
            untrusted_default = (
                configured if configured in untrusted_modes
                else helpers.SYNCED_SETTINGS_DEFAULTS.get(untrusted_key)
            )
        providers[provider.value] = ProviderBootstrap(
            provider=provider,
            is_disabled=provider.value in disabled,
            agent_settings_categories=provider_data.get("agent_settings_categories", {}),
            agent_settings_choices=provider_data.get("agent_settings_choices", {}),
            agent_settings_aliases=provider_data.get("agent_settings_aliases", {}),
            untrusted_permission_modes=untrusted_modes,
            untrusted_permission_mode_default=untrusted_default,
            model_registry=provider_data.get("model_registry", []),
            presets=presets,
        )

    return LocalBootstrap(
        disabled_providers_present=disabled_present,
        disabled_providers=disabled,
        default_provider=default_provider,
        providers=providers,
    )
