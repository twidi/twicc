"""Top-level ``twicc send-message`` command."""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_strings import NO_EXPAND_HELP, PROMPT_INCLUDE_HINT


def send_message_cmd(
    session_id: str = typer.Argument(
        ...,
        help=(
            "Id of the existing session to send the message to, or the "
            "keyword 'parent' to target the parent of the calling session "
            "(resolved via PID ancestry → current session's spawned_by)."
        ),
    ),
    prompt: str = typer.Argument(
        None,
        help=(
            "Message text, or path to a file whose content is the message. Over "
            "--remote the file is read locally; prefix an absolute path with "
            "'remote:' to read it on the remote server instead. Optional when "
            "at least one --attach is given: a message made only of "
            "attachments is valid."
        ) + PROMPT_INCLUDE_HINT,
    ),
    no_expand: bool = typer.Option(
        False,
        "--no-expand",
        help=NO_EXPAND_HELP,
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
    wait_reply: bool = typer.Option(
        False,
        "--wait-reply",
        help=(
            "Keep going after the message is delivered, until the session "
            "answers. Adds a `reply` block to the result carrying the answer's "
            "text, its `line_num`, and an `outcome` saying what ended the "
            "wait: 'replied' (the message closing the turn), 'provider_error' "
            "(the provider refused: quota, outage), 'ended' (the turn is over "
            "and nothing closed it), 'timeout', 'backend_gone', or "
            "'wait_failed'. Only a line written after this message counts, so "
            "the previous turn's answer is not returned in its place. An "
            "agent that blocks on a click DURING the turn does not end the "
            "wait — a human can still answer — so the result carries "
            "`awaiting_user_input: true` instead (a target already blocked "
            "when you send is refused outright, exit 3). The exit code never "
            "changes: the message was sent either way, so read `outcome`."
        ),
    ),
    reply_timeout: float = typer.Option(
        None,
        "--reply-timeout",
        help=(
            "Seconds --wait-reply may spend waiting (default 300, the ceiling "
            "MCP callers are asked to respect — an MCP client may itself give "
            "up on a tool silent that long, so over MCP pass a shorter value "
            "and come back rather than riding the default to its end). A "
            "timeout is not a failure and nothing is lost — the agent keeps "
            "working, and the result carries `since_line_num` to resume from. "
            "Requires --wait-reply."
        ),
    ),
    no_reply_text: bool = typer.Option(
        False,
        "--no-reply-text",
        help=(
            "With --wait-reply, report that the answer arrived without "
            "returning its text — `line_num` is still there to fetch it. "
            "Requires --wait-reply."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the message may still be sent on "
            "the server side."
        ),
    ),
) -> None:
    """Send a message to an existing session.

    PROMPT may be omitted when at least one ``--attach`` is given: both
    providers accept a message made only of attachments.

    The session keeps its currently stored agent settings (model, effort,
    permission mode, ...). To change settings, use
    ``twicc update-session <ID> settings`` — or the UI.

    When the caller is itself a TwiCC session, the recipient receives the
    text under a sender header (a single ":: message from <relation> session
    <id> (\"**<title>**\")" line, then the text) identifying the calling session
    and its spawn-tree relation to the recipient
    (spawned/parent/sibling/another).

    Asynchronous by default: a "sent" status only means the message was
    handed to the agent — not that the agent has finished processing it.

    Pass --wait-reply to keep going until the session answers and get that
    answer back with the result, in one call. It waits for the turn THIS
    message triggers: the reply is matched against a cursor the server reads
    when the agent takes the message, so the previous turn's closing message
    is not returned in its place. That is the way to collect an answer; reach
    for "twicc process <SESSION_ID> wait <STATE>..." only to ask whether a
    session is still running, not what it said.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.bootstrap_local import load_local_bootstrap
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import (
        emit_final,
        emit_validation_errors,
    )
    from twicc.cli._drop_request.prompt import resolve_prompt, PromptError
    from twicc.cli._drop_request.sender_header import prefix_sender_header
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.create_session.command import DEFAULT_REPLY_TIMEOUT_SECONDS
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error
    from twicc.providers.helpers import get_provider_helpers

    # Refused rather than ignored, and through the structured payload like
    # every other local check: an MCP client echoing the documented default is
    # exactly who trips the first one, and it parses `errors`, not stderr.
    wait_errors: list[ValidationError] = []
    if not wait_reply:
        for flag, given in (("--reply-timeout", reply_timeout is not None),
                            ("--no-reply-text", no_reply_text)):
            if given:
                wait_errors.append(ValidationError(
                    flag, "requires_wait_reply", f"{flag} requires --wait-reply.",
                ))
    if reply_timeout is not None and reply_timeout <= 0:
        wait_errors.append(ValidationError(
            "--reply-timeout", "invalid_value",
            f"--reply-timeout must be > 0 (got {reply_timeout:g}).",
        ))
    if wait_errors:
        emit_validation_errors(wait_errors)
        raise typer.Exit(1)
    if reply_timeout is None:
        reply_timeout = DEFAULT_REPLY_TIMEOUT_SECONDS

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Identify the calling agent (PID ancestry; MCP sets a forced session id).
    # Used both to resolve the 'parent' keyword and to put a sender header
    # above the message (see sender_header.py) — the recipient is otherwise
    # blind to which session is talking. None for a human invoking the CLI
    # from a plain shell → no header.
    current_session = resolve_current_session()

    # 'parent' keyword: the current session's `spawned_by` field, which is the
    # session that originally created the current one via
    # `twicc create-session`. Done before lookup_session, which expects a real
    # session_id.
    if session_id == "parent":
        if current_session is None:
            emit_validation_errors(
                [ValidationError(
                    "SESSION_ID",
                    "parent_not_found",
                    "No TwiCC session found in the process ancestry. "
                    "Run from inside a TwiCC-spawned agent, or pass an "
                    "explicit session_id.",
                )],
            )
            raise typer.Exit(1)
        if current_session.spawned_by_id is None:
            emit_validation_errors(
                [ValidationError(
                    "SESSION_ID",
                    "parent_not_found",
                    f"Current session {current_session.id!r} has no "
                    f"spawned_by — it was not created via "
                    f"`twicc create-session` from a parent agent. Pass an "
                    f"explicit session_id instead.",
                )],
            )
            raise typer.Exit(1)
        session_id = current_session.spawned_by_id

    # Local pre-check: session must exist, not be stale, and have a project
    # directory. The watcher-side service re-validates these in case the DB
    # state changed between this lookup and the actual send.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors(
            [ValidationError("SESSION_ID", e.code, e.message)],
        )
        raise typer.Exit(1)

    # Resolve the prompt (inline text or file path → text content). Omitting it
    # is only valid when the message carries attachments instead.
    if prompt is None:
        if not attach:
            emit_validation_errors(
                [ValidationError(
                    "PROMPT", "missing_prompt",
                    "PROMPT is required unless the message carries at least "
                    "one --attach file.",
                )],
            )
            raise typer.Exit(1)
        text = ""
    else:
        try:
            text = resolve_prompt(prompt, expand=not no_expand)
        except PromptError as e:
            emit_validation_errors(
                [ValidationError("PROMPT", "invalid_prompt", str(e))],
            )
            raise typer.Exit(1)

    # Put the sender header above the message whenever the caller is itself
    # a TwiCC session, whatever the target — otherwise the recipient would
    # receive an anonymous follow-up from "the user" and have no way to tell
    # another session is talking. No-op for a human caller or a self-send.
    text = prefix_sender_header(
        text,
        current_session,
        recipient_id=resolved.session_id,
        recipient_spawned_by_id=resolved.spawned_by_id,
    )

    # Attachments are validated against the resolved session's provider, with
    # the resize cap derived from its currently stored ``selected_model``
    # (looked up via the helpers). The user has no way to override either
    # here — both come from the existing session.
    bootstrap = load_local_bootstrap()
    helpers_obj = get_provider_helpers(resolved.provider)
    support = (
        bootstrap.providers[resolved.provider].attachment_support
        if resolved.provider in bootstrap.providers else {}
    )

    # Effective model = stored on Session, with synced default as fallback,
    # then provider-specific consistency rules. Mirrors what the backend
    # service will compute (single source of truth: provider helpers).
    from twicc.core.models import Session as SessionModel
    session_row = SessionModel.objects.filter(id=resolved.session_id).first()
    from twicc.providers.helpers import AgentSettings
    stored = AgentSettings(**{
        field: getattr(session_row, field) for field in AgentSettings._fields
    })
    effective_settings = helpers_obj.resolve_agent_settings(stored)
    effective_settings = helpers_obj.enforce_agent_settings_consistency(effective_settings)
    effective_model = effective_settings.selected_model

    errors: list[ValidationError] = []
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

    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Payload — minimum required for the ``send`` kind. The watcher derives
    # provider, project, cwd, and current settings from the DB row.
    payload = {
        "session_id": resolved.session_id,
        "text": text,
        "images": attach_result.images,
        "documents": attach_result.documents,
    }

    sub = transport.submit(payload, kind="session:send_message")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    if wait_reply and outcome.status == "sent":
        # The cursor comes from the server, read the instant the agent took the
        # message. Anything past it was written afterwards, which is what keeps
        # the previous turn's closing message from answering for this one.
        from twicc.cli._output import emit_json
        from twicc.cli._drop_request.output import build_final
        from twicc.cli._wait_reply import wait_for_reply_or_degrade

        final = build_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
        final["reply"] = wait_for_reply_or_degrade(
            final.get("session_id"),
            since_line_num=final.get("last_line") or 0,
            timeout=reply_timeout,
            want_text=not no_reply_text,
        )
        emit_json(final)
        raise typer.Exit(0)

    emit_final(
        outcome,
        request_uuid=sub.request_uuid,
        timeout=timeout,
    )

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
