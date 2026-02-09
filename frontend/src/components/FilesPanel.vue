<script setup>
import { ref, computed, watch, nextTick, shallowRef } from 'vue'
import { apiFetch } from '../utils/api'
import FileTree from './FileTree.vue'
import FileContentViewer from './FileContentViewer.vue'

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

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

// Lazy init: defer all loading until the tab becomes active for the first time
const started = ref(false)

// ─── Root directory selection ────────────────────────────────────────────────

/**
 * Available root directories.
 * Each entry: { key, label, path }
 *
 * If both git root and project directory exist and point to the same path,
 * they are merged into a single entry labelled "Project directory (git root)".
 * Otherwise, git root comes first (default), then project directory.
 */
const availableRoots = computed(() => {
    const git = props.gitDirectory
    const project = props.projectDirectory

    if (git && project && git === project) {
        // Same path — merge into one entry
        return [{ key: 'project', label: 'Project directory (git root)', path: project }]
    }

    const roots = []
    if (git) {
        roots.push({ key: 'git', label: 'Git root', path: git })
    }
    if (project && project !== git) {
        roots.push({ key: 'project', label: 'Project directory', path: project })
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
const autoOpen = ref(false)

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
 * Set of absolute paths that should be forced open in FileTree.
 * Populated by scrollToPath(), consumed by FileTree components.
 * Uses shallowRef + reassignment for reactivity.
 */
const revealedPaths = shallowRef(new Set())

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
    revealedPaths.value = new Set()  // Clear any previous reveal state

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

// ─── Search state ────────────────────────────────────────────────────────────

const searchQuery = ref('')
const searchTree = ref(null)
const searchTotal = ref(0)
const searchTruncated = ref(false)
const searchLoading = ref(false)
const isSearching = ref(false)
const searchResponded = ref(false)  // true once the first search response has arrived

// Lazy init: start loading only when the tab becomes active for the first time
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
        if (isSearching.value && searchQuery.value.trim()) {
            doSearch(searchQuery.value.trim())
        } else {
            clearSearch(false)  // No reveal — tree is being re-fetched
        }
    },
    { immediate: true }
)

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
        doSearch(query.trim())
    }, 250)
}

async function doSearch(query) {
    if (!props.projectId || !directory.value) return

    searchLoading.value = true
    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-search/?path=${encodeURIComponent(directory.value)}&q=${encodeURIComponent(query)}${optionsQuery()}`
        )
        if (res.ok) {
            const data = await res.json()
            // Only update if query still matches (avoid stale results)
            if (searchQuery.value.trim() === query) {
                searchTree.value = data
                searchTotal.value = data.total
                searchTruncated.value = data.truncated
                searchResponded.value = true
            }
        }
    } catch {
        // Silently fail — keep previous results
    } finally {
        searchLoading.value = false
    }
}

function handleOptionsSelect(event) {
    const value = event.detail?.item?.value
    if (value === 'show-hidden') {
        showHidden.value = !showHidden.value
    } else if (value === 'show-ignored') {
        showIgnored.value = !showIgnored.value
    } else if (value?.startsWith('root:')) {
        handleRootSelect(value.slice(5))
    } else if (value === 'auto-open') {
        autoOpen.value = !autoOpen.value
    } else if (value === 'reveal-in-tree') {
        if (selectedAbsPath.value) {
            scrollToPath(selectedAbsPath.value)
        }
    } else if (value === 'refresh') {
        refresh()
    }
}

/**
 * Refresh: re-fetch the tree from the root, and re-run the search if active.
 * After refresh, scrolls back to the previously selected file. If it no longer
 * exists, clears the selection and scrolls to the root.
 */
async function refresh() {
    const fileToScroll = selectedAbsPath.value

    await fetchTree(props.projectId, props.sessionId, directory.value)

    if (isSearching.value && searchQuery.value.trim()) {
        doSearch(searchQuery.value.trim())
    }

    // Scroll to previously selected file in the refreshed tree
    if (fileToScroll && tree.value) {
        const found = await scrollToPath(fileToScroll)
        if (!found) {
            // File no longer exists — clear selection and scroll to root
            selectedFile.value = null
            await scrollToPath(directory.value)
        }
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
    if (pathToScroll && tree.value) {
        scrollToPath(pathToScroll)
    }
}

// ─── Scroll-to-path ─────────────────────────────────────────────────────────

/**
 * Unified function: reveal a path in the tree, scroll to it, and focus it.
 *
 * Takes an *absolute* path (e.g. "/home/user/project/src/utils/file.txt").
 * Computes the relative path from the current root directory, then walks the
 * tree data node by node along the path segments. For each directory that has
 * `loaded: false`, calls the directory-tree API to fetch its children.
 * Collects all directory paths that need to be opened, sets revealedPaths
 * so FileTree components react by opening themselves, then waits for the DOM
 * to update and scrolls the target element into view with focus.
 *
 * Handles compact folders: when a directory has exactly one child that is also
 * a directory, FileTree merges them visually (e.g. "A/B/C"). The revealedPaths
 * must contain the *effective* path (the end of the compact chain) so that the
 * FileTree watcher matches correctly. This function follows those same chains
 * when walking the data, consuming multiple path segments at once.
 *
 * @param {string} absolutePath — the absolute path to scroll to
 * @returns {boolean} true if the target was found and revealed, false otherwise
 */
async function scrollToPath(absolutePath) {
    if (!absolutePath || !tree.value || !directory.value) return false

    // Compute relative path from the root directory
    const prefix = directory.value + '/'
    let relativePath
    if (absolutePath === directory.value) {
        // Target is the root itself — just scroll to it
        revealedPaths.value = new Set([directory.value])
        focusedPath.value = directory.value
        await nextTick()
        const el = treeContainerRef.value?.querySelector(`[data-path="${CSS.escape(directory.value)}"]`)
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
    const pathsToOpen = [directory.value]  // Root always needs to be open
    let currentNode = tree.value
    let currentAbsPath = directory.value
    let i = 0

    while (i < segments.length) {
        // If this directory isn't loaded yet, fetch its contents from the backend
        if (currentNode.loaded === false) {
            try {
                const res = await apiFetch(
                    `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(currentAbsPath)}${optionsQuery()}`
                )
                if (!res.ok) return false
                const data = await res.json()
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

// ─── File selection ──────────────────────────────────────────────────────────

const selectedFile = ref(null)

/**
 * Handle file selection from either the main tree or search results.
 * Receives the absolute path, stores the relative path for display.
 */
function onFileSelect(absolutePath) {
    const prefix = directory.value + '/'
    selectedFile.value = absolutePath.startsWith(prefix)
        ? absolutePath.slice(prefix.length)
        : absolutePath
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
    if (!selectedFile.value || !directory.value) return null
    return `${directory.value}/${selectedFile.value}`
})

/**
 * The tree node to display: search results when searching, main tree otherwise.
 */
const displayTree = computed(() => {
    if (isSearching.value && searchTree.value?.children?.length) {
        return searchTree.value
    }
    return tree.value
})

// ─── Focus management ────────────────────────────────────────────────────────

const searchInputRef = ref(null)

/**
 * Focus the search input field.
 * wa-input is a web component, so we need to call .focus() on it directly.
 */
function focusSearchInput() {
    searchInputRef.value?.focus()
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
</script>

<template>
    <div class="files-panel">
        <wa-split-panel
            class="files-split-panel"
            :position-in-pixels="250"
            primary="start"
            snap="150px 250px 350px"
            snap-threshold="30"
        >
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- File tree (left panel) -->
            <div slot="start" class="files-tree-panel">
                <!-- Search input + options -->
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
                        <wa-dropdown-item
                            type="checkbox"
                            value="auto-open"
                            :checked="autoOpen"
                        >
                            Auto-open
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
                        <wa-dropdown-item
                            v-if="selectedFile"
                            value="reveal-in-tree"
                        >
                            <wa-icon slot="icon" name="crosshairs"></wa-icon>
                            <div>Scroll to selected file</div>
                            <div class="root-path">{{ selectedFile }}</div>
                        </wa-dropdown-item>
                        <wa-dropdown-item value="refresh">
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
                <div v-else-if="!isSearching && !directory" class="panel-placeholder">
                    No directory
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
                            :path="directory"
                            :project-id="projectId"
                            :session-id="sessionId"
                            :is-root="true"
                            :all-open="isSearching"
                            :focused-path="focusedPath"
                            :extra-query="optionsQuery()"
                            :revealed-paths="revealedPaths"
                            :selected-path="selectedAbsPath"
                            :is-draft="isDraft"
                            @select="onFileSelect"
                            @focus="onNodeFocus"
                        />
                    </div>
                    <div v-if="isSearching && searchTruncated" class="search-truncated">
                        {{ searchTotal }} matches — showing first {{ searchTree.children.length }}
                    </div>
                </template>
            </div>

            <!-- File content (right panel) -->
            <div slot="end" class="files-content-panel">
                <FileContentViewer
                    v-show="selectedFile"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :file-path="selectedAbsPath"
                    :is-draft="isDraft"
                />
                <div v-show="!selectedFile" class="panel-placeholder">
                    Select a file
                </div>
            </div>
        </wa-split-panel>
    </div>
</template>

<style scoped>
.files-panel {
    height: 100%;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Split panel: file tree (start/primary) + file content (end)
   ═══════════════════════════════════════════════════════════════════════════ */

.files-split-panel {
    height: 100%;
    --min: 120px;
    --max: 60%;

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

.files-tree-panel {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.files-content-panel {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
}

/* Placeholder styling (for loading, error, and empty states) */
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
   Tree container (keyboard-navigable wrapper)
   ═══════════════════════════════════════════════════════════════════════════ */

.tree-container {
    flex: 1;
    overflow: auto;
    outline: none;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Search input
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

.root-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.root-missing {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-fill-loud);
}

.search-truncated {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    text-align: center;
    border-top: 1px solid var(--wa-color-surface-border);
}
</style>
