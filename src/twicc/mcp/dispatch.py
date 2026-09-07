"""Prepare ordinary MCP commands without invoking them or importing the catalog."""
from copy import deepcopy
from typing import NamedTuple

import jsonschema

from twicc.rpc.generator import CommandSpec


class UnknownToolError(Exception):
    pass


class PreparedTool(NamedTuple):
    name: str
    spec: CommandSpec
    arguments: dict


def check_caller_arguments(name: str, arguments: dict, *, external: bool) -> None:
    if not external:
        return
    from twicc.cli._remote import HOST_BOUND_PARAMS

    if name == "whoami":
        raise UnknownToolError(name)
    if name == "topology" and not arguments.get("session_id"):
        raise ValueError("External MCP requires an explicit session_id for topology.")
    for key, value in arguments.items():
        values = value if isinstance(value, list) else [value]
        if key in HOST_BOUND_PARAMS and any(v in ("self", "parent") for v in values):
            raise ValueError(f"External MCP requires explicit IDs for {key}.")


def prepare_tool(name: str, arguments: dict, *, registry: dict[str, CommandSpec],
                 external: bool) -> PreparedTool:
    spec = registry.get(name)
    if spec is None:
        raise UnknownToolError(name)
    jsonschema.validate(instance=arguments, schema=spec.json_schema)
    check_caller_arguments(name, arguments, external=external)
    return PreparedTool(name, spec, deepcopy(arguments))
