"""
Pricing utilities for calculating API costs and synchronizing model prices.

This module provides functions for:
- Extracting model family and version from raw model names
- Calculating line-level costs based on token usage
- Calculating context usage (total tokens) from usage data
- Fetching and synchronizing Anthropic model prices from OpenRouter API
"""

from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

import httpx

from twicc_poc.core.models import DEFAULT_FAMILY_PRICES, ModelPrice


# OpenRouter API endpoint for model pricing data
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class ModelInfo(NamedTuple):
    """
    Extracted model information from a raw model name.

    Attributes:
        family: The model family name (e.g., "opus", "sonnet", "haiku")
        version: The model version (e.g., "4.5", "4", "3.7")
    """

    family: str
    version: str


def extract_model_info(raw_name: str) -> ModelInfo | None:
    """
    Extract family and version from a raw model name.

    Handles various Claude model naming patterns found in JSONL files.

    Examples:
        "claude-opus-4-5-20251101" -> ModelInfo("opus", "4.5")
        "claude-sonnet-4" -> ModelInfo("sonnet", "4")
        "claude-3-7-sonnet" -> ModelInfo("sonnet", "3.7")

    Args:
        raw_name: The raw model name from API response (e.g., "claude-opus-4-5-20251101")

    Returns:
        ModelInfo with family and version extracted, or None if format not recognized.
    """
    families = {"opus", "sonnet", "haiku"}

    if not raw_name.lower().startswith("claude-"):
        return None

    parts = raw_name.lower().removeprefix("claude-").split("-")

    family = None
    version_parts = []

    for part in parts:
        if part in families:
            family = part
        elif part.isdigit() and len(version_parts) < 2:
            version_parts.append(part)
        elif version_parts:
            # Version is complete, rest is suffix (date, etc.)
            break

    if not family or not version_parts:
        return None

    version = ".".join(version_parts)
    return ModelInfo(family=family, version=version)


def parse_timestamp_to_date(timestamp: str) -> date | None:
    """
    Parse an ISO timestamp string to a date object.

    Args:
        timestamp: ISO format timestamp (e.g., "2026-01-22T10:53:42.927Z")

    Returns:
        date object, or None if parsing fails.
    """
    if not timestamp:
        return None

    try:
        # Handle ISO format with or without timezone suffix
        # Remove trailing 'Z' if present and parse
        clean_timestamp = timestamp.rstrip("Z")
        # Try to parse with microseconds first, then without
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(clean_timestamp, fmt).date()
            except ValueError:
                continue
        return None
    except (ValueError, TypeError):
        return None


def _get_family_from_model_id(model_id: str) -> str | None:
    """Extract family from model_id: 'anthropic/claude-opus-4.5' -> 'opus'"""
    if not model_id.startswith("anthropic/claude-"):
        return None
    suffix = model_id.removeprefix("anthropic/claude-")
    parts = suffix.split("-")
    return parts[0] if parts else None


def calculate_line_cost(
    usage: dict,
    model_id: str,
    line_date: date,
) -> Decimal | None:
    """
    Calculate the cost of a line from its token usage.

    Uses the pricing information from ModelPrice for the given model and date.
    Falls back to DEFAULT_FAMILY_PRICES if no price found in database.

    Applies the formula:
        cost = (
            input_tokens * price_input
            + output_tokens * price_output
            + cache_read_input_tokens * price_cache_read
            + cache_creation.ephemeral_5m_input_tokens * price_cache_write_5m
            + cache_creation.ephemeral_1h_input_tokens * price_cache_write_1h
        ) / 1_000_000

    Args:
        usage: Dict with token counts (input_tokens, output_tokens,
               cache_read_input_tokens, cache_creation_input_tokens,
               cache_creation.ephemeral_5m_input_tokens, cache_creation.ephemeral_1h_input_tokens)
        model_id: OpenRouter model ID (e.g., "anthropic/claude-opus-4.5")
        line_date: Date of the message for historical price lookup

    Returns:
        Cost in USD as Decimal. Returns None only if model family is unknown.
    """
    price = ModelPrice.get_price_for_date(model_id, line_date)

    if price:
        # Use price from database
        input_price = price.input_price
        output_price = price.output_price
        cache_read_price = price.cache_read_price
        cache_write_5m_price = price.cache_write_5m_price
        cache_write_1h_price = price.cache_write_1h_price
    else:
        # Fallback to default family prices
        family = _get_family_from_model_id(model_id)
        if not family or family not in DEFAULT_FAMILY_PRICES:
            return None

        default_prices = DEFAULT_FAMILY_PRICES[family]
        input_price = default_prices["input_price"]
        output_price = default_prices["output_price"]
        cache_read_price = default_prices["cache_read_price"]
        cache_write_5m_price = default_prices["cache_write_5m_price"]
        cache_write_1h_price = default_prices["cache_write_1h_price"]

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)

    # Cache creation: prefer detailed ephemeral breakdown if available,
    # otherwise fallback to cache_creation_input_tokens with 5min price
    cache_creation = usage.get("cache_creation", {})
    if cache_creation:
        cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
        cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
    else:
        cache_5m = usage.get("cache_creation_input_tokens", 0)
        cache_1h = 0

    cost = (
        Decimal(input_tokens) * input_price
        + Decimal(output_tokens) * output_price
        + Decimal(cache_read) * cache_read_price
        + Decimal(cache_5m) * cache_write_5m_price
        + Decimal(cache_1h) * cache_write_1h_price
    ) / Decimal(1_000_000)

    return cost


def calculate_line_context_usage(usage: dict) -> int:
    """
    Calculate the context usage (total tokens) from a line's usage data.

    Context usage represents the total number of tokens at a given point
    in the conversation. This is not cumulative across messages but rather
    the total for the current API call.

    Args:
        usage: Dict with token counts (input_tokens, output_tokens,
               cache_read_input_tokens, cache_creation_input_tokens)

    Returns:
        Total token count as integer.
    """
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def fetch_anthropic_prices() -> list[dict]:
    """
    Fetch Anthropic model prices from the OpenRouter API.

    Calls the OpenRouter models endpoint and extracts pricing information
    for all Anthropic models. Prices are converted from per-token to
    per-million-tokens format.

    Returns:
        List of dicts, each containing:
            - model_id: OpenRouter model ID (e.g., "anthropic/claude-opus-4.5")
            - input_price: Price per million input tokens (Decimal)
            - output_price: Price per million output tokens (Decimal)
            - cache_read_price: Price per million cache read tokens (Decimal)
            - cache_write_5m_price: Price per million 5-minute cache write tokens (Decimal)
            - cache_write_1h_price: Price per million 1-hour cache write tokens (Decimal)

    Raises:
        httpx.HTTPStatusError: If the API request fails
        httpx.TimeoutException: If the request times out
    """
    response = httpx.get(OPENROUTER_MODELS_URL, timeout=30)
    response.raise_for_status()
    data = response.json()

    results = []
    for model in data.get("data", []):
        model_id = model.get("id", "")
        if not model_id.startswith("anthropic/"):
            continue

        pricing = model.get("pricing", {})

        # Convert from per-token to per-million tokens
        # OpenRouter prices are strings for precision
        prompt = Decimal(pricing.get("prompt", "0")) * 1_000_000
        completion = Decimal(pricing.get("completion", "0")) * 1_000_000
        cache_read = Decimal(pricing.get("input_cache_read", "0")) * 1_000_000
        cache_write_5m = Decimal(pricing.get("input_cache_write", "0")) * 1_000_000
        # Cache 1h price = input_price * 2 (per Anthropic documentation)
        cache_write_1h = prompt * 2

        results.append(
            {
                "model_id": model_id,
                "input_price": prompt,
                "output_price": completion,
                "cache_read_price": cache_read,
                "cache_write_5m_price": cache_write_5m,
                "cache_write_1h_price": cache_write_1h,
            }
        )

    return results


def sync_model_prices() -> dict[str, int]:
    """
    Synchronize model prices from OpenRouter to the database.

    Fetches current prices from OpenRouter and creates new ModelPrice entries
    only when prices have changed from the last known values for each model.
    This preserves price history for accurate historical cost calculations.

    Returns:
        Dict with statistics:
            - 'created': Number of new price entries created
            - 'unchanged': Number of models with unchanged prices
    """
    today = date.today()
    prices = fetch_anthropic_prices()

    stats = {"created": 0, "unchanged": 0}

    for price_data in prices:
        model_id = price_data["model_id"]

        # Get the most recent price entry for this model
        latest = (
            ModelPrice.objects.filter(model_id=model_id).order_by("-effective_date").first()
        )

        # Check if prices have changed
        if latest and (
            latest.input_price == price_data["input_price"]
            and latest.output_price == price_data["output_price"]
            and latest.cache_read_price == price_data["cache_read_price"]
            and latest.cache_write_5m_price == price_data["cache_write_5m_price"]
            and latest.cache_write_1h_price == price_data["cache_write_1h_price"]
        ):
            stats["unchanged"] += 1
            continue

        # Create new price entry with today's effective date
        ModelPrice.objects.create(
            model_id=model_id,
            effective_date=today,
            **{k: v for k, v in price_data.items() if k != "model_id"},
        )
        stats["created"] += 1

    return stats
