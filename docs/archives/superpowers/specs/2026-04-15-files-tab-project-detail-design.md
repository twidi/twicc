# Files Tab in ProjectDetailPanel

**Date:** 2026-04-15
**Status:** Draft

## Goal

Add a "Files" tab to `ProjectDetailPanel` (the panel shown when no session is selected), reusing the existing `FilesPanel` component. The tab must work in three modes: All Projects, Workspace, and single Project — each with appropriate default root directory and root choices.

## Current State

- `FilesPanel` exists and works for session mode (and draft/project-level via `isDraft`).
- `ProjectDetailPanel` has two tabs: Stats and Terminal.
- Backend has `standalone_directory_tree` at `/api/directory-tree/` but no standalone `file-search` or `file-content`.
- The terminal tab already handles all three modes (all-projects, workspace, project) with the LCA (lowest common ancestor) pattern for workspace directories.

## Design

### 1. Backend — New Standalone Endpoints with Root Restriction

Add two new standalone endpoints alongside the existing `/api/directory-tree/`:

**`/api/file-search/`** (GET)
- Accepts `?path=<absolute_dir>&q=<query>&show_hidden=1&show_ignored=1&limit=N&root=<absolute_dir>`
- Reuses `search_files()` from `twicc.file_tree`

**`/api/file-content/`** (GET + PUT)
- GET: `?path=<absolute_file_path>&root=<absolute_dir>` — reads file content
- PUT: `{ "path": "<absolute>", "content": "...", "root": "<absolute_dir>" }` — writes file content
- Reuses `get_file_content()` / `write_file_content()` from `twicc.file_content`

**Root restriction (`?root=` parameter):**

All three standalone endpoints (`directory-tree`, `file-search`, `file-content`) accept an optional `?root=<absolute_dir>` parameter. When provided, the backend validates that the requested `?path=` is equal to or a subdirectory of `?root=` (using `os.path.normpath` and `str.startswith` with trailing `/`). If validation fails, return 403 Forbidden.

This provides server-side path restriction for all-projects and workspace modes:
- **All-projects:** `?root=$HOME` — restricts browsing to the user's home directory
- **Workspace:** `?root=<LCA>` — restricts browsing to the workspace's common directory

When `?root=` is absent, no path restriction is applied (backward-compatible for `DirectoryPickerPopup`).

**Helper function** (shared by all 3 standalone endpoints):
```python
def validate_standalone_root(path, root):
    """Validate that path is within root directory. Returns error response or None."""
    if not root:
        return None  # No restriction
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    if path != root and not path.startswith(root + os.sep):
        return JsonResponse({"error": "Path is outside the allowed root directory"}, status=403)
    return None
```

**Modify `standalone_directory_tree`:**
- Add `?root=` validation (using the shared helper above)
- Currently forces `show_ignored=True`. Change to: respect `show_ignored` query param when present, default to `True` when absent (backward-compatible for `DirectoryPickerPopup` which doesn't pass the param).

**New URL patterns in `urls.py`:**
```python
path("api/file-search/", views.standalone_file_search),
path("api/file-content/", views.standalone_file_content),
```

### 2. Frontend — FilesPanel: Accept `apiPrefix` and `rootRestriction` as Props

Currently `FilesPanel` computes `apiPrefix` internally from `projectId`/`sessionId`/`isDraft`. Change to:

- Add a new prop `apiPrefix` (String, default `null`), renamed internally to `resolvedApiPrefix`
- `resolvedApiPrefix` computed: if prop is provided, use it; otherwise compute from `projectId`/`sessionId`/`isDraft` as before (backward-compatible)
- Replace all internal uses of `apiPrefix` with `resolvedApiPrefix`

Add a new prop `rootRestriction` (String, default `null`):
- When provided, appended as `&root=<value>` to all standalone API requests (`fetchTree`, `doSearch`, `lazyLoadDir`)
- Not used when `apiPrefix` is project-scoped (project endpoints have their own path validation via `validate_path`)
- Passed down to `FilePane` for `file-content` requests

Update `optionsQuery()` to include the root restriction:
```js
function optionsQuery() {
    let qs = ''
    if (showHidden.value) qs += '&show_hidden=1'
    if (showIgnored.value) qs += '&show_ignored=1'
    if (props.rootRestriction) qs += `&root=${encodeURIComponent(props.rootRestriction)}`
    return qs
}
```

This way, all API calls (`fetchTree`, `doSearch`, `lazyLoadDir`) automatically include the root restriction in their query strings, with no changes needed to their individual implementations.

### 3. Frontend — FilesPanel: Simplify `fetchTree` Signature

Currently: `fetchTree(projectId, sessionId, dirPath)` — but after the guard change (see below), `projectId` and `sessionId` are not used inside the function (the guard uses `resolvedApiPrefix`, and the API call uses `resolvedApiPrefix.value`).

Simplify to: **`fetchTree(dirPath)`**. Update all call sites:
- The watcher at line 350: `fetchTree(newDir)` instead of `fetchTree(newProjectId, newSessionId, newDir)`
- `refresh()` at line 383: `fetchTree(directory.value)` instead of `fetchTree(props.projectId, props.sessionId, directory.value)`

The watcher list changes from `[started, projectId, sessionId, directory, showHidden, showIgnored]` to `[started, resolvedApiPrefix, directory, showHidden, showIgnored]`. This ensures the tree re-fetches if the `apiPrefix` changes (e.g., switching from one mode to another), and also covers the case where `projectId`/`sessionId` change (since those affect the computed `resolvedApiPrefix`).

Guard change: `if (!resolvedApiPrefix.value || !dirPath)` instead of `if (!projectId || !dirPath)`.

Similarly, `doSearch` guard changes from `if (!props.projectId || !directory.value)` to `if (!resolvedApiPrefix.value || !directory.value)`.

### 4. Frontend — FilesPanel: Accept External Root Configuration

Add new prop to `FilesPanel`:

```js
/** When provided, overrides the internally-computed availableRoots. */
externalRoots: { type: Array, default: null },  // [{key, label, path}]
```

Behavior:
- When `externalRoots` is provided and non-null, the `availableRoots` computed uses it directly instead of computing from session/project props.
- The rest of the root selection logic (selectedRootKey, directory, missingRoots, handleRootSelect) works unchanged — it operates on `availableRoots` regardless of source.
- When `externalRoots` is null (default), behavior is identical to today.

### 5. Frontend — FilesPanel: Disable Features in Standalone Mode

Some features are session-specific and should be disabled in standalone mode:
- **Code comments** (`commentedPaths`): guard on `projectId && sessionId` — already does this, no change needed.
- **syncedGitDir**: only relevant for session (cross-tab sync with Git tab) — no change needed, parent simply doesn't pass the prop.
- **revealFile / setRootByPath**: exposed methods — harmless if called, but parent won't call them in standalone mode.

### 6. Frontend — FilePane: apiPrefix and rootRestriction Props

`FilePane` also computes its own `apiPrefix` from `projectId`/`sessionId`/`isDraft`. Add:
- `apiPrefix` prop (String, default `null`)
- `rootRestriction` prop (String, default `null`)
- Internally: `resolvedApiPrefix` computed — use prop when provided, else compute as before
- `FilesPanel` passes its `resolvedApiPrefix` and `rootRestriction` down to `FilePane`

`FilePane` appends `&root=<rootRestriction>` to its `file-content` GET requests and includes `"root": rootRestriction` in PUT request bodies when the prop is provided.

The `commentContext` computed in `FilePane` uses `projectId`/`sessionId`. In standalone mode these are null, so `commentContext` will be null — which disables code comments. This is correct.

### 7. Frontend — FileTreePanel and FileTree: No Changes Needed

`FileTreePanel` passes `projectId`, `sessionId`, `isDraft`, and `extraQuery` down to `FileTree`. `FileTree` has its own `apiPrefix` computed that it uses as a **fallback** for lazy-loading directories — but only when `lazyLoadFn` is not provided.

In the `FilesPanel` context, `lazyLoadFn` is **always** provided (via the `lazyLoadDir` function that uses `resolvedApiPrefix`). The fallback path in `FileTree` is never reached. Therefore `FileTree` and `FileTreePanel` do not need changes.

This is safe because:
- `FilesPanel` always passes `lazyLoadFn` to `FileTreePanel` (line 604 of `FilesPanel.vue`)
- `FileTreePanel` always passes `lazyLoadFn` to `FileTree`
- `FileTree` checks `lazyLoadFn` first (line 228) before falling back to its internal `apiPrefix`
- In standalone mode, `projectId`/`sessionId` will be null, making `FileTree`'s internal `apiPrefix` resolve to an invalid path — but this is harmless since the fallback is never reached

### 8. Frontend — ProjectDetailPanel: Add Files Tab

**Import FilesPanel:**
```js
import FilesPanel from './FilesPanel.vue'
```

**TABS array:**
```js
const TABS = [
    { id: 'stats', label: 'Stats', icon: 'chart-simple' },
    { id: 'files', label: 'Files', icon: 'folder-open' },
    { id: 'terminal', label: 'Terminal', icon: 'terminal' },
]
```

**Route names for the files tab:**
- `project-files` → single project mode
- `projects-files` → all-projects mode

Both all-projects and workspace modes use `projects-files` — workspace context is preserved via the `?workspace=<id>` query param (same pattern as the terminal tab).

**activeTab computed — add files detection:**
```js
if (name === 'project-files' || name === 'projects-files') return 'files'
```

**switchToTab — add files case:**
```js
if (tabId === 'files') {
    router.push({
        name: isAllProjectsMode.value ? 'projects-files' : 'project-files',
        params: isAllProjectsMode.value ? {} : { projectId: props.projectId },
        query: route.query,
    })
}
```

**Fetch `$HOME` for all-projects mode:**
```js
const homeDir = ref(null)

// Fetch home directory on mount (needed for all-projects files tab)
onMounted(async () => {
    if (props.projectId === ALL_PROJECTS_ID) {
        try {
            const res = await apiFetch('/api/home-directory/')
            if (res.ok) {
                const data = await res.json()
                homeDir.value = data.path
            }
        } catch { /* ignore */ }
    }
})
```

Note: also fetch `$HOME` when the component is mounted with a workspace projectId, since we might need it as a fallback. Actually no — workspaces always have project directories for the LCA. `$HOME` is only needed for all-projects mode.

But wait — `ProjectDetailPanel` is KeepAlive'd and its `projectId` prop can change when navigating between projects/workspaces/all-projects. The `onMounted` only fires once. Better approach: use a `watchEffect` that fetches `$HOME` when needed.

```js
const homeDir = ref(null)

watchEffect(async () => {
    if (props.projectId === ALL_PROJECTS_ID && !homeDir.value) {
        try {
            const res = await apiFetch('/api/home-directory/')
            if (res.ok) {
                const data = await res.json()
                homeDir.value = data.path
            }
        } catch { /* ignore */ }
    }
})
```

Since `$HOME` never changes, once fetched it stays valid forever. The `!homeDir.value` guard prevents re-fetching.

**Computed props for FilesPanel:**

`filesApiPrefix`:
```js
const filesApiPrefix = computed(() => {
    // Single project mode: use project-scoped endpoints (browsing restricted to project paths via validate_path)
    if (!isAllProjectsMode.value && !isWorkspaceMode.value) {
        return `/api/projects/${props.projectId}`
    }
    // All-projects and workspace modes: use standalone endpoints (restricted via ?root= param)
    return '/api'
})
```

`filesRootRestriction` — server-side path restriction for standalone modes:
```js
const filesRootRestriction = computed(() => {
    // Project mode: restriction handled by validate_path, no need for ?root=
    if (!isAllProjectsMode.value && !isWorkspaceMode.value) return null
    // All-projects mode: restrict to $HOME
    if (props.projectId === ALL_PROJECTS_ID) return homeDir.value
    // Workspace mode: restrict to LCA
    return terminalCwd.value  // the LCA of workspace project directories
})
```

| Mode | apiPrefix | Restriction mechanism |
|------|-----------|----------------------|
| All projects | `/api` (standalone) | None (unrestricted) |
| Workspace | `/api` (standalone) | `?root=<LCA>` |
| Project | `/api/projects/<id>` | `validate_path` (server-side, existing) |

`filesAvailableRoots`:
```js
const filesAvailableRoots = computed(() => {
    // All-projects mode
    if (props.projectId === ALL_PROJECTS_ID) {
        if (!homeDir.value) return []
        return [{ key: 'home', label: 'Home directory', path: homeDir.value }]
    }

    // Workspace mode
    if (isWorkspaceMode.value) {
        const roots = []
        const lca = terminalCwd.value  // reuse the LCA already computed for terminal
        if (!lca) return []
        roots.push({ key: 'common', label: 'Common directory', path: lca })

        // Add unique project directories that differ from LCA
        const seen = new Set([lca])
        const projectEntries = []
        for (const pid of workspaceProjectIds.value || []) {
            const project = dataStore.getProject(pid)
            const dir = project?.directory
            if (!dir || seen.has(dir)) continue
            seen.add(dir)
            projectEntries.push({
                key: `p:${pid}`,
                label: project.name || dir.split('/').pop(),
                path: dir,
            })
        }
        projectEntries.sort((a, b) => a.label.localeCompare(b.label))
        roots.push(...projectEntries)
        return roots
    }

    // Single project mode
    const project = dataStore.getProject(props.projectId)
    if (!project?.directory) return []
    const roots = [{ key: 'directory', label: 'Project directory', path: project.directory }]
    if (project.git_root && project.git_root !== project.directory) {
        roots.push({ key: 'git', label: 'Git root', path: project.git_root })
    }
    return roots
})
```

**Template — FilesPanel in tab panel:**
```html
<wa-tab-panel name="files">
    <FilesPanel
        :api-prefix="filesApiPrefix"
        :project-id="filesProjectId"
        :root-restriction="filesRootRestriction"
        :external-roots="filesAvailableRoots"
        :active="isActive && activeTab === 'files'"
    />
</wa-tab-panel>
```

`filesProjectId` — the real project ID for single project mode (needed by `FilePane` for `file-content` calls), `null` for all-projects/workspace:
```js
const filesProjectId = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(props.projectId)) return null
    return props.projectId
})
```

Note: `sessionId`, `isDraft`, `gitDirectory`, `sessionCwd`, `projectGitRoot`, `projectDirectory` are NOT passed — they are session-mode props. `syncedGitDir` is not passed either (no Git tab to sync with). The `@root-changed` event is not handled (no cross-tab sync needed).

### 9. Frontend — Router: New Routes

```js
// Under /project/:projectId children:
{ path: 'files', name: 'project-files', component: { render: () => null } },

// Under /projects children:
{ path: 'files', name: 'projects-files', component: { render: () => null } },
```

### 10. "Show git ignored files" Behavior

The `isGit` ref in `FilesPanel` is set from the `is_git` field returned by the `directory-tree` API response. This works automatically for all modes:
- If the opened root is inside a git repo, `is_git` will be `true` → checkbox appears
- If not (e.g., `$HOME` is not a git repo), `is_git` will be `false` → checkbox hidden

No special handling needed per mode. The API response already tells us.

**However:** `standalone_directory_tree` currently forces `show_ignored=True`, so it doesn't pass through the user's preference. The modification described in section 1 (respect `show_ignored` when explicitly passed) fixes this: the frontend will pass `&show_ignored=1` or omit it, and the backend will respect it.

### 11. CSS — Tab Panel Overflow Override

`ProjectDetailPanel` applies `overflow-y: auto; padding-bottom: 3rem` to all active tab panels (for the stats tab's scrollable content). `FilesPanel` manages its own internal scrolling (split panel with independent scroll regions) and sets `height: 100%; overflow: hidden` on its root element.

The `overflow: hidden` on `.files-panel` prevents content from leaking into the parent's `overflow-y: auto`, so no double scrollbar will appear. However, the `padding-bottom: 3rem` on the tab panel is unnecessary for files and terminal panels. Add a CSS override:

```css
.detail-tabs :deep(wa-tab-panel[name="files"])::part(base),
.detail-tabs :deep(wa-tab-panel[name="terminal"])::part(base) {
    overflow-y: hidden;
    padding-bottom: 0;
}
```

### 12. KeepAlive Considerations

`ProjectDetailPanel` is wrapped in `<KeepAlive>` (via `ProjectView`). `FilesPanel` has KeepAlive-specific logic:
- `keepAliveHidden` ref to hide the split panel during transitions
- `onDeactivated` / `onActivated` handlers for DOM reparenting and split panel width restoration

These lifecycle hooks fire when the entire `ProjectDetailPanel` is KeepAlive-deactivated/reactivated (i.e., when switching between the project detail view and a session view). They do **not** fire when switching tabs within `ProjectDetailPanel` — tab switching is CSS-based (`wa-tab-panel` active attribute), not unmount-based. This is correct: `FilesPanel` uses the `active` prop for tab visibility awareness (lazy init, focus management), while KeepAlive hooks handle the deeper preservation of split panel state.

### 13. Interaction with Session FilesPanel

The session-level `FilesPanel` (in `SessionView`) is completely separate from the project-level one. They have different instances, different props, different KeepAlive lifecycles. No cross-contamination is possible.

The session-level `FilesPanel` continues to use `projectId`/`sessionId`-based `apiPrefix` computation. No changes to session mode behavior.

## Files Changed

### Backend
- `src/twicc/views.py` — add `standalone_file_search()`, `standalone_file_content()`, modify `standalone_directory_tree()` to respect `show_ignored` param
- `src/twicc/urls.py` — add 2 new URL patterns

### Frontend
- `frontend/src/router.js` — add `project-files` and `projects-files` routes
- `frontend/src/components/ProjectDetailPanel.vue` — add Files tab, compute `filesApiPrefix`, `filesAvailableRoots`, fetch `$HOME`
- `frontend/src/components/FilesPanel.vue` — add `apiPrefix`, `rootRestriction`, and `externalRoots` props, simplify `fetchTree` signature, adjust guards to use `resolvedApiPrefix`, append `?root=` via `optionsQuery()`
- `frontend/src/components/FilePane.vue` — add `apiPrefix` and `rootRestriction` props, use when provided

### Frontend — No Changes Needed
- `frontend/src/components/FileTreePanel.vue` — receives `projectId`/`sessionId` but passes them through; `lazyLoadFn` is always provided by `FilesPanel` so the fallback `apiPrefix` in `FileTree` is never used
- `frontend/src/components/FileTree.vue` — same reasoning; internal `apiPrefix` fallback is never reached

## Edge Cases

1. **Workspace with 0 visible projects:** `filesAvailableRoots` is empty → `FilesPanel` shows nothing (same as terminal).
2. **Workspace with 1 project:** LCA = that project's directory. Only one root entry.
3. **Project without directory:** `project.directory` is null → no root available → placeholder shown.
4. **Project with directory = git_root:** Only one root entry (no duplicate).
5. **$HOME fetch failure:** `filesAvailableRoots` returns `[]` → placeholder shown.
6. **missingRoots:** Handled by existing `FilesPanel` logic — if a root's directory doesn't exist, it's marked as missing and the next root is selected.
7. **Standalone directory-tree backward compat:** `DirectoryPickerPopup` doesn't pass `show_ignored` → defaults to `True` → no behavior change.
8. **Workspace projects with duplicate directories:** Deduplicated by `seen` Set in `filesAvailableRoots`. Only the first project with that directory gets an entry; others are skipped.
9. **switchToTab for stats (default):** The existing `else` branch in `switchToTab` handles stats. The `if/else if` structure becomes `if (files) ... else if (terminal) ... else (stats)`.
