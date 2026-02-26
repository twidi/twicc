"""
Title suggestion service using Claude Haiku via the Agent SDK.
"""
import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

import orjson

from twicc.compute import extract_text_from_content, get_message_content
from twicc.core.models import SessionItem
from twicc.core.enums import ItemKind

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15


async def generate_title(user_message: str, system_prompt: str) -> str | None:
    """
    Generate a title suggestion from a user message and system prompt.

    Args:
        user_message: The user's message text
        system_prompt: The system prompt with {text} placeholder

    Returns:
        The suggested title, or None if generation failed
    """
    return await _call_haiku(user_message, system_prompt, source="prompt")



async def get_first_user_message(session_id: str) -> str | None:
    """Extract text from the first user message in a session."""
    from asgiref.sync import sync_to_async

    @sync_to_async
    def fetch():
        item = SessionItem.objects.filter(
            session_id=session_id,
            kind=ItemKind.USER_MESSAGE
        ).order_by('line_num').first()

        if not item:
            return None

        try:
            parsed = orjson.loads(item.content)
            content = get_message_content(parsed)
            return extract_text_from_content(content)
        except Exception as e:
            logger.warning("Failed to parse message for session %s: %s", session_id, e)
            return None

    return await fetch()


async def _call_haiku(user_message: str, system_prompt: str, source: str = "unknown") -> str | None:
    """
    Call Claude Haiku via the SDK and return the title suggestion.

    Uses SDK in streaming mode, sends one message, waits for response, then kills.

    Args:
        user_message: The user's message text
        system_prompt: The system prompt with {text} placeholder
        source: Source identifier for logging
    """
    # Truncate long messages
    if len(user_message) > 2000:
        user_message = user_message[:2000] + "..."

    full_prompt = system_prompt.replace("{text}", user_message)

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
        allowed_tools=[],
    )

    client = ClaudeSDKClient(options=options)

    try:
        await asyncio.wait_for(
            client.connect(),
            timeout=SUGGESTION_TIMEOUT_SECONDS
        )

        await client.query(full_prompt)

        # Collect response
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

        suggestion = response_text.strip()

        # Basic validation
        if not suggestion:
            logger.warning("Title suggestion: empty response (source=%s)", source)
            return None
        if len(suggestion) > 200:
            logger.warning("Title suggestion: too long (%d chars), source=%s: %s", len(suggestion), source, suggestion)
            return None

        logger.info("Title suggestion generated: %r (source=%s)", suggestion, source)
        return suggestion

    except asyncio.TimeoutError:
        logger.warning("Title suggestion: timeout after %ds (source=%s)", SUGGESTION_TIMEOUT_SECONDS, source)
        return None
    except Exception as e:
        logger.exception("Title suggestion error (source=%s): %s", source, e)
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
