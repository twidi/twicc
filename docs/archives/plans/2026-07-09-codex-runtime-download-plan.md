# Plan: download the Codex CLI runtime at launch (drop the PyPI cli-bin dependency)

Status: **ready to implement** (not started). Self-contained — a fresh session can execute this without prior context.

Target Codex version: **rust-v0.144.0** (SDK) + **openai-codex-cli-bin 0.144.0** binary.

---

## 1. Problem

TwiCC drives Codex through the vendored `openai_codex` SDK, which launches the
`codex` CLI binary (`codex app-server --listen stdio://`). Until now the binary
came from the PyPI package `openai-codex-cli-bin`, declared as a normal
dependency in `pyproject.toml`.

**OpenAI stopped publishing stable `openai-codex-cli-bin` wheels on PyPI after
`0.136.0`.** The latest stable on PyPI is `0.136.0` (what `main` currently
pins); everything newer on PyPI is a mid-cycle alpha (`0.137.0a4`). Meanwhile
upstream ships stable tags `0.137.0` … `0.144.0`+ on GitHub. Reason: the wheel
is ~122 MB (the `codex` binary alone is ~296 MB uncompressed), above practical
PyPI quotas. We are now ~7 stable versions behind and cannot catch up through
PyPI.

**Every tagged stable still ships the same `manylinux_2_17` / macOS wheels as
GitHub Release assets.** So we can fetch the binary from GitHub — the same
mechanism TwiCC used originally, before manylinux existed on PyPI.

## 2. Goal & chosen approach

Download the platform-specific `openai-codex-cli-bin` **wheel** from the GitHub
Release at **first launch**, extract the whole `codex_cli_bin/` tree into a
shared cache, and point the SDK at the extracted binary via
`CodexConfig(codex_bin=...)`.

This keeps every packaging win from the previous migration:
- TwiCC wheel stays a single `py3-none-any`.
- The sdist stays publishable (no per-platform binary inside the TwiCC wheel).
- No re-introduction of the multi-wheel `hatch_build.py` binary-bundling dance.

Cost: the first launch downloads ~110–122 MB and needs network at that moment.
Acceptable — TwiCC already needs the network for the provider APIs.

## 3. Decisions (all validated with the user)

| Aspect | Decision |
|---|---|
| Source | `openai-codex-cli-bin` **wheel** from GitHub Release `rust-v0.144.0` |
| Extract | the **whole** `codex_cli_bin/` tree (codex + code-mode-host + rg + bwrap + zsh), so `codex` finds its sibling resources exactly like a normal pip install |
| Storage | `~/.cache/twicc/codex-runtime/<version>/` (XDG, honours `$XDG_CACHE_HOME`), shared across the main instance and all worktrees, **never** per-worktree |
| Timing | **at global startup, unconditionally**, in a background task — NOT gated on the Codex provider being enabled (the user can enable Codex live from the UI and must not wait for the download then) |
| Progress | backend logs only (V1) |
| Integrity | hardcoded sha256 of each wheel, verified after download; file lock to avoid concurrent downloads |
| Windows | no binary. WSL2 is a real Linux kernel → uses the Linux binary. Native Windows is unsupported by TwiCC anyway |
| Platforms | 4: `linux x86_64`, `linux aarch64`, `macOS arm64`, `macOS x86_64` |
| App code | SDK 0.144 exports are identical to our current 0.136 → **no import renames** in TwiCC |

## 4. Target version, platform mapping, sha256

Constants for `runtime.py` (verified 2026-07-09):

```
CODEX_VERSION      = "0.144.0"
CODEX_RELEASE_TAG  = "rust-v0.144.0"
Release asset URL  = https://github.com/openai/codex/releases/download/rust-v0.144.0/<wheel>
```

| platform tag (our key) | wheel filename | sha256 |
|---|---|---|
| `manylinux_2_17_x86_64` | `openai_codex_cli_bin-0.144.0-py3-none-manylinux_2_17_x86_64.whl` | `b183990e979fa85ba6e19a1c0132094b533f280f707155ce61908dcd312b312e` |
| `manylinux_2_17_aarch64` | `openai_codex_cli_bin-0.144.0-py3-none-manylinux_2_17_aarch64.whl` | `25f3fb3e66bd1cf296efe71ff2a33e65578de76f6760633e6164288fb05b901e` |
| `macosx_11_0_arm64` | `openai_codex_cli_bin-0.144.0-py3-none-macosx_11_0_arm64.whl` | `98ff0e4268f4ed5de4822e1d8a7de7e22ea587f274593070ec66e83ea19b4a87` |
| `macosx_10_9_x86_64` | `openai_codex_cli_bin-0.144.0-py3-none-macosx_10_9_x86_64.whl` | `6919031c4801218fad455f9a79e9abdeead7c02cc3b7d04e8f93de6ddce2f58f` |

## 5. Established technical facts (do not re-research)

- Wheel internal layout (extract preserves this):
  ```
  codex_cli_bin/bin/codex                     (296 MB, the app-server binary)
  codex_cli_bin/bin/codex-code-mode-host      (46 MB)
  codex_cli_bin/codex-path/rg                  (ripgrep)
  codex_cli_bin/codex-resources/bwrap          (Linux sandbox helper)
  codex_cli_bin/codex-resources/zsh/bin/zsh
  codex_cli_bin/__init__.py, codex-package.json
  ```
- The SDK launches `codex app-server` (`openai_codex/client.py`, ~line 252).
- The SDK only prepends `codex-path/` (ripgrep) to `PATH` **when `config.codex_bin`
  is `None`** (`client.py` ~line 247, `_installed_codex_path_dirs()` guarded by
  `if self.config.codex_bin is None`). We always pass `codex_bin` explicitly, so
  **we must add `codex-path/` to `PATH` ourselves** via `CodexConfig(env=...)`.
- `bwrap` and `zsh` live under `codex-resources/` and are found by `codex`
  **relative to its own executable path** — no PATH needed, just the preserved
  tree.
- SDK 0.144 `__init__.py` exports are identical to our current 0.136 (verified
  with a diff). Class names unchanged (`CodexConfig`, `AsyncCodex`,
  `AsyncCodexClient`, `CodexClient`, …). The monkey-patch path
  `codex._client._sync._approval_handler` holds. `_inputs._normalize_run_input`
  / `_to_wire_input` present. `codex_cli_bin.bundled_codex_path` still imported.
  Generated params (`ThreadStartParams`, `ThreadResumeParams`, `TurnStartParams`
  with `approval_policy` / `approvals_reviewer` / `sandbox(_policy)`) and enums
  (`AskForApprovalValue` = untrusted/on-failure/on-request/never, `SandboxMode`,
  `ApprovalsReviewer`) all present. So `sdk_wrappers.py` needs **no change**.
- All TwiCC code paths that build a `CodexConfig` are **async**. The only sync
  consumer of the binary path is `auth.py` (Codex login-status check via
  subprocess), which already catches `FileNotFoundError`.

## 6. Implementation — full code

### 6.1 Re-vendor the SDK to rust-v0.144.0

```bash
cd <repo-root>
rm -rf src/openai_codex
mkdir -p /tmp/codex-sdk-143
curl -sL "https://codeload.github.com/openai/codex/tar.gz/refs/tags/rust-v0.144.0" \
  | tar -xz --strip-components=4 --wildcards "*/sdk/python/src/openai_codex/*" -C /tmp/codex-sdk-143
mv /tmp/codex-sdk-143/openai_codex src/openai_codex
# sanity
python -c "import ast, pathlib; ast.parse(pathlib.Path('src/openai_codex/__init__.py').read_text())"
ls src/openai_codex/   # expect: api.py, client.py, async_client.py, _approval_mode.py, _inputs.py, _sandbox.py, generated/, _goal.py, ...
```

Note: 0.144 adds `_goal.py` vs 0.136 — harmless, we don't import it. No new
runtime dependency in the SDK's own `pyproject.toml` (still only `pydantic>=2.12`,
which we already declare).

### 6.2 New module `src/twicc/providers/codex/runtime.py`

Create it with exactly this content:

```python
"""Download and cache the Codex CLI runtime from GitHub Releases.

OpenAI stopped publishing stable ``openai-codex-cli-bin`` wheels on PyPI after
0.136.0 (the wheel is ~122 MB, above practical PyPI quotas), but every tagged
stable ships the same manylinux/macOS wheels as GitHub Release assets. Since we
can no longer depend on the PyPI package, we download the platform wheel at
launch, extract it into a shared cache, and point the SDK at the extracted
binary via ``CodexConfig(codex_bin=...)``.

The whole ``codex_cli_bin/`` tree is extracted (not just the ``codex`` binary)
so the sibling resources ship too: ``codex-resources/bwrap`` (Linux sandbox
helper) and ``codex-resources/zsh`` are found by ``codex`` relative to its own
path, and ``codex-path/rg`` (ripgrep) is put on PATH by
``twicc.providers.codex.bin.make_codex_config``.

Cache location: ``$XDG_CACHE_HOME/twicc/codex-runtime/<version>/`` (default
``~/.cache/twicc/...``). Shared across the main instance and every worktree,
so the ~300 MB extracted tree is downloaded once. Independent of
``TWICC_DATA_DIR`` on purpose — this is a regenerable runtime cache, not user
data.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import platform
import shutil
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

CODEX_VERSION = "0.144.0"
CODEX_RELEASE_TAG = "rust-v0.144.0"
_RELEASE_URL = f"https://github.com/openai/codex/releases/download/{CODEX_RELEASE_TAG}"

# our platform key -> (wheel filename, sha256 of the wheel)
# Recompute the sha256 values on every version bump (see the bump note at the
# bottom of docs/plans/2026-07-09-codex-runtime-download-plan.md).
_WHEELS: dict[str, tuple[str, str]] = {
    "manylinux_2_17_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_x86_64.whl",
        "b183990e979fa85ba6e19a1c0132094b533f280f707155ce61908dcd312b312e",
    ),
    "manylinux_2_17_aarch64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_aarch64.whl",
        "25f3fb3e66bd1cf296efe71ff2a33e65578de76f6760633e6164288fb05b901e",
    ),
    "macosx_11_0_arm64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_11_0_arm64.whl",
        "98ff0e4268f4ed5de4822e1d8a7de7e22ea587f274593070ec66e83ea19b4a87",
    ),
    "macosx_10_9_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_10_9_x86_64.whl",
        "6919031c4801218fad455f9a79e9abdeead7c02cc3b7d04e8f93de6ddce2f58f",
    ),
}

# Executable bits to restore after extraction (relative to the store dir).
_EXECUTABLES = (
    "codex_cli_bin/bin/codex",
    "codex_cli_bin/bin/codex-code-mode-host",
    "codex_cli_bin/codex-path/rg",
    "codex_cli_bin/codex-resources/bwrap",
    "codex_cli_bin/codex-resources/zsh/bin/zsh",
)


class CodexRuntimeError(RuntimeError):
    """Base error for Codex runtime provisioning."""


class CodexRuntimeUnsupportedPlatform(CodexRuntimeError):
    """The current OS/arch has no published Codex binary."""


class CodexRuntimeIntegrityError(CodexRuntimeError):
    """Downloaded wheel failed sha256 verification."""


# In-process short-circuit once the runtime is confirmed present.
_ready_in_process = False


def _platform_tag() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "manylinux_2_17_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_2_17_aarch64"
    elif system == "Darwin":
        if machine == "arm64":
            return "macosx_11_0_arm64"
        if machine == "x86_64":
            return "macosx_10_9_x86_64"
    raise CodexRuntimeUnsupportedPlatform(
        f"No Codex binary for system={system!r} machine={machine!r}. "
        f"Supported: {sorted(_WHEELS)}."
    )


def _cache_root() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "twicc" / "codex-runtime"


def _store_dir() -> Path:
    return _cache_root() / CODEX_VERSION


def _ready_marker() -> Path:
    return _store_dir() / ".ready"


def codex_binary_path() -> Path:
    return _store_dir() / "codex_cli_bin" / "bin" / "codex"


def codex_path_dir() -> Path:
    """Directory holding ``rg`` — to prepend to PATH (SDK does this itself only
    when codex_bin is auto-resolved, which is never our case)."""
    return _store_dir() / "codex_cli_bin" / "codex-path"


def is_runtime_ready() -> bool:
    return _ready_marker().is_file() and codex_binary_path().is_file()


@contextmanager
def _file_lock(path: Path):
    """Inter-process exclusive lock (POSIX flock). TwiCC is Linux/macOS only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = open(path, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading Codex runtime %s from %s", CODEX_VERSION, url)
    with urllib.request.urlopen(url) as resp:  # follows the GitHub 302 redirect
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        next_pct = 10
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct >= next_pct:
                        logger.info(
                            "Codex runtime download: %d%% (%d/%d MB)",
                            pct, downloaded // (1024 * 1024), total // (1024 * 1024),
                        )
                        next_pct += 10
    logger.info("Codex runtime download complete (%d bytes)", downloaded)


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    if got != expected:
        path.unlink(missing_ok=True)
        raise CodexRuntimeIntegrityError(
            f"sha256 mismatch for {path.name}: expected {expected}, got {got}"
        )


def _download_and_extract() -> None:
    tag = _platform_tag()
    wheel_name, expected_sha = _WHEELS[tag]
    url = f"{_RELEASE_URL}/{wheel_name}"

    cache_root = _cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)

    with _file_lock(cache_root / f"{CODEX_VERSION}.lock"):
        if is_runtime_ready():
            return

        tmp_whl = cache_root / f"{wheel_name}.part"
        tmp_extract = cache_root / f"{CODEX_VERSION}.tmp"
        store = _store_dir()

        _download(url, tmp_whl)
        _verify_sha256(tmp_whl, expected_sha)

        shutil.rmtree(tmp_extract, ignore_errors=True)
        with zipfile.ZipFile(tmp_whl) as zf:
            zf.extractall(tmp_extract)
        tmp_whl.unlink(missing_ok=True)

        for rel in _EXECUTABLES:
            member = tmp_extract / rel
            if member.is_file():
                member.chmod(0o755)

        # Atomic-ish swap: remove any stale store, move the fresh tree in,
        # then write the .ready marker last so a crash mid-swap is never
        # mistaken for a ready runtime.
        shutil.rmtree(store, ignore_errors=True)
        tmp_extract.rename(store)
        _ready_marker().write_text(f"{CODEX_VERSION}\n{tag}\n", encoding="utf-8")
        logger.info("Codex runtime %s ready at %s", CODEX_VERSION, store)


def ensure_codex_runtime_sync() -> Path:
    """Ensure the runtime is present on disk; download+extract if missing.

    Blocking. Idempotent. Safe across threads and processes. Returns the store
    directory. Callers in an async context must use :func:`ensure_codex_runtime`
    instead so the download runs off the event loop.
    """
    global _ready_in_process
    if _ready_in_process or is_runtime_ready():
        _ready_in_process = True
        return _store_dir()
    _download_and_extract()
    _ready_in_process = True
    return _store_dir()


async def ensure_codex_runtime() -> Path:
    """Async wrapper: run the (blocking) provisioning in a worker thread."""
    return await asyncio.to_thread(ensure_codex_runtime_sync)
```

### 6.3 Rewrite `src/twicc/providers/codex/bin.py`

Replace the entire file with:

```python
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
    """
    path_dir = str(codex_path_dir())
    existing = os.environ.get("PATH", "")
    return {"PATH": f"{path_dir}{os.pathsep}{existing}" if existing else path_dir}


async def make_codex_config(*, cwd: str | None = None, **extra) -> CodexConfig:
    """Ensure the runtime is present, then build a ``CodexConfig`` for it.

    Async because the first call may trigger the one-time runtime download
    (run off the event loop). Every backend Codex entry point that builds a
    ``CodexConfig`` uses this.
    """
    await ensure_codex_runtime()
    return CodexConfig(
        codex_bin=str(codex_binary_path()),
        env=_codex_env(),
        cwd=cwd,
        **extra,
    )
```

### 6.4 Update the call sites

For each, ensure the enclosing function is `async` (all are), swap the
`resolve_bundled_binary()` + `CodexConfig(...)` pair for one
`await make_codex_config(...)`, and fix imports (drop the now-unused
`CodexConfig` and `resolve_bundled_binary` imports; add `make_codex_config`).

**`agent/manager.py`**
- Imports: remove `from openai_codex import CodexConfig`; change
  `from ..bin import resolve_bundled_binary` → `from ..bin import make_codex_config`.
- Body (the `_create_agent` block):
  ```python
  bundled_bin = resolve_bundled_binary()
  config = CodexConfig(codex_bin=str(bundled_bin), cwd=cwd)
  ```
  →
  ```python
  config = await make_codex_config(cwd=cwd)
  ```

**`credentials.py`** (2 sites, `refresh_token_via_codex_sdk` and the nested
`_execute`)
- Imports: `from openai_codex import CodexConfig, TextInput` → `from openai_codex import TextInput`;
  `from .bin import resolve_bundled_binary` → `from .bin import make_codex_config`.
- Each site:
  ```python
  bundled_bin = resolve_bundled_binary()
  config = CodexConfig(codex_bin=str(bundled_bin))
  ```
  →
  ```python
  config = await make_codex_config()
  ```

**`titles.py`** (3 sites)
- Imports: `from openai_codex import CodexConfig, AsyncCodex` → `from openai_codex import AsyncCodex`;
  `from .bin import resolve_bundled_binary` → `from .bin import make_codex_config`.
- Each site (`bundled_bin = resolve_bundled_binary()` + `config = CodexConfig(codex_bin=str(bundled_bin))`)
  → `config = await make_codex_config()`.

**`title_suggest.py`** (1 site, inside `async def generate_title`)
- Imports: `from openai_codex import CodexConfig, TextInput` → `from openai_codex import TextInput`;
  `from .bin import resolve_bundled_binary` → `from .bin import make_codex_config`.
- Site → `config = await make_codex_config()`.

**`commands_task.py`** (1 site, inside `async def _sync_to_database`; imports are local)
- Local imports: remove `from openai_codex import CodexConfig`; keep
  `from openai_codex.async_client import AsyncCodexClient`; change
  `from twicc.providers.codex.bin import resolve_bundled_binary` →
  `from twicc.providers.codex.bin import make_codex_config`.
- Site:
  ```python
  bundled_bin = resolve_bundled_binary()
  ...
  config = CodexConfig(codex_bin=str(bundled_bin), cwd=str(Path.home()))
  ```
  →
  ```python
  config = await make_codex_config(cwd=str(Path.home()))
  ```

**`plugin_install.py`** (1 site, inside `async def ensure_twicc_plugin_installed`; imports local)
- Local imports: remove `from openai_codex import CodexConfig`; keep
  `AsyncCodexClient` + the generated response imports; change
  `resolve_bundled_binary` → `make_codex_config`.
- Site (`bundled_bin = resolve_bundled_binary()` + `config = CodexConfig(codex_bin=..., cwd=...)`)
  → `config = await make_codex_config(cwd=str(Path.home()))`.

**`trust.py`** (1 site, inside the async config-write helper; imports local)
- Local imports: remove `from openai_codex import CodexConfig`; keep
  `AsyncCodexClient` + `ConfigWriteResponse`; change `resolve_bundled_binary` →
  `make_codex_config`.
- Site (`bundled = resolve_bundled_binary()` + `config = CodexConfig(codex_bin=str(bundled), cwd=str(Path.home()))`)
  → `config = await make_codex_config(cwd=str(Path.home()))`.

**`auth.py`** — **no change**. It stays sync and keeps
`binary = str(resolve_bundled_binary())` inside its `try/except FileNotFoundError`.
`resolve_bundled_binary()` now raises `CodexRuntimeNotReady` (a
`FileNotFoundError` subclass) if the runtime isn't downloaded yet, so the
existing handler already covers it (returns "not logged in" until the runtime
lands, which happens shortly after startup).

After editing, verify nothing else in these files still references the dropped
names:
```bash
rg -n "resolve_bundled_binary|CodexConfig\(" src/twicc/providers/codex/ | rg -v "bin.py|auth.py|make_codex_config"
# expected: no matches (auth.py keeps resolve_bundled_binary; bin.py defines both)
```

### 6.5 Boot hook — unconditional background pre-download

In `src/twicc/orchestrator.py`, class `OrchestratorRegistry`:

- In `__init__`, add:
  ```python
  self._codex_runtime_task: asyncio.Task | None = None
  ```
- At the very top of `start_all()` (BEFORE `enabled = get_enabled_providers()`
  and its `if not enabled: return`), add:
  ```python
  # Pre-download the Codex CLI runtime unconditionally, in the background.
  # NOT gated on Codex being enabled: the user can enable Codex live from the
  # UI and must not wait ~1 min for the binary at that point. Best-effort —
  # a failure here only means Codex features degrade until the next attempt;
  # TwiCC and Claude Code keep running.
  self._codex_runtime_task = asyncio.create_task(
      self._predownload_codex_runtime(),
      name="codex-runtime-predownload",
  )
  ```
- Add the method:
  ```python
  async def _predownload_codex_runtime(self) -> None:
      try:
          from twicc.providers.codex.runtime import ensure_codex_runtime
          await ensure_codex_runtime()
      except Exception:
          logger.exception(
              "Codex runtime pre-download failed; it will be retried on first use"
          )
  ```
- In the registry's shutdown path (`shutdown_all` / equivalent), cancel it
  best-effort if still running:
  ```python
  if self._codex_runtime_task is not None and not self._codex_runtime_task.done():
      self._codex_runtime_task.cancel()
  ```

Note: the per-provider Codex orchestrator (`providers/codex/orchestrator.py`)
already `await`s the binary indirectly through `ensure_twicc_plugin_installed()`
→ `make_codex_config()` → `ensure_codex_runtime()`, so it self-heals even if the
background pre-download hasn't finished when Codex starts. No change needed
there.

### 6.6 `pyproject.toml` + lockfile

- Remove the dependency line:
  ```
  "openai-codex-cli-bin==0.136.0",
  ```
- Update the `[tool.hatch.build.targets.wheel]` comment that mentions the
  version tag: `rust-v0.135.0` → `rust-v0.144.0` (and drop the "Codex CLI binary
  comes from openai-codex-cli-bin on PyPI" clause — it now comes from GitHub at
  runtime).
- Regenerate the lockfile: `uv lock` (this removes `openai-codex-cli-bin` and
  its transitive entries).

No change to `hatch_build.py`, `build-release.sh`, `.gitignore` — the TwiCC
wheel stays `py3-none-any`, the sdist stays publishable, and the runtime cache
lives outside the repo (`~/.cache/twicc/`).

### 6.7 Docs

**`docs/codex-vendoring.md`** — rewrite the "CLI binary" bullet and the layout
table to describe the runtime download. Key points to convey:
- SDK still vendored from `rust-v0.144.0` into `src/openai_codex/`.
- CLI binary downloaded at launch from the GitHub Release (not PyPI; OpenAI
  stopped publishing stable cli-bin wheels there after 0.136.0), cached under
  `~/.cache/twicc/codex-runtime/<version>/`, provisioned by
  `src/twicc/providers/codex/runtime.py`.
- The bump procedure now lives in the memory `reference_codex_sdk_update_procedure.md`.

**`CLAUDE.md`** and **`AGENTS.md`** — update any Release-Process wording that
says the Codex binary comes from the `openai-codex-cli-bin` PyPI dependency:
it is now downloaded at runtime from GitHub Releases. (The wheel/sdist story is
unchanged: single `py3-none-any` wheel + publishable sdist.) Keep both files in
sync (AGENTS.md mirrors CLAUDE.md).

**`CHANGELOG.md`** — replace the existing `[Unreleased] > Changed` line
```
- Bump vendored Codex Python SDK to rust-v0.136.0 (bundled Codex CLI: 0.131.0a4 → 0.136.0)
```
with
```
- Bump vendored Codex Python SDK to rust-v0.144.0. The Codex CLI runtime is now downloaded on first launch from GitHub Releases and cached under `~/.cache/twicc/` (OpenAI stopped publishing stable Codex binaries to PyPI).
```

### 6.8 Memory update

Update `reference_codex_sdk_update_procedure.md` (in the user's memory dir) to
reflect: (a) the binary is no longer a PyPI dep but a runtime download from
GitHub Releases, (b) the new bump procedure = re-vendor SDK + update
`CODEX_VERSION`/`CODEX_RELEASE_TAG` + recompute the 4 sha256, (c) `runtime.py` is
a new dependency-of-note. Add the sha256 recompute one-liner (see §10).

## 7. Implementation order

1. Re-vendor SDK 0.144.0 (§6.1).
2. Add `runtime.py` (§6.2).
3. Rewrite `bin.py` (§6.3).
4. Update the 8 call sites (§6.4); run the `rg` verification.
5. Add the boot hook in `orchestrator.py` (§6.5).
6. `pyproject.toml` + `uv lock` (§6.6).
7. Docs + CHANGELOG + memory (§6.7, §6.8).
8. `uv sync`, then the smoke test (§8).

## 8. Verification / smoke test

Import/parse check (no network):
```bash
cd <repo-root> && TWICC_DATA_DIR=$PWD uv run python -c "
import twicc.providers.codex.runtime as r
import twicc.providers.codex.bin as b
print('platform tag:', r._platform_tag())
print('store dir:', r._store_dir())
print('ready?', r.is_runtime_ready())
"
```

First-launch download (network): start the servers via `devctl.py start`, then
`tail -f <data_dir>/logs/backend.log` and confirm:
- `Downloading Codex runtime 0.144.0 from https://github.com/openai/codex/releases/download/rust-v0.144.0/...`
- progress lines `10% ... 100%`
- `Codex runtime 0.144.0 ready at ~/.cache/twicc/codex-runtime/0.144.0`
- the download happens **even if the Codex provider is disabled**.

Functional (user, in the UI):
- Create a Codex session in `read_only` mode → trigger a shell command that
  needs approval → the approval prompt must appear in the UI (not auto-accepted).
- Confirm `rg`-based search works inside a Codex turn (proves the PATH overlay).
- Test a `yolo`-mode session → approvals stay silent.
- Verify the binary lives under `~/.cache/twicc/codex-runtime/0.144.0/codex_cli_bin/bin/codex`
  and that `codex-resources/bwrap` + `codex-path/rg` are present.

Build check:
```bash
./scripts/build-release.sh && ls -lh dist/
# expect exactly: twicc-<ver>.tar.gz + twicc-<ver>-py3-none-any.whl (single wheel, no binary inside)
```

## 9. Rollback

Revert the commit. `main` currently pins `openai-codex-cli-bin==0.136.0` (still
present on PyPI) with SDK `rust-v0.136.0`, which is a working state.

## 10. Future version bumps

When a newer stable ships (and OpenAI still doesn't publish it to PyPI):
1. Re-vendor SDK from the new `rust-vX.Y.Z` tag (§6.1), run the memory checklist
   `reference_codex_sdk_update_procedure.md` (monkey-patch path, `_inputs`
   helpers, generated params, `codex_cli_bin` import, `__init__` diff for class
   renames).
2. Bump `CODEX_VERSION` + `CODEX_RELEASE_TAG` in `runtime.py`.
3. Recompute the 4 sha256:
   ```bash
   TAG=rust-vX.Y.Z; V=X.Y.Z
   for w in manylinux_2_17_x86_64 manylinux_2_17_aarch64 macosx_11_0_arm64 macosx_10_9_x86_64; do
     f=openai_codex_cli_bin-$V-py3-none-$w.whl
     echo "$f  $(curl -sL https://github.com/openai/codex/releases/download/$TAG/$f | sha256sum | cut -d' ' -f1)"
   done
   ```
   Paste into `_WHEELS`.
4. Update docs + CHANGELOG. Smoke-test.
```
