"""Tests for the per-provider model id parsers.

Each backend provider ships two parsers for the same set of models:

- ``BaseProviderHelpers.extract_family_and_version`` parses OpenRouter
  ids (``anthropic/claude-...``, ``openai/gpt-...``) into
  ``(family, version)``.
- ``providers/<name>/pricing.py:extract_model_info`` parses the raw
  JSONL ``message.model`` (Claude Code) or
  ``turn_context.payload.model`` (Codex) names into a ``ModelInfo``.

The two encodings can differ — Claude OpenRouter ids use a dotted
``4.5`` while JSONL emits ``4-5``, OpenRouter accepts ``:thinking`` /
``-fast`` colon variants and JSONL strips the colon, JSONL alone
carries an optional date stamp; Codex differs only by the ``openai/``
prefix — so each provider has its own parsers. But for any given
model both parsers must agree on ``(family, version)``.

The :data:`PROVIDERS` table below is the single ground truth: each
:class:`ProviderSpec` lists every observed encoding for every model
plus the strings each parser must reject. Both parsers of every
provider are exercised from those tables so a future divergence (e.g.
the shared ``CLAUDE_FAMILIES`` set going out of sync with one parser,
or a JSONL-only stamp shape changing) is caught immediately.
"""

from __future__ import annotations

from typing import NamedTuple
from collections.abc import Callable

import pytest

from twicc.core.enums import Provider
from twicc.providers.claude_code.pricing import (
    CLAUDE_FAMILIES,
    extract_model_info as claude_extract_model_info,
)
from twicc.providers.codex.pricing import (
    extract_model_info as codex_extract_model_info,
)
from twicc.providers.helpers import get_provider_helpers


# =============================================================================
# Ground truth tables — one row per model, one spec per provider
# =============================================================================


class ModelCase(NamedTuple):
    """One model and the encodings each parser must handle.

    ``openrouter_ids`` lists every OpenRouter id that maps to this
    ``(family, version)`` — usually just one entry, but can carry more
    when the same model is exposed under multiple ids (e.g. dated
    snapshots like ``openai/gpt-3.5-turbo-0613`` and the canonical
    ``openai/gpt-3.5-turbo`` both reduce to ``("gpt-turbo", "3.5")``).
    Empty when the model has no OpenRouter counterpart at all (e.g.
    JSONL-only artefacts of a transitional release).

    ``jsonl_names`` lists every JSONL ``message.model`` /
    ``turn_context.payload.model`` string we have actually observed for
    this model (with and without date stamp, etc.). Add new observed
    names here as we see them; the cross-parser invariant test catches
    any drift between the two encodings.
    """
    family: str
    version: str
    openrouter_ids: tuple[str, ...] = ()
    jsonl_names: tuple[str, ...] = ()


class ProviderSpec(NamedTuple):
    """Everything needed to exercise one provider's two parsers.

    ``extract_model_info`` is the provider-specific JSONL parser; the
    OpenRouter parser is reached through
    :func:`get_provider_helpers(provider).extract_family_and_version`.
    """
    provider: Provider
    extract_model_info: Callable[[str], object | None]
    model_cases: list[ModelCase]
    openrouter_rejections: list[str]
    jsonl_rejections: list[str]


# -----------------------------------------------------------------------------
# Claude Code
# -----------------------------------------------------------------------------


CLAUDE_MODEL_CASES: list[ModelCase] = [
    # New layout: family-then-version
    ModelCase("fable", "5.1", ("anthropic/claude-fable-5.1",), ("claude-fable-5-1",)),
    ModelCase("fable", "5", ("anthropic/claude-fable-5",), ("claude-fable-5",)),
    ModelCase("opus", "5", ("anthropic/claude-opus-5",), ("claude-opus-5",)),
    ModelCase("opus", "4.8", ("anthropic/claude-opus-4.8",), ("claude-opus-4-8",)),
    ModelCase("opus", "4.7", ("anthropic/claude-opus-4.7",), ("claude-opus-4-7",)),
    ModelCase("opus", "4.6", ("anthropic/claude-opus-4.6",), ("claude-opus-4-6",)),
    ModelCase("opus", "4.5", ("anthropic/claude-opus-4.5",),
              ("claude-opus-4-5", "claude-opus-4-5-20251101")),
    ModelCase("opus", "4.1", ("anthropic/claude-opus-4.1",), ("claude-opus-4-1",)),
    ModelCase("opus", "4", ("anthropic/claude-opus-4",), ("claude-opus-4",)),
    ModelCase("sonnet", "4.6", ("anthropic/claude-sonnet-4.6",), ("claude-sonnet-4-6",)),
    ModelCase("sonnet", "4.5", ("anthropic/claude-sonnet-4.5",),
              ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929")),
    ModelCase("sonnet", "4", ("anthropic/claude-sonnet-4",), ("claude-sonnet-4",)),
    ModelCase("haiku", "4.5", ("anthropic/claude-haiku-4.5",),
              ("claude-haiku-4-5",)),

    # Legacy layout: version-then-family
    ModelCase("sonnet", "3.7", ("anthropic/claude-3.7-sonnet",), ("claude-3-7-sonnet",)),
    ModelCase("sonnet", "3.5", ("anthropic/claude-3.5-sonnet",),
              ("claude-3-5-sonnet", "claude-3-5-sonnet-20241022")),
    ModelCase("haiku", "3.5", ("anthropic/claude-3.5-haiku",),
              ("claude-3-5-haiku", "claude-3-5-haiku-20241022")),
    ModelCase("haiku", "3", ("anthropic/claude-3-haiku",),
              ("claude-3-haiku", "claude-3-haiku-20240307")),

    # Variants. JSONL strips the colon and dash-joins the variant name.
    ModelCase("sonnet-thinking", "3.7", ("anthropic/claude-3.7-sonnet:thinking",),
              ("claude-3-7-sonnet-thinking",)),
    ModelCase("opus-fast", "4.6", ("anthropic/claude-opus-4.6-fast",),
              ("claude-opus-4-6-fast",)),
]


CLAUDE_SPEC = ProviderSpec(
    provider=Provider.CLAUDE_CODE,
    extract_model_info=claude_extract_model_info,
    model_cases=CLAUDE_MODEL_CASES,
    openrouter_rejections=[
        "openai/gpt-5.4",                # different OpenRouter provider
        "openai/gpt-5.4-mini",
        "anthropic/claude-quasar-7",     # unknown family
        "anthropic/claude-",              # bare prefix
        "",                               # empty
    ],
    jsonl_rejections=[
        "gpt-5.4",                # different provider
        "opus-4-7",               # missing the "claude-" prefix
        "claude-quasar-7",        # unknown family
        "claude-opus",            # no version
        "",                       # empty
    ],
)


# -----------------------------------------------------------------------------
# Codex
# -----------------------------------------------------------------------------
#
# OpenRouter side is exhaustive: every ``openai/gpt-*`` id observed in
# the live ``/api/v1/models`` response is represented either as a
# parseable :class:`ModelCase` or in
# :attr:`CODEX_SPEC.openrouter_rejections`. JSONL side mirrors the
# OpenRouter id with the ``openai/`` prefix stripped — Codex CLI does
# not add date stamps or colon variants the way Anthropic does, so each
# case has a single ``jsonl_names`` entry.


CODEX_MODEL_CASES: list[ModelCase] = [
    # gpt (flagship line — bare family, no variant suffix). ``gpt-4-0314``
    # is a dated snapshot of ``gpt-4`` so it shares the same case via
    # the date-stamp drop rule.
    ModelCase("gpt", "4",   ("openai/gpt-4", "openai/gpt-4-0314"),
              ("gpt-4", "gpt-4-0314")),
    ModelCase("gpt", "4.1", ("openai/gpt-4.1",), ("gpt-4.1",)),
    ModelCase("gpt", "5",   ("openai/gpt-5",),   ("gpt-5",)),
    ModelCase("gpt", "5.1", ("openai/gpt-5.1",), ("gpt-5.1",)),
    ModelCase("gpt", "5.2", ("openai/gpt-5.2",), ("gpt-5.2",)),
    ModelCase("gpt", "5.4", ("openai/gpt-5.4",), ("gpt-5.4",)),
    ModelCase("gpt", "5.5", ("openai/gpt-5.5",), ("gpt-5.5",)),

    # gpt-5.6 capability tiers (Sol / Terra / Luna). The tier name is a variant
    # suffix, so each tier folds into its own pricing family — they are priced
    # differently, unlike a dated snapshot of the same model.
    ModelCase("gpt-sol",   "5.6", ("openai/gpt-5.6-sol",),   ("gpt-5.6-sol",)),
    ModelCase("gpt-terra", "5.6", ("openai/gpt-5.6-terra",), ("gpt-5.6-terra",)),
    ModelCase("gpt-luna",  "5.6", ("openai/gpt-5.6-luna",),  ("gpt-5.6-luna",)),

    # gpt-codex (Codex CLI's main model)
    ModelCase("gpt-codex", "5",   ("openai/gpt-5-codex",),   ("gpt-5-codex",)),
    ModelCase("gpt-codex", "5.1", ("openai/gpt-5.1-codex",), ("gpt-5.1-codex",)),
    ModelCase("gpt-codex", "5.2", ("openai/gpt-5.2-codex",), ("gpt-5.2-codex",)),
    ModelCase("gpt-codex", "5.3", ("openai/gpt-5.3-codex",), ("gpt-5.3-codex",)),

    # gpt-codex-max / gpt-codex-mini (priced separately from base codex)
    ModelCase("gpt-codex-max",  "5.1", ("openai/gpt-5.1-codex-max",),  ("gpt-5.1-codex-max",)),
    ModelCase("gpt-codex-mini", "5.1", ("openai/gpt-5.1-codex-mini",), ("gpt-5.1-codex-mini",)),

    # gpt-mini / gpt-nano / gpt-pro (size variants)
    ModelCase("gpt-mini", "4.1", ("openai/gpt-4.1-mini",), ("gpt-4.1-mini",)),
    ModelCase("gpt-mini", "5",   ("openai/gpt-5-mini",),   ("gpt-5-mini",)),
    ModelCase("gpt-mini", "5.4", ("openai/gpt-5.4-mini",), ("gpt-5.4-mini",)),
    ModelCase("gpt-nano", "4.1", ("openai/gpt-4.1-nano",), ("gpt-4.1-nano",)),
    ModelCase("gpt-nano", "5",   ("openai/gpt-5-nano",),   ("gpt-5-nano",)),
    ModelCase("gpt-nano", "5.4", ("openai/gpt-5.4-nano",), ("gpt-5.4-nano",)),
    ModelCase("gpt-pro",  "5",   ("openai/gpt-5-pro",),    ("gpt-5-pro",)),
    ModelCase("gpt-pro",  "5.2", ("openai/gpt-5.2-pro",),  ("gpt-5.2-pro",)),
    ModelCase("gpt-pro",  "5.4", ("openai/gpt-5.4-pro",),  ("gpt-5.4-pro",)),
    ModelCase("gpt-pro",  "5.5", ("openai/gpt-5.5-pro",),  ("gpt-5.5-pro",)),

    # gpt-chat (conversational variants)
    ModelCase("gpt-chat", "5",   ("openai/gpt-5-chat",),   ("gpt-5-chat",)),
    ModelCase("gpt-chat", "5.1", ("openai/gpt-5.1-chat",), ("gpt-5.1-chat",)),
    ModelCase("gpt-chat", "5.2", ("openai/gpt-5.2-chat",), ("gpt-5.2-chat",)),
    ModelCase("gpt-chat", "5.3", ("openai/gpt-5.3-chat",), ("gpt-5.3-chat",)),

    # gpt-image / gpt-image-mini / gpt-image-2 (the trailing "2" is
    # treated as part of the variant suffix — singleton family today,
    # but the parser stays consistent should image-3 ship later).
    ModelCase("gpt-image",      "5",   ("openai/gpt-5-image",),      ("gpt-5-image",)),
    ModelCase("gpt-image-mini", "5",   ("openai/gpt-5-image-mini",), ("gpt-5-image-mini",)),
    ModelCase("gpt-image-2",    "5.4", ("openai/gpt-5.4-image-2",),  ("gpt-5.4-image-2",)),

    # gpt-turbo (legacy — gpt-3.5-turbo / gpt-4-turbo). ``gpt-3.5-turbo-0613``
    # is a dated snapshot that the date-stamp drop rule reduces to the
    # same ``(gpt-turbo, 3.5)`` bucket.
    ModelCase("gpt-turbo", "3.5", ("openai/gpt-3.5-turbo", "openai/gpt-3.5-turbo-0613"),
              ("gpt-3.5-turbo", "gpt-3.5-turbo-0613")),
    ModelCase("gpt-turbo", "4",   ("openai/gpt-4-turbo",),   ("gpt-4-turbo",)),

    # gpt-turbo-* (legacy non-dated suffixes — kept as their own
    # singleton families: ``16k`` / ``instruct`` / ``preview`` are
    # genuine variants, not date stamps).
    ModelCase("gpt-turbo-16k",      "3.5", ("openai/gpt-3.5-turbo-16k",),      ("gpt-3.5-turbo-16k",)),
    ModelCase("gpt-turbo-instruct", "3.5", ("openai/gpt-3.5-turbo-instruct",), ("gpt-3.5-turbo-instruct",)),
    ModelCase("gpt-turbo-preview",  "4",   ("openai/gpt-4-turbo-preview",),    ("gpt-4-turbo-preview",)),

    # gpt-preview (the 1106 date stamp is dropped, leaving just the
    # ``preview`` suffix as the family).
    ModelCase("gpt-preview", "4", ("openai/gpt-4-1106-preview",), ("gpt-4-1106-preview",)),
]


CODEX_SPEC = ProviderSpec(
    provider=Provider.CODEX,
    extract_model_info=codex_extract_model_info,
    model_cases=CODEX_MODEL_CASES,
    openrouter_rejections=[
        # ``4o`` mixes a digit with a trailing letter — every gpt-4o-*
        # id falls in this bucket. Documenting the limitation: the
        # parser is intentionally strict on the version segment.
        "openai/gpt-4o",
        "openai/gpt-4o-2024-05-13",
        "openai/gpt-4o-mini",
        "openai/gpt-4o-mini-2024-07-18",
        "openai/gpt-4o-audio-preview",
        "openai/gpt-4o-mini-search-preview",
        "openai/gpt-4o-search-preview",
        # Non-numeric first segment after ``gpt-`` (no version at all).
        "openai/gpt-audio",
        "openai/gpt-audio-mini",
        "openai/gpt-chat-latest",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-120b:free",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-20b:free",
        "openai/gpt-oss-safeguard-20b",
        # Outside the ``openai/gpt-`` namespace.
        "openai/o3-deep-research",
        "anthropic/claude-opus-4.5",
        # Bare or missing prefixes.
        "openai/",
        "openai/gpt-",
        "",
    ],
    jsonl_rejections=[
        "gpt-4o",                # ``4o`` — mixed digit + letter (matches OpenRouter rejection)
        "gpt-4o-mini",
        "gpt-audio",             # no version
        "gpt-chat-latest",       # ``chat`` is not dotted-numeric
        "gpt-oss-120b",          # ``oss`` is not dotted-numeric
        "gpt-",                  # bare prefix without body
        "opus-4-7",              # different provider (Anthropic raw form)
        "claude-opus-4",         # different provider
        "openai/gpt-5-codex",    # JSONL doesn't carry the ``openai/`` prefix
        "",                      # empty
    ],
)


# -----------------------------------------------------------------------------
# Cross-provider parametrize tables
# -----------------------------------------------------------------------------


PROVIDERS: list[ProviderSpec] = [CLAUDE_SPEC, CODEX_SPEC]


def _case_id(spec: ProviderSpec, case: ModelCase) -> str:
    """Build a parametrize id like ``claude_code-opus-4.7``."""
    return f"{spec.provider.value}-{case.family}-{case.version}"


# (spec, case, openrouter_id) flattened so each id shows up as its own
# parametrize entry — same shape as JSONL_PARAMS, since one case can
# carry multiple OpenRouter ids that all reduce to the same family /
# version (e.g. ``openai/gpt-3.5-turbo`` and its dated snapshot
# ``openai/gpt-3.5-turbo-0613``).
OPENROUTER_PARAMS = [
    pytest.param(spec, case, oid, id=f"{_case_id(spec, case)}::{oid}")
    for spec in PROVIDERS
    for case in spec.model_cases
    for oid in case.openrouter_ids
]


# (spec, case, jsonl_name) flattened so each name shows up as its own
# parametrize id and a per-name failure points at the exact string.
JSONL_PARAMS = [
    pytest.param(spec, case, name, id=f"{_case_id(spec, case)}::{name}")
    for spec in PROVIDERS
    for case in spec.model_cases
    for name in case.jsonl_names
]


# (spec, case) for the cross-parser invariant: only models that have
# both encodings can be compared.
CROSS_PARSER_PARAMS = [
    pytest.param(spec, case, id=_case_id(spec, case))
    for spec in PROVIDERS
    for case in spec.model_cases
    if case.openrouter_ids and case.jsonl_names
]


# (spec, model_id) for the OpenRouter rejection cases.
OPENROUTER_REJECTION_PARAMS = [
    pytest.param(spec, model_id, id=f"{spec.provider.value}::{model_id or '<empty>'}")
    for spec in PROVIDERS
    for model_id in spec.openrouter_rejections
]


# (spec, raw_name) for the JSONL rejection cases.
JSONL_REJECTION_PARAMS = [
    pytest.param(spec, raw_name, id=f"{spec.provider.value}::{raw_name or '<empty>'}")
    for spec in PROVIDERS
    for raw_name in spec.jsonl_rejections
]


# =============================================================================
# OpenRouter parser
# =============================================================================


@pytest.mark.parametrize(("spec", "case", "openrouter_id"), OPENROUTER_PARAMS)
def test_openrouter_id_decomposes_to_expected_family_and_version(
    spec, case, openrouter_id,
):
    helpers = get_provider_helpers(spec.provider)
    assert helpers.extract_family_and_version(openrouter_id) == (
        case.family, case.version,
    )


@pytest.mark.parametrize(("spec", "model_id"), OPENROUTER_REJECTION_PARAMS)
def test_openrouter_parser_rejects_unknown_input(spec, model_id):
    helpers = get_provider_helpers(spec.provider)
    assert helpers.extract_family_and_version(model_id) == (None, None)


# =============================================================================
# JSONL parser
# =============================================================================


@pytest.mark.parametrize(("spec", "case", "jsonl_name"), JSONL_PARAMS)
def test_jsonl_name_decomposes_to_expected_family_and_version(
    spec, case, jsonl_name,
):
    info = spec.extract_model_info(jsonl_name)
    assert info is not None
    assert (info.family, info.version) == (case.family, case.version)


@pytest.mark.parametrize(("spec", "raw_name"), JSONL_REJECTION_PARAMS)
def test_jsonl_parser_rejects_unknown_input(spec, raw_name):
    assert spec.extract_model_info(raw_name) is None


# =============================================================================
# Cross-parser invariants
# =============================================================================


@pytest.mark.parametrize(("spec", "case"), CROSS_PARSER_PARAMS)
def test_openrouter_and_jsonl_agree_on_family_and_version(spec, case):
    """Both parsers of a provider must produce the same
    ``(family, version)`` for a model that appears in both encodings —
    including variants that one encoding writes differently from the
    other (e.g. Claude's ``:thinking`` vs ``-thinking``) and dated
    snapshots that the date-stamp drop rule reduces to the canonical
    bucket on both sides.

    Restricted to cases whose JSONL names we have actually observed:
    new combinations should be added to the matching :data:`MODEL_CASES`
    table as we see them, and any drift between the two parsers will
    surface here.
    """
    helpers = get_provider_helpers(spec.provider)
    expected = (case.family, case.version)
    for openrouter_id in case.openrouter_ids:
        openrouter_result = helpers.extract_family_and_version(openrouter_id)
        assert openrouter_result == expected, (
            f"OpenRouter parser drift for {spec.provider.value} "
            f"{case.family} {case.version}: "
            f"{openrouter_id!r} -> {openrouter_result}"
        )
        for jsonl_name in case.jsonl_names:
            info = spec.extract_model_info(jsonl_name)
            assert info is not None
            jsonl_result = (info.family, info.version)
            assert openrouter_result == jsonl_result, (
                f"Parsers disagree for {spec.provider.value} "
                f"{case.family} {case.version}: "
                f"OpenRouter {openrouter_id!r} -> {openrouter_result}, "
                f"JSONL {jsonl_name!r} -> {jsonl_result}"
            )


# =============================================================================
# Provider-specific tests — behaviours that don't generalise cross-provider
# =============================================================================


def test_claude_jsonl_parser_is_case_insensitive():
    """Claude JSONL names should round-trip through any casing the SDK
    might emit. Codex CLI hasn't shown mixed casing in the wild, so we
    don't assert anything for it here.
    """
    info = claude_extract_model_info("Claude-Opus-4-7")
    assert info is not None
    assert (info.family, info.version) == ("opus", "4.7")


def test_claude_openrouter_parser_handles_family_only():
    """Claude OpenRouter ids without a version segment still parse
    family-only: ``anthropic/claude-opus`` → ``("opus", None)``.
    Codex returns ``(None, None)`` in the equivalent case
    (``openai/gpt-`` is in :data:`CODEX_SPEC.openrouter_rejections`)
    because OpenAI never ships a bare-family id.
    """
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    assert helpers.extract_family_and_version("anthropic/claude-opus") == ("opus", None)


def test_claude_both_parsers_use_the_same_family_set():
    """A drift in :data:`CLAUDE_FAMILIES` would silently break one of
    the two Claude parsers — pin the bare family of every Claude
    test case to that set.
    """
    for case in CLAUDE_MODEL_CASES:
        bare_family = case.family.split("-")[0]
        assert bare_family in CLAUDE_FAMILIES, (case, bare_family)
