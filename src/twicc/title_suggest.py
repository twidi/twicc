"""
Title suggestion service using Claude Haiku via the Agent SDK.
"""
import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from twicc.compute import extract_text_from_content, get_message_content
from twicc.core.models import SessionItem
from twicc.core.enums import ItemKind
import orjson

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15

TITLE_PROMPT = """Summarize the following user message in 5-7 words to create a concise session title.
Return ONLY the title, nothing else. No quotes, no explanation, no punctuation at the end.

IMPORTANT: The title must be in the same language as the user message. However, do not translate technical terms or words that are already in another language (e.g., if the user writes in French about code, keep English technical terms as-is).

User message:
{message}"""


async def generate_title_from_prompt(prompt: str) -> str | None:
    """
    Generate a title suggestion from a prompt text.
    Used for draft sessions and new sessions.
    """
    return await _call_haiku(prompt, source="prompt")


async def generate_title_from_session(session_id: str) -> str | None:
    """
    Generate a title suggestion for an existing session.
    Fetches the first user message from the database.
    """
    first_message = await get_first_user_message(session_id)
    if not first_message:
        logger.warning("No user message found for session %s", session_id)
        return None

    return await _call_haiku(first_message, source=f"session:{session_id}")


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


async def _call_haiku(user_message: str, source: str = "unknown") -> str | None:
    """
    Call Claude Haiku via the SDK and return the title suggestion.

    Uses SDK in streaming mode, sends one message, waits for response, then kills.
    """
    # Truncate long messages
    if len(user_message) > 2000:
        user_message = user_message[:2000] + "..."

    full_prompt = TITLE_PROMPT.format(message=user_message)

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
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
            logger.warning("Title suggestion: too long (%d chars), source=%s", len(suggestion), source)
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
