"""Proxy to the Codex CLI runtime downloaded from GitHub Releases.

A standalone ``twicc codex`` invocation runs as its own process and
``execvp``s straight into the bundled ``codex`` binary, so the backend's
background pre-download never runs for it. It therefore ensures the runtime
itself — downloading it once (blocking) if the cache is cold — before handing
off.

Honours the instance's ``.env`` (loaded by the ``twicc.cli`` package import),
provider homes included: the CLI runs against the same ``CODEX_HOME`` as the
backend, created here when configured and missing (Codex refuses to start on
a missing home).
"""

import os
import sys

from twicc.provider_homes import ensure_codex_home, provider_env_overlay
from twicc.providers.codex.bin import resolve_bundled_binary
from twicc.providers.codex.runtime import CodexRuntimeError, ensure_codex_runtime_sync


def main(args: list[str]) -> None:
    """Replace the current process with the bundled Codex CLI."""
    try:
        ensure_codex_runtime_sync()
        binary = str(resolve_bundled_binary())
        ensure_codex_home()
    except (CodexRuntimeError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    # ``execvp`` inherits ``os.environ`` (already right after the .env load);
    # the overlay is applied explicitly all the same.
    os.environ.update(provider_env_overlay())
    os.execvp(binary, [binary, *args])
