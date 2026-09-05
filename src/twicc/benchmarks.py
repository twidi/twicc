"""Cross-provider model-benchmark ingestion.

DeepSWE (datacurve.ai) publishes a static leaderboard JSON that scores each
model — per ``(model, reasoning_effort)`` on the "mini-swe-agent" harness — on
a SWE-bench-style task set. We refresh it once a day, keep only the models
present in our own per-provider registry (normalising DeepSWE's dashed ids to
our internal SDK identifiers), and upsert the handful of metrics we care about
into :class:`ModelBenchmark`.

The lifecycle mirrors the OpenRouter price sync (:mod:`twicc.pricing` +
:mod:`twicc.pricing_task`):

- :func:`fetch_deepswe_leaderboard` does the single HTTP GET (+ orjson parse);
- :func:`extract_benchmarks` maps/filters the rows against the model registry
  (pure Python, no DB) so it can run on a worker thread;
- :func:`persist_benchmarks` upserts the result into :class:`ModelBenchmark`,
  routed through the single-writer DB path via
  :class:`_PersistModelBenchmarksJob` (see :mod:`twicc.benchmarks_task`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import orjson
from django.utils import timezone

if TYPE_CHECKING:
    from twicc.core.enums import Provider

logger = logging.getLogger(__name__)


# Static DeepSWE leaderboard JSON — a single unauthenticated GET, no params.
DEEPSWE_LEADERBOARD_URL = "https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json"

# Only this harness is scored today; filtering to it keeps exactly one row per
# (model, reasoning_effort) should DeepSWE ever publish more harnesses.
DEEPSWE_HARNESS = "mini-swe-agent"

# DeepSWE model id -> our internal SDK model identifier (the ``full_name`` in
# each provider's ``MODEL_VERSIONS`` registry). DeepSWE writes every id with
# dashes (``gpt-5-6-sol``); our Claude ids already match, while our GPT ids use
# a dot in the version (``gpt-5.6-sol``). Only the models we support appear
# here; any other DeepSWE model (gemini/glm/kimi/deepseek/grok/minimax/qwen) is
# absent and therefore dropped. Every mapped identifier is re-validated against
# the live registry in :func:`extract_benchmarks`, so a model removed from the
# registry is dropped even if it stays listed here.
DEEPSWE_TO_INTERNAL_MODEL = {
    # Claude models (claude_code provider) — DeepSWE ids already match full_name
    "claude-fable-5-1": "claude-fable-5-1",
    "claude-fable-5": "claude-fable-5",
    "claude-opus-5": "claude-opus-5",
    "claude-opus-4-8": "claude-opus-4-8",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    # GPT models (codex provider) — DeepSWE dashes -> our dotted full_name
    "gpt-6-astra": "gpt-6-astra",
    "gpt-5-6-sol": "gpt-5.6-sol",
    "gpt-5-6-terra": "gpt-5.6-terra",
    "gpt-5-6-luna": "gpt-5.6-luna",
    "gpt-5-5": "gpt-5.5",
    "gpt-5-4": "gpt-5.4",
}


def fetch_deepswe_leaderboard() -> list[dict]:
    """Fetch the DeepSWE leaderboard once and return its raw ``rows``.

    A single blocking HTTP GET on a static JSON file (no auth, no params),
    parsed with orjson. Caller (:func:`extract_benchmarks`) maps/filters the
    rows; kept as a thin wrapper mirroring :func:`fetch_openrouter_models`.
    """
    response = httpx.get(DEEPSWE_LEADERBOARD_URL, timeout=30)
    response.raise_for_status()
    return orjson.loads(response.content).get("rows", [])


def _internal_model_registry() -> dict[str, Provider]:
    """Build a ``{full_name: Provider}`` index over every registered model.

    The per-provider ``MODEL_VERSIONS`` lists are the single source of truth
    for which models we support and which provider owns each one.
    """
    from twicc.providers.helpers import get_provider_helpers_registry

    index: dict[str, Provider] = {}
    for helpers in get_provider_helpers_registry().values():
        for mv in helpers.MODEL_VERSIONS:
            index[mv.full_name] = mv.provider
    return index


def extract_benchmarks(rows: list[dict]) -> list[dict]:
    """Map/filter raw DeepSWE ``rows`` down to our supported models.

    Pure, I/O-free transformation (runs on a worker thread): keep only the
    ``mini-swe-agent`` harness, map each DeepSWE model id to our internal
    identifier + provider via :data:`DEEPSWE_TO_INTERNAL_MODEL` and the live
    model registry (dropping anything we don't support), and pick out the
    metrics we store. Returns dicts ready for :func:`persist_benchmarks`.
    """
    registry = _internal_model_registry()

    results: list[dict] = []
    for row in rows:
        if row.get("harness") != DEEPSWE_HARNESS:
            continue

        internal_model = DEEPSWE_TO_INTERNAL_MODEL.get(row.get("model", ""))
        if internal_model is None:
            continue  # not one of our models (gemini/glm/kimi/...)

        provider = registry.get(internal_model)
        if provider is None:
            continue  # mapped, but no longer registered — drop it

        reasoning_effort = row.get("reasoning_effort")
        if not reasoning_effort:
            continue  # can't form the (provider, model, effort) key

        results.append({
            "provider": provider.value,
            "model": internal_model,
            "reasoning_effort": reasoning_effort,
            "pass_at_1": row.get("pass_at_1"),
            "pass_at_4": row.get("pass_at_4"),
            "mean_cost_usd": row.get("mean_cost_usd"),
            "median_duration_seconds": row.get("median_duration_seconds"),
        })

    return results


def persist_benchmarks(benchmarks: list[dict]) -> dict[str, int]:
    """Upsert ``benchmarks`` into :class:`ModelBenchmark`.

    For each ``(provider, model, reasoning_effort)`` present in the fresh data,
    update the metric fields and set ``updated_at`` to the refresh instant, or
    insert the row if it's new. Rows absent from ``benchmarks`` are left
    untouched — a model that stops being reported keeps its last-known metrics
    and ``updated_at``. Returns ``{"created": N, "updated": M}``.
    """
    from twicc.core.models import ModelBenchmark

    now = timezone.now()
    stats = {"created": 0, "updated": 0}

    for benchmark in benchmarks:
        _, created = ModelBenchmark.objects.update_or_create(
            provider=benchmark["provider"],
            model=benchmark["model"],
            reasoning_effort=benchmark["reasoning_effort"],
            defaults={
                "pass_at_1": benchmark["pass_at_1"],
                "pass_at_4": benchmark["pass_at_4"],
                "mean_cost_usd": benchmark["mean_cost_usd"],
                "median_duration_seconds": benchmark["median_duration_seconds"],
                "updated_at": now,
            },
        )
        stats["created" if created else "updated"] += 1

    return stats
