"""Watcher for per-session artifact directories.

Tracks which sessions have at least one artifact on disk under
``<data_dir>/artifacts/<session_id>/``. Artifacts are files an agent saves to
surface to the user (screenshots, generated assets, ...). Their presence drives
the session view's *Artifacts* tab.

The watcher keeps an in-memory ``set`` of session ids whose artifact directory
is non-empty. The set is **monotonic**: once a session is known to have
artifacts it stays in the set for the process lifetime — we never hide the
Artifacts tab again once shown (a deliberate design choice). ``serialize_session``
reads the set in O(1) via :func:`session_has_artifacts`, and an
``artifacts_available`` WebSocket message is broadcast on the false->true
transition so an already-open session view reveals its Artifacts tab live.

Once a session is known, subsequent changes under its dir are relayed as
``artifact_files_changed`` messages (carrying the changed paths, relative to
the session artifacts dir) so an open Artifacts tab can live-refresh its tree
and reload a rendered HTML page.

Purely filesystem-driven: it never touches the ORM, so it starts immediately
and runs independently of the initial JSONL sync (mirrors
:class:`twicc.drop_requests_watcher.DropRequestsWatcher`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from channels.layers import get_channel_layer
from watchfiles import awatch

from twicc.paths import get_artifacts_dir, get_session_artifacts_dir
from twicc.uploads.store import is_upload_temp_name

logger = logging.getLogger(__name__)


def _dir_non_empty(path: Path | str) -> bool:
    """True if ``path`` is a directory containing at least one entry."""
    try:
        with os.scandir(path) as it:
            for _ in it:
                return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    return False


class ArtifactsWatcher:
    def __init__(self, enqueue=None) -> None:
        self._enqueue = enqueue
        self.directory = Path(get_artifacts_dir())
        # The watched dir may itself be a symlink (worktree mode points
        # <worktree>/artifacts at the main data dir). awatch can report event
        # paths under either the link path or its canonical target, so we keep
        # both prefixes and try each when extracting the session id. Resolved
        # for real in start() once the path (and any symlink) exists.
        self._roots = [self.directory]
        self._sessions: set[str] = set()
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Read API — used by serialize_session (sync, in-memory, O(1)).
    # ------------------------------------------------------------------
    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Now that the dir (and any symlink) exists, record its canonical path
        # too so _session_rel_for matches event paths reported either way.
        resolved = self.directory.resolve()
        self._roots = [self.directory] if resolved == self.directory else [self.directory, resolved]
        # Start awatch FIRST so artifacts written during the boot scan are not
        # missed, then take the initial snapshot.
        watch_task = asyncio.ensure_future(self._watch_loop())
        await asyncio.to_thread(self._scan_existing)
        try:
            await watch_task
        except asyncio.CancelledError:  # noqa: TRY203 — explicit: cancellation propagates
            raise

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Boot snapshot — populate the set, no broadcast (this is the initial
    # state; nobody is listening for a transition yet).
    # ------------------------------------------------------------------
    def _scan_existing(self) -> None:
        try:
            entries = list(os.scandir(self.directory))
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.is_dir() and _dir_non_empty(entry.path):
                self._sessions.add(entry.name)
        logger.info(
            "[ArtifactsWatcher] boot snapshot: %d session(s) with artifacts",
            len(self._sessions),
        )

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------
    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            # Group the tick's changes by session id, collecting the POSIX
            # relative path (within the session dir) of each touched entry.
            touched: dict[str, set[str]] = {}
            for _change_type, raw_path in changes:
                # An upload's cross-filesystem temp file (design
                # 2026-09-28-file-upload §5.6) is dropped before the grouping:
                # alone, it would mark an empty session as having artifacts.
                if is_upload_temp_name(os.path.basename(raw_path)):
                    continue
                resolved = self._session_rel_for(raw_path)
                if resolved is None:
                    continue
                session_id, rel = resolved
                paths = touched.setdefault(session_id, set())
                if rel:
                    paths.add(rel)

            for session_id, rel_paths in touched.items():
                # Enqueue even the first transition and unknown ORM sessions.
                # The coordinator resolves shares/readiness outside this watcher.
                if self._enqueue is None:
                    from twicc.inline_artifacts.share_exports import get_inline_export_coordinator
                    enqueue = get_inline_export_coordinator().files_changed
                else:
                    enqueue = self._enqueue
                enqueue(session_id, sorted(rel_paths) or ['inline-artifacts'])
                if session_id not in self._sessions:
                    # First sighting of this session's subtree. Confirm the dir is
                    # actually non-empty (the change could be a deletion, or the
                    # mkdir of a still-empty session dir) before flipping it on.
                    # The Artifacts tab fetches the full tree on first open, so the
                    # transition tick itself does not also relay file changes.
                    session_dir = self.directory / session_id
                    if await asyncio.to_thread(_dir_non_empty, session_dir):
                        self._sessions.add(session_id)
                        logger.info("[ArtifactsWatcher] artifacts detected for session %s", session_id)
                        await self._broadcast_available(session_id)
                    continue
                # Already-known session: relay the changed files so an open
                # Artifacts tab can live-refresh (and reload a rendered HTML page).
                if rel_paths:
                    await self._broadcast_files_changed(session_id, sorted(rel_paths))

    def _session_rel_for(self, raw_path: str) -> tuple[str, str] | None:
        """Return ``(session_id, rel)`` for a changed path, or ``None``.

        ``session_id`` is the first path segment under the artifacts root; ``rel``
        is the POSIX path of the change *within* that session dir (``""`` when the
        change is the session dir itself). Tries each known root prefix (the
        watched path and its canonical target) so it works whether awatch reports
        link-relative or canonical paths.
        """
        p = Path(raw_path)
        for base in self._roots:
            try:
                rel = p.relative_to(base)
            except ValueError:
                continue
            parts = rel.parts
            if not parts:
                continue
            return parts[0], "/".join(parts[1:])
        return None

    # ------------------------------------------------------------------
    # Broadcasts
    # ------------------------------------------------------------------
    async def _broadcast_available(self, session_id: str) -> None:
        """Announce the false->true transition (session gained its first artifact)."""
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "artifacts_available",
                    "session_id": session_id,
                    "artifacts_dir": str(get_session_artifacts_dir(session_id)),
                },
            },
        )

    async def _broadcast_files_changed(self, session_id: str, paths: list[str]) -> None:
        """Relay changed file paths (relative to the session artifacts dir)."""
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "artifact_files_changed",
                    "session_id": session_id,
                    "artifacts_dir": str(get_session_artifacts_dir(session_id)),
                    "paths": paths,
                },
            },
        )


_watcher_instance: ArtifactsWatcher | None = None


def get_artifacts_watcher() -> ArtifactsWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = ArtifactsWatcher()
    return _watcher_instance


def session_has_artifacts(session_id: str) -> bool:
    """O(1) in-memory check; safe from sync or async contexts (no I/O).

    Outside the running server (CLI, background-compute process) the watcher
    never started, so the set is empty and this returns False — acceptable, as
    the Artifacts tab is a live-server, web-UI concern.
    """
    return get_artifacts_watcher().has(session_id)
