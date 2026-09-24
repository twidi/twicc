"""``twicc update-session <ID> settings`` sub-command.

Drops a ``kind="session:update_settings"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server applies the change via
:func:`twicc.core.services.session_update.update_session_settings_from_payload`.

The semantics mirror the UI's settings-only update path:
- Without ``--preset``, only the fields explicitly touched (per-flag values
  and ``--unset <field>``) are written.
- With ``--preset``, every settings field is rewritten: the preset's values
  set the fields it defines, fields absent from the preset become ``NULL``
  (use the synced default), per-flag values override the preset, and
  ``--unset`` forces a field back to ``NULL`` even if the preset set it.

``--model X`` combined with ``--unset model`` is rejected up-front
(``unset_conflict``). ``--unset <field>`` for a field the session's provider
doesn't support is silently ignored (no-op), as is a per-flag value for an
unsupported field; aliases (``max``, ``open``, ...) are resolved to the
provider's concrete value before validation.

The flag parsing (provider-independent) and the per-provider resolution both
live in :mod:`twicc.cli._drop_request.settings_resolution`, shared with the
batch ``twicc update-sessions settings`` so the two stay byte-for-byte aligned.
"""

from __future__ import annotations

import typer

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


# Load the user's current providers + presets at module import time so the
# Typer ``help=`` strings can mention them. Same trick as
# ``create_session.command`` — kept Django-free, ~30 ms cold, degrades to
# "no info" on missing / malformed files.
_HELP_CTX = load_help_context()


def update_settings_cmd(
    ctx: typer.Context,
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=preset_help(_HELP_CTX) + (
            " WARNING: passing --preset replaces every settings field. The "
            "preset's values write the fields it defines; fields absent from "
            "the preset become NULL (use the synced default); per-flag "
            "options override the preset; --unset still forces a field to "
            "NULL. Without --preset, only the fields you explicitly touch "
            "via per-flag options or --unset are written; every other field "
            "keeps its current value in DB."
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
            "Claude Code only. Enable extended thinking. Omit to leave the "
            "current value unchanged."
            + default_suffix(_HELP_CTX, "thinking_enabled")
        ),
    ),
    claude_in_chrome: bool | None = typer.Option(
        None,
        "--claude-in-chrome/--no-claude-in-chrome",
        help=(
            "Claude Code only. Enable the Chrome MCP integration. Omit to "
            "leave the current value unchanged."
            + default_suffix(_HELP_CTX, "claude_in_chrome")
        ),
    ),
    fast_mode: bool | None = typer.Option(
        None,
        "--fast-mode/--no-fast-mode",
        help=(
            "Enable fast mode on supported Claude Code or Codex models (higher "
            "throughput and credit usage). Omit to leave "
            "the current value unchanged."
            + default_suffix(_HELP_CTX, "fast_mode")
        ),
    ),
    question_widget: bool | None = typer.Option(
        None,
        "--question-widget/--no-question-widget",
        help=(
            "Enable interactive question widgets. Pass --no-question-widget "
            "to force the agent to ask its questions as plain text. A startup "
            "setting: Claude Code restarts the agent to apply it, Codex applies "
            "it only at the next process start (stop the session, then send a "
            "message to resume it). Omit to leave the current value unchanged."
            + default_suffix(_HELP_CTX, "question_widget")
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
        help=unset_help(),
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
    """Update the agent settings of an existing session.

    At least one of --preset, a per-field flag, or --unset is required. If
    nothing is passed, the command fails with ``no_op``.
    """
    session_id: str = ctx.obj

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._drop_request.settings_resolution import (
        parse_settings_flags, prepare_settings,
    )
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error, emit_json

    # Server-up check first (exit 2 wins over any validation error), matching
    # the original ordering of this command.
    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Local pre-check: session must exist, not be a subagent, not be stale,
    # and have a project directory. The watcher-side service re-validates
    # the same conditions in case the DB state changed.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    # 1. Provider-independent flag validation (unknown --unset token, malformed
    # --context-max, set/unset conflict, no-op).
    parsed = parse_settings_flags(
        model=model, effort=effort, permission_mode=permission_mode,
        thinking=thinking, claude_in_chrome=claude_in_chrome,
        fast_mode=fast_mode, question_widget=question_widget,
        context_max=context_max, unset=unset, preset=preset,
    )
    if isinstance(parsed, list):
        emit_validation_errors(parsed)
        raise typer.Exit(1)
    overrides, unset_fields = parsed

    # 2. Per-provider resolution (provider enabled, --unset supported, preset
    # resolution, value validation, hidden invariants).
    bootstrap = load_local_bootstrap()
    result = prepare_settings(
        resolved, overrides=overrides, unset_fields=unset_fields,
        preset=preset, bootstrap=bootstrap,
    )
    if isinstance(result, list):
        emit_validation_errors(result)
        raise typer.Exit(1)
    updates, replace_all = result

    if not updates and not replace_all:
        # Every field touched was a no-op for this session's provider (e.g.
        # --thinking on Codex): nothing to write. Report a silent no-op rather
        # than dropping an empty-updates request the server would reject.
        emit_json({"status": "noop", "session_id": resolved.session_id})
        raise typer.Exit(0)

    payload = {
        "session_id": resolved.session_id,
        "updates": updates,
        "replace_all": replace_all,
    }

    sub = transport.submit(payload, kind="session:update_settings")
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
