"""Top-level ``twicc update-sessions`` sub-app (batch updates).

One sub-command per supported batch update — ``archive`` / ``unarchive``,
``pin`` / ``unpin``, ``hide`` / ``unhide``, ``mute`` / ``notify``,
``annotations``, ``settings`` — each applying the SAME change to every targeted
session via the shared
:func:`twicc.cli._batch_runner.run_batch`.

Deliberately excluded from the batch surface:

- ``title`` — setting the same title on several sessions is meaningless.

Unlike ``update-session`` (where the single id is a parent-callback argument),
the session selector here is a positional ``SESSION_ID...`` list on each
sub-command, mirroring ``processes stop``. So the values the singular command
took positionally move to options: ``pin --mode`` and ``annotations --op``.
The shared ``--spawned-by`` / ``--descendants`` / ``--annotation`` scope
filters are merged (union) with the explicit ids, explicit ids first.
"""

from __future__ import annotations

import typer

from twicc.cli._batch_runner import run_batch
from twicc.cli._drop_request.help_context import load_help_context
from twicc.cli._drop_request.help_strings import (
    EFFORT_ALIAS_HINT,
    PERMISSION_ALIAS_HINT,
    context_max_help,
    default_suffix,
    model_help,
    preset_help,
)
from twicc.cli._drop_request.settings_resolution import unset_help
from twicc.cli._output import emit_error

# Load the user's providers + presets once so the ``settings`` --help strings
# can mention them (Django-free, ~30 ms cold). Same trick as the singular.
_HELP_CTX = load_help_context()


# Mirrors :class:`twicc.core.models.PinMode`. Kept as a flat tuple so ``pin``
# validates ``--mode`` locally without importing Django.
_VALID_PIN_MODES: tuple[str, ...] = ("project", "workspace", "all")


# Shared help strings — every sub-command reuses the same scope-filter and
# timeout wording (kept identical to ``processes stop`` semantics).
_SESSION_IDS_HELP = (
    "Sessions to update. Optional if you pass --spawned-by or --descendants "
    "(explicit ids and scope-selected ids are merged, explicit first). "
    "Use 'self' for the current session."
)
_SPAWNED_BY_HELP = (
    "Also target sessions spawned by the given session_id, or 'self' for the "
    "current session. Merged (union) with explicit SESSION_IDs. "
    "'parent' is not supported. Mutually exclusive with --descendants."
)
_DESCENDANTS_HELP = (
    "Also target the proper descendants of the given session_id, or 'self' "
    "for the current session. Merged (union) with explicit SESSION_IDs. "
    "'parent' is not supported. Mutually exclusive with --spawned-by."
)
_ANNOTATION_HELP = (
    "Narrow the --spawned-by / --descendants scope by annotation. Repeatable, "
    "AND-combined. Requires a filiation scope; does NOT filter explicit "
    "SESSION_IDs. Same syntax as `twicc sessions --annotation`."
)
_TIMEOUT_HELP = (
    "Wall-clock seconds to wait for the whole batch (drops are processed in "
    "parallel server-side, so this is a budget, not N×timeout). Per-id "
    'entries with no final status by the deadline get status="timeout". '
    "Must be > 0."
)
_OP_HELP = (
    "Annotation operation, applied in order to every targeted session. "
    "Repeatable; at least one required. One of: clear, replace-file:PATH, "
    "merge-file:PATH, set:KEY=VALUE, unset:KEY."
)


update_sessions_app = typer.Typer(
    name="update-sessions",
    help=(
        "Apply the same update to several sessions at once (archive, "
        "unarchive, pin, unpin, hide, unhide, mute, notify, annotations, "
        "settings)."
    ),
    invoke_without_command=False,
)


@update_sessions_app.command(name="archive")
def _archive(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Archive every targeted session.

    Per session, same effect as `update-session <ID> archive`: stops the live
    agent, closes the session's terminals, may auto-unpin
    (autoUnpinOnArchive synced setting).
    """
    run_batch(
        session_ids or [],
        kind="session:update_archived",
        prepare=lambda r: {"session_id": r.session_id, "archived": True},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unarchive")
def _unarchive(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unarchive every targeted session (flips archived back to False; no resume)."""
    run_batch(
        session_ids or [],
        kind="session:update_archived",
        prepare=lambda r: {"session_id": r.session_id, "archived": False},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="pin")
def _pin(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    mode: str = typer.Option(
        ...,
        "--mode",
        help=(
            "Pin scope applied to every targeted session: 'project', "
            "'workspace', or 'all'. Same vocabulary as the UI's pin menu."
        ),
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Pin every targeted session in the given scope."""
    if mode not in _VALID_PIN_MODES:
        emit_error(
            f"Error: invalid --mode {mode!r}. Accepted: {list(_VALID_PIN_MODES)}.",
            code=1,
        )
    run_batch(
        session_ids or [],
        kind="session:update_pinned",
        prepare=lambda r: {"session_id": r.session_id, "pinned": mode},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unpin")
def _unpin(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unpin every targeted session (regardless of its current pin scope)."""
    run_batch(
        session_ids or [],
        kind="session:update_pinned",
        prepare=lambda r: {"session_id": r.session_id, "pinned": None},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="hide")
def _hide(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Hide every targeted session.

    Per session, same invariants as `update-session <ID> hide`: a session
    whose settings break the hidden whitelist (permission_mode /
    question_widget) is rejected individually (status=rejected) while the
    others are hidden.
    """
    run_batch(
        session_ids or [],
        kind="session:update_hidden",
        prepare=lambda r: {"session_id": r.session_id, "hidden": True},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unhide")
def _unhide(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unhide every targeted session (re-enters lists / search / counters)."""
    run_batch(
        session_ids or [],
        kind="session:update_hidden",
        prepare=lambda r: {"session_id": r.session_id, "hidden": False},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="mute")
def _mute(
    session_ids: list[str] | None = typer.Argument(  # noqa: B008
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),  # noqa: B008
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Suppress finished-working notifications for every targeted session."""
    run_batch(
        session_ids or [],
        kind="session:update_mute_on_user_turn",
        prepare=lambda resolved: {
            "session_id": resolved.session_id,
            "mute_on_user_turn": True,
        },
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="notify")
def _notify(
    session_ids: list[str] | None = typer.Argument(  # noqa: B008
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),  # noqa: B008
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Enable finished-working notifications for every targeted session.

    Existing global notification settings still apply.
    """
    run_batch(
        session_ids or [],
        kind="session:update_mute_on_user_turn",
        prepare=lambda resolved: {
            "session_id": resolved.session_id,
            "mute_on_user_turn": False,
        },
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="annotations")
def _annotations(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    op: list[str] = typer.Option([], "--op", metavar="OPERATION", help=_OP_HELP),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Apply the same ordered annotation operations to every targeted session.

    Note the two distinct annotation flags: ``--op`` is the mutation applied to
    each session; ``--annotation`` is a read-only FILTER that narrows the
    --spawned-by / --descendants scope. Operations are parsed once (a malformed
    --op fails the whole command); a per-session apply error (e.g. a path
    conflict against that session's existing annotations) is reported per id.
    """
    # Operation parsing is global (same ops for every id) and Django-free, so
    # do it up-front: a malformed --op is a classic argument error (exit 1).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.annotations import parse_annotation_update_operations

    parsed_operations, errors = parse_annotation_update_operations(op)
    if errors:
        emit_error(
            "Error: invalid --op:\n"
            + "\n".join(f"  - {e.code}: {e.message}" for e in errors),
            code=1,
        )

    run_batch(
        session_ids or [],
        kind="session:update_annotations",
        prepare=lambda r: {
            "session_id": r.session_id, "operations": parsed_operations,
        },
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="settings")
def _settings(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=preset_help(_HELP_CTX) + (
            " WARNING: --preset replaces every settings field on each session "
            "(preset values set what it defines; absent fields become NULL / "
            "synced default; per-flag options override the preset; --unset "
            "forces NULL). Without --preset, only the fields you touch are "
            "written; every other field keeps its current value."
        ),
    ),
    model: str | None = typer.Option(None, "--model", help=model_help(_HELP_CTX)),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help=(
            "Reasoning effort. Claude Code: 'low', 'medium', 'high', 'xhigh', 'max'. "
            "Codex: 'low', 'medium', 'high', 'xhigh'."
            + EFFORT_ALIAS_HINT
            + default_suffix(_HELP_CTX, "effort")
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
            + default_suffix(_HELP_CTX, "permission_mode")
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help=(
            "Claude Code only. Enable extended thinking. Omit to leave unchanged."
            + default_suffix(_HELP_CTX, "thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to leave "
            "unchanged."
            + default_suffix(_HELP_CTX, "claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Enable fast mode on supported Claude Code or Codex models. Omit to leave "
            "unchanged."
            + default_suffix(_HELP_CTX, "fast_mode")
        ),
    ),
    question_widget: bool | None = typer.Option(
        None,
        "--question-widget/--no-question-widget",
        help=(
            "Enable interactive question widgets. Pass --no-question-widget to "
            "force plain-text questions. A startup setting: Claude Code restarts "
            "the agent to apply it, Codex applies it only at the next process "
            "start (stop the session, then send a message to resume it). Omit to "
            "leave unchanged."
            + default_suffix(_HELP_CTX, "question_widget")
        ),
    ),
    context_max: str | None = typer.Option(
        None, "--context-max", help=context_max_help(_HELP_CTX),
    ),
    unset: list[str] = typer.Option([], "--unset", help=unset_help()),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Apply the same agent-settings change to every targeted session.

    Flags mirror `update-session settings` (patch by default; `--preset`
    switches to replace mode). Resolution is per-session against each session's
    provider: aliases resolve to that provider's concrete value, fields
    the provider doesn't support are dropped silently (no-op), and a session
    whose provider rejects a value yields a per-id validation_error
    (invalid_choice / invalid_format / invalid_preset) while the others proceed.

    Startup settings (effort, thinking, claude-in-chrome, fast-mode,
    question-widget on Claude Code) are applied on the next restart: the agent
    is stopped (at the end of its current assistant turn if working) so the
    next message it receives starts it with the new settings.
    """
    # At least one of --preset / a per-field flag / --unset is required, like
    # the singular command (enforced by parse_settings_flags' no_op check).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.settings_resolution import (
        parse_settings_flags,
        prepare_settings,
    )

    # Provider-independent flag validation (global, fatal — same for every id).
    parsed = parse_settings_flags(
        model=model, effort=effort, permission_mode=permission_mode,
        thinking=thinking, claude_in_chrome=claude_in_chrome,
        fast_mode=fast_mode, question_widget=question_widget,
        context_max=context_max, unset=unset, preset=preset,
    )
    if isinstance(parsed, list):
        emit_error(
            "Error: invalid settings arguments:\n"
            + "\n".join(f"  - {e.field}: {e.code}: {e.message}" for e in parsed),
            code=1,
        )
    overrides, unset_fields = parsed

    bootstrap = load_local_bootstrap()

    def _prepare(resolved):
        """Per-id: resolve against the session's provider → payload or errors."""
        result = prepare_settings(
            resolved, overrides=overrides, unset_fields=unset_fields,
            preset=preset, bootstrap=bootstrap,
        )
        if isinstance(result, list):
            return result
        updates, replace_all = result
        if not updates and not replace_all:
            return None  # every touched field is a no-op for this provider
        return {
            "session_id": resolved.session_id,
            "updates": updates,
            "replace_all": replace_all,
        }

    run_batch(
        session_ids or [],
        kind="session:update_settings",
        prepare=_prepare,
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )
