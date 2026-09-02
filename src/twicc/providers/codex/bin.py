"""Resolve the Codex CLI binary and build a ready-to-use ``CodexConfig``.

The binary is provisioned by :mod:`twicc.providers.codex.runtime` (downloaded
from GitHub Releases at launch, cached under ``~/.cache/twicc/``). This module
is the single entry point every Codex code path uses to (a) get the binary
path and (b) build a ``CodexConfig`` that also puts ``rg`` on PATH.

Mirrors the shape of ``twicc.providers.claude_code.bin`` (same function name
``resolve_bundled_binary``) for the sync callers (e.g. the auth-status check).
"""

from __future__ import annotations

import os
from pathlib import Path

from openai_codex import CodexConfig

from .runtime import (
    codex_binary_path,
    codex_path_dir,
    ensure_codex_runtime,
    is_runtime_ready,
)


class CodexRuntimeNotReady(FileNotFoundError):
    """Raised by the sync resolver when the runtime hasn't been downloaded yet.

    Subclasses ``FileNotFoundError`` so existing sync callers (auth-status
    check) that already catch ``FileNotFoundError`` degrade gracefully.
    """


def resolve_bundled_binary() -> Path:
    """Return the codex binary path IF the runtime is already present.

    Sync and non-blocking: it never downloads. Raises
    :class:`CodexRuntimeNotReady` when the runtime isn't ready yet. The
    download is performed by :func:`twicc.providers.codex.runtime.ensure_codex_runtime`
    at global startup (and on demand by :func:`make_codex_config`).
    """
    if not is_runtime_ready():
        raise CodexRuntimeNotReady(
            "Codex runtime not downloaded yet; it is fetched at TwiCC startup."
        )
    return codex_binary_path()


def _codex_env() -> dict[str, str]:
    """A minimal env overlay putting ``codex-path/`` (ripgrep) first on PATH.

    Reproduces what the SDK does automatically only when ``codex_bin`` is
    auto-resolved — which never applies to us since we always pass
    ``codex_bin`` explicitly.

    In debug mode (``TWICC_DEBUG``, set by devctl) the overlay also turns on
    the app-server's tracing output via ``RUST_LOG`` — persisted per session
    by ``sdk_logger.attach_stderr_logging`` — unless the operator already
    exported their own ``RUST_LOG``.

    Also carries the configured provider homes (``CODEX_HOME`` & co,
    ``twicc.provider_homes.provider_env_overlay``): this one function covers
    every app-server TwiCC spawns (agent manager, titles, credentials, trust,
    plugin install, skill catalogue), so the invariant that a launched process
    never touches another home holds here explicitly.
    """
    from twicc.provider_homes import provider_env_overlay

    path_dir = str(codex_path_dir())
    existing = os.environ.get("PATH", "")
    env = {"PATH": f"{path_dir}{os.pathsep}{existing}" if existing else path_dir}
    debug = os.environ.get("TWICC_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if debug and "RUST_LOG" not in os.environ:
        env["RUST_LOG"] = "codex_core=debug,codex_app_server=debug"
    env.update(provider_env_overlay())
    return env


async def make_codex_config(*, cwd: str | None = None, **extra) -> CodexConfig:
    """Ensure the runtime is present, then build a ``CodexConfig`` for it.

    Async because the first call may trigger the one-time runtime download
    (run off the event loop). Every backend Codex entry point that builds a
    ``CodexConfig`` uses this. Also creates a configured ``CODEX_HOME`` when
    missing: Codex refuses to start otherwise (nothing created, exit 1).
    """
    from twicc.provider_homes import ensure_codex_home

    await ensure_codex_runtime()
    ensure_codex_home()
    return CodexConfig(
        codex_bin=str(codex_binary_path()),
        env=_codex_env(),
        cwd=cwd,
        **extra,
    )
