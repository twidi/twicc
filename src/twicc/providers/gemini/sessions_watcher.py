"""Gemini CLI file watcher.

Thin :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` subclass
for the ``<gemini home>/.gemini/tmp/`` layout:

    <project-slug>/.project_root            absolute project directory
    <project-slug>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl

The session id is the full ``sessionId`` of the file's first line (the file
name only keeps 8 characters). The JSONL carries no working directory, so the
project comes from ``.project_root`` and is registered with its directory
before the base class syncs the file.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import NamedTuple

import orjson
from watchfiles import Change

from twicc.core.models import SessionType
from twicc.paths import path_to_project_id
from twicc.provider_homes import gemini_sessions_dir
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile, SessionChangeResult

from . import records
from .compute import get_compute as _get_compute

logger = logging.getLogger(__name__)

CHATS_DIR = "chats"
PROJECT_ROOT_FILE = ".project_root"
MAIN_KIND = "main"


class SessionFileMeta(NamedTuple):
    session_id: str
    cwd: str


def read_project_root(slug_dir: Path) -> str | None:
    """The absolute project directory recorded by the CLI for a slug folder."""
    try:
        value = (slug_dir / PROJECT_ROOT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    # Older layouts only keep the mapping in ``projects.json``.
    try:
        projects = orjson.loads((slug_dir.parent.parent / "projects.json").read_bytes()).get("projects")
    except (OSError, orjson.JSONDecodeError, AttributeError):
        return None
    if isinstance(projects, dict):
        for directory, slug in projects.items():
            if slug == slug_dir.name and isinstance(directory, str):
                return directory
    return None


def read_session_file_meta(path: Path) -> SessionFileMeta | None:
    """Identify a Gemini session file worth syncing, ``None`` otherwise.

    Skipped: unreadable files, a header that is not a ``main`` session
    (subagents, out of scope for now), and files without any user prompt yet.
    The CLI creates a session file as soon as a client connects (an ACP
    ``initialize`` writes one), so most of those never get a prompt; a real
    session is picked up on the event that appends its first prompt.
    """
    try:
        with path.open("rb") as handle:
            header = orjson.loads(handle.readline() or b"null")
            if not isinstance(header, dict) or not records.is_header(header):
                return None
            if header.get("kind", MAIN_KIND) != MAIN_KIND:
                return None
            has_prompt = False
            for line in handle:
                try:
                    record = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                if isinstance(record, dict) and records.user_text(record):
                    has_prompt = True
                    break
    except (OSError, orjson.JSONDecodeError):
        return None
    if not has_prompt:
        return None
    cwd = read_project_root(path.parent.parent)
    if not cwd:
        return None
    return SessionFileMeta(header["sessionId"], cwd)


class GeminiSessionsWatcher(BaseSessionsWatcher):
    """File watcher for the Gemini CLI ``tmp`` directory."""

    def __init__(self) -> None:
        super().__init__()
        self.projects_dir = gemini_sessions_dir()
        # session_id -> project directory, filled by parse_session_file and
        # consumed by sync_and_broadcast.
        self._directories: dict[str, str] = {}
        # Files already identified: skip the prompt scan on later events.
        self._known: dict[Path, SessionFileMeta] = {}

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        try:
            relative = path.relative_to(self.projects_dir)
        except ValueError:
            return None
        parts = relative.parts
        if len(parts) != 3 or parts[1] != CHATS_DIR:
            return None
        filename = parts[2]
        if not (filename.startswith("session-") and filename.endswith(".jsonl")):
            return None

        meta = self._known.get(path)
        if meta is None:
            meta = await asyncio.to_thread(read_session_file_meta, path)
            if meta is None:
                return None
            self._known[path] = meta
        self._directories[meta.session_id] = meta.cwd
        return ParsedSessionFile(
            path_to_project_id(meta.cwd),
            meta.session_id,
            SessionType.SESSION,
            file_path=str(relative),
        )

    async def sync_and_broadcast(
        self,
        path: Path,
        parsed: ParsedSessionFile,
        change_type: Change,
        channel_layer,
        *,
        read_path: Path | None = None,
    ) -> SessionChangeResult:
        directory = self._directories.get(parsed.session_id)
        if directory and change_type != Change.deleted:
            from twicc.projects import register_project

            # Creates the project with its directory, or adopts the directory
            # on a row created without one. Never overwrites a known one.
            await register_project(parsed.project_id, directory=directory)
        return await super().sync_and_broadcast(path, parsed, change_type, channel_layer, read_path=read_path)

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()


_watcher: GeminiSessionsWatcher | None = None


def get_watcher() -> GeminiSessionsWatcher:
    """Return the process-local Gemini watcher singleton."""
    global _watcher
    if _watcher is None:
        _watcher = GeminiSessionsWatcher()
    return _watcher
