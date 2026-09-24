"""``twicc whoami`` — identify the session that owns the calling process."""

from __future__ import annotations

import typer

from twicc.cli._output import (
    WHOAMI_FULL_HELP, WHOAMI_SLIM_HELP, emit_error, emit_json, listing_cutover_passed, slim_notice,
)


def whoami_cmd(
    slim: bool = typer.Option(False, "--slim", help=WHOAMI_SLIM_HELP),
    full: bool = typer.Option(False, "--full", help=WHOAMI_FULL_HELP),
) -> None:
    """Print details of the session that owns the calling process.

    Walks the PID ancestry from the current process upward and matches
    against the live agents tracked by TwiCC.

    With ``--slim`` or ``--full`` — and from the cutover without a flag — it
    prints the ``session self`` payload: the session row (reduced, or in full
    with ``--full``) with its ``process`` block inside, built by
    :func:`twicc.cli.session.build_session_payload`.

    Without a flag, until the cutover, it prints its historical object:
    ``session_id``, ``title``, ``project_id``, ``project_directory``,
    ``current_working_directory`` (resolved from tool_use paths, may differ
    from ``project_directory`` when the agent works in a worktree or other
    repo), ``artifacts_dir`` and ``scratch_dir`` (the session's own working
    directories, already joined with the session id),
    ``orchestration_scratch_dir`` (the shared scratch folder, present only when
    the session is part of an orchestration tree), the resolved
    ``agent_settings``, the ``session`` sub-object (the serializer payload,
    without the CLI enrichment ``session <ID>`` adds), and the matching
    ``process`` row with nine fields (``provider``, ``session_id``,
    ``session_title`` and ``project_id`` on top of the compact block's five).

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # First: the refusal wins outside a session and records no notice.
    if slim and full:
        emit_error("Error: --slim and --full are mutually exclusive.", code=2)

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

    # Read before slim_notice, which only returns a boolean.
    legacy = not slim and not full and not listing_cutover_passed()
    slim = slim_notice("whoami", slim, full, kind="whoami")
    if not legacy:
        from twicc.cli.session import build_session_payload

        emit_json(build_session_payload(session, slim=slim))
        return

    # The historical object, unchanged until the cutover.
    from twicc.agent.states import AgentState
    from twicc.cli._process_state import (
        serialize_dead_process_row,
        serialize_process_row,
    )
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Project
    from twicc.core.serializers import serialize_session
    from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir
    from twicc.pending_titles import get_pending_title
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    helpers = get_provider_helpers(session.provider)
    resolved_settings = helpers.resolve_agent_settings(AgentSettings.from_session(session))

    project_directory = None
    if session.project_id:
        project = Project.objects.filter(id=session.project_id).only("directory").first()
        if project is not None:
            project_directory = project.directory

    info = resolve_live_twicc_or_exit()
    row = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid, session_id=session.id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )
    if row is not None:
        process = serialize_process_row(row, session)
    else:
        process = serialize_dead_process_row(session, session_id=session.id)

    # Per-session working directories, pre-composed so the agent doesn't have
    # to join the base dir (given in the system-prompt Context block) with its
    # own session id. Both always point at the session's *own* folders —
    # whoami only echoes the session's base data. The shared scratch folder of
    # an orchestration tree (carried by the ``scratch_dir`` annotation) is a
    # distinct concept, surfaced as ``orchestration_scratch_dir`` only when the
    # session is actually part of an orchestration; the key is omitted otherwise.
    orchestration_scratch_dir = (session.annotations or {}).get("scratch_dir")

    data = {
        "session_id": session.id,
        "title": get_pending_title(session.id) or session.title,
        "project_id": session.project_id,
        "project_directory": project_directory,
        "current_working_directory": session.git_directory,
        "artifacts_dir": str(get_session_artifacts_dir(session.id)),
        "scratch_dir": str(get_session_scratch_dir(session.id)),
        **({"orchestration_scratch_dir": orchestration_scratch_dir} if orchestration_scratch_dir else {}),
        "agent_settings": resolved_settings._asdict(),
        "session": serialize_session(session),
        "process": process,
    }
    emit_json(data)
