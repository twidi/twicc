"""Top-level ``twicc update-project`` group.

Backward-compatible shape: the flat field patch (``--name`` / ``--color`` /
``--archive`` / ``--default-provider`` / trust flags) lives on the group
callback itself (``invoke_without_command=True``), so the historical
``twicc update-project <PROJECT> --name X`` keeps working unchanged, while
``settings`` is a real sub-command (``twicc update-project <PROJECT>
settings --provider P ...``) mirroring ``twicc update-session <ID> settings``.

The flat patch drops a ``kind="project:update"`` payload in
``<data_dir>/drop-requests/`` so the live TwiCC server applies it via
:func:`twicc.core.services.project_mutation.update_project_from_payload`
— validation + atomic update under the DB write lock +
``project_updated`` broadcast.

Mirrors the HTTP ``PUT /api/projects/<id>/`` endpoint: ``name``, ``color``,
``archived``, ``default_provider``, ``worktree_directory``, and the saved
``browser_urls`` are mutable; the ``directory`` is immutable (the
project id is derived from it). There is no ``delete-project`` counterpart by
design.

The ``--trust`` / ``--untrust`` / ``--reset-trust`` flags are a separate,
**human-only** decision routed through a ``kind="project:trust"`` drop-file to
:func:`twicc.core.services.project_mutation.decide_project_trust_from_payload`
(the same ``decide_project_trust`` service the web dialog uses). They do not
combine with the field patch and are never exposed as an agent-facing skill.
"""

from __future__ import annotations

import typer
from typer.core import TyperGroup

from twicc.cli.update_project.settings_command import update_project_settings_cmd


class _FlatBackcompatGroup(TyperGroup):
    """Group whose own options may appear anywhere when no sub-command is invoked.

    A Click group parses its own options only up to the first positional
    token — everything after is reserved for the sub-command. That would
    break the historical flat form ``update-project <PROJECT> --name X``
    (options after the positional), which predates the group conversion.

    Sub-command intent is strictly positional here (``<PROJECT> <subcommand>
    ...``): when ``args[1]`` is not a known sub-command name, there is no
    sub-command in play, so the whole argv is parsed like a plain command
    (interspersed options allowed) and the flat flags land on the callback
    wherever they appear. When ``args[1]`` IS a sub-command, the default
    group parsing applies and the sub-command's options flow to it untouched.
    """

    def parse_args(self, ctx, args):
        if not (len(args) >= 2 and args[1] in self.commands):
            ctx.allow_interspersed_args = True
        return super().parse_args(ctx, args)


update_project_app = typer.Typer(
    name="update-project",
    cls=_FlatBackcompatGroup,
    help=(
        "Update an existing project: name, color, archived state, default "
        "provider, worktree directory, browser URL (flat flags), or its "
        "per-provider agent-settings defaults (`settings` sub-command)."
    ),
    invoke_without_command=True,
)


@update_project_app.callback()
def update_project_main(
    ctx: typer.Context,
    project_id: str = typer.Argument(
        ...,
        metavar="PROJECT",
        help=(
            "Project to update. Either a project ID (slug form, with or "
            "without leading dash — same format as `twicc projects` output) "
            "or a directory path (absolute or relative); paths are resolved "
            "via realpath and converted to their canonical id."
        ),
    ),
    new_name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Set a new display name. Trimmed; ≤ 25 characters; globally "
            "unique. Mutually exclusive with `--unset-name`."
        ),
    ),
    unset_name: bool = typer.Option(
        False,
        "--unset-name",
        help=(
            "Clear the custom display name (the UI falls back to the "
            "directory's basename). Mutually exclusive with `--name`."
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
            "Clear the project's color. Mutually exclusive with `--color`."
        ),
    ),
    archive: bool = typer.Option(
        False,
        "--archive",
        help=(
            "Mark the project as archived (excluded from default listings). "
            "Mutually exclusive with `--unarchive`."
        ),
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help=(
            "Mark the project as not archived. Mutually exclusive with "
            "`--archive`."
        ),
    ),
    default_provider: str | None = typer.Option(
        None,
        "--default-provider",
        help=(
            "Provider a NEW session in this project defaults to (e.g. "
            "'claude_code', 'codex'). Inherited by sub-projects and git "
            "worktrees; unset = inherit from the parent chain, ultimately "
            "the global default. Mutually exclusive with "
            "`--unset-default-provider`."
        ),
    ),
    unset_default_provider: bool = typer.Option(
        False,
        "--unset-default-provider",
        help=(
            "Clear the project's default provider (back to inherit). "
            "Mutually exclusive with `--default-provider`."
        ),
    ),
    worktree_directory: str | None = typer.Option(
        None,
        "--worktree-directory",
        help=(
            "Absolute base directory under which new git worktrees of this "
            "project are created from the UI. Free-form (not required to live "
            "under the git root). Mutually exclusive with "
            "`--unset-worktree-directory`."
        ),
    ),
    unset_worktree_directory: bool = typer.Option(
        False,
        "--unset-worktree-directory",
        help=(
            "Clear the project's worktree directory (the worktree-create "
            "dialog falls back to the global default, composed against the "
            "git root). Mutually exclusive with `--worktree-directory`."
        ),
    ),
    add_browser_url: str | None = typer.Option(
        None,
        "--add-browser-url",
        help=(
            "Add a URL to the project's saved Browser-pane URLs (http(s) "
            "only — e.g. the project's dev server). Idempotent on an "
            "already-saved URL. The first saved URL becomes the default "
            "(Home target); combine with `--set-default` to flag this one, "
            "`--browser-url-label` to name it."
        ),
    ),
    browser_url_label: str | None = typer.Option(
        None,
        "--browser-url-label",
        help=(
            "Optional label for the URL passed to `--add-browser-url` / "
            "`--default-browser-url` (shown in the Browser pane's menus)."
        ),
    ),
    set_default: bool = typer.Option(
        False,
        "--set-default",
        help=(
            "Flag the URL passed to `--add-browser-url` as the project's "
            "default (Home target)."
        ),
    ),
    remove_browser_url: str | None = typer.Option(
        None,
        "--remove-browser-url",
        help=(
            "Remove a URL from the project's saved Browser-pane URLs "
            "(idempotent — an absent URL is a no-op)."
        ),
    ),
    set_default_browser_url: str | None = typer.Option(
        None,
        "--set-default-browser-url",
        help=(
            "Flag an already-saved URL as the project's default (Home "
            "target). Fails if the URL is not in the saved list."
        ),
    ),
    default_browser_url: str | None = typer.Option(
        None,
        "--default-browser-url",
        help=(
            "Shorthand for `--add-browser-url URL --set-default`: save the "
            "URL (if not already saved) and make it the default. Inherited "
            "by sub-projects and git worktrees; when the project saves no "
            "URL at all, the Browser pane inherits, then falls back to a "
            "containing workspace's saved URLs."
        ),
    ),
    unset_default_browser_url: bool = typer.Option(
        False,
        "--unset-default-browser-url",
        help=(
            "Clear ALL the project's saved Browser-pane URLs (back to "
            "inherit). Mutually exclusive with the other browser-URL flags."
        ),
    ),
    trust: bool = typer.Option(
        False,
        "--trust",
        help=(
            "Mark the project as trusted. A human-only decision: it does not "
            "combine with the other flags, nor with `--untrust` / "
            "`--reset-trust`."
        ),
    ),
    untrust: bool = typer.Option(
        False,
        "--untrust",
        help=(
            "Mark the project as untrusted — sessions created there fall under "
            "the restricted permission set. Mutually exclusive with `--trust` / "
            "`--reset-trust` and the field flags."
        ),
    ),
    reset_trust: bool = typer.Option(
        False,
        "--reset-trust",
        help=(
            "Clear the project's own trust decision so it inherits from its "
            "parent / git root. Mutually exclusive with `--trust` / `--untrust`."
        ),
    ),
    propagate: bool | None = typer.Option(
        None,
        "--propagate/--no-propagate",
        help=(
            "Whether the trust decision also covers sub-paths (only meaningful "
            "with `--trust`/`--untrust`; ignored on reset). Defaults to whether "
            "the project is under git."
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
    """Update an existing project's name, color, archived state, default provider, worktree directory, or saved browser URLs."""
    from twicc.cli._drop_request.project import derive_project_id
    from twicc.cli._output import emit_error

    # Accept a path or an id — derive the canonical project_id once,
    # before any DB lookup or validation downstream. Stashed in ``ctx.obj``
    # for the ``settings`` sub-command.
    project_id = derive_project_id(project_id)[0]
    ctx.obj = project_id

    # All flags above belong to the flat patch — they don't combine with a
    # sub-command (which has its own options). Reject loudly rather than
    # silently ignoring what the user typed.
    if ctx.invoked_subcommand is not None:
        passed = [n for n, on in (
            ("--name", new_name is not None), ("--unset-name", unset_name),
            ("--color", color is not None), ("--unset-color", unset_color),
            ("--archive", archive), ("--unarchive", unarchive),
            ("--default-provider", default_provider is not None),
            ("--unset-default-provider", unset_default_provider),
            ("--worktree-directory", worktree_directory is not None),
            ("--unset-worktree-directory", unset_worktree_directory),
            ("--add-browser-url", add_browser_url is not None),
            ("--browser-url-label", browser_url_label is not None),
            ("--set-default", set_default),
            ("--remove-browser-url", remove_browser_url is not None),
            ("--set-default-browser-url", set_default_browser_url is not None),
            ("--default-browser-url", default_browser_url is not None),
            ("--unset-default-browser-url", unset_default_browser_url),
            ("--trust", trust), ("--untrust", untrust),
            ("--reset-trust", reset_trust),
            ("--propagate/--no-propagate", propagate is not None),
        ) if on]
        if passed:
            emit_error(
                f"Error: {', '.join(passed)} cannot be combined with the "
                f"'{ctx.invoked_subcommand}' sub-command.",
                code=64,
            )
        return

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.core.enums import Provider
    from twicc.core.models import Project
    from twicc.projects import validate_project_name_format
    from twicc.workspaces import MAX_BROWSER_URL_LABEL_LENGTH, validate_browser_url, validate_color

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    def _submit_and_exit(payload: dict, kind: str) -> None:
        sub = transport.submit(payload, kind=kind)
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

    # Trust is a separate, human-only decision: it does not combine with the
    # name/color/archive patch, mirroring the web UI where it is its own action.
    # Handle it (and bail) before the field-patch path below.
    chosen_trust = [n for n, on in (
        ("--trust", trust), ("--untrust", untrust), ("--reset-trust", reset_trust),
    ) if on]
    if chosen_trust:
        trust_errors: list[ValidationError] = []
        if len(chosen_trust) > 1:
            trust_errors.append(ValidationError(
                chosen_trust[0], "conflicting_flags",
                f"{' / '.join(chosen_trust)} are mutually exclusive."))
        field_flags = [f for f, on in (
            ("--name", new_name is not None), ("--unset-name", unset_name),
            ("--color", color is not None), ("--unset-color", unset_color),
            ("--archive", archive), ("--unarchive", unarchive),
            ("--default-provider", default_provider is not None),
            ("--unset-default-provider", unset_default_provider),
            ("--worktree-directory", worktree_directory is not None),
            ("--unset-worktree-directory", unset_worktree_directory),
            ("--add-browser-url", add_browser_url is not None),
            ("--browser-url-label", browser_url_label is not None),
            ("--set-default", set_default),
            ("--remove-browser-url", remove_browser_url is not None),
            ("--set-default-browser-url", set_default_browser_url is not None),
            ("--default-browser-url", default_browser_url is not None),
            ("--unset-default-browser-url", unset_default_browser_url),
        ) if on]
        if field_flags:
            trust_errors.append(ValidationError(
                chosen_trust[0], "conflicting_flags",
                f"Trust flags cannot be combined with {', '.join(field_flags)}; "
                "set trust in its own command."))
        if trust_errors:
            emit_validation_errors(trust_errors)
            raise typer.Exit(1)
        if not Project.objects.filter(id=project_id).exists():
            emit_validation_errors([ValidationError(
                "PROJECT", "project_not_found", f"Project {project_id!r} not found.")])
            raise typer.Exit(1)
        trusted_value = True if trust else (False if untrust else None)
        trust_payload: dict = {"project_id": project_id, "trusted": trusted_value}
        if trusted_value is not None and propagate is not None:
            trust_payload["propagation"] = propagate
        _submit_and_exit(trust_payload, "project:trust")

    # Mutually-exclusive flag checks (don't depend on DB state).
    errors: list[ValidationError] = []
    if new_name is not None and unset_name:
        errors.append(ValidationError("--name", "conflicting_flags",
                                       "--name and --unset-name cannot be used together."))
    if color is not None and unset_color:
        errors.append(ValidationError("--color", "conflicting_flags",
                                       "--color and --unset-color cannot be used together."))
    if archive and unarchive:
        errors.append(ValidationError("--archive", "conflicting_flags",
                                       "--archive and --unarchive cannot be used together."))
    if default_provider is not None and unset_default_provider:
        errors.append(ValidationError("--default-provider", "conflicting_flags",
                                       "--default-provider and --unset-default-provider "
                                       "cannot be used together."))
    if worktree_directory is not None and unset_worktree_directory:
        errors.append(ValidationError("--worktree-directory", "conflicting_flags",
                                       "--worktree-directory and --unset-worktree-directory "
                                       "cannot be used together."))
    if default_browser_url is not None and add_browser_url is not None:
        errors.append(ValidationError("--default-browser-url", "conflicting_flags",
                                       "--default-browser-url and --add-browser-url "
                                       "cannot be used together (the former is a shorthand "
                                       "for the latter plus --set-default)."))
    if unset_default_browser_url and any((
        add_browser_url is not None, browser_url_label is not None, set_default,
        remove_browser_url is not None, set_default_browser_url is not None,
        default_browser_url is not None,
    )):
        errors.append(ValidationError("--unset-default-browser-url", "conflicting_flags",
                                       "--unset-default-browser-url cannot be combined with "
                                       "the other browser-URL flags."))
    if (browser_url_label is not None or set_default) and (
        add_browser_url is None and default_browser_url is None
    ):
        errors.append(ValidationError("--browser-url-label" if browser_url_label is not None else "--set-default",
                                       "missing_flag",
                                       "--browser-url-label and --set-default only apply to "
                                       "--add-browser-url / --default-browser-url."))

    # No-op check.
    has_patch = (
        new_name is not None
        or unset_name
        or color is not None
        or unset_color
        or archive
        or unarchive
        or default_provider is not None
        or unset_default_provider
        or worktree_directory is not None
        or unset_worktree_directory
        or add_browser_url is not None
        or remove_browser_url is not None
        or set_default_browser_url is not None
        or default_browser_url is not None
        or unset_default_browser_url
    )
    if not has_patch:
        errors.append(ValidationError("update-project", "no_op",
                                       "Nothing to update — pass at least one --name / --unset-name / "
                                       "--color / --unset-color / --archive / --unarchive / "
                                       "--default-provider / --unset-default-provider / "
                                       "--worktree-directory / --unset-worktree-directory / "
                                       "--add-browser-url / --remove-browser-url / "
                                       "--set-default-browser-url / --default-browser-url / "
                                       "--unset-default-browser-url."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Project existence + per-field validation.
    if not Project.objects.filter(id=project_id).exists():
        emit_validation_errors(
            [ValidationError("PROJECT", "project_not_found",
                              f"Project {project_id!r} not found.")],
        )
        raise typer.Exit(1)

    if not unset_name and new_name is not None:
        for e in validate_project_name_format(new_name, field="--name"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if not unset_color and color is not None:
        for e in validate_color(color, field="--color"):
            errors.append(ValidationError(e.field, e.code, e.message))

    if default_provider is not None:
        valid_providers = sorted(p.value for p in Provider)
        if default_provider not in valid_providers:
            errors.append(ValidationError(
                "--default-provider", "invalid_provider",
                f"Unknown provider {default_provider!r}. Available: {valid_providers}.",
            ))

    if worktree_directory is not None and not worktree_directory.strip():
        errors.append(ValidationError(
            "--worktree-directory", "invalid_value",
            "--worktree-directory cannot be empty; use "
            "--unset-worktree-directory to clear it.",
        ))

    for flag, value in (
        ("--add-browser-url", add_browser_url),
        ("--remove-browser-url", remove_browser_url),
        ("--set-default-browser-url", set_default_browser_url),
        ("--default-browser-url", default_browser_url),
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

    if not unset_name and new_name is not None and not errors:
        trimmed = new_name.strip()
        if trimmed:
            collision = Project.objects.filter(name=trimmed).exclude(id=project_id).exists()
            if collision:
                errors.append(ValidationError("--name", "duplicate_name",
                                               f"Another project already uses the name {trimmed!r}."))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    if archive:
        archived_value: bool | None = True
    elif unarchive:
        archived_value = False
    else:
        archived_value = None

    payload = {
        "project_id": project_id,
        "name": new_name,
        "unset_name": unset_name,
        "color": color,
        "unset_color": unset_color,
        "archived": archived_value,
        "default_provider": default_provider,
        "unset_default_provider": unset_default_provider,
        "worktree_directory": (
            worktree_directory.strip() if worktree_directory is not None else None
        ),
        "unset_worktree_directory": unset_worktree_directory,
    }

    # Browser-URL ops: --default-browser-url is a shorthand for
    # --add-browser-url + --set-default; --unset-default-browser-url clears
    # the whole list.
    add_url = default_browser_url if default_browser_url is not None else add_browser_url
    if add_url is not None:
        payload["add_browser_url"] = {
            "url": add_url.strip(),
            "label": (browser_url_label or "").strip() or None,
            "set_default": set_default or default_browser_url is not None,
        }
    if remove_browser_url is not None:
        payload["remove_browser_url"] = remove_browser_url.strip()
    if set_default_browser_url is not None:
        payload["set_default_browser_url"] = set_default_browser_url.strip()
    if unset_default_browser_url:
        payload["clear_browser_urls"] = True

    _submit_and_exit(payload, "project:update")


update_project_app.command(name="settings")(update_project_settings_cmd)
