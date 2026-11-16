"""Gemini CLI slash commands, synced from ACP ``available_commands_update``.

The CLI has no command-listing call outside a session: each ACP session
announces the commands it accepts (built-ins such as ``memory`` or
``extensions list``, the user's ``~/.gemini/commands`` and the project's
``.gemini/commands``). The latest announcement of a project replaces that
project's rows; announcements of other projects are kept, seeded from the
database on the first update after a backend start.
"""

from __future__ import annotations

import asyncio
import logging

from asgiref.sync import sync_to_async

from twicc.core.enums import Provider

logger = logging.getLogger(__name__)

ACTIVATION_CHAR = "/"

# project_id -> {name: fields}, the desired catalogue of every project seen.
_catalogue: dict[str, dict[str, dict]] | None = None
_lock = asyncio.Lock()


def _commands_from_update(commands: list) -> dict[str, dict]:
    desired: dict[str, dict] = {}
    for command in commands:
        if not isinstance(command, dict):
            continue
        name = command.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        hint = command.get("input", {}).get("hint") if isinstance(command.get("input"), dict) else None
        desired[name.strip()] = {
            "plugin_name": None,
            "description": str(command.get("description") or ""),
            "argument_hint": hint if isinstance(hint, str) and hint else None,
            "is_builtin": False,
            "is_workflow": False,
        }
    return desired


def _load_catalogue() -> dict[str, dict[str, dict]]:
    from twicc.core.models import Command

    catalogue: dict[str, dict[str, dict]] = {}
    rows = Command.objects.filter(provider=Provider.GEMINI.value, activation_char=ACTIVATION_CHAR)
    for row in rows:
        if row.project_id is None:
            continue
        catalogue.setdefault(row.project_id, {})[row.name] = {
            "plugin_name": row.plugin_name,
            "description": row.description,
            "argument_hint": row.argument_hint,
            "is_builtin": row.is_builtin,
            "is_workflow": row.is_workflow,
        }
    return catalogue


async def sync_project_commands(project_id: str, commands: list) -> dict[str, int] | None:
    """Replace ``project_id``'s Gemini commands with an ACP announcement."""
    from twicc.providers.db_writer import _ApplyDesiredCommandsJob, submit_async_job

    global _catalogue
    desired_project = _commands_from_update(commands)
    async with _lock:
        if _catalogue is None:
            _catalogue = await sync_to_async(_load_catalogue)()
        if _catalogue.get(project_id) == desired_project:
            return None
        _catalogue[project_id] = desired_project
        desired = {
            (project, name): fields
            for project, entries in _catalogue.items()
            for name, fields in entries.items()
        }
    future = asyncio.get_running_loop().create_future()
    return await submit_async_job(_ApplyDesiredCommandsJob(
        provider=Provider.GEMINI,
        activation_char=ACTIVATION_CHAR,
        desired=desired,
        future=future,
    ))


def _reset_for_tests() -> None:
    global _catalogue
    _catalogue = None
