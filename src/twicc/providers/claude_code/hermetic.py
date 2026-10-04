"""
Hermetic Claude calls: zero tools, no settings/MCP/skills/memory, one turn.

See docs/plans/2026-10-03-hermetic-llm-calls-design.md §5.3 and §5.5.
"""
import logging
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny

from twicc.providers.hermetic import HermeticGuardViolation, hermetic_cwd

logger = logging.getLogger(__name__)

# Requested alias -> prefix of the model name the CLI reports in ``init``.
FAMILY_PREFIXES: dict[str, str] = {"haiku": "claude-haiku-"}

_TOOL_BLOCK_NAMES = frozenset({"ToolUseBlock", "ToolResultBlock", "ServerToolUseBlock", "ServerToolResultBlock"})


class HermeticClaudeResult(NamedTuple):
    """What a hermetic Claude call produced, plus what the guard needs."""
    text: str
    assistant_error: str | None
    is_error: bool
    usage: dict
    init: dict
    num_turns: int | None
    tool_blocks_seen: int
    permission_callback_calls: int
    violation: str | None = None   # always None on the public path; set by the diagnostic seam


def _build_options(*, model: str, effort: str, cwd: Path, permission_calls: list[str]) -> ClaudeAgentOptions:
    from twicc.provider_homes import provider_env_overlay

    async def deny_all(tool_name, tool_input, context):
        permission_calls.append(tool_name)
        return PermissionResultDeny(message="No tool may run during a hermetic call.", interrupt=True)

    return ClaudeAgentOptions(
        model=model,
        effort=effort,
        permission_mode="dontAsk",
        tools=[],
        allowed_tools=[],
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=1,
        can_use_tool=deny_all,
        cwd=str(cwd),
        system_prompt=None,
        extra_args={"no-session-persistence": None, "disable-slash-commands": None},
        # Configured provider homes, explicit (see the SDK agent's env_option), plus no automatic memory.
        env={**provider_env_overlay(), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
    )


def hermetic_client_options(*, model: str, effort: str = "low") -> ClaudeAgentOptions:
    """The options of every hermetic Claude call (§5.3). All of them are required together."""
    return _build_options(model=model, effort=effort, cwd=hermetic_cwd(), permission_calls=[])


def check_claude_init(init: dict, *, cwd: Path, alias: str) -> None:
    """Fail unless the ``init`` system message reports the intended restricted state (§5.3 / §5.5)."""
    for key in ("tools", "mcp_servers", "slash_commands", "skills", "permissionMode", "cwd", "model"):
        if init.get(key) is None:
            raise HermeticGuardViolation(f"init message has no {key!r}")
    for key in ("tools", "mcp_servers", "slash_commands", "skills"):
        if init[key] != []:
            raise HermeticGuardViolation(f"init reports non-empty {key}: {init[key]!r}")
    if init["permissionMode"] != "dontAsk":
        raise HermeticGuardViolation(f"init reports permissionMode {init['permissionMode']!r}")
    if Path(init["cwd"]).resolve() != Path(cwd).resolve():
        raise HermeticGuardViolation(f"init reports cwd {init['cwd']!r}")
    prefix = FAMILY_PREFIXES.get(alias)
    if prefix is None or not str(init["model"]).startswith(prefix):
        raise HermeticGuardViolation(f"init reports model {init['model']!r} for alias {alias!r}")


def check_claude_result(result: HermeticClaudeResult) -> None:
    """Fail on any tool activity; let error results through so auth probes keep their signal (§5.5)."""
    if result.tool_blocks_seen or result.permission_callback_calls:
        raise HermeticGuardViolation(
            f"tool activity: {result.tool_blocks_seen} tool block(s), "
            f"{result.permission_callback_calls} permission request(s)"
        )
    if result.is_error or result.assistant_error or result.num_turns is None:
        return   # an error result keeps its signal; no ResultMessage means there is no turn count to judge
    if result.num_turns != 1:
        raise HermeticGuardViolation(f"expected one turn, got {result.num_turns!r}")
