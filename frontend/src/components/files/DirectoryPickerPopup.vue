<script setup>
/**
 * DirectoryPickerPopup - A popup file tree for selecting directories.
 *
 * Opens a wa-popup anchored to a Browse button, showing a FileTreePanel
 * in directoriesOnly mode. The tree is fetched from the standalone
 * /api/directory-tree/ endpoint (no project required).
 *
 * The popup starts at the directory specified by modelValue (if it exists),
 * falling back to fallbackPath (if provided), then $HOME. Clicking any
 * directory both opens/closes it and selects it (emits update:modelValue).
 * The popup stays open until the user closes it explicitly (click outside,
 * Escape, or Browse button).
 *
 * Props:
 *   modelValue: current directory path (v-model)
 *   fallbackPath: starting directory when modelValue is empty/unresolvable
 *
 * Events:
 *   update:modelValue: emitted when a directory is selected
 */

import { ref, watch, nextTick, useId, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import FileTreePanel from './FileTreePanel.vue'
import { usePopupMotion } from '../../composables/usePopupMotion'

const props = defineProps({
    modelValue: {
        type: String,
        default: '',
    },
    fallbackPath: {
        type: String,
        default: '',
    },
})

const emit = defineEmits(['update:modelValue'])

// ─── Popup state ─────────────────────────────────────────────────────────────

const popupRef = ref(null)
const anchorId = useId()
const isOpen = ref(false)
// The panel grows from its anchor on each opening; the close stays instant (overlay motion §6).
const panelRef = ref(null)
usePopupMotion(isOpen, panelRef, popupRef)

// ─── Tree state ──────────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const error = ref(null)
const rootPath = ref(null)
const fileTreePanelRef = ref(null)
// Show hidden (dot) directories — e.g. a `.worktrees/` folder would otherwise
// be unreachable from the picker.
const showHidden = ref(false)

/**
 * Fetch a directory tree from the standalone endpoint.
 */
async function fetchTree(dirPath) {
    const hidden = showHidden.value ? '&show_hidden=1' : ''
    const res = await apiFetch(
        `/api/directory-tree/?path=${encodeURIComponent(dirPath)}&directories_only=1${hidden}`
    )
    if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || 'Failed to load directory')
    }
    return await res.json()
}

/**
 * Lazy-load function provided to FileTreePanel and FileTree.
 * Fetches children for a directory that hasn't been loaded yet.
 */
async function lazyLoadDir(path) {
    return await fetchTree(path)
}

/**
 * Fetch the user's home directory path.
 */
async function fetchHomePath() {
    try {
        const res = await apiFetch('/api/home-directory/')
        if (res.ok) {
            const data = await res.json()
            return data.path
        }
    } catch {
        // Fall through
    }
    return '/'
}

/**
 * Resolve the starting path for the tree.
 * If modelValue is a valid existing directory, start there.
 * Otherwise walk up to find the nearest existing parent.
 * Falls back to $HOME.
 */
async function resolveStartPath(inputPath) {
    const trimmed = (inputPath || '').trim()
    if (!trimmed || !trimmed.startsWith('/')) {
        return null
    }

    // Try the path as-is, then walk up to find an existing parent
    const segments = trimmed.replace(/\/+$/, '').split('/')
    for (let i = segments.length; i >= 1; i--) {
        const candidate = segments.slice(0, i).join('/') || '/'
        try {
            const data = await fetchTree(candidate)
            return { path: candidate, data }
        } catch {
            // Continue to parent
        }
    }
    return null
}

// ─── Open / close ────────────────────────────────────────────────────────────

async function openPopup() {
    if (isOpen.value) {
        closePopup()
        return
    }

    isOpen.value = true
    loading.value = true
    error.value = null

    try {
        // Try to resolve the starting path from the current modelValue,
        // then from the caller-provided fallback (e.g. a repo's git root)
        let resolved = await resolveStartPath(props.modelValue)
        if (!resolved && props.fallbackPath) {
            resolved = await resolveStartPath(props.fallbackPath)
        }

        if (resolved) {
            rootPath.value = resolved.path
            tree.value = resolved.data
        } else {
            // Fallback to $HOME
            const homePath = await fetchHomePath()
            const data = await fetchTree(homePath)
            rootPath.value = homePath
            tree.value = data
        }
    } catch (e) {
        error.value = e.message || 'Failed to load directory'
    } finally {
        loading.value = false
    }

    // Wait for both the wa-popup rendering and FileTreePanel's inner rendering
    // before the tree becomes interactive (same pattern as FileTreePanel.scrollToPath)
    await nextTick()
    await nextTick()

    // Focus the root node so keyboard navigation works immediately
    if (rootPath.value) {
        fileTreePanelRef.value?.scrollToPath(rootPath.value)
    }
}

function closePopup() {
    isOpen.value = false
    tree.value = null
    rootPath.value = null
    error.value = null
}

// ─── Hidden directories toggle ───────────────────────────────────────────────

/** Toggle hidden (dot) directories and reload the tree from the current root.
 *  A full refetch is required: lazily-loaded children are cached in `tree`. */
async function toggleHidden() {
    showHidden.value = !showHidden.value
    if (!rootPath.value) return
    loading.value = true
    error.value = null
    try {
        tree.value = await fetchTree(rootPath.value)
    } catch (e) {
        error.value = e.message || 'Failed to load directory'
    } finally {
        loading.value = false
    }
}

// ─── Navigate up ─────────────────────────────────────────────────────────────

async function navigateUp() {
    if (!rootPath.value || rootPath.value === '/') return

    const parentPath = rootPath.value.replace(/\/[^/]+$/, '') || '/'
    loading.value = true
    error.value = null
    try {
        const data = await fetchTree(parentPath)
        rootPath.value = parentPath
        tree.value = data
    } catch (e) {
        error.value = e.message || 'Failed to load directory'
    } finally {
        loading.value = false
    }
}

// ─── Selection ───────────────────────────────────────────────────────────────

function onDirectorySelect(pathOrRelative) {
    let absolutePath
    if (pathOrRelative.startsWith('/')) {
        // Already absolute (root was selected, or path wasn't stripped)
        absolutePath = pathOrRelative
    } else {
        absolutePath = rootPath.value === '/'
            ? `/${pathOrRelative}`
            : `${rootPath.value}/${pathOrRelative}`
    }
    emit('update:modelValue', absolutePath)
}

// ─── Click outside to close ──────────────────────────────────────────────────

function onDocumentClick(event) {
    if (!isOpen.value) return
    const popup = popupRef.value
    const anchor = document.getElementById(anchorId)
    if (!popup || !anchor) return

    // Check if click is inside the popup content or the anchor button
    if (popup.contains(event.target) || anchor.contains(event.target)) return

    closePopup()
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
 * Handle special keys for the popup (capture phase, fires before FileTreePanel).
 *
 * - Escape: close the popup (prevents FileTreePanel from focusing its search input).
 * - PageUp on root node when rootPath ≠ '/': navigate to parent directory
 *   (same as clicking the ↑ button in the header).
 */
async function onPickerKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closePopup()
        return
    }

    if (event.key === 'PageUp' && rootPath.value && rootPath.value !== '/') {
        const activeEl = document.activeElement
        if (
            activeEl?.getAttribute('role') === 'treeitem'
            && activeEl?.dataset?.path === rootPath.value
        ) {
            event.preventDefault()
            event.stopPropagation()
            await navigateUp()
            await nextTick()
            await nextTick()
            if (rootPath.value) {
                fileTreePanelRef.value?.scrollToPath(rootPath.value)
            }
        }
    }
}

// Clean up document listener if component is unmounted while popup is open
onBeforeUnmount(() => {
    document.removeEventListener('click', onDocumentClick, true)
})
</script>

<template>
    <div class="directory-picker">
        <wa-button
            :id="anchorId"
            variant="neutral"
            appearance="plain"
            size="small"
            class="browse-button"
            @click="openPopup"
        >
            <wa-icon name="folder-open" label="Browse directories"></wa-icon>
        </wa-button>

        <wa-popup
            ref="popupRef"
            :anchor="anchorId"
            placement="bottom-end"
            :active="isOpen"
            :distance="4"
            flip
            shift
            shift-padding="8"
            class="picker-popup"
        >
            <div ref="panelRef" class="picker-panel glass-surface" @keydown.capture="onPickerKeydown">
                <!-- Header: current path + navigate up -->
                <div class="picker-header">
                    <wa-button
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        :disabled="!rootPath || rootPath === '/'"
                        @click="navigateUp"
                    >
                        <wa-icon name="arrow-up" label="Go to parent directory"></wa-icon>
                    </wa-button>
                    <span class="picker-path" :title="rootPath">{{ rootPath || '...' }}</span>
                    <wa-button
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="hidden-toggle"
                        :class="{ 'hidden-toggle-active': showHidden }"
                        :title="showHidden ? 'Hide hidden directories' : 'Show hidden directories'"
                        @click="toggleHidden"
                    >
                        <wa-icon :name="showHidden ? 'eye' : 'eye-slash'" label="Toggle hidden directories"></wa-icon>
                    </wa-button>
                </div>

                <!-- Tree -->
                <FileTreePanel
                    ref="fileTreePanelRef"
                    :tree="tree"
                    :loading="loading"
                    :error="error"
                    :root-path="rootPath"
                    :lazy-load-fn="lazyLoadDir"
                    :show-refresh="false"
                    directories-only
                    :compact-folders="false"
                    mode="files"
                    @file-select="onDirectorySelect"
                />
            </div>
        </wa-popup>
    </div>
</template>

<style scoped>
.directory-picker {
    display: inline-flex;
    position: relative;
}

.browse-button {
    font-size: var(--wa-font-size-l);
}

.picker-panel {
    width: min(30rem, calc(100vw - 1rem));
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
    /* --glass-edge-gap (set inside glass surfaces): the focus ring clears the glass border. */
    padding: calc(var(--wa-space-2xs) + var(--glass-edge-gap, 0px)) var(--wa-space-xs);
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

.hidden-toggle {
    flex-shrink: 0;
    opacity: 0.7;
}

.hidden-toggle-active {
    opacity: 1;
    color: var(--wa-color-brand-text);
}
</style>
