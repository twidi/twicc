# Working Tools Broadcast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the front-end's heuristic "Claude is …ing" wording with a backend-driven feed of currently-active tools (PreToolUse / PostToolUse SDK hooks) and shared tool-summary logic.

**Architecture:** Backend tracks active tools per `ClaudeProcess` in a dict keyed by `tool_use_id`; PreToolUse adds, PostToolUse removes; after every change a `process_tools` WebSocket frame carries the full live list (filtered to drop large fields). The front merges that into `processState.tools`, then `WorkingAssistantMessage.vue` renders one phrase by grouping verbs and reusing `computeToolSummary()` — the same utility now also drives `ToolUseContent.vue`.

**Tech Stack:** Python 3.13 (asyncio, claude-agent-sdk hooks, Django Channels), Vue 3 Composition API, Pinia.

**Spec:** `docs/superpowers/specs/2026-04-25-working-tools-broadcast-design.md` — read it before starting.

**Important:** Per CLAUDE.md, this project does **no automated tests** and **no linting**. Every "test" step in this plan is a manual smoke test in the browser or REPL. **No commits at any step** — the user will commit when ready.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/twicc/agent/tool_label_filter.py` | Create | `INPUT_DENYLIST` and `filter_tool_input(name, input)` — pure helper, no Django imports. |
| `src/twicc/agent/process.py` | Modify | Add `_active_tools` instance dict; convert `_pre_tool_use_hook` to a closure inside `start()`; add catch-all `_post_tool_use` closure; register both hooks; add `_broadcast_process_tools()`; reset `_active_tools` at every `start()`. |
| `frontend/src/utils/toolSummary.js` | Create | Pure utility module exporting `formatRelativePath`, `getDisplayName`, `getVerb`, `computeToolSummary`. Owns all per-tool-name knowledge. No Vue/store imports. |
| `frontend/src/components/items/content/ToolUseContent.vue` | Modify | Replace ~10 inline computeds with one `summary = computed(() => computeToolSummary(...))`. Template re-targeted to `summary.*`. Visual output unchanged. |
| `frontend/src/composables/useWebSocket.js` | Modify | New `case 'process_tools'` handler with 1-second anti-flicker debounce on the empty-list transition. |
| `frontend/src/stores/data.js` | Modify | Initialize `tools: []` and `_toolsClearTimer: null` in `setProcessState` and `setActiveProcesses`. Remove walk-back loop in the synthetic `workingMessage` builder. Inject `tools` into the working message's parsed content. |
| `frontend/src/components/items/Message.vue` | Modify | Replace `:tool-use` and `:tool-use-completed` props with `:tools` and `:session-id` on `WorkingAssistantMessage`. |
| `frontend/src/components/items/WorkingAssistantMessage.vue` | Modify | Drop `toolAction`, `displayedAction`, the 5-second timer, and the `AGENT_TOOL_NAMES`/`TASK_SUBAGENT_LABELS` constants. New computed `phrase` with priority compacting > rendered tools > "thinking". |

`frontend/src/constants.js` is left alone — `AGENT_TOOL_NAMES` is still consumed by `ToolUseContent.vue`.

---

## Task 1: Backend — Input filter helper

**Files:**
- Create: `src/twicc/agent/tool_label_filter.py`

- [ ] **Step 1: Create the module**

Write the file with exactly this content:

```python
"""Filtering rules for tool inputs broadcast as part of the active-tools status feed.

We strip large fields that are useless for the UI status line ("Claude is …") to
keep the WebSocket payload small. Anything not listed in INPUT_DENYLIST passes
through unchanged.
"""

from typing import Any

INPUT_DENYLIST: dict[str, frozenset[str]] = {
    "Edit": frozenset({"old_string", "new_string"}),
    "MultiEdit": frozenset({"edits"}),
    "Write": frozenset({"content"}),
}


def filter_tool_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``tool_input`` with large fields removed for the given tool."""
    deny = INPUT_DENYLIST.get(tool_name)
    if not deny:
        return dict(tool_input)
    return {k: v for k, v in tool_input.items() if k not in deny}
```

- [ ] **Step 2: Quick REPL sanity check**

Run, from the worktree root:

```bash
uv run python -c "
from twicc.agent.tool_label_filter import filter_tool_input
assert filter_tool_input('Edit', {'file_path': '/x', 'old_string': 'a', 'new_string': 'b'}) == {'file_path': '/x'}
assert filter_tool_input('Write', {'file_path': '/x', 'content': 'big'}) == {'file_path': '/x'}
assert filter_tool_input('MultiEdit', {'file_path': '/x', 'edits': []}) == {'file_path': '/x'}
assert filter_tool_input('Read', {'file_path': '/x'}) == {'file_path': '/x'}
assert filter_tool_input('Bash', {'command': 'ls'}) == {'command': 'ls'}
assert filter_tool_input('mcp__foo__bar', {'q': 'x'}) == {'q': 'x'}
print('OK')
"
```

Expected output: a single line `OK`.

---

## Task 2: Backend — Add `_active_tools` to `ClaudeProcess`

**Files:**
- Modify: `src/twicc/agent/process.py`

This task only adds the per-instance state and import. The hooks themselves come in Task 3.

- [ ] **Step 1: Add the import**

In the imports block of `process.py` (after the line `from .states import …` near line 33), add:

```python
from .tool_label_filter import filter_tool_input
```

`Any` is already imported at line 15 (`from typing import Any`); no further import is needed.

- [ ] **Step 2: Add the instance attribute**

Find `ClaudeProcess.__init__`. Look for the body that follows the docstring at line 92. Add this line alongside other transient per-process state (a sensible place is right next to other `_…` attributes; if no obvious neighbor jumps out, place it just before the closing of `__init__`):

```python
self._active_tools: dict[str, dict[str, Any]] = {}
```

- [ ] **Step 3: Reset `_active_tools` at session start**

In `start()` (around line 820), at the very beginning of the `try:` block (just before the thinking-config setup at lines 822-827), add:

```python
self._active_tools = {}
```

This guarantees no leak from a previous, possibly crashed, run.

- [ ] **Step 4: Smoke test the import only**

This change is internal. Verify the module still imports cleanly:

```bash
uv run python -c "from twicc.agent.process import ClaudeProcess; print('OK')"
```

Expected: `OK`.

---

## Task 3: Backend — Pre/Post tool-use hooks and broadcast

**Files:**
- Modify: `src/twicc/agent/process.py`

This task wires the hooks into the SDK and adds the broadcast method.

- [ ] **Step 1: Add the new hook closures inside `start()`**

Inside `start()`, immediately after the existing `_on_cron_tool` closure definition (currently at line 831, before the `extra_args = …` block at ~835), add **two** closures:

```python
async def _pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
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

async def _post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    if tool_use_id and tool_use_id in self._active_tools:
        self._active_tools.pop(tool_use_id, None)
        await self._broadcast_process_tools()
    return {"continue_": True}
```

`_capture_original_file` (currently called from the module-level `_pre_tool_use_hook` at line 41) stays unchanged at line 54; the closure simply takes over the call.

- [ ] **Step 2: Update the hook registration**

Replace the existing `hooks=…` block at lines 855-858 with:

```python
hooks={
    "PreToolUse": [HookMatcher(matcher=None, hooks=[_pre_tool_use])],
    "PostToolUse": [
        HookMatcher(matcher=None, hooks=[_post_tool_use]),
        HookMatcher(matcher="CronCreate|CronDelete", hooks=[_on_cron_tool]),
    ],
},
```

The catch-all PostToolUse runs on every tool; the `CronCreate|CronDelete` matcher continues to do its own cron persistence work alongside it.

- [ ] **Step 3: Delete the now-unused module-level `_pre_tool_use_hook`**

Delete the entire function at lines 41-51 (the closure has taken over). Keep `_capture_original_file` at line 54 — the new closure still calls it.

- [ ] **Step 4: Add `_broadcast_process_tools()`**

Right after `_broadcast_process_label` (line 1569), insert this new method:

```python
async def _broadcast_process_tools(self) -> None:
    """Broadcast the current list of in-progress tools for the status display."""
    channel_layer = get_channel_layer()
    tools = [
        {"id": tool_use_id, "name": entry["name"], "input": entry["input"]}
        for tool_use_id, entry in self._active_tools.items()
    ]
    await channel_layer.group_send(
        "updates",
        {"type": "broadcast", "data": {
            "type": "process_tools",
            "session_id": self.session_id,
            "tools": tools,
        }},
    )
```

- [ ] **Step 5: Module import smoke test**

```bash
uv run python -c "from twicc.agent.process import ClaudeProcess; print('OK')"
```

Expected: `OK`.

- [ ] **Step 6: Manual broadcast smoke test**

The user will need to **restart the backend** (per CLAUDE.md, that is a user action — remind them; do not run `devctl.py restart` yourself).

After restart, in the browser DevTools Network tab:
1. Filter for the `/ws/` WebSocket frames.
2. Send a prompt that triggers a `Read` (e.g. "read README.md"). Expected: a frame with `{"type": "process_tools", "tools": [{"id": "toolu_…", "name": "Read", "input": {"file_path": "…"}}]}` then a frame with `"tools": []`.
3. Send a prompt that triggers an `Edit`. Expected: the `input` in the `process_tools` frame contains `file_path` only — no `old_string`, no `new_string`.

If either expectation fails, stop and diagnose before moving on.

---

## Task 4: Frontend — Tool summary utility

**Files:**
- Create: `frontend/src/utils/toolSummary.js`

This module owns all per-tool-name knowledge. It must not import any Vue or store module — keeping it pure prevents the HMR circular-import issues called out in `CLAUDE.md`.

- [ ] **Step 1: Create the file**

Write the file with exactly this content:

```javascript
/**
 * Pure utilities for tool input summarization.
 *
 * Used by:
 *   - ToolUseContent.vue           — per-tool card (rich rendering)
 *   - WorkingAssistantMessage.vue  — "Claude is …" status line (inline rendering)
 *
 * Keep this file free of Vue/store imports so HMR cycles don't form
 * (see CLAUDE.md "Avoiding Circular Imports").
 */

import { getIconUrl, getFileIconId } from './fileIcons'
import { getTodoDescription, isValidTodos } from './todoList'

const FILE_PATH_TOOLS = new Set(['Edit', 'Write', 'Read'])
const AGENT_TOOL_NAMES = new Set(['Task', 'Agent'])

const TASK_SUBAGENT_LABELS = {
    explore: 'exploring',
    plan: 'planning',
    bash: 'bashing',
}

/**
 * Make `path` relative to `baseDir` if it lives under it; otherwise return it unchanged.
 */
export function formatRelativePath(path, baseDir) {
    if (!path) return path
    if (baseDir && path.startsWith(baseDir + '/')) {
        return path.slice(baseDir.length + 1)
    }
    return path
}

function capitalize(str) {
    return str.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

/**
 * Display name override for a tool (Task subagent_type, Skill name).
 * Returns { name, namespace } or null if the regular tool name should be shown.
 */
export function getDisplayName(name, input) {
    if (AGENT_TOOL_NAMES.has(name)) {
        const sat = input?.subagent_type
        if (!sat || sat === 'general-purpose') return null
        const colonIdx = sat.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(sat.slice(colonIdx + 1)),
                namespace: capitalize(sat.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(sat), namespace: null }
    }
    if (name === 'Skill' && input?.skill) {
        const skill = input.skill
        const colonIdx = skill.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(skill.slice(colonIdx + 1)),
                namespace: capitalize(skill.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(skill), namespace: null }
    }
    return null
}

/**
 * Convert a tool name + input to a gerund form.
 *
 *   Task / Agent      → subagent_type-derived label ("exploring", "planning",
 *                       "bashing", "agenting")
 *   mcp__server__tool → "mcping (server)"
 *   generic           → lower-case, strip trailing vowels, append "ing"
 */
export function getVerb(name, input) {
    if (!name) return null
    if (AGENT_TOOL_NAMES.has(name)) {
        const subtype = input?.subagent_type?.toLowerCase()
        if (subtype && TASK_SUBAGENT_LABELS[subtype]) {
            return TASK_SUBAGENT_LABELS[subtype]
        }
        return 'agenting'
    }
    if (name.startsWith('mcp__')) {
        const parts = name.split('__')
        const server = parts[1] || 'mcp'
        return `mcping (${server})`
    }
    const lower = name.toLowerCase()
    return lower.replace(/[aeiou]+$/, '') + 'ing'
}

function fileIconFor(filePath) {
    if (!filePath) return null
    const filename = filePath.split('/').pop() || filePath
    const iconId = getFileIconId(filename)
    return iconId !== 'default-file' ? getIconUrl(iconId) : null
}

function buildGrepInline(pattern, fileType, path) {
    const parts = []
    if (pattern) parts.push(pattern)
    if (fileType) parts.push(`in ${fileType} files`)
    if (path) parts.push(`in ${path}`)
    return parts.length ? parts.join(' ') : null
}

function emptyRich(kind = null, overrides = {}) {
    return {
        kind,
        description: null,
        fileIconSrc: null,
        skill: null,
        grep: null,
        globPattern: null,
        webFetchUrl: null,
        webSearchQuery: null,
        toolSearchQuery: null,
        todoDescription: null,
        ...overrides,
    }
}

/**
 * Compute structured + inline summary for a tool_use input.
 *
 * @param {string} name         Tool name (e.g. "Read", "mcp__foo__bar").
 * @param {object} input        Tool input dict (already filtered server-side for some tools).
 * @param {string|null} baseDir Session base dir (git_directory || cwd) for relative paths.
 * @returns {object}            See module docblock for shape.
 */
export function computeToolSummary(name, input, baseDir) {
    const safeInput = input || {}
    const displayName = getDisplayName(name, safeInput)

    // File-path tools ───────────────────────────────────────────────
    if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
        const description = formatRelativePath(safeInput.file_path, baseDir)
        return {
            displayName,
            inline: description,
            rich: emptyRich('description', {
                description,
                fileIconSrc: fileIconFor(safeInput.file_path),
            }),
        }
    }

    // Skill ────────────────────────────────────────────────────────
    if (name === 'Skill' && displayName) {
        return {
            displayName,
            inline: displayName.name,
            rich: emptyRich('skill', { skill: displayName }),
        }
    }

    // Grep ─────────────────────────────────────────────────────────
    if (name === 'Grep') {
        const pattern = safeInput.pattern || null
        const fileType = safeInput.type || safeInput.glob || null
        const rawPath = safeInput.path || null
        if (pattern || fileType || rawPath) {
            const path = rawPath ? formatRelativePath(rawPath, baseDir) : null
            const pathIconSrc = rawPath ? fileIconFor(rawPath) : null
            return {
                displayName,
                inline: buildGrepInline(pattern, fileType, path),
                rich: emptyRich('grep', {
                    grep: { pattern, fileType, path, pathIconSrc },
                }),
            }
        }
    }

    // Glob ─────────────────────────────────────────────────────────
    if (name === 'Glob' && safeInput.pattern) {
        return {
            displayName,
            inline: safeInput.pattern,
            rich: emptyRich('glob', { globPattern: safeInput.pattern }),
        }
    }

    // WebFetch ─────────────────────────────────────────────────────
    if (name === 'WebFetch' && safeInput.url) {
        return {
            displayName,
            inline: safeInput.url,
            rich: emptyRich('webFetch', { webFetchUrl: safeInput.url }),
        }
    }

    // WebSearch ────────────────────────────────────────────────────
    if (name === 'WebSearch' && safeInput.query) {
        return {
            displayName,
            inline: safeInput.query,
            rich: emptyRich('webSearch', { webSearchQuery: safeInput.query }),
        }
    }

    // ToolSearch ───────────────────────────────────────────────────
    if (name === 'ToolSearch' && safeInput.query) {
        return {
            displayName,
            inline: safeInput.query,
            rich: emptyRich('toolSearch', { toolSearchQuery: safeInput.query }),
        }
    }

    // TodoWrite ────────────────────────────────────────────────────
    if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
        return {
            displayName,
            inline: null,
            rich: emptyRich('todo', {
                todoDescription: getTodoDescription(safeInput.todos),
            }),
        }
    }

    // Generic / fallback (Task with no displayName, Bash, MCP, …) ─
    const description = safeInput.description || null
    return {
        displayName,
        inline: description,
        rich: emptyRich(description ? 'description' : null, { description }),
    }
}
```

- [ ] **Step 2: Browser-console smoke test**

Once the dev server is running (the user will start it; do not run `devctl.py` yourself), open the browser DevTools console for the TwiCC tab and run:

```javascript
const m = await import('/src/utils/toolSummary.js')

// Verbs
console.assert(m.getVerb('Read') === 'reading', 'Read')
console.assert(m.getVerb('Write') === 'writing', 'Write')
console.assert(m.getVerb('Edit') === 'editing', 'Edit')
console.assert(m.getVerb('Bash') === 'bashing', 'Bash')
console.assert(m.getVerb('mcp__foo__bar') === 'mcping (foo)', 'MCP')
console.assert(m.getVerb('Task', { subagent_type: 'explore' }) === 'exploring', 'Task explore')
console.assert(m.getVerb('Task', { subagent_type: 'general-purpose' }) === 'agenting', 'Task generic')

// Path formatting
console.assert(m.formatRelativePath('/x/y/z.py', '/x') === 'y/z.py', 'rel path')
console.assert(m.formatRelativePath('/other/z.py', '/x') === '/other/z.py', 'abs path')

// Summaries
const r = m.computeToolSummary('Read', { file_path: '/x/y/z.py' }, '/x')
console.assert(r.inline === 'y/z.py', 'Read inline')
console.assert(r.rich.kind === 'description', 'Read kind')

const b = m.computeToolSummary('Bash', { command: 'ls', description: 'List dir' }, null)
console.assert(b.inline === 'List dir', 'Bash inline')

const t = m.computeToolSummary('TodoWrite', { todos: [] }, null)
console.assert(t.inline === null, 'Todo inline null')

console.log('OK')
```

Expected: `OK` printed; no assertion failures.

---

## Task 5: Frontend — Migrate `ToolUseContent.vue` to `computeToolSummary`

**Files:**
- Modify: `frontend/src/components/items/content/ToolUseContent.vue`

**Goal:** No visual regression. Replace ~10 individual computeds with a single `summary = computed(() => computeToolSummary(...))` call. The template re-targets to `summary.value.*` properties through narrow per-template-branch refs.

- [ ] **Step 1: Add the import and adjust the existing one**

At the top of `<script setup>`, locate the existing `import { getTodoDescription, isValidTodos } from '../../../utils/todoList'` (line 15). The new utility uses `getTodoDescription` internally, so this file no longer needs it. Replace that line with:

```javascript
import { isValidTodos } from '../../../utils/todoList'
```

Then, near the other utility imports (after `import { getLanguageFromPath } from '../../../utils/languages'` at line 10), add:

```javascript
import { computeToolSummary } from '../../../utils/toolSummary'
```

The local `getIconUrl, getFileIconId` import (line 9) is still needed elsewhere — leave it.

- [ ] **Step 2: Delete the per-tool computeds**

Delete each of the following (lines noted are pre-edit; expect to see them in the same file in roughly that range):

- `usesFilePath` and `fileIconSrc` (lines 417-465)
- `description` (lines 467-480)
- `isSkill`, `skillDescription` (lines 482-495)
- `isGrep`, `grepParts` (lines 497-520)
- `isGlob`, `globPattern` (lines 522-527)
- `isWebFetch`, `webFetchUrl` (lines 529-534)
- `isWebSearch`, `webSearchQuery` (lines 536-541)
- `isToolSearch`, `toolSearchQuery` (lines 543-548)
- `isTodoWrite`, `todosValid`, `todoDescription` (lines 558-564)
- The local `capitalize` helper (lines 765-767)
- `taskDisplayName` (lines 769-780)

**Keep** `sessionBaseDir` (lines 421-424); the new `summary` consumes it. **Keep** `firstModifiedLine`, `viewInFilesButtonId`, `canViewInFilesTab`, `openInFilesTab` (~ lines 427-457).

- [ ] **Step 3: Insert the new `summary` and convenience refs**

Just below `sessionBaseDir` (~ line 425), insert:

```javascript
const summary = computed(() => computeToolSummary(props.name, props.input, sessionBaseDir.value))

// Convenience refs so the template stays readable without value-piercing.
const displayName = computed(() => summary.value.displayName)
const summaryDescription = computed(() => summary.value.rich.description)
const summaryFileIconSrc = computed(() => summary.value.rich.fileIconSrc)
const summarySkill = computed(() => summary.value.rich.skill)
const summaryGrep = computed(() => summary.value.rich.grep)
const summaryGlob = computed(() => summary.value.rich.globPattern)
const summaryWebFetchUrl = computed(() => summary.value.rich.webFetchUrl)
const summaryWebSearchQuery = computed(() => summary.value.rich.webSearchQuery)
const summaryToolSearchQuery = computed(() => summary.value.rich.toolSearchQuery)
const summaryTodo = computed(() => summary.value.rich.todoDescription)

// Local guards still used by the body rendering / non-summary code paths.
const isTodoWrite = computed(() => props.name === 'TodoWrite')
const todosValid = computed(() => isTodoWrite.value && isValidTodos(props.input?.todos))
const isSkill = computed(() => props.name === 'Skill')

// File-path detection still drives canViewInFilesTab and shouldAutoOpen.
const usesFilePath = computed(
    () => (props.name === 'Edit' || props.name === 'Write' || props.name === 'Read') && !!props.input?.file_path
)
```

- [ ] **Step 4: Re-target the template**

In the `<template>` (lines 852-979 area), apply these renames. Use Find-and-Replace within the template to be exhaustive.

| Old reference | New reference |
|---|---|
| `taskDisplayName` | `displayName` |
| `description` (the computed; not the *prop* of any child component) | `summaryDescription` |
| `fileIconSrc` | `summaryFileIconSrc` |
| `skillDescription` | `summarySkill` |
| `grepParts` | `summaryGrep` |
| `globPattern` | `summaryGlob` |
| `webFetchUrl` | `summaryWebFetchUrl` |
| `webSearchQuery` | `summaryWebSearchQuery` |
| `toolSearchQuery` | `summaryToolSearchQuery` |
| `todoDescription` | `summaryTodo` |

**Be careful** with the `<strong>` block at line 856-858. Replace it exactly with:

```html
<strong v-if="isTask && displayName" class="items-details-summary-name">{{ displayName.name }}<span v-if="displayName.namespace" class="items-details-summary-quiet"> ({{ displayName.namespace }})</span></strong>
<strong v-else-if="isTodoWrite" class="items-details-summary-name">Todo</strong>
<strong v-else class="items-details-summary-name">{{ name.replaceAll('__', ' ') }}</strong>
```

The first `v-if` previously read `v-if="taskDisplayName"`. The new condition `isTask && displayName` is intentional: `displayName` is `null` when the subagent_type is `general-purpose`, in which case we fall through to the raw tool-name `<strong>` (matches old behavior).

- [ ] **Step 5: Visual regression check (manual)**

The user starts the dev servers — **do not** run `devctl.py` yourself. Once running, open in the browser:

1. A session containing each of: `Read`, `Edit`, `Write`, `Bash` (with description), `Grep` (with all three of `pattern` / `type` / `path`), `Glob`, `WebFetch`, `WebSearch`, `ToolSearch`, `Skill` (namespaced like `superpowers:foo`), `Task` (with non-`general-purpose` subagent_type), `TodoWrite`, an MCP tool. Confirm each summary line looks identical to the screenshot before the change.
2. Send a fresh `Edit` to verify the diff still auto-opens.
3. Confirm the file icon next to a `Read` summary still appears.

If any visible regression is observed, the bug is most likely in the template mapping (Step 4); revert and re-apply.

---

## Task 6: Frontend — `processStates` shape & WebSocket handler

**Files:**
- Modify: `frontend/src/stores/data.js`
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Add `tools` and `_toolsClearTimer` in `setProcessState`**

In `data.js`, locate `setProcessState` (line 2133). Inside the `else` branch where `this.processStates[sessionId] = { … }` is built (lines 2145-2156), **add** these two fields anywhere in the object literal:

```javascript
tools: [],
_toolsClearTimer: null,
```

- [ ] **Step 2: Add the same fields in `setActiveProcesses`**

In `data.js`, locate `setActiveProcesses` (line 2179). Inside the loop that builds `this.processStates[p.session_id] = { … }` (lines 2188-2199), **add** the same two fields:

```javascript
tools: [],
_toolsClearTimer: null,
```

- [ ] **Step 3: Add the `process_tools` handler in `useWebSocket.js`**

In `useWebSocket.js`, immediately after the `case 'process_label'` block (lines 803-810), add:

```javascript
case 'process_tools': {
    // Active-tool list for the WorkingAssistantMessage status line.
    const ps = store.processStates[msg.session_id]
    if (!ps) break
    const tools = Array.isArray(msg.tools) ? msg.tools : []
    if (tools.length === 0) {
        // Debounce empty-list transitions to avoid a "thinking" flash
        // between PostToolUse(A) and PreToolUse(B) of parallel tools.
        if (ps._toolsClearTimer) break
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

- [ ] **Step 4: Smoke test the broadcast → store path**

In Vue Devtools → Pinia → `data` store → `processStates[<session-id>]`:

1. While Claude is running a `Read`, observe `tools` populated with one entry.
2. After the read finishes, observe a 1-second delay then `tools` becomes `[]`.
3. For parallel tools (prompt: "read file A and file B in parallel"), observe `tools.length === 2` momentarily, then drops to 1, then to 0 (after the 1-second debounce on the final empty).

Expected: each transition matches. If not, check the handler ordering (step 3) and the store initialisation (steps 1-2).

---

## Task 7: Frontend — Remove the walk-back heuristic

**Files:**
- Modify: `frontend/src/stores/data.js`

- [ ] **Step 1: Delete the walk-back loop**

In `data.js`, locate the synthetic working-message build (around line 1411). Delete the entire block from `let toolUse = null` (line 1421) through and including the closing `}` of the `for` loop (line 1443):

```javascript
// DELETE FROM HERE
let toolUse = null
let toolUseCompleted = false
for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i]
    if (item.kind === 'system') continue
    if (item.kind !== 'assistant_message' && item.kind !== 'content_items') break
    const parsed = getParsedContent(item)
    if (!parsed) break
    const contentArray = parsed?.message?.content
    if (!Array.isArray(contentArray) || contentArray.length === 0) break
    const lastContent = contentArray[contentArray.length - 1]
    if (lastContent.type === 'tool_use') {
        toolUse = lastContent
        break
    }
    // If every entry is a tool_result, skip this item and keep looking
    if (contentArray.every(c => c.type === 'tool_result')) {
        toolUseCompleted = true
        continue
    }
    // Otherwise (text, image, etc.) stop searching
    break
}
// DELETE TO HERE
```

- [ ] **Step 2: Update the `setParsedContent` payload**

Right after the deleted block, the `setParsedContent(workingMessage, { … })` call (lines 1454-1464) currently includes `toolUse` and `toolUseCompleted`. Replace the whole call with:

```javascript
setParsedContent(workingMessage, {
    type: 'assistant',
    syntheticKind,
    label: processState?.label || null,
    tools: processState?.tools || [],
    message: {
        role: 'assistant',
        content: []
    }
})
```

- [ ] **Step 3: Confirm no other reader of `toolUse`/`toolUseCompleted` in `data.js`**

Run from the worktree root:

```bash
rg -n "toolUse|toolUseCompleted" frontend/src/stores/data.js
```

Expected: zero matches. If anything is left, remove it — those references were tied to the deleted loop.

- [ ] **Step 4: Visual interim check**

At this point the working line will say "Claude is thinking…" while a tool runs (Task 8 wires up the new rendering). That is the expected interim state.

---

## Task 8: Frontend — `WorkingAssistantMessage.vue` and `Message.vue`

**Files:**
- Modify: `frontend/src/components/items/Message.vue`
- Modify: `frontend/src/components/items/WorkingAssistantMessage.vue`

- [ ] **Step 1: Update the prop binding in `Message.vue`**

In `Message.vue`, line 82, replace this single line:

```html
<WorkingAssistantMessage v-else-if="isWorkingAssistantMessage" :label="data.label || null" :tool-use="data.toolUse || null" :tool-use-completed="data.toolUseCompleted || false" />
```

with:

```html
<WorkingAssistantMessage v-else-if="isWorkingAssistantMessage" :label="data.label || null" :tools="data.tools || []" :session-id="sessionId" />
```

`sessionId` is already a prop of `Message.vue` (visible in the `defineProps` block; it is referenced elsewhere in the template). No other change needed in this file.

- [ ] **Step 2: Rewrite `WorkingAssistantMessage.vue`**

Replace the entire file with:

```vue
<script setup>
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import { computeToolSummary, getVerb } from '../../utils/toolSummary'
import ProcessIndicator from '../ProcessIndicator.vue'

const props = defineProps({
    label: { type: String, default: null },
    processState: { type: String, default: 'assistant_turn' },
    tools: { type: Array, default: () => [] },
    sessionId: { type: String, default: null },
})

const dataStore = useDataStore()

const sessionBaseDir = computed(() => {
    if (!props.sessionId) return null
    const session = dataStore.getSession(props.sessionId)
    return session?.git_directory || session?.cwd || null
})

const phrase = computed(() => {
    if (props.label === 'compacting') return 'compacting'
    const tools = props.tools || []
    if (tools.length === 0) return 'thinking'
    return renderToolsPhrase(tools, sessionBaseDir.value)
})

function renderToolsPhrase(tools, baseDir) {
    // Group tools by verb, preserving first-occurrence order from the current frame.
    const groups = new Map()
    for (const t of tools) {
        const verb = getVerb(t.name, t.input)
        if (!verb) continue
        const { inline } = computeToolSummary(t.name, t.input, baseDir)
        if (!groups.has(verb)) groups.set(verb, [])
        groups.get(verb).push(inline)
    }

    // 1 tool total → no parens (would be redundant with the tool card right above).
    const showSummaries = tools.length > 1

    const parts = []
    for (const [verb, summaries] of groups) {
        if (!showSummaries) {
            parts.push(verb)
            continue
        }
        const targets = summaries.filter(s => s != null)
        parts.push(targets.length === 0 ? verb : `${verb} (${targets.join(', ')})`)
    }
    return parts.join(', ')
}
</script>

<template>
    <div class="working-assistant-message text-content">
        <ProcessIndicator :state="processState" size="small" :animate-states="['starting', 'assistant_turn']" />
        <span>Claude is {{ phrase }}...</span>
    </div>
</template>

<style scoped>
.working-assistant-message {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-style: italic;
    font-size: var(--wa-font-size-m);
}
</style>
```

Note the deletions from the previous version: `toolAction`, `displayedAction`, `pendingTimer`, the `watch`, `onUnmounted`, `FALLBACK_DELAY_MS`, `TASK_SUBAGENT_LABELS`, the `AGENT_TOOL_NAMES` import, the `toolUse` / `toolUseCompleted` props.

- [ ] **Step 3: End-to-end visual smoke test**

The user reloads (HMR will pick the change up). Then in the browser:

1. **Single tool** — prompt "read README.md". Expected: "Claude is reading…" (no parens).
2. **Parallel reads** — prompt "in parallel, read README.md and pyproject.toml". Expected: "Claude is reading (README.md, pyproject.toml)…".
3. **Mixed parallel** — prompt "read README.md and edit pyproject.toml in parallel". Expected: "Claude is reading (README.md), editing (pyproject.toml)…".
4. **Bash without description** — prompt "run `ls`". Expected: "Claude is bashing…".
5. **Compacting** — long session that triggers /compact. Expected: "Claude is compacting…" (label takes priority).
6. **Empty list debounce** — observe that between two parallel-tool transitions there is no flash of "thinking".

If a step fails, isolate by checking `processStates[id].tools` in Vue Devtools at the moment of the issue.

---

## Task 9: End-to-end manual verification & cleanup

**Files:** none

- [ ] **Step 1: Re-run all phrase scenarios**

Verify each from the Frontend rendering section of the spec:

- 0 tools active → "thinking".
- 1 Read → "reading".
- 1 Bash with description → "bashing".
- 2 Reads → "reading (A, B)".
- Read + Edit → "reading (A), editing (B)".
- Bash (no description) + WebFetch URL → "bashing, fetching (https://…)".
- 3 Reads + 1 Edit → "reading (A, B, C), editing (D)".
- Compacting overrides any active tools list.

- [ ] **Step 2: Backend payload size sanity**

In DevTools Network tab, inspect a `process_tools` frame for an `Edit`: confirm `input` only carries `file_path` (no `old_string` / `new_string`). Same check on a `Write` (no `content`). Same on a `MultiEdit` (no `edits`).

- [ ] **Step 3: Task / MCP coverage check (call out gaps)**

Trigger a `Task` tool. Look for a `process_tools` frame containing the Task entry. If present, the phrase reads `"exploring" / "planning" / …`; if absent, the phrase falls back to "thinking" while the Task runs.

Same for an MCP tool.

If either is missing, **do not** attempt to fix it inside this plan. Document the gap in a single sentence to the user (e.g. "PreToolUse fires for Task: yes/no") and let the user decide on a follow-up.

- [ ] **Step 4: Stop dev servers**

If you started any dev server during verification, stop them per CLAUDE.md:

```bash
uv run ./devctl.py stop all
```

---

## Notes for the executor

- This worktree is `.worktrees/working-tools-broadcast/` on branch `feat/working-tools-broadcast`. All paths in this plan are relative to the worktree root unless absolute.
- **No commits** at any step — the user will commit when ready.
- **Restart the backend** is a user-only operation per CLAUDE.md. Whenever a Python file is edited (Tasks 1-3), prompt the user to restart and wait. Frontend changes pick up via Vite HMR; the user does not need to restart for those.
- If any task fails its smoke test, stop, report the failure, and do not advance to the next task.
