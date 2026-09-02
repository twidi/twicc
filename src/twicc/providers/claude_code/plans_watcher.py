"""Watcher for Claude Code plan files.

Claude Code writes one markdown plan per session under
``<claude home>/plans/<slug>.md`` (``~/.claude`` or the configured
``CLAUDE_CONFIG_DIR``; the slug is :attr:`Session.slug`). ``has_plan``
presence is **not** monotonic: deleting the plan file flips it back — by
design. The watcher additionally mirrors every transition into
:attr:`Session.plan_paths` (the ``claude_plan``-sourced entry consumed by the
Plan tab), where deletion only flips the entry's ``exists`` flag: the tab
keeps listing the (gone) document alongside any detected plan docs.

The watcher keeps an in-memory ``set`` of plan slugs (file stems) currently
present in the directory. ``serialize_session`` reads it in O(1) via the
provider helper ``session_has_plan`` (``slug in set``). On every transition it
broadcasts over the ``updates`` group so an already-open session view reveals
or hides its Plan tab and an open Plan pane reloads:

- ``plan_available`` — a plan file appeared (false->true)
- ``plan_changed``   — an existing plan file's content changed
- ``plan_gone``      — a plan file was deleted (true->false)

Each broadcast carries the affected ``session_id``(s). The watcher resolves the
file stem to sessions via :attr:`Session.slug` — the one place this provider
watcher touches the ORM. Unlike :class:`twicc.artifacts_watcher.ArtifactsWatcher`
(whose directory *is* the session id), the plan file is named by slug, so a
pure-filesystem session mapping is impossible.

This is the Claude Code *detection* half of an otherwise provider-agnostic Plan
tab: a future provider plugs in its own watcher plus ``resolve_plan_path`` /
``session_has_plan`` without touching the serializer or the frontend.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC
from pathlib import Path

from channels.layers import get_channel_layer
from watchfiles import awatch

from twicc.provider_homes import claude_plans_dir

logger = logging.getLogger(__name__)

_PLAN_SUFFIX = ".md"


class ClaudeCodePlansWatcher:
    def __init__(self) -> None:
        # Resolved at instantiation (the watcher is created lazily by
        # ``get_watcher()``), never at import: the home is per instance.
        self.directory: Path = claude_plans_dir()
        self._slugs: set[str] = set()
        self._stop = asyncio.Event()
        # True only while the watch loop is live and ``_slugs`` is an accurate
        # mirror of the directory. False in any process that never ran the
        # watcher (the standalone CLI, background compute) and during the boot
        # scan / after ``stop()`` — there the in-memory set is empty or stale, so
        # ``session_has_plan`` reads the disk directly instead of trusting it.
        self._active = False

    # ------------------------------------------------------------------
    # Read API — used by ClaudeCodeHelpers.session_has_plan (sync, O(1)).
    # ------------------------------------------------------------------
    def has_slug(self, slug: str) -> bool:
        return slug in self._slugs

    def is_active(self) -> bool:
        """Whether the watch loop is live and ``_slugs`` can be trusted.

        When False, callers must fall back to an on-disk check (see
        ``ClaudeCodeHelpers.session_has_plan``).
        """
        return self._active

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Reset state in case this is a hot-restart (the provider was toggled off
        # then back on): ``stop()`` set the event and left the slug set populated.
        # Clear both so awatch doesn't exit immediately and the boot scan rebuilds
        # presence from disk (dropping plans deleted while the provider was off).
        self._stop.clear()
        self._active = False
        self._slugs.clear()
        # Start awatch FIRST so plans written during the boot scan are not
        # missed, then take the initial snapshot.
        watch_task = asyncio.ensure_future(self._watch_loop())
        await asyncio.to_thread(self._scan_existing)
        # Snapshot ready and the loop is live: ``_slugs`` can now be trusted.
        self._active = True
        try:
            await watch_task
        except asyncio.CancelledError:  # noqa: TRY203 — explicit: cancellation propagates
            raise

    def stop(self) -> None:
        self._stop.set()
        self._active = False

    # ------------------------------------------------------------------
    # Boot snapshot — populate the set, no broadcast (nobody is listening
    # for a transition yet; the serializer carries the initial state).
    # ------------------------------------------------------------------
    def _scan_existing(self) -> None:
        try:
            entries = list(self.directory.iterdir())
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.suffix == _PLAN_SUFFIX and entry.is_file():
                self._slugs.add(entry.stem)
        logger.info("[ClaudeCodePlansWatcher] boot snapshot: %d plan(s)", len(self._slugs))

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------
    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            # Collect the plan slugs touched this tick (only ``*.md`` files
            # directly under the watched dir — the plans dir is flat).
            touched: set[str] = set()
            for _change_type, raw_path in changes:
                slug = self._slug_for(raw_path)
                if slug is not None:
                    touched.add(slug)
            for slug in touched:
                # One bad tick (transient DB error in the plan_paths latch,
                # channel-layer hiccup) must not kill live plan detection.
                try:
                    await self._reconcile(slug)
                except Exception:
                    logger.exception("[ClaudeCodePlansWatcher] reconcile failed for %s", slug)

    def _slug_for(self, raw_path: str) -> str | None:
        """Return the plan slug for a changed path, or ``None`` if irrelevant."""
        p = Path(raw_path)
        if p.suffix != _PLAN_SUFFIX:
            return None
        try:
            rel = p.relative_to(self.directory)
        except ValueError:
            return None
        if len(rel.parts) != 1:  # nested file — not a top-level plan
            return None
        return p.stem

    async def _reconcile(self, slug: str) -> None:
        """Re-stat the slug's file and broadcast the resulting transition."""
        plan_file = self.directory / f"{slug}{_PLAN_SUFFIX}"
        present = await asyncio.to_thread(plan_file.is_file)
        was = slug in self._slugs
        if present and not was:
            self._slugs.add(slug)
            logger.info("[ClaudeCodePlansWatcher] plan appeared: %s", slug)
            await self._latch_plan_paths(slug, "write")
            await self._broadcast(slug, "plan_available")
        elif not present and was:
            self._slugs.discard(slug)
            logger.info("[ClaudeCodePlansWatcher] plan removed: %s", slug)
            await self._latch_plan_paths(slug, "delete")
            await self._broadcast(slug, "plan_gone")
        elif present and was:
            await self._latch_plan_paths(slug, "write")
            await self._broadcast(slug, "plan_changed")
        # not present and not was: transient flicker (e.g. an editor's temp
        # file) — nothing to do.

    # ------------------------------------------------------------------
    # Session.plan_paths latch — mirror the native plan file into the
    # session's plan-doc list, so the Plan tab appears/refreshes live
    # without waiting for a JSONL line (the batch recompute seeds the same
    # entry via ``extra_doc_edit_events``).
    # ------------------------------------------------------------------
    async def _latch_plan_paths(self, slug: str, action: str) -> None:
        from asgiref.sync import sync_to_async

        from twicc.providers.plan_docs import DocEditEvent, apply_doc_edit_events

        plan_file = self.directory / f"{slug}{_PLAN_SUFFIX}"
        if action == "write":
            def _stat_mtime() -> float | None:
                try:
                    return plan_file.stat().st_mtime
                except OSError:
                    return None

            mtime = await asyncio.to_thread(_stat_mtime)
            if mtime is None:
                # Deleted between the reconcile stat and now — the follow-up
                # "gone" tick will flip ``exists``.
                return
            timestamp = datetime.fromtimestamp(mtime, tz=UTC)
        else:
            timestamp = datetime.now(tz=UTC)

        event = DocEditEvent(str(plan_file), action, "claude_plan")

        @sync_to_async
        def _apply() -> list:
            from twicc.core.enums import Provider
            from twicc.core.models import Session, SessionType

            # Fresh read-fold-write, one session at a time: the event only
            # touches its own ``claude_plan`` entry, preserving ``detected``
            # entries a concurrent JSONL sync batch may have written (the
            # symmetric fold lives in the compute paths). The plans path is
            # outside any project, so no project_root relativization.
            updated = []
            sessions = Session.objects.filter(
                provider=Provider.CLAUDE_CODE.value, slug=slug, type=SessionType.SESSION,
            )
            for session in sessions:
                new_entries, changed = apply_doc_edit_events(
                    session.plan_paths, [(event, timestamp)], project_root=None,
                )
                if changed:
                    session.plan_paths = new_entries
                    session.save(update_fields=["plan_paths"])
                    updated.append(session)
            return updated

        updated_sessions = await _apply()
        if not updated_sessions:
            return
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        from twicc.core.serializers import serialize_session
        from twicc.providers.sessions_watcher import broadcast_message

        for session in updated_sessions:
            if session.hidden:
                continue
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

    # ------------------------------------------------------------------
    # Broadcasts — resolve the slug to its session(s) and notify each.
    # ------------------------------------------------------------------
    async def _broadcast(self, slug: str, message_type: str) -> None:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        for session_id in await self._sessions_for_slug(slug):
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {"type": message_type, "session_id": session_id},
                },
            )

    @staticmethod
    async def _sessions_for_slug(slug: str) -> list[str]:
        from asgiref.sync import sync_to_async

        from twicc.core.enums import Provider
        from twicc.core.models import Session

        @sync_to_async
        def _query() -> list[str]:
            return list(
                Session.objects.filter(
                    provider=Provider.CLAUDE_CODE.value, slug=slug
                ).values_list("id", flat=True)
            )

        return await _query()


_watcher_instance: ClaudeCodePlansWatcher | None = None


def get_claude_code_plans_watcher() -> ClaudeCodePlansWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = ClaudeCodePlansWatcher()
    return _watcher_instance
