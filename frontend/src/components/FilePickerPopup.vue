<script setup>
/**
 * FilePickerPopup - A popup file tree for selecting files to reference with @.
 *
 * Opens a wa-popup anchored to a given element, showing a FileTreePanel
 * with search and options. Uses the same root directory logic as FilesPanel.
 *
 * The popup fetches the tree from the session-level or project-level
 * directory-tree endpoint (depending on whether the session is a draft).
 *
 * Props:
 *   sessionId: current session id
 *   projectId: current project id
 *   anchorId: id of the element to anchor the popup to
 *
 * Events:
 *   select(relativePath): emitted when a file is selected (path relative to session cwd)
 *   close(): emitted when the popup is closed without selection
 */

import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useDataStore } from '../stores/data'
import { apiFetch } from '../utils/api'
import FileTreePanel from './FileTreePanel.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true,
    },
    projectId: {
        type: String,
        required: true,
    },
    anchorId: {
        type: String,
        required: true,
    },
})

const emit = defineEmits(['select', 'close', 'filter-change'])

const store = useDataStore()

// ─── Session & project data from store ────────────────────────────────────

const session = computed(() => store.getSession(props.sessionId))
const project = computed(() => store.getProject(props.projectId))
const isDraft = computed(() => session.value?.draft === true)

// ─── API prefix ───────────────────────────────────────────────────────────

const apiPrefix = computed(() => {
    if (isDraft.value) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
const fileTreePanelRef = ref(null)

// ─── Display options ──────────────────────────────────────────────────────

const showHidden = ref(false)
const showIgnored = ref(false)
const isGit = ref(false)

function optionsQuery() {
    let qs = ''
    if (showHidden.value) qs += '&show_hidden=1'
    if (showIgnored.value) qs += '&show_ignored=1'
    return qs
}

// ─── Root directory selection ─────────────────────────────────────────────
// Same logic as FilesPanel's availableRoots.

const availableRoots = computed(() => {
    const sessionGit = session.value?.git_directory
    const cwd = session.value?.cwd
    const projectGitRoot = project.value?.git_root
    const projectDir = project.value?.directory

    const pathRoles = new Map()

    function register(path, role, key) {
        if (!path) return
        if (pathRoles.has(path)) {
            pathRoles.get(path).roles.add(role)
        } else {
            pathRoles.set(path, { key, roles: new Set([role]) })
        }
    }

    if (sessionGit) {
        register(sessionGit, 'git_root', 'git')
    }
    register(cwd, 'cwd', 'cwd')
    register(projectDir, 'project_dir', 'project')
    if (!sessionGit) {
        register(projectGitRoot, 'git_root', 'git')
    }

    function buildLabel(roles) {
        const isGitRole = roles.has('git_root')
        const isProject = roles.has('project_dir')
        const isCwd = roles.has('cwd')

        if (isProject && isGitRole) return 'Project directory (git root)'
        if (isProject)              return 'Project directory'
        if (isGitRole)              return 'Git root'
        if (isCwd)                  return 'Working directory'
        return 'Directory'
    }

    const order = sessionGit
        ? [sessionGit, cwd, projectDir]
        : [projectDir, cwd, projectGitRoot]

    const roots = []
    const seen = new Set()
    for (const path of order) {
        if (!path || seen.has(path)) continue
        seen.add(path)
        const info = pathRoles.get(path)
        roots.push({
            key: info.key,
            label: buildLabel(info.roles),
            path,
        })
    }
    return roots
})

const selectedRootKey = ref(null)

const directory = computed(() => {
    const roots = availableRoots.value
    if (!roots.length) return null
    const selected = roots.find(r => r.key === selectedRootKey.value)
    return selected ? selected.path : roots[0].path
})

// ─── Tree state ───────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const error = ref(null)

async function fetchTree(dirPath) {
    if (!dirPath) {
        tree.value = null
        return
    }
    loading.value = true
    error.value = null
    try {
        const res = await apiFetch(
            `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(dirPath)}${optionsQuery()}`
        )
        if (!res.ok) {
            const data = await res.json()
            error.value = data.error || `HTTP ${res.status}`
            tree.value = null
            return
        }
        const data = await res.json()
        isGit.value = !!data.is_git
        tree.value = data
    } catch (err) {
        error.value = err.message
        tree.value = null
    } finally {
        loading.value = false
    }
}

/**
 * Lazy-load function for FileTreePanel: fetches children for an unexpanded directory.
 */
async function lazyLoadDir(path) {
    const res = await apiFetch(
        `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(path)}${optionsQuery()}`
    )
    if (!res.ok) return null
    return await res.json()
}

/**
 * Search callback for FileTreePanel: calls the backend file-search API.
 */
async function doSearch(query) {
    if (!directory.value) return null
    const res = await apiFetch(
        `${apiPrefix.value}/file-search/?path=${encodeURIComponent(directory.value)}&q=${encodeURIComponent(query)}${optionsQuery()}`
    )
    if (res.ok) {
        const data = await res.json()
        return { tree: data, total: data.total, truncated: data.truncated }
    }
    return null
}

// ─── Open / close ─────────────────────────────────────────────────────────

async function open() {
    if (isOpen.value) return

    // Initialize root selection
    const roots = availableRoots.value
    if (!roots.length) return
    if (!selectedRootKey.value || !roots.find(r => r.key === selectedRootKey.value)) {
        selectedRootKey.value = roots[0].key
    }

    isOpen.value = true
    await fetchTree(directory.value)

    // Wait for the popup and FileTreePanel to render
    await nextTick()
    await nextTick()

    // Reset any previous search and focus the search input
    fileTreePanelRef.value?.clearSearch(false)
    fileTreePanelRef.value?.focusSearchInput()
}

function close() {
    isOpen.value = false
    tree.value = null
    error.value = null
    emit('close')
}

// ─── File selection ───────────────────────────────────────────────────────

function onFileSelect(relPath) {
    // relPath is relative to the current root (directory.value)
    const absolutePath = `${directory.value}/${relPath}`

    // Compute path relative to session.cwd when available
    const cwd = session.value?.cwd
    let resultPath
    if (cwd) {
        resultPath = computeRelativePath(cwd, absolutePath)
    } else {
        // No cwd — use path relative to the displayed root
        resultPath = relPath
    }

    emit('select', resultPath)
    close()
}

/**
 * Compute a relative path from `from` directory to `to` file/directory.
 * E.g. relativePath('/a/b/c', '/a/b/d/e.js') → '../d/e.js'
 */
function computeRelativePath(from, to) {
    const fromParts = from.split('/').filter(Boolean)
    const toParts = to.split('/').filter(Boolean)

    // Find common prefix length
    let common = 0
    while (common < fromParts.length && common < toParts.length && fromParts[common] === toParts[common]) {
        common++
    }

    const upCount = fromParts.length - common
    const remaining = toParts.slice(common)
    const parts = []
    for (let i = 0; i < upCount; i++) parts.push('..')
    parts.push(...remaining)

    return parts.join('/') || '.'
}

// ─── Options handling ─────────────────────────────────────────────────────

function handleOptionsSelect(value) {
    if (value === 'show-hidden') {
        showHidden.value = !showHidden.value
    } else if (value === 'show-ignored') {
        showIgnored.value = !showIgnored.value
    } else if (value?.startsWith('root:')) {
        const key = value.slice(5)
        if (key !== selectedRootKey.value) {
            selectedRootKey.value = key
        }
    }
}

// Refetch tree when options or root change while the popup is open
watch(
    () => [selectedRootKey.value, showHidden.value, showIgnored.value],
    () => {
        if (!isOpen.value || !directory.value) return
        fetchTree(directory.value)
        // Re-run the active search if any, so results reflect new options
        if (fileTreePanelRef.value?.isSearching && fileTreePanelRef.value?.searchQuery.trim()) {
            fileTreePanelRef.value.rerunSearch()
        }
    }
)

// ─── Click outside to close ───────────────────────────────────────────────

function onDocumentClick(event) {
    if (!isOpen.value) return
    const popup = popupRef.value
    if (!popup) return
    if (popup.contains(event.target)) return
    close()
}

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

/**
 * Handle Escape key to close the popup.
 * Intercepts the event before FileTreePanel's own Escape handler.
 */
function onPickerKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
    }
}

// Clean up document listener if component is unmounted while popup is open
onBeforeUnmount(() => {
    document.removeEventListener('click', onDocumentClick, true)
})

defineExpose({ open, close, isOpen })
</script>

<template>
    <wa-popup
        ref="popupRef"
        :anchor="anchorId"
        placement="top-start"
        :active="isOpen"
        :distance="4"
        flip
        shift
        shift-padding="8"
    >
        <div class="picker-panel" @keydown.capture="onPickerKeydown">
            <!-- Header: current root path -->
            <div class="picker-header">
                <span class="picker-path" :title="directory">{{ directory || '...' }}</span>
            </div>

            <!-- File tree with search and options -->
            <FileTreePanel
                ref="fileTreePanelRef"
                :tree="tree"
                :loading="loading"
                :error="error"
                :root-path="directory"
                :search-fn="doSearch"
                :lazy-load-fn="lazyLoadDir"
                :project-id="projectId"
                :session-id="sessionId"
                :is-draft="isDraft"
                :extra-query="optionsQuery()"
                :show-refresh="false"
                :show-shared-options="false"
                mode="files"
                @file-select="onFileSelect"
                @option-select="handleOptionsSelect"
                @filter-input="(query) => emit('filter-change', query)"
            >
                <template #options-before>
                    <wa-dropdown-item
                        type="checkbox"
                        value="show-hidden"
                        :checked="showHidden"
                    >
                        Show hidden files
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-if="isGit"
                        type="checkbox"
                        value="show-ignored"
                        :checked="showIgnored"
                    >
                        Show git ignored files
                    </wa-dropdown-item>
                    <template v-if="availableRoots.length >= 1">
                        <wa-divider></wa-divider>
                        <wa-dropdown-item disabled class="dropdown-header">
                            Root:
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            v-for="root in availableRoots"
                            :key="root.key"
                            type="checkbox"
                            :value="'root:' + root.key"
                            :checked="selectedRootKey === root.key"
                            :data-root-selected="selectedRootKey === root.key ? 'true' : 'false'"
                        >
                            <div>{{ root.label }}</div>
                            <div class="root-path">{{ root.path }}</div>
                        </wa-dropdown-item>
                    </template>
                    <wa-divider></wa-divider>
                </template>
            </FileTreePanel>
        </div>
    </wa-popup>
</template>

<style scoped>
.picker-panel {
    width: min(40rem, calc(100vw - 1rem));
    max-height: min(25rem, 80vh);
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

.root-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

/* Force-sync checkmark visual on root selector items (same fix as FilesPanel) */
wa-dropdown-item[data-root-selected="true"]::part(checkmark) {
    visibility: visible;
}
wa-dropdown-item[data-root-selected="false"]::part(checkmark) {
    visibility: hidden;
}
</style>
