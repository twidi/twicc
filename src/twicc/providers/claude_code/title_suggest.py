"""
Title suggestion service using Claude Haiku via the Agent SDK.
"""
import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15
MAX_RETRIES = 5


async def generate_title(user_message: str, system_prompt: str) -> str | None:
    """
    Generate a title suggestion from a user message and system prompt.

    Retries up to MAX_RETRIES times on any failure (timeout, empty response,
    response too long, SDK errors). No delay between retries.

    Args:
        user_message: The user's message text
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
        user_message: The user's message text
        system_prompt: The system prompt with {text} placeholder
        source: Source identifier for logging
        attempt: Current attempt number (for logging)
    """
    # Truncate long messages
    if len(user_message) > 2000:
        user_message = user_message[:2000] + "…"

    full_prompt = system_prompt.replace("{text}", user_message)

    from twicc.provider_homes import provider_env_overlay

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
        allowed_tools=[],
        effort='low',
        # Configured provider homes, explicit (see the SDK agent's env_option).
        env=provider_env_overlay(),
    )

    client = ClaudeSDKClient(options=options)

    async def _execute() -> str:
        """Run the full SDK interaction: connect, query, collect response."""
        await client.connect()
        await client.query(full_prompt)

        response_text = ""
        async for msg in client.receive_messages():
            # Extract content from message
            if hasattr(msg, "content"):
                for block in msg.content:
                    if hasattr(block, "text"):
                        response_text += block.text
            elif hasattr(msg, "message") and hasattr(msg.message, "content"):
                for block in msg.message.content:
                    if hasattr(block, "text"):
                        response_text += block.text

            if isinstance(msg, ResultMessage):
                break

        return response_text.strip()

    try:
        suggestion = await asyncio.wait_for(_execute(), timeout=SUGGESTION_TIMEOUT_SECONDS)

        # Basic validation
        if not suggestion:
            logger.warning(
                "Title suggestion: empty response (source=%s, attempt=%d/%d)", source, attempt, MAX_RETRIES
            )
            return None
        if len(suggestion) > 200:
            logger.warning(
                "Title suggestion: too long (%d chars, source=%s, attempt=%d/%d): %s",
                len(suggestion), source, attempt, MAX_RETRIES, suggestion,
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
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
