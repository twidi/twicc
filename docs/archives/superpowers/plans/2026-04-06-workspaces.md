# Workspaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace support — named groups of projects that let users focus on a subset of their projects, with full CRUD, sync across devices, and workspace-aware navigation.

**Architecture:** Backend stores workspace definitions in `~/.twicc/workspaces.json`, synced via WebSocket (same pattern as synced_settings). Frontend adds a `workspaces` Pinia store, a navigation guard that propagates `?workspace=` in the URL, and UI components for management and display. The `/api/sessions/` endpoint gains a `?project_ids=` filter for efficient workspace session loading.

**Spec:** `docs/superpowers/specs/2026-04-06-workspaces.md`

**Tech Stack:** Django/ASGI backend, Vue 3 + Pinia frontend, Web Awesome components, WebSocket sync

**Important project conventions:**
- No tests, no linting (per CLAUDE.md)
- All code content in English (UI strings, comments, variables)
- Use `orjson` for JSON in backend
- Vue Composition API with `<script setup>`
- Web Awesome 3.1 components (native events without `wa-` prefix, custom events with `wa-` prefix)
- Each WA component used must be imported in `frontend/src/main.js`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `src/twicc/workspaces.py` | Read/write `workspaces.json` (atomic file I/O) |
| `frontend/src/stores/workspaces.js` | Pinia store: workspace state, getters, CRUD actions |
| `frontend/src/utils/workspaceIds.js` | Helpers: `WORKSPACE_PREFIX`, `isWorkspaceProjectId()`, `toWorkspaceProjectId()`, `extractWorkspaceId()` |
| `frontend/src/utils/projectSort.js` | Utility: split projects into workspace-first + others |
| `frontend/src/components/WorkspaceManageDialog.vue` | Dialog: list view + edit view for workspace CRUD |
| `frontend/src/components/WorkspaceCard.vue` | Card component for workspace display on Home page |

### Modified files

| File | Changes |
|------|---------|
| `src/twicc/paths.py` | Add `get_workspaces_path()` |
| `src/twicc/asgi.py` | Add WS handler `_handle_update_workspaces`, send on connect, dispatch |
| `src/twicc/views.py` | Add `?project_ids=` parameter to `all_sessions` / `_get_sessions_page` |
| `frontend/src/composables/useWebSocket.js` | Handle `workspaces_updated` message, add `sendWorkspaces` function |
| `frontend/src/router.js` | Navigation guard: propagate `?workspace=` query param |
| `frontend/src/stores/data.js` | `_fetchSessionsPage`: pass `project_ids`. New getter helpers. |
| `frontend/src/stores/settings.js` | Add `showArchivedWorkspaces` setting |
| `frontend/src/utils/projectSort.js` | New utility for workspace-first project sorting |
| `frontend/src/components/ProjectSelectOptions.vue` | Add `priorityProjectIds` prop for workspace-first ordering |
| `frontend/src/views/HomeView.vue` | Add workspaces section before project list |
| `frontend/src/components/ProjectList.vue` | Minor: pass through workspace context if needed |
| `frontend/src/views/ProjectView.vue` | Rework project selector, workspace-aware `effectiveProjectId`, session loading |
| `frontend/src/components/SessionList.vue` | Workspace filtering on `baseSessions` |
| `frontend/src/components/TerminalSnippetsDialog.vue` | Workspace-first project ordering in scope selector |
| `frontend/src/components/MessageSnippetsDialog.vue` | Same as above |
| `frontend/src/components/SearchOverlay.vue` | Workspace-first ordering in project filter |
| `frontend/src/main.js` | Import any new WA components needed |

---

## Task 1: Backend — `workspaces.py` and `paths.py`

**Files:**
- Create: `src/twicc/workspaces.py`
- Modify: `src/twicc/paths.py:84` (add new path function after `get_synced_settings_path`)

- [ ] **Step 1: Add `get_workspaces_path()` to `paths.py`**

In `src/twicc/paths.py`, add after the existing `get_synced_settings_path()` (line 84):

```python
def get_workspaces_path() -> Path:
    """Path to the workspaces definition file."""
    return get_data_dir() / "workspaces.json"
```

- [ ] **Step 2: Create `workspaces.py`**

Create `src/twicc/workspaces.py`, following the exact pattern from `synced_settings.py`:

```python
"""Read/write workspaces.json — atomic file I/O.

Follow the exact same pattern as synced_settings.py.
"""

import os
import tempfile

import orjson

from twicc.paths import get_workspaces_path


def read_workspaces() -> dict:
    """Read workspaces from disk. Returns {} on missing or corrupt file."""
    path = get_workspaces_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}


def write_workspaces(data: dict) -> None:
    """Atomically write workspaces to disk."""
    path = get_workspaces_path()
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/workspaces.py src/twicc/paths.py
git commit -m "feat(workspaces): add workspaces.json read/write backend"
```

---

## Task 2: Backend — WebSocket handler in `asgi.py`

**Files:**
- Modify: `src/twicc/asgi.py`

Reference: the `_handle_update_synced_settings` handler (line 1134) and the `synced_settings_updated` send on connect (lines 621-624).

- [ ] **Step 1: Add import**

At the top of `asgi.py`, in the imports section, add:

```python
from .workspaces import read_workspaces, write_workspaces
```

- [ ] **Step 2: Send workspaces on connect**

In the `connect()` method, after the `synced_settings_updated` block (around line 624), add:

```python
if self._should_send("workspaces_updated"):
    workspaces = await sync_to_async(read_workspaces)()
    await self.send_json({"type": "workspaces_updated", "workspaces": workspaces.get("workspaces", [])})
```

- [ ] **Step 3: Add dispatch case in `receive_json`**

In `receive_json()`, in the `elif` chain (around line 699), add a new case:

```python
elif msg_type == "update_workspaces":
    await self._handle_update_workspaces(content)
```

- [ ] **Step 4: Implement `_handle_update_workspaces`**

Add the handler method to `UpdatesConsumer`, following the pattern of `_handle_update_synced_settings`:

```python
async def _handle_update_workspaces(self, content: dict) -> None:
    """Handle workspace definitions update from a client."""
    workspaces = content.get("workspaces")
    if not isinstance(workspaces, list):
        return

    def _write():
        write_workspaces({"workspaces": workspaces})

    await sync_to_async(_write)()

    await self.channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {"type": "workspaces_updated", "workspaces": workspaces},
        },
    )
```

- [ ] **Step 5: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat(workspaces): add WebSocket handler for workspace sync"
```

---

## Task 3: Backend — `?project_ids=` filter on `/api/sessions/`

**Files:**
- Modify: `src/twicc/views.py:32-71`

- [ ] **Step 1: Modify `_get_sessions_page`**

Add a `project_id_list` parameter to `_get_sessions_page` (line 32):

```python
def _get_sessions_page(project_id: str | None, before_mtime: str | None, project_id_list: list[str] | None = None) -> dict:
```

In the function body, after the `if project_id is not None:` block (around line 41), add:

```python
    elif project_id_list is not None:
        sessions = sessions.filter(project_id__in=project_id_list)
```

- [ ] **Step 2: Modify `all_sessions` to parse `project_ids`**

In `all_sessions()` (line 62), add parsing of the query parameter:

```python
def all_sessions(request):
    before_mtime = request.GET.get("before_mtime")
    project_ids_param = request.GET.get("project_ids")
    project_id_list = project_ids_param.split(",") if project_ids_param else None
    return JsonResponse(_get_sessions_page(None, before_mtime, project_id_list=project_id_list))
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat(workspaces): add project_ids filter to /api/sessions/ endpoint"
```

---

## Task 4: Frontend — Workspaces Pinia store

**Files:**
- Create: `frontend/src/stores/workspaces.js`

- [ ] **Step 1: Create the store**

```javascript
import { defineStore } from 'pinia'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from './settings'
import { useDataStore } from './data'

export const useWorkspacesStore = defineStore('workspaces', {
    state: () => ({
        workspaces: [],           // Array of { id, name, archived, projectIds: string[] }
        _isApplyingRemote: false, // Guard to prevent echo on WS receive
    }),

    getters: {
        /** All workspaces, in their stored order. */
        getAllWorkspaces: (state) => state.workspaces,

        /** Get a workspace by its ID. */
        getWorkspaceById: (state) => (id) =>
            state.workspaces.find(w => w.id === id) || null,

        /**
         * Visible project IDs for a given workspace, respecting "show archived projects".
         * Returns the projects in their custom order, filtered to only visible ones.
         */
        getVisibleProjectIds() {
            return (workspaceId) => {
                const ws = this.getWorkspaceById(workspaceId)
                if (!ws) return []
                const dataStore = useDataStore()
                const settingsStore = useSettingsStore()
                const showArchived = settingsStore.isShowArchivedProjects
                return ws.projectIds.filter(pid => {
                    const project = dataStore.getProject(pid)
                    return project && (showArchived || !project.archived)
                })
            }
        },

        /** Whether a workspace is activable (has at least one visible project). */
        isActivable() {
            return (workspaceId) => this.getVisibleProjectIds(workspaceId).length > 0
        },

        /** Non-archived workspaces that are activable. For use in selectors. */
        getSelectableWorkspaces() {
            const settingsStore = useSettingsStore()
            const showArchivedWs = settingsStore.isShowArchivedWorkspaces
            return this.workspaces.filter(ws =>
                (showArchivedWs || !ws.archived) && this.isActivable(ws.id)
            )
        },

        /** Whether any archived workspace exists (to show/hide the toggle). */
        hasArchivedWorkspaces: (state) => state.workspaces.some(w => w.archived),
    },

    actions: {
        /** Apply workspaces received from the backend (WS message). */
        applyWorkspaces(workspaces) {
            this._isApplyingRemote = true
            this.workspaces = workspaces || []
            this._isApplyingRemote = false
        },

        /** Send current workspaces to the backend via WebSocket. */
        async _sendWorkspaces() {
            if (this._isApplyingRemote) return
            const { sendWorkspaces } = await import('../composables/useWebSocket')
            sendWorkspaces(this.workspaces)
        },

        /** Generate a unique workspace ID. */
        _generateId() {
            return 'ws_' + crypto.randomUUID().replace(/-/g, '').slice(0, 12)
        },

        /** Create a new workspace. Returns the new workspace object. */
        createWorkspace({ name, projectIds = [], archived = false }) {
            const ws = {
                id: this._generateId(),
                name: name.trim(),
                archived,
                projectIds,
            }
            this.workspaces.push(ws)
            this._sendWorkspaces()
            return ws
        },

        /** Update an existing workspace. */
        updateWorkspace(id, { name, projectIds, archived }) {
            const ws = this.workspaces.find(w => w.id === id)
            if (!ws) return
            if (name !== undefined) ws.name = name.trim()
            if (projectIds !== undefined) ws.projectIds = projectIds
            if (archived !== undefined) ws.archived = archived
            this._sendWorkspaces()
        },

        /** Delete a workspace by ID. */
        deleteWorkspace(id) {
            const index = this.workspaces.findIndex(w => w.id === id)
            if (index === -1) return
            this.workspaces.splice(index, 1)
            this._sendWorkspaces()
        },

        /** Reorder workspaces (move from fromIndex to toIndex). */
        reorderWorkspace(fromIndex, toIndex) {
            if (toIndex < 0 || toIndex >= this.workspaces.length) return
            const [item] = this.workspaces.splice(fromIndex, 1)
            this.workspaces.splice(toIndex, 0, item)
            this._sendWorkspaces()
        },
    },
})
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/workspaces.js
git commit -m "feat(workspaces): add workspaces Pinia store"
```

---

## Task 5: Frontend — WebSocket handler for `workspaces_updated`

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Add `sendWorkspaces` export function**

Near the other send functions (around `sendSyncedSettings` at line 264), add:

```javascript
export function sendWorkspaces(workspaces) {
    sendWsMessage({ type: 'update_workspaces', workspaces })
}
```

- [ ] **Step 2: Add message handler in `handleMessage`**

In the `switch (msg.type)` block, after the `synced_settings_updated` case (around line 738), add:

```javascript
case 'workspaces_updated': {
    const { useWorkspacesStore } = await import('../stores/workspaces')
    useWorkspacesStore().applyWorkspaces(msg.workspaces)
    break
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(workspaces): add WebSocket handler for workspace sync"
```

---

## Task 6: Frontend — `showArchivedWorkspaces` setting

**Files:**
- Modify: `frontend/src/stores/settings.js`

- [ ] **Step 1: Add setting to schema, validator, getter, and setter**

Four places to modify in `settings.js`:

1. In `SETTINGS_SCHEMA` (around line 39, after `showArchivedProjects`), add:
```javascript
showArchivedWorkspaces: false,
```

2. In `SETTINGS_VALIDATORS` (around line 91, after `showArchivedProjects` validator), add:
```javascript
showArchivedWorkspaces: (v) => typeof v === 'boolean',
```

3. In the getters section (near `isShowArchivedProjects`), add:
```javascript
isShowArchivedWorkspaces: (state) => state.showArchivedWorkspaces,
```

4. In the actions section (near `setShowArchivedProjects`), add:
```javascript
setShowArchivedWorkspaces(value) {
    this.showArchivedWorkspaces = value
},
```

**Note:** The setting is automatically persisted to localStorage by the existing settings watcher in `initSettings()` — the watcher saves all keys from `SETTINGS_SCHEMA` that don't start with `_`. Verify this by reading the watcher code (around line 665-708) to confirm it uses `Object.keys(SETTINGS_SCHEMA)` dynamically rather than a hardcoded list.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/settings.js
git commit -m "feat(workspaces): add showArchivedWorkspaces setting"
```

---

## Task 7: Frontend — Router navigation guard

**Files:**
- Modify: `frontend/src/router.js`

This is the critical piece that auto-propagates `?workspace=` across navigations.

- [ ] **Step 1: Add the workspace propagation guard**

In `router.js`, after the existing `beforeEach` guard (which ends around line 113), add a new guard:

```javascript
// Propagate workspace query param across navigations.
// The workspace is preserved unless:
// - The destination explicitly sets/clears ?workspace= (e.g., switching workspaces)
// - The destination project is not in the workspace (e.g., following a search result to another project)
// - Navigating to the home page or a route without a project context
router.beforeEach(async (to, from) => {
    const currentWs = from.query?.workspace
    if (!currentWs) return                          // No workspace active
    if (to.query.workspace !== undefined) return     // Destination explicitly sets/clears

    // Check if the destination has a projectId (works for both /project/:id and /projects/:id/session/:id routes)
    const targetProjectId = to.params?.projectId
    if (targetProjectId) {
        // Any route with a projectId: keep workspace only if the project belongs to it
        const { useWorkspacesStore } = await import('./stores/workspaces')
        const wsStore = useWorkspacesStore()
        const ws = wsStore.getWorkspaceById(currentWs)
        if (ws?.projectIds.includes(targetProjectId)) {
            return { ...to, query: { ...to.query, workspace: currentWs } }
        }
        // Project not in workspace → workspace dropped
        return
    }

    // Routes without a projectId:
    // - projects-all → keep workspace (going to workspace view)
    // - home, login, etc. → drop workspace
    if (to.name === 'projects-all') {
        return { ...to, query: { ...to.query, workspace: currentWs } }
    }
    // All other routes (home, login, etc.) → workspace dropped
})
```

**Key design choice:** The guard checks `targetProjectId` FIRST for all routes (including `projects-session`, `projects-session-subagent`, etc.). This correctly handles the case where a user navigates to a session whose project is NOT in the workspace (e.g., from global search) — the workspace is dropped because the project doesn't belong to it. Only `projects-all` (the workspace view with no specific project) keeps the workspace unconditionally.

Note: uses lazy `await import()` for the store to avoid circular imports (per CLAUDE.md rules).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/router.js
git commit -m "feat(workspaces): add navigation guard for workspace URL propagation"
```

---

## Task 8: Frontend — Workspace ID helpers

**Files:**
- Create: `frontend/src/utils/workspaceIds.js`

These helpers centralize the `ws:` prefix convention so it's not scattered across multiple files as raw string operations.

- [ ] **Step 1: Create the utility**

```javascript
/**
 * Convention: when a workspace is used as an effectiveProjectId in the data store,
 * it is encoded as "ws:<workspaceId>". These helpers centralize this convention.
 */

export const WORKSPACE_PREFIX = 'ws:'

export function isWorkspaceProjectId(id) {
    return typeof id === 'string' && id.startsWith(WORKSPACE_PREFIX)
}

export function toWorkspaceProjectId(workspaceId) {
    return WORKSPACE_PREFIX + workspaceId
}

export function extractWorkspaceId(workspaceProjectId) {
    return workspaceProjectId.slice(WORKSPACE_PREFIX.length)
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/utils/workspaceIds.js
git commit -m "feat(workspaces): add workspace ID prefix helpers"
```

---

## Task 9: Frontend — Project sort utility

**Files:**
- Create: `frontend/src/utils/projectSort.js`

- [ ] **Step 1: Create the utility**

```javascript
/**
 * Split and sort projects for workspace-first display.
 *
 * @param {Array} projects - All projects to display
 * @param {Array|null} priorityProjectIds - Ordered array of workspace project IDs (null = no workspace)
 * @param {Set|null} visibleProjectIdSet - Set of visible project IDs within workspace (null = use all from priorityProjectIds)
 * @returns {{ prioritized: Array, others: Array }} Two arrays: workspace projects (in custom order), then the rest
 */
export function splitProjectsByPriority(projects, priorityProjectIds, visibleProjectIdSet = null) {
    if (!priorityProjectIds?.length) {
        return { prioritized: [], others: projects }
    }

    const prioritySet = visibleProjectIdSet || new Set(priorityProjectIds)
    const othersMap = new Map(projects.map(p => [p.id, p]))

    // Build prioritized list in workspace custom order, only including visible ones
    const prioritized = []
    for (const pid of priorityProjectIds) {
        if (!prioritySet.has(pid)) continue
        const project = othersMap.get(pid)
        if (project) {
            prioritized.push(project)
            othersMap.delete(pid)
        }
    }

    return { prioritized, others: Array.from(othersMap.values()) }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/utils/projectSort.js
git commit -m "feat(workspaces): add project sort utility for workspace-first ordering"
```

---

## Task 9: Frontend — Data store modifications

**Files:**
- Modify: `frontend/src/stores/data.js`

- [ ] **Step 1: Modify `_fetchSessionsPage` to accept `projectIds`**

In `_fetchSessionsPage` (around line 981), modify to accept and pass workspace project IDs:

Find the section that builds the URL (around lines 985-988):
```javascript
const isAllProjects = projectId === ALL_PROJECTS_ID
const baseUrl = isAllProjects
    ? '/api/sessions/'
    : `/api/projects/${projectId}/sessions/`
```

Replace with (using helpers from `utils/workspaceIds.js`):
```javascript
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'

// ...

const isMultiProject = projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)
let baseUrl
if (isMultiProject) {
    baseUrl = '/api/sessions/'
} else {
    baseUrl = `/api/projects/${projectId}/sessions/`
}
```

Then, where the URL params are built (the `before_mtime` handling), add the `project_ids` parameter for workspace mode:

```javascript
// After building params with before_mtime
if (isWorkspaceProjectId(projectId)) {
    const wsId = extractWorkspaceId(projectId)
    const { useWorkspacesStore } = await import('./workspaces')
    const wsStore = useWorkspacesStore()
    const visibleIds = wsStore.getVisibleProjectIds(wsId)
    if (visibleIds.length) {
        params.set('project_ids', visibleIds.join(','))
    }
}
```

The exact integration will depend on how the URL is currently assembled — read the surrounding code carefully. The key is: when `projectId` is a workspace ID (`ws:xxx`), use `/api/sessions/?project_ids=id1,id2,...`.

**Pagination cache invalidation:** When the workspace's project list changes (project added/removed), the pagination state in `localState.projects['ws:xxx']` becomes stale. Add a mechanism to invalidate it — for example, in `ProjectView.vue`, reset the pagination state when `workspaceVisibleProjectIds` changes by calling `store.loadSessions(effectiveProjectId, { force: true })`.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(workspaces): add workspace session loading with project_ids filter"
```

---

## Task 10: Frontend — `ProjectSelectOptions.vue` workspace-first ordering

**Files:**
- Modify: `frontend/src/components/ProjectSelectOptions.vue`

**Important:** This component renders `<wa-option>` elements for use inside `<wa-select>`. It is used by `SearchOverlay.vue` and the "New session" dropdowns (which use `<wa-dropdown>` — see note below). It is NOT used by the sidebar project selector (which is being replaced with a `<wa-dropdown>` in Task 14 that renders its own `<wa-dropdown-item>` elements).

Note: the "New session" dropdowns in ProjectView actually use `<wa-dropdown-item>` elements inline, not `ProjectSelectOptions`. So this component's changes primarily affect `SearchOverlay`.

- [ ] **Step 1: Add `priorityProjectIds` prop**

Add to props:

```javascript
priorityProjectIds: { type: Array, default: null },
```

- [ ] **Step 2: Use `splitProjectsByPriority` to reorder**

Import the utility and modify the computed that processes projects to split them into workspace-first and others groups. Add a divider between the two groups in the template.

When `priorityProjectIds` is set:
1. Use `splitProjectsByPriority(projects, priorityProjectIds)` to get `{ prioritized, others }`
2. Render `prioritized` projects first as flat `<wa-option>` elements (in custom order, with `ProjectBadge`)
3. Render a `<wa-divider>` if both groups are non-empty
4. Render `others` using the existing named/unnamed split logic

When `priorityProjectIds` is null: render exactly as today (no change).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ProjectSelectOptions.vue
git commit -m "feat(workspaces): add workspace-first project ordering to ProjectSelectOptions"
```

---

## Task 11: Frontend — Workspace management dialog

**Files:**
- Create: `frontend/src/components/WorkspaceManageDialog.vue`

This is the largest new component. Follow the pattern from `TerminalSnippetsDialog.vue`: a `view` ref that switches between `'list'` and `'form'`.

- [ ] **Step 1: Create dialog skeleton with list view**

Create `WorkspaceManageDialog.vue` with:
- `<wa-dialog>` wrapper with proper label, event guards (bubbling fix from TerminalSnippetsDialog pattern)
- `defineExpose({ open, close, openForWorkspace })` — `openForWorkspace(id)` opens directly to the edit view for a specific workspace
- `view` ref: `'list'` or `'form'`
- List view: iterate `workspacesStore.getAllWorkspaces`, filtered by `showArchivedWorkspaces` toggle
- Each entry: name + archived indicator + reorder arrows + edit button + delete button
- Footer: "Close" + "New workspace" buttons
- "Show archived workspaces" toggle at the top of the list

- [ ] **Step 2: Add edit/create form view**

The form view contains:
- Name input field
- Archived toggle switch
- Project list with reorder arrows and remove buttons
- "Add project" button that opens an inline `<wa-select>` or `<wa-dropdown>` showing available projects (excluding those already in the workspace), with `ProjectSelectOptions`
- Footer: "Cancel" + "Save" buttons

Local form state (`formData` ref) is populated on open and only persisted to the store on "Save". This is different from the snippets dialog which saves immediately — here we buffer changes.

```javascript
const formData = ref({
    id: null,          // null for create mode
    name: '',
    archived: false,
    projectIds: [],    // local copy, manipulated freely
})
```

Validation on save:
- Name must be non-empty after trim
- Name must be unique (excluding self when editing)

On save:
- Create mode: `workspacesStore.createWorkspace(formData.value)`
- Edit mode: `workspacesStore.updateWorkspace(formData.value.id, formData.value)`
- Switch back to list view

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/WorkspaceManageDialog.vue
git commit -m "feat(workspaces): add workspace management dialog"
```

---

## Task 12: Frontend — Workspace card component

**Files:**
- Create: `frontend/src/components/WorkspaceCard.vue`

- [ ] **Step 1: Create the component**

Props: `workspace` (object, required)

Displays:
- Workspace name
- Project badges of visible projects (using `ProjectBadge` component)
- Aggregate session count (sum of `sessions_count` across visible workspace projects)
- "Archived" tag if `workspace.archived`
- Context menu dropdown (click.stop to prevent card click): "Manage" and "Archive"/"Unarchive"

Emits: `select`, `menu-select`

When the workspace is not activable (no visible projects), add a `disabled` class that greys out the card and prevents click.

Style: follow `ProjectCard.vue` pattern (wa-card, outlined appearance, hover lift).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/WorkspaceCard.vue
git commit -m "feat(workspaces): add WorkspaceCard component for Home page"
```

---

## Task 13: Frontend — Home page workspaces section

**Files:**
- Modify: `frontend/src/views/HomeView.vue`
- Modify: `frontend/src/components/ProjectList.vue` (minor)

- [ ] **Step 1: Add workspace imports and data to HomeView**

Import `WorkspaceCard`, `WorkspaceManageDialog`, and `useWorkspacesStore`. Add computeds for:
- `nonArchivedWorkspaces` — workspaces where `!ws.archived`
- `archivedWorkspacesExist` — `workspacesStore.hasArchivedWorkspaces`
- `showArchivedWorkspaces` — from settings store
- `visibleWorkspaces` — filtered by archive toggle

- [ ] **Step 2: Add workspace section to template**

Before the `<ProjectList>` component (line 101), add the workspaces section:

```html
<!-- Workspaces section -->
<template v-if="visibleWorkspaces.length || workspacesStore.getAllWorkspaces.length">
    <div class="section-header">
        Workspaces
        <wa-switch
            v-if="archivedWorkspacesExist"
            size="small"
            :checked="showArchivedWorkspaces"
            @change="handleToggleShowArchivedWorkspaces"
        >
            Show archived
        </wa-switch>
    </div>
    <div class="workspace-cards">
        <WorkspaceCard
            v-for="ws in visibleWorkspaces"
            :key="ws.id"
            :workspace="ws"
            @select="handleWorkspaceSelect"
            @menu-select="handleWorkspaceMenuSelect"
        />
    </div>
</template>
<!-- "Manage workspaces" link: always visible -->
<wa-button ... @click="manageDialogRef?.open()">
    Manage workspaces
</wa-button>

<WorkspaceManageDialog ref="manageDialogRef" />
```

- [ ] **Step 3: Add handlers**

```javascript
function handleWorkspaceSelect(workspace) {
    router.push({ name: 'projects-all', query: { workspace: workspace.id } })
}

function handleWorkspaceMenuSelect(event, workspace) {
    const item = event.detail?.item
    if (item?.value === 'manage') {
        manageDialogRef.value?.openForWorkspace(workspace.id)
    } else if (item?.value === 'archive') {
        workspacesStore.updateWorkspace(workspace.id, { archived: true })
    } else if (item?.value === 'unarchive') {
        workspacesStore.updateWorkspace(workspace.id, { archived: false })
    }
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/HomeView.vue frontend/src/components/ProjectList.vue
git commit -m "feat(workspaces): add workspaces section to Home page"
```

---

## Task 14: Frontend — Project selector rework in ProjectView

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

This is the most complex task. The `<wa-select>` (lines 756-774) needs to be replaced with a `<wa-dropdown>` to support the multi-section layout with multiple highlighted items.

- [ ] **Step 1: Add workspace-related computeds**

Add to the `<script setup>` section:

```javascript
import { useWorkspacesStore } from '../stores/workspaces'

const workspacesStore = useWorkspacesStore()
const activeWorkspaceId = computed(() => route.query.workspace || null)
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)
const isWorkspaceMode = computed(() => isAllProjectsMode.value && !!activeWorkspaceId.value)
const workspaceVisibleProjectIds = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getVisibleProjectIds(activeWorkspaceId.value) : []
)
```

- [ ] **Step 2: Modify `effectiveProjectId`**

Change `effectiveProjectId` (line 184-186) to account for workspace mode. Use the helper:

```javascript
import { toWorkspaceProjectId } from '../utils/workspaceIds'

const effectiveProjectId = computed(() => {
    if (!isAllProjectsMode.value) return projectId.value
    if (activeWorkspaceId.value) return toWorkspaceProjectId(activeWorkspaceId.value)
    return ALL_PROJECTS_ID
})
```

- [ ] **Step 3: Replace `<wa-select>` with `<wa-dropdown>` for the project selector**

**Critical note:** The existing `<wa-select>` renders `<wa-option>` elements (via `ProjectSelectOptions`). The new `<wa-dropdown>` renders `<wa-dropdown-item>` elements. These are NOT interchangeable — `ProjectSelectOptions` cannot be used inside a `<wa-dropdown>`. The sidebar selector must render its own `<wa-dropdown-item>` elements inline.

`ProjectSelectOptions` (which renders `<wa-option>`) is still used by `SearchOverlay` (which uses `<wa-select>`), so it remains unchanged for that purpose.

Replace the entire `<wa-select id="project-selector">` block (lines 756-774) with a `<wa-dropdown>` that renders the sections described in the spec using `<wa-dropdown-item>` elements:

1. "All Projects" item
2. Divider + Workspaces section (selectable workspaces + "Manage workspaces...")
3. Divider + Workspace projects sub-section (if workspace active): "All projects" + individual projects in custom order
4. Divider + Other projects (named first, then unnamed tree — replicate the rendering logic that was previously in `ProjectSelectOptions`, but with `<wa-dropdown-item>` elements)

The trigger button should display the current context:
- "All Projects" when in all-projects mode without workspace
- Workspace name when workspace is active (workspace view or single-project within workspace)
- Show the project badge when viewing a single project

Each `<wa-dropdown-item>` needs proper `value` attributes to distinguish the action type. Use a `@wa-select` handler on the `<wa-dropdown>`.

- [ ] **Step 4: Rewrite `handleProjectChange` as `handleSelectorSelect`**

Replace the old handler. **Critical:** preserve the existing session-preserving logic from the current `handleProjectChange` (lines 313-340). When switching between projects or modes while a session is open, the session should remain visible if it belongs to the target scope.

```javascript
function handleSelectorSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return

    if (value === ALL_PROJECTS_ID) {
        // Switch to All Projects, clear workspace
        if (sessionId.value && projectId.value) {
            // Preserve current session
            router.push({ name: 'projects-session', params: { projectId: projectId.value, sessionId: sessionId.value }, query: {} })
        } else {
            router.push({ name: 'projects-all', query: {} })
        }
    } else if (value.startsWith('workspace:')) {
        const wsId = value.slice('workspace:'.length)
        if (sessionId.value && projectId.value) {
            // Preserve current session if its project is in the target workspace
            const ws = workspacesStore.getWorkspaceById(wsId)
            if (ws?.projectIds.includes(projectId.value)) {
                router.push({ name: 'projects-session', params: { projectId: projectId.value, sessionId: sessionId.value }, query: { workspace: wsId } })
            } else {
                router.push({ name: 'projects-all', query: { workspace: wsId } })
            }
        } else {
            router.push({ name: 'projects-all', query: { workspace: wsId } })
        }
    } else if (value === '__manage_workspaces__') {
        manageWorkspacesDialogRef.value?.open()
    } else if (value === 'ws-all') {
        // "All projects" within workspace view
        router.push({ name: 'projects-all', query: { workspace: activeWorkspaceId.value } })
    } else {
        // Regular project selection — navigation guard handles workspace propagation
        const targetProjectId = value
        if (sessionId.value && projectId.value === targetProjectId) {
            // Session belongs to target project → preserve it
            router.push({ name: 'session', params: { projectId: targetProjectId, sessionId: sessionId.value } })
        } else {
            router.push({ name: 'project', params: { projectId: targetProjectId } })
        }
    }
}
```

Note: explicitly passing `query: {}` when clearing workspace prevents the navigation guard from re-adding it (since `to.query.workspace` will be `undefined` but the empty object signals intent).

- [ ] **Step 5: Pass workspace context to SessionList**

Modify the SessionList props (line 858):

```html
<SessionList
    ...
    :show-project-name="isAllProjectsMode && (!activeWorkspace || workspaceVisibleProjectIds.length > 1)"
    ...
/>
```

- [ ] **Step 6: Pass workspace-first ordering to "New session" dropdowns**

The `nonStaleNamedProjects` and `nonStaleFlatTree` computeds (lines 192-201) need to respect workspace ordering. Use `splitProjectsByPriority` to reorder them when a workspace is active.

- [ ] **Step 7: Add workspace management dialog ref**

Add to template:
```html
<WorkspaceManageDialog ref="manageWorkspacesDialogRef" />
```

- [ ] **Step 8: Handle workspace becoming non-activable, deleted, or archived**

Add a watcher that checks if the active workspace should still be active. This covers:
- Workspace deleted (by current user or from another device via WS)
- Workspace becomes non-activable (last visible project archived)
- Workspace archived while "Show archived workspaces" is off

```javascript
watch(
    [activeWorkspaceId, () => workspacesStore.workspaces, () => settingsStore.isShowArchivedWorkspaces, () => settingsStore.isShowArchivedProjects],
    () => {
        if (!activeWorkspaceId.value) return
        const ws = workspacesStore.getWorkspaceById(activeWorkspaceId.value)
        const shouldClear = (
            !ws ||                                                          // deleted
            !workspacesStore.isActivable(ws.id) ||                          // no visible projects
            (ws.archived && !settingsStore.isShowArchivedWorkspaces)         // archived + toggle off
        )
        if (shouldClear) {
            router.replace({ name: 'projects-all', query: {} })
        }
    }
)
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "feat(workspaces): rework project selector with workspace support"
```

---

## Task 15: Frontend — SessionList workspace filtering

**Files:**
- Modify: `frontend/src/components/SessionList.vue`

- [ ] **Step 1: Modify `baseSessions` computed**

The current code (lines 50-53):
```javascript
const baseSessions = props.projectId === ALL_PROJECTS_ID
    ? store.getAllSessions
    : store.getProjectSessions(props.projectId)
```

Replace with:
```javascript
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'

const baseSessions = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        // Workspace mode: get all sessions, filter to workspace projects
        const wsId = extractWorkspaceId(props.projectId)
        const wsStore = useWorkspacesStore()
        const visibleIds = new Set(wsStore.getVisibleProjectIds(wsId))
        return store.getAllSessions.filter(s => visibleIds.has(s.project_id))
    }
    if (props.projectId === ALL_PROJECTS_ID) {
        return store.getAllSessions
    }
    return store.getProjectSessions(props.projectId)
})
```

Note: import `useWorkspacesStore` at the top of the script. The `getAllSessions` getter returns all sessions from the store — the workspace filtering here is client-side on the already-loaded data. The backend filtering (via `?project_ids=`) ensures only relevant sessions are fetched in the first place.

- [ ] **Step 2: Update VirtualScroller key**

The `:key` on the VirtualScroller (currently `:key="projectId"`) already changes when `projectId` changes (including `ws:xxx`), so it should work as-is. Verify this is the case.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SessionList.vue
git commit -m "feat(workspaces): add workspace session filtering to SessionList"
```

---

## Task 16: Frontend — Workspace-first ordering in all other selectors

**Files:**
- Modify: `frontend/src/components/TerminalSnippetsDialog.vue`
- Modify: `frontend/src/components/MessageSnippetsDialog.vue`
- Modify: `frontend/src/components/SearchOverlay.vue`

- [ ] **Step 1: TerminalSnippetsDialog — workspace-first scope ordering**

In the scope selector (lines 359-406), the `namedProjects` and `unnamedFlatTree` computeds (lines 45-59) need to be modified. Import the workspace store and `splitProjectsByPriority`. Use the active workspace's project IDs (from `route.query.workspace`) to reorder the projects.

Since this dialog receives `currentProjectId` as a prop and doesn't directly access the route, the workspace context needs to come either from the route (via `useRoute()`) or as a new prop. Using `useRoute()` is simpler:

```javascript
import { useRoute } from 'vue-router'
import { useWorkspacesStore } from '../stores/workspaces'
import { splitProjectsByPriority } from '../utils/projectSort'

const route = useRoute()
const workspacesStore = useWorkspacesStore()

const activeWsProjectIds = computed(() => {
    const wsId = route.query.workspace
    return wsId ? workspacesStore.getVisibleProjectIds(wsId) : null
})
```

Then use `splitProjectsByPriority` when building the project lists for the scope selector. If `activeWsProjectIds` is non-null, show workspace projects first in the scope options.

- [ ] **Step 2: MessageSnippetsDialog — same changes**

Apply the identical pattern as TerminalSnippetsDialog. The two dialogs have the same scope selector structure.

- [ ] **Step 3: SearchOverlay — workspace-first in project filter**

In `SearchOverlay.vue` (line 425-439), the `<wa-select>` uses `<ProjectSelectOptions>`. Pass the `priorityProjectIds` prop:

```html
<ProjectSelectOptions
    :projects="projects"
    :priority-project-ids="activeWsProjectIds"
/>
```

Add the same `activeWsProjectIds` computed as above.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/TerminalSnippetsDialog.vue frontend/src/components/MessageSnippetsDialog.vue frontend/src/components/SearchOverlay.vue
git commit -m "feat(workspaces): add workspace-first project ordering to all selectors"
```

---

## Task 17: Frontend — Import new WA components if needed

**Files:**
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Check which WA components are newly used**

Review all new/modified components for any `wa-*` elements not already imported in `main.js`. Common candidates:
- `wa-checkbox` (if used in workspace edit for project selection)
- Any other WA component not already in the imports

Check the existing imports in `main.js` and add any missing ones.

- [ ] **Step 2: Commit (if changes needed)**

```bash
git add frontend/src/main.js
git commit -m "feat(workspaces): import new Web Awesome components"
```

---

## Task 18: Verification and final commit

- [ ] **Step 1: Start dev servers in the worktree**

```bash
cd /home/twidi/dev/twicc-poc-workspaces
uv run ./devctl.py start
```

- [ ] **Step 2: Manual verification checklist**

1. Home page shows "Manage workspaces" link even with no workspaces
2. Create a workspace with 2 projects → workspace card appears on Home
3. Click workspace card → navigates to workspace view with merged sessions
4. Sidebar selector shows workspace sections correctly
5. Click a project within workspace → single-project view, workspace preserved in URL
6. Click a project outside workspace → workspace cleared
7. "New session" dropdown shows workspace projects first
8. Edit workspace (rename, add/remove projects) → changes save correctly
9. Archive workspace → disappears from selectors, toggle to show it again
10. Delete workspace while in it → navigates to All Projects
11. Search overlay shows workspace projects first in filter
12. Open second browser tab → workspaces are synced

- [ ] **Step 3: Commit the spec to the feature branch**

```bash
git add docs/superpowers/specs/2026-04-06-workspaces.md docs/superpowers/plans/2026-04-06-workspaces.md
git commit -m "docs: add workspaces spec and implementation plan"
```
