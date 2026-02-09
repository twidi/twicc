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
import { apiFetch } from '../utils/api'
import { getIconUrl, getFileIconId, getFolderIconId } from '../utils/fileIcons'

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
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    depth: {
        type: Number,
        default: 0,
    },
    isRoot: {
        type: Boolean,
        default: false,
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
})

const emit = defineEmits(['select', 'focus'])

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
const compact = computed(() => {
    if (props.node.type !== 'directory' || props.isRoot) {
        return { displayName: props.node.name, effectiveNode: props.node, effectivePath: props.path }
    }

    let current = props.node
    let currentPath = props.path
    const nameParts = [current.name]

    while (
        current.type === 'directory' &&
        current.loaded !== false &&
        current.children?.length === 1 &&
        current.children[0].type === 'directory'
    ) {
        current = current.children[0]
        currentPath = `${currentPath}/${current.name}`
        nameParts.push(current.name)
    }

    return { displayName: nameParts.join('/'), effectiveNode: current, effectivePath: currentPath }
})

// Directories: root and allOpen start open, others start closed.
// Also open if this node's effective path is in the revealedPaths set (for reveal-in-tree).
const isOpen = ref(
    props.isRoot || props.allOpen || (props.node.type === 'directory' && props.revealedPaths.has(compact.value.effectivePath))
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
    } else {
        emit('select', props.path)
    }
}

async function toggleOpen() {
    if (props.node.type !== 'directory') return

    const { effectiveNode, effectivePath } = compact.value

    // If opening a not-yet-loaded directory, fetch its children first
    if (!isOpen.value && effectiveNode.loaded === false) {
        isLoading.value = true
        try {
            const res = await apiFetch(
                `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(effectivePath)}${props.extraQuery}`
            )
            if (res.ok) {
                const data = await res.json()
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
                { 'is-selected': isSelected }
            ]"
            :style="{ '--level': depth }"
            :data-path="nodePath"
            :data-type="node.type"
            :data-open="node.type === 'directory' ? (isOpen ? 'true' : 'false') : undefined"
            role="treeitem"
            :tabindex="isFocused ? 0 : -1"
            @click="handleClick"
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
        </div>

        <!-- Children (only rendered when directory is open) -->
        <div
            v-if="isOpen && compact.effectiveNode.type === 'directory' && compact.effectiveNode.children?.length"
            class="node-children"
            role="group"
        >
            <FileTree
                v-for="child in compact.effectiveNode.children"
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
                @select="(path) => emit('select', path)"
                @focus="(path) => emit('focus', path)"
            />
        </div>
    </div>
</template>

<style scoped>
.file-tree-node {
    user-select: none;
    font-size: var(--wa-font-size-m);
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
}

.node-label:hover {
    background-color: var(--wa-color-surface-raised);
}

.node-label.is-selected {
    background-color: var(--wa-color-neutral-fill-quiet);
}

.node-label.is-selected:hover {
    background-color: var(--wa-color-neutral-fill-quiet);
}

.node-label.is-focused {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
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

</style>
