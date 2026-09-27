<script setup>
/**
 * CommandPickerPopup - A popup for selecting a provider command triggered by
 * an activation character (e.g. ``/`` for Claude Code; Codex will eventually
 * also expose ``$``). One instance is mounted per ``MessageInput`` and reused
 * across activation chars by swapping the ``activationChar`` prop — the
 * consumer sets it to whatever char triggered the open and the popup fetches
 * and inserts using that char.
 *
 * Opens a wa-popup anchored to a given element, showing a filterable list of
 * available commands fetched from the backend API for the current
 * (provider, activationChar) pair.
 *
 * Keyboard navigation follows the same two-handler pattern as FileTreePanel:
 * - The search input handles ArrowDown/PageDown to move focus into the list
 * - The list container handles all navigation keys (arrows, Home/End, Page, Enter, Escape)
 * - DOM focus physically moves between the search input and the list container
 *
 * Props:
 *   projectId: current project id
 *   provider: provider key of the current session (may be ``null`` briefly)
 *   activationChar: prefix char the popup was opened for (e.g. "/"). Used as
 *                   the backend query param, as the leading char stripped
 *                   from the search field, and as the prefix inserted with
 *                   the selected command.
 *   anchorId: id of the element to anchor the popup to
 *
 * Events:
 *   select(commandText): emitted when a command is selected (e.g. "/commit " or "/plugin:command ")
 *   close(): emitted when the popup is closed without selection
 */

import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import { getProviderHelpers } from '../../providers'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    // Provider key of the session the popup is opened for. May be ``null``
    // briefly while the session hasn't loaded yet — the popup is mounted
    // eagerly via Teleport but ``open()`` is gated by the consumer, which
    // only triggers it once a provider is known.
    provider: {
        type: String,
        default: null,
    },
    // Activation prefix the popup is opened for. The consumer flips this
    // prop just before calling ``open()``; the rest of the lifecycle reads
    // it through the prop directly.
    activationChar: {
        type: String,
        default: null,
    },
    anchorId: {
        type: String,
        required: true,
    },
})

const emit = defineEmits(['select', 'close', 'filter-change'])

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
const searchInputRef = ref(null)
const listRef = ref(null)

// ─── Data ─────────────────────────────────────────────────────────────────

const allCommands = ref([])
const loading = ref(false)
const error = ref(null)
const searchQuery = ref('')
const activeIndex = ref(0)

// Number of items to jump with PageUp/PageDown
const PAGE_SIZE = 10

// ─── Display tag ──────────────────────────────────────────────────────────
// Builds the parenthesized tag shown next to the command name.
// Examples: (built-in) — (superpowers plugin, project) — (global) — (project)
//           (built-in workflow) — (workflow, project) — (superpowers plugin workflow, global)

function commandTag(cmd) {
    const scope = cmd.is_global ? 'global' : 'project'
    if (cmd.is_workflow) {
        if (cmd.is_builtin) return 'built-in workflow'
        if (cmd.plugin_name) return `${cmd.plugin_name} plugin workflow, ${scope}`
        return `workflow, ${scope}`
    }
    if (cmd.is_builtin) return 'built-in'
    if (cmd.plugin_name) return `${cmd.plugin_name} plugin, ${scope}`
    return scope
}

// ─── Filtered commands ────────────────────────────────────────────────────

const filteredCommands = computed(() => {
    const query = searchQuery.value.trim().toLowerCase()
    if (!query) return allCommands.value
    return allCommands.value.filter(cmd => {
        if (cmd.name.toLowerCase().includes(query)) return true
        if (cmd.plugin_name && cmd.plugin_name.toLowerCase().includes(query)) return true
        return false
    })
})

// ─── API fetch ────────────────────────────────────────────────────────────

async function fetchCommands() {
    if (!props.provider || !props.activationChar) {
        allCommands.value = []
        return
    }
    loading.value = true
    error.value = null
    try {
        const res = await apiFetch(`/api/projects/${props.projectId}/commands/?provider=${encodeURIComponent(props.provider)}&activation_char=${encodeURIComponent(props.activationChar)}`)
        if (!res.ok) {
            const data = await res.json()
            error.value = data.error || `HTTP ${res.status}`
            allCommands.value = []
            return
        }
        const data = await res.json()
        const helpers = getProviderHelpers(props.provider)
        const builtins = helpers ? helpers.getBuiltInCommands(props.activationChar) : []
        const all = [...builtins, ...(data.commands || [])]
        all.sort((a, b) => a.name.localeCompare(b.name))
        allCommands.value = all
    } catch (err) {
        error.value = err.message
        allCommands.value = []
    } finally {
        loading.value = false
    }
}

// ─── Open / close ─────────────────────────────────────────────────────────

async function open() {
    if (isOpen.value) return

    searchQuery.value = ''
    activeIndex.value = 0
    isOpen.value = true

    await fetchCommands()

    // Wait for popup and input to render
    await nextTick()
    await nextTick()

    // Focus the search input
    focusSearchInput()
}

function close(payload = {}) {
    isOpen.value = false
    allCommands.value = []
    error.value = null
    searchQuery.value = ''
    emit('close', payload)
}

// ─── Focus management ─────────────────────────────────────────────────────

function focusSearchInput() {
    try {
        searchInputRef.value?.focus()
    } catch {
        // wa-input.focus() can throw if the shadow DOM isn't ready yet.
    }
}

// ─── Search input handler ─────────────────────────────────────────────────

function onSearchInput(event) {
    const raw = event.target.value
    // Strip the leading activation char — the user already typed it to open
    // the popup, so it's not part of the filter the picker matches against.
    const prefix = props.activationChar
    searchQuery.value = prefix && raw.startsWith(prefix) ? raw.slice(prefix.length) : raw
}

// ─── Selection ────────────────────────────────────────────────────────────

function selectCommand(cmd) {
    const prefix = props.activationChar ?? ''
    let text
    if (cmd.plugin_name) {
        text = `${prefix}${cmd.plugin_name}:${cmd.name} `
    } else {
        text = `${prefix}${cmd.name} `
    }
    emit('select', text)
    close()
}

function selectActive() {
    const cmds = filteredCommands.value
    if (cmds.length > 0 && activeIndex.value < cmds.length) {
        selectCommand(cmds[activeIndex.value])
    }
}

// ─── Scroll active item into view ─────────────────────────────────────────

function scrollActiveIntoView() {
    nextTick(() => {
        const container = listRef.value
        if (!container) return
        const el = container.querySelector(`[data-index="${activeIndex.value}"]`)
        el?.scrollIntoView({ block: 'nearest' })
    })
}

// ─── Search input keyboard handler ────────────────────────────────────────
// Only handles keys that move focus out of the search input into the list.
// All other keys pass through for normal typing.

function handleSearchKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
        return
    }
    if (event.key === 'ArrowDown') {
        event.preventDefault()
        const count = filteredCommands.value.length
        if (count > 1) {
            // First item is already selected, move to second
            activeIndex.value = 1
            listRef.value?.focus()
            scrollActiveIntoView()
        } else if (count === 1) {
            listRef.value?.focus()
        }
        return
    }
    if (event.key === 'PageDown') {
        event.preventDefault()
        const count = filteredCommands.value.length
        if (count > 0) {
            activeIndex.value = Math.min(PAGE_SIZE, count - 1)
            listRef.value?.focus()
            scrollActiveIntoView()
        }
        return
    }
    if (event.key === 'Enter') {
        // Cmd/Ctrl+Enter is a no-op inside the popover (does not close, does not send).
        if (event.metaKey || event.ctrlKey) return
        event.preventDefault()
        if (filteredCommands.value.length > 0) {
            selectActive()
        } else {
            // No match: close and ask the parent to preserve the typed text.
            // In char-trigger mode the text is already mirrored in the textarea;
            // in button mode the parent will insert it at the right position.
            close({ preserveText: searchQuery.value })
        }
        return
    }
}

// ─── List keyboard handler ────────────────────────────────────────────────
// Handles all navigation when focus is in the list container.

function handleListKeydown(event) {
    const cmds = filteredCommands.value
    const count = cmds.length
    if (!count) return

    switch (event.key) {
        case 'ArrowDown': {
            event.preventDefault()
            const next = activeIndex.value + 1
            if (next < count) {
                activeIndex.value = next
                scrollActiveIntoView()
            }
            break
        }

        case 'ArrowUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                // First item → go back to search input, first item stays highlighted
                activeIndex.value = 0
                focusSearchInput()
            } else {
                activeIndex.value = activeIndex.value - 1
                scrollActiveIntoView()
            }
            break
        }

        case 'Home': {
            event.preventDefault()
            activeIndex.value = 0
            scrollActiveIntoView()
            break
        }

        case 'End': {
            event.preventDefault()
            activeIndex.value = count - 1
            scrollActiveIntoView()
            break
        }

        case 'PageDown': {
            event.preventDefault()
            activeIndex.value = Math.min(activeIndex.value + PAGE_SIZE, count - 1)
            scrollActiveIntoView()
            break
        }

        case 'PageUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                activeIndex.value = 0
                focusSearchInput()
            } else {
                activeIndex.value = Math.max(activeIndex.value - PAGE_SIZE, 0)
                scrollActiveIntoView()
            }
            break
        }

        case 'Enter': {
            if (event.metaKey || event.ctrlKey) break
            event.preventDefault()
            if (filteredCommands.value.length > 0) {
                selectActive()
            } else {
                close({ preserveText: searchQuery.value })
            }
            break
        }

        case 'Escape': {
            event.preventDefault()
            event.stopPropagation()
            close()
            break
        }

        default:
            // Any other key (letter, etc.) → go back to search input for typing
            if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
                focusSearchInput()
            }
            return
    }
}

// ─── Reset active index when search changes ───────────────────────────────

watch(searchQuery, (newVal) => {
    activeIndex.value = 0
    emit('filter-change', newVal)
})

// ─── Click outside to close ───────────────────────────────────────────────

function onDocumentClick(event) {
    if (!isOpen.value) return
    const popup = popupRef.value
    if (!popup) return
    if (popup.contains(event.target)) return
    close()
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

// Clean up document listener if component is unmounted while popup is open
onBeforeUnmount(() => {
    document.removeEventListener('click', onDocumentClick, true)
})

defineExpose({ open, close, isOpen })
</script>

<template>
    <wa-popup
        ref="popupRef"
        :anchor="anchorId"
        placement="top-start"
        :active="isOpen"
        :distance="4"
        flip
        shift
        shift-padding="8"
        class="picker-popup"
    >
        <div class="picker-panel glass-surface">
            <!-- Search input -->
            <div class="picker-search">
                <wa-input
                    ref="searchInputRef"
                    :value="searchQuery"
                    placeholder="Filter commands..."
                    size="small"
                    with-clear
                    class="picker-search-input"
                    @input="onSearchInput"
                    @keydown="handleSearchKeydown"
                >
                    <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                </wa-input>
            </div>

            <!-- Command list (focusable container for keyboard navigation) -->
            <div
                ref="listRef"
                class="picker-list"
                tabindex="0"
                @keydown="handleListKeydown"
            >
                <template v-if="loading">
                    <div class="picker-status">Loading...</div>
                </template>
                <template v-else-if="error">
                    <div class="picker-status picker-error">{{ error }}</div>
                </template>
                <template v-else-if="filteredCommands.length === 0">
                    <div class="picker-status">
                        {{ searchQuery ? 'No matching commands' : 'No commands available' }}
                    </div>
                </template>
                <template v-else>
                    <div
                        v-for="(cmd, index) in filteredCommands"
                        :key="(cmd.plugin_name || '') + ':' + cmd.name"
                        :data-index="index"
                        class="picker-item"
                        :class="{ active: index === activeIndex }"
                        @click="selectCommand(cmd)"
                        @mouseenter="activeIndex = index"
                    >
                        <div class="item-header">
                            <span class="cmd-name">{{ activationChar }}{{ cmd.name }}</span>
                            <span v-if="cmd.argument_hint" class="cmd-hint">{{ cmd.argument_hint }}</span>
                            <span class="cmd-tag">({{ commandTag(cmd) }})</span>
                        </div>
                        <div v-if="cmd.description" class="item-desc">{{ cmd.description }}</div>
                    </div>
                </template>
            </div>
        </div>
    </wa-popup>
</template>

<style scoped>
.picker-panel {
    width: min(40rem, calc(100vw - 1rem));
    max-height: min(25rem, 50dvh);
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

/* ─── Search ──────────────────────────────────────────────────────────── */

.picker-search {
    /* --glass-edge-gap (set inside glass surfaces): the focus ring clears the glass border. */
    padding: calc(var(--wa-space-2xs) + var(--glass-edge-gap, 0px));
    border-bottom: 1px solid var(--wa-color-surface-border);
    flex-shrink: 0;
}

.picker-search-input {
    width: 100%;
}

/* ─── List ────────────────────────────────────────────────────────────── */

.picker-list {
    overflow-y: auto;
    flex: 1;
    min-height: 0;
    outline: none;
}

.picker-status {
    padding: var(--wa-space-m) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    text-align: center;
}

.picker-error {
    color: var(--wa-color-status-danger-text);
}

/* ─── Item ────────────────────────────────────────────────────────────── */

.picker-item {
    padding: var(--wa-space-xs);
    cursor: pointer;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
    row-gap: var(--wa-space-2xs);
}

.picker-item:hover {
    background: var(--glass-item-hover);
}

.picker-item.active {
    background: var(--glass-item-highlight);
}

.item-header {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    row-gap: 0;
}

.cmd-name {
    font-family: var(--wa-font-family-code);
    font-weight: 600;
    color: var(--wa-color-text-default);
    white-space: nowrap;
}

.cmd-hint {
    white-space: nowrap;
}

.cmd-tag {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
    margin-left: var(--wa-space-3xs);
}

.item-desc {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
