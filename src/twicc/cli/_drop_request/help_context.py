"""Django-free help-string context for the CLI commands.

Loaded at module import time of :mod:`twicc.cli.create_session.command` and
:mod:`twicc.cli.update_session.settings_command` to enrich the ``--help``
output with the user's current providers, presets, per-field defaults, and
model aliases — without paying for ``django.setup()``.

Pure file I/O + ``Provider`` enum + per-provider ``constants.py``
(themselves Django-free by design). ~30 ms cold. Designed to never raise:
a missing / malformed file degrades to "no info" so ``--help`` always
succeeds.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

import orjson

from twicc.core.enums import Provider
from twicc.paths import get_data_dir
from twicc.providers.claude_code.constants import (
    AGENT_SETTINGS_FIELDS_MAPPING as _CC_FIELD_MAP,
    MODEL_VERSIONS as _CC_MODEL_VERSIONS,
    SYNCED_SETTINGS_DEFAULTS as _CC_BUILTIN_DEFAULTS,
)
from twicc.providers.codex.constants import (
    AGENT_SETTINGS_FIELDS_MAPPING as _CX_FIELD_MAP,
    MODEL_VERSIONS as _CX_MODEL_VERSIONS,
    SYNCED_SETTINGS_DEFAULTS as _CX_BUILTIN_DEFAULTS,
)


# Provider → (synced-key → cli-field mapping, built-in defaults). Keyed by
# ``Provider.value`` so the ``--help`` rendering stays Django-free.
_PER_PROVIDER: dict[str, tuple[dict[str, str], dict, list]] = {
    Provider.CLAUDE_CODE.value: (_CC_FIELD_MAP, _CC_BUILTIN_DEFAULTS, _CC_MODEL_VERSIONS),
    Provider.CODEX.value: (_CX_FIELD_MAP, _CX_BUILTIN_DEFAULTS, _CX_MODEL_VERSIONS),
}


class ModelAlias(NamedTuple):
    """One row of the model registry for ``--help`` rendering.

    ``alias`` is the value the user would pass to ``--model`` (e.g.
    ``opus`` or ``opus-4.5``). ``is_latest`` flags the bare-alias-for-latest
    entries so the help can mark them, and ``version`` is the underlying
    family version (e.g. ``"4.7"``) so the latest tag can name the
    concrete version that the alias currently points to.
    """
    alias: str
    is_latest: bool
    version: str


class HelpContext(NamedTuple):
    """Lightweight snapshot for ``--help`` enrichment.

    ``providers`` lists the currently enabled providers (registered AND not
    in ``disabledProviders``). ``default_provider`` is the default provider
    from settings when known. ``presets`` maps each enabled provider to its
    saved preset names. ``field_defaults`` is keyed by the CLI flag field
    name (``effort``, ``selected_model``, etc.) and lists, per enabled
    provider that actually uses the field, its resolved default
    (user-overridden settings value or built-in fallback). ``model_aliases``
    is the provider → ordered list of :class:`ModelAlias` rows used to
    populate the ``--model`` help.
    """
    providers: list[str]
    default_provider: str | None
    presets: dict[str, list[str]]
    field_defaults: dict[str, dict[str, object]]
    model_aliases: dict[str, list[ModelAlias]]


def _alias_for(mv) -> str:
    """Mirror ``BaseProviderHelpers.selected_model_value`` Django-free."""
    return mv.model if mv.latest else f"{mv.model}-{mv.version}"


def _is_offered(mv) -> bool:
    """Whether ``mv`` may be passed to ``--model``: enabled and not retired.

    Inlined rather than imported: this module is deliberately Django-free (see
    the module docstring), so it cannot reach ``BaseProviderHelpers``. The
    retirement comparison is strict, exactly like ``is_model_version_retired``
    — a model stays offered through the whole of its retirement day.
    """
    if not mv.enabled:
        return False
    return mv.retirement_date is None or date.today() <= mv.retirement_date


def load_help_context() -> HelpContext:
    """Read settings + presets files; no Django setup involved."""
    data_dir = get_data_dir()

    try:
        synced = orjson.loads((data_dir / "settings.json").read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError, OSError):
        synced = {}

    disabled = set(synced.get("disabledProviders") or [])
    default_provider = synced.get("defaultProvider") or None

    providers: list[str] = []
    presets_by_provider: dict[str, list[str]] = {}
    for p in Provider:
        if p.value in disabled:
            continue
        providers.append(p.value)
        try:
            data = orjson.loads(
                (data_dir / f"{p.value}-settings-presets.json").read_bytes()
            )
            names = [
                preset.get("name")
                for preset in (data.get("presets") or [])
                if preset.get("name")
            ]
        except (FileNotFoundError, orjson.JSONDecodeError, OSError):
            names = []
        presets_by_provider[p.value] = names

    # field → {provider: resolved value}
    field_defaults: dict[str, dict[str, object]] = {}
    model_aliases: dict[str, list[ModelAlias]] = {}
    for provider_name in providers:
        mapping, builtins, model_versions = _PER_PROVIDER.get(
            provider_name, ({}, {}, []),
        )
        for field, key in mapping.items():
            value = synced.get(key, builtins.get(key))
            if value is None:
                continue
            field_defaults.setdefault(field, {})[provider_name] = value
        model_aliases[provider_name] = [
            ModelAlias(alias=_alias_for(mv), is_latest=mv.latest, version=mv.version)
            for mv in model_versions
            if _is_offered(mv)
        ]

    return HelpContext(
        providers=providers,
        default_provider=default_provider,
        presets=presets_by_provider,
        field_defaults=field_defaults,
        model_aliases=model_aliases,
    )
