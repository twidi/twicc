# Gemini provider — design (v1)

Status: implemented in `feat/gemini-provider`.
Target: Gemini CLI `0.68.x` (`@google/gemini-cli` on npm).

## Goal

Add Google's **Gemini CLI** as a third TwiCC provider, next to `claude_code` and
`codex`, with a focused but end-to-end scope:

- list and read Gemini sessions from disk (initial scan + live watcher);
- create a session from TwiCC, chat with streaming text and thinking;
- resume an existing session (from TwiCC or started in a terminal);
- approve or reject tool calls from the TwiCC UI;
- pick the model and the approval mode;
- cost and token accounting from the session file.

Everything the two existing providers do beyond that (subagents, plans, usage
quotas, title sync, hybrid terminal mode, trust sync, attachments, ephemeral
sessions, …) is explicitly out of scope for v1 (see the end of this document).

## Integration choice: ACP over stdio

The Gemini CLI offers three non-interactive surfaces:

| Surface | Streaming | Resume | Tool approvals | Model / mode switch |
|---|---|---|---|---|
| `gemini -p … --output-format stream-json` | yes | `--resume` by index only | no (must run `--yolo` or deny everything) | per process |
| `gemini --acp` (Agent Client Protocol, JSON-RPC 2.0 over stdio) | yes (`session/update`) | `session/load {sessionId}` | yes (`session/request_permission`) | `session/set_model`, `session/set_mode` |
| interactive TUI | n/a | n/a | n/a | n/a |

**ACP is the only surface with tool approvals and in-process model/mode
changes**, and it is the protocol the CLI maintains for editor integrations, so
v1 drives `gemini --acp`. One long-lived `gemini --acp` process per live session,
exactly like the Codex app-server model: TwiCC spawns it, `initialize`s,
`authenticate`s with the auth type the user already selected in
`~/.gemini/settings.json`, then `session/new` (provider-minted id, rebound with
`session_bound` like Codex) or `session/load` (resume).

TwiCC ships a small asyncio JSON-RPC client (`providers/gemini/acp.py`, no new
dependency). Observed protocol (0.68.0):

- `initialize` → `agentCapabilities.loadSession = true`, auth methods.
- `authenticate {methodId}` — the CLI reads credentials itself (API key from the
  environment / `.env`, OAuth cache, Vertex). TwiCC never sees a secret.
- `session/new {cwd, mcpServers: []}` → `sessionId`, `modes`
  (`default`, `autoEdit`, `yolo`, `plan`), `models`.
- `session/load {sessionId, cwd, mcpServers}` replays the history as
  `session/update` notifications before answering. TwiCC ignores that replay:
  the history comes from the session file.
- `session/prompt {sessionId, prompt: [{type: "text", text}]}` resolves at the
  end of the turn with `stopReason` and `usage`. Meanwhile:
  `session/update` with `agent_message_chunk`, `agent_thought_chunk`,
  `tool_call`, `tool_call_update`, `usage_update`, `available_commands_update`.
- `session/request_permission` (agent → client request) with options
  `proceed_always` / `proceed_once` / `cancel` (kinds `allow_always`,
  `allow_once`, `reject_once`) and the pending `toolCall`.
- `session/cancel` (notification) interrupts the current turn.

## History: the session files

Gemini CLI records every conversation as **append-only JSONL**:
`<GEMINI_CLI_HOME or ~>/.gemini/tmp/<project-slug>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl`.
This fits TwiCC's model (watcher reads from the last offset, one `SessionItem`
per line) without any conversion.

- `<project-slug>/.project_root` holds the absolute project directory. The JSONL
  itself has no `cwd`, so the watcher reads it to resolve the TwiCC project
  (shared with Claude/Codex sessions of the same directory).
- Line 1: `{sessionId, projectHash, startTime, lastUpdated, kind: "main"}`.
  Files whose `kind` is not `main` are ignored in v1 (subagents).
- Message records: `{id, timestamp, type: "user" | "gemini" | "info" |
  "error" | "warning", content, …}`. `gemini` records add `thoughts`, `tokens`,
  `model`, `toolCalls` (each call embeds its `args`, `result`, `status`,
  `resultDisplay`). Tool results are also echoed as a `user` record made of
  `functionResponse` parts.
- Control records: `{"$set": {...}}` (metadata), `{"$patch": {...}}` (content
  patch), `{"$rewindTo": id}` (rewind). Rendered as debug-only lines in v1.
- Every session starts with an injected `<session_context>` user record
  (environment description): classified as a system line.

### Re-appended records

The CLI re-appends a **whole** `gemini` record, with the same `id`, every time
it changes (tokens arrive, a tool call is added, a tool call finishes). A naive
one-line-one-bubble rendering would repeat thoughts and double-count tokens.

The Gemini compute keeps, per session, what was already shown for each record id
(thought count, content, finished tool calls, tokens) and rewrites each repeated
record into its **delta** through the standard `_transform_inline_provider` hook:
already-shown thoughts, unchanged content, already-finished tool calls and
already-counted tokens are dropped, and `twicc_gemini_repeat: true` marks the
line. An empty delta becomes a debug-only system line. The state is declared in
`live_state_maps` (live rollback) and rebuilt from the file on a cold start
(backend restart in the middle of a session), so rendering is identical in the
live and batch paths.

Tool calls are rendered once, when they reach a terminal status
(`success`, `error`, `cancelled`); while a call runs, the live stream shows the
working state.

### Kinds

| Line | Kind |
|---|---|
| header, `$set`/`$patch`/`$rewindTo`, `<session_context>`, `info`/`warning` | `system` |
| `user` with text | `user_message` |
| `user` with `functionResponse` only | `system` (tool result echo, debug only) |
| `gemini` with text content | `assistant_message` (thoughts collapsible above, finished tool calls below) |
| `gemini` without text, with finished tool calls | `tool_use` |
| `gemini` with thoughts only | `reasoning` |
| `error` | `api_error` |

Costs come from `tokens` (`input − cached` as input, `cached` as cache read,
`output + thoughts` as output) priced through the regular `ModelPrice` table
(`OPENROUTER_MODEL_PREFIX = "google/"`) with built-in fallback prices.

## Agent settings

The closed bundle is reused, no new `Session` column:

| Field | Gemini | Category | Wire |
|---|---|---|---|
| `selected_model` | `gemini-pro`, `gemini-flash`, `gemini-flash-lite` (+ versioned aliases) | IDLE | `-m` at spawn, `session/set_model` between turns |
| `permission_mode` | `default`, `auto_edit`, `plan`, `yolo` (the CLI approval modes) | IDLE | `session/set_mode` (`autoEdit` for `auto_edit`) |
| `effort` | single fixed `medium` level | — | none (the CLI exposes no effort control over ACP; the single level only keeps the model matrix clickable) |

`yolo` is excluded from untrusted projects; `yolo` is the only non-interactive
mode (hidden sessions). The model catalogue is static (`constants.py`), like
Codex, cross-checked against the CLI's `session/new` model list.

## Pieces

Backend, `src/twicc/providers/gemini/`:

- `constants.py` — Django-free catalogue, defaults, categories, aliases.
- `helpers.py` — `GeminiHelpers` (text extraction, model serialization, prices,
  bootstrap data, pending-request normalization).
- `records.py` — pure functions over Gemini records (shared by compute,
  helpers and tests).
- `compute.py` — `GeminiSessionCompute` (kinds, delta rewrite, tool links, cost).
- `sessions_watcher.py` — `GeminiSessionsWatcher` (path → session, project from
  `.project_root`).
- `orchestrator.py` — watcher + initial catch-up + generic background compute.
  Starts cleanly when the CLI is not installed (the directory simply never
  appears).
- `acp.py` — JSON-RPC stdio client.
- `agent/agent.py`, `agent/manager.py` — `GeminiAgent` / `GeminiAgentManager`.
- `ws.py` — `gemini:pending_request_response`.

Plus the registries (`Provider.GEMINI`, orchestrator, agent managers, helpers,
WS handlers), `GEMINI_COMPUTE_VERSION`, the `GEMINI_CLI_HOME` provider home
(data-dir `.env`, isolated in tests), the CLI tables (`help_strings`,
`help_context`, settings key prefix, wait-reply roots) and the few
claude-vs-codex `else` branches that would otherwise run Claude code on Gemini
lines.

Frontend, `frontend/src/providers/gemini/` (store, helpers, tool helpers, ws,
stream matching) and `components/session/detail/items/gemini/` (message, tool
call, pending request body), registered in `providers/index.js`, the timeline
provider list and the i18n catalogs (en, de, es, fr, pt-BR).

The `gemini` binary is looked up on `PATH` (override: `TWICC_GEMINI_BIN`). It is
not bundled: it is an npm package, unlike the Codex wheel.

## Out of scope for v1 (follow-ups)

- Subagent sessions (`kind != "main"`), `$rewindTo` / `$patch` faithful replay
  (shown as debug lines).
- Attachments (images, documents), ephemeral sessions, `/goal`-like features.
- Slash command catalogue (`available_commands_update`) and the composer picker.
- Usage quotas, status page, title generation with a Gemini model, title sync
  back to the CLI, trusted-folder sync, hybrid terminal mode, a `twicc gemini`
  passthrough command.
- Reasoning effort (needs a CLI-side `thinkingLevel` per session).
- Bundling the CLI with TwiCC.

## Known limitations

- The Gemini CLI resolves its `.env` by walking **up from the session
  directory** before falling back to `~/.gemini/.env`. A project located inside a
  directory that holds another `.env` (for instance a TwiCC data dir) hides the
  Gemini API key; the session then fails with "API key is missing". Put the key
  in the environment of the TwiCC process or in `~/.gemini/.env` and keep project
  directories outside such folders.
- Upgrades enable the new provider by default (the provider list is a deny
  list). Without the CLI, nothing runs; creating a Gemini session reports that
  the `gemini` binary is missing.
