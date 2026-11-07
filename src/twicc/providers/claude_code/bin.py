"""
Locate the Claude Code ``claude`` binary shipped inside the
``claude-agent-sdk`` wheel (``claude_agent_sdk/_bundled/claude``).

Mirrors :mod:`twicc.providers.codex.bin` so every codepath that needs
the bundled binary imports the same helper, with the same name, from
the same shape of module:
``twicc.providers.<provider>.bin.resolve_bundled_binary()``.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from twicc.core.ui_messages import ui_message_text

# Resolved path is process-wide constant (the SDK wheel layout doesn't change
# once installed), so we cache after the first successful lookup. Misses
# (binary absent) deliberately don't cache.
_BIN_PATH: Path | None = None


def resolve_bundled_binary() -> Path:
    """Return the path to the Claude binary shipped inside the SDK wheel."""
    global _BIN_PATH
    if _BIN_PATH is not None:
        return _BIN_PATH

    bundled_dir = Path(str(files("claude_agent_sdk"))) / "_bundled"
    bin_path = bundled_dir / "claude"
    if not bin_path.is_file():
        raise FileNotFoundError(ui_message_text(
            f"Bundled Claude binary not found at {bin_path}",
            "backend.claude.binary_not_found", {"path": str(bin_path)},
        ))
    _BIN_PATH = bin_path
    return _BIN_PATH
