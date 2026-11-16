"""Provider helpers for Gemini CLI sessions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, NamedTuple

import orjson
from django.conf import settings

from twicc.core.content_storage.readers import effective_current, load_current
from twicc.core.enums import Provider
from twicc.pricing import FamilyPrices
from twicc.providers.helpers import AgentSettingCategory, AttachmentPolicy, BaseProviderHelpers, ModelVersion
from twicc.providers.message_text import clean_message_text
from twicc.providers.pending_question import out_of_scope_entry

from . import records
from .constants import AGENT_SETTINGS_ALIASES as _AGENT_SETTINGS_ALIASES
from .constants import AGENT_SETTINGS_CATEGORIES as _AGENT_SETTINGS_CATEGORIES
from .constants import AGENT_SETTINGS_DESCRIPTIONS as _AGENT_SETTINGS_DESCRIPTIONS
from .constants import AGENT_SETTINGS_FIELDS_MAPPING as _AGENT_SETTINGS_FIELDS_MAPPING
from .constants import EFFORTS, PERMISSION_MODES
from .constants import MODEL_VERSIONS as _MODEL_VERSIONS
from .constants import NON_INTERACTIVE_PERMISSION_MODES as _NON_INTERACTIVE_PERMISSION_MODES
from .constants import SYNCED_SETTINGS_DEFAULTS as _SYNCED_SETTINGS_DEFAULTS
from .constants import UNTRUSTED_PERMISSION_MODE_SYNCED_KEY as _UNTRUSTED_PERMISSION_MODE_SYNCED_KEY
from .constants import UNTRUSTED_PERMISSION_MODES as _UNTRUSTED_PERMISSION_MODES

if TYPE_CHECKING:
    from twicc.core.models import SessionItem


AGENT_SETTINGS_CHOICES: dict[str, list] = {
    "permission_mode": list(PERMISSION_MODES),
    "effort": list(EFFORTS),
}


# Attachments are not delivered natively in v1 (every attachment becomes a
# file reference in the prompt). The limits below only matter once images are
# sent as ACP ``image`` content blocks.
ATTACHMENT_POLICY = AttachmentPolicy(
    native_kinds=frozenset(),
    hybrid_native_kinds=frozenset(),
    pdf_native_max_bytes=None,
    text_native_max_bytes=None,
    max_native_items=0,
    max_native_items_1m=0,
    volume_budget=16 * 1024 * 1024,
    volume_budget_third_party=16 * 1024 * 1024,
    image_long_edge=3072,
    per_image_base64_limit=None,
    per_image_base64_limit_third_party=None,
)


class ModelInfo(NamedTuple):
    family: str
    version: str


# ``gemini-<version>-<tier>[-<qualifier>...]``. The version is dotted numeric;
# qualifiers that only name a release channel or a tool-tuned variant
# (``preview``, ``customtools``, ``exp``, dates) are not pricing buckets.
_MODEL_RE = re.compile(r"^gemini-(?P<version>\d+(?:\.\d+)?)-(?P<rest>[a-z][a-z0-9-]*)$")
_IGNORED_QUALIFIERS = frozenset({"preview", "customtools", "exp", "latest"})


def extract_model_info(raw_name: str) -> ModelInfo | None:
    """Parse a Gemini model id into ``(family, version)``.

    ``gemini-3.8-flash`` → ``("gemini-flash", "3.8")``,
    ``gemini-3.5-flash-lite`` → ``("gemini-flash-lite", "3.5")``,
    ``gemini-3.1-pro-preview-customtools`` → ``("gemini-pro", "3.1")``.
    ``None`` for anything else (``auto``, ``gemma-…``).
    """
    match = _MODEL_RE.match(raw_name)
    if match is None:
        return None
    tier = [
        part for part in match.group("rest").split("-")
        if part not in _IGNORED_QUALIFIERS and not part.isdigit()
    ]
    if not tier:
        return None
    return ModelInfo(f"gemini-{'-'.join(tier)}", match.group("version"))


class GeminiHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Gemini CLI."""

    provider: ClassVar[Provider] = Provider.GEMINI
    LABEL: ClassVar[str] = "Gemini"

    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = _SYNCED_SETTINGS_DEFAULTS
    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = _AGENT_SETTINGS_CATEGORIES
    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = _AGENT_SETTINGS_FIELDS_MAPPING
    AGENT_SETTINGS_DESCRIPTIONS: ClassVar[dict[str, dict]] = _AGENT_SETTINGS_DESCRIPTIONS
    AGENT_SETTINGS_ALIASES: ClassVar[dict[str, dict[str, str]]] = _AGENT_SETTINGS_ALIASES
    NON_INTERACTIVE_PERMISSION_MODES: ClassVar[frozenset[str]] = _NON_INTERACTIVE_PERMISSION_MODES
    UNTRUSTED_PERMISSION_MODES: ClassVar[frozenset[str]] = _UNTRUSTED_PERMISSION_MODES
    UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: ClassVar[str | None] = _UNTRUSTED_PERMISSION_MODE_SYNCED_KEY

    # Prices are synced from OpenRouter (``google/gemini-…``); the fallbacks
    # below (USD per million tokens, the public paid-tier list prices of each
    # family's prompts up to 200K tokens) only apply to a model the sync does
    # not know yet. Gemini has no cache-write charge per call.
    OPENROUTER_MODEL_PREFIX: ClassVar[str | None] = "google/"
    DEFAULT_FAMILY_PRICES: ClassVar[dict[str, FamilyPrices]] = {
        "gemini-pro": FamilyPrices(
            input_price=Decimal("2.00"),
            output_price=Decimal("12.00"),
            cache_read_price=Decimal("0.20"),
            cache_write_5m_price=Decimal(0),
            cache_write_1h_price=Decimal(0),
        ),
        "gemini-flash": FamilyPrices(
            input_price=Decimal("0.50"),
            output_price=Decimal("3.00"),
            cache_read_price=Decimal("0.05"),
            cache_write_5m_price=Decimal(0),
            cache_write_1h_price=Decimal(0),
        ),
        "gemini-flash-lite": FamilyPrices(
            input_price=Decimal("0.10"),
            output_price=Decimal("0.40"),
            cache_read_price=Decimal("0.01"),
            cache_write_5m_price=Decimal(0),
            cache_write_1h_price=Decimal(0),
        ),
    }

    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = _MODEL_VERSIONS

    @property
    def current_compute_version(self) -> int | None:
        """Return :data:`settings.GEMINI_COMPUTE_VERSION`."""
        return settings.GEMINI_COMPUTE_VERSION

    # ------------------------------------------------------------------
    # Commands, pending requests
    # ------------------------------------------------------------------

    def is_command_message(self, content: str) -> bool:
        return content.lstrip().startswith("/")

    def normalize_pending_request(self, pending, *, raw: bool = False) -> dict:
        # Tool approvals only: nothing a CLI ``answer`` can resolve.
        return out_of_scope_entry(pending, raw=raw)

    def build_question_response(self, pending, *, action: str, answers: dict):
        raise ValueError("Gemini pending requests are tool approvals, not answerable questions")

    # ------------------------------------------------------------------
    # Agent settings
    # ------------------------------------------------------------------

    def get_agent_settings_choices(self) -> dict[str, list]:
        return AGENT_SETTINGS_CHOICES

    def get_attachment_policy(self) -> AttachmentPolicy:
        return ATTACHMENT_POLICY

    def find_model(self, identifier: str) -> ModelVersion | None:
        """Look up a model by its ``selected_model`` alias (``gemini-flash``, ``gemini-pro-2.5``)."""
        for mv in self.MODEL_VERSIONS:
            if mv.latest and mv.model == identifier:
                return mv
        for mv in self.MODEL_VERSIONS:
            if identifier in (f"{mv.model}-{mv.version}", mv.full_name):
                return mv
        return None

    def resolve_cli_model(self, selected_model: str | None) -> str | None:
        """The model id to hand to the CLI for a ``selected_model`` alias."""
        if not selected_model:
            return None
        selected_model = self.sdk_model_safety_net(selected_model)
        mv = self.find_model(selected_model)
        return mv.full_name if mv else selected_model

    def serialize_model_extra(self, mv: ModelVersion) -> dict:
        return mv.provider_extra._asdict() if mv.provider_extra else {}

    # ------------------------------------------------------------------
    # Models and prices
    # ------------------------------------------------------------------

    def extract_family_and_version(self, model_id: str) -> tuple[str | None, str | None]:
        """Parse an OpenRouter id (``google/gemini-3.8-flash``) into ``(family, version)``."""
        prefix = "google/"
        if not model_id.startswith(prefix):
            return None, None
        info = extract_model_info(model_id.removeprefix(prefix))
        if info is None:
            return None, None
        return info.family, info.version

    def serialize_model(self, model: str | None) -> dict | None:
        if not model:
            return None
        info = extract_model_info(model)
        if info is None:
            return {"raw": model, "family": None, "version": None}
        return {"raw": model, "family": info.family, "version": info.version}

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------

    def extract_indexable_text_from_parsed(self, parsed: dict) -> str:
        return clean_message_text(records.user_text(parsed) or records.assistant_text(parsed) or "")

    def extract_timeline_text_from_parsed(self, parsed: dict) -> str:
        return clean_message_text(records.user_text(parsed) or records.assistant_text(parsed) or "")

    def is_final_assistant_message(self, parsed: dict) -> bool | None:
        # A Gemini turn ends with a ``gemini`` record that carries text and no
        # tool call. While tools run, records carry ``toolCalls``. A record can
        # gain tool calls in a later copy; the text-only copy is then followed
        # by that repeat, which the reader sees as the newer line.
        if parsed.get("type") != records.TYPE_GEMINI:
            return None
        if records.tool_calls(parsed):
            return False
        if records.assistant_text(parsed) is None:
            return None
        return True

    # ------------------------------------------------------------------
    # Tool results
    # ------------------------------------------------------------------

    def get_tool_results(self, items: Iterable[SessionItem], tool_use_id: str) -> list[dict]:
        """The ``functionResponse`` payloads answering ``tool_use_id``.

        The linked items are the ``user`` records echoing tool results (see
        ``GeminiSessionCompute.is_tool_result_item``).
        """
        results: list[dict] = []
        for item in items:
            try:
                load_current([item])
                parsed = orjson.loads(effective_current(item))
            except (orjson.JSONDecodeError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            for response in records.function_responses(parsed):
                if response.get("id") == tool_use_id:
                    results.append(response)
        return results

    # ------------------------------------------------------------------
    # Titles
    # ------------------------------------------------------------------

    async def rename_session(self, session_id: str, title: str) -> None:
        """No-op: the session file has no title record TwiCC could write.

        The CLI derives its own summary; the TwiCC title lives in the database
        only.
        """
        return
