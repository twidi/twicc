"""
Title suggestion service using Claude Haiku via the Agent SDK.
"""
import asyncio
import logging

from twicc.providers.claude_code.hermetic import run_hermetic_claude
from twicc.title_transcript import title_rejection_reasons

logger = logging.getLogger(__name__)

# Measured on 49 calls: the median is 17s, the 95th percentile 39s and the
# maximum 51s (the CLI alone takes ~10s to answer a trivial prompt). 15s cut
# off about half of them. A title that takes longer is preferred to no title.
SUGGESTION_TIMEOUT_SECONDS = 60
# The attempt budget is shared with the other provider: the WS handler falls
# back to it when this one gives up (``asgi._handle_suggest_title``). Two
# attempts per provider, and spreading the retries over two models covers a
# flaky answer better than five shots at the same one. The worst case is
# 2 × 60s here before the fallback, on purpose.
MAX_RETRIES = 2


async def generate_title(user_message: str, system_prompt: str) -> str | None:
    """
    Generate a title suggestion from a user message and system prompt.

    Retries up to MAX_RETRIES times on any failure (timeout, empty response,
    response too long, SDK errors). No delay between retries.

    Args:
        user_message: The text to summarize (the user's messages, already bounded)
        system_prompt: The system prompt with {text} placeholder

    Returns:
        The suggested title, or None if all attempts failed
    """
    for attempt in range(1, MAX_RETRIES + 1):
        result = await _call_haiku(user_message, system_prompt, source="prompt", attempt=attempt)
        if result is not None:
            return result
    logger.warning("Title suggestion: all %d attempts exhausted", MAX_RETRIES)
    return None



async def _call_haiku(
    user_message: str, system_prompt: str, source: str = "unknown", attempt: int = 1
) -> str | None:
    """
    Single attempt to call Claude Haiku via the SDK and return the title suggestion.

    The full operation (connect, query, receive) is wrapped in a single timeout.
    Returns the suggested title, or None on any failure.

    Args:
        user_message: The text to summarize (the user's messages, already bounded)
        system_prompt: The system prompt with {text} placeholder
        source: Source identifier for logging
        attempt: Current attempt number (for logging)
    """
    full_prompt = system_prompt.replace("{text}", user_message)

    # ``HermeticConfigError`` and ``HermeticGuardViolation`` from the helper land in
    # the ``except Exception`` below and return ``None``, so the retry and the WS
    # handler's fallback to the other provider still run.
    async def _execute() -> str:
        """Run the hermetic call and return the answer text."""
        result = await run_hermetic_claude(full_prompt, model="haiku")
        if result.is_error or result.assistant_error:
            raise RuntimeError(
                f"hermetic Claude call failed (error={result.assistant_error!r}, is_error={result.is_error})"
            )
        return result.text

    try:
        suggestion = await asyncio.wait_for(_execute(), timeout=SUGGESTION_TIMEOUT_SECONDS)

        if reasons := title_rejection_reasons(suggestion):
            logger.warning(
                "Title suggestion rejected: %s (source=%s, attempt=%d/%d): %r",
                "; ".join(reasons), source, attempt, MAX_RETRIES, suggestion,
            )
            return None

        logger.info("Title suggestion generated: %r (source=%s, attempt=%d/%d)", suggestion, source, attempt, MAX_RETRIES)
        return suggestion

    except TimeoutError:
        logger.warning(
            "Title suggestion: timeout after %ds (source=%s, attempt=%d/%d)",
            SUGGESTION_TIMEOUT_SECONDS, source, attempt, MAX_RETRIES,
        )
        return None
    except Exception as e:
        logger.exception("Title suggestion error (source=%s, attempt=%d/%d): %s", source, attempt, MAX_RETRIES, e)
        return None
