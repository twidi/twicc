<script setup>
import { ref, computed, watch, nextTick, onMounted, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import { useContainerBreakpoint } from '../../composables/useContainerBreakpoint'
import { usePanelContentFocus } from '../../composables/usePanelContentFocus'
import FileTreePanel from './FileTreePanel.vue'
import FilePane from './FilePane.vue'
import ArtifactBookmarkTree from '../artifacts/ArtifactBookmarkTree.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import { useCodeCommentsStore, buildCommentedPathsSet } from '../../stores/codeComments'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { deriveFileRoots, getWorktreeParent } from '../../utils/projectRoots'
import { useSplitDividerDragFlag } from '../../composables/useSplitDividerDragFlag'

const emit = defineEmits(['navigate'])

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
    sessionCwd: {
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
    apiPrefix: {
        type: String,
        default: null,
    },
    rootRestriction: {
        type: String,
        default: null,
    },
    externalRoots: {
        type: Array,
        default: null,
    },
    // Hide the "Root:" section of the options menu — for a single fixed root
    // (e.g. the Artifacts tab) the selector is pure noise.
    showRootSelector: {
        type: Boolean,
        default: true,
    },
    // Display label for the tree's root node. Defaults to the absolute path;
    // set e.g. "Artifacts" to show a friendly name instead (the real path is
    // still used for all API calls and path math).
    rootLabel: {
        type: String,
        default: null,
    },
    // Open markdown / SVG files directly in their rendered preview (the eye
    // toggle stays available). Used by the Artifacts tab.
    previewByDefault: {
        type: Boolean,
        default: false,
    },
    renderOnly: {
        type: Boolean,
        default: false,
    },
    artifactBookmarkSessionId: {
        type: String,
        default: null,
    },
    // Contextually visible bookmarks rendered as a second, virtual tree root.
    artifactBookmarks: {
        type: Array,
        default: () => [],
    },
    artifactBookmarkProjectId: {
        type: String,
        default: null,
    },
    artifactBookmarkMainProjectId: {
        type: String,
        default: null,
    },
    routeRootKey: {
        default: undefined,
    },
    routeFilePath: {
        default: undefined,
    },
    // Whether this panel currently owns the URL (it is the focused tab). When the
    // dockable layout shows several tool panels at once, only the focused one owns
    // the route; non-owners receive blanked route props (the URL params belong to
    // whoever owns it). Sync-from-route must run for the owner ONLY — a non-owner
    // would read the blank props as "no file selected" and wrongly clear the file
    // it has open. Defaults to true (non-docked: a panel is shown only when active,
    // so it always owns the route).
    routeOwner: {
        type: Boolean,
        default: true,
    },
    // Forwarded to FilePane: raise its pooled HTML-preview frame above the
    // docking overlay when this panel is shown there.
    frameElevated: {
        type: Boolean,
        default: false,
    },
})

// ─── Mobile breakpoint detection ─────────────────────────────────────────────
// Container query on the panel's OWN rendered width (ResizeObserver on its root, no selector),
// not a viewport media query — so it reacts to the width it is actually given: its dock region
// when docked, the center slot otherwise, or the surrounding content area outside the layout.

const { isBelowBreakpoint: isMobile } = useContainerBreakpoint({
    breakpoint: 800,
})

// ─── Code comments ───────────────────────────────────────────────────────────

const codeCommentsStore = useCodeCommentsStore()

const commentedPaths = computed(() => {
    if (!props.projectId || !props.sessionId) return new Set()
    const comments = codeCommentsStore.getCommentsBySession(props.projectId, props.sessionId)
        .filter(c => c.source === 'files' && c.sourceRef === '')
    return buildCommentedPathsSet(comments.map(c => c.filePath))
})

// API prefix: use explicit prop when provided, otherwise project-level for drafts, session-level otherwise
const resolvedApiPrefix = computed(() => {
    if (props.apiPrefix) return props.apiPrefix
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
const filePaneRef = ref(null)
let syncingFromRoute = false

// ─── Root directory selection ────────────────────────────────────────────────

const dataStore = useDataStore()

/**
 * Available root directories for the Files tab. Each entry: { key, label, path, roles },
 * plus an optional `projectId` (workspace mode) that shows the project badge in
 * place of the label.
 *
 * Built from the session/project roots plus — when the project is a git
 * worktree — the main repository's directories (see utils/projectRoots.js).
 * In workspace / all-projects mode the caller supplies externalRoots instead.
 */
const availableRoots = computed(() => {
    if (props.externalRoots) return props.externalRoots
    const parent = getWorktreeParent(dataStore.getProject(props.projectId), dataStore)
    return deriveFileRoots({
        gitDirectory: props.gitDirectory,
        cwd: props.sessionCwd,
        projectDirectory: props.projectDirectory,
        projectGitRoot: props.projectGitRoot,
        parentDirectory: parent?.directory,
        parentGitRoot: parent?.git_root,
    })
})

const selectedRootKey = ref(null)

function clearSelectedFile() {
    if (fileTreePanelRef.value?.selectedFile != null) {
        fileTreePanelRef.value.selectedFile = null
    }
}

function clearSelectedBookmark() {
    selectedBookmarkId.value = null
}

function emitNavigate({ rootKey = selectedRootKey.value, filePath = undefined, replace = false }) {
    if (!props.active) return
    emit('navigate', { rootKey, filePath, replace })
}

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
    return selected ? selected.path : null
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
        clearSelectedFile()
        clearSelectedBookmark()
        emitNavigate({ rootKey: key })
    }
}

/**
 * Programmatically select the root whose path matches the given directory.
 * Used by explicit in-app navigation helpers before revealing a target file.
 * Does not emit navigation because the caller is responsible for the URL.
 */
function setRootByPath(path) {
    if (!path) return
    const root = availableRoots.value.find(r => r.path === path)
    if (root && root.key !== selectedRootKey.value && !missingRoots.value.has(root.key)) {
        syncingFromRoute = true
        selectedRootKey.value = root.key
        clearSelectedFile()
        nextTick(() => {
            syncingFromRoute = false
        })
    }
}

// ─── Display options ─────────────────────────────────────────────────────────

const settingsStore = useSettingsStore()
const showHidden = computed(() => settingsStore.showHiddenFiles)
const showIgnored = computed(() => settingsStore.showGitIgnoredFiles)
const isGit = ref(false)

/**
 * Build the query string fragment for display options.
 */
function optionsQuery() {
    let qs = ''
    if (showHidden.value) qs += '&show_hidden=1'
    if (showIgnored.value) qs += '&show_ignored=1'
    if (props.rootRestriction) qs += `&root=${encodeURIComponent(props.rootRestriction)}`
    return qs
}

// ─── Tree state ──────────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const error = ref(null)
const loadedDirectory = ref(null)
const routeRootIssue = ref(null)
const routeFileIssue = ref(null)

function makeRouteIssue(before, detail = null, after = '') {
    return { before, detail, after }
}

const routeIssueMessage = computed(() => routeRootIssue.value || routeFileIssue.value)

/**
 * Fetch the directory tree from the backend.
 */
async function fetchTree(dirPath) {
    if (!resolvedApiPrefix.value || !dirPath) {
        tree.value = null
        loadedDirectory.value = null
        return
    }

    // Re-fetching the directory already on screen (Refresh button): keep
    // `loadedDirectory` on it, so the route-sync watch below still sees a tree
    // that matches the route and leaves the selection alone. Nulling it drops
    // the selected file for the duration of the fetch, and the re-selection
    // that follows resets the open file's view state — preview mode (markdown,
    // SVG, HTML, Mermaid), edit mode, zoom and scroll position.
    const isReload = loadedDirectory.value === dirPath
    loading.value = true
    error.value = null
    if (!isReload) loadedDirectory.value = null

    try {
        const res = await apiFetch(
            `${resolvedApiPrefix.value}/directory-tree/?path=${encodeURIComponent(dirPath)}${optionsQuery()}`
        )
        if (!res.ok) {
            const data = await res.json()

            // If the directory was not found, mark this root as missing and
            // surface it as a route issue without switching to another root.
            if (res.status === 404 && selectedRootKey.value) {
                missingRoots.value = new Set([...missingRoots.value, selectedRootKey.value])
                routeRootIssue.value = makeRouteIssue(
                    'Root ',
                    props.routeRootKey || selectedRootKey.value,
                    ' is no longer available.',
                )
                tree.value = null
                loadedDirectory.value = null
                return
            }

            error.value = data.error || `HTTP ${res.status}`
            tree.value = null
            loadedDirectory.value = null
            return
        }
        const data = await res.json()
        isGit.value = !!data.is_git
        tree.value = data
        loadedDirectory.value = dirPath
    } catch (err) {
        error.value = err.message
        tree.value = null
        loadedDirectory.value = null
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
    if (!resolvedApiPrefix.value || !directory.value) return null

    const res = await apiFetch(
        `${resolvedApiPrefix.value}/file-search/?path=${encodeURIComponent(directory.value)}&q=${encodeURIComponent(query)}${optionsQuery()}`
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
    if (!resolvedApiPrefix.value) return null
    const res = await apiFetch(
        `${resolvedApiPrefix.value}/directory-tree/?path=${encodeURIComponent(path)}${optionsQuery()}`
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

// Fetch tree whenever the resolved API prefix, directory, or display options change
// (only after the panel has been started)
watch(
    () => [started.value, resolvedApiPrefix.value, directory.value, showHidden.value, showIgnored.value],
    ([isStarted, , newDir]) => {
        if (!isStarted) return
        fetchTree(newDir)
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
        settingsStore.setShowHiddenFiles(!showHidden.value)
    } else if (value === 'show-ignored') {
        settingsStore.setShowGitIgnoredFiles(!showIgnored.value)
    } else if (value?.startsWith('root:')) {
        handleRootSelect(value.slice(5))
    }
}

/**
 * Refresh: re-fetch the tree from the root, and re-run the search if active.
 * After refresh, scrolls back to the previously selected file (or a specific
 * target path if provided). If the target no longer exists, clears the
 * selection and scrolls to the root.
 *
 * @param {object} [hints] - Optional hints from file operations.
 * @param {string} [hints.scrollTo] - Absolute path to scroll to after refresh.
 */
async function refresh(hints) {
    const scrollTarget = hints?.scrollTo || fileTreePanelRef.value?.selectedAbsPath

    await fetchTree(directory.value)

    if (fileTreePanelRef.value?.isSearching && fileTreePanelRef.value?.searchQuery.trim()) {
        fileTreePanelRef.value.rerunSearch()
    }

    if (scrollTarget && tree.value) {
        const found = await fileTreePanelRef.value?.scrollToPath(scrollTarget)
        if (!found) {
            clearSelectedFile()
            await fileTreePanelRef.value?.scrollToPath(directory.value)
        }
    }

    filePaneRef.value?.reload()
}

/**
 * Refresh the tree in place WITHOUT disturbing the open file or preview.
 *
 * Unlike refresh()/fetchTree, this never flips `loading`, so the tree panel
 * never swaps to its loading placeholder and the route-sync watch (below) never
 * re-runs its reveal/scroll pass. Here we fetch into a local and swap
 * `tree.value` atomically, so the current selection — and any rendered HTML
 * page — is left untouched. New files still appear in the tree.
 */
async function refreshTreeSoft() {
    const dirPath = directory.value
    if (!resolvedApiPrefix.value || !dirPath) return
    try {
        const res = await apiFetch(
            `${resolvedApiPrefix.value}/directory-tree/?path=${encodeURIComponent(dirPath)}${optionsQuery()}`
        )
        if (!res.ok) return
        const data = await res.json()
        isGit.value = !!data.is_git
        // Keep loadedDirectory consistent and never touch `loading`, so the
        // route-sync watch sees a settled tree and re-reveals (never clears).
        loadedDirectory.value = dirPath
        tree.value = data
        if (fileTreePanelRef.value?.isSearching && fileTreePanelRef.value?.searchQuery.trim()) {
            fileTreePanelRef.value.rerunSearch()
        }
    } catch {
        // Best-effort: leave the existing tree in place on failure.
    }
}

// ─── Live artifact changes (Artifacts tab) ───────────────────────────────────

// Pending changed paths (relative to the artifacts root) accumulated across a
// burst of WebSocket messages, flushed once after a short debounce.
let pendingArtifactPathsBySession = new Map()
let artifactFlushTimer = null

/**
 * Whether a changed file should reload the currently-previewed HTML page. An
 * HTML page pulls its assets (CSS/JS/images) from its own folder and below, so
 * reload when the page sits directly at the artifacts root (any non-data change),
 * or when a changed file shares the page's top-level folder — i.e. the two have a
 * common ancestor below the artifacts root. Changes under the page's own data/
 * subfolder (design 2026-08-05 §7) are excluded: that's the artifact's own state
 * store, and reloading on its own writes would wipe the very state it persists —
 * those changes still refresh the tree, just never the preview. The bare data/
 * directory path itself is excluded too — watchfiles emits an event for it on
 * its very first creation, alongside the file written inside it. Paths are
 * relative to the root (forward slashes).
 *
 * @param {string} renderedRel — the previewed HTML file, relative to the root
 * @param {string[]} paths — changed files, relative to the root
 */
function changeAffectsHtmlPage(renderedRel, paths) {
    const renderedSegments = renderedRel.split('/')
    // A page's own data/ store (design 2026-08-05 §7): the artifact writing its
    // state must NOT reload itself — the save would wipe the very state it
    // persists. data/ changes still refresh the tree, never the preview.
    const pageDir = renderedSegments.slice(0, -1).join('/')
    const dataPrefix = (pageDir ? pageDir + '/' : '') + 'data/'
    const relevant = paths.filter(p => p !== dataPrefix.slice(0, -1) && !p.startsWith(dataPrefix))
    if (renderedSegments.length <= 1) return relevant.length > 0  // page at the root → any non-data change
    const topFolder = renderedSegments[0]
    return relevant.some(p => {
        const segments = p.split('/')
        return segments.length > 1 && segments[0] === topFolder
    })
}

function flushArtifactChanges() {
    artifactFlushTimer = null
    const pathsBySession = pendingArtifactPathsBySession
    pendingArtifactPathsBySession = new Map()

    // Refresh the physical root only for changes from the session which owns it.
    if (pathsBySession.has(props.artifactBookmarkSessionId)) refreshTreeSoft()

    // Reload the *displayed* content only when the change is relevant to it.
    const renderedRel = selectedContentPath.value
    const renderedSessionId = selectedArtifactSessionId.value
    const changedPaths = pathsBySession.get(renderedSessionId)
    if (!renderedRel || !changedPaths) return
    const paths = [...changedPaths]
    if (filePaneRef.value?.isHtmlPreviewActive) {
        // HTML page: reload the rendered iframe when an asset in its folder changed.
        if (changeAffectsHtmlPage(renderedRel, paths)) {
            filePaneRef.value.reloadHtmlPreview()
        }
    } else if (paths.includes(renderedRel)) {
        // Any other displayed file (source, markdown/mermaid preview, image…):
        // reload only when that exact file itself changed.
        filePaneRef.value?.reload()
    }
}

/**
 * Live-reload hook for the Artifacts tab, called by SessionView when the backend
 * relays artifact file change(s) for any session. Changes from the physical
 * root refresh its tree; changes from the owner of a selected virtual bookmark
 * refresh only that preview.
 *
 * @param {string} sourceSessionId — session whose artifacts changed
 * @param {string[]} paths — changed files, relative to the artifacts root
 */
function onArtifactFilesChanged(sourceSessionId, paths) {
    // Compatibility with callers predating cross-session bookmark previews.
    if (Array.isArray(sourceSessionId)) {
        paths = sourceSessionId
        sourceSessionId = props.artifactBookmarkSessionId
    }
    if (!started.value || !paths?.length) return
    const relevant = sourceSessionId === props.artifactBookmarkSessionId
        || sourceSessionId === selectedArtifactSessionId.value
    if (!relevant) return
    const pending = pendingArtifactPathsBySession.get(sourceSessionId) || new Set()
    for (const p of paths) pending.add(p)
    pendingArtifactPathsBySession.set(sourceSessionId, pending)
    if (artifactFlushTimer) clearTimeout(artifactFlushTimer)
    artifactFlushTimer = setTimeout(flushArtifactChanges, 250)
}

/**
 * Full live-refresh after a WebSocket reconnection. Unlike onArtifactFilesChanged
 * (which knows exactly which files moved), a reconnect can have missed any number
 * of changes during the outage, so we refresh the tree softly and reload the
 * currently-displayed file / HTML preview unconditionally. No-op until the panel
 * has been opened once (it fetches fresh on first open anyway).
 */
async function reloadAll() {
    if (!started.value) return
    await refreshTreeSoft()
    const renderedRel = selectedContentPath.value
    if (!renderedRel) return
    if (filePaneRef.value?.isHtmlPreviewActive) {
        filePaneRef.value.reloadHtmlPreview()
    } else {
        filePaneRef.value?.reload()
    }
}

onBeforeUnmount(() => {
    if (artifactFlushTimer) clearTimeout(artifactFlushTimer)
})

// ─── File selection ──────────────────────────────────────────────────────────

/**
 * Selected file relative path — proxied from the FileTreePanel ref.
 */
const selectedFile = computed(() => fileTreePanelRef.value?.selectedFile ?? null)
const selectedAbsPath = computed(() => {
    if (!selectedFile.value || !directory.value) return null
    const dir = directory.value
    return `${dir === '/' ? '' : dir}/${selectedFile.value}`
})
const selectedBookmarkId = ref(null)
const selectedBookmark = computed(() => {
    if (selectedBookmarkId.value == null) return null
    return props.artifactBookmarks.find(
        bookmark => String(bookmark.id) === String(selectedBookmarkId.value),
    ) || null
})

const selectedContentPath = computed(() =>
    selectedBookmark.value?.relative_path || selectedFile.value,
)
const selectedContentRoot = computed(() =>
    selectedBookmark.value?.root || props.rootRestriction,
)
const selectedContentAbsPath = computed(() => {
    if (selectedBookmark.value) {
        const root = selectedBookmark.value.root?.replace(/\/$/, '')
        const relativePath = selectedBookmark.value.relative_path?.replace(/^\//, '')
        return root && relativePath ? `${root}/${relativePath}` : null
    }
    return selectedAbsPath.value
})
const selectedArtifactSessionId = computed(() =>
    selectedBookmark.value?.session_id || props.artifactBookmarkSessionId,
)
const hasContentSelection = computed(() => !!selectedContentPath.value)

// Mobile only: the file-tree overlay covers the whole panel when open. A
// pooled HTML-preview frame paints above pane content and would show through
// that overlay, so suppress it while the overlay is up (the content is fully
// covered anyway). Proxied from the FileTreePanel ref like selectedFile.
const treeOverlayOpen = computed(() => isMobile.value && !!fileTreePanelRef.value?.fileTreeOpen)

// On a tab-activation focus request from the layout (focusRequest bumped by SessionView), focus the
// panel's primary content: the file viewer when a file is open (so keyboard nav keeps reading/scrolling
// it), otherwise the tree's search filter. Deferred until the host sub-panel ref appears (it can mount a
// frame or more after the request on a cold load), so the request is never dropped — see usePanelContentFocus.
usePanelContentFocus({
    focusRequest: () => props.focusRequest,
    selectedFile: selectedContentPath,
    filePaneRef,
    fileTreePanelRef,
})

function handleFileSelect(path) {
    if (!props.active || syncingFromRoute) return
    clearSelectedBookmark()
    routeFileIssue.value = null
    emitNavigate({
        rootKey: selectedRootKey.value,
        filePath: path || undefined,
    })
}

function handleBookmarkSelect(bookmark) {
    if (!props.active || !bookmark) return
    selectedBookmarkId.value = bookmark.id
    clearSelectedFile()
    routeRootIssue.value = null
    routeFileIssue.value = null
    emitNavigate({ rootKey: 'bookmarks', filePath: String(bookmark.id) })
    if (isMobile.value) fileTreePanelRef.value?.closeFileTree()
}

function handleBookmarkFocus(path) {
    fileTreePanelRef.value?.onNodeFocus(path)
}

async function waitForTreePanelReady() {
    await nextTick()
    await nextTick()
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
}

async function revealRouteFile(absolutePath) {
    const treePanel = fileTreePanelRef.value
    if (!treePanel) return false

    treePanel.clearSearch(false)

    for (let attempt = 0; attempt < 3; attempt++) {
        await waitForTreePanelReady()
        if (await treePanel.scrollToPath(absolutePath)) {
            return true
        }
    }

    return false
}

watch(
    () => [props.active, props.routeRootKey, availableRoots.value.map(root => root.key).join('|'), props.routeOwner],
    ([active, routeRootKey]) => {
        if (!active || !props.routeOwner) return
        if (routeRootKey === 'bookmarks') {
            routeRootIssue.value = null
            return
        }

        clearSelectedBookmark()
        const roots = availableRoots.value
        if (!roots.length) return

        if (!routeRootKey) {
            routeRootIssue.value = null
            const defaultRoot = roots.find(root => !missingRoots.value.has(root.key)) || roots[0]
            if (selectedRootKey.value !== defaultRoot?.key) {
                syncingFromRoute = true
                selectedRootKey.value = defaultRoot?.key ?? null
                clearSelectedFile()
                nextTick(() => {
                    syncingFromRoute = false
                })
            }
            return
        }

        const requestedRoot = roots.find(root => root.key === routeRootKey)
        if (!requestedRoot) {
            routeRootIssue.value = makeRouteIssue('Root ', routeRootKey, ' is not available.')
            if (selectedRootKey.value !== null) {
                syncingFromRoute = true
                selectedRootKey.value = null
                clearSelectedFile()
                nextTick(() => {
                    syncingFromRoute = false
                })
            }
            return
        }

        if (missingRoots.value.has(routeRootKey)) {
            routeRootIssue.value = makeRouteIssue('Root ', routeRootKey, ' is no longer available.')
            if (selectedRootKey.value !== routeRootKey) {
                syncingFromRoute = true
                selectedRootKey.value = routeRootKey
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
            clearSelectedFile()
            nextTick(() => {
                syncingFromRoute = false
            })
        }
    },
    { immediate: true },
)

watch(
    () => [
        props.active,
        props.routeOwner,
        props.routeRootKey,
        props.routeFilePath,
        props.artifactBookmarks.map(bookmark => bookmark.id).join('|'),
    ],
    async ([active, routeOwner, routeRootKey, routeFilePath]) => {
        if (!active || !routeOwner || routeRootKey !== 'bookmarks') return

        const bookmark = props.artifactBookmarks.find(
            row => String(row.id) === String(routeFilePath),
        )
        syncingFromRoute = true
        clearSelectedFile()
        if (routeFilePath == null) {
            selectedBookmarkId.value = null
            routeFileIssue.value = routeFilePath === null
                ? makeRouteIssue('Requested bookmark id is invalid.')
                : null
        } else if (bookmark) {
            selectedBookmarkId.value = bookmark.id
            routeFileIssue.value = null
        } else {
            selectedBookmarkId.value = null
            routeFileIssue.value = makeRouteIssue(
                'Bookmarked artifact ',
                routeFilePath,
                ' is no longer available in this session scope.',
            )
        }
        await nextTick()
        syncingFromRoute = false
    },
    { immediate: true },
)

watch(
    () => [props.active, tree.value, directory.value, loadedDirectory.value, props.routeFilePath, props.routeRootKey, loading.value, props.routeOwner],
    async ([active, treeData, dirPath, loadedDir, routeFilePath, , isLoading]) => {
        if (props.routeRootKey === 'bookmarks') return
        if (!active || !props.routeOwner || !dirPath || !selectedRootKey.value) return

        if (!treeData || loadedDir !== dirPath) {
            if (routeFilePath != null && selectedFile.value) {
                syncingFromRoute = true
                clearSelectedFile()
                await nextTick()
                syncingFromRoute = false
            }
            return
        }

        // A refresh of the directory already on screen: the tree on display
        // still matches the route, so keep the selection (and the open file's
        // view state) until the fetch settles and this watch runs again.
        if (isLoading) return

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
                routeFileIssue.value = null
            }
            return
        }

        routeFileIssue.value = null

        const absolutePath = `${dirPath === '/' ? '' : dirPath}/${routeFilePath}`
        syncingFromRoute = true
        const found = await revealRouteFile(absolutePath)
        if (found) {
            if (selectedFile.value !== routeFilePath) {
                fileTreePanelRef.value?.onFileSelect(absolutePath)
            }
            await waitForTreePanelReady()
            await fileTreePanelRef.value?.scrollToPath(absolutePath)
        } else {
            clearSelectedFile()
            routeFileIssue.value = makeRouteIssue(
                'File ',
                routeFilePath,
                ' is no longer available in this root.',
            )
        }
        await nextTick()
        syncingFromRoute = false
    },
    { immediate: true },
)

// ─── Split panel position (KeepAlive-safe) ──────────────────────────────────

const TREE_DEFAULT_WIDTH = 250
const treePanelWidth = ref(TREE_DEFAULT_WIDTH)
const splitPanelRef = ref(null)
// Neutralize pooled-iframe pointer-events while this split's divider is dragged
// (the content pane on the right can be a pooled HTML preview that would freeze
// the drag; a pooled frame from another pane can also sit under its path).
useSplitDividerDragFlag(splitPanelRef)

// The FilePane's over-iframe overlay layer, when its HTML preview owns a pooled
// frame — the route-issue callout is teleported there so it paints above the
// frame (its own z-index can't, inside DockRegion's isolated context).
const filePaneFrameOverlayEl = computed(() => filePaneRef.value?.frameOverlayEl ?? null)

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
// container so the component state (tree expansion, editor, unsaved
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

// ─── External file reveal (used by "View in Files tab" from Git panel) ───────

/**
 * Navigate to and select a file by its absolute path, optionally scrolling
 * the editor to a specific line number.
 *
 * Ensures the panel is started (tree loaded), clears any active search,
 * then scrolls to the file and selects it.
 *
 * @param {string} absolutePath — the absolute filesystem path to reveal
 * @param {Object} [options]
 * @param {number|null} [options.lineNum=null] — 1-based line to scroll to after opening
 * @returns {boolean} true if the file was found and selected
 */
async function revealFile(absolutePath, { lineNum = null } = {}) {
    // Ensure the panel is started (triggers tree fetch via the watcher if needed)
    if (!started.value) {
        started.value = true
        // Wait for the tree to be fetched by the watcher
        await new Promise(resolve => {
            const stop = watch(
                () => [tree.value, error.value],
                ([t, err]) => {
                    if (t !== null || err) {
                        stop()
                        resolve()
                    }
                },
                { immediate: true },
            )
        })
    }

    if (!tree.value) return false

    // Clear any active search first (without triggering a reveal)
    fileTreePanelRef.value?.clearSearch(false)

    // scrollToPath handles lazy-loading directories, setting revealedPaths,
    // and scrolling to the target
    let found = await fileTreePanelRef.value?.scrollToPath(absolutePath)

    // Miss: the target may have just been created (e.g. a fresh artifact) and
    // not be in the cached tree yet. Refresh the tree once and retry so it lists
    // and loads correctly instead of showing a stale "not found".
    if (!found) {
        await fetchTree(directory.value)
        found = await fileTreePanelRef.value?.scrollToPath(absolutePath)
    }

    // Check if the file is already selected (same path) — will need a reload
    const alreadySelected = selectedAbsPath.value === absolutePath

    // Always select the file so FilePane attempts to load it.
    // If found in the tree, scrollToPath has already revealed the path.
    // If not found (deleted, moved, etc.), FilePane will fetch the content
    // and display the backend error (e.g. "File not found").
    fileTreePanelRef.value?.onFileSelect(absolutePath)

    // Scroll to the requested line (default: top of file)
    const targetLine = lineNum ?? 1
    if (alreadySelected) {
        // File already selected — reload from backend to avoid stale content,
        // then scroll after the fetch completes.
        await filePaneRef.value?.reload()
        await nextTick()
        filePaneRef.value?.scrollToLine(targetLine)
    } else {
        // New file — wait for FilePane to finish loading, then scroll.
        // isLoading goes true during fetch and false when content is ready.
        const stop = watch(
            () => filePaneRef.value?.isLoading,
            (loading) => {
                if (loading === false) {
                    stop()
                    nextTick(() => filePaneRef.value?.scrollToLine(targetLine))
                }
            },
        )
    }

    return !!found
}

defineExpose({ revealFile, setRootByPath, onArtifactFilesChanged, reloadAll })
</script>

<template>
    <div class="files-panel">
        <!-- Route-issue callout. Teleported over the pooled HTML frame when the
             preview owns one (pane-local z-index can't beat the FrameHost layer);
             renders in place otherwise. -->
        <Teleport :to="filePaneFrameOverlayEl" :disabled="!filePaneFrameOverlayEl">
            <div v-if="routeIssueMessage" class="pane-callout-overlay">
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
        </Teleport>

        <!-- ═══ Hidden owners: single instances that get reparented ═══ -->
        <div ref="treeOwnerRef" class="reparent-owner">
            <FileTreePanel
                ref="fileTreePanelRef"
                :tree="tree"
                :loading="loading"
                :error="error"
                :root-path="directory"
                :root-label="rootLabel"
                :search-fn="doSearch"
                :lazy-load-fn="lazyLoadDir"
                :project-id="projectId"
                :session-id="sessionId"
                :is-draft="isDraft"
                :root-restriction="rootRestriction"
                :extra-query="optionsQuery()"
                :show-refresh="true"
                :is-mobile="isMobile"
                :artifact-bookmark-session-id="artifactBookmarkSessionId"
                :has-extra-tree="artifactBookmarks.length > 0"
                :external-selection-label="selectedBookmark?.relative_path || null"
                :external-selection-tooltip="selectedBookmark?.relative_path || null"
                :external-artifact-session-id="selectedBookmark?.session_id || null"
                :external-artifact-relative-path="selectedBookmark?.relative_path || null"
                :external-artifact-abs-path="selectedBookmark ? selectedContentAbsPath : null"
                :search-placeholder="artifactBookmarks.length ? 'Filter artifacts...' : 'Filter files...'"
                :commented-paths="commentedPaths"
                enable-context-menu
                mode="files"
                @file-select="handleFileSelect"
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
                    <!-- Root selector — hidden for a single fixed root (e.g. Artifacts) -->
                    <template v-if="showRootSelector">
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
                            :disabled="missingRoots.has(root.key)"
                        >
                            <div v-if="root.projectId" class="root-label">
                                <ProjectBadge :project-id="root.projectId" />
                            </div>
                            <div v-else>{{ root.label }}</div>
                            <div class="root-path">{{ root.path }}</div>
                            <div v-if="missingRoots.has(root.key)" class="root-missing">Directory no longer exists</div>
                        </wa-dropdown-item>
                    </template>
                    <wa-divider></wa-divider>
                </template>
                <template #tree-after="{ searchQuery }">
                    <ArtifactBookmarkTree
                        v-if="artifactBookmarks.length"
                        :bookmarks="artifactBookmarks"
                        :current-project-id="artifactBookmarkProjectId"
                        :main-project-id="artifactBookmarkMainProjectId"
                        :selected-bookmark-id="selectedBookmarkId"
                        :search-query="searchQuery"
                        @select="handleBookmarkSelect"
                        @focus="handleBookmarkFocus"
                    />
                </template>
            </FileTreePanel>
        </div>

        <div ref="contentOwnerRef" class="reparent-owner">
            <div class="files-content-inner">
                <FilePane
                    ref="filePaneRef"
                    v-show="hasContentSelection"
                    :project-id="selectedBookmark ? null : projectId"
                    :session-id="selectedBookmark ? null : sessionId"
                    :file-path="selectedContentAbsPath"
                    :display-path="!isMobile ? selectedContentPath : null"
                    :active="active"
                    :is-draft="isDraft"
                    :api-prefix="selectedBookmark ? '/api' : resolvedApiPrefix"
                    :root-restriction="selectedContentRoot"
                    :preview-by-default="previewByDefault"
                    :render-only="renderOnly"
                    :artifact-bookmark-session-id="selectedArtifactSessionId"
                    :frame-elevated="frameElevated"
                    :frame-suppressed="treeOverlayOpen"
                />
                <div v-show="!hasContentSelection" class="panel-placeholder">
                    {{ artifactBookmarkSessionId || artifactBookmarks.length ? 'Select an artifact' : 'Select a file' }}
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
    display: flex;
    flex-direction: column;
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

/* ═══════════════════════════════════════════════════════════════════════════
   Mobile layout
   ═══════════════════════════════════════════════════════════════════════════ */

.mobile-layout {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Split panel: file tree (start/primary) + file content (end)
   ═══════════════════════════════════════════════════════════════════════════ */

.files-split-panel {
    flex: 1;
    min-height: 0;
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

.root-label {
    display: flex;
    min-width: 0;
}

.root-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.root-missing {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-fill-loud);
}

/* Force-sync the checkmark visual on root selector items via CSS ::part().
   Same fix as GitPanel: the wa-dropdown-item's internal `checked` property
   can get desynced from Vue's reactive state after makeSelection() toggles it.
   The data-root-selected HTML attribute stays in sync with Vue's state. */
wa-dropdown-item[data-root-selected="true"]::part(checkmark) {
    visibility: visible;
}
wa-dropdown-item[data-root-selected="false"]::part(checkmark) {
    visibility: hidden;
}
</style>
