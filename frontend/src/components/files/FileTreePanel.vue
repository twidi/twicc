<script setup>
/**
 * FileTreePanel - Shared component encapsulating a file tree with search,
 * keyboard navigation, options dropdown, and scroll-to-path functionality.
 *
 * Used by both FilesPanel (files mode) and GitPanel (git mode). The parent
 * provides the tree data and a search function; FileTreePanel handles the
 * entire search lifecycle, keyboard navigation, focus management, and
 * placeholder states.
 *
 * Props:
 *   tree: root tree node ({ name, type, children, loaded? }) — provided by parent
 *   loading: whether the tree is being loaded
 *   error: error message to display
 *   rootPath: root path for FileTree :path prop (filesystem path or tree name)
 *   searchFn: async (query) => { tree, total, truncated } — search implementation
 *   lazyLoadFn: async (path) => { children } | null — for scrollToPath lazy-load
 *   projectId, sessionId: for FileTree API calls
 *   isDraft: for FileTree API prefix
 *   extraQuery: for FileTree lazy-load query string
 *   showRefresh: whether to show the Refresh option in the dropdown
 *   mode: 'files' | 'git' — passed through to FileTree
 *
 * Events:
 *   file-select(path): a file was selected in the tree
 *   refresh(): user clicked Refresh
 *   option-select(value): an unrecognized option was selected (for parent handling)
 *
 * Slots:
 *   options-before: injected before the shared options in the dropdown
 */

import { ref, computed, watch, nextTick, shallowRef, useId } from 'vue'
import { apiFetch } from '../../utils/api'
import { buildFileDownloadUrl, triggerDownload } from '../../utils/download'
import FileTree from './FileTree.vue'
import FileTreeContextMenu from './FileTreeContextMenu.vue'
import FileRenameDialog from './FileRenameDialog.vue'
import FileDeleteDialog from './FileDeleteDialog.vue'
import FileCreateDialog from './FileCreateDialog.vue'
import FileMoveDialog from './FileMoveDialog.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import GitStatusBadge from '../ui/GitStatusBadge.vue'
import ArtifactBookmarkButton from '../artifacts/ArtifactBookmarkButton.vue'
import { useDataStore } from '../../stores/data'
import { isRenderableArtifactPath } from '../../utils/artifactBookmark'
import { useFocusRetry } from '../../composables/useFocusRetry'

const props = defineProps({
    tree: {
        type: Object,
        default: null,
    },
    loading: {
        type: Boolean,
        default: false,
    },
    error: {
        type: String,
        default: null,
    },
    rootPath: {
        type: String,
        default: null,
    },
    // Optional display label for the root node (defaults to rootPath itself).
    rootLabel: {
        type: String,
        default: null,
    },
    searchFn: {
        type: Function,
        default: null,
    },
    lazyLoadFn: {
        type: Function,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    sessionId: {
        type: String,
        default: null,
    },
    isDraft: {
        type: Boolean,
        default: false,
    },
    extraQuery: {
        type: String,
        default: '',
    },
    showRefresh: {
        type: Boolean,
        default: true,
    },
    mode: {
        type: String,
        default: 'files',  // 'files' | 'git'
    },
    directoriesOnly: {
        type: Boolean,
        default: false,
    },
    compactFolders: {
        type: Boolean,
        default: true,
    },
    /**
     * Whether to show the shared options (Auto-open, Scroll to selected file).
     * Set to false for popup contexts where these don't apply.
     */
    showSharedOptions: {
        type: Boolean,
        default: true,
    },
    /**
     * Whether the panel is in mobile layout mode.
     * When true, the file tree is hidden behind a header that shows the
     * selected file path. Clicking the header opens the tree as an overlay.
     */
    isMobile: {
        type: Boolean,
        default: false,
    },
    /** Set of paths with code comments (files + ancestor dirs). */
    commentedPaths: {
        type: Set,
        default: () => new Set(),
    },
    enableContextMenu: {
        type: Boolean,
        default: false,
    },
    /** Context menu mode: 'files' (full file ops), 'git-index' (uncommitted), 'git-commit' (committed) */
    contextMenuMode: {
        type: String,
        default: 'files',
    },
    /** Absolute path of the git directory (for building fullPath in git mode) */
    gitDirectory: {
        type: String,
        default: null,
    },
    /**
     * Confinement root for the standalone (project-less) raw-file endpoint —
     * the Artifacts tab passes the session's artifacts dir. Ignored when a
     * projectId is set, where the project/session scope applies instead.
     */
    rootRestriction: {
        type: String,
        default: null,
    },
    // When set (the owning session), the mobile header shows an artifact bookmark
    // toggle for renderable artifacts and surfaces the artifact bookmark name.
    artifactBookmarkSessionId: {
        type: String,
        default: null,
    },
    /** Whether a parent-provided virtual tree root should share this navigator. */
    hasExtraTree: {
        type: Boolean,
        default: false,
    },
    /** Selection metadata supplied by a virtual root (for the compact header). */
    externalSelectionLabel: {
        type: String,
        default: null,
    },
    externalSelectionTooltip: {
        type: String,
        default: null,
    },
    /** Bookmark action inputs supplied by a virtual artifact selection. */
    externalArtifactSessionId: {
        type: String,
        default: null,
    },
    externalArtifactRelativePath: {
        type: String,
        default: null,
    },
    externalArtifactAbsPath: {
        type: String,
        default: null,
    },
    searchPlaceholder: {
        type: String,
        default: 'Filter files...',
    },
})

const emit = defineEmits([
    'file-select', 'refresh', 'option-select', 'filter-input',
    'git-stage', 'git-unstage', 'git-discard',
    // Git modes only: the owner (GitPanel) knows the revision being viewed, so
    // it builds the download URL. In files mode the panel handles it itself.
    'download', 'download-diff',
])

// ─── Mobile overlay state ────────────────────────────────────────────────────

const fileTreeOpen = ref(false)

function toggleFileTree() {
    fileTreeOpen.value = !fileTreeOpen.value
}

function closeFileTree() {
    fileTreeOpen.value = false
}

/**
 * Placeholder text for the mobile header when no file is selected.
 * Mirrors the placeholder states from the template.
 */
const headerPlaceholder = computed(() => {
    if (isSearching.value && !searchTree.value?.children?.length && searchResponded.value) {
        return 'No matches'
    }
    if (!props.rootPath && !props.hasExtraTree) {
        return props.mode === 'git' ? 'No changes' : 'No directory'
    }
    return 'Select a file'
})

// ─── Search state ────────────────────────────────────────────────────────────

const searchQuery = ref('')
const searchTree = ref(null)
const searchTotal = ref(0)
const searchTruncated = ref(false)
const searchLoading = ref(false)
const isSearching = ref(false)
const searchResponded = ref(false)  // true once the first search response has arrived

let searchDebounceTimer = null

function onSearchInput(event) {
    const query = event.target.value
    searchQuery.value = query
    emit('filter-input', query)

    clearTimeout(searchDebounceTimer)

    if (!query.trim()) {
        clearSearch()
        return
    }

    // Reset response state if we have nothing to show (first search or previous "no matches")
    // When we already have results, keep them visible during the new search
    if (!searchTree.value?.children?.length) {
        searchResponded.value = false
    }
    isSearching.value = true
    searchDebounceTimer = setTimeout(() => {
        executeSearch(query.trim())
    }, 250)
}

async function executeSearch(query) {
    if (!props.searchFn || !props.rootPath) {
        // A virtual root filters locally through the scoped slot. Mark the
        // search as answered so an empty virtual result can show "No matches".
        searchResponded.value = true
        return
    }

    searchLoading.value = true
    try {
        const result = await props.searchFn(query)
        // Only update if query still matches (avoid stale results)
        if (result && searchQuery.value.trim() === query) {
            searchTree.value = result.tree
            searchTotal.value = result.total
            searchTruncated.value = result.truncated
            searchResponded.value = true
        }
    } catch {
        // Silently fail — keep previous results
    } finally {
        searchLoading.value = false
    }
}

/**
 * Re-execute the current search query (e.g. after tree data changes or
 * display options change). Call from parent via ref.
 */
function rerunSearch() {
    if (isSearching.value && searchQuery.value.trim()) {
        executeSearch(searchQuery.value.trim())
    }
}

/**
 * Clear the search state and optionally scroll to the focused item in the tree.
 * @param {boolean} reveal - If true, scroll to the previously focused item in
 *   the normal tree. Set to false when clearing as part of a tree re-fetch (the
 *   tree is being replaced, so reveal would be wasted).
 */
function clearSearch(reveal = true) {
    // Capture the focused path before clearing search state.
    // Only reveal if we were actually searching — this prevents double reveals
    // when wa-input fires both @wa-clear and @input on the clear button click.
    const pathToScroll = (reveal && isSearching.value) ? focusedPath.value : null

    searchQuery.value = ''
    emit('filter-input', '')
    isSearching.value = false
    searchTree.value = null
    searchTotal.value = 0
    searchTruncated.value = false
    searchResponded.value = false

    // If a path was focused during search, scroll to it in the normal tree
    if (pathToScroll && props.tree) {
        scrollToPath(pathToScroll)
    }
}

// ─── File selection ──────────────────────────────────────────────────────────

const selectedFile = ref(null)
const selectedFileId = useId()

const effectiveSelectionLabel = computed(() => props.externalSelectionLabel || selectedFile.value)
const effectiveSelectionTooltip = computed(() =>
    props.externalSelectionTooltip || effectiveSelectionLabel.value,
)
const effectiveArtifactSessionId = computed(() =>
    props.externalArtifactSessionId || props.artifactBookmarkSessionId,
)
const effectiveArtifactRelativePath = computed(() =>
    props.externalArtifactRelativePath || selectedFile.value,
)

const dataStore = useDataStore()
// Mobile header artifact-bookmark integration (artifact context only).
const headerArtifactBookmark = computed(() =>
    effectiveArtifactSessionId.value && effectiveArtifactRelativePath.value
        ? dataStore.artifactBookmarkFor(effectiveArtifactSessionId.value, effectiveArtifactRelativePath.value)
        : null,
)
const headerArtifactBookmarkable = computed(() =>
    !!effectiveArtifactSessionId.value && isRenderableArtifactPath(effectiveArtifactRelativePath.value),
)
const fileOptionsButtonId = useId()

/** Git status node for the selected file — drives the mobile header flag (git mode only). */
const selectedFileNode = computed(() => {
    if (props.mode !== 'git') return null
    const file = selectedFile.value
    const tree = props.tree
    if (!file || !tree) return null
    let node = tree
    for (const part of file.split('/')) {
        node = node.children?.find(c => c.name === part)
        if (!node) return null
    }
    return node.type === 'file' ? node : null
})

/**
 * Handle file selection from either the main tree or search results.
 * Receives the path from FileTree, stores it and emits to parent.
 */
function onFileSelect(path) {
    const prefix = props.rootPath + '/'
    selectedFile.value = path.startsWith(prefix)
        ? path.slice(prefix.length)
        : path
    emit('file-select', selectedFile.value)

    // In mobile mode, close the overlay after selecting a file
    if (props.isMobile) {
        fileTreeOpen.value = false
    }
}

// In mobile mode, default the file-tree overlay OPEN whenever there is a tree to browse but no file
// is selected yet — i.e. arriving on /files, /git, /artifacts with no file/commit in the URL (or the
// panel becoming narrow with nothing selected). Saves a click, handy now that docks can show several
// of these tabs at once. Git "no changes" is excluded for free: rootPath is null then, so the
// condition is false. The watch only fires on these dep transitions, so a manual close persists and
// selecting a file (which clears the condition) never reopens it.
watch(
    () => props.isMobile && !effectiveSelectionLabel.value && (!!props.rootPath || props.hasExtraTree),
    (shouldOpen) => {
        if (shouldOpen) fileTreeOpen.value = true
    },
    { immediate: true },
)

/**
 * Handle focus change from a click on any tree node (file or directory).
 * Updates focusedPath so keyboard navigation resumes from the clicked item.
 */
function onNodeFocus(absolutePath) {
    focusedPath.value = absolutePath
}

/**
 * Absolute path of the currently selected file.
 * Used by FileTree to highlight the selected file and its ancestor directories.
 */
const selectedAbsPath = computed(() => {
    if (!selectedFile.value || !props.rootPath) return null
    return `${props.rootPath}/${selectedFile.value}`
})

/**
 * The tree node to display: search results when searching, main tree otherwise.
 */
const displayTree = computed(() => {
    if (isSearching.value && searchTree.value?.children?.length) {
        return searchTree.value
    }
    return props.tree
})

// ─── Options dropdown ────────────────────────────────────────────────────────

const autoOpen = ref(false)

function handleOptionsSelect(event) {
    const value = event.detail?.item?.value
    if (value === 'auto-open') {
        autoOpen.value = !autoOpen.value
    } else if (value === 'reveal-in-tree') {
        if (selectedAbsPath.value) {
            scrollToPath(selectedAbsPath.value)
        }
    } else if (value === 'refresh') {
        emit('refresh')
    } else if (value) {
        // Unknown value: let parent handle it
        emit('option-select', value)
    }
}

// ─── Focus management ────────────────────────────────────────────────────────

const searchInputRef = ref(null)

const requestSearchFocus = useFocusRetry()

function holdsFocus(el) {
    // wa-input retargets focus into its shadow <input>, so the host is document.activeElement.
    return document.activeElement === el || el?.shadowRoot?.activeElement != null
}

// Focus the search input. Routed through the focus-retry pump because the request can fire before the
// field is visible (the panel is still navigating in; the field renders only once the tree has loaded,
// v-if="tree && searchFn") and because a route-sync reveal can steal focus to a tree item right after.
// Exposed so the parent panels (which route filter-vs-viewer focus) and the picker popups can call it.
function focusSearchInput() {
    requestSearchFocus(() => {
        const el = searchInputRef.value
        if (!el) return null // input not rendered yet (tree still loading) — wait for it to appear
        if (!holdsFocus(el)) {
            try {
                el.focus()
            } catch {
                // WaInput.focus() can throw if its shadow <input> isn't ready yet. Benign — retry.
            }
        }
        return holdsFocus(el)
    })
}

// ─── Keyboard navigation ─────────────────────────────────────────────────────

const focusedPath = ref(null)
const treeContainerRef = ref(null)

const PAGE_SIZE = 10  // Number of items to skip with PageUp/PageDown

/**
 * Get all visible tree item elements in DOM order.
 * A node is visible if it has a non-zero height (i.e. not inside a closed parent).
 */
function getVisibleItems() {
    if (!treeContainerRef.value) return []
    const all = treeContainerRef.value.querySelectorAll('[role="treeitem"]')
    return Array.from(all).filter(el => el.offsetHeight > 0)
}

/**
 * Find the index of the currently focused item in the visible items list.
 */
function getFocusedIndex(items) {
    if (!focusedPath.value) return -1
    return items.findIndex(el => el.dataset.path === focusedPath.value)
}

/**
 * Offset that brings [start, end] into [viewStart, viewEnd] on one axis, following
 * the `scrollIntoView` 'nearest' rule.
 */
function nearestScrollDelta(start, end, viewStart, viewEnd) {
    const size = end - start
    const view = viewEnd - viewStart
    const startOut = start < viewStart
    const endOut = end > viewEnd
    if (startOut && endOut) return 0
    if ((startOut && size < view) || (endOut && size > view)) return start - viewStart
    if ((startOut && size > view) || (endOut && size < view)) return end - viewEnd
    return 0
}

/**
 * Scroll a tree item into view inside the tree's own scroller ONLY.
 *
 * Never use `Element.scrollIntoView()` here: it also scrolls every scrollable
 * ancestor, `overflow: hidden` ones included. Tree rows are far wider than the
 * tree (`.file-tree-node` is `width: 1000%`), so Firefox aligns their left edge
 * and scrolls the session layout sideways — the whole layout shifts and leaves
 * a blank strip the user cannot scroll back.
 */
function scrollItemIntoTree(el) {
    const container = treeContainerRef.value
    if (!container || !el) return
    const box = container.getBoundingClientRect()
    const item = el.getBoundingClientRect()
    const top = box.top + container.clientTop
    const left = box.left + container.clientLeft
    container.scrollTop += nearestScrollDelta(item.top, item.bottom, top, top + container.clientHeight)
    container.scrollLeft += nearestScrollDelta(item.left, item.right, left, left + container.clientWidth)
}

/**
 * Set focus to a specific item element: update focusedPath, scroll into view,
 * and move DOM focus.
 */
function focusItem(el) {
    if (!el) return
    focusedPath.value = el.dataset.path
    nextTick(() => {
        scrollItemIntoTree(el)
        el.focus({ preventScroll: true })
    })
    // Auto-open: select the file automatically when navigating to it
    if (autoOpen.value && el.dataset.type === 'file') {
        onFileSelect(el.dataset.path)
    }
}

/**
 * Simulate a click on the focused node-label element.
 * This triggers the same click handler as a mouse click, which handles
 * open/close for directories and file selection.
 */
function activateFocused(items, index) {
    if (index < 0 || index >= items.length) return
    items[index].click()
}

/**
 * Open a directory node by clicking it if it's currently closed.
 */
function openDirectory(el) {
    if (el.dataset.type === 'directory' && el.dataset.open === 'false') {
        el.click()
    }
}

/**
 * Close a directory node by clicking it if it's currently open.
 */
function closeDirectory(el) {
    if (el.dataset.type === 'directory' && el.dataset.open === 'true') {
        el.click()
    }
}

/**
 * Find the parent directory element of a given item.
 * Walks up the DOM from the item's .file-tree-node to find the parent's .node-label.
 */
function findParentDirectoryEl(el) {
    // el is a .node-label inside a .file-tree-node
    // Its parent .file-tree-node is inside a .node-children inside another .file-tree-node
    const treeNode = el.closest('.file-tree-node')
    if (!treeNode) return null
    const parentChildren = treeNode.parentElement
    if (!parentChildren || !parentChildren.classList.contains('node-children')) return null
    const parentTreeNode = parentChildren.closest('.file-tree-node')
    if (!parentTreeNode) return null
    return parentTreeNode.querySelector(':scope > [role="treeitem"]')
}

/**
 * Recursively expand all directory children of the focused node.
 * Uses the DOM to find nested directory labels and clicks each closed one.
 * We must wait for Vue to re-render after each level since lazy-loaded
 * directories may not have children in the DOM until loaded.
 */
async function expandAll(el) {
    if (el.dataset.type !== 'directory') return

    // Open this directory if closed
    if (el.dataset.open === 'false') {
        el.click()
        // Wait for Vue to render the children
        await nextTick()
        // Additional delay for lazy-loaded directories
        await new Promise(resolve => setTimeout(resolve, 50))
    }

    // Find all direct child directory labels (that are now visible)
    const treeNode = el.closest('.file-tree-node')
    if (!treeNode) return
    const childrenContainer = treeNode.querySelector(':scope > .node-children')
    if (!childrenContainer) return

    const childDirLabels = childrenContainer.querySelectorAll(
        ':scope > .file-tree-node > [role="treeitem"][data-type="directory"]'
    )

    for (const childLabel of childDirLabels) {
        await expandAll(childLabel)
    }
}

function handleSearchKeydown(event) {
    if (event.key === 'Escape') {
        if (searchQuery.value) {
            clearSearch()
        }
        event.preventDefault()
        return
    }
    if (event.key === 'ArrowDown') {
        // Move focus from search input to the first tree item
        event.preventDefault()
        const items = getVisibleItems()
        if (items.length) {
            focusItem(items[0])
        }
        return
    }
    if (event.key === 'PageDown') {
        // Jump into the tree as if starting from before the first item
        event.preventDefault()
        const items = getVisibleItems()
        if (items.length) {
            const target = Math.min(PAGE_SIZE - 1, items.length - 1)
            focusItem(items[target])
        }
        return
    }
    if (event.key === 'PageUp') {
        // Already at the top — stay in search input
        event.preventDefault()
    }
}

/**
 * Main keyboard handler for the tree container.
 * Handles: ArrowDown, ArrowUp, ArrowRight, ArrowLeft, Home, End,
 * Enter, Space, +, -, *, PageUp, PageDown, Escape.
 */
function handleTreeKeydown(event) {
    const items = getVisibleItems()
    if (!items.length) return

    let index = getFocusedIndex(items)

    switch (event.key) {
        case 'ArrowDown': {
            event.preventDefault()
            const next = Math.min(index + 1, items.length - 1)
            if (index === -1) {
                // No focus yet — focus first item
                focusItem(items[0])
            } else {
                focusItem(items[next])
            }
            break
        }

        case 'ArrowUp': {
            event.preventDefault()
            if (index <= 0) {
                // Already on first item (or no focus) → go back to search input
                focusedPath.value = null
                focusSearchInput()
            } else {
                focusItem(items[index - 1])
            }
            break
        }

        case 'ArrowRight': {
            event.preventDefault()
            if (index < 0) break
            const el = items[index]
            if (el.dataset.type === 'directory') {
                if (el.dataset.open === 'false') {
                    // Closed directory → open it
                    openDirectory(el)
                } else {
                    // Open directory → move to first child
                    const refreshed = getVisibleItems()
                    const newIndex = refreshed.findIndex(e => e.dataset.path === el.dataset.path)
                    if (newIndex >= 0 && newIndex + 1 < refreshed.length) {
                        focusItem(refreshed[newIndex + 1])
                    }
                }
            }
            break
        }

        case 'ArrowLeft': {
            event.preventDefault()
            if (index < 0) break
            const el = items[index]
            if (el.dataset.type === 'directory' && el.dataset.open === 'true') {
                // Open directory → close it
                closeDirectory(el)
            } else {
                // Closed directory or file → move to parent
                const parentEl = findParentDirectoryEl(el)
                if (parentEl && parentEl.dataset.path) {
                    focusedPath.value = parentEl.dataset.path
                    nextTick(() => {
                        scrollItemIntoTree(parentEl)
                        parentEl.focus({ preventScroll: true })
                    })
                }
            }
            break
        }

        case 'Home': {
            event.preventDefault()
            focusItem(items[0])
            break
        }

        case 'End': {
            event.preventDefault()
            focusItem(items[items.length - 1])
            break
        }

        case 'Enter': {
            // Cmd/Ctrl+Enter is a no-op (does not activate the focused item).
            if (event.metaKey || event.ctrlKey) break
            event.preventDefault()
            if (index >= 0) {
                activateFocused(items, index)
            }
            break
        }

        case ' ': {
            event.preventDefault()
            if (index >= 0) {
                activateFocused(items, index)
            }
            break
        }

        case '+':
        case '=': {
            // + (normal or numpad): open focused directory
            event.preventDefault()
            if (index >= 0) {
                openDirectory(items[index])
            }
            break
        }

        case '-': {
            // - (normal or numpad): close focused directory
            event.preventDefault()
            if (index >= 0) {
                closeDirectory(items[index])
            }
            break
        }

        case '*': {
            event.preventDefault()
            if (index >= 0) {
                expandAll(items[index])
            }
            break
        }

        case 'PageDown': {
            event.preventDefault()
            if (index === -1) {
                focusItem(items[0])
            } else {
                const target = Math.min(index + PAGE_SIZE, items.length - 1)
                focusItem(items[target])
            }
            break
        }

        case 'PageUp': {
            event.preventDefault()
            if (index <= 0) {
                // Already on first item (or no focus) → go back to search input
                focusedPath.value = null
                focusSearchInput()
            } else {
                const target = Math.max(index - PAGE_SIZE, 0)
                focusItem(items[target])
            }
            break
        }

        case 'Escape': {
            event.preventDefault()
            focusedPath.value = null
            focusSearchInput()
            break
        }

        default:
            // Letter navigation: jump to next same-level sibling whose display
            // name starts with the typed letter (case-insensitive, wraps around).
            if (
                event.key.length === 1
                && /[a-z]/i.test(event.key)
                && !event.ctrlKey && !event.metaKey && !event.altKey
            ) {
                if (index < 0) return

                const el = items[index]
                const letter = event.key.toLowerCase()

                // Find the parent container that holds siblings at this level
                const treeNode = el.closest('.file-tree-node')
                if (!treeNode) return
                const parentContainer = treeNode.parentElement
                if (!parentContainer) return

                // Collect visible siblings (direct children of the same parent)
                const siblings = Array.from(
                    parentContainer.querySelectorAll(
                        ':scope > .file-tree-node > [role="treeitem"]'
                    )
                ).filter(sibling => sibling.offsetHeight > 0)

                const siblingIndex = siblings.indexOf(el)
                if (siblingIndex === -1) return

                const nameStartsWith = (sibling) => {
                    const name = sibling.querySelector('.node-name')?.textContent || ''
                    return name.toLowerCase().startsWith(letter)
                }

                // Search after current position first
                for (let i = siblingIndex + 1; i < siblings.length; i++) {
                    if (nameStartsWith(siblings[i])) {
                        event.preventDefault()
                        focusItem(siblings[i])
                        return
                    }
                }

                // Wrap around: search from beginning up to current position
                for (let i = 0; i < siblingIndex; i++) {
                    if (nameStartsWith(siblings[i])) {
                        event.preventDefault()
                        focusItem(siblings[i])
                        return
                    }
                }
            }
            return  // Don't prevent default for unhandled keys
    }
}

// ─── Scroll-to-path ─────────────────────────────────────────────────────────

/**
 * Set of absolute paths that should be forced open in FileTree.
 * Populated by scrollToPath(), consumed by FileTree components.
 * Uses shallowRef + reassignment for reactivity.
 */
const revealedPaths = shallowRef(new Set())

/**
 * Unified function: reveal a path in the tree, scroll to it, and focus it.
 *
 * Takes a path (absolute filesystem path in files mode, tree-relative in git mode).
 * Computes the relative path from the current root, then walks the tree data
 * node by node. For directories with `loaded: false`, calls lazyLoadFn to fetch
 * children (skipped when lazyLoadFn is null, e.g. in git mode where all data
 * is already loaded).
 *
 * Handles compact folders: follows single-child directory chains.
 *
 * @param {string} absolutePath — the path to scroll to
 * @returns {boolean} true if the target was found and revealed, false otherwise
 */
async function scrollToPath(absolutePath) {
    if (!absolutePath || !props.tree || !props.rootPath) return false

    // Compute relative path from the root
    const prefix = props.rootPath + '/'
    let relativePath
    if (absolutePath === props.rootPath) {
        // Target is the root itself — just scroll to it
        revealedPaths.value = new Set([props.rootPath])
        focusedPath.value = props.rootPath
        await nextTick()
        const el = treeContainerRef.value?.querySelector(`[data-path="${CSS.escape(props.rootPath)}"]`)
        if (el) {
            scrollItemIntoTree(el)
            el.focus({ preventScroll: true })
        }
        return true
    } else if (absolutePath.startsWith(prefix)) {
        relativePath = absolutePath.slice(prefix.length)
    } else {
        return false  // Path is outside the current root
    }

    const segments = relativePath.split('/')
    const pathsToOpen = [props.rootPath]  // Root always needs to be open
    let currentNode = props.tree
    let currentAbsPath = props.rootPath
    let i = 0

    while (i < segments.length) {
        // If this directory isn't loaded yet, try to lazy-load it
        if (currentNode.loaded === false) {
            if (!props.lazyLoadFn) return false  // Can't lazy-load in this mode
            try {
                const data = await props.lazyLoadFn(currentAbsPath)
                if (!data) return false
                currentNode.children = data.children || []
                currentNode.loaded = true
            } catch {
                return false
            }
        }

        const segment = segments[i]
        const isLast = i === segments.length - 1

        if (isLast) {
            // Last segment: check if the file/dir exists as a child
            const found = currentNode.children?.some(child => child.name === segment)
            if (!found) return false
            break
        }

        // Find the child directory matching this segment
        const child = currentNode.children?.find(
            c => c.name === segment && c.type === 'directory'
        )
        if (!child) return false

        currentAbsPath = `${currentAbsPath}/${segment}`
        currentNode = child
        i++

        // Follow compact folder chain: if this directory has exactly one child
        // that is also a directory (and is loaded), it will be compacted by FileTree.
        // We need to follow the chain and consume the corresponding path segments,
        // so that revealedPaths contains the *effective* path (end of the chain).
        while (
            currentNode.loaded !== false &&
            currentNode.children?.length === 1 &&
            currentNode.children[0].type === 'directory' &&
            i < segments.length - 1 &&  // Don't consume the file segment
            currentNode.children[0].name === segments[i]
        ) {
            currentAbsPath = `${currentAbsPath}/${segments[i]}`
            currentNode = currentNode.children[0]
            i++
        }

        // This is the effective path after compaction — add it to pathsToOpen
        pathsToOpen.push(currentAbsPath)
    }

    // Set revealedPaths — FileTree components will react and open themselves
    revealedPaths.value = new Set(pathsToOpen)

    // Update focused path to the target
    focusedPath.value = absolutePath

    // Wait for the DOM to render the newly opened directories, then scroll to target
    await nextTick()
    // Extra tick: FileTree watchers may trigger additional renders (compact folders, etc.)
    await nextTick()
    const targetEl = treeContainerRef.value?.querySelector(`[data-path="${CSS.escape(absolutePath)}"]`)
    if (targetEl) {
        scrollItemIntoTree(targetEl)
        targetEl.focus({ preventScroll: true })
    }

    return true
}

// ─── Tree node lookup (for local mutations) ────────────────────────────────

/**
 * Walk the tree data to find a node by its absolute path.
 * Returns { node, parent, index } or null if not found.
 */
function findNodeInTree(absolutePath) {
    if (!props.tree || !props.rootPath) return null
    if (absolutePath === props.rootPath) {
        return { node: props.tree, parent: null, index: -1 }
    }
    const prefix = props.rootPath + '/'
    if (!absolutePath.startsWith(prefix)) return null
    const segments = absolutePath.slice(prefix.length).split('/')

    let current = props.tree
    for (let i = 0; i < segments.length; i++) {
        if (!current.children) return null
        const idx = current.children.findIndex(c => c.name === segments[i])
        if (idx === -1) return null
        if (i === segments.length - 1) {
            return { node: current.children[idx], parent: current, index: idx }
        }
        current = current.children[idx]
    }
    return null
}

// ─── Context menu ───────────────────────────────────────────────────────────

const contextMenu = ref({
    visible: false,
    x: 0,
    y: 0,
    path: '',
    name: '',
    type: 'file',
    writable: false,
    writableLoading: false,
    stagedStatus: null,
    unstagedStatus: null,
    status: null,
})

const renameDialogRef = ref(null)
const deleteDialogRef = ref(null)
const createDialogRef = ref(null)
const moveDialogRef = ref(null)

const apiPrefix = computed(() => {
    if (!props.projectId) return '/api'
    if (props.isDraft || !props.sessionId) return `/api/projects/${props.projectId}`
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

async function onContextMenu(data) {
    contextMenu.value = {
        visible: true,
        x: data.x,
        y: data.y,
        path: data.path,
        name: data.name,
        type: data.type,
        writable: false,
        writableLoading: props.contextMenuMode === 'files',
        stagedStatus: data.stagedStatus || null,
        unstagedStatus: data.unstagedStatus || null,
        status: data.status || null,
    }

    // In files mode, check writable status via API. In git modes, skip.
    if (props.contextMenuMode !== 'files') return

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/?path=${encodeURIComponent(data.path)}&meta_only=true`
        )
        if (res.ok) {
            const meta = await res.json()
            if (contextMenu.value.path === data.path && contextMenu.value.visible) {
                contextMenu.value.writable = meta.writable
            }
        }
    } catch {
        // leave writable as false
    } finally {
        if (contextMenu.value.path === data.path && contextMenu.value.visible) {
            contextMenu.value.writableLoading = false
        }
    }
}

function closeContextMenu() {
    contextMenu.value.visible = false
}

function computeRelativePath(absolutePath) {
    if (!props.rootPath) return absolutePath
    const prefix = props.rootPath + '/'
    return absolutePath.startsWith(prefix)
        ? absolutePath.slice(prefix.length)
        : absolutePath
}

function computeFullPath(absolutePath) {
    if (props.gitDirectory) {
        const relativePath = computeRelativePath(absolutePath)
        return `${props.gitDirectory}/${relativePath}`
    }
    return absolutePath
}

/**
 * Download the file the context menu points at.
 *
 * Files mode serves it straight from the raw endpoint, which the panel can
 * address on its own. Git modes go up to the owner instead: the bytes depend on
 * the revision being viewed, which only GitPanel knows.
 */
function handleDownload() {
    if (props.contextMenuMode !== 'files') {
        emit('download', { path: computeRelativePath(contextMenu.value.path) })
        return
    }
    triggerDownload(buildFileDownloadUrl({
        filePath: contextMenu.value.path,
        projectId: props.projectId,
        apiPrefix: apiPrefix.value,
        root: props.rootRestriction || props.rootPath,
    }))
}

/** Download the unified patch. Offered in git modes only (see the menu). */
function handleDownloadDiff() {
    emit('download-diff', { path: computeRelativePath(contextMenu.value.path) })
}

function handleGitStage() {
    emit('git-stage', { path: computeRelativePath(contextMenu.value.path) })
}

function handleGitUnstage() {
    emit('git-unstage', { path: computeRelativePath(contextMenu.value.path) })
}

function handleGitDiscard() {
    emit('git-discard', { path: computeRelativePath(contextMenu.value.path) })
}

function handleRename() {
    renameDialogRef.value?.open({
        path: contextMenu.value.path,
        name: contextMenu.value.name,
        type: contextMenu.value.type,
    })
}

function handleDelete() {
    deleteDialogRef.value?.open({
        path: computeFullPath(contextMenu.value.path),
        name: contextMenu.value.name,
        type: contextMenu.value.type,
    })
}

function handleCopyName() {
    navigator.clipboard.writeText(contextMenu.value.name)
}

function handleCopyRelativePath() {
    navigator.clipboard.writeText(computeRelativePath(contextMenu.value.path))
}

function handleCopyFullPath() {
    navigator.clipboard.writeText(computeFullPath(contextMenu.value.path))
}

function handleMove() {
    moveDialogRef.value?.open({
        path: contextMenu.value.path,
        name: contextMenu.value.name,
        type: contextMenu.value.type,
        treeRootPath: props.rootPath,
    })
}

function onMoved({ oldPath, newPath }) {
    const wasSelected = selectedAbsPath.value === oldPath || selectedAbsPath.value?.startsWith(oldPath + '/')
    if (wasSelected) {
        onFileSelect(newPath)
    }
    emit('refresh', { scrollTo: newPath })
}

function handleCreateFile() {
    createDialogRef.value?.open({
        path: contextMenu.value.path,
        createKind: 'file',
    })
}

function handleCreateFolder() {
    createDialogRef.value?.open({
        path: contextMenu.value.path,
        createKind: 'directory',
    })
}

function onCreated({ newPath }) {
    emit('refresh', { scrollTo: newPath })
}

function onRenamed({ oldPath, newPath, newName }) {
    const found = findNodeInTree(oldPath)
    if (found && found.parent) {
        found.node.name = newName
    }
    if (selectedAbsPath.value === oldPath) {
        onFileSelect(newPath)
    }
}

function onDeleted({ path }) {
    if (selectedAbsPath.value === path || selectedAbsPath.value?.startsWith(path + '/')) {
        selectedFile.value = null
    }
    const found = findNodeInTree(path)
    if (found && found.parent) {
        found.parent.children.splice(found.index, 1)
    } else {
        emit('refresh')
    }
}

// ─── Expose methods and state for parent access ─────────────────────────────

defineExpose({
    scrollToPath,
    clearSearch,
    focusSearchInput,
    rerunSearch,
    onFileSelect,
    onNodeFocus,
    selectedAbsPath,
    selectedFile,
    autoOpen,
    isSearching,
    searchQuery,
    fileTreeOpen,
    toggleFileTree,
    closeFileTree,
})
</script>

<template>
    <div class="file-tree-panel" :class="{ 'file-tree-panel--mobile': isMobile }">
        <!-- Mobile header: shows selected file path, click to open overlay. For a
             bookmarkable artifact it carries the bookmark name (in the label) and
             a bookmark toggle on the right (kept outside the header button). -->
        <div v-if="isMobile" class="files-panel-header-row">
            <button
                class="files-panel-header"
                :class="{ open: fileTreeOpen }"
                @click="toggleFileTree"
            >
                <span class="files-panel-header-label" :id="selectedFileId">
                    {{ effectiveSelectionLabel || headerPlaceholder }}<span v-if="headerArtifactBookmark" class="files-panel-header-artifact-bookmark-name"> ({{ headerArtifactBookmark.name }})</span>
                </span>
                <AppTooltip v-if="effectiveSelectionTooltip" :for="selectedFileId">{{ effectiveSelectionTooltip }}<template v-if="headerArtifactBookmark"> ({{ headerArtifactBookmark.name }})</template></AppTooltip>
                <GitStatusBadge v-if="selectedFileNode" :node="selectedFileNode" class="mobile-header-badge" />
                <wa-icon
                    class="chevron"
                    :name="fileTreeOpen ? 'chevron-up' : 'chevron-down'"
                ></wa-icon>
            </button>
            <ArtifactBookmarkButton
                v-if="headerArtifactBookmarkable"
                class="files-panel-header-artifact-bookmark-btn"
                :session-id="effectiveArtifactSessionId"
                :relative-path="effectiveArtifactRelativePath"
                :file-abs-path="externalArtifactAbsPath || selectedAbsPath"
            />
        </div>

        <!-- File tree content: inline on desktop, overlay on mobile.
             Use v-show (not v-if) so the tree DOM, search state, scroll
             position and focus are preserved when the overlay is closed. -->
        <div
            v-show="!isMobile || fileTreeOpen"
            class="file-tree-panel-content"
        >
            <!-- Search input + options (only shown when tree is loaded) -->
            <div v-if="(tree && searchFn) || hasExtraTree" class="files-search">
                <wa-dropdown
                    placement="bottom-start"
                    class="files-options-dropdown"
                    @wa-select="handleOptionsSelect"
                >
                    <wa-button
                        :id="fileOptionsButtonId"
                        slot="trigger"
                        variant="neutral"
                        appearance="filled-outlined"
                        size="small"
                    >
                        <wa-icon name="sliders"></wa-icon>
                    </wa-button>

                    <!-- Parent-specific options (injected via slot) -->
                    <slot name="options-before" />

                    <!-- Shared options (hidden in popup contexts) -->
                    <template v-if="showSharedOptions">
                        <wa-dropdown-item
                            type="checkbox"
                            value="auto-open"
                            :checked="autoOpen"
                        >
                            Auto-open
                        </wa-dropdown-item>
                        <wa-divider></wa-divider>
                        <wa-dropdown-item
                            v-if="selectedFile"
                            value="reveal-in-tree"
                        >
                            <wa-icon slot="icon" name="crosshairs"></wa-icon>
                            <div>Scroll to selected file</div>
                            <div class="reveal-path">{{ selectedFile }}</div>
                        </wa-dropdown-item>
                    </template>
                    <wa-dropdown-item
                        v-if="showRefresh"
                        value="refresh"
                    >
                        <wa-icon slot="icon" name="arrows-rotate"></wa-icon>
                        Refresh
                    </wa-dropdown-item>
                </wa-dropdown>
                <AppTooltip :for="fileOptionsButtonId">Files options</AppTooltip>
                <wa-input
                    ref="searchInputRef"
                    :value="searchQuery"
                    :placeholder="searchPlaceholder"
                    size="small"
                    with-clear
                    class="files-search-input"
                    @input="onSearchInput"
                    @keydown="handleSearchKeydown"
                    @wa-clear="clearSearch"
                >
                    <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                </wa-input>
            </div>

            <!-- Placeholder states -->
            <div v-if="isSearching && !hasExtraTree && !searchTree?.children?.length && !searchResponded" class="panel-placeholder">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="isSearching && !hasExtraTree && !searchTree?.children?.length && searchResponded" class="panel-placeholder">
                No matches
            </div>
            <div v-else-if="!isSearching && loading" class="panel-placeholder">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="!isSearching && error" class="panel-placeholder panel-error">
                {{ error }}
            </div>
            <div v-else-if="!isSearching && !rootPath && !hasExtraTree" class="panel-placeholder">
                {{ mode === 'git' ? 'No changes' : 'No directory' }}
            </div>

            <!-- Tree (same structure for both browse and search) -->
            <template v-else-if="displayTree || hasExtraTree">
                <div
                    ref="treeContainerRef"
                    class="tree-container"
                    role="tree"
                    tabindex="0"
                    @keydown="handleTreeKeydown"
                >
                    <FileTree
                        v-if="displayTree && (!isSearching || searchTree?.children?.length)"
                        :node="displayTree"
                        :path="rootPath"
                        :root-label="rootLabel"
                        :project-id="projectId"
                        :session-id="sessionId"
                        :is-root="true"
                        :all-open="isSearching"
                        :focused-path="focusedPath"
                        :extra-query="extraQuery"
                        :revealed-paths="revealedPaths"
                        :selected-path="selectedAbsPath"
                        :is-draft="isDraft"
                        :mode="mode"
                        :directories-only="directoriesOnly"
                        :compact-folders="compactFolders"
                        :lazy-load-fn="lazyLoadFn"
                        :commented-paths="commentedPaths"
                        @select="onFileSelect"
                        @focus="onNodeFocus"
                        @context-menu="enableContextMenu ? onContextMenu($event) : null"
                    />
                    <slot
                        name="tree-after"
                        :search-query="searchQuery"
                        :is-searching="isSearching"
                    />
                </div>
                <div v-if="isSearching && searchTruncated" class="search-truncated">
                    {{ searchTotal }} matches — showing first {{ searchTree.children.length }}
                </div>
            </template>
        </div>

        <!-- Context menu + dialogs (disabled in nested contexts like move dialog to avoid infinite recursion) -->
        <template v-if="enableContextMenu">
            <FileTreeContextMenu
                :visible="contextMenu.visible"
                :x="contextMenu.x"
                :y="contextMenu.y"
                :node-name="contextMenu.name"
                :node-type="contextMenu.type"
                :relative-path="computeRelativePath(contextMenu.path)"
                :full-path="computeFullPath(contextMenu.path)"
                :writable="contextMenu.writable"
                :writable-loading="contextMenu.writableLoading"
                :mode="contextMenuMode"
                :staged-status="contextMenu.stagedStatus"
                :unstaged-status="contextMenu.unstagedStatus"
                :status="contextMenu.status"
                @close="closeContextMenu"
                @create-file="handleCreateFile"
                @create-folder="handleCreateFolder"
                @rename="handleRename"
                @move="handleMove"
                @delete="handleDelete"
                @copy-name="handleCopyName"
                @copy-relative-path="handleCopyRelativePath"
                @copy-full-path="handleCopyFullPath"
                @git-stage="handleGitStage"
                @git-unstage="handleGitUnstage"
                @git-discard="handleGitDiscard"
                @download="handleDownload"
                @download-diff="handleDownloadDiff"
            />
            <FileRenameDialog
                v-if="apiPrefix"
                ref="renameDialogRef"
                :api-prefix="apiPrefix"
                @renamed="onRenamed"
            />
            <FileDeleteDialog
                v-if="apiPrefix"
                ref="deleteDialogRef"
                :api-prefix="apiPrefix"
                @deleted="onDeleted"
            />
            <FileCreateDialog
                v-if="apiPrefix"
                ref="createDialogRef"
                :api-prefix="apiPrefix"
                @created="onCreated"
            />
            <FileMoveDialog
                v-if="apiPrefix"
                ref="moveDialogRef"
                :api-prefix="apiPrefix"
                :project-id="projectId"
                :session-id="sessionId"
                :is-draft="isDraft"
                @moved="onMoved"
            />
        </template>
    </div>
</template>

<style scoped>
.file-tree-panel {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.file-tree-panel-content {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Search input + options toolbar
   ═══════════════════════════════════════════════════════════════════════════ */

.files-search {
    padding: var(--wa-space-2xs);
    flex-shrink: 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.files-search-input {
    flex: 1;
    min-width: 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tree container (keyboard-navigable wrapper)
   ═══════════════════════════════════════════════════════════════════════════ */

.tree-container {
    flex: 1;
    overflow: auto;
    outline: none;
    display: flex;
    flex-direction: column;
    align-items: stretch;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Placeholder states
   ═══════════════════════════════════════════════════════════════════════════ */

.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.panel-error {
    color: var(--wa-color-danger-fill-loud);
    padding: var(--wa-space-s);
    text-align: center;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Miscellaneous
   ═══════════════════════════════════════════════════════════════════════════ */

.reveal-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.search-truncated {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    text-align: center;
    border-top: 1px solid var(--wa-color-surface-border);
}

/* ═══════════════════════════════════════════════════════════════════════════
   Mobile layout: header + overlay
   ═══════════════════════════════════════════════════════════════════════════ */

.file-tree-panel--mobile {
    /* In mobile mode, the panel is no longer a flex column filling the
       split-panel slot. It must be position: relative so the overlay
       can use position: absolute with inset: 0. But since the panel
       itself is inside an absolute/flex container from the parent,
       we keep height: 100% and let the overlay cover the parent's
       content area via the parent's positioning context. */
    height: auto;
    overflow: visible;
}

/* ----- Mobile header (click to open file tree overlay) ----- */

/* Mobile header row: the full-width header button + (for a bookmarkable
   artifact) the bookmark toggle on the right. Carries the chrome the button
   used to own (border, background, stacking) so the bottom border spans both. */
.files-panel-header-row {
    display: flex;
    align-items: center;
    background: var(--wa-color-surface-default);
    border-bottom: var(--divider-size) solid var(--wa-color-surface-border);
    /* Stay above the overlay (z-index: 10) */
    position: relative;
    z-index: 11;
}

.files-panel-header-row > .files-panel-header {
    flex: 1;
    min-width: 0;
    width: auto;
    border-bottom: none;
    position: static;
    z-index: auto;
}

.files-panel-header-artifact-bookmark-btn {
    flex-shrink: 0;
    align-self: center;
    margin-right: var(--wa-space-2xs);
}

.files-panel-header-artifact-bookmark-name {
    color: var(--wa-color-text-quiet);
}

.files-panel-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: calc(1px + var(--wa-space-2xs)) var(--wa-space-s);
    background: var(--wa-color-surface-default);
    border: none;
    border-bottom: 1px solid var(--wa-color-surface-border);
    cursor: pointer;
    font-family: inherit;
    font-size: var(--wa-font-size-s);
    font-weight: normal;
    color: inherit;
    text-align: left;
    transition: background-color 0.15s ease;
    box-shadow: none;
    margin: 0;
    translate: none !important;
    transform: none !important;
    justify-content: start;
    flex-wrap: wrap;
    height: auto;
    flex-shrink: 0;
    /* Stay above the overlay (z-index: 10) */
    position: relative;
    z-index: 11;
}

.files-panel-header:hover {
    background-color: var(--wa-color-surface-alt);
}

.files-panel-header.open {
    background-color: var(--wa-color-surface-alt);
}

.files-panel-header-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
    /* Truncate the START of the path so the file name (at the end, what matters
       most) stays visible. An rtl base direction moves the ellipsis to the left;
       text-align: left keeps short, non-truncated paths left-aligned as usual.
       The path stays a single LTR run, so it (and any " (bookmark)" suffix) reads
       normally. */
    direction: rtl;
    text-align: left;
}

.mobile-header-badge {
    flex-shrink: 0;
}

.files-panel-header .chevron {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    transition: transform 0.2s ease;
}

/* ----- Mobile overlay (same pattern as .gitlog-overlay in GitPanel) ----- */

.file-tree-panel--mobile > .file-tree-panel-content {
    position: absolute;
    inset: 0;
    top: 2rem;
    z-index: 10;
    overflow: hidden;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
}
</style>
