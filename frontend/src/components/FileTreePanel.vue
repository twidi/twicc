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
 *   active: whether the panel is currently active (for auto-focus)
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

import { ref, computed, watch, nextTick, shallowRef } from 'vue'
import FileTree from './FileTree.vue'

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
    active: {
        type: Boolean,
        default: false,
    },
    mode: {
        type: String,
        default: 'files',  // 'files' | 'git'
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
})

const emit = defineEmits(['file-select', 'refresh', 'option-select'])

// ─── Mobile overlay state ────────────────────────────────────────────────────

const fileTreeOpen = ref(false)

function toggleFileTree() {
    fileTreeOpen.value = !fileTreeOpen.value
}

/**
 * Placeholder text for the mobile header when no file is selected.
 * Mirrors the placeholder states from the template.
 */
const headerPlaceholder = computed(() => {
    if (isSearching.value && !searchTree.value?.children?.length && searchResponded.value) {
        return 'No matches'
    }
    if (!props.rootPath) {
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
    if (!props.searchFn || !props.rootPath) return

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

/**
 * Focus the search input field.
 * wa-input is a web component, so we need to call .focus() on it directly.
 */
function focusSearchInput() {
    try {
        searchInputRef.value?.focus()
    } catch {
        // WaInput.focus() can throw if the web component's internal <input>
        // element isn't ready yet (e.g. shadow DOM not fully initialized).
        // This is a benign race condition — silently ignore.
    }
}

// Auto-focus search input when the panel becomes active
watch(
    () => props.active,
    (active) => {
        if (active) {
            nextTick(() => focusSearchInput())
        }
    }
)

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
 * Set focus to a specific item element: update focusedPath, scroll into view,
 * and move DOM focus.
 */
function focusItem(el) {
    if (!el) return
    focusedPath.value = el.dataset.path
    nextTick(() => {
        el.scrollIntoView({ block: 'nearest' })
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
                        parentEl.scrollIntoView({ block: 'nearest' })
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

        case 'Enter':
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
            el.scrollIntoView({ block: 'nearest' })
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
        targetEl.scrollIntoView({ block: 'nearest' })
        targetEl.focus({ preventScroll: true })
    }

    return true
}

// ─── Expose methods and state for parent access ─────────────────────────────

defineExpose({
    scrollToPath,
    clearSearch,
    focusSearchInput,
    rerunSearch,
    onFileSelect,
    selectedAbsPath,
    selectedFile,
    autoOpen,
    isSearching,
    searchQuery,
    fileTreeOpen,
    toggleFileTree,
})
</script>

<template>
    <div class="file-tree-panel" :class="{ 'file-tree-panel--mobile': isMobile }">
        <!-- Mobile header: shows selected file path, click to open overlay -->
        <button
            v-if="isMobile"
            class="files-panel-header"
            :class="{ open: fileTreeOpen }"
            @click="toggleFileTree"
        >
            <span class="files-panel-header-label" :title="selectedFile || undefined">
                {{ selectedFile || headerPlaceholder }}
            </span>
            <wa-icon
                class="chevron"
                :name="fileTreeOpen ? 'chevron-up' : 'chevron-down'"
            ></wa-icon>
        </button>

        <!-- File tree content: inline on desktop, overlay on mobile.
             Use v-show (not v-if) so the tree DOM, search state, scroll
             position and focus are preserved when the overlay is closed. -->
        <div
            v-show="!isMobile || fileTreeOpen"
            class="file-tree-panel-content"
        >
            <!-- Search input + options (only shown when tree is loaded) -->
            <div v-if="tree" class="files-search">
                <wa-dropdown
                    placement="bottom-start"
                    class="files-options-dropdown"
                    @wa-select="handleOptionsSelect"
                >
                    <wa-button
                        slot="trigger"
                        variant="neutral"
                        appearance="filled-outlined"
                        size="small"
                    >
                        <wa-icon name="sliders"></wa-icon>
                    </wa-button>

                    <!-- Parent-specific options (injected via slot) -->
                    <slot name="options-before" />

                    <!-- Shared options -->
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
                    <wa-dropdown-item
                        v-if="showRefresh"
                        value="refresh"
                    >
                        <wa-icon slot="icon" name="arrows-rotate"></wa-icon>
                        Refresh
                    </wa-dropdown-item>
                </wa-dropdown>
                <wa-input
                    ref="searchInputRef"
                    :value="searchQuery"
                    placeholder="Filter files..."
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
            <div v-if="isSearching && !searchTree?.children?.length && !searchResponded" class="panel-placeholder">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="isSearching && !searchTree?.children?.length && searchResponded" class="panel-placeholder">
                No matches
            </div>
            <div v-else-if="!isSearching && loading" class="panel-placeholder">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="!isSearching && error" class="panel-placeholder panel-error">
                {{ error }}
            </div>
            <div v-else-if="!isSearching && !rootPath" class="panel-placeholder">
                {{ mode === 'git' ? 'No changes' : 'No directory' }}
            </div>

            <!-- Tree (same structure for both browse and search) -->
            <template v-else-if="displayTree">
                <div
                    ref="treeContainerRef"
                    class="tree-container"
                    role="tree"
                    tabindex="0"
                    @keydown="handleTreeKeydown"
                >
                    <FileTree
                        :node="displayTree"
                        :path="rootPath"
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
                        @select="onFileSelect"
                        @focus="onNodeFocus"
                    />
                </div>
                <div v-if="isSearching && searchTruncated" class="search-truncated">
                    {{ searchTotal }} matches — showing first {{ searchTree.children.length }}
                </div>
            </template>
        </div>
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

.files-panel-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: var(--wa-space-xs) var(--wa-space-s);
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
    top: 3rem;
    z-index: 10;
    overflow: hidden;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
}
</style>
