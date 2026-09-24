"""``twicc update-workspace <ID> [OPTIONS]`` command.

Drops a ``kind="workspace:update"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server applies the patch via
:func:`twicc.core.services.workspace_mutation.update_workspace_from_payload`
— validation + atomic read-modify-write under ``_workspaces_lock`` +
``workspaces_updated`` broadcast.

Flags are combinable: every operation specified is applied in the same
atomic write. At least one effective patch must be supplied (no flag at
all → ``no_op`` error). ``--color`` and ``--unset-color`` are mutually
exclusive; so are ``--archive`` and ``--unarchive``. ``--unset-browser-url``
(clear ALL saved URLs) does not combine with the other browser-URL flags,
and ``--browser-url`` is a shorthand for ``--add-browser-url`` +
``--set-default``.

Add/remove on projects and patterns are idempotent (silently skip
already-present additions and already-absent removals), matching the
auto-add helper's semantics.
"""

from __future__ import annotations

import typer


def update_workspace_cmd(
    workspace_id: str = typer.Argument(
        ...,
        metavar="WORKSPACE_ID",
        help="Id of the existing workspace to update.",
    ),
    new_name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Rename the workspace. Trimmed; must be non-empty, ≤ 20 "
            "characters, unique (case-insensitive). The workspace's id "
            "stays immutable — only the display name changes."
        ),
    ),
    color: str | None = typer.Option(
        None,
        "--color",
        help=(
            "Set a CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). "
            "Mutually exclusive with `--unset-color`."
        ),
    ),
    unset_color: bool = typer.Option(
        False,
        "--unset-color",
        help=(
            "Clear the workspace's color. Mutually exclusive with `--color`."
        ),
    ),
    add_projects: list[str] = typer.Option(
        [],
        "--add-project",
        help=(
            "Add a project to the workspace (idempotent). Repeat for "
            "multiple projects. Each value is a project ID (with or without "
            "leading dash) or a directory path (absolute or relative); paths "
            "are resolved via realpath and converted to their canonical id. "
            "The resolved project must already exist in TwiCC."
        ),
    ),
    remove_projects: list[str] = typer.Option(
        [],
        "--remove-project",
        help=(
            "Remove a project from the workspace (idempotent — silently "
            "skips projects already absent). Repeat for multiple projects. "
            "Each value is a project ID (with or without leading dash) or "
            "a directory path (absolute or relative); paths are resolved "
            "via realpath and converted to their canonical id."
        ),
    ),
    add_patterns: list[str] = typer.Option(
        [],
        "--add-pattern",
        help=(
            "Add an auto-add directory pattern (idempotent). Repeat for "
            "multiple patterns."
        ),
    ),
    remove_patterns: list[str] = typer.Option(
        [],
        "--remove-pattern",
        help=(
            "Remove an auto-add directory pattern (idempotent). Repeat for "
            "multiple patterns."
        ),
    ),
    add_browser_url: str | None = typer.Option(
        None,
        "--add-browser-url",
        help=(
            "Add a URL to the workspace's saved Browser-pane URLs (http(s) "
            "only; a project's own saved URLs take precedence). Idempotent "
            "on an already-saved URL. The first saved URL becomes the "
            "default (Home target); combine with `--set-default` to flag "
            "this one, `--browser-url-label` to name it."
        ),
    ),
    browser_url_label: str | None = typer.Option(
        None,
        "--browser-url-label",
        help=(
            "Optional label for the URL passed to `--add-browser-url` / "
            "`--browser-url` (shown in the Browser pane's menus)."
        ),
    ),
    set_default: bool = typer.Option(
        False,
        "--set-default",
        help=(
            "Flag the URL passed to `--add-browser-url` as the workspace's "
            "default (Home target)."
        ),
    ),
    remove_browser_url: str | None = typer.Option(
        None,
        "--remove-browser-url",
        help=(
            "Remove a URL from the workspace's saved Browser-pane URLs "
            "(idempotent — an absent URL is a no-op)."
        ),
    ),
    set_default_browser_url: str | None = typer.Option(
        None,
        "--set-default-browser-url",
        help=(
            "Flag an already-saved URL as the workspace's default (Home "
            "target). Fails if the URL is not in the saved list."
        ),
    ),
    browser_url: str | None = typer.Option(
        None,
        "--browser-url",
        help=(
            "Shorthand for `--add-browser-url URL --set-default`: save the "
            "URL (if not already saved) and make it the workspace's default."
        ),
    ),
    unset_browser_url: bool = typer.Option(
        False,
        "--unset-browser-url",
        help=(
            "Clear ALL the workspace's saved Browser-pane URLs. Mutually "
            "exclusive with the other browser-URL flags."
        ),
    ),
    archive: bool = typer.Option(
        False,
        "--archive",
        help=(
            "Mark the workspace as archived. Mutually exclusive with "
            "`--unarchive`."
        ),
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help=(
            "Mark the workspace as not archived. Mutually exclusive with "
            "`--archive`."
        ),
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
    """Update an existing workspace."""
    from twicc.cli._drop_request.project import derive_project_id

    # Accept paths or ids for each --add-project / --remove-project — derive
    # the canonical project_id once, before any DB lookup or validation
    # downstream. The derivation is pure (no DB); existence is enforced
    # later for additions only (removals stay idempotent on missing ids).
    add_projects = [derive_project_id(value)[0] for value in add_projects]
    remove_projects = [derive_project_id(value)[0] for value in remove_projects]

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error
    from twicc.core.models import Project
    from twicc.workspaces import (
        MAX_BROWSER_URL_LABEL_LENGTH,
        read_workspaces,
        validate_browser_url,
        validate_color,
        validate_pattern,
        validate_workspace_name,
    )

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Mutually-exclusive flag checks first — they don't depend on disk state.
    errors: list[ValidationError] = []
    if color is not None and unset_color:
        errors.append(ValidationError("--color", "conflicting_flags",
                                       "--color and --unset-color cannot be used together."))
    if browser_url is not None and add_browser_url is not None:
        errors.append(ValidationError("--browser-url", "conflicting_flags",
                                       "--browser-url and --add-browser-url cannot be used together "
                                       "(the former is a shorthand for the latter plus --set-default)."))
    if unset_browser_url and any((
        add_browser_url is not None, browser_url_label is not None, set_default,
        remove_browser_url is not None, set_default_browser_url is not None,
        browser_url is not None,
    )):
        errors.append(ValidationError("--unset-browser-url", "conflicting_flags",
                                       "--unset-browser-url cannot be combined with the other "
                                       "browser-URL flags."))
    if (browser_url_label is not None or set_default) and (
        add_browser_url is None and browser_url is None
    ):
        errors.append(ValidationError("--browser-url-label" if browser_url_label is not None else "--set-default",
                                       "missing_flag",
                                       "--browser-url-label and --set-default only apply to "
                                       "--add-browser-url / --browser-url."))
    if archive and unarchive:
        errors.append(ValidationError("--archive", "conflicting_flags",
                                       "--archive and --unarchive cannot be used together."))

    # No-op check: at least one effective field must be touched.
    has_patch = (
        new_name is not None
        or color is not None
        or unset_color
        or bool(add_projects)
        or bool(remove_projects)
        or bool(add_patterns)
        or bool(remove_patterns)
        or add_browser_url is not None
        or remove_browser_url is not None
        or set_default_browser_url is not None
        or browser_url is not None
        or unset_browser_url
        or archive
        or unarchive
    )
    if not has_patch:
        errors.append(ValidationError("update-workspace", "no_op",
                                       "Nothing to update — pass at least one --name / --color / "
                                       "--unset-color / --add-project / --remove-project / "
                                       "--add-pattern / --remove-pattern / --add-browser-url / "
                                       "--remove-browser-url / --set-default-browser-url / "
                                       "--browser-url / --unset-browser-url / --archive / "
                                       "--unarchive."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Workspace existence + per-field validation against the current snapshot.
    existing_workspaces = read_workspaces().get("workspaces", [])
    if not any(w.get("id") == workspace_id for w in existing_workspaces):
        emit_validation_errors(
            [ValidationError("WORKSPACE_ID", "workspace_not_found",
                              f"Workspace {workspace_id!r} not found.")],
        )
        raise typer.Exit(1)

    if new_name is not None:
        name_errs = validate_workspace_name(
            new_name,
            existing_workspaces=existing_workspaces,
            current_id=workspace_id,
            field="--name",
        )
        for e in name_errs:
            errors.append(ValidationError(e.field, e.code, e.message))

    if color is not None and not unset_color:
        for e in validate_color(color, field="--color"):
            errors.append(ValidationError(e.field, e.code, e.message))

    for p in add_patterns:
        for e in validate_pattern(p, field="--add-pattern"):
            errors.append(ValidationError(e.field, e.code, e.message))

    for flag, value in (
        ("--add-browser-url", add_browser_url),
        ("--remove-browser-url", remove_browser_url),
        ("--set-default-browser-url", set_default_browser_url),
        ("--browser-url", browser_url),
    ):
        if value is None:
            continue
        trimmed_url = value.strip()
        if not trimmed_url:
            errors.append(ValidationError(flag, "invalid_value", f"{flag} cannot be empty."))
        else:
            for e in validate_browser_url(trimmed_url, field=flag):
                errors.append(ValidationError(e.field, e.code, e.message))

    if browser_url_label is not None:
        trimmed_label = browser_url_label.strip()
        if len(trimmed_label) > MAX_BROWSER_URL_LABEL_LENGTH:
            errors.append(ValidationError(
                "--browser-url-label", "invalid_value",
                f"--browser-url-label must be ≤ {MAX_BROWSER_URL_LABEL_LENGTH} "
                f"characters (got {len(trimmed_label)}).",
            ))

    # Project existence — only check the additions; silent removals don't
    # need DB validation (they're idempotent against missing ids).
    if add_projects:
        existing_project_ids = set(
            Project.objects.filter(id__in=add_projects).values_list("id", flat=True)
        )
        for pid in add_projects:
            if pid not in existing_project_ids:
                errors.append(ValidationError("--add-project", "project_not_found",
                                              f"Project {pid!r} not found."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    archived_value: bool | None
    if archive:
        archived_value = True
    elif unarchive:
        archived_value = False
    else:
        archived_value = None

    payload = {
        "workspace_id": workspace_id,
        "name": new_name,
        "color": color,
        "unset_color": unset_color,
        "add_projects": list(add_projects),
        "remove_projects": list(remove_projects),
        "add_patterns": list(add_patterns),
        "remove_patterns": list(remove_patterns),
        "archived": archived_value,
    }

    # Browser-URL ops: --browser-url is a shorthand for --add-browser-url +
    # --set-default; --unset-browser-url clears the whole list.
    add_url = browser_url if browser_url is not None else add_browser_url
    if add_url is not None:
        payload["add_browser_url"] = {
            "url": add_url.strip(),
            "label": (browser_url_label or "").strip() or None,
            "set_default": set_default or browser_url is not None,
        }
    if remove_browser_url is not None:
        payload["remove_browser_url"] = remove_browser_url.strip()
    if set_default_browser_url is not None:
        payload["set_default_browser_url"] = set_default_browser_url.strip()
    if unset_browser_url:
        payload["clear_browser_urls"] = True

    sub = transport.submit(payload, kind="workspace:update")
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
