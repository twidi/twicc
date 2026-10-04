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
    if not isinstance(init["cwd"], str):
        raise HermeticGuardViolation(f"init reports cwd {init['cwd']!r}")
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


from collections.abc import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

# Replaced by the unit tests.
_client_factory = ClaudeSDKClient


def _count_tool_blocks(message) -> int:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return 0
    return sum(1 for block in content if type(block).__name__ in _TOOL_BLOCK_NAMES)


async def _run(prompt, *, model, cwd, options_override, raise_on_violation) -> HermeticClaudeResult:
    expected_cwd = Path(cwd) if cwd is not None else hermetic_cwd()
    permission_calls: list[str] = []
    options = _build_options(model=model, effort="low", cwd=expected_cwd, permission_calls=permission_calls)
    guarded = options_override is None
    if options_override is not None:
        options = options_override(options)
    client = _client_factory(options=options)

    text = ""
    assistant_error = None
    init: dict = {}
    usage: dict = {}
    is_error = False
    num_turns = None
    tool_blocks = 0
    violation: HermeticGuardViolation | None = None   # constructing it logs the single warning line
    try:
        await client.connect()
        await client.query(prompt)
        async for message in client.receive_messages():
            if isinstance(message, SystemMessage) and getattr(message, "subtype", None) == "init":
                init = dict(getattr(message, "data", {}) or {})
                if guarded:
                    try:
                        check_claude_init(init, cwd=expected_cwd, alias=model)
                    except HermeticGuardViolation as exc:
                        violation = exc
                        break
            elif isinstance(message, AssistantMessage):
                tool_blocks += _count_tool_blocks(message)
                if message.error:
                    assistant_error = message.error
                text += "".join(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                tool_blocks += _count_tool_blocks(message)
                usage, is_error, num_turns = dict(message.usage or {}), bool(message.is_error), message.num_turns
                break
            else:
                tool_blocks += _count_tool_blocks(message)
        if violation is None and guarded and not init:
            violation = HermeticGuardViolation("no init message received")
        if violation is not None:
            try:
                await client.interrupt()
            except Exception:
                logger.debug("interrupt failed after a guard violation", exc_info=True)
    finally:
        try:
            await client.disconnect()
        except Exception:
            logger.debug("disconnect failed", exc_info=True)

    result = HermeticClaudeResult(
        text=text.strip(), assistant_error=assistant_error, is_error=is_error, usage=usage, init=init,
        num_turns=num_turns, tool_blocks_seen=tool_blocks, permission_callback_calls=len(permission_calls),
    )
    if guarded and violation is None:
        try:
            check_claude_result(result)   # tool activity counts even when no ResultMessage arrived
        except HermeticGuardViolation as exc:
            violation = exc
    if violation is not None:
        if raise_on_violation:
            raise violation
        return result._replace(violation=violation.reason)
    return result


async def run_hermetic_claude(prompt: str, *, model: str) -> HermeticClaudeResult:
    """One hermetic Claude turn. Raises ``HermeticGuardViolation`` on a guard failure.

    Callers wrap it in their own ``asyncio.wait_for`` and retry loop.
    """
    return await _run(prompt, model=model, cwd=None, options_override=None, raise_on_violation=True)


async def _run_hermetic_claude_for_diagnostic(
    prompt: str,
    *,
    model: str,
    cwd: Path | None = None,
    options_override: Callable[[ClaudeAgentOptions], ClaudeAgentOptions] | None = None,
) -> HermeticClaudeResult:
    """Diagnostic seam (§5.1): same implementation, but the violation is returned, not raised.

    ``cwd`` sets both the options' working directory and the guard's expected one.
    ``options_override`` builds the diagnostic's unrestricted control options; the guard is then skipped.
    """
    return await _run(prompt, model=model, cwd=cwd, options_override=options_override, raise_on_violation=False)
