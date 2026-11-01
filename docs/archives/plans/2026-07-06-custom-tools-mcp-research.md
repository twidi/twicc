# Custom Tools via MCP — Research

**Status:** research / feasibility only. **No decision taken, nothing implemented.** Captured to avoid re-running the investigation when the feature is opened.
**Date:** 2026-07-06.
**Scope:** can TwiCC expose its *own* tools to the agents (Claude Code + Codex), on top of the built-in tools — i.e. genuinely new callable tools the model can invoke? If yes, how, at what context cost, and with what scoping (per-session vs machine-global)? The motivating goal is to go beyond what the current `twicc-*` skills + CLI can do — in particular to let a tool call, running inside TwiCC's ASGI backend, drive the front-end live over WebSocket.

> Self-contained. Assumes the TwiCC codebase but no prior conversation. All code references verified against the versions below; treat undocumented mechanisms as version-fragile (re-check on every SDK/CLI bump).

## 0. Versions investigated

| Component | Version |
|---|---|
| `claude-agent-sdk` (Python) | 0.2.110 (`.venv/.../claude_agent_sdk/_version.py`) |
| bundled `claude` CLI (the one the SDK actually spawns) | 2.1.191 (`claude_agent_sdk/_bundled/claude`; `_cli_version.py`) |
| `openai_codex` vendored SDK | source tag `rust-v0.136.0` (`src/openai_codex/`, `docs/codex-vendoring.md`) |
| `openai-codex-cli-bin` | 0.136.0 (`.venv/.../codex_cli_bin/bin/codex --version`) |
| Codex Rust upstream (cross-checked) | clone at `/home/twidi/dev/codex`, tag `rust-v0.136.0`, commit `7ca611348d` |

## 1. Bottom line

- **Yes for both providers**, but the common denominator is **MCP**.
  - **Claude**: first-class **in-process** custom tools (`@tool` + `create_sdk_mcp_server`), *or* external MCP (stdio/SSE/HTTP).
  - **Codex**: **external MCP servers only** — no in-process tool registration, no `@tool`, no `tools=` param anywhere in the SDK.
- **Recommended shape:** a single **MCP Streamable-HTTP server exposed as a route of TwiCC's own ASGI backend** (not a separate process). Both providers consume it over HTTP; because the endpoint *is* the backend, tools have direct access to the DB, session state, and the **Channels channel layer** → a tool call can `group_send` to `UpdatesConsumer` and drive the front-end live. This is the capability a skill+CLI (external, one-shot, no return channel to the front) cannot match.
- **Scoping is symmetric** (good surprise): both providers can be wired **per session**, so we do *not* have to pollute the machine-global Codex config the way the Codex plugin had to.
- **The one real asymmetry is context cost** for a large tool set: Claude defers MCP tool schemas by default; Codex only defers above 100 tools.

## 2. Axis 1 — Adding custom tools

### Claude (`claude-agent-sdk`)
- `@tool(name, description, input_schema, annotations=None)` wraps an `async def handler(args)->dict` into an `SdkMcpTool`; `create_sdk_mcp_server(name, version, tools=[...])` returns `McpSdkServerConfig(type="sdk", name, instance=<live mcp.server.Server>)`. Runs **in-process** — the instance never crosses the subprocess boundary; the CLI invokes it over the stdio control protocol (`_internal/query.py`, `subprocess_cli.py:307-332`).
- Wired via `ClaudeAgentOptions.mcp_servers: dict[str, McpServerConfig]` (`types.py:1670`); tool naming `mcp__<mcp_servers-key>__<tool-name>`, referenced in `allowed_tools`/`disallowed_tools`.
- External servers also supported and mixable: `McpStdioServerConfig` / `McpSSEServerConfig` / `McpHttpServerConfig` (`types.py:601-637`).
- **No non-MCP path**: `tools=` only selects built-ins; `can_use_tool` and `hooks` only gate/observe existing tool calls; `agents=`/`skills=` reuse the `Agent`/`Skill` tools. Every custom tool funnels through `mcp_servers`.

### Codex (`openai_codex`, vendored)
- **No in-process mechanism.** No `@tool`, no `tools=` on `thread_start`/`turn`/`run`; no `mcp_servers` field on any `*Params` model. Public surface: `api.py` (`Codex`/`Thread`/`TurnHandle`).
- The only app-provided callback is an **`ApprovalHandler`** (`client.py:58`, `_default_approval_handler` 603-609) — accept/deny for built-in gated actions (exec, file change, permission). **Not** a tool-registration mechanism.
- `DynamicToolSpec` / `item/tool/call` exist in the wire schema as a "dynamic tool dispatch to client" concept, but the vendored SDK models none of it and TwiCC has explicitly deferred it (see `docs/superpowers/specs/2026-05-14-codex-approvals-design.md`). Not usable today.
- ⇒ Extra tools for Codex = **MCP servers** (stdio local or Streamable HTTP), configured through Codex's config layer, not through typed SDK objects.

## 3. Axis 2 — Context pollution & deferred loading

### Claude — deferred **on by default**, finely controllable if we author the server
- The spawned CLI (2.1.191) runs Claude Code **"Tool Search"** by default: only tool **names** + server instructions load at start; schemas are fetched on demand. Every `isMcp` tool is deferred unconditionally (subject to overrides), provided: model supports `tool_reference` blocks (Sonnet 4+/Opus 4+/Haiku 4.5+), deployment isn't an unsupported Foundry target, and the `ToolSearch` tool isn't disallowed. (Gate logic extracted from the compiled binary; default mode = `tst`/on when `ENABLE_TOOL_SEARCH` is unset.)
- **Global control from the SDK:** only via the generic env passthrough `ClaudeAgentOptions.env={"ENABLE_TOOL_SEARCH": "auto:N" | "false" | ...}` (`types.py:1777` → `subprocess_cli.py:434`). No typed option. `auto`/`auto:N` = load upfront while it fits ~N% of context, defer the overflow; `0/false/off` = disabled.
- **Per-tool control when we author the server** — the CLI reads, off each tool's `tools/list` entry:
  - `_meta["anthropic/alwaysLoad"] = true` → never defer this tool (use for the 3–5 hottest tools);
  - `_meta["anthropic/searchHint"] = "..."` → custom search-index hint.
  - Also a per-server `alwaysLoad: true` key accepted inside the `mcp_servers` config dict (passes through untyped).
- ⚠️ Caveats: (a) depends on `tool_reference` blocks surviving the round-trip — a custom `ANTHROPIC_BASE_URL` proxy often strips them and Claude Code then **silently disables** Tool Search (verify against our infra); (b) all of these levers are **undocumented in the Python SDK** (found by string-extracting the binary) → reachable but version-fragile.

### Codex — deferral exists but is **threshold-gated at 100 tools**
- Real BM25 `tool_search` + `defer_loading`/`ToolExposure::Deferred` in Rust core (`mcp_tool_exposure.rs`, `tools/handlers/tool_search.rs`, `tools/handlers/dynamic.rs`).
- **But** `DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100`: with < 100 MCP tools combined across all servers, **all schemas load eagerly into context**. Deferral auto-activates only at ≥ 100, or when the experimental `tool_search_always_defer_mcp_tools` feature is enabled (default **off**, "under development"; `codex features list`).
  - ⇒ **~30 tools would all sit in Codex's context by default.**
- Force it early: `-c features.tool_search_always_defer_mcp_tools=true` (a.k.a. `--enable tool_search_always_defer_mcp_tools`) — experimental, risky.
- Reduce instead: per-server `enabled_tools` / `disabled_tools` filter at registration (Rust `codex-mcp/src/tools.rs`) → filtered tools are never fetched, **zero context**. Config-only (not in the Python SDK).
- `default_tools_approval_mode` / per-tool `approval_mode` = execution/approval gate only, **no** context effect.
- `DynamicToolSpec.defer_loading` in the vendored SDK is **dead**: carrier `thread/start.dynamicTools` is `#[experimental]` and absent from generated `ThreadStartParams` (`v2_all.py:6539`).

## 4. Axis 3 — Per-session scoping (avoiding machine-global Codex config)

Both providers can be wired per session. Ranked options for Codex (verified live via `codex mcp list/get --json`, no model run):

1. **★ `thread_start(config={"mcp_servers": {...}})`** — **per-thread**, zero disk writes, `mcp_servers` unrestricted on this path. App-server merges each key via `json_to_toml` onto the same `ConfigBuilder` as `-c` (`config_manager.rs::load_with_cli_overrides`). **TwiCC already uses this exact channel** (`providers/codex/agent/manager.py:584-606` passes `config={"model_reasoning_summary": "detailed"}`; in-repo comment already flags it as preferred over `-c`). Adding an `mcp_servers` key = same-pattern extension.
2. **`-c mcp_servers.twicc.url="..."`** via `CodexConfig.config_overrides` (`client.py:220-230` → `--config` per entry). Confirmed live: dotted-path TOML or inline table, additive, leaves `~/.codex/config.toml` untouched. Per-process = per-session in TwiCC's one-codex-per-session architecture.
3. **Project `.codex/config.toml`** — Codex reads `mcp_servers` from a `.codex/config.toml` walked from git-root to cwd (`ConfigLayerSource::Project`), gated by the same `trust_level` TwiCC already manages (`providers/codex/trust.py`). But: durable/repo-footprint, and **no RPC to write it** (`config/value/write` is hard-restricted to the user config → would need plain file I/O).
4. ❌ `codex mcp add` — machine-global only. Avoid.

Claude side: native per-session via `ClaudeAgentOptions.mcp_servers` (an HTTP entry `{"type": "http", "url": "...", "headers": {...}}`).

⇒ Unlike the Codex **plugin** (forced global — see `project_twicc_plugin_scoping` memory), an MCP server can be injected **per session** on both providers.

## 5. Proposed architecture sketch (not decided)

- **One** MCP Streamable-HTTP server, a route of the TwiCC ASGI backend (Django/Channels).
- Per-session wiring: Claude → `options.mcp_servers`; Codex → `thread_start(config={"mcp_servers": {...}})`.
- Tools run in-process → direct DB + channel-layer access → live front-end effects via `group_send` to `/ws/` `UpdatesConsumer`; potentially bidirectional (tool waits on a UI interaction, returns the result to the model).
- Context: free + fine-grained on Claude (`_meta` per tool); on Codex keep the exposed set small or enable the experimental flag.

## 6. Open questions / decisions to make
1. **Codex context** for a large tool set (< 100 threshold): small curated set vs experimental defer flag vs wait for the feature to mature.
2. **`ANTHROPIC_BASE_URL` proxy** in our deployments — does it preserve `tool_reference` blocks (else Claude Tool Search silently off)?
3. **Endpoint auth + dynamic port** (worktree default+1) — how the per-session URL/headers are resolved and secured (ties into `local_only_no_password` gate).
4. **Relation to existing skills+CLI**: does the MCP server replace, duplicate, or complement them? Scope depends on this.
5. **Fragility**: undocumented Claude levers (`_meta`, `ENABLE_TOOL_SEARCH`) and the Codex experimental flag must be re-tested on every SDK/CLI bump (fold into `reference_sdk_update_procedure` / `reference_codex_sdk_update_procedure`).

## 7. Sources
- Claude — [Custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools), [Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- Codex — [MCP](https://developers.openai.com/codex/mcp), [Config reference](https://developers.openai.com/codex/config-reference), [SDK](https://developers.openai.com/codex/sdk)
- Cross-checked against installed/vendored code and the `claude` 2.1.191 / `codex` 0.136.0 binaries.
