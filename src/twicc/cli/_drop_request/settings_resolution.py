"""Shared resolution of ``settings`` updates for the singular and batch CLI.

Single source of truth for ``twicc update-session <ID> settings`` and
``twicc update-sessions settings``: both parse the same flags and resolve the
same per-provider settings update. Split in two so the batch can reuse the
exact same logic with a different error-handling shape:

- :func:`parse_settings_flags` — **provider-independent** (global) validation of
  the raw CLI flags: unknown ``--unset`` token, ``--<field>`` / ``--unset``
  conflict, no-op. Same for every targeted session, so the singular and the
  batch validate it once. ``--context-max`` is NOT parsed here anymore: an
  alias like ``max`` only has meaning once the provider is known, so the raw
  string is carried through to :func:`prepare_settings`.
- :func:`prepare_settings` — **per-provider** resolution against one resolved
  session: provider enabled, alias resolution + ``context_max`` parse,
  silent drop of fields the provider doesn't support (set or ``--unset``),
  preset resolution (presets are provider-scoped), value validation, hidden
  invariants. The singular runs it for one session; the batch runs it per id (a
  failure becomes a per-id ``validation_error`` while the other sessions proceed).

Both return either their successful product or a flat ``list[ValidationError]``
so the caller decides how to surface it (emit + exit for the singular / global
batch errors; a per-id entry for the batch).
"""

from __future__ import annotations

# Only Django-free modules at import time: this module is imported at the top of
# ``settings_command`` (loaded when the CLI boots, before ``django.setup()``),
# so the Django-heavy bits (``presets``, ``providers.helpers``) are imported
# lazily inside :func:`prepare_settings`. ``aliases`` and ``validation`` are
# Django-free at module top (``aliases`` only pulls ``help_strings`` and
# ``validation`` in turn).
from twicc.cli._drop_request.aliases import resolve_overrides, supported_fields
from twicc.cli._drop_request.validation import (
    ValidationError,
    validate_no_set_unset_conflict,
    validate_provider,
    validate_settings,
)


# Public token (typed after ``--unset``) -> AgentSettings field name. The token
# is the dash-cased public flag name without the leading ``--`` (``--model`` ->
# ``model``, ``--permission-mode`` -> ``permission-mode``, ...).
UNSET_TOKEN_TO_FIELD: dict[str, str] = {
    "model": "selected_model",
    "effort": "effort",
    "permission-mode": "permission_mode",
    "thinking": "thinking_enabled",
    "claude-in-chrome": "claude_in_chrome",
    "fast-mode": "fast_mode",
    "context-max": "context_max",
    "question-widget": "question_widget",
}


def unset_help() -> str:
    """Help string for the ``--unset`` option (shared by both commands)."""
    tokens = sorted(UNSET_TOKEN_TO_FIELD.keys())
    return (
        "Reset a setting back to NULL (use the synced default). Repeatable: "
        "pass once per setting to clear. Accepted tokens: "
        + ", ".join(f"'{t}'" for t in tokens)
        + ". Conflicts with the matching per-field flag (e.g. --unset model "
        "and --model X cannot be combined). Allowed alongside --preset to "
        "wipe a field the preset would otherwise have set."
    )


def parse_settings_flags(
    *,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    thinking: bool | None,
    claude_in_chrome: bool | None,
    fast_mode: bool | None,
    question_widget: bool | None,
    context_max: str | None,
    unset: list[str],
    preset: str | None,
) -> tuple[dict[str, object | None], list[str]] | list[ValidationError]:
    """Provider-independent parse + validation of the raw settings flags.

    Returns ``(overrides, unset_fields)`` on success, or a flat list of
    :class:`ValidationError` (``unknown_unset_field`` / ``unset_conflict`` /
    ``no_op``) when the flags are malformed on their own — independent of any
    target session's provider. ``context_max`` is left as a raw string for
    per-provider alias resolution + parsing in :func:`prepare_settings`.
    """
    errors: list[ValidationError] = []

    # --unset tokens -> internal field names.
    unset_fields: list[str] = []
    for token in unset:
        field = UNSET_TOKEN_TO_FIELD.get(token)
        if field is None:
            accepted = sorted(UNSET_TOKEN_TO_FIELD.keys())
            errors.append(ValidationError(
                f"--unset {token}", "unknown_unset_field",
                f"Unknown setting {token!r}. Accepted tokens: {accepted}.",
            ))
        else:
            unset_fields.append(field)

    overrides: dict[str, object | None] = {
        "selected_model": model,
        "effort": effort,
        "permission_mode": permission_mode,
        "thinking_enabled": thinking,
        "claude_in_chrome": claude_in_chrome,
        "fast_mode": fast_mode,
        # Raw string: aliases (``max``/``min``) and the literal token
        # forms (``200k``/``1m``/...) are both resolved + parsed per-provider in
        # ``prepare_settings`` — an alias only has meaning once we know which
        # provider's table to consult.
        "context_max": context_max,
        "question_widget": question_widget,
    }

    # Contradictory ``--<field> VALUE`` + ``--unset <field>``.
    errors.extend(validate_no_set_unset_conflict(overrides, unset_fields))

    # No-op: use the RAW user inputs (not parsed forms) so a malformed value
    # still counts as "the user tried to touch this field" and does not
    # spuriously add no_op on top of the parse error.
    user_touched_something = (
        preset is not None
        or any(v is not None for v in (
            model, effort, permission_mode, thinking, claude_in_chrome,
            fast_mode, context_max, question_widget,
        ))
        or bool(unset)
    )
    if not user_touched_something:
        errors.append(ValidationError(
            "<all>", "no_op",
            "Nothing to update; pass at least one of --preset, a per-field "
            "flag, or --unset <field>.",
        ))

    if errors:
        return errors
    return overrides, unset_fields


def prepare_settings(
    resolved,
    *,
    overrides: dict[str, object | None],
    unset_fields: list[str],
    preset: str | None,
    bootstrap,
) -> tuple[dict[str, object | None], bool] | list[ValidationError]:
    """Resolve the settings update for one session against its provider.

    ``overrides`` / ``unset_fields`` come from :func:`parse_settings_flags`
    (already globally validated). Aliases are resolved and fields the
    provider doesn't support are dropped silently (no-op) before validation.
    Returns ``(updates, replace_all)`` ready for a
    ``kind="session:update_settings"`` payload, or a flat list of
    :class:`ValidationError` (``unknown_provider`` / ``provider_disabled`` /
    ``invalid_format`` / ``invalid_preset`` / ``invalid_choice`` / hidden
    invariants) when this session's provider rejects the requested change.
    """
    # Lazy (Django-heavy) imports — see the module note above.
    from twicc.cli._drop_request.aliases import clamp_untrusted_permission_mode
    from twicc.cli._drop_request.presets import PresetError, apply_preset_and_overrides
    from twicc.cli._drop_request.validation import validate_hidden_constraints
    from twicc.core.services.trust import project_is_untrusted
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    provider = resolved.provider

    # Provider still enabled. A disabled/unknown provider can't resolve a
    # change, so bail before touching its aliases or choices.
    errors: list[ValidationError] = list(validate_provider(provider, bootstrap))
    if errors:
        return errors
    pb = bootstrap.providers[provider]

    # Whether the session's project is untrusted (unknown trust counts as
    # untrusted): routes permission_mode alias resolution + the clamp, so an
    # update in an untrusted project lands inside the restricted set.
    untrusted = project_is_untrusted(resolved.project_id)

    # Silent no-op: drop ``--unset`` tokens for fields this provider doesn't
    # support (symmetric with the set-field drop done by ``resolve_overrides``).
    unset_fields = [f for f in unset_fields if f in supported_fields(pb)]

    # Per-provider alias resolution (``max`` → flagship, ``open`` →
    # most permissive non-interactive mode, ...) + ``context_max`` parse +
    # silent drop of unsupported set fields. After this, ``overrides`` holds
    # only concrete, provider-valid literals.
    overrides, alias_errors = resolve_overrides(overrides, pb, untrusted=untrusted)
    if alias_errors:
        return alias_errors

    # Resolve the final settings + updates dict.
    presets_for_provider = bootstrap.providers[provider].presets
    if preset is not None:
        try:
            settings = apply_preset_and_overrides(
                preset, presets_for_provider, overrides, unset=unset_fields, untrusted=untrusted,
            )
        except PresetError as e:
            return [ValidationError("--preset", "invalid_preset", str(e))]
        if untrusted:
            settings = clamp_untrusted_permission_mode(settings, pb)
        updates = settings._asdict()
        replace_all = True
    else:
        # Patch mode: only explicitly-touched fields end up in the payload.
        # ``settings`` is built solely so ``validate_settings`` can vet the
        # non-None values; it does not drive the DB write.
        settings = AgentSettings(**{f: overrides.get(f) for f in AgentSettings._fields})
        if untrusted:
            settings = clamp_untrusted_permission_mode(settings, pb)
        updates = {f: v for f, v in overrides.items() if v is not None}
        # Reflect a clamped permission_mode in the patch payload — only when the
        # user actually set it (an untouched field never enters ``updates``).
        if untrusted and "permission_mode" in updates:
            updates["permission_mode"] = settings.permission_mode
        for field in unset_fields:
            updates[field] = None
        replace_all = False

    # Validate each non-None setting against the provider's choices.
    value_errors = validate_settings(provider, settings, bootstrap)
    if value_errors:
        return value_errors

    # Hidden invariants: if the session is currently hidden, the EFFECTIVE
    # settings after this update must keep the non-interactive whitelist.
    # Skipped for a stale session: it can never run again.
    if resolved.hidden and not resolved.stale:
        merged_base = resolved.current_settings._asdict()
        merged_base.update(updates)
        merged_settings = AgentSettings(**merged_base)
        helpers_obj = get_provider_helpers(provider)
        effective = helpers_obj.resolve_agent_settings(merged_settings)
        effective = helpers_obj.enforce_agent_settings_consistency(effective)
        hidden_errors = validate_hidden_constraints(provider, effective, hidden=True)
        if hidden_errors:
            return hidden_errors

    return updates, replace_all
