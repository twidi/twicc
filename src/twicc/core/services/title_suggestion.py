"""Shared title generation provider selection and fallback."""

import logging
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.state import is_provider_running

logger = logging.getLogger(__name__)

TITLE_SUGGESTION_MODEL_PROVIDERS = {
    "haiku": Provider.CLAUDE_CODE,
    "luna": Provider.CODEX,
}

# Providers that can generate a title, in fallback order. Derived from the
# routing table above, which already declares exactly that set — a provider
# without a title route keeps the base ``generate_title`` returning ``None``.
# ``dict.fromkeys`` deduplicates while preserving the declaration order.
TITLE_CAPABLE_PROVIDERS = tuple(dict.fromkeys(TITLE_SUGGESTION_MODEL_PROVIDERS.values()))


class TitleSuggestionResult(NamedTuple):
    suggestion: str | None
    requested_provider: Provider
    title_provider: Provider | None
    error: str | None


async def suggest_title(
    source: str | None,
    system_prompt: str,
    session_provider: Provider,
    *,
    title_model: str | None = None,
    no_fallback: bool = False,
    current_title: str | None = None,
) -> TitleSuggestionResult:
    """Generate a title through the requested provider or an available fallback."""
    requested_provider = TITLE_SUGGESTION_MODEL_PROVIDERS.get(title_model, session_provider)
    suggestion = None
    title_provider = None
    error = None

    if not source:
        error = "no_prompt"
    else:
        candidates = [requested_provider]
        if not no_fallback:
            candidates += [p for p in TITLE_CAPABLE_PROVIDERS if p != requested_provider]
        available = [p for p in candidates if is_provider_running(p)]
        if not available:
            logger.warning(
                "suggest_title: no title-capable provider is running (requested=%s)",
                requested_provider.value,
            )
            error = "no_provider_available"
        else:
            for candidate in available:
                try:
                    suggestion = await get_provider_helpers(candidate).generate_title(
                        source, system_prompt, current_title=current_title,
                    )
                except Exception:
                    # A provider that raises is a provider that failed: the
                    # next one still gets its turn, and the reply still goes
                    # out. ``generate_title`` promises ``str | None``, but a
                    # crash while building its client escapes that promise.
                    logger.exception(
                        "suggest_title: %s raised while generating", candidate.value,
                    )
                    suggestion = None
                if suggestion:
                    title_provider = candidate
                    break
                # Normalize a falsy non-None answer, so the checks below and
                # the payload agree on a single "no suggestion" value.
                suggestion = None
            if suggestion is None:
                logger.warning(
                    "suggest_title: every provider failed (requested=%s, tried=%s)",
                    requested_provider.value, [p.value for p in available],
                )
                error = "generation_failed"
            elif title_provider != requested_provider:
                logger.info(
                    "suggest_title: fell back from %s to %s",
                    requested_provider.value, title_provider.value,
                )

    return TitleSuggestionResult(suggestion, requested_provider, title_provider, error)
