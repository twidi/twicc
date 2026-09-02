"""Proxy to the Claude Code CLI bundled in claude-agent-sdk.

Honours the instance's ``.env`` (loaded by the ``twicc.cli`` package import),
provider homes included: the CLI runs against the same ``CLAUDE_CONFIG_DIR``
as the backend.
"""

import os
import sys

from twicc.provider_homes import provider_env_overlay
from twicc.providers.claude_code.bin import resolve_bundled_binary


def main(args: list[str]) -> None:
    """Replace the current process with the bundled Claude Code CLI."""
    try:
        binary = str(resolve_bundled_binary())
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    # ``execvp`` inherits ``os.environ`` (already right after the .env load);
    # the overlay is applied explicitly all the same.
    os.environ.update(provider_env_overlay())
    os.execvp(binary, [binary, *args])
