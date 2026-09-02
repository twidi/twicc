"""Read/write the Codex per-project ``trust_level`` in ``<codex home>/config.toml``.

``<codex home>`` is ``~/.codex`` or the configured ``CODEX_HOME``
(``twicc.provider_homes.codex_home``); the write goes through the app-server,
which receives the same ``CODEX_HOME``.

Read = the project's own ``[projects."<root>"].trust_level`` via ``tomllib``.
Write = the app-server RPC ``config/batchWrite`` (so the Codex binary owns the
lock / atomicity / format preservation), keyed by the **canonical realpath** of
the project root. The path segment is double-quoted in the dotted key path. See
docs/plans/2026-06-09-project-trust-design.md §7 (validated against the Codex
Rust source: ``set_project_trust_level`` / ``config_manager_service``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

# Serializes the short-lived app-server clients spawned by ``_write_value``:
# concurrent trust projections (e.g. the startup backfill settling several
# projects at once) would otherwise each spawn their own Codex subprocess.
_write_lock = asyncio.Lock()


def _config_path() -> Path:
    from twicc.provider_homes import codex_home

    return codex_home().path / "config.toml"


def read_trust(root: str) -> bool | None:
    """Return the project root's ``trust_level`` as a bool, else None.

    Tries the canonical (realpath) key first, then the raw key — matching the
    way Codex resolves trust keys. None means no own decision recorded.
    """
    path = _config_path()
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (tomllib.TOMLDecodeError, OSError):
        logger.warning("Could not read %s for trust seeding", path, exc_info=True)
        return None
    projects = data.get("projects") or {}
    if not isinstance(projects, dict):
        return None
    for key in (os.path.realpath(root), root):
        entry = projects.get(key)
        if isinstance(entry, dict) and "trust_level" in entry:
            level = entry.get("trust_level")
            if level == "trusted":
                return True
            if level == "untrusted":
                return False
    return None


def _key_path(root: str) -> str:
    """Dotted config key path ``projects."<realpath>".trust_level``.

    The path segment is double-quoted; inside double quotes the Codex parser
    only treats ``\\`` and ``"`` specially (``.`` and ``/`` are literal), so we
    escape just those two.
    """
    canon = os.path.realpath(root)
    escaped = canon.replace("\\", "\\\\").replace('"', '\\"')
    return f'projects."{escaped}".trust_level'


async def write_trust(root: str, trusted: bool) -> None:
    """Set the project root's ``trust_level`` via ``config/batchWrite`` (upsert)."""
    await _write_value(root, "trusted" if trusted else "untrusted")


async def clear_trust(root: str) -> None:
    """Remove the project root's ``trust_level`` entry (reset to inherit).

    A ``null`` value deletes the key on the Codex side.
    """
    await _write_value(root, None)


async def _write_value(root: str, value: str | None) -> None:
    """Write (or delete, when *value* is ``None``) ``trust_level`` for *root*.

    Spawns a short-lived app-server client (same pattern as ``plugin_install``).
    Failures are logged, not raised: the DB stays the source of truth, so a
    failed projection degrades (Codex won't load project layers) but never
    blocks the session.
    """
    from openai_codex.async_client import AsyncCodexClient
    from openai_codex.generated.v2_all import ConfigWriteResponse

    from twicc.providers.codex.bin import make_codex_config

    config = await make_codex_config(cwd=str(Path.home()))
    try:
        async with _write_lock, AsyncCodexClient(config=config) as client:
            await client.initialize()
            await client.request(
                "config/batchWrite",
                {
                    "edits": [
                        {
                            "keyPath": _key_path(root),
                            "mergeStrategy": "upsert",
                            "value": value,
                        }
                    ]
                },
                response_model=ConfigWriteResponse,
            )
        logger.info("Codex trust for %s -> %s", root, value)
    except Exception:
        logger.exception("Failed to write Codex trust for %s", root)
