# Ephemeral Sessions Design

**Status:** adversarial review passed (backend round 3, frontend round 3, provider verification); ready for implementation planning
**Date:** 2026-09-05

## 1. Scope

An *ephemeral session* is a one-shot agent run that leaves **no transcript**:
the provider writes no session JSONL (Claude Code `--no-session-persistence`, Codex
`ephemeral` thread), so TwiCC's watcher never sees it and no `Session` row is
ever created. It is not a `hidden` session — a hidden session is recorded and
counted; an ephemeral one is not.

The user composes it with the normal new-session composer, sends one message,
and gets **only the agent's final answer** back, rendered like a normal
assistant message. No streaming, no tool cards, no intermediate state. The
entry lives in browser memory and IndexedDB until the user discards it.
A received answer survives a backend restart. A running entry becomes `lost`
when reconnect finds no matching live run. No result replay is provided.

Decisions already taken with the user (not re-opened here):

| Decision | Choice |
|---|---|
| What TwiCC keeps of the run | The live agent only (in-memory, stoppable). **No `ProcessRun` row.** |
| What the user sees | A callout, then the final assistant answer. Nothing in between. |
| Entry point | A discreet composer icon, same pattern as hybrid mode. Not the agent-settings popover (the flag is not an agent setting). |
| TwiCC MCP server + plugin | **Off** for ephemeral runs. |
| Page reload | The entry survives (IndexedDB). A backend restart marks a running entry *lost*; received answers remain. |
| After the answer | The composer is gone; a **Discard** bar replaces it. |

Rules from `CLAUDE.md` apply: no CHANGELOG entry without an explicit ask,
`AGENTS.md` mirrors every `CLAUDE.md` change, no migration is needed (nothing
touches the schema).

## 2. Verified facts

### 2.1 Provider flags

- **Claude Code** (bundled CLI 2.1.259): `claude -p --session-id <uuid>
  --no-session-persistence` honours the supplied id (the result frame echoes
  it), returns `result` (final text) and `total_cost_usd`, and writes **no**
  JSONL under `~/.claude/projects/`. Verified 2026-09-05 with the SDK-bundled
  binary. TwiCC already passes this flag for its throwaway auth calls
  (`src/twicc/providers/claude_code/auth.py`, `extra_args={"no-session-persistence": None}`).
  Side effect observed: the CLI creates `<projects>/<cwd-key>/memory/` for the
  cwd. That directory already exists for any project that had a session; it is
  provider behaviour, outside TwiCC's control.
- **Codex**: `ThreadStartParams.ephemeral` exists
  (`src/openai_codex/generated/v2_all.py`; the resulting `Thread.ephemeral` is
  documented as "should not be materialized on disk"). TwiCC's own wrapper
  already accepts it and never sets it:
  `TwiccAsyncCodex.thread_start_with_policy(..., ephemeral=None)` in
  `src/twicc/providers/codex/sdk_wrappers.py`. Constraint: the vendored SDK
  client refuses to start a goal on an ephemeral thread
  (`src/openai_codex/client.py`, `_start_goal_operation`: "thread must be
  persisted before starting a goal"). This IS reachable: `CodexAgentManager.create_session`
  runs `parse_hardcoded_command(text)` on the first message and starts the
  agent with `command=…` instead of a turn, so a `/goal …` first message would
  fail and settle on USER_TURN with an empty answer. The service rejects
  hardcoded commands as the first message of an ephemeral Codex run (§4.1).

### 2.2 The backend already runs agents without a `Session` row

Between `_register_and_start` and the first JSONL line, every new session is
exactly in the state an ephemeral session stays in forever:

- `ProcessRun.session_id` is a plain `CharField`, not a FK
  (`BaseAgentManager._register_and_start`, `src/twicc/agent/base_manager.py`).
- `BaseAgentManager._broadcast_session_updated` is a documented no-op when the
  row is absent.
- `Session.objects.filter(id=...).update(...)` calls are silent no-ops.
- `process_state` broadcasts come from `AgentInfo`
  (`src/twicc/agent/states.py`), never from the row.
- `BaseAgent._is_session_hidden` reads the row only and returns `False`
  (uncached) when no row exists — broadcasts are never gated off.
- The MCP token is a pure HMAC of the session id (`src/twicc/mcp/identity.py`)
  — no storage — which is exactly why it must not be wired (§4.4).

### 2.3 The frontend already renders sessions without a row

A **draft** is a full `sessions[id]` entry with a client-generated UUID,
snapshotted agent settings and layout, persisted to IndexedDB, and the whole
session UI works on it (`createDraftSession`, `frontend/src/stores/data.js`).
`SessionItemsList.vue` already skips item loading for drafts and has a
draft-only empty state. Client-side items with negative `lineNum` exist
(`SYNTHETIC_ITEM`, `frontend/src/constants.js`) and are rendered by the normal
item components through `setParsedContent`.

### 2.4 The final answer is already parsed

| Provider | Where | Fields |
|---|---|---|
| Claude Code | `ResultMessage` branch of `_run_message_loop` (`src/twicc/providers/claude_code/agent/agent.py`) | `result` (final text), `total_cost_usd`, `is_error`, `errors`, `duration_ms`, `usage` |
| Codex | `item/completed` on an `agentMessage` item (`_handle_stream_event`, `_agent_message_item`) | `AgentMessageThreadItem.text` |

Codex exposes no cost in the events TwiCC consumes today (`Turn` has no usage;
`thread/tokenUsage/updated` exists in the protocol but is not handled). Cost
display is therefore Claude-only in V1.

### 2.5 In-memory buffers have no expiry

`pending_agent_settings` and `pending_session_attributes` are popped by the
watcher at row creation. `pending_titles` is popped by the manager once the
ASSISTANT_TURN title flush succeeds (`_on_flush_success`); a failed flush
(no row) schedules `_retry_flush_pending_title` in the background. `_start_agent`
also pops and re-sets all three under the canonical id for Codex. None of
these fire for an ephemeral run: the manager must pop its own entries at the
right moments (§4.2) or they leak — and the title retry loop would spin for a
row that never comes.

## 3. Definition of the feature

- **Creation only from the web UI** (`send_message` over `/ws/`), on a draft.
  The flag rides the payload like `hybrid` does and is honoured only on that
  trusted path (`create_session_from_payload(payload, allow_ephemeral=True)`).
  The CLI/drop-request path ignores it in V1.
- **Exclusive with hybrid** (hybrid is a persistent tmux CLI). A draft can be
  one or the other; the composer enforces it, the service re-validates
  (`ephemeral_hybrid_conflict`).
- **One message, one answer.** Attachments follow existing provider capabilities:
  images for both providers; documents for Claude Code only.
  Permission prompts and question widgets **work**: they ride `AgentInfo.
  pending_requests` and `resolve_pending_request`, which only need the live
  agent. On Codex the first message must be a plain prompt: hardcoded
  commands (`/goal`, `/compact`, `/plan`, … — `parse_hardcoded_command`) are
  refused (`ephemeral_command_unsupported`). Claude Code slash commands are
  ordinary text for the CLI and pass through.
- **What the entry shows:** a callout (running / done / error / lost), the
  optimistic user message, then the final assistant answer rendered by the
  normal assistant-message components. Cost and duration in the callout when
  known.
- **What the user can do:** stop it while running, discard it any time. Nothing
  else (no pin, archive, rename-through-API, share, fork, cron, hide, second
  message, and no soft interrupt — Escape / `interrupt_session` is hidden for
  ephemeral entries, §5.7; should one slip through, the agent records it and
  the result is emitted as `stopped`, §4.2).

## 4. Backend

### 4.1 Payload and service (`core/services/session_creation.py`)

`_handle_send_message` (`src/twicc/asgi.py`) adds `"ephemeral": bool(content.get("ephemeral"))`
next to `"hybrid"` and calls the service with `allow_ephemeral=True`, the same
trusted keyword-only switch pattern as `allow_hybrid`.

In the service, for **every** creation request, before any stashing or
side effects, check all registered managers for a known ephemeral id. Refuse
it with `ephemeral_readonly`, even when the flag is absent/false or the
requested provider differs. The manager repeats the check under its lock.

When `ephemeral` is true:

- A persistent Session id is refused with `ephemeral_existing_session`. Release
  that provisional reservation completely after the DB proves the row exists.
  Do not retain a readonly tombstone that would block ordinary later sends.

- Preserve this early refusal (`manager.is_ephemeral_id(session_id)`, §4.2). `_handle_send_message` resolves `exists` from the DB
  only, so a second send on an ephemeral id (alive or dead) always lands in
  this create path; without this early check the three `set_pending_*` calls
  below would run first and leak.
- Reject `hybrid` (`ephemeral_hybrid_conflict`), `worktree_branch`/`worktree_path`
  (`ephemeral_worktree_unsupported`), `spawned_by_session_id`
  (`ephemeral_spawn_unsupported`). None of these can come from the UI path
  today; the checks are defence in depth for the drop-file boundary.
- For Codex, reject a first message that `parse_hardcoded_command` recognises
  (`ephemeral_command_unsupported`, §2.1).
- Keep: provider/project resolution, `ensure_provider_running`, title
  validation, agent-settings resolution + consistency + hidden constraints,
  the trust clamp (unchanged, done in each provider's `_create_agent`).
- Compose the addendum with `compose_addendum(..., ephemeral=True)` (§4.6).
- Stash the pending buffers **as today** (`set_pending_title`,
  `set_pending_agent_settings`, `set_pending_session_attributes(...,
  ephemeral=True)`): the provider agents read the addendum and `hidden` from
  them during start. The manager pops them at the right moments (§4.2).
- Skip the layout resolution (an ephemeral entry renders in a single pane,
  §5.3).
- Call `manager.create_session(..., ephemeral=True)`.
- The `except RuntimeError` branch forwards `getattr(e, "code", "manager_busy")`
  as the error code (`SendDeliveryError` is a `RuntimeError` carrying `code`),
  so a provider-side refusal reaches the client with its real code instead of
  `manager_busy`.

`PendingSessionAttributes` gains an `ephemeral: bool` field (default `False`).
It is the carrier the manager and both agents read — it never reaches the
watcher for an ephemeral run.

### 4.2 Manager (`agent/base_manager.py`)

`create_session(..., ephemeral=False)` forwards to `_start_agent(...,
ephemeral=...)`, which forwards to `_create_agent(..., ephemeral=...)`
(new keyword on the abstract factory, default `False`).

`_register_and_start(agent, text, resume, *, ephemeral=False, ...)`:

- Registers the agent in `self._agents` **as today** — this is what keeps
  `kill_agent`, `interrupt_agent`, `resolve_pending_request`, the timeout
  monitor, `shutdown` and `get_active_agents` working.
- **Skips** `_persist_run_and_start_timestamps` (no `ProcessRun`, no
  `Session` update) and `_broadcast_session_updated` (a no-op anyway).
- Still emits the STARTING broadcast and calls `agent.start(...)`.

`notify_session_bound` is **kept** for ephemeral runs. For Codex the agent
is keyed by the thread id Codex mints, and every broadcast (`process_state`,
`ephemeral_result`) carries that id; the frontend re-keys its local entry on
`session_bound` (§5.2). For Claude Code the ids are equal and the frame is a
no-op, as today.

**Pending buffers — two pop points.** The agents read the buffers at
different times: Codex reads the addendum and seeds the context baseline
inside `_create_agent`; Claude Code reads the addendum
(`_read_system_prompt_addendum`) and seeds the baseline inside `agent.start()`,
which runs from `_register_and_start`. The title is read by nobody at start
but the ASSISTANT_TURN flush pops it on success and schedules a background
retry on failure — and for Claude that callback fires inside `start()`. So:

- `pending_title`: popped **before** `agent.start()` (right after
  `_create_agent` returns), under both ids. The flush then finds nothing and
  no retry is scheduled. The title stays purely frontend-side (§5.3).
- `pending_agent_settings` + `pending_session_attributes`: popped **after**
  `agent.start()` returns, under both ids.
- A `finally` covers factory, binding, and start failures. It drains all
  three buffers under every known id. The service also drains its draft
  buffers if validation or manager invocation fails after stashing.
- Reserve the draft id under the manager lock before the first asynchronous
  factory operation. Add the canonical id immediately when available. Failed
  launches keep their reserved ids; retries use a fresh draft id. This avoids
  ambiguous replay after a launch partially succeeds.

`BaseAgent` gains `ephemeral: bool` (constructor keyword, default `False`);
`BaseAgent.get_info` sets `extra={"ephemeral": True, "ephemeral_draft_id": <original draft id>}` when set (`get_info`
sets no `extra` today; the hybrid override that replaces `extra` cannot apply
— hybrid and ephemeral are exclusive). The `active_processes` snapshot
(`asgi.py`) and every `process_state` frame therefore say which processes are
ephemeral, on live frames and on reconnect.

`_persist_process_run_transition` and `_update_session_stopped_at` are
skipped when `agent.ephemeral` (there is no row to mirror to; today they
already tolerate `agent.process_run is None`, the skip only avoids the DB
round-trips and the log noise).

**Result chokepoint.** In `_on_state_change`, when `agent.ephemeral`:

- On the transition **to `USER_TURN`** (the first time): broadcast
  `ephemeral_result` (§4.5) built from `agent.ephemeral_final_text` and
  `agent.ephemeral_usage` — with `status: "stopped"` instead of `done` when
  the agent flags the turn as soft-interrupted (Claude's `_soft_interrupting`
  path falls through to USER_TURN with an error `ResultMessage`; the agent
  records `ephemeral_soft_interrupted = True` there) — then **schedule** the
  stop out of the callback:
  `asyncio.create_task(self.kill_agent(session_id, reason="ephemeral-done"))`
  with a strong reference and exception logging (the `_spawn_detached`
  pattern of `asgi.py`). It must not be awaited inline: the USER_TURN callback
  runs inside the agent's own message-loop task (Claude
  `_run_message_loop`) / turn task (Codex `_turn_task`), and both
  `interrupt_or_kill` paths cancel and await that very task. The DEAD
  transition then arrives from the kill task.
  Using the USER_TURN transition — not the raw `ResultMessage` — means the
  existing holds (Claude background tasks / Monitors / scheduled wake-ups,
  Codex subagent hold) are honoured: the answer is emitted when the agent
  would otherwise settle idle.
- On the transition **to `DEAD`** with no result emitted yet:
  - `kill_reason == "shutdown"`: no frame. The backend is going away; the
    client marks the entry `lost` on reconnect (§5.6).
  - `kill_reason in DELIBERATE_STOP_REASONS` (`manual`, `force`, `archived`;
    `src/twicc/agent/states.py`): `status: "stopped"`.
  - otherwise: `status: "error"`, `error: agent.error or agent.kill_reason`.
- The in-memory context baseline both providers seed needs no extra work:
  `BaseAgent._transition_to_dead` already calls `clear_context` and
  `reset_baseline` on every DEAD path. `_cleanup_dead` runs as today.

**Admission and refusing a second message.** A shared process-local registry
contains only ids, provider, project id, and admission state, never content.
It reserves a draft id synchronously before the first asynchronous operation
in the WS send handler. The service can reserve directly for trusted internal
callers; a private ownership token lets the service and manager reuse their
own reservation. Every creation checks this registry, independent of provider
and requested mode. This prevents cross-provider concurrent reuse.

The manager exposes `is_ephemeral_id(session_id) -> bool` through the registry.
`_start_agent` adds the canonical id. The pending admission ends in a `finally`
after agent registration or failure, including WS/service validation errors
before manager invocation. On admission failure, broadcast
`ephemeral_admission_failed` with `draft_session_id`, `provider`, `project_id`,
and the generic error "The ephemeral run could not start." This frame carries
no prompt or provider error payload. It reaches a replacement socket that saw
`ephemeral_starting` but cannot receive the original socket's `send_error`.
The client marks a matching running entry `error` and clears pending control
records; the original correlated `send_error` may still restore a fresh draft.
Ignore this frame for terminal or discarded entries. Both ids remain readonly tombstones until
shutdown. A failed attempt uses a fresh UUID for its recovery draft. The
service checks it first thing (§4.1). `create_session` also raises
`SendDeliveryError(code="ephemeral_readonly")` for such an id as defence in
depth (Codex's `create_session` has no "already exists" guard at all).
Without this, a `send_message` on a dead ephemeral id would fall into the
create path of `_handle_send_message` (no row → "new session") and mint a
**persisted** session under the ephemeral id. The registry is cleared by
`shutdown` (a restart also empties the frontend's notion of a live process,
§5.6).

### 4.3 Provider agents — Claude Code (`providers/claude_code/agent/`)

`ClaudeCodeAgentManager._create_agent(..., ephemeral)` passes the flag to
`ClaudeCodeAgent`. In `start()`:

- `extra_args["no-session-persistence"] = None`, alongside the existing
  `extra_args["session-id"] = self.session_id` (verified compatible, §2.1).
- `mcp_servers={}`, `allowed_tools=[]`, no `write_claude_mcp_config` call (no
  `mcp-configs/<id>.json` on disk), `strict_mcp_config=True`, `plugins=[]` (the TwiCC skills document the
  MCP/CLI surface that is not wired). Strict MCP configuration also disables
  other inherited MCP servers for this Claude run; this is an accepted V1
  limitation.
- `add_dirs=[]`: `resolve_and_create_work_dirs` is **not** called (no
  `artifacts/<id>` / `scratch/<id>` directories).
- Hooks, `can_use_tool`, `setting_sources`, `settings` (fast mode), `env`
  (`CLAUDE_CODE_ENABLE_TODO_TOOLS`), `include_partial_messages`: unchanged except cron hooks below.
  `StreamEvent`s still flow to the frontend as `stream_block_*` frames; the
  frontend ignores them for ephemeral entries (§5.4).
- `patch_client_for_logging` (dev-mode SDK log at `logs/sdk/claude_code/<id>.jsonl`)
  is skipped: it is a transcript by another name.
- Disable `CronCreate`, `CronDelete`, and `CronList` for ephemeral runs.
  Do not install cron persistence hooks. Manager cron callbacks also return
  immediately for ephemeral agents, so no `SessionCron` stores a prompt.
- Suppress raw stderr and content-bearing result/error logs for ephemeral
  runs. Keep technical state diagnostics without SDK payloads.
- `_seed_context_baseline` / `_reconcile_context`: unchanged (the reconcile is
  a no-op without a row). The baseline reset at DEAD is the manager's (§4.2).

In `_run_message_loop`, the `ResultMessage` branch records
`self.ephemeral_final_text = msg.result` and `self.ephemeral_usage = {
"cost_usd": msg.total_cost_usd, "duration_ms": msg.duration_ms, "usage":
msg.usage }` before the USER_TURN transition. Only the last `ResultMessage`
counts (a held turn may see several).

### 4.4 Provider agents — Codex (`providers/codex/agent/`)

`CodexAgentManager._create_agent(..., ephemeral)`:

- Add process-local `features.plugins=false` to `CodexConfig.config_overrides`.
  Codex ignores plugin enable-state overrides, so omitting TwiCC MCP is not
  sufficient. This disables all Codex plugins for this run, an accepted V1
  tradeoff. It does not mutate user config or copy credentials. Verified with
  bundled Codex 0.153.4 using initialize + skills/list: enabled TwiCC skills
  disappear with this flag. Other sessions keep their plugins.
- `thread_start_with_policy(..., ephemeral=True)`.
- Do not inject the TwiCC MCP server (`_twicc_mcp_server_config` and
  `_apply_codex_mcp_context_mode` are not called); `register_draft_alias`
  is not called. Read effective config before thread start and explicitly
  disable existing inherited MCP servers through thread config. Do not add
  transportless entries for absent servers. Other MCP servers are also off
  for this Codex run, matching the Claude V1 limitation.
- `resolve_and_create_work_dirs` not called; the workspace-write sandbox
  keeps only the cwd as writable root (`update_settings_with_policy` skipped).
  The agent must receive `work_dirs=[]` explicitly: `CodexAgent.start()`
  resolves and creates the directories itself when `_work_dirs is None`.
- `inject_context(thread.id, session_id=thread.id)` skipped: there is no
  session id to announce.
- `attach_stderr_logging`, `log_stream_event`, `log_approval_request`, and
  `log_approval_response` are all skipped. Debug mode must not create an SDK
  log for the ephemeral id. Suppress payload-bearing error and approval
  exception logs too, including startup errors propagated through manager,
  service and WS handling. Keep diagnostics limited to technical status.
  Quote literal MCP server names in dotted TOML override keys.
- `CodexAgent(..., ephemeral=True, work_dirs=[])`.

In `_handle_stream_event`, after the parent-thread filter, on `item/completed`
for an `agentMessage`, retain the last `phase="final_answer"` text. Use the
last unphased message only when no final-answer message exists. Do not use
commentary or child-thread text as the final answer. No usage in V1
(§2.4).

### 4.5 The `ephemeral_result` frame

Broadcast on the `updates` group (same channel as `process_state`):

```json
{
  "type": "ephemeral_result",
  "session_id": "<canonical id>",
  "project_id": "<project id>",
  "provider": "claude_code",
  "status": "done",
  "text": "<final assistant text, may be empty>",
  "error": null,
  "cost_usd": 0.0197,
  "duration_ms": 2784,
  "finished_at": "2026-09-05T21:44:10.123Z"
}
```

`status` ∈ `done` | `error` | `stopped`. `cost_usd`/`duration_ms` are `null`
when unknown (Codex). Emitted exactly once per ephemeral agent.

### 4.6 System-prompt addendum (`agent/system_prompt.py`)

`compose_addendum(..., ephemeral: bool = False)`. When true:

- The **static blocks** — the shared `STATIC_ADDENDUM` (skills, MCP tools,
  artifacts and scratch conventions) **and** the provider one
  (`helpers.SYSTEM_PROMPT_STATIC_ADDENDUM`: Claude's mid-turn messages
  section, Codex's artifacts-dir mention) — are replaced by one short static
  text: this run is ephemeral, TwiCC and the provider do not write a local session transcript, there is no session id,
  no artifacts or scratch directory, no TwiCC tools; deliver the result in the
  final answer or as side effects in the project directory.
- The **dynamic block** keeps project, provider, resolved agent settings and
  `started_at`, drops `session_id` / `artifacts_base_dir` / `scratch_base_dir`,
  and adds `ephemeral: true`.

### 4.7 What still shows up outside the web UI

- **Nothing.** `twicc processes`, `twicc process <id>`, `process wait` and
  `processes wait` are `ProcessRun`-backed (`src/twicc/cli/processes.py`,
  `process.py`, `process_wait.py`, `processes_wait.py`); with no row they
  report an unknown session. `twicc sessions` / `twicc session` never see it
  either. This is the accepted consequence of "no `ProcessRun` row" (§1).
  `extra.ephemeral` is visible only on the WS frames (`process_state`,
  `active_processes`), which is all the web UI needs.
- The in-process registry consumers that do see it (`views.py` active-process
  endpoints, `usage_task.py` idle gating, the share consumer) treat it as any
  live agent; the usage-task gate correctly counts it as activity.
- Boot-time `ProcessRun` cleanup has nothing to do (no row).
- `manager.shutdown()` kills it like any agent; nothing to recover on restart.

### 4.8 Residual traces (accepted)

- Provider-side `memory/` directory for the cwd (Claude, §2.1).
- `backend.log` lines naming the session id (state transitions). Do not log
  prompt, answer, tool arguments, or SDK event bodies for ephemeral runs.
- IndexedDB retains the prompt summary and final answer until Discard. This
  is browser-local storage, not backend session history.
- Files created by agent tasks and provider caches or diagnostics are outside
  the session-transcript guarantee. No claim covers remote provider retention.

## 5. Frontend

### 5.1 Composer entry point

Same pattern as hybrid (`MessageInput.vue`, `hybrid*` refs and
`openHybridDialog('draft-enable')`):

- A discreet icon button in the composer toolbar, drafts only, hidden once
  the session exists. Tooltip "Start as an ephemeral session".
- First click opens an explainer `wa-dialog` (title "Start this session as
  ephemeral?", body: neither TwiCC nor the provider writes a local session transcript;
  this browser saves the prompt and final answer until Discard; no session
  history, no resume, and an interrupted connection can lose the answer) with a "Don't show again" switch persisted
  as a dedicated synced settings flag (like `claudeHybridExplainerSeen` /
  `setClaudeHybridExplainerSeen` in `stores/settings.js`), and a confirm
  button. Later clicks toggle directly.
- Store action `setDraftEphemeral(sessionId, value)` mirroring
  `setDraftHybrid` (draft-only, persisted to IndexedDB). Turning ephemeral on
  turns hybrid off and vice versa; the icons reflect it.
- Keyboard shortcut and command-palette entry: none in V1.

### 5.2 Sending

`MessageInput.sendMessage` adds `payload.ephemeral = session.value?.ephemeral === true`
for drafts, next to `payload.hybrid`. `payload.layout` is omitted for
ephemeral drafts.

In the store send flow (`sendMessage` → `registerInflightSend` …):

- The optimistic user message and the optimistic `STARTING` process state are
  set as today.
- The draft entry is **promoted in place**: `draft` removed, `ephemeral: true`,
  `ephemeralPhase: 'running'`, `ephemeralStartedAt`, and
  `ephemeralPrompt: { text, attachments }` where `attachments` is the
  lightweight summary the optimistic message renders (names/types, not the
  blobs — the draft medias are deleted from IndexedDB on send). Define the
  summary as `{name, media_type, kind: "image" | "document"}`. Render it
  through a summary-aware user-message builder, not SDK image helpers that
  require `source.data`. Both initial and rehydrated prompts use that builder. The prompt is
  persisted because the optimistic user message lives only in
  `localState.optimisticMessages` and the in-flight snapshot is dropped on
  `send_ack`; without it a reload shows an answer with no question.
- It is saved to IndexedDB in the drafts store. **Both the writer and the
  hydrator are whitelists today** and must change: `_saveDraftToIndexedDB`
  returns early unless `s.draft` and writes a fixed field set; it accepts
  `draft || ephemeral` and adds `ephemeral`, `ephemeralPhase`,
  `ephemeralStartedAt`, `ephemeralPrompt`, `ephemeralResult`.
  `hydrateDraftSessions` currently forces `draft: true`; it must preserve the
  stored `draft` flag and restore those fields. An unsent draft with ephemeral
  mode enabled stays `draft: true`. An ephemeral entry therefore comes back as an
  ephemeral entry, never as a sendable draft.
- The in-flight send registry entry is kept for error recovery. Today
  `failInflightSend` → `_applySendFailure` only clears the optimistic message,
  drops the optimistic STARTING state and materialises the failed-send bubble
  (a normal draft stays `draft: true` through the send; only its IndexedDB row
  is deleted, `deleteDraftSession(id, { keepInStore: true })`). The ephemeral
  branch of `_applySendFailure` is new logic: (1) set `draft: true` back,
  (2) delete the phase, timestamps, prompt and result fields but retain
  `ephemeral: true`, (3) re-save the draft row to IndexedDB,
  (4) keep the failed bubble as today. The recovery draft receives a fresh
  UUID for every refused or failed creation attempt. `FailedSendBanner` Retry uses
  the same creation payload/promotion helper as the composer. It preserves
  ephemeral mode and omits layout. No error recovery may launch a persistent
  run accidentally.
- **No redirect.** `session_bound` handling: when the local entry under
  `draft_session_id` is ephemeral, the store moves it to `session_id`
  (`sessions`, IndexedDB entry, MRU slot, optimistic message, process state,
  title suggestion — the same rekeys `bindDraftSession` already performs, plus
  the entry itself, in-flight snapshots and their IndexedDB copies, failed
  sends, and pending Stop/Discard intentions) and `router.replace`s to the canonical id. No
  `pendingDraftBindings` wait: the canonical session will never arrive.
- The reconciliation sweep and the in-flight audit skip ephemeral ids exactly
  as they skip drafts (they compare against the server's session list; an
  ephemeral id is never there).

### 5.3 Session view

Throughout the frontend, a **launched ephemeral entry** means
`session.ephemeral === true && !session.draft`. An unsent ephemeral draft
retains its composer, settings and draft actions. Use this distinction for
all read-only restrictions, rendering, sidebar phases and reconnect handling.

`SessionItemsList.vue` renders the launched ephemeral entry (it already owns the
callouts, the items list, the pending-request form and the composer slot):

- Never fetches items (extend the existing draft guard to `session.ephemeral`).
- A `wa-callout` at the top, by phase:
  - `running` (info, spinner): "Ephemeral session running. TwiCC and &lt;provider label&gt; do not save a
    local session transcript. This browser keeps your prompt and final answer
    until you discard them." The provider label comes from `getProviderLabel(session.provider)`
    ("Claude Code" / "Codex"): the notice must name both sides and distinguish browser persistence.
    This feature does not promise remote-provider data retention controls or
    prevent files that the agent creates to perform the requested task.
  - `done` (success): "Ephemeral session finished." + cost/duration when
    known.
  - `error` (danger): "Ephemeral session failed." + the error.
  - `stopped` (neutral): "Ephemeral session stopped."
  - `lost` (warning): "This ephemeral run is no longer available. Its answer was not received."
- Visual items: the optimistic user message (existing), the
  `WORKING_ASSISTANT_MESSAGE` placeholder while `running` (existing, driven by
  process state), the pending-request form when a permission/question is
  pending (existing), and the final answer as
  `SYNTHETIC_ITEM.EPHEMERAL_RESULT = { lineNum: -400, kind: 'ephemeral-result' }`
  (free slot, sorts after the `-500` working placeholder). The injected item
  follows `setOptimisticMessage`'s shape: `kind: 'assistant_message'` (the
  renderer key), `syntheticKind` from the constant, `display_level: ALWAYS`,
  parsed content set through `setParsedContent` in the provider's native
  assistant-message layout (a new `helpers.buildEphemeralResultContent(text)`
  next to `buildOptimisticUserMessageContent`), so the normal assistant
  components render it (markdown, copy, etc.). The `syntheticKind`
  propagation loop in `computeVisualItems` (explicit `else-if` chain on known
  lineNums) gets one more branch for `-400`.
- `stream_block_*` frames for an ephemeral id are ignored (no live text —
  decision §1).
- The composer (`<MessageInput>`) is not rendered once the entry is ephemeral;
  a `EphemeralActionsBar.vue` takes its place: **Stop** while running
  (existing `stopSessionProcess`), **Discard** always.
- The view is a single pane: the session layout / dock system is bypassed for
  ephemeral entries (`SessionView.vue` renders the items list directly). Files,
  Terminal, Browser, Plan, Tasks, Artifacts, Workflows tabs are not offered.
- Title: `needs-title` still opens the rename dialog; for an ephemeral entry
  the dialog writes the local title (the draft path) and never calls
  `renameSession` (REST PATCH). Title suggestion (`suggest_title`, a
  throwaway call that carries the prompt) keeps working.
- `session_viewed` pings, unread state, `last_viewed_at`: skipped for
  ephemeral entries as for drafts.

### 5.4 Receiving the answer

`useWebSocket.js` handles `ephemeral_result`: the store sets
`ephemeralPhase` from `status`, stores `ephemeralResult = { text, error,
cost_usd, duration_ms, finished_at }` on the entry, saves it to IndexedDB, and
recomputes visual items. The `process_state` → `dead` frame that follows
clears the process indicator as today.

Notifications: `notifyProcessStateChange` uses the entry's local title and
"Ephemeral session finished" wording for the user-turn toast/sound/browser
notification, and skips `forceNotifySessionViewed`.

### 5.5 Sidebar

- Ordering comes from `sessionSortComparator` (`stores/data.js`), not from
  the classification: it gets a rule 0 — ephemeral entries first, among
  themselves by `ephemeralStartedAt` descending — before the pinned rule.
  `SessionList.vue`'s `separatorBeforeIds` then labels them with a new section
  key `n-ephemeral` ("Ephemeral") ahead of `n-pinned`. Ephemeral entries are
  already part of `natural` (they live in `sessions` with a `project_id`, like
  drafts); `utils/sidebarSessions.js` excludes drafts from the cross-filter
  "Active elsewhere" block and must exclude ephemeral entries the same way.
- `SessionListItem.vue`: an "Ephemeral" `wa-tag` (like the "Draft" tag), phase
  colour on the process dot, no cost/meta row, context menu reduced to
  Discard (+ Stop while running).
- `SessionSelectionBar.vue`: ephemeral entries are excluded from pin/archive/
  hide batches and included in the "delete drafts" batch (relabelled "Discard").

### 5.6 Persistence and reload

- Entries persist in the drafts IndexedDB store with their phase, prompt and
  result (§5.2). At hydration the store re-creates the optimistic user
  message from `ephemeralPrompt` with its summary-aware builder and, when a result
  is stored, the result item (§5.3).
- On WebSocket (re)connect, after `active_processes`: first bind local draft
  ids using `extra.ephemeral_draft_id`. Then every launched ephemeral entry
  in `running` phase whose canonical id is absent becomes `lost`.
  Unsent ephemeral drafts are excluded. The snapshot also includes
  `ephemeral_starting`, with draft/provider/project ids for pending admissions.
  An entry present there remains running until binding or a later snapshot.
  Construct the agents and admissions portion from one final synchronous read
  after asynchronous snapshot enrichment, so stale pre-enrichment state cannot
  mark a newly started run lost. This
  covers a backend restart and a process that died while the tab was closed.
- **Discard**: `stopSessionProcess` if running, then remove the entry
  (store, IndexedDB, MRU) and navigate away — the `session.delete-draft`
  command path, generalised. Also clear optimistic/result items, in-flight
  sends and their stored snapshots, failed sends, title suggestions and local
  view state. Late frames cannot recreate a discarded entry.
- Stop or Discard can precede `session_bound`. Persist a small pending control
  record keyed by draft id. Stop is retried against the canonical id when
  binding or the active snapshot provides it. A Discard record is a tombstone:
  it stops the canonical run without recreating its entry. Keep records across
  reload until binding plus terminal state, or a later authoritative snapshot
  confirms absence after creation has settled. A snapshot during admission
  alone cannot prove that a pending run will never start.

### 5.7 The `!s.draft` audit

Every guard that reads `!s.draft` / `session.draft` to mean "a real session"
is reviewed for ephemeral entries. Known sites: `SessionView.vue` command
`when()` guards (rename, archive, pin, hide, share, fork, mark unread, stop),
`SessionHeader.vue` (eleven `!session.draft` guards on header actions),
`SessionListItem.vue` (`canToggleReadState`, menu actions, meta row),
`SessionSelectionBar.vue`, `SessionList.vue` search label,
`utils/sidebarSessions.js` (cross-filter active block), `stores/data.js`
(`_saveDraftToIndexedDB`, `hydrateDraftSessions`, `deleteDraftSession`,
`setDraftHybrid`, `sessionSortComparator`), `utils/sessions.js`
`isSessionUnread` (guards on `draft` only → also skip `ephemeral`), the
`process_state` handler's optimistic `last_new_content_at` write on leaving
`assistant_turn` in `useWebSocket.js` (skip for ephemeral, or the entry reads
as unread once dead), the `interrupt_session` command / Escape binding (hidden
for ephemeral), `useReconciliation.js` / `reconcileSweep.js` (already skip:
they only touch sessions with `itemsFetched`), the in-flight audit (same
guard), `MessageInput.vue` settings-apply logic, and the `session_updated`
handler in `useWebSocket.js` (an update for an ephemeral id can never arrive;
assert-log if it does). The implementation plan lists each site with its
expected outcome.

## 6. What is lost (accepted)

Transcript, tool calls, diffs, todos, plan, subagents, resume, second message,
aggregated cost (`WeeklyActivity` / `DailyActivity` / `Session.total_cost`
come from JSONL compute), search index, share, cron, fork, hide, `/goal`
(Codex), recovery of a running agent across a backend restart, Codex per-run cost (V1).

## 7. Out of scope (V1)

- `twicc create-session --ephemeral` (CLI / agent-created ephemeral runs).
- Ephemeral children in an orchestration tree (`spawned_by`).
- Live text streaming into the ephemeral view.
- Codex cost via `thread/tokenUsage/updated`.
- Aggregating ephemeral cost into activity stats.

## 8. Risks and open points

1. **Claude `ResultMessage.result` on a held turn.** When background agents
   or a wake-up hold ASSISTANT_TURN, several `ResultMessage`s arrive; the
   design keeps the last one. To confirm during implementation that the last
   one is the user-facing answer and not an internal continuation summary.
2. **Codex `agentMessage` granularity.** A turn may complete several
   `agentMessage` items. Prefer the explicit final-answer phase. Verify
   extraction on a real multi-message turn with a controlled local model server.
3. **Reused draft id after Discard.** The manager's `_ephemeral_ids` refusal
   also blocks creating a *normal* session with that id in the same backend
   process. Drafts mint fresh UUIDs, so this only bites a hand-crafted id.
4. **`process_state` before `session_bound`** cannot happen (`notify_session_bound`
   runs before `_register_and_start`), but the frontend's `loadSessionById`
   fallback for an unknown id must stay harmless (404 → `null`, no-op) if
   ordering ever changes.

## 9. Test plan

Backend (`uv run pytest`):

- Service: `ephemeral=True` rejects hybrid / worktree / spawned_by / a Codex
  hardcoded command / a known ephemeral id (before any stashing — assert the
  buffers stay empty); composes the ephemeral addendum; forwards a
  `SendDeliveryError.code`.
- Manager (fake agent): factory/binding/start failures drain buffers; duplicate
  admission is serialized; cross-provider and flagless replay are refused.
  Ephemeral start creates no `ProcessRun`, registers the
  agent, pops the title before `start()` and the other two buffers after
  `start()` under both ids (a fake Claude-like agent reads the addendum inside
  `start()` and must still see it), emits exactly one `ephemeral_result`
  (`done` on USER_TURN, `error` on DEAD-first, `stopped` on `manual`/`force`,
  none on `shutdown`, `stopped` on a soft-interrupted USER_TURN), schedules —
  not awaits — the kill after `done` (the USER_TURN callback returns before
  the agent dies), refuses a second `create_session` on the id.
- Claude agent options: `no-session-persistence` present, no MCP config file
  written, `plugins=[]`, `add_dirs=[]`; cron tools/hooks do not persist rows;
  debug/error/stderr paths do not write content.
- Codex manager: `thread_start_with_policy` called with `ephemeral=True`, no
  injected TwiCC MCP server, no work dirs; all SDK debug logs are suppressed;
  the globally installed TwiCC plugin is disabled for this run only.
- `compose_addendum(ephemeral=True)` content.

Frontend (`cd frontend && npm test`):

- Store: unsent ephemeral draft reload; Retry retains mode; attachment summary
  rendering; startup Stop/Discard, reload before binding, late error after
  binding, and complete discard cleanup. Snapshot during suspended factory
  preserves the running entry via pending admissions; a subsequent factory
  failure reaches the replacement socket through `ephemeral_admission_failed`. Also draft → ephemeral promotion, `session_bound` re-key, `ephemeral_result`
  phases, `lost` marking from an `active_processes` snapshot, discard cleanup,
  `failInflightSend` restoring the draft, IndexedDB round-trip of an ephemeral
  entry (writer + hydrator: comes back ephemeral with prompt and result, never
  as a draft), visual items after hydration (user message + result).
- `sessionSortComparator` places ephemeral entries first; the sidebar
  classification labels them.

Runtime verification must exercise provider-created subagents and check that
no main or child session JSONL appears. Use isolated provider homes and
controlled local model endpoints where supported. Do not restart the user
instance for validation.

Manual E2E, both providers: send with an attachment, answer a permission
prompt, stop mid-run, discard, reload during the run, restart the backend
during the run.

## 10. Adversarial review record (2026-09-07)

Three independent internal reviewers inspect backend lifecycle, frontend
state, and provider persistence. Final verdicts: PASS in all three scopes.
Corrections cover admission races, browser storage, retry identity, startup
controls, debug logging, cron persistence, inherited plugins/MCP, and final
answer selection. Local Codex 0.153.4 probes verify plugin exclusion and
inherited MCP disabling without model requests. Full runtime persistence
checks remain implementation acceptance gates, not completed evidence.
