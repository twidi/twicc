"""
Background task for periodic Codex command discovery.

Codex commands are surfaced as **skills** under the ``$`` activation
prefix. Every 5 minutes the task asks the Codex app-server for the
current skill catalogue (``skills/list`` JSON-RPC, fed with the cwds
of every active Codex project so repo-scoped skills come back too),
maps each skill onto a ``Command`` row, and hands the desired state
to the shared diff/apply helper.

Each cycle waits for ``plugin/reconcile`` before reading skills. Codex
loads remote plugins asynchronously at startup, so an immediate skills
read can return an incomplete catalogue. A failed reconciliation leaves
the saved commands unchanged until the next cycle.

The mapping is centralized in :func:`_skill_to_desired_entry`; in
particular:

- ``scope=Repo``: the row is anchored to the project whose cwd
  matched the entry — ``project_id`` non-NULL.
- ``scope=User``: ``project_id=NULL`` (global). Plugin-shipped
  skills (whose ``name`` carries a ``<plugin>:`` namespace) populate
  ``plugin_name``.
- ``scope=System`` / ``scope=Admin``: ``project_id=NULL`` and
  ``is_builtin=True`` (these are runtime-shipped catalogues, the same
  picker affordance as Claude's CLI built-ins).
- Disabled skills are skipped — they have no place in a "what can I
  run" picker.

User / System / Admin skills appear in **every** ``SkillsListEntry``
(they're independent of the cwd), so duplicates are absorbed by the
"first occurrence wins" rule when populating the desired dict.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Stop event for the commands sync task
_stop_event: asyncio.Event | None = None

# Interval: 5 minutes
SYNC_INTERVAL = 5 * 60

# Activation character used by Codex to invoke skills.
ACTIVATION_CHAR = "$"


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event for the commands sync task."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_commands_task() -> None:
    """Signal the commands sync task to stop."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()


def _skill_to_desired_entry(skill, project_id_for_repo: str | None) -> tuple[tuple[str | None, str], dict] | None:
    """Translate one Codex ``SkillMetadata`` into a ``(key, fields)`` pair.

    Returns ``None`` for skills that should not be persisted (currently
    only ``enabled=False`` rows).

    ``project_id_for_repo`` is the project id whose cwd matched the
    enclosing ``SkillsListEntry`` — used only when ``scope=Repo``;
    other scopes always land as global rows.
    """
    if not skill.enabled:
        return None

    scope = skill.scope.value  # 'user' | 'repo' | 'system' | 'admin'

    # Project anchoring: only ``Repo`` skills are tied to a specific
    # cwd; everything else is global by definition.
    if scope == "repo":
        project_id = project_id_for_repo
        # Defensive: if we couldn't map the cwd back to a known project
        # (e.g. the SkillsListEntry came back for a directory we no
        # longer have in DB), drop the row rather than guess.
        if project_id is None:
            return None
    else:
        project_id = None

    # ``System`` and ``Admin`` skills are runtime-shipped — the picker
    # tags them ``(built-in)`` the same way it tags Claude's CLI
    # commands.
    is_builtin = scope in ("system", "admin")

    # ``name`` may carry a ``<plugin>:<skill>`` namespace; split it so
    # the picker can show the plugin tag separately and the inserted
    # text is just ``$<skill>``.
    raw_name = skill.name
    if ":" in raw_name:
        plugin_name, name = raw_name.split(":", 1)
    else:
        plugin_name, name = None, raw_name

    # Description preference: the curated short blurb from the SKILL.json
    # interface first, then the legacy SKILL.md ``short_description``,
    # falling back to the LLM-targeted long description (better than
    # showing nothing).
    interface = skill.interface
    description = (
        (interface.short_description if interface else None)
        or skill.short_description
        or skill.description
    )

    fields = {
        "plugin_name": plugin_name,
        "description": description,
        "argument_hint": None,  # Codex skills don't carry argument hints
        "is_builtin": is_builtin,
        "is_workflow": False,  # workflows are a Claude-Code-only concept
    }
    return (project_id, name), fields


def _build_desired_from_response(resp, cwd_to_project_id: dict[str, str]) -> dict[tuple[str | None, str], dict]:
    """Walk a ``SkillsListResponse`` and produce the desired-state dict.

    ``cwd_to_project_id`` maps an absolute cwd to the project id we
    want to anchor that entry's ``Repo``-scoped skills against.
    """
    desired: dict[tuple[str | None, str], dict] = {}
    for entry in resp.data:
        # Log any per-cwd scan errors so they don't go silent, but keep
        # iterating — the rest of the entry's skills are still useful.
        if entry.errors:
            logger.warning(
                "skills/list returned %d error(s) for cwd %s: %s",
                len(entry.errors), entry.cwd, entry.errors,
            )
        project_id = cwd_to_project_id.get(entry.cwd)
        for skill in entry.skills:
            translated = _skill_to_desired_entry(skill, project_id)
            if translated is None:
                continue
            key, fields = translated
            # First occurrence wins. User / System / Admin skills are
            # echoed by every cwd entry, so this naturally dedupes them.
            desired.setdefault(key, fields)
    return desired


async def _sync_to_database() -> dict[str, int]:
    """Run one discovery + reconcile cycle.

    Producer side: read the (non-stale) Codex projects, ask the app-server
    for its current skill catalogue, translate skills into the desired-state
    map. The DB write (diff + delete + create + update) goes through the
    DB writer via :class:`_ApplyDesiredCommandsJob`, so this task
    never races the DB writer on the SQLite write lock.

    Returns the stats dict the DB writer's apply produced.
    """
    # Local imports keep the module importable without Django apps
    # being ready (the task is scheduled by the orchestrator after
    # startup, but the module itself may be imported earlier).
    from asgiref.sync import sync_to_async

    from openai_codex.async_client import AsyncCodexClient
    from openai_codex.generated.v2_all import PluginReconcileResponse, SkillsListResponse

    from twicc.core.enums import Provider
    from twicc.core.models import Project
    from twicc.providers.codex.bin import make_codex_config
    from twicc.providers.db_writer import _ApplyDesiredCommandsJob, submit_async_job

    # --- 1. Pick every Codex project we can scan ---
    projects = await sync_to_async(list)(
        Project.objects.filter(directory__isnull=False, stale=False)
        .values_list("id", "directory")
    )
    cwd_to_project_id: dict[str, str] = {}
    for project_id, directory in projects:
        # Resolve to an absolute path so the comparison against
        # ``SkillsListEntry.cwd`` (which the app-server returns
        # canonicalized) lines up.
        resolved = str(Path(directory).resolve())
        cwd_to_project_id[resolved] = project_id

    cwds = list(cwd_to_project_id.keys())

    # --- 2. Ask the app-server for the current skill catalogue ---
    # A throwaway client per cycle is fine at the 5-min cadence and
    # avoids us holding a stdio pipe open between syncs.
    # ``cwd`` here is the app-server's own working directory, not a
    # skill scan path — those are passed via ``cwds``. ``Path.home()``
    # is a safe neutral choice that exists on every platform.
    config = await make_codex_config(cwd=str(Path.home()))

    async with AsyncCodexClient(config=config) as client:
        await client.initialize()
        # initialize() does not wait for remote plugins. Reconcile their
        # bundles and installed inventory before replacing the saved catalogue.
        reconciliation = await client.request(
            "plugin/reconcile",
            {"reason": "twicc-command-discovery"},
            response_model=PluginReconcileResponse,
        )
        if reconciliation.failed_remote_plugin_ids or reconciliation.failed_materialization_remote_plugin_ids:
            raise RuntimeError(
                "Remote plugin reconciliation failed; keeping the saved command catalogue. "
                f"Failed plugins: {reconciliation.failed_remote_plugin_ids}; "
                f"failed bundles: {reconciliation.failed_materialization_remote_plugin_ids}"
            )
        resp = await client.request(
            "skills/list",
            {"cwds": cwds, "forceReload": True},
            response_model=SkillsListResponse,
        )

    # --- 3. Translate the response into the desired state ---
    desired = _build_desired_from_response(resp, cwd_to_project_id)

    # --- 4. Reconcile against the database (via the DB writer) ---
    future = asyncio.get_running_loop().create_future()
    return await submit_async_job(_ApplyDesiredCommandsJob(
        provider=Provider.CODEX,
        activation_char=ACTIVATION_CHAR,
        desired=desired,
        future=future,
    ))


async def start_commands_task() -> None:
    """Background task that periodically discovers and syncs Codex skills.

    Runs until the stop event is set:
    - Syncs immediately on startup
    - Then waits ``SYNC_INTERVAL`` before the next sync
    - Handles graceful shutdown via the stop event
    """
    stop_event = get_stop_event()
    # Reset for hot-restart support: clear the stop event so a relaunched
    # task isn't killed by a stale set() left by the prior shutdown.
    stop_event.clear()

    logger.info("Codex commands sync task started")

    while not stop_event.is_set():
        try:
            stats = await _sync_to_database()
            if stats["created"] or stats["updated"] or stats["deleted"]:
                logger.info(
                    "Codex commands sync: %d created, %d updated, %d deleted, %d unchanged",
                    stats["created"], stats["updated"], stats["deleted"], stats["unchanged"],
                )
            else:
                logger.debug(
                    "Codex commands sync: %d unchanged",
                    stats["unchanged"],
                )
        except Exception as e:
            logger.error("Codex commands sync failed: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SYNC_INTERVAL)
        except TimeoutError:
            pass

    logger.info("Codex commands sync task stopped")
