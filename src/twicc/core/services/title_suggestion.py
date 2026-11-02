"""Shared title generation provider selection and fallback."""

import logging
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.openrouter_text import openrouter_key_set
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.state import is_provider_running
from twicc.title_openrouter import generate_openrouter_title

logger = logging.getLogger(__name__)

TITLE_SUGGESTION_MODEL_PROVIDERS = {
    "haiku": Provider.CLAUDE_CODE,
    "luna": Provider.CODEX,
}

# The title model of each title-capable provider: the inverse of the table above.
PROVIDER_TITLE_MODELS = {provider: model for model, provider in TITLE_SUGGESTION_MODEL_PROVIDERS.items()}

# OpenRouter: no provider behind it, usable only with the OpenRouter key.
# A failure falls back to the agents, from the session provider (as "provider").
OPENROUTER_TITLE_MODEL = "openrouter"

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
    # The model asked for (``"openrouter"``, or the model of ``requested_provider``)
    # and the model that made the title (None when none did).
    requested_model: str | None = None
    title_model: str | None = None


def requested_title_model(title_model: str | None, session_provider: Provider) -> str | None:
    """The model a title request really asks for.

    ``openrouter`` without the OpenRouter key, and any value that names no model, work
    as ``provider``: the model of the session provider.
    """
    if title_model == OPENROUTER_TITLE_MODEL and openrouter_key_set():
        return OPENROUTER_TITLE_MODEL
    return PROVIDER_TITLE_MODELS.get(TITLE_SUGGESTION_MODEL_PROVIDERS.get(title_model, session_provider))


async def _generate_with_openrouter(
    source: str, system_prompt: str, current_title: str | None,
) -> str | None:
    try:
        suggestion = await generate_openrouter_title(source, system_prompt, current_title=current_title)
    except Exception:
        # ``generate_openrouter_title`` promises ``str | None``; a raise is a failure.
        logger.exception("suggest_title: openrouter raised while generating")
        return None
    # A falsy non-None answer is no suggestion too.
    return suggestion or None


async def suggest_title(
    source: str | None,
    system_prompt: str,
    session_provider: Provider,
    *,
    title_model: str | None = None,
    no_fallback: bool = False,
    current_title: str | None = None,
) -> TitleSuggestionResult:
    """Generate a title through the requested model or an available fallback.

    With OpenRouter asked (and the OpenRouter key set), OpenRouter is tried first, once; when
    it fails, the agents are tried as for ``provider``, unless ``no_fallback``.
    """
    requested_provider = TITLE_SUGGESTION_MODEL_PROVIDERS.get(title_model, session_provider)
    requested_model = requested_title_model(title_model, session_provider)
    openrouter_asked = requested_model == OPENROUTER_TITLE_MODEL

    def result(
        *,
        suggestion: str | None = None,
        title_provider: Provider | None = None,
        error: str | None = None,
        used_model: str | None = None,
    ) -> TitleSuggestionResult:
        if used_model is None and title_provider is not None:
            used_model = PROVIDER_TITLE_MODELS.get(title_provider)
        return TitleSuggestionResult(
            suggestion, requested_provider, title_provider, error, requested_model, used_model,
        )

    if not source:
        return result(error="no_prompt")

    if openrouter_asked:
        # Before any provider check: OpenRouter needs no running agent.
        suggestion = await _generate_with_openrouter(source, system_prompt, current_title)
        if suggestion is not None:
            return result(suggestion=suggestion, used_model=OPENROUTER_TITLE_MODEL)
        if no_fallback:
            logger.warning("suggest_title: openrouter failed (no fallback)")
            return result(error="generation_failed")

    candidates = [requested_provider]
    if not no_fallback:
        candidates += [p for p in TITLE_CAPABLE_PROVIDERS if p != requested_provider]
    available = [p for p in candidates if is_provider_running(p)]
    if not available:
        logger.warning(
            "suggest_title: no title-capable provider is running (requested=%s)",
            requested_model if openrouter_asked else requested_provider.value,
        )
        # A model was tried: the title could not be generated.
        return result(error="generation_failed" if openrouter_asked else "no_provider_available")

    suggestion = None
    title_provider = None
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
            requested_model if openrouter_asked else requested_provider.value,
            [p.value for p in available],
        )
        return result(error="generation_failed")
    if openrouter_asked or title_provider != requested_provider:
        logger.info(
            "suggest_title: fell back from %s to %s",
            requested_model if openrouter_asked else requested_provider.value, title_provider.value,
        )
    return result(suggestion=suggestion, title_provider=title_provider)
