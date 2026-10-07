"""Top-level ``twicc peer-send`` command (write, drop-request)."""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_strings import PEER_ATTACH_HELP, PEER_TIMEOUT_HELP

# The wait of a message without files; a message with files waits PEER_SEND_TIMEOUT_WITH_FILES.
PEER_SEND_DEFAULT_TIMEOUT = 30


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
    attach: list[str] = typer.Option([], "--attach", help=PEER_ATTACH_HELP),
    timeout: int | None = typer.Option(None, "--timeout", help=PEER_TIMEOUT_HELP),
) -> None:
    """Send a titled message to a peer TwiCC instance.

    Every send carries a required TITLE — the subject the remote user triages
    on. No confirmation on this side — but delivery requires the REMOTE user's
    approval: the returned peer_status stays "pending" until they deliver it
    to an agent, mark it done (dealt with themselves), or refuse it. Re-check
    later with "twicc peer-message <MESSAGE_ID>".

    Files travel inline to the peer: send one message with large files per
    tool call. A 50 MB send can take minutes, and a tool call times out after
    10 minutes.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import attach_sources, transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import build_final, emit_validation_errors
    from twicc.cli._drop_request.prompt import PromptError, resolve_prompt
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error, emit_json
    from twicc.core.models import Peer, PeerMessage, PeerState
    from twicc.core.services.attachments.inline import PEER_SEND_TIMEOUT_WITH_FILES, PEER_TOO_LARGE_HINT
    from twicc.core.services.peer_messages import validate_reply_to, validate_title
    from twicc.core.services.peer_tokens import mint_message_id, peer_credentials_are_active

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

    # Every file travels inline on the peer wire, paths included: all count toward the
    # 50 MB limit, checked before anything is copied.
    sources, attach_errors = attach_sources.resolve(attach or [], hint=PEER_TOO_LARGE_HINT, count_paths=True)
    if attach_errors:
        emit_validation_errors(attach_errors)
        raise typer.Exit(1)

    # Minted here, so every output of a submitted send can name the message, a timeout included.
    message_id = mint_message_id()
    payload = {
        "peer": peer_row.id,
        "title": clean_title,
        "reply_to": clean_reply_to,
        "text": text,
        "message_id": message_id,
    }
    # Origin: best-effort identity of the calling session — the MCP dispatcher
    # sets the forced_session_id ContextVar on every tool call, and a real CLI
    # subprocess resolves via PID ancestry.
    current_session = resolve_current_session()
    if current_session is not None:
        payload["origin_session_id"] = current_session.id

    kind = "peer:send"
    if sources:
        refs, stage_errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
        if stage_errors:
            emit_validation_errors(stage_errors)
            raise typer.Exit(1)
        payload["attachments"] = attach_sources.as_payload(refs)
        # A kind of its own: an older backend refuses it instead of sending the text alone.
        kind = "peer:send_attachments"
    if timeout is None:
        timeout = PEER_SEND_TIMEOUT_WITH_FILES if sources else PEER_SEND_DEFAULT_TIMEOUT

    sub = transport.submit(payload, kind=kind)
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    final = build_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
    # A ``sent`` status names the stored id (an older backend mints its own for a text-only
    # send). Every other outcome names the id minted here, so the caller can check it with
    # ``peer-message`` before sending again.
    if not final.get("message_id"):
        final["message_id"] = message_id
    if not final.get("peer_id"):
        final["peer_id"] = peer_row.id
    emit_json(final)

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
