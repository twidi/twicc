"""
Hermetic Claude calls: zero tools, no settings/MCP/skills/memory, one turn.

See docs/plans/2026-10-03-hermetic-llm-calls-design.md §5.3 and §5.5.
"""
import logging
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny

from twicc.providers.hermetic import (
    HermeticGuardViolation,
    HermeticStreamError,
    hermetic_cwd,
    interrupt_abandoned_stream,
)

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


def _build_options(
    *, model: str, effort: str, cwd: Path, permission_calls: list[str], include_partial_messages: bool = False,
) -> ClaudeAgentOptions:
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
        include_partial_messages=include_partial_messages,   # the text deltas, for the streaming mode only
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


import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
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


def _text_delta(event) -> str | None:
    """The text of a ``content_block_delta`` stream event carrying a ``text_delta``, else ``None``."""
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return text if isinstance(text, str) else None


class _Outcome:
    """What the shared loop leaves once the turn ended normally (the stream was not abandoned)."""

    def __init__(self) -> None:
        self.result: HermeticClaudeResult | None = None
        self.violation: HermeticGuardViolation | None = None
        self.restarted = False   # a second ``message_start``: the answer would be streamed again


async def _turn(prompt, *, model, cwd, options_override, stream, outcome: _Outcome) -> AsyncIterator[str]:
    """The loop of every hermetic Claude call: connect, query, guard, collect, disconnect.

    With ``stream`` it asks for partial messages and yields their text deltas (it yields nothing otherwise),
    after the ``init`` check. ``outcome`` is filled once the client is disconnected.
    """
    expected_cwd = Path(cwd) if cwd is not None else hermetic_cwd()
    permission_calls: list[str] = []
    options = _build_options(
        model=model, effort="low", cwd=expected_cwd, permission_calls=permission_calls,
        include_partial_messages=stream,
    )
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
    message_starts = 0
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
            elif stream and isinstance(message, StreamEvent):
                event = message.event if isinstance(message.event, dict) else {}
                if event.get("type") == "message_start":
                    message_starts += 1
                    if message_starts > 1:
                        outcome.restarted = True
                        break
                    continue
                chunk = _text_delta(event)
                if chunk:
                    if guarded and not init:
                        violation = HermeticGuardViolation("no init message received before the text")
                        break
                    yield chunk
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
        if violation is not None or outcome.restarted:
            try:
                await client.interrupt()
            except Exception:
                logger.debug("interrupt failed after a guard violation", exc_info=True)
    except (GeneratorExit, asyncio.CancelledError):
        # The consumer abandoned the stream mid-turn: stop the generation before the disconnect.
        if stream:
            await interrupt_abandoned_stream(client)
        raise
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
    outcome.result, outcome.violation = result, violation


async def _run(prompt, *, model, cwd, options_override, raise_on_violation) -> HermeticClaudeResult:
    outcome = _Outcome()
    async for _ in _turn(prompt, model=model, cwd=cwd, options_override=options_override, stream=False,
                         outcome=outcome):
        pass   # nothing is yielded without ``stream``
    result, violation = outcome.result, outcome.violation
    if violation is not None:
        if raise_on_violation:
            raise violation
        return result._replace(violation=violation.reason)
    return result


def _stream_failure(outcome: _Outcome) -> str | None:
    """Why a streamed answer is not complete (user-safe, without the prompt), ``None`` when it is."""
    result = outcome.result
    if outcome.restarted:
        return "The model restarted its answer"
    if result.assistant_error:
        return f"The model returned an error ({result.assistant_error})"
    if result.is_error:
        return "The model returned an error result"
    if result.num_turns is None:
        return "The call ended without a result"
    return None


async def run_hermetic_claude(prompt: str, *, model: str) -> HermeticClaudeResult:
    """One hermetic Claude turn. Raises ``HermeticGuardViolation`` on a guard failure.

    Callers wrap it in their own ``asyncio.wait_for`` and retry loop.
    """
    return await _run(prompt, model=model, cwd=None, options_override=None, raise_on_violation=True)


async def stream_hermetic_claude(prompt: str, *, model: str) -> AsyncIterator[str]:
    """One hermetic Claude turn whose answer text is yielded as it is generated (the text deltas).

    The ``init`` check runs before the first yield, the result check at the end. Raises
    ``HermeticGuardViolation`` on a guard failure, possibly after some yields, and ``HermeticStreamError``
    when the turn did not end with a success result: unlike the title call, an error result is a failure here.
    Closing the generator early interrupts the turn and disconnects. Callers bound it with their own time limits.
    """
    outcome = _Outcome()
    async with aclosing(_turn(prompt, model=model, cwd=None, options_override=None, stream=True,
                              outcome=outcome)) as deltas:
        async for chunk in deltas:
            yield chunk
    if outcome.violation is not None:
        raise outcome.violation
    failure = _stream_failure(outcome)
    if failure is not None:
        raise HermeticStreamError(failure)


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
