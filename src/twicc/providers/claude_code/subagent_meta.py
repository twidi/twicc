"""Read Claude spawn sidecars from the root's flat subagents directory."""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import orjson

from twicc.provider_homes import claude_projects_dir


class SubagentSpawnMeta(NamedTuple):
    agent_id: str
    tool_use_id: str | None
    parent_agent_id: str | None
    spawn_depth: int | None


def subagents_dir_for_file(file_path: str) -> Path:
    path = claude_projects_dir() / file_path
    if path.parent.name == "subagents":
        return path.parent
    return path.with_suffix("") / "subagents"


def read_subagent_meta(directory: Path, agent_id: str) -> SubagentSpawnMeta | None:
    try:
        data = orjson.loads((directory / f"agent-{agent_id}.meta.json").read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    parent = data.get("parentAgentId")
    tool = data.get("toolUseId")
    depth = data.get("spawnDepth")
    # A malformed launcher must not silently turn into a depth-1 root launcher.
    if parent is not None and (not isinstance(parent, str) or not parent):
        return None
    if tool is not None and (not isinstance(tool, str) or not tool):
        return None
    return SubagentSpawnMeta(agent_id, tool, parent, depth if type(depth) is int else None)


def read_subagent_metas(directory: Path) -> dict[str, SubagentSpawnMeta]:
    try:
        paths = list(directory.iterdir())
    except OSError:
        return {}
    result = {}
    for path in paths:
        match = re.fullmatch(r"agent-(a[0-9a-f]+)\.meta\.json", path.name)
        if match and (meta := read_subagent_meta(directory, match[1])) is not None:
            result[meta.agent_id] = meta
    return result
