"""Build the RPC route registry from the Click tree; render body → argv.

Each registry entry (``CommandSpec``) maps a ``/rpc/`` path to enough Click
metadata to (a) build a request-body JSON Schema and (b) render a validated
body back into the argv that :func:`twicc.rpc.invoker.invoke` runs.
"""

from __future__ import annotations

from typing import NamedTuple

import click

from twicc.cli._local_only import LOCAL_ONLY_COMMANDS
from twicc.rpc.invoker import get_command
from twicc.rpc.schema import ParamSpec, json_schema_for, param_spec


class Level(NamedTuple):
    token: str | None              # argv token for this level (None = root)
    arguments: list[ParamSpec]     # positional args consumed at this level


class CommandSpec(NamedTuple):
    path: str                      # e.g. "session/content"
    chain: list[Level]             # ordered levels (root excluded from tokens)
    options: list[ParamSpec]       # leaf options
    params: list[ParamSpec]        # every arg in the chain + leaf options
    json_schema: dict
    summary: str

    @property
    def validation_schema(self) -> dict:
        """Accept retired options without listing them in discovery schemas."""
        return json_schema_for(self.params, include_hidden=True)


def _split_params(cmd: click.Command) -> tuple[list[ParamSpec], list[ParamSpec]]:
    args: list[ParamSpec] = []
    opts: list[ParamSpec] = []
    for p in cmd.params:
        spec = param_spec(p)
        if spec is None:
            continue
        (args if spec.kind == "argument" else opts).append(spec)
    return args, opts


def _register(registry: dict, chain: list[Level], options: list[ParamSpec], cmd: click.Command) -> None:
    tokens = [lvl.token for lvl in chain if lvl.token is not None]
    path = "/".join(tokens)
    if not path:
        return
    params = [a for lvl in chain for a in lvl.arguments] + options
    registry[path] = CommandSpec(
        path=path,
        chain=chain,
        options=options,
        params=params,
        json_schema=json_schema_for(params),
        summary=(cmd.get_short_help_str() or "") if hasattr(cmd, "get_short_help_str") else "",
    )


def _walk(cmd: click.Command, token: str | None, chain: list[Level], registry: dict, *,
          is_root: bool, excluded_roots: frozenset[str]) -> None:
    args, opts = _split_params(cmd)
    if isinstance(cmd, click.Group):
        # Ancestor levels carry the group's ARGUMENTS only (its options default
        # when a subcommand is invoked).
        child_chain = chain if is_root else chain + [Level(token=token, arguments=args)]
        # A group that is itself callable (invoke_without_command) is a route:
        # its own args + options. (e.g. bare `sessions`, `session <id>`.)
        if not is_root and getattr(cmd, "invoke_without_command", False):
            _register(registry, child_chain, opts, cmd)
        for sub_name, sub in cmd.commands.items():
            if is_root and sub_name in excluded_roots:
                continue
            _walk(sub, sub_name, child_chain, registry, is_root=False, excluded_roots=excluded_roots)
    else:
        leaf_chain = chain + [Level(token=token, arguments=args)]
        _register(registry, leaf_chain, opts, cmd)


_registries: dict[frozenset[str], dict[str, CommandSpec]] = {}


def build_registry(*, excluded_roots: frozenset[str] = frozenset(LOCAL_ONLY_COMMANDS)) -> dict[str, CommandSpec]:
    """Build (cached per ``excluded_roots``) the path → CommandSpec registry.

    The default excludes the local-only commands (the ``/rpc/`` surface). The
    MCP server passes its own exclusion set (see :mod:`twicc.mcp.tools`).
    """
    global _registries
    if excluded_roots not in _registries:
        reg: dict[str, CommandSpec] = {}
        _walk(get_command(), None, [], reg, is_root=True, excluded_roots=excluded_roots)
        _registries[excluded_roots] = reg
    return _registries[excluded_roots]


def _render_argument(p: ParamSpec, body: dict) -> list[str]:
    if p.name not in body:
        return []
    val = body[p.name]
    if p.variadic or p.multiple:
        return [str(v) for v in (val or [])]
    return [str(val)]


def _render_option(p: ParamSpec, body: dict) -> list[str]:
    if p.name not in body:
        return []
    val = body[p.name]
    if p.is_flag:
        if p.secondary_opt:
            return [p.opt if val else p.secondary_opt]
        return [p.opt] if val else []
    if p.multiple:
        return [f"{p.opt}={v}" for v in (val or [])]
    return [f"{p.opt}={val}"]


def render_argv(spec: CommandSpec, body: dict) -> list[str]:
    """Render a validated request body into the CLI argv for the invoker.

    Options are emitted as ``--opt=value`` (single token), and the leaf
    command's positional arguments are placed after a ``--`` separator (with
    options before it), so a value that starts with ``-`` (e.g. a
    dash-prefixed project id, or an arbitrary prompt/query) is never
    misparsed as an option by Click. Ancestor group positional args are
    emitted inline (a ``--`` there would swallow the following subcommand
    token; those values — session ids, slugs — are never dash-prefixed).
    """
    argv: list[str] = []
    levels = spec.chain
    last = len(levels) - 1
    for i, level in enumerate(levels):
        if level.token is not None:
            argv.append(level.token)
        if i != last:
            for arg in level.arguments:
                argv += _render_argument(arg, body)
    leaf = levels[last]
    for opt in spec.options:
        argv += _render_option(opt, body)
    pos: list[str] = []
    for arg in leaf.arguments:
        pos += _render_argument(arg, body)
    if pos:
        argv.append("--")
        argv += pos
    return argv
