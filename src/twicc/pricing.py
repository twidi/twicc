"""Cross-provider pricing primitives.

Defines the provider-neutral types and helpers used to:

- normalise a token usage payload into :class:`TokenUsage` (each provider's
  compute layer maps its raw counters into this shape);
- fetch the OpenRouter model list **once** via
  :func:`fetch_openrouter_models`, filter it per provider through
  :func:`extract_provider_prices`, and persist the result into
  :class:`ModelPrice` via :func:`persist_provider_prices` —
  :func:`sync_provider_model_prices` chains the three for the single
  provider case while a multi-provider orchestrator can share the
  ``models`` payload across every provider's call;
- compute the per-line cost from a :class:`TokenUsage` and a model_id,
  with the same fallback chain for every provider (exact model price →
  same-family lower / higher version → :class:`FamilyPrices` defaults
  declared on the helper);
- compute the per-line context window usage (sum of token counts).

The provider-specific bits are kept narrow and live on
:class:`BaseProviderHelpers`: the OpenRouter prefix, the
``DEFAULT_FAMILY_PRICES`` mapping, the ``extract_family_and_version``
parser, and the ``compute_cache_write_1h_price`` rule (Claude is the
exception that bills 1h cache writes at ``prompt_price * 2`` while every
other provider matches the 5-minute price).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

import httpx
from django.db import transaction

if TYPE_CHECKING:
    from twicc.core.enums import Provider

logger = logging.getLogger(__name__)


# OpenRouter API endpoint for model pricing data
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class TokenUsage(NamedTuple):
    """Provider-neutral token counts used by :func:`calculate_line_cost`.

    Each provider's compute layer is responsible for converting its own
    usage payload (e.g. Anthropic's ``cache_creation.ephemeral_5m_input_tokens``
    breakdown, OpenAI's flat ``cached_tokens`` field) into this shape so
    the cross-provider cost calculator has a stable input.
    """
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_write_input_tokens_5m: int
    cache_write_input_tokens_1h: int


class FamilyPrices(NamedTuple):
    """Default per-family prices in USD per million tokens.

    Used as the last-resort fallback when no :class:`ModelPrice` row
    matches the requested ``model_id`` and no other version of the same
    family is in the DB. Each provider declares its own family-keyed
    mapping in :attr:`BaseProviderHelpers.DEFAULT_FAMILY_PRICES`.
    """
    input_price: Decimal
    output_price: Decimal
    cache_read_price: Decimal
    cache_write_5m_price: Decimal
    cache_write_1h_price: Decimal


def fetch_openrouter_models() -> list[dict]:
    """Fetch the full OpenRouter models list once and return its raw entries.

    Caller code filters by provider prefix and converts pricing fields
    to its own shape via :func:`extract_provider_prices`. Kept as a
    thin wrapper so a multi-provider sync orchestrator can fetch once
    and feed every provider from the same response (avoiding N
    redundant calls to the same URL).
    """
    response = httpx.get(OPENROUTER_MODELS_URL, timeout=30)
    response.raise_for_status()
    return response.json().get("data", [])


def extract_provider_prices(
    models: list[dict], provider: Provider,
) -> list[dict]:
    """Filter ``models`` to entries owned by ``provider`` and shape them.

    Reads :attr:`BaseProviderHelpers.OPENROUTER_MODEL_PREFIX` to decide
    which OpenRouter rows belong to this provider, converts the
    per-token rates into per-million ``Decimal`` values, and exposes
    them under the names used by the :class:`ModelPrice` columns. The
    1-hour cache-write price is computed via
    :meth:`BaseProviderHelpers.compute_cache_write_1h_price`.

    Returns dicts that can be fed straight to ``ModelPrice.objects.create``
    once a ``provider`` and ``effective_date`` are added.
    """
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers(provider)
    prefix = helpers.OPENROUTER_MODEL_PREFIX
    if not prefix:
        raise ValueError(
            f"Provider {provider.value!r} has no OPENROUTER_MODEL_PREFIX configured"
        )

    results: list[dict] = []
    for model in models:
        model_id = model.get("id", "")
        if not model_id.startswith(prefix):
            continue

        pricing = model.get("pricing", {})
        prompt = Decimal(pricing.get("prompt", "0")) * 1_000_000
        completion = Decimal(pricing.get("completion", "0")) * 1_000_000
        cache_read = Decimal(pricing.get("input_cache_read", "0")) * 1_000_000
        cache_write_5m = Decimal(pricing.get("input_cache_write", "0")) * 1_000_000
        cache_write_1h = helpers.compute_cache_write_1h_price(prompt, cache_write_5m)

        # Round to the stored precision (``ModelPrice`` keeps 6 decimals): a
        # per-token price with a repeating decimal (Google publishes some
        # cache prices as 0.0833333…/M) would otherwise never compare equal to
        # its stored copy, and every sync would insert a duplicate row.
        results.append(
            {
                "model_id": model_id,
                "input_price": _price_quantum(prompt),
                "output_price": _price_quantum(completion),
                "cache_read_price": _price_quantum(cache_read),
                "cache_write_5m_price": _price_quantum(cache_write_5m),
                "cache_write_1h_price": _price_quantum(cache_write_1h),
            }
        )

    return results


_PRICE_PLACES = Decimal("0.000001")


def _price_quantum(value: Decimal) -> Decimal:
    """``value`` rounded to the precision of the ``ModelPrice`` price columns."""
    return value.quantize(_PRICE_PLACES)


def persist_provider_prices(
    provider: Provider, prices: list[dict],
) -> dict[str, int]:
    """Persist ``prices`` for ``provider`` into :class:`ModelPrice`.

    A new row is inserted only when prices have actually changed since
    the last known values for the same ``(provider, model_id)``,
    preserving price history for accurate historical cost calculations.
    Invalidates the in-memory cache when at least one row was created.
    """
    from twicc.core.models import ModelPrice

    today = date.today()
    stats = {"created": 0, "unchanged": 0}
    provider_value = provider.value

    for price_data in prices:
        model_id = price_data["model_id"]
        latest = (
            ModelPrice.objects
            .filter(provider=provider_value, model_id=model_id)
            .order_by("-effective_date")
            .first()
        )

        if latest and (
            latest.input_price == price_data["input_price"]
            and latest.output_price == price_data["output_price"]
            and latest.cache_read_price == price_data["cache_read_price"]
            and latest.cache_write_5m_price == price_data["cache_write_5m_price"]
            and latest.cache_write_1h_price == price_data["cache_write_1h_price"]
        ):
            stats["unchanged"] += 1
            continue

        ModelPrice.objects.create(
            provider=provider_value,
            model_id=model_id,
            effective_date=today,
            **{k: v for k, v in price_data.items() if k != "model_id"},
        )
        stats["created"] += 1

    if stats["created"] > 0:
        # Defer the in-process cache invalidation until the surrounding
        # transaction commits. R17#5 wraps every call to this helper in
        # ``transaction.atomic`` on the consumer's single-writer path; without
        # ``on_commit`` the new rows are still uncommitted when we invalidate,
        # and a concurrent reader can repopulate the cache from the OLD
        # committed rows — leaving stale prices cached *after* our commit.
        # When no transaction is active (legacy / test-time auto-commit call
        # sites), Django runs the callback immediately, so the non-atomic
        # path behaves the same as before.
        transaction.on_commit(ModelPrice.invalidate_price_cache)

    return stats


def sync_provider_model_prices(provider: Provider) -> dict[str, int]:
    """Single-provider end-to-end sync: fetch + filter + persist.

    Convenience wrapper used when only one provider needs syncing
    (e.g. each provider's own periodic loop in
    :mod:`twicc.pricing_task`). Multi-provider orchestrators should
    instead call :func:`fetch_openrouter_models` once and feed
    :func:`extract_provider_prices` + :func:`persist_provider_prices`
    per provider so the HTTP fetch is shared.
    """
    models = fetch_openrouter_models()
    prices = extract_provider_prices(models, provider)
    return persist_provider_prices(provider, prices)


def calculate_line_cost(
    provider: Provider,
    usage: TokenUsage,
    model_id: str,
    line_date: date,
) -> Decimal | None:
    """Compute the cost of one line in USD from its :class:`TokenUsage`.

    Pricing fallback chain (handled inside :meth:`ModelPrice.get_price_for_date`):

    1. exact ``(provider, model_id)`` row valid at ``line_date``;
    2. exact ``(provider, model_id)`` oldest known row (for old messages);
    3. same family as ``model_id``, lower version (closest);
    4. same family as ``model_id``, higher version (closest);
    5. helper's :attr:`DEFAULT_FAMILY_PRICES` keyed by family — when the
       provider can extract one from ``model_id``;
    6. ``None`` (caller decides what to do).

    Returns the cost quantised to 6 decimal places to match the
    :class:`SessionItem` ``cost`` precision.
    """
    from twicc.core.models import ModelPrice
    from twicc.providers.helpers import get_provider_helpers

    price = ModelPrice.get_price_for_date(provider, model_id, line_date)

    if price:
        input_price = price.input_price
        output_price = price.output_price
        cache_read_price = price.cache_read_price
        cache_write_5m_price = price.cache_write_5m_price
        cache_write_1h_price = price.cache_write_1h_price
    else:
        helpers = get_provider_helpers(provider)
        family, _ = helpers.extract_family_and_version(model_id)
        if not family:
            return None
        defaults = helpers.DEFAULT_FAMILY_PRICES.get(family)
        if defaults is None:
            return None
        input_price = defaults.input_price
        output_price = defaults.output_price
        cache_read_price = defaults.cache_read_price
        cache_write_5m_price = defaults.cache_write_5m_price
        cache_write_1h_price = defaults.cache_write_1h_price

    cost = (
        Decimal(usage.input_tokens) * input_price
        + Decimal(usage.output_tokens) * output_price
        + Decimal(usage.cache_read_input_tokens) * cache_read_price
        + Decimal(usage.cache_write_input_tokens_5m) * cache_write_5m_price
        + Decimal(usage.cache_write_input_tokens_1h) * cache_write_1h_price
    ) / Decimal(1_000_000)

    return cost.quantize(Decimal("0.000001"))


def calculate_line_context_usage(usage: TokenUsage) -> int:
    """Total tokens for the API call represented by ``usage``.

    Provider-neutral: every counter declared on :class:`TokenUsage`
    contributes to the context window, regardless of which provider
    emitted it.
    """
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_input_tokens
        + usage.cache_write_input_tokens_5m
        + usage.cache_write_input_tokens_1h
    )
