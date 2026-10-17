"""Kill an existing session's live agent.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
with ``kind="process:stop"``. Mirrors the ``_handle_kill_process`` branch of
:meth:`twicc.asgi.WSConsumer` (used when the user clicks the *Stop process*
button in the UI): asks the registry to kill the agent attached to the
session with ``reason="manual"`` and lets the manager's normal teardown
broadcast ``process_state`` etc. — there is no DB write to do here.

The function does NOT raise for business-rule errors (missing session,
subagent, provider disabled, etc.); it returns an :class:`UpdateSessionResult`
with ``success=False`` and a list of structured error tuples. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).

The result type :class:`UpdateSessionResult` is reused from
:mod:`twicc.core.services.session_update` because the shape (success +
session/provider/project ids + structured errors) is identical to what
the other CLI services already return.
"""

from __future__ import annotations

import logging

from twicc.core.services.session_update import (
    UpdateSessionError,
    UpdateSessionResult,
    _lookup_session_for_update,
)


logger = logging.getLogger(__name__)


async def kill_session_process_from_payload(payload: dict) -> UpdateSessionResult:
    """Kill the live agent attached to a session.

    Expected keys in ``payload``:
    - ``session_id`` (required).
    - ``force`` (optional): when truthy, hard-kill — SIGKILL the process tree
      now, bypassing the grace window and the manager lock a soft stop may
      hold. Mirrors the WS ``kill_process`` ``force`` branch.

    Idempotent: if no live agent is currently attached to the session, the
    call still succeeds with the standard ``UpdateSessionResult`` (no
    ``no_running_process`` error). This matches the UI's *Stop process*
    behaviour — clicking it twice is harmless.

    Business-rule rejections (returned as ``success=False``):
    - the standard session-lookup guards shared by every other CLI
      service (``session_not_found``, ``is_subagent``,
      ``project_no_directory``, ``unknown_provider``, ``provider_disabled``).
    """
    session_id = payload.get("session_id")

    if not session_id:
        return UpdateSessionResult(False, None, None, None, [
            UpdateSessionError("session_id", "missing", "session_id is required"),
        ])

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    from twicc.agent.registry import get_agent_manager_registry
    registry = get_agent_manager_registry()
    force = bool(payload.get("force"))
    if force:
        killed = await registry.hard_kill_agent(session_id)
    else:
        killed = await registry.kill_agent(session_id, reason="manual")

    logger.info(
        "[kill_session_process] session=%s force=%s killed=%s",
        session_id, force, killed,
    )

    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
    )
