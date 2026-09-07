"""Create a new agent session from a generic payload.

Called by both ``WSConsumer._handle_send_message`` (when the front-end sends
``send_message``) and ``DropRequestsWatcher`` (when the CLI drops a
request file with ``kind="session:create"``). Centralises validation,
project resolution, pending-settings stashing, and agent-manager
invocation so both entry points stay in sync.

The function does NOT raise for business-rule errors (missing project,
disabled provider, etc.); it returns a :class:`SessionCreationResult` with
``success=False`` and a list of structured error dicts. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async

from twicc.agent.system_prompt import compose_addendum
from twicc.agent import ephemeral as ephemeral_runs
from twicc.agent.exceptions import SendDeliveryError
from twicc.core.enums import Provider
from twicc.pending_agent_settings import set_pending_agent_settings
from twicc.pending_session_attributes import set_pending_session_attributes
from twicc.pending_titles import set_pending_title
from twicc.projects import register_project
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


class SessionCreationError(NamedTuple):
    field: str
    code: str
    message: str


class SessionCreationResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionCreationError] | None


async def create_session_from_payload(
    payload: dict, *, allow_hybrid: bool = False, allow_ephemeral: bool = False,
    ephemeral_admission=None,
) -> SessionCreationResult:
    """Admit ephemeral creation before any asynchronous operation or buffer write."""
    session_id = payload.get("session_id")
    ephemeral = allow_ephemeral and bool(payload.get("ephemeral"))
    try:
        ephemeral_runs.check_readonly(session_id, ephemeral_admission, creation=True)
    except SendDeliveryError as exc:
        return SessionCreationResult(False, None, None, None, [SessionCreationError("session", exc.code, str(exc))])
    if ephemeral_admission is None and isinstance(session_id, str) and session_id:
        ephemeral_admission = ephemeral_runs.reserve(
            session_id, str(payload.get("provider") or ""), str(payload.get("project_id") or ""), ephemeral=ephemeral,
        )
    result = None
    try:
        if ephemeral and session_id:
            from twicc.core.models import Session
            if await sync_to_async(Session.objects.filter(pk=session_id).exists)():
                if ephemeral_admission is not None:
                    try:
                        await ephemeral_runs.finish(ephemeral_admission, failed=True)
                    finally:
                        ephemeral_runs.release(ephemeral_admission)
                return SessionCreationResult(False, None, None, None, [SessionCreationError(
                    "session", "ephemeral_existing_session", "An existing session cannot become ephemeral.",
                )])
        result = await _create_session_from_payload(
            payload, allow_hybrid=allow_hybrid, ephemeral=ephemeral,
            ephemeral_admission=ephemeral_admission,
        )
        return result
    finally:
        if ephemeral:
            if isinstance(session_id, str):
                ephemeral_runs.drain_buffers(session_id)
        await ephemeral_runs.finish(ephemeral_admission, failed=result is None or not result.success)


async def _create_session_from_payload(
    payload: dict, *, allow_hybrid: bool = False, ephemeral: bool = False,
    ephemeral_admission=None,
) -> SessionCreationResult:
    """Create a new session from a normalised payload.

    ``allow_hybrid`` is a TRUSTED keyword-only switch: only the WS handler
    (human web UI) passes ``True``. This service is also the entry point for
    ``session:create`` drop-request files, which any agent can write — a
    ``"hybrid": true`` key in the raw payload is therefore ignored unless the
    caller is trusted (hybrid mode is human-only, like project trust).

    Expected keys in ``payload``:
    - ``session_id``: client-supplied UUID (used as Claude Code session id;
      Codex mints its own and the canonical id is returned).
    - ``project_id``: project identifier (must exist in DB unless
      ``directory`` is also provided — see next entry).
    - ``directory``: optional. When present (CLI flow), the server creates
      the Project here via :func:`twicc.projects.register_project` if it
      doesn't exist yet. This is what makes ``project_added`` and
      ``workspaces_updated`` broadcasts originate from the main process,
      where they can reach connected UI clients live (the CLI runs in a
      separate process and can't broadcast itself). UI flow omits this
      key — the Project is then required to exist already.
    - ``provider``: string value of ``Provider`` enum.
    - ``text``: non-empty for new sessions.
    - ``title``: optional, max 200 chars.
    - ``images``, ``documents``: lists of SDK block dicts (already validated
      by the caller — the service does not re-validate attachments).
    - ``worktree_branch``, ``worktree_path``, ``worktree_start_from``:
      optional (CLI only). When ``worktree_branch`` is set, a new git
      worktree of the source project is created at ``worktree_path`` and the
      session is retargeted at it (``project_id``/``directory`` then name the
      source repo). ``worktree_start_from`` defaults to the source HEAD.
    - Plus all six ``AgentSettings`` fields (``None`` = use synced default).
    """
    # --- payload extraction (defensive, no schema validation) ----
    session_id = payload.get("session_id")
    project_id = payload.get("project_id")
    directory_hint = payload.get("directory")
    provider_str = payload.get("provider")
    text = (payload.get("text") or "").strip()
    title = payload.get("title")
    images = payload.get("images") or []
    documents = payload.get("documents") or []
    hidden = bool(payload.get("hidden", False))
    mute_on_user_turn = payload.get("mute_on_user_turn") is True
    hybrid = bool(payload.get("hybrid")) if allow_hybrid else False
    if ephemeral:
        conflict = None
        if payload.get("hybrid"):
            conflict = ("ephemeral_hybrid_conflict", "Ephemeral sessions cannot use hybrid mode.")
        elif payload.get("worktree_branch") or payload.get("worktree_path"):
            conflict = ("ephemeral_worktree_unsupported", "Create the project worktree before starting an ephemeral session.")
        elif payload.get("spawned_by_session_id") is not None:
            conflict = ("ephemeral_spawn_unsupported", "Ephemeral sessions cannot be spawned sessions.")
        elif provider_str == Provider.CODEX.value:
            from twicc.providers.codex.agent.hardcoded_commands import parse_hardcoded_command
            if parse_hardcoded_command(text) is not None:
                conflict = ("ephemeral_command_unsupported", "An ephemeral Codex session requires a plain prompt.")
        if conflict is not None:
            return SessionCreationResult(False, None, None, None, [SessionCreationError("ephemeral", *conflict)])
    if hybrid:
        # Hybrid mode is a feature-flagged capability (default OFF). Refuse to
        # mint a new already-hybrid session when the flag is unset — the agent
        # factory would refuse to launch it anyway (see ClaudeCodeAgentManager.
        # _create_agent), so reject early with a clear error.
        from django.conf import settings as django_settings

        if not django_settings.CLAUDE_HYBRID_ENABLED:
            return SessionCreationResult(
                False, None, None, None,
                [SessionCreationError("hybrid", "hybrid_disabled", "Hybrid mode is disabled on this server")],
            )
    spawned_by_session_id = payload.get("spawned_by_session_id")  # str | None
    annotations = payload.get("annotations", {})
    if annotations is None:
        annotations = {}
    # Dockable-layout intention to freeze onto the new row. The web UI draft
    # carries its resolved/seeded layout here; a CLI session omits it, and we
    # resolve the project/global default below (just before stashing).
    layout = payload.get("layout")

    errors: list[SessionCreationError] = []
    if not session_id:
        errors.append(SessionCreationError("session_id", "missing", "session_id is required"))
    if not project_id:
        errors.append(SessionCreationError("project_id", "missing", "project_id is required"))
    if not provider_str:
        errors.append(SessionCreationError("provider", "missing", "provider is required"))
    if not text:
        errors.append(SessionCreationError("text", "empty_text", "text is required for a new session"))
    annotations_error = _validate_annotations(annotations)
    if annotations_error is not None:
        errors.append(annotations_error)
    if errors:
        return SessionCreationResult(False, None, None, None, errors)

    # --- provider resolution ---------------------------------------
    try:
        provider = Provider(provider_str)
    except ValueError:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "unknown_provider", f"Unknown provider: {provider_str}")
        ])

    # --- runtime gate ----------------------------------------------
    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "provider_disabled", str(e))
        ])

    # --- project resolution / creation ---------------------------
    # CLI flow (``directory`` provided): get-or-create the Project here so
    # ``project_added`` + workspace auto-add broadcasts fire from the main
    # process. ``register_project`` also adopts the directory if the row
    # existed but had none yet (typical of claude_code/initial_sync rows
    # before background compute filled in the cwd).
    #
    # UI flow (no ``directory`` in payload): the Project is expected to
    # exist in DB already (created via ``POST /api/projects/`` or detected
    # by the sessions watcher).
    from twicc.core.models import Project

    # Worktree flow (CLI ``create-session``): land the session in a worktree of
    # the source project — either a NEW one (``--worktree-branch``, created
    # first) or an EXISTING one adopted in place (``--worktree-path`` alone) —
    # then retarget the session at the resulting worktree project. Only the CLI
    # sends these keys; the UI uses ``POST /api/projects/<id>/worktrees/`` (or
    # ``.../adopt``) and opens a draft against the result. The worktree inherits
    # the source's agent defaults / trust through ``worktree_of``, so the
    # settings the CLI resolved against the source still apply.
    worktree_branch = (payload.get("worktree_branch") or "").strip()
    worktree_path = (payload.get("worktree_path") or "").strip()
    if worktree_branch or worktree_path:
        from twicc.core.services.worktree_creation import (
            adopt_existing_worktree,
            create_worktree_from_source,
        )
        if worktree_branch:
            wt_result = await create_worktree_from_source(
                source_project_id=project_id,
                source_directory=directory_hint,
                path=worktree_path,
                branch=worktree_branch,
                start_from=payload.get("worktree_start_from"),
            )
        else:
            wt_result = await adopt_existing_worktree(
                source_project_id=project_id,
                source_directory=directory_hint,
                path=worktree_path,
            )
        if not wt_result.success:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError(e.field, e.code, e.message)
                for e in (wt_result.errors or [])
            ])
        project = wt_result.project
        project_id = wt_result.project_id
    elif directory_hint:
        # ``register_project`` is the only direct DB write in this
        # service before the manager handoff — everything else here is
        # reads or in-memory caches. (``manager.create_session`` below
        # does perform DB writes itself, e.g. ``ProcessRun`` creation
        # and session-row updates inside ``BaseAgentManager._register_
        # and_start``; those will be wired separately in WIRE#3.) Wrap
        # it under
        # the DB write lock so the project create/adopt + the
        # ``project_added`` / workspace-auto-add broadcasts that follow
        # serialise FIFO with every other DB writer (watcher, DB-writer
        # consumer, etc.). The lock is NOT held across
        # ``manager.create_session`` below — that call boots the SDK
        # and can take seconds; holding the lock through it would
        # stall every other writer. Reentrance (LOCK#2) makes this
        # safe even if a future caller of the service wraps it under
        # the lock too.
        project, _ = await run_under_db_write_lock(
            lambda pid=project_id, d=directory_hint: register_project(pid, directory=d)
        )
    else:
        try:
            project = await sync_to_async(Project.objects.get)(id=project_id)
        except Project.DoesNotExist:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError("project_id", "project_not_found",
                                      f"Project {project_id!r} not found")
            ])

    cwd = project.directory
    if not cwd:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("project_id", "project_no_directory",
                                  f"Project {project_id!r} has no directory set")
        ])

    # --- spawned tree validation ---------------------------------
    # A forged or stale ID would later raise IntegrityError on the FK
    # constraint when the watcher creates the Session row. Fail loudly
    # now with a clear error rather than a downstream stack trace. When a
    # normal top-level session spawns its first child, initialize its own
    # spawn_root to itself so the whole tree can be queried by one indexed FK.
    spawn_root_session_id = None
    if spawned_by_session_id is not None:
        spawn_root_session_id = await run_under_db_write_lock(
            lambda: sync_to_async(_resolve_or_initialize_spawn_root_session_id)(
                spawned_by_session_id
            )
        )
        if spawn_root_session_id is None:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError(
                    "spawned_by_session_id", "invalid_spawned_by",
                    f"spawned_by session {spawned_by_session_id!r} does not exist",
                )
            ])

    # --- build agent settings from the closed bundle --------------
    # NOTE: this reads every AgentSettings field — including those listed in
    # ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` — because this service is also
    # the CLI drop-file entry point, and the CLI is a legitimate backend-side
    # source that can set hidden fields (e.g. ``--no-question-widget``). The
    # WS path strips hidden fields upstream via
    # ``agent_settings_kwargs_from_frontend_payload`` before calling here.
    agent_settings = AgentSettings(**{
        field: payload.get(field) for field in AgentSettings._fields
    })

    # --- resolve provider helpers (needed for title validation and
    #     agent-settings resolution below) -------------------------
    helpers = get_provider_helpers(provider)

    # --- title --------------------------------------------------------
    if title is not None:
        title_result = helpers.validate_title(title)
        if title_result.error:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError("title", "invalid_title", title_result.error)
            ])
        if not ephemeral:
            set_pending_title(session_id, title_result.title)

    # --- stash agent settings (consumed by the watcher when it creates
    #     the Session row from the JSONL) ---------------------------
    set_pending_agent_settings(session_id, agent_settings)

    # --- resolve to effective settings: None -> global synced default --
    effective = helpers.resolve_agent_settings(agent_settings)
    # enforce_agent_settings_consistency RETURNS an AgentSettings (may be
    # the same instance if no demotion was needed, or a fresh one via
    # _replace). Capture it.
    effective = helpers.enforce_agent_settings_consistency(effective)

    # --- hidden constraints (defence in depth) -------------------
    # The CLI validates these before writing the drop-file; we re-validate
    # from the payload because the drop-file is a trust boundary (forged
    # or version-skewed callers can submit invalid combinations).
    from twicc.cli._drop_request.validation import validate_hidden_constraints
    hidden_errors = validate_hidden_constraints(
        provider.value, effective, hidden=hidden,
    )
    if hidden_errors:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError(e.field, e.code, e.message) for e in hidden_errors
        ])

    # --- compose the TwiCC system-prompt addendum --------------------
    # Frozen at creation time and persisted on the row by the watcher.
    # See ``Session.system_prompt_addendum``: the cache key on Anthropic /
    # OpenAI requires the system prompt to be byte-stable across resumes,
    # so this snapshot is what every subsequent ``--append-system-prompt``
    # / ``developer_instructions`` replay will use verbatim.
    addendum_session_id = session_id if provider == Provider.CLAUDE_CODE else None

    def _build_addendum() -> str:
        from twicc.core.models import Session as SessionModel

        spawned_by_project_id: str | None = None
        spawned_by_title: str | None = None
        if spawned_by_session_id:
            parent = (
                SessionModel.objects
                .filter(id=spawned_by_session_id)
                .only("project_id", "title")
                .first()
            )
            if parent is not None:
                spawned_by_project_id = parent.project_id
                # May still be None: a parent spawned moments ago has no title
                # yet (it is derived from its own first user message).
                spawned_by_title = parent.title

        return compose_addendum(
            provider=provider.value,
            project_id=project_id,
            resolved_settings=effective,
            session_id=addendum_session_id,
            started_at=datetime.now(UTC),
            spawned_by_id=spawned_by_session_id,
            spawned_by_title=spawned_by_title,
            spawned_by_project_id=spawned_by_project_id,
            hidden=hidden,
            annotations=annotations,
            **({"ephemeral": True} if ephemeral else {}),
        )

    system_prompt_addendum = await sync_to_async(_build_addendum)()

    # No layout in the payload (CLI path) → resolve the inherited project/global default to freeze.
    if ephemeral:
        layout = {}
    elif not isinstance(layout, dict):
        from twicc.project_layout_default import resolve_project_layout_default
        layout = await sync_to_async(resolve_project_layout_default)(project_id, directory=directory_hint)

    set_pending_session_attributes(
        session_id,
        hidden=hidden,
        mute_on_user_turn=mute_on_user_turn,
        spawned_by_id=spawned_by_session_id,
        spawn_root_id=spawn_root_session_id,
        annotations=annotations,
        system_prompt_addendum=system_prompt_addendum,
        hybrid=hybrid,
        layout=layout,
        ephemeral=ephemeral,
    )

    # --- invoke the agent manager --------------------------------
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    try:
        canonical_id = await manager.create_session(
            session_id, project_id, cwd, text,
            settings=effective, images=images, documents=documents,
            ephemeral_admission=ephemeral_admission,
            **({"ephemeral": True} if ephemeral else {}),
        )
    except RuntimeError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("session", getattr(e, "code", "manager_busy"), str(e))
        ])

    return SessionCreationResult(
        success=True,
        session_id=canonical_id,
        provider=provider.value,
        project_id=project_id,
        errors=None,
    )


def _validate_annotations(annotations: object) -> SessionCreationError | None:
    if not isinstance(annotations, dict):
        return SessionCreationError(
            "annotations",
            "invalid_annotations",
            "annotations must be a JSON object.",
        )
    try:
        orjson.dumps(annotations)
    except TypeError as e:
        return SessionCreationError(
            "annotations",
            "invalid_annotations",
            f"annotations must be JSON-serializable: {e}",
        )
    return None


def _resolve_or_initialize_spawn_root_session_id(spawned_by_session_id: str) -> str | None:
    """Return the spawner's root id and initialize it when absent.

    Synchronous by design; async callers must run this through
    ``sync_to_async``. The helper writes the spawner row when it becomes
    the root of a spawned-session tree, so callers should hold the DB
    write lock.
    """
    from twicc.core.models import Session

    spawned_by_session = (
        Session.objects
        .filter(pk=spawned_by_session_id)
        .values("id", "spawn_root_id")
        .first()
    )
    if spawned_by_session is None:
        return None

    root_id = spawned_by_session["spawn_root_id"] or spawned_by_session["id"]
    if spawned_by_session["spawn_root_id"] is None:
        Session.objects.filter(
            pk=spawned_by_session["id"],
            spawn_root__isnull=True,
        ).update(spawn_root_id=spawned_by_session["id"])
    return root_id
