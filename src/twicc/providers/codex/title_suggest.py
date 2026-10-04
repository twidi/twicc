"""
Title suggestion service for Codex sessions using gpt-6-luna via the
Codex SDK.

Single-shot prompt through the hermetic Codex runner
(``providers/codex/hermetic.py``): one *ephemeral* thread (so the rollout
JSONL is never materialized on disk and the watcher doesn't pick up a stray
file), read-only sandbox, no tool, one turn, then the transport is closed.
The title prompt is instructional only, so the hermetic configuration leaves
the model nothing to run.

The retry / timeout / validation contract mirrors
``providers/claude_code/title_suggest.py`` so the WS-level surface
(:func:`UpdatesConsumer._handle_suggest_title`) doesn't need to know
which provider it talked to.
"""

import asyncio
import logging

from openai_codex.generated.v2_all import ReasoningEffort

from twicc.title_transcript import build_title_prompt, title_rejection_reasons

from .hermetic import prepare_hermetic_codex, run_hermetic_codex

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15
# The attempt budget is shared with the other provider: the WS handler falls
# back to it when this one gives up (``asgi._handle_suggest_title``). Two
# attempts per provider, and spreading the retries over two models covers a
# flaky answer better than five shots at the same one. That bound covers the
# model calls only: building the client can download the Codex runtime on a
# pruned cache, which is deliberately outside the timeout below.
MAX_RETRIES = 2

# Fixed SDK model name for Codex title generation. The global title-suggestion
# setting selects this provider route; this module always uses Luna and bypasses
# the agent-model alias machinery. Luna is the cheapest model of the catalogue;
# its lowest reasoning effort is ``low``
# (the CLI's ``model/list`` exposes no ``none``/``minimal`` on any model), so
# the turn below pins ``low`` rather than taking Luna's ``medium`` default.
TITLE_MODEL = "gpt-6-luna"


async def generate_title(
    user_message: str, system_prompt: str, *, current_title: str | None = None,
) -> str | None:
    """
    Generate a title suggestion from a user message and system prompt.

    Retries up to MAX_RETRIES times on any failure (timeout, empty response,
    response too long, SDK errors). No delay between retries.

    Args:
        user_message: The text to summarize (the user's messages, already bounded)
        system_prompt: The system prompt with {text} placeholder
        current_title: Automatic title to compare against, after a successful check

    Returns:
        The suggested title, or None if all attempts failed
    """
    for attempt in range(1, MAX_RETRIES + 1):
        result = await _call_codex(
            user_message, system_prompt, source="prompt", attempt=attempt, current_title=current_title,
        )
        if result is not None:
            return result
    logger.warning("Codex title suggestion: all %d attempts exhausted", MAX_RETRIES)
    return None


async def _call_codex(
    user_message: str, system_prompt: str, source: str = "unknown", attempt: int = 1,
    *, current_title: str | None = None,
) -> str | None:
    """
    Single attempt to call gpt-6-luna via the Codex SDK and return the title suggestion.

    The full operation (start ephemeral thread → run turn → close transport)
    is wrapped in a single timeout. Returns the suggested title, or None on
    any failure.

    Args:
        user_message: The text to summarize (the user's messages, already bounded)
        system_prompt: The system prompt with {text} placeholder
        current_title: Automatic title to compare against, after a successful check
        source: Source identifier for logging
        attempt: Current attempt number (for logging)
    """
    full_prompt = build_title_prompt(system_prompt, user_message, current_title)

    # Guarded, and outside the timeout below: preparing the hermetic plan builds the catalogue and
    # may download the runtime when the cache was pruned. Unguarded, a failure would escape
    # ``_call_codex`` instead of the documented ``None``, skipping both the retry and the WS
    # handler's fallback to the other provider.
    try:
        plan = await prepare_hermetic_codex(TITLE_MODEL)
    except Exception as e:
        logger.exception("Codex title suggestion: client unavailable (source=%s, attempt=%d/%d): %s (reason=%s)",
                         source, attempt, MAX_RETRIES, e, getattr(e, "reason", None))
        return None

    async def _execute() -> str:
        result = await run_hermetic_codex(plan, full_prompt, effort=ReasoningEffort.low)
        if result.terminal_error is not None:
            raise RuntimeError(f"Codex terminal error: {result.terminal_error!r}")
        return result.text

    try:
        suggestion = await asyncio.wait_for(_execute(), timeout=SUGGESTION_TIMEOUT_SECONDS)

        if reasons := title_rejection_reasons(suggestion):
            logger.warning(
                "Codex title suggestion rejected: %s (source=%s, attempt=%d/%d): %r",
                "; ".join(reasons), source, attempt, MAX_RETRIES, suggestion,
            )
            return None

        logger.info(
            "Codex title suggestion generated: %r (source=%s, attempt=%d/%d)",
            suggestion, source, attempt, MAX_RETRIES,
        )
        return suggestion

    except TimeoutError:
        logger.warning(
            "Codex title suggestion: timeout after %ds (source=%s, attempt=%d/%d)",
            SUGGESTION_TIMEOUT_SECONDS, source, attempt, MAX_RETRIES,
        )
        return None
    except Exception as e:
        logger.exception(
            "Codex title suggestion error (source=%s, attempt=%d/%d): %s (reason=%s)",
            source, attempt, MAX_RETRIES, e, getattr(e, "reason", None),
        )
        return None
