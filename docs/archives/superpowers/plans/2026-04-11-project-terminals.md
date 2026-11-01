# Project-Level Terminals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow terminals to be opened at the project, workspace, or "all projects" level — not tied to any session — via a Terminal tab in the ProjectDetailPanel.

**Architecture:** Currently terminals are coupled to session IDs at every layer (WebSocket URL, tmux naming, store keying, WS messages). We introduce a **terminal context key** (`tctx`) — a string like `session:<id>`, `project:<id>`, or `global` — to generalize the identity of a terminal owner. Backend gets additional URL routes without `session_id`. Frontend generalizes the store and composable to work with context keys instead of session IDs.

**Tech Stack:** Django ASGI (WebSocket routes), Vue 3 + Pinia (store, composable, components)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/twicc/terminal.py` | Modify | Generalize `get_session_info` → `get_terminal_cwd`, update `tmux_session_name` to accept context keys, update all tmux helpers signatures |
| `src/twicc/asgi.py` | Modify | Add new WS URL route without `session_id`, make `session_id` optional in `terminal_application`, generalize WS message handlers (`list_terminals`, `kill_terminal`, `rename_terminal`) to use `terminal_context` |
| `frontend/src/stores/terminalTabs.js` | Modify | Replace `sessionId` keys with generic `contextKey` |
| `frontend/src/stores/terminalConfig.js` | Modify | Add `getGlobalSnippets()` and `getSnippetsForWorkspace()` getters for non-session terminal contexts |
| `frontend/src/composables/useTerminal.js` | Modify | Accept `contextKey` + optional `projectId` instead of `sessionId`, update WS URL construction, remove `!sessionId` guards |
| `frontend/src/composables/useWebSocket.js` | Modify | Route terminal WS messages using `terminal_context` instead of `session_id` |
| `frontend/src/components/TerminalPanel.vue` | Modify | Accept `contextKey` + `projectId` as alternative to `sessionId`, adapt WS messages |
| `frontend/src/components/TerminalInstance.vue` | Modify | Pass `contextKey` instead of `sessionId` to `useTerminal` |
| `frontend/src/components/ProjectDetailPanel.vue` | Modify | Wire real `TerminalPanel` into the Terminal tab with correct context key |
| `frontend/src/views/SessionView.vue` | Modify | Pass `contextKey` instead of raw `sessionId` to `TerminalPanel` |

---

## Concept: Terminal Context Key

A terminal context key (`tctx`) is a string that identifies the "owner" of a terminal. It replaces the raw `session_id` everywhere terminals are identified.

| Mode | Context Key | Backend cwd | tmux session name |
|------|-------------|-------------|-------------------|
| Session terminal | `s:<session_id>` | Session git_directory → Project directory → ~ | `twicc-<session_id>` (unchanged) |
| Project terminal | `p:<project_id>` | Project directory → ~ | `twicc-p_<project_id>` |
| Workspace terminal | `w:<workspace_id>` | Common ancestor of workspace projects' directories (from frontend via `cwd` query string) → `~` | `twicc-w_<workspace_id>` |
| Global terminal (all projects) | `global` | `~` (home) | `twicc-global` |

**Session tmux names:** Session terminals keep their existing tmux naming scheme (no prefix change) for backward compatibility with existing tmux sessions.

**Workspace context:** The workspace ID and name are frontend concepts — the backend has no knowledge of workspaces. The frontend passes the tmux session name via `name` query string and the cwd via `cwd` query string.

**Workspace cwd:** The frontend computes the lowest common ancestor directory of all projects in the workspace and passes it as `?cwd=/path/to/dir`. The backend only accepts the `cwd` query param when a `name` query param is also present (workspace mode). For all other modes (global, project, session), the backend determines the cwd itself and ignores any `cwd` param.

**WebSocket URL:** Global and workspace terminals use the same WS URL route (no `project_id` or `session_id`). For workspace terminals, the frontend passes `?name=w:42&cwd=/path/to/dir`. When no `name` is provided, the backend uses `global` as the tmux session name and `~` as the cwd.

**Terminal snippets by context:**

| Context | Available snippets |
|---------|--------------------|
| Global (All Projects) | Global snippets only |
| Workspace | Global + workspace + all projects in the workspace |
| Project / Session | Global + workspace + project |

---

### Task 1: Backend — Generalize `get_session_info` and tmux naming

**Files:**
- Modify: `src/twicc/terminal.py:67-161`

- [ ] **Step 1: Rename and generalize `get_session_info`**

Replace the function with `get_terminal_cwd` that accepts optional `session_id` and optional `project_id`:

```python
@sync_to_async
def get_terminal_cwd(session_id: str | None = None, project_id: str | None = None) -> tuple[str, bool]:
    """Resolve the working directory and archived status for a terminal.

    Returns (cwd, archived).

    When session_id is provided, the priority order is:
    - Session.git_directory → Project.directory → Project.git_root → ~
    When only project_id is provided:
    - Project.directory → Project.git_root → ~
    When neither is provided:
    - ~ (home directory)
    """
    from twicc.core.models import Project, Session

    home = os.path.expanduser("~")

    # No session — resolve from project only
    if not session_id:
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                for candidate in (project.directory, project.git_root):
                    if candidate and os.path.isdir(candidate):
                        return candidate, False
            except Project.DoesNotExist:
                pass
        return home, False

    # Session provided — existing logic
    try:
        session = Session.objects.select_related("project").get(id=session_id)
    except Session.DoesNotExist:
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                for candidate in (project.directory, project.git_root):
                    if candidate and os.path.isdir(candidate):
                        return candidate, False
            except Project.DoesNotExist:
                pass
        return home, False

    if session.git_directory:
        candidates = [
            session.git_directory,
            session.project.directory if session.project else None,
        ]
    else:
        candidates = [
            session.project.directory if session.project else None,
            session.project.git_root if session.project else None,
        ]

    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate, session.archived

    return home, session.archived
```

- [ ] **Step 2: Generalize `tmux_session_name`**

Change signature to accept a `terminal_context` string instead of `session_id`:

```python
def tmux_session_name(terminal_context: str, terminal_index: int = 0) -> str:
    """Return the tmux session name for a given terminal context and index.

    terminal_context is a string like 's:<id>', 'p:<id>', 'w:<id>', or 'global'.
    For session terminals ('s:' prefix), keep the existing naming scheme
    for backward compatibility with running tmux sessions.
    All names are prefixed with 'twicc-' and sanitized (no dots/colons).
    """
    if terminal_context.startswith("s:"):
        # Backward compat: session terminals keep old naming
        session_id = terminal_context[2:]
        base = "twicc-" + session_id.replace(".", "_").replace(":", "_")
    else:
        # Project, workspace, global: twicc- prefix + sanitized context
        sanitized = terminal_context.replace(":", "_").replace(".", "_")
        base = "twicc-" + sanitized
    if terminal_index == 0:
        return base
    return f"{base}__{terminal_index}"
```

- [ ] **Step 3: Update all tmux helper signatures**

All functions that currently take `session_id` should take `terminal_context` instead:
- `tmux_session_exists(terminal_context, terminal_index)` (line 287)
- `list_tmux_sessions_for_session(terminal_context)` → rename to `list_tmux_terminals(terminal_context)` (line 305)
- `kill_tmux_session(terminal_context, terminal_index)` → rename to `kill_tmux_terminal(terminal_context, terminal_index)` (line 349)
- `kill_all_tmux_sessions(terminal_context)` → rename to `kill_all_tmux_terminals(terminal_context)` (line 373)
- `tmux_set_option(terminal_context, ...)` (line ~390)
- `set_tmux_terminal_label(terminal_context, terminal_index, label)` (line 406)
- `_unset_tmux_terminal_label(terminal_context, terminal_index)` (line ~426)
- `_tmux_scroll(terminal_context, scroll_lines, terminal_index)` (line ~458)
- `_tmux_pane_state(terminal_context, terminal_index)` (line ~502)
- `_tmux_pane_monitor(terminal_context, send, terminal_index)` (line ~546)
- `spawn_tmux_pty(cwd, terminal_context, terminal_index)` (line 214)

The internal logic stays the same — they all call `tmux_session_name(...)` which now accepts the context key.

- [ ] **Step 4: Update callers inside `terminal.py`**

In `terminal_application`, update all calls to use the new function names and pass `terminal_context`.

- [ ] **Step 5: Update callers in `views.py`**

`kill_all_tmux_sessions(session_id)` → `kill_all_tmux_terminals(f"s:{session_id}")` in session archive handler.

- [ ] **Step 6: Commit**

```bash
git add src/twicc/terminal.py src/twicc/views.py
git commit -m "refactor: generalize terminal.py from session_id to terminal_context"
```

---

### Task 2: Backend — Add WS routes without `session_id` and generalize `terminal_application`

**Files:**
- Modify: `src/twicc/asgi.py:1392-1473` (WS message handlers)
- Modify: `src/twicc/asgi.py:1496-1499` (URL patterns)
- Modify: `src/twicc/asgi.py:598-605` (terminal_application kwargs)

- [ ] **Step 1: Add new URL routes**

```python
websocket_urlpatterns = [
    # Terminal with session context
    path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
    # Terminal with project context only (no session)
    path("ws/terminal/<str:project_id>/<int:terminal_index>/", terminal_application),
    # Terminal with no project (global context)
    path("ws/terminal/<int:terminal_index>/", terminal_application),
    path("ws/", UpdatesConsumer.as_asgi()),
]
```

- [ ] **Step 2: Build terminal_context from URL kwargs in `terminal_application`**

Replace the current kwargs extraction (lines 598-604):

```python
    # ── Resolve terminal context and working directory ─────────────
    session_id = scope["url_route"]["kwargs"].get("session_id")
    project_id = scope["url_route"]["kwargs"].get("project_id")
    terminal_index = scope["url_route"]["kwargs"].get("terminal_index", 0)

    # Build terminal context key and resolve cwd
    if session_id:
        terminal_context = f"s:{session_id}"
        cwd, archived = await get_terminal_cwd(session_id, project_id)
    elif project_id:
        terminal_context = f"p:{project_id}"
        cwd, archived = await get_terminal_cwd(None, project_id)
    else:
        # Global or workspace: check query string for name and cwd
        query_string = scope.get("query_string", b"").decode()
        from urllib.parse import parse_qs
        qs = parse_qs(query_string)
        terminal_context = qs.get("name", ["global"])[0]

        # Only accept cwd from query string for workspace terminals (name present)
        home = os.path.expanduser("~")
        if terminal_context != "global":
            # Workspace: use frontend-provided cwd, validate it exists
            requested_cwd = qs.get("cwd", [None])[0]
            cwd = requested_cwd if requested_cwd and os.path.isdir(requested_cwd) else home
        else:
            cwd = home
        archived = False
```

Note: The `cwd` query string is only accepted when `name` is also present (workspace mode). For global terminals, the backend always uses `~`. For session/project terminals, the backend resolves the cwd from the database. The `cwd` value is validated with `os.path.isdir()` before use.

- [ ] **Step 3: Update all `terminal_application` references to use `terminal_context`**

Replace every `session_id` usage after the context resolution with `terminal_context`:
- `tmux_session_exists(terminal_context, terminal_index)` (line 618)
- `spawn_tmux_pty(cwd, terminal_context, terminal_index)` (line 623)
- `tmux_set_option(terminal_context, ...)` (line 651)
- `_tmux_pane_monitor(terminal_context, ...)` (line 759)
- `_tmux_scroll(terminal_context, ...)` (line 741)
- `terminal_created` broadcast — include `terminal_context` instead of `session_id` (lines 770-776)

The log messages can still reference `session_id` or `terminal_context` for readability.

- [ ] **Step 4: Generalize `_handle_list_terminals`, `_handle_kill_terminal`, `_handle_rename_terminal`**

These WS message handlers currently require `session_id`. Change them to accept `terminal_context` directly:

```python
async def _handle_list_terminals(self, data):
    terminal_context = data.get("terminal_context")
    if not terminal_context:
        await self.send_json({"type": "error", "message": "Missing terminal_context"})
        return

    from twicc.terminal import list_tmux_terminals
    terminals = await asyncio.to_thread(list_tmux_terminals, terminal_context)

    await self.send_json({
        "type": "terminal_list",
        "terminal_context": terminal_context,
        "terminals": [t.index for t in terminals],
        "labels": {str(t.index): t.label for t in terminals if t.label},
    })
```

Apply the same pattern to `_handle_kill_terminal` and `_handle_rename_terminal` — replace `session_id` with `terminal_context` in both the input and broadcast messages.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: add project/global terminal WS routes, generalize to terminal_context"
```

---

### Task 3: Frontend — Generalize `terminalTabs.js` store

**Files:**
- Modify: `frontend/src/stores/terminalTabs.js`

- [ ] **Step 1: Replace `sessionId` with `contextKey` in all actions**

Rename every parameter and comment from `sessionId` to `contextKey`. The store logic is identical — only the key name changes:

```js
export const useTerminalTabsStore = defineStore('terminalTabs', {
    state: () => ({
        // contextKey → sorted array of terminal indices from backend
        indices: {},
        // contextKey → { terminalIndex: label } — labels from tmux user options
        labels: {},
    }),
    actions: {
        setIndices(contextKey, terminalIndices) {
            this.indices[contextKey] = [...terminalIndices].sort((a, b) => a - b)
        },
        addIndex(contextKey, index) {
            if (!this.indices[contextKey]) {
                this.indices[contextKey] = [index]
                return
            }
            if (!this.indices[contextKey].includes(index)) {
                this.indices[contextKey] = [...this.indices[contextKey], index].sort((a, b) => a - b)
            }
        },
        removeIndex(contextKey, index) {
            if (this.indices[contextKey]) {
                this.indices[contextKey] = this.indices[contextKey].filter(i => i !== index)
            }
            if (this.labels[contextKey]) {
                delete this.labels[contextKey][index]
            }
        },
        setLabels(contextKey, labelsMap) {
            this.labels[contextKey] = {}
            for (const [index, label] of Object.entries(labelsMap)) {
                if (label) {
                    this.labels[contextKey][Number(index)] = label
                }
            }
        },
        setLabel(contextKey, index, label) {
            if (!this.labels[contextKey]) {
                this.labels[contextKey] = {}
            }
            if (label) {
                this.labels[contextKey][index] = label
            } else {
                delete this.labels[contextKey][index]
            }
        },
        getLabel(contextKey, index) {
            return this.labels[contextKey]?.[index] || ''
        },
    },
})
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/terminalTabs.js
git commit -m "refactor: generalize terminalTabs store from sessionId to contextKey"
```

---

### Task 4: Frontend — Generalize `useWebSocket.js` terminal message handlers

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js:788-811`

- [ ] **Step 1: Update WS message handlers to use `terminal_context`**

The backend now sends `terminal_context` instead of `session_id` in terminal messages. Update the handlers:

```js
case 'terminal_list':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        const store = useTerminalTabsStore()
        store.setIndices(msg.terminal_context, msg.terminals)
        if (msg.labels) {
            store.setLabels(msg.terminal_context, msg.labels)
        }
    })
    break
case 'terminal_created':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().addIndex(msg.terminal_context, msg.terminal_index)
    })
    break
case 'terminal_killed':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().removeIndex(msg.terminal_context, msg.terminal_index)
    })
    break
case 'terminal_renamed':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().setLabel(msg.terminal_context, msg.terminal_index, msg.label)
    })
    break
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "refactor: use terminal_context in WS terminal message handlers"
```

---

### Task 5: Frontend — Generalize `TerminalPanel.vue` and `TerminalInstance.vue`

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue:20-60, 140, 147-151, 197-202, 278-283, 375-378`
- Modify: `frontend/src/components/TerminalInstance.vue:6-19, 31`

- [ ] **Step 1: Update `TerminalPanel.vue` props**

Add `contextKey`, `projectId` and `cwd` props. Keep `sessionId` for backward compat (session terminals still need it for session-specific behavior like archive detection, snippet context):

```js
const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
})
```

- [ ] **Step 2: Update session/project resolution and snippet context in `TerminalPanel.vue`**

The session, projectId, placeholderContext, and snippetsForProject computed properties must work without a session:

```js
const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const resolvedProjectId = computed(() => props.projectId || session.value?.project_id)

// Build a placeholder resolution context for a given snippet.
// For snippets scoped to a project (scope "project:<id>"), use THAT project's data
// for project-related placeholders — even in workspace terminals showing snippets
// from multiple projects. Session is always from props (null for non-session terminals).
function buildPlaceholderContext(snippet) {
    const s = session.value
    // Extract project from snippet scope (e.g. "project:abc" → "abc")
    let pid = resolvedProjectId.value
    if (snippet?._scope?.startsWith('project:')) {
        pid = snippet._scope.slice('project:'.length)
    }
    const project = pid ? dataStore.getProject(pid) : null
    const projectName = pid ? dataStore.getProjectDisplayName(pid) : null
    return { session: s, project, projectName }
}

// Default context (no snippet-specific project) — used by the send dialog.
const placeholderContext = computed(() => buildPlaceholderContext(null))

// Workspace IDs for snippet scoping:
// - Session/project terminal in a workspace URL: use that workspace
// - Session/project terminal outside workspace: all workspaces containing the project
// - Workspace terminal: use that workspace (extracted from contextKey)
// - Global terminal: empty (global snippets only)
const snippetWorkspaceIds = computed(() => {
    const wsId = route.query.workspace
    if (wsId) return [wsId]
    // Workspace terminal: contextKey is "w:<workspaceId>"
    if (props.contextKey.startsWith('w:')) {
        return [props.contextKey.slice(2)]
    }
    const pid = resolvedProjectId.value
    if (!pid) return []
    return workspacesStore.getWorkspacesForProject(pid).map(ws => ws.id)
})

// Snippets available in current context, with placeholder availability checks.
// For workspace terminals (no single project), get snippets from all workspace projects.
const snippetsForProject = computed(() => {
    let raw
    if (resolvedProjectId.value) {
        // Session or project terminal: snippets for that project
        raw = terminalConfigStore.getSnippetsForProject(resolvedProjectId.value, snippetWorkspaceIds.value)
    } else if (props.contextKey.startsWith('w:')) {
        // Workspace terminal: merge snippets from all projects in the workspace
        const wsId = props.contextKey.slice(2)
        const projectIds = workspacesStore.getVisibleProjectIds(wsId) || []
        raw = terminalConfigStore.getSnippetsForWorkspace(projectIds, snippetWorkspaceIds.value)
    } else {
        // Global terminal: global snippets only
        raw = terminalConfigStore.getGlobalSnippets()
    }

    return raw.map(snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        // Per-snippet context: project-scoped snippets resolve {project-dir} etc.
        // using their own project's data, not the terminal's project.
        const ctx = buildPlaceholderContext(snippet)
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})
```

Note: The `TerminalSnippetSendDialog` receives `placeholderContext` as a prop for its own availability check. When opening the send dialog for a specific snippet, the caller should pass `buildPlaceholderContext(snippet)` instead of the default `placeholderContext`. This ensures the send dialog also resolves project placeholders from the snippet's scope.

- [ ] **Step 2b: Add missing getters to `terminalConfig.js` store**

Add two new getters alongside the existing `getSnippetsForProject`:

```js
// Global snippets only (for "All Projects" terminal)
getGlobalSnippets: (state) => () => {
    return state.snippets.global || []
},

// Global + workspace(s) + all projects in the workspace (for workspace terminals)
getSnippetsForWorkspace: (state) => (projectIds, workspaceIds = null) => {
    const global = state.snippets.global || []
    const wsSnippets = (workspaceIds || []).flatMap(wsId => state.snippets[`workspace:${wsId}`] || [])
    const projSnippets = projectIds.flatMap(pid => state.snippets[`project:${pid}`] || [])
    return [...global, ...wsSnippets, ...projSnippets]
},
```

- [ ] **Step 3: Replace all `props.sessionId` usages in WS messages with `props.contextKey`**

In `TerminalPanel.vue`, update:
- `list_terminals` message: `session_id` → `terminal_context: props.contextKey`
- `kill_terminal` message: `session_id` → `terminal_context: props.contextKey`
- `rename_terminal` message: `session_id` → `terminal_context: props.contextKey`
- `terminalTabsStore` calls: `.indices[props.sessionId]` → `.indices[props.contextKey]`, same for labels/removeIndex/etc.

- [ ] **Step 4: Update `TerminalInstance.vue`**

Change props and pass `contextKey` + `projectId` to `useTerminal`:

```js
const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    terminalIndex: {
        type: Number,
        default: 0,
    },
    active: {
        type: Boolean,
        default: false,
    },
})
```

Update the `useTerminal` call:
```js
const { containerRef, isConnected, started, start, ... } = useTerminal(props.contextKey, props.terminalIndex, {
    sessionId: props.sessionId,
    projectId: props.projectId,
    cwd: props.cwd,
})
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TerminalPanel.vue frontend/src/components/TerminalInstance.vue
git commit -m "refactor: generalize TerminalPanel/Instance from sessionId to contextKey"
```

---

### Task 6: Frontend — Generalize `useTerminal.js` composable

**Files:**
- Modify: `frontend/src/composables/useTerminal.js:196, 277-296, 1325, 1510, 1722`

- [ ] **Step 1: Change signature**

```js
/**
 * @param {string} contextKey - Terminal context key (e.g. 's:<id>', 'p:<id>', 'global')
 * @param {number} terminalIndex - The terminal index (0 = main, N = secondary)
 * @param {Object} options
 * @param {string|null} options.sessionId - Session ID (for session terminals)
 * @param {string|null} options.projectId - Project ID (for cwd resolution)
 * @param {string|null} options.cwd - Explicit working directory (for workspace terminals)
 */
export function useTerminal(contextKey, terminalIndex = 0, { sessionId = null, projectId = null, cwd = null } = {}) {
```

- [ ] **Step 2: Update `getWsUrl()`**

Build the URL based on what's available. For workspace terminals, pass the context key as a `name` query string parameter:

```js
function getWsUrl() {
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    let path
    const params = new URLSearchParams()
    if (sessionId) {
        // Session terminal: /ws/terminal/<projectId>/<sessionId>/<index>/
        const pid = projectId || dataStore.getSession(sessionId)?.project_id || '_'
        path = `${pid}/${sessionId}/${terminalIndex}`
    } else if (projectId) {
        // Project terminal: /ws/terminal/<projectId>/<index>/
        path = `${projectId}/${terminalIndex}`
    } else {
        // Global or workspace terminal: /ws/terminal/<index>/
        path = `${terminalIndex}`
        // For workspace terminals (contextKey like "w:42"), pass name and cwd
        if (contextKey !== 'global') {
            params.set('name', contextKey)
            if (cwd) {
                params.set('cwd', cwd)
            }
        }
    }
    if (shouldUseTmux()) {
        params.set('tmux', '1')
    }
    const qs = params.toString()
    const base = `${wsProtocol}//${location.host}/ws/terminal/${path}/`
    return qs ? `${base}?${qs}` : base
}
```

- [ ] **Step 3: Update guards**

Replace `if (!sessionId)` guards with `if (!contextKey)`:
- Line 1325: `if (!containerRef.value || !contextKey) return`
- Line 1510: `if (containerRef.value && contextKey)`
- Line 1722: `if (el && !terminal && started.value && contextKey)`

- [ ] **Step 4: Update `shouldUseTmux()`**

```js
function shouldUseTmux() {
    if (!settingsStore.isTerminalUseTmux) return false
    if (sessionId) {
        const session = dataStore.getSession(sessionId)
        if (session?.draft || session?.archived) return false
    }
    return true
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/useTerminal.js
git commit -m "refactor: generalize useTerminal from sessionId to contextKey"
```

---

### Task 7: Frontend — Update `SessionView.vue` to pass contextKey

**Files:**
- Modify: `frontend/src/views/SessionView.vue:1084-1088`

- [ ] **Step 1: Update TerminalPanel usage**

```html
<wa-tab-panel name="terminal">
    <TerminalPanel
        :context-key="`s:${session.id}`"
        :session-id="session.id"
        :project-id="session.project_id"
        :active="isActive && activeTabId === 'terminal'"
    />
</wa-tab-panel>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "feat: pass contextKey to TerminalPanel in SessionView"
```

---

### Task 8: Frontend — Wire TerminalPanel into ProjectDetailPanel

**Files:**
- Modify: `frontend/src/components/ProjectDetailPanel.vue`

- [ ] **Step 1: Import TerminalPanel**

Add import:
```js
import TerminalPanel from './TerminalPanel.vue'
```

- [ ] **Step 2: Compute the terminal context key**

```js
import { ALL_PROJECTS_ID } from '../stores/data'
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'

const terminalContextKey = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID) {
        return 'global'
    }
    if (isWorkspaceProjectId(props.projectId)) {
        return `w:${extractWorkspaceId(props.projectId)}`
    }
    return `p:${props.projectId}`
})

// For project terminals, pass the real project ID (not workspace/all-projects pseudo-IDs)
const terminalProjectId = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(props.projectId)) {
        return null
    }
    return props.projectId
})

// For workspace terminals, compute the lowest common ancestor of all project directories
const terminalCwd = computed(() => {
    if (!isWorkspaceMode.value || !workspaceProjectIds.value) return null
    const dirs = workspaceProjectIds.value
        .map(pid => dataStore.getProject(pid))
        .map(p => p?.directory)
        .filter(Boolean)
    if (dirs.length === 0) return null
    if (dirs.length === 1) return dirs[0]
    // Find the longest common path prefix
    const parts = dirs.map(d => d.split('/'))
    const common = []
    for (let i = 0; i < parts[0].length; i++) {
        const segment = parts[0][i]
        if (parts.every(p => p[i] === segment)) {
            common.push(segment)
        } else {
            break
        }
    }
    return common.length > 1 ? common.join('/') : '/'
})
```

- [ ] **Step 3: Replace the placeholder with TerminalPanel**

```html
<wa-tab-panel name="terminal">
    <TerminalPanel
        :context-key="terminalContextKey"
        :project-id="terminalProjectId"
        :cwd="terminalCwd"
        :active="activeTab === 'terminal'"
    />
</wa-tab-panel>
```

- [ ] **Step 4: Remove the `.terminal-placeholder` CSS**

Delete the `.terminal-placeholder` style block (was `.dummy-content`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ProjectDetailPanel.vue
git commit -m "feat: wire TerminalPanel into ProjectDetailPanel for project/global terminals"
```

---

### Task 9: Cleanup — Remove dead `project_id = '_'` fallback

**Files:**
- Modify: `src/twicc/asgi.py:602-604`

- [ ] **Step 1: Remove the `_` placeholder logic**

Since `project_id` now comes from URL route kwargs (present or absent, never `_`), remove:
```python
    # A placeholder "_" is sent by the frontend when no project is associated
    if project_id == "_":
        project_id = None
```

This is no longer needed — the frontend will use the correct URL route (with or without project_id).

- [ ] **Step 2: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "cleanup: remove dead project_id='_' placeholder logic"
```

---

## Notes

- **KeepAlive already in place:** `ProjectDetailPanel` is wrapped in `<KeepAlive :key="effectiveProjectId">` in `ProjectView.vue`, with `active` prop and `onActivated`/`onDeactivated` lifecycle hooks. Each project/workspace/global gets its own cached instance. Terminal connections survive when navigating to a session and back.
- **Workspace terminals:** Each workspace gets its own tmux session (`w:<workspace_id>`). The frontend computes the lowest common ancestor directory of all projects in the workspace and passes it as a `cwd` query string parameter. The backend only accepts this `cwd` when a `name` param is also present. The backend has no knowledge of workspaces — it just uses the name for tmux and the cwd for the shell.
- **Session tmux backward compatibility:** Session terminals keep the existing tmux naming scheme (`twicc-<session_id>`) — no prefix change — so existing tmux sessions continue working without orphaning.
- **tmux naming:** All tmux names are prefixed with `twicc-`. Project terminals use `twicc-p_<project_id>`, workspace terminals use `twicc-w_<workspace_id>`, global uses `twicc-global`. These are distinct from session naming (`twicc-<uuid>`) thanks to the `p_`/`w_` prefixes.
- **Backward compatibility:** The old URL route `/ws/terminal/<project_id>/<session_id>/<index>/` remains. No existing functionality breaks.
- **Terminal snippets:** The snippet resolution logic depends on the terminal context. Global terminals only get global snippets. Workspace terminals get global + workspace + all projects in the workspace. Project/session terminals get global + workspace + project snippets.
