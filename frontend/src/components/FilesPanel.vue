<script setup>
import { ref, computed, watch, nextTick, onMounted, onActivated, onDeactivated } from 'vue'
import { apiFetch } from '../utils/api'
import { useContainerBreakpoint } from '../composables/useContainerBreakpoint'
import FileTreePanel from './FileTreePanel.vue'
import FilePane from './FilePane.vue'

const props = defineProps({
    projectId: {
        type: String,
        default: null,
    },
    sessionId: {
        type: String,
        default: null,
    },
    gitDirectory: {
        type: String,
        default: null,
    },
    projectGitRoot: {
        type: String,
        default: null,
    },
    projectDirectory: {
        type: String,
        default: null,
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

// ─── Mobile breakpoint detection ─────────────────────────────────────────────
// Uses a ResizeObserver on .main-content instead of a viewport media query,
// so the panel reacts to the actual available width (e.g. sidebar open/close).

const { isBelowBreakpoint: isMobile } = useContainerBreakpoint({
    containerSelector: '.main-content',
    breakpoint: 640,
})

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

// Lazy init: defer all loading until the tab becomes active for the first time
const started = ref(false)

// Template ref for the FileTreePanel child component — declared early because
// immediate watchers below may reference it before the "File selection" section.
const fileTreePanelRef = ref(null)

// ─── Root directory selection ────────────────────────────────────────────────

/**
 * Available root directories.
 * Each entry: { key, label, path }
 *
 * The effective git root is the session's git_directory (from tool_use analysis)
 * or, if absent, the project's git_root (from walking up from project directory).
 *
 * If git root and project directory are the same path, they are merged into a
 * single entry labelled "Project directory (git root)".
 *
 * When the git root comes from the session (active git context), it is the
 * default (listed first). When it comes from the project only (the project
 * happens to be inside a git repo, but the session hasn't touched git), the
 * project directory is the default and the git root is listed second.
 */
const availableRoots = computed(() => {
    const sessionGit = props.gitDirectory
    const projectGitRoot = props.projectGitRoot
    const git = sessionGit || projectGitRoot
    const project = props.projectDirectory

    if (git && project && git === project) {
        // Same path — merge into one entry
        return [{ key: 'project', label: 'Project directory (git root)', path: project }]
    }

    const roots = []
    if (sessionGit) {
        // Session has an active git context — git root is the default
        roots.push({ key: 'git', label: 'Git root', path: sessionGit })
        if (project && project !== sessionGit) {
            roots.push({ key: 'project', label: 'Project directory', path: project })
        }
    } else {
        // No session git — project directory is the default
        if (project) {
            roots.push({ key: 'project', label: 'Project directory', path: project })
        }
        if (projectGitRoot && projectGitRoot !== project) {
            roots.push({ key: 'git', label: 'Git root', path: projectGitRoot })
        }
    }
    return roots
})

const selectedRootKey = ref(null)

/**
 * Set of root keys whose directories no longer exist on disk.
 * Populated when fetchTree receives a 404 for a root directory.
 * Used to disable the corresponding dropdown items.
 */
const missingRoots = ref(new Set())

/**
 * The currently active directory path, derived from the selected root.
 */
const directory = computed(() => {
    const roots = availableRoots.value
    if (!roots.length) return null
    const selected = roots.find(r => r.key === selectedRootKey.value)
    return selected ? selected.path : roots[0].path
})

// Reset selection when the available roots change (e.g. new session)
watch(availableRoots, (roots) => {
    if (!roots.length) {
        selectedRootKey.value = null
        return
    }
    // Keep current selection if still valid
    if (selectedRootKey.value && roots.find(r => r.key === selectedRootKey.value)) return
    // Default to first (git > cwd > project)
    selectedRootKey.value = roots[0].key
}, { immediate: true })

function handleRootSelect(key) {
    if (key !== selectedRootKey.value && !missingRoots.value.has(key)) {
        selectedRootKey.value = key
    }
}

// ─── Display options ─────────────────────────────────────────────────────────

const showHidden = ref(false)
const showIgnored = ref(false)
const isGit = ref(false)

/**
 * Build the query string fragment for display options.
 */
function optionsQuery() {
    let qs = ''
    if (showHidden.value) qs += '&show_hidden=1'
    if (showIgnored.value) qs += '&show_ignored=1'
    return qs
}

// ─── Tree state ──────────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const error = ref(null)

/**
 * Fetch the directory tree from the backend.
 */
async function fetchTree(projectId, sessionId, dirPath) {
    if (!projectId || !dirPath) {
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

            // If the directory was not found, mark this root as missing
            // and automatically fall back to the project directory when possible.
            if (res.status === 404 && selectedRootKey.value === 'git') {
                missingRoots.value = new Set([...missingRoots.value, 'git'])
                const projectRoot = availableRoots.value.find(r => r.key === 'project')
                if (projectRoot) {
                    selectedRootKey.value = 'project'
                    // The watcher on `directory` will re-trigger fetchTree
                    // with the project directory, so we can return here.
                    return
                }
            }

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

// ─── Search & lazy-load callbacks for FileTreePanel ─────────────────────────

/**
 * Search callback: calls the backend file-search API.
 * Returns { tree, total, truncated } on success, null on failure.
 */
async function doSearch(query) {
    if (!props.projectId || !directory.value) return null

    const res = await apiFetch(
        `${apiPrefix.value}/file-search/?path=${encodeURIComponent(directory.value)}&q=${encodeURIComponent(query)}${optionsQuery()}`
    )
    if (res.ok) {
        const data = await res.json()
        return { tree: data, total: data.total, truncated: data.truncated }
    }
    return null
}

/**
 * Lazy-load callback for scrollToPath: fetches a directory's children.
 * Returns { children: [...] } on success, null on failure.
 */
async function lazyLoadDir(path) {
    const res = await apiFetch(
        `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(path)}${optionsQuery()}`
    )
    if (!res.ok) return null
    return await res.json()
}

// ─── Lazy init ───────────────────────────────────────────────────────────────

// Start loading only when the tab becomes active for the first time
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            started.value = true
        }
    },
    { immediate: true },
)

// Fetch tree whenever the directory, projectId, or display options change
// (only after the panel has been started)
watch(
    () => [started.value, props.projectId, props.sessionId, directory.value, showHidden.value, showIgnored.value],
    ([isStarted, newProjectId, newSessionId, newDir]) => {
        if (!isStarted) return
        fetchTree(newProjectId, newSessionId, newDir)
        // Re-run the active search if any, so results reflect new options
        if (fileTreePanelRef.value?.isSearching && fileTreePanelRef.value?.searchQuery.trim()) {
            fileTreePanelRef.value.rerunSearch()
        } else {
            fileTreePanelRef.value?.clearSearch(false)  // No reveal — tree is being re-fetched
        }
    },
    { immediate: true }
)

// ─── Options handling (files-specific items) ─────────────────────────────────

function handleOptionsSelect(value) {
    if (value === 'show-hidden') {
        showHidden.value = !showHidden.value
    } else if (value === 'show-ignored') {
        showIgnored.value = !showIgnored.value
    } else if (value?.startsWith('root:')) {
        handleRootSelect(value.slice(5))
    }
}

/**
 * Refresh: re-fetch the tree from the root, and re-run the search if active.
 * After refresh, scrolls back to the previously selected file. If it no longer
 * exists, clears the selection and scrolls to the root.
 */
async function refresh() {
    const fileToScroll = fileTreePanelRef.value?.selectedAbsPath

    await fetchTree(props.projectId, props.sessionId, directory.value)

    if (fileTreePanelRef.value?.isSearching && fileTreePanelRef.value?.searchQuery.trim()) {
        fileTreePanelRef.value.rerunSearch()
    }

    // Scroll to previously selected file in the refreshed tree
    if (fileToScroll && tree.value) {
        const found = await fileTreePanelRef.value?.scrollToPath(fileToScroll)
        if (!found) {
            // File no longer exists — clear selection and scroll to root
            fileTreePanelRef.value.selectedFile.value = null
            await fileTreePanelRef.value?.scrollToPath(directory.value)
        }
    }
}

// ─── File selection ──────────────────────────────────────────────────────────

/**
 * Selected file relative path — proxied from the FileTreePanel ref.
 */
const selectedFile = computed(() => fileTreePanelRef.value?.selectedFile ?? null)

/**
 * Absolute path of the currently selected file.
 */
const selectedAbsPath = computed(() => {
    if (!selectedFile.value || !directory.value) return null
    return `${directory.value}/${selectedFile.value}`
})

// ─── Split panel position (KeepAlive-safe) ──────────────────────────────────

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

    // wa-split-panel's internal state gets desynchronized from the HTML attribute
    // during KeepAlive transitions. Vue updates the attribute but the web component
    // doesn't re-read it. Force the JS property directly, then reveal.
    const panel = splitPanelRef.value
    const savedWidth = treePanelWidth.value
    // wa-split-panel re-initializes its internal state (connectedCallback / ResizeObserver)
    // when KeepAlive re-inserts the DOM, overwriting positionInPixels to NaN.
    // A double rAF waits for the web component to finish its re-init cycle,
    // then we force our saved width and reveal.
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
// During transitions, wa-split-panel emits spurious reposition events (null, 0,
// or clamped to --min). Ignore all repositions while the panel is hidden.
function handleTreeReposition(event) {
    if (keepAliveHidden.value) return
    const newWidth = event.target.positionInPixels
    if (newWidth == null || Number.isNaN(newWidth) || newWidth <= 0) return
    treePanelWidth.value = newWidth
}

// ─── DOM reparenting on layout switch ────────────────────────────────────────
// A single FileTreePanel and FilePane instance are always rendered inside
// hidden "owner" divs. When the layout switches between desktop (split panel)
// and mobile (stacked), we move the actual DOM nodes into the appropriate
// container so the component state (tree expansion, Monaco editor, unsaved
// changes…) is fully preserved.

const treeOwnerRef = ref(null)        // hidden div that owns the FileTreePanel instance
const contentOwnerRef = ref(null)     // hidden div that owns the FilePane instance
const desktopTreeSlotRef = ref(null)  // slot="start" container inside wa-split-panel
const desktopContentSlotRef = ref(null) // slot="end" container inside wa-split-panel
const mobileTreeSlotRef = ref(null)   // mobile container for the tree
const mobileContentSlotRef = ref(null) // mobile container for the content

// Persistent references to the actual DOM nodes being reparented.
// Set once on mount, then reused across all reparenting operations.
let treeNode = null
let contentNode = null

function reparentNodes(mobile) {
    const treeTarget = mobile ? mobileTreeSlotRef.value : desktopTreeSlotRef.value
    const contentTarget = mobile ? mobileContentSlotRef.value : desktopContentSlotRef.value

    if (!treeTarget || !contentTarget) return

    // Lazily grab the nodes on first call
    if (!treeNode) treeNode = treeOwnerRef.value?.firstElementChild
    if (!contentNode) contentNode = contentOwnerRef.value?.firstElementChild
    if (!treeNode || !contentNode) return

    // Move nodes only if they aren't already in the right container
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

// Initial placement once the DOM is ready
onMounted(() => {
    nextTick(() => reparentNodes(isMobile.value))
})
</script>

<template>
    <div class="files-panel">
        <!-- ═══ Hidden owners: single instances that get reparented ═══ -->
        <div ref="treeOwnerRef" class="reparent-owner">
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
                :show-refresh="true"
                :active="active"
                :is-mobile="isMobile"
                mode="files"
                @refresh="refresh"
                @option-select="handleOptionsSelect"
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
                        :disabled="missingRoots.has(root.key)"
                    >
                        <div>{{ root.label }}</div>
                        <div class="root-path">{{ root.path }}</div>
                        <div v-if="missingRoots.has(root.key)" class="root-missing">Directory no longer exists</div>
                    </wa-dropdown-item>
                    <wa-divider></wa-divider>
                </template>
            </FileTreePanel>
        </div>

        <div ref="contentOwnerRef" class="reparent-owner">
            <div class="files-content-inner">
                <FilePane
                    v-show="selectedFile"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :file-path="selectedAbsPath"
                    :active="active"
                    :is-draft="isDraft"
                />
                <div v-show="!selectedFile" class="panel-placeholder">
                    Select a file
                </div>
            </div>
        </div>

        <!-- ═══ Desktop layout: split panel ═══ -->
        <wa-split-panel
            v-show="!isMobile"
            ref="splitPanelRef"
            class="files-split-panel"
            :class="{ 'keep-alive-hidden': keepAliveHidden }"
            :position-in-pixels="treePanelWidth"
            primary="start"
            snap="150px 250px 350px"
            snap-threshold="30"
            @wa-reposition="handleTreeReposition"
        >
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- Empty slots — filled by reparenting -->
            <div ref="desktopTreeSlotRef" slot="start" class="files-tree-slot"></div>
            <div ref="desktopContentSlotRef" slot="end" class="files-content-panel"></div>
        </wa-split-panel>

        <!-- ═══ Mobile layout: stacked ═══
             Always in the DOM (v-show) so reparenting can move nodes
             back to desktop slots before the mobile container disappears. -->
        <div v-show="isMobile" class="mobile-layout">
            <div ref="mobileTreeSlotRef" class="mobile-tree-slot"></div>
            <div ref="mobileContentSlotRef" class="files-content-panel"></div>
        </div>
    </div>
</template>

<style scoped>
/* Hidden owner divs: components are rendered here then reparented into
   the appropriate layout container (desktop split-panel or mobile stack). */
.reparent-owner {
    display: none;
}

.files-panel {
    height: 100%;
    overflow: hidden;
    position: relative;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Mobile layout
   ═══════════════════════════════════════════════════════════════════════════ */

.mobile-layout {
    display: flex;
    flex-direction: column;
    height: 100%;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Split panel: file tree (start/primary) + file content (end)
   ═══════════════════════════════════════════════════════════════════════════ */

.files-split-panel {
    height: 100%;
    --min: 120px;
    --max: 60%;

    /* Hide during KeepAlive transitions to prevent the visual glitch where
       wa-split-panel briefly renders at position 0 before Vue re-applies
       the correct width binding. */
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

/* ═══════════════════════════════════════════════════════════════════════════
   Panel slots
   ═══════════════════════════════════════════════════════════════════════════ */

.files-tree-slot {
    height: 100%;
    overflow: hidden;
}

.files-content-panel {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
    position: relative;
}

.files-content-inner {
    height: 100%;
    display: flex;
    flex-direction: column;
}

.mobile-layout > .files-content-panel {
    flex: 1;
    min-height: 0;
}

/* Placeholder styling */
.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* ═══════════════════════════════════════════════════════════════════════════
   Options slot styling (root items)
   ═══════════════════════════════════════════════════════════════════════════ */

.root-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.root-missing {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-fill-loud);
}
</style>
