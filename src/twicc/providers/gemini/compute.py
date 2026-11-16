"""Metadata compute for Gemini CLI sessions.

Classification (bump ``GEMINI_COMPUTE_VERSION`` when these rules change):

- header, ``$set`` / ``$patch`` / ``$rewindTo``, the injected
  ``<session_context>`` user record, ``info`` / ``warning`` records and
  repeats that add nothing → ``SYSTEM`` (debug only);
- ``user`` record with text → ``USER_MESSAGE``;
- ``user`` record made of ``functionResponse`` parts → tool result echo
  (``SYSTEM``, linked to its call through ``ToolResultLink``);
- ``gemini`` record with text → ``ASSISTANT_MESSAGE``;
- ``gemini`` record without text but with finished tool calls → ``TOOL_USE``;
- ``gemini`` record with thoughts only → ``REASONING``;
- ``error`` record → ``API_ERROR``.

Re-appended records are reduced to their delta first (see
:class:`~twicc.providers.gemini.records.RecordDeltaTracker`), so every rule
above sees only what a line adds.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import ClassVar

import orjson

from twicc.core.content_storage.readers import effective_current, iter_current_items
from twicc.core.enums import ItemKind, Provider
from twicc.pricing import TokenUsage, calculate_line_context_usage
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    ToolResultInfo,
    parse_timestamp_to_datetime,
)

from . import records

# Argument names of the Gemini CLI built-in tools that carry a path.
_PATH_ARGUMENTS = ("file_path", "absolute_path", "dir_path", "path")


def to_token_usage(tokens: dict) -> TokenUsage:
    """Convert a Gemini ``tokens`` payload into the provider-neutral shape.

    ``input`` counts the cached tokens too, and thinking tokens are billed as
    output.
    """
    def count(key: str) -> int:
        value = tokens.get(key)
        return value if isinstance(value, int) and value > 0 else 0

    cached = count("cached")
    return TokenUsage(
        input_tokens=max(count("input") - cached, 0),
        output_tokens=count("output") + count("thoughts"),
        cache_read_input_tokens=cached,
        cache_write_input_tokens_5m=0,
        cache_write_input_tokens_1h=0,
    )


class GeminiSessionCompute(BaseSessionCompute):
    """Compute pipeline for Gemini CLI session files."""

    provider: ClassVar[Provider] = Provider.GEMINI
    live_state_maps: ClassVar[tuple[str, ...]] = ("_trackers", "_tracker_lines")

    def __init__(self) -> None:
        super().__init__()
        # session_id -> RecordDeltaTracker (what each record id already showed).
        self._trackers: dict[str, records.RecordDeltaTracker] = {}
        # session_id -> last line number the tracker saw. A line at or before
        # it means the session restarted from scratch (file replaced, rows
        # rebuilt): the tracker is then rebuilt from the stored lines.
        self._tracker_lines: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Per-session state
    # ------------------------------------------------------------------

    def begin_session_compute(self, session_id: str) -> None:
        # Batch replays every line from the first one.
        self._trackers[session_id] = records.RecordDeltaTracker()
        self._tracker_lines[session_id] = 0

    def end_session_compute(self, session_id: str) -> None:
        self._trackers.pop(session_id, None)
        self._tracker_lines.pop(session_id, None)

    def _tracker(self, session_id: str, line_num: int) -> records.RecordDeltaTracker:
        """The session's tracker, rebuilt from the stored lines on a cold live start."""
        tracker = self._trackers.get(session_id)
        if tracker is not None and self._tracker_lines.get(session_id, 0) < line_num:
            self._tracker_lines[session_id] = line_num
            return tracker
        tracker = records.RecordDeltaTracker()
        from twicc.core.models import SessionItem

        queryset = SessionItem.objects.filter(session_id=session_id, line_num__lt=line_num).order_by("line_num")
        for item in iter_current_items(queryset, 500, 8 * 1024 * 1024):
            try:
                parsed = orjson.loads(effective_current(item))
            except orjson.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                tracker.reduce(parsed)
        self._trackers[session_id] = tracker
        self._tracker_lines[session_id] = line_num
        return tracker

    def _transform_inline_provider(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> str | None:
        if not records.is_message(parsed_json):
            return None
        if self._tracker(session_id, line_num).reduce(parsed_json):
            return orjson.dumps(parsed_json).decode()
        return None

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        if not records.is_message(parsed_json):
            return ItemKind.SYSTEM
        record_type = parsed_json.get("type")
        if record_type == records.TYPE_USER:
            return ItemKind.USER_MESSAGE if records.user_text(parsed_json) else ItemKind.SYSTEM
        if record_type == records.TYPE_GEMINI:
            if records.assistant_text(parsed_json):
                return ItemKind.ASSISTANT_MESSAGE
            if records.tool_calls(parsed_json):
                return ItemKind.TOOL_USE
            if records.thoughts(parsed_json):
                return ItemKind.REASONING
            return ItemKind.SYSTEM
        if record_type == records.TYPE_ERROR and not records.is_empty_repeat(parsed_json):
            return ItemKind.API_ERROR
        return ItemKind.SYSTEM

    def detect_prefix_suffix(self, parsed_json: dict, kind: ItemKind | None) -> tuple[bool, bool]:
        return False, False

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        timestamp = parsed_json.get("timestamp") or parsed_json.get("startTime")
        return parse_timestamp_to_datetime(timestamp) if isinstance(timestamp, str) else None

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        return records.is_header(parsed_json)

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        model = parsed_json.get("model")
        if parsed_json.get("type") == records.TYPE_GEMINI and isinstance(model, str) and model:
            return {"model": model}
        return {}

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        return records.user_text(parsed_json)

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        return None

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def compute_item_cost_and_usage(
        self,
        item,
        parsed_json: dict,
        seen_message_ids: set[str],
        current_model: str | None,
    ) -> None:
        if parsed_json.get("type") != records.TYPE_GEMINI:
            return
        record_id = parsed_json.get("id")
        tokens = records.tokens(parsed_json)
        if not tokens or not isinstance(record_id, str):
            return
        usage = to_token_usage(tokens)
        context_usage = calculate_line_context_usage(usage)
        if context_usage == 0:
            return
        item.message_id = record_id
        item.context_usage = context_usage
        # Repeats lose their tokens in the delta rewrite; the id check guards a
        # record whose tokens arrive only in a later copy.
        if record_id in seen_message_ids:
            return
        seen_message_ids.add(record_id)
        model = parsed_json.get("model") or current_model
        timestamp = self.extract_item_timestamp(parsed_json)
        if not model or timestamp is None:
            return
        from twicc.providers.helpers import get_provider_helpers

        item.cost = get_provider_helpers(Provider.GEMINI).calculate_line_cost(
            usage, f"google/{model}", timestamp.date(),
        )

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        return records.is_tool_result_echo(parsed_json)

    def extract_tool_use_entries(self, parsed_json: dict, *, session_id: str) -> dict[str, str]:
        if parsed_json.get("type") != records.TYPE_GEMINI:
            return _EMPTY_TOOL_USE_ENTRIES
        calls = records.tool_calls(parsed_json)
        if not calls:
            return _EMPTY_TOOL_USE_ENTRIES
        return {call["id"]: str(call.get("name") or "") for call in calls}

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map=None,
    ) -> ToolResultInfo | None:
        if not records.is_tool_result_echo(parsed_json):
            return None
        response = records.function_responses(parsed_json)[0]
        tool_use_id = response.get("id") if isinstance(response.get("id"), str) else None
        payload = response.get("response")
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            return ToolResultInfo(tool_use_id, True, error if isinstance(error, str) else str(error))
        return ToolResultInfo(tool_use_id, False, None)

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        if parsed_json.get("type") != records.TYPE_GEMINI:
            return _EMPTY_FILE_PATHS
        paths = []
        for call in records.tool_calls(parsed_json):
            args = call.get("args")
            if not isinstance(args, dict):
                continue
            for key in _PATH_ARGUMENTS:
                value = args.get(key)
                if isinstance(value, str) and os.path.isabs(value):
                    paths.append(value)
            display = call.get("resultDisplay")
            if isinstance(display, dict) and isinstance(display.get("filePath"), str):
                if os.path.isabs(display["filePath"]):
                    paths.append(display["filePath"])
        return paths or _EMPTY_FILE_PATHS

    def compute_link_extra(
        self,
        parsed_json: dict,
        tool_name: str,
        *,
        session_id: str | None = None,
        tool_use_id: str | None = None,
    ) -> str | None:
        return None

    # Gemini CLI subagents write their own files (``kind`` other than
    # ``main``), not linked in v1.

    def extract_agent_info_from_tool_result(self, parsed_json: dict) -> tuple[str, str, bool] | None:
        return None

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        return _EMPTY_TASK_TOOL_USES

    def extract_task_tool_use_prompts(self, parsed_json: dict) -> list[tuple[str, str, bool]]:
        return []

    # ------------------------------------------------------------------
    # Batch single pass
    # ------------------------------------------------------------------

    def analyze_content(self, parsed_json: dict, *, session_id: str, tool_use_map) -> ContentAnalysis:
        if not records.is_message(parsed_json):
            return _EMPTY_ANALYSIS
        result_info = self.extract_tool_result_info(parsed_json, session_id=session_id)
        text = records.user_text(parsed_json) or records.assistant_text(parsed_json)
        has_visible = bool(
            text
            or (parsed_json.get("type") == records.TYPE_GEMINI
                and (records.thoughts(parsed_json) or records.tool_calls(parsed_json)))
        )
        return ContentAnalysis(
            has_visible_content=has_visible,
            text_content=text,
            is_system_xml=False,
            has_tool_result=result_info is not None,
            tool_result_id=result_info.tool_use_id if result_info else None,
            tool_result_error=result_info.error_text if result_info else None,
            tool_use_entries=self.extract_tool_use_entries(parsed_json, session_id=session_id),
            task_tool_uses=_EMPTY_TASK_TOOL_USES,
            file_paths=self.extract_paths_from_tool_uses(parsed_json),
            has_prefix=False,
            has_suffix=False,
            tool_result_agent_info=None,
        )


_compute_instance: GeminiSessionCompute | None = None


def get_compute() -> GeminiSessionCompute:
    """Return the process-local :class:`GeminiSessionCompute` singleton."""
    global _compute_instance
    if _compute_instance is None:
        _compute_instance = GeminiSessionCompute()
    return _compute_instance
