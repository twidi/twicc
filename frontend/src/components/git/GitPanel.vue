<script setup>
import { ref, computed, watch, nextTick, provide, onMounted, onUnmounted, onActivated, onDeactivated, useId } from 'vue'
import { apiFetch } from '../../utils/api'
import { useSettingsStore } from '../../stores/settings'
import { useContainerBreakpoint } from '../../composables/useContainerBreakpoint'
import {
    GitLog,
    GitLogGraphHTMLGrid,
    GitLogTable,
    GitLogTags,
} from './GitLog'
import GitPanelHeader from './GitPanelHeader.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import GitStatusBadge from '../ui/GitStatusBadge.vue'
import FileTreePanel from '../files/FileTreePanel.vue'
import FilePane from '../files/FilePane.vue'
import { searchTreeFiles } from '../../utils/treeSearch'
import { triggerDownload } from '../../utils/download'
import { useCodeCommentsStore, buildCommentedPathsSet } from '../../stores/codeComments'
import { usePanZoom, useSyncedPanZoom } from '../../composables/usePanZoom'
import { usePanelContentFocus } from '../../composables/usePanelContentFocus'
import { useDataStore } from '../../stores/data'
import { useSplitDividerDragFlag } from '../../composables/useSplitDividerDragFlag'
import { PROCESS_STATE } from '../../constants'
import { deriveGitRoots, getWorktreeParent } from '../../utils/projectRoots'

const emit = defineEmits(['navigate', 'index-status'])

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
    projectGitRoot: {
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
    // Whether the owning session is the displayed one (not kept alive in the background). Gates
    // the light refresh of the uncommitted-changes counts shown in the Git tab's label, which
    // runs while this pane itself is hidden.
    sessionActive: {
        type: Boolean,
        default: false,
    },
    // Bumped by the parent to focus the tree's search input on a real navigation toward
    // this tab (forwarded straight to FileTreePanel; never tied to `active`/visibility).
    focusRequest: {
        type: Number,
        default: 0,
    },
    isDraft: {
        type: Boolean,
        default: false,
    },
    routeRootKey: {
        default: undefined,
    },
    routeCommitRef: {
        default: undefined,
    },
    routeFilePath: {
        default: undefined,
    },
    // Whether this panel currently owns the URL (it is the focused tab). When the
    // dockable layout shows several tool panels at once, only the focused one owns
    // the route; non-owners receive blanked route props (the URL params belong to
    // whoever owns it). Sync-from-route must run for the owner ONLY — a non-owner
    // would read the blank props as "nothing selected" and wrongly reset its root /
    // commit / open file. Defaults to true (non-docked: a panel is shown only when
    // active, so it always owns the route).
    routeOwner: {
        type: Boolean,
        default: true,
    },
})

const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const dataStore = useDataStore()
const refreshButtonId = useId()
const gitDirButtonId = useId()
let syncingFromRoute = false
const routeRootIssue = ref(null)
const routeCommitIssue = ref(null)
const routeFileIssue = ref(null)

function makeRouteIssue(before, detail = null, after = '') {
    return { before, detail, after }
}

// Transient sibling of routeFileIssue: shown (same look) when a file the user
// was viewing vanishes live — a commit or refresh drops it from the view. Unlike
// routeFileIssue it auto-dismisses, but only after the Git tab has actually been
// VISIBLE for a while: the countdown is armed on visibility (props.active), not
// when the file vanishes. A notice raised while the tab is hidden/off-screen
// therefore waits, and only starts dismissing once the tab is shown — never spent
// counting down where nobody can see it. It lives in its own ref so route resets
// can't wipe it and it can't be requalified as a permanent dead-deep-link callout.
// Posted in both the route-owner and docked (non-owner) paths so the behaviour is
// identical whether or not the Git tab currently owns the URL.
const TRANSIENT_FILE_NOTICE_MS = 5000
const transientFileNotice = ref(null)
let transientFileNoticeTimer = null
// The file whose live disappearance we last flashed, kept until the dead path is
// scrubbed from the route (or another file is selected). Lets the route-owner
// resolver treat a later focus onto that now-dead path as a vanished-while-hidden
// file (case B — transient) instead of a never-seen dead deep-link (case A —
// permanent): the docked path flags the file here but can't scrub the route
// itself. See the route-file watcher below.
let lastVanishedFilePath = null

function showTransientFileGone(filePath) {
    lastVanishedFilePath = filePath
    transientFileNotice.value = makeRouteIssue('File ', filePath, ' is no longer available in this view.')
    armTransientFileNoticeTimer()
}

// (Re)start the auto-dismiss countdown — but only while the panel is actually
// visible. Hidden (docked in the background, not its region's active tab, minimized,
// or in a non-active session) → stay disarmed and keep the notice; the props.active
// watcher re-arms it the moment the tab becomes visible.
function armTransientFileNoticeTimer() {
    if (transientFileNoticeTimer) {
        clearTimeout(transientFileNoticeTimer)
        transientFileNoticeTimer = null
    }
    if (!transientFileNotice.value || !props.active) return
    transientFileNoticeTimer = setTimeout(clearTransientFileNotice, TRANSIENT_FILE_NOTICE_MS)
}

function clearTransientFileNotice() {
    if (transientFileNoticeTimer) {
        clearTimeout(transientFileNoticeTimer)
        transientFileNoticeTimer = null
    }
    transientFileNotice.value = null
}

// Drive the countdown off real visibility: start/restart it when the tab becomes
// visible, pause it (keeping the notice) when it goes hidden.
watch(() => props.active, (active) => {
    if (active) {
        armTransientFileNoticeTimer()
    } else if (transientFileNoticeTimer) {
        clearTimeout(transientFileNoticeTimer)
        transientFileNoticeTimer = null
    }
})

const routeIssueMessage = computed(() =>
    routeRootIssue.value || routeCommitIssue.value || routeFileIssue.value || transientFileNotice.value
)

// ---------------------------------------------------------------------------
// Git root selector
// ---------------------------------------------------------------------------

/**
 * Available git roots for the directory selector. When the project is a git
 * worktree, the main repository's git root is offered too (see
 * utils/projectRoots.js → deriveGitRoots).
 */
const availableGitRoots = computed(() => {
    const parent = getWorktreeParent(dataStore.getProject(props.projectId), dataStore)
    return deriveGitRoots({
        gitDirectory: props.gitDirectory,
        projectGitRoot: props.projectGitRoot,
        parentGitRoot: parent?.git_root,
    })
})

const selectedRootKey = ref(null)

function clearSelectedFile() {
    if (fileTreePanelRef.value?.selectedFile != null) {
        fileTreePanelRef.value.selectedFile = null
    }
}

function emitNavigate({ rootKey = selectedRootKey.value, commitRef, filePath, replace = false }) {
    if (!props.active) return
    emit('navigate', {
        rootKey,
        commitRef: commitRef ?? (selectedCommit.value?.hash ?? 'index'),
        filePath,
        replace,
    })
}

/**
 * Set of root keys whose directories no longer exist on disk.
 * Populated when fetchGitLog receives a 404 for a root directory.
 * Used to disable the corresponding dropdown items.
 */
const missingRoots = ref(new Set())

/**
 * The currently active git directory path, derived from the selected root.
 */
const effectiveGitDirectory = computed(() => {
    const roots = availableGitRoots.value
    if (!roots.length) return null
    const selected = roots.find(r => r.key === selectedRootKey.value)
    return selected ? selected.path : null
})

// Reset selection when the available roots change (e.g. new session)
watch(availableGitRoots, (roots) => {
    if (!roots.length) {
        selectedRootKey.value = null
        return
    }
    // Keep current selection if still valid
    if (selectedRootKey.value && roots.find(r => r.key === selectedRootKey.value)) return
    // Default to first
    selectedRootKey.value = roots[0].key
}, { immediate: true })

function handleRootSelect(key) {
    if (key !== selectedRootKey.value && !missingRoots.value.has(key)) {
        selectedRootKey.value = key
        selectedCommit.value = null
        clearSelectedFile()
        clearTransientFileNotice()
        emitNavigate({ rootKey: key, commitRef: 'index' })
    }
}

/**
 * Handler for the overlay header git directory dropdown's wa-select event.
 * Extracts the selected item's value and delegates to handleRootSelect.
 */
function onGitDirDropdownSelect(event) {
    const value = event.detail?.item?.value
    if (value) {
        handleRootSelect(value)
    }
}

// ─── Mobile breakpoint detection ─────────────────────────────────────────────
// Container query on the panel's OWN rendered width (ResizeObserver on its root, no selector),
// not a viewport media query — so it reacts to the width it is actually given: its dock region
// when docked, the center slot otherwise, or the surrounding content area outside the layout.

const { isBelowBreakpoint: isMobile } = useContainerBreakpoint({
    breakpoint: 800,
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

/**
 * Query string fragment for the git_dir parameter.
 * Returns '&git_dir=...' or '?git_dir=...' ready to append to URLs,
 * but only when a non-default root is selected (to avoid unnecessary params).
 * Use appendGitDir(url) helper instead of using this directly.
 */
function appendGitDir(url) {
    const dir = effectiveGitDirectory.value
    if (!dir) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}git_dir=${encodeURIComponent(dir)}`
}

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

/** Counts from index — passed to GitLog's indexStatus prop, and emitted for the Git tab's label. */
const indexStatus = computed(() => indexFilesData.value?.stats ?? null)
watch(indexStatus, (stats) => emit('index-status', stats), { immediate: true })

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
const headerSelectedCommit = computed(() => {
    const issue = routeCommitIssue.value
    if (issue) {
        return {
            message: `${issue.before ?? ''}${issue.detail ?? ''}${issue.after ?? ''}`,
        }
    }
    return selectedCommit.value
})
const showGitMainContent = computed(() => !routeRootIssue.value && !routeCommitIssue.value)

/** Stats for the header: commit-specific when a commit is selected, index otherwise. */
const headerStats = computed(() => {
    if (routeRootIssue.value || routeCommitIssue.value) return null
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
    clearTransientFileNotice()
    refreshGitLog()
}

function onCommitSelected(commit) {
    selectedCommit.value = commit || null
    clearSelectedFile()
    clearTransientFileNotice()
    if (commit) {
        gitLogOpen.value = false
    }
    if (!syncingFromRoute) {
        emitNavigate({
            rootKey: selectedRootKey.value,
            commitRef: commit?.hash ?? 'index',
        })
    }
}

// ---------------------------------------------------------------------------
// Current files data (index or commit)
// ---------------------------------------------------------------------------

/** The current files data: index when viewing uncommitted, commit-specific otherwise. */
const currentFilesData = computed(() => {
    if (routeRootIssue.value || routeCommitIssue.value) return null
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
    if (routeRootIssue.value || routeCommitIssue.value) return false
    return !selectedCommit.value || selectedCommit.value.hash === 'index'
})

/** Set of paths with code comments, scoped to the current commit/index view.
 *  Paths are remapped from absolute (as stored in comments) to tree-relative
 *  (as used by FileTree), replacing the git directory prefix with the tree root name. */
const commentedPaths = computed(() => {
    if (!props.projectId || !props.sessionId) return new Set()
    const sourceRef = isViewingIndex.value ? '' : (selectedCommit.value?.hash ?? '')
    const gitDir = effectiveGitDirectory.value
    const treeRoot = displayTree.value?.name
    if (!gitDir || !treeRoot) return new Set()
    const comments = codeCommentsStore.getCommentsBySession(props.projectId, props.sessionId)
        .filter(c => c.source === 'git' && c.sourceRef === sourceRef)
    // Remap: /absolute/git/dir/file.py → TreeRootName/file.py
    const treePaths = comments
        .map(c => c.filePath.startsWith(gitDir + '/') ? treeRoot + c.filePath.slice(gitDir.length) : null)
        .filter(Boolean)
    return buildCommentedPathsSet(treePaths)
})

/** Provide a decoration checker for the git log commit list.
 *  CommitMessageData injects this to show a comment icon per commit. */
provide('gitCommitHasDecoration', (hash) => {
    if (!props.projectId || !props.sessionId) return false
    const sourceRef = hash === 'index' ? '' : hash
    return codeCommentsStore.countBySourceRef(props.projectId, props.sessionId, 'git', sourceRef) > 0
})

// ---------------------------------------------------------------------------
// Fetch commit files when a commit is selected
// ---------------------------------------------------------------------------

watch(selectedCommit, async (commit) => {
    // Reset data
    commitFilesData.value = null

    if (routeCommitIssue.value || routeRootIssue.value) {
        return
    }

    if (!commit || commit.hash === 'index') {
        // Re-fetch index files silently (they may have changed)
        await refreshIndexFiles()
        return
    }

    commitFilesLoading.value = true
    try {
        const url = appendGitDir(`${apiPrefix.value}/git-commit-files/${commit.hash}/`)
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
// Commit detail (for popover)
// ---------------------------------------------------------------------------

async function fetchCommitDetail(commitHash) {
    const url = appendGitDir(`${apiPrefix.value}/git-commit-detail/${commitHash}/`)
    const res = await apiFetch(url)
    if (!res.ok) return null
    return await res.json()
}

// ---------------------------------------------------------------------------
// File tree panel integration
// ---------------------------------------------------------------------------

const fileTreePanelRef = ref(null)
const filePaneRef = ref(null)

/** Selected file relative path from the FileTreePanel. */
const selectedFile = computed(() => fileTreePanelRef.value?.selectedFile ?? null)

// On a tab-activation focus request from the layout (focusRequest bumped by SessionView), focus the
// panel's primary content: the diff viewer when a file's diff is shown (so keyboard nav keeps reading
// it), otherwise the tree's search filter. The sub-panel mounts only after the git log loads (~2s on a
// cold first activation), so this defers the request until the host ref appears — see usePanelContentFocus.
usePanelContentFocus({ focusRequest: () => props.focusRequest, selectedFile, filePaneRef, fileTreePanelRef })

/** The tree node for the currently selected file (or null) — drives its status flag. */
const selectedFileNode = computed(() => {
    const file = selectedFile.value
    const tree = displayTree.value
    if (!file || !tree) return null

    let node = tree
    for (const part of file.split('/')) {
        node = node.children?.find(c => c.name === part)
        if (!node) return null
    }
    return node.type === 'file' ? node : null
})

/**
 * Search callback: filters the current tree client-side.
 * Uses the same fuzzy/exact search logic as the backend.
 */
function handleOptionsSelect(value) {
    if (value === 'show-untracked') {
        showUntracked.value = !showUntracked.value
    } else if (value?.startsWith('root:')) {
        handleRootSelect(value.slice(5))
    }
}

const contextMenuMode = computed(() => {
    if (isViewingIndex.value) return 'git-index'
    return 'git-commit'
})

/**
 * Download a file from the Git tab — its content at the viewed revision
 * (``kind`` = ``file``) or the patch that revision carries for it (``diff``).
 *
 * The endpoints resolve the bytes themselves: at the index they read the
 * working tree (falling back to HEAD for a file deleted from disk), at a commit
 * they read the blob (falling back to the parent for a file that commit
 * deleted). The panel only supplies the revision it is showing.
 */
function handleDownload(kind, { path }) {
    const endpoint = kind === 'diff' ? 'git-diff-download' : 'git-file-download'
    const params = new URLSearchParams({
        path,
        ref: isViewingIndex.value ? 'index' : (selectedCommit.value?.hash ?? 'index'),
    })
    if (effectiveGitDirectory.value) {
        params.set('git_dir', effectiveGitDirectory.value)
    }
    triggerDownload(`${apiPrefix.value}/${endpoint}/?${params}`)
}

async function handleGitAction(action, { path }) {
    const endpoint = {
        'git-stage': 'git-stage',
        'git-unstage': 'git-unstage',
        'git-discard': 'git-discard',
    }[action]
    if (!endpoint) return

    try {
        const url = `${apiPrefix.value}/${endpoint}/`
        const body = { path }
        if (effectiveGitDirectory.value) {
            body.git_dir = effectiveGitDirectory.value
        }
        const res = await apiFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        if (!res.ok) {
            const data = await res.json().catch(() => ({}))
            console.error(`Git ${action} failed:`, data.error || res.status)
        }
    } catch (e) {
        console.error(`Git ${action} error:`, e)
    }

    await refreshIndexFiles()
}

function doSearch(query) {
    const tree = displayTree.value
    if (!tree) return null
    return searchTreeFiles(tree, query)
}

function handleFileSelect(path) {
    if (!props.active || syncingFromRoute) return
    clearTransientFileNotice()
    emitNavigate({
        rootKey: selectedRootKey.value,
        commitRef: selectedCommit.value?.hash ?? 'index',
        filePath: path || undefined,
    })
}

/**
 * Check whether a relative file path exists in a tree.
 * The targetPath should NOT include the root node name (matches the format
 * of selectedFile which has the rootPath stripped by FileTreePanel).
 */
function fileExistsInTree(node, targetPath) {
    if (!node) return false
    // Start searching from the root's children, skipping the root name itself
    function search(n, parentPath) {
        const currentPath = parentPath ? `${parentPath}/${n.name}` : n.name
        if (n.type === 'file' && currentPath === targetPath) return true
        if (n.children) {
            for (const child of n.children) {
                if (search(child, currentPath)) return true
            }
        }
        return false
    }
    // Search children of the root node (root name = rootPath, stripped from selectedFile)
    if (node.children) {
        for (const child of node.children) {
            if (search(child, '')) return true
        }
    }
    return false
}

// Re-run search and clear stale file selection when the tree data changes.
// When a file the user was viewing disappears (a commit/refresh dropped it),
// flash a self-dismissing notice. The route-owner path is handled by the route
// watcher below (which can also drop the dead filePath from the URL); here we
// only cover the docked, non-owner path, whose route props are blanked.
watch(displayTree, (tree) => {
    fileTreePanelRef.value?.clearSearch()

    if (!tree) {
        // Tree emptied (e.g. every uncommitted change was just committed).
        if (selectedFile.value && !props.routeOwner) showTransientFileGone(selectedFile.value)
        clearSelectedFile()
        diffData.value = null
        return
    }

    if (selectedFile.value && !fileExistsInTree(tree, selectedFile.value)) {
        if (!props.routeOwner) showTransientFileGone(selectedFile.value)
        clearSelectedFile()
    }
})

/**
 * Fetch index files from the dedicated endpoint and store them in indexFilesData.
 * Returns whether the stored data changed. Throws on network errors.
 */
async function fetchIndexFiles() {
    const url = appendGitDir(`${apiPrefix.value}/git-index-files/`)
    const res = await apiFetch(url)
    if (!res.ok) return false
    const newData = (await res.json()) || null
    // The selected root may have changed while the request was in flight.
    if (url !== appendGitDir(`${apiPrefix.value}/git-index-files/`)) return false
    // Only update the ref if the data actually changed — avoids
    // unnecessary re-renders of the file tree when nothing changed.
    if (JSON.stringify(newData) === JSON.stringify(indexFilesData.value)) return false
    indexFilesData.value = newData
    return true
}

/**
 * Refresh index files (tree + stats) and the open file's diff.
 * Lightweight alternative to re-fetching the entire git log.
 */
async function refreshIndexFiles({ silent = false } = {}) {
    // Only show loading state if we have no data yet — avoids flashing
    // the tree empty when refreshing with existing content visible.
    // Background polling passes silent:true so the empty "No changes" state
    // never flashes a spinner on each tick (the loading placeholder otherwise
    // takes precedence over the "No changes" one).
    const isInitialLoad = !silent && !indexFilesData.value
    if (isInitialLoad) commitFilesLoading.value = true
    let treeChanged = false
    try {
        treeChanged = await fetchIndexFiles()
    } catch {
        // Silently ignore — index data just stays stale
    } finally {
        if (isInitialLoad) commitFilesLoading.value = false
    }

    // Re-fetch the diff for the currently selected file (if any)
    if (selectedFile.value) {
        fetchDiff(selectedFile.value)
        // Scroll to the selected file so it's visible after tree refresh
        if (treeChanged) {
            await nextTick()
            if (selectedFilePath.value) {
                fileTreePanelRef.value?.scrollToPath(selectedFilePath.value)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Diff data (fetched when a file is selected)
// ---------------------------------------------------------------------------

const diffData = ref(null)        // { original, modified, binary, image, error }
const diffImageRef = ref(null)
const { reset: resetDiffImageZoom } = usePanZoom(diffImageRef)
const imageDiffContainerRef = ref(null)
const diffBeforeImageRef = ref(null)
const diffAfterImageRef = ref(null)
const { reset: resetComparisonZoom } = useSyncedPanZoom(imageDiffContainerRef, diffBeforeImageRef, diffAfterImageRef)
const diffLoading = ref(false)
const diffLoadingVisible = ref(false)  // delayed: only true after 500ms of diffLoading
let _diffLoadingTimer = null
const diffError = ref(null)

watch(diffLoading, (loading) => {
    if (loading) {
        _diffLoadingTimer = setTimeout(() => { diffLoadingVisible.value = true }, 500)
    } else {
        clearTimeout(_diffLoadingTimer)
        diffLoadingVisible.value = false
    }
})

/** Absolute file path for the selected file (needed by FilePane for language detection and save). */
const selectedFilePath = computed(() => {
    if (!selectedFile.value) return null
    if (effectiveGitDirectory.value) {
        return `${effectiveGitDirectory.value}/${selectedFile.value}`
    }
    return selectedFile.value
})

/** Fetch diff data for a file. */
async function fetchDiff(file) {
    if (!file) {
        diffData.value = null
        diffLoading.value = false
        diffError.value = null
        return
    }

    // Set loading flag but do NOT clear diffData — the editor stays visible
    // with its current content while the new diff loads in the background.
    diffLoading.value = true
    diffError.value = null

    try {
        let url
        if (isViewingIndex.value) {
            url = appendGitDir(`${apiPrefix.value}/git-index-file-diff/?path=${encodeURIComponent(file)}`)
        } else {
            url = appendGitDir(`${apiPrefix.value}/git-commit-file-diff/${selectedCommit.value.hash}/?path=${encodeURIComponent(file)}`)
        }

        const res = await apiFetch(url)
        const data = await res.json()

        if (!res.ok || data.error) {
            diffError.value = data.error || 'Failed to load diff'
            diffData.value = null
            return
        }

        // Only update if the diff content actually changed — avoids
        // unnecessary MergeView destroy/recreate when nothing changed.
        if (data.original !== diffData.value?.original || data.modified !== diffData.value?.modified || data.binary !== diffData.value?.binary || data.image !== diffData.value?.image) {
            diffData.value = data
        }
    } catch {
        diffError.value = 'Network error'
        diffData.value = null
    } finally {
        diffLoading.value = false
    }
}

// Fetch diff when a file is selected
watch(selectedFile, (file) => {
    resetDiffImageZoom()
    resetComparisonZoom()
    fetchDiff(file)
})

// ---------------------------------------------------------------------------
// Color scheme — follow app-wide effective color scheme
// ---------------------------------------------------------------------------

const colorScheme = computed(() => settingsStore.getEffectiveColorScheme)

// Use a palette that matches the current color scheme
const colours = computed(() =>
    colorScheme.value === 'dark' ? 'neon-aurora-dark' : 'neon-aurora-light'
)

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const apiUrl = computed(() => {
    const base = `${apiPrefix.value}/git-log/`
    if (selectedBranch.value) {
        return appendGitDir(`${base}?branch=${encodeURIComponent(selectedBranch.value)}`)
    }
    return appendGitDir(base)
})

async function fetchGitLog() {
    loading.value = true
    error.value = null

    // First fetch is always unfiltered (--all) so we get the full branch list
    // and current branch. We'll select the right branch and re-fetch if needed.
    const base = appendGitDir(`${apiPrefix.value}/git-log/`)

    try {
        const res = await apiFetch(base)

        if (!res.ok) {
            const data = await res.json().catch(() => ({}))

            // If the directory was not found, mark this root as missing and
            // surface it as a route issue without switching to another root.
            if (res.status === 404 && selectedRootKey.value) {
                missingRoots.value = new Set([...missingRoots.value, selectedRootKey.value])
                routeRootIssue.value = makeRouteIssue(
                    'Git root ',
                    props.routeRootKey || selectedRootKey.value,
                    ' is no longer available.',
                )
                entries.value = []
                currentBranch.value = ''
                headCommitHash.value = ''
                hasMore.value = false
                branches.value = []
                selectedBranch.value = ''
                indexFilesData.value = null
                commitFilesData.value = null
                diffData.value = null
                diffError.value = null
                return
            }

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
 * Refresh the git log (overlay header button, branch change, pane re-activation,
 * and the background commit-list poll). Unlike the initial fetchGitLog, this sets
 * `refreshing` instead of `loading` so the overlay stays visible with its current
 * content while refreshing.
 *
 * `silent` (background poll) skips the `refreshing` spinner. In all modes we only
 * replace `entries`/`branches`/`index_files` when their value actually changed, so
 * a periodic poll never re-renders the commit graph — nor shifts the open
 * selector's rows — unless a real change landed (primitives are auto-guarded by
 * Vue's ref equality check). The `gitLogInFlight` flag is shared with the manual
 * refresh so the two never run concurrently or double-fetch.
 */
let gitLogInFlight = false
async function refreshGitLog({ silent = false } = {}) {
    if (gitLogInFlight) return
    gitLogInFlight = true
    if (!silent) refreshing.value = true

    try {
        const res = await apiFetch(apiUrl.value)

        if (!res.ok) {
            return
        }

        const data = await res.json()
        const newEntries = data.entries || []
        if (JSON.stringify(newEntries) !== JSON.stringify(entries.value)) {
            entries.value = newEntries
        }
        const newBranches = data.branches || []
        if (JSON.stringify(newBranches) !== JSON.stringify(branches.value)) {
            branches.value = newBranches
        }
        currentBranch.value = data.current_branch || ''
        headCommitHash.value = data.head_commit_hash || ''
        const newIndexFiles = data.index_files || null
        if (JSON.stringify(newIndexFiles) !== JSON.stringify(indexFilesData.value)) {
            indexFilesData.value = newIndexFiles
        }
        hasMore.value = data.has_more || false
    } catch {
        // Silently ignore — existing data stays visible
    } finally {
        if (!silent) refreshing.value = false
        gitLogInFlight = false
    }
}

watch(
    () => [props.active, props.routeRootKey, availableGitRoots.value.map(root => root.key).join('|'), props.routeOwner],
    ([active, routeRootKey]) => {
        if (!active || !props.routeOwner) return
        const roots = availableGitRoots.value
        if (!roots.length) return

        if (!routeRootKey) {
            routeRootIssue.value = null
            const defaultRoot = roots.find(root => !missingRoots.value.has(root.key)) || roots[0]
            syncingFromRoute = true
            selectedRootKey.value = defaultRoot?.key ?? null
            selectedCommit.value = null
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
            return
        }

        const requestedRoot = roots.find(root => root.key === routeRootKey)
        if (!requestedRoot) {
            routeRootIssue.value = makeRouteIssue('Git root ', routeRootKey, ' is not available.')
            entries.value = []
            currentBranch.value = ''
            headCommitHash.value = ''
            hasMore.value = false
            branches.value = []
            selectedBranch.value = ''
            indexFilesData.value = null
            commitFilesData.value = null
            diffData.value = null
            diffError.value = null
            if (selectedRootKey.value !== null) {
                syncingFromRoute = true
                selectedRootKey.value = null
                selectedCommit.value = null
                clearSelectedFile()
                nextTick(() => {
                    syncingFromRoute = false
                })
            }
            return
        }

        if (missingRoots.value.has(routeRootKey)) {
            routeRootIssue.value = makeRouteIssue('Git root ', routeRootKey, ' is no longer available.')
            entries.value = []
            currentBranch.value = ''
            headCommitHash.value = ''
            hasMore.value = false
            branches.value = []
            selectedBranch.value = ''
            indexFilesData.value = null
            commitFilesData.value = null
            diffData.value = null
            diffError.value = null
            if (selectedRootKey.value !== routeRootKey) {
                syncingFromRoute = true
                selectedRootKey.value = routeRootKey
                selectedCommit.value = null
                clearSelectedFile()
                nextTick(() => {
                    syncingFromRoute = false
                })
            }
            return
        }

        routeRootIssue.value = null

        if (selectedRootKey.value !== requestedRoot.key) {
            syncingFromRoute = true
            selectedRootKey.value = requestedRoot.key
            selectedCommit.value = null
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
        }
    },
    { immediate: true },
)

watch(
    () => [props.active, started.value, loading.value, refreshing.value, selectedRootKey.value, props.routeCommitRef, entries.value.length, props.routeOwner],
    ([active, started, isLoading, isRefreshing]) => {
        if (!active || !props.routeOwner || !started || isLoading || isRefreshing || !selectedRootKey.value || routeRootIssue.value) return

        const targetCommitRef = props.routeCommitRef ?? 'index'

        if (props.routeCommitRef == null) {
            routeCommitIssue.value = null
            syncingFromRoute = true
            selectedCommit.value = null
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
            return
        }

        if (targetCommitRef === 'index') {
            routeCommitIssue.value = null
            if (selectedCommit.value?.hash !== 'index' && selectedCommit.value !== null) {
                syncingFromRoute = true
                selectedCommit.value = null
                clearSelectedFile()
                nextTick(() => {
                    syncingFromRoute = false
                })
            }
            return
        }

        const matchingCommit = entries.value.find(entry => entry.hash === targetCommitRef)
        if (!matchingCommit) {
            routeCommitIssue.value = makeRouteIssue('Commit ', targetCommitRef, ' is no longer available.')
            syncingFromRoute = true
            selectedCommit.value = null
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
            return
        }

        routeCommitIssue.value = null

        if (selectedCommit.value?.hash !== matchingCommit.hash) {
            syncingFromRoute = true
            selectedCommit.value = matchingCommit
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
        }
    },
    { immediate: true },
)

watch(
    () => [props.active, displayTree.value, props.routeFilePath, selectedRootKey.value, selectedCommit.value?.hash ?? 'index', props.routeOwner],
    async ([active, tree, routeFilePath, rootKey, commitHash], [, oldTree] = []) => {
        if (!active || !props.routeOwner || !rootKey) return

        if (routeFilePath == null) {
            if (selectedFile.value) {
                syncingFromRoute = true
                clearSelectedFile()
                await nextTick()
                syncingFromRoute = false
            }
            if (routeFilePath === null) {
                routeFileIssue.value = makeRouteIssue('Requested file path is invalid.')
            } else {
                lastVanishedFilePath = null
                routeFileIssue.value = null
            }
            return
        }

        // The route points at a file absent from the current view (the tree may
        // also be empty now, e.g. everything was just committed). Tell apart a
        // file that vanished while on screen (case B — present in the previous
        // tree, dropped by a commit/refresh) from a dead deep-link the user never
        // saw (case A — absent all along).
        if (!tree || !fileExistsInTree(tree, routeFilePath)) {
            if (selectedFile.value) {
                syncingFromRoute = true
                clearSelectedFile()
                await nextTick()
                syncingFromRoute = false
            }
            if (oldTree && fileExistsInTree(oldTree, routeFilePath)) {
                // Case B: the file vanished while on screen (present in the previous
                // tree, dropped by a commit/refresh). Flash a self-dismissing notice,
                // then drop the now-dead filePath from the route so a later refresh
                // can't requalify it as case A and pin a permanent callout.
                showTransientFileGone(routeFilePath)
                routeFileIssue.value = null
                emitNavigate({ filePath: undefined, replace: true })
            } else if (routeFilePath === lastVanishedFilePath) {
                // Case B, deferred: the file vanished live while the Git tab was hidden,
                // so the docked path flashed the transient but couldn't scrub the route
                // (non-owners don't navigate). Now that we own it, finish the job — drop
                // the dead filePath — instead of misreading it as a never-seen dead
                // deep-link. The transient, if still pending, is armed by the visibility
                // watcher; if it was already shown and dismissed, nothing re-appears.
                lastVanishedFilePath = null
                routeFileIssue.value = null
                emitNavigate({ filePath: undefined, replace: true })
            } else {
                // Case A: a dead deep-link to a file never shown — persistent, it
                // explains why nothing is selected.
                routeFileIssue.value = makeRouteIssue('File ', routeFilePath, ' is no longer available in this view.')
            }
            return
        }

        lastVanishedFilePath = null
        routeFileIssue.value = null
        clearTransientFileNotice()

        if (selectedFile.value === routeFilePath) return

        const rootPath = tree.name
        const fullPath = rootPath ? `${rootPath}/${routeFilePath}` : routeFilePath
        syncingFromRoute = true
        await fileTreePanelRef.value?.scrollToPath(fullPath)
        fileTreePanelRef.value?.onFileSelect(fullPath)
        await nextTick()
        syncingFromRoute = false
    },
    { immediate: true },
)

// ---------------------------------------------------------------------------
// Re-fetch when git root changes
// ---------------------------------------------------------------------------

watch(effectiveGitDirectory, (newDir, oldDir) => {
    if (!newDir || newDir === oldDir) return
    if (!started.value) {
        // Never activated: only the tab label's counts exist, reload them for the new root.
        indexFilesData.value = null
        refreshLabelStats()
        return
    }

    // Full reset of all git state
    entries.value = []
    currentBranch.value = ''
    headCommitHash.value = ''
    selectedBranch.value = ''
    selectedCommit.value = null
    indexFilesData.value = null
    commitFilesData.value = null
    diffData.value = null
    diffError.value = null
    branches.value = []
    hasMore.value = false
    filterText.value = ''

    // Re-fetch everything from scratch
    fetchGitLog()
})

// ---------------------------------------------------------------------------
// Lazy init + auto-refresh when the tab becomes active
// ---------------------------------------------------------------------------

watch(
    () => props.active,
    async (active) => {
        if (!active) return

        if (!started.value) {
            // First activation: full initial fetch
            started.value = true
            fetchGitLog()
            return
        }

        // Subsequent activations refresh data; route watchers restore the
        // canonical commit/file selection when needed.
        await refreshGitLog()

        // If a commit was selected, check it still exists in the new data
        if (selectedCommit.value && selectedCommit.value.hash !== 'index') {
            const stillExists = entries.value.some(
                (e) => e.hash === selectedCommit.value.hash,
            )
            if (!stillExists) {
                selectedCommit.value = null
            }
        }

        // If viewing uncommitted changes, refresh the file tree & diff
        if ((!selectedCommit.value || selectedCommit.value.hash === 'index') && !routeCommitIssue.value) {
            await refreshIndexFiles()
        }
    },
    { immediate: true },
)

// ---------------------------------------------------------------------------
// Live polling while the Git pane is visible
// ---------------------------------------------------------------------------
// While the Git pane is visible (props.active), we're viewing the index
// (uncommitted changes), AND an assistant is working on the project (see
// shouldPoll), poll in the background so the data stays live without the user
// pressing Refresh. Two loops share that gate:
//   - every 5s:  the change tree & the open file's diff (refreshIndexFiles) —
//                exactly what the "Refresh" options-menu item triggers.
//   - every 30s: the commit list / selector & the header HEAD/branch
//                (refreshGitLog) — so a commit landed in the background appears.
// Each tick runs silently. We never poll a historical commit (immutable) and
// pause while the browser tab is hidden. Because props.active is only ever true
// for the one visible session/project pane, at most one set of loops runs.
// Edges the loop can't cover (tab refocus, assistant-turn end) get a one-shot
// catchUpRefresh instead.

// Only poll while an assistant is actively working on the git directory's
// project — when nothing is running the working tree / commits can't change, so
// polling would just burn requests. The git root in view picks the project: the
// 'parent' root (a worktree showing its main repo) targets the parent project,
// every other root targets this pane's own project/worktree.
const targetProjectId = computed(() => {
    if (selectedRootKey.value === 'parent') {
        return getWorktreeParent(dataStore.getProject(props.projectId), dataStore)?.id ?? null
    }
    return props.projectId ?? null
})

/** True while at least one session on the target project is in its assistant turn
 *  (our own session counts — if it's the one working, git may change). */
const hasActiveAssistant = computed(() => {
    const pid = targetProjectId.value
    if (!pid) return false
    const sessions = dataStore.sessions
    for (const [id, process] of Object.entries(dataStore.processStates)) {
        if (process?.state === PROCESS_STATE.ASSISTANT_TURN && sessions[id]?.project_id === pid) {
            return true
        }
    }
    return false
})

/** True while a background poll is warranted (visible pane, viewing the index, an
 *  assistant working on the project). */
const shouldPoll = computed(() => props.active && isViewingIndex.value && hasActiveAssistant.value)

/** Also gate on browser-tab visibility — no point polling a hidden tab. */
function pollActive() {
    return shouldPoll.value && !document.hidden
}

/**
 * One-shot silent refresh of both the index and the commit list, when the pane is
 * actually being looked at. Covers the edges the recurring poll doesn't: returning
 * to the tab, and an assistant turn ending (the loop stops, but its final commit /
 * edits should still appear).
 */
function catchUpRefresh() {
    if (!props.active || !isViewingIndex.value || document.hidden) return
    refreshIndexFiles({ silent: true })
    refreshGitLog({ silent: true })
}

/**
 * Build a self-rescheduling poll loop. The next tick is scheduled `intervalMs`
 * after the previous one *completes*, so a slow repo self-throttles and ticks
 * never overlap. One-shot catch-up refreshes (tab refocus, assistant-turn end)
 * are handled by catchUpRefresh, not the loop.
 */
function createPollLoop({ intervalMs, run, isActive = pollActive }) {
    let timer = null
    let inFlight = false

    function cancel() {
        if (timer !== null) {
            clearTimeout(timer)
            timer = null
        }
    }

    function arm() {
        cancel()
        if (!isActive() || inFlight) return
        timer = setTimeout(tick, intervalMs)
    }

    async function tick() {
        timer = null
        if (!isActive()) return
        inFlight = true
        try {
            await run()
        } finally {
            inFlight = false
        }
        arm()
    }

    return { arm, cancel }
}

const indexPoll = createPollLoop({
    intervalMs: 5000,
    run: () => refreshIndexFiles({ silent: true }),
})
const gitLogPoll = createPollLoop({
    intervalMs: 30000,
    run: () => refreshGitLog({ silent: true }),
})

// ---------------------------------------------------------------------------
// Uncommitted-changes counts for the Git tab's label
// ---------------------------------------------------------------------------
// The session view shows indexStatus next to the Git tab's label, also while this
// pane is hidden (another tab active, dock minimized) or was never opened. The
// pane's own loading is lazy and its polls only run while it is visible, so the
// label gets a lighter refresh of its own — the index only (git-index-files), never
// the diff or the commit list: once when the session is displayed, when the
// browser tab comes back, when an assistant working on the project ends its turn,
// and every 30s while one works. It stands down whenever the pane's own 5s index
// poll covers it, and never runs for a session kept alive in the background.

/** True when the pane's own index refresh does not already keep the counts current. */
const labelStatsUncovered = computed(() => !(props.active && isViewingIndex.value))

function labelStatsRefreshAllowed() {
    return props.sessionActive && !document.hidden && !routeRootIssue.value && !!effectiveGitDirectory.value
}

/** One-shot silent refresh of the label's counts. */
function refreshLabelStats() {
    if (!labelStatsRefreshAllowed()) return
    fetchIndexFiles().catch(() => {
        // Silently ignore — the label just keeps its previous counts
    })
}

const labelStatsPoll = createPollLoop({
    intervalMs: 30000,
    run: () => fetchIndexFiles().catch(() => {}),
    isActive: () => labelStatsRefreshAllowed() && labelStatsUncovered.value && hasActiveAssistant.value,
})

watch(
    () => [props.sessionActive, labelStatsUncovered.value, hasActiveAssistant.value, !!routeRootIssue.value],
    () => labelStatsPoll.arm(),
    { immediate: true },
)

// Displaying the session: load or catch up the label's counts, unless the pane
// is shown too — its activation watch above already refreshes the index then.
watch(() => props.sessionActive, (sessionActive) => {
    if (sessionActive && !props.active) refreshLabelStats()
}, { immediate: true })

function onPollVisibilityChange() {
    if (document.hidden) {
        indexPoll.cancel()
        gitLogPoll.cancel()
        labelStatsPoll.cancel()
        return
    }
    // Returning to the tab: catch up once on whatever changed while it was hidden
    // (e.g. an assistant that ran and finished meanwhile), regardless of whether
    // one is running now — a single fetch on a deliberate return, not polling.
    // Then resume the recurring loops only if still warranted.
    catchUpRefresh()
    if (labelStatsUncovered.value) refreshLabelStats()
    indexPoll.arm()
    gitLogPoll.arm()
    labelStatsPoll.arm()
}

// When the working assistant stops, the recurring loop stops too — do one last
// catch-up so its final commit / edits appear without waiting for the next turn.
watch(hasActiveAssistant, (now, was) => {
    if (!was || now) return
    catchUpRefresh()
    if (labelStatsUncovered.value) refreshLabelStats()
})

document.addEventListener('visibilitychange', onPollVisibilityChange)

// Start/stop the loops as the pane's visibility or index/commit view changes.
// No immediate tick here: when the pane becomes active the activation watch
// above already issues a fresh refreshIndexFiles()/refreshGitLog(), and switching
// back from a commit to the index re-triggers the index refresh via the
// selectedCommit watch — so we only schedule the *next* tick and avoid a double
// fetch.
watch(shouldPoll, () => {
    indexPoll.arm()
    gitLogPoll.arm()
}, { immediate: true })

onUnmounted(() => {
    indexPoll.cancel()
    gitLogPoll.cancel()
    labelStatsPoll.cancel()
    document.removeEventListener('visibilitychange', onPollVisibilityChange)
    clearTransientFileNotice()
})

// ---------------------------------------------------------------------------
// Split panel position (KeepAlive-safe)
// ---------------------------------------------------------------------------

const TREE_DEFAULT_WIDTH = 250
const treePanelWidth = ref(TREE_DEFAULT_WIDTH)
const splitPanelRef = ref(null)
// Neutralize pooled-iframe pointer-events while this split's divider is dragged.
// Pooled frames are absolutely positioned over the whole content area, so a
// frame from another pane (e.g. a Browser tab docked beside Git) can sit under
// this divider's path and would otherwise capture the drag.
useSplitDividerDragFlag(splitPanelRef)

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

// The owners and layout containers live inside a v-else-if that is only
// rendered once git data has loaded. Watch the owner ref so reparenting
// runs as soon as the conditional block appears in the DOM.
watch(treeOwnerRef, (el) => {
    // When the v-if/v-else chain toggles (e.g. loading → content), the owner div
    // is destroyed and recreated. The old treeNode/contentNode references become
    // stale — they point to DOM elements from a destroyed Vue component instance.
    // Without this reset, reparentNodes would skip grabbing the new elements
    // (because treeNode is already set) and leave the old dead DOM visible
    // while the new working component stays hidden in the owner div.
    if (treeNode && treeNode.parentElement) {
        treeNode.remove()
    }
    if (contentNode && contentNode.parentElement) {
        contentNode.remove()
    }
    treeNode = null
    contentNode = null
    if (el) nextTick(() => reparentNodes(isMobile.value))
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
            <wa-callout variant="danger" appearance="filled-outlined" class="pane-callout">
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
        <div v-else-if="routeIssueMessage && entries.length === 0" class="panel-state">
            <wa-callout variant="warning" appearance="filled-outlined" class="pane-callout">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                <span>{{ routeIssueMessage.before }}</span>
                <span v-if="routeIssueMessage.detail" class="pane-callout-detail">{{ routeIssueMessage.detail }}</span>
                <span>{{ routeIssueMessage.after }}</span>
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
                :selected-commit="headerSelectedCommit"
                :selected-branch="selectedBranch"
                :stats="headerStats"
                :stats-loading="commitFilesLoading"
                :git-log-open="gitLogOpen"
                :fetch-commit-detail="fetchCommitDetail"
                @toggle-git-log="toggleGitLog"
            />
            <wa-divider></wa-divider>

            <!-- Content area (position: relative so overlay can cover it) -->
            <div class="git-panel-content">
                <div v-if="routeIssueMessage && !gitLogOpen" class="pane-callout-overlay">
                    <wa-callout
                        variant="warning"
                        appearance="filled-outlined"
                        class="pane-callout"
                    >
                        <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                        <span>{{ routeIssueMessage.before }}</span>
                        <span v-if="routeIssueMessage.detail" class="pane-callout-detail">{{ routeIssueMessage.detail }}</span>
                        <span>{{ routeIssueMessage.after }}</span>
                    </wa-callout>
                </div>

                <!-- Main content — hidden when a blocking route issue (root or commit) is present -->
                <template v-if="showGitMainContent">
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
                        :is-mobile="isMobile"
                        :commented-paths="commentedPaths"
                        mode="git"
                        enable-context-menu
                        :context-menu-mode="contextMenuMode"
                        :git-directory="effectiveGitDirectory"
                        @file-select="handleFileSelect"
                        @refresh="refreshIndexFiles()"
                        @option-select="handleOptionsSelect"
                        @git-stage="handleGitAction('git-stage', $event)"
                        @git-unstage="handleGitAction('git-unstage', $event)"
                        @git-discard="handleGitAction('git-discard', $event)"
                        @download="handleDownload('file', $event)"
                        @download-diff="handleDownload('diff', $event)"
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
                            <wa-divider></wa-divider>
                            <wa-dropdown-item disabled class="dropdown-header">
                                Git directory:
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-for="root in availableGitRoots"
                                :key="root.key"
                                type="checkbox"
                                :value="'root:' + root.key"
                                :checked="selectedRootKey === root.key"
                                :data-root-selected="selectedRootKey === root.key ? 'true' : 'false'"
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
                    <div class="git-content-inner">
                        <!-- File path bar (desktop only) -->
                        <template v-if="!isMobile && selectedFile">
                            <div class="file-path-header">
                                <span class="file-path-label">{{ selectedFile }}</span>
                                <GitStatusBadge v-if="selectedFileNode" :node="selectedFileNode" class="path-header-badge" />
                            </div>
                            <wa-divider></wa-divider>
                        </template>

                        <!-- Content area (flex:1 so absolute-positioned children stay below the path header) -->
                        <div class="git-content-area">
                            <!-- Diff error -->
                            <div v-if="diffError" class="panel-placeholder">
                                <wa-callout variant="danger" size="small">
                                    {{ diffError }}
                                </wa-callout>
                            </div>

                            <!-- Binary image diff (both sides) Logically inverted for a more logical visual rendering for the user -->
                            <div v-else-if="diffData?.image && diffData.original && diffData.modified" ref="imageDiffContainerRef" class="image-diff-container">
                                <wa-comparison>
                                    <img ref="diffAfterImageRef" slot="before" :src="diffData.modified" :alt="selectedFile" style="width: 100%; height: 100%; object-fit: contain;" />
                                    <img ref="diffBeforeImageRef" slot="after" :src="diffData.original" :alt="selectedFile" style="width: 100%; height: 100%; object-fit: contain;" />
                                </wa-comparison>
                            </div>

                            <!-- Binary image (single side: added or deleted) -->
                            <div v-else-if="diffData?.image" class="image-preview-container">
                                <img
                                    ref="diffImageRef"
                                    :src="diffData.modified || diffData.original"
                                    :alt="selectedFile"
                                    class="image-preview"
                                />
                            </div>

                            <!-- Non-image binary file -->
                            <div v-else-if="diffData?.binary" class="panel-placeholder">
                                Binary file cannot be diffed
                            </div>

                            <!-- Diff viewer (CodeMirror diff editor via FilePane) -->
                            <!-- Kept mounted: content updates in-place via prop changes, no destroy/recreate -->
                            <FilePane
                                v-else-if="selectedFile && diffData"
                                ref="filePaneRef"
                                :project-id="projectId"
                                :session-id="sessionId"
                                :file-path="selectedFilePath"
                                :active="active"
                                diff-mode
                                :original-content="diffData.original"
                                :modified-content="diffData.modified"
                                :diff-read-only="!isViewingIndex"
                                :commit-sha="isViewingIndex ? null : selectedCommit?.hash ?? null"
                                @revert="fetchDiff(selectedFile)"
                            />

                            <!-- No file selected / no changes -->
                            <div v-else-if="!selectedFile" class="panel-placeholder">
                                {{ !displayTree ? 'No changes' : 'Select a file' }}
                            </div>

                            <!-- Loading overlay (shown on top of existing content) -->
                            <div v-if="diffLoadingVisible && selectedFile && diffData" class="diff-loading-overlay">
                                <wa-spinner></wa-spinner>
                            </div>

                            <!-- Initial loading (no content yet) -->
                            <div v-else-if="diffLoadingVisible && !diffData" class="panel-placeholder">
                                <wa-spinner></wa-spinner>
                            </div>
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
                </template>

                <!-- Git log overlay (absolute, shown when chevron is clicked) -->
                <div v-if="gitLogOpen" class="gitlog-overlay">
                    <!-- Overlay header -->
                    <div class="gitlog-overlay-header">
                        <!-- Git directory selector -->
                        <wa-dropdown class="git-dir-dropdown" @wa-select="onGitDirDropdownSelect">
                            <wa-button
                                slot="trigger"
                                :id="gitDirButtonId"
                                variant="neutral"
                                appearance="filled-outlined"
                                size="small"
                                caret
                            >
                                <wa-icon name="folder"></wa-icon>
                            </wa-button>
                            <wa-dropdown-item disabled class="dropdown-header">
                                <small>Git directory:</small>
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-for="root in availableGitRoots"
                                :key="root.key"
                                :value="root.key"
                                :disabled="missingRoots.has(root.key)"
                            >
                                <wa-icon slot="icon" name="check" :style="{ visibility: root.key === selectedRootKey ? 'visible' : 'hidden' }"></wa-icon>
                                {{ root.label }}
                                <div class="root-path">{{ root.path }}</div>
                                <div v-if="missingRoots.has(root.key)" class="root-missing">Directory no longer exists</div>
                            </wa-dropdown-item>
                        </wa-dropdown>
                        <AppTooltip :for="gitDirButtonId">Switch git directory</AppTooltip>

                        <wa-select
                            v-if="branches.length"
                            size="small"
                            class="branch-select"
                            :value.prop="selectedBranch"
                            @change="onBranchChange"
                        >
                            <wa-icon slot="start" name="code-branch"></wa-icon>
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
                            <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                        </wa-input>

                        <wa-button
                            :id="refreshButtonId"
                            class="refresh-button"
                            variant="neutral"
                            appearance="filled-outlined"
                            size="small"
                            :loading="refreshing"
                            @click="refreshGitLog()"
                        >
                            <wa-icon name="arrow-rotate-right"></wa-icon>
                        </wa-button>
                        <AppTooltip :for="refreshButtonId">Refresh git log</AppTooltip>
                    </div>

                    <GitLog
                        :entries="entries"
                        :current-branch="currentBranch"
                        :head-commit-hash="headCommitHash"
                        :index-status="indexStatus"
                        :color-scheme="colorScheme"
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
    max-width: min(40rem, 90dvh);
}

.pane-callout-overlay {
    position: absolute;
    inset: 0;
    z-index: 20;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-m);
    pointer-events: none;
}

.pane-callout {
    flex: 0 0 auto;
    width: auto;
    max-width: min(40rem, 100%);
    pointer-events: auto;
}

.pane-callout-detail {
    font-family: var(--wa-font-family-code);
}

.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.image-diff-container {
    position: absolute;
    inset: 0;
    overflow: hidden;

    wa-comparison {
        width: 100%;
        height: 100%;
    }
}

.image-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    display: flex;
    padding: var(--wa-space-m);
}

.image-preview {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    touch-action: none;
    margin: auto;
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
        --width: var(--divider-size);
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
        width: var(--divider-size);
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

.git-content-area {
    flex: 1;
    min-height: 0;
    position: relative;
}

.file-path-header {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    min-height: 1.5rem;
    flex-shrink: 0;
    background: var(--wa-color-surface-alt);

    & + wa-divider {
        flex-shrink: 0;
        --width: 4px;
        --spacing: 0;
    }
}

.file-path-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    /* Truncate the START of the path so the file name stays visible (rtl puts
       the ellipsis on the left; text-align: left keeps short paths normal). */
    direction: rtl;
    text-align: left;
}

.path-header-badge {
    flex-shrink: 0;
    margin-left: var(--wa-space-xs);
}

.diff-loading-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    xbackground: color-mix(in srgb, var(--wa-color-surface-default) 60%, transparent);
    z-index: 1;
    pointer-events: none;
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

/* Git directory dropdown */

.git-dir-dropdown {
    flex-shrink: 0;

    wa-button::part(base) {
        padding: var(--wa-space-3xs) var(--wa-space-2xs);
    }
}

.dropdown-header {
    font-weight: var(--wa-font-weight-semibold);
    opacity: 0.7;
}

.root-path {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
    margin-top: var(--wa-space-3xs);
    word-break: break-all;
}

.root-missing {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-fill-loud);
}

/* Force-sync the checkmark visual on root selector items via CSS ::part().
   The wa-dropdown-item's internal `checked` property (a Lit reactive property)
   can get desynced from Vue's reactive state: the web component's makeSelection()
   toggles `checked` directly, and Vue's VNode diffing may later skip re-setting
   it when old/new VNode values are identical. The `data-root-selected` HTML
   attribute is immune to this issue (nothing external modifies it), so we use
   it to override the shadow DOM's :state(checked)-based visibility.
   Only root items carry this attribute; other checkboxes are unaffected. */
wa-dropdown-item[data-root-selected="true"]::part(checkmark) {
    visibility: visible;
}
wa-dropdown-item[data-root-selected="false"]::part(checkmark) {
    visibility: hidden;
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

<style>
.image-diff-container wa-comparison::part(base),
.image-diff-container wa-comparison::part(before) {
    height: 100%;
}
</style>
