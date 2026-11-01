# TwiCC MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the whole skill-covered TwiCC CLI surface as MCP tools served by TwiCC's own backend at `/mcp`, wired per-session into both Claude Code and Codex agents, executing in-process (no drop files, no subprocess, no HTTP round-trip to self).

**Architecture:** A Streamable-HTTP MCP endpoint mounted as a raw-ASGI route in front of Django (same process as the channel layer, agent registry and DB). Tools are auto-generated from the existing Click/Typer command tree via the same `rpc/` registry machinery (`build_registry()` → JSON Schemas → `render_argv()` → `invoke()`), so the CLI stays the single source of truth. Two ContextVars turn the in-process invocation into a first-class citizen: one carries the caller's session identity (replacing PID-ancestry `whoami`), one carries the running event loop so the drop-request transport short-circuits into a direct `await` of the same `core/services/*` handlers the filesystem watcher would call.

**Tech Stack:** Python `mcp` SDK 1.27 (lowlevel `Server` + `StreamableHTTPSessionManager`, stateless + JSON responses) · existing Typer/Click CLI + `twicc.rpc` registry · Django 6 ASGI/Channels · HMAC session tokens.

---

## 0. Context and references (read before starting)

- Research doc (feasibility, provider wiring options, context-cost analysis): `docs/plans/2026-07-06-custom-tools-mcp-research.md`. This plan implements its "recommended shape" (§1, §5). Do **not** edit the research doc.
- The three existing "front doors" this plan adds a fourth to:
  - **CLI**: Typer tree in `src/twicc/cli/__init__.py`; read commands hit the ORM directly, write commands drop a file in `<data_dir>/drop-requests/` and poll a status file (`src/twicc/cli/_drop_request/`, `src/twicc/drop_requests_watcher.py`).
  - **Skills**: prose wrappers of the CLI in `src/twicc/agent/plugin/twicc/skills/` (26 command skills + 3 orchestration role skills). Untouched by this plan (see Decision B).
  - **RPC**: `POST /rpc/<command>` (`src/twicc/rpc/`), auto-generated from the Click tree: `generator.build_registry()` (path → `CommandSpec` with JSON Schema), `render_argv()` (JSON body → argv), `invoker.invoke()` (in-process Click execution, ContextVar-captured output). The MCP server reuses ALL of this.
- Key verified facts (re-verify on SDK bumps, see §10):
  - `mcp` 1.27.0 is already in the venv (transitive dep of `claude-agent-sdk`); `StreamableHTTPSessionManager(app, json_response=True, stateless=True, security_settings=...)` exposes `handle_request(scope, receive, send)` (raw ASGI) and an `async with manager.run():` lifespan.
  - The streamable-HTTP transport passes the starlette `Request` into `RequestContext.request` → tool handlers can read the caller's `Authorization` header (`server.request_context.request.headers`). One server instance serves every session.
  - Lowlevel `Server.call_tool()` validates arguments against the tool's `inputSchema` (jsonschema, `additionalProperties: false` already emitted by `twicc.rpc.schema.json_schema_for`) and a handler returning a `dict` is auto-wrapped as structured content + JSON text.
  - `mcp.types.Tool` has `annotations` and `meta` (serialized as `_meta`) → carries `anthropic/alwaysLoad` for Claude's Tool Search.
  - Claude SDK: `ClaudeAgentOptions.mcp_servers` accepts a dict (`McpHttpServerConfig`, `types.py:619-624`) **or a `str | Path` to a config file — the form this plan uses (Task 9), because the dict form is serialized inline onto the CLI argv (token visible in `ps`)**; TwiCC builds options in `src/twicc/providers/claude_code/agent/agent.py` (the `ClaudeAgentOptions(...)` call around line 956); hybrid CLI launch builds argv in `src/twicc/providers/claude_code/agent/hybrid/launch.py` (~line 141, where `--plugin-dir` is added).
  - Codex 0.136: streamable HTTP MCP client is native (no feature flag; `codex-rs/codex-mcp/src/connection_manager.rs` instantiates `McpServerTransportConfig::StreamableHttp` unconditionally). Per-server config keys verified in `codex-rs/config/src/mcp_types.rs`: `url`, `http_headers` (static map), `env_http_headers`, `bearer_token_env_var` (`bearer_token` inline is rejected for HTTP), `default_tools_approval_mode` (`auto`/`prompt`/`approve`; `approve` short-circuits the whole approval check), `tool_timeout_sec`, `startup_timeout_sec`, `enabled_tools`/`disabled_tools`. TwiCC passes per-thread config in `src/twicc/providers/codex/agent/manager.py` `_create_agent` (`thread_config` dict, ~line 594).
  - Codex mints its canonical session id only when `thread_start` returns (`thread.id`); the draft id passed into `_create_agent` differs for brand-new sessions.
  - Registry stats (this checkout): 71 RPC routes; excluding the `settings` group → 56 routes ≈ 68 KB of JSON Schema + ~10 KB of full Click help text. All 56 fit under Codex's 100-tool eager-loading threshold, so Codex would load every schema into context (~17k tokens) **unless deferral is forced** — which this plan does by default (`TWICC_MCP_CODEX_DEFER=True`, D10). Claude defers everything via Tool Search (names only) natively.
  - `django.conf.settings.SECRET_KEY` is a hardcoded dev constant → NOT usable as an HMAC key; a per-install secret file is required.
  - All `core/services/*` payload handlers are `async def` and safe to await on the event loop (they already serialize writes via `run_under_db_write_lock`); the channel layer is in-memory → the MCP endpoint MUST live in the backend process (it does: it's an ASGI route).

### Worktree discipline (from CLAUDE.md — repeated because violations are destructive)

- Prefix **every** Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && `.
- Tests: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run --active pytest ...` (without `--active`, tests run against `main`'s source).
- Never run `migrate`/`npm install` by hand. No Django model changes in this plan → no migrations at all.

### Testing conventions (apply to every test snippet in this plan)

- The test stack is `pytest` + `pytest-django` **only** — there is NO `pytest-asyncio` and no `asyncio_mode` config. **Never use `@pytest.mark.asyncio` or async fixtures.** Every async scenario in this plan is written as a sync test that calls `asyncio.run(scenario())`; keep that shape.
- Data-dir isolation: tests that touch data-dir files (`workspaces.json`, drop files, the MCP secret) must not write into the real worktree data dir. Before writing such a test, `rg -l "TWICC_DATA_DIR|get_data_dir" tests/` and reuse the existing isolation fixture/pattern found there (monkeypatched env + any cache reset it performs). Where this plan's snippets create workspaces (`ws-inproc`, `ws-via-mcp`, `mcp-test-ws`), wrap them with that fixture.
- Monkeypatching path helpers: modules that do `from twicc.paths import X` bind the symbol at import time — patch the **consumer's** symbol (e.g. `twicc.cli._drop_request.drop_file.get_drop_requests_dir`), not `twicc.paths.X`. (`twicc.mcp.identity` deliberately calls `paths.get_mcp_secret_path()` dynamically, so patching `twicc.paths` works there.)
- Model fixtures: `Project` has a `directory` field (NOT `path`); `Session(id=..., project=...)` is enough (`provider`/`file_path` are non-null CharFields defaulting to `""`). Mirror an existing test in `tests/` that creates these models.

---

## 1. Decisions taken (defaults chosen; branches only where flagged)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Transport | Streamable HTTP, **stateless**, **JSON responses** (no SSE), mounted at `/mcp` as raw ASGI before Django | Simplest conformant mode; no session bookkeeping; both clients support it. No Django middleware/urls.py/SPA-regex involvement. |
| D2 | Tool source of truth | The Click tree via `twicc.rpc.generator` — zero per-tool glue | Same auto-generation contract as `/rpc/`; adding a CLI command automatically adds the RPC route, the OpenAPI entry AND the MCP tool. |
| D3 | Tool set | All registry routes **except** the `settings` group, **plus** `whoami` (re-admitted from local-only) | Matches the skill-covered surface exactly (skills deliberately exclude `settings`); `whoami` becomes the identity primitive. `password`/`token`/`run`/`claude`/`codex` stay excluded (local-only). |
| D4 | Execution path | `render_argv()` → `invoke()` in a worker thread (same as `/rpc/`), **but** mutations bypass the drop-file: a transport seam routes the payload straight to the watcher's service handler on the event loop | "Simulate the drop request" without filesystem or polling latency; every CLI validation/alias/payload-building line still runs. |
| D5 | Caller identity | Self-describing HMAC token `twicc_mcp_<session_id>.<sig>` in the `Authorization: Bearer` header; secret = per-install random file in the data dir | Deterministic → survives backend restarts (hybrid tmux agents outlive the backend); no DB column; `self`/`parent`/`whoami`/auto-`spawned_by` resolve from a ContextVar override instead of PID ancestry. |
| D6 | Auth policy | `/mcp` **always** requires a valid token — session token (identity-bound) or an existing `twicc_pat_*` API token (full access, no identity). Unauthenticated → 401. Remote callers additionally pass through the same `scope_remote_access_blocked` gate as WS | Auth comes free with identity; external MCP clients (user's own tools) reuse `twicc token create`. No new auth surface to explain. |
| D7 | Descriptions | Tool description = full Click long help (`cmd.help`), falling back to the short summary | Only ~10 KB across all tools; it IS the maintained documentation (options semantics, exit codes, `self`/`parent` behavior). |
| D8 | Claude context | Rely on default Tool Search deferral; mark 5 hot tools `_meta["anthropic/alwaysLoad"] = true` | Free, fine-grained; per research doc §3. |
| D9 | **Availability & approval — control plane, orthogonal to project permissions (USER DECISION)** | **Every MCP tool is available in every permission mode, on both providers, and auto-approved (no per-call prompt).** No `enabled_tools` filtering by mode; Codex `default_tools_approval_mode="approve"`; Claude `can_use_tool` auto-allows `mcp__twicc__*`. | The `twicc-*` tools drive TwiCC's control plane (sessions, messages, bookmarks…), never the project's code. The permission mode governs what the agent may do **to the project** — gating "send a message to session X" behind an approval is meaningless. Exactly like the skills, which are available in every mode. **Consciously revises the earlier "read-only orchestration leaves stay pull-only" stance:** a read-only/strict session can now act on the orchestration via MCP (a channel the exec sandbox doesn't block). Accepted by the user (2026-07-06): "tout dispo, auto-approve partout". |
| D10 | Codex context | **Constant-switched, both modes coded.** `TWICC_MCP_CODEX_DEFER` (module constant, default `True`) → forced deferral (`features.tool_search_always_defer_mcp_tools=true` + `suppress_unstable_features_warning=true`); set to `False` → eager (all schemas in context). | User wants both codepaths present and a one-line flip. Default = forced defer (clean Codex context, full tool set); fall back to eager by flipping the constant if the experimental flag regresses on a Codex bump. |
| D11 | Kill switch | Env `TWICC_NO_MCP=1` disables mount + injection (mirrors `TWICC_NO_CODEX_PLUGIN`) | Escape hatch; no settings/UI surface for this feature. |
| D12 | Skills | Untouched (no plugin version bump). MCP and skills coexist; agents naturally prefer typed tools | See Branch B if/when the user wants the skills to advertise MCP (deferred by the user). |
| D13 | `/rpc/` flip | Final optional task flips `/rpc/` mutations onto the same in-process transport (one ContextVar set in the dispatch view) | Same code path, removes drop-file latency from the RPC API too. Recommended. |

### Branch A — Codex context cost — RESOLVED (D10)

Decided: both codepaths are implemented and selected by the module constant `TWICC_MCP_CODEX_DEFER` (Task 10). Default `True` = forced deferral (A3); `False` = eager. The A2 "curated subset" idea is **dropped** (the user wants the full tool set on Codex, not a subset). Kept here only as a note: were context ever a hard problem, a per-server `enabled_tools` allowlist is the zero-context lever — but it is not implemented.

### Branch B — Skills coexistence wording — DEFERRED by the user

No skill change now. Later (user's call): add one line to the shared "How to invoke" preamble of the 26 command skills ("If the `mcp__twicc__*` tools are available, prefer them over the CLI — same semantics, same JSON output.") + bump `plugin.json` version (minor). Mechanical follow-up; not part of the tasks below.

### Branch C — Codex/MCP approval gating — RESOLVED (D9)

Decided: **auto-approve everywhere, no per-mode filtering** (see D9). This side-steps — but does NOT fix — a broader, pre-existing gap the user acknowledged as a **separate workstream**: TwiCC's Codex approval bridge (`providers/codex/agent/approvals.py`) handles only `commandExecution`/`fileChange`/`permissions` and silently declines `mcpServer/elicitation/request` (verified against vendored Codex `rust-v0.136.0`; documented in `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` §1.6/Q9). Because we set `default_tools_approval_mode="approve"`, TwiCC-MCP never triggers that path. If per-call MCP prompts are ever wanted (Claude-parity), the path is: implement `mcpServer/elicitation/request` in the Codex bridge, then set the twicc server's `default_tools_approval_mode="prompt"`. Tracked in the risk ledger (§16), not a task here.

---

## 2. File structure

New package `src/twicc/mcp/` (no import conflict with the pip `mcp` package — absolute imports resolve top-level `mcp` normally):

| File | Responsibility |
|---|---|
| `src/twicc/mcp/__init__.py` | Package marker + `mcp_enabled()` + `mcp_base_url()` helpers + the `TWICC_MCP_CODEX_DEFER` constant |
| `src/twicc/mcp/identity.py` | Per-install secret, session-token mint/verify, Codex draft-id alias map |
| `src/twicc/mcp/tools.py` | MCP tool registry: route selection, naming, descriptions, annotations, `_meta` |
| `src/twicc/mcp/wiring.py` | Per-session Claude `.mcp.json` config-file writer (shared by SDK + hybrid paths) |
| `src/twicc/mcp/server.py` | Lowlevel `Server` (list_tools / call_tool), `StreamableHTTPSessionManager` singleton, instructions text |
| `src/twicc/mcp/endpoint.py` | Raw-ASGI handler: auth gate (401/403), delegation to the session manager, lifespan task for `run.py` |

Modified:

| File | Change |
|---|---|
| `src/twicc/paths.py` | `get_mcp_secret_path()` |
| `src/twicc/cli/_drop_request/whoami.py` | ContextVar identity override in `resolve_current_session()` |
| `src/twicc/drop_requests_watcher.py` | Extract `execute_drop_payload()` + `stamp_status_times()` from `_process_file`/`_write_status` |
| `src/twicc/cli/_drop_request/transport.py` (new) | Dual-mode submit/poll seam (drop-file vs in-process) |
| ~20 CLI command modules + `src/twicc/cli/_batch_runner.py` (authoritative list in Task 4) | Swap heartbeat/write/poll/unlink for the transport API |
| `src/twicc/rpc/generator.py` | Parameterize the excluded-roots set (needed to re-admit `whoami` for MCP) |
| `src/twicc/asgi.py` | HTTP router wrapping Django to intercept `/mcp` |
| `src/twicc/cli/run.py` | Start/stop the MCP session-manager lifespan task |
| `src/twicc/providers/claude_code/agent/agent.py` | `mcp_servers` + `env` in `ClaudeAgentOptions`; auto-approve `mcp__twicc__*` in `_handle_pending_request` |
| `src/twicc/providers/claude_code/agent/hybrid/launch.py` | `--mcp-config <file>` wiring |
| `src/twicc/providers/codex/agent/manager.py` | `thread_config["mcp_servers"]` (auto-approve) + context-mode features + draft-alias registration |
| `src/twicc/rpc/views.py` | (Task 11, optional) in-process transport for `/rpc/` mutations |
| `.env.example` | Document `TWICC_NO_MCP` |
| `SKILLS-AND-CLI.md`, `CLAUDE.md`, `AGENTS.md`, `RPC-API.md` | Documentation (Task 12) |

Tests (all new files under `tests/`): `test_mcp_identity.py`, `test_mcp_tools.py`, `test_mcp_server.py`, `test_mcp_endpoint.py`, `test_drop_transport.py`. Existing suites to keep green: `test_rpc_*` and any drop-request/CLI tests touched by the transport refactor.

---

## 3. Task 1 — Identity: secret file + session tokens + alias map

**Files:**
- Modify: `src/twicc/paths.py`
- Create: `src/twicc/mcp/__init__.py`, `src/twicc/mcp/identity.py`
- Test: `tests/test_mcp_identity.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_identity.py
"""Session-token mint/verify and the Codex draft-id alias map."""
import re

import pytest

from twicc.mcp import identity


@pytest.fixture(autouse=True)
def _isolated_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "twicc.paths.get_mcp_secret_path", lambda: tmp_path / "mcp-secret",
    )
    identity._reset_for_tests()
    yield
    identity._reset_for_tests()


def test_mint_and_resolve_roundtrip():
    token = identity.mint_session_token("abc-123")
    assert token.startswith("twicc_mcp_")
    assert identity.resolve_session_token(token) == "abc-123"


def test_token_is_deterministic_across_secret_reloads():
    t1 = identity.mint_session_token("abc-123")
    identity._reset_for_tests()  # drop the cached secret; file persists
    assert identity.mint_session_token("abc-123") == t1


def test_tampered_token_rejected():
    token = identity.mint_session_token("abc-123")
    sid, _, sig = token.removeprefix("twicc_mcp_").rpartition(".")
    forged = f"twicc_mcp_other-session.{sig}"
    assert identity.resolve_session_token(forged) is None
    assert identity.resolve_session_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert identity.resolve_session_token("garbage") is None
    assert identity.resolve_session_token("") is None


def test_secret_file_created_with_0600(tmp_path):
    identity.mint_session_token("abc")
    path = tmp_path / "mcp-secret"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_draft_alias_resolution():
    token = identity.mint_session_token("draft-id")
    identity.register_draft_alias("draft-id", "canonical-id")
    assert identity.resolve_session_token(token) == "canonical-id"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_identity.py -v`
Expected: FAIL (ModuleNotFoundError: twicc.mcp)

- [ ] **Step 3: Implement**

Append to `src/twicc/paths.py` (next to `get_api_tokens_path`):

```python
def get_mcp_secret_path() -> Path:
    """Per-install secret used to sign per-session MCP tokens (chmod 600)."""
    return get_data_dir() / "mcp-secret"
```

`src/twicc/mcp/__init__.py`:

```python
"""TwiCC's own MCP server: the CLI surface as per-session MCP tools.

The server is a Streamable-HTTP endpoint at ``/mcp`` on the backend's own
port, mounted as raw ASGI in :mod:`twicc.asgi` and started from ``run.py``
(:mod:`twicc.mcp.endpoint`). Tools are auto-generated from the Click tree
(:mod:`twicc.mcp.tools`) and executed in-process (:mod:`twicc.mcp.server`).
"""

from __future__ import annotations

import os


def mcp_enabled() -> bool:
    """Kill switch: ``TWICC_NO_MCP=1`` disables mount and per-session wiring."""
    return os.environ.get("TWICC_NO_MCP", "").strip().lower() not in ("1", "true", "yes")


def mcp_base_url() -> str:
    """Loopback URL agents call back to. Always local: agents run on this host."""
    port = os.environ.get("TWICC_PORT", "3500")
    return f"http://127.0.0.1:{port}/mcp"
```

`src/twicc/mcp/identity.py`:

```python
"""Caller identity for the MCP endpoint.

A session token is self-describing and deterministic:
``twicc_mcp_<session_id>.<hmac_sha256(secret, session_id)[:32]>``. The secret
is a per-install random file in the data dir, so tokens survive backend
restarts (a hybrid tmux agent outlives the backend and must keep calling
``/mcp`` with the token baked into its launch config) and need no storage or
revocation: they only grant "act as this session on this machine", the same
authority the PID-ancestry CLI grants any local process today.

Brand-new Codex sessions are the one wrinkle: the token is minted against the
frontend draft id (the canonical id only exists once ``thread_start``
returns), so the Codex manager registers a draft→canonical alias right after
the thread starts. The alias map is process-local by design — after a backend
restart the resume path re-wires the session with a token minted against the
canonical id, and the alias is no longer needed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

from twicc import paths

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "twicc_mcp_"
_SIG_LEN = 32  # hex chars = 128 bits, ample for a local HMAC capability

_secret: bytes | None = None
_draft_aliases: dict[str, str] = {}


def _reset_for_tests() -> None:
    global _secret
    _secret = None
    _draft_aliases.clear()


def _get_secret() -> bytes:
    """Read (or create once) the per-install signing secret, cached."""
    global _secret
    if _secret is None:
        path = paths.get_mcp_secret_path()
        try:
            _secret = bytes.fromhex(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            _secret = secrets.token_bytes(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(_secret.hex())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    return _secret


def _sign(session_id: str) -> str:
    mac = hmac.new(_get_secret(), f"mcp:{session_id}".encode(), hashlib.sha256)
    return mac.hexdigest()[:_SIG_LEN]


def mint_session_token(session_id: str) -> str:
    return f"{TOKEN_PREFIX}{session_id}.{_sign(session_id)}"


def resolve_session_token(token: str) -> str | None:
    """Return the (alias-resolved) session id, or None if invalid."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    session_id, sep, sig = token.removeprefix(TOKEN_PREFIX).rpartition(".")
    if not sep or not session_id:
        return None
    if not hmac.compare_digest(sig, _sign(session_id)):
        return None
    return _draft_aliases.get(session_id, session_id)


def register_draft_alias(draft_id: str, canonical_id: str) -> None:
    """Map a Codex draft session id to the canonical id minted by thread_start."""
    if draft_id != canonical_id:
        _draft_aliases[draft_id] = canonical_id
        logger.info("MCP identity: draft %s aliased to %s", draft_id, canonical_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_identity.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Add `mcp` as a direct dependency**

In `pyproject.toml` `[project] dependencies`, add `"mcp>=1.27,<2"` (it is currently only a transitive dep of `claude-agent-sdk`; the server now imports it directly). Do NOT run `uv add`/`uv sync` yourself — note at the end of the task that the user should run `uv sync` (or that devctl's editable rebuild covers it on next start). The venv already contains 1.27.0, so tests run fine meanwhile.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/mcp/__init__.py src/twicc/mcp/identity.py src/twicc/paths.py tests/test_mcp_identity.py pyproject.toml && git commit -m "feat(mcp): per-install secret and self-describing session tokens"
```

---

## 4. Task 2 — Identity override in `whoami`

`resolve_current_session()` is the single chokepoint behind `twicc whoami`, `self`/`parent` keywords, `--spawned-by self`, and the silent `spawned_by` auto-fill of `create-session`. MCP tool calls execute inside the backend, so PID ancestry would resolve to nothing (or worse, to whatever spawned the backend). A ContextVar override, set by the MCP dispatcher before `invoke()`, makes every one of those behaviors work identically — `asyncio.to_thread` propagates ContextVars into the worker thread.

**Files:**
- Modify: `src/twicc/cli/_drop_request/whoami.py`
- Test: `tests/test_mcp_identity.py` (extend) — plus DB-backed test

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_identity.py
import pytest
from twicc.cli._drop_request.whoami import forced_session_id, resolve_current_session


@pytest.mark.django_db
def test_forced_session_id_overrides_pid_walk():
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="-tmp-proj", directory="/tmp/proj", name="proj")
    session = Session.objects.create(
        id="11111111-1111-1111-1111-111111111111", project=project,
    )
    token = forced_session_id.set(session.id)
    try:
        resolved = resolve_current_session()
        assert resolved is not None and resolved.id == session.id
    finally:
        forced_session_id.reset(token)


@pytest.mark.django_db
def test_forced_unknown_session_id_resolves_none():
    token = forced_session_id.set("no-such-session")
    try:
        assert resolve_current_session() is None
    finally:
        forced_session_id.reset(token)
```

Note: mirror an existing test that creates `Project`/`Session` rows for any additional required kwargs (see Testing conventions in §0).

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_identity.py -v -k forced`
Expected: FAIL (ImportError: forced_session_id)

- [ ] **Step 3: Implement**

In `src/twicc/cli/_drop_request/whoami.py`, add near the top:

```python
from contextvars import ContextVar

# Out-of-band caller identity. The MCP endpoint (and any future in-backend
# invoker that knows who is calling) sets this before running a command
# in-process; PID ancestry is meaningless there (the "caller" is the backend
# itself). ``None`` = unset → fall back to the PID walk.
forced_session_id: ContextVar[str | None] = ContextVar("twicc_forced_session_id", default=None)
```

At the very start of `resolve_current_session()` (before the DB read of `ProcessRun`):

```python
    forced = forced_session_id.get()
    if forced is not None:
        from twicc.core.models import Session

        return Session.objects.filter(pk=forced).first()
```

(Keep the lazy import style of the module; the existing body remains the fallback.)

- [ ] **Step 4: Run tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_identity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/cli/_drop_request/whoami.py tests/test_mcp_identity.py && git commit -m "feat(mcp): ContextVar identity override for whoami/self/parent resolution"
```

---

## 5. Task 3 — Extract the watcher's execution core

Make the payload→service→status-dict pipeline callable without files, exactly once, shared by the watcher and the in-process transport.

**Files:**
- Modify: `src/twicc/drop_requests_watcher.py`
- Test: `tests/test_drop_transport.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drop_transport.py
"""In-process execution of drop-request payloads (no files involved)."""
import asyncio

import pytest

from twicc.drop_requests_watcher import execute_drop_payload


def test_execute_unknown_kind_returns_failed():
    status = asyncio.run(execute_drop_payload({"kind": "nope:nope"}, "nope:nope"))
    assert status["status"] == "failed"
    assert "Unknown payload kind" in status["error"]
    assert "failed_at" in status


@pytest.mark.django_db(transaction=True)
def test_execute_workspace_create_roundtrip(isolated_data_dir):  # see §0 conventions
    payload = {"kind": "workspace:create", "name": "mcp-test-ws"}
    status = asyncio.run(execute_drop_payload(payload, "workspace:create"))
    assert status["status"] == "created", status
    assert status["workspace_id"]
    assert "created_at" in status
```

Before finalizing: check how existing tests exercise `workspace:create` (search `tests/` for `create_workspace_from_payload` or `workspace:create`) and mirror their fixtures/marks — workspaces live in `workspaces.json`, so the data-dir isolation pattern from §0 is required (replace the placeholder `isolated_data_dir` with the repo's actual fixture/pattern).

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_drop_transport.py -v`
Expected: FAIL (ImportError: execute_drop_payload)

- [ ] **Step 3: Implement the extraction**

In `src/twicc/drop_requests_watcher.py`:

1. Extract the timestamp-stamping block of `_write_status` (the `status → *_at setdefault` ladder, lines ~313-329) into a module-level function, and make `_write_status` use it:

```python
def stamp_status_times(data: dict) -> dict:
    """Stamp the per-status timestamp field (same contract as the status file)."""
    stamps = {
        "received": "received_at", "created": "created_at", "sent": "sent_at",
        "updated": "updated_at", "stopped": "stopped_at", "deleted": "deleted_at",
        "rejected": "rejected_at", "failed": "failed_at",
    }
    field = stamps.get(data.get("status"))
    if field:
        data.setdefault(field, _iso_now())
    return data
```

2. Extract the dispatch/service/result-shaping middle of `_process_file` (from the `_KIND_HANDLERS.get(kind)` lookup down to building the success/rejected/failed dict) into:

```python
async def execute_drop_payload(payload: dict, kind: str | None) -> dict:
    """Route ``payload`` to its kind service and return the final status dict.

    Exactly the dict the watcher would persist as ``<uuid>.status.json``
    (timestamps included, ``request_uuid`` NOT included — the transport layer
    owns that key). Never raises: service exceptions become ``failed``.
    """
    handler = _KIND_HANDLERS.get(kind) if kind else None
    if handler is None:
        return stamp_status_times({"status": "failed", "error": f"Unknown payload kind: {kind!r}"})

    module_path, attr_name, success_status = handler
    service = getattr(importlib.import_module(module_path), attr_name)
    try:
        result = await service(payload)
    except Exception as e:  # noqa: BLE001 — mirror the watcher's catch-all
        logger.exception("[drop-request] service raised for kind %s", kind)
        return stamp_status_times({"status": "failed", "error": f"{type(e).__name__}: {e}"})

    if result.success:
        status_data: dict = {"status": success_status}
        for attr in _RESULT_ID_FIELDS:
            value = getattr(result, attr, None)
            if value is not None:
                status_data[attr] = value
        extra = getattr(result, "status_extra", None)
        if isinstance(extra, dict):
            status_data.update(extra)
        return stamp_status_times(status_data)
    return stamp_status_times({
        "status": "rejected",
        "errors": [e._asdict() for e in (result.errors or [])],
    })
```

3. Rewrite `_process_file` to call `execute_drop_payload(payload, kind)` after writing the `received` status, then `await self._write_status(request_uuid, status_data)` with the returned dict (keep the log lines; `_write_status` still adds `request_uuid` and is now a no-op for timestamps already stamped — `setdefault` semantics make this safe).

- [ ] **Step 4: Run the new test + the existing watcher/RPC suites**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_drop_transport.py tests/ -v -k "drop or rpc"`
Expected: PASS, no regression

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/drop_requests_watcher.py tests/test_drop_transport.py && git commit -m "refactor(drop-requests): extract file-free execute_drop_payload core"
```

---

## 6. Task 4 — Dual-mode transport seam + refactor the single-drop call sites

**Files:**
- Create: `src/twicc/cli/_drop_request/transport.py`
- Modify (single-submission sites, all following the same recipe):
  - `src/twicc/cli/artifacts_mutation.py` (worked example below)
  - `src/twicc/cli/create_project.py`
  - `src/twicc/cli/create_session/command.py`
  - `src/twicc/cli/create_workspace.py`
  - `src/twicc/cli/delete_workspace.py`
  - `src/twicc/cli/process_stop.py`
  - `src/twicc/cli/send_message/command.py`
  - `src/twicc/cli/settings/command.py`, `src/twicc/cli/settings/notifications.py`, `src/twicc/cli/settings/provider.py` (they share `settings/_output.py` helpers — refactor whatever helper wraps write+poll there)
  - `src/twicc/cli/update_project/command.py`, `src/twicc/cli/update_project/settings_command.py`
  - `src/twicc/cli/update_session/{annotations,archived,hidden,pinned,settings,title}_command.py`
  - `src/twicc/cli/update_workspace.py`
  - `src/twicc/cli/process_wait.py`, `src/twicc/cli/processes_wait.py` — **recipe step 1 only**: they poll the DB, never drop files, but call `check_heartbeat()` (lines ~50/86 and ~81/149) which must become `transport.ensure_server_available()` so the grep audit passes and waits work in-backend
- Test: extend `tests/test_drop_transport.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_drop_transport.py
import asyncio

from twicc.cli._drop_request import transport


def test_local_mode_still_uses_files(tmp_path, monkeypatch):
    # drop_file.py binds the symbol at import time — patch the consumer.
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: tmp_path,
    )
    sub = transport.submit({"name": "x"}, kind="workspace:create")
    assert (tmp_path / f"{sub.request_uuid}.json").exists()
    assert sub.poll() is None  # no watcher running → still pending
    sub.cleanup()
    assert not (tmp_path / f"{sub.request_uuid}.json").exists()


@pytest.mark.django_db(transaction=True)
def test_backend_mode_executes_without_files(tmp_path, monkeypatch, isolated_data_dir):
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: tmp_path,
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        token = transport.backend_loop.set(loop)
        try:
            # The CLI side runs in a worker thread, like invoke() under /mcp.
            outcome = await asyncio.to_thread(_cli_side)
        finally:
            transport.backend_loop.reset(token)
        return outcome

    def _cli_side():
        transport.ensure_server_available()          # no-op in backend mode
        sub = transport.submit({"name": "ws-inproc"}, kind="workspace:create")
        out = transport.wait(sub, timeout_seconds=10)
        sub.cleanup()
        return out

    outcome = asyncio.run(scenario())
    assert outcome.status == "created"
    assert outcome.data["workspace_id"]
    assert list(tmp_path.iterdir()) == []            # zero files touched


def test_backend_mode_session_create_injects_uuid(monkeypatch):
    # session:create must mint request_uuid == session_id, like write_drop_file.
    captured = {}

    async def fake_execute(payload, kind):
        captured.update(payload)
        return {"status": "created", "session_id": payload["session_id"]}

    monkeypatch.setattr(
        "twicc.drop_requests_watcher.execute_drop_payload", fake_execute,
    )

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(_cli_side)
        finally:
            transport.backend_loop.reset(token)

    def _cli_side():
        sub = transport.submit({"prompt": "hi"}, kind="session:create")
        out = transport.wait(sub, timeout_seconds=5)
        return sub, out

    sub, out = asyncio.run(scenario())
    assert captured["session_id"] == sub.request_uuid
    assert out.data["session_id"] == sub.request_uuid
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_drop_transport.py -v`
Expected: FAIL (no module `transport`)

- [ ] **Step 3: Implement `transport.py`**

```python
# src/twicc/cli/_drop_request/transport.py
"""Dual-mode drop-request transport.

Every mutating CLI command funnels its (payload, kind) through this seam:

- **Local mode** (a real ``twicc`` process talking to a separate backend):
  identical to the historical behavior — heartbeat preflight, atomic drop
  file, status-file polling, caller-side cleanup.
- **Backend mode** (the command runs *inside* the backend process — MCP tool
  calls, and optionally ``/rpc/``): no filesystem at all. The payload is
  executed by scheduling :func:`twicc.drop_requests_watcher.execute_drop_payload`
  on the backend's event loop; polling reads a concurrent Future.

Mode is selected by the ``backend_loop`` ContextVar: the in-backend dispatcher
sets it to its running loop before running the command in a worker thread
(ContextVars propagate through ``asyncio.to_thread``). CLI processes never set
it, so the default path is byte-for-byte the previous one.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import Future
from contextvars import ContextVar
from pathlib import Path

import orjson

from twicc.cli._drop_request.discovery import check_heartbeat  # re-exported semantics
from twicc.cli._drop_request.drop_file import write_drop_file
from twicc.cli._drop_request.polling import POLL_INTERVAL_SECONDS, PollOutcome

backend_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar(
    "twicc_backend_loop", default=None,
)


def _in_backend() -> bool:
    return backend_loop.get() is not None


def ensure_server_available() -> None:
    """Heartbeat preflight; a no-op in backend mode (we ARE the server).

    Raises :class:`twicc.cli._drop_request.discovery.ServerDownError` like
    ``check_heartbeat`` did — call sites keep their except clauses unchanged.
    """
    if not _in_backend():
        check_heartbeat()


class Submission:
    """One in-flight request; uniform poll/cleanup over both modes."""

    def __init__(self, request_uuid: str, *, status_path: Path | None = None,
                 drop_path: Path | None = None, future: Future | None = None) -> None:
        self.request_uuid = request_uuid
        self._status_path = status_path
        self._drop_path = drop_path
        self._future = future

    def poll(self) -> PollOutcome | None:
        """Non-blocking check. None while pending; PollOutcome when final."""
        if self._future is not None:
            if not self._future.done():
                return None
            try:
                data = dict(self._future.result())
            except Exception as e:  # scheduling failure (loop gone, ...)
                data = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
            data.setdefault("request_uuid", self.request_uuid)
            return PollOutcome(status=data.get("status"), data=data, received_seen=True)
        # Local mode: single-shot read of the status file.
        try:
            data = orjson.loads(self._status_path.read_bytes())
        except (FileNotFoundError, ValueError, OSError):
            return None
        status = data.get("status")
        if status == "received":
            return None
        if status in ("created", "sent", "updated", "stopped", "deleted", "rejected", "failed"):
            return PollOutcome(status=status, data=data, received_seen=True)
        return None

    def cleanup(self) -> None:
        """Delete the request/status files (local mode); no-op in backend mode."""
        if self._drop_path is not None:
            self._drop_path.unlink(missing_ok=True)
        if self._status_path is not None:
            self._status_path.unlink(missing_ok=True)


def submit(payload: dict, *, kind: str) -> Submission:
    """Submit one request in the active mode."""
    loop = backend_loop.get()
    if loop is None:
        drop = write_drop_file(payload, kind=kind)
        return Submission(
            drop.request_uuid,
            status_path=drop.path.with_name(f"{drop.request_uuid}.status.json"),
            drop_path=drop.path,
        )
    # Backend mode: replicate write_drop_file's envelope semantics without I/O.
    request_uuid = str(uuid.uuid4())
    full_payload = {**payload, "kind": kind}
    if kind == "session:create":
        full_payload["session_id"] = request_uuid
    from twicc.drop_requests_watcher import execute_drop_payload

    future = asyncio.run_coroutine_threadsafe(
        execute_drop_payload(full_payload, kind), loop,
    )
    return Submission(request_uuid, future=future)


def wait(submission: Submission, timeout_seconds: int) -> PollOutcome:
    """Block until final status or timeout (same contract as poll_status).

    Timeout returns ``data=None`` where the old ``poll_status`` returned the
    last partial read — deliberately: no caller reads ``.data`` on timeout
    (``build_final``'s timeout branch only uses ``received_seen``).
    """
    deadline = time.time() + timeout_seconds
    received_seen = _in_backend()  # backend mode: submission IS receipt
    while time.time() < deadline:
        outcome = submission.poll()
        if outcome is not None:
            return outcome
        received_seen = received_seen or _local_received_seen(submission)
        time.sleep(POLL_INTERVAL_SECONDS)
    return PollOutcome(status=None, data=None, received_seen=received_seen)


def _local_received_seen(submission: Submission) -> bool:
    if submission._status_path is None:
        return True
    try:
        return orjson.loads(submission._status_path.read_bytes()).get("status") == "received"
    except (FileNotFoundError, ValueError, OSError):
        return False
```

Note on `wait()` in backend mode: the busy-wait (`future.done()` every 100 ms in a worker thread) matches the existing polling cadence and keeps one code path; the event loop is never blocked.

- [ ] **Step 4: Refactor the call sites — worked example**

`src/twicc/cli/artifacts_mutation.py` `_run_drop` becomes:

```python
def _run_drop(payload: dict, *, kind: str, success_status: str, timeout: int) -> None:
    """Submit a request via the transport seam, wait, emit final JSON, exit."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final
    from twicc.cli._drop_request import transport
    from twicc.cli._output import emit_error

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    sub = transport.submit(payload, kind=kind)
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == success_status:
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
```

Recipe for every other single-submission file (mechanical, same shape):
1. Replace the `check_heartbeat()` call with `transport.ensure_server_available()` (imports: drop `check_heartbeat`, keep `ServerDownError`).
2. Replace `drop = write_drop_file(payload, kind=...)` + `status_path = drop.path.with_name(...)` + `outcome = poll_status(status_path, ...)` with `sub = transport.submit(payload, kind=...)` + `outcome = transport.wait(sub, timeout_seconds=...)`.
3. Replace the two `unlink(missing_ok=True)` lines with `sub.cleanup()`.
4. Replace `drop.request_uuid` with `sub.request_uuid` everywhere downstream (notably `create_session/command.py`, which uses it as the new session id in its final output).

Do them one file at a time; `rg -n "write_drop_file|poll_status|check_heartbeat" src/twicc/cli/` must end up matching only `_drop_request/` internals (`drop_file.py`, `polling.py`, `discovery.py`, `transport.py`) and `_batch_runner.py` (Task 5).

- [ ] **Step 5: Run the full test suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/ -x -q`
Expected: PASS (the local-mode path is behavior-identical; failures = a call site missed in the recipe)

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/cli/_drop_request/transport.py src/twicc/cli/ tests/test_drop_transport.py && git commit -m "feat(cli): dual-mode drop-request transport (file-based or in-process)"
```

---

## 7. Task 5 — Batch runner on the transport

**Files:**
- Modify: `src/twicc/cli/_batch_runner.py`, `src/twicc/cli/send_messages.py`, `src/twicc/cli/update_sessions/command.py`, `src/twicc/cli/processes_stop.py` (whichever of these write files directly — follow the grep)

- [ ] **Step 1: Refactor**

In `_batch_runner.py` (and `processes_stop.py` if it has its own multi-drop loop):
1. `check_heartbeat()` → `transport.ensure_server_available()`.
2. The per-id `write_drop_file(...)` → `subs[session_id] = transport.submit(payload, kind=kind)`.
3. The aggregated polling loop (single wall-clock deadline over all status files) → same loop shape over `subs`, calling `sub.poll()` per pending id each tick instead of reading its status file; on final outcome, `sub.cleanup()` and record `build_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)`.
4. On deadline expiry, pending ids get the same timeout `PollOutcome(status=None, ...)` as today (compute `received_seen` per sub: backend mode → `True`).

Behavior contract to preserve exactly (assert against the module docstring): per-id `validation_error`/`noop` short-circuits, exit codes 0/1/2/6, `summary` counts.

- [ ] **Step 2: Run tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/ -q -k "batch or send_messages or update_sessions or processes"`
Expected: PASS

- [ ] **Step 3: Full-suite check + grep audit**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && rg -n "write_drop_file|poll_status\(|check_heartbeat\(" src/twicc/cli/ --glob '!_drop_request/*'`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/cli/ && git commit -m "refactor(cli): route batch drop-requests through the transport seam"
```

---

## 8. Task 6 — MCP tool registry

**Files:**
- Modify: `src/twicc/rpc/generator.py` (parameterize exclusions)
- Create: `src/twicc/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_tools.py
"""Tool derivation from the Click tree: selection, naming, metadata."""
from twicc.mcp.tools import build_mcp_registry, iter_mcp_tools
from twicc.rpc.generator import build_registry


def test_selection_matches_the_skill_surface():
    reg = build_mcp_registry()
    paths = set(reg)
    assert "whoami" in paths                       # re-admitted local-only
    assert not any(p.split("/")[0] == "settings" for p in paths)
    for banned in ("password", "token", "run", "claude", "codex"):
        assert not any(p.split("/")[0] == banned for p in paths)
    # Everything else from the RPC registry is present.
    rpc_paths = {p for p in build_registry() if p.split("/")[0] != "settings"}
    assert rpc_paths <= paths


def test_tool_names_are_mcp_safe_and_bijective():
    tools = iter_mcp_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    for n in names:
        assert n.replace("_", "").isalnum() and n == n.lower()
    assert "create_session" in names
    assert "update_session_settings" in names
    assert "session_content" in names


def test_schemas_and_descriptions():
    by_name = {t.name: t for t in iter_mcp_tools()}
    reg = build_mcp_registry()
    assert by_name["create_session"].inputSchema == reg["create-session"].json_schema
    assert by_name["create_session"].description  # full help, non-empty
    assert len(by_name["create_session"].description) > len(reg["create-session"].summary)


def test_annotations_and_always_load():
    by_name = {t.name: t for t in iter_mcp_tools()}
    assert by_name["sessions"].annotations.readOnlyHint is True
    assert by_name["create_session"].annotations.readOnlyHint is False
    assert (by_name["whoami"].meta or {}).get("anthropic/alwaysLoad") is True
    assert (by_name["update_workspace"].meta or {}).get("anthropic/alwaysLoad") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_tools.py -v`
Expected: FAIL (no module)

- [ ] **Step 3: Parameterize `build_registry`**

In `src/twicc/rpc/generator.py`, change the excluded-set plumbing (keep the public no-arg call 100% compatible):

```python
def build_registry(*, excluded_roots: frozenset[str] = frozenset(LOCAL_ONLY_COMMANDS)) -> dict[str, CommandSpec]:
    """Build (cached per excluded_roots) the path → CommandSpec registry."""
    global _registries
    if excluded_roots not in _registries:
        reg: dict[str, CommandSpec] = {}
        _walk(get_command(), None, [], reg, is_root=True, excluded_roots=excluded_roots)
        _registries[excluded_roots] = reg
    return _registries[excluded_roots]
```

(`_registry: dict | None` becomes `_registries: dict[frozenset, dict] = {}`; `_walk` takes `excluded_roots` and uses it instead of the module constant. `/rpc/` callers are untouched.)

- [ ] **Step 4: Implement `tools.py`**

```python
# src/twicc/mcp/tools.py
"""Derive the MCP tool list from the CLI's Click tree.

Selection rule: the RPC registry (everything the CLI exposes minus
local-only) minus the ``settings`` group (not skill-covered; agents must not
mutate global settings) plus ``whoami`` (local-only for /rpc/ because PID
ancestry is meaningless over HTTP — but the MCP dispatcher injects the caller
identity, making it THE discovery primitive).

Naming: registry path with ``/`` and ``-`` mapped to ``_``
(``update-session/settings`` → ``update_session_settings``). Claude prefixes
these as ``mcp__twicc__<name>``.
"""

from __future__ import annotations

from functools import cache

import click
from mcp import types as mcp_types

from twicc.cli._local_only import LOCAL_ONLY_COMMANDS
from twicc.rpc.generator import CommandSpec, build_registry
from twicc.rpc.invoker import get_command
from twicc.rpc.permissions import COOKIE_READONLY_COMMANDS

# The MCP surface: local-only minus whoami, plus the settings group.
MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
    (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings"}
)

# Read-only annotation source (metadata only — NOT used for availability).
# Every tool is exposed in every mode (D9); `readOnlyHint` is honest metadata
# for clients (and on Codex it feeds `requires_mcp_tool_approval`, though our
# `default_tools_approval_mode="approve"` makes that moot). COOKIE_READONLY_COMMANDS
# is the vetted fail-closed list; the session read subviews and whoami are pure
# reads that were simply never needed on the cookie path.
MCP_READ_ONLY_PATHS: frozenset[str] = COOKIE_READONLY_COMMANDS | frozenset(
    {"session/plan", "session/workflows", "session/workflow", "whoami"}
)

# Hot tools Claude should never defer (Tool Search loads names only for the
# rest). Keep this list tiny — every entry is permanent context.
ALWAYS_LOAD_PATHS: frozenset[str] = frozenset(
    {"whoami", "create-session", "send-message", "sessions", "session"}
)


def tool_name_for(path: str) -> str:
    return path.replace("/", "_").replace("-", "_")


@cache
def build_mcp_registry() -> dict[str, CommandSpec]:
    """path → CommandSpec for the MCP-exposed surface."""
    return build_registry(excluded_roots=MCP_EXCLUDED_ROOTS)


@cache
def tools_by_name() -> dict[str, CommandSpec]:
    return {tool_name_for(p): spec for p, spec in build_mcp_registry().items()}


def _click_leaf(path: str) -> click.Command:
    cmd: click.Command = get_command()
    for token in path.split("/"):
        if isinstance(cmd, click.Group) and token in cmd.commands:
            cmd = cmd.commands[token]
    return cmd


def _description_for(path: str, spec: CommandSpec) -> str:
    help_text = (_click_leaf(path).help or "").strip()
    return help_text or spec.summary


@cache
def iter_mcp_tools() -> list[mcp_types.Tool]:
    out: list[mcp_types.Tool] = []
    for path, spec in sorted(build_mcp_registry().items()):
        meta = {"anthropic/alwaysLoad": True} if path in ALWAYS_LOAD_PATHS else None
        out.append(
            mcp_types.Tool(
                name=tool_name_for(path),
                description=_description_for(path, spec),
                inputSchema=spec.json_schema,
                annotations=mcp_types.ToolAnnotations(
                    readOnlyHint=path in MCP_READ_ONLY_PATHS,
                ),
                _meta=meta,
            )
        )
    return out
```

Note (verified on mcp 1.27.0): the constructor kwarg MUST be `_meta=` — the wire alias. Passing `meta=` is **silently dropped** (`.meta` stays `None`). The code above is correct as written; the test asserts on the `.meta` attribute, which is where `_meta=` lands.

- [ ] **Step 5: Run tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_tools.py tests/ -q -k "mcp or rpc"`
Expected: PASS (including the existing `/rpc/` suites against the parameterized generator)

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/rpc/generator.py src/twicc/mcp/tools.py tests/test_mcp_tools.py && git commit -m "feat(mcp): auto-generate the MCP tool registry from the Click tree"
```

---

## 9. Task 7 — MCP server: list_tools / call_tool dispatch

**Files:**
- Create: `src/twicc/mcp/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py
"""call_tool dispatch: identity binding, in-process execution, envelope."""
import asyncio

import pytest

from twicc.mcp import server as mcp_server


@pytest.mark.django_db(transaction=True)
def test_call_tool_runs_command_and_returns_envelope():
    result = asyncio.run(mcp_server.dispatch_tool("workspaces", {}, session_id=None))
    assert set(result) == {"exit_code", "result", "error"}
    assert result["exit_code"] == 0


@pytest.mark.django_db(transaction=True)
def test_call_tool_whoami_uses_bound_identity():
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="-tmp-p2", directory="/tmp/p2", name="p2")
    session = Session.objects.create(
        id="22222222-2222-2222-2222-222222222222", project=project,
    )
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id=session.id))
    assert result["exit_code"] == 0
    assert result["result"]["session"]["id"] == session.id


@pytest.mark.django_db(transaction=True)
def test_call_tool_mutation_bypasses_drop_files(tmp_path, monkeypatch, isolated_data_dir):
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: tmp_path,
    )
    result = asyncio.run(mcp_server.dispatch_tool(
        "create_workspace", {"name": "ws-via-mcp"}, session_id=None,
    ))
    assert result["exit_code"] == 0
    assert result["result"]["status"] == "created"
    assert list(tmp_path.iterdir()) == []


def test_unknown_tool_raises():
    with pytest.raises(mcp_server.UnknownToolError):
        asyncio.run(mcp_server.dispatch_tool("nope", {}, session_id=None))
```

Check `twicc whoami`'s exact output shape first (`src/twicc/cli/whoami.py`) and adjust the assertion path (`result["result"]["session"]["id"]` vs a flatter shape). Same fixture caveats as §0 Testing conventions (`isolated_data_dir` is the placeholder for the repo's real data-dir isolation pattern).

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `server.py`**

```python
# src/twicc/mcp/server.py
"""The lowlevel MCP server: tool listing + in-process command dispatch.

One ``Server`` instance serves every agent session; per-call identity comes
from the ``Authorization`` header of the underlying HTTP request (available
via ``request_context.request`` on the streamable-HTTP transport) and is
bound into two ContextVars before the command runs in a worker thread:

- ``whoami.forced_session_id`` — makes ``self``/``parent``/``whoami``/
  ``spawned_by`` auto-fill resolve to the calling session;
- ``transport.backend_loop`` — routes mutations straight to the drop-request
  service handlers on this event loop instead of the drop-file dance.

The tool result is the same envelope as ``POST /rpc/<command>``:
``{"exit_code": int, "result": ..., "error": ...}`` — returned as MCP
structured content. Non-zero exit codes are data, not MCP errors (parity with
the CLI/skills contract agents already know).
"""

from __future__ import annotations

import asyncio
import logging

from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from twicc.cli._drop_request import transport
from twicc.cli._drop_request.whoami import forced_session_id
from twicc.mcp.identity import resolve_session_token
from twicc.mcp.tools import iter_mcp_tools, tools_by_name
from twicc.rpc.generator import render_argv
from twicc.rpc.views import _run_invoke

logger = logging.getLogger(__name__)


class UnknownToolError(Exception):
    pass


INSTRUCTIONS = """\
These tools are the TwiCC CLI (`twicc <command>`), one tool per command; the
`twicc-*` skills document the same surface in depth. Results are the CLI's
JSON wrapped in {"exit_code", "result", "error"} — exit_code 0 is success,
non-zero maps to the exit codes the skills document (3 rejected, 4 failed,
5 timeout, ...).

Conventions:
- Session-targeting arguments accept `self` (your own session) and `parent`
  (the session that spawned you); your identity is carried by this connection,
  so `whoami` works and `create_session` records you as the spawner.
- Always pass absolute paths (directories, attachments): tools execute inside
  the TwiCC backend, whose working directory is not yours.
- Keep `*_wait` timeouts ≤ 300 seconds; poll again rather than exceeding them.
- Catalogues (models, presets, providers) drift: fetch them live with `info`.
"""


async def dispatch_tool(name: str, arguments: dict, *, session_id: str | None) -> dict:
    """Execute one tool call in-process; returns the RPC-style envelope."""
    spec = tools_by_name().get(name)
    if spec is None:
        raise UnknownToolError(name)
    argv = render_argv(spec, arguments)
    loop = asyncio.get_running_loop()
    tok_sid = forced_session_id.set(session_id)
    tok_loop = transport.backend_loop.set(loop)
    try:
        result = await asyncio.to_thread(_run_invoke, argv)
    finally:
        transport.backend_loop.reset(tok_loop)
        forced_session_id.reset(tok_sid)
    return {"exit_code": result.exit_code, "result": result.result, "error": result.error}


def _session_id_from_request() -> str | None:
    """Caller identity from the HTTP Authorization header, if session-bound."""
    ctx = _server.request_context
    request = getattr(ctx, "request", None)
    if request is None:
        return None
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    return resolve_session_token(token)


_server: Server = Server("twicc", instructions=INSTRUCTIONS)


@_server.list_tools()
async def _list_tools() -> list[mcp_types.Tool]:
    return iter_mcp_tools()


@_server.call_tool()
async def _call_tool(name: str, arguments: dict) -> dict:
    session_id = _session_id_from_request()
    try:
        return await dispatch_tool(name, arguments, session_id=session_id)
    except UnknownToolError:
        raise ValueError(f"Unknown tool: {name}")
    except Exception:
        logger.exception("MCP tool %r failed (arguments=%r)", name, arguments)
        raise


_session_manager: StreamableHTTPSessionManager | None = None


def get_session_manager() -> StreamableHTTPSessionManager:
    """Process-wide singleton; created lazily, run by twicc.mcp.endpoint."""
    global _session_manager
    if _session_manager is None:
        _session_manager = StreamableHTTPSessionManager(
            app=_server,
            json_response=True,
            stateless=True,
            # The Bearer token is the real gate (endpoint.py); Host/Origin
            # validation would only break worktree ports and tunnels.
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            ),
        )
    return _session_manager
```

- [ ] **Step 4: Run tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/mcp/server.py tests/test_mcp_server.py && git commit -m "feat(mcp): lowlevel server with identity-bound in-process dispatch"
```

---

## 10. Task 8 — ASGI mount, auth gate, lifespan

**Files:**
- Create: `src/twicc/mcp/endpoint.py`
- Modify: `src/twicc/asgi.py` (http router), `src/twicc/cli/run.py` (lifespan task), `.env.example`
- Test: `tests/test_mcp_endpoint.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_endpoint.py
"""End-to-end JSON-RPC over the raw-ASGI /mcp endpoint (sync tests, asyncio.run)."""
import asyncio
import contextlib

import httpx
import pytest

from twicc.mcp import identity
from twicc.mcp.endpoint import handle_mcp, mcp_lifespan


HEADERS_BASE = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _rpc(method: str, params: dict | None = None, id_: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id_ is not None:
        msg["id"] = id_
    return msg


INIT = _rpc("initialize", {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
})


@contextlib.asynccontextmanager
async def _client():
    async with mcp_lifespan():
        transport = httpx.ASGITransport(app=handle_mcp, client=("127.0.0.1", 9999))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def test_unauthenticated_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post("/mcp", json=INIT, headers=HEADERS_BASE)

    assert asyncio.run(scenario()).status_code == 401


def test_bad_token_is_401():
    async def scenario():
        async with _client() as client:
            return await client.post(
                "/mcp", json=INIT,
                headers={**HEADERS_BASE, "authorization": "Bearer twicc_mcp_x.deadbeef"},
            )

    assert asyncio.run(scenario()).status_code == 401


@pytest.mark.django_db(transaction=True)
def test_initialize_list_call_roundtrip():
    headers = {
        **HEADERS_BASE,
        "authorization": f"Bearer {identity.mint_session_token('some-session')}",
    }

    async def scenario():
        async with _client() as client:
            r = await client.post("/mcp", json=INIT, headers=headers)
            assert r.status_code == 200
            assert r.json()["result"]["serverInfo"]["name"] == "twicc"

            r = await client.post(
                "/mcp", json=_rpc("notifications/initialized", id_=None), headers=headers,
            )
            assert r.status_code in (200, 202)

            r = await client.post("/mcp", json=_rpc("tools/list", {}, 2), headers=headers)
            assert r.status_code == 200
            names = {t["name"] for t in r.json()["result"]["tools"]}
            assert "whoami" in names and "create_session" in names

            r = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "workspaces", "arguments": {}}, 3),
                headers=headers,
            )
            assert r.status_code == 200
            payload = r.json()["result"]
            assert payload["structuredContent"]["exit_code"] == 0

    asyncio.run(scenario())
```

Notes for the implementer: in **stateless** mode no `mcp-session-id` header round-trip is needed; each POST is independent (the transport still requires `initialize` semantics per request internally — it synthesizes them, that is what stateless means). If `tools/list` without a prior `initialize` on the same connection errors, drop the separate posts and just assert the initialize response + a `tools/call` (stateless mode accepts direct calls). Adapt to observed behavior — the assertions that matter: 401 paths, 200 initialize, tool call envelope.

- [ ] **Step 2: Implement `endpoint.py`**

```python
# src/twicc/mcp/endpoint.py
"""Raw-ASGI entry for /mcp: auth gate + streamable-HTTP session manager.

Mounted by twicc.asgi *in front of* Django (no middleware, no urls.py, no
SPA-catch-all involvement). Authentication is mandatory and header-based:

- ``Authorization: Bearer twicc_mcp_<sid>.<sig>`` — a per-session token
  minted at agent wiring time (twicc.mcp.identity); grants full access and
  binds caller identity (whoami / self / parent).
- ``Authorization: Bearer twicc_pat_...`` — a user-created API token
  (``twicc token create``); full access, no session identity.

Anything else → 401. Additionally, remote connections go through the same
``scope_remote_access_blocked`` gate as the WebSocket consumers (an
unprotected instance refuses non-loopback callers outright).
"""

from __future__ import annotations

import contextlib
import logging

import orjson

from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.tokens import verify_token
from twicc.mcp import mcp_enabled
from twicc.mcp.identity import TOKEN_PREFIX, resolve_session_token
from twicc.mcp.server import get_session_manager

logger = logging.getLogger(__name__)

_started = False


def _bearer(scope) -> str:
    for key, value in scope.get("headers") or ():
        if key == b"authorization":
            return value.decode("latin-1").removeprefix("Bearer ").strip()
    return ""


def _authorized(scope) -> bool:
    token = _bearer(scope)
    if token.startswith(TOKEN_PREFIX):
        return resolve_session_token(token) is not None
    if token:
        return verify_token(token) is not None
    return False


async def _plain_response(send, status: int, body: dict, *, headers=()) -> None:
    payload = orjson.dumps(body)
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), *headers],
    })
    await send({"type": "http.response.body", "body": payload})


async def handle_mcp(scope, receive, send) -> None:
    """ASGI handler for every /mcp request."""
    if scope["type"] != "http":  # pragma: no cover — router only sends http
        return
    if not mcp_enabled() or not _started:
        await _plain_response(send, 503, {"error": "MCP server not available."})
        return
    if scope_remote_access_blocked(scope):
        await _plain_response(send, 403, {"error": "Remote access is disabled."})
        return
    if not _authorized(scope):
        await _plain_response(
            send, 401, {"error": "A TwiCC MCP session token or API token is required."},
            headers=[(b"www-authenticate", b"Bearer")],
        )
        return
    # The session manager expects to own the path; it treats the mount point
    # as the endpoint regardless of the exact path value.
    await get_session_manager().handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def mcp_lifespan():
    """Run the session manager's task group (call once, from run.py or tests)."""
    global _started
    manager = get_session_manager()
    async with manager.run():
        _started = True
        logger.info("MCP server ready at /mcp")
        try:
            yield
        finally:
            _started = False


async def start_mcp_task(shutdown_event) -> None:
    """run.py background task: keep the session manager alive until shutdown."""
    if not mcp_enabled():
        logger.info("MCP server disabled (TWICC_NO_MCP)")
        return
    async with mcp_lifespan():
        await shutdown_event.wait()
```

- [ ] **Step 3: Mount in `asgi.py`**

At the ProtocolTypeRouter composition (lines ~1992-2004), wrap the http branch:

```python
django_asgi_app = get_asgi_application()


async def http_router(scope, receive, send):
    """Route /mcp to the raw-ASGI MCP endpoint; everything else to Django."""
    path = scope.get("path", "")
    if path == "/mcp" or path.startswith("/mcp/"):
        from twicc.mcp.endpoint import handle_mcp

        await handle_mcp(scope, receive, send)
        return
    await django_asgi_app(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": http_router,
        "websocket": ...,  # unchanged
    }
)
```

(BlackNoise stays outermost and only intercepts `/static`.)

- [ ] **Step 4: Start the lifespan task in `run.py`**

Next to the drop-watcher task (~line 298):

```python
from twicc.mcp.endpoint import start_mcp_task
mcp_task = asyncio.create_task(start_mcp_task(shutdown_event))
```

Register `mcp_task` in the same gather/cancel shutdown path as its neighbors (mirror exactly how `drop_watcher_task` is awaited/cancelled at shutdown).

Add to `.env.example`, next to the other opt-outs:

```
# Set to 1 to disable the built-in MCP server (/mcp) and its per-session wiring.
#TWICC_NO_MCP=
```

- [ ] **Step 5: Run tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_mcp_endpoint.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/mcp/endpoint.py src/twicc/asgi.py src/twicc/cli/run.py .env.example tests/test_mcp_endpoint.py && git commit -m "feat(mcp): mount /mcp raw-ASGI endpoint with token auth and lifespan"
```

---

## 11. Task 9 — Claude Code wiring (SDK + hybrid)

**Files:**
- Create: `src/twicc/mcp/wiring.py`
- Modify: `src/twicc/providers/claude_code/agent/agent.py`, `src/twicc/providers/claude_code/agent/hybrid/launch.py`

**Token-secrecy constraint (why a config FILE on both paths):** the SDK serializes a `mcp_servers` **dict** inline onto the spawned CLI's argv (`subprocess_cli.py:307-332` does `cmd.extend(["--mcp-config", json.dumps({...})])`) — the Bearer token would be visible in `ps` for the whole process lifetime. `ClaudeAgentOptions.mcp_servers` also accepts `str | Path` (`types.py:1670`), which the SDK passes as `--mcp-config <path>`. So BOTH the SDK path and the hybrid path write a per-session 0600 config file and pass its path.

- [ ] **Step 1: Shared config-file writer**

```python
# src/twicc/mcp/wiring.py
"""Per-session Claude MCP config file (.mcp.json shape).

A FILE, never inline JSON: both the SDK (dict form) and the hybrid CLI would
put the config — token included — on the ``claude`` argv, visible in ``ps``.
Rewritten on every (re)launch: the URL follows the current port and the token
is deterministic, so a stale file self-heals at next start.
"""

from __future__ import annotations

import os
from pathlib import Path

import orjson

from twicc.mcp import mcp_base_url
from twicc.mcp.identity import mint_session_token
from twicc.paths import get_data_dir


def write_claude_mcp_config(session_id: str) -> Path:
    """Write ``<data_dir>/mcp-configs/<session_id>.json`` (0600); return the path."""
    directory = get_data_dir() / "mcp-configs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    config = {
        "mcpServers": {
            "twicc": {
                "type": "http",
                "url": mcp_base_url(),
                "headers": {"Authorization": f"Bearer {mint_session_token(session_id)}"},
            }
        }
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(config))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path
```

- [ ] **Step 2: SDK sessions**

In the `ClaudeAgentOptions(...)` call in `agent.py` (~line 956), add — gated on the kill switch:

```python
    from twicc.mcp import mcp_enabled
    from twicc.mcp.wiring import write_claude_mcp_config
    ...
    mcp_servers=str(write_claude_mcp_config(self.session_id)) if mcp_enabled() else {},
    env={"MCP_TOOL_TIMEOUT": "600000"} if mcp_enabled() else {},
```

Three verifications while editing (do not skip):
- Confirm the str/Path form reaches the CLI as `--mcp-config <path>` (`subprocess_cli.py`, `types.py:1670`) and that the `.mcp.json` `{"mcpServers": {...}}` shape is what `--mcp-config` expects.
- Confirm `ClaudeAgentOptions.env` semantics are additive (verified: `subprocess_cli.py:430-434` merges over the inherited env).
- Confirm nothing in the existing `disallowed_tools` list would match `mcp__twicc__*` (currently only `["AskUserQuestion"]` — it must stay that way).

Identity note: for both new sessions (`extra_args["session-id"] = self.session_id`) and resumes, `self.session_id` IS the canonical id → deterministic token, no alias needed.

- [ ] **Step 3: Auto-approve `mcp__twicc__*` in every mode (D9)**

The `can_use_tool` callback is `ClaudeCodeAgent._handle_pending_request` (`agent.py:623`). It already has an early auto-approval branch (the system-work-dir case, ~lines 652-666). Add an earlier, unconditional short-circuit for our own tools — **before** the `untrusted` resolution and before any pending request is created, so no prompt ever fires regardless of permission mode or trust:

```python
        # TwiCC MCP tools are a control plane (drive sessions, not the project's
        # code); they are auto-approved in every mode/trust, exactly like the
        # skills+CLI the agent already has. See plan D9.
        if tool_name.startswith("mcp__twicc__"):
            return PermissionResultAllow()
```

Place it at the very top of `_handle_pending_request`, right after `request_id = str(uuid.uuid4())` (or before it — it returns immediately). Note: in `bypassPermissions` mode `can_use_tool` is never invoked at all (already allowed); this branch covers the restrictive modes (`default`/`plan`/`acceptEdits`) where the callback *does* fire. Net effect: `mcp__twicc__*` never prompts, in any mode. Do NOT also add `mcp__twicc__*` to `allowed_tools` — the callback branch is sufficient and keeps the logic in one place.

- [ ] **Step 4: Hybrid (tmux CLI) sessions**

In `hybrid/launch.py`, where argv gains `--plugin-dir` (~line 141):

```python
from twicc.mcp import mcp_enabled
from twicc.mcp.wiring import write_claude_mcp_config

if mcp_enabled():
    argv += ["--mcp-config", str(write_claude_mcp_config(session_id))]
```

Verify the bundled CLI (2.1.191) accepts `--mcp-config <path>` (re-check on the installed bundle: `.venv/.../claude_agent_sdk/_bundled/claude --help | grep mcp-config`). Do NOT pass `--strict-mcp-config` — user-configured servers must survive.

- [ ] **Step 5: Manual smoke test**

Ask the user to restart the worktree dev servers when convenient (do NOT restart yourself — reserved operation), then: create a Claude session **in a restrictive mode (e.g. `default` or plan mode, NOT bypass)** and prompt: *"Call the mcp__twicc__whoami tool and show the result."* Expected: the session's own id **with no approval prompt** (validates Step 3). Then *"Use mcp__twicc__create_session to spawn a child session"* — must run prompt-free too. Also `ps aux | grep 'mcp-config'` → the argv must show a **path**, never inline JSON.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/mcp/wiring.py src/twicc/providers/claude_code/agent/agent.py src/twicc/providers/claude_code/agent/hybrid/launch.py && git commit -m "feat(mcp): wire the TwiCC MCP server into Claude Code sessions (SDK + hybrid)"
```

---

## 12. Task 10 — Codex wiring

**Files:**
- Modify: `src/twicc/providers/codex/agent/manager.py`
- Modify: `src/twicc/mcp/__init__.py` (the two module constants)

**Design (D9 + D10):** all tools available in every mode, auto-approved; Codex context controlled by a constant. No `permission_mode` branching, no `enabled_tools` filtering.

- [ ] **Step 1: The two module constants**

In `src/twicc/mcp/__init__.py`, add:

```python
# Codex: force Tool Search deferral of MCP schemas (Codex loads all schemas
# eagerly below its 100-tool threshold). True = defer (clean context, full
# tool set) via the experimental `tool_search_always_defer_mcp_tools` feature;
# flip to False for eager loading if that under-development flag regresses on a
# Codex bump. See plan D10 / research doc §3.
TWICC_MCP_CODEX_DEFER = True
```

- [ ] **Step 2: Thread config injection**

In `_create_agent` (manager.py, where `thread_config` is built ~line 594):

```python
        from twicc.mcp import mcp_enabled

        if mcp_enabled():
            thread_config["mcp_servers"] = {"twicc": _twicc_mcp_server_config(session_id)}
            _apply_codex_mcp_context_mode(thread_config)
```

with, at module level:

```python
def _twicc_mcp_server_config(session_id: str) -> dict:
    """Per-thread TwiCC MCP server entry (TOML-shaped, json_to_toml-merged).

    Every tool is exposed in every mode; ``default_tools_approval_mode="approve"``
    makes them auto-approve without a per-call prompt, independent of the
    session's approval_policy (Codex's ``AppToolApproval::Approve`` short-circuits
    the whole approval check — verified in vendored ``codex-mcp/src/mcp/mod.rs``).
    TwiCC MCP is a control plane, orthogonal to the project's permission mode
    (plan D9).
    """
    from twicc.mcp import mcp_base_url
    from twicc.mcp.identity import mint_session_token

    return {
        "url": mcp_base_url(),
        "http_headers": {"Authorization": f"Bearer {mint_session_token(session_id)}"},
        "default_tools_approval_mode": "approve",
        "tool_timeout_sec": 600,
        "startup_timeout_sec": 30,
    }


def _apply_codex_mcp_context_mode(thread_config: dict) -> None:
    """Force MCP tool-schema deferral on Codex unless the constant disables it."""
    from twicc.mcp import TWICC_MCP_CODEX_DEFER

    if not TWICC_MCP_CODEX_DEFER:
        return  # eager: Codex loads all schemas into context
    features = thread_config.setdefault("features", {})
    features["tool_search_always_defer_mcp_tools"] = True
    # Enabling an under-development feature otherwise prints an unstable-features
    # warning on every thread start.
    thread_config["suppress_unstable_features_warning"] = True
```

Confirm the resume path (`thread_resume_with_policy`) passes the same `thread_config` — it does (same dict, lines ~597-607); resume uses the canonical id so the token is deterministic.

Verify the key names against the vendored Codex version (re-run at every Codex bump — Task 12 folds this into the vendoring doc):
- `cd /home/twidi/dev/codex && rg -n "default_tools_approval_mode|tool_timeout_sec|startup_timeout_sec|http_headers" codex-rs/config/src/mcp_types.rs` — all exist at rust-v0.136.0.
- `rg -n "tool_search_always_defer_mcp_tools" codex-rs/features/src/lib.rs` and confirm `features` is a top-level `ConfigToml` table (`codex-rs/config/src/config_toml.rs`, `pub features: Option<FeaturesToml>`) — so the per-thread `config` patch reaches it. Note it is `Stage::UnderDevelopment` (default off): if a bump promotes/removes it, flip `TWICC_MCP_CODEX_DEFER=False` (eager) as the safe fallback.

- [ ] **Step 3: Draft-id alias for brand-new sessions**

Right after `thread_start_with_policy` returns and `thread.id` is known (~line 636-645, next to the existing `inject_context(thread.id, session_id=thread.id)`):

```python
            from twicc.mcp.identity import register_draft_alias

            register_draft_alias(session_id, thread.id)
```

(`session_id` here is the draft id `_create_agent` received. Harmless no-op when equal.)

- [ ] **Step 4: Manual smoke test**

After a user-triggered restart: create a Codex session **in a non-yolo mode (e.g. `auto`)** and prompt *"Call the twicc whoami MCP tool."* Expected: the **canonical** session id (proves the alias), no approval prompt. Then a mutation: *"Use the twicc update_session_title tool to set this session's title to 'mcp-test' (session self)."* — verify the title changes live in the UI, **with no approval prompt** (validates `default_tools_approval_mode="approve"`), and no file appears in `<data_dir>/drop-requests/`. Also confirm a `read_only`/`strict`-mode Codex session can call a write tool too (the availability decision D9): ask it to run `update_session_title` on itself.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/mcp/__init__.py src/twicc/providers/codex/agent/manager.py && git commit -m "feat(mcp): wire the TwiCC MCP server into Codex threads (auto-approve, deferred schemas)"
```

---

## 13. Task 11 — Optional (recommended): flip `/rpc/` onto the in-process transport

**Files:**
- Modify: `src/twicc/rpc/views.py`

- [ ] **Step 1: Implement**

In `dispatch()`, replace the invoke block:

```python
    from twicc.cli._drop_request import transport

    loop = asyncio.get_running_loop()
    token = transport.backend_loop.set(loop)
    try:
        result = await asyncio.to_thread(_run_invoke, argv)
    finally:
        transport.backend_loop.reset(token)
```

(Identity is deliberately NOT bound here: `/rpc/` callers are not sessions; `self`/`parent` keep failing with the clean "no TwiCC session found" error, as today.)

- [ ] **Step 2: Run the RPC suites**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/ -q -k rpc`
Expected: PASS. If any RPC test asserts on drop files existing, that test documents the old transport — update it to assert the outcome instead.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add src/twicc/rpc/views.py tests/ && git commit -m "perf(rpc): execute mutations in-process via the drop-request transport seam"
```

---

## 14. Task 12 — Documentation

**Files:**
- Modify: `SKILLS-AND-CLI.md`, `CLAUDE.md`, `AGENTS.md`, `RPC-API.md`, `docs/codex-vendoring.md`

- [ ] **Step 1: `SKILLS-AND-CLI.md`**

In the intro ("two front doors"), make it three, with a short subsection after the Conventions block:

```markdown
## The MCP server (`/mcp`)

Inside a TwiCC-driven agent session, every command below (minus `settings`,
plus `whoami`) is also available as an MCP tool (`mcp__twicc__<command>`
on Claude Code, names with `_` instead of `/` and `-`: `create_session`,
`session_content`, `update_session_settings`, ...). Same arguments (the JSON
schema mirrors the CLI options), same JSON output wrapped in
`{"exit_code", "result", "error"}`, same exit codes. Available in every
permission mode and auto-approved (no prompt) — TwiCC's control plane, not the
project's code. Prefer the tools when they are available: no shell, no
drop-file latency, and your session identity travels with the call
(`self`/`parent`/`whoami` work without PID tricks). The CLI remains the way to
script TwiCC from outside a session, and the only surface for `settings`,
`password`, `token`, `claude`/`codex`.
```

Keep it that short — this file's audience note per project conventions (concise, mirror neighboring entries' brevity).

- [ ] **Step 2: `CLAUDE.md` + `AGENTS.md`**

In `CLAUDE.md` Architecture section, one bullet after the watchfiles/periodic items:

```markdown
- **MCP server:** the skill-covered CLI surface is auto-exposed as MCP tools at `/mcp` (raw-ASGI, token-auth; `src/twicc/mcp/`), wired per-session into both providers (Claude `mcp_servers` option, Codex `thread_start` config). Tools run in-process: reads via `rpc/invoker`, writes through the dual-mode drop-request transport (`cli/_drop_request/transport.py`) straight into `core/services/*` — no drop files. A control plane orthogonal to project permissions: every tool is available in every mode and auto-approved (Claude auto-allows `mcp__twicc__*` in `can_use_tool`; Codex `default_tools_approval_mode="approve"`). Adding a CLI command automatically adds the RPC route AND the MCP tool. Kill switch: `TWICC_NO_MCP=1`; Codex schema deferral: `TWICC_MCP_CODEX_DEFER` constant.
```

Propagate a condensed equivalent to `AGENTS.md` (project rule: AGENTS.md follows CLAUDE.md).

- [ ] **Step 3: `RPC-API.md`**

One paragraph noting `/mcp` shares the registry, schemas and envelope with `/rpc/`, and that `/rpc/` mutations now execute in-process (if Task 11 landed).

- [ ] **Step 4: `docs/codex-vendoring.md`**

Add to the update-procedure checklist: "re-verify the `mcp_servers` per-thread config keys TwiCC uses (`url`, `http_headers`, `default_tools_approval_mode`, `tool_timeout_sec`, `startup_timeout_sec`) against `codex-rs/config/src/mcp_types.rs`; that the streamable-HTTP client stays un-gated (`codex-rs/codex-mcp/src/connection_manager.rs`); and the `tool_search_always_defer_mcp_tools` feature key (`codex-rs/features/src/lib.rs`) — still `Stage::UnderDevelopment`? removed/promoted? If it changed, flip `TWICC_MCP_CODEX_DEFER=False` (eager fallback)."

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp && git add SKILLS-AND-CLI.md CLAUDE.md AGENTS.md RPC-API.md docs/codex-vendoring.md && git commit -m "docs: document the /mcp server as the third front door"
```

---

## 15. Final verification (manual, with the user)

- [ ] Full test suite: `cd /home/twidi/dev/twicc-poc/.worktrees/mcp && TWICC_DATA_DIR=$PWD uv run --active pytest tests/ -q` → all green.
- [ ] Remind the user to restart the worktree servers via devctl (auto-runs `uv sync`-equivalent editable rebuild for the new `mcp` direct dep) — do not restart yourself.
- [ ] Claude SDK session: `whoami` tool → own id; `sessions` list; `update_session_title` on `self`; `create_session` → child appears in UI with correct `spawned_by`; check `<data_dir>/drop-requests/` stays empty throughout.
- [ ] Claude hybrid session (tmux): same `whoami` check (validates the config-file wiring + token survival across a backend restart: restart backend mid-session, call the tool again).
- [ ] Codex session: `whoami` → canonical id (alias path); a write tool works **with no approval prompt** in a non-yolo mode (`auto`); a `read_only`/`strict`-mode Codex session can ALSO call a write tool (D9: all tools available in every mode).
- [ ] Codex context mode: with `TWICC_MCP_CODEX_DEFER=True` (default), confirm the thread starts cleanly and tools resolve on demand; temporarily flip to `False` and confirm eager loading still works (fallback path).
- [ ] `curl -s -X POST http://127.0.0.1:<port>/mcp -H 'content-type: application/json' -d '{}'` → 401 JSON (auth gate up); with a `twicc token create` PAT → past auth.
- [ ] CLI regression: `twicc create-workspace smoke-test` from a terminal (local mode, real drop files) then `twicc delete-workspace <id>`.
- [ ] Existing surfaces intact: SPA loads, `/rpc/` roundtrip, artifacts serving, terminal WS.

## 16. Known risks / fragility ledger

- **Claude Tool Search behind `ANTHROPIC_BASE_URL` proxies** silently disables deferral (tool_reference blocks stripped) → all ~56 schemas load eagerly on Claude too. Not blocking (Claude context is 200k-1M); flagged in the research doc §3.
- **`_meta["anthropic/alwaysLoad"]` and `MCP_TOOL_TIMEOUT`** are undocumented Claude levers — harmless if ignored by a future CLI, but re-check on SDK bumps (procedure docs updated in Task 12).
- **Codex deferral flag is experimental** — `tool_search_always_defer_mcp_tools` is `Stage::UnderDevelopment` (default off upstream). We enable it by default (`TWICC_MCP_CODEX_DEFER=True`) to keep the Codex context clean with the full tool set. If a Codex bump removes/renames/breaks it, the thread would emit a warning or ignore the key (schemas load eagerly, ~17k tokens — degraded, not broken); flip `TWICC_MCP_CODEX_DEFER=False` for the clean eager path. Re-checked in the vendoring doc (Task 12).
- **Codex MCP approval bridge gap (pre-existing, orthogonal)** — TwiCC's Codex approval bridge (`providers/codex/agent/approvals.py`) does NOT handle `mcpServer/elicitation/request`; any approval-requiring MCP tool on Codex is silently declined. We avoid it entirely by `default_tools_approval_mode="approve"` (TwiCC-MCP never asks). This affects ANY MCP server on Codex, not just ours (e.g. a user's own `~/.codex/config.toml` servers) — a **separate workstream** the user owns. Closing it (implement the elicitation approval → route to TwiCC's UI) is the prerequisite if per-call MCP prompts (Claude-parity) are ever wanted; then set the twicc server's `default_tools_approval_mode="prompt"`.
- **D9 revises the read-only invariant** — a read-only/strict session can now act on the orchestration via TwiCC-MCP (spawn/drive sessions), a channel the exec sandbox doesn't block. Deliberate carve-out from the earlier "read-only modes = no execution / pull-only leaves" stance. If this ever proves too broad, the lever is a per-mode `enabled_tools` allowlist in `_twicc_mcp_server_config` (Codex) + a mode check in the `_handle_pending_request` short-circuit (Claude) — not implemented.
- **Stateless JSON mode client quirks** — both current clients handle stateless servers; if a future client insists on SSE, flipping `json_response=False` in `get_session_manager()` is the only change needed.
- **`process wait`-style long calls** run in `asyncio.to_thread` worker threads (default pool ≈ 32); dozens of concurrent multi-minute waits could exhaust the pool. Acceptable now; revisit with a dedicated executor if it bites.
