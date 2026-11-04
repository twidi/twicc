"""Shared small streamed text calls for adaptation and summaries.

Runners, models, routing, and time limits retain the adaptation configuration.
Private binding arguments preserve adaptation's existing monkeypatch surface.
"""
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.openrouter_text import OPENROUTER_TEXT_MODEL, OpenRouterTextError, openrouter_key_set, stream_openrouter_text  # noqa: F401 - shared call-time bindings
from twicc.providers.claude_code.hermetic import stream_hermetic_claude  # noqa: F401 - shared call-time bindings
from twicc.providers.codex.hermetic import prepare_hermetic_codex, stream_hermetic_codex  # noqa: F401 - shared call-time bindings
from twicc.providers.codex.title_suggest import TITLE_MODEL  # noqa: F401 - shared call-time bindings
from twicc.providers.state import is_provider_running  # noqa: F401 - shared call-time bindings

# The longest wait for the first adapted text (a hermetic call's start includes the model's process
# start), then for each next piece of it.
FIRST_TEXT_SECONDS = 30.0
NEXT_TEXT_SECONDS = 15.0
# The same for OpenRouter: its first text comes in under a second, and a failed call still leaves
# the time for an agent to adapt the text.
OPENROUTER_FIRST_TEXT_SECONDS = 5.0
OPENROUTER_NEXT_TEXT_SECONDS = 5.0
# The HTTP statuses of an OpenRouter call refused for its key or its account (wrong key, no credits).
_REFUSED_STATUSES = frozenset({401, 402, 403})
# The longest text OpenRouter adapts: its model holds 65.5K tokens for the text and its adaptation
# together (32.8K output at most). A longer text goes to an agent, which has no such limit.
OPENROUTER_ADAPT_MAX_CHARS = 60_000


class Engine(NamedTuple):
    """A text model's setting name and runner provider (None for OpenRouter)."""

    name: str
    provider: Provider | None


# The agents, in the default order: the first one whose provider runs adapts the text.
ENGINES = (Engine("haiku", Provider.CLAUDE_CODE), Engine("luna", Provider.CODEX))
_ENGINES_BY_NAME = {engine.name: engine for engine in ENGINES}
# The OpenRouter text model: chosen only, never in the default order.
OPENROUTER_ENGINE = Engine("openrouter", None)


def _resolve_engine(choice: str | None, text: str, bindings: dict) -> tuple[Engine | None, str | None]:
    """The engine that adapts ``text`` for the stored ``choice``, else why the text is read as
    written (``"off"``, ``"no_provider"``). A chosen agent whose provider does not run gives way to
    the other one; no choice takes the first agent whose provider runs. OpenRouter needs only the
    OpenRouter key, no provider: without the key, or for a text beyond its limit, an OpenRouter
    choice resolves as no choice."""
    if choice == "off":
        return None, "off"
    if choice == OPENROUTER_ENGINE.name:
        if bindings["openrouter_key_set"]() and len(text) <= OPENROUTER_ADAPT_MAX_CHARS:
            return OPENROUTER_ENGINE, None
        choice = None
    chosen = _ENGINES_BY_NAME.get(choice)
    candidates = ENGINES if chosen is None else (chosen, *(engine for engine in ENGINES if engine != chosen))
    for engine in candidates:
        if bindings["is_provider_running"](engine.provider):
            return engine, None
    return None, "no_provider"


def _fallback_engine(engine: Engine, bindings: dict) -> Engine | None:
    """The agent that adapts the text when ``engine`` fails before its first part: for OpenRouter,
    the first agent whose provider runs; else (or when none runs) None."""
    if engine != OPENROUTER_ENGINE:
        return None
    for agent in ENGINES:
        if bindings["is_provider_running"](agent.provider):
            return agent
    return None


def _step_limits(engine: Engine, bindings: dict) -> tuple[float, float]:
    """The longest waits for the first text of ``engine``'s call, then for each next piece (read at
    call time)."""
    if engine == OPENROUTER_ENGINE:
        return bindings["OPENROUTER_FIRST_TEXT_SECONDS"], bindings["OPENROUTER_NEXT_TEXT_SECONDS"]
    return bindings["FIRST_TEXT_SECONDS"], bindings["NEXT_TEXT_SECONDS"]


def _openrouter_reason(error: OpenRouterTextError) -> str:
    """The adaptation reason of a failed OpenRouter call: ``"refused"`` for its key or its account,
    else ``"failed"``."""
    if error.reason == "no_key" or error.status in _REFUSED_STATUSES:
        return "refused"
    return "failed"


async def _stream_text(engine: Engine, prompt: str, bindings: dict) -> AsyncIterator[str]:
    """The streamed call of ``engine``: the text deltas of its answer. OpenRouter streams over HTTP,
    an agent in a hermetic call. The Codex preparation runs on the first step, so that the caller's
    time limit for the first text bounds it too."""
    if engine == OPENROUTER_ENGINE:
        stream = bindings["stream_openrouter_text"](bindings["OPENROUTER_TEXT_MODEL"], prompt)
    elif engine.provider == Provider.CLAUDE_CODE:
        stream = bindings["stream_hermetic_claude"](prompt, model="haiku")
    else:
        plan = await bindings["prepare_hermetic_codex"](bindings["TITLE_MODEL"])
        stream = bindings["stream_hermetic_codex"](plan, prompt, effort="low")
    async with aclosing(stream):
        async for delta in stream:
            yield delta


def resolve_engine(choice: str | None, text: str) -> tuple[Engine | None, str | None]:
    return _resolve_engine(choice, text, globals())


def fallback_engine(engine: Engine) -> Engine | None:
    return _fallback_engine(engine, globals())


def step_limits(engine: Engine) -> tuple[float, float]:
    return _step_limits(engine, globals())


async def stream_text(engine: Engine, prompt: str) -> AsyncIterator[str]:
    async with aclosing(_stream_text(engine, prompt, globals())) as deltas:
        async for delta in deltas:
            yield delta
