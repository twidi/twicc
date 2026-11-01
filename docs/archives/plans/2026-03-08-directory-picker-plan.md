# Directory Picker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a directory picker popup to the project creation dialog, reusing existing FileTree/FileTreePanel components with a new `directoriesOnly` mode, and prepare the architecture for future reuse as a generic tree picker.

**Architecture:** A new `DirectoryPickerPopup` component wraps `FileTreePanel` inside a `wa-popup`, providing its own tree fetch/lazy-load logic via a new project-independent API endpoint. `FileTree` and `FileTreePanel` gain a `directoriesOnly` prop to filter out files. `FileTree` also gains an optional `lazyLoadFn` prop to decouple it from project-specific API calls.

**Tech Stack:** Vue 3 (Composition API), Web Awesome 3 (`wa-popup`, `wa-icon-button`), Django REST API, existing `file_tree.py` backend logic.

---

### Task 1: Backend — Add `directories_only` support to `get_directory_tree`

**Files:**
- Modify: `src/twicc/file_tree.py`

**Step 1: Add `directories_only` parameter to `get_directory_tree`**

In `file_tree.py`, modify the function signature and logic:

```python
def get_directory_tree(dir_path, show_hidden=False, show_ignored=False, directories_only=False):
```

Two changes inside the function:

1. In `list_directory`, when `directories_only` is True, filter out file entries before returning:

```python
        result.sort(key=lambda x: (0 if x[1] == "directory" else 1, x[0].lower()))
        if directories_only:
            result = [(name, typ) for name, typ in result if typ == "directory"]
        return result
```

Note: `directories_only` must be accessed from the outer scope (closure), same pattern as `show_hidden` and `show_ignored`. Either pass it as a parameter to `list_directory` or rely on closure. The existing code uses closure for `show_hidden`/`show_ignored`/`git_root`, so use the same pattern.

2. In the BFS loop, when `directories_only` is True, do NOT count files toward `node_count` (files are already excluded by `list_directory`, so this happens naturally — no additional change needed in the BFS loop itself).

**Step 2: Verify no tests break**

Run: `uv run pytest tests/ -x -q` (if tests exist for file_tree)

**Step 3: Commit**

```bash
git add src/twicc/file_tree.py
git commit -m "feat(file_tree): add directories_only param to get_directory_tree"
```

---

### Task 2: Backend — Add standalone `/api/directory-tree/` endpoint

**Files:**
- Modify: `src/twicc/views.py`
- Modify: `src/twicc/urls.py`

**Step 1: Add the view function in `views.py`**

Add a new view function `standalone_directory_tree` near the existing `directory_tree` view. This endpoint does NOT require a project — it accepts any absolute directory path and returns its tree. Authentication is handled by the existing `PasswordAuthMiddleware`.

```python
def standalone_directory_tree(request):
    """GET directory tree listing for any absolute directory path.

    Unlike the project-scoped directory-tree endpoint, this does not require
    a project and does not validate path ownership. Used by the directory
    picker in the project creation dialog.

    Authentication is enforced by PasswordAuthMiddleware.
    """
    import os

    from twicc.file_tree import get_directory_tree

    dir_path = request.GET.get("path", "").strip()
    if not dir_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    if not os.path.isabs(dir_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    if not os.path.isdir(dir_path):
        return JsonResponse({"error": "Directory not found"}, status=404)

    show_hidden = request.GET.get("show_hidden") == "1"
    directories_only = request.GET.get("directories_only") == "1"

    tree = get_directory_tree(dir_path, show_hidden=show_hidden, directories_only=directories_only)
    return JsonResponse(tree)
```

Note: No `show_ignored` param needed here since we're outside a git project context — the user is browsing arbitrary directories. But actually `get_directory_tree` handles `show_ignored` internally (it detects git repos), so we can include it for completeness, defaulting to `show_ignored=True` (show everything by default in a free-form picker). Actually, let's just pass `show_ignored=True` always to avoid hiding directories in repos the user browses through:

```python
    tree = get_directory_tree(dir_path, show_hidden=show_hidden, show_ignored=True, directories_only=directories_only)
```

**Step 2: Add the URL pattern in `urls.py`**

Add the new route before the project-scoped routes but after auth endpoints. Insert after the `path("api/projects/", ...)` line:

```python
    # Standalone filesystem endpoint (for directory picker, no project required)
    path("api/directory-tree/", views.standalone_directory_tree),
```

**Step 3: Commit**

```bash
git add src/twicc/views.py src/twicc/urls.py
git commit -m "feat(api): add standalone /api/directory-tree/ endpoint for directory picker"
```

---

### Task 3: Frontend — Add `directoriesOnly` prop to `FileTree.vue`

**Files:**
- Modify: `frontend/src/components/FileTree.vue`

**Step 1: Add the prop**

Add to the `defineProps`:

```js
    directoriesOnly: {
        type: Boolean,
        default: false,
    },
```

**Step 2: Add `lazyLoadFn` prop**

This optional prop allows the parent to provide a custom lazy-loading function instead of FileTree making API calls directly. When provided, it replaces the built-in `apiFetch` call in `toggleOpen()`.

```js
    lazyLoadFn: {
        type: Function,
        default: null,
    },
```

**Step 3: Filter children in the template**

Replace the `v-for` on children with a computed that filters out files when `directoriesOnly` is true. Add a computed:

```js
/**
 * Visible children of the effective node.
 * In directoriesOnly mode, file nodes are filtered out.
 */
const visibleChildren = computed(() => {
    const children = compact.value.effectiveNode.children
    if (!children) return []
    if (props.directoriesOnly) {
        return children.filter(child => child.type === 'directory')
    }
    return children
})
```

Update the template to use `visibleChildren` instead of `compact.effectiveNode.children`:

In the `v-if` on the children container, change:
```html
v-if="isOpen && compact.effectiveNode.type === 'directory' && compact.effectiveNode.children?.length"
```
to:
```html
v-if="isOpen && compact.effectiveNode.type === 'directory' && visibleChildren.length"
```

And the `v-for`:
```html
v-for="child in visibleChildren"
```

**Step 4: Update compact to handle directoriesOnly**

In the `compact` computed, the compaction logic checks `children.length === 1`. In `directoriesOnly` mode, we should check that there's exactly one *directory* child (ignoring files). Modify the while loop condition:

```js
while (
    current.type === 'directory' &&
    current.loaded !== false &&
    (() => {
        const dirs = current.children?.filter(c => c.type === 'directory') ?? []
        const relevant = props.directoriesOnly ? dirs : (current.children ?? [])
        return relevant.length === 1 && (props.directoriesOnly ? true : current.children[0].type === 'directory')
    })()
) {
```

Actually, this is getting complex with an IIFE. A cleaner approach: extract a helper:

```js
/**
 * Get compactable children for the compact folder logic.
 * In directoriesOnly mode, only directory children are considered.
 */
function getCompactableChildren(node) {
    if (!node.children) return []
    if (props.directoriesOnly) {
        return node.children.filter(c => c.type === 'directory')
    }
    return node.children
}
```

Then rewrite the compact computed's while loop:

```js
    while (
        current.type === 'directory' &&
        current.loaded !== false &&
        (() => {
            const children = getCompactableChildren(current)
            return children.length === 1 && children[0].type === 'directory'
        })()
    ) {
        const children = getCompactableChildren(current)
        current = children[0]
        currentPath = `${currentPath}/${current.name}`
        nameParts.push(current.name)
    }
```

**Step 5: Modify `toggleOpen` to use `lazyLoadFn` when provided**

In `toggleOpen()`, replace the API call block:

```js
    if (!isOpen.value && effectiveNode.loaded === false && props.mode !== 'git') {
        isLoading.value = true
        try {
            let data
            if (props.lazyLoadFn) {
                data = await props.lazyLoadFn(effectivePath)
            } else {
                const res = await apiFetch(
                    `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(effectivePath)}${props.extraQuery}`
                )
                if (res.ok) {
                    data = await res.json()
                }
            }
            if (data) {
                effectiveNode.children = data.children || []
                effectiveNode.loaded = true
            }
        } catch {
            // Silently fail — the folder just won't open
        } finally {
            isLoading.value = false
        }
    }
```

**Step 6: Pass new props to recursive children**

In the template's recursive `<FileTree>`, pass the new props:

```html
:directories-only="directoriesOnly"
:lazy-load-fn="lazyLoadFn"
```

**Step 7: Commit**

```bash
git add frontend/src/components/FileTree.vue
git commit -m "feat(FileTree): add directoriesOnly and lazyLoadFn props"
```

---

### Task 4: Frontend — Add `directoriesOnly` prop to `FileTreePanel.vue`

**Files:**
- Modify: `frontend/src/components/FileTreePanel.vue`

**Step 1: Add the props**

Add to `defineProps`:

```js
    directoriesOnly: {
        type: Boolean,
        default: false,
    },
    lazyLoadFn: {
        type: Function,
        default: null,
    },
```

**Step 2: Pass props to FileTree in the template**

On the `<FileTree>` component in the template, add:

```html
:directories-only="directoriesOnly"
:lazy-load-fn="lazyLoadFn"
```

**Step 3: Adapt keyboard handling for directory selection**

In `handleTreeKeydown`, the `Enter` and `Space` cases currently both call `activateFocused` (which does `el.click()`). For a regular FileTree, clicking a directory toggles it open/close. But in `directoriesOnly` mode, we want:

- **Enter** = toggle open/close (unchanged — let click handle it)
- **Space** = select the directory (emit `file-select` with the path)

Modify the `Enter`/`Space` case:

```js
        case 'Enter': {
            event.preventDefault()
            if (index >= 0) {
                activateFocused(items, index)
            }
            break
        }

        case ' ': {
            event.preventDefault()
            if (index >= 0) {
                if (props.directoriesOnly && items[index].dataset.type === 'directory') {
                    // In directory picker mode, Space selects the directory
                    emit('file-select', items[index].dataset.path)
                } else {
                    activateFocused(items, index)
                }
            }
            break
        }
```

This means we need to split the existing `case 'Enter': case ' ':` into two separate cases.

**Step 4: Commit**

```bash
git add frontend/src/components/FileTreePanel.vue
git commit -m "feat(FileTreePanel): add directoriesOnly and lazyLoadFn props, adapt keyboard for directory selection"
```

---

### Task 5: Frontend — Register `wa-popup` component

**Files:**
- Modify: `frontend/src/main.js`

**Step 1: Add the import**

Add after the existing Web Awesome imports (e.g. after the `popover` import):

```js
import '@awesome.me/webawesome/dist/components/popup/popup.js'
```

**Step 2: Also add `wa-icon-button` if not already imported**

Check if `icon-button` is already imported. If not, add:

```js
import '@awesome.me/webawesome/dist/components/icon-button/icon-button.js'
```

**Step 3: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: register wa-popup and wa-icon-button components"
```

---

### Task 6: Frontend — Create `DirectoryPickerPopup.vue`

**Files:**
- Create: `frontend/src/components/DirectoryPickerPopup.vue`

This is the main new component. It wraps a FileTreePanel in a wa-popup, anchored to a trigger button. It handles its own tree fetching and lazy-loading via the standalone `/api/directory-tree/` endpoint.

**Step 1: Create the component**

```vue
<script setup>
/**
 * DirectoryPickerPopup - A popup file tree for selecting directories.
 *
 * Opens a wa-popup anchored to a Browse button, showing a FileTreePanel
 * in directoriesOnly mode. The tree is fetched from the standalone
 * /api/directory-tree/ endpoint (no project required).
 *
 * The popup starts at the directory specified by initialPath (if it exists),
 * falling back to $HOME. When a directory is selected (via the select icon
 * or Space key), the component emits update:modelValue with the absolute path.
 *
 * Props:
 *   modelValue: current directory path (v-model)
 *
 * Events:
 *   update:modelValue: emitted when a directory is selected
 */

import { ref, watch, nextTick, useId } from 'vue'
import { apiFetch } from '../utils/api'
import FileTreePanel from './FileTreePanel.vue'

const props = defineProps({
    modelValue: {
        type: String,
        default: '',
    },
})

const emit = defineEmits(['update:modelValue'])

// ─── Popup state ─────────────────────────────────────────────────────────────

const popupRef = ref(null)
const anchorId = useId()
const isOpen = ref(false)

// ─── Tree state ──────────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const error = ref(null)
const rootPath = ref(null)
const fileTreePanelRef = ref(null)

/**
 * Resolve the starting path for the tree.
 * Priority:
 *   1. modelValue if it's an existing directory
 *   2. Walk up modelValue to find the nearest existing parent
 *   3. Fall back to $HOME (fetched from a simple endpoint or hardcoded)
 *
 * Since we can't check directory existence client-side, we attempt to
 * fetch the tree at the given path and fall back on 404.
 */
async function resolveStartPath(inputPath) {
    const trimmed = (inputPath || '').trim()
    if (!trimmed || !trimmed.startsWith('/')) {
        return null  // Will use fallback
    }

    // Try the path as-is, then walk up to find an existing parent
    const segments = trimmed.replace(/\/+$/, '').split('/')
    for (let i = segments.length; i >= 1; i--) {
        const candidate = segments.slice(0, i).join('/') || '/'
        try {
            const res = await apiFetch(
                `/api/directory-tree/?path=${encodeURIComponent(candidate)}&directories_only=1`
            )
            if (res.ok) {
                return { path: candidate, data: await res.json() }
            }
        } catch {
            // Continue to parent
        }
    }
    return null
}

async function fetchTree(dirPath) {
    loading.value = true
    error.value = null
    try {
        const res = await apiFetch(
            `/api/directory-tree/?path=${encodeURIComponent(dirPath)}&directories_only=1`
        )
        if (!res.ok) {
            const data = await res.json()
            error.value = data.error || 'Failed to load directory'
            return null
        }
        return await res.json()
    } catch {
        error.value = 'Network error'
        return null
    } finally {
        loading.value = false
    }
}

/**
 * Lazy-load function provided to FileTreePanel and FileTree.
 * Fetches children for a directory that hasn't been loaded yet.
 */
async function lazyLoadDir(path) {
    const res = await apiFetch(
        `/api/directory-tree/?path=${encodeURIComponent(path)}&directories_only=1`
    )
    if (!res.ok) return null
    return await res.json()
}

// ─── Open / close ────────────────────────────────────────────────────────────

async function openPopup() {
    if (isOpen.value) {
        closePopup()
        return
    }

    isOpen.value = true
    loading.value = true

    // Try to resolve the starting path from the current modelValue
    const resolved = await resolveStartPath(props.modelValue)

    if (resolved) {
        rootPath.value = resolved.path
        tree.value = resolved.data
    } else {
        // Fallback to $HOME
        const homePath = await fetchHomePath()
        const data = await fetchTree(homePath)
        if (data) {
            rootPath.value = homePath
            tree.value = data
        }
    }

    loading.value = false

    // Focus the tree panel after the popup renders
    await nextTick()
    await nextTick()
    fileTreePanelRef.value?.focusSearchInput?.()
}

function closePopup() {
    isOpen.value = false
    tree.value = null
    rootPath.value = null
    error.value = null
}

/**
 * Fetch the user's home directory from a simple endpoint,
 * or fall back to a reasonable default.
 */
async function fetchHomePath() {
    // We'll add a tiny endpoint for this, but for now use a simple approach:
    // try /api/home-directory/ and fall back to /home or /
    try {
        const res = await apiFetch('/api/home-directory/')
        if (res.ok) {
            const data = await res.json()
            return data.path
        }
    } catch {
        // Fall through
    }
    return '/'
}

// ─── Navigate up ─────────────────────────────────────────────────────────────

async function navigateUp() {
    if (!rootPath.value || rootPath.value === '/') return

    const parentPath = rootPath.value.replace(/\/[^/]+$/, '') || '/'
    loading.value = true
    const data = await fetchTree(parentPath)
    if (data) {
        rootPath.value = parentPath
        tree.value = data
    }
    loading.value = false
}

// ─── Selection ───────────────────────────────────────────────────────────────

function onDirectorySelect(path) {
    // path comes from FileTreePanel as a relative path; we need absolute
    // FileTreePanel strips the rootPath prefix, so reconstruct absolute
    const absolutePath = rootPath.value === '/'
        ? `/${path}`
        : `${rootPath.value}/${path}`
    emit('update:modelValue', absolutePath)
    closePopup()
}

// ─── Click outside to close ──────────────────────────────────────────────────

function onDocumentClick(event) {
    if (!isOpen.value) return
    const popup = popupRef.value
    const anchor = document.getElementById(anchorId)
    if (!popup || !anchor) return

    // Check if click is inside the popup content or the anchor button
    if (popup.contains(event.target) || anchor.contains(event.target)) return

    closePopup()
}

// Setup/teardown click-outside listener
watch(isOpen, (open) => {
    if (open) {
        // Delay to avoid the opening click from immediately closing
        setTimeout(() => {
            document.addEventListener('click', onDocumentClick, true)
        }, 0)
    } else {
        document.removeEventListener('click', onDocumentClick, true)
    }
})
</script>

<template>
    <div class="directory-picker">
        <wa-icon-button
            :id="anchorId"
            name="folder-open"
            label="Browse directories"
            class="browse-button"
            @click="openPopup"
        ></wa-icon-button>

        <wa-popup
            ref="popupRef"
            :anchor="anchorId"
            placement="bottom-end"
            :active="isOpen"
            :distance="4"
            flip
            shift
            shift-padding="8"
        >
            <div class="picker-panel">
                <!-- Header: current path + navigate up -->
                <div class="picker-header">
                    <wa-icon-button
                        name="arrow-up"
                        label="Go to parent directory"
                        size="small"
                        :disabled="!rootPath || rootPath === '/'"
                        @click="navigateUp"
                    ></wa-icon-button>
                    <span class="picker-path" :title="rootPath">{{ rootPath || '...' }}</span>
                </div>

                <!-- Tree -->
                <FileTreePanel
                    ref="fileTreePanelRef"
                    :tree="tree"
                    :loading="loading"
                    :error="error"
                    :root-path="rootPath"
                    :lazy-load-fn="lazyLoadDir"
                    :show-refresh="false"
                    directories-only
                    mode="files"
                    @file-select="onDirectorySelect"
                />
            </div>
        </wa-popup>
    </div>
</template>

<style scoped>
.directory-picker {
    display: inline-flex;
    position: relative;
}

.browse-button {
    font-size: var(--wa-font-size-l);
}

.picker-panel {
    width: 450px;
    height: 350px;
    display: flex;
    flex-direction: column;
    background: var(--wa-color-surface-default);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    box-shadow: var(--wa-shadow-l);
    overflow: hidden;
}

.picker-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-surface-border);
    flex-shrink: 0;
}

.picker-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-family: var(--wa-font-family-code);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
}
</style>
```

**Step 2: Commit**

```bash
git add frontend/src/components/DirectoryPickerPopup.vue
git commit -m "feat: create DirectoryPickerPopup component"
```

---

### Task 7: Frontend — Add select button to FileTree directory nodes in `directoriesOnly` mode

**Files:**
- Modify: `frontend/src/components/FileTree.vue`

**Step 1: Add the select icon button**

In the template, after the `<span class="node-name">` and `<span v-if="gitBadge" ...>`, add a select button that only shows in `directoriesOnly` mode (for non-root nodes):

```html
            <wa-icon-button
                v-if="directoriesOnly && node.type === 'directory' && !isRoot"
                name="check"
                label="Select this directory"
                class="dir-select-button"
                @click.stop="handleDirectorySelect"
            ></wa-icon-button>
```

**Step 2: Add the handler**

```js
function handleDirectorySelect() {
    emit('select', compact.value.effectivePath)
}
```

Note: `emit('select')` is already defined. In files mode, `select` is emitted for file clicks. In `directoriesOnly` mode, we emit it for directory selection too.

**Step 3: Add styles for the select button**

```css
.dir-select-button {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    opacity: 0;
    transition: opacity 0.15s ease;
}

.node-label:hover .dir-select-button,
.node-label.is-focused .dir-select-button {
    opacity: 1;
}

.dir-select-button:hover {
    color: var(--wa-color-brand-text);
}
```

This keeps the button invisible until the user hovers over or focuses a row, avoiding visual clutter.

**Step 4: Commit**

```bash
git add frontend/src/components/FileTree.vue
git commit -m "feat(FileTree): add select button for directory picking"
```

---

### Task 8: Backend — Add `/api/home-directory/` endpoint

**Files:**
- Modify: `src/twicc/views.py`
- Modify: `src/twicc/urls.py`

**Step 1: Add the view**

```python
def home_directory(request):
    """GET the current user's home directory path."""
    import os
    return JsonResponse({"path": os.path.expanduser("~")})
```

**Step 2: Add the URL**

In `urls.py`, add near the standalone directory-tree endpoint:

```python
    path("api/home-directory/", views.home_directory),
```

**Step 3: Commit**

```bash
git add src/twicc/views.py src/twicc/urls.py
git commit -m "feat(api): add /api/home-directory/ endpoint"
```

---

### Task 9: Frontend — Integrate `DirectoryPickerPopup` into `ProjectEditDialog.vue`

**Files:**
- Modify: `frontend/src/components/ProjectEditDialog.vue`

**Step 1: Import the component**

```js
import DirectoryPickerPopup from './DirectoryPickerPopup.vue'
```

**Step 2: Replace the directory input template**

Change the create-mode directory form group from:

```html
            <div v-if="isCreateMode" class="form-group">
                <label class="form-label">Directory</label>
                <wa-input
                    ref="directoryInputRef"
                    :value.prop="localDirectory"
                    @input="onDirectoryInput"
                    placeholder="/path/to/your/project"
                ></wa-input>
                <div class="form-hint">Absolute path to the project directory</div>
            </div>
```

to:

```html
            <div v-if="isCreateMode" class="form-group">
                <label class="form-label">Directory</label>
                <div class="directory-input-row">
                    <wa-input
                        ref="directoryInputRef"
                        :value.prop="localDirectory"
                        @input="onDirectoryInput"
                        placeholder="/path/to/your/project"
                        class="directory-input"
                    ></wa-input>
                    <DirectoryPickerPopup v-model="localDirectory" />
                </div>
                <div class="form-hint">Absolute path to the project directory</div>
            </div>
```

**Step 3: Add styles for the row layout**

```css
.directory-input-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.directory-input {
    flex: 1;
    min-width: 0;
}
```

**Step 4: Verify v-model works correctly**

The `DirectoryPickerPopup` uses `v-model` which maps to `modelValue` + `update:modelValue`. When the user selects a directory in the popup, `localDirectory` is updated. When the user types in the input, `onDirectoryInput` updates `localDirectory` — and since DirectoryPickerPopup reads `modelValue`, it will use that path when opening.

**Step 5: Commit**

```bash
git add frontend/src/components/ProjectEditDialog.vue
git commit -m "feat(ProjectEditDialog): integrate DirectoryPickerPopup for directory selection"
```

---

### Task 10: Integration testing and refinements

**Files:**
- Potentially modify: `frontend/src/components/DirectoryPickerPopup.vue`
- Potentially modify: `frontend/src/components/FileTree.vue`
- Potentially modify: `frontend/src/components/FileTreePanel.vue`

**Step 1: Start the dev servers**

```bash
uv run ./devctl.py restart all
```

**Step 2: Test the following scenarios**

1. Open the "New Project" dialog — verify the Browse button appears next to the directory input
2. Click Browse — verify the popup opens showing `$HOME` contents (directories only, no files)
3. Click on directories to expand them — verify lazy-loading works
4. Click the navigate-up button — verify it goes to the parent directory
5. Click the select (check) icon on a directory — verify the input updates and popup closes
6. Type a path in the input, then click Browse — verify the tree opens at that path
7. Type a non-existent path — verify it walks up to the nearest existing parent
8. Keyboard: navigate with arrows, press Space to select, press Enter to toggle open/close
9. Verify the rest of the form (name, color) still works
10. Submit the form — verify project creation works with the selected path
11. Verify existing FileTree behavior in the Files tab is unchanged (no regressions)
12. Verify existing FileTree behavior in the Git tab is unchanged

**Step 3: Fix any issues found**

Common things to watch for:
- FileTreePanel's search bar: should be hidden in the picker since we decided no search. Either hide it via a new prop or simply don't provide a `searchFn` (the search bar only shows when `tree` is set — check if the search input is conditionally shown). Looking at FileTreePanel template: the search bar shows `v-if="tree"`. We need a way to hide it. Add a `showSearch` prop (default `true`) to FileTreePanel, and set it to `false` from DirectoryPickerPopup. Alternatively, since we don't pass a `searchFn`, we can check: `v-if="tree && searchFn"`.
- The options dropdown (sliders icon) in FileTreePanel should also be hidden in picker mode. Same approach: conditionally show it.

**Recommended approach:** Change the search bar condition in FileTreePanel from `v-if="tree"` to `v-if="tree && searchFn"`. This way, when no `searchFn` is provided (as in DirectoryPickerPopup), the entire search bar + options dropdown is hidden. This is clean because the search bar is useless without a search function.

**Step 4: Final commit**

```bash
git add -p  # Stage only the relevant files
git commit -m "fix: refine DirectoryPickerPopup integration and hide search when no searchFn"
```

---

### Task 11: Handle FileTreePanel `file-select` path format

**Files:**
- Modify: `frontend/src/components/DirectoryPickerPopup.vue`

**Step 1: Review how FileTreePanel emits paths**

Looking at `FileTreePanel.onFileSelect()`:
```js
function onFileSelect(path) {
    const prefix = props.rootPath + '/'
    selectedFile.value = path.startsWith(prefix)
        ? path.slice(prefix.length)
        : path
    emit('file-select', selectedFile.value)
}
```

It strips the rootPath prefix and emits a **relative** path. But in `DirectoryPickerPopup.onDirectorySelect`, we reconstruct the absolute path. Verify this works correctly:

- If rootPath = `/home/user` and user selects `/home/user/projects/myapp`, FileTreePanel emits `projects/myapp`
- `onDirectorySelect` receives `projects/myapp` and builds `/home/user/projects/myapp` ✓

- If rootPath = `/` and user selects `/home`, FileTreePanel emits `home` (strips `//` → `/` prefix)
- `onDirectorySelect` receives `home` and builds `/home` ✓

Edge case: the user selects the root node itself. FileTreePanel's `onFileSelect` gets `path === rootPath`, so `selectedFile.value = path` (no stripping). Then `onDirectorySelect` would build `rootPath/rootPath`. We need to handle this:

```js
function onDirectorySelect(pathOrRelative) {
    let absolutePath
    if (pathOrRelative.startsWith('/')) {
        // Already absolute (root was selected)
        absolutePath = pathOrRelative
    } else {
        absolutePath = rootPath.value === '/'
            ? `/${pathOrRelative}`
            : `${rootPath.value}/${pathOrRelative}`
    }
    emit('update:modelValue', absolutePath)
    closePopup()
}
```

**Step 2: Commit**

```bash
git add frontend/src/components/DirectoryPickerPopup.vue
git commit -m "fix(DirectoryPickerPopup): handle absolute vs relative paths correctly"
```
