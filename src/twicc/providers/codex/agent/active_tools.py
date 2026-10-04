"""Map Codex app-server thread items to the live "active tools" entries.

The working-status line ("Codex is sleeping", ...) is driven by the
``process_tools`` WebSocket message, a list of ``{id, name, input}``. The
frontend feeds ``name`` / ``input`` to the provider's ``getVerb``, so the
names here MUST match the canonical tool names the JSONL path produces (what
``items/codex/ToolUse.vue`` builds): ``exec_command``, ``apply_patch``,
``web_search_call``, ``view_image``, ``mcp__<server>__<tool>``,
``<namespace>__<tool>`` for namespaced function calls (``clock__sleep``).

Agent-control items (``collabAgentToolCall``, ``subAgentActivity``) are left
out: they already own the status label (sub-agent wait label).
"""

from __future__ import annotations

from typing import Any

# Canonical name of the hosted image-generation tool (frontend
# ``IMAGE_GEN_TOOL_NAME`` in ``providers/codex/codeModeDisplay.js``).
IMAGE_GEN_TOOL_NAME = "image_gen__imagegen"


def _arguments_dict(arguments: Any) -> dict:
    return arguments if isinstance(arguments, dict) else {}


def active_tool_from_item(item: Any) -> tuple[str, dict] | None:
    """Return ``(name, input)`` for a tool-like thread item, else ``None``.

    ``item`` is the unwrapped ``ThreadItem`` model of an ``item/started``
    notification.
    """
    match getattr(item, "type", None):
        case "commandExecution":
            return "exec_command", {"cmd": getattr(item, "command", "")}
        case "fileChange":
            return "apply_patch", {}
        case "mcpToolCall":
            return f"mcp__{item.server}__{item.tool}", _arguments_dict(item.arguments)
        case "dynamicToolCall":
            namespace = getattr(item, "namespace", None)
            name = f"{namespace}__{item.tool}" if namespace else item.tool
            return name, _arguments_dict(item.arguments)
        case "webSearch":
            return "web_search_call", {}
        case "imageView":
            return "view_image", {}
        case "imageGeneration":
            return IMAGE_GEN_TOOL_NAME, {}
        case "sleep":
            return "clock__sleep", {"duration_ms": item.duration_ms}
    return None
