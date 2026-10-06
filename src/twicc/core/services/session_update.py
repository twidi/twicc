"""Apply updates to an existing agent session.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
whose ``kind`` matches one of the supported update actions:

- ``kind="session:update_settings"`` → :func:`update_session_settings_from_payload`.
  Mirrors the settings-only branch of
  :meth:`twicc.asgi.WSConsumer._handle_send_message`: write the new values
  to the ``Session`` row under the DB write lock, broadcast
  ``session_updated`` out of the lock, and propagate to the live agent via
  ``manager.send_to_session(text="")`` when a process is attached.
- ``kind="session:update_title"`` → :func:`update_session_title_from_payload`.
  Mirrors the title branch of ``PATCH /api/projects/.../sessions/<id>/`` in
  :mod:`twicc.views`: validate via the provider's ``validate_title``, write
  under the DB write lock, re-index the full-text search document, ask the
  provider to persist into its backing store (JSONL custom-title entry for
  Claude Code, ``thread/name/set`` for Codex), then broadcast
  ``session_updated``.
- ``kind="session:update_archived"`` → :func:`update_session_archived_from_payload`.
  Wraps the same archive flow the HTTP ``PATCH`` endpoint runs (now
  factored into :func:`apply_session_archived_change`): persist the flag,
  optionally unpin under the ``autoUnpinOnArchive`` synced setting,
  re-index search, kill the live agent + tmux on archive, broadcast.
- ``kind="session:update_pinned"`` → :func:`update_session_pinned_from_payload`.
  Wraps the pin/unpin flow of the HTTP ``PATCH`` endpoint (now factored
  into :func:`apply_session_pinned_change`): write the new ``pinned``
  value (``NULL`` to unpin, or one of ``PinMode.values``) and broadcast.
- ``kind="session:update_mute_on_user_turn"`` →
  :func:`update_session_mute_on_user_turn_from_payload`. Persists the
  per-session finished-working notification gate and broadcasts the update.
- ``kind="session:update_hidden"`` → :func:`update_session_hidden_from_payload`.
  Looks up the session via the standard guards then delegates to
  :func:`session_visibility.hide_session` / ``unhide_session``.
- ``kind="session:update_annotations"`` → :func:`update_session_annotations_from_payload`.
  Applies ordered free-form JSON annotation operations, writes the final
  annotations object under the DB write lock, and broadcasts
  ``session_updated``.

The functions do NOT raise for business-rule errors (missing session,
provider disabled, etc.); they return an :class:`UpdateSessionResult` with
``success=False`` and a list of structured error tuples. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).

:func:`apply_session_archived_change` and :func:`apply_session_pinned_change`
are public helpers, reused by the HTTP ``PATCH`` endpoint (:mod:`twicc.views`)
so the single-session HTTP path and the CLI path stay byte-for-byte
aligned.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any, NamedTuple

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction

from twicc.core.enums import Provider
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)

logger = logging.getLogger(__name__)

_MISSING = object()


class UpdateSessionError(NamedTuple):
    field: str
    code: str
    message: str


class UpdateSessionResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[UpdateSessionError] | None


async def _lookup_session_for_update(
    session_id: str | None,
):
    """Resolve a session for any update operation.

    Returns ``(session, project, provider, None)`` on success, or
    ``(None, None, None, error_result)`` where ``error_result`` is the
    :class:`UpdateSessionResult` the caller must return as-is. Centralises
    the same business-rule guards used by every ``update_*`` service so a
    new sub-command (title, archive, pin, stop, ...) only writes its
    action-specific logic on top.
    """
    from twicc.core.models import Session, SessionType

    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_not_found",
                                f"Session {session_id!r} not found"),
        ])
    if session.type == SessionType.SUBAGENT:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "is_subagent",
                                f"Session {session_id!r} is a subagent; "
                                "subagents cannot be updated directly. "
                                "Target the parent session instead."),
        ])
    if session.stale:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "session_stale",
                                f"Session {session_id!r} is stale "
                                "(its JSONL file no longer exists on disk)"),
        ])

    project = session.project
    if project is None or not project.directory:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "project_no_directory",
                                f"Session {session_id!r} has no project directory"),
        ])

    try:
        provider = Provider(session.provider)
    except ValueError:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "unknown_provider",
                                f"Session {session_id!r} has unknown provider "
                                f"{session.provider!r}"),
        ])

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return None, None, None, UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("provider", "provider_disabled", str(e)),
        ])

    return session, project, provider, None


@transaction.atomic
def persist_session_settings(session_id: str, updates: dict) -> None:
    """Write settings and order widget snapshots under the DB writer lease."""
    from twicc.core.models import Session

    previous = None
    if "question_widget" in updates:
        previous = Session.objects.filter(id=session_id).values("provider", "question_widget").first()
    Session.objects.filter(id=session_id).update(**updates)
    if previous is not None and previous["provider"] == Provider.CODEX:
        from twicc.core.services.async_questions import refresh_question_widget_snapshot

        refresh_question_widget_snapshot(session_id, previous_enabled=previous["question_widget"] is not False)


async def update_session_settings_from_payload(payload: dict) -> UpdateSessionResult:
    """Apply a partial or full settings update to an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the target session (required).
    - ``updates``: dict mapping AgentSettings field name → new value. A key
      present in the dict means "write this value" (including ``None``,
      which resets to "use the synced default"). A key absent from the
      dict means "do not touch this field".
    - ``replace_all``: bool — informational only (telemetry / log). True
      means the CLI assembled the dict from a preset replacement; False
      means a patch from individual flags / ``--unset``.

    Business-rule rejections (returned as ``success=False``):
    - ``empty_updates``: ``updates`` is missing or empty.
    - ``invalid_field``: ``updates`` contains a key that is not an
      AgentSettings field.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service (``session_not_found``, ``is_subagent``,
      ``session_stale``, ``project_no_directory``, ``unknown_provider``,
      ``provider_disabled``).
    """
    session_id = payload.get("session_id")
    updates: dict[str, Any] = payload.get("updates") or {}
    replace_all = bool(payload.get("replace_all"))

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if not updates:
        errors.append(UpdateSessionError("updates", "empty_updates",
                                          "updates dict is empty; nothing to write"))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    invalid_keys = [k for k in updates if k not in AgentSettings._fields]
    if invalid_keys:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError(
                "updates", "invalid_field",
                f"Unknown AgentSettings field(s): {sorted(invalid_keys)}",
            ),
        ])

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    # --- Hidden invariants guard (defence in depth) --------------------
    # The CLI pre-validates this too; the server re-checks because the
    # drop file is a trust boundary — a direct write or a race condition
    # could bypass the CLI guard.
    if session.hidden:
        from twicc.cli._drop_request.validation import validate_hidden_constraints
        # Build the merged settings: current DB values overlaid with the
        # incoming updates so the check reflects what would actually be written.
        merged_base = AgentSettings.from_session(session)._asdict()
        merged_base.update(updates)
        merged_settings = AgentSettings(**merged_base)
        helpers_for_hidden = get_provider_helpers(provider)
        resolved_for_hidden = helpers_for_hidden.resolve_agent_settings(merged_settings)
        resolved_for_hidden = helpers_for_hidden.enforce_agent_settings_consistency(resolved_for_hidden)
        hidden_errors = validate_hidden_constraints(
            provider.value, resolved_for_hidden, hidden=True,
        )
        if hidden_errors:
            return UpdateSessionResult(False, None, None, None, [
                UpdateSessionError(e.field, e.code, e.message)
                for e in hidden_errors
            ])

    # --- DB write under the write lock ---------------------------------
    # Mirrors the path taken by ``WSConsumer._handle_send_message`` for the
    # settings-only update: persist under the lock; reload + broadcast
    # outside the lock so other writers don't queue behind the broadcast.
    await run_under_db_write_lock(
        lambda: sync_to_async(persist_session_settings)(session_id, updates)
    )

    updated_session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()

    channel_layer = get_channel_layer()
    if updated_session is not None and channel_layer is not None and not updated_session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(updated_session),
                },
            },
        )

    logger.info(
        "[update_session_settings] session=%s replace_all=%s fields=%s",
        session_id, replace_all, sorted(updates.keys()),
    )

    # --- propagate to the live agent if one is attached ----------------
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    if manager.get_agent_info(session_id) is None:
        # No live agent: the DB is the source of truth and the next session
        # resume will pick up the new settings naturally.
        return UpdateSessionResult(
            success=True,
            session_id=session_id,
            provider=provider.value,
            project_id=session.project_id,
            errors=None,
        )

    helpers = get_provider_helpers(provider)
    # Rehydrate from the freshly reloaded row so the manager sees exactly
    # what the DB now contains (including fields untouched by this update).
    agent_settings = AgentSettings.from_session(updated_session or session)
    effective = helpers.resolve_agent_settings(agent_settings)
    effective = helpers.enforce_agent_settings_consistency(effective)

    try:
        await manager.send_to_session(
            session_id, session.project_id, project.directory, "",
            settings=effective, images=None, documents=None,
        )
    except RuntimeError as e:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session", "manager_busy", str(e)),
        ])

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


async def update_session_title_from_payload(payload: dict) -> UpdateSessionResult:
    """Set a new title on an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the target session (required).
    - ``title``: new title string (required). Validated via the provider's
      ``validate_title`` (trim, non-empty, ≤ ``MAX_TITLE_LENGTH``).

    Business-rule rejections (returned as ``success=False``):
    - ``invalid_title``: ``validate_title`` rejected the trimmed value.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service.
    """
    session_id = payload.get("session_id")
    raw_title = payload.get("title")

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if raw_title is None:
        errors.append(UpdateSessionError("title", "missing", "title is required"))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    helpers = get_provider_helpers(provider)
    validation = helpers.validate_title(raw_title)
    if validation.error:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("title", "invalid_title", validation.error),
        ])
    title = validation.title

    # --- DB write under the write lock ---------------------------------
    # Confirming unchanged text also validates the title as a user choice.
    session.title = title
    session.title_origin = "user"
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["title", "title_origin"])
    )

    # --- search reindex (non-critical) ---------------------------------
    # The title is part of the full-text search document; if reindex fails,
    # the next full-search rebuild catches up. Don't fail the whole update.
    from twicc import search
    if search.is_initialized():
        try:
            await asyncio.to_thread(search.reindex_session, session_id)
        except Exception:
            logger.warning(
                "[update_session_title] search reindex failed for %s",
                session_id, exc_info=True,
            )

    # --- provider rename (non-critical) --------------------------------
    # Claude Code appends a ``custom-title`` JSONL entry and protects it
    # against stale CLI re-appends; Codex calls ``thread/name/set``. The
    # DB is already updated, so a transient failure here is logged and
    # the watcher / next resume reconciles.
    try:
        await helpers.rename_session(session_id, title)
    except Exception:
        logger.warning(
            "[update_session_title] provider rename_session failed for %s",
            session_id, exc_info=True,
        )

    # --- broadcast ----------------------------------------------------
    # Always broadcast explicitly even though the provider rename usually
    # triggers a file-watcher broadcast too: the CLI needs a deterministic
    # outcome and the broadcast is idempotent for UI consumers.
    from twicc.core.serializers import serialize_session
    channel_layer = get_channel_layer()
    if channel_layer is not None and not session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )

    logger.info("[update_session_title] session=%s title=%r", session_id, title)

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


async def apply_session_archived_change(
    session,
    archived: bool,
    *,
    also_unpin: bool = False,
) -> None:
    """Persist an ``archived`` change on a :class:`Session` row.

    Single source of truth for the archive flow, shared by the HTTP
    ``PATCH /api/projects/.../sessions/<id>/`` endpoint and the CLI
    ``twicc update-session <ID> archive`` / ``twicc update-sessions archive``
    sub-commands. Performs:

    1. DB write under the lock — ``archived`` (and ``pinned=None`` when
       ``also_unpin`` is set) saved via ``Session.asave``.
    2. Full-text search reindex (non-critical; logged on failure since the
       ``archived`` flag is denormalised into every indexed document).
    3. When ``archived`` flips to ``True``: kill the live agent
       (``reason="archived"``), drop the session's surviving DEAD
       :class:`ProcessRun` rows, and tear down any tmux terminal attached to
       this session id.

    Does NOT broadcast ``session_updated``; the caller is responsible so
    a payload combining several field updates (e.g. archived + pinned at
    once) only emits one broadcast carrying the final row state.

    ``also_unpin`` is the decision the caller already made by checking
    ``autoUnpinOnArchive`` against ``session.pinned``; the helper applies
    it without re-checking — matching the way the frontend builds the
    PATCH body for the HTTP endpoint.
    """
    from twicc import search
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.terminal import kill_all_tmux_terminals

    was_archived = session.archived
    fields = ["archived"]
    session.archived = archived
    if also_unpin:
        session.pinned = None
        fields.append("pinned")

    await run_under_db_write_lock(
        lambda: session.asave(update_fields=fields)
    )

    # Search reindex — non-critical because the next full rebuild reconciles
    # if this attempt fails. The ``archived`` flag is denormalised into every
    # document, so we re-index the whole session.
    if search.is_initialized():
        try:
            await asyncio.to_thread(search.reindex_session, session.id)
        except Exception:
            logger.warning(
                "[apply_session_archived_change] search reindex failed for %s",
                session.id, exc_info=True,
            )

    # Tear down anything tied to a "live" session when flipping to archived.
    if archived:
        await get_agent_manager_registry().kill_agent(session.id, reason="archived")
        await _drop_dead_process_runs(session.id)
        await asyncio.to_thread(kill_all_tmux_terminals, f"s:{session.id}")
        if not was_archived:
            from twicc.title_auto_task import request_title_check

            request_title_check(session.id, closing=True)


async def _drop_dead_process_runs(session_id: str) -> None:
    """Delete the session's DEAD :class:`ProcessRun` rows (crons cascade).

    Archiving is a deliberate stop: nothing attached to the session may come
    back to life. Killing the live agent covers the running case — the DEAD
    transition drops its row, see
    :meth:`ClaudeCodeHelpers.should_keep_dead_process_run`.

    A session that was ALREADY dead needs this pass: Claude Code deliberately
    keeps a DEAD row alive when a session died on its own with crons attached,
    so that the runtime and boot-time restarts can honour them. Left in place,
    the next TwiCC start would relaunch (and auto-unarchive) the session the
    user just put away. Deleting the row cascades its :class:`SessionCron`
    rows, which also drains the runtime restart loop — it re-reads the crons
    from the DB on every attempt and stops when none are left.

    Provider-agnostic: providers that do not keep DEAD rows (the default rule
    deletes them on death) simply have nothing to delete here.
    """
    from twicc.agent import AgentState
    from twicc.core.models import ProcessRun

    try:
        deleted, _ = await run_under_db_write_lock(
            lambda: asyncio.to_thread(
                lambda: ProcessRun.objects.filter(
                    session_id=session_id, state=AgentState.DEAD.value
                ).delete()
            )
        )
    except Exception:
        logger.warning(
            "[apply_session_archived_change] failed to drop dead process runs for %s",
            session_id, exc_info=True,
        )
        return

    if deleted:
        logger.info(
            "[apply_session_archived_change] dropped %d dead process run(s) for %s",
            deleted, session_id,
        )


async def update_session_archived_from_payload(payload: dict) -> UpdateSessionResult:
    """Archive or unarchive an existing session.

    Expected keys in ``payload``:
    - ``session_id`` (required).
    - ``archived``: bool (required). ``True`` archives, ``False`` unarchives.

    The auto-unpin decision is NOT in the payload — the service reads it
    from the synced settings (``autoUnpinOnArchive``) so the CLI applies
    the same rule the UI already respects, with no override flag.

    Business-rule rejections (returned as ``success=False``):
    - ``invalid_archived``: ``archived`` missing or not a bool.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service.
    """
    session_id = payload.get("session_id")
    archived = payload.get("archived")

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if not isinstance(archived, bool):
        errors.append(UpdateSessionError(
            "archived", "invalid_archived",
            "archived must be a boolean (true / false)",
        ))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    # Auto-unpin on archive: read the synced setting and combine with the
    # session's current pin state, exactly like
    # ``frontend/src/stores/data.js`` ``setSessionArchived``.
    from twicc.synced_settings import read_synced_settings
    synced = read_synced_settings()
    auto_unpin_on_archive = bool(synced.get("autoUnpinOnArchive", True))
    also_unpin = archived and bool(session.pinned) and auto_unpin_on_archive

    await apply_session_archived_change(session, archived, also_unpin=also_unpin)

    # Broadcast once, after the helper has applied every side effect.
    from twicc.core.serializers import serialize_session
    channel_layer = get_channel_layer()
    if channel_layer is not None and not session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )

    logger.info(
        "[update_session_archived] session=%s archived=%s also_unpin=%s",
        session_id, archived, also_unpin,
    )

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


async def apply_session_pinned_change(session, pinned) -> None:
    """Persist a ``pinned`` change on a :class:`Session` row.

    Single source of truth for the pin / unpin flow, shared by the HTTP
    ``PATCH /api/projects/.../sessions/<id>/`` endpoint and the CLI
    ``twicc update-session <ID> pin / unpin`` (and ``twicc update-sessions
    pin / unpin``) sub-commands.

    ``pinned`` is ``None`` to unpin, or one of ``PinMode.values``
    (``"project"`` / ``"workspace"`` / ``"all"``). The caller is
    responsible for validating the value (the HTTP endpoint does it
    inline; the CLI service does it before calling here).

    Does NOT broadcast ``session_updated``; the caller emits the broadcast
    so a payload combining several field updates (e.g. archived + pinned
    set together by the UI's archive flow) only fires one frame.
    """
    session.pinned = pinned
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["pinned"])
    )


async def apply_session_mute_on_user_turn_change(session, mute_on_user_turn: bool) -> None:
    """Persist the per-session finished-working notification gate."""
    session.mute_on_user_turn = mute_on_user_turn
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["mute_on_user_turn"])
    )


async def apply_session_layout_change(session, layout: dict) -> None:
    """Persist a ``layout`` change on a :class:`Session` row.

    ``layout`` is the per-session dockable-layout intention — a plain dict
    (``{}`` = single pane). Written by the HTTP ``PATCH
    /api/projects/.../sessions/<id>/`` endpoint, debounced from the frontend
    as the user docks / resizes / loads layouts. The caller validates the
    shape (an object) and emits the ``session_updated`` broadcast so a payload
    combining several field updates fires a single frame.
    """
    session.layout = layout if isinstance(layout, dict) else {}
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["layout"])
    )


async def apply_session_browser_url_change(session, browser_url: str | None) -> None:
    """Persist the Browser pane's last URL (validated by the PATCH endpoint)."""
    session.browser_url = browser_url
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["browser_url"])
    )


async def apply_session_goal_dismissed_change(session, created_at: str) -> str | None:
    """Mark the session's latest goal as dismissed (hide its footer bar).

    ``created_at`` identifies the goal the user actually clicked, guarding
    against a new goal having taken the last slot between render and click.
    Only a **closed** goal (completed or cleared) can be dismissed — an active
    goal keeps its bar (the UI never offers the cross on it, this re-validates
    server-side). Dismissal is one-way; there is no un-dismiss.

    The whole read-modify-write runs on a fresh row under the DB write lock:
    ``Session.goals`` is compute-owned and rewritten by the watcher / the
    recompute, so mutating a possibly stale in-memory copy could resurrect old
    entries (both compute paths re-port the flag itself, see
    ``preserve_dismissed_flags``). The passed ``session`` instance is updated
    in place so the caller's ``session_updated`` broadcast carries the change.

    Returns ``None`` on success (including the already-dismissed no-op), or an
    error string for the caller to surface.
    """
    from twicc.core.models import Session
    from twicc.providers.goals import GOAL_STATE_COMPLETED

    def _dismiss() -> tuple[str | None, list | None]:
        goals = (
            Session.objects.filter(id=session.id)
            .values_list("goals", flat=True)
            .first()
        )
        last = goals[-1] if goals else None
        if not last or last.get("created_at") != created_at:
            return "goal not found (it may have been superseded)", None
        if last.get("state") != GOAL_STATE_COMPLETED and not last.get("cleared"):
            return "an active goal cannot be dismissed", None
        if not last.get("dismissed"):
            last["dismissed"] = True
            Session.objects.filter(id=session.id).update(goals=goals)
        return None, goals

    error, goals = await run_under_db_write_lock(lambda: sync_to_async(_dismiss)())
    if goals is not None:
        session.goals = goals
    return error


async def refresh_session_plan_existence(session) -> bool:
    """Re-probe the on-disk existence of every ``Session.plan_paths`` entry.

    Each entry's ``exists`` flag is computed at write-detection time from the
    ``tool_use`` line — which is logged *before* the tool actually creates the
    file — so a document written exactly once can be recorded as missing and
    stay that way until the next full recompute (which re-probes, but rarely
    runs). This lets the Plan tab trigger a cheap on-demand re-probe when the
    user brings it to the foreground, flipping stale ``missing`` flags back
    live via the caller's ``session_updated`` broadcast.

    Like ``apply_session_goal_dismissed_change``, the whole read-modify-write
    runs on a fresh row under the DB write lock: ``plan_paths`` is compute-owned
    and rewritten concurrently (watcher live-sync, full recompute, plans
    watcher, subagent folds), so mutating a possibly stale in-memory copy could
    clobber entries this pass never saw. ``_plan_doc_roots`` and the
    ``os.path.exists`` probes are sync-only, so they run inside the locked
    thread. The passed ``session`` instance is updated in place so the caller's
    broadcast carries the change.

    Returns ``True`` when at least one entry's ``exists`` flag flipped.
    """
    from twicc.core.models import Session
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.plan_docs import refresh_entries_existence

    def _refresh() -> list | None:
        entries = (
            Session.objects.filter(id=session.id)
            .values_list("plan_paths", flat=True)
            .first()
        )
        if not entries:
            return None
        roots = BaseSessionCompute._plan_doc_roots(session)
        if not refresh_entries_existence(entries, roots):
            return None
        Session.objects.filter(id=session.id).update(plan_paths=entries)
        return entries

    entries = await run_under_db_write_lock(lambda: sync_to_async(_refresh)())
    if entries is None:
        return False
    session.plan_paths = entries
    return True


async def update_session_pinned_from_payload(payload: dict) -> UpdateSessionResult:
    """Pin or unpin an existing session.

    Expected keys in ``payload``:
    - ``session_id`` (required).
    - ``pinned``: ``None`` to unpin, or one of ``PinMode.values``
      (``"project"`` / ``"workspace"`` / ``"all"``).

    Business-rule rejections (returned as ``success=False``):
    - ``invalid_pinned``: ``pinned`` is neither ``None`` nor a valid
      ``PinMode`` value.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service.
    """
    from twicc.core.models import PinMode

    session_id = payload.get("session_id")
    pinned = payload.get("pinned", "<missing>")  # sentinel: missing key

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if pinned == "<missing>":
        errors.append(UpdateSessionError(
            "pinned", "missing",
            "pinned is required (use null to unpin or one of "
            f"{list(PinMode.values)})",
        ))
    elif pinned is not None and pinned not in PinMode.values:
        errors.append(UpdateSessionError(
            "pinned", "invalid_pinned",
            f"pinned must be null or one of {list(PinMode.values)}; got {pinned!r}",
        ))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    await apply_session_pinned_change(session, pinned)

    from twicc.core.serializers import serialize_session
    channel_layer = get_channel_layer()
    if channel_layer is not None and not session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )

    logger.info(
        "[update_session_pinned] session=%s pinned=%r",
        session_id, pinned,
    )

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


async def update_session_mute_on_user_turn_from_payload(payload: dict) -> UpdateSessionResult:
    """Enable or suppress finished-working notifications for one session."""
    session_id = payload.get("session_id")
    mute_on_user_turn = payload.get("mute_on_user_turn", _MISSING)

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if mute_on_user_turn is _MISSING:
        errors.append(UpdateSessionError(
            "mute_on_user_turn",
            "missing",
            "mute_on_user_turn is required",
        ))
    elif not isinstance(mute_on_user_turn, bool):
        errors.append(UpdateSessionError(
            "mute_on_user_turn",
            "invalid_mute_on_user_turn",
            "mute_on_user_turn must be a boolean (true / false)",
        ))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, _project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    await apply_session_mute_on_user_turn_change(session, mute_on_user_turn)

    from twicc.core.serializers import serialize_session

    channel_layer = get_channel_layer()
    if channel_layer is not None and not session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )

    logger.info(
        "[update_session_mute_on_user_turn] session=%s mute_on_user_turn=%s",
        session_id,
        mute_on_user_turn,
    )

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


async def update_session_hidden_from_payload(payload: dict):
    """Drop-file glue for ``kind="session:update_hidden"``.

    Looks up the session via the standard guards (not_found, subagent, stale,
    no_directory, unknown_provider, provider_disabled), then delegates to
    :func:`session_visibility.hide_session` or
    :func:`session_visibility.unhide_session` based on the boolean in the
    payload.

    Returns either an :class:`UpdateSessionResult` (on lookup failure) or a
    :class:`~twicc.core.services.session_visibility.SessionVisibilityResult`
    (on the hide/unhide path). Both are compatible with the watcher's
    attribute access pattern (``success``, ``session_id``, ``provider``,
    ``project_id``, ``errors``).

    Business-rule rejections (returned as ``success=False``):
    - ``missing``: ``session_id`` is absent from the payload.
    - ``invalid_hidden``: ``hidden`` is absent or not a bool.
    - Plus the standard session-lookup guards shared by every
      ``update_*`` service.
    - ``not_top_level``: session is a subagent (re-checked in the
      visibility service; consistent with the lookup guard above).
    - ``permission_mode_not_allowed`` / ``question_widget_enabled``: the
      session's current settings violate the hidden invariants (hide only).
    """
    session_id = payload.get("session_id")
    hidden = payload.get("hidden")

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if not isinstance(hidden, bool):
        errors.append(UpdateSessionError(
            "hidden", "invalid_hidden",
            "hidden must be a boolean (true / false)",
        ))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    from twicc.core.services.session_visibility import hide_session, unhide_session

    if hidden:
        return await hide_session(session)
    return await unhide_session(session)


async def update_session_annotations_from_payload(payload: dict) -> UpdateSessionResult:
    """Apply ordered annotation operations to an existing session.

    Supported structured operations:
    - ``{"op": "clear"}``
    - ``{"op": "replace", "value": {...}}``
    - ``{"op": "merge", "value": {...}}``
    - ``{"op": "set", "path": ["a", "b"], "value": scalar}``
    - ``{"op": "unset", "path": ["a", "b"]}``
    """
    session_id = payload.get("session_id")
    operations = payload.get("operations")

    errors: list[UpdateSessionError] = []
    if not session_id:
        errors.append(UpdateSessionError("session_id", "missing", "session_id is required"))
    if not isinstance(operations, list) or not operations:
        errors.append(UpdateSessionError(
            "operations", "empty_operations",
            "At least one annotation operation is required.",
        ))
    if errors:
        return UpdateSessionResult(False, None, None, None, errors)

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    annotations, operation_errors = apply_annotation_operations(
        session.annotations or {},
        operations,
    )
    if operation_errors:
        return UpdateSessionResult(False, None, None, None, operation_errors)

    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    await run_under_db_write_lock(
        lambda: Session.objects.filter(id=session_id).aupdate(annotations=annotations)
    )

    updated_session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()

    channel_layer = get_channel_layer()
    if updated_session is not None and channel_layer is not None and not updated_session.hidden:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(updated_session),
                },
            },
        )

    logger.info(
        "[update_session_annotations] session=%s operations=%s",
        session_id, len(operations),
    )

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )


def apply_annotation_operations(
    current_annotations: object,
    operations: list,
) -> tuple[dict, list[UpdateSessionError]]:
    """Apply structured annotation operations and return a fresh object."""
    if not isinstance(current_annotations, dict):
        annotations: dict[str, Any] = {}
    else:
        annotations = deepcopy(current_annotations)

    errors: list[UpdateSessionError] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            errors.append(UpdateSessionError(
                "operations", "invalid_annotation_operation",
                f"Operation #{index + 1} must be an object.",
            ))
            continue

        op = None
        try:
            op = operation.get("op")
            if op == "clear":
                annotations = {}
            elif op == "replace":
                value = operation.get("value")
                error = _validate_annotation_object(value, field="operations")
                if error is None:
                    annotations = deepcopy(value)
                else:
                    errors.append(error)
            elif op == "merge":
                value = operation.get("value")
                error = _validate_annotation_object(value, field="operations")
                if error is None:
                    _merge_annotation_object(annotations, value)
                else:
                    errors.append(error)
            elif op == "set":
                path = operation.get("path")
                if _validate_operation_path(path):
                    error = _set_annotation_path(annotations, path, operation.get("value"))
                    if error is not None:
                        errors.append(error)
                else:
                    errors.append(UpdateSessionError(
                        "operations", "invalid_annotation_path",
                        f"Operation #{index + 1} has an invalid annotation path.",
                    ))
            elif op == "unset":
                path = operation.get("path")
                if _validate_operation_path(path):
                    _unset_annotation_path(annotations, path)
                else:
                    errors.append(UpdateSessionError(
                        "operations", "invalid_annotation_path",
                        f"Operation #{index + 1} has an invalid annotation path.",
                    ))
            else:
                errors.append(UpdateSessionError(
                    "operations", "invalid_annotation_operation",
                    f"Operation #{index + 1} has unknown op {op!r}.",
                ))
        except Exception as e:
            errors.append(_annotation_operation_exception(index, op, e))

    if errors:
        return {}, errors

    error = _validate_annotation_object(annotations, field="annotations")
    if error is not None:
        return {}, [error]
    return annotations, []


def _validate_annotation_object(value: object, *, field: str) -> UpdateSessionError | None:
    if not isinstance(value, dict):
        return UpdateSessionError(
            field,
            "invalid_annotations",
            "annotations must be a JSON object.",
        )
    try:
        orjson.dumps(value)
    except TypeError as e:
        return UpdateSessionError(
            field,
            "invalid_annotations",
            f"annotations must be JSON-serializable: {e}",
        )
    return None


def _annotation_operation_exception(
    index: int,
    op: object,
    exception: Exception,
) -> UpdateSessionError:
    return UpdateSessionError(
        "operations",
        "annotation_operation_failed",
        f"Operation #{index + 1} ({op!r}) failed while applying annotations: {exception}",
    )


def _validate_operation_path(path: object) -> bool:
    return (
        isinstance(path, list)
        and bool(path)
        and all(isinstance(segment, str) and segment for segment in path)
    )


def _merge_annotation_object(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_annotation_object(target[key], value)
        else:
            target[key] = deepcopy(value)


def _set_annotation_path(
    annotations: dict,
    path: list[str],
    value: Any,
) -> UpdateSessionError | None:
    current = annotations
    for segment in path[:-1]:
        if segment not in current:
            current[segment] = {}
        elif not isinstance(current[segment], dict):
            return UpdateSessionError(
                "operations",
                "annotation_path_conflict",
                f"Annotation path {'.'.join(path)!r} conflicts with existing scalar value.",
            )
        current = current[segment]

    leaf = path[-1]
    if isinstance(current.get(leaf), dict):
        return UpdateSessionError(
            "operations",
            "annotation_path_conflict",
            f"Annotation path {'.'.join(path)!r} would replace an existing object.",
        )
    current[leaf] = value
    return None


def _unset_annotation_path(annotations: dict, path: list[str]) -> None:
    current = annotations
    for segment in path[:-1]:
        next_value = current.get(segment)
        if not isinstance(next_value, dict):
            return
        current = next_value
    current.pop(path[-1], None)
