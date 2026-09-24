"""``twicc settings provider <provider>`` group — show + agent-defaults flags.

The group **callback** carries the per-provider agent-defaults patch: passing
any of the agent-defaults flags (``--model`` / ``--effort`` / ... / the usage-file
flags / ``--quota-wakeup-time``) builds a ``{provider}Default*`` (or other
``{provider}*``) synced-settings patch and drops a ``kind="settings:update"``
request, exactly like ``twicc settings set``.

With **no flags and no sub-command** the callback prints the provider's slice of
the synced settings (offline read): whether it is enabled / the global default /
orchestration-enabled, its agent defaults, the untrusted default, the
usage-file settings, and the quota warm-up time.

The provider sub-commands (``enable`` / ``disable`` / ``set-default`` /
``orchestration-*``) are added in a later task; this group only declares the
flat-args parsing + the callback.

Per-provider field sets differ: the common ``--model`` / ``--effort`` /
``--permission-mode`` / ``--context-max`` exist everywhere, but the Claude-only
booleans (``--thinking`` / ``--fast`` / ``--chrome``) are **rejected** with a
validation error for a provider that does not declare them (deliberate
divergence from the session commands' silent-drop). Aliases (``max`` / ``min`` /
...) are resolved per provider via :mod:`twicc.cli._drop_request.aliases`.

``--untrusted-permission-mode`` is a trap: it is NOT one of the closed-bundle
fields in ``AGENT_SETTINGS_FIELDS_MAPPING`` — its synced key is the separate
``UNTRUSTED_PERMISSION_MODE_SYNCED_KEY`` and it is validated against the
provider's untrusted-allowed set (mirroring the project-settings command).

All command bodies call ``django.setup()`` lazily (after ``--help``) because the
helpers registry / ``read_synced_settings`` need the app registry.
"""

from __future__ import annotations

import typer
from typer.core import TyperGroup


class _FlatBackcompatGroup(TyperGroup):
    """Group whose own options may appear anywhere when no sub-command is invoked.

    A Click group parses its own options only up to the first positional
    token — everything after is reserved for the sub-command. That would
    break the flat form ``settings provider <PROVIDER> --model X`` (options
    after the positional). Sub-command intent is strictly positional here
    (``<PROVIDER> <subcommand> ...``): when ``args[1]`` is not a known
    sub-command name, the whole argv is parsed like a plain command
    (interspersed options allowed) and the flat flags land on the callback
    wherever they appear. When ``args[1]`` IS a sub-command, the default group
    parsing applies and the sub-command's options flow to it untouched.
    """

    def parse_args(self, ctx, args):
        if not (len(args) >= 2 and args[1] in self.commands):
            ctx.allow_interspersed_args = True
        return super().parse_args(ctx, args)


provider_app = typer.Typer(
    name="provider",
    cls=_FlatBackcompatGroup,
    help=(
        "Show one provider's settings slice (bare form), set its per-provider "
        "agent-settings defaults (flat flags), or toggle it (sub-commands)."
    ),
    invoke_without_command=True,
)


# Public field token -> internal AgentSettings field name. The closed-bundle
# fields that map through ``AGENT_SETTINGS_FIELDS_MAPPING`` to a ``{provider}Default*``
# synced key. ``--untrusted-permission-mode`` is handled separately (its key is
# NOT in the mapping).
_FLAG_TO_FIELD: dict[str, str] = {
    "model": "selected_model",
    "effort": "effort",
    "permission_mode": "permission_mode",
    "context_max": "context_max",
    "thinking": "thinking_enabled",
    "fast": "fast_mode",
    "chrome": "claude_in_chrome",
}


def _usage_key(untrusted_synced_key: str, kind: str, suffix: str) -> str:
    """Build a ``{prefix}Usage{Read|Dump}File{Enabled|Path}`` synced key.

    The provider prefix is derived from the untrusted synced key (always
    ``{prefix}DefaultUntrustedPermissionMode``), so it matches the exact
    casing of the other ``{provider}*`` keys without a separate table.
    """
    prefix = untrusted_synced_key.split("Default", 1)[0]
    return f"{prefix}Usage{kind}File{suffix}"


def _quota_wakeup_key(untrusted_synced_key: str) -> str:
    """Build the ``{prefix}QuotaWakeupTime`` synced key (prefix from the untrusted key)."""
    prefix = untrusted_synced_key.split("Default", 1)[0]
    return f"{prefix}QuotaWakeupTime"


def _valid_quota_wakeup_time(value: str) -> bool:
    """Whether *value* is an acceptable quota warm-up time.

    Empty string (warm-up disabled) or a ``"HH:MM"`` 24-hour wall-clock time.
    Mirrors the canonical parse in ``twicc.quota_wakeup_task._parse_hhmm`` but
    keeps the empty/malformed distinction the CLI needs (empty = a valid
    "disable", malformed = a user error worth flagging).
    """
    if value == "":
        return True
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def build_provider_patch(provider: str, flags: dict) -> tuple[dict, list]:
    """Assemble the ``{provider}Default*`` synced patch from the callback flags.

    Pure (no server, no drop file) so it is unit-testable. ``flags`` is the dict
    of raw flag values (``None`` = flag not passed). Returns
    ``(patch, errors)``; ``errors`` is a list of
    :class:`~twicc.cli._drop_request.validation.ValidationError`.

    Rules:

    - Common + Claude-only closed-bundle fields map through
      ``AGENT_SETTINGS_FIELDS_MAPPING`` to ``{provider}Default*`` keys, with
      aliases resolved + ``context_max`` parsed via ``resolve_overrides``.
    - A flag the provider does not declare (e.g. ``--fast`` on codex) is a
      validation error (not a silent no-op).
    - ``--untrusted-permission-mode`` resolves via the untrusted alias path and
      must land in the provider's untrusted-allowed set; written under
      ``UNTRUSTED_PERMISSION_MODE_SYNCED_KEY``.
    - ``--usage-read-file PATH`` sets path + enabled True; ``--no-usage-read-file``
      sets enabled False. Same for the dump file.
    """
    from twicc.cli._drop_request.aliases import resolve_overrides
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.providers.helpers import get_provider_helpers

    errors: list[ValidationError] = []
    patch: dict = {}

    helpers = get_provider_helpers(provider)
    mapping = helpers.AGENT_SETTINGS_FIELDS_MAPPING
    untrusted_synced_key = helpers.UNTRUSTED_PERMISSION_MODE_SYNCED_KEY

    bootstrap = load_local_bootstrap()
    pb = bootstrap.providers[provider]

    # --- closed-bundle fields (resolve aliases via the provider's pb) ---------
    overrides: dict[str, object | None] = {}
    for flag_token, field in _FLAG_TO_FIELD.items():
        value = flags.get(flag_token)
        if value is None:
            continue
        if field not in mapping:
            errors.append(ValidationError(
                f"--{flag_token.replace('_', '-')}", "unsupported_field",
                f"--{flag_token.replace('_', '-')} is not supported by {provider}. "
                f"Supported agent-defaults flags: "
                f"{sorted(_flag_for_field(f) for f in mapping)}.",
            ))
            continue
        overrides[field] = value

    if overrides:
        resolved, resolve_errors = resolve_overrides(overrides, pb)
        errors.extend(resolve_errors)
        for field, value in resolved.items():
            if value is None:
                continue
            patch[mapping[field]] = value

    # --- untrusted permission mode (separate key, separate validation) -------
    untrusted = flags.get("untrusted_permission_mode")
    if untrusted is not None:
        if untrusted_synced_key is None or not pb.untrusted_permission_modes:
            errors.append(ValidationError(
                "--untrusted-permission-mode", "unsupported_field",
                f"--untrusted-permission-mode is not supported by {provider}.",
            ))
        else:
            from twicc.cli._drop_request.aliases import resolve_alias
            resolved_untrusted = resolve_alias(
                "permission_mode", untrusted, pb, untrusted=True,
            )
            if resolved_untrusted not in pb.untrusted_permission_modes:
                expected = sorted(pb.untrusted_permission_modes)
                aliases = sorted(((pb.agent_settings_aliases or {})
                                  .get("permission_mode_if_untrusted") or {}).keys())
                hint = f" Or an alias: {aliases}." if aliases else ""
                errors.append(ValidationError(
                    "--untrusted-permission-mode", "invalid_choice",
                    f"invalid value {untrusted!r} for {provider}. "
                    f"Expected: {expected}." + hint,
                ))
            else:
                patch[untrusted_synced_key] = resolved_untrusted

    # --- usage files (path + enabled flag pairs) -----------------------------
    if untrusted_synced_key is not None:
        read_path = flags.get("usage_read_file")
        if read_path is not None:
            patch[_usage_key(untrusted_synced_key, "Read", "Path")] = read_path
            patch[_usage_key(untrusted_synced_key, "Read", "Enabled")] = True
        elif flags.get("no_usage_read_file"):
            patch[_usage_key(untrusted_synced_key, "Read", "Enabled")] = False

        dump_path = flags.get("usage_dump_file")
        if dump_path is not None:
            patch[_usage_key(untrusted_synced_key, "Dump", "Path")] = dump_path
            patch[_usage_key(untrusted_synced_key, "Dump", "Enabled")] = True
        elif flags.get("no_usage_dump_file"):
            patch[_usage_key(untrusted_synced_key, "Dump", "Enabled")] = False

    # --- quota warm-up time ("HH:MM" or "" to disable) -----------------------
    quota = flags.get("quota_wakeup_time")
    if quota is not None:
        quota_key = _quota_wakeup_key(untrusted_synced_key) if untrusted_synced_key else None
        if quota_key is None or quota_key not in helpers.SYNCED_SETTINGS_DEFAULTS:
            errors.append(ValidationError(
                "--quota-wakeup-time", "unsupported_field",
                f"--quota-wakeup-time is not supported by {provider}.",
            ))
        else:
            quota = quota.strip()
            if not _valid_quota_wakeup_time(quota):
                errors.append(ValidationError(
                    "--quota-wakeup-time", "invalid_value",
                    f"--quota-wakeup-time expects 'HH:MM' (24-hour) or '' to "
                    f"disable, got {quota!r}.",
                ))
            else:
                patch[quota_key] = quota

    return patch, errors


def _flag_for_field(field: str) -> str:
    """Reverse map an internal field to its public flag name (for messages)."""
    for token, mapped in _FLAG_TO_FIELD.items():
        if mapped == field:
            return f"--{token.replace('_', '-')}"
    return f"--{field.replace('_', '-')}"


def build_provider_show(provider: str, settings: dict) -> dict:
    """Project the provider's slice from a synced-settings dict (pure).

    Reads only ``settings`` + the provider's helpers (mapping / usage / untrusted
    keys), so it is unit-testable against a seeded dict. Falls back to the
    provider's defaults for any key absent from ``settings``.
    """
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers(provider)
    mapping = helpers.AGENT_SETTINGS_FIELDS_MAPPING
    untrusted_synced_key = helpers.UNTRUSTED_PERMISSION_MODE_SYNCED_KEY
    defaults = helpers.SYNCED_SETTINGS_DEFAULTS

    def _get(key: str):
        return settings.get(key, defaults.get(key))

    disabled = settings.get("disabledProviders") or []
    orchestration_disabled = settings.get("orchestrationDisabledProviders") or []

    agent_defaults = {field: _get(key) for field, key in mapping.items()}

    out: dict = {
        "provider": provider,
        "enabled": provider not in disabled,
        "is_default": settings.get("defaultProvider") == provider,
        "orchestration_enabled": provider not in orchestration_disabled,
        "agent_defaults": agent_defaults,
    }
    if untrusted_synced_key is not None:
        out["untrusted_permission_mode_default"] = _get(untrusted_synced_key)
        out["usage_read_file"] = {
            "enabled": _get(_usage_key(untrusted_synced_key, "Read", "Enabled")),
            "path": _get(_usage_key(untrusted_synced_key, "Read", "Path")),
        }
        out["usage_dump_file"] = {
            "enabled": _get(_usage_key(untrusted_synced_key, "Dump", "Enabled")),
            "path": _get(_usage_key(untrusted_synced_key, "Dump", "Path")),
        }
        quota_key = _quota_wakeup_key(untrusted_synced_key)
        if quota_key in defaults:
            out["quota_wakeup_time"] = _get(quota_key)
    return out


@provider_app.callback(invoke_without_command=True)
def provider_main(
    ctx: typer.Context,
    provider: str = typer.Argument(
        ...,
        metavar="PROVIDER",
        help="Provider to inspect or configure (e.g. 'claude_code', 'codex').",
    ),
    model: str | None = typer.Option(
        None, "--model",
        help=(
            "Default model for NEW sessions of this provider. Accepts aliases "
            "(e.g. 'max', 'min') resolved per provider."
        ),
    ),
    effort: str | None = typer.Option(
        None, "--effort",
        help=(
            "Default reasoning effort for NEW sessions. Claude Code: 'low', "
            "'medium', 'high', 'xhigh', 'max'. Codex: 'low', 'medium', 'high', "
            "'xhigh'. Aliases: 'min', 'max'."
        ),
    ),
    permission_mode: str | None = typer.Option(
        None, "--permission-mode",
        help=(
            "Default tool-permission policy for NEW sessions in a TRUSTED "
            "project. Aliases resolved per provider ('min'/'safe', 'max', ...)."
        ),
    ),
    context_max: str | None = typer.Option(
        None, "--context-max",
        help=(
            "Default max context window for NEW sessions (token count, e.g. "
            "'200k', '1m', or aliases 'min'/'max')."
        ),
    ),
    thinking: bool | None = typer.Option(
        None, "--thinking/--no-thinking",
        help="Claude Code only. Default for extended thinking.",
    ),
    fast: bool | None = typer.Option(
        None, "--fast/--no-fast",
        help="Default for fast mode on supported Claude Code or Codex models.",
    ),
    chrome: bool | None = typer.Option(
        None, "--chrome/--no-chrome",
        help="Claude Code only. Default for the Chrome MCP integration.",
    ),
    untrusted_permission_mode: str | None = typer.Option(
        None, "--untrusted-permission-mode",
        help=(
            "Default tool-permission policy for NEW sessions in an UNTRUSTED "
            "project. Restricted to the provider's untrusted-allowed modes; "
            "aliases 'min'/'safe', 'max' resolve within that set."
        ),
    ),
    usage_read_file: str | None = typer.Option(
        None, "--usage-read-file",
        help=(
            "Path to a JSON file TwiCC reads this provider's usage quota from "
            "(also enables it). Use --no-usage-read-file to disable."
        ),
    ),
    no_usage_read_file: bool = typer.Option(
        False, "--no-usage-read-file",
        help="Disable the usage read-file for this provider.",
    ),
    usage_dump_file: str | None = typer.Option(
        None, "--usage-dump-file",
        help=(
            "Path to a JSON file TwiCC dumps this provider's usage quota to "
            "(also enables it). Use --no-usage-dump-file to disable."
        ),
    ),
    no_usage_dump_file: bool = typer.Option(
        False, "--no-usage-dump-file",
        help="Disable the usage dump-file for this provider.",
    ),
    quota_wakeup_time: str | None = typer.Option(
        None, "--quota-wakeup-time",
        help=(
            "Daily quota warm-up time as 'HH:MM' (24-hour, server local clock) — "
            "TwiCC opens a fresh usage window at that time. Pass '' to disable."
        ),
    ),
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Show a provider's settings slice, or set its agent-settings defaults."""
    # Stash the resolved provider for the sub-commands (added in a later task).
    ctx.obj = provider

    # Lazy Django setup — keeps --help fast (no app registry until needed).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error

    # Validate the provider is a real, registered provider.
    bootstrap = load_local_bootstrap()
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        emit_validation_errors([ValidationError(
            "PROVIDER", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        )])
        raise typer.Exit(1)

    # A sub-command is in play (added in a later task): the callback only stashes
    # the provider and the flat flags are reserved for the bare form.
    if ctx.invoked_subcommand is not None:
        return

    flags = {
        "model": model,
        "effort": effort,
        "permission_mode": permission_mode,
        "context_max": context_max,
        "thinking": thinking,
        "fast": fast,
        "chrome": chrome,
        "untrusted_permission_mode": untrusted_permission_mode,
        "usage_read_file": usage_read_file,
        "no_usage_read_file": no_usage_read_file,
        "usage_dump_file": usage_dump_file,
        "no_usage_dump_file": no_usage_dump_file,
        "quota_wakeup_time": quota_wakeup_time,
    }
    any_flag = any(
        v not in (None, False) for v in flags.values()
    )

    # Bare form (no flags) → show the provider's slice (offline read).
    if not any_flag:
        from twicc.cli._output import emit_json
        from twicc.synced_settings import read_synced_settings

        emit_json(build_provider_show(provider, read_synced_settings()))
        raise typer.Exit(0)

    # Flags present → assemble the patch and drop a settings:update request.
    patch, errors = build_provider_patch(provider, flags)
    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)
    if not patch:
        emit_validation_errors([ValidationError(
            "<all>", "no_op",
            "Nothing to update for this provider — every flag was a no-op.",
        )])
        raise typer.Exit(1)

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli.settings._output import emit_settings_final

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    sub = transport.submit({"patch": patch}, kind="settings:update")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    code = emit_settings_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
    raise typer.Exit(code)


# ---------------------------------------------------------------------------
# Pure helpers for list-valued provider keys
# ---------------------------------------------------------------------------


def compute_disabled(current_list: list, provider: str, enable: bool) -> list:
    """Return a new ``disabledProviders`` list after enabling or disabling *provider*.

    Idempotent: enabling a provider not in the list is a no-op (list unchanged);
    disabling a provider already in the list is a no-op.  Order is preserved;
    no duplicates are introduced.

    Parameters
    ----------
    current_list:
        The current ``disabledProviders`` value (may be ``None`` or empty).
    provider:
        The provider identifier to add or remove.
    enable:
        ``True`` → remove *provider* from the list (enable it);
        ``False`` → add *provider* to the list (disable it).
    """
    lst = list(current_list) if current_list else []
    if enable:
        return [p for p in lst if p != provider]
    else:
        if provider not in lst:
            lst.append(provider)
        return lst


def compute_orchestration_disabled(
    current_list: list, provider: str, enable: bool
) -> list:
    """Return a new ``orchestrationDisabledProviders`` list after the operation.

    Same semantics as :func:`compute_disabled` — enable removes, disable adds,
    idempotent, order preserved, no duplicates.
    """
    return compute_disabled(current_list, provider, enable)


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def _drop_patch(patch: dict, timeout: int) -> int:
    """Drop a ``settings:update`` request and return the exit code.

    Shared by all five sub-commands to avoid repeating the check/drop/poll loop.
    """
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._output import emit_error
    from twicc.cli.settings._output import emit_settings_final

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    sub = transport.submit({"patch": patch}, kind="settings:update")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    return emit_settings_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)


def _setup_and_validate(ctx: typer.Context):
    """Django setup + validate the stashed provider; return provider string.

    Called at the top of every sub-command body to avoid repetition.
    Exits with code 1 if the provider is unknown.
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    provider = ctx.obj
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError

    bootstrap = load_local_bootstrap()
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        emit_validation_errors([ValidationError(
            "PROVIDER", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        )])
        raise typer.Exit(1)

    return provider


@provider_app.command("enable")
def provider_enable(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Enable the provider (remove it from disabledProviders)."""
    provider = _setup_and_validate(ctx)

    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("disabledProviders") or []
    new_list = compute_disabled(current, provider, enable=True)
    code = _drop_patch({"disabledProviders": new_list}, timeout)
    raise typer.Exit(code)


@provider_app.command("disable")
def provider_disable(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Disable the provider (add it to disabledProviders).

    The server may reject the disable if there are live agents running under
    this provider; any corrections are printed via emit_settings_final.
    """
    provider = _setup_and_validate(ctx)

    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("disabledProviders") or []
    new_list = compute_disabled(current, provider, enable=False)
    code = _drop_patch({"disabledProviders": new_list}, timeout)
    raise typer.Exit(code)


@provider_app.command("set-default")
def provider_set_default(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Set the provider as the global default (defaultProvider).

    Rejected client-side if the provider is currently in disabledProviders —
    a disabled provider cannot be the default.
    """
    provider = _setup_and_validate(ctx)

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    disabled = settings.get("disabledProviders") or []
    if provider in disabled:
        emit_validation_errors([ValidationError(
            "PROVIDER", "provider_disabled",
            f"Cannot set {provider!r} as the default: it is currently disabled. "
            f"Enable it first with `twicc settings provider {provider} enable`.",
        )])
        raise typer.Exit(1)

    code = _drop_patch({"defaultProvider": provider}, timeout)
    raise typer.Exit(code)


@provider_app.command("orchestration-enable")
def provider_orchestration_enable(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Allow the provider to be used in orchestration (remove from orchestrationDisabledProviders)."""
    provider = _setup_and_validate(ctx)

    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("orchestrationDisabledProviders") or []
    new_list = compute_orchestration_disabled(current, provider, enable=True)
    code = _drop_patch({"orchestrationDisabledProviders": new_list}, timeout)
    raise typer.Exit(code)


@provider_app.command("orchestration-disable")
def provider_orchestration_disable(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Exclude the provider from orchestration (add to orchestrationDisabledProviders)."""
    provider = _setup_and_validate(ctx)

    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("orchestrationDisabledProviders") or []
    new_list = compute_orchestration_disabled(current, provider, enable=False)
    code = _drop_patch({"orchestrationDisabledProviders": new_list}, timeout)
    raise typer.Exit(code)
