# Working tools broadcast — design

**Date:** 2026-04-25
**Status:** Proposed

## Summary

Replace the front-end's heuristic "Claude is …ing" wording with a backend-driven feed of currently-active tools. The backend tracks tools via the Claude SDK's `PreToolUse` and `PostToolUse` hooks and broadcasts the live list over a new `process_tools` WebSocket message. The front-end groups tools by verb and renders one phrase, reusing the same per-tool summary logic that already drives the per-tool card. This makes the status line accurate, makes parallel-tool sessions intelligible, and removes a fragile JSONL walk-back from the store.

## Problem

Today the working-status line ("Claude is reading…", "Claude is writing…", etc.) is computed entirely client-side from the JSONL stream:

- `frontend/src/stores/data.js` (lines 1421-1443) walks backwards through items inside `assistant_turn` to find the most recent `tool_use` block, and tracks whether its result has already arrived (`toolUseCompleted`).
- `frontend/src/components/items/WorkingAssistantMessage.vue` derives a gerund (`reading`, `writing`, …) from `tool_use.name` and `tool_use.input.subagent_type`. A 5-second fallback timer keeps the gerund visible after a tool completes.

The per-tool card lives at `frontend/src/components/items/content/ToolUseContent.vue` and is referenced as `ToolUseContent.vue` throughout this document.

This approach has several issues:

1. **Indirect signal.** The walk-back scans content blocks instead of consuming the SDK's actual lifecycle events, which is brittle and makes parallel-tool support impossible.
2. **No parallel-tool awareness.** When Claude launches several tools at once (e.g. multiple `Read`s), the heuristic shows only the most recent and never the combined activity.
3. **Latency tied to JSONL.** The phrase only updates once Claude has finished writing the `tool_use` block to JSONL and the watcher has parsed it — strictly later than the tool actually starting.
4. **Duplicated per-tool knowledge.** `ToolUseContent.vue` (lines 416-572) computes summaries for Read/Write/Edit/Grep/Glob/WebFetch/WebSearch/ToolSearch/Skill/TodoWrite/Task; the working-status path computes a separate, simpler gerund. Future tools must be wired into both places.

The compacting state already takes a different, cleaner path: the SDK emits `SystemMessage(subtype="status", data={"status": "compacting"})`, the backend broadcasts a `process_label` WebSocket message, and the front sets `processState.label = "compacting"`. We extend that idea to all tools, with a richer payload.

## Goals

1. Drive the working-status phrase from a backend feed, not from a JSONL walk-back.
2. Support arbitrary numbers of parallel tools, with a phrase that combines them clearly.
3. Reuse the existing per-tool summary logic (`ToolUseContent.vue`) without duplication.
4. Preserve the existing compacting display.

## Non-goals

- **Per-tool card rendering.** The cards inside session views (`ToolUseContent.vue`) keep their current visuals. Only their internal computation is refactored to share code.
- **Tests.** No automated tests are added (project policy).
- **History rendering.** The phrase shows live activity. Once a tool completes, the per-tool card already renders its own history; the status line returns to "thinking".
- **Backend-side phrase formatting.** All wording (gerunds, parentheses, commas, "thinking", "compacting" priority) is done client-side. The backend ships raw structured data only.
- **Compacting × tool overlap policy.** In practice compacting fires between turns, never during a tool. We treat compacting as a strict override (`label === "compacting"` wins over any active tools list) and don't model interleaving.
- **Replacing `process_label`.** Compacting keeps using `process_label`; the new feed (`process_tools`) is independent.

## Architecture

```
┌──────────────────────────────────────────┐         ┌─────────────────────────────────┐
│ Backend                                  │         │ Frontend                        │
│                                          │         │                                 │
│  ClaudeProcess (process.py)              │         │  useWebSocket.js                │
│   ├─ _active_tools: dict[str, dict]      │         │   ├─ case 'process_tools'       │
│   ├─ PreToolUse  closure ───────────────►│         │   │   └─ debounced apply        │
│   ├─ PostToolUse closure ───────────────►│         │   └─ case 'process_label'       │
│   ├─ filter_tool_input(name, input)      │  WS  ─► │       (compacting, unchanged)   │
│   └─ _broadcast_process_tools()          │         │                                 │
│                                          │         │  stores/data.js                 │
│  tool_label_filter.py                    │         │   processStates[sid] gains:     │
│   └─ INPUT_DENYLIST                      │         │     tools: [], _toolsClearTimer │
│                                          │         │                                 │
│                                          │         │  utils/toolSummary.js (new)     │
│                                          │         │   ├─ getVerb(name, input)       │
│                                          │         │   ├─ formatRelativePath         │
│                                          │         │   ├─ getDisplayName             │
│                                          │         │   └─ computeToolSummary         │
│                                          │         │                                 │
│                                          │         │  ToolUseContent.vue             │
│                                          │         │   └─ uses computeToolSummary    │
│                                          │         │                                 │
│                                          │         │  WorkingAssistantMessage.vue    │
│                                          │         │   └─ phrase = render(tools)     │
└──────────────────────────────────────────┘         └─────────────────────────────────┘
```

The broadcast pipeline reuses the existing Channels infrastructure (`channel_layer.group_send("updates", …)` → consumer `broadcast` → WS `send_json`), exactly like `process_label` and `process_state`.

## Backend

### Tool tracking

`ClaudeProcess` gains an instance attribute:

```python
self._active_tools: dict[str, dict[str, Any]] = {}
# tool_use_id → {"name": str, "input": dict}
```

Reset to `{}` at the start of every `start()`/`resume()` so a crashed hook from a previous run cannot leak entries.

The SDK exposes `PreToolUse` and `PostToolUse` hooks via `HookMatcher`. Today only:

- `PreToolUse` (matcher `None`) at `process.py:41-51` — module-level `_pre_tool_use_hook` that captures the original file content for `Edit`/`Write`.
- `PostToolUse` (matcher `"CronCreate|CronDelete"`) at `process.py:831` — closure inside `start()`.

The new design replaces the `PreToolUse` hook with a closure inside `start()` (so it can access `self`), preserves the original-file capture, and adds a catch-all `PostToolUse` closure:

```python
async def _pre_tool_use(input_data, tool_use_id, context):
    tool_name = input_data.get("tool_name", "") or ""
    if tool_name in ("Edit", "Write"):
        _capture_original_file(input_data, tool_use_id)
    if tool_use_id:
        tool_input = input_data.get("tool_input", {}) or {}
        self._active_tools[tool_use_id] = {
            "name": tool_name,
            "input": filter_tool_input(tool_name, tool_input),
        }
        await self._broadcast_process_tools()
    return {"continue_": True}

async def _post_tool_use(input_data, tool_use_id, context):
    if tool_use_id and tool_use_id in self._active_tools:
        self._active_tools.pop(tool_use_id, None)
        await self._broadcast_process_tools()
    return {"continue_": True}
```

Hook registration becomes:

```python
hooks={
    "PreToolUse": [HookMatcher(matcher=None, hooks=[_pre_tool_use])],
    "PostToolUse": [
        HookMatcher(matcher=None, hooks=[_post_tool_use]),
        HookMatcher(matcher="CronCreate|CronDelete", hooks=[_on_cron_tool]),
    ],
}
```

The cron hook continues to do its own work alongside the catch-all.

### Input filtering

A small dedicated module `src/twicc/agent/tool_label_filter.py` exposes:

```python
INPUT_DENYLIST: dict[str, frozenset[str]] = {
    "Edit":      frozenset({"old_string", "new_string"}),
    "MultiEdit": frozenset({"edits"}),
    "Write":     frozenset({"content"}),
}

def filter_tool_input(tool_name: str, tool_input: dict) -> dict: ...
```

The denylist drops fields that are large *and* useless for the status line. Anything not listed flows through unchanged. `TodoWrite`'s `todos` is intentionally **kept** because the front-end's summary rendering uses it.

The denylist is incomplete by design: only the tools we know about today are listed. A new tool with a large, useless input field will pass through verbatim until we add an entry. This is acceptable — payloads stay JSON, the WebSocket layer handles them, and the user observation drives the next addition.

### Broadcast contract

New message type `process_tools` on the existing `/ws/` channel:

```json
{
  "type": "process_tools",
  "session_id": "<uuid>",
  "tools": [
    { "id": "toolu_…", "name": "Read", "input": { "file_path": "/abs/path/foo.py" } },
    { "id": "toolu_…", "name": "Edit", "input": { "file_path": "/abs/path/bar.py" } }
  ]
}
```

Properties:

- One frame is sent per `_active_tools` mutation (insert or remove). Empty list (`"tools": []`) is sent when the last tool completes.
- The list reflects the **full** current state, not a delta. The front always replaces, never merges. This matches the existing `claude_settings_presets_updated` / `terminal_config_updated` patterns in this codebase (full state on every change).
- `id` is the SDK's `tool_use_id`. The front does not currently use it for matching; it is preserved for future needs (e.g. cancelling, agent linking).
- `input` has been filtered through `filter_tool_input(name, input)`.

### Compatibility with `process_label`

`process_label` is unchanged. Compacting still emits `_broadcast_process_label("compacting")` from the message loop's `_is_compacting_status` branch. The two channels coexist; the front decides priority.

## Frontend

### Shared tool-summary utility

A new pure module `frontend/src/utils/toolSummary.js` owns *all* per-tool-name knowledge. It must not import any Vue or store module — keeping it pure prevents the HMR circular-import issues called out in `CLAUDE.md`.

Exports:

- `formatRelativePath(path, baseDir)` — make a path relative when it lives under `baseDir`, otherwise return it unchanged.
- `getDisplayName(name, input)` — `{name, namespace}` override for `Task` (subagent_type) and `Skill`, `null` otherwise.
- `getVerb(name, input)` — gerund form. Same rules as the current `WorkingAssistantMessage.vue:43-66`: Task/Agent → `"exploring" | "planning" | "bashing" | "agenting"` from `subagent_type`; `mcp__server__tool` → `"mcping (server)"`; otherwise lowercase name, strip trailing vowels, append `"ing"`.
- `computeToolSummary(name, input, baseDir)` — the main entry point. Returns:

```
{
  displayName: { name, namespace } | null,
  inline:      string | null,           // flat text used in parentheses for multi-tool rendering
  rich: {
    kind: 'description' | 'skill' | 'grep' | 'glob' | 'webFetch'
        | 'webSearch' | 'toolSearch' | 'todo' | null,
    description:        string | null,
    fileIconSrc:        string | null,
    skill:              { name, namespace } | null,
    grep:               { pattern, fileType, path, pathIconSrc } | null,
    globPattern:        string | null,
    webFetchUrl:        string | null,
    webSearchQuery:     string | null,
    toolSearchQuery:    string | null,
    todoDescription:    Array<{ text, status }> | null,
  }
}
```

`rich` is consumed by the per-tool card; `inline` is consumed by the working-status line. Mappings:

| Tool | `inline` |
|---|---|
| Read / Write / Edit | relative file path |
| Bash | `input.description` if present, else `null` |
| Skill | the display name |
| Grep | `"<pattern> in <type|glob> files in <path>"`, skipping null parts |
| Glob | `pattern` |
| WebFetch | `url` |
| WebSearch / ToolSearch | `query` |
| TodoWrite | `null` (no inline summary by design) |
| Task / Agent | `null` (verb already encodes the subagent_type) |
| MCP | `null` (verb already encodes the server) |
| Any other tool | `input.description` if present, else `null` |

### `ToolUseContent.vue` migration

The roughly ten per-tool computeds at lines 416-572 (`description`, `usesFilePath`, `fileIconSrc`, `skillDescription`, `grepParts`, `globPattern`, `webFetchUrl`, `webSearchQuery`, `toolSearchQuery`, `todoDescription`, `taskDisplayName`, plus the local `capitalize` helper) are replaced by a single `summary = computed(() => computeToolSummary(props.name, props.input, sessionBaseDir.value))`. The template is re-targeted to `summary.rich.*` and `summary.displayName`.

Behavior outside the summary scope (auto-open Edit/Write diffs, tool result fetching/polling, agent-link wiring, file-change stats, View-in-Files button, error rendering, code-comments indicator, tool-running spinner) is unaffected. There must be no visible regression on existing sessions.

A thin local `usesFilePath` computed stays, since `canViewInFilesTab` and `shouldAutoOpen` consume it.

### Process-state shape

Each entry in `processStates[sessionId]` (initialized in `setProcessState` and `setActiveProcesses`) gains two fields:

```js
tools: [],            // Array<{id, name, input}>
_toolsClearTimer: null  // setTimeout handle, internal
```

`tools` is the live list; the underscore-prefixed `_toolsClearTimer` is internal bookkeeping for the debounce.

### WebSocket handler & debounce

`useWebSocket.js` adds a new branch alongside `case 'process_label'`:

```js
case 'process_tools': {
    const ps = store.processStates[msg.session_id]
    if (!ps) break
    const tools = Array.isArray(msg.tools) ? msg.tools : []
    if (tools.length === 0) {
        if (ps._toolsClearTimer) break  // already pending
        ps._toolsClearTimer = setTimeout(() => {
            ps.tools = []
            ps._toolsClearTimer = null
        }, 1000)
    } else {
        if (ps._toolsClearTimer) {
            clearTimeout(ps._toolsClearTimer)
            ps._toolsClearTimer = null
        }
        ps.tools = tools
    }
    break
}
```

**Why the debounce is on the empty-list transition only.** Between `PostToolUse(A)` and `PreToolUse(B)` of two parallel tools, a brief frame can carry an empty list. Without the debounce the phrase would flash "thinking" between the two tools. A 1-second hold-off swallows that gap. A non-empty broadcast immediately cancels the pending clear and replaces the list, so the user never sees stale entries for tools that have actually completed.

### Working-message rendering

The synthetic `workingMessage` built in `data.js:1411-1466` simplifies. The walk-back loop (lines 1421-1443) is deleted. The `setParsedContent` call drops `toolUse` and `toolUseCompleted` and gains `tools`:

```js
setParsedContent(workingMessage, {
    type: 'assistant',
    syntheticKind,
    label: processState?.label || null,
    tools: processState?.tools || [],
    message: { role: 'assistant', content: [] },
})
```

`Message.vue` no longer passes `:tool-use` / `:tool-use-completed` to `WorkingAssistantMessage`; instead it passes `:tools` and `:session-id`.

`WorkingAssistantMessage.vue` is rewritten:

- Drops `toolAction`, `displayedAction`, the 5-second fallback timer, the `watch`, `onUnmounted`, `TASK_SUBAGENT_LABELS`, the `AGENT_TOOL_NAMES` import.
- Adds `tools` (Array) and `sessionId` (String) props.
- Computes `sessionBaseDir` from the data store (`session.git_directory || session.cwd`).
- Computes `phrase` with the priority: compacting > rendered tools > "thinking".

### Phrase rendering rules

```
0 tools  → "thinking"
1 tool   → "<verb>"                 (no parentheses)
2+ tools → join(", ", per_group)
   per_group(verb, []) = "<verb>"
   per_group(verb, [s1, s2, …]) = "<verb> (s1, s2, …)"
```

Grouping:

- Tools sharing a verb collapse into one group. Group order is determined by the order of first occurrence **within the current broadcast frame** — the front does not maintain a cross-frame cache of seen verbs.
- `inline` summaries that are `null` are dropped from a group's parenthesised list.
- A group whose summaries are all `null` (e.g. several Bash without `description`) renders as the verb alone.

Examples:

| Active tools | Phrase |
|---|---|
| Read foo.py | `Claude is reading...` |
| Read foo.py + Read bar.py | `Claude is reading (foo.py, bar.py)...` |
| Read foo.py + Edit bar.py | `Claude is reading (foo.py), editing (bar.py)...` |
| Bash (no description) | `Claude is bashing...` |
| Bash (no description) + WebFetch https://… | `Claude is bashing, fetching (https://…)...` |
| Read A + Read B + Read C + Edit D | `Claude is reading (A, B, C), editing (D)...` |
| (compacting) | `Claude is compacting...` |

The "no parentheses for a single tool" rule is intentional: when only one tool is active, the per-tool card directly above the working-status line already shows the file or summary, and parenthesising it again would be redundant.

## Trade-offs and risks

### Coverage gap if `Task` does not fire `PreToolUse`

The Claude Agent SDK fires `PreToolUse` for the regular tools we care about. It is **not** verified at design time that the `Task` tool follows the same code path inside the SDK; if it doesn't, sessions running `Task` will display "Claude is thinking…" while the agent runs, instead of "Claude is exploring…" / "planning…" / etc.

This is a regression compared to today's walk-back path, which catches `Task` because it's just another tool_use in the JSONL. We accept the risk because:

- The verification is a single manual test (start a `Task` and observe the `process_tools` frame).
- If the gap exists, the fallback ("Claude is thinking…") is correct, just less informative.
- A targeted follow-up — re-introducing a small walk-back specifically for `Task` — is straightforward.

The same caveat applies to MCP tools, which we believe go through `PreToolUse` but should be confirmed.

### Hook leak on crash

If a `PreToolUse` hook adds an entry but the matching `PostToolUse` never fires (SDK crash, hook timeout, kill mid-tool), `_active_tools` retains a stale entry. The reset at `start()`/`resume()` bounds the leak to a single process lifetime; we do not add per-tool watchdogs because a crashed process gets killed end-to-end and a new one starts with a clean dict.

### Filter denylist drift

`INPUT_DENYLIST` will lag the real set of "tools with large useless inputs". A new tool ships its full input until someone adds an entry. The mitigation is observation: if a payload is ever too big, add the field to the denylist; no protocol change is required.

### Front-end backwards compatibility

Old front-ends connecting to a new backend ignore unknown messages (`useWebSocket.js`'s switch falls through), so the only effect for an outdated client is "no working-status line for tools" — same as today. New front-ends connecting to an old backend simply never receive `process_tools`, and `processState.tools` stays an empty array, so the phrase is "Claude is thinking…" while tools run — degraded but functional.

## Out-of-band items

- Restarting the backend after the change is required (process hooks are bound at SDK connection time).
- A WebSocket message type is added to the implicit protocol; no client-side filter currently restricts message types, so no allow-list maintenance is needed.
- `CLAUDE.md` does not need a new section; the new utility module follows the existing "no circular imports" rule documented under "Avoiding Circular Imports".
