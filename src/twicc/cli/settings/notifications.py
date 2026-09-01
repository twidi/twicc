"""``twicc settings notifications`` command group — list / add / update / remove.

The bare ``twicc settings notifications`` prints the current notification
targets (offline read) together with the two global notification fields
``publicBaseUrl`` and ``notifyOnExtraUsageStart``.

``add <url>`` / ``update <id>`` / ``remove <id>`` read the current targets,
apply the matching :mod:`twicc.cli.settings._targets` helper, then drop a
``settings:update`` patch whose ``externalNotificationTargets`` value is the
**whole** new list (matching the WS whole-list-overwrite semantics).

The add / update commands expose paired boolean flags so that only the toggles
the user **explicitly passed** end up in the patch (``None`` = not passed →
let ``add_target``'s defaults apply):

  - ``--enabled / --disabled``        → ``enabled``
  - ``--user-turn / --no-user-turn``  → ``notifyUserTurn``
  - ``--pending / --no-pending``      → ``notifyPendingRequest``
  - ``--extra-usage / --no-extra-usage`` → ``notifyExtraUsageStart``
  - ``--peer / --no-peer``            → ``notifyPeer``
  - ``--away-only / --no-away-only``  → ``awayOnly``
  - ``--name TEXT``                   → ``name``

``update`` additionally accepts ``--url`` to change the target URL (which
resets ``tested`` to ``None`` via :func:`~twicc.cli.settings._targets.update_target`).

All command bodies call ``django.setup()`` lazily (after ``--help``).
``remove`` / ``update`` validate the id client-side via
:func:`~twicc.cli.settings._targets.find_target` and exit 1 with a clean
validation error if the id is not found.
"""

from __future__ import annotations


import typer


notifications_app = typer.Typer(
    name="notifications",
    help=(
        "Manage external Apprise notification targets — list them (bare form), or "
        "add / update / remove / test a target (sub-commands). Each target is an "
        "Apprise URL TwiCC pushes alerts to (agent waiting, pending request, extra "
        "usage, peer message or request)."
    ),
    invoke_without_command=True,
)


def build_notifications_list(settings: dict) -> dict:
    """Project the notifications slice from a synced-settings dict (pure).

    Returns the ``externalNotificationTargets`` list together with the two
    global notification fields ``publicBaseUrl`` and ``notifyOnExtraUsageStart``.
    Usable in tests without a running server.
    """
    return {
        "externalNotificationTargets": settings.get("externalNotificationTargets") or [],
        "publicBaseUrl": settings.get("publicBaseUrl", ""),
        "notifyOnExtraUsageStart": settings.get("notifyOnExtraUsageStart", True),
    }


@notifications_app.callback(invoke_without_command=True)
def _notifications_default(ctx: typer.Context) -> None:
    """Print current Apprise notification targets and global notification settings (offline read)."""
    if ctx.invoked_subcommand is not None:
        return

    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._output import emit_json
    from twicc.synced_settings import read_synced_settings

    emit_json(build_notifications_list(read_synced_settings()))


def _build_flags_dict(
    enabled: bool | None,
    user_turn: bool | None,
    pending: bool | None,
    extra_usage: bool | None,
    peer: bool | None,
    away_only: bool | None,
) -> dict:
    """Build the flags dict from explicitly-set toggles only (skip None values).

    Only keys explicitly passed by the user are included; None means the user
    did not pass the flag, so ``add_target``'s defaults should apply.
    """
    flags: dict = {}
    if enabled is not None:
        flags["enabled"] = enabled
    if user_turn is not None:
        flags["notifyUserTurn"] = user_turn
    if pending is not None:
        flags["notifyPendingRequest"] = pending
    if extra_usage is not None:
        flags["notifyExtraUsageStart"] = extra_usage
    if peer is not None:
        flags["notifyPeer"] = peer
    if away_only is not None:
        flags["awayOnly"] = away_only
    return flags


def _drop_and_poll(payload: dict, kind: str, timeout: int):
    """Drop a request file and poll for the status, returning the outcome.

    Returns a tuple ``(outcome, request_uuid)`` — the caller is responsible
    for calling :func:`~twicc.cli.settings._output.emit_settings_final` with
    the outcome and for raising ``typer.Exit`` with the returned code.

    Raises ``SystemExit(2)`` directly (via :func:`~twicc.cli._output.emit_error`)
    when the server is down, consistent with every other drop command.
    """
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._output import emit_error

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    sub = transport.submit(payload, kind=kind)
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    return outcome, sub.request_uuid


def _drop_patch(patch: dict, timeout: int) -> int:
    """Drop a ``settings:update`` request and return the exit code.

    Mirrors :func:`twicc.cli.settings.provider._drop_patch`.
    """
    from twicc.cli.settings._output import emit_settings_final

    outcome, request_uuid = _drop_and_poll({"patch": patch}, kind="settings:update", timeout=timeout)
    return emit_settings_final(outcome, request_uuid=request_uuid, timeout=timeout)


@notifications_app.command("add")
def notifications_add(
    url: str = typer.Argument(help="Apprise URL for the notification target."),
    name: str = typer.Option("", "--name", help="Optional human-readable name for this target."),
    enabled: bool | None = typer.Option(
        None, "--enabled/--disabled",
        help="Whether this target is active. Default: enabled.",
    ),
    user_turn: bool | None = typer.Option(
        None, "--user-turn/--no-user-turn",
        help="Send a notification when the agent is waiting for the user. Default: on.",
    ),
    pending: bool | None = typer.Option(
        None, "--pending/--no-pending",
        help="Send a notification when a permission request is pending. Default: on.",
    ),
    extra_usage: bool | None = typer.Option(
        None, "--extra-usage/--no-extra-usage",
        help="Send a notification when extra usage starts. Default: on.",
    ),
    peer: bool | None = typer.Option(
        None, "--peer/--no-peer",
        help="Send a notification when a peer message or pairing request arrives. Default: on.",
    ),
    away_only: bool | None = typer.Option(
        None, "--away-only/--no-away-only",
        help="Hold notifications while the user is present; send when they go away. Default: on.",
    ),
    test: bool = typer.Option(
        False, "--test",
        help=(
            "After a successful add, immediately send a test notification to the "
            "new target and emit the test result."
        ),
    ),
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply."
        ),
    ),
) -> None:
    """Add a new Apprise notification target (URL TwiCC pushes alerts to)."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli.settings._targets import add_target
    from twicc.cli.settings._output import emit_settings_final
    from twicc.synced_settings import read_synced_settings

    flags = _build_flags_dict(enabled, user_turn, pending, extra_usage, peer, away_only)
    settings = read_synced_settings()
    current = settings.get("externalNotificationTargets") or []
    new_list = add_target(current, url=url, name=name, flags=flags)

    # The new target is the last element; capture its id for --test chaining.
    new_target_id = new_list[-1]["id"]

    add_outcome, add_uuid = _drop_and_poll(
        {"patch": {"externalNotificationTargets": new_list}},
        kind="settings:update",
        timeout=timeout,
    )

    if not test or add_outcome.status != "updated":
        # Either --test was not requested, or the add failed — emit the add
        # result (which may be a rejection/failure/timeout) and exit.
        code = emit_settings_final(add_outcome, request_uuid=add_uuid, timeout=timeout)
        raise typer.Exit(code)

    # Add succeeded and --test was requested: run the notification test on the
    # newly created target.
    test_outcome, test_uuid = _drop_and_poll(
        {"id": new_target_id},
        kind="settings:notification_test",
        timeout=timeout,
    )
    code = emit_settings_final(test_outcome, request_uuid=test_uuid, timeout=timeout)
    raise typer.Exit(code)


@notifications_app.command("update")
def notifications_update(
    target_id: str = typer.Argument(
        metavar="ID",
        help="ID of the notification target to update.",
    ),
    url: str | None = typer.Option(
        None, "--url",
        help="New Apprise URL (resets the 'tested' flag to null).",
    ),
    name: str | None = typer.Option(
        None, "--name",
        help="New human-readable name.",
    ),
    enabled: bool | None = typer.Option(
        None, "--enabled/--disabled",
        help="Whether this target is active.",
    ),
    user_turn: bool | None = typer.Option(
        None, "--user-turn/--no-user-turn",
        help="Send a notification when the agent is waiting for the user.",
    ),
    pending: bool | None = typer.Option(
        None, "--pending/--no-pending",
        help="Send a notification when a permission request is pending.",
    ),
    extra_usage: bool | None = typer.Option(
        None, "--extra-usage/--no-extra-usage",
        help="Send a notification when extra usage starts.",
    ),
    peer: bool | None = typer.Option(
        None, "--peer/--no-peer",
        help="Send a notification when a peer message or pairing request arrives.",
    ),
    away_only: bool | None = typer.Option(
        None, "--away-only/--no-away-only",
        help="Hold notifications while the user is present; send when they go away.",
    ),
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply."
        ),
    ),
) -> None:
    """Update an existing Apprise notification target."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.settings._targets import TargetNotFound, find_target, update_target
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("externalNotificationTargets") or []

    try:
        find_target(current, target_id)
    except TargetNotFound:
        emit_validation_errors([ValidationError(
            "ID", "not_found",
            f"No notification target with id {target_id!r}.",
        )])
        raise typer.Exit(1)

    patch: dict = _build_flags_dict(enabled, user_turn, pending, extra_usage, peer, away_only)
    if url is not None:
        patch["url"] = url
    if name is not None:
        patch["name"] = name

    if not patch:
        emit_validation_errors([ValidationError(
            "<all>", "no_op",
            "Nothing to update — no flags were provided.",
        )])
        raise typer.Exit(1)

    new_list = update_target(current, target_id, patch)
    code = _drop_patch({"externalNotificationTargets": new_list}, timeout)
    raise typer.Exit(code)


@notifications_app.command("remove")
def notifications_remove(
    target_id: str = typer.Argument(
        metavar="ID",
        help="ID of the notification target to remove.",
    ),
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply."
        ),
    ),
) -> None:
    """Remove an Apprise notification target."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.settings._targets import TargetNotFound, find_target, remove_target
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("externalNotificationTargets") or []

    try:
        find_target(current, target_id)
    except TargetNotFound:
        emit_validation_errors([ValidationError(
            "ID", "not_found",
            f"No notification target with id {target_id!r}.",
        )])
        raise typer.Exit(1)

    new_list = remove_target(current, target_id)
    code = _drop_patch({"externalNotificationTargets": new_list}, timeout)
    raise typer.Exit(code)


@notifications_app.command("test")
def notifications_test(
    target_id: str = typer.Argument(
        metavar="ID",
        help="ID of the notification target to test.",
    ),
    timeout: int = typer.Option(
        30, "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the test may still complete."
        ),
    ),
) -> None:
    """Send a test Apprise notification to an existing target and report the result.

    The server sends an Apprise test message, persists the ``tested`` flag on
    the target, and returns ``tested`` and ``test_results`` in the response.
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.settings._output import emit_settings_final
    from twicc.cli.settings._targets import TargetNotFound, find_target
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    current = settings.get("externalNotificationTargets") or []

    try:
        find_target(current, target_id)
    except TargetNotFound:
        emit_validation_errors([ValidationError(
            "ID", "not_found",
            f"No notification target with id {target_id!r}.",
        )])
        raise typer.Exit(1)

    outcome, request_uuid = _drop_and_poll(
        {"id": target_id},
        kind="settings:notification_test",
        timeout=timeout,
    )
    code = emit_settings_final(outcome, request_uuid=request_uuid, timeout=timeout)
    raise typer.Exit(code)
