"""The session payload every CLI command emits.

Order, shared by every caller: ``serialize_session`` (unchanged, shared with the
UI) → this enrichment → the reduced projection when asked → the ``process``
block. Design: docs/plans/2026-09-23-cli-consistency-before-cutover-design.md, §2.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Keys this enrichment adds that ``serialize_session`` never emits. The
#: ``sessions get`` placeholder carries them too, ``null``.
CLI_ENRICHED_KEYS: tuple[str, ...] = (
    "project_directory", "scratch_dir", "orchestration_scratch_dir", "question_widget",
)


def cli_session_payloads(sessions: Iterable) -> list[dict]:
    """Serialize and enrich ``sessions`` (``Session`` rows), in input order.

    One query at most, for the project directories the cache lacks.
    ``artifacts_dir`` is always the path to write to. (``has_artifacts`` says
    whether it holds anything only when the backend runs the command — MCP,
    RPC; in a terminal process the artifacts watcher never started and it is
    always ``False``, ``artifacts_watcher.session_has_artifacts``.) Agent
    settings are the effective values on a session row (stored value, else the
    current global default; ``question_widget`` ``null`` means enabled) and the
    stored values on a subagent row, which runs inside its parent's process.
    """
    from twicc.core.serializers import serialize_session
    from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir
    from twicc.projects import project_directories_cached
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    sessions = list(sessions)
    directories = project_directories_cached(s.project_id for s in sessions if s.project_id)
    payloads = []
    for session in sessions:
        data = serialize_session(session)
        data["project_directory"] = directories.get(session.project_id) if session.project_id else None
        data["artifacts_dir"] = str(get_session_artifacts_dir(session.id))
        data["scratch_dir"] = str(get_session_scratch_dir(session.id))
        data["orchestration_scratch_dir"] = (session.annotations or {}).get("scratch_dir") or None
        settings = AgentSettings.from_session(session)
        if session.parent_session_id is None:
            settings = get_provider_helpers(session.provider).resolve_agent_settings(settings)
            if settings.question_widget is None:
                settings = settings._replace(question_widget=True)
        data.update(settings._asdict())
        payloads.append(data)
    return payloads
