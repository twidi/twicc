"""``twicc settings`` command group — read and write synced settings.

The generic backbone (``twicc settings`` / ``get`` / ``set`` / ``unset``)
operates **only** on the generic, directly-settable keys — the ones a user can
really change through this command family. Provider keys (``claudeCode*`` /
``codex*`` / ``defaultProvider`` / …) and notification keys live in their own
worlds and are read/written through ``twicc settings provider`` and
``twicc settings notifications``; the UI-only ``excluded`` keys (``waTheme``,
``waBrand``, ``defaultLayoutId``) are not exposed here at all.

So the bare ``twicc settings`` prints only the generic keys (offline read,
``_version`` stripped), and ``twicc settings get <KEY>`` prints a single
generic key as ``{key: value}`` — rejecting / redirecting any non-generic key
exactly like ``set`` / ``unset`` do (via :func:`_reject_key`).

``twicc settings set <KEY> <VALUE>`` and ``twicc settings unset <KEY>`` write
a scalar setting via the ``settings:update`` drop-request kind. The key
classification from :mod:`twicc.cli.settings._keys` gates which keys are
accepted.

All command bodies call ``django.setup()`` lazily (after ``--help``) because
``read_synced_settings`` / ``SYNCED_SETTINGS_DEFAULTS`` need the app registry.
The ``--help`` key listing comes from :func:`~twicc.cli.settings._keys.format_settable_keys_help`,
which is Django-free, so it stays cheap. Tests run under pytest-django which
calls ``django.setup()`` before collecting.
"""

from __future__ import annotations

import typer

from twicc.cli.settings._keys import format_settable_keys_help
from twicc.cli.settings.notifications import notifications_app
from twicc.cli.settings.provider import provider_app

settings_app = typer.Typer(
    name="settings",
    help="Read and write the generic synced settings (provider/notification keys have their own sub-commands).",
    invoke_without_command=True,
)

settings_app.add_typer(provider_app)
settings_app.add_typer(notifications_app)


# Shared `--help` body listing the settable generic keys (built once at import,
# Django-free). Appended to each backbone command's help.
_SETTABLE_KEYS_HELP = format_settable_keys_help()

_GET_HELP = (
    "Print the value of a single generic synced settings key as JSON.\n\n"
    "Only generic keys are accepted; provider and notification keys are "
    "redirected to their own commands (same gate as `set`/`unset`).\n\n"
    + _SETTABLE_KEYS_HELP
)
_SET_HELP = (
    "Set a generic synced setting to VALUE (type inferred from the key's default).\n\n"
    + _SETTABLE_KEYS_HELP
)
_UNSET_HELP = (
    "Reset a generic synced setting to its default value.\n\n"
    + _SETTABLE_KEYS_HELP
)


def build_settings_dump() -> dict:
    """Return the generic synced settings (``_version`` + non-generic keys stripped).

    Pure, unit-testable core for the read commands. Only the keys that classify
    as ``generic`` survive — provider, notification and UI-only ``excluded``
    keys are dropped (they are read through their own commands). Requires Django
    to be set up before calling (``classify_key`` / ``SYNCED_SETTINGS_DEFAULTS``
    need the app registry). Command bodies call ``django.setup()`` first;
    pytest-django handles it in the test suite.
    """
    from twicc.cli.settings._keys import classify_key
    from twicc.synced_settings import prepare_settings_for_client, read_synced_settings

    settings = read_synced_settings()
    clean, _version = prepare_settings_for_client(settings)
    return {key: value for key, value in clean.items() if classify_key(key) == "generic"}


@settings_app.callback(invoke_without_command=True)
def _settings_default(ctx: typer.Context) -> None:
    """Print the generic synced settings as JSON (default action)."""
    if ctx.invoked_subcommand is not None:
        return

    # Lazy Django setup — keeps --help fast (no app registry until needed).
    import django

    django.setup()

    from twicc.cli._output import emit_json

    emit_json(build_settings_dump())


@settings_app.command("get", help=_GET_HELP)
def settings_get(
    key: str = typer.Argument(help="Generic settings key to retrieve (e.g. autoUnpinOnArchive)."),
) -> None:
    """Print the value of a single generic synced settings key as JSON."""
    # Lazy Django setup — keeps --help fast.
    import django

    django.setup()

    from twicc.cli._output import emit_json

    # Same gate as set/unset: only generic keys are readable here; provider /
    # notification keys are redirected to their commands, excluded/unknown
    # rejected. ``build_settings_dump`` is already filtered to generic keys, so
    # the key is guaranteed present once ``_reject_key`` lets it through.
    _reject_key(key)
    emit_json({key: build_settings_dump()[key]})


def _reject_key(key: str) -> None:
    """Emit a validation error and exit 1 unless the key is a generic backbone key.

    Shared gate for ``set`` / ``unset`` (write) and ``get`` (read): all three
    restrict to the generic keys and route the rest identically. Checks the four
    rejection categories:

    - ``excluded``      — UI-only visual preference; not settable via CLI.
    - ``provider``      — use ``twicc settings provider …``.
    - ``notifications`` — use ``twicc settings notifications …``.
    - ``unknown``       — no such setting.

    Returns silently when the key classifies as ``generic`` (accepted).
    """
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.settings._keys import classify_key

    category = classify_key(key)
    if category == "excluded":
        emit_validation_errors([ValidationError(
            "KEY", "excluded",
            f"{key!r} is a UI-only visual preference; not settable via CLI.",
        )])
        raise typer.Exit(1)
    if category == "provider":
        emit_validation_errors([ValidationError(
            "KEY", "provider_key",
            f"{key!r} is a provider setting; use `twicc settings provider …`.",
        )])
        raise typer.Exit(1)
    if category == "notifications":
        emit_validation_errors([ValidationError(
            "KEY", "notifications_key",
            f"{key!r} is a notification setting; use `twicc settings notifications …`.",
        )])
        raise typer.Exit(1)
    if category == "unknown":
        emit_validation_errors([ValidationError(
            "KEY", "unknown_key",
            f"No such setting {key!r}.",
        )])
        raise typer.Exit(1)
    # category == "generic" → accepted; fall through silently.


@settings_app.command("set", help=_SET_HELP)
def settings_set(
    key: str = typer.Argument(help="Generic settings key to set (e.g. autoUnpinOnArchive)."),
    value: str = typer.Argument(help="New value (type-coerced to match the key's default type)."),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Set a generic synced setting to VALUE."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error
    from twicc.cli.settings._keys import ValueParseError, parse_value
    from twicc.cli.settings._output import emit_settings_final

    _reject_key(key)

    try:
        parsed = parse_value(key, value)
    except ValueParseError as exc:
        emit_validation_errors([ValidationError("VALUE", "invalid_value", str(exc))])
        raise typer.Exit(1)

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    payload = {"patch": {key: parsed}}
    sub = transport.submit(payload, kind="settings:update")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    code = emit_settings_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
    raise typer.Exit(code)


@settings_app.command("unset", help=_UNSET_HELP)
def settings_unset(
    key: str = typer.Argument(help="Generic settings key to reset to its default value."),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the update may still apply."
        ),
    ),
) -> None:
    """Reset a generic synced setting to its default value."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._output import emit_error
    from twicc.cli.settings._output import emit_settings_final
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    _reject_key(key)

    default = SYNCED_SETTINGS_DEFAULTS[key]

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    payload = {"patch": {key: default}}
    sub = transport.submit(payload, kind="settings:update")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    code = emit_settings_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
    raise typer.Exit(code)
