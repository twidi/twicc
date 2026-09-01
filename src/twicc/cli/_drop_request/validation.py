"""Pre-flight validation for CLI commands that talk to the live TwiCC server.

Shared by ``twicc create-session`` and ``twicc update-session settings``.
Aggregates errors so the user sees every problem at once, lint-style.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple


_FIELD_TO_FLAG = {
    "selected_model": "--model",
    "thinking_enabled": "--thinking",
}


def _field_to_flag(field: str) -> str:
    if field in _FIELD_TO_FLAG:
        return _FIELD_TO_FLAG[field]
    return f"--{field.replace('_', '-')}"


def _field_to_unset_token(field: str) -> str:
    """Translate an internal AgentSettings field to its ``--unset <token>`` value.

    The token is the dash-cased form of the public flag name (without the
    leading ``--``). ``selected_model`` -> ``model``, ``thinking_enabled`` ->
    ``thinking``, etc. Mirrors :func:`_field_to_flag` but for the value the
    user actually types after ``--unset``.
    """
    flag = _field_to_flag(field)
    return flag.removeprefix("--")


class ValidationError(NamedTuple):
    field: str
    code: str
    message: str


def validate_provider(provider: str, bootstrap) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        errors.append(ValidationError(
            "provider", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        ))
        return errors
    if not bootstrap.disabled_providers_present:
        errors.append(ValidationError(
            "provider", "no_provider_configured",
            "TwiCC has never been started. Run `twicc` once to activate providers.",
        ))
        return errors
    if bootstrap.providers[provider].is_disabled:
        errors.append(ValidationError(
            "provider", "provider_disabled",
            f"Provider {provider} is disabled. Enable it from the UI or settings.",
        ))
    return errors


def _model_offered(entry: dict) -> bool:
    """Whether a serialised model-registry entry is a valid ``--model`` value.

    Both a disabled and a retired model resolve away to a fallback, so accepting
    either would silently run something other than what the caller asked for.
    ``info agent-settings`` and the ``--model`` help list exactly this set.

    ``retirement_date`` rides the wire as an ISO day, so the comparison against
    today is a plain string compare — and it is strict, like
    ``BaseProviderHelpers.is_model_version_retired``: a model stays valid
    through the whole of its retirement day.
    """
    if not entry.get("enabled", True):
        return False
    retirement_date = entry.get("retirement_date")
    return not retirement_date or date.today().isoformat() <= retirement_date


def validate_settings(provider: str, settings, bootstrap) -> list[ValidationError]:
    """Check each non-None field against the provider's choices."""
    errors: list[ValidationError] = []
    pb = bootstrap.providers[provider]
    categories = pb.agent_settings_categories or {}
    all_supported = set()
    for fields in categories.values():
        all_supported.update(fields)
    choices = pb.agent_settings_choices or {}
    model_ids = {
        m.get("selected_model") for m in pb.model_registry or [] if _model_offered(m)
    }
    aliases = pb.agent_settings_aliases or {}

    def _alias_hint(field_name: str) -> str:
        # Surface the provider's aliases on an invalid value so a user
        # who typed an unsupported alias discovers the accepted ones.
        keys = sorted((aliases.get(field_name) or {}).keys())
        return f" Or an alias: {keys}." if keys else ""

    for field, value in settings._asdict().items():
        if value is None:
            continue
        if field not in all_supported:
            errors.append(ValidationError(
                _field_to_flag(field), "unsupported_field",
                f"{field} is not supported by {provider}. Supported: {sorted(all_supported)}.",
            ))
            continue
        if field == "selected_model":
            if value not in model_ids:
                ids = sorted(m for m in model_ids if m)
                errors.append(ValidationError(
                    _field_to_flag(field), "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {ids}." + _alias_hint(field),
                ))
        elif field in choices:
            if value not in choices[field]:
                errors.append(ValidationError(
                    _field_to_flag(field), "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {choices[field]}." + _alias_hint(field),
                ))
    return errors


def validate_no_set_unset_conflict(
    overrides: dict[str, object | None], unset_fields: list[str],
) -> list[ValidationError]:
    """Reject ``--<field> VALUE`` combined with ``--unset <field>`` for the same field.

    Setting a value and immediately unsetting it is a contradictory intent;
    we fail loudly rather than picking a winner silently.
    """
    errors: list[ValidationError] = []
    unset_set = set(unset_fields)
    for field, value in overrides.items():
        if value is not None and field in unset_set:
            errors.append(ValidationError(
                f"--unset {_field_to_unset_token(field)}", "unset_conflict",
                f"--unset {_field_to_unset_token(field)} conflicts with "
                f"{_field_to_flag(field)} {value!r}; pick one.",
            ))
    return errors


def validate_hidden_constraints(
    provider: str,
    settings,
    *,
    hidden: bool,
) -> list[ValidationError]:
    """Enforce hidden-session invariants on the resolved AgentSettings bundle.

    Called with the **post-resolution** settings (preset applied + CLI
    overrides + ``enforce_agent_settings_consistency`` already run), so the
    ``permission_mode`` and ``question_widget`` values reflect what the
    session would actually be started with.

    When ``hidden`` is ``False``, returns an empty list — the constraints
    only apply to hidden sessions. When ``hidden`` is ``True``:

    - ``permission_mode`` must be in the provider helpers'
      ``NON_INTERACTIVE_PERMISSION_MODES`` (each provider declares which of its
      modes run without interactive approval);
    - ``question_widget`` must NOT be ``True`` for providers that use it
      (they declare it in their ``AGENT_SETTINGS_CATEGORIES``). A provider
      that does not is left alone.

    Always returns a flat list of :class:`ValidationError` so the caller
    can aggregate with the other validators.
    """
    if not hidden:
        return []
    errors: list[ValidationError] = []

    # --- permission_mode whitelist -------------------------------
    # Each provider declares its non-interactive modes via
    # ``NON_INTERACTIVE_PERMISSION_MODES`` on its helpers class. An empty frozenset
    # means the provider does not support hidden sessions.
    from twicc.providers.helpers import get_provider_helpers
    try:
        helpers = get_provider_helpers(provider)
        whitelist = helpers.NON_INTERACTIVE_PERMISSION_MODES
    except KeyError:
        whitelist = frozenset()
    if not whitelist:
        errors.append(ValidationError(
            "--hidden", "hidden_unsupported_provider",
            f"--hidden is not supported for provider {provider!r}. "
            f"No non-interactive permission mode is configured for it.",
        ))
        return errors

    mode = getattr(settings, "permission_mode", None)
    if mode not in whitelist:
        whitelist_str = ", ".join(sorted(whitelist))
        errors.append(ValidationError(
            "--permission-mode", "hidden_requires_non_interactive",
            f"--hidden requires a non-interactive permission_mode. "
            f"Provider {provider} accepts: {whitelist_str}. Got: {mode!r}.",
        ))

    # --- question_widget incompatibility -------------------------
    # Only for providers that actually map the field to a widget tool — they
    # declare it in their categories. A provider that ignores it is left alone.
    supported: set[str] = set()
    for fields in (helpers.AGENT_SETTINGS_CATEGORIES or {}).values():
        supported.update(fields)
    if "question_widget" in supported:
        qw = getattr(settings, "question_widget", None)
        if qw is True:
            errors.append(ValidationError(
                "--question-widget", "hidden_incompatible_with_question_widget",
                "--hidden is incompatible with question_widget=True. "
                "Pass --no-question-widget to override.",
            ))

    return errors
