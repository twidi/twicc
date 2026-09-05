# Codex canonical format — end-to-end UI test scenario

**Date:** 2026-09-05
**Branch:** `codex-new-format` (Codex runtime 0.153.2, paginated rollouts)
**Goal:** after the rollout migration, prove that a live Codex session and the
rows the watcher writes for it render correctly in TwiCC — every tool card,
every live-only behaviour, and the same view after a reload and after a
recompute.

The scenario is built to be short: each prompt triggers several items of the
inventory at once. Everything below is derived from the code paths listed in
§1; §2 is the checklist, §3 the ordered scenario, §4 the database checks.

---

## 1. Where the behaviour lives

| Layer | Code | What it produces |
|---|---|---|
| Classification (kind, display level, pairing) | `src/twicc/providers/codex/compute.py` (module docstring lists every rule), `canonical.py` | `SessionItem.kind` / `display_level`, `ToolResultLink` (`tool_name`, `error`, `extra`), `AgentLink`, `Session.tasks`, `Session.plan_paths`, goal events, runtime fields, costs |
| Shared by the watcher and the compute | same `CodexSessionCompute`; the watcher calls it inline per new line (`sessions_watcher.py`), the compute worker calls it in bulk. Live-only hooks: `remap_tool_result_id_live`, `transform_tool_result_with_cache` (splices `original_files` into the stored `SessionItem.content`) on the compute, `CodexHelpers.enrich_live_items_payload` (attaches `stream_uuid`) on the provider helpers | identical rows either way — §3 step 14 verifies it. The recompute has no live agent, so it *preserves* the existing `error` / `extra` of a row rather than re-deriving user-terminated markers |
| Live agent — stream events | `src/twicc/providers/codex/agent/agent.py` `_handle_stream_event`: `error`, `item/started`, `item/agentMessage/delta`, `item/reasoning/summaryPartAdded`, `item/reasoning/summaryTextDelta`, `item/completed`, `item/autoApprovalReview/completed` | streaming blocks, working message, status label, original-files capture, subagent tracking |
| Live agent — server requests | `agent.py` `_sync_approval_handler` / `_async_approval_handler`, `agent/approvals.py`: `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `mcpServer/elicitation/request` (MCP tool approval, form, URL), `item/tool/requestUserInput` | pending-request forms |
| Live agent — TwiCC commands | `agent/hardcoded_commands.py` (`KNOWN_COMMANDS = {compact, goal, plan}`), `agent.py` `compact`, `run_goal_command`, `run_plan_command`, `soft_interrupt`, `current_status_label` | `/compact`, `/goal`, `/plan`, Stop, `compacting` / `waiting for N subagents` labels |
| Frontend readers | `frontend/src/providers/codex/canonical.js` (item accessors), `toolHelpers.js` (tool dispatch, result pick, spinner rules), `helpers.js` (optimistic message, attachments, capabilities), `parseCommand.js` (read / search / list_files variants), `codeModeDisplay.js`, `interAgentTask.js`, `proposedPlan.js` | what each card shows |
| Frontend renderers | `frontend/src/components/session/detail/items/codex/*.vue`: `Message`, `UserMessage`, `AssistantMessage`, `Reasoning`, `ToolUse`, `ExecResultContent`, `ReadResultContent`, `ApplyPatchContent` (+ `ApplyPatchFileEntry`), `ViewImageResult`, `ImageGeneration`, `SpawnAgentResult`, `PendingRequestBody`, `RequestUserInputBody`, `McpToolCallApprovalBody`, `AutoReviewDenialBody`, `PlanImplementationBody`; shared: `items/CompactSummary`, `items/TodoContent`, `items/ApiError`, `items/shared/ElicitationFormBody` / `ElicitationUrlBody`, `items/summary/GoalUpdateSummary`, `components/message/GoalBlock`; tabs `components/tasks/TaskPane.vue` (`Session.tasks`), `components/plan/PlanPane.vue` (`Session.plan_paths`) | the visible result |

Two tool dispatch generations coexist and both must be exercised
(`code_mode_script.py`, `docs/plans/2026-07-10-codex-code-mode-display-design.md`):

- **GPT-5.6 (gpt-sol / gpt-terra / gpt-luna) — code mode.** Almost every
  action is a `custom_tool_call name=exec` running a JS script that wraps the
  real tool (shell, apply_patch, MCP, update_plan, write_stdin), plus
  `function_call name=wait`. TwiCC rebinds the nested results
  (`exec-<uuid>` ids) onto the outer `exec` card. Observed in the migrated
  test copy for September 2026: 1400 `exec` calls, 9 `wait`,
  4 `request_user_input`, 2 `spawn_agent`.
- **GPT-5.5 (`gpt`) — native function calls.** `exec_command` +
  `write_stdin` chains, `shell` / `shell_command` / `local_shell_call`,
  native `apply_patch` (JSON or freeform), `update_plan`, `view_image`,
  `web_search_call`, MCP tools as `function_call` with `namespace` + `name`
  (stored as `namespace__name`). Image generation is `image_gen__imagegen`
  on 5.5 too (observed 2026-09-05); the older `image_generation_call` shape
  only survives in pre-0.15x rollouts.

Agent settings (`selected_model`, `effort`, `permission_mode`, `context_max`,
`fast_mode`) are **idle** settings: a change applies at the next turn, never
inside a running one (`constants.py` `IDLE`). `question_widget` is a
**startup** setting (needs a new process).

---

## 2. Inventory — what must be verified

Legend: **W** = written by the watcher (check in DB and after reload),
**L** = live-only (check while the turn runs), **F** = frontend rendering.
Display levels: ALWAYS = 1, COLLAPSIBLE = 2, DEBUG_ONLY = 3.

### 2.1 Messages

| # | Item | W | L | F | Expected |
|---|---|---|---|---|---|
| M1 | User message text | W | | F | `UserMessage` canonical item → kind `user_message`, ALWAYS; text joined from `text` entries; sidebar preview and title suggestion use it |
| M2 | User message with image attachment | W | L | F | optimistic bubble shows the thumbnail before the JSONL line lands; persisted `image` entry renders the same thumbnail; attachment-only prompt still visible |
| M3 | Assistant message | W | L | F | `AgentMessage` → `assistant_message`, ALWAYS; live: `stream_block_*` deltas paint the text progressively, then the persisted line replaces the placeholder without a flicker or duplicate |
| M4 | Reasoning | W | L | F | `response_item.reasoning` with a non-empty summary → `reasoning`, COLLAPSIBLE (empty summary → SYSTEM, hidden); live summary deltas stream into the collapsible block |
| M5 | Working / starting assistant placeholder | | L | F | shown while the turn runs, removed when the final message lands |
| M6 | Empty assistant message | W | | F | stays `assistant_message`, renders the "empty message" notice, never a blank bubble |
| M7 | Compaction | W | L | F | `/compact`: the agent injects a `/compact` user line (rewritten to a visible `user_message`), the status label reads `compacting`, the optimistic `/compact` bubble is retired on `manual_compaction_done`; the top-level `compacted` line → `compact_summary` ALWAYS (`CompactSummary`, encrypted placeholder) |

### 2.2 Tools — cards, results, spinners

| # | Item | W | L | F | Expected |
|---|---|---|---|---|---|
| T1 | Shell command, quick, exit 0 | W | L | F | tool card COLLAPSIBLE; result row DEBUG_ONLY paired by `call_id`; `extra.is_terminated` on the closing chunk stops the spinner |
| T2 | Shell command, non-zero exit | W | | F | native dispatch (5.5): `ToolResultLink.error` set from the exit code — `shell` / `local_shell_call` structured JSON `metadata.exit_code`, `shell_command` freeform output opening with `Exit code: N`, `exec_command` `Process exited with code N` trailer — card in error state. Code mode (5.6): a nested command exiting non-zero does **not** fail the script, `error` stays NULL unless the output opens with `Script failed` |
| T3 | Long-running shell (`sleep`) | W | L | F | 5.5: `exec_command` + chained `write_stdin` polls rebound to the parent (`remap_tool_result_id_live`), `write_stdin` calls are SYSTEM (no card), spinner until the closing chunk; 5.6: `exec` with "Script running with cell ID" then `wait` chunks rebound through the cell map |
| T4 | Read / search / list variants | | | F | `cat` → read view with file name, `rg`/`grep` → search view with query, `ls` → list view (`parseCommand.js`) |
| T5 | apply_patch, add + update + delete | W | L | F | `FileChange` item paired by `id`; `ApplyPatchContent` one block per file; `extra` = `lines_added` / `lines_removed` / `files`; the live capture splices `original_files` into the stored `SessionItem.content` → full-file diff with gutter numbers, still there after reload and after recompute |
| T6 | apply_patch on a plan-like doc | W | | F | `Session.plan_paths` gains the path (`plan_docs.py`) → Plan tab lists it; a deleted doc stays listed with `exists: false` |
| T7 | update_plan | W | L | F | `Session.tasks` refreshed → Tasks tab; card rendered as `Todo` |
| T8 | MCP tool call, native dispatch (5.5; TwiCC MCP, e.g. `whoami`) | W | | F | two result rows: `function_call_output` + canonical `McpToolCall`; the Result section shows the `McpToolCall` payload (`result.structuredContent`, else the whole `result` object, else `error.message`), not the raw text; `tool_name` stored as `mcp__twicc__whoami` (Codex namespaces MCP servers as `mcp__<server>`; the frontend's two-row rule keys on that prefix) |
| T9 | MCP tool call failing | W | | F | `McpToolCall.error.message` → `ToolResultLink.error` set and shown as-is (whitespace-trimmed); `result.isError` (or a message-less `error`) yields the generic `Tool error` label; the item's `status` is not consulted |
| T10 | Code-mode `exec` wrapping a single MCP / apply_patch / write_stdin | W | | F | nested result (`McpToolCall` / `FileChange` with an `exec-<uuid>` id) rebound to the `exec` card; the link's `tool_name` is `exec`; card summary shows the nested tool (`resolveCodeModeCall`), not the JS script. In a code-mode session this is how MCP calls arrive; the direct `mcp__` two-row form (T8) is the native-dispatch shape (5.5, and older 5.6 rollouts made before code mode) — the compute keys on the line shape, not on the model |
| T11 | `wait` (code mode) | W | | F | SYSTEM (no card), its output chunks land on the owning `exec` |
| T12 | Web search, 5.5: `web_search_call` | W | | F | resultless: card alone, no spinner, no link row. Only if `tools.web_search` is enabled in the Codex config |
| T12b | Web search, 5.6: `web__run` (`namespace=web name=run`) | W | | F | direct form: a normal paired tool, one result row, spinner until it lands. Wrapped form (the common one in code mode): the call sits in an `exec` script, the link row is the `exec`'s own output (`tool_name` = `exec`). Both cards are labelled Web search / Web fetch from the arguments (`describeWebRun`, nested branch of `resolveCodeModeCall`) |
| T13 | `view_image` | W | | F | `ViewImageResult` shows the image inline |
| T14 | Image generation, 5.5: `image_generation_call` | W | | F | `ImageGeneration` item → kind `image`, ALWAYS: inline PNG, revised prompt, saved path. Only if the feature is enabled in the Codex config |
| T14b | Image generation, 5.6: `image_gen__imagegen` + `Extension kind=image_gen.generation` | W | | F | the `image` card comes from the Extension item in both forms (camelCase fields `revisedPrompt`, `savedPath`, `transparentBackground`, `failure`). Direct form: `function_call image_gen__imagegen` paired with its own output row. Wrapped form: the call sits inside an `exec` script and the Extension id is `exec-<uuid>`. In both forms the tool card reads "Image generation", summarises the prompt, and its Result disclosure shows the picture (`ViewImageResult`) — also when the generation outlived the script's `yield_time_ms` and the image arrived in a later `wait` chunk. Extension items are never tool results (`canonical_result_item` accepts only `FileChange` / `McpToolCall`), so the `image` card cannot be swallowed by a pairing — note which form you got |
| T15 | Interrupted turn | W | L | F | Stop button → `soft_interrupt`; in-flight `commandExecution` / `fileChange` items get `error = "User interrupted the turn"` (+ `extra.is_terminated`). Shell-family tools and code-mode `exec` also stop spinning once the process is back to USER_TURN (`isToolRunning` gate in `toolHelpers.js`), so a code-mode `exec` may keep `error` NULL and still stop; any other card stops only through `toolState.error` |

### 2.3 Approvals and questions (permission mode `auto` = `workspace-write` + `on-request`)

`auto` writes freely inside the workspace, the session's artifacts / scratch
dirs plus the orchestration tree's shared scratch (`writable_roots`), and `/tmp` / `$TMPDIR` (the vendored
`WorkspaceWriteSandboxPolicy` defaults `exclude_slash_tmp` /
`exclude_tmpdir_env_var` to false); anything else prompts.

| # | Item | L | F | Expected |
|---|---|---|---|---|
| A1 | Command approval or permissions request (`item/commandExecution/requestApproval`, `item/permissions/requestApproval`) | L | F | `PendingRequestBody` with the command / requested permissions; Approve / Deny; the message input shows the pending form |
| A2 | File change approval outside the writable roots (`item/fileChange/requestApproval`) | L | F | body enriched with the item payload → per-file diff preview before deciding |
| A3 | `request_user_input` | L | F | `RequestUserInputBody` form; the answer is sent back and the turn continues. Precondition: `question_widget` on at process start |
| A4 | MCP approval | L | F | `McpToolCallApprovalBody`; optional: needs an MCP server whose tools are not auto-approved — any `default_tools_approval_mode` other than `approve` may prompt; `approve` is what TwiCC sets on its own server to skip the prompt |
| A5 | Denied request | W | F | tool card shows the denial (`error` set); for apply_patch no `FileChange` row ever comes and the card must not spin forever |
| A6 | Permission mode change between turns | L | | switching to `yolo` while idle → next commands run without prompts |
| A7 | MCP elicitation (form / URL) | L | F | `ElicitationFormBody` / `ElicitationUrlBody`; optional, needs an MCP server that elicits |

### 2.4 Subagents (multi-agent v2, collaboration tools)

| # | Item | W | L | F | Expected |
|---|---|---|---|---|---|
| S1 | `spawn_agent` × 2 in parallel | W | L | F | `SubAgentActivity` item → `AgentLink`; `SpawnAgentResult` card with View Agent; spinner while running. No Stop button on the card nor on the subagent header (`canStopSubagent` is false: Codex has no stop RPC) |
| S2 | Status label | | L | | `waiting for 2 subagents` (or `waiting`) under the working message, only while the parent blocks on a collaboration `wait_agent` call or sits in the subagent hold; cleared when they finish |
| S3 | Subagent session | W | | F | separate `Session` type subagent with `slug` = nickname; opens at the top in its tab; running / idle state follows its `task_started` / `task_complete`; its opening `NEW_TASK` envelope (`response_item.agent_message`) renders in the user bubble (`interAgentTask.js`) |
| S4 | Completion | W | L | F | the `FINAL_ANSWER` inter-agent message is rebound as the spawn's second link row (`extra` NULL); the frontend expects 2 rows for a spawn, so the spinner stops on the second |
| S5 | Other collaboration tools | W | | F | `wait_agent` → SYSTEM, no card, no link row; mid-flight `MESSAGE` exchanges render as `send_message` tool cards on both the parent and the subagent side. Any other `collaboration__*` call (`list_agents`, `close_agent`, …) renders as a generic paired card. Stored `tool_name`s carry the namespace: `collaboration__spawn_agent`, `collaboration__send_message` |

### 2.5 TwiCC commands and goal

| # | Item | W | L | F | Expected |
|---|---|---|---|---|---|
| C1 | `/plan <task>` | W | L | F | plan collaboration mode; the user bubble reads `/plan <task>` (prefix restored by `_restore_plan_prefix`, live and after recompute); proposed plan → `PlanImplementationBody` with the Implement button; clicking it switches the thread back to Default mode, then runs the fixed `Implement the plan.` turn (no turn if the switch fails). A bare `/plan` injects a visible `/plan` user line |
| C2 | `/goal <objective>` | W | L | F | `GoalBlock` in the input area, `GoalUpdateSummary` cards, goal continuation across turns until a `thread_goal_updated` line — or, on 5.6 code mode, a successful Goal-tool result with a non-`active` status — completes it. A `get_goal` probe shows no card (DEBUG_ONLY) |
| C3 | `/goal clear` | W | | F | must target an **open** goal: send it while the continuation of a long `/goal` is still running (allowed mid-continuation). Private canonical `UserMessage` `/goal clear` visible, `Session.goals[-1].cleared` true, `GoalBlock` reads Stopped, session back to USER_TURN. On an already completed goal the command is a no-op by design (the goal is closed; the block shows Completed with a dismiss cross) |
| C4 | `/compact` | see M7 | | | |

### 2.6 Session-level data (sidebar, header, tabs)

| # | Item | W | Expected |
|---|---|---|---|
| D1 | Title | W | Codex thread name synced at boot (`_sync_titles_at_boot`) and once when the watcher first creates the session row; renaming in TwiCC calls `thread/name/set` (`titles.py`); TwiCC title suggestion works on the first exchange. An existing title is never overwritten by a compute (`initial_title_set` guard) |
| D2 | Sidebar preview, `user_message_count`, unread badge, `last_new_content_at` | W | update live on every exchange |
| D3 | Cost, context usage, context window | W | `event_msg.token_count` → `self_cost`, context bar moves; `Session.context_max` from `task_started.model_context_window`; model label from `turn_context` |
| D4 | Model / effort switch between turns | W | `Session.model` follows the next `turn_context`; picker constraints apply (`max` effort only on 5.6 tiers; `ultra` is disabled product-wide) |
| D5 | `cwd`, `cwd_git_branch` | W | from `session_meta`; branch shown in the header |
| D6 | Search | W | live indexing: a word from the new session is found in the search UI within seconds |
| D7 | Live share | | prerequisite: a share host configured in Settings > Sharing (the instance under test had none, so this item was skipped on 2026-09-05); on a fresh checkout run `cd frontend && npm run build` first (the share viewer bundle is not HMR'd and not committed); create a live share link; the viewer renders the canonical items and streams new ones |
| D8 | `<twicc:context>` hygiene | W | after a settings change, the next user bubble must not show the injected `<twicc:context>` block (scrubbed from the stored copy) |

### 2.7 Migration-specific continuity

| # | Item | Expected |
|---|---|---|
| G1 | Resume a migrated legacy thread | send a follow-up in an old session: appended paginated lines ingest incrementally, tool pairing works, no false rewrite detection (no `was rewritten … scheduling a rebuild` in the log) |
| G2 | Recompute parity | reset `compute_version` of the test session → coordinator picks it up within 30 s (COMPUTE_ONLY: no gate, no agent deferral, for a session without a snapshot-share anchor), spawns a fresh compute worker (the previous one was stopped at the end of the initial pass) and recomputes; rows and title identical before / after (§4.3). Also proves the coordinator is alive after the initial pass |
| G3 | Reload parity | after every step, F5 must show exactly the live view minus the streaming placeholders |

---

## 3. Scenario

### 3.0 Setup

- Worktree instance: backend 3502, Vite 5175, `CODEX_HOME` = the migrated
  copy. Codex 0.153.2 bundled.
- Test project: a throwaway git repo, e.g.
  `/home/twidi/.twicc/scratch/codex-ui-test/` with `git init` and one commit
  (gives `cwd_git_branch`). Create it before the first session.
- Two sessions from the TwiCC UI on that project, question widget on:
  - **S1** — model `gpt-terra` (5.6, code mode), permission mode `auto`,
    effort medium.
  - **S2** — model `gpt` 5.5 (native tools), permission mode `auto`.
- **S3** — an existing migrated session of your choice, any model.
- Keep `logs/backend.log` open (`tail -f`, filtered on
  `ERROR|WARNING|Traceback|rewritten|rebuild`).

After each step: check the live view, then F5 and compare (G3).

### 3.1 S1 — code mode (gpt-terra)

**Step 1 — first exchange, attachment, streaming.** Attach a small PNG and send:

> Describe the attached image in one sentence. Then create `README.md` with a title and two lines, and `docs/plans/2026-09-05-test-plan.md` with a three-item plan. Use apply_patch.

Covers: M1, M2, M3 (streaming), M4, M5, T5 (add), T6 (Plan tab), T10 (on 5.6 the patch always arrives wrapped in `exec`), D1 (title suggestion), D2, D3, D5.

**Step 2 — shell variants, long run, error.** Send:

> Run these one after the other, each as its own command: `ls -la`, `cat README.md`, `rg -n title README.md`, `sleep 25 && echo done`, `false`.

Covers: T1, T3 (long-running with `wait` chunks and spinner), T4 (list / read / search views), T11. `false` is here to confirm T2's code-mode expectation (no error state unless `Script failed`).

**Step 3 — patch update + delete, plan tool, MCP.** Send:

> Update README.md: replace the second line, delete docs/plans/2026-09-05-test-plan.md. Then call the TwiCC MCP tool `whoami` and tell me my session id. Track your steps with update_plan.

Covers: T5 (update with `original_files` full diff, delete), T7 (Tasks tab), T10 (the MCP call arrives wrapped in `exec`), T6 (Plan tab entry marked as no longer existing).

**Step 4 — approvals.** Send:

> Write the current date into `~/codex-ui-test.txt` (my home directory, outside this repo), then run `curl -sI https://example.com | head -1`.

Approve the file change, deny the command (the network call may arrive as a
permissions request instead of a command approval — both are A1). Covers:
A1, A2, A5. Then send:

> Ask me which colour I prefer, using the request_user_input tool, and wait for my answer before replying.

Covers: A3.

**Step 5 — subagents.** Send:

> Spawn two subagents in parallel: one counts the lines of README.md, the other lists the files in the repo. Wait for both and summarise.

Covers: S1, S2 (label visible while the parent waits), S3 (open both View Agent tabs), S4.

**Step 6 — commands.** Send `/goal Make README.md mention TwiCC in its first line`, let it complete (C2). Then `/goal Rewrite README.md into five sections, one section per turn, explaining each` and, within ~10 s while the continuation runs, `/goal clear` (C3). Then `/plan Add a CONTRIBUTING.md with three sections`, click Implement. Then `/compact`.

Covers: C1, C2, C3, M7.

**Step 7 — interrupt, permission mode, model switch.** Send `Run sleep 60 then echo done`, click Stop after a few seconds (T15). While idle, switch the permission mode to `yolo` and the model to `gpt-sol`, send `Run echo hello` (A6, D4: no prompt, model label updated, no `<twicc:context>` text in the bubble: D8).

### 3.2 S2 — native tools (gpt 5.5)

**Step 8.** Send the same prompts as steps 1 to 3 (attachment, files, shell variants, long run with `sleep 25`, `false`, patch update, update_plan, MCP `whoami`). Then send `Run sleep 60 then echo done` and click Stop within a few seconds, before the first `Process running with session ID` chunk returns (T15 on a native `exec_command`: `error = "User interrupted the turn"` on the in-flight item; if the first chunk already returned, the interrupted row is a `write_stdin` poll — record its `error`). Covers the native dispatch: `exec_command` + `write_stdin` chain (T3), `shell` structured JSON / `shell_command` freeform trailer (T1, T2 with `error` set on `false`, T4), native `apply_patch` with `FileChange` (T5), `update_plan` as `function_call` (T7), MCP as namespaced `function_call` with two result rows (T8).

**Step 9 — image and web tools (conditional on the Codex config).** Send, first in S2 then in S1 (the 5.6 shapes differ: T12b, T14b):

> Look at README.md's folder for any PNG and view it with view_image. Then search the web for "Codex CLI paginated rollouts" and give me one link. Then generate a tiny 256×256 image of a blue square.

Covers: T13, T12 / T12b, T14 / T14b (the image-generation shape changed with the migration: check inline image, revised prompt and saved path). If the model says a tool is unavailable, skip that item and note it.

### 3.3 S3 — a migrated session

**Step 10.** Open an old, already migrated session with tools in it. Check the history renders (cards, diffs, MCP results, images if any). Send:

> Summarise what we did in this session in two sentences, then run `git status`.

Covers: G1 — the reply and the tool card append live, pairing works, no `was rewritten` line in the log.

### 3.4 Cross-cutting

**Step 11 — search.** Search a distinctive word from step 1's README in the search UI (D6).

**Step 12 — share.** On a fresh checkout, build the frontend bundles first (`cd frontend && npm run build`). Create a live share of S1, open it in a private window, send one more message in S1, watch it arrive in the viewer (D7).

**Step 13 — reload parity.** F5 on S1, S2, S3; scroll through each; nothing must differ from the live view except the absence of streaming placeholders (G3).

**Step 14 — recompute parity.** With S1 idle: dump its rows (§4.3), reset its `compute_version`, wait for the log line `Codex rollout migration: session <id> metadata recomputed in …` (pickup ≤ 30 s, then worker spawn + compute), dump again, diff (G2). A manual SQL reset broadcasts nothing, so the UI only reacts when the apply's `session_updated` lands. Do not F5 between the reset and the log line: with `compute_version` NULL the session shows the computing placeholder and drops live items until the apply lands.

---

## 4. Database checks

Run from the worktree; `db/data.sqlite` is live, so use
`sqlite3 -cmd ".timeout 5000"` and read-only queries except the one reset in
§4.3. Columns used below: `core_toolresultlink(session_id, tool_use_line_num,
tool_use_id, tool_result_line_num, tool_name, error, extra)`,
`core_agentlink(session_id, tool_use_line_num, tool_use_id, agent_id)`.

### 4.1 Kinds and display levels of a session

```sql
select kind, display_level, count(*)
from core_sessionitem where session_id = :sid
group by 1, 2 order by 1, 2;
```

Expect `user_message` / `assistant_message` / `compact_summary` / `image` at
1 (ALWAYS), `tool_use` / `reasoning` at 2 (COLLAPSIBLE), tool results and
`system` at 3 (DEBUG_ONLY). No `NULL` kind at level 1.

### 4.2 Pairing and extras

```sql
-- every tool_use has at least one link (web_search_call: none), MCP calls have two
select tu.line_num,
       coalesce(json_extract(tu.content, '$.payload.namespace') || '__', '')
         || coalesce(json_extract(tu.content, '$.payload.name'), json_extract(tu.content, '$.payload.type')) as name,
       count(l.id) as links,
       group_concat(l.tool_name, ',') as link_tool_names,
       sum(l.error is not null) as errors,
       group_concat(l.extra, ' | ') as extras
from core_sessionitem tu
left join core_toolresultlink l on l.session_id = tu.session_id and l.tool_use_line_num = tu.line_num
where tu.session_id = :sid and tu.kind = 'tool_use'
group by tu.line_num order by tu.line_num;

-- subagent links
select tool_use_line_num, tool_use_id, agent_id from core_agentlink where session_id = :sid;

-- session-level payloads
select title, model, cwd, cwd_git_branch, self_cost, context_usage, context_max,
       json_extract(tasks, '$.items') as tasks, plan_paths, unavailable_reason
from core_session where id = :sid;
```

Expect: `extra` carries `is_terminated` on the closing shell chunk and on
`exec` completion; `lines_added` / `lines_removed` / `files` on
`apply_patch`; `collaboration__spawn_agent` rows have `extra` NULL and two links;
`error` set on `false` in S2 only (native exit-code surfaces; in S1 the
nested `false` leaves it NULL), on denied requests, on a failed MCP call
and on the `exec_command` interrupted at step 8; on the step-7 code-mode
`exec` it is either NULL or `User interrupted the turn` — record which, and
check that its spinner stopped (USER_TURN gate); `web_search_call` has no link row.

### 4.3 Recompute parity (step 14)

```bash
cd <worktree>
Q() { sqlite3 -cmd ".timeout 5000" db/data.sqlite "$@"; }
dump() { Q "
  select line_num, kind, display_level, group_head, group_tail from core_sessionitem where session_id='$1' order by line_num;
  select tool_use_line_num, tool_use_id, tool_result_line_num, tool_name, error, extra from core_toolresultlink where session_id='$1' order by 1,3;
  select tool_use_line_num, tool_use_id, agent_id from core_agentlink where session_id='$1' order by 1;
  select title, model, cwd, cwd_git_branch, self_cost, context_usage, context_max, tasks, plan_paths from core_session where id='$1';" ; }
dump "$SID" > /tmp/before.txt
Q "update core_session set compute_version = NULL where id='$SID'"
# wait for: "Codex rollout migration: session <SID> metadata recomputed in …" in logs/backend.log (≤ 30 s)
dump "$SID" > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt && echo "watcher == compute"
```

An empty diff proves the watcher's inline classification and the batch
compute agree on the canonical format. Tolerated: `self_cost` rounding.
`error` / `extra` match by preservation only when the recompute yields
`None` (rows whose live source is gone, e.g. user-terminated markers). A
denied `apply_patch` is the known exception: live, the link's `error` is
TwiCC's decision label (`User denied this action`); the recompute derives
`patch rejected by user` from Codex's output row and overwrites it. Same
fact, different wording — observed 2026-09-05, accepted.
`title` must be unchanged: the compute only extracts a first-message title
for a session that has none (`initial_title_set` guard in
`compute_base.py`). Observation on the test machine after run 4 (not reproducible from the
repo): all 593 named Codex threads still matched `Session.title`.

---

## 5. Out of scope here

- The unavailable-session notice (rollout missing / refused by Codex): covered
  by `tests/test_codex_migration_scheduler.py` and the real-binary
  integration tests; a manual check needs a stopped backend and a moved
  rollout file.
- The rewrite-detection path (external `codex migrate-rollouts` on a
  session TwiCC already holds): covered by unit tests. Step 14 does not
  exercise it: a manual SQL reset bypasses `MarkSessionRebuildJob`, so the
  `session_updated` broadcast added for it (`_broadcast_flagged_session`)
  is not observed either.
- Guardian auto-review denials (`AutoReviewDenialBody`): needs the guardian
  feature enabled in the Codex config.
- Provider error recovery (`twicc_provider_error` → `ApiError` with Resend,
  hidden `<twicc-resume>` message → SYSTEM): cannot be triggered on demand.
