"""``twicc update-project <PROJECT> settings`` sub-command.

Drops a ``kind="project:update_settings"`` payload in
``<data_dir>/drop-requests/`` so the live TwiCC server applies the change via
:func:`twicc.core.services.project_mutation.update_project_agent_settings_from_payload`.

Edits ONE provider's bundle inside the project's ``default_agent_settings``
(the per-project defaults that seed NEW sessions — they never touch running
or already-created sessions). Patch semantics, mirroring the web edit dialog:

- ``--provider`` is required: a project carries one bundle per provider.
- A per-field flag sets that field in the bundle.
- ``--unset <field>`` removes it (back to inherit: parent projects' bundles,
  then the global synced default).
- Untouched fields — and the other providers' bundles — are left alone.
- There is no ``--preset`` (use individual flags; to wipe a bundle, ``--unset``
  every field).

Two deltas vs ``update-session settings``:

- ``--permission-mode-if-untrusted`` exists here: the default-shaping field
  seeded as a new session's ``permission_mode`` when the project resolves
  untrusted. Its values are restricted to the provider's untrusted-allowed
  set. ``--permission-mode`` itself is NOT clamped — it is the trusted-case
  default, so ``bypassPermissions`` / ``yolo`` are legitimate values.
- A **disabled** provider is accepted (matching the web dialog): a project may
  keep defaults for a temporarily-deactivated provider.

Aliases (``max`` / ``min`` / ...) are resolved per provider for every
field, exactly like the session settings commands. Fields the provider does
not support are dropped silently (no-op), also like the session commands.
"""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_context import load_help_context
from twicc.cli._drop_request.help_strings import (
    EFFORT_ALIAS_HINT,
    PERMISSION_ALIAS_HINT,
    context_max_help,
    model_help,
)

# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Same trick as
# ``create_session.command`` — kept Django-free, ~30 ms cold, degrades to
# "no info" on missing / malformed files.
_HELP_CTX = load_help_context()


# Public token (typed after ``--unset``) -> bundle field name. Mirrors the
# session commands' ``UNSET_TOKEN_TO_FIELD`` minus the hidden
# ``question-widget`` (never stored in project defaults) plus the
# project-only ``permission-mode-if-untrusted``.
UNSET_TOKEN_TO_FIELD: dict[str, str] = {
    "model": "selected_model",
    "effort": "effort",
    "permission-mode": "permission_mode",
    "permission-mode-if-untrusted": "permission_mode_if_untrusted",
    "thinking": "thinking_enabled",
    "claude-in-chrome": "claude_in_chrome",
    "fast-mode": "fast_mode",
    "context-max": "context_max",
}

# Bundle fields that take aliases at the string level (the session
# commands' ordered fields + the project-only untrusted variant, whose alias
# table / native set live on the provider bootstrap too).
_ALIASABLE_FIELDS = (
    "selected_model", "effort", "permission_mode", "permission_mode_if_untrusted",
)


def _unset_help() -> str:
    tokens = sorted(UNSET_TOKEN_TO_FIELD.keys())
    return (
        "Remove a field from the bundle (back to inherit: parent projects, "
        "then the global default). Repeatable: pass once per field. Accepted "
        "tokens: " + ", ".join(f"'{t}'" for t in tokens) + ". Conflicts with "
        "the matching per-field flag (e.g. --unset model and --model X cannot "
        "be combined)."
    )


def update_project_settings_cmd(
    ctx: typer.Context,
    provider: str = typer.Option(
        ...,
        "--provider",
        help=(
            "Provider whose defaults bundle to edit (required — a project "
            "carries one bundle per provider). E.g. 'claude_code', 'codex'. "
            "A disabled provider is accepted: a project may keep defaults "
            "for a temporarily-deactivated provider."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=model_help(_HELP_CTX),
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help=(
            "Reasoning effort. Claude Code: 'low', 'medium', 'high', 'xhigh', 'max' "
            "(xhigh/max require a capable model; otherwise silently demoted). "
            "Codex: 'low', 'medium', 'high', 'xhigh'."
            + EFFORT_ALIAS_HINT
        ),
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help=(
            "Default tool permission policy for new sessions when the project "
            "is TRUSTED. Claude Code: 'default', 'acceptEdits', 'plan', "
            "'dontAsk', 'bypassPermissions'. Codex: 'read_only', 'strict', "
            "'auto', 'autonomous', 'yolo'."
            + PERMISSION_ALIAS_HINT
        ),
    ),
    permission_mode_if_untrusted: str | None = typer.Option(
        None,
        "--permission-mode-if-untrusted",
        help=(
            "Default tool permission policy for new sessions when the project "
            "resolves UNTRUSTED (or unknown trust). Restricted to the "
            "provider's untrusted-allowed modes — Claude Code: 'default', "
            "'auto', 'acceptEdits', 'plan', 'dontAsk'; Codex: 'read_only', "
            "'strict', 'auto', 'autonomous'. Aliases (resolved per provider): "
            "'min'/'safe' (most locked), 'max' (most permissive "
            "untrusted-allowed mode)."
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Default for extended thinking. Omit to leave "
            "the bundle's current value unchanged."
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Default for the Chrome MCP integration. Omit "
            "to leave the bundle's current value unchanged."
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Default for fast mode on supported Claude Code or Codex models "
            "(higher throughput and credit usage). Omit to "
            "leave the bundle's current value unchanged."
        ),
    ),
    context_max: str | None = typer.Option(
        None,
        "--context-max",
        help=context_max_help(_HELP_CTX),
    ),
    unset: list[str] = typer.Option(
        [],
        "--unset",
        help=_unset_help(),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Edit one provider's agent-settings defaults bundle on a project.

    At least one per-field flag or --unset is required. If nothing is
    passed, the command fails with ``no_op``.
    """
    project_id: str = ctx.obj

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.aliases import resolve_alias, supported_fields
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.help_strings import parse_context_max
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.validation import (
        ValidationError,
        validate_no_set_unset_conflict,
        validate_settings,
    )
    from twicc.core.models import Project
    from twicc.providers.helpers import AgentSettings
    from twicc.cli._output import emit_error, emit_json

    # Server-up check first (exit 2 wins over any validation error), matching
    # the other drop-file commands.
    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    errors: list[ValidationError] = []

    # --unset tokens -> bundle field names.
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
        "permission_mode_if_untrusted": permission_mode_if_untrusted,
        "thinking_enabled": thinking,
        "claude_in_chrome": claude_in_chrome,
        "fast_mode": fast_mode,
        "context_max": context_max,  # raw string, parsed per-provider below
    }

    # Contradictory ``--<field> VALUE`` + ``--unset <field>``.
    errors.extend(validate_no_set_unset_conflict(overrides, unset_fields))

    # No-op: nothing touched at all.
    if all(v is None for v in overrides.values()) and not unset:
        errors.append(ValidationError(
            "<all>", "no_op",
            "Nothing to update; pass at least one per-field flag or "
            "--unset <field>.",
        ))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Provider must be registered. Deliberately NOT gated on enablement
    # (unlike the session commands): the web dialog lets a project keep
    # defaults for disabled providers, the CLI matches.
    bootstrap = load_local_bootstrap()
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        emit_validation_errors([ValidationError(
            "--provider", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        )])
        raise typer.Exit(1)
    pb = bootstrap.providers[provider]

    # Project must exist (the server re-checks under the lock).
    if not Project.objects.filter(id=project_id).exists():
        emit_validation_errors([ValidationError(
            "PROJECT", "project_not_found", f"Project {project_id!r} not found.")])
        raise typer.Exit(1)

    # Fields this provider's project bundle may hold: its supported settings
    # fields plus the untrusted permission variant (when the provider has an
    # untrusted-allowed set), minus nothing else — ``question_widget`` (hidden)
    # has no flag here so it can never appear.
    bundle_fields = set(supported_fields(pb))
    if pb.untrusted_permission_modes:
        bundle_fields.add("permission_mode_if_untrusted")

    # Per-provider alias resolution + ``context_max`` parse + silent
    # drop of fields the provider doesn't support (set or ``--unset``) —
    # mirroring the session commands' ``resolve_overrides`` semantics, with
    # ``permission_mode_if_untrusted`` as an extra aliasable field.
    unset_fields = [f for f in unset_fields if f in bundle_fields]
    resolved: dict[str, object] = {}
    for field, value in overrides.items():
        if value is None:
            continue
        if field not in bundle_fields:
            continue  # silent no-op for an unsupported field
        if field in _ALIASABLE_FIELDS:
            resolved[field] = resolve_alias(field, value, pb)
        elif field == "context_max":
            ctx_aliases = (pb.agent_settings_aliases or {}).get("context_max", {})
            raw = ctx_aliases.get(value, value)
            try:
                resolved[field] = parse_context_max(raw)
            except ValueError as e:
                emit_validation_errors([ValidationError(
                    "--context-max", "invalid_format", str(e))])
                raise typer.Exit(1)
        else:
            resolved[field] = value

    if not resolved and not unset_fields:
        # Every field touched was a no-op for this provider (e.g. --thinking
        # with --provider codex): nothing to write.
        emit_json({"status": "noop", "project_id": project_id})
        raise typer.Exit(0)

    # Value validation. The closed-bundle fields go through the shared
    # validator; ``permission_mode_if_untrusted`` is checked against the
    # provider's untrusted-allowed set (the same rule the server re-enforces).
    pmiu = resolved.get("permission_mode_if_untrusted")
    closed = {f: v for f, v in resolved.items() if f in AgentSettings._fields}
    settings = AgentSettings(**{f: closed.get(f) for f in AgentSettings._fields})
    errors = list(validate_settings(provider, settings, bootstrap))
    if pmiu is not None and pmiu not in pb.untrusted_permission_modes:
        expected = sorted(pb.untrusted_permission_modes)
        aliases = sorted(((pb.agent_settings_aliases or {})
                          .get("permission_mode_if_untrusted") or {}).keys())
        hint = f" Or an alias: {aliases}." if aliases else ""
        errors.append(ValidationError(
            "--permission-mode-if-untrusted", "invalid_choice",
            f"invalid value {pmiu!r} for {provider}. Expected: {expected}." + hint,
        ))
    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    updates: dict[str, object | None] = dict(resolved)
    for field in unset_fields:
        updates[field] = None

    payload = {
        "project_id": project_id,
        "provider": provider,
        "updates": updates,
    }

    sub = transport.submit(payload, kind="project:update_settings")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
