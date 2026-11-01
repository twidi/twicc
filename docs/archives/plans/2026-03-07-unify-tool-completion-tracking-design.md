# Unify Tool Completion Tracking

## Problem

AgentLink duplicates data already available in ToolResultLink: `is_background`, `result_count`, `started_at`, `completed_at`. The Bash tool spinner (just implemented) already uses ToolResultLink as its source of truth. AgentLink should be simplified to only store the unique mapping it provides (tool_use_id → agent_id), and agent completion should be derived from ToolResultLink like Bash.

## Design

### Model Changes

**AgentLink** — remove `is_background`, `result_count`, `started_at`, `completed_at`. Keep only:
- `session` (FK)
- `tool_use_line_num`
- `tool_use_id`
- `agent_id`

Migration removes the 4 fields. Index on `(session, tool_use_id)` stays.

### Backend

**`AgentLinkUpdate`** — simplified to `(parent_session_id, agent_id, tool_use_id)`. No more `is_done`, `is_background`, `started_at`, `completed_at`. Emitted only when a new AgentLink is **created**.

**`BashToolUpdate`** — renamed to **`ToolResultUpdate`**. Same structure: `(session_id, tool_use_id, result_count, completed_at)`. Emitted for Bash, Task, and Agent tool_results (not all tools).

**`create_tool_result_link_live()`** — emits `ToolResultUpdate` for Bash + Agent tools. No longer emits `AgentLinkUpdate` on result receipt (that's only on link creation).

**Agent link creation functions** — simplified: no more `_increment_agent_link_result_count`, `mark_agent_link_done`, `is_agent_link_done` complexity around result_count. Just create the link and emit the update.

**Background compute** — AgentLink dicts simplified (no `is_background`, `result_count`, `started_at`, `completed_at`).

### API

**`GET .../subagents/`** — returns `[{ "tool_use_id": "...", "agent_id": "..." }]`. No more `is_done`, `started_at`, etc.

**`GET .../bash-tool-states/`** — renamed to **`GET .../tool-states/`**. Returns states for Bash + Agent tools (not all tools). Same format: `{ "tools": { "toolu_xxx": { "result_count": N, "completed_at": "..." } } }`.

### WebSocket Messages

**`agent_link_created`** (replaces `subagent_state_changed`):
```json
{ "type": "agent_link_created", "parent_session_id": "...", "tool_use_id": "...", "agent_id": "...", "project_id": "..." }
```
Sent when an AgentLink is created. Frontend populates `agentLinks` cache and creates a synthetic process state.

**`tool_state`** (replaces `bash_tool_state`):
```json
{ "type": "tool_state", "session_id": "...", "tool_use_id": "...", "result_count": 1, "completed_at": "..." }
```
Sent for Bash + Agent tool_results. Frontend updates `toolStates` cache. For agents, when `resultCount >= requiredCount`, frontend removes the synthetic process state.

### Frontend Store

- `bashToolStates` → renamed **`toolStates`**. Same structure.
- `getBashToolState` → renamed **`getToolState`**. Used by both Bash and Agent.
- `fetchBashToolStates` → renamed **`fetchToolStates`**. Calls `/tool-states/`.
- `fetchSubagentsState` → simplified: only populates `agentLinks` cache + creates synthetic process states (using `toolStates` to determine if agent is done).

### Frontend ToolUseContent

**Bash** — unchanged logic, uses `getToolState` instead of `getBashToolState`.

**Agent** — `isAgentRunning` changes source of truth. Instead of checking `processStates[agentId]?.synthetic`, uses the same pattern as Bash:
```js
const toolState = computed(() => dataStore.getToolState(props.sessionId, props.toolId))
const isToolRunning = computed(() => {
    const resultCount = toolState.value?.resultCount || 0
    const isBackground = !!props.input?.run_in_background
    return resultCount < (isBackground ? 2 : 1)
})
```

### Synthetic Process States

Kept for agents. Lifecycle:
- **Created**: on `agent_link_created` WS message, or on session load via `fetchSubagentsState` when `getToolState` shows agent not done.
- **Removed**: in `tool_state` WS handler, when `resultCount >= requiredCount` for an agent tool_use, look up `agentId` from `agentLinks` cache and call `removeSyntheticProcessState`.

Used by: `SessionHeader` (stop button guard), `ToolUseContent` (tooltip duration via `state_changed_at`).

### Compute Version

Bump `CURRENT_COMPUTE_VERSION` to trigger re-computation of AgentLinks without the removed fields.
