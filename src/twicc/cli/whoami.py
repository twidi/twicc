"""``twicc whoami`` — identify the session that owns the calling process."""

from __future__ import annotations

import typer

from twicc.cli._output import WHOAMI_FULL_HELP, emit_error, emit_json


def whoami_cmd(
    slim: bool = typer.Option(False, "--slim", hidden=True),
    full: bool = typer.Option(False, "--full", help=WHOAMI_FULL_HELP),
) -> None:
    """Print the ``session self`` payload of the session that owns the calling process.

    Walks the PID ancestry from the current process upward and matches
    against the live agents tracked by TwiCC. It prints the session row
    (reduced by default, in full with ``--full``) with its ``process`` block
    inside, built by :func:`twicc.cli.session.build_session_payload`.

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import resolve_current_session

    session = resolve_current_session()
    if session is None:
        msg = (
            "No TwiCC session found in PID ancestry. whoami is only "
            "meaningful from inside an active agent session."
        )
        typer.echo(msg, err=True)
        raise typer.Exit(1)

    from twicc.cli.session import build_session_payload

    emit_json(build_session_payload(session, slim=not full))
