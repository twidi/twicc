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
- cost and token accounting from the session file;
- TwiCC's MCP server and system-prompt addendum inside Gemini sessions;
- composer attachments (delivered as files);
- the CLI's slash commands in the composer picker;
- subagent runs (`invoke_agent`) listed under their session.

What is left out, and why, is at the end of this document.

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
exactly like the Codex app-server model: TwiCC spawns it, `initialize`s, then
`session/new` (provider-minted id, rebound with `session_bound` like Codex) or
`session/load` (resume). Both authenticate with the auth type the user already
selected in `~/.gemini/settings.json`; TwiCC never calls `authenticate` (which
would rewrite that setting) and never sees a credential.

TwiCC ships a small asyncio JSON-RPC client (`providers/gemini/acp.py`, no new
dependency). Observed protocol (0.68.0):

- `initialize` → `agentCapabilities.loadSession = true`, auth methods.
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
- Subagent runs (the `invoke_agent` tool) write
  `chats/<parent-session-id>/<subagent-id>.jsonl` with `kind: "subagent"`: they
  become TwiCC subagent sessions of their main session. Their first message
  wraps the delegated prompt in `<objective>…</objective>`; the compute exposes
  that inner prompt, so the shared exact-prompt matching links the subagent to
  its `invoke_agent` call (the call's result does not name the subagent).
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
`live_state_maps` (live rollback) and rebuilt from the stored lines on a cold start
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
| `effort` | single fixed `medium` level | — | none (see "Out of scope"; the single level only keeps the model matrix clickable) |

### TwiCC's MCP server

`session/new` / `session/load` receive TwiCC's MCP endpoint in `mcpServers`
(`http`, with the session's bearer token in an `Authorization` header). ACP has
no way to mark a server as trusted, so the agent answers the CLI's approval
requests for that server's tools itself (call ids `twicc_<tool>__call_<n>`):
TwiCC's MCP server is a control plane, orthogonal to the approval mode, as for
Codex. A new session's token names the draft id; a draft→canonical alias is
registered once `session/new` returns. `TWICC_NO_MCP` disables it.

### TwiCC instructions and context

ACP has no "append to the system prompt" either. The first message of a new
session carries the session's frozen addendum in a `<twicc:instruction>` block
(the CLI keeps it in the conversation it resumes, so it is never re-sent), and
every message goes through the shared context reconciliation
(`<twicc:context>`, anchored first in the text). The canonical session id, known
only after `session/new`, is injected the same way as for Codex. Both blocks are
removed from the stored copy by the generic ingestion strip. Slash commands are
sent untouched.

The CLI only reads and writes inside its workspace, so the session's artifacts
and scratch directories (where attachments land) are granted with
`--include-directories` at spawn. A resumed session gets exactly its own
directories; a new session, whose id `session/new` mints after the spawn, gets
their parent directories. Approvals that only touch the session's own
directories pass without asking (trusted projects); anything else still asks.

### Attachments

Composer attachments are planned with no native kind, so every attachment is a
file under the session's artifacts directory, listed in the `<twicc:attachments>`
manifest the CLI receives (Gemini reads images and documents with `read_file`).
The ingestion finds the manifest in the user record's text parts (new `gemini`
slot format: parts carry no `type`), stores it as `twicc_attachments` and
strips it, like for the other providers.

### Slash commands

Each ACP session announces its commands (`available_commands_update`:
built-ins, `~/.gemini/commands`, the project's `.gemini/commands`). They are
stored in the `Command` table per project (`/` activation), replacing that
project's previous announcement.

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

## Out of scope (follow-ups)

- **Reasoning effort.** The thinking level is a model-config setting
  (`modelConfigs.customOverrides` on `chat-base-3` / `chat-base-2.5`), with no
  ACP option (`session/set_config_option` is not implemented by the CLI) and no
  environment variable. The only per-process channel, the system settings and
  system defaults files (`GEMINI_CLI_SYSTEM_SETTINGS_PATH` /
  `GEMINI_CLI_SYSTEM_DEFAULTS_PATH`), is refused by Gemini CLI 0.68 unless the
  file and its directory are owned by root; the user's `settings.json` and the
  project's `.gemini/settings.json` are not TwiCC's to rewrite per session.
- **Ephemeral sessions.** TwiCC's contract is "no local transcript", and the
  Gemini CLI always records the session file (no option disables chat
  recording). Refused with an explicit message.
- **Usage quotas.** Only the Google sign-in (Code Assist) flavour exposes a
  quota call (`retrieveUserQuota`); API-key and Vertex users have none. Needs
  the OAuth credentials handling and a test account.
- **Native image input.** ACP accepts `image` blocks, but the CLI then stores
  the base64 inline in the session file; images go as files instead.
- `$rewindTo` / `$patch` faithful replay (shown as debug lines), status page,
  title generation with a Gemini model, title sync back to the CLI,
  trusted-folder sync, hybrid terminal mode, a `twicc gemini` passthrough
  command, bundling the CLI with TwiCC.

## Known limitations

- The Gemini CLI resolves its `.env` by walking **up from the session
  directory** before falling back to `~/.gemini/.env`. A project located inside a
  directory that holds another `.env` (for instance a TwiCC data dir) hides the
  Gemini API key; the session then fails with "API key is missing". Put the key
  in the environment of the TwiCC process or in `~/.gemini/.env` and keep project
  directories outside such folders.
- In a folder it does not trust, the CLI reads no `.env` file at all. TwiCC
  passes `GEMINI_CLI_TRUST_WORKSPACE=true` only for projects the user trusted in
  TwiCC; in an untrusted project an API key stored in `~/.gemini/.env` is not
  found, and the start error says so (trust the project, or export
  `GEMINI_API_KEY` in TwiCC's environment; OAuth sign-in is unaffected).
- When the very first prompt of a session fails (quota, network), the CLI
  rewinds it and, at the next load, deletes the now empty session file: TwiCC
  then shows the session as stale.
- `session/load` makes the CLI create and remove a short-lived session file;
  the watcher logs one harmless "file not found" error for it.
- The live text stream is matched to the stored record by content (ACP chunks
  carry no record id), so a streamed block can disappear for a moment at the
  end of a turn, before the watcher stores the final record.
- Upgrades enable the new provider by default (the provider list is a deny
  list). Without the CLI, nothing runs; creating a Gemini session reports that
  the `gemini` binary is missing.
