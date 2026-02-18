<script setup>
import { ref, computed, watch, nextTick, onMounted, onActivated, onDeactivated } from 'vue'
import { apiFetch } from '../utils/api'
import { useSettingsStore } from '../stores/settings'
import { useContainerBreakpoint } from '../composables/useContainerBreakpoint'
import {
    GitLog,
    GitLogGraphHTMLGrid,
    GitLogTable,
    GitLogTags,
} from './GitLog'
import GitPanelHeader from './GitPanelHeader.vue'
import FileTreePanel from './FileTreePanel.vue'
import FilePane from './FilePane.vue'
import { searchTreeFiles } from '../utils/treeSearch'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    gitDirectory: {
        type: String,
        default: null,
    },
    initialBranch: {
        type: String,
        default: '',
    },
    active: {
        type: Boolean,
        default: false,
    },
    isDraft: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()

// ─── Mobile breakpoint detection ─────────────────────────────────────────────
// Uses a ResizeObserver on .main-content instead of a viewport media query,
// so the panel reacts to the actual available width (e.g. sidebar open/close).

const { isBelowBreakpoint: isMobile } = useContainerBreakpoint({
    containerSelector: '.main-content',
    breakpoint: 640,
})

// ---------------------------------------------------------------------------
// API prefix (project-level for drafts, session-level otherwise)
// ---------------------------------------------------------------------------

const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const started = ref(false)
const loading = ref(false)
const refreshing = ref(false)
const error = ref(null)
const entries = ref([])
const currentBranch = ref('')
const headCommitHash = ref('')
const hasMore = ref(false)
const branches = ref([])
const selectedBranch = ref(props.initialBranch)  // '' = all branches

/** Other branches (all except the current/session branch). */
const otherBranches = computed(() =>
    branches.value.filter((b) => b !== currentBranch.value)
)

/**
 * Index changed files data from the git-log response.
 * Shape: { stats: { modified, added, deleted }, tree: { name, type, loaded, children } }
 * or null if no changes.
 */
const indexFilesData = ref(null)

/** Counts from index — passed to GitLog's indexStatus prop. */
const indexStatus = computed(() => indexFilesData.value?.stats ?? null)

// ---------------------------------------------------------------------------
// Git log filter
// ---------------------------------------------------------------------------

const filterText = ref('')

const commitFilter = computed(() => {
    const text = filterText.value.trim().toLowerCase()
    if (!text) return undefined
    return (commits) => commits.filter((c) =>
        c.message.toLowerCase().includes(text)
    )
})

// ---------------------------------------------------------------------------
// Git log overlay toggle & commit selection
// ---------------------------------------------------------------------------

const gitLogOpen = ref(false)
const selectedCommit = ref(null)

/**
 * Commit changed files data fetched from git-commit-files endpoint.
 * Shape: { stats: { modified, added, deleted }, tree: { name, type, loaded, children } }
 */
const commitFilesData = ref(null)
const commitFilesLoading = ref(false)

/** Stats for the header: commit-specific when a commit is selected, index otherwise. */
const headerStats = computed(() => {
    if (!selectedCommit.value || selectedCommit.value.hash === 'index') {
        return indexFilesData.value?.stats ?? null
    }
    return commitFilesData.value?.stats ?? null
})

function toggleGitLog() {
    gitLogOpen.value = !gitLogOpen.value
}

function onBranchChange(event) {
    selectedBranch.value = event.target.value
    refreshGitLog()
}

function onCommitSelected(commit) {
    selectedCommit.value = commit || null
    if (commit) {
        gitLogOpen.value = false
    }
}

// ---------------------------------------------------------------------------
// Current files data (index or commit)
// ---------------------------------------------------------------------------

/** The current files data: index when viewing uncommitted, commit-specific otherwise. */
const currentFilesData = computed(() => {
    if (!selectedCommit.value || selectedCommit.value.hash === 'index') {
        return indexFilesData.value
    }
    return commitFilesData.value
})

/** The raw tree from the API (before filtering). */
const currentTree = computed(() => currentFilesData.value?.tree ?? null)

// --- Display options ---
const showUntracked = ref(true)

/**
 * Recursively filter out untracked files from a tree.
 * Returns a new tree without untracked files, or null if the tree is empty after filtering.
 */
function filterUntracked(node) {
    if (!node) return null
    if (node.type === 'file') {
        return node.unstaged_status === 'untracked' ? null : node
    }
    if (!node.children) return node
    const children = node.children
        .map(filterUntracked)
        .filter(Boolean)
    if (children.length === 0) return null
    return { ...node, children }
}

/** The tree to display in the file tree panel (with optional untracked filtering). */
const displayTree = computed(() => {
    const tree = currentTree.value
    if (!tree || showUntracked.value) return tree
    return filterUntracked(tree)
})

/** Whether we are viewing the index (uncommitted changes) vs a specific commit. */
const isViewingIndex = computed(() => {
    return !selectedCommit.value || selectedCommit.value.hash === 'index'
})

// ---------------------------------------------------------------------------
// Fetch commit files when a commit is selected
// ---------------------------------------------------------------------------

watch(selectedCommit, async (commit) => {
    // Reset data
    commitFilesData.value = null

    if (!commit || commit.hash === 'index') {
        // Re-fetch index files silently (they may have changed)
        await refreshIndexFiles()
        return
    }

    commitFilesLoading.value = true
    try {
        const url = `${apiPrefix.value}/git-commit-files/${commit.hash}/`
        const res = await apiFetch(url)

        if (res.ok) {
            commitFilesData.value = await res.json()
        }
    } catch {
        // Silently ignore — header will just not show stats
    } finally {
        commitFilesLoading.value = false
    }
})

// ---------------------------------------------------------------------------
// File tree panel integration
// ---------------------------------------------------------------------------

const fileTreePanelRef = ref(null)

/** Selected file relative path from the FileTreePanel. */
const selectedFile = computed(() => fileTreePanelRef.value?.selectedFile ?? null)

/**
 * Search callback: filters the current tree client-side.
 * Uses the same fuzzy/exact search logic as the backend.
 */
function handleOptionsSelect(value) {
    if (value === 'show-untracked') {
        showUntracked.value = !showUntracked.value
    }
}

function doSearch(query) {
    const tree = displayTree.value
    if (!tree) return null
    return searchTreeFiles(tree, query)
}

/**
 * Find the first file node in a tree (depth-first).
 * Returns the full path built by joining ancestor names, matching
 * the path format used by FileTree (rootPath/dir/dir/file).
 */
function findFirstFile(node, parentPath = '') {
    if (!node) return null
    const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name
    if (node.type === 'file') return currentPath
    if (node.children) {
        for (const child of node.children) {
            const found = findFirstFile(child, currentPath)
            if (found) return found
        }
    }
    return null
}

// Re-run search and auto-select first file when the tree data changes
// (e.g. switching between index and commit)
watch(displayTree, (tree) => {
    fileTreePanelRef.value?.clearSearch()

    // Auto-select first file in the new tree
    if (tree) {
        const firstFilePath = findFirstFile(tree)
        if (firstFilePath) {
            nextTick(() => {
                fileTreePanelRef.value?.onFileSelect(firstFilePath)
            })
        }
    }
})

/**
 * Fetch index files from the dedicated endpoint.
 * Lightweight alternative to re-fetching the entire git log.
 */
async function refreshIndexFiles() {
    commitFilesLoading.value = true
    try {
        const url = `${apiPrefix.value}/git-index-files/`
        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            indexFilesData.value = data || null
        }
    } catch {
        // Silently ignore — index data just stays stale
    } finally {
        commitFilesLoading.value = false
    }

    // Re-fetch the diff for the currently selected file (if any)
    if (selectedFile.value) {
        fetchDiff(selectedFile.value)
    }
}

// ---------------------------------------------------------------------------
// Diff data (fetched when a file is selected)
// ---------------------------------------------------------------------------

const diffData = ref(null)        // { original, modified, binary, error }
const diffLoading = ref(false)
const diffError = ref(null)

/** Absolute file path for the selected file (needed by FilePane for language detection and save). */
const selectedFilePath = computed(() => {
    if (!selectedFile.value) return null
    if (props.gitDirectory) {
        return `${props.gitDirectory}/${selectedFile.value}`
    }
    return selectedFile.value
})

/** Fetch diff data for a file. */
async function fetchDiff(file) {
    if (!file) {
        diffData.value = null
        return
    }

    diffLoading.value = true
    diffError.value = null

    try {
        let url
        if (isViewingIndex.value) {
            url = `${apiPrefix.value}/git-index-file-diff/?path=${encodeURIComponent(file)}`
        } else {
            url = `${apiPrefix.value}/git-commit-file-diff/${selectedCommit.value.hash}/?path=${encodeURIComponent(file)}`
        }

        const res = await apiFetch(url)
        const data = await res.json()

        if (!res.ok || data.error) {
            diffError.value = data.error || 'Failed to load diff'
            return
        }

        diffData.value = data
    } catch {
        diffError.value = 'Network error'
    } finally {
        diffLoading.value = false
    }
}

// Fetch diff when a file is selected
watch(selectedFile, (file) => {
    fetchDiff(file)
})

// ---------------------------------------------------------------------------
// Theme — follow app-wide effective theme
// ---------------------------------------------------------------------------

const themeMode = computed(() => settingsStore.getEffectiveTheme)

// Use a palette that matches the current theme
const colours = computed(() =>
    themeMode.value === 'dark' ? 'neon-aurora-dark' : 'neon-aurora-light'
)

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const apiUrl = computed(() => {
    const base = `${apiPrefix.value}/git-log/`
    if (selectedBranch.value) {
        return `${base}?branch=${encodeURIComponent(selectedBranch.value)}`
    }
    return base
})

async function fetchGitLog() {
    loading.value = true
    error.value = null

    // First fetch is always unfiltered (--all) so we get the full branch list
    // and current branch. We'll select the right branch and re-fetch if needed.
    const base = `${apiPrefix.value}/git-log/`

    try {
        const res = await apiFetch(base)

        if (!res.ok) {
            const data = await res.json().catch(() => ({}))
            error.value = data.error || `Request failed (${res.status})`
            return
        }

        const data = await res.json()
        entries.value = data.entries || []
        currentBranch.value = data.current_branch || ''
        headCommitHash.value = data.head_commit_hash || ''
        indexFilesData.value = data.index_files || null
        hasMore.value = data.has_more || false
        branches.value = data.branches || []

        // Determine which branch to select:
        // - If initialBranch exists in the branch list, use it
        // - Otherwise, fall back to the actual current branch of the git directory
        const branchList = data.branches || []
        let targetBranch = currentBranch.value
        if (props.initialBranch && branchList.includes(props.initialBranch)) {
            targetBranch = props.initialBranch
        }

        if (targetBranch) {
            selectedBranch.value = targetBranch
        }
    } catch (e) {
        error.value = 'Failed to load git history'
    } finally {
        loading.value = false
    }

    // If a specific branch was selected, re-fetch filtered by that branch
    if (selectedBranch.value) {
        refreshGitLog()
    }
}

/**
 * Refresh the git log (called from the overlay header button).
 * Unlike the initial fetchGitLog, this sets `refreshing` instead of `loading`
 * so the overlay stays visible with its current content while refreshing.
 */
async function refreshGitLog() {
    if (refreshing.value) return
    refreshing.value = true

    try {
        const res = await apiFetch(apiUrl.value)

        if (!res.ok) {
            return
        }

        const data = await res.json()
        entries.value = data.entries || []
        currentBranch.value = data.current_branch || ''
        headCommitHash.value = data.head_commit_hash || ''
        indexFilesData.value = data.index_files || null
        hasMore.value = data.has_more || false
        branches.value = data.branches || []
    } catch {
        // Silently ignore — existing data stays visible
    } finally {
        refreshing.value = false
    }
}

// ---------------------------------------------------------------------------
// Lazy init: fetch only when the tab becomes active for the first time
// ---------------------------------------------------------------------------

watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            started.value = true
            fetchGitLog()
        }
    },
    { immediate: true },
)

// ---------------------------------------------------------------------------
// Split panel position (KeepAlive-safe)
// ---------------------------------------------------------------------------

const TREE_DEFAULT_WIDTH = 250
const treePanelWidth = ref(TREE_DEFAULT_WIDTH)
const splitPanelRef = ref(null)

// Hide the split panel during KeepAlive transitions to prevent the visual
// glitch where wa-split-panel briefly renders at position 0 before Vue
// re-applies the correct width binding.
const keepAliveHidden = ref(false)

onDeactivated(() => {
    keepAliveHidden.value = true
})

onActivated(() => {
    // Ensure reparented nodes are in the right container after KeepAlive reactivation
    nextTick(() => reparentNodes(isMobile.value))

    const panel = splitPanelRef.value
    const savedWidth = treePanelWidth.value
    // wa-split-panel re-initializes its internal state when KeepAlive re-inserts
    // the DOM. A double rAF waits for the web component to finish its re-init
    // cycle, then we force our saved width and reveal.
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            if (panel) {
                panel.positionInPixels = savedWidth
            }
            keepAliveHidden.value = false
        })
    })
})

// Track the last valid position to restore it after KeepAlive reactivation.
function handleTreeReposition(event) {
    if (keepAliveHidden.value) return
    const newWidth = event.target.positionInPixels
    if (newWidth == null || Number.isNaN(newWidth) || newWidth <= 0) return
    treePanelWidth.value = newWidth
}

// ─── DOM reparenting on layout switch ────────────────────────────────────────
// Same pattern as FilesPanel: a single FileTreePanel and FilePane instance
// are rendered in hidden "owner" divs, then moved into the active layout
// container (desktop split-panel or mobile stack) to preserve all state.

const treeOwnerRef = ref(null)
const contentOwnerRef = ref(null)
const desktopTreeSlotRef = ref(null)
const desktopContentSlotRef = ref(null)
const mobileTreeSlotRef = ref(null)
const mobileContentSlotRef = ref(null)

let treeNode = null
let contentNode = null

function reparentNodes(mobile) {
    const treeTarget = mobile ? mobileTreeSlotRef.value : desktopTreeSlotRef.value
    const contentTarget = mobile ? mobileContentSlotRef.value : desktopContentSlotRef.value

    if (!treeTarget || !contentTarget) return

    if (!treeNode) treeNode = treeOwnerRef.value?.firstElementChild
    if (!contentNode) contentNode = contentOwnerRef.value?.firstElementChild
    if (!treeNode || !contentNode) return

    if (treeNode.parentElement !== treeTarget) {
        treeTarget.appendChild(treeNode)
    }
    if (contentNode.parentElement !== contentTarget) {
        contentTarget.appendChild(contentNode)
    }
}

watch(isMobile, (mobile) => {
    nextTick(() => reparentNodes(mobile))
})

onMounted(() => {
    nextTick(() => reparentNodes(isMobile.value))
})
</script>

<template>
    <div class="git-panel">
        <!-- Loading state -->
        <div v-if="loading" class="panel-state">
            <wa-spinner></wa-spinner>
            <span>Loading git history...</span>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="panel-state">
            <wa-callout variant="danger" appearance="outlined">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                <div class="error-content">
                    <div>{{ error }}</div>
                    <wa-button
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        @click="fetchGitLog"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Retry
                    </wa-button>
                </div>
            </wa-callout>
        </div>

        <!-- Empty state (no commits) -->
        <div v-else-if="started && entries.length === 0" class="panel-state">
            <span class="panel-placeholder">No commits found</span>
        </div>

        <!-- Main content: header + split panel + git log overlay -->
        <template v-else-if="entries.length > 0">
            <!-- Header with commit selector -->
            <GitPanelHeader
                :selected-commit="selectedCommit"
                :selected-branch="selectedBranch"
                :stats="headerStats"
                :stats-loading="commitFilesLoading"
                :git-log-open="gitLogOpen"
                @toggle-git-log="toggleGitLog"
            />
            <wa-divider></wa-divider>

            <!-- Content area (position: relative so overlay can cover it) -->
            <div class="git-panel-content">
                <!-- ═══ Hidden owners: single instances that get reparented ═══ -->
                <div ref="treeOwnerRef" class="reparent-owner">
                    <FileTreePanel
                        ref="fileTreePanelRef"
                        :tree="displayTree"
                        :loading="commitFilesLoading"
                        :root-path="displayTree?.name"
                        :search-fn="doSearch"
                        :lazy-load-fn="null"
                        :project-id="projectId"
                        :session-id="sessionId"
                        :show-refresh="isViewingIndex"
                        :active="active"
                        :is-mobile="isMobile"
                        mode="git"
                        @refresh="refreshIndexFiles"
                        @option-select="handleOptionsSelect"
                    >
                        <template #options-before>
                            <wa-dropdown-item
                                v-if="isViewingIndex"
                                type="checkbox"
                                value="show-untracked"
                                :checked="showUntracked"
                            >
                                Show untracked files
                            </wa-dropdown-item>
                        </template>
                    </FileTreePanel>
                </div>

                <div ref="contentOwnerRef" class="reparent-owner">
                    <div class="git-content-inner">
                        <!-- Loading diff -->
                        <div v-if="diffLoading" class="panel-placeholder">
                            <wa-spinner></wa-spinner>
                        </div>

                        <!-- Diff error -->
                        <div v-else-if="diffError" class="panel-placeholder">
                            <wa-callout variant="danger" size="small">
                                {{ diffError }}
                            </wa-callout>
                        </div>

                        <!-- Binary file -->
                        <div v-else-if="diffData?.binary" class="panel-placeholder">
                            Binary file cannot be diffed
                        </div>

                        <!-- Diff viewer (Monaco diff editor via FilePane) -->
                        <FilePane
                            v-else-if="selectedFile && diffData"
                            :project-id="projectId"
                            :session-id="sessionId"
                            :file-path="selectedFilePath"
                            diff-mode
                            :original-content="diffData.original"
                            :modified-content="diffData.modified"
                            :diff-read-only="!isViewingIndex"
                            @revert="fetchDiff(selectedFile)"
                        />

                        <!-- No file selected / no changes -->
                        <div v-else-if="!selectedFile" class="panel-placeholder">
                            {{ !displayTree ? 'No changes' : 'Select a file' }}
                        </div>
                    </div>
                </div>

                <!-- ═══ Desktop layout: split panel ═══ -->
                <wa-split-panel
                    v-show="!isMobile"
                    ref="splitPanelRef"
                    class="git-split-panel"
                    :class="{ 'keep-alive-hidden': keepAliveHidden }"
                    :position-in-pixels="treePanelWidth"
                    primary="start"
                    snap="150px 250px 350px"
                    snap-threshold="30"
                    @wa-reposition="handleTreeReposition"
                >
                    <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

                    <!-- Empty slots — filled by reparenting -->
                    <div ref="desktopTreeSlotRef" slot="start" class="git-tree-slot"></div>
                    <div ref="desktopContentSlotRef" slot="end" class="git-content-panel"></div>
                </wa-split-panel>

                <!-- ═══ Mobile layout: stacked ═══ -->
                <div v-show="isMobile" class="mobile-layout">
                    <div ref="mobileTreeSlotRef" class="mobile-tree-slot"></div>
                    <div ref="mobileContentSlotRef" class="git-content-panel"></div>
                </div>

                <!-- Git log overlay (absolute, shown when chevron is clicked) -->
                <div v-if="gitLogOpen" class="gitlog-overlay">
                    <!-- Overlay header -->
                    <div class="gitlog-overlay-header">
                        <wa-select
                            v-if="branches.length"
                            size="small"
                            class="branch-select"
                            :value.prop="selectedBranch"
                            @change="onBranchChange"
                        >
                            <wa-icon slot="prefix" name="code-branch"></wa-icon>
                            <wa-option value="">All branches</wa-option>
                            <wa-divider></wa-divider>
                            <wa-option
                                v-if="currentBranch"
                                :value="currentBranch"
                            >{{ currentBranch }}</wa-option>
                            <template v-if="otherBranches.length">
                                <wa-divider></wa-divider>
                                <wa-option
                                    v-for="branch in otherBranches"
                                    :key="branch"
                                    :value="branch"
                                >{{ branch }}</wa-option>
                            </template>
                        </wa-select>
                        <wa-input
                            v-model="filterText"
                            class="filter-input"
                            size="small"
                            placeholder="Filter commits..."
                            clearable
                        >
                            <wa-icon slot="prefix" name="magnifying-glass"></wa-icon>
                        </wa-input>

                        <wa-button
                            class="refresh-button"
                            variant="neutral"
                            appearance="filled-outlined"
                            size="small"
                            :loading="refreshing"
                            @click="refreshGitLog"
                        >
                            <wa-icon name="arrow-rotate-right"></wa-icon>
                        </wa-button>
                    </div>

                    <GitLog
                        :entries="entries"
                        :current-branch="currentBranch"
                        :head-commit-hash="headCommitHash"
                        :index-status="indexStatus"
                        :theme="themeMode"
                        :colours="colours"
                        :filter="commitFilter"
                        :show-headers="false"
                        :node-size=10
                        :row-height=28
                        :on-select-commit="onCommitSelected"
                    >
                        <template #tags>
                            <GitLogTags />
                        </template>
                        <template #graph>
                            <GitLogGraphHTMLGrid
                                :show-commit-node-tooltips="true"
                                :show-commit-node-hashes="false"
                            />
                        </template>
                        <template #table>
                            <GitLogTable timestamp-format="YYYY-MM-DD HH:mm" />
                        </template>
                    </GitLog>
                </div>
            </div>
        </template>
    </div>
</template>

<style scoped>
.git-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative;
}

.panel-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

wa-callout {
    max-width: min(40rem, 90vh);
}

.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}


.git-panel-header {
    & + wa-divider {
        flex-shrink: 0;
        --width: 4px;
        --spacing: 0;
    }
}

/* ----- Content area ----- */

.git-panel-content {
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
}

/* Hidden owner divs: components are rendered here then reparented into
   the appropriate layout container (desktop split-panel or mobile stack). */
.reparent-owner {
    display: none;
}

/* Mobile layout */
.mobile-layout {
    display: flex;
    flex-direction: column;
    height: 100%;
}

.mobile-layout > .git-content-panel {
    flex: 1;
    min-height: 0;
}

/* ----- Split panel ----- */

.git-split-panel {
    height: 100%;
    --min: 120px;
    --max: 60%;

    &.keep-alive-hidden {
        visibility: hidden;
    }

    &::part(divider) {
        background-color: var(--wa-color-surface-border);
        width: 4px;
    }
}

/* Divider handle (visible on touch devices only) */
.divider-handle {
    color: var(--wa-color-surface-border);
    display: none;
    scale: 3;
}

@media (pointer: coarse) {
    .divider-handle {
        display: inline;
    }
}

/* ----- Panel slots ----- */

.git-tree-slot {
    height: 100%;
    overflow: hidden;
}

.git-content-panel {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
    position: relative;
}

.git-content-inner {
    height: 100%;
    display: flex;
    flex-direction: column;
}

/* ----- Git log overlay (absolute over content) ----- */

.gitlog-overlay {
    position: absolute;
    inset: 0;
    z-index: 11;
    overflow: hidden;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;

    /* Let the GitLog component fill the remaining space below the header */
    :deep(> .container) {
        flex: 1;
        min-height: 0;
    }
}

/* Overlay header */

.gitlog-overlay-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    padding: var(--wa-space-3xs) var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-surface-border);
}

.branch-select {
    flex-shrink: 0;
    max-width: 12rem;
    wa-divider {
        --spacing: var(--wa-space-2xs);
    }
}

.filter-input {
    flex: 1;
    min-width: 0;
}

.refresh-button {
    flex-shrink: 0;

    &::part(base) {
        padding: var(--wa-space-3xs) var(--wa-space-2xs);
    }
}
</style>
