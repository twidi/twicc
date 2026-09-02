"""
Title suggestion service for Codex sessions using gpt-5.6-luna via the
Codex SDK.

Single-shot prompt: open an *ephemeral* thread (the SDK passes
``ephemeral=True`` to ``thread/start`` so the rollout JSONL is never
materialized on disk and the watcher doesn't pick up a stray file),
issue one ``thread.turn(TextInput(prompt))``, collect the assistant
text, close the transport. Approvals are bypassed at the server level
via ``danger_full_access`` + ``approval_policy="never"`` because the
title prompt is instructional only — Codex has no reason to call a
tool, but the residual safety net keeps the run from freezing on an
exotic approval request.

The retry / timeout / validation contract mirrors
``providers/claude_code/title_suggest.py`` so the WS-level surface
(:func:`UpdatesConsumer._handle_suggest_title`) doesn't need to know
which provider it talked to.
"""

import asyncio
import logging

from openai_codex import TextInput
from openai_codex.generated.v2_all import AskForApproval, ReasoningEffort, SandboxMode

from .bin import make_codex_config
from .sdk_wrappers import TwiccAsyncCodex

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15
MAX_RETRIES = 5

# Fixed SDK model name for Codex title generation. The global title-suggestion
# setting selects this provider route; this module always uses Luna and bypasses
# the agent-model alias machinery. Luna is the cheapest model of the catalogue;
# its lowest reasoning effort is ``low``
# (the CLI's ``model/list`` exposes no ``none``/``minimal`` on any model), so
# the turn below pins ``low`` rather than taking Luna's ``medium`` default.
TITLE_MODEL = "gpt-5.6-luna"


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
        result = await _call_codex(user_message, system_prompt, source="prompt", attempt=attempt)
        if result is not None:
            return result
    logger.warning("Codex title suggestion: all %d attempts exhausted", MAX_RETRIES)
    return None


def _extract_assistant_text(items: object) -> str:
    """Concatenate the assistant text from an iterable of ``ThreadItem``.

    Mirrors the ``assistant_text_from_turn`` helper used by the Codex SDK
    examples (``sdk/python/examples/_bootstrap.py``); inlined and adapted
    here because the SDK package doesn't export it and because we feed
    it items collected from the live event stream rather than from a
    persisted turn (ephemeral threads forbid ``includeTurns``). Keeps:

    - ``agentMessage.text`` (the assembled agent message item), and
    - ``message.content[*].text`` where ``role=='assistant'`` and the
      content block is ``type=='output_text'`` (the OpenAI Responses-API
      shape Codex re-emits).
    """
    chunks: list[str] = []
    for item in items or []:
        raw = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw, dict):
            continue

        item_type = raw.get("type")
        if item_type == "agentMessage":
            text = raw.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            continue

        if item_type != "message" or raw.get("role") != "assistant":
            continue

        for content in raw.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)

    return "".join(chunks)


async def _call_codex(
    user_message: str, system_prompt: str, source: str = "unknown", attempt: int = 1,
) -> str | None:
    """
    Single attempt to call gpt-5.6-luna via the Codex SDK and return the title suggestion.

    The full operation (start ephemeral thread → run turn → close transport)
    is wrapped in a single timeout. Returns the suggested title, or None on
    any failure.

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

    config = await make_codex_config()
    codex = TwiccAsyncCodex(config=config)

    async def _execute() -> str:
        """Open an ephemeral thread, stream a single turn, collect the assistant text.

        Ephemeral threads (``ephemeral=True``) skip the on-disk JSONL
        rollout, which is what we want for a fire-and-forget title
        prompt. The flip side is that the server refuses
        ``thread.read(include_turns=True)`` for them, so the items
        delivered as the turn ran are the only handle we get. We
        therefore consume the live event stream (``turn.stream()``):
        each ``item/completed`` event carries one assembled
        ``ThreadItem`` we can hand to :func:`_extract_assistant_text`,
        and ``turn/completed`` signals the end of the stream (the SDK
        breaks the iterator there).
        """
        thread = await codex.thread_start_with_policy(
            model=TITLE_MODEL,
            ephemeral=True,
            sandbox=SandboxMode.danger_full_access,
            approval_policy=AskForApproval.model_validate("never"),
        )
        turn_handle = await thread.turn_with_policy(
            TextInput(full_prompt),
            effort=ReasoningEffort.low,
        )

        items: list[object] = []
        async for event in turn_handle.stream():
            if event.method == "item/completed":
                item = getattr(event.payload, "item", None)
                if item is not None:
                    items.append(item)

        return _extract_assistant_text(items).strip()

    try:
        suggestion = await asyncio.wait_for(_execute(), timeout=SUGGESTION_TIMEOUT_SECONDS)

        # Basic validation
        if not suggestion:
            logger.warning(
                "Codex title suggestion: empty response (source=%s, attempt=%d/%d)",
                source, attempt, MAX_RETRIES,
            )
            return None
        if len(suggestion) > 200:
            logger.warning(
                "Codex title suggestion: too long (%d chars, source=%s, attempt=%d/%d): %s",
                len(suggestion), source, attempt, MAX_RETRIES, suggestion,
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
            "Codex title suggestion error (source=%s, attempt=%d/%d): %s",
            source, attempt, MAX_RETRIES, e,
        )
        return None
    finally:
        try:
            await codex.close()
        except Exception:
            logger.debug(
                "codex.close() failed while unwinding title suggestion",
                exc_info=True,
            )
