"""Back orchestration for project trust: ``resolve`` (with seeding) and ``decide``.

Glues the pure resolver (:mod:`twicc.trust`) to the provider-config projection
(:mod:`twicc.providers.claude_code.trust`, :mod:`twicc.providers.codex.trust`)
and the DB. The DB is the source of truth; provider configs are a write-mostly
projection, read once per project at seeding (guarded by ``trust_imported``).
See docs/archives/plans/2026-06-09-project-trust-design.md §5–§6.

Both entry points are async (they do provider IO) and never raise on a provider
write failure — the DB decision stands regardless.
"""

from __future__ import annotations

import asyncio
import logging
import os

from asgiref.sync import sync_to_async

from twicc.providers.db_writer import run_under_db_write_lock
from twicc.trust import TrustResolution, effective_trust, load_trust_rows

logger = logging.getLogger(__name__)

# Keeps strong references to in-flight background projection tasks so they
# are not garbage-collected mid-run (asyncio only holds weak refs).
_projection_tasks: set[asyncio.Task] = set()


def _schedule_projection(coro, label: str) -> None:
    """Run a provider-config projection in the background, never raising.

    Provider writes are best-effort (the DB is the source of truth) and can be
    slow — the Codex write spawns an app-server subprocess. Keeping them out of
    the request path makes ``resolve``/``decide`` respond as soon as the DB is
    settled, so a slow or failing projection can no longer stall or break the
    frontend trust gate (the root of the "session silently seeded untrusted"
    incident).
    """

    async def _run() -> None:
        try:
            await coro
        except Exception:
            logger.exception("Trust projection failed (%s)", label)

    task = asyncio.create_task(_run())
    _projection_tasks.add(task)
    task.add_done_callback(_projection_tasks.discard)


# --- key / git helpers (sync, filesystem) ---------------------------------


def _is_git(directory: str) -> bool:
    from twicc.git import resolve_git_from_path

    return resolve_git_from_path(directory, use_cache=False) is not None


def _codex_root_key(directory: str) -> str:
    """The canonical (realpath) Codex trust key for *directory*.

    = the git root when *directory* is in a repo (the worktree's own root for a
    worktree), else the directory itself.
    """
    from twicc.git import resolve_git_from_path

    resolved = resolve_git_from_path(directory, use_cache=False)
    root = resolved[0] if resolved else directory
    return os.path.realpath(root)


# --- provider IO wrappers -------------------------------------------------


def _claude_write(directory: str, trusted: bool) -> None:
    from twicc.core.enums import Provider
    from twicc.logging_context import provider_log_context
    from twicc.providers.claude_code import trust as claude_trust

    with provider_log_context(Provider.CLAUDE_CODE):
        claude_trust.write_trust(directory, trusted)


async def _codex_write(root_key: str, trusted: bool) -> None:
    from twicc.core.enums import Provider
    from twicc.logging_context import provider_log_context
    from twicc.providers.codex import trust as codex_trust

    with provider_log_context(Provider.CODEX):
        await codex_trust.write_trust(root_key, trusted)


def _claude_clear(directory: str) -> None:
    from twicc.core.enums import Provider
    from twicc.logging_context import provider_log_context
    from twicc.providers.claude_code import trust as claude_trust

    with provider_log_context(Provider.CLAUDE_CODE):
        claude_trust.clear_trust(directory)


async def _codex_clear(root_key: str) -> None:
    from twicc.core.enums import Provider
    from twicc.logging_context import provider_log_context
    from twicc.providers.codex import trust as codex_trust

    with provider_log_context(Provider.CODEX):
        await codex_trust.clear_trust(root_key)


def _seed_read(directory: str) -> bool | None:
    """Read the project's OWN trust from both providers and collapse it.

    Claude: the exact-path entry. Codex: only when the project is itself its own
    git root (otherwise the ``[projects."<root>"]`` entry belongs to an ancestor
    repo, not this project). No walk-up.

    Collapse: *trusted on either → True*. For untrusted we trust only Codex's
    explicit ``"untrusted"`` (a deliberate decline) — a Claude ``false`` is
    treated as noise (the CLI auto-creates ``false`` entries before any decision)
    and ignored, so we don't import bogus declines for projects the user trusts.
    """
    from twicc.core.enums import Provider
    from twicc.logging_context import provider_log_context
    from twicc.providers.claude_code import trust as claude_trust
    from twicc.providers.codex import trust as codex_trust

    with provider_log_context(Provider.CLAUDE_CODE):
        claude = claude_trust.read_trust(directory)
    codex: bool | None = None
    root_key = _codex_root_key(directory)
    if root_key == os.path.realpath(directory):  # P is its own root
        with provider_log_context(Provider.CODEX):
            codex = codex_trust.read_trust(root_key)

    if claude is True or codex is True:
        return True
    if codex is False:  # Codex only ever records a deliberate "untrusted"
        return False
    return None  # a Claude `false` is noise, not a decline


# --- projection / materialization -----------------------------------------


async def _project_decision(directory: str, trusted: bool) -> None:
    """Write an EXPLICIT decision onto the providers (the project's own keys).

    Codex is keyed by the git root, so we only write it when the project IS its
    own root — for a subdir of a repo the entry would belong to (and clobber) the
    repo, and Codex cannot represent a subdir's trust independently anyway (design
    §10). Claude is keyed per-directory, so it always records the decision.
    """
    if not directory:
        return
    await sync_to_async(_claude_write)(directory, trusted)
    key = await sync_to_async(_codex_root_key)(directory)
    if key == os.path.realpath(directory):
        await _codex_write(key, trusted)


async def _clear_project_entries(directory: str) -> None:
    """Remove the project's own provider entries (used when resetting to inherit).

    Same own-root guard as ``_project_decision`` for Codex so we never clear an
    ancestor repo's entry.
    """
    if not directory:
        return
    await sync_to_async(_claude_clear)(directory)
    key = await sync_to_async(_codex_root_key)(directory)
    if key == os.path.realpath(directory):
        await _codex_clear(key)


async def _materialize(target, source_directory: str | None, state: bool) -> None:
    """Write a project's own provider entries for an INHERITED resolution, only
    where the provider would not natively see it as trusted.

    - Claude: only **worktrees** (the walk-up covers ordinary subdirs via the
      explicit ancestor's entry; worktrees are not inherited — security patch).
    - Codex: only when the project's git-root key differs from the source's
      (i.e. a worktree, or a descendant whose trust source sits above its git
      root). An ordinary in-repo subdir shares the repo's git-root entry → skip.
    """
    directory = target.directory
    if not directory:
        return

    if target.worktree_of_id is not None:
        await sync_to_async(_claude_write)(directory, state)

    project_key = await sync_to_async(_codex_root_key)(directory)
    source_key = (
        await sync_to_async(_codex_root_key)(source_directory)
        if source_directory
        else None
    )
    if project_key and project_key != source_key:
        await _codex_write(project_key, state)


# --- broadcast ------------------------------------------------------------


async def _broadcast_project_updated(project_id: str) -> None:
    """Emit a ``project_updated`` WS event so all clients see the new trust."""
    from channels.layers import get_channel_layer

    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    layer = get_channel_layer()
    if layer is None:
        return
    try:
        project = await Project.objects.filter(id=project_id).afirst()
        if project is not None:
            await layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )
    except Exception:
        logger.exception("Failed to broadcast project_updated for %s", project_id)


# --- DB load --------------------------------------------------------------


def _load_resolution(project_id: str):
    """Return ``(target_row, resolution, source_directory)`` (sync DB access)."""
    rows = load_trust_rows()
    by_id = {r.id: r for r in rows}
    target = by_id.get(project_id)
    if target is None:
        return None, None, None
    resolution = effective_trust(target, rows)
    source_directory = None
    if resolution.source_id and resolution.source_id in by_id:
        source_directory = by_id[resolution.source_id].directory
    return target, resolution, source_directory


# --- enforcement (Part 2) ---------------------------------------------------
#
# Sync helpers used by the agent build paths to clamp permission settings in
# untrusted projects. The clamp is the backend security floor: it never trusts
# the frontend (which filters choices for UX only). Unknown trust is treated as
# untrusted — the session-creation gate normally forces a decision first, so an
# unresolved state here means the gate was bypassed.
# See docs/archives/plans/2026-06-09-project-trust-design.md §13.


def project_is_untrusted(project_id: str) -> bool:
    """True when the project's effective trust is NOT trusted (sync, DB reads).

    Pure resolution — no provider-config seeding (the gate already did it).
    """
    from twicc.trust import resolve_project

    return resolve_project(project_id).state is not True


def untrusted_permission_mode_default(provider) -> str:
    """The provider's configured default permission mode for untrusted projects.

    Reads the global synced setting; falls back to the provider's hard default.
    A configured value outside the provider's untrusted-allowed set (stale or
    hand-edited settings.json) is ignored in favor of the hard default.
    """
    from twicc.providers.helpers import get_provider_helpers
    from twicc.synced_settings import read_synced_settings

    helpers = get_provider_helpers(provider)
    key = helpers.UNTRUSTED_PERMISSION_MODE_SYNCED_KEY
    if key is None:
        raise ValueError(f"Provider {provider!r} has no untrusted permission mode")
    value = read_synced_settings().get(key)
    if value in helpers.UNTRUSTED_PERMISSION_MODES:
        return value
    return helpers.SYNCED_SETTINGS_DEFAULTS[key]


def clamp_permission_mode_for_untrusted(provider, permission_mode: str | None) -> str:
    """Clamp a permission mode to the provider's untrusted-allowed set.

    In-set values pass through; anything else (including ``None``) falls back to
    the global untrusted default. Callers apply this only when the project
    resolved untrusted (:func:`project_is_untrusted`).
    """
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers(provider)
    if permission_mode in helpers.UNTRUSTED_PERMISSION_MODES:
        return permission_mode
    clamped = untrusted_permission_mode_default(provider)
    if permission_mode is not None:
        logger.warning(
            "Untrusted project: permission_mode %r clamped to %r (%s)",
            permission_mode, clamped, provider,
        )
    return clamped


# --- public entry points --------------------------------------------------


async def resolve_project_trust(project_id: str) -> dict:
    """Resolve a project's effective trust, seeding from providers if needed.

    Returns ``{state, via, source_id}`` where ``state`` is True / False / None
    (None → the caller should prompt the user). Side effects: on a resolved
    project at its first gate, materializes the provider entries and marks
    ``trust_imported``; on an unresolved-but-seedable project, adopts and
    persists the provider's own decision.
    """
    from twicc.core.models import Project

    target, resolution, source_directory = await sync_to_async(_load_resolution)(
        project_id
    )
    if target is None:
        return {"state": None, "via": "unresolved", "source_id": None}

    # Resolved (self-explicit or inherited).
    if resolution.state is not None:
        # Materialize + mark imported ONLY at the first gate of this project.
        project = await Project.objects.filter(id=project_id).afirst()
        if project is not None and not project.trust_imported:
            _schedule_projection(
                _materialize(target, source_directory, resolution.state),
                f"materialize {project_id}",
            )
            await run_under_db_write_lock(
                lambda: Project.objects.filter(id=project_id).aupdate(
                    trust_imported=True
                )
            )
        return {
            "state": resolution.state,
            "via": resolution.via,
            "source_id": resolution.source_id,
        }

    # Unresolved: seed once from the providers (guarded by trust_imported).
    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return {"state": None, "via": "unresolved", "source_id": None}
    if project.trust_imported:
        return {"state": None, "via": "unresolved", "source_id": None}

    seeded = await sync_to_async(_seed_read)(target.directory)
    if seeded is not None:
        propagation = await sync_to_async(_is_git)(target.directory) if target.directory else False
        await run_under_db_write_lock(
            lambda: Project.objects.filter(id=project_id).aupdate(
                trust=seeded, trust_propagation=propagation, trust_imported=True
            )
        )
        # Reconcile the project's own provider entries to the adopted value.
        _schedule_projection(
            _project_decision(target.directory, seeded),
            f"seed projection {project_id}",
        )
        await _broadcast_project_updated(project_id)
        return {"state": seeded, "via": "seed", "source_id": project_id}

    # Nothing to import → ask. Do NOT mark imported; decide() will.
    return {"state": None, "via": "unresolved", "source_id": None}


async def decide_project_trust(
    project_id: str, trusted: bool | None, propagation: bool | None = None
) -> dict:
    """Persist a trust decision (or reset to inherit) and re-project it.

    ``trusted`` is True / False for an explicit decision, or ``None`` to reset the
    project to inheritance. ``propagation`` defaults to "the project is under git"
    for an explicit decision (ignored on reset).
    """
    from twicc.core.models import Project

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return {"ok": False, "error": "project_not_found"}

    if trusted is None:
        # Reset to inherit: drop the explicit decision (keep trust_imported so we
        # never re-seed), then reconcile the providers to the NEW effective trust —
        # clear the project's own entries, then materialize the inherited value
        # where the providers cannot inherit it (worktrees / above-git-root).
        await run_under_db_write_lock(
            lambda: Project.objects.filter(id=project_id).aupdate(
                trust=None, trust_imported=True
            )
        )

        async def _reset_projection() -> None:
            # Ordered sequence: clear the own entries first, THEN materialize
            # the newly-effective inherited value where providers need it.
            await _clear_project_entries(project.directory)
            target, resolution, source_directory = await sync_to_async(
                _load_resolution
            )(project_id)
            if target is not None and resolution.state is not None:
                await _materialize(target, source_directory, resolution.state)

        _schedule_projection(_reset_projection(), f"reset projection {project_id}")
        await _broadcast_project_updated(project_id)
        return {"ok": True, "state": None}

    if propagation is None:
        propagation = (
            await sync_to_async(_is_git)(project.directory)
            if project.directory
            else False
        )

    await run_under_db_write_lock(
        lambda: Project.objects.filter(id=project_id).aupdate(
            trust=trusted, trust_propagation=bool(propagation), trust_imported=True
        )
    )
    _schedule_projection(
        _project_decision(project.directory, trusted),
        f"decide projection {project_id}",
    )
    await _broadcast_project_updated(project_id)
    return {"ok": True, "state": trusted, "propagation": bool(propagation)}


async def backfill_unimported_trust() -> None:
    """Startup backfill: settle the trust of every not-yet-imported project.

    Until v1.8.0 the provider-config seed only ran lazily, at the first
    session-creation gate of each project — so after the upgrade every
    pre-existing project sat in the "unknown" state (treated as untrusted:
    clamped session seeds, lock indicator) even when the user had long trusted
    it in the Claude/Codex CLIs. This one-shot pass runs the exact same
    resolve+seed path per project at boot, so the existing provider state is
    imported immediately. Idempotent: only ``trust_imported=False`` projects
    are considered; projects with nothing to import stay unresolved (the gate
    will ask) and are re-checked at next boot — the per-project cost is two
    local file reads.
    """
    from twicc.core.models import Project

    ids = await sync_to_async(list)(
        Project.objects.filter(
            trust_imported=False, stale=False, directory__isnull=False,
        ).values_list("id", flat=True)
    )
    if not ids:
        return
    settled = 0
    for project_id in ids:
        try:
            result = await resolve_project_trust(project_id)
            if result.get("state") is not None:
                settled += 1
        except Exception:
            logger.exception("Trust backfill failed for project %s", project_id)
    logger.info(
        "Trust backfill: %d unimported project(s) checked, %d settled",
        len(ids), settled,
    )


__all__ = [
    "TrustResolution",
    "backfill_unimported_trust",
    "clamp_permission_mode_for_untrusted",
    "decide_project_trust",
    "project_is_untrusted",
    "resolve_project_trust",
    "untrusted_permission_mode_default",
]
