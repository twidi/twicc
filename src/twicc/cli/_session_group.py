"""The ``session`` group: ``--slim`` / ``--full`` accepted after the id too.

A Click group does not intersperse arguments: in ``session X --full`` the
parser stops at ``X`` and reads ``--full`` as a subcommand name. This group
moves a run of ``--slim`` / ``--full`` that directly follows the id and ends
the argv in front of the id, and refuses one that precedes a subcommand.
Design: docs/plans/2026-09-23-cli-consistency-before-cutover-design.md, §2.
"""

from __future__ import annotations

from typer.core import TyperGroup

from twicc.cli._output import emit_error

_GROUP_FLAGS = ("--slim", "--full")

#: ``ctx.meta`` key: a help option follows the id, so the callback must not
#: refuse a ``self`` / ``parent`` it cannot resolve (the help must print outside
#: a session); it still resolves one it can, since the token may be an option's
#: value.
HELP_REQUESTED = "twicc.session_help"


class SessionGroup(TyperGroup):
    def parse_args(self, ctx, args):
        args = list(args)
        index = 0
        while index < len(args) and args[index].startswith("-"):
            if args[index] == "--":
                # The argv render_argv builds for MCP / RPC: already parseable.
                return super().parse_args(ctx, args)
            index += 1
        if index < len(args):
            rest = args[index + 1:]
            # Tokens after `--` are values, never a help request.
            before_dashdash = rest[:rest.index("--")] if "--" in rest else rest
            ctx.meta[HELP_REQUESTED] = any(
                token in ctx.help_option_names for token in before_dashdash
            )
            run_length = 0
            while run_length < len(rest) and rest[run_length] in _GROUP_FLAGS:
                run_length += 1
            if run_length:
                following = rest[run_length:]
                if not following:
                    args = args[:index] + rest[:run_length] + [args[index]]
                elif following[0] in self.commands and not ctx.resilient_parsing:
                    emit_error(
                        "Error: --slim / --full apply to `session <id>` alone, "
                        f"not to `{following[0]}`.",
                        code=2,
                    )
        return super().parse_args(ctx, args)
