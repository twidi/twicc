"""Shared permission-request bricks for the Claude Code provider.

Used by BOTH the SDK agent (``agent/agent.py``) and the hybrid CLI agent
(``agent/hybrid/agent.py``) so the two modes present identical suggestion
shapes to the frontend and share the ExitPlanMode plan-file write.

The SDK-only enrichments intentionally do NOT live here: the synthesized
MCP/WebFetch/WebSearch/file-rule suggestions and the injected ``setMode``
mode-picker suggestion stay in the SDK agent — the hybrid path presents
hook-native suggestions only (design doc 2026-06-12 §3/§4.4). Note the
asymmetry on ``setMode``: the SDK agent drops CLI-provided ``setMode``
entries (its own picker replaces them), while the hybrid agent keeps them
verbatim (e.g. the "allow all edits" ``acceptEdits`` suggestion) — so the
drop is NOT part of the shared filter.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import PermissionUpdate

logger = logging.getLogger(__name__)

# Field order matching the PermissionUpdate dataclass definition. Private
# fields (prefixed with ``_``) are preserved at the end for frontend use.
_SUGGESTION_FIELD_ORDER = ("type", "rules", "behavior", "mode", "directories", "destination")

# Tools whose input names an exact, complete target path — so an approval for
# one of them touches exactly that path and nothing else. Maps the tool name to
# the input key holding the path. (``MultiEdit`` edits a single file too.)
_EXACT_PATH_INPUT_KEYS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


def extract_claude_tool_paths(
    tool_name: str, tool_input: dict, blocked_path: str | None = None,
) -> tuple[list[str], bool]:
    """Return ``(candidate_abs_paths, fully_known)`` for a Claude tool approval.

    Shared by the SDK ``can_use_tool`` callback and the hybrid ``PermissionRequest``
    hook. ``fully_known=False`` means the request's full footprint cannot be
    enumerated, so the caller must NOT auto-approve.

    - File tools (``Read``/``Write``/``Edit``/``MultiEdit``/``NotebookEdit``)
      expose the exact, complete target via ``file_path`` / ``notebook_path``.
    - For ``Bash`` (and any other multi-effect tool), the only signal is
      ``blocked_path`` — the single path that tripped the permission prompt. When
      present we treat that lone path as the footprint (best-effort heuristic, by
      design); otherwise the footprint is unknowable → not auto-approvable.
    """
    key = _EXACT_PATH_INPUT_KEYS.get(tool_name)
    if key is not None:
        path = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(path, str) and path:
            return [path], True
        return [], False
    if isinstance(blocked_path, str) and blocked_path:
        return [blocked_path], True
    return [], False


def serialize_and_filter_suggestions(suggestions, cwd: str) -> list[dict]:
    """Serialize, filter and ungroup raw permission suggestions.

    Accepts ``PermissionUpdate`` dataclass instances (SDK context) or plain
    dicts (SDK edge cases, hybrid hook payloads) and normalises everything
    into JSON-serialisable dicts:

    - ``addDirectories`` / ``removeDirectories``: the project directory
      (``cwd``) is removed from the directories list (it is always implicitly
      allowed); if the list becomes empty the suggestion is dropped entirely.
    - Suggestions bundling multiple rules are split into one suggestion per
      rule so the frontend can present them individually.
    """
    serialized = [s.to_dict() if isinstance(s, PermissionUpdate) else s for s in suggestions or ()]

    result: list[dict] = []
    for suggestion in serialized:
        suggestion_type = suggestion.get("type")

        if suggestion_type in ("addDirectories", "removeDirectories"):
            directories = suggestion.get("directories")
            if directories is not None:
                directories = [d for d in directories if d != cwd]
                if not directories:
                    # Nothing left to suggest after filtering
                    continue
                suggestion = {**suggestion, "directories": directories}

        rules = suggestion.get("rules")
        if rules and len(rules) > 1:
            for rule in rules:
                result.append({**suggestion, "rules": [rule]})
        else:
            result.append(suggestion)

    return result


def order_suggestion_fields(suggestions: list[dict]) -> list[dict] | None:
    """Normalize suggestion field order for transmission to the frontend.

    Returns ``None`` when there is nothing to send (the ``PendingRequest``
    field is optional and the serializer omits empty values).
    """
    result = [
        {k: s[k] for k in _SUGGESTION_FIELD_ORDER if k in s}
        | {k: v for k, v in s.items() if k.startswith("_")}
        for s in suggestions
    ]
    return result or None


async def maybe_update_plan_file(
    tool_input: dict | None,
    updated_input: dict | None,
    *,
    slug_getter: Callable[[str], Awaitable[str | None]] | None = None,
    session_id: str | None = None,
) -> None:
    """Persist a user-modified ExitPlanMode plan to Claude's plan file.

    Because of a "bug" in claude-agent-sdk / claude-code, the plan carried in
    the approval's ``updated_input`` is not taken into account by the CLI, so
    TwiCC rewrites the plan file itself. No-op unless ``updated_input``
    carries a plan that differs from the original ``tool_input`` plan.

    The target path comes from ``tool_input["planFilePath"]`` — CLI-injected
    into the ExitPlanMode tool input itself (verified in transcripts since
    ~2.1.91), present in both the SDK ``can_use_tool`` input and the hybrid
    hook payload. The slug lookup (``slug_getter(session_id)`` →
    ``<claude home>/plans/{slug}.md``) is a legacy fallback for older payloads;
    only the SDK caller provides it.
    """
    tool_input = tool_input or {}
    new_plan = (updated_input or {}).get("plan")
    if new_plan is None or new_plan == tool_input.get("plan"):
        return

    plan_path = tool_input.get("planFilePath")
    if plan_path:
        plan_file = Path(plan_path)
    else:
        if slug_getter is None or session_id is None:
            logger.warning("Cannot update plan: no planFilePath and no slug fallback available")
            return
        slug = await slug_getter(session_id)
        if slug is None:
            logger.warning(
                "Cannot update plan for session %s: no slug found in session items",
                session_id,
            )
            return
        from twicc.provider_homes import claude_plans_dir

        plan_file = claude_plans_dir() / f"{slug}.md"

    if not plan_file.exists():
        logger.warning("Plan file does not exist: %s", plan_file)
        return

    try:
        plan_file.write_text(new_plan, encoding="utf-8")
        logger.info("Plan file updated: %s (%d chars)", plan_file, len(new_plan))
    except OSError as e:
        logger.error("Failed to write plan file %s: %s", plan_file, e)
