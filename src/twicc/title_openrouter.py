"""
Title suggestion through Qwen on OpenRouter.

Same prompt, same input and same checks of the answer as an agent title call
(``providers/claude_code/title_suggest.py``), in one attempt: the agents'
fallback chain (``core/services/title_suggestion.py``) takes the place of a retry.
"""
import logging

from twicc.openrouter_text import QWEN_MODEL, OpenRouterTextError, complete_openrouter_text
from twicc.title_transcript import build_title_prompt, title_rejection_reasons, unwrap_title_suggestion

logger = logging.getLogger(__name__)

# Qwen on Cerebras answers a title in about a second; a longer wait only delays
# the fallback to the agents.
QWEN_TITLE_TIMEOUT_SECONDS = 10

_SOURCE = "openrouter"


async def generate_openrouter_title(
    user_message: str, system_prompt: str, *, current_title: str | None = None,
) -> str | None:
    """
    Generate a title suggestion with Qwen, in one attempt.

    Args:
        user_message: The text to summarize (the user's messages, already bounded)
        system_prompt: The system prompt with {text} placeholder
        current_title: Automatic title to compare against, after a successful check

    Returns:
        The suggested title, or None on any failure (call failure, time limit, rejected answer)
    """
    # The agents send the built prompt as their user prompt: no system prompt here either.
    full_prompt = build_title_prompt(system_prompt, user_message, current_title)
    try:
        suggestion = unwrap_title_suggestion(
            await complete_openrouter_text(QWEN_MODEL, full_prompt, timeout_seconds=QWEN_TITLE_TIMEOUT_SECONDS)
        )
    except OpenRouterTextError as error:
        if error.reason == "timeout":
            logger.warning(
                "Title suggestion: timeout after %ds (source=%s, attempt=1/1)",
                QWEN_TITLE_TIMEOUT_SECONDS, _SOURCE,
            )
        else:
            # An expected failure, already logged by the call itself: no traceback.
            logger.warning(
                "Title suggestion error (source=%s, attempt=1/1): %s (reason=%s)",
                _SOURCE, error, error.reason,
            )
        return None
    except Exception as error:
        logger.exception(
            "Title suggestion error (source=%s, attempt=1/1): %s (reason=%s)",
            _SOURCE, error, getattr(error, "reason", None),
        )
        return None

    if reasons := title_rejection_reasons(suggestion):
        logger.warning(
            "Title suggestion rejected: %s (source=%s, attempt=1/1): %r",
            "; ".join(reasons), _SOURCE, suggestion,
        )
        return None

    logger.info("Title suggestion generated: %r (source=%s, attempt=1/1)", suggestion, _SOURCE)
    return suggestion
