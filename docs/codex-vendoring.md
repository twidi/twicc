# Codex Python SDK — vendored

> Maintainer-level notes on how the Codex provider is bundled, and how to update it.

The Codex provider relies on OpenAI's Codex Python SDK (`openai_codex`) plus the Codex CLI binary it drives over JSON-RPC.

- The **SDK** is vendored from the `openai/codex` repository at tag [`rust-v0.153.4`](https://github.com/openai/codex/releases/tag/rust-v0.153.4). A PyPI release (`openai-codex`) exists but currently pins an older runtime version, so we stay on the vendored source to ride a known-good combination with the matching upstream tag.
- The **CLI binary** is downloaded at first launch from the matching GitHub Release (`openai-codex-cli-bin` wheel), extracted into a shared cache, and pointed at via `CodexConfig(codex_bin=…)`. It is no longer a PyPI dependency: OpenAI stopped publishing stable `openai-codex-cli-bin` wheels on PyPI after `0.136.0` (the wheel is ~122 MB, above practical PyPI quotas), but every tagged stable still ships the same manylinux / macOS wheels as GitHub Release assets. Provisioning lives in `src/twicc/providers/codex/runtime.py`; the download is triggered unconditionally in the background at startup (`OrchestratorRegistry.start_all`) and on demand by `make_codex_config`.

The cache is shared by every checkout on the machine, so bumping `CODEX_VERSION` in a worktree would prune the version the main instance is running on. `TWICC_NO_CODEX_RUNTIME_CLEANUP=1` (set by devctl in worktree mode) downloads without ever pruning, and provisioning re-checks the store on every call so a runtime deleted under a live process is downloaded again.

## Layout

| Path                                         | Origin                                                                              |
|----------------------------------------------|-------------------------------------------------------------------------------------|
| `src/openai_codex/`                          | Vendored SDK source. Treat as read-only — edits land upstream, then we re-sync — except the local patches listed below. |
| `src/twicc/providers/codex/runtime.py`       | Downloads + caches the Codex CLI binary wheel from the GitHub Release into `~/.cache/twicc/codex-runtime/<version>/` (honours `$XDG_CACHE_HOME`). Holds the pinned `CODEX_VERSION` / `CODEX_RELEASE_TAG` and the 4 wheel sha256. |
| `src/twicc/providers/codex/sdk_wrappers.py`  | TwiCC subclasses (`TwiccAsyncCodex`, `TwiccAsyncThread`) that expose `*_with_policy` methods so we can keep our 5 fine-grained presets — the upstream SDK now only exposes the coarse `ApprovalMode`. |
| `src/twicc/providers/codex/bin.py`           | Single entry point: `resolve_bundled_binary()` (sync, non-downloading) + `make_codex_config()` (async, ensures the runtime and builds a `CodexConfig` that also puts `rg` on PATH). |

The extracted tree is the whole `codex_cli_bin/` package (not just the `codex` binary), so its sibling resources ship too: `codex-resources/bwrap` (Linux sandbox helper) and `codex-resources/zsh` are found by `codex` relative to its own path, and `codex-path/rg` (ripgrep) is put on PATH by `make_codex_config` (the SDK only does that itself when `codex_bin` is auto-resolved, which is never our case).

## Local patches

Deliberate divergences from the pristine upstream tree. Re-apply them after every re-vendor (a `diff -rq <pristine> src/openai_codex` against the current version's pristine tree finds them all), and drop each one once upstream ships the fix.

- `generated/v2_all.py` — `SubAgentActivityKind`: added `completed = "completed"` (2026-08-27, retained on 0.153.4). The runtime emits `subAgentActivity` items with that kind, but the generated models at the same tag lag behind; without the patch, `thread/resume` validation (`ThreadResumeResponse`) rejects any rollout containing one and the session can no longer resume.

## Updating to a newer Codex version

Assumes the new version is published as a `rust-vX.Y.Z` GitHub tag/Release with the `openai-codex-cli-bin` wheels attached (they still are, even though PyPI stopped receiving stable ones).

1. Pick the release tag matching the upstream version you want, e.g. `rust-v0.153.4`. Verify that `sdk/python/src/openai_codex/` exists at that tag and the GitHub Release carries the 4 platform wheels.
2. Re-vendor the SDK source (extract the `openai_codex` package from the tarball):
   ```bash
   rm -rf src/openai_codex
   mkdir -p /tmp/codex-sdk
   curl -sL "https://codeload.github.com/openai/codex/tar.gz/refs/tags/<new_tag>" -o /tmp/codex.tar.gz
   tar -xzf /tmp/codex.tar.gz -C /tmp/codex-sdk "codex-<new_tag>/sdk/python/src/openai_codex"
   mv /tmp/codex-sdk/codex-<new_tag>/sdk/python/src/openai_codex src/openai_codex
   ```
   Then re-apply the [local patches](#local-patches) still needed at the new tag.
3. Bump `CODEX_VERSION` and `CODEX_RELEASE_TAG` in `src/twicc/providers/codex/runtime.py`, and recompute the 4 wheel sha256:
   ```bash
   TAG=rust-vX.Y.Z; V=X.Y.Z
   for w in manylinux_2_17_x86_64 manylinux_2_17_aarch64 macosx_11_0_arm64 macosx_10_9_x86_64; do
     f=openai_codex_cli_bin-$V-py3-none-$w.whl
     echo "$f  $(curl -sL https://github.com/openai/codex/releases/download/$TAG/$f | sha256sum | cut -d' ' -f1)"
   done
   ```
   Paste the digests into `_WHEELS`.
4. Diff the new SDK's `pyproject.toml` against ours — copy any new **runtime** dependency over (today only `pydantic>=2.12` is shared; the SDK's own `openai-codex-cli-bin` pin is deliberately ignored — that's the whole point of the runtime download).
5. Run the checklist from the `reference_codex_sdk_update_procedure.md` memory: verify the monkey-patch path `_client._sync._approval_handler`, that `ThreadStartParams`/`TurnStartParams` still accept `approval_policy`/`approvals_reviewer`/`sandbox(_policy)`, that the SDK subclassing in `sdk_wrappers.py` still compiles, that the `_inputs._normalize_run_input` / `_to_wire_input` helpers still exist, etc. Note the lazy `from codex_cli_bin import bundled_codex_path` in `client.py` is fine to leave — it's only reached when `codex_bin` is `None`, which we never do, and it degrades to a `FileNotFoundError` if the package is absent.
6. Re-verify the TwiCC MCP server's per-thread config keys (`src/twicc/providers/codex/agent/manager.py`, `_twicc_mcp_server_config`): `url`, `http_headers`, `default_tools_approval_mode`, `tool_timeout_sec`, `startup_timeout_sec` against `codex-rs/config/src/mcp_types.rs`; that the streamable-HTTP MCP client stays un-gated (`codex-rs/codex-mcp/src/connection_manager.rs`); and the `tool_search_always_defer_mcp_tools` feature key (`codex-rs/features/src/lib.rs`) — `Stage::Removed` with `default_enabled: true` since at least 0.144.6, so Codex silently ignores the key and always defers. `_apply_codex_mcp_context_mode` is therefore a harmless no-op: flip `TWICC_MCP_CODEX_DEFER=False` in `src/twicc/mcp/__init__.py` (eager fallback) only if deferral itself regresses, not because the flag disappeared.
7. Run `uv lock`, then `./scripts/build-release.sh` and check the resulting single `py3-none-any` wheel installs and runs locally (first launch downloads the runtime).

## Why we still vendor the SDK

The PyPI package `openai-codex` exists but currently pins an older runtime than the wheel we want to ship, and TwiCC reaches into private SDK attributes:

- `_client._sync._approval_handler` for the approval bridge (`CodexAgent.__init__`)
- `_client._sync._proc` for the subprocess PID (`CodexAgent.get_pid`)
- `_inputs._normalize_run_input` / `_inputs._to_wire_input` for the `*_with_policy` wrappers

Vendoring keeps the SDK pinned to the same commit as the binary we depend on, and makes any upstream refactor of those private attributes visible in the diff (during the next vendor refresh) rather than as a runtime `AttributeError`.

If/when the PyPI SDK catches up and TwiCC migrates off those private attributes (e.g. when OpenAI exposes a public approval-handler hook), we can drop the vendoring entirely and depend on `openai-codex` from PyPI like any other library.
