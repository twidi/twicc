"""``twicc session <ID> pending-requests`` and ``twicc session <ID> answer``.

The first reports what a session's live agent is waiting on; the second
answers the one question among it that TwiCC can answer from outside the web
UI. Both ride the drop-request transport, so both share the same failure
surface: exit 2 with no backend, 3 on a server rejection, 4 on a service
failure, 5 on a timeout.

Design: ``docs/plans/2026-09-18-question-cli-design.md``.
"""

from __future__ import annotations

import typer


ANSWER_ACTIONS = ("answer", "cancel")


def _setup_django() -> None:
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()


def parse_answers(raw_answers: list[str]) -> dict[str, list[str]]:
    """Turn repeated ``--answer ID=VALUE`` into ``{id: [value, …]}``.

    Split on the **first** ``=``: a value may contain more, which is reachable
    since free text is accepted. An argument with no ``=`` at all is the error.
    Repeating an id is how a multi-select question is answered.

    Raises :class:`ValueError` with a caller-facing message.
    """
    answers: dict[str, list[str]] = {}
    for item in raw_answers:
        question_id, separator, value = item.partition("=")
        if not separator or not question_id:
            raise ValueError(
                f"Invalid --answer {item!r}: expected ID=VALUE "
                "(the id comes from 'session <ID> pending-requests')"
            )
        answers.setdefault(question_id, []).append(value)
    return answers


def _with_caller(payload: dict) -> dict:
    """Stamp the calling session, the one input the self-answer rule reads.

    Best-effort on the CLI — a guardrail, not a security boundary, exactly like
    the share commands. Over MCP the same resolution reads an id pinned from the
    connection's signed token, which is the surface an agent actually uses.
    """
    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    if current is not None:
        payload["caller_session_id"] = current.id
    return payload


def _run(payload: dict, *, kind: str, success_status: str, timeout: int) -> None:
    """Submit, wait, emit, and map the outcome onto the shared exit codes."""
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.output import emit_final

    sub = transport.submit(payload, kind=kind)
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == success_status:
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def _prepare(session_id: str, timeout: int):
    """Shared preflight: a live backend, a sane timeout, a targetable session."""
    _setup_django()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.session_lookup import SessionLookupError, lookup_session
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error

    if timeout <= 0:
        emit_error("Error: --timeout must be a positive number of seconds.", code=1)

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    try:
        return lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)


def read_cmd(session_id: str, *, raw: bool, timeout: int) -> None:
    """Report every pending request of the session's live agent."""
    resolved = _prepare(session_id, timeout)

    payload: dict = {"session_id": resolved.session_id}
    if raw:
        payload["raw"] = True

    _run(payload, kind="session:pending_requests", success_status="fetched", timeout=timeout)


def answer_cmd(session_id: str, action: str, *, request_id: str | None,
               raw_answers: list[str], timeout: int) -> None:
    """Answer (or decline) the session's pending question."""
    from twicc.cli._output import emit_error

    if action not in ANSWER_ACTIONS:
        # A positional argument, so Typer accepts it and this body refuses it.
        emit_error(
            f"Error: unknown action {action!r}; expected one of "
            f"{', '.join(ANSWER_ACTIONS)}.",
            code=1,
        )

    try:
        answers = parse_answers(raw_answers)
    except ValueError as e:
        emit_error(f"Error: {e}", code=1)

    resolved = _prepare(session_id, timeout)

    payload: dict = {"session_id": resolved.session_id, "action": action}
    if request_id:
        payload["request_id"] = request_id
    if answers:
        payload["answers"] = answers

    _run(_with_caller(payload), kind="session:answer_pending_question",
         success_status="updated", timeout=timeout)
