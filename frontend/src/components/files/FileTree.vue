<script setup>
/**
 * FileTree - Recursive tree component for displaying directory structures.
 *
 * Displays a nested file/directory tree with file-type-specific icons from
 * vscode-icons (served via the Iconify CDN).
 *
 * Directories can be opened/closed by clicking on them. The root directory
 * is always open and cannot be closed. Non-root directories start closed.
 * Closing a parent does NOT cascade to children — they keep their own state,
 * so reopening a parent restores the previous expanded layout.
 *
 * Directories with `loaded: false` are lazy-loaded: their children are fetched
 * from the backend on first open.
 *
 * Compact folders: when a directory's only child is another directory, they are
 * visually merged into a single node (e.g. "src/utils/helpers"). The icon and
 * toggle apply to the last directory in the chain. This stops at `loaded: false`
 * boundaries since we can't know what's inside yet.
 *
 * Props:
 *   node: { name, type, loaded?, children? } — the tree node data (mutated on lazy-load)
 *   path: absolute filesystem path of this node (used for lazy-load API calls)
 *   projectId: project ID (used for lazy-load API calls)
 *   sessionId: session ID (used for lazy-load API calls, scoped to the session)
 *   depth: nesting depth (used for indentation), defaults to 0
 *   isRoot: whether this is the root node (always open), defaults to false
 *   allOpen: if true, all directories are forced open (used for search results)
 *   focusedPath: the path of the currently keyboard-focused node (managed by parent)
 *   revealedPaths: Set of absolute paths that should be forced open (for reveal-in-tree)
 *   selectedPath: absolute path of the selected file (highlights file + ancestor dirs)
 *
 * Events:
 *   select(path): emitted when a file is activated (click or Enter/Space)
 *   focus(path): emitted when any node is clicked (file or directory), for focus tracking
 */

import { ref, computed, watch } from 'vue'
import { apiFetch } from '../../utils/api'
import { getIconUrl, getFileIconId, getFolderIconId } from '../../utils/fileIcons'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import GitStatusBadge from '../ui/GitStatusBadge.vue'

const props = defineProps({
    node: {
        type: Object,
        required: true,
    },
    path: {
        type: String,
        required: true,
    },
    projectId: {
        type: String,
        default: null,
    },
    sessionId: {
        type: String,
        default: null,
    },
    depth: {
        type: Number,
        default: 0,
    },
    isRoot: {
        type: Boolean,
        default: false,
    },
    // Display label for the root node only (defaults to its path). Lets callers
    // show a friendly name (e.g. "Artifacts") without altering the real path.
    rootLabel: {
        type: String,
        default: null,
    },
    allOpen: {
        type: Boolean,
        default: false,
    },
    focusedPath: {
        type: String,
        default: null,
    },
    extraQuery: {
        type: String,
        default: '',
    },
    revealedPaths: {
        type: Set,
        default: () => new Set(),
    },
    selectedPath: {
        type: String,
        default: null,
    },
    isDraft: {
        type: Boolean,
        default: false,
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
    lazyLoadFn: {
        type: Function,
        default: null,
    },
    /** Set of absolute paths (files + ancestor dirs) that have code comments. */
    commentedPaths: {
        type: Set,
        default: () => new Set(),
    },
})

const emit = defineEmits(['select', 'focus', 'context-menu'])

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

/**
 * Compact folders: walk down single-child directory chains.
 *
 * Returns { displayName, effectiveNode, effectivePath } where:
 * - displayName: combined name like "A/B/C"
 * - effectiveNode: the last directory node in the chain (owns the children)
 * - effectivePath: the absolute path to that last directory
 *
 * Stops compacting when:
 * - the node is not a directory
 * - the directory has != 1 child
 * - the single child is not a directory
 * - the directory has loaded: false (not yet fetched)
 * - the node is the root (root is never compacted with its children)
 */
/**
 * Get children relevant for compact folder logic.
 * In directoriesOnly mode, only directory children are considered.
 */
function getCompactableChildren(node) {
    if (!node.children) return []
    if (props.directoriesOnly) {
        return node.children.filter(c => c.type === 'directory')
    }
    return node.children
}

const compact = computed(() => {
    if (props.node.type !== 'directory' || props.isRoot || !props.compactFolders) {
        const displayName = props.isRoot ? (props.rootLabel || props.path) : props.node.name
        return { displayName, effectiveNode: props.node, effectivePath: props.path }
    }

    let current = props.node
    let currentPath = props.path
    const nameParts = [current.name]

    while (true) {
        const children = getCompactableChildren(current)
        if (
            current.type !== 'directory' ||
            current.loaded === false ||
            children.length !== 1 ||
            children[0].type !== 'directory'
        ) break
        current = children[0]
        currentPath = `${currentPath}/${current.name}`
        nameParts.push(current.name)
    }

    return { displayName: nameParts.join('/'), effectiveNode: current, effectivePath: currentPath }
})

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

// Directories: root and allOpen start open, others start closed.
// In git mode, all directories start open (the tree is fully loaded and small).
// Also open if this node's effective path is in the revealedPaths set (for reveal-in-tree).
const isOpen = ref(
    props.isRoot || props.allOpen || props.mode === 'git' || (props.node.type === 'directory' && props.revealedPaths.has(compact.value.effectivePath))
)
const isLoading = ref(false)

// React to revealedPaths changes: open this node if its effective path appears in the set
watch(
    () => props.revealedPaths,
    (paths) => {
        if (props.node.type === 'directory' && paths.has(compact.value.effectivePath)) {
            isOpen.value = true
        }
    }
)

function handleClick() {
    emit('focus', nodePath.value)
    if (props.node.type === 'directory') {
        toggleOpen()
        // In directoriesOnly mode, clicking a directory also selects it
        if (props.directoriesOnly) {
            emit('select', compact.value.effectivePath)
        }
    } else {
        emit('select', props.path)
    }
}

async function toggleOpen() {
    if (props.node.type !== 'directory') return

    const { effectiveNode, effectivePath } = compact.value

    // If opening a not-yet-loaded directory, fetch its children first.
    // In git mode all data is already loaded, so skip the API call.
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
                // Mutate the node in-place: inject fetched children
                effectiveNode.children = data.children || []
                effectiveNode.loaded = true
            }
        } catch {
            // Silently fail — the folder just won't open
        } finally {
            isLoading.value = false
        }
    }

    isOpen.value = !isOpen.value
}

const iconUrl = computed(() => {
    const { effectiveNode } = compact.value
    if (effectiveNode.type === 'directory') {
        return getIconUrl(getFolderIconId(effectiveNode.name, isOpen.value))
    }
    return getIconUrl(getFileIconId(effectiveNode.name))
})

/**
 * Build the absolute path for a child node.
 */
function childPath(childName) {
    return `${compact.value.effectivePath}/${childName}`
}

/**
 * The effective path of this node (after compact resolution).
 * Used as the data-path attribute for keyboard navigation.
 */
const nodePath = computed(() => compact.value.effectivePath)

/**
 * Whether this node is the keyboard-focused node.
 */
const isFocused = computed(() => props.focusedPath === nodePath.value)

/**
 * Whether this node is on the selected file's path.
 * True for the selected file itself AND all its ancestor directories.
 * A directory is "on the path" if the selected file's absolute path starts
 * with this node's effective path followed by a "/".
 */
const isSelected = computed(() => {
    if (!props.selectedPath) return false
    const ep = nodePath.value
    return props.selectedPath === ep || props.selectedPath.startsWith(ep + '/')
})

const commentCount = computed(() => {
    if (props.commentedPaths.size === 0) return 0
    return props.commentedPaths.has(nodePath.value) ? 1 : 0
})

// ─── Context menu (right-click + long press for iOS) ────────────────────────

function handleContextMenu(event) {
    event.preventDefault()
    emitContextMenu(event.clientX, event.clientY)
}

function emitContextMenu(x, y) {
    const effectiveNode = compact.value.effectiveNode
    const node = effectiveNode.type ? effectiveNode : props.node
    emit('context-menu', {
        path: nodePath.value,
        name: node.name || props.node.name,
        type: node.type || props.node.type,
        x,
        y,
        stagedStatus: node.staged_status || null,
        unstagedStatus: node.unstaged_status || null,
        status: node.status || null,
    })
}

let longPressTimer = null
let longPressTriggered = false

function onTouchStart(event) {
    if (event.touches.length !== 1) return
    longPressTriggered = false
    const touch = event.touches[0]
    const x = touch.clientX
    const y = touch.clientY
    longPressTimer = setTimeout(() => {
        longPressTriggered = true
        emitContextMenu(x, y)
    }, 500)
}

function onTouchMove() {
    clearTimeout(longPressTimer)
    longPressTimer = null
}

function onTouchEnd(event) {
    clearTimeout(longPressTimer)
    longPressTimer = null
    if (longPressTriggered) {
        event.preventDefault()
    }
}
</script>

<template>
    <div class="file-tree-node" :class="{ 'is-root': isRoot }">
        <!-- Node label -->
        <div
            class="node-label"
            :class="[
                node.type === 'directory' ? 'is-directory' : 'is-file',
                { 'is-toggle': node.type === 'directory' },
                { 'is-clickable': node.type === 'file' },
                { 'is-focused': isFocused },
                { 'is-selected': isSelected },
            ]"
            :style="{ '--level': depth }"
            :data-path="nodePath"
            :data-type="node.type"
            :data-open="node.type === 'directory' ? (isOpen ? 'true' : 'false') : undefined"
            role="treeitem"
            :tabindex="isFocused ? 0 : -1"
            @click="handleClick"
            @contextmenu="handleContextMenu"
            @touchstart.passive="onTouchStart"
            @touchmove.passive="onTouchMove"
            @touchend="onTouchEnd"
        >
            <!-- Loading spinner (replaces icon while fetching) -->
            <wa-spinner v-if="isLoading" class="node-spinner"></wa-spinner>
            <img
                v-else
                :src="iconUrl"
                :alt="compact.displayName"
                class="node-icon"
                loading="lazy"
                width="16"
                height="16"
            />
            <span class="node-name">{{ compact.displayName }}</span>
            <CodeCommentsIndicator :count="commentCount" :show-tooltip="false" class="comment-badge" />
            <GitStatusBadge v-if="mode === 'git' && node.type === 'file'" :node="node" class="git-meta" />
        </div>

        <!-- Children (only rendered when directory is open) -->
        <div
            v-if="isOpen && compact.effectiveNode.type === 'directory' && visibleChildren.length"
            class="node-children"
            role="group"
        >
            <FileTree
                v-for="child in visibleChildren"
                :key="child.name"
                :node="child"
                :path="childPath(child.name)"
                :project-id="projectId"
                :session-id="sessionId"
                :depth="depth + 1"
                :all-open="allOpen"
                :focused-path="focusedPath"
                :extra-query="extraQuery"
                :revealed-paths="revealedPaths"
                :selected-path="selectedPath"
                :is-draft="isDraft"
                :mode="mode"
                :directories-only="directoriesOnly"
                :compact-folders="compactFolders"
                :lazy-load-fn="lazyLoadFn"
                :commented-paths="commentedPaths"
                @select="(path) => emit('select', path)"
                @focus="(path) => emit('focus', path)"
                @context-menu="(data) => emit('context-menu', data)"
            />
        </div>
    </div>
</template>

<style scoped>
.file-tree-node {
    user-select: none;
    font-size: var(--wa-font-size-m);
    display: flex;
    flex-direction: column;
    align-items: stretch;
    /* Only way I found to have the git badges stick on the right regardless of the width of the node content */
    width: 1000%;
}

.node-children {
    display: flex;
    flex-direction: column;
    align-items: stretch;
}

.node-label {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-3xs) var(--wa-space-xs) var(--wa-space-3xs) calc(var(--level) * var(--wa-space-m) + var(--wa-space-2xs));
    line-height: 1.6;
    cursor: default;
    white-space: nowrap;
    width: fit-content;
    min-width: 100%;
    outline: none;
    position: relative;
    /* Contextual row tokens: glass surfaces (styles/glass.css) set --row-*; the Files
       and Git panes keep these fallbacks. */
    --node-bg-color: var(--row-bg, var(--wa-color-surface-default));
    background-color: var(--node-bg-color);
}

.node-label:hover {
    --node-bg-color: var(--row-hover-bg, var(--wa-color-surface-raised));
}

.node-label.is-selected .node-name {
    text-decoration: underline;
}

.node-label.is-selected:hover {
    --node-bg-color: var(--row-active-bg, var(--wa-color-surface-lowered));
}

.node-label.is-focused {
    --node-bg-color: var(--row-active-bg, var(--wa-color-surface-lowered));
}

.node-label.is-toggle,
.node-label.is-clickable {
    cursor: pointer;
}

.node-label.is-directory {
    color: var(--wa-color-text-normal);
    font-weight: 500;
}

.node-label.is-file {
    color: var(--wa-color-text-quiet);
}

.node-icon {
    flex-shrink: 0;
    width: var(--wa-space-m);
    height: var(--wa-space-m);
}

.node-spinner {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    --indicator-color: var(--wa-color-text-quiet);
    --track-width: 2px;
}

.node-name {
}

/* ----- Comment badge ----- */

.comment-badge {
    flex-shrink: 0;
    font-size: 0.7em;
}

/* ----- Git status badge (git mode only) ----- */

/* Positioning wrapper for the GitStatusBadge in a tree row: pinned to the right
   edge (sticky) so the flag stays visible regardless of the node name's width.
   The badge / line-stat visuals all live in GitStatusBadge.vue. */
.git-meta {
    position: sticky;
    right: 0;
    flex-shrink: 0;
    margin-left: auto;
    background-color: var(--node-bg-color);
    padding-block: .15rem;
    padding-inline: .4rem .25rem;
}

</style>
