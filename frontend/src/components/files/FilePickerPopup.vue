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
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { apiFetch } from '../../utils/api'
import FileTreePanel from './FileTreePanel.vue'
import { deriveFileRoots, getWorktreeParent } from '../../utils/projectRoots'
import { usePopupMotion } from '../../composables/usePopupMotion'

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
const settingsStore = useSettingsStore()

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
// The panel grows from its anchor on each opening; the close stays instant (overlay motion §6).
const panelRef = ref(null)
usePopupMotion(isOpen, panelRef, popupRef)
const fileTreePanelRef = ref(null)

// ─── Display options ──────────────────────────────────────────────────────

const showHidden = computed(() => settingsStore.showHiddenFiles)
const showIgnored = computed(() => settingsStore.showGitIgnoredFiles)
const isGit = ref(false)

function optionsQuery() {
    let qs = ''
    if (showHidden.value) qs += '&show_hidden=1'
    if (showIgnored.value) qs += '&show_ignored=1'
    return qs
}

// ─── Root directory selection ─────────────────────────────────────────────
// Canonical derivation shared with FilesPanel (utils/projectRoots.js),
// including the worktree main-repo roots.

const availableRoots = computed(() => {
    const parent = getWorktreeParent(project.value, store)
    return deriveFileRoots({
        gitDirectory: session.value?.git_directory,
        cwd: session.value?.cwd,
        projectDirectory: project.value?.directory,
        projectGitRoot: project.value?.git_root,
        parentDirectory: parent?.directory,
        parentGitRoot: parent?.git_root,
    })
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

function close(payload = {}) {
    isOpen.value = false
    tree.value = null
    error.value = null
    emit('close', payload)
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
        settingsStore.setShowHiddenFiles(!showHidden.value)
    } else if (value === 'show-ignored') {
        settingsStore.setShowGitIgnoredFiles(!showIgnored.value)
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
 * Handle Escape and Enter keys at the popup level.
 *
 * Escape: close the popup (intercepts before FileTreePanel's own handler).
 *
 * Plain Enter in the search input (focus NOT in the tree): close the popup
 * and ask the parent to preserve what was typed — never auto-select a tree
 * match from the search field. The user must explicitly navigate into the
 * tree with ArrowDown to select a file via Enter.
 *
 * Plain Enter inside the tree container: let FileTreePanel handle it
 * (activates the focused item).
 *
 * Cmd/Ctrl+Enter: no-op (does not close, does not send).
 */
function onPickerKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
        return
    }
    if (event.key === 'Enter' && !event.metaKey && !event.ctrlKey) {
        // Only intercept Enter from the search input itself. The tree's own
        // Enter handler keeps activating the focused item; the options
        // dropdown trigger and its items keep their native click behavior.
        if (!event.target.closest?.('.files-search-input')) return
        event.preventDefault()
        event.stopPropagation()
        const filterText = fileTreePanelRef.value?.searchQuery ?? ''
        close({ preserveText: filterText })
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
        class="picker-popup"
    >
        <div ref="panelRef" class="picker-panel glass-surface" @keydown.capture="onPickerKeydown">
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
    max-height: min(25rem, 60dvh);
    display: flex;
    flex-direction: column;
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}
@media (max-height: 640px) {
    .picker-popup::part(popup) {
        top: 0 !important;
    }
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
