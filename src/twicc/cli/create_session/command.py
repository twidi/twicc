"""Top-level ``twicc create-session`` command (stub)."""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_context import load_help_context
from twicc.cli._drop_request.help_strings import (
    EFFORT_ALIAS_HINT,
    NO_EXPAND_HELP,
    PERMISSION_ALIAS_HINT,
    PROMPT_INCLUDE_HINT,
    context_max_help,
    default_suffix,
    model_help,
    preset_help,
    provider_help,
)

# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Cheap (~30 ms, pure file I/O,
# no Django) and degrades to "no extra info" on missing / malformed files.
_HELP_CTX = load_help_context()


def create_session_cmd(
    prompt: str = typer.Argument(
        ...,
        help=(
            "Prompt text, or path to a file whose content is the prompt. Over "
            "--remote the file is read locally; prefix an absolute path with "
            "'remote:' to read it on the remote server instead."
        ) + PROMPT_INCLUDE_HINT,
    ),
    no_expand: bool = typer.Option(
        False,
        "--no-expand",
        help=NO_EXPAND_HELP,
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help=(
            "Project id (with or without leading dash) or directory path "
            "(absolute or relative). New directories are auto-resolved to "
            "their canonical project id via realpath. Defaults to the current "
            "working directory."
        ),
    ),
    worktree_branch: str | None = typer.Option(
        None,
        "--worktree-branch",
        help=(
            "Create the session in a NEW git worktree of --project on this "
            "branch (existing local branch => checked out; new => created with "
            "-b). --project is then the source repository and the session lands "
            "in the worktree, registered as its own project linked back to it. "
            "Requires --worktree-path."
        ),
    ),
    worktree_path: str | None = typer.Option(
        None,
        "--worktree-path",
        help=(
            "Absolute path of the git worktree's directory. With "
            "--worktree-branch the NEW worktree is created there (git rejects "
            "a non-empty target). WITHOUT --worktree-branch it must point to "
            "an EXISTING worktree of --project, which is adopted (registered + "
            "session opened) without creating anything."
        ),
    ),
    worktree_start_from: str | None = typer.Option(
        None,
        "--worktree-start-from",
        help=(
            "Branch/revision the new branch starts from (only when "
            "--worktree-branch does not yet exist). Defaults to the source "
            "repo's current HEAD. Ignored for an existing-branch checkout."
        ),
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help=provider_help(_HELP_CTX),
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=preset_help(_HELP_CTX),
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
            + default_suffix(_HELP_CTX,"effort")
        ),
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help=(
            "Tool permission policy. Claude Code: 'default', 'acceptEdits', 'plan', "
            "'dontAsk', 'bypassPermissions'. Codex: 'read_only', 'strict', 'auto', "
            "'autonomous', 'yolo'."
            + PERMISSION_ALIAS_HINT
            + default_suffix(_HELP_CTX,"permission_mode")
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Enable extended thinking. Omit to keep the "
            "preset's value (or the default from settings)."
            + default_suffix(_HELP_CTX,"thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to "
            "keep the preset's value (or the default from settings)."
            + default_suffix(_HELP_CTX,"claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Enable fast mode on supported Claude Code or Codex models (higher token "
            "throughput and credit usage). Omit to keep the preset's value "
            "(or the default from settings)."
            + default_suffix(_HELP_CTX,"fast_mode")
        ),
    ),
    question_widget: bool | None = typer.Option(
        None,
        "--question-widget/--no-question-widget",
        help=(
            "Enable interactive question widgets. Pass --no-question-widget to "
            "force the agent to ask its questions as plain text instead of using "
            "a UI widget (useful for CLI workflows where you want to read and "
            "answer questions textually, without going through the TwiCC UI). "
            "Honored by providers that map this flag to a widget tool: Claude "
            "Code (AskUserQuestion) and Codex (request_user_input). Omit to use "
            "the default (widget enabled)."
            + default_suffix(_HELP_CTX,"question_widget")
        ),
    ),
    hidden: bool = typer.Option(
        False,
        "--hidden",
        help=(
            "Create the session as hidden — invisible from every list, "
            "search, broadcast, and counter shown to the user, while still "
            "counted in cost aggregates. Requires a non-interactive "
            "permission_mode (bypassPermissions/dontAsk for Claude Code; "
            "yolo/strict for Codex) and question_widget=False."
        ),
    ),
    mute_on_user_turn: bool = typer.Option(
        False,
        "--mute-on-user-turn",
        help=(
            "Suppress this session's finished-working toast, sound, browser "
            "notification, and Apprise user-turn event. Questions, approvals, "
            "failures, and usage alerts remain enabled."
        ),
    ),
    context_max: str | None = typer.Option(
        None,
        "--context-max",
        help=context_max_help(_HELP_CTX),
    ),
    title: str | None = typer.Option(
        None,
        "--title",
        help=(
            "Custom session title (max 200 characters). When omitted, the "
            "title is auto-derived from the first message."
        ),
    ),
    attach: list[str] = typer.Option(
        [],
        "--attach",
        help=(
            "Path to a file to attach (repeatable). Claude Code accepts "
            "PNG/JPEG/GIF/WebP/PDF/text/plain up to 5 MB each. Codex accepts "
            "images only. Max 100 files, 32 MB total. "
            "Each value is either a local file path OR a base64 data URI "
            "(data:<mime>;base64,<data>) — the data-URI form lets remote/API "
            "callers attach files without a shared filesystem. Over --remote, "
            "prefix an absolute path with 'remote:' to read it on the remote "
            "server instead."
        ),
    ),
    annotation: list[str] = typer.Option(
        [],
        "--annotation",
        help=(
            "Free-form session annotation as key=value (repeatable). Keys may "
            "use dotted paths; values support true, false, null, numbers, or strings."
        ),
    ),
    annotations_file: str | None = typer.Option(
        None,
        "--annotations-file",
        help="Path to a JSON object containing session annotations.",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the session may still be created on "
            "the server side."
        ),
    ),
) -> None:
    """Create a new session by dropping a request file the server picks up.

    All options are optional — only the PROMPT argument is required. With no
    flags, the command uses the default provider from settings, falls back
    to the current directory as the project, and lets the defaults from
    settings drive model / effort / permission mode / etc.

    Asynchronous: a "created" status only means the session was started and
    the prompt handed to the agent — not that the agent has finished, which
    can take a while. It keeps working in the background. To block until it
    reaches a given state, follow up with
    "twicc process <SESSION_ID> wait <STATE>... --timeout N" (e.g. user_turn
    once the reply is complete).
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.aliases import clamp_untrusted_permission_mode, resolve_overrides
    from twicc.cli._drop_request.annotations import parse_annotations
    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.prompt import resolve_prompt, PromptError
    from twicc.cli._drop_request.project import resolve_project
    from twicc.cli._drop_request.presets import apply_preset_and_overrides, PresetError
    from twicc.cli._drop_request.validation import (
        ValidationError,
        validate_hidden_constraints,
        validate_provider,
        validate_settings,
    )
    from twicc.cli._output import emit_error
    from twicc.providers.helpers import get_provider_helpers

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    bootstrap = load_local_bootstrap()

    alias_errors: list[ValidationError] = []
    try:
        text = resolve_prompt(prompt, expand=not no_expand)
        resolved_project = resolve_project(project)

        # Resolve provider: explicit flag wins; otherwise the project chain's
        # inherited default provider (worktree main repo / path ancestors —
        # same resolution the frontend applies when creating a draft), then
        # the global default from settings. If none resolves, fail fast with
        # a clear validation error.
        if provider is None:
            from twicc.project_agent_defaults import resolve_project_default_provider
            provider = resolve_project_default_provider(
                resolved_project.project_id, directory=resolved_project.directory,
            ) or bootstrap.default_provider
            if provider is None:
                emit_validation_errors(
                    [ValidationError(
                        "--provider",
                        "no_default_provider",
                        "No --provider given and no default provider set in settings.",
                    )],
                )
                raise typer.Exit(1)
        # Whether the target project is untrusted (unknown trust counts as
        # untrusted). Drives permission_mode alias resolution + the clamp, so a
        # CLI-created session in an untrusted project gets the same restricted
        # permission set the UI offers there, with a clear note on a downgrade.
        from twicc.core.services.trust import project_is_untrusted
        untrusted_project = project_is_untrusted(resolved_project.project_id)
        overrides = {
            "selected_model": model,
            "effort": effort,
            "permission_mode": permission_mode,
            "thinking_enabled": thinking,
            "claude_in_chrome": claude_in_chrome,
            "fast_mode": fast_mode,
            # Raw string: aliases (``max``/``min``) and the literal
            # token forms are resolved + parsed per-provider just below.
            "context_max": context_max,
            "question_widget": question_widget,
        }
        # Resolve aliases (``max``/``open``/...), parse context_max, and
        # drop fields this provider doesn't support — against the resolved
        # provider, so the rest of the pipeline sees concrete literals only.
        if provider in bootstrap.providers:
            overrides, alias_errors = resolve_overrides(
                overrides, bootstrap.providers[provider], untrusted=untrusted_project,
            )
        preset_list = bootstrap.providers[provider].presets if provider in bootstrap.providers else []
        settings = apply_preset_and_overrides(
            preset, preset_list, overrides, untrusted=untrusted_project,
        )
        # Materialize the inherited defaults (project chain → global synced
        # default) onto every still-unset supported field — the CLI
        # counterpart of the frontend draft pre-fill: a new session stores a
        # concrete snapshot at creation and never follows later default
        # changes.
        if provider in bootstrap.providers:
            from twicc.project_agent_defaults import materialize_inherited_defaults

            settings = materialize_inherited_defaults(
                settings,
                project_id=resolved_project.project_id,
                directory=resolved_project.directory,
                provider=provider,
                pb=bootstrap.providers[provider],
                untrusted=untrusted_project,
            )
        if untrusted_project and provider in bootstrap.providers:
            # Creation: seed the untrusted default when no permission_mode was
            # given (frontend parity; the materialization above normally
            # leaves nothing to seed), and clamp an out-of-set value.
            settings = clamp_untrusted_permission_mode(
                settings, bootstrap.providers[provider], seed_when_absent=True,
            )
    except PromptError as e:
        emit_validation_errors([ValidationError("prompt", "invalid_prompt", str(e))])
        raise typer.Exit(1)
    except PresetError as e:
        emit_validation_errors([ValidationError("--preset", "invalid_preset", str(e))])
        raise typer.Exit(1)

    # ``resolve_project`` only raises in API mode (the relative-path guard,
    # via ``emit_error``); otherwise, when the input is an id with no matching
    # Project row and no on-disk directory backing it, it returns
    # ``directory=None``. ``create-session`` needs the directory (to seed
    # the project server-side), so reject here with the same UX as before.
    if resolved_project.directory is None:
        emit_validation_errors(
            [ValidationError(
                "--project", "invalid_project",
                f"--project: {project!r} is neither an existing directory "
                f"nor a known project_id (tried also with leading '-').",
            )],
        )
        raise typer.Exit(1)

    errors: list[ValidationError] = []
    annotations, annotation_errors = parse_annotations(annotation or [], annotations_file)
    errors.extend(annotation_errors)
    provider_errors = validate_provider(provider, bootstrap)
    errors.extend(provider_errors)
    errors.extend(alias_errors)  # invalid_format from --context-max (per-provider)
    if not provider_errors:  # only validate settings if the provider is OK
        errors.extend(validate_settings(provider, settings, bootstrap))

    # --hidden auto-forces question_widget=False so the agent never lands
    # in an interactive state nobody can see. Only error when the user
    # explicitly contradicts that with --question-widget — the validator
    # below catches that case once the effective bundle is built.
    if hidden and settings.question_widget is not True:
        settings = settings._replace(question_widget=False)

    # Resolve effective settings (None → synced default, then consistency
    # demotion) so we know the real model that will drive the resize cap.
    # The back-end service redoes this for the actual session creation;
    # the duplicated call here is cheap and local.
    helpers_obj = (
        get_provider_helpers(provider) if provider in bootstrap.providers else None
    )
    effective_model: str | None = None
    if helpers_obj is not None and not errors:
        effective_settings = helpers_obj.resolve_agent_settings(settings)
        effective_settings = helpers_obj.enforce_agent_settings_consistency(
            effective_settings
        )
        effective_model = effective_settings.selected_model
        errors.extend(validate_hidden_constraints(provider, effective_settings, hidden=hidden))

    support = bootstrap.providers[provider].attachment_support if provider in bootstrap.providers else {}
    try:
        attach_result = validate_and_encode(
            attach or [], support, helpers_obj, effective_model,
        )
    except AttachmentResizeError as e:
        errors.append(ValidationError(
            f"--attach {e.path}", "resize_failed", e.message,
        ))
        emit_validation_errors(errors)
        raise typer.Exit(1)

    for err in attach_result.errors:
        errors.append(ValidationError(f"--attach {err.file}", err.code, err.message))

    # Worktree flags: --worktree-branch turns --project into the source repo
    # and creates the session in a NEW worktree; --worktree-path alone (no
    # branch) adopts an EXISTING worktree of --project instead. A path is
    # required to create and must be absolute either way (no caller CWD to
    # resolve it against, like every other path the CLI hands the server).
    # --worktree-start-from only shapes new-branch creation. Git-level and
    # worktree-membership checks are the server's authority.
    wt_branch = (worktree_branch or "").strip()
    wt_path = (worktree_path or "").strip()
    wt_start_from = (worktree_start_from or "").strip()
    if wt_start_from and not wt_branch:
        errors.append(ValidationError(
            "--worktree-branch", "missing_worktree_branch",
            "--worktree-start-from requires --worktree-branch.",
        ))
    if wt_branch and not wt_path:
        errors.append(ValidationError(
            "--worktree-path", "missing_worktree_path",
            "--worktree-branch requires --worktree-path (absolute path of "
            "the new worktree directory).",
        ))
    if wt_path and not os.path.isabs(wt_path):
        errors.append(ValidationError(
            "--worktree-path", "invalid_worktree_path",
            "--worktree-path must be an absolute path.",
        ))

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Auto-fill spawned_by silently via PID ancestry. No CLI flag exposes
    # this — the agent never has to know its own session_id to call us.
    try:
        from twicc.cli._drop_request.whoami import resolve_current_session
        current = resolve_current_session()
        spawned_by_session_id = current.id if current is not None else None
    except Exception:
        spawned_by_session_id = None

    # Build the WS-compatible payload. ``directory`` is passed alongside
    # ``project_id`` so the server (DropRequestsWatcher → service) can
    # auto-create the Project from inside the main process — that's where
    # the WS broadcasts of ``project_added`` and ``workspaces_updated``
    # need to originate to reach connected UI clients live.
    from twicc.mcp.identity import external_caller
    if external_caller.get() is not None:
        from twicc.cli._drop_request.sender_header import prefix_sender_header
        text = prefix_sender_header(text, None, recipient_id="", recipient_spawned_by_id=None)
    payload = {
        "project_id": resolved_project.project_id,
        "directory": resolved_project.directory,
        "provider": provider,
        "text": text,
        "title": title,
        "images": attach_result.images,
        "documents": attach_result.documents,
        "hidden": hidden,
        "mute_on_user_turn": mute_on_user_turn,
        "spawned_by_session_id": spawned_by_session_id,
        "annotations": annotations,
        **settings._asdict(),
    }
    # ``project_id``/``directory`` stay the SOURCE repo; the server creates (or
    # adopts) the worktree from it and retargets the session at it. The server
    # disambiguates on the presence of ``worktree_branch``: present => create a
    # new worktree; absent with ``worktree_path`` => adopt an existing one.
    if wt_branch:
        payload["worktree_branch"] = wt_branch
        payload["worktree_path"] = wt_path
        if wt_start_from:
            payload["worktree_start_from"] = wt_start_from
    elif wt_path:
        payload["worktree_path"] = wt_path

    sub = transport.submit(payload, kind="session:create")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(
        outcome,
        request_uuid=sub.request_uuid,
        timeout=timeout,
    )

    # Exit code mapping (spec §2.5)
    if outcome.status == "created":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


# _materialize_inherited_defaults moved to
# twicc.project_agent_defaults.materialize_inherited_defaults (shared with the
# server-side peer-message delivery path).
