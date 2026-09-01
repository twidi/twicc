"""Top-level ``twicc peer-send`` command (write, drop-request)."""

from __future__ import annotations

import typer


def peer_send_cmd(
    peer: str = typer.Argument(
        ...,
        help="Peer id (peer_...) or its exact local name — see `twicc peers`.",
    ),
    title: str = typer.Argument(
        ...,
        help=(
            "Required subject, shown prominently to the remote user (inbox, "
            "notification, delivery). One line of text (never a file path), "
            "100 characters max — aim shorter, like an email subject."
        ),
    ),
    prompt: str = typer.Argument(
        ...,
        help=(
            "Message text, or path to a file whose content is the message. "
            "The receiving agent shares no memory with this instance — the "
            "message must be fully self-contained."
        ),
    ),
    reply_to: str | None = typer.Option(
        None,
        "--reply-to",
        help=(
            "The peer message this one answers (pm_…), taken from the "
            "header of a delivered peer message."
        ),
    ),
    attach: list[str] = typer.Option(
        [],
        "--attach",
        help=(
            "Path to a file to attach (repeatable): PNG/JPEG/GIF/WebP/PDF/"
            "text/plain up to 5 MB each, max 100 files, 32 MB total. Each "
            "value is either a local file path OR a base64 data URI "
            "(data:<mime>;base64,<data>)."
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
    """Send a titled message to a peer TwiCC instance.

    Every send carries a required TITLE — the subject the remote user triages
    on. No confirmation on this side — but delivery requires the REMOTE user's
    approval: the returned peer_status stays "pending" until they deliver it
    to an agent, mark it done (dealt with themselves), or refuse it. Re-check
    later with "twicc peer-message <MESSAGE_ID>".
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.attachments import (
        AttachmentResizeError,
        validate_and_encode,
    )
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.prompt import PromptError, resolve_prompt
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error
    from twicc.core.models import Peer, PeerMessage, PeerState
    from twicc.core.services.peer_messages import validate_reply_to, validate_title
    from twicc.core.services.peer_tokens import peer_credentials_are_active
    from twicc.providers.helpers import get_provider_helpers

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Local pre-check (the watcher-side service re-validates): peer must exist
    # and be active. Mirrors send-message's lookup_session pre-check UX.
    peer_row = Peer.objects.filter(id=peer).first() or Peer.objects.filter(name=peer).first()
    if peer_row is None:
        emit_error(
            f"No peer matches {peer!r} (by id or exact name). "
            "List available peers with `twicc peers`.",
            code=1,
        )
    if peer_row.state == PeerState.BROKEN:
        emit_error(
            "This peer no longer accepts messages (revoked or unreachable). "
            "Ask your user to check the relationship in Settings › Peers.",
            code=1,
        )
    if peer_row.state != PeerState.ACTIVE:
        emit_error("This peer relationship is still pending — it cannot receive messages yet.", code=1)
    if not peer_credentials_are_active(peer_row):
        emit_error(
            "This peer was paired at another local address. "
            "Ask your user to reconnect it in Settings › Peers.",
            code=1,
        )

    # Same normalization + cap as the watcher-side service (which re-validates):
    # flattened to one line, stripped, 100 chars max — over-long is REJECTED,
    # never truncated. TITLE is always inline text, never a file path.
    clean_title, title_error = validate_title(title)
    if title_error is not None:
        emit_validation_errors([ValidationError("TITLE", title_error.code, title_error.message)])
        raise typer.Exit(1)

    # Same grammar check as the watcher-side service (which re-validates),
    # run BEFORE the lookup below. The lookup itself deliberately ignores
    # direction and status: a failed or refused parent is still a valid
    # reply target.
    clean_reply_to, reply_to_error = validate_reply_to(reply_to)
    if reply_to_error is not None:
        emit_validation_errors([ValidationError("--reply-to", reply_to_error.code, reply_to_error.message)])
        raise typer.Exit(1)
    if clean_reply_to and not PeerMessage.objects.filter(
            peer=peer_row, message_id=clean_reply_to).exists():
        emit_validation_errors([ValidationError(
            "--reply-to",
            "unknown_reply_to",
            "No message with this id exists for the selected peer.",
        )])
        raise typer.Exit(1)

    # Peer messages are authored verbatim by the sender — no ``@@`` include
    # expansion (mirrored client-side by ``_NO_EXPAND_PATHS`` in ``_remote.py``).
    try:
        text = resolve_prompt(prompt, expand=False)
    except PromptError as e:
        emit_validation_errors([ValidationError("PROMPT", "invalid_prompt", str(e))])
        raise typer.Exit(1)

    # Attachment validation: peer-send has no target session/provider — the
    # peer wire format reuses the provider-common SDK block shape, and the
    # size/count caps are identical across providers, so claude_code's helpers
    # (the wider mime/document acceptance) stand in. ``helpers_obj`` must be
    # the ClaudeCodeHelpers INSTANCE (validate_and_encode calls its
    # get_effective_image_dimension method); model=None resolves to the
    # default image dimension.
    helpers_obj = get_provider_helpers("claude_code")
    support = helpers_obj.get_attachment_support()

    errors: list[ValidationError] = []
    try:
        attach_result = validate_and_encode(attach or [], support, helpers_obj, None)
    except AttachmentResizeError as e:
        emit_validation_errors([ValidationError(f"--attach {e.path}", "resize_failed", e.message)])
        raise typer.Exit(1)
    for err in attach_result.errors:
        errors.append(ValidationError(f"--attach {err.file}", err.code, err.message))
    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    # Origin: best-effort identity of the calling session — the MCP dispatcher
    # sets the forced_session_id ContextVar on every tool call, and a real CLI
    # subprocess resolves via PID ancestry. Without it the wire session_title
    # is simply null.
    payload = {
        "peer": peer_row.id,
        "title": clean_title,
        "reply_to": clean_reply_to,
        "text": text,
        "images": attach_result.images,
        "documents": attach_result.documents,
    }
    current_session = resolve_current_session()
    if current_session is not None:
        payload["origin_session_id"] = current_session.id

    sub = transport.submit(payload, kind="peer:send")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
